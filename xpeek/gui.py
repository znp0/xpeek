"""GTK4 translation popup, using layer-shell where available."""
from __future__ import annotations

import io
import json
import os
import signal
import sys
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from ctypes import CDLL
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from queue import Empty
from threading import Event

# Load layer-shell before GTK/libwayland, as required by its Python bindings.
try:
    _layer_library = CDLL("libgtk4-layer-shell.so.0")
except OSError:
    _layer_library = None

os.environ.setdefault("GDK_BACKEND", "wayland")
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk
except (ImportError, ValueError) as exc:
    raise ImportError(
        "GTK4/PyGObject is unavailable. On Arch/CachyOS, install "
        "python-gobject gtk4 gtk4-layer-shell, then rerun python3 setup.py install."
    ) from exc

try:
    if _layer_library is None:
        raise ImportError("Layer-shell library not installed")
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except (ImportError, ValueError):
    LayerShell = None

from . import APPLICATION_ID
from .capture import CaptureCancelled, CaptureError, capture_screen, select_region
from .config import Config, Region
from .history import HistoryStore
from .ocr import OcrError, extract_text, shutdown_backends
from .providers import API_KEY_ENV, TranslationError, get_translator
from .remote import remote_notice
from .window_state import MIN_HEIGHT, MIN_WIDTH, WindowState, reset_saved_size

DISPLAY_LIMIT = 10


@dataclass(eq=False)
class DisplayEntry:
    heading: str
    text: str
    start: Gtk.TextMark | None = None
    body_start: Gtk.TextMark | None = None
    body_end: Gtk.TextMark | None = None
    end: Gtk.TextMark | None = None
    has_heading: bool = False
    rendered_text: str | None = None


def translation_result(
    config: Config, image: Path, credentials: dict | None = None
) -> tuple[str, str, str]:
    """Perform blocking work without accessing GTK objects."""
    original = ""
    try:
        try:
            original = extract_text(image, config.ocr_engine, config.ocr_options.get(config.ocr_engine, {}))
        except OcrError as exc:
            return f"OCR error: {exc}", "", ""
        finally:
            image.unlink(missing_ok=True)
        # Game dialog lines often belong to one sentence.
        original = original.replace("\n", " ").strip()
        if not original:
            return "", "", ""
        try:
            variable = API_KEY_ENV.get(config.provider)
            if variable and credentials is not None:
                options = dict(config.provider_options.get(config.provider, {}))
                key = options.get("api_key") or credentials.get(variable)
                if not key:
                    raise TranslationError(f"{config.provider} requires {variable}; set it in .env or export it.")
                options["api_key"] = key
                config = replace(config, provider_options={**config.provider_options, config.provider: options})
            provider = get_translator(config.provider, config.provider_options)
            translated = provider.translate(original, config.source_lang, config.target_lang)
        except TranslationError as exc:
            return f"Translation error: {exc}", original, ""
        HistoryStore(limit=config.history_limit).add(original, translated)
        return "", original, translated
    except Exception as exc:  # noqa: BLE001 - Report worker failures in the popup.
        return f"Error: {exc}", original, ""


class OutputWindow(Gtk.Window):
    """The interactive portion of the same popup on another output."""

    def __init__(self, owner, monitor, x, y):
        super().__init__(application=owner.app, title="xpeek")
        self.owner = owner
        self.set_decorated(False)
        self.add_css_class("xpeek")
        self.set_default_size(owner.state.width, owner.state.height)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "xpeek")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.get_keyboard_mode(owner))
        LayerShell.set_monitor(self, monitor)
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
        owner._build_content(self)
        self.scroll_syncing = False
        self.owner_adjustment = owner.scroll.get_vadjustment()
        self.adjustment = self.scroll.get_vadjustment()
        self.scroll_handler = self.owner_adjustment.connect("value-changed", self._follow_scroll)
        self.local_scroll_handler = self.adjustment.connect("value-changed", self._scrolled)
        self.scroll_tick = self.add_tick_callback(self._scroll_frame)
        self.connect("close-request", lambda _window: owner._close_requested(_window))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", owner._key_pressed)
        self.add_controller(keys)
        self.move_to(x, y)
        self.present()

    def move_to(self, x, y):
        self.set_default_size(self.owner.state.width, self.owner.state.height)
        LayerShell.set_margin(self, LayerShell.Edge.LEFT, x)
        LayerShell.set_margin(self, LayerShell.Edge.TOP, y)

    def _follow_scroll(self, _adjustment):
        self.scroll_syncing = True
        try:
            self.adjustment.set_value(self.owner_adjustment.get_value())
        finally:
            self.scroll_syncing = False

    def _scrolled(self, adjustment):
        if self.scroll_syncing or not self.get_mapped():
            return
        # Ignore layout corrections while this view is still being allocated.
        if (abs(adjustment.get_upper() - self.owner_adjustment.get_upper()) < 1
                and abs(adjustment.get_page_size() - self.owner_adjustment.get_page_size()) < 1):
            self.owner_adjustment.set_value(adjustment.get_value())

    def _scroll_frame(self, _widget, _clock):
        self._follow_scroll(self.owner_adjustment)
        return GLib.SOURCE_CONTINUE

    def destroy(self):
        # Each TextView owns its adjustment. Sharing one also shares GTK's
        # widget-specific adjustment callbacks, which outlive a destroyed view.
        self.owner_adjustment.disconnect(self.scroll_handler)
        self.adjustment.disconnect(self.local_scroll_handler)
        self.remove_tick_callback(self.scroll_tick)
        super().destroy()


class OverlayWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, config: Config, mode: str, *, display_limit: int = DISPLAY_LIMIT):
        super().__init__(application=app, title="xpeek")
        self.app = app
        self.config = config
        remembered = WindowState.load()
        self.normal_height = remembered.height
        self.persistent_height = remembered.persistent_height
        self.persistent = config.persistent_window
        self.show_headings = self.persistent or mode == "last"
        self.preferred = replace(remembered, height=(
            self.persistent_height if self.persistent else self.normal_height))
        self.state = replace(self.preferred)
        self.closed = False
        self.remote = None
        self.remote_port = None
        self.remote_active = False
        self.remote_pairing = None
        self.remote_requests_box = None
        self.remote_requests_signature = None
        self.remote_qr_content = None
        self.pairing_restore_desktop = False
        self.remote_source = None
        self.remote_held = False
        self.entries = []
        self.rendered_entries = []
        self.jobs = {}
        self.capture_jobs = set()
        self.capture_cancelled = Event()
        self.scroll_source = None
        if not hasattr(app, "translation_executor"):
            # One worker keeps RapidOCR and history writes serialized, including
            # when a closed popup is reopened while its last request finishes.
            app.translation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xpeek-translation")
        self.save_source = None
        self.drag_origin = None
        self.output_windows = {}
        self.output_transfer = False
        self.drag_position = None
        self.drag_target = None
        self.drag_finish_source = None
        self.drag_cleanup_tick = None
        self.monitor = None
        self.monitor_handler = None
        self.layer = LayerShell is not None and LayerShell.is_supported()
        self.display = self.get_display()
        self.monitors = self.display.get_monitors()
        self.monitors_handler = self.monitors.connect("items-changed", self._outputs_changed)
        self.set_decorated(False)
        self.set_resizable(True)
        self.add_css_class("xpeek")
        self.set_default_size(self.state.width, self.state.height)

        if self.layer:
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, "xpeek")
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            # Use the full output coordinate space without reserving space.
            LayerShell.set_exclusive_zone(self, -1)
            keyboard = (LayerShell.KeyboardMode.ON_DEMAND
                        if LayerShell.get_protocol_version() >= 4
                        else LayerShell.KeyboardMode.NONE)
            LayerShell.set_keyboard_mode(self, keyboard)
            # Allow the compositor to choose on the first opening.
            self._select_saved_output()
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, self.state.x is None)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, self.state.x is not None)
            LayerShell.set_margin(self, LayerShell.Edge.TOP, self.state.y)
            edge = LayerShell.Edge.RIGHT if self.state.x is None else LayerShell.Edge.LEFT
            LayerShell.set_margin(self, edge, 24 if self.state.x is None else self.state.x)
        else:
            print("Layer-shell unavailable; using a normal GTK window. "
                  "The desktop controls its position.", file=sys.stderr)

        self._build_content()
        self.connect("map", self._mapped)
        self.connect("close-request", self._close_requested)
        self.add_tick_callback(self._tick)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)

        if mode == "last":
            entries = HistoryStore(limit=config.history_limit).all()[-display_limit:]
            self.entries = [DisplayEntry(entry.timestamp, entry.translation) for entry in entries]
            if not self.entries:
                self.entries.append(DisplayEntry("History", "No translation history."))
            self._render_entries()

    def _build_content(self, window=None):
        window = self if window is None else window
        grid = Gtk.Grid()
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("popup-header")
        handle = Gtk.Label(label="xpeek", xalign=0)
        handle.set_hexpand(True)
        handle.set_cursor_from_name("grab")
        self._add_drag(handle, "move")
        header.append(handle)
        remote = Gtk.Button(icon_name="web-browser-symbolic")
        remote.set_tooltip_text("Move translations to remote browser")
        remote.connect("clicked", lambda _button: self._pair_remote())
        header.append(remote)
        close = Gtk.Button(icon_name="window-close-symbolic")
        close.set_tooltip_text("Close")
        close.connect("clicked", lambda _button: self.dismiss("desktop close button"))
        header.append(close)
        body.append(header)

        text = Gtk.TextView(editable=False, cursor_visible=False,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR)
        for method in (text.set_left_margin, text.set_right_margin,
                       text.set_top_margin, text.set_bottom_margin):
            method(12)
        if window is self:
            self.buffer = text.get_buffer()
            self.heading_tag = self.buffer.create_tag("heading", foreground="#aaaaaa", scale=0.8)
            self.end_padding_tag = self.buffer.create_tag("end-padding")
            self.end_mark = self.buffer.create_mark("latest", self.buffer.get_end_iter(), False)
            self.text = text
        else:
            text.set_buffer(self.buffer)
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(text)
        if window is self:
            self.scroll = scroll
            self.padding_bounds = (0, 0)
            scroll.get_vadjustment().connect("changed", self._update_end_padding)
        else:
            window.scroll = scroll
        scrolling = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scrolling.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scrolling.connect("scroll", self._scroll_input)
        scroll.add_controller(scrolling)
        body.append(scroll)
        grid.attach(body, 1, 1, 1, 1)

        for edge, column, row in (
            ("nw", 0, 0), ("n", 1, 0), ("ne", 2, 0),
            ("w", 0, 1), ("e", 2, 1),
            ("sw", 0, 2), ("s", 1, 2), ("se", 2, 2),
        ):
            grip = Gtk.Box()
            grip.set_size_request(6, 6)
            grip.set_cursor_from_name(f"{edge}-resize")
            self._add_drag(grip, edge)
            grid.attach(grip, column, row, 1, 1)
        window.set_child(grid)
        window.set_focus(close)

    def _add_drag(self, widget, edge):
        gesture = Gtk.GestureDrag(button=1)
        gesture.connect("drag-begin", self._drag_begin, edge)
        gesture.connect("drag-update", self._drag_update, edge)
        gesture.connect("drag-end", self._drag_end)
        gesture.connect("cancel", self._drag_cancel)
        widget.add_controller(gesture)

    def _select_saved_output(self):
        monitors = list(self.monitors)
        if not monitors:
            return
        chosen = self._choose_monitor(monitors)
        initial = self.state.x is None
        self._place_on_monitor(chosen)
        if initial:
            self.state.x = None
        if self.preferred.output:
            self._watch_monitor(chosen)
            LayerShell.set_monitor(self, chosen)
        self.set_default_size(self.state.width, self.state.height)

    def _choose_monitor(self, monitors):
        preferred = next((m for m in monitors if m.get_connector() == self.preferred.output), None)
        if preferred is not None:
            return preferred
        old = self.preferred.output_layout.get(self.preferred.output)
        if old is None:
            return monitors[0]

        def distance(monitor):
            bounds = monitor.get_geometry()
            left, top, width, height = self.preferred.output_layout.get(
                monitor.get_connector(), (bounds.x, bounds.y, bounds.width, bounds.height))
            x, y, w, h = old
            dx = max(left - (x + w), x - (left + width), 0)
            dy = max(top - (y + h), y - (top + height), 0)
            centers = (2 * left + width - 2 * x - w) ** 2 + (2 * top + height - 2 * y - h) ** 2
            return dx * dx + dy * dy, centers

        return min(monitors, key=distance)

    def _place_on_monitor(self, monitor):
        # Always calculate from the user's placement, never a previous fallback.
        self.state = replace(self.preferred)
        bounds = monitor.get_geometry()
        output = monitor.get_connector()
        old = self.preferred.output_layout.get(self.preferred.output)
        if output != self.preferred.output and old is not None:
            new = self.preferred.output_layout.get(
                output, (bounds.x, bounds.y, bounds.width, bounds.height))
            self.state.relocate(old, new, (bounds.width, bounds.height))
        else:
            self.state.output = output
            self.state.fit_desktop(self._output_layout())
        self.state.output = output

    def _output_layout(self):
        return {
            monitor.get_connector(): (bounds.x, bounds.y, bounds.width, bounds.height)
            for monitor in self.monitors
            if monitor.get_connector() is not None
            and (bounds := monitor.get_geometry()).width > 0 and bounds.height > 0
        }

    def _watch_monitor(self, monitor):
        if self.monitor == monitor:
            return
        if self.monitor_handler is not None:
            self.monitor.disconnect(self.monitor_handler)
        self.monitor = monitor
        self.monitor_handler = monitor.connect("notify::geometry", self._monitor_changed)
        self._remember_outputs()

    def _remember_outputs(self):
        layout = self._output_layout()
        # Keep the old topology while the preferred monitor is disconnected.
        if self.preferred.output in layout:
            self.preferred.output_layout = layout

    def _mapped(self, _window):
        # wl_surface.enter arrives after ::map. GDK can still report the old
        # output here, including when restoring a saved window on another one.
        monitor = LayerShell.get_monitor(self) if self.layer else None
        if monitor is None:
            monitor = self.display.get_monitor_at_surface(self.get_surface())
        if monitor is not None:
            self._watch_monitor(monitor)
            bounds = monitor.get_geometry()
            if self.layer:
                self._place_on_monitor(monitor)
                if self.preferred.output is None:
                    self.preferred = replace(self.state)
                self._remember_outputs()
                self._apply_geometry()
                self._queue_save()
            else:
                self.set_default_size(min(self.state.width, bounds.width),
                                      min(self.state.height, bounds.height))

    def _monitor_changed(self, *_args):
        if self.closed or not self.layer:
            return
        bounds = self.monitor.get_geometry()
        if bounds.width <= 0 or bounds.height <= 0 or self.monitor not in list(self.monitors):
            return
        self._cancel_drag_for_layout()
        self._place_on_monitor(self.monitor)
        self._remember_outputs()
        self._apply_geometry()
        self._queue_save()

    def _outputs_changed(self, *_args):
        if self.closed or not self.layer:
            return
        self._cancel_drag_for_layout()
        monitors = list(self.monitors)
        if not monitors:
            return
        chosen = self._choose_monitor(monitors)
        changing_output = chosen != self.monitor
        self._place_on_monitor(chosen)
        self._watch_monitor(chosen)
        self._remember_outputs()
        self._apply_geometry()
        if changing_output:
            LayerShell.set_monitor(self, chosen)
        self._queue_save()

    def _cancel_drag_for_layout(self):
        if self.drag_cleanup_tick is not None:
            self.output_transfer = False
            self.remove_tick_callback(self.drag_cleanup_tick)
            self.drag_cleanup_tick = None
        if self.drag_finish_source is not None:
            GLib.source_remove(self.drag_finish_source)
            self.drag_finish_source = GLib.idle_add(self._finish_drag, None, None, None)
        elif self.drag_origin is not None:
            self._drag_cancel(None, None)

    def _apply_geometry(self):
        self.set_default_size(self.state.width, self.state.height)
        LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, False)
        LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
        LayerShell.set_margin(self, LayerShell.Edge.RIGHT, 0)
        LayerShell.set_margin(self, LayerShell.Edge.LEFT, self.state.x or 0)
        LayerShell.set_margin(self, LayerShell.Edge.TOP, self.state.y)
        self._sync_output_windows()

    def _sync_output_windows(self):
        if self.closed or self.remote_active or self.remote_pairing is not None or not self.layer or self.monitor is None:
            return
        home = self.monitor.get_geometry()
        x, y = home.x + (self.state.x or 0), home.y + self.state.y
        visible = set()
        for monitor in self.monitors:
            bounds = monitor.get_geometry()
            if (monitor != self.monitor and bounds.width > 0 and bounds.height > 0
                    and x < bounds.x + bounds.width and x + self.state.width > bounds.x
                    and y < bounds.y + bounds.height and y + self.state.height > bounds.y):
                visible.add(monitor)
                if monitor not in self.output_windows:
                    self.output_windows[monitor] = OutputWindow(self, monitor, x - bounds.x, y - bounds.y)
        for monitor, window in list(self.output_windows.items()):
            if (monitor in visible or self.drag_origin is not None
                    or (self.output_transfer and monitor == self.monitor)):
                bounds = monitor.get_geometry()
                window.move_to(x - bounds.x, y - bounds.y)
            else:
                self.output_windows.pop(monitor).destroy()

    def _drag_begin(self, gesture, x, y, edge):
        if not self.layer:
            event = gesture.get_current_event()
            surface = self.get_surface()
            if event is not None:
                # Event coordinates are surface-relative, unlike widget coordinates.
                _, sx, sy = event.get_position()
                if edge == "move":
                    surface.begin_move(event.get_device(), 1, sx, sy, event.get_time())
                else:
                    directions = {"n": "NORTH", "ne": "NORTH_EAST", "e": "EAST",
                                  "se": "SOUTH_EAST", "s": "SOUTH", "sw": "SOUTH_WEST",
                                  "w": "WEST", "nw": "NORTH_WEST"}
                    surface.begin_resize(getattr(Gdk.SurfaceEdge, directions[edge]),
                                         event.get_device(), 1, sx, sy, event.get_time())
            return
        self.drag_origin = replace(self.state)
        if self.drag_cleanup_tick is not None:
            self.remove_tick_callback(self.drag_cleanup_tick)
            self.drag_cleanup_tick = None
        self.output_transfer = False
        self.drag_position = None
        self.drag_target = None
        self.drag_edge = edge
        self.drag_moved = False
        bounds = self.monitor.get_geometry()
        self.drag_output_origin = (bounds.x, bounds.y)
        event = gesture.get_current_event() if gesture is not None else None
        self.drag_press = event.get_position()[1:] if event is not None else None
        self.drag_pointer = self.drag_press or (x, y)

    def _drag_update(self, gesture, dx, dy, edge):
        if self.drag_origin is None or not self.layer or self.monitor is None:
            return
        origin = self.drag_origin
        event = gesture.get_current_event() if gesture is not None else None
        if event is not None and self.drag_press is not None:
            _, sx, sy = event.get_position()
            # Resize grips move within the surface as its size changes.
            dx, dy = sx - self.drag_press[0], sy - self.drag_press[1]
        # Drag offsets are cumulative from the press. Always apply them to the
        # starting geometry; adding previous movement makes the popup run away.
        if edge == "move":
            self._move_drag(dx, dy)
            return
        else:
            ox, oy = self.drag_output_origin
            layout = self._output_layout()
            minimum_x = min(rect[0] for rect in layout.values())
            minimum_y = min(rect[1] for rect in layout.values())
            maximum_x = max(rect[0] + rect[2] for rect in layout.values())
            maximum_y = max(rect[1] + rect[3] for rect in layout.values())
            minimum_width = min(MIN_WIDTH, maximum_x - minimum_x)
            minimum_height = min(MIN_HEIGHT, maximum_y - minimum_y)
            left, top = ox + (origin.x or 0), oy + origin.y
            right, bottom = left + origin.width, top + origin.height
            if "w" in edge:
                left = max(minimum_x, min(round(left + dx), right - minimum_width))
            if "e" in edge:
                right = min(maximum_x, max(round(right + dx), left + minimum_width))
            if "n" in edge:
                top = max(minimum_y, min(round(top + dy), bottom - minimum_height))
            if "s" in edge:
                bottom = min(maximum_y, max(round(bottom + dy), top + minimum_height))
            self.state.x, self.state.y = left - ox, top - oy
            self.state.width, self.state.height = right - left, bottom - top
        self.state.fit_desktop(layout)
        self._apply_geometry()

    def _move_drag(self, dx, dy):
        origin = self.drag_origin
        ox, oy = self.drag_output_origin
        x = round(ox + (origin.x or 0) + dx)
        y = round(oy + origin.y + dy)
        px, py = x + self.drag_pointer[0], y + self.drag_pointer[1]
        self.drag_position = (x, y)
        self.drag_moved = self.drag_moved or abs(dx) >= 1 or abs(dy) >= 1
        self.drag_target = self.drag_target or self.monitor
        # Move the original surface on its output. Signed margins let it clip
        # naturally at the boundary without unmapping and losing the grab.
        self.state.x, self.state.y = x - ox, y - oy
        self._apply_geometry()
        targets = []
        for monitor in self.monitors:
            bounds = monitor.get_geometry()
            if bounds.width <= 0 or bounds.height <= 0:
                continue
            if (bounds.x <= px < bounds.x + bounds.width and
                    bounds.y <= py < bounds.y + bounds.height):
                targets.append(monitor)
        if targets and self.drag_target not in targets:
            self.drag_target = targets[0]

    def _clear_output_windows(self):
        if self.drag_cleanup_tick is not None:
            self.remove_tick_callback(self.drag_cleanup_tick)
            self.drag_cleanup_tick = None
        windows, self.output_windows = self.output_windows, {}
        self.output_transfer = False
        self.drag_position = None
        self.drag_target = None
        for window in windows.values():
            window.destroy()

    def _drop_frame(self, _widget, _clock):
        if not self.get_mapped() or self.display.get_monitor_at_surface(self.get_surface()) != self.monitor:
            return GLib.SOURCE_CONTINUE
        if not self.drag_drop_ready:
            self.drag_drop_ready = True
            return GLib.SOURCE_CONTINUE
        # Keep the destination portion through the first frame of the remapped
        # main surface, so releasing across outputs cannot expose an empty frame.
        self.drag_cleanup_tick = None
        self.output_transfer = False
        self._sync_output_windows()
        return GLib.SOURCE_REMOVE

    def _drag_end(self, _gesture, _dx, _dy):
        if self.drag_origin is None:
            return
        size = None
        if self.drag_edge != "move" and (
                self.state.width, self.state.height) != (self.drag_origin.width, self.drag_origin.height):
            size = (self.state.width, self.state.height)
        self.drag_origin = None
        # GTK still dispatches the release event after this callback. Remapping
        # or destroying surfaces here invalidates the widgets it is processing.
        self.drag_finish_source = GLib.idle_add(
            self._finish_drag, self.drag_position if self.drag_moved else None,
            self.drag_target, size)

    def _finish_drag(self, position, target, size):
        self.drag_finish_source = None
        if self.closed:
            return GLib.SOURCE_REMOVE
        if position is not None and target in list(self.monitors):
            bounds = target.get_geometry()
            self.state.x, self.state.y = position[0] - bounds.x, position[1] - bounds.y
            self.state.output = target.get_connector()
            self.state.fit_desktop(self._output_layout())
            self.preferred = replace(self.state)
            changing_output = target != self.monitor
            self.output_transfer = changing_output
            self._watch_monitor(target)
            self._remember_outputs()
            self._apply_geometry()
            # Remapping only after release leaves the pointer grab intact.
            if changing_output:
                LayerShell.set_monitor(self, target)
                self.drag_drop_ready = False
                self.drag_cleanup_tick = self.add_tick_callback(self._drop_frame)
        elif size is not None:
            self.preferred.width, self.preferred.height = size
        self.drag_position = None
        self.drag_target = None
        self._sync_output_windows()
        self._queue_save()
        return GLib.SOURCE_REMOVE

    def _drag_cancel(self, _gesture, _sequence):
        if self.drag_origin is None:
            return
        if self.drag_edge == "move":
            self.state = replace(self.drag_origin)
            self._apply_geometry()
        self.drag_origin = None
        self.drag_finish_source = GLib.idle_add(self._finish_drag, None, None, None)

    def _tick(self, _widget, _clock):
        if self.closed:
            return GLib.SOURCE_REMOVE
        if self.drag_origin is None and self.get_mapped():
            surface = self.get_surface()
            width, height = surface.get_width(), surface.get_height()
            if width > 0 and height > 0 and (width, height) != (self.state.width, self.state.height):
                self.state.width, self.state.height = width, height
                if self.layer:
                    self._sync_output_windows()
                self._queue_save()
        return GLib.SOURCE_CONTINUE

    def _queue_save(self):
        if self.save_source is not None:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(200, self._save_geometry)

    def _save_geometry(self):
        self.save_source = None
        if self.get_mapped():
            surface = self.get_surface()
            self.state.width = surface.get_width()
            self.state.height = surface.get_height()
        if not self.layer:
            self.preferred.width, self.preferred.height = self.state.width, self.state.height
        if self.persistent:
            replace(self.preferred, height=self.normal_height, persistent_height=self.preferred.height).save()
        else:
            replace(self.preferred, persistent_height=self.persistent_height).save()
        return GLib.SOURCE_REMOVE

    def enable_persistent(self):
        if self.persistent:
            return
        self._cancel_drag_for_layout()
        self.normal_height = self.preferred.height
        self.preferred.height = self.persistent_height
        self.persistent = True
        self.show_headings = True
        if self.layer:
            self._place_on_monitor(self.monitor)
            self._apply_geometry()
        else:
            self.state.height = self.persistent_height
            self.set_default_size(self.state.width, self.state.height)
        self._queue_save()
        self._render_entries()

    def disable_persistent(self):
        if not self.persistent:
            return
        self._cancel_drag_for_layout()
        self.persistent_height = self.preferred.height
        self.preferred.height = self.normal_height
        self.persistent = False
        if self.layer:
            self._place_on_monitor(self.monitor)
            self._apply_geometry()
        else:
            self.state.height = self.normal_height
            self.set_default_size(self.state.width, self.state.height)
        self._queue_save()
        self._publish_remote()

    def reset_size(self):
        self._cancel_drag_for_layout()
        if self.drag_finish_source is not None:
            GLib.source_remove(self.drag_finish_source)
            self.drag_finish_source = None
        self.drag_position = None
        self.drag_target = None
        if self.save_source is not None:
            GLib.source_remove(self.save_source)
            self.save_source = None
        saved = replace(self.preferred)
        saved.reset_size()
        self.normal_height = saved.height
        self.persistent_height = saved.persistent_height
        self.preferred = replace(saved, height=(saved.persistent_height if self.persistent else saved.height))
        self.state.width, self.state.height = self.preferred.width, self.preferred.height
        if self.layer:
            self._apply_geometry()
        else:
            self.set_default_size(self.state.width, self.state.height)
        return saved.save()

    def _pair_remote(self):
        if self.remote_pairing is not None:
            self.remote_pairing.present()
            return self.remote is not None and self.remote.thread.is_alive()
        dialog = Gtk.Window(title="Connect a browser", transient_for=self, application=self.app)
        dialog.set_default_size(380, 540)
        dialog.add_css_class("xpeek")
        if self.layer:
            LayerShell.init_for_window(dialog)
            LayerShell.set_namespace(dialog, "xpeek-pairing")
            LayerShell.set_layer(dialog, LayerShell.Layer.OVERLAY)
            LayerShell.set_exclusive_zone(dialog, -1)
            keyboard = (LayerShell.KeyboardMode.ON_DEMAND
                        if LayerShell.get_protocol_version() >= 4
                        else LayerShell.KeyboardMode.NONE)
            LayerShell.set_keyboard_mode(dialog, keyboard)
            monitor = self.monitor or LayerShell.get_monitor(self)
            if monitor is not None and monitor in list(self.monitors):
                LayerShell.set_monitor(dialog, monitor)
                dialog.set_default_size(380, min(540, max(240, monitor.get_geometry().height - 48)))
            dialog.set_decorated(False)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        dialog.set_child(scroll)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for method in (box.set_margin_top, box.set_margin_bottom, box.set_margin_start, box.set_margin_end):
            method(16)
        scroll.set_child(box)
        self.pairing_restore_desktop = self.get_visible()
        self.remote_pairing = dialog
        self.remote_requests_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.remote_requests_signature = None
        box.append(self.remote_requests_box)
        qr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.remote_qr_content = qr_box
        box.append(qr_box)
        success = False
        try:
            import qrcode

            from .remote import RemoteServer
            if self.remote is None:
                self.remote = RemoteServer() if self.remote_port is None else RemoteServer(self.remote_port)
                self.remote_source = GLib.timeout_add(250, self._remote_tick)
                self._publish_remote()
            with self.remote.lock:
                connected = time.monotonic() - self.remote.last_seen < 10
            if connected and not self.remote.pending_requests():
                self._set_remote_output(True)
                return True
            links = self.remote.links()
            if not links:
                raise RuntimeError("No local IPv4 address found. Connect the desktop to your local network and retry.")
            label = Gtk.Label(label="Open the address in another browser, or scan the QR.\nApprove the matching code here to connect.")
            label.set_wrap(True)
            qr_box.append(label)
            image = qrcode.make(links[0])
            data = io.BytesIO()
            image.save(data, format="PNG")
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data.getvalue()))
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_size_request(260, 260)
            qr_box.append(picture)
            for link in links:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                address = Gtk.Entry()
                address.set_text(link)
                address.set_editable(False)
                address.set_width_chars(12)
                address.set_hexpand(True)
                row.append(address)
                copy = Gtk.Button(icon_name="edit-copy-symbolic")
                copy.set_tooltip_text("Copy pairing link")
                copy.set_valign(Gtk.Align.CENTER)
                copy.connect("clicked", lambda _button, url=link: self.get_clipboard().set(url))
                row.append(copy)
                qr_box.append(row)
            hint = Gtk.Label(label="If connection fails, check that your network allows devices to reach each other and the displayed TCP port is allowed by your firewall.")
            hint.set_wrap(True)
            qr_box.append(hint)
            success = True
        except Exception as exc:  # noqa: BLE001 - Keep the desktop usable when pairing fails.
            remote_notice(f"pairing failed: {exc}")
            label = Gtk.Label(label=f"Cannot open remote pairing: {exc}")
            label.set_wrap(True)
            qr_box.append(label)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self._cancel_pairing())
        box.append(cancel)
        dialog.set_focus(cancel)
        dialog.connect("close-request", lambda _dialog: self._cancel_pairing())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _controller, key, _code, _modifiers: key == Gdk.KEY_Escape and self._cancel_pairing())
        dialog.add_controller(keys)
        self._refresh_pairing_requests()
        self._cancel_drag_for_layout()
        self._clear_output_windows()
        self.set_visible(False)
        dialog.present()
        return success

    def _refresh_pairing_requests(self):
        if self.remote is None or self.remote_requests_box is None:
            return
        requests = self.remote.pending_requests()
        signature = tuple(request["id"] for request in requests)
        if signature == self.remote_requests_signature:
            return
        self.remote_requests_signature = signature
        container = self.remote_requests_box
        while (child := container.get_first_child()) is not None:
            container.remove(child)
        container.set_visible(bool(requests))
        self.remote_qr_content.set_visible(not requests)
        for request in requests:
            label = Gtk.Label(label=f"{request['label']} requests pairing.\nConfirm code {request['code']} matches your browser.", xalign=0)
            label.set_wrap(True)
            container.append(label)
            address = Gtk.Label(label=request["address"], xalign=0)
            address.add_css_class("pairing-address")
            container.append(address)
            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            for title, allowed in (("Approve", True), ("Reject", False)):
                button = Gtk.Button(label=title)
                button.connect("clicked", lambda _button, ticket=request["id"], approve=allowed: self._decide_remote_pairing(ticket, approve))
                buttons.append(button)
            container.append(buttons)

    def _decide_remote_pairing(self, ticket, approve):
        if self.remote is None:
            return
        if self.remote.decide_pairing(ticket, approve) and approve:
            self._set_remote_output(True)
        else:
            self._refresh_pairing_requests()

    def _cancel_pairing(self):
        if self.remote is not None:
            for request in self.remote.pending_requests():
                self.remote.decide_pairing(request["id"], False)
        restore = self.pairing_restore_desktop
        self._close_pairing()
        if not self.closed and not self.get_visible() and not self.remote_active:
            if restore or self.entries or self.capture_jobs:
                self.present()
                self._sync_output_windows()
            else:
                self.dismiss("pairing canceled")
        return True

    def _close_pairing(self):
        dialog, self.remote_pairing = self.remote_pairing, None
        self.pairing_restore_desktop = False
        self.remote_requests_box = None
        self.remote_requests_signature = None
        self.remote_qr_content = None
        if dialog is not None:
            dialog.destroy()
        return True

    def _set_remote_output(self, active):
        if not active and not self.remote_active and self.remote_pairing is not None:
            self._cancel_pairing()
            return
        self._close_pairing()
        if active == self.remote_active:
            return
        self.remote_active = active
        if active:
            self.app.hold()
            self.remote_held = True
            with self.remote.lock:
                self.remote.last_seen = time.monotonic()
            self._clear_output_windows()
            self.set_visible(False)
        else:
            self.present()
            self._sync_output_windows()
            if self.remote_held:
                self.remote_held = False
                self.app.release()
        self._publish_remote()

    def _publish_remote(self):
        if self.remote is None or self.closed:
            return
        entries = (self.entries if self.persistent else self.entries[-1:]) if self.remote_active else []
        self.remote.publish({"remote": self.remote_active, "bottom_spacing_percent": self.config.remote_bottom_spacing_percent, "entries": [
            {"heading": entry.heading, "text": entry.text} for entry in entries]})

    def _remote_tick(self):
        if self.closed or self.remote is None:
            return GLib.SOURCE_REMOVE
        while True:
            try:
                action = self.remote.actions.get_nowait()
            except Empty:
                break
            if action == "activate" and self.remote_pairing is not None:
                self._set_remote_output(True)
            elif action == "desktop":
                self._set_remote_output(False)
            elif action == "end":
                self.dismiss("remote End session")
                return GLib.SOURCE_REMOVE
        if self.remote.pending_requests() and self.remote_pairing is None:
            self._pair_remote()
        self._refresh_pairing_requests()
        with self.remote.lock:
            expired = time.monotonic() - self.remote.last_seen > 15
        if self.remote_active and expired:
            self._set_remote_output(False)
        return GLib.SOURCE_CONTINUE

    def submit_translation(self, config, image, credentials=None):
        heading = f"{datetime.now().astimezone():%H:%M:%S} · {config.provider} · {config.source_lang} → {config.target_lang}"
        entry = DisplayEntry(heading, "…")
        if self.remote_active and not self.persistent:
            self.entries = []
        self.entries.append(entry)
        self.entries = self.entries[-DISPLAY_LIMIT:]
        self._render_entries()
        self.app.ocr_started = True
        # GTK windows and active jobs hold the application. GApplication starts
        # this countdown only after their final release, and reopening cancels it.
        self.app.set_inactivity_timeout(config.ocr_keepalive_seconds * 1000)
        self.app.hold()
        future = self.app.translation_executor.submit(translation_result, config, image, credentials)
        self.jobs[future] = image
        future.add_done_callback(lambda done: GLib.idle_add(self._translation_finished, entry, done))

    def _update_end_padding(self, adjustment):
        height = adjustment.get_page_size()
        if height <= 0:
            return
        upper, page_size = self.padding_bounds
        follow = adjustment.get_value() + page_size >= upper - 5
        self.padding_bounds = (adjustment.get_upper(), height)
        _, line_height = self.text.create_pango_layout("Ag").get_pixel_size()
        usable_height = height - self.text.get_top_margin() - self.text.get_bottom_margin()
        compact = usable_height < 6 * line_height
        if self.end_padding_tag.get_property("invisible") != compact:
            self.end_padding_tag.set_property("invisible", compact)
            if follow:
                self._queue_scroll_latest()

    def _render_entries(self):
        self._publish_remote()
        adjustment = self.scroll.get_vadjustment()
        follow = self.scroll_source is not None or (
            adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 5)
        self.buffer.remove_tag(self.end_padding_tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())
        for entry in self.rendered_entries:
            if entry not in self.entries:
                self.buffer.delete(self.buffer.get_iter_at_mark(entry.start), self.buffer.get_iter_at_mark(entry.end))
                for mark in (entry.start, entry.body_start, entry.body_end, entry.end):
                    self.buffer.delete_mark(mark)
        for entry in self.entries:
            if entry.start is None:
                entry.start = self.buffer.create_mark(None, self.buffer.get_end_iter(), True)
                if self.show_headings:
                    self.buffer.insert_with_tags(self.buffer.get_end_iter(), entry.heading + "\n", self.heading_tag)
                    entry.has_heading = True
                entry.body_start = self.buffer.create_mark(None, self.buffer.get_end_iter(), True)
                self.buffer.insert(self.buffer.get_end_iter(), entry.text)
                entry.body_end = self.buffer.create_mark(None, self.buffer.get_end_iter(), True)
                self.buffer.insert(self.buffer.get_end_iter(), "\n\n")
                entry.end = self.buffer.create_mark(None, self.buffer.get_end_iter(), True)
            else:
                if self.show_headings and not entry.has_heading:
                    start = self.buffer.get_iter_at_mark(entry.start).get_offset()
                    self.buffer.insert_with_tags(self.buffer.get_iter_at_offset(start), entry.heading + "\n", self.heading_tag)
                    self.buffer.move_mark(entry.body_start, self.buffer.get_iter_at_offset(start + len(entry.heading) + 1))
                    entry.has_heading = True
                if entry.text != entry.rendered_text:
                    start = self.buffer.get_iter_at_mark(entry.body_start).get_offset()
                    self.buffer.delete(self.buffer.get_iter_at_mark(entry.body_start), self.buffer.get_iter_at_mark(entry.body_end))
                    self.buffer.insert(self.buffer.get_iter_at_offset(start), entry.text)
                    self.buffer.move_mark(entry.body_end, self.buffer.get_iter_at_offset(start + len(entry.text)))
            entry.rendered_text = entry.text
        self.rendered_entries = self.entries.copy()
        if self.entries:
            # Keep entry separators visible; only the final breathing room yields to a short viewport.
            self.buffer.apply_tag(self.end_padding_tag,
                                  self.buffer.get_iter_at_mark(self.entries[-1].body_end),
                                  self.buffer.get_end_iter())
        self.buffer.move_mark(self.end_mark, self.buffer.get_end_iter())
        if follow:
            self._queue_scroll_latest()

    def _queue_scroll_latest(self):
        self.scroll_frames = 0
        if self.scroll_source is None:
            self.scroll_source = self.add_tick_callback(self._scroll_latest)

    def _scroll_latest(self, _widget, _clock):
        # Keep following through asynchronous TextView layout. Scrolling by the
        # user cancels this callback and native GTK viewport anchoring takes over.
        self.text.scroll_to_mark(self.end_mark, 0, True, 0, 1)
        adjustment = self.scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        self.scroll_frames += 1
        if self.scroll_frames < 3:
            return GLib.SOURCE_CONTINUE
        self.scroll_source = None
        return GLib.SOURCE_REMOVE

    def _scroll_input(self, _controller, _dx, _dy):
        # Native scrolling wins over a pending automatic scroll after appending.
        if self.scroll_source is not None:
            self.remove_tick_callback(self.scroll_source)
            self.scroll_source = None
        return False

    def _translation_finished(self, entry, future):
        self.jobs.pop(future, None)
        try:
            error, original, translated = future.result()
        except CancelledError:
            error, original, translated = "", "", ""
        except Exception as exc:  # noqa: BLE001 - Always release the application's worker hold.
            error, original, translated = str(exc), "", ""
        if not self.closed and entry in self.entries:
            entry.text = f"Error:\n{error}" if error else translated if original else "Could not detect any text."
            self._render_entries()
        self.app.release()
        return GLib.SOURCE_REMOVE

    def _key_pressed(self, _controller, key, _keycode, _modifiers):
        if key == Gdk.KEY_Escape:
            self.dismiss("desktop Escape")
            return True
        return False

    def _close_requested(self, _window):
        self.dismiss("desktop close request")
        return True

    def dismiss(self, reason="session closed"):
        if self.closed:
            return
        self.closed = True
        self._close_pairing()
        if self.remote_source is not None:
            GLib.source_remove(self.remote_source)
            self.remote_source = None
        if self.remote is not None:
            self.remote.close(reason)
            self.remote = None
        if self.remote_held:
            self.remote_held = False
            self.app.release()
        self.capture_cancelled.set()
        for future in list(self.capture_jobs):
            future.cancel()
        for future, image in list(self.jobs.items()):
            if future.cancel():
                image.unlink(missing_ok=True)
        if self.scroll_source is not None:
            self.remove_tick_callback(self.scroll_source)
            self.scroll_source = None
        self.drag_origin = None
        if self.drag_finish_source is not None:
            GLib.source_remove(self.drag_finish_source)
            self.drag_finish_source = None
        self._clear_output_windows()
        if self.save_source is not None:
            GLib.source_remove(self.save_source)
        self._save_geometry()
        self.monitors.disconnect(self.monitors_handler)
        if self.monitor_handler is not None:
            self.monitor.disconnect(self.monitor_handler)
        self.destroy()


def _run_gui_translation(request: dict, *, on_ready=None) -> int:
    initialized = Gtk.init_check()
    display = Gdk.Display.get_default() or Gdk.Display.open(None)
    if not initialized or display is None:
        print("Cannot open the popup: no accessible Wayland display.", file=sys.stderr)
        return 1
    app = Gtk.Application(application_id=APPLICATION_ID,
                          flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    GLib.set_application_name("xpeek")
    sources = []
    window = None
    local_exit_status = None
    notified = False

    def notify(status):
        nonlocal notified
        if on_ready is not None and not notified:
            notified = True
            on_ready(status)

    app.capture_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xpeek-capture")
    app.ocr_started = False
    app.stopping = False

    def print_error(command, message):
        printer = getattr(command, "printerr_literal", None)
        if printer is not None:
            printer(message)
        else:
            print(message, end="", file=sys.stderr)

    def capture_finished(target, current, request, command, future):
        nonlocal local_exit_status
        image = None
        status = 0
        target.capture_jobs.discard(future)
        try:
            result = future.result()
            image = result.image
            if not target.closed:
                if result.warning:
                    print_error(command, f"Warning: {result.warning}\n")
                target.submit_translation(current, image, request["credentials"])
                image = None  # The translation worker owns this image now.
                if not target.get_visible() and not target.remote_active and target.remote_pairing is None:
                    target.present()
        except (CancelledError, CaptureCancelled):
            pass
        except Exception as exc:  # noqa: BLE001 - Return failures to the requesting CLI.
            status = 1
            print_error(command, f"Error: {exc}\n")
            if not target.closed and not target.entries and not target.capture_jobs and target.remote is None:
                target.dismiss()
        finally:
            if image is not None:
                image.unlink(missing_ok=True)
            command.set_exit_status(status)
            if not command.get_is_remote():
                # GApplication.run() retains the initial handler's synchronous
                # status for the primary process; preserve its deferred result.
                local_exit_status = status
                notify(status)
            done = getattr(command, "done", None)
            if done is not None:
                done()
            # Older GLib completes the command when this retained reference is released.
            app.release()
        return GLib.SOURCE_REMOVE

    def startup(application):
        def shutdown(_action, _parameter):
            application.stopping = True
            application.set_inactivity_timeout(0)
            # Replace an existing idle countdown, while allowing outstanding
            # job callbacks to clean up captures and release their holds.
            application.hold()
            try:
                if window is not None:
                    window.dismiss("shutdown request")
            finally:
                application.release()

        action = Gio.SimpleAction.new("shutdown", None)
        action.connect("activate", shutdown)
        application.add_action(action)
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            window.xpeek { background: #101010; color: #ffffff; border: 1px solid #363636; }
            .xpeek textview, .xpeek textview text { background: #101010; color: #ffffff;
                font-family: sans-serif; font-size: 18px; }
            .xpeek .popup-header { padding: 2px 6px; }
            .xpeek .pairing-address { color: #aaaaaa; font-size: 0.85em; }
            .xpeek button { background: transparent; color: #ffffff; border: none;
                box-shadow: none; min-width: 20px; min-height: 20px; padding: 2px; }
            .xpeek button:hover { background: #303030; }
        """)
        Gtk.StyleContext.add_provider_for_display(display, css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        def dismiss():
            if window is not None:
                window.dismiss("desktop dismiss signal")
            return GLib.SOURCE_CONTINUE

        for sig in (signal.SIGUSR1, signal.SIGUSR2):
            sources.append(GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, dismiss))

    def handle_command_line(application, command):
        nonlocal window, local_exit_status
        try:
            if application.stopping:
                raise RuntimeError("The popup is stopping for an update; retry after the update finishes.")
            if application.get_dbus_connection() is None:
                raise RuntimeError("No accessible session D-Bus; cannot manage a single popup.")
            request = json.loads(command.get_arguments()[1])
            if request["mode"] == "reset-size":
                success = window.reset_size() if window is not None and not window.closed else reset_saved_size()
                return 0 if success else 1
            requested = request["persistent"]
            current = Config(**request["config"])
            if application.ocr_started:
                application.set_inactivity_timeout(current.ocr_keepalive_seconds * 1000)
            if window is not None and window.closed:
                window = None
            if window is not None:
                window.config = replace(window.config, remote_bottom_spacing_percent=current.remote_bottom_spacing_percent)
                window._publish_remote()
            if request["mode"] == "remote":
                if window is None:
                    window = OverlayWindow(application, current, "empty")
                port = request.get("remote_port")
                if port is not None and window.remote is not None and window.remote.http.server_port != port:
                    raise RuntimeError(f"This session is listening on TCP {window.remote.http.server_port}; end the session before changing ports.")
                window.remote_port = port
                status = 0 if window._pair_remote() else 1
                command.set_exit_status(status)
                if not command.get_is_remote():
                    local_exit_status = status
                return status
            append = requested if requested is not None else (
                window.persistent if window is not None else request["config"]["persistent_window"])
            # Decide whether to close before selecting or capturing any region.
            if window is not None and (request["mode"] == "last" or (not append and not window.remote_active and window.remote_pairing is None)):
                window.dismiss("history toggle" if request["mode"] == "last" else "translation toggle")
                return 0
            if request["mode"] != "last":
                selected = select_region() if request["temporary"] else (
                    Region(**request["region"]) if request["region"] else None)
                if selected is None:
                    raise CaptureError("No region has been saved yet. Run `xpeek select` first.")
            if window is None:
                current = replace(current, persistent_window=append)
                window = OverlayWindow(application, current, "last" if request["mode"] == "last" else "empty",
                                       display_limit=request.get("display_limit", DISPLAY_LIMIT))
                if request["mode"] == "last":
                    window.present()
            elif append:
                window.enable_persistent()
            elif window.remote_active:
                window.disable_persistent()
            if request["mode"] != "last":
                # A separate worker captures every request before queued OCR work.
                target = window
                application.hold()
                try:
                    future = app.capture_executor.submit(
                        capture_screen, selected, current.capture_backend,
                        display=request.get("gamescope_display"), cancelled=target.capture_cancelled,
                    )
                except Exception:
                    application.release()
                    raise
                command.xpeek_capture_pending = True
                target.capture_jobs.add(future)
                future.add_done_callback(lambda completed: GLib.idle_add(
                    capture_finished, target, current, request, command, completed))
            return 0
        except Exception as exc:  # noqa: BLE001 - Return errors to the invoking CLI over D-Bus.
            message = f"Error: {exc}\n"
            print_error(command, message)
            if window is None:
                application.quit()
            elif not window.get_visible() and not window.entries and not window.capture_jobs and window.remote is None:
                window.dismiss()
            command.set_exit_status(1)
            if not command.get_is_remote():
                local_exit_status = 1
            return 1

    def command_line(application, command):
        status = handle_command_line(application, command)
        if not command.get_is_remote() and not getattr(command, "xpeek_capture_pending", False):
            notify(status)
            if status and on_ready is not None:
                if window is not None:
                    window.dismiss("startup failed")
                application.quit()
        return status

    app.connect("startup", startup)
    app.connect("command-line", command_line)
    try:
        # GApplication forwards the in-memory request; credentials never enter OS argv.
        status = app.run(["xpeek", json.dumps(request)])
        status = status if local_exit_status is None else local_exit_status
        notify(status)
        return status
    finally:
        if window is not None and not window.closed:
            window.dismiss("application exiting")
        for source in sources:
            GLib.source_remove(source)
        app.capture_executor.shutdown(wait=True)
        executor = getattr(app, "translation_executor", None)
        if executor is not None:
            executor.shutdown(wait=True)
        shutdown_backends()

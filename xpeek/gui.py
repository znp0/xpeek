"""GTK4 translation popup, using layer-shell where available."""
from __future__ import annotations

from ctypes import CDLL
from dataclasses import replace
import os
import signal
import sys
from threading import Thread

# Load layer-shell before GTK/libwayland, as required by its Python bindings.
try:
    _layer_library = CDLL("libgtk4-layer-shell.so.0")
except OSError:
    _layer_library = None

os.environ.setdefault("GDK_BACKEND", "wayland")
try:
    import cairo
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

from .capture import CaptureError, capture_region
from .config import Config
from .history import HistoryStore
from .ocr import OcrError, extract_text
from .providers import TranslationError, get_translator
from .window_state import MIN_HEIGHT, MIN_WIDTH, WindowState


def translation_result(config: Config) -> tuple[str, str, str]:
    """Perform blocking work without accessing GTK objects."""
    original = ""
    try:
        try:
            image = capture_region(config.region)
        except CaptureError as exc:
            return f"Capture error: {exc}", "", ""
        try:
            original = extract_text(image)
        except OcrError as exc:
            return f"OCR error: {exc}", "", ""
        finally:
            image.unlink(missing_ok=True)
        # Game dialog lines often belong to one sentence.
        original = original.replace("\n", " ").strip()
        if not original:
            return "", "", ""
        try:
            provider = get_translator(config.provider, config.provider_options)
            translated = provider.translate(original, config.source_lang, config.target_lang)
        except TranslationError as exc:
            return f"Translation error: {exc}", original, ""
        HistoryStore(limit=config.history_limit).add(original, translated)
        return "", original, translated
    except Exception as exc:
        return f"Error: {exc}", original, ""


class DragPreview(Gtk.Window):
    """Draw the moving popup on one output without taking pointer input."""

    def __init__(self, owner, monitor, x, y):
        super().__init__(application=owner.app, title="xpeek")
        self.set_decorated(False)
        self.set_focusable(False)
        self.add_css_class("xpeek")
        self.set_default_size(owner.drag_origin.width, owner.drag_origin.height)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "xpeek-drag")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        LayerShell.set_monitor(self, monitor)
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
        self.set_child(Gtk.Picture(
            paintable=Gtk.WidgetPaintable.new(owner.get_child()), can_shrink=True))
        self.connect("realize", self._ignore_pointer)
        self.connect("map", self._ignore_pointer)
        self.move_to(x, y)
        self.present()

    def _ignore_pointer(self, _window):
        self.get_surface().set_input_region(cairo.Region())

    def move_to(self, x, y):
        # Negative margins let the preview straddle a monitor boundary.
        LayerShell.set_margin(self, LayerShell.Edge.LEFT, x)
        LayerShell.set_margin(self, LayerShell.Edge.TOP, y)


class OverlayWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, config: Config, mode: str):
        super().__init__(application=app, title="xpeek")
        self.app = app
        self.config = config
        self.preferred = WindowState.load()
        self.state = replace(self.preferred)
        self.closed = False
        self.worker = None
        self.save_source = None
        self.drag_origin = None
        self.drag_previews = {}
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
            entries = HistoryStore(limit=config.history_limit).all()
            self.buffer.set_text(entries[-1].translation if entries else "No translation history.")
        else:
            self.buffer.set_text("…")
            self.app.hold()
            self.worker = Thread(target=self._translate, name="xpeek-translation")
            self.worker.start()

    def _build_content(self):
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
        close = Gtk.Button(icon_name="window-close-symbolic")
        close.set_tooltip_text("Close")
        close.connect("clicked", lambda _button: self.dismiss())
        header.append(close)
        body.append(header)

        text = Gtk.TextView(editable=False, cursor_visible=False,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR)
        for method in (text.set_left_margin, text.set_right_margin,
                       text.set_top_margin, text.set_bottom_margin):
            method(12)
        self.buffer = text.get_buffer()
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(text)
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
        self.set_child(grid)

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
            self.state.clamp(bounds.width, bounds.height)
        self.state.output = output

    def _watch_monitor(self, monitor):
        if self.monitor == monitor:
            return
        if self.monitor_handler is not None:
            self.monitor.disconnect(self.monitor_handler)
        self.monitor = monitor
        self.monitor_handler = monitor.connect("notify::geometry", self._monitor_changed)
        self._remember_outputs()

    def _remember_outputs(self):
        layout = {
            monitor.get_connector(): (bounds.x, bounds.y, bounds.width, bounds.height)
            for monitor in self.monitors
            if monitor.get_connector() is not None
            and (bounds := monitor.get_geometry()).width > 0 and bounds.height > 0
        }
        # Keep the old topology while the preferred monitor is disconnected.
        if self.preferred.output in layout:
            self.preferred.output_layout = layout

    def _mapped(self, _window):
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
            self._clear_drag_previews()
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
        self._clear_drag_previews()
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
        bounds = self.monitor.get_geometry()
        if edge == "move":
            self._move_drag(dx, dy)
            return
        else:
            left, top = origin.x or 0, origin.y
            right, bottom = left + origin.width, top + origin.height
            if "w" in edge:
                left = max(0, min(round(left + dx), right - min(MIN_WIDTH, bounds.width)))
            if "e" in edge:
                right = min(bounds.width, max(round(right + dx), left + min(MIN_WIDTH, bounds.width)))
            if "n" in edge:
                top = max(0, min(round(top + dy), bottom - min(MIN_HEIGHT, bounds.height)))
            if "s" in edge:
                bottom = min(bounds.height, max(round(bottom + dy), top + min(MIN_HEIGHT, bounds.height)))
            self.state.x, self.state.y = left, top
            self.state.width, self.state.height = right - left, bottom - top
        self.state.clamp(bounds.width, bounds.height)
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
        visible = set()
        targets = []
        for monitor in self.monitors:
            bounds = monitor.get_geometry()
            if bounds.width <= 0 or bounds.height <= 0:
                continue
            if (bounds.x <= px < bounds.x + bounds.width and
                    bounds.y <= py < bounds.y + bounds.height):
                targets.append(monitor)
            if (monitor != self.monitor and
                    x < bounds.x + bounds.width and x + origin.width > bounds.x and
                    y < bounds.y + bounds.height and y + origin.height > bounds.y):
                visible.add(monitor)
                left, top = x - bounds.x, y - bounds.y
                if monitor in self.drag_previews:
                    self.drag_previews[monitor].move_to(left, top)
                else:
                    self.drag_previews[monitor] = DragPreview(self, monitor, left, top)
        if targets and self.drag_target not in targets:
            self.drag_target = targets[0]
        for monitor in list(self.drag_previews):
            if monitor not in visible:
                self.drag_previews.pop(monitor).destroy()

    def _clear_drag_previews(self):
        if self.drag_cleanup_tick is not None:
            self.remove_tick_callback(self.drag_cleanup_tick)
            self.drag_cleanup_tick = None
        previews, self.drag_previews = self.drag_previews, {}
        self.drag_position = None
        self.drag_target = None
        for preview in previews.values():
            preview.destroy()

    def _drop_frame(self, _widget, _clock):
        if not self.get_mapped() or self.display.get_monitor_at_surface(self.get_surface()) != self.monitor:
            return GLib.SOURCE_CONTINUE
        if not self.drag_drop_ready:
            self.drag_drop_ready = True
            return GLib.SOURCE_CONTINUE
        # Keep the destination preview through the first frame of the remapped
        # original, so releasing across outputs cannot expose an empty frame.
        self.drag_cleanup_tick = None
        self._clear_drag_previews()
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
            self.state.clamp(bounds.width, bounds.height)
            self.state.output = target.get_connector()
            self.preferred = replace(self.state)
            changing_output = target != self.monitor
            self._watch_monitor(target)
            self._remember_outputs()
            self._apply_geometry()
            # Remapping only after release leaves the pointer grab intact.
            if changing_output:
                # Cover the final placement until the original has painted on
                # its new output. Ordinary moves keep the same visible surface.
                preview = self.drag_previews.get(target)
                if preview is not None:
                    preview.set_default_size(self.state.width, self.state.height)
                    preview.move_to(self.state.x, self.state.y)
                for monitor in list(self.drag_previews):
                    if monitor != target:
                        self.drag_previews.pop(monitor).destroy()
                LayerShell.set_monitor(self, target)
                self.drag_drop_ready = False
                self.drag_cleanup_tick = self.add_tick_callback(self._drop_frame)
            else:
                self._clear_drag_previews()
        elif size is not None:
            self.preferred.width, self.preferred.height = size
            self._clear_drag_previews()
        else:
            self._clear_drag_previews()
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
        self.preferred.save()
        return GLib.SOURCE_REMOVE

    def _translate(self):
        result = translation_result(self.config)
        GLib.idle_add(self._translation_finished, *result)

    def _translation_finished(self, error, original, translated):
        if not self.closed:
            self.buffer.set_text(f"Error:\n{error}" if error else
                                 translated if original else "Could not detect any text.")
        self.app.release()
        return GLib.SOURCE_REMOVE

    def _key_pressed(self, _controller, key, _keycode, _modifiers):
        if key == Gdk.KEY_Escape:
            self.dismiss()
            return True
        return False

    def _close_requested(self, _window):
        self.dismiss()
        return True

    def dismiss(self):
        if self.closed:
            return
        self.closed = True
        self.drag_origin = None
        if self.drag_finish_source is not None:
            GLib.source_remove(self.drag_finish_source)
            self.drag_finish_source = None
        self._clear_drag_previews()
        if self.save_source is not None:
            GLib.source_remove(self.save_source)
        self._save_geometry()
        self.monitors.disconnect(self.monitors_handler)
        if self.monitor_handler is not None:
            self.monitor.disconnect(self.monitor_handler)
        self.destroy()


def run_gui_translation(config: Config, mode: str = "translate") -> int:
    initialized = Gtk.init_check()
    display = Gdk.Display.get_default() or Gdk.Display.open(None)
    if not initialized or display is None:
        print("Cannot open the popup: no accessible Wayland display.", file=sys.stderr)
        return 1
    app = Gtk.Application(application_id="io.github.znp0.xpeek",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    GLib.set_application_name("xpeek")
    sources = []
    startup_failed = False

    def activate(application):
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            window.xpeek { background: #101010; color: #ffffff; border: 1px solid #363636; }
            .xpeek textview, .xpeek textview text { background: #101010; color: #ffffff;
                font-family: sans-serif; font-size: 18px; }
            .xpeek .popup-header { padding: 2px 6px; }
            .xpeek button { background: transparent; color: #ffffff; border: none;
                box-shadow: none; min-width: 20px; min-height: 20px; padding: 2px; }
            .xpeek button:hover { background: #303030; }
        """)
        Gtk.StyleContext.add_provider_for_display(display, css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window = OverlayWindow(application, config, mode)

        def dismiss():
            window.dismiss()
            return GLib.SOURCE_CONTINUE

        for sig in (signal.SIGUSR1, signal.SIGUSR2):
            sources.append(GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, dismiss))
        window.present()

    def start(application):
        nonlocal startup_failed
        try:
            activate(application)
        except Exception as exc:
            startup_failed = True
            print(f"Cannot open the popup: {exc}", file=sys.stderr)
            application.quit()

    app.connect("activate", start)
    try:
        # CLI arguments are already parsed; do not pass them to GTK.
        result = app.run([])
        return 1 if startup_failed else result
    finally:
        for source in sources:
            GLib.source_remove(source)

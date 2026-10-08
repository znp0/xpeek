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


class OverlayWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, config: Config, mode: str):
        super().__init__(application=app, title="xpeek")
        self.app = app
        self.config = config
        self.state = WindowState.load()
        self.closed = False
        self.worker = None
        self.save_source = None
        self.drag_origin = None
        self.monitor = None
        self.monitor_handler = None
        self.output_bounds = {}
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
        chosen = next((m for m in monitors if m.get_connector() == self.state.output), monitors[0])
        bounds = chosen.get_geometry()
        initial = self.state.x is None
        self.state.clamp(bounds.width, bounds.height)
        if initial:
            self.state.x = None
        if self.state.output:
            self._watch_monitor(chosen)
            LayerShell.set_monitor(self, chosen)
        self.set_default_size(self.state.width, self.state.height)

    def _watch_monitor(self, monitor):
        if self.monitor == monitor:
            return
        if self.monitor_handler is not None:
            self.monitor.disconnect(self.monitor_handler)
        self.monitor = monitor
        self.monitor_handler = monitor.connect("notify::geometry", self._monitor_changed)
        self._remember_outputs()

    def _remember_outputs(self):
        # Removed monitors may report empty geometry, and surviving outputs may
        # move. Keep their previous layout for choosing the facing edges.
        self.output_bounds = {
            monitor: (bounds.x, bounds.y, bounds.width, bounds.height)
            for monitor in self.monitors
            if (bounds := monitor.get_geometry()).width > 0 and bounds.height > 0
        }

    def _mapped(self, _window):
        monitor = self.display.get_monitor_at_surface(self.get_surface())
        if monitor is not None:
            self._watch_monitor(monitor)
            bounds = monitor.get_geometry()
            if self.layer:
                self.state.output = monitor.get_connector()
                self.state.clamp(bounds.width, bounds.height)
                self._apply_geometry()
            else:
                self.set_default_size(min(self.state.width, bounds.width),
                                      min(self.state.height, bounds.height))

    def _monitor_changed(self, *_args):
        if self.closed or not self.layer:
            return
        bounds = self.monitor.get_geometry()
        if bounds.width <= 0 or bounds.height <= 0 or self.monitor not in list(self.monitors):
            return
        self.state.clamp(bounds.width, bounds.height)
        self._remember_outputs()
        self._apply_geometry()
        self._queue_save()

    def _outputs_changed(self, *_args):
        if self.closed or not self.layer:
            return
        monitors = list(self.monitors)
        if monitors and self.monitor not in monitors:
            old_bounds = self.output_bounds.get(self.monitor)

            def previous_bounds(monitor):
                bounds = monitor.get_geometry()
                return self.output_bounds.get(
                    monitor, (bounds.x, bounds.y, bounds.width, bounds.height))

            def distance(monitor):
                left, top, width, height = previous_bounds(monitor)
                x, y, w, h = old_bounds
                dx = max(left - (x + w), x - (left + width), 0)
                dy = max(top - (y + h), y - (top + height), 0)
                centers = (2 * left + width - 2 * x - w) ** 2 + (2 * top + height - 2 * y - h) ** 2
                return dx * dx + dy * dy, centers

            chosen = min(monitors, key=distance) if old_bounds else monitors[0]
            bounds = chosen.get_geometry()
            if old_bounds and bounds.width > 0 and bounds.height > 0:
                self.state.relocate(old_bounds, previous_bounds(chosen),
                                    (bounds.width, bounds.height))
            self.drag_origin = None
            self._watch_monitor(chosen)
            self.state.output = self.monitor.get_connector()
            LayerShell.set_monitor(self, self.monitor)
            self._monitor_changed()
        elif self.monitor in monitors:
            self._remember_outputs()

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
        event = gesture.get_current_event() if gesture is not None else None
        self.drag_press = event.get_position()[1:] if event is not None else None

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
            self.state.x = round((origin.x or 0) + dx)
            self.state.y = round(origin.y + dy)
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

    def _drag_end(self, _gesture, _dx, _dy):
        self.drag_origin = None
        self._queue_save()

    def _drag_cancel(self, _gesture, _sequence):
        self.drag_origin = None
        self._queue_save()

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
        self.state.save()
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

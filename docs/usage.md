# Usage

Run `xpeek --help` or `xpeek COMMAND --help` for available flags and examples. Use `xpeek --version` (or `-V`) to print the installed version and exit.

| Command | Behavior |
| --- | --- |
| `select` | Dim the screen and save a region; Escape cancels |
| `translate` | Capture the saved region and translate it |
| `translate -p clipboard` | Copy OCR text from the saved region |
| `ocr` | Select a temporary region and copy text without changing the saved region |
| `ocr -p PROVIDER` | Translate a temporary region |
| `show-history [-n N]` | Open the latest N translations in a popup; default 10 |
| `reset-size` | Restore default popup sizes, keeping position and content |
| `history [-n N]` | Print saved OCR text and translations; default all |
| `history --watch [-n N]` | Watch recent entries in a scrollable terminal view; default 10 |
| `clear-history` | Remove saved translation history |

## Flags and clipboard

Both `translate` and `ocr` accept `-p` / `--provider`, `--ocr-engine`, `--capture-backend {desktop,gamescope}`, `-s` / `--source-lang`, `-t` / `--target-lang`, and `--persistent` / `--no-persistent`. Flags apply only to that invocation and never write config. `translate` defaults to the configured provider; `ocr` defaults to `clipboard`. Engines must be selected during setup. Switching engines also selects its capture setting from `ocr_options`; `--capture-backend` temporarily overrides it. Native gamescope capture reuses the selected region on supported niri layouts; see the [capture guide](capture.md). Fallback warnings appear only in the invoking terminal, including popup requests.

Source `auto` or `detect` requests translation-language detection; language codes depend on the provider. These flags do not change OCR recognition languages. Clipboard mode copies original OCR text, ignores language/persistence flags, and runs independently of the popup. Empty OCR leaves the clipboard unchanged. Successful copies send a notification when `notify-send` and a notification daemon are available. Clipboard-only calls do not enter translation history.

## Popup and history

Only one translation popup is used. In normal mode, a translation invocation closes an existing popup and exits; call it again to translate. `--persistent` enables appending, retaining the latest 10 entries with scrolling. Later calls inherit the open popup's mode, including calls with another provider or OCR engine. `--no-persistent` restores normal behavior and closes an existing popup.

Persistent appending follows new entries only while already at the bottom. Scrolling up preserves the reading position. Trimming old display entries never removes saved history. Persistent and normal heights are remembered separately; their initial heights are 700 and 400 logical pixels.

`xpeek reset-size` restores the width to 500 and both heights to their defaults, in logical pixels. An open popup resizes immediately without changing its position, content, or mode. With no popup open, the command only updates saved sizes for the next opening.

`show-history` displays entries chronologically and starts at the bottom. Its positive `-n` / `--limit` does not change persistent mode's 10-entry limit. If a popup is already open, `show-history` closes it and exits. The close button or Escape when focused also closes the popup.

`history --watch` refreshes when history changes. Scroll with arrows, Page Up/Down, Home/End, or the mouse wheel. New entries follow the bottom unless you scroll up. Press Ctrl+C or `q` to exit.

## Placement

On niri and other layer-shell desktops, no popup window rules are needed. Drag the header between monitors; the popup stays where released and can span adjacent screens. Its portions share content and scroll position. Resize from an edge or corner.

When the saved monitor disconnects, the popup temporarily moves to the nearest remaining monitor. Its gap from the facing edge scales proportionally: a popup near the left edge of a monitor on the right lands near the remaining monitor's right edge. The original monitor and position remain saved across restarts, and reconnecting restores them. Only dragging the header saves a new home and position; resizing updates size.

Placement is stored in `$XDG_STATE_HOME/xpeek/window-state.json` (default `~/.local/state/xpeek/window-state.json`). Delete it to reset geometry. Without layer-shell, GTK restores size and the compositor controls placement.

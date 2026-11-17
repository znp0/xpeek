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
| `update [--tag vX.Y.Z] [--ocr-engines ENGINES]` | Fetch and install a release, keeping installed engines by default |

`xpeek update` runs the checkout's `setup.py update` from any directory. After fetching and verifying the release, setup closes the popup and stops its process and OCR workers before replacing the installation. Model retention does not delay updating; blocked work is forcibly stopped after a short cleanup period, so an in-flight translation may be interrupted. Failed release verification and no-op updates leave the popup running. A managed installation and clean git checkout are required; newer development versions are kept unless `--tag` explicitly selects an older release. Other config settings and state are preserved while `$schema` is refreshed. Use `--ocr-engines all` or comma-separated engine names to change the selection. Before uninstalling manually, close the popup and wait for its idle process to exit.

## Flags and clipboard

Both `translate` and `ocr` accept `-p` / `--provider`, `--ocr-engine`, `--capture-backend {desktop,gamescope}`, `-s` / `--source-lang`, `-t` / `--target-lang`, and `--persistent` / `--no-persistent`. Flags apply only to that invocation and never write config. `translate` defaults to the configured provider; `ocr` defaults to `clipboard`. Engines must be selected during setup. Switching engines also selects its capture setting from `ocr_options`; `--capture-backend` temporarily overrides it. Native gamescope capture reuses the selected region on supported niri layouts; see the [capture guide](capture.md). Fallback warnings appear only in the invoking terminal, including popup requests.

Source `auto` or `detect` requests translation-language detection; language codes depend on the provider. These flags do not change OCR recognition languages. Clipboard mode copies original OCR text, ignores language/persistence flags, and runs independently of the popup. Empty OCR leaves the clipboard unchanged. Successful copies send a notification when `notify-send` and a notification daemon are available. Clipboard-only calls do not enter translation history.

## Popup and history

Only one translation popup is used. In normal mode, a translation invocation closes an existing popup and exits; call it again to translate. `--persistent` enables appending, retaining the latest 10 entries with scrolling. Later calls inherit the open popup's mode, including calls with another provider or OCR engine. `--no-persistent` restores normal behavior and closes an existing popup.

Persistent appending follows new entries only while already at the bottom. Scrolling up preserves the reading position. Trimming old display entries never removes saved history. Persistent and normal heights are remembered separately; their initial heights are 700 and 400 logical pixels.

Short popups hide the trailing blank lines when the text area has room for fewer than six lines. Taller popups keep that extra bottom space; spacing between entries stays the same.

Closing a popup keeps its process and loaded OCR models alive for 60 idle seconds by default. Reopening reuses those models and cancels the countdown; closing again resets it. Both popup modes retain models while open, and active work finishes before the countdown starts. Configure `ocr_keepalive_seconds` to change the duration or set `0` to exit immediately after closing and finishing work. The initial terminal invocation remains running during retention; later commands return through the existing process. Clipboard-only commands do not use or extend this cache.

`xpeek reset-size` restores the width to 500 and both heights to their defaults, in logical pixels. An open popup resizes immediately without changing its position, content, or mode. With no popup open, the command only updates saved sizes for the next opening.

`show-history` displays entries chronologically and starts at the bottom. Its positive `-n` / `--limit` does not change persistent mode's 10-entry limit. If a popup is already open, `show-history` closes it and exits. The close button or Escape when focused also closes the popup.

`history --watch` refreshes when history changes. Scroll with arrows, Page Up/Down, Home/End, or the mouse wheel. New entries follow the bottom unless you scroll up. Press Ctrl+C or `q` to exit.

## Placement

On niri and other layer-shell desktops, no popup window rules are needed. Drag the header between monitors; the popup stays where released and can span adjacent screens. Its portions share content and scroll position. Resize from an edge or corner.

When the saved monitor disconnects, the popup temporarily moves to the nearest remaining monitor. Its gap from the facing edge scales proportionally: a popup near the left edge of a monitor on the right lands near the remaining monitor's right edge. The original monitor and position remain saved across restarts, and reconnecting restores them. Only dragging the header saves a new home and position; resizing updates size.

Placement is stored in `$XDG_STATE_HOME/xpeek/window-state.json` (default `~/.local/state/xpeek/window-state.json`). Delete it to reset geometry. Without layer-shell, GTK restores size and the compositor controls placement.

## Remote browser display

Run `xpeek remote` to open pairing without running OCR, or click the remote button in an existing popup title bar. On layer-shell desktops such as niri, pairing opens a centered overlay on the popup’s monitor. The translation popup hides while pairing so it cannot cover the QR code; Cancel or Escape restores it without changing its saved placement.

Open the displayed plain address in a browser on another device or scan its QR code, then compare the pairing code on both screens and click **Approve** on the desktop. The request shows an inferred browser/device label, with its IP below. Android model names are used when the user-agent supplies one; generic or reduced information falls back to Android. Browsers do not reliably expose the device’s saved name or model; the label is descriptive, so always compare the pairing code. The QR and Copy button provide the same address without a token; scanning also requires approval. You can bookmark that address and request approval again next session. **Reject**, Cancel, or Escape denies waiting requests. Requests expire after two minutes, and opening or refreshing the page requires fresh approval. The viewer starts automatically from either `xpeek remote` or the popup’s remote button. Failed commands leave an existing viewer running; startup failures return a nonzero CLI status. The command returns success when the viewer is ready, or failure if startup fails; approval happens in the detached GUI. Closing the launching terminal does not stop the viewer. Startup messages go to the invoking terminal; later non-secret lifecycle notices are kept in `$XDG_STATE_HOME/xpeek/popup.log` (default `~/.local/state/xpeek/popup.log`), with owner-only permissions and bounded rotation. Approval is valid only for the current desktop session; no pairing credentials are saved in configuration or browser storage.

The desktop and remote device must be on a local network that permits them to reach each other. Keep the pairing window or paired session open so the viewer remains available. Allow TCP 38853 through the desktop firewall; `xpeek remote --port PORT` chooses another port for the session. End the current session before changing its port. Use `http://`, not `https://`. The bookmark may need updating if the desktop’s IP address changes.

After pairing, the desktop popup hides and every translation hotkey produces a new translation. Updates are pushed over an authenticated server-sent event stream as soon as results change, without periodic translation polling. The page reconnects automatically and receives the latest session state after a connection interruption. Normal mode shows the latest request; persistent mode keeps the latest 10 and follows new entries only when you are already at the bottom. The page leaves blank space below the latest entry for comfortable reading. Configure `remote_bottom_spacing_percent` from 0–100 (default 30); 0 removes the extra space. Changes apply on the next GUI command and are pushed to the connected browser. Pending results and errors appear in the remote browser, while capture warnings remain in the invoking terminal. Clipboard requests remain independent.

Tap **Return to desktop** to restore the popup at its remembered position and resume its normal toggle behavior. Keep the remote page open in standby; clicking the popup’s remote button transfers output again without another scan. After 15 seconds without remote browser contact, xpeek restores the desktop automatically; reconnecting does not take it over again. Keep the remote browser active while reading, since screen locking or browser suspension may interrupt contact.

Tap **End session** in the remote browser or close the desktop popup to revoke approvals and stop network access. Pairing is session-only and does not change configuration or saved window placement. OCR work and idle retention follow the usual cleanup rules. No cloud service or separate background service is installed.

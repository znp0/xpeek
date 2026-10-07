# xpeek

Copy and translate screen text on Linux/Wayland. Select a region, extract text with RapidOCR, and display a translation or copy the original text to your clipboard. Defaults to English → Vietnamese using Google Translate.

## Install

Requires Python 3.10+, `grim`, `slurp`, and `wl-copy`. On Arch/CachyOS:

```bash
sudo pacman -S python python-pip grim slurp wl-clipboard
# Optional desktop notifications (also needs a notification daemon):
sudo pacman -S libnotify

git clone https://github.com/znp0/xpeek.git
cd xpeek
python3 setup.py install
```

The installer creates `.venv`, installs all Python/provider dependencies, and links `~/.local/bin/xpeek` to the command. No `sudo` is needed for setup. Keep the checkout in place; rerun `python3 setup.py install` after updating it. If `xpeek` is not found, add `~/.local/bin` to your shell's PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

To uninstall, run this from the same checkout:

```bash
python3 setup.py uninstall
```

Uninstall removes the managed environment and command symlink, plus any empty directories created by install. It preserves configuration, history, and files that existed before installation. System packages are managed separately.

## Usage

```bash
xpeek select                      # Select and save a translation region
xpeek translate                   # Translate the saved region in an overlay
xpeek translate -p gemini         # Override the provider for this invocation
xpeek translate -p=clipboard      # Copy OCR text from the saved region
xpeek ocr                         # Select a temporary region and copy its text
xpeek show-last                   # Show the most recent translation
xpeek history -n 10               # Show recent history
xpeek clear-history
xpeek --help
xpeek translate --help
```

Only one translation window is used. If it already exists, `translate` closes
it and exits, regardless of provider. Run the command again to open a new
translation or copy from the saved region. `ocr` runs independently and never
changes the saved region. Successful copies send a desktop notification when
`notify-send` is available; empty OCR leaves the clipboard unchanged.

## Configuration

Config: `~/.config/xpeek/config.json`. History: `~/.local/share/xpeek/history.json`. Both respect `XDG_CONFIG_HOME` / `XDG_DATA_HOME`. Run `xpeek select` to create the config, then edit it using [config.example.json](config.example.json) as a reference. Set `provider`, `source_lang`, `target_lang`, `history_limit`, and
per-provider models/hosts in `provider_options`.

Providers: `google` (no key), `deepl`, `openai`, `gemini`, and `ollama` (local server). `-p` / `--provider` overrides the saved provider without saving it; `clipboard` skips translation. Supply keys using `DEEPL_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`, or a `.env` file in the checkout.

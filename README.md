# xpeek

Copy and translate screen text on Linux/Wayland. Select a region, extract text with RapidOCR, and display a translation or copy the original text to your clipboard. Defaults to automatic source-language detection → English using Google Translate.

## Install

Requires Python 3.10+, `grim`, `slurp`, and `wl-copy`. OCR uses bundled
RapidOCR/ONNX models; Tesseract and language packs are not required.

On Arch/CachyOS:

```bash
sudo pacman -S python python-pip grim slurp wl-clipboard
# Optional desktop notifications (also needs a notification daemon):
sudo pacman -S libnotify

git clone https://github.com/znp0/xpeek.git
cd xpeek
python3 setup.py install
```

The installer creates `.venv`, installs all Python/provider dependencies, links `~/.local/bin/xpeek` to the command, and creates `~/.config/xpeek/config.json` if missing. No `sudo` is needed for setup. Keep the checkout in place; rerun `python3 setup.py install` after updating it. If `xpeek` is not found, add `~/.local/bin` to your shell's PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

To uninstall, run this from the same checkout:

```bash
python3 setup.py uninstall                 # Keep config (default)
python3 setup.py uninstall --keep-config   # Explicitly keep config
python3 setup.py uninstall --remove-config # Also delete config.json
```

Uninstall removes the managed environment and command symlink, plus any empty directories created by install. Configuration is kept unless `--remove-config` is specified. History and unrelated files are always preserved. System packages are managed separately.

## Usage

```bash
xpeek select                      # Select and save a translation region
xpeek translate                   # Translate the saved region in an overlay
xpeek translate -p gemini         # Override the provider for this invocation
xpeek translate -s en -t ja       # Translate English text to Japanese
xpeek translate -s auto -t en     # Detect the source language and translate
xpeek translate -p=clipboard      # Copy OCR text from the saved region
xpeek ocr                         # Select a temporary region and copy its text
xpeek ocr -p google               # Translate a temporary region in an overlay
xpeek ocr -p gemini -s auto -t en  # Choose provider and languages for that region
xpeek show-last                   # Show the most recent translation
xpeek history -n 10               # Show recent history
xpeek clear-history
xpeek --help
xpeek translate --help
xpeek ocr --help
```

Only one translation window is used. If it already exists, `translate` or
`ocr -p PROVIDER` with a translation provider closes it and exits. Run the command
again to start a translation. `ocr` always leaves the saved region unchanged;
without a provider (or with `-p clipboard`), it copies text and runs independently.
Successful copies send a desktop notification when
`notify-send` is available; empty OCR leaves the clipboard unchanged.

## Configuration

Config: `~/.config/xpeek/config.json`. History: `~/.local/share/xpeek/history.json`. Both respect `XDG_CONFIG_HOME` / `XDG_DATA_HOME`. Setup creates the config; `xpeek select` saves the region into it. Edit it using [config.example.json](config.example.json) as a reference. Set `provider`, `source_lang`, `target_lang`, `history_limit`, and
per-provider models/hosts in `provider_options`.

The installed config starts with `source_lang: "auto"`, `target_lang: "en"`,
and empty `provider_options`. xpeek reads this file; if it is missing, rerun
`python3 setup.py install`. Reinstall preserves existing configuration. The
example illustrates optional provider settings and is not loaded automatically.
Obsolete `ocr_lang` and `overlay_enabled` fields in older configs are ignored
and removed when the config is next saved.

Providers: `google` (no key), `deepl`, `openai`, `gemini`, and `ollama` (local server). `-p` / `--provider` overrides the saved provider without saving it; `clipboard` skips translation.

Both `translate` and `ocr` accept `-p` / `--provider`, `-s` / `--source-lang`, and `-t` / `--target-lang`. `translate` defaults to the configured provider; `ocr` defaults to `clipboard`. Language flags override translation languages without saving; omitted flags use the configured languages (`auto` → `en` initially). Use `--source-lang auto` (or `detect`) to request automatic source detection. Supported languages and codes depend on the provider. These flags do not change the OCR model; recognition depends on its supported characters. Clipboard mode copies the original text and ignores translation language flags.

### API keys

Keep keys out of `config.json`. From the checkout, create a local `.env`:

```bash
cp -n .env.example .env
# Edit .env and fill in only DEEPL_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY.
```

xpeek loads the checkout's `.env` automatically, including when launched from
another directory. `.env` is ignored by Git; `.env.example` contains no keys.
You can also export a key instead:

```bash
export GEMINI_API_KEY="your-key"
xpeek translate -p gemini
```

Exported variables take precedence over `.env`. Keep `provider_options` empty
or use it only for non-secret settings such as models and hosts.

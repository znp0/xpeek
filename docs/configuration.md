# Configuration

Setup creates `$XDG_CONFIG_HOME/xpeek/config.json` (default `~/.config/xpeek/config.json`). Edit it using [config.example.json](../config.example.json). The example is not loaded automatically. If config is missing, rerun setup.

New configs use Google, `source_lang: "auto"`, `target_lang: "en"`, `persistent_window: false`, `history_limit: 100`, and empty `provider_options` / `ocr_options`. The initial `ocr_engine` is chosen among installed engines in this order: Paddle tiny, RapidOCR, Paddle small. Setup preserves existing config, including its engine choice.

| Setting | Purpose |
| --- | --- |
| `provider` | `google`, `deepl`, `openai`, `gemini`, or `ollama` |
| `source_lang` / `target_lang` | Translation languages; `auto` source requests detection |
| `persistent_window` | Start new popups in append mode |
| `history_limit` | Maximum saved history entries |
| `ocr_engine` | Installed OCR engine; see the [OCR guide](ocr.md) |
| `ocr_options` | Settings keyed by OCR engine |
| `provider_options` | Non-secret settings keyed by translation provider |

Provider/language/engine/persistence flags override these settings for one invocation. An unavailable engine reports an error; xpeek never silently chooses another.

## Keys and provider options

Google needs no key. DeepL, OpenAI, and Gemini read `DEEPL_API_KEY`, `OPENAI_API_KEY`, and `GEMINI_API_KEY`. Ollama uses a local server.

```bash
# From the checkout:
cp -n .env.example .env
# Edit .env and fill in only the keys you need.

# Or export a key for your session:
export GEMINI_API_KEY="your-key"
xpeek translate -p gemini
```

xpeek loads the checkout's `.env`, including when launched from another directory. Exported environment variables take precedence. `.env` is Git-ignored; `.env.example` contains placeholders. Keep secrets out of shared config files.

The example config lists supported provider settings with empty `api_key` values, which fall back to environment variables. Keep these empty when using `.env`. Optional settings include `model` for OpenAI/Gemini, `base_url` for OpenAI-compatible servers, and `model`, `host`, and `num_gpu` for Ollama. A `null` value leaves `base_url` or `num_gpu` at the provider's default. For example:

```json
{
  "provider_options": {
    "ollama": {"host": "http://localhost:11434", "model": "llama3.1"}
  }
}
```

## State and data

The saved region lives in `$XDG_STATE_HOME/xpeek/region.json` and popup geometry in `window-state.json` beside it (default `~/.local/state/xpeek/`). History lives in `$XDG_DATA_HOME/xpeek/history.json` (default `~/.local/share/xpeek/history.json`). Regions and geometry do not belong in config. Install preserves all these files; uninstall keeps them by default and always preserves history.

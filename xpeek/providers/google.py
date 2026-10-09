"""Google Translate provider (free, no API key)."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

from .base import TranslationError, Translator


class GoogleTranslator(Translator):
    def __init__(self, **_ignored) -> None:
        # No config needed; accepts/ignores stray provider_options keys.
        pass

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if not text.strip():
            return ""

        sl = source_lang if source_lang else "auto"
        tl = target_lang

        url = (
            "https://translate.googleapis.com/translate_a/single"
            "?client=dict-chrome-ex"
            f"&sl={sl}"
            f"&tl={tl}"
            "&dt=t"
            f"&q={urllib.parse.quote(text)}"
        )

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise TranslationError(
                f"Google Translate request failed with status {exc.code}: {exc.reason}"
            ) from exc
        except URLError as exc:
            raise TranslationError(
                f"Google Translate network error: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise TranslationError(f"Google Translate request failed: {exc}") from exc

        try:
            data = json.loads(body)
            if not isinstance(data, list) or not data or not isinstance(data[0], list):
                raise ValueError("Invalid response format")

            parts = []
            for segment in data[0]:
                if isinstance(segment, list) and len(segment) > 0 and isinstance(segment[0], str):
                    parts.append(segment[0])

            if not parts:
                raise ValueError("No translation found in response")

            return "".join(parts)

        except Exception as exc:
            raise TranslationError(
                f"Failed to parse Google Translate response: {exc}"
            ) from exc

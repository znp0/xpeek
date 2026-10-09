"""Gemini API provider."""
from __future__ import annotations

import os

from .base import TranslationError, Translator, translation_instruction

_LANG_NAMES = {
    "en": "English",
    "vi": "Vietnamese",
}

class GeminiTranslator(Translator):
    def __init__(self, api_key: str | None = None, model: str = "gemini-3.1-flash-lite", **_ignored) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise TranslationError(
                "Gemini provider requires an API key. Set it via config "
                "provider_options.gemini.api_key or the GEMINI_API_KEY env var."
            )
        self.model = model

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if not text.strip():
            return ""
        try:
            import requests
        except ImportError as exc:
            raise TranslationError("Missing dependency: pip install requests") from exc

        src_name = _LANG_NAMES.get(source_lang, source_lang)
        tgt_name = _LANG_NAMES.get(target_lang, target_lang)

        prompt = (
            translation_instruction(src_name, tgt_name)
            + "Reply with ONLY the translation, no commentary, no markdown, no quotes.\n\n"
            f"{text}"
        )

        # Standard REST endpoint for Gemini API
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,  # Low temperature for more accurate, literal translation
            }
        }

        try:
            resp = requests.post(url, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            err_msg = str(exc)
            if hasattr(exc, 'response') and exc.response is not None:
                try:
                    err_msg += f" - {exc.response.json()}"
                except ValueError:
                    err_msg += f" - {exc.response.text}"
            raise TranslationError(f"Gemini API request failed: {err_msg}") from exc

        try:
            result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as exc:
            raise TranslationError(f"Unexpected response format from Gemini: {data}") from exc

        if not result:
            raise TranslationError("Gemini returned an empty result")
        return result

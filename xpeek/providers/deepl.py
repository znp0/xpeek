"""DeepL provider (requires an API key, free or pro tier)."""
from __future__ import annotations

import os

from .base import Translator, TranslationError


class DeepLTranslator(Translator):
    def __init__(self, api_key: str | None = None, **_ignored) -> None:
        # Prefer explicit config, fall back to env var so the key needn't
        # live in the config file on disk.
        self.api_key = api_key or os.environ.get("DEEPL_API_KEY")
        if not self.api_key:
            raise TranslationError(
                "DeepL provider requires an API key. Set it via config "
                "provider_options.deepl.api_key or the DEEPL_API_KEY env var."
            )

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if not text.strip():
            return ""
        try:
            from deep_translator import DeeplTranslator as _DT
        except ImportError as exc:
            raise TranslationError(
                "Missing dependency: pip install deep-translator"
            ) from exc

        try:
            translator = _DT(
                api_key=self.api_key,
                source=source_lang,
                target=target_lang,
            )
            if source_lang == "auto":
                # Requests omits None-valued parameters; DeepL detects the
                # source language when source_lang is absent.
                translator.source = None
            result = translator.translate(text)
        except Exception as exc:
            raise TranslationError(f"DeepL request failed: {exc}") from exc

        if not result:
            raise TranslationError("DeepL returned an empty result")
        return result

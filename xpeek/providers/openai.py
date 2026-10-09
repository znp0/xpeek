"""Translation via an OpenAI-compatible chat completion API."""
from __future__ import annotations

import os

from .base import Translator, TranslationError, translation_instruction

_LANG_NAMES = {
    "en": "English",
    "vi": "Vietnamese",
}


class OpenAITranslator(Translator):
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        **_ignored,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self.base_url = base_url
        if not self.api_key:
            raise TranslationError(
                "OpenAI provider requires an API key. Set it via config "
                "provider_options.openai.api_key or the OPENAI_API_KEY env var."
            )

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if not text.strip():
            return ""
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise TranslationError("Missing dependency: pip install openai") from exc

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        src_name = _LANG_NAMES.get(source_lang, source_lang)
        tgt_name = _LANG_NAMES.get(target_lang, target_lang)

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            translation_instruction(src_name, tgt_name)
                            + "Reply with only the translation, no commentary, "
                            "no quotes, no explanations."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0,
            )
        except Exception as exc:
            raise TranslationError(f"OpenAI request failed: {exc}") from exc

        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise TranslationError("OpenAI returned an empty result")
        return content.strip()

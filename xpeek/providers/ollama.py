"""Translation via a local Ollama server (no API key, no cloud dependency)."""
from __future__ import annotations

from .base import Translator, TranslationError, translation_instruction

_LANG_NAMES = {
    "en": "English",
    "vi": "Vietnamese",
}


class OllamaTranslator(Translator):
    def __init__(
        self,
        model: str = "llama3.1",
        host: str = "http://localhost:11434",
        num_gpu: int | None = None,
        **_ignored,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.num_gpu = num_gpu

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
            + "Reply with only the translation, no commentary, no quotes.\n\n"
            f"{text}"
        )

        payload = {"model": self.model, "prompt": prompt, "stream": False}
        if self.num_gpu is not None:
            payload["options"] = {"num_gpu": self.num_gpu}

        try:
            resp = requests.post(
                f"{self.host}/api/generate",
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise TranslationError(
                f"Ollama request failed (is `ollama serve` running at {self.host}?): {exc}"
            ) from exc

        data = resp.json()
        result = data.get("response", "").strip()
        if not result:
            raise TranslationError("Ollama returned an empty result")
        return result

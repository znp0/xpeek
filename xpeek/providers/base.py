"""Common interface all translation providers must implement."""
from __future__ import annotations

from abc import ABC, abstractmethod


def translation_instruction(source_name: str, target_name: str) -> str:
    """Build an LLM instruction for a known or automatically detected source."""
    if source_name in ("auto", "detect"):
        return f"Detect the source language and translate the following text to {target_name}. "
    return f"Translate the following {source_name} text to {target_name}. "


class TranslationError(RuntimeError):
    """Raised when a translation provider fails."""


class Translator(ABC):
    """All providers implement this single method."""

    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate `text` from source_lang to target_lang (ISO 639-1 codes).

        source_lang may be 'auto' to request automatic source detection.

        Must raise TranslationError on failure (network issues, bad API key,
        empty response, etc.) rather than letting raw exceptions propagate.
        """
        raise NotImplementedError

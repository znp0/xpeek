"""Common interface all translation providers must implement."""
from __future__ import annotations

from abc import ABC, abstractmethod


class TranslationError(RuntimeError):
    """Raised when a translation provider fails."""


class Translator(ABC):
    """All providers implement this single method."""

    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate `text` from source_lang to target_lang (ISO 639-1 codes).

        Must raise TranslationError on failure (network issues, bad API key,
        empty response, etc.) rather than letting raw exceptions propagate.
        """
        raise NotImplementedError

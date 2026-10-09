"""Pluggable translation providers.

Add a new provider by:
  1. Creating a module here implementing the `Translator` protocol (see base.py).
  2. Registering it in the PROVIDERS dict below.
No other part of the application needs to change.
"""
from __future__ import annotations

from typing import Any

from .base import TranslationError, Translator


def _load_google(options: dict[str, Any]) -> Translator:
    from .google import GoogleTranslator

    return GoogleTranslator(**options)


def _load_deepl(options: dict[str, Any]) -> Translator:
    from .deepl import DeepLTranslator

    return DeepLTranslator(**options)


def _load_openai(options: dict[str, Any]) -> Translator:
    from .openai import OpenAITranslator

    return OpenAITranslator(**options)


def _load_ollama(options: dict[str, Any]) -> Translator:
    from .ollama import OllamaTranslator

    return OllamaTranslator(**options)


def _load_gemini(options: dict[str, Any]) -> Translator:
    from .gemini import GeminiTranslator

    return GeminiTranslator(**options)


# name -> loader function (lazy import so users only need deps for the
# provider they actually use)
PROVIDERS = {
    "google": _load_google,
    "deepl": _load_deepl,
    "openai": _load_openai,
    "ollama": _load_ollama,
    "gemini": _load_gemini,
}


def get_translator(name: str, provider_options: dict[str, Any] | None = None) -> Translator:
    """Instantiate the translation provider registered under `name`."""
    provider_options = provider_options or {}
    loader = PROVIDERS.get(name)
    if loader is None:
        available = ", ".join(sorted(PROVIDERS))
        raise TranslationError(
            f"Unknown translation provider '{name}'. Available: {available}"
        )
    options = provider_options.get(name, {})
    return loader(options)


__all__ = ["PROVIDERS", "TranslationError", "Translator", "get_translator"]

"""Validate OCR settings and lazily dispatch to installed engines."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .runtime import ENGINES, runtime_root

DEFAULTS = {"threads": 1, "scale": 1, "preprocess": "none"}


class OcrError(RuntimeError):
    """Raised when OCR extraction fails."""


def validated_options(engine: str, options: dict[str, Any]) -> dict[str, Any]:
    if engine not in ENGINES:
        raise ValueError(f"Unsupported OCR engine {engine!r}; choose " + ", ".join(ENGINES)
                         + ". Remove obsolete engine settings from ocr_options manually.")
    if not isinstance(options, dict):
        raise TypeError(f"ocr_options.{engine} must be an object")
    unknown = options.keys() - DEFAULTS.keys()
    if unknown:
        raise ValueError(f"Unsupported {engine} OCR options: {', '.join(sorted(map(str, unknown)))}")
    values = {**DEFAULTS, **options}
    if type(values["threads"]) is not int or not 1 <= values["threads"] <= 4:
        raise ValueError("OCR threads must be an integer between 1 and 4")
    scale = values["scale"]
    if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("OCR scale must be a finite positive number")
    if values["preprocess"] not in ("none", "grayscale"):
        raise ValueError("OCR preprocess must be none or grayscale")
    return values


def validate_settings(engine: str, options: dict[str, dict[str, Any]]) -> None:
    validated_options(engine, {})
    if not isinstance(options, dict):
        raise TypeError("ocr_options must be an object keyed by engine")
    for name, settings in options.items():
        validated_options(name, settings)


def extract_text(image_path: Path, engine: str = "rapidocr", options: dict[str, Any] | None = None) -> str:
    """Recognize lines without importing unused backends."""
    try:
        settings = validated_options(engine, {} if options is None else options)
        marker = runtime_root().parent / ".xpeek-install.json"
        if marker.is_file():
            installed = json.loads(marker.read_text()).get("ocr_engines")
            if installed is not None and engine not in installed:
                raise OcrError(f"{engine} is not installed. Rerun `python3 setup.py install` and select it.")
        if engine == "rapidocr":
            from .rapidocr import extract_text as recognize
        else:
            from .paddleocr import extract_text as recognize
            return recognize(image_path, engine=engine, **settings)
        return recognize(image_path, **settings)
    except OcrError:
        raise
    except ImportError as exc:
        raise OcrError(f"Missing {engine} dependencies. Rerun `python3 setup.py install` and select it.") from exc
    except (OSError, TypeError, ValueError, OverflowError) as exc:
        raise OcrError(str(exc)) from exc


def shutdown_backends() -> None:
    """Release an owned Paddle worker without importing a new backend."""
    import sys

    module = sys.modules.get("xpeek.ocr.paddleocr")
    if module is not None:
        module.shutdown()

"""Dependency-free paths and constants for managed OCR installations."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENGINES = ("rapidocr", "paddleocr-tiny", "paddleocr-small")
DEFAULT_ORDER = ("paddleocr-tiny", "rapidocr", "paddleocr-small")
PADDLE_REQUIREMENTS = (
    "paddleocr==3.7.0", "paddlepaddle==3.3.1", "paddlex==3.7.2",
    "numpy==2.3.5", "pillow==12.3.0", "threadpoolctl>=3.6.0",
)
PYTHON_RELEASE = "20261003"
PYTHON_VERSION = "3.12.15"
PYTHON_HASHES = {
    "x86_64": "731af898886c5f821890dc901eca3c651cca8e51fa7308c159d12a1194aeac91",
}
# Exact model files used in the gameplay/native benchmarks.
MODEL_HASHES = {
    "PP-OCRv6_tiny_det": {
        "inference.json": "935c18972799dd8f2a2ff424b9f8b2c6adee4b89f3a76f0746210ec33ba9b3d8",
        "inference.pdiparams": "853f7ed317d4f2f80de646842e5bbc32f9d39c601562cbe466cda42e4bafb1b1",
        "inference.yml": "3ac018be6f97499a08faa3bbdeb33640968d9307f6736d152902747a9f259593",
    },
    "PP-OCRv6_tiny_rec": {
        "inference.json": "b5b14770c7dcf092781e92f4278a2ae5f95048f08b4b8a04140e88cb2745f147",
        "inference.pdiparams": "bb2f8f54d1e25f28c71b6fa4fe23f5940e159cae27fbee96155c99f822156e57",
        "inference.yml": "66170210bad538e83fff3c4a3867e547d6bf20b50d64b20347c4b913f3034ea1",
    },
    "PP-OCRv6_small_det": {
        "inference.json": "89240f689a4a77aad75ef55a8df0a15c8e1d4980a327d17e58f24bbadde5aeab",
        "inference.pdiparams": "5043d4ccc8d63402ccea8feefcee4db57077431a873e78d2191836a178a492da",
        "inference.yml": "193f435274bf9f0b5f71a929bbfbcf148282df7e633b34e7c373e8f44741b516",
    },
    "PP-OCRv6_small_rec": {
        "inference.json": "f0bf53c853937a917affdd74467472167727f8ab0f0f7bded01c4a16c27e46e6",
        "inference.pdiparams": "406e1e689c9a7fbb04178007a3fe10cf852afe7bf8bb3bc6dbb9d532b13bd907",
        "inference.yml": "ab078671bb49f06228eadccd34f1bb501e157f7a047095ffb943ba81512c77d1",
    },
}


def runtime_root() -> Path:
    prefix = Path(sys.prefix)
    if (prefix / ".xpeek-install.json").is_file():
        return prefix / "ocr"
    return Path(__file__).resolve().parents[2] / ".venv" / "ocr"


def model_names(engine: str) -> tuple[str, str]:
    variant = engine.removeprefix("paddleocr-")
    return f"PP-OCRv6_{variant}_det", f"PP-OCRv6_{variant}_rec"


def worker_environment(root: Path, threads: int = 1) -> dict[str, str]:
    environment = os.environ.copy()
    # Neither imports nor model libraries should use another Python environment.
    for name in ("PYTHONHOME", "PYTHONPATH"):
        environment.pop(name, None)
    environment.update({
        "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1",
        "OMP_NUM_THREADS": str(threads), "OPENBLAS_NUM_THREADS": str(threads),
        "MKL_NUM_THREADS": str(threads), "NUMEXPR_NUM_THREADS": str(threads),
        "XDG_CACHE_HOME": str(root / "cache"),
        "PADDLE_PDX_CACHE_HOME": str(root / "cache" / "paddlex"),
        "PADDLE_EXTENSION_DIR": str(root / "cache" / "paddle-extension"),
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
        "HF_HOME": str(root / "cache" / "huggingface"),
        "MODELSCOPE_CACHE": str(root / "cache" / "modelscope"),
        "MPLCONFIGDIR": str(root / "cache" / "matplotlib"),
        "PIP_CACHE_DIR": str(root / "cache" / "pip"),
    })
    return environment

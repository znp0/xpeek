"""RapidOCR with per-reader ONNX thread limits and optional preprocessing."""
from __future__ import annotations

import os
from pathlib import Path
from threading import RLock

from . import OcrError
from .images import preprocess_image

for _variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")

_READERS = {}
_LOCK = RLock()


def get_ocr_instance(threads: int = 1):
    """Cache readers by thread count without patching ONNX Runtime globally."""
    from rapidocr_onnxruntime import RapidOCR, utils

    with _LOCK:
        if threads not in _READERS:
            original = utils.SessionOptions

            def session_options():
                options = original()
                options.intra_op_num_threads = threads
                options.inter_op_num_threads = 1
                return options

            utils.SessionOptions = session_options
            try:
                _READERS[threads] = RapidOCR()
            finally:
                utils.SessionOptions = original
        return _READERS[threads]


def extract_text(image_path: Path, *, threads: int = 1, scale: float = 1, preprocess: str = "none") -> str:
    import cv2
    from PIL import Image
    from threadpoolctl import threadpool_limits

    with _LOCK, threadpool_limits(limits=threads):
        try:
            cv2.setNumThreads(threads)
            reader = get_ocr_instance(threads)
            image = reader.load_img(str(image_path))
            # Match the installed loader, including its RGBA-to-BGR conversion.
            with Image.open(image_path) as source:
                color_order = "BGR" if source.mode == "RGBA" else "RGB"
            image = preprocess_image(image, scale, preprocess, color_order=color_order)
            result, _ = reader(image)
            return "\n".join(line[1] for line in result).strip() if result else ""
        except ImportError:
            raise
        except Exception as exc:
            raise OcrError(f"RapidOCR failed: {exc}") from exc

"""OCR via RapidOCR (PaddleOCR ONNX Runtime)."""
from __future__ import annotations

from pathlib import Path

try:
    import onnxruntime

    # Monkey-patch ONNX Runtime SessionOptions to limit threads.
    # RapidOCR doesn't expose thread count options, and by default ONNX Runtime
    # uses all available cores, causing severe CPU spikes during inference.
    _original_init = onnxruntime.SessionOptions.__init__

    def _patched_init(self, *args, **kwargs):
        _original_init(self, *args, **kwargs)
        self.intra_op_num_threads = 1
        self.inter_op_num_threads = 1

    onnxruntime.SessionOptions.__init__ = _patched_init

    from rapidocr_onnxruntime import RapidOCR
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Missing OCR dependencies. Install with: pip install rapidocr-onnxruntime"
    ) from exc


class OcrError(RuntimeError):
    """Raised when OCR extraction fails."""


# Initialize the model once globally to avoid reloading it on every capture
_OCR_INSTANCE = None


def get_ocr_instance() -> RapidOCR:
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        _OCR_INSTANCE = RapidOCR()
    return _OCR_INSTANCE


def extract_text(image_path: Path) -> str:
    """Run RapidOCR on the given image and return extracted text.
    """
    try:
        ocr = get_ocr_instance()
        # rapidocr returns (result, elapse)
        # result is a list of tuples: [([[box points]], text, confidence), ...]
        result, elapse = ocr(str(image_path))

        if not result:
            return ""

        texts = [line[1] for line in result]
        return "\n".join(texts).strip()
    except Exception as exc:
        raise OcrError(f"RapidOCR failed: {exc}") from exc

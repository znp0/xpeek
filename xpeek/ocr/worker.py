"""Paddle worker entry point; deliberately independent of GTK/system Python."""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import signal
import sys
from ctypes import CDLL, c_int, c_ulong
from pathlib import Path

# Load only xpeek's pure Python package. Adding its containing site-packages
# would expose distro-Python extensions to this Python 3.12 interpreter.
package = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "xpeek", package / "__init__.py", submodule_search_locations=[str(package)],
)
module = importlib.util.module_from_spec(spec)
sys.modules["xpeek"] = module
spec.loader.exec_module(module)
logger = logging.getLogger(__name__)
PR_SET_PDEATHSIG = 1


def main() -> None:
    # End the owned worker even if its parent is killed during inference.
    parent = os.getppid()
    libc = CDLL(None)
    libc.prctl.argtypes = [c_int, c_ulong, c_ulong, c_ulong, c_ulong]
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        raise RuntimeError("Cannot bind the OCR worker's lifetime to its parent")
    if parent == 1 or os.getppid() != parent:
        return
    # Reserve a protocol descriptor before redirecting Python and native logs.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1, encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    import cv2
    from paddleocr import PaddleOCR
    from threadpoolctl import threadpool_limits

    from xpeek.ocr import validated_options
    from xpeek.ocr.images import preprocess_image
    from xpeek.ocr.runtime import model_names

    threads = int(sys.argv[2])
    root = Path(sys.argv[1])
    cv2.setNumThreads(threads)
    readers = {}
    protocol.write(json.dumps({"ready": True}) + "\n")
    with threadpool_limits(limits=threads):
        for line in sys.stdin:
            try:
                request = json.loads(line)
                engine = request["engine"]
                settings = validated_options(engine, request["options"])
                if engine not in ("paddleocr-tiny", "paddleocr-small"):
                    raise ValueError("Paddle worker requires a Paddle engine")
                if settings["threads"] != threads:
                    raise ValueError("Worker thread count does not match the request")
                if engine not in readers:
                    detector, recognizer = model_names(engine)
                    for model in (detector, recognizer):
                        if not all((root / "models" / model / name).is_file()
                                   for name in ("inference.json", "inference.yml", "inference.pdiparams")):
                            raise ValueError(f"Missing {model}; rerun setup and select {engine}")
                    readers[engine] = PaddleOCR(
                        text_detection_model_name=detector,
                        text_recognition_model_name=recognizer,
                        text_detection_model_dir=str(root / "models" / detector),
                        text_recognition_model_dir=str(root / "models" / recognizer),
                        device="cpu", cpu_threads=threads, enable_mkldnn=False,
                        use_doc_orientation_classify=False, use_doc_unwarping=False,
                        use_textline_orientation=False,
                    )
                image = cv2.imread(request["image"], cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError("Cannot decode the captured image")
                image = preprocess_image(image, settings["scale"], settings["preprocess"], color_order="BGR")
                results = readers[engine].predict(image)
                text = "\n".join(text for result in results for text in result["rec_texts"]).strip()
                response = {"text": text}
            except Exception as exc:
                logger.exception("Paddle OCR request failed")
                response = {"error": str(exc)}
            protocol.write(json.dumps(response, ensure_ascii=False) + "\n")
    protocol.close()


if __name__ == "__main__":
    main()

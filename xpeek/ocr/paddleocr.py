"""Own a reusable Paddle worker in the managed Python 3.12 runtime."""
from __future__ import annotations

import atexit
import json
import selectors
import subprocess
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import RLock

from . import OcrError
from .runtime import runtime_root, worker_environment

TIMEOUT = 120
_LOCK = RLock()
_WORKER = None


@contextmanager
def worker_logs():
    with tempfile.TemporaryFile() as handle:
        yield handle


class PaddleWorker:
    def __init__(self, root: Path, threads: int):
        python = root / "python" / "bin" / "python3"
        if not python.is_file():
            raise OcrError("Paddle runtime is missing. Rerun `python3 setup.py install` and select a Paddle engine.")
        self.threads = threads
        self.resources = ExitStack()
        self.errors = self.resources.enter_context(worker_logs())
        self.process = None
        try:
            self.process = subprocess.Popen(
                [str(python), "-I", str(Path(__file__).with_name("worker.py")), str(root), str(threads)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
                env=worker_environment(root, threads),
            )
            if self.receive().get("ready") is not True:
                raise OcrError("Paddle worker did not initialize")
        except Exception:
            self.close()
            raise

    def receive(self) -> dict:
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(TIMEOUT):
                raise OcrError(f"Paddle OCR timed out after {TIMEOUT} seconds")
        line = self.process.stdout.readline()
        if not line:
            self.errors.seek(0, 2)
            size = self.errors.tell()
            self.errors.seek(max(0, size - 3000))
            message = self.errors.read().decode("utf-8", errors="replace").strip()
            raise OcrError("Paddle worker exited unexpectedly" + (f": {message}" if message else ""))
        try:
            response = json.loads(line)
            if not isinstance(response, dict):
                raise TypeError("response is not an object")
            return response
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise OcrError("Invalid response from Paddle worker") from exc

    def recognize(self, image: Path, engine: str, options: dict) -> str:
        request = {"image": str(image.resolve()), "engine": engine, "options": options}
        self.process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        response = self.receive()
        if "error" in response:
            raise OcrError(f"{engine} failed: {response['error']}")
        if not isinstance(response.get("text"), str):
            raise OcrError("Paddle worker returned no OCR text")
        return response["text"]

    def close(self) -> None:
        if self.process is not None:
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            if self.process.stdout:
                self.process.stdout.close()
        self.resources.close()


def extract_text(image_path: Path, *, engine: str, threads: int, scale: float, preprocess: str) -> str:
    global _WORKER
    with _LOCK:
        if _WORKER is not None and (_WORKER.threads != threads or _WORKER.process.poll() is not None):
            shutdown()
        try:
            if _WORKER is None:
                _WORKER = PaddleWorker(runtime_root(), threads)
            return _WORKER.recognize(image_path, engine, {"threads": threads, "scale": scale, "preprocess": preprocess})
        except (OSError, OcrError) as exc:
            shutdown()
            raise OcrError(str(exc)) from exc


def shutdown() -> None:
    global _WORKER
    with _LOCK:
        if _WORKER is not None:
            _WORKER.close()
            _WORKER = None


atexit.register(shutdown)

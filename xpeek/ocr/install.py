"""Installer helpers; downloads and model storage stay in the managed environment."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from .runtime import (
    DEFAULT_ORDER,
    ENGINES,
    MODEL_HASHES,
    PADDLE_REQUIREMENTS,
    PYTHON_HASHES,
    PYTHON_RELEASE,
    PYTHON_VERSION,
    model_names,
    worker_environment,
)


def parse_engines(raw: str) -> list[str]:
    names = [name.strip() for name in raw.split(",")]
    if names == ["all"]:
        return list(ENGINES)
    if not names or any(name not in ENGINES for name in names):
        raise ValueError("Choose 'all' or one or more comma-separated engines: " + ", ".join(ENGINES))
    return [name for name in ENGINES if name in names]


def select_engines(raw: str | None, installed: list[str] | None = None) -> list[str]:
    if raw is not None:
        return parse_engines(raw)
    if not sys.stdin.isatty():
        raise RuntimeError("Non-interactive install requires --ocr-engines ENGINE[,ENGINE...]")
    defaults = [name for name in (installed or ["paddleocr-tiny"]) if name in ENGINES]
    if not defaults:
        defaults = ["paddleocr-tiny"]
    print("Choose OCR engines to install (comma-separated numbers or names, or all):")
    descriptions = ("current lightweight ONNX engine", "best tested speed/meaning balance", "best tested accuracy, slower")
    for index, (engine, description) in enumerate(zip(ENGINES, descriptions), 1):
        mark = "*" if engine in defaults else " "
        print(f"  {index}. [{mark}] {engine}: {description}")
    print("Unselected engines will be removed from this managed installation.")
    while True:
        try:
            answer = input("Engines [" + ", ".join(defaults) + "]: ").strip()
        except EOFError as exc:
            raise RuntimeError("No engine selection; use --ocr-engines for unattended installs") from exc
        if not answer:
            return defaults
        choices = answer.replace(" ", ",").split(",")
        names = [ENGINES[int(choice) - 1] if choice in ("1", "2", "3") else choice for choice in choices if choice]
        try:
            return parse_engines(",".join(names))
        except ValueError as exc:
            print(exc)


def default_engine(engines: list[str]) -> str:
    return next(engine for engine in DEFAULT_ORDER if engine in engines)


def installed_engines(environment: Path) -> list[str]:
    """Discover installed engines when no selection has been recorded yet."""
    selected = []
    python = environment / "bin" / "python"
    if python.is_file():
        result = subprocess.run(
            [str(python), "-c", "import importlib.util; print(bool(importlib.util.find_spec('rapidocr_onnxruntime')))"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "True":
            selected.append("rapidocr")
    root = environment / "ocr"
    if (root / "python" / "bin" / "python3").is_file():
        for engine in ENGINES[1:]:
            if all((root / "models" / model / filename).is_file()
                   for model in model_names(engine) for filename in MODEL_HASHES[model]):
                selected.append(engine)
    return selected


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, checksum: str | None = None) -> None:
    request = Request(url, headers={"User-Agent": "xpeek-installer"})
    with urlopen(request, timeout=60) as source, destination.open("wb") as output:
        shutil.copyfileobj(source, output)
    if checksum is not None and file_hash(destination) != checksum:
        destination.unlink()
        raise RuntimeError("Downloaded Python archive failed SHA-256 verification")


def install_python(root: Path) -> Path:
    python = root / "python" / "bin" / "python3"
    marker = root / "python" / ".xpeek-python.json"
    expected = {"version": PYTHON_VERSION, "release": PYTHON_RELEASE}
    if python.is_file() and marker.is_file() and json.loads(marker.read_text()) == expected:
        return python
    architecture = platform.machine()
    if architecture not in PYTHON_HASHES or platform.libc_ver()[0] != "glibc":
        raise RuntimeError("Managed Paddle requires Linux with glibc on x86_64; choose rapidocr instead.")
    root.mkdir(parents=True, exist_ok=True)
    filename = f"cpython-{PYTHON_VERSION}%2B{PYTHON_RELEASE}-{architecture}-unknown-linux-gnu-install_only_stripped.tar.gz"
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_RELEASE}/{filename}"
    print(f"Downloading verified Python {PYTHON_VERSION} for Paddle...", flush=True)
    with tempfile.TemporaryDirectory(prefix="python-install-", dir=root) as directory:
        staging = Path(directory)
        archive_path = staging / "python.tar.gz"
        download(url, archive_path, PYTHON_HASHES[architecture])
        with tarfile.open(archive_path) as archive:
            if hasattr(tarfile, "data_filter"):
                archive.extractall(staging, filter="data")
            else:  # Verified upstream archive; also reject paths escaping staging.
                for member in archive.getmembers():
                    target = (staging / member.name).resolve()
                    if not target.is_relative_to(staging):
                        raise RuntimeError("Unsafe path in Python archive")
                    if member.issym() or member.islnk():
                        link = (target.parent / member.linkname).resolve() if member.issym() else (staging / member.linkname).resolve()
                        if not link.is_relative_to(staging):
                            raise RuntimeError("Unsafe link in Python archive")
                archive.extractall(staging)
        if not (staging / "python" / "bin" / "python3").is_file():
            raise RuntimeError("Python archive contains no interpreter")
        if (root / "python").exists():
            shutil.rmtree(root / "python")
        shutil.move(str(staging / "python"), root / "python")
    marker.write_text(json.dumps(expected) + "\n")
    return python


def install_model(root: Path, name: str) -> None:
    destination = root / "models" / name
    hashes = MODEL_HASHES[name]
    if all((destination / filename).is_file() and file_hash(destination / filename) == checksum
           for filename, checksum in hashes.items()):
        return
    print(f"Downloading {name}...", flush=True)
    url = f"https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/{name}_infer.tar"
    with tempfile.TemporaryDirectory(prefix="model-install-", dir=root) as directory:
        staging = Path(directory)
        archive_path = staging / "model.tar"
        download(url, archive_path)
        extracted = staging / name
        extracted.mkdir()
        with tarfile.open(archive_path) as archive:
            for filename, checksum in hashes.items():
                candidates = [member for member in archive.getmembers()
                              if member.isfile() and Path(member.name).name == filename]
                if len(candidates) != 1:
                    raise RuntimeError(f"Missing or ambiguous {filename} in {name}")
                with archive.extractfile(candidates[0]) as source, (extracted / filename).open("wb") as output:
                    shutil.copyfileobj(source, output)
                if file_hash(extracted / filename) != checksum:
                    raise RuntimeError(f"{name}/{filename} does not match the benchmarked model checksum")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(extracted), destination)


def install_paddle(root: Path, engines: list[str]) -> None:
    selected = [engine for engine in engines if engine.startswith("paddleocr-")]
    if not selected:
        return
    python = install_python(root)
    environment = worker_environment(root)
    print("Installing isolated Paddle dependencies...", flush=True)
    subprocess.run([str(python), "-I", "-m", "pip", "install", *PADDLE_REQUIREMENTS],
                   env=environment, check=True)
    # Paddle's extras install contrib OpenCV. Use the benchmarked headless build
    # last so cv2 is consistent and has no additional GUI runtime requirements.
    subprocess.run([str(python), "-I", "-m", "pip", "install", "--force-reinstall", "--no-deps",
                    "opencv-python-headless==5.0.0.93"], env=environment, check=True)
    for engine in selected:
        for name in model_names(engine):
            install_model(root, name)
    subprocess.run([str(python), "-I", str(Path(__file__).with_name("worker.py")), str(root), "1"],
                   input="", text=True, env=environment, check=True)


def prune_paddle(root: Path, engines: list[str]) -> None:
    if not root.exists():
        return
    selected = [engine for engine in engines if engine.startswith("paddleocr-")]
    if not selected:
        shutil.rmtree(root)
        return
    wanted = {name for engine in selected for name in model_names(engine)}
    models = root / "models"
    if models.is_dir():
        for directory in models.iterdir():
            if directory.name in MODEL_HASHES and directory.name not in wanted:
                shutil.rmtree(directory)

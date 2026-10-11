"""Screen region selection and capture via `slurp` + `grim` (Wayland-native).

Both tools must be on PATH. On CachyOS/Arch: `pacman -S grim slurp`.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from .config import Region


class CaptureError(RuntimeError):
    """Raised when screenshot or region-selection tooling fails."""


class CaptureCancelled(CaptureError):
    """The requesting popup closed before capture completed."""


@dataclass(frozen=True)
class CaptureResult:
    image: Path
    backend: str
    warning: str | None = None


def capture_screen(
    region: Region, backend: str = "desktop", *, display: str | None = None,
    cancelled: Event | None = None,
) -> CaptureResult:
    """Capture natively when requested, falling back to the desktop on failure."""
    if cancelled is not None and cancelled.is_set():
        raise CaptureCancelled("Capture cancelled")
    warning = None
    if backend == "gamescope":
        from .gamescope_capture import capture_native
        try:
            return CaptureResult(capture_native(region, display, cancelled), "gamescope")
        except CaptureCancelled:
            raise
        except CaptureError as exc:
            warning = f"Gamescope capture unavailable ({exc}); using desktop capture."
    elif backend != "desktop":
        raise CaptureError("Unknown capture backend")
    if cancelled is not None and cancelled.is_set():
        raise CaptureCancelled("Capture cancelled")
    image = capture_region(region)
    if cancelled is not None and cancelled.is_set():
        image.unlink(missing_ok=True)
        raise CaptureCancelled("Capture cancelled")
    return CaptureResult(image, "desktop", warning)


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise CaptureError(
            f"Required tool '{name}' was not found on PATH. "
            f"Install it (e.g. `sudo pacman -S {name}`) and try again."
        )
    return path


def select_region() -> Region:
    """Interactively let the user drag-select a region using slurp.

    Returns the selected Region. Raises CaptureError if the user cancels
    (e.g. presses Escape) or slurp is missing.
    """
    _require_binary("slurp")
    try:
        result = subprocess.run(
            ["slurp", "-b", "00000080", "-s", "00000000",
             "-c", "ffffffcc", "-w", "1"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise CaptureError(f"Failed to launch slurp: {exc}") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise CaptureError(
            "Region selection was cancelled or failed"
            + (f": {stderr}" if stderr else ".")
        )

    return Region.from_slurp_output(result.stdout)


def capture_region(region: Region, out_path: Path | None = None) -> Path:
    """Capture the given region to a PNG file using grim and return its path.

    If out_path is not given, writes to a fresh temp file.
    """
    _require_binary("grim")

    temporary = out_path is None
    if temporary:
        fd, name = tempfile.mkstemp(prefix="screen-ocr-", suffix=".png")
        import os

        os.close(fd)
        out_path = Path(name)

    try:
        result = subprocess.run(
            ["grim", "-g", region.as_geometry(), str(out_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise CaptureError(f"grim failed to capture region: {stderr or 'unknown error'}")
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise CaptureError("grim produced no output image")
    except (OSError, CaptureError, subprocess.TimeoutExpired) as exc:
        if temporary:
            out_path.unlink(missing_ok=True)
        if isinstance(exc, CaptureError):
            raise
        if isinstance(exc, subprocess.TimeoutExpired):
            raise CaptureError("Desktop capture timed out") from exc
        raise CaptureError(f"Failed to capture region: {exc}") from exc

    return out_path

"""Copy text to the Wayland clipboard using wl-copy."""
from __future__ import annotations

import shutil
import subprocess


class ClipboardError(RuntimeError):
    """Raised when clipboard tooling is missing or fails."""


def copy_text(text: str) -> None:
    executable = shutil.which("wl-copy")
    if executable is None:
        raise ClipboardError(
            "Required tool 'wl-copy' was not found on PATH. "
            "Install it with `sudo pacman -S wl-clipboard` and try again."
        )
    try:
        result = subprocess.run(
            [executable, "--type", "text/plain;charset=utf-8"],
            input=text,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ClipboardError(f"Failed to launch wl-copy: {exc}") from exc
    if result.returncode != 0:
        raise ClipboardError(
            f"wl-copy failed: {result.stderr.strip() or 'unknown error'}"
        )

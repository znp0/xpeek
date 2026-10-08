"""Copy text to the Wayland clipboard using wl-copy."""
from __future__ import annotations

import shutil
import subprocess
import tempfile


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
        # The background clipboard owner inherits stderr. Capturing it through
        # a pipe would wait for EOF until another application replaces the text.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errors:
            result = subprocess.run(
                [executable, "--type", "text/plain;charset=utf-8"],
                input=text,
                encoding="utf-8",
                stdout=subprocess.DEVNULL,
                stderr=errors,
                check=False,
            )
            if result.returncode != 0:
                errors.seek(0)
                raise ClipboardError(
                    f"wl-copy failed: {errors.read().strip() or 'unknown error'}"
                )
    except OSError as exc:
        raise ClipboardError(f"Failed to launch wl-copy: {exc}") from exc

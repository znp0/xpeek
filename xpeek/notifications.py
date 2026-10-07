"""Best-effort desktop notifications via notify-send."""
from __future__ import annotations

import subprocess


def notify_clipboard_copied() -> None:
    """Confirm copying without making notification support a requirement."""
    try:
        subprocess.run(
            [
                "notify-send", "--app-name", "xpeek",
                "--expire-time", "2000",
                "Copied to clipboard", "OCR text is ready to paste.",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        pass

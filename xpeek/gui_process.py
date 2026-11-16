"""Launch the shared popup independently of the invoking terminal."""
from __future__ import annotations

import json
import logging
import os
import select
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from .providers import API_KEY_ENV

if TYPE_CHECKING:
    from .config import Config, Region


def run_gui_translation(
    config: Config, mode: str = "translate", *, persistent: bool | None = None,
    region: Region | None = None, temporary: bool = False, display_limit: int = 10,
    phone_port: int | None = None,
) -> int:
    request = {"config": asdict(config), "mode": mode, "persistent": persistent,
               "region": asdict(region) if region is not None else None,
               "temporary": temporary, "display_limit": display_limit, "phone_port": phone_port,
               "gamescope_display": os.environ.get("GAMESCOPE_WAYLAND_DISPLAY"),
               "credentials": {name: os.environ.get(name) for name in API_KEY_ENV.values()}}
    return launch(request)


def launch(request: dict) -> int:
    ready_read, ready_write = os.pipe()
    try:
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "xpeek.gui_process", str(ready_write)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors,
                    pass_fds=(ready_write,), start_new_session=True,
                )
            except OSError as exc:
                print(f"Cannot start the popup: {exc}", file=sys.stderr)
                return 1
            os.close(ready_write)
            ready_write = -1
            try:
                process.stdin.write(json.dumps(request).encode())
                process.stdin.close()
                deadline = time.monotonic() + 20 if request["mode"] == "phone" else None
                while True:
                    remaining = None if deadline is None else max(0, deadline - time.monotonic())
                    readable, _, _ = select.select([ready_read], [], [], remaining)
                    if not readable:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        print("Cannot start phone pairing: the popup did not respond within 20 seconds.", file=sys.stderr)
                        return 1
                    result = os.read(ready_read, 32)
                    status = int(result.strip()) if result.strip() in (b"0", b"1") else 1
                    break
            except (BrokenPipeError, OSError) as exc:
                print(f"Cannot contact the popup: {exc}", file=sys.stderr)
                status = 1
            errors.seek(0)
            message = errors.read(65536).decode(errors="replace")
            if message:
                print(message, end="", file=sys.stderr)
            if process.poll() is not None:
                process.wait()
            return status
    finally:
        os.close(ready_read)
        if ready_write != -1:
            os.close(ready_write)


class _PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8")


def _configure_notices():
    # Only lifecycle notices are logged; request payloads and credentials are never logged.
    directory = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "xpeek"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "popup.log"
    handler = _PrivateRotatingHandler(path, maxBytes=65536, backupCount=1)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger = logging.getLogger("xpeek.phone")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def main() -> int:
    ready = int(sys.argv[1])
    notified = False

    def notify(status):
        nonlocal notified
        if notified:
            return
        notified = True
        # Close inherited terminal/temporary streams before acknowledging the caller.
        sys.stderr.flush()
        with open(os.devnull, "rb+") as null:
            for descriptor in (0, 1, 2):
                os.dup2(null.fileno(), descriptor)
        try:
            os.write(ready, f"{status}\n".encode())
        except OSError:
            pass  # The terminal may have closed while startup was in progress.
        finally:
            os.close(ready)
        try:
            _configure_notices()
        except OSError:
            pass  # Unwritable diagnostic storage must not prevent the popup from running.

    try:
        request = json.load(sys.stdin)
        from .gui import _run_gui_translation
        status = _run_gui_translation(request, on_ready=notify)
        notify(status)
        return status
    except ImportError as exc:
        print(f"Error loading GUI: {exc}", file=sys.stderr)
        notify(1)
        return 1
    except Exception:  # noqa: BLE001 - Report startup failures through the private acknowledgement pipe.
        import traceback
        traceback.print_exc()
        notify(1)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

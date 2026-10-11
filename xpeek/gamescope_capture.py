"""Native gamescope screenshots mapped from niri's logical window geometry."""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from PIL import Image

from .capture import CaptureCancelled, CaptureError, _require_binary
from .config import Region

NATIVE_TIMEOUT = 2.0


def _remaining(deadline: float, cancelled: Event | None = None) -> float:
    if cancelled is not None and cancelled.is_set():
        raise CaptureCancelled("Capture cancelled")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CaptureError("native capture timed out")
    return remaining


def _niri_snapshot(deadline: float, cancelled: Event | None) -> dict:
    path = os.environ.get("NIRI_SOCKET")
    if not path:
        raise CaptureError("niri IPC is unavailable")
    result = {}
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(_remaining(deadline, cancelled))
        connection.connect(path)
        with connection.makefile("rb") as stream:
            for request in ("Windows", "Workspaces", "Outputs", "OverviewState"):
                connection.settimeout(_remaining(deadline, cancelled))
                connection.sendall(json.dumps(request).encode() + b"\n")
                reply = json.loads(stream.readline())
                result[request] = reply["Ok"][request]
    if result["OverviewState"]["is_open"]:
        raise CaptureError("niri overview is open")
    return result


class _OptionsParser(argparse.ArgumentParser):
    def error(self, message):
        # Never expose a game's command line in an error message.
        raise CaptureError("gamescope render options could not be verified")


def _render_size(arguments: list[str], *, require_fullscreen: bool = False) -> tuple[int, int]:
    parser = _OptionsParser(add_help=False, allow_abbrev=False)
    parser.add_argument("-w", "--nested-width", type=int)
    parser.add_argument("-h", "--nested-height", type=int)
    parser.add_argument("-S", "--scaler", default="auto")
    parser.add_argument("-m", "--max-scale")
    parser.add_argument("-b", "--borderless", action="store_true")
    parser.add_argument("-f", "--fullscreen", action="store_true")
    options = arguments[:arguments.index("--")] if "--" in arguments else arguments
    parsed, _ = parser.parse_known_args(options)
    if not (parsed.borderless or parsed.fullscreen):
        raise CaptureError("only fullscreen or borderless gamescope is supported")
    if require_fullscreen and not parsed.fullscreen:
        raise CaptureError("niri does not expose this game's position")
    if parsed.scaler not in ("auto", "fit") or parsed.max_scale is not None:
        raise CaptureError("custom gamescope scaling is unsupported")
    if not parsed.nested_width or not parsed.nested_height or min(parsed.nested_width, parsed.nested_height) <= 0:
        raise CaptureError("explicit gamescope -w and -h dimensions are required")
    return parsed.nested_width, parsed.nested_height


def _gamescope_process(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/exe").resolve(strict=True).name == "gamescope"
    except OSError:
        return False


def _owned_display(pid: int, requested: str | None) -> str:
    inodes = set()
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[8:-1])
    paths = set()
    for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
        fields = line.split(maxsplit=7)
        if len(fields) != 8 or fields[6] not in inodes:
            continue
        path = Path(fields[7])
        # The sibling -ei socket speaks libei, not the Wayland control protocol.
        if (not path.is_absolute() or not path.name.startswith("gamescope-")
                or path.name.endswith("-ei")):
            continue
        try:
            info = path.stat()
            if info.st_uid == os.getuid() and stat.S_ISSOCK(info.st_mode):
                paths.add(str(path))
        except OSError:
            continue
    if requested:
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not Path(requested).is_absolute() and not runtime:
            raise CaptureError("Wayland runtime directory is unavailable")
        selected = str(Path(requested) if Path(requested).is_absolute() else Path(runtime) / requested)
        if selected not in paths:
            raise CaptureError("requested gamescope socket does not belong to the selected game")
        return selected
    if len(paths) != 1:
        raise CaptureError("gamescope socket is unavailable or ambiguous")
    return paths.pop()


@dataclass(frozen=True)
class NativeContext:
    window_id: int
    pid: int
    output: str
    display: str
    bounds: tuple[float, float, float, float]
    render_size: tuple[int, int]
    output_geometry: tuple


def _contains(bounds: tuple, region: Region) -> bool:
    x, y, width, height = bounds
    return (region.width > 0 and region.height > 0 and x <= region.x and y <= region.y
            and region.x + region.width <= x + width and region.y + region.height <= y + height)


def _context(snapshot: dict, region: Region, requested: str | None) -> NativeContext:
    candidates = []
    for window in snapshot["Windows"]:
        pid = window.get("pid")
        if not pid or not _gamescope_process(pid):
            continue
        workspace = next((ws for ws in snapshot["Workspaces"] if ws["id"] == window["workspace_id"]), None)
        if workspace is None or not workspace["is_active"]:
            continue
        output = snapshot["Outputs"].get(workspace["output"])
        if not output or not output.get("logical"):
            continue
        logical = output["logical"]
        layout = window["layout"]
        tile = layout["tile_pos_in_workspace_view"]
        width, height = layout["window_size"]
        if tile is None:
            # niri exposes positions only for floating tiles. An active,
            # output-sized fullscreen game has a verifiable output origin.
            if (workspace.get("active_window_id") != window["id"]
                    or abs(width - logical["width"]) > 1
                    or abs(height - logical["height"]) > 1):
                continue
            arguments = Path(f"/proc/{pid}/cmdline").read_bytes().decode().rstrip("\0").split("\0")[1:]
            _render_size(arguments, require_fullscreen=True)
            tile = (0, 0)
        offset = layout["window_offset_in_tile"]
        bounds = (logical["x"] + tile[0] + offset[0], logical["y"] + tile[1] + offset[1], width, height)
        if _contains(bounds, region):
            candidates.append((window, output, bounds))
    if len(candidates) != 1:
        raise CaptureError("no unambiguous visible gamescope window contains the region")
    window, output, bounds = candidates[0]
    logical = output["logical"]
    if logical["transform"] != "Normal":
        raise CaptureError("rotated outputs are unsupported")
    if not all(math.isfinite(value) for value in bounds) or min(bounds[2:]) <= 0:
        raise CaptureError("invalid gamescope window geometry")
    # niri floors logical output dimensions, while toplevels can round up.
    if not _contains((logical["x"] - 1, logical["y"] - 1,
                      logical["width"] + 2, logical["height"] + 2),
                     Region(*bounds)):
        raise CaptureError("gamescope window extends beyond its output")
    arguments = Path(f'/proc/{window["pid"]}/cmdline').read_bytes().decode().rstrip("\0").split("\0")[1:]
    size = _render_size(arguments)
    # Permit only integer logical-size rounding, not letterboxing or stretching.
    if abs(bounds[2] - bounds[3] * size[0] / size[1]) > 2:
        raise CaptureError("game and window aspect ratios differ")
    display = _owned_display(window["pid"], requested)
    return NativeContext(window["id"], window["pid"], output["name"], display, bounds, size,
                         (logical["x"], logical["y"], logical["width"], logical["height"], logical["scale"]))


def crop_box(region: Region, bounds: tuple, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Map logical crop edges outward into the native frame without resizing."""
    if not _contains(bounds, region):
        raise CaptureError("selected region extends outside the game")
    x, y, width, height = bounds
    native_width, native_height = size
    return (max(0, math.floor((region.x - x) * native_width / width)),
            max(0, math.floor((region.y - y) * native_height / height)),
            min(native_width, math.ceil((region.x + region.width - x) * native_width / width)),
            min(native_height, math.ceil((region.y + region.height - y) * native_height / height)))


def capture_native(region: Region, display: str | None, cancelled: Event | None) -> Path:
    deadline = time.monotonic() + NATIVE_TIMEOUT
    cropped = None
    try:
        tool = _require_binary("gamescopectl")
        context = _context(_niri_snapshot(deadline, cancelled), region, display)
        with tempfile.TemporaryDirectory(prefix="xpeek-gamescope-") as directory:
            frame = Path(directory) / "frame.png"
            environment = {**os.environ, "GAMESCOPE_WAYLAND_DISPLAY": context.display}
            result = subprocess.run([tool, "screenshot", str(frame)], env=environment,
                                    capture_output=True, timeout=_remaining(deadline, cancelled), check=False)
            if result.returncode:
                raise CaptureError("gamescopectl screenshot failed")
            image = None
            while image is None:
                _remaining(deadline, cancelled)
                try:
                    with Image.open(frame) as pending:
                        if pending.format != "PNG":
                            raise CaptureError("gamescope did not produce a PNG")
                        pending.verify()
                    with Image.open(frame) as pending:
                        pending.load()
                        image = pending.copy()
                except (OSError, SyntaxError):
                    # The command returns before gamescope's PNG writer completes.
                    delay = min(0.02, _remaining(deadline, cancelled))
                    if cancelled is not None:
                        cancelled.wait(delay)
                    else:
                        time.sleep(delay)
            with image:
                if image.size != context.render_size:
                    raise CaptureError("screenshot does not match the verified render resolution")
                if _context(_niri_snapshot(deadline, cancelled), region, display) != context:
                    raise CaptureError("game geometry changed during capture")
                with image.crop(crop_box(region, context.bounds, image.size)) as crop:
                    fd, name = tempfile.mkstemp(prefix="xpeek-native-", suffix=".png")
                    os.close(fd)
                    cropped = Path(name)
                    crop.save(cropped)
            _remaining(deadline, cancelled)
        return cropped
    except CaptureCancelled:
        if cropped is not None:
            cropped.unlink(missing_ok=True)
        raise
    except (CaptureError, OSError, ValueError, KeyError, IndexError, TypeError,
            Image.DecompressionBombError, subprocess.TimeoutExpired) as exc:
        if cropped is not None:
            cropped.unlink(missing_ok=True)
        if isinstance(exc, CaptureError):
            raise
        raise CaptureError("native capture could not be verified") from exc

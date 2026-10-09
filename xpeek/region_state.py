"""Persist the selected capture region separately from user settings."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from .config import ConfigError, Region

REGION_PATH = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    / "xpeek" / "region.json"
)


def _parse_region(raw: dict) -> Region:
    region = Region(**raw)
    for name in ("x", "y", "width", "height"):
        value = getattr(region, name)
        if type(value) is not int or abs(value) > 2**31 - 1:
            raise ValueError("Region coordinates must be integers")
    if region.width <= 0 or region.height <= 0:
        raise ValueError("Region dimensions must be positive")
    return region


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_region(path: Path = REGION_PATH) -> Region | None:
    try:
        return _parse_region(json.loads(path.read_text()))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigError(f"Cannot load saved region at {path}. Run `xpeek select` to replace it.") from exc


def save_region(region: Region, path: Path = REGION_PATH) -> None:
    try:
        _write_json(path, asdict(_parse_region(asdict(region))))
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigError(f"Cannot save selected region to {path}.") from exc

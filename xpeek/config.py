"""Persistent configuration for the screen OCR translator.

Config lives at $XDG_CONFIG_HOME/xpeek/config.json
(falls back to ~/.config/xpeek/config.json).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv
    # Support source checkouts and installations into the checkout's .venv.
    load_dotenv(Path(__file__).parent.parent / ".env")
    load_dotenv(Path(sys.prefix).parent / ".env")
    load_dotenv(Path.cwd() / ".env")
except ImportError:
    pass


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "xpeek"


def _data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "xpeek"


CONFIG_PATH = _config_dir() / "config.json"
HISTORY_PATH = _data_dir() / "history.json"


@dataclass
class Region:
    x: int
    y: int
    width: int
    height: int

    def as_geometry(self) -> str:
        """Format as a grim -g geometry string: 'X,Y WxH'."""
        return f"{self.x},{self.y} {self.width}x{self.height}"

    @classmethod
    def from_slurp_output(cls, raw: str) -> "Region":
        """Parse slurp's 'X,Y WxH' output into a Region."""
        raw = raw.strip()
        try:
            pos, size = raw.split(" ")
            x_str, y_str = pos.split(",")
            w_str, h_str = size.split("x")
            return cls(x=int(x_str), y=int(y_str), width=int(w_str), height=int(h_str))
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Could not parse slurp output: {raw!r}") from exc


@dataclass
class Config:
    region: Optional[Region] = None
    history_limit: int = 100
    provider: str = "google"
    source_lang: str = "en"
    target_lang: str = "vi"
    overlay_enabled: bool = False
    ocr_lang: str = "en"
    # Free-form per-provider options, e.g. {"deepl": {"api_key": "..."}, "ollama": {"model": "llama3.1", "host": "http://localhost:11434"}}
    provider_options: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Config file at {path} is corrupted: {exc}") from exc

        region_raw = raw.pop("region", None)
        region = Region(**region_raw) if region_raw else None
        cfg = cls(region=region, **raw)
        return cfg

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)

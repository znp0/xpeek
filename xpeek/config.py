"""Persistent configuration for xpeek.

Config lives at $XDG_CONFIG_HOME/xpeek/config.json
(falls back to ~/.config/xpeek/config.json).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict

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


class ConfigError(RuntimeError):
    """Raised when user configuration is missing or invalid."""


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
    history_limit: int = 100
    provider: str = "google"
    source_lang: str = "auto"
    target_lang: str = "en"
    persistent_window: bool = False
    # Per-provider settings such as models and hosts; keys can come from .env.
    provider_options: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            raise ConfigError(
                f"Config file not found: {path}. Run `python3 setup.py install` first."
            )
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Config file at {path} is corrupted: {exc}") from exc

        try:
            config = cls(**raw)
            if type(config.persistent_window) is not bool:
                raise ValueError("persistent_window must be true or false")
            return config
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid config file at {path}: {exc}") from exc

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)

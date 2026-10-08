"""Remember popup geometry independently of OCR and translation settings."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
import tempfile


WINDOW_STATE_PATH = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    / "xpeek" / "window-state.json"
)
MIN_WIDTH = 240
MIN_HEIGHT = 120


@dataclass
class WindowState:
    width: int = 500
    height: int = 400
    x: int | None = None  # None means upper-right on the first opening.
    y: int = 24
    output: str | None = None

    @classmethod
    def load(cls, path: Path = WINDOW_STATE_PATH) -> "WindowState":
        try:
            raw = json.loads(path.read_text())
            state = cls(**raw)
            for key in ("width", "height", "y"):
                value = getattr(state, key)
                if type(value) is not int or not 0 <= value <= 2**31 - 1:
                    raise ValueError("Invalid geometry")
            if state.width == 0 or state.height == 0:
                raise ValueError("Invalid size")
            if state.x is not None and (
                type(state.x) is not int or not 0 <= state.x <= 2**31 - 1
            ):
                raise ValueError("Invalid position")
            if state.output is not None and not isinstance(state.output, str):
                raise ValueError("Invalid output")
            return state
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError):
            print("Ignoring unreadable or invalid popup state.", file=sys.stderr)
            return cls()

    def clamp(self, width: int, height: int) -> None:
        """Keep all of the popup inside an output, using logical pixels."""
        self.width = min(max(MIN_WIDTH, self.width), max(1, width))
        self.height = min(max(MIN_HEIGHT, self.height), max(1, height))
        x = width - self.width - 24 if self.x is None else self.x
        self.x = max(0, min(x, width - self.width))
        self.y = max(0, min(self.y, height - self.height))

    def relocate(self, old: tuple[int, int, int, int],
                 new: tuple[int, int, int, int],
                 size: tuple[int, int] | None = None) -> None:
        """Transfer proportional edge gaps between outputs in the old layout."""
        old_left, old_top, old_width, old_height = old
        new_left, new_top, layout_width, layout_height = new
        new_width, new_height = size or (layout_width, layout_height)
        self.clamp(old_width, old_height)
        x, y, width, height = self.x, self.y, self.width, self.height
        self.clamp(new_width, new_height)

        if old_left >= new_left + layout_width:
            # Output on the right disappeared: its left gap becomes a right gap.
            self.x = round(new_width - self.width - x * new_width / old_width)
        elif new_left >= old_left + old_width:
            self.x = round((old_width - width - x) * new_width / old_width)
        else:
            self.x = round(x * new_width / old_width)

        if old_top >= new_top + layout_height:
            self.y = round(new_height - self.height - y * new_height / old_height)
        elif new_top >= old_top + old_height:
            self.y = round((old_height - height - y) * new_height / old_height)
        else:
            self.y = round(y * new_height / old_height)
        self.clamp(new_width, new_height)

    def save(self, path: Path = WINDOW_STATE_PATH) -> None:
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".window-state-", dir=path.parent)
            temporary = Path(name)
            with os.fdopen(fd, "w") as handle:
                json.dump(asdict(self), handle, indent=2)
                handle.write("\n")
            temporary.replace(path)
        except OSError as exc:
            print(f"Could not save popup state: {exc}", file=sys.stderr)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

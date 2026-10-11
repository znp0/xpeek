"""Scrollable terminal view of saved translations, refreshed without losing position."""
from __future__ import annotations

import curses
import json
import sys
import time
import unicodedata
from pathlib import Path

from .history import HistoryEntry


def _wrap(text: str, width: int) -> list[str]:
    """Wrap by terminal cells, retaining explicit newlines and Unicode text."""
    lines = []
    for paragraph in text.expandtabs(4).splitlines() or [""]:
        line = ""
        cells = 0
        for character in paragraph:
            category = unicodedata.category(character)
            if category.startswith("C"):
                continue
            size = 0 if unicodedata.combining(character) else (
                2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            )
            if cells + size > width and line:
                lines.append(line)
                line, cells = "", 0
            if size > width:
                character, size = "?", 1
            line += character
            cells += size
        lines.append(line)
    return lines


class _View:
    def __init__(self) -> None:
        self.entries: list[HistoryEntry] = []
        self.rows: list[tuple[HistoryEntry | None, int, str]] = []
        self.width = 0
        self.height = 0
        self.top = 0

    @property
    def bottom(self) -> int:
        return max(0, len(self.rows) - self.height)

    def update(self, entries: list[HistoryEntry], width: int, height: int) -> bool:
        if (entries, width, height) == (self.entries, self.width, self.height):
            return False
        following = self.top == self.bottom
        anchor = self.rows[self.top][:2] if self.top < len(self.rows) else None
        if entries != self.entries or width != self.width:
            self.rows = []
            for entry in entries:
                text = f"[{entry.timestamp}]\nOCR: {entry.ocr_text}\nTranslation: {entry.translation}\n"
                for index, line in enumerate(_wrap(text, width)):
                    self.rows.append((entry, index, line))
                self.rows.append((entry, index + 1, ""))
            if not self.rows:
                self.rows = [(None, 0, "No history yet. Waiting for translations…")]
        self.entries, self.width, self.height = entries, width, height
        if following:
            self.top = self.bottom
        elif anchor:
            self.top = next(
                (index for index, row in enumerate(self.rows) if row[:2] == anchor), 0,
            )
            self.top = min(self.top, self.bottom)
        return True

    def scroll(self, amount: int) -> bool:
        previous = self.top
        self.top = max(0, min(self.bottom, self.top + amount))
        return self.top != previous


def _signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


def _read_entries(path: Path, limit: int) -> list[HistoryEntry]:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    entries = [HistoryEntry(**item) for item in raw[-limit:]]
    if any(not all(isinstance(value, str) for value in (
        entry.timestamp, entry.ocr_text, entry.translation,
    )) for entry in entries):
        raise ValueError("Invalid history entry")
    return entries


def _draw(screen, view: _View, limit: int, error: str) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    following = "following" if view.top == view.bottom else "scrolled"
    title = f"xpeek history · latest {limit} ({len(view.entries)} shown) · {following}"
    footer = error or "↑/↓ · PgUp/PgDn · Home/End · mouse wheel · Ctrl+C or q to exit"
    lines = [(0, title, curses.A_BOLD)]
    for index, (_, row, text) in enumerate(view.rows[view.top:view.top + view.height], 1):
        lines.append((index, text, curses.A_BOLD if row == 0 else curses.A_NORMAL))
    if height > 1:
        lines.append((height - 1, footer, curses.A_REVERSE))
    for y, text, attribute in lines:
        if y >= height:
            continue
        try:
            screen.addstr(y, 0, _wrap(text, max(1, width))[0], attribute)
        except curses.error:
            # Writing the bottom-right cell and very small terminals can raise.
            pass
    screen.refresh()


def _watch(screen, path: Path, limit: int) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.mouseinterval(0)
    screen.timeout(100)
    view = _View()
    signature = object()
    entries: list[HistoryEntry] = []
    error = ""
    next_poll = 0.0
    dirty = True
    while True:
        now = time.monotonic()
        if now >= next_poll:
            next_poll = now + 0.5
            try:
                current = _signature(path)
                if current != signature or error:
                    entries = _read_entries(path, limit)
                    signature = current
                    error = ""
                    dirty = True
            except (OSError, ValueError, TypeError) as exc:
                message = f"Cannot read history ({type(exc).__name__}); retrying…"
                dirty |= message != error
                error = message
        height, width = screen.getmaxyx()
        dirty |= view.update(entries, max(1, width), max(0, height - 2))
        if dirty:
            _draw(screen, view, limit, error)
            dirty = False
        key = screen.getch()
        if key in (3, ord("q")):
            return
        amount = 0
        if key in (curses.KEY_UP, ord("k")):
            amount = -1
        elif key in (curses.KEY_DOWN, ord("j")):
            amount = 1
        elif key == curses.KEY_PPAGE:
            amount = -max(1, view.height - 1)
        elif key == curses.KEY_NPAGE:
            amount = max(1, view.height - 1)
        elif key == curses.KEY_HOME:
            amount = -view.top
        elif key == curses.KEY_END:
            amount = view.bottom - view.top
        elif key == curses.KEY_MOUSE:
            try:
                state = curses.getmouse()[4]
                if state & curses.BUTTON4_PRESSED:
                    amount = -3
                elif state & getattr(curses, "BUTTON5_PRESSED", 0):
                    amount = 3
            except curses.error:
                pass
        dirty |= view.scroll(amount)


def watch_history(path: Path, limit: int = 10) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Error: history --watch requires an interactive terminal.", file=sys.stderr)
        return 1
    try:
        curses.wrapper(_watch, path, limit)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        print(f"Error: cannot start history viewer: {exc}", file=sys.stderr)
        return 1
    return 0

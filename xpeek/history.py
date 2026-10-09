"""Translation history: bounded, persisted, ring-buffer-like storage."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import HISTORY_PATH


@dataclass
class HistoryEntry:
    timestamp: str
    ocr_text: str
    translation: str

    @staticmethod
    def now(ocr_text: str, translation: str) -> HistoryEntry:
        return HistoryEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ocr_text=ocr_text,
            translation=translation,
        )


class HistoryStore:
    """Loads/saves a capped list of HistoryEntry, oldest-first.

    When more than `limit` entries would be stored, the oldest are dropped.
    """

    def __init__(self, path: Path = HISTORY_PATH, limit: int = 100) -> None:
        self.path = path
        self.limit = limit
        self.entries: list[HistoryEntry] = self._load()

    def _load(self) -> list[HistoryEntry]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            # Corrupted history shouldn't crash the app; start fresh but
            # don't silently overwrite the bad file until the next save.
            return []
        return [HistoryEntry(**item) for item in raw]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(e) for e in self.entries]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def add(self, ocr_text: str, translation: str) -> HistoryEntry:
        entry = HistoryEntry.now(ocr_text, translation)
        self.entries.append(entry)
        if len(self.entries) > self.limit:
            self.entries = self.entries[-self.limit :]
        self.save()
        return entry

    def clear(self) -> None:
        self.entries = []
        self.save()

    def all(self) -> list[HistoryEntry]:
        return list(self.entries)

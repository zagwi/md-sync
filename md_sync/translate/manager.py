"""Translation mapping cache.

Stores translations keyed by the *original* (source) text. Each entry
holds translations for every target language we support, e.g.::

    {
      "<hash>": {
        "zh": "...",   # translation into Chinese (for en source)
        "en": "...",   # translation into English (for zh source)
        "status": "auto" | "done" | "pending"
      }
    }

The manager is symmetric: the "source" text is whatever the original
document was written in, and we can translate it *to* any target
language. This makes zh→en and en→zh both first-class.

Legacy helpers (``lookup``, ``store``, ``mark_pending`` with no target
language) are kept for backward compatibility and default to the ``en``
target.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _hash(text: str) -> str:
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()[:12]


class TranslationManager:
    """Persistent mapping of original text → {target_lang: translation}."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        self._dirty = False
        self.load()

    # ── persistence ─────────────────────────────────────────────────
    def load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def save(self) -> None:
        if self._dirty:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._dirty = False

    def all_entries(self) -> dict:
        return self._data

    # ── bidirectional API ──────────────────────────────────────────
    def lookup(self, source: str, target_lang: str = "en") -> str | None:
        """Return the cached translation of ``source`` into ``target_lang``."""
        key = _hash(source)
        entry = self._data.get(key)
        if not entry:
            return None
        return entry.get(target_lang) or None

    def get_status(self, source: str) -> str | None:
        key = _hash(source)
        entry = self._data.get(key)
        return entry.get("status") if entry else None

    def store(
        self, source: str, translation: str, target_lang: str = "en", status: str = "done"
    ) -> None:
        key = _hash(source)
        entry = self._data.setdefault(key, {})
        entry[target_lang] = translation
        entry["status"] = status
        self._dirty = True

    def mark_pending(self, source: str, target_lang: str = "en") -> None:
        key = _hash(source)
        entry = self._data.setdefault(key, {})
        if target_lang not in entry:
            entry[target_lang] = ""
        entry["status"] = "pending"
        self._dirty = True

    def has_translation(self, source: str, target_lang: str) -> bool:
        return bool(self.lookup(source, target_lang))

    def pending_entries(self, target_lang: str = "en"):
        """Yield ``(key, source_text, target_lang)`` for still-missing entries."""
        for key, entry in self._data.items():
            if not entry.get(target_lang):
                yield key, entry, target_lang

    # ── legacy (single-target) helpers ─────────────────────────────
    def lookup_legacy(self, source: str) -> str | None:
        return self.lookup(source, "en")

    def store_legacy(self, source: str, translation: str) -> None:
        self.store(source, translation, "en", status="done")

    def mark_pending_legacy(self, source: str) -> None:
        self.mark_pending(source, "en")

    def pending_count(self) -> int:
        return sum(1 for e in self._data.values() if not e.get("en"))

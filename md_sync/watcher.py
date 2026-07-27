"""File system watcher — monitors source MD file for changes.

Uses ``watchdog`` to detect file modifications and triggers
the sync pipeline with debouncing.
"""
from __future__ import annotations

import time
from pathlib import Path
from threading import Event, Thread
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class _ChangeHandler(FileSystemEventHandler):
    """Watchdog handler that triggers callback on file modifications."""

    def __init__(
        self,
        source_path: Path,
        callback: Callable[[Path], None],
        debounce: float = 1.5,
    ):
        self._source = source_path.resolve()
        self._callback = callback
        self._debounce = debounce
        self._last_trigger: float = 0

    def on_modified(self, event):
        if event.is_directory:
            return
        event_path = Path(event.src_path).resolve()
        if event_path != self._source:
            return

        now = time.time()
        if now - self._last_trigger < self._debounce:
            return
        self._last_trigger = now

        # Small delay to ensure file write is complete
        time.sleep(0.3)
        self._callback(event_path)


class FileWatcher:
    """Monitor a source MD file and trigger sync on changes."""

    def __init__(
        self,
        source_path: Path | str,
        on_change: Callable[[Path], None],
        debounce: float = 1.5,
    ):
        self._source = Path(source_path).resolve()
        self._on_change = on_change
        self._debounce = debounce
        self._observer: Optional[Observer] = None
        self._thread: Optional[Thread] = None
        self._stop_event = Event()

    def start(self) -> None:
        """Start watching in a background thread."""
        if self._observer:
            return

        handler = _ChangeHandler(self._source, self._on_change, self._debounce)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._source.parent), recursive=False)

        self._thread = Thread(target=self._observer.start, daemon=True)
        self._thread.start()
        print(f"[watch] Watching: {self._source.name}")

    def stop(self) -> None:
        """Stop the watcher."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        print("[watch] Stopped")

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

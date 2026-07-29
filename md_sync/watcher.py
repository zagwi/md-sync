"""File system watcher — monitors source MD file for changes.

Uses ``watchdog`` to detect file modifications and triggers
the sync pipeline with debouncing.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class _ChangeHandler(FileSystemEventHandler):
    """Watchdog handler that triggers callback on source changes.

    Also catches atomic-save editors (e.g. some Markdown apps) that write a
    temp file then rename it onto the source path: created/moved events whose
    (dest) path resolves to the source are treated as edits too.
    """

    def __init__(
        self,
        source_path: Path,
        callback: Callable[[Path], None],
        debounce: float = 1.5,
        output_root: Path | None = None,
    ):
        self._source = source_path.resolve()
        self._callback = callback
        self._debounce = debounce
        self._last_trigger: float = 0
        # NOTE: we deliberately do NOT watch the output directory. Watching it
        # made the watcher fire on our own generated files, causing an infinite
        # regenerate loop. The only legitimate trigger is the source MD file.
        self._output_root = None

    def on_modified(self, event):
        if event.is_directory:
            return
        self._maybe_trigger(Path(event.src_path).resolve())

    def on_created(self, event):
        if event.is_directory:
            return
        self._maybe_trigger(Path(event.src_path).resolve())

    def on_deleted(self, event):
        if event.is_directory:
            return
        self._maybe_trigger(Path(event.src_path).resolve())

    def on_moved(self, event):
        dest = getattr(event, "dest_path", None)
        if dest:
            self._maybe_trigger(Path(dest).resolve())

    def _maybe_trigger(self, event_path: Path):
        # Only the source MD file is a legitimate trigger. Edits to anything
        # else (e.g. our own generated outputs) are ignored to avoid loops.
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
        output_root: Path | str | None = None,
    ):
        self._source = Path(source_path).resolve()
        self._on_change = on_change
        self._debounce = debounce
        self._output_root = Path(output_root).resolve() if output_root else None
        self._observer: Observer | None = None
        self._thread: Thread | None = None
        self._stop_event = Event()

    def start(self) -> None:
        """Start watching in a background thread."""
        if self._observer:
            return

        handler = _ChangeHandler(
            self._source, self._on_change, self._debounce, None)
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

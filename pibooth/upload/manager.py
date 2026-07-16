"""Background upload worker with a disk-persisted, crash-resilient queue."""

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pibooth.upload.base import UploadBackend
from pibooth.utils import LOGGER

#: Give up on an entry after this many failed attempts
DEFAULT_MAX_ATTEMPTS = 8
#: Oldest 'done' entries are dropped once the journal exceeds this size
MAX_JOURNAL_ENTRIES = 200


def default_backoff(attempts: int) -> float:
    """Return the number of seconds to wait before the next attempt
    (exponential backoff capped at 15 minutes).
    """
    return min(60 * 2**attempts, 900)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class UploadManager:
    """Uploads pictures in a background thread, retrying on failure.

    Every enqueued file is recorded in a JSON journal on disk so pending or
    failed uploads survive a pibooth restart.
    """

    def __init__(
        self,
        journal_path: str,
        backend: UploadBackend | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_fn: "Callable[[int], float] | None" = None,
    ) -> None:
        self.journal_path = journal_path
        self.max_attempts = max_attempts
        self.backoff_fn = backoff_fn or default_backoff

        self._lock = threading.Lock()
        self._backend = backend
        self._entries: list[dict[str, Any]] = []
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._load()

    # ------------------------------------------------------------ journal

    def _load(self) -> None:
        if not os.path.isfile(self.journal_path):
            return
        try:
            with open(self.journal_path, encoding="utf-8") as fp:
                self._entries = json.load(fp)
        except (OSError, ValueError) as ex:
            LOGGER.warning("Cannot load upload journal '%s': %s", self.journal_path, ex)
            self._entries = []

    def _save(self) -> None:
        """Save the journal atomically (write to a tmp file then replace it)."""
        dirname = os.path.dirname(self.journal_path) or "."
        os.makedirs(dirname, exist_ok=True)
        tmp_path = f"{self.journal_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(self._entries, fp, indent=2)
        os.replace(tmp_path, self.journal_path)

    def _trim(self) -> None:
        """Keep at most MAX_JOURNAL_ENTRIES, dropping the oldest 'done' ones first."""
        while len(self._entries) > MAX_JOURNAL_ENTRIES:
            for index, entry in enumerate(self._entries):
                if entry["status"] == "done":
                    del self._entries[index]
                    break
            else:
                break  # No more 'done' entries to drop

    # -------------------------------------------------------------- public

    def set_backend(self, backend: "UploadBackend | None") -> None:
        with self._lock:
            self._backend = backend
        self._wakeup.set()

    def enqueue(self, filename: str) -> None:
        entry = {
            "id": uuid.uuid4().hex,
            "filename": filename,
            "backend": self._backend.id if self._backend is not None else None,
            "status": "pending",
            "attempts": 0,
            "error": None,
            "url": None,
            "added": _now_iso(),
            "updated": _now_iso(),
            "next_try": 0.0,
        }
        with self._lock:
            self._entries.append(entry)
            self._trim()
            self._save()
        self._wakeup.set()

    def retry_failed(self) -> None:
        with self._lock:
            changed = False
            for entry in self._entries:
                if entry["status"] == "failed":
                    entry["status"] = "pending"
                    entry["attempts"] = 0
                    entry["error"] = None
                    entry["next_try"] = 0.0
                    entry["updated"] = _now_iso()
                    changed = True
            if changed:
                self._save()
        self._wakeup.set()

    def entries(self) -> "list[dict[str, Any]]":
        """Return journal entries, newest first."""
        with self._lock:
            return [dict(entry) for entry in reversed(self._entries)]

    # -------------------------------------------------------------- worker

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pibooth-upload", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wakeup.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            processed = self._process_next()
            if not processed:
                self._wakeup.wait(timeout=1)
                self._wakeup.clear()

    def _process_next(self) -> bool:
        """Process a single due entry if any. Return True if one was processed."""
        now = time.time()
        with self._lock:
            backend = self._backend
            if backend is None:
                return False
            entry = None
            for candidate in self._entries:
                if candidate["status"] == "pending" and candidate.get("next_try", 0) <= now:
                    entry = candidate
                    break
            if entry is None:
                return False

        try:
            result = backend.upload(entry["filename"])
        except Exception as ex:
            with self._lock:
                entry["attempts"] += 1
                entry["error"] = str(ex)
                entry["updated"] = _now_iso()
                if entry["attempts"] >= self.max_attempts:
                    entry["status"] = "failed"
                    LOGGER.warning("Giving up uploading '%s': %s", entry["filename"], ex)
                else:
                    entry["next_try"] = time.time() + self.backoff_fn(entry["attempts"])
                    LOGGER.warning(
                        "Upload attempt %s/%s failed for '%s': %s",
                        entry["attempts"],
                        self.max_attempts,
                        entry["filename"],
                        ex,
                    )
                self._save()
            return True

        with self._lock:
            entry["status"] = "done"
            entry["error"] = None
            entry["url"] = result.url
            entry["updated"] = _now_iso()
            self._trim()
            self._save()
        LOGGER.info("Uploaded '%s'", entry["filename"])
        return True

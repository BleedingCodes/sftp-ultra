from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .model import Result

SCHEMA = """
CREATE TABLE IF NOT EXISTS transfer_journal (
    remote_path TEXT NOT NULL,
    local_path TEXT,
    status TEXT NOT NULL,
    remote_size INTEGER,
    remote_mtime INTEGER,
    checksum TEXT,
    message TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    run_kind TEXT NOT NULL DEFAULT 'session',
    PRIMARY KEY (remote_path, run_kind)
);
"""

# F12 (AUDIT-FINDINGS): the primary key used to be remote_path alone, so a
# dry run ("planned"/"skipped" rows) rewrote the last real statuses of the
# same paths with ON CONFLICT DO UPDATE — silently rewriting the audit
# history of a previous run. run_kind separates the two namespaces: a dry run
# only overwrites its own previous dry-run rows, real statuses are preserved.
DRY_RUN_KIND = "dry_run"
SESSION_KIND = "session"


class Journal:
    """Long-lived SQLite journal (F12):

    * one connection guarded by the lock, never per-record (the old per-write
      connect churned a file descriptor per record and relied on GC/refcount
      to actually be reclaimed — sqlite3's ``with`` commits but does not
      close);
    * check_same_thread=False because transfer_one() records from the worker
      pool, while __init__/close() run on the main thread;
    * explicit close() — run_plan() closes the journal in a finally.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = sqlite3.connect(self.path, check_same_thread=False)
            connection.execute(SCHEMA)
            self._connection = connection

    def record(
        self,
        result: Result,
        *,
        remote_size: int,
        remote_mtime: int,
        run_kind: str = SESSION_KIND,
    ) -> None:
        with self._lock, self._connection as connection:
            connection.execute(
                """
                INSERT INTO transfer_journal (
                    remote_path,
                    local_path,
                    status,
                    remote_size,
                    remote_mtime,
                    checksum,
                    message,
                    updated_at,
                    run_kind
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(remote_path, run_kind) DO UPDATE SET
                    local_path = excluded.local_path,
                    status = excluded.status,
                    remote_size = excluded.remote_size,
                    remote_mtime = excluded.remote_mtime,
                    checksum = excluded.checksum,
                    message = excluded.message,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    result.remote_path,
                    result.local_path,
                    result.status.value,
                    remote_size,
                    remote_mtime,
                    result.checksum,
                    result.message,
                    run_kind,
                ),
            )

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

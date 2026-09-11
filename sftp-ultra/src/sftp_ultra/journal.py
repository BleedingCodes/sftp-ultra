from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from .model import Result


SCHEMA = """
CREATE TABLE IF NOT EXISTS transfer_journal (
    remote_path TEXT PRIMARY KEY,
    local_path TEXT,
    status TEXT NOT NULL,
    remote_size INTEGER,
    remote_mtime INTEGER,
    checksum TEXT,
    message TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def record(
        self,
        result: Result,
        *,
        remote_size: int,
        remote_mtime: int,
    ) -> None:
        with self._lock, self._connect() as connection:
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
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(remote_path) DO UPDATE SET
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
                ),
            )

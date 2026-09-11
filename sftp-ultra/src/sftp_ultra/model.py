from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any


class OverwritePolicy(str, Enum):
    ASK = "ask"
    SKIP = "skip"
    REPLACE = "replace"
    RENAME = "rename"


class ChecksumMode(str, Enum):
    NONE = "none"
    SHA256 = "sha256"


class Status(str, Enum):
    PLANNED = "planned"
    SKIPPED = "skipped"
    COPIED = "copied"
    MOVED = "moved"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Config:
    target: str
    username: str
    port: int
    remote_root: PurePosixPath
    destination: Path
    workers: int
    retries: int
    timeout_seconds: int
    overwrite: OverwritePolicy
    checksum: ChecksumMode
    resume: bool
    delete_source: bool
    trust_unknown_host: bool
    bandwidth_limit_kib: int | None
    journal_path: Path
    manifest_path: Path | None
    report_path: Path | None
    dry_run: bool
    assume_yes: bool


@dataclass(frozen=True, slots=True)
class RemoteFile:
    path: PurePosixPath
    size: int
    mtime: int


@dataclass(frozen=True, slots=True)
class PlanItem:
    remote: RemoteFile
    local_path: Path
    action: str
    reason: str = ""


@dataclass(slots=True)
class Result:
    remote_path: str
    local_path: str | None
    status: Status
    bytes_transferred: int = 0
    resumed_from: int = 0
    attempts: int = 0
    checksum: str | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "remote_path": self.remote_path,
            "local_path": self.local_path,
            "status": self.status.value,
            "bytes_transferred": self.bytes_transferred,
            "resumed_from": self.resumed_from,
            "attempts": self.attempts,
            "checksum": self.checksum,
            "message": self.message,
        }

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Sequence

import paramiko

from .journal import Journal
from .model import ChecksumMode, Config, PlanItem, Result, Status
from .ssh import SFTPConnectionFactory


LOG = logging.getLogger("sftp_ultra")


class TokenBucket:
    def __init__(self, kib_per_second: int | None) -> None:
        self.rate = None if kib_per_second is None else kib_per_second * 1024
        self.tokens = float(self.rate or 0)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def consume(self, amount: int) -> None:
        if self.rate is None:
            return

        while True:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.updated
                self.updated = now
                self.tokens = min(
                    float(self.rate),
                    self.tokens + elapsed * self.rate,
                )

                if self.tokens >= amount:
                    self.tokens -= amount
                    return

                missing = amount - self.tokens
                sleep_for = missing / self.rate

            time.sleep(max(sleep_for, 0.001))


def sha256_file(path: Path, bucket: TokenBucket | None = None) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if bucket:
                bucket.consume(len(chunk))
            digest.update(chunk)

    return digest.hexdigest()


def remote_sha256(
    sftp: paramiko.SFTPClient,
    remote_path: str,
    bucket: TokenBucket | None = None,
) -> str:
    digest = hashlib.sha256()

    with sftp.open(remote_path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if bucket:
                bucket.consume(len(chunk))
            digest.update(chunk)

    return digest.hexdigest()


def copy_with_resume(
    sftp: paramiko.SFTPClient,
    remote_path: str,
    temporary_path: Path,
    *,
    resume: bool,
    bucket: TokenBucket,
) -> int:
    resumed_from = temporary_path.stat().st_size if resume and temporary_path.exists() else 0

    if resumed_from and not resume:
        temporary_path.unlink()
        resumed_from = 0

    mode = "ab" if resumed_from else "wb"

    with sftp.open(remote_path, "rb") as remote, temporary_path.open(mode) as local:
        if resumed_from:
            remote.seek(resumed_from)

        while chunk := remote.read(1024 * 1024):
            bucket.consume(len(chunk))
            local.write(chunk)

        local.flush()
        os.fsync(local.fileno())

    return resumed_from


def transfer_one(
    item: PlanItem,
    *,
    config: Config,
    factory: SFTPConnectionFactory,
    journal: Journal,
    bucket: TokenBucket,
) -> Result:
    remote = item.remote
    local_path = item.local_path

    if item.action == "skip":
        result = Result(
            remote_path=str(remote.path),
            local_path=str(local_path),
            status=Status.SKIPPED,
            message=item.reason,
        )
        journal.record(result, remote_size=remote.size, remote_mtime=remote.mtime)
        return result

    local_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = local_path.with_name(local_path.name + ".part")

    last_error = ""

    for attempt in range(1, config.retries + 2):
        try:
            with factory.open() as sftp:
                before = sftp.stat(str(remote.path))
                resumed_from = copy_with_resume(
                    sftp,
                    str(remote.path),
                    temporary_path,
                    resume=config.resume,
                    bucket=bucket,
                )
                after = sftp.stat(str(remote.path))

                if int(before.st_size) != int(after.st_size):
                    raise RuntimeError("Remote file size changed during transfer.")

                if int(before.st_mtime) != int(after.st_mtime):
                    raise RuntimeError("Remote file modification time changed during transfer.")

                local_size = temporary_path.stat().st_size
                if local_size != int(after.st_size):
                    raise RuntimeError(
                        f"Size mismatch: remote={after.st_size}, local={local_size}"
                    )

                checksum = None
                if config.checksum is ChecksumMode.SHA256:
                    local_hash = sha256_file(temporary_path, bucket)
                    remote_hash = remote_sha256(
                        sftp,
                        str(remote.path),
                        bucket,
                    )
                    if local_hash != remote_hash:
                        raise RuntimeError("SHA-256 checksum mismatch.")
                    checksum = local_hash

                os.replace(temporary_path, local_path)

                if config.delete_source:
                    sftp.remove(str(remote.path))
                    status = Status.MOVED
                else:
                    status = Status.COPIED

                result = Result(
                    remote_path=str(remote.path),
                    local_path=str(local_path),
                    status=status,
                    bytes_transferred=int(after.st_size),
                    resumed_from=resumed_from,
                    attempts=attempt,
                    checksum=checksum,
                )
                journal.record(
                    result,
                    remote_size=remote.size,
                    remote_mtime=remote.mtime,
                )
                return result

        except Exception as error:
            last_error = str(error)
            if attempt <= config.retries:
                delay = min(30.0, (2 ** (attempt - 1)) + random.random())
                LOG.warning(
                    "Retrying %s after error: %s",
                    remote.path,
                    error,
                )
                time.sleep(delay)

    result = Result(
        remote_path=str(remote.path),
        local_path=str(local_path),
        status=Status.FAILED,
        attempts=config.retries + 1,
        message=last_error,
    )
    journal.record(result, remote_size=remote.size, remote_mtime=remote.mtime)
    return result


def run_plan(
    plan: Sequence[PlanItem],
    *,
    config: Config,
    factory: SFTPConnectionFactory,
) -> list[Result]:
    journal = Journal(config.journal_path)
    bucket = TokenBucket(config.bandwidth_limit_kib)

    if config.dry_run:
        return [
            Result(
                remote_path=str(item.remote.path),
                local_path=str(item.local_path),
                status=Status.PLANNED if item.action != "skip" else Status.SKIPPED,
                message=item.reason,
            )
            for item in plan
        ]

    results: list[Result] = []

    with ThreadPoolExecutor(
        max_workers=config.workers,
        thread_name_prefix="sftp-worker",
    ) as pool:
        futures = {
            pool.submit(
                transfer_one,
                item,
                config=config,
                factory=factory,
                journal=journal,
                bucket=bucket,
            ): item
            for item in plan
        }

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            LOG.info(
                "%s: %s",
                result.status.value.upper(),
                result.remote_path,
            )

    return results


def write_report(path: Path, results: Sequence[Result]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            status.value: sum(1 for r in results if r.status is status)
            for status in Status
        },
        "results": [result.as_dict() for result in results],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

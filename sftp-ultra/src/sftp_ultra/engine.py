from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import sqlite3
import stat as stat_mod
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Sequence

import paramiko

from .journal import Journal
from .model import ChecksumMode, Config, OverwritePolicy, PlanItem, Result, Status
from .paths import unique_destination
from .ssh import SFTPConnectionFactory

LOG = logging.getLogger("sftp_ultra")


def record_result_failsafe(journal: Journal, result: Result, **sizes: int) -> None:
    """Record a journal entry without ever failing the transfer.

    F5 (AUDIT-FINDINGS): the journal used to sit inside the same retried
    region as the transfer. A transient sqlite failure (disk full, DB locked)
    after the irreversible steps -- os.replace / sftp.remove -- then made a
    completed move retry and report FAILED even though it succeeded. A
    journal blip must never influence the transfer outcome: log and continue.
    """
    try:
        journal.record(result, **sizes)
    except sqlite3.Error as error:
        LOG.warning(
            "Journal write failed for %s (result kept locally): %s",
            result.remote_path,
            error,
        )


def _completed_result_from_local(
    item: PlanItem,
    journal: Journal,
    local_path: Path,
    config: Config,
    attempt: int,
) -> Result:
    """Result for 'the previous attempt already completed this transfer'.

    F5: a FileNotFoundError from the initial sftp.stat on attempt >= 2 means
    the source already vanished -- which only our sftp.remove (or an outside
    actor) does -- so the previous attempt completed instead of failing.
    """
    status = Status.MOVED if config.delete_source else Status.COPIED
    bytes_transferred = local_path.stat().st_size if local_path.exists() else 0
    result = Result(
        remote_path=str(item.remote.path),
        local_path=str(local_path),
        status=status,
        bytes_transferred=bytes_transferred,
        attempts=attempt,
        message="already completed by a previous attempt (remote path gone)",
    )
    record_result_failsafe(
        journal,
        result,
        remote_size=bytes_transferred,
        remote_mtime=0,
    )
    return result


class TokenBucket:
    def __init__(self, kib_per_second: int | None) -> None:
        self.rate = None if kib_per_second is None else kib_per_second * 1024
        # F1 (AUDIT-FINDINGS): the transfer loop reads fixed 1 MiB chunks.
        # Capping the bucket at the rate made consume(amount > rate) loop
        # forever (tokens can never exceed the rate), deadlocking every
        # worker behind the first full-size chunk as soon as
        # --bandwidth-limit-kib < 1024 was used. The bucket must be able to
        # hold at least one interactive unit, so its capacity is max(rate,
        # 1 MiB). Throughput is still bounded by the refill rate; only the
        # maximum save-up/credit is enlarged, never the pacing.
        self.capacity = float(max(self.rate or 0, 1024 * 1024))
        self.tokens = float(self.capacity)
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
                    self.capacity,
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


def _resume_meta_path(temporary_path: Path) -> Path:
    return temporary_path.with_name(temporary_path.name + ".meta")


def _read_resume_meta(temporary_path: Path) -> dict | None:
    meta_path = _resume_meta_path(temporary_path)
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_resume_meta(temporary_path: Path, *, remote_size: int, remote_mtime: int) -> None:
    meta_path = _resume_meta_path(temporary_path)
    meta_path.write_text(
        json.dumps({"remote_size": remote_size, "remote_mtime": remote_mtime}),
        encoding="utf-8",
    )


def _clear_resume_meta(temporary_path: Path) -> None:
    meta_path = _resume_meta_path(temporary_path)
    meta_path.unlink(missing_ok=True)


def copy_with_resume(
    sftp: paramiko.SFTPClient,
    remote_path: str,
    temporary_path: Path,
    *,
    resume: bool,
    bucket: TokenBucket,
    remote_size: int,
    remote_mtime: int,
) -> int:
    resumed_from = 0

    if resume and temporary_path.exists():
        meta = _read_resume_meta(temporary_path)
        # Only trust the partial bytes already on disk if we recorded, at
        # the time they were downloaded, the exact remote size/mtime we are
        # about to resume against. If the remote file has been rewritten
        # since (e.g. an encoder finalizing an mp4 by rewriting its header)
        # the old bytes no longer belong to this version of the file, and
        # splicing them onto the new tail would silently corrupt the
        # output. In that case, start the download over from scratch.
        if meta and meta.get("remote_size") == remote_size and meta.get("remote_mtime") == remote_mtime:
            resumed_from = temporary_path.stat().st_size
        else:
            temporary_path.unlink()

    if not resume and temporary_path.exists():
        temporary_path.unlink()
        _clear_resume_meta(temporary_path)

    mode = "ab" if resumed_from else "wb"
    _write_resume_meta(temporary_path, remote_size=remote_size, remote_mtime=remote_mtime)

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
                try:
                    before = sftp.stat(str(remote.path))
                except FileNotFoundError:
                    # F5 (AUDIT-FINDINGS): from attempt 2 onward this means
                    # the PREVIOUS attempt already completed the transfer
                    # (e.g. sftp.remove executed) instead of an error. Never
                    # retry a completed transfer because of it.
                    if attempt > 1:
                        return _completed_result_from_local(
                            item,
                            journal,
                            local_path,
                            config,
                            attempt,
                        )
                    raise

                # F8 (AUDIT-FINDINGS): ``sftp.stat``/``sftp.open`` follow
                # symlinks server-side, and the discovery -> transfer window
                # (interactive prompts, earlier transfers, retries) is wide
                # enough for a planted symlink. Discovery's lstat guard must
                # be mirrored here or root confinement silently escapes at
                # execution time. The guard degrades to check-less when a
                # server/client does not support lstat.
                try:
                    lstat_mode = sftp.lstat(str(remote.path)).st_mode
                    refused = isinstance(lstat_mode, int) and (
                        stat_mod.S_ISLNK(lstat_mode)
                        or not stat_mod.S_ISREG(lstat_mode)
                    )
                except (AttributeError, TypeError, OSError):
                    refused = False
                if refused:
                    result = Result(
                        remote_path=str(remote.path),
                        local_path=str(local_path),
                        status=Status.FAILED,
                        attempts=attempt,
                        message=(
                            "Refusing non-regular remote file at transfer "
                            f"time (symlink/unknown): {remote.path}"
                        ),
                    )
                    record_result_failsafe(
                        journal,
                        result,
                        remote_size=remote.size,
                        remote_mtime=remote.mtime,
                    )
                    return result

                resumed_from = copy_with_resume(
                    sftp,
                    str(remote.path),
                    temporary_path,
                    resume=config.resume,
                    bucket=bucket,
                    remote_size=int(before.st_size),
                    remote_mtime=int(before.st_mtime),
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
                    # Pass bucket=None for both checksum reads. The transfer
                    # already consumed the bandwidth budget for these bytes.
                    # Passing the bucket here would charge for them a second
                    # time and silently halve effective throughput on every
                    # checksummed file.
                    local_hash = sha256_file(temporary_path, None)
                    remote_hash = remote_sha256(
                        sftp,
                        str(remote.path),
                        None,
                    )
                    if local_hash != remote_hash:
                        raise RuntimeError("SHA-256 checksum mismatch.")
                    checksum = local_hash

                # F6 (AUDIT-FINDINGS): the overwrite policy was last checked
                # at planning time (planner.exists()). It must also be applied
                # here, immediately before the irreversible os.replace, or a
                # file created in the planning -> execution window is silently
                # overwritten under the default skip policy.
                if config.overwrite is OverwritePolicy.SKIP and not item.reason:
                    # '' means: planned as transfer because destination was
                    # absent at planning time. If it exists now, skip.
                    if local_path.exists():
                        temporary_path.unlink(missing_ok=True)
                        _clear_resume_meta(temporary_path)
                        result = Result(
                            remote_path=str(remote.path),
                            local_path=str(local_path),
                            status=Status.SKIPPED,
                            attempts=attempt,
                            message="destination created after planning (skip policy)",
                        )
                        record_result_failsafe(
                            journal,
                            result,
                            remote_size=remote.size,
                            remote_mtime=remote.mtime,
                        )
                        return result
                elif config.overwrite is OverwritePolicy.RENAME and local_path.exists():
                    # F6 TOCTOU for RENAME: the unique_destination() chosen
                    # at planning time (or the bare base name, when the
                    # destination was free at planning) can have been taken
                    # since — re-derive the unique name unconditionally.
                    relative = item.remote.path.relative_to(config.remote_root)
                    retarget = unique_destination(
                        config.destination.joinpath(*relative.parts)
                    )
                    if retarget != local_path:
                        local_path = retarget

                os.replace(temporary_path, local_path)
                _clear_resume_meta(temporary_path)

                status = None
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
                # N2 (AUDIT-REVIEW, adopted): record the stats the transfer
                # actually validated (before/after), not the discovery-time
                # remote.size/remote.mtime -- a journal meant for auditability
                # should not describe a file that never existed at transfer
                # time.
                record_result_failsafe(
                    journal,
                    result,
                    remote_size=int(after.st_size),
                    remote_mtime=int(before.st_mtime),
                )
                return result

        except (
            OSError,
            TimeoutError,
            paramiko.SSHException,
            EOFError,
            RuntimeError,
        ) as error:
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
    record_result_failsafe(
        journal,
        result,
        remote_size=remote.size,
        remote_mtime=remote.mtime,
    )
    return result


def run_plan(
    plan: Sequence[PlanItem],
    *,
    config: Config,
    factory: SFTPConnectionFactory,
) -> list[Result]:
    journal = Journal(config.journal_path)
    bucket = TokenBucket(config.bandwidth_limit_kib)

    try:
        return _run_plan_locked(
            plan,
            config=config,
            factory=factory,
            journal=journal,
            bucket=bucket,
        )
    finally:
        # F12 (AUDIT-FINDINGS): the journal holds one long-lived sqlite
        # connection; it must be closed explicitly (sqlite3's context
        # manager commits but never closes).
        journal.close()


def _run_plan_locked(
    plan: Sequence[PlanItem],
    *,
    config: Config,
    factory: SFTPConnectionFactory,
    journal: Journal,
    bucket: TokenBucket,
) -> list[Result]:
    if config.dry_run:
        # Build results and journal them so the plan is queryable after a
        # dry run. Status.PLANNED was defined for exactly this purpose — a
        # dry-run entry that records what would have transferred without any
        # files being moved. Without journaling here, Status.PLANNED is an
        # enum value that is defined but never persisted, which is confusing.
        dry_results: list[Result] = []
        for item in plan:
            result = Result(
                remote_path=str(item.remote.path),
                local_path=str(item.local_path),
                status=Status.PLANNED if item.action != "skip" else Status.SKIPPED,
                message=item.reason,
            )
            # F12: dry-run rows live in their own run_kind namespace so they
            # can never overwrite the real statuses of a previous run.
            journal.record(
                result,
                remote_size=item.remote.size,
                remote_mtime=item.remote.mtime,
                run_kind="dry_run",
            )
            dry_results.append(result)
        return dry_results

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

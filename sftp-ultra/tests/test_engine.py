"""
Tests for engine.py transfer logic.

Uses unittest.mock to avoid requiring a live SSH connection. Covers:
  - transfer_one: success path (COPIED)
  - transfer_one: delete_source path (MOVED)
  - transfer_one: size mismatch triggers retry and eventual FAILED
  - transfer_one: SHA-256 checksum mismatch raises and produces FAILED
  - transfer_one: skip action produces SKIPPED without touching sftp
  - run_plan: dry_run journals Status.PLANNED entries and returns them
"""
from __future__ import annotations

import sqlite3
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from sftp_ultra.engine import run_plan, transfer_one
from sftp_ultra.journal import Journal
from sftp_ultra.model import (
    ChecksumMode,
    Config,
    OverwritePolicy,
    PlanItem,
    RemoteFile,
    Status,
)
from sftp_ultra.ssh import SFTPConnectionFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(
    tmp_path: Path,
    *,
    checksum: ChecksumMode = ChecksumMode.NONE,
    retries: int = 0,
    delete_source: bool = False,
    dry_run: bool = False,
    bandwidth_limit_kib: int | None = None,
) -> Config:
    return Config(
        target="host",
        username="user",
        port=22,
        remote_root=PurePosixPath("/remote"),
        destination=tmp_path / "dest",
        workers=1,
        retries=retries,
        timeout_seconds=10,
        overwrite=OverwritePolicy.SKIP,
        checksum=checksum,
        resume=False,
        delete_source=delete_source,
        trust_unknown_host=False,
        bandwidth_limit_kib=bandwidth_limit_kib,
        journal_path=tmp_path / "journal.sqlite3",
        manifest_path=None,
        report_path=None,
        dry_run=dry_run,
        assume_yes=True,
    )


def make_remote(name: str = "file.txt", size: int = 10, mtime: int = 1) -> RemoteFile:
    return RemoteFile(
        path=PurePosixPath(f"/remote/{name}"),
        size=size,
        mtime=mtime,
    )


def make_plan_item(remote: RemoteFile, local_path: Path, action: str = "transfer") -> PlanItem:
    return PlanItem(remote=remote, local_path=local_path, action=action)


def make_mock_factory(tmp_path: Path, remote: RemoteFile) -> MagicMock:
    """
    Returns a mock SFTPConnectionFactory whose open() context manager
    yields a mock SFTPClient. The mock sftp.stat() returns consistent
    before/after size and mtime, and sftp.open() writes the expected
    number of zero bytes to the .part file so size checks pass.
    """
    factory = MagicMock(spec=SFTPConnectionFactory)
    sftp = MagicMock()

    stat_result = SimpleNamespace(st_size=remote.size, st_mtime=remote.mtime)
    sftp.stat.return_value = stat_result

    def fake_open(path, mode):
        # Simulate the remote file as a stream of `remote.size` zero bytes.
        data = b"\x00" * remote.size
        handle = MagicMock()
        chunks = [data[i:i+1024*1024] for i in range(0, len(data), 1024*1024)] + [b""]
        handle.read.side_effect = chunks
        handle.__enter__ = lambda s: s
        handle.__exit__ = MagicMock(return_value=False)
        return handle

    sftp.open.side_effect = fake_open

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=sftp)
    ctx.__exit__ = MagicMock(return_value=False)
    factory.open.return_value = ctx

    return factory, sftp


# ---------------------------------------------------------------------------
# transfer_one tests
# ---------------------------------------------------------------------------

class TestTransferOneSkip:
    def test_skip_action_returns_skipped_without_sftp(self, tmp_path: Path):
        config = make_config(tmp_path)
        remote = make_remote()
        local = tmp_path / "dest" / "file.txt"
        item = make_plan_item(remote, local, action="skip")

        factory = MagicMock(spec=SFTPConnectionFactory)
        journal = Journal(config.journal_path)
        from sftp_ultra.engine import TokenBucket
        bucket = TokenBucket(None)

        result = transfer_one(item, config=config, factory=factory, journal=journal, bucket=bucket)

        assert result.status is Status.SKIPPED
        factory.open.assert_not_called()

        # Journal entry must exist
        conn = sqlite3.connect(config.journal_path)
        row = conn.execute(
            "SELECT status FROM transfer_journal WHERE remote_path = ?",
            (str(remote.path),),
        ).fetchone()
        assert row is not None
        assert row[0] == "skipped"


class TestTransferOneCopied:
    def test_successful_transfer_produces_copied(self, tmp_path: Path):
        config = make_config(tmp_path)
        remote = make_remote(size=5)
        local = tmp_path / "dest" / "file.txt"
        item = make_plan_item(remote, local)

        factory, sftp = make_mock_factory(tmp_path, remote)
        journal = Journal(config.journal_path)
        from sftp_ultra.engine import TokenBucket
        bucket = TokenBucket(None)

        result = transfer_one(item, config=config, factory=factory, journal=journal, bucket=bucket)

        assert result.status is Status.COPIED
        assert result.attempts == 1
        assert result.checksum is None
        assert local.exists()


class TestTransferOneMoved:
    def test_delete_source_produces_moved(self, tmp_path: Path):
        config = make_config(tmp_path, delete_source=True)
        remote = make_remote(size=5)
        local = tmp_path / "dest" / "file.txt"
        item = make_plan_item(remote, local)

        factory, sftp = make_mock_factory(tmp_path, remote)
        journal = Journal(config.journal_path)
        from sftp_ultra.engine import TokenBucket
        bucket = TokenBucket(None)

        result = transfer_one(item, config=config, factory=factory, journal=journal, bucket=bucket)

        assert result.status is Status.MOVED
        sftp.remove.assert_called_once_with(str(remote.path))


class TestTransferOneSizeMismatch:
    def test_size_mismatch_exhausts_retries_and_fails(self, tmp_path: Path):
        config = make_config(tmp_path, retries=1)
        remote = make_remote(size=10)
        local = tmp_path / "dest" / "file.txt"
        item = make_plan_item(remote, local)

        factory = MagicMock(spec=SFTPConnectionFactory)
        sftp = MagicMock()

        # before stat reports size=10, after stat reports size=99 — mismatch
        before = SimpleNamespace(st_size=10, st_mtime=1)
        after = SimpleNamespace(st_size=99, st_mtime=1)
        sftp.stat.side_effect = [before, after, before, after]

        data = b"\x00" * 10
        def fake_open(path, mode):
            handle = MagicMock()
            handle.read.side_effect = [data, b"", data, b""]
            handle.__enter__ = lambda s: s
            handle.__exit__ = MagicMock(return_value=False)
            return handle
        sftp.open.side_effect = fake_open

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=sftp)
        ctx.__exit__ = MagicMock(return_value=False)
        factory.open.return_value = ctx

        journal = Journal(config.journal_path)
        from sftp_ultra.engine import TokenBucket
        bucket = TokenBucket(None)

        result = transfer_one(item, config=config, factory=factory, journal=journal, bucket=bucket)

        assert result.status is Status.FAILED
        assert result.attempts == 2  # retries=1 → 2 total attempts
        assert "size" in result.message.lower() or "changed" in result.message.lower()


class TestTransferOneChecksumMismatch:
    def test_sha256_mismatch_produces_failed(self, tmp_path: Path):
        config = make_config(tmp_path, checksum=ChecksumMode.SHA256)
        remote = make_remote(size=5)
        local = tmp_path / "dest" / "file.txt"
        item = make_plan_item(remote, local)

        factory, sftp = make_mock_factory(tmp_path, remote)
        journal = Journal(config.journal_path)
        from sftp_ultra.engine import TokenBucket
        bucket = TokenBucket(None)

        # Patch both hash functions to return different digests
        with (
            patch("sftp_ultra.engine.sha256_file", return_value="aaa"),
            patch("sftp_ultra.engine.remote_sha256", return_value="bbb"),
        ):
            result = transfer_one(
                item, config=config, factory=factory, journal=journal, bucket=bucket
            )

        assert result.status is Status.FAILED
        assert "checksum" in result.message.lower() or "sha" in result.message.lower()


class TestChecksumBandwidthNotDoubled:
    def test_checksum_passes_none_bucket_to_hash_functions(self, tmp_path: Path):
        """
        Regression: sha256_file and remote_sha256 must receive bucket=None
        so the bandwidth limit is not charged twice per file.
        """
        config = make_config(tmp_path, checksum=ChecksumMode.SHA256)
        remote = make_remote(size=5)
        local = tmp_path / "dest" / "file.txt"
        item = make_plan_item(remote, local)

        factory, sftp = make_mock_factory(tmp_path, remote)
        journal = Journal(config.journal_path)
        from sftp_ultra.engine import TokenBucket
        bucket = TokenBucket(None)

        with (
            patch("sftp_ultra.engine.sha256_file", return_value="abc") as mock_local,
            patch("sftp_ultra.engine.remote_sha256", return_value="abc") as mock_remote,
        ):
            transfer_one(item, config=config, factory=factory, journal=journal, bucket=bucket)

        # Both must be called with bucket=None, not the live bucket
        _, local_kwargs = mock_local.call_args
        _, remote_kwargs = mock_remote.call_args
        assert local_kwargs.get("bucket") is None or mock_local.call_args[0][1] is None
        assert remote_kwargs.get("bucket") is None or mock_remote.call_args[0][2] is None


# ---------------------------------------------------------------------------
# run_plan dry-run test
# ---------------------------------------------------------------------------

class TestRunPlanDryRun:
    def test_dry_run_journals_planned_entries(self, tmp_path: Path):
        config = make_config(tmp_path, dry_run=True)
        remote = make_remote()
        local = tmp_path / "dest" / "file.txt"
        plan = [make_plan_item(remote, local, action="transfer")]

        factory = MagicMock(spec=SFTPConnectionFactory)

        results = run_plan(plan, config=config, factory=factory)

        # No real transfers
        factory.open.assert_not_called()

        # Result has PLANNED status
        assert len(results) == 1
        assert results[0].status is Status.PLANNED

        # Journal entry exists with status=planned
        conn = sqlite3.connect(config.journal_path)
        row = conn.execute(
            "SELECT status FROM transfer_journal WHERE remote_path = ?",
            (str(remote.path),),
        ).fetchone()
        assert row is not None, "dry_run must journal PLANNED entries"
        assert row[0] == "planned"

    def test_dry_run_skip_items_journal_as_skipped(self, tmp_path: Path):
        config = make_config(tmp_path, dry_run=True)
        remote = make_remote()
        local = tmp_path / "dest" / "file.txt"
        plan = [make_plan_item(remote, local, action="skip")]

        factory = MagicMock(spec=SFTPConnectionFactory)
        results = run_plan(plan, config=config, factory=factory)

        assert results[0].status is Status.SKIPPED

        conn = sqlite3.connect(config.journal_path)
        row = conn.execute(
            "SELECT status FROM transfer_journal WHERE remote_path = ?",
            (str(remote.path),),
        ).fetchone()
        assert row is not None
        assert row[0] == "skipped"

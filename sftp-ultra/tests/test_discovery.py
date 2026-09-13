"""
Tests for discovery.py remote_walk symlink guard (Fix #6).

Uses a mock SFTPClient. Verifies that symlinked entries encountered
during recursive walk are silently skipped — consistent with the
explicit symlink rejection in discover_directories().
"""
from __future__ import annotations

import stat
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from sftp_ultra.discovery import remote_walk
from sftp_ultra.model import RemoteFile


def make_entry(name: str, mode: int, size: int = 10, mtime: int = 1) -> SimpleNamespace:
    return SimpleNamespace(filename=name, st_mode=mode, st_size=size, st_mtime=mtime)


def make_lstat(mode: int, size: int = 10, mtime: int = 1) -> SimpleNamespace:
    return SimpleNamespace(st_mode=mode, st_size=size, st_mtime=mtime)


class TestRemoteWalkSymlinkGuard:
    def test_symlinked_file_is_skipped(self):
        """A symlink in listdir_attr must be skipped, not yielded."""
        sftp = MagicMock()
        root = PurePosixPath("/remote")

        sftp.listdir_attr.return_value = [
            make_entry("link_to_file.txt", stat.S_IFLNK | 0o777),
            make_entry("real_file.txt", stat.S_IFREG | 0o644),
        ]

        # lstat returns the true type for each path
        def fake_lstat(path: str) -> SimpleNamespace:
            if "link_to_file" in path:
                return make_lstat(stat.S_IFLNK | 0o777)
            return make_lstat(stat.S_IFREG | 0o644)

        sftp.lstat.side_effect = fake_lstat

        results = list(remote_walk(sftp, root))

        # Only the real file should be yielded
        assert len(results) == 1
        assert results[0].path == PurePosixPath("/remote/real_file.txt")

    def test_symlinked_directory_is_skipped_not_recursed(self):
        """A symlink to a directory must not be recursed into."""
        sftp = MagicMock()
        root = PurePosixPath("/remote")

        sftp.listdir_attr.return_value = [
            make_entry("link_to_dir", stat.S_IFLNK | 0o777),
        ]

        sftp.lstat.return_value = make_lstat(stat.S_IFLNK | 0o777)

        results = list(remote_walk(sftp, root))

        assert results == []
        # listdir_attr must NOT be called a second time (no recursion into symlink)
        sftp.listdir_attr.assert_called_once_with(str(root))

    def test_regular_file_yields_lstat_size_and_mtime(self):
        """
        After fixing to use lstat(), yielded RemoteFile must use lstat
        size/mtime, not the potentially-stale listdir_attr values.
        """
        sftp = MagicMock()
        root = PurePosixPath("/remote")

        sftp.listdir_attr.return_value = [
            make_entry("file.txt", stat.S_IFREG | 0o644, size=999, mtime=999),
        ]

        # lstat returns authoritative values
        sftp.lstat.return_value = make_lstat(stat.S_IFREG | 0o644, size=42, mtime=12345)

        results = list(remote_walk(sftp, root))

        assert len(results) == 1
        assert results[0].size == 42
        assert results[0].mtime == 12345

    def test_regular_directory_recurses(self):
        """Non-symlink directories are still walked recursively."""
        sftp = MagicMock()
        root = PurePosixPath("/remote")

        # root/ contains one dir, that dir contains one file
        sftp.listdir_attr.side_effect = [
            [make_entry("subdir", stat.S_IFDIR | 0o755)],
            [make_entry("file.txt", stat.S_IFREG | 0o644)],
        ]

        def fake_lstat(path: str) -> SimpleNamespace:
            if path.endswith("subdir"):
                return make_lstat(stat.S_IFDIR | 0o755)
            return make_lstat(stat.S_IFREG | 0o644, size=5, mtime=1)

        sftp.lstat.side_effect = fake_lstat

        results = list(remote_walk(sftp, root))

        assert len(results) == 1
        assert results[0].path == PurePosixPath("/remote/subdir/file.txt")

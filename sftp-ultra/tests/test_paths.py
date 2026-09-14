"""
Tests for paths.py — remote path validation and confinement.

Covers:
  - normalize_remote: empty path, non-absolute path, valid absolute path
  - is_within: path equals root, path inside root, path outside root
  - resolve_under_root: relative input resolved under root, absolute input
    within root, traversal escape rejected
  - local_for_remote: maps remote path to correct local path
  - unique_destination: returns original if not exists, increments counter
    on collision
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from sftp_ultra.paths import (
    RemotePathError,
    is_within,
    local_for_remote,
    normalize_remote,
    resolve_under_root,
    unique_destination,
)


class TestNormalizeRemote:
    def test_valid_absolute_path(self):
        result = normalize_remote("/home/side/recordings")
        assert result == PurePosixPath("/home/side/recordings")

    def test_empty_path_raises(self):
        with pytest.raises(RemotePathError, match="empty"):
            normalize_remote("")

    def test_whitespace_only_raises(self):
        with pytest.raises(RemotePathError, match="empty"):
            normalize_remote("   ")

    def test_relative_path_raises(self):
        with pytest.raises(RemotePathError, match="absolute"):
            normalize_remote("relative/path")

    def test_path_with_dotdot_is_normalized(self):
        # os.path.normpath collapses .. — result must still be absolute
        result = normalize_remote("/home/side/../side/recordings")
        assert result == PurePosixPath("/home/side/recordings")


class TestIsWithin:
    root = PurePosixPath("/home/side")

    def test_path_equals_root(self):
        assert is_within(self.root, self.root) is True

    def test_path_inside_root(self):
        assert is_within(PurePosixPath("/home/side/recordings/cam1.mp4"), self.root) is True

    def test_path_outside_root(self):
        assert is_within(PurePosixPath("/etc/passwd"), self.root) is False

    def test_path_sibling_not_within(self):
        # /home/sidecar is NOT within /home/side
        assert is_within(PurePosixPath("/home/sidecar/file.txt"), self.root) is False


class TestResolveUnderRoot:
    root = PurePosixPath("/home/side")

    def test_relative_path_resolved_under_root(self):
        result = resolve_under_root("recordings", self.root)
        assert result == PurePosixPath("/home/side/recordings")

    def test_absolute_path_within_root(self):
        result = resolve_under_root("/home/side/recordings", self.root)
        assert result == PurePosixPath("/home/side/recordings")

    def test_traversal_escape_rejected(self):
        with pytest.raises(RemotePathError, match="escapes"):
            resolve_under_root("/etc/passwd", self.root)

    def test_dotdot_traversal_rejected(self):
        with pytest.raises(RemotePathError, match="escapes"):
            resolve_under_root("../../etc/passwd", self.root)

    def test_exact_root_accepted(self):
        result = resolve_under_root("/home/side", self.root)
        assert result == self.root


class TestLocalForRemote:
    def test_maps_remote_to_local_preserving_structure(self, tmp_path: Path):
        remote = PurePosixPath("/home/side/recordings/cam1.mp4")
        root = PurePosixPath("/home/side")
        dest = tmp_path / "downloads"

        result = local_for_remote(remote, root, dest)
        assert result == dest / "recordings" / "cam1.mp4"

    def test_file_directly_under_root(self, tmp_path: Path):
        remote = PurePosixPath("/home/side/file.txt")
        root = PurePosixPath("/home/side")
        dest = tmp_path / "downloads"

        result = local_for_remote(remote, root, dest)
        assert result == dest / "file.txt"

    def test_path_escaping_root_raises(self, tmp_path: Path):
        remote = PurePosixPath("/etc/passwd")
        root = PurePosixPath("/home/side")
        dest = tmp_path / "downloads"

        with pytest.raises(RemotePathError, match="escapes"):
            local_for_remote(remote, root, dest)


class TestUniqueDestination:
    def test_returns_original_if_not_exists(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        result = unique_destination(target)
        assert result == target

    def test_increments_on_first_collision(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        target.write_text("existing")

        result = unique_destination(target)
        assert result == tmp_path / "file_1.txt"

    def test_increments_past_multiple_collisions(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        target.write_text("existing")
        (tmp_path / "file_1.txt").write_text("existing")
        (tmp_path / "file_2.txt").write_text("existing")

        result = unique_destination(target)
        assert result == tmp_path / "file_3.txt"

    def test_preserves_extension(self, tmp_path: Path):
        target = tmp_path / "recording.mp4"
        target.write_text("data")

        result = unique_destination(target)
        assert result.suffix == ".mp4"
        assert result.stem == "recording_1"

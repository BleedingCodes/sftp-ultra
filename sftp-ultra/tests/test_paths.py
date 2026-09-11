from pathlib import Path, PurePosixPath

import pytest

from sftp_ultra.paths import (
    RemotePathError,
    is_within,
    local_for_remote,
    resolve_under_root,
)


def test_is_within_root():
    root = PurePosixPath("/home/side")
    assert is_within(PurePosixPath("/home/side"), root)
    assert is_within(PurePosixPath("/home/side/a/b.txt"), root)
    assert not is_within(PurePosixPath("/home/other/file.txt"), root)


def test_resolve_relative_path():
    root = PurePosixPath("/home/side")
    assert resolve_under_root("recordings", root) == PurePosixPath(
        "/home/side/recordings"
    )


def test_reject_escape():
    root = PurePosixPath("/home/side")
    with pytest.raises(RemotePathError):
        resolve_under_root("../other", root)


def test_local_mapping():
    assert local_for_remote(
        PurePosixPath("/home/side/a/b.txt"),
        PurePosixPath("/home/side"),
        Path("/tmp/out"),
    ) == Path("/tmp/out/a/b.txt")

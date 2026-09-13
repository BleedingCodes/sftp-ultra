from __future__ import annotations

import fnmatch
import stat
from pathlib import PurePosixPath
from typing import Iterable, Iterator, Sequence

import paramiko

from .model import RemoteFile
from .paths import RemotePathError, resolve_under_root


def remote_walk(
    sftp: paramiko.SFTPClient,
    directory: PurePosixPath,
) -> Iterator[RemoteFile]:
    for entry in sftp.listdir_attr(str(directory)):
        path = directory / entry.filename

        if stat.S_ISDIR(entry.st_mode):
            yield from remote_walk(sftp, path)
        elif stat.S_ISREG(entry.st_mode):
            yield RemoteFile(
                path=path,
                size=int(entry.st_size),
                mtime=int(entry.st_mtime),
            )


def discover_patterns(
    sftp: paramiko.SFTPClient,
    *,
    root: PurePosixPath,
    patterns: Sequence[str],
) -> list[RemoteFile]:
    folded = [PurePosixPath(p).name.casefold() for p in patterns]
    found = []

    for remote_file in remote_walk(sftp, root):
        name = remote_file.path.name.casefold()
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in folded):
            found.append(remote_file)

    return deduplicate(found)


def discover_directories(
    sftp: paramiko.SFTPClient,
    *,
    root: PurePosixPath,
    directories: Sequence[str],
) -> list[RemoteFile]:
    found = []

    for selection in directories:
        directory = resolve_under_root(selection, root)

        # Use lstat, not stat: stat() follows symlinks server-side, which
        # would let a symlink planted under the remote root point anywhere
        # on the remote filesystem and completely defeat the root
        # confinement resolve_under_root() is supposed to guarantee.
        info = sftp.lstat(str(directory))

        if stat.S_ISLNK(info.st_mode):
            raise RemotePathError(
                f"Refusing to follow symlink under remote root: {directory}"
            )

        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(str(directory))

        found.extend(remote_walk(sftp, directory))

    return deduplicate(found)


def deduplicate(files: Iterable[RemoteFile]) -> list[RemoteFile]:
    by_path: dict[PurePosixPath, RemoteFile] = {}
    for item in files:
        by_path.setdefault(item.path, item)
    return list(by_path.values())

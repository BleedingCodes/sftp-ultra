from __future__ import annotations

import fnmatch
import logging
import stat
from pathlib import PurePosixPath
from typing import Iterable, Iterator, Sequence

import paramiko

from .model import RemoteFile
from .paths import RemotePathError, resolve_under_root

LOG = logging.getLogger("sftp_ultra")


def remote_walk(
    sftp: paramiko.SFTPClient,
    directory: PurePosixPath,
) -> Iterator[RemoteFile]:
    for entry in sftp.listdir_attr(str(directory)):
        filename = entry.filename
        if filename in {".", ".."}:
            # F7 (AUDIT-FINDINGS): listdir entries are server-supplied
            # strings. POSIX filesystems cannot store '.'/'..' entries, so
            # one means a buggy, proxying, or hostile server — walking them
            # recursed above the configured root ('..') or infinitely on the
            # same directory ('.', RecursionError).
            LOG.warning(
                "Skipping non-conforming server entry %r in %s", filename, directory
            )
            continue

        path = directory / filename

        # F7: a file-name containing '/' (e.g. "../other") must also be
        # rejected: joining it must add exactly one normal part to the
        # current directory, never reach above it.
        if len(path.parts) != len(directory.parts) + 1 or ".." in path.parts:
            LOG.warning(
                "Skipping server entry that escapes directory %s: %r",
                directory,
                filename,
            )
            continue

        # listdir_attr() behaviour is SFTP server-dependent: some servers
        # return lstat results (no symlink resolution), others follow symlinks
        # before returning attributes. Calling lstat() explicitly on every
        # entry guarantees we always see the symlink itself, not its target.
        # This prevents a symlink-to-directory planted anywhere under the
        # remote root from redirecting the walk to an arbitrary location on
        # the remote filesystem and silently escaping root confinement.
        lstat_info = sftp.lstat(str(path))

        if stat.S_ISLNK(lstat_info.st_mode):
            # Skip symlinks entirely during recursive walk — consistent with
            # the explicit symlink rejection in discover_directories().
            continue

        if stat.S_ISDIR(lstat_info.st_mode):
            yield from remote_walk(sftp, path)
        elif stat.S_ISREG(lstat_info.st_mode):
            # F7: some servers/proxies omit fields in SFTPAttributes; the
            # bare int() conversions crashed with TypeError on them.
            yield RemoteFile(
                path=path,
                size=int(lstat_info.st_size or 0),
                mtime=int(lstat_info.st_mtime or 0),
            )


def discover_patterns(
    sftp: paramiko.SFTPClient,
    *,
    root: PurePosixPath,
    patterns: Sequence[str],
) -> list[RemoteFile]:
    # F10 (AUDIT-FINDINGS): patterns used to be folded to their basename,
    # silently widening 'sub/*.mp4' to every *.mp4 under the root (and with
    # --delete-source, widening the deletions). Two behaviors now:
    #  * pattern without '/': basename match exactly as before;
    #  * pattern with '/': path-aware match against the path relative to the
    #    root (e.g. 'sub/*.mp4' matches <root>/sub/**.mp4 only). If the
    #    pattern literally starts with the root, that prefix is stripped so
    #    absolute-root patterns behave as relative ones.
    folded: list[tuple[bool, str]] = []
    for pattern in patterns:
        if "/" in pattern:
            relative_pattern = pattern.casefold()
            root_prefix = str(root / "").casefold()
            folded.append((True, relative_pattern.removeprefix(root_prefix)))
        else:
            folded.append((False, PurePosixPath(pattern).name.casefold()))

    found = []
    for remote_file in remote_walk(sftp, root):
        relative = remote_file.path.relative_to(root).as_posix().casefold()
        name = remote_file.path.name.casefold()
        for path_aware, matcher in folded:
            match_target = relative if path_aware else name
            if fnmatch.fnmatchcase(match_target, matcher):
                found.append(remote_file)
                break

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

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath


class RemotePathError(ValueError):
    pass


def normalize_remote(path: str | PurePosixPath) -> PurePosixPath:
    raw = str(path).strip()
    if not raw:
        raise RemotePathError("Remote path cannot be empty.")

    normalized = PurePosixPath(os.path.normpath(raw))
    if not normalized.is_absolute():
        raise RemotePathError(f"Remote path must be absolute: {path}")

    return normalized


def is_within(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def resolve_under_root(
    selection: str | PurePosixPath,
    root: PurePosixPath,
) -> PurePosixPath:
    candidate = PurePosixPath(str(selection).strip())
    if not candidate.is_absolute():
        candidate = root / candidate

    candidate = normalize_remote(candidate)

    if not is_within(candidate, root):
        raise RemotePathError(
            f"Remote path escapes configured root {root}: {candidate}"
        )

    return candidate


def local_for_remote(
    remote_path: PurePosixPath,
    remote_root: PurePosixPath,
    destination_root: Path,
) -> Path:
    if not is_within(remote_path, remote_root):
        raise RemotePathError(
            f"Remote file escapes configured root: {remote_path}"
        )

    relative = remote_path.relative_to(remote_root)
    return destination_root.joinpath(*relative.parts)


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 1
    while True:
        candidate = path.with_name(
            f"{path.stem}_{counter}{path.suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1

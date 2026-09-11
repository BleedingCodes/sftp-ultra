from pathlib import Path, PurePosixPath

from sftp_ultra.model import (
    ChecksumMode,
    Config,
    OverwritePolicy,
    RemoteFile,
)
from sftp_ultra.planner import plan_transfers


def make_config(tmp_path: Path, policy: OverwritePolicy) -> Config:
    return Config(
        target="host",
        username="user",
        port=22,
        remote_root=PurePosixPath("/home/side"),
        destination=tmp_path,
        workers=2,
        retries=1,
        timeout_seconds=10,
        overwrite=policy,
        checksum=ChecksumMode.NONE,
        resume=True,
        delete_source=False,
        trust_unknown_host=False,
        bandwidth_limit_kib=None,
        journal_path=tmp_path / "journal.sqlite3",
        manifest_path=None,
        report_path=None,
        dry_run=False,
        assume_yes=True,
    )


def test_skip_existing(tmp_path: Path):
    local = tmp_path / "x.txt"
    local.write_text("existing")

    remote = RemoteFile(
        path=PurePosixPath("/home/side/x.txt"),
        size=10,
        mtime=1,
    )

    plan = plan_transfers(
        [remote],
        make_config(tmp_path, OverwritePolicy.SKIP),
    )

    assert plan[0].action == "skip"

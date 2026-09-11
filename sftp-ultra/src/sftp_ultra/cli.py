from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path, PurePosixPath

import paramiko

from .discovery import discover_directories, discover_patterns
from .engine import run_plan, write_report
from .model import ChecksumMode, Config, OverwritePolicy, Status
from .paths import normalize_remote
from .planner import plan_transfers
from .ssh import SFTPConnectionFactory, prompt_credentials


LOG = logging.getLogger("sftp_ultra")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sftp-ultra")
    sub = root.add_subparsers(dest="command", required=True)

    pull = sub.add_parser("pull", help="Copy files from a remote host.")
    pull.add_argument("--target", required=True)
    pull.add_argument("--username", required=True)
    pull.add_argument("--port", type=int, default=22)
    pull.add_argument("--remote-root", type=PurePosixPath, default=PurePosixPath("/home/side"))
    pull.add_argument("--destination", type=Path, default=Path("./downloads"))
    pull.add_argument("--pattern", action="append", default=[])
    pull.add_argument("--directory", action="append", default=[])
    pull.add_argument("--workers", type=int, default=4)
    pull.add_argument("--retries", type=int, default=3)
    pull.add_argument("--timeout", type=int, default=20)
    pull.add_argument(
        "--overwrite",
        choices=[p.value for p in OverwritePolicy],
        default=OverwritePolicy.SKIP.value,
    )
    pull.add_argument(
        "--checksum",
        choices=[m.value for m in ChecksumMode],
        default=ChecksumMode.NONE.value,
    )
    pull.add_argument("--resume", action="store_true")
    pull.add_argument("--delete-source", action="store_true")
    pull.add_argument("--trust-unknown-host", action="store_true")
    pull.add_argument("--bandwidth-limit-kib", type=int)
    pull.add_argument("--journal", type=Path, default=Path(".sftp-ultra.sqlite3"))
    pull.add_argument("--manifest", type=Path)
    pull.add_argument("--report", type=Path)
    pull.add_argument("--dry-run", action="store_true")
    pull.add_argument("--yes", action="store_true")
    pull.add_argument("--verbose", action="store_true")

    return root


def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = input(f"{prompt} [y/N]: ").strip().casefold()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        print("Please enter yes or no.")


def build_config(args: argparse.Namespace) -> Config:
    if not 1 <= args.workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    if args.retries < 0:
        raise ValueError("retries cannot be negative")
    if args.timeout <= 0:
        raise ValueError("timeout must be positive")

    return Config(
        target=args.target,
        username=args.username,
        port=args.port,
        remote_root=normalize_remote(args.remote_root),
        destination=args.destination.expanduser().resolve(),
        workers=args.workers,
        retries=args.retries,
        timeout_seconds=args.timeout,
        overwrite=OverwritePolicy(args.overwrite),
        checksum=ChecksumMode(args.checksum),
        resume=args.resume,
        delete_source=args.delete_source,
        trust_unknown_host=args.trust_unknown_host,
        bandwidth_limit_kib=args.bandwidth_limit_kib,
        journal_path=args.journal.expanduser().resolve(),
        manifest_path=args.manifest,
        report_path=args.report,
        dry_run=args.dry_run,
        assume_yes=args.yes,
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
    )

    try:
        config = build_config(args)

        if bool(args.pattern) == bool(args.directory):
            raise ValueError(
                "Specify one or more --pattern values or one or more --directory values."
            )

        credentials = prompt_credentials(config)
        factory = SFTPConnectionFactory(config, credentials)

        with factory.open() as sftp:
            if args.pattern:
                files = discover_patterns(
                    sftp,
                    root=config.remote_root,
                    patterns=args.pattern,
                )
            else:
                files = discover_directories(
                    sftp,
                    root=config.remote_root,
                    directories=args.directory,
                )

        plan = plan_transfers(files, config)

        if config.manifest_path:
            config.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            config.manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "remote": str(item.remote.path),
                            "size": item.remote.size,
                            "mtime": item.remote.mtime,
                            "local": str(item.local_path),
                            "action": item.action,
                            "reason": item.reason,
                        }
                        for item in plan
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )

        print(f"Discovered {len(files)} file(s).")
        print(f"Planned {sum(1 for p in plan if p.action != 'skip')} transfer(s).")
        print(f"Skipped {sum(1 for p in plan if p.action == 'skip')} existing file(s).")

        if config.delete_source and not config.assume_yes:
            if not ask_yes_no(
                "Verified source files will be deleted. Continue?"
            ):
                return 0

        if not config.assume_yes and not config.dry_run:
            if not ask_yes_no("Execute this transfer plan?"):
                return 0

        results = run_plan(plan, config=config, factory=factory)

        if config.report_path:
            write_report(config.report_path, results)

        summary = {
            status.value: sum(1 for r in results if r.status is status)
            for status in Status
        }

        print(json.dumps(summary, indent=2))

        return 1 if summary[Status.FAILED.value] else 0

    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except (
        ValueError,
        OSError,
        paramiko.SSHException,
    ) as error:
        LOG.error("%s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

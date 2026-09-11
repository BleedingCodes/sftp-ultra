# sftp-ultra

A production SFTP transfer engine for Linux environments. Built for teams and
workflows that need reliable, verifiable, unattended file transfer — not a
one-off script.

---

## What It Does

- **Concurrent transfers** — configurable worker pool (default: 4, max: 32)
- **Resumable downloads** — `.part` file pattern, picks up where interrupted transfers left off
- **SHA-256 verification** — optional checksum comparison between remote and local after transfer
- **Remote-change detection** — checks file size and mtime before and after download; aborts if the remote file changed mid-transfer
- **Retry with exponential backoff** — configurable retry count with jitter to avoid thundering herd on reconnect
- **SQLite transfer journal** — every transfer result is recorded with path, size, mtime, checksum, and timestamp
- **Dry-run mode** — plan a transfer and see what would happen without touching any files
- **Overwrite policies** — `skip`, `replace`, `rename` (auto-increments filename), or `ask`
- **Verified source deletion** — optionally removes remote originals after successful transfer, requires confirmation
- **Bandwidth throttling** — token-bucket rate limiter, set in KiB/s
- **JSON manifests and reports** — export the transfer plan before execution; export results after
- **Remote-root confinement** — all remote paths are validated against a configured root; path traversal is rejected
- **Structured logging** — timestamped, thread-labeled output; `--verbose` for debug-level detail
- **Deterministic exit codes** — `0` on success, `1` if any transfer failed, `2` on configuration error, `130` on interrupt

---

## Who It's For

Electronics labs, Linux sysadmins, and small DevOps teams that move files
between machines over SSH on a regular basis — camera recording systems, lab
data collection nodes, remote backup workflows, and any other environment where
a dropped transfer needs to be recoverable and auditable.

Built and used in a home lab running RTSP camera recording on a headless Linux
node. Designed to run unattended or as part of a larger pipeline.

---

## Requirements

- Python 3.11+
- Linux (developed and tested on Ubuntu / Linux Mint)
- `paramiko >= 3.4`
- SSH access to the remote host (password authentication supported; key-based recommended)

---

## Installation

```bash
git clone https://github.com/BleedingCodes/sftp-ultra.git
cd sftp-ultra
pip install -e .
```

This installs the `sftp-ultra` command into your environment.

---

## Quick Start

### Pull files matching a pattern

```bash
sftp-ultra pull \
  --target 192.168.1.105 \
  --username side \
  --remote-root /home/side \
  --destination /media/drive/downloads \
  --pattern "*.mp4" \
  --workers 4 \
  --resume \
  --checksum sha256
```

### Pull a specific directory

```bash
sftp-ultra pull \
  --target 192.168.1.105 \
  --username side \
  --remote-root /home/side \
  --destination /media/drive/downloads \
  --directory recordings \
  --workers 4
```

### Dry run — see what would transfer without moving anything

```bash
sftp-ultra pull \
  --target 192.168.1.105 \
  --username side \
  --remote-root /home/side \
  --destination /media/drive/downloads \
  --pattern "*.mp4" \
  --dry-run
```

### Move files — delete remote originals after verified transfer

```bash
sftp-ultra pull \
  --target 192.168.1.105 \
  --username side \
  --remote-root /home/side \
  --destination /media/drive/downloads \
  --directory recordings \
  --delete-source
```

The delete prompt can be bypassed with `--yes` for unattended operation.

---

## All Options

```
sftp-ultra pull --help
```

| Flag | Default | Description |
|---|---|---|
| `--target` | required | Remote hostname or IP address |
| `--username` | required | SSH username |
| `--port` | `22` | SSH port |
| `--remote-root` | `/home/side` | Remote root directory — all paths confined here |
| `--destination` | `./downloads` | Local destination directory |
| `--pattern` | — | Filename pattern(s) to match (e.g. `*.mp4`) |
| `--directory` | — | Remote subdirectory path(s) to transfer in full |
| `--workers` | `4` | Number of concurrent transfer workers (max: 32) |
| `--retries` | `3` | Retry attempts per file on failure |
| `--timeout` | `20` | SSH connection timeout in seconds |
| `--overwrite` | `skip` | Overwrite policy: `skip`, `replace`, `rename`, `ask` |
| `--checksum` | `none` | Checksum mode: `none` or `sha256` |
| `--resume` | off | Resume interrupted `.part` downloads |
| `--delete-source` | off | Delete remote file after verified transfer |
| `--trust-unknown-host` | off | Auto-accept unknown host keys (use with caution) |
| `--bandwidth-limit-kib` | unlimited | Bandwidth cap in KiB/s |
| `--journal` | `.sftp-ultra.sqlite3` | SQLite journal path |
| `--manifest` | — | Write transfer plan to JSON file before execution |
| `--report` | — | Write transfer results to JSON file after execution |
| `--dry-run` | off | Plan transfers without executing them |
| `--yes` | off | Skip confirmation prompts |
| `--verbose` | off | Debug-level logging |

Use `--pattern` or `--directory`, not both.

---

## Transfer Journal

Every run appends to a SQLite journal at the path set by `--journal`
(default: `.sftp-ultra.sqlite3` in the working directory).

Schema:

| Column | Description |
|---|---|
| `remote_path` | Full remote path (primary key) |
| `local_path` | Local destination path |
| `status` | `copied`, `moved`, `skipped`, `failed`, `planned` |
| `remote_size` | File size on remote at transfer time |
| `remote_mtime` | File mtime on remote at transfer time |
| `checksum` | SHA-256 hex digest (if `--checksum sha256`) |
| `message` | Error message on failure |
| `updated_at` | Timestamp of last update |

Query the journal directly:

```bash
sqlite3 .sftp-ultra.sqlite3 "SELECT remote_path, status, checksum FROM transfer_journal;"
```

---

## Running Tests

```bash
pip install pytest
pytest
```

Tests cover path confinement, remote path resolution, path traversal rejection,
and transfer planning logic. No live SSH connection required.

---

## Project Structure

```
sftp-ultra/
├── src/
│   └── sftp_ultra/
│       ├── cli.py          # Argument parsing and entry point
│       ├── engine.py       # Transfer execution, retries, checksums, concurrency
│       ├── discovery.py    # Remote file discovery by pattern or directory
│       ├── planner.py      # Transfer plan generation and overwrite logic
│       ├── journal.py      # SQLite transfer journal
│       ├── ssh.py          # SSH/SFTP connection factory
│       ├── paths.py        # Remote path validation and confinement
│       └── model.py        # Data classes and enums
├── tests/
│   ├── test_planner.py
│   └── test_paths.py
├── pyproject.toml
└── README.md
```

---

## Built by MainbyteLabs

Python tooling for electronics labs, hardware shops, and Linux-based tech teams.

[MainbyteLabs](https://github.com/MR-MainbyteLabs) ·
[LinkedIn](https://linkedin.com/in/michael-rivera-c0ding) ·
mr.mainbytelabs@gmail.com

---

## License

MIT License

Copyright (c) 2026 Michael Rivera

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

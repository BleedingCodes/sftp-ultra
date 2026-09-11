# sftp-ultra

A production-style SFTP transfer engine with:

- concurrent workers
- resumable `.part` downloads
- remote-change detection
- optional SHA-256 verification
- retry with exponential backoff and jitter
- SQLite transfer journal
- JSON manifests and JSON result reports
- overwrite policies
- remote-root confinement
- dry-run planning
- optional bandwidth throttling
- optional verified source deletion
- structured logging
- deterministic exit codes
- unit-testable path and planning logic

## Install

```bash
python3 -m pip install -e .
```

## Examples

Interactive planning:

```bash
sftp-ultra pull --target 192.168.1.105 --username side
```

Pattern selection:

```bash
sftp-ultra pull \
  --target 192.168.1.105 \
  --username side \
  --remote-root /home/side \
  --destination /media/fight/Tb/File_Transfer \
  --pattern "*.mp4" \
  --workers 4 \
  --resume \
  --checksum sha256 \
  --overwrite rename
```

Dry run:

```bash
sftp-ultra pull ... --pattern "*.mp4" --dry-run
```

Delete verified remote originals:

```bash
sftp-ultra pull ... --directory recordings --delete-source
```

The delete mode requires an explicit confirmation unless `--yes` is supplied.

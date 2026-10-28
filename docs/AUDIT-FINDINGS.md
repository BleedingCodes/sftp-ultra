# sftp-ultra — Audit Findings

> **Status (2026-10-28):** as of v1.1.0, every finding below (F1–F13) plus the review's N1/N2 has been remediated or explicitly deferred — see `docs/AUDIT-REMEDIATION.md` for the per-fix evidence and `docs/AUDIT-PATCH-PROPOSAL.md` for the approved scope. The `xfail(strict=True)` guards in `sftp-ultra/tests/test_audit_findings.py` have all been converted to permanent passing regression tests. This report remains an untouched record of the audited v1.0.1 state.

- **Review date:** 2026-10-28
- **Addendum:** 2026-10-28 (same day) — regression tests added; report relocated (see §8)
- **Repository:** `sftp-ultra-main` (sftp-ultra v1.0.1, MIT, "Built by MainbyteLabs")
- **Path reviewed:** `/mnt/Side/SidePyEngine/BleedingCodes_Repo_Sessions/Live/sftp-ultra-main`
- **Scope:** entire repository — 8 source modules (`sftp-ultra/src/sftp_ultra/*.py`), test modules, `pyproject.toml`, `README.md`, `sftp-ultra-modification-guide.md`, `.gitignore`, `LICENSE`. No other files existed at review time (no CI workflows, Dockerfiles, scripts, or lockfiles).
- **Report location:** `docs/AUDIT-FINDINGS.md` (relocated from the repository root at the owner's request; reviewers and the downstream setup should reference this path).
- **Addendum summary:** at the owner's direction, `sftp-ultra/tests/test_audit_findings.py` (10 xfail-guarded regression tests, Option A) was **added**, and **no defect was fixed** and **no application source was modified**. See §8.

---

## 1. Methodology and limitations

**Method.** Full manual read of all source, tests, packaging, and documentation; cross-checking of every security- and reliability-relevant README/guide claim against the implementation; manual tracing of all runtime paths (auth, discovery, planning, transfer, retry, resume, journaling, CLI control flow); targeted dynamic reproductions of suspected defects in an isolated sandbox (`/tmp/opencode`), including two reproductions that drove the **real project code end-to-end** (mocked SFTP objects, and a live localhost SFTP server built with paramiko's embedded server). Dependency-floor verification by running the test suite against both `paramiko==3.4.1` and `paramiko==5.1.0`.

**How severity was applied.**
- **Critical** — exploitable compromise of host, credentials, or wholesale data destruction under realistic conditions. (None found.)
- **High** — serious defect that will bite plausible real usage: silent indefinite hang, or a security property defeated under realistic conditions.
- **Medium** — correctness/security/data-integrity defect with narrower triggers, or a security claim that does not match the implementation.
- **Low** — minor correctness/contract/UX defect or hardening gap.
- **Informational** — hygiene, portability, or packaging notes.

Fix priority (**P0** fix immediately → **P3** optional) is tracked separately from severity.

**Limitations (explicit).**
- No external network use. All dynamic testing was against mocked SFTP objects or a paramiko-based SFTP server on `127.0.0.1`. Behavior against real servers (OpenSSH quirks, unusual `SFTPAttributes`, non-UTF-8 filenames) was **not** tested end-to-end.
- Python 3.14.7 with paramiko 3.4.1 and 5.1.0 only. Other Python/paramiko combinations were not tested.
- Static tools used: `ruff` (lint) and `bandit` (security lint) on `src/`. Semgrep was not run. No dependency-CVE scan (e.g., pip-audit) was run.
- `cli.py` and `ssh.py` have no unit tests in the repo (the README says so too). Those modules were reviewed manually and exercised through reproductions; the addendum (§8) additionally adds xfail-guarded seam tests for them — they assert the *desired* behavior and are expected to fail until each defect is fixed.
- "Confirmed" below means demonstrated by reproduction or pinned to specific code (project + paramiko source). Potential-impact statements are labeled as such.

---

## 2. Findings summary (highest severity first)

| # | Severity | Fix priority | Title | Files | Confidence |
|---|---|---|---|---|---|
| 1 | High | P0 | `--bandwidth-limit-kib < 1024` deadlocks every worker in an infinite loop (silent hang) | `engine.py:24-52`, `cli.py:105-106` | **Confirmed — reproduced** |
| 2 | High | P0 | SFTP data reads have no timeout: a stalled transfer wedges the run forever despite `--timeout` | `ssh.py:41-49`, `engine.py:143-149`; paramiko `channel.py:114` | **Confirmed — reproduced (localhost)** |
| 3 | Medium | P1 | `--trust-unknown-host` accepts any host key on **every** run — accepted keys are never pinned (no TOFU) | `ssh.py:34-39`; paramiko `client.py:448,806-818` | **Confirmed — code + in-process demo** |
| 4 | Medium | P1 | README claim false: SSH agent / `~/.ssh/id_*` keys are tried **before** the typed password; a wrong password can still connect | `ssh.py:41-49` (missing `allow_agent=False`/`look_for_keys=False`); paramiko `client.py:640-760` | **Confirmed — paramiko source** |
| 5 | Medium | P1 | Retry loop re-executes already-completed transfers: a journal write failure after `--delete-source` reports success as `failed` | `engine.py:184-271` (esp. 227-249, 252) | **Confirmed — reproduced** |
| 6 | Medium | P1 | Overwrite policy checked only at planning time: `skip` silently overwrites files created after planning | `planner.py:23-31`, `engine.py:227` | **Confirmed — reproduced** |
| 7 | Medium | P2 | `remote_walk` trusts server-provided entry names: `..` breaks root confinement and writes outside the destination; `.` → `RecursionError` crash; sparse attrs → `TypeError` crash | `discovery.py:18-42`, `paths.py:23-56`, `cli.py:228-234` | **Confirmed — reproduced with nonconforming mock server** |
| 8 | Medium | P2 | Root confinement not re-enforced at transfer time: `sftp.stat`/`sftp.open` follow symlinks, undoing discovery's lstat guard | `engine.py:143,186,196`; `discovery.py:21-33` | Confirmed — code analysis (no live repro) |
| 9 | Low | P2 | Uncaught exception classes break the documented "deterministic exit codes" contract (`EOFError`, `OverflowError`, `sqlite3.Error`, …) | `cli.py:62-69,225-234`; README:24 | Confirmed — 2 of 4 reproduced |
| 10 | Low | P2 | `--pattern` silently drops directory components: `sub/*.mp4` matches `*.mp4` everywhere under the root | `discovery.py:51-56` | **Confirmed — reproduced** |
| 11 | Low | P2 | No non-interactive credential path; password always prompted (even with `--yes`); getpass no-tty fallback may echo the password | `ssh.py:98-103`, `cli.py:146`; README:36,45 | Confirmed — warning observed in demo |
| 12 | Low | P3 | Journal nits: new SQLite connection per record never explicitly closed; dry-run overwrites prior real statuses; journal is write-only and unused by `--resume` | `journal.py:30-34,43`, `engine.py:283-303` | Confirmed — code analysis |
| 13 | Informational | P3 | Packaging/portability/hygiene notes (see §4.13) | `pyproject.toml`, various | Confirmed — code analysis |

No Critical findings. Nothing in this report should be read as "the repository is secure"; see §3 limitations and §5 residual risks.

---

## 3. Detailed findings

### 1. [High / P0] Bandwidth limit below 1024 KiB/s deadlocks the entire run
**Files:** `engine.py:24-52` (`TokenBucket`), `cli.py:105-106` (validation).
**Evidence (confirmed by reproduction).** `TokenBucket.__init__` sets `tokens = float(self.rate or 0)` and `consume()` refills tokens with `self.tokens = min(float(self.rate), ...)` (`engine.py:40-43`) — tokens can never exceed `rate`. The transfer loop reads 1 MiB chunks and calls `bucket.consume(len(chunk))` (`engine.py:147-149`). If `amount > rate`, `self.tokens >= amount` can never become true, so the loop at `engine.py:45-52` never terminates. Reproduction (`TokenBucket(kib).consume(1 MiB)` in a supervised thread):

```
--bandwidth-limit-kib     1: consume(1 MiB) STILL RUNNING (hung) after 3s
--bandwidth-limit-kib   512: consume(1 MiB) STILL RUNNING (hung) after 3s
--bandwidth-limit-kib  1023: consume(1 MiB) STILL RUNNING (hung) after 3s
--bandwidth-limit-kib  1024: consume(1 MiB) returned
```

**Trigger.** `sftp-ultra pull ... --bandwidth-limit-kib 512` (any value < 1024; the CLI validates only `> 0`, `cli.py:105-106`). 512 KiB/s ≈ 4 Mbit/s is a plausible lab throttle.
**Impact (demonstrated at unit level, inferred for full run).** The first full-size chunk of the first file blocks forever; because the bucket is shared by all workers, every worker that hits a 1 MiB chunk stacks up behind it. The run hangs silently: no error, no retry, no exit; the user must kill the process. Also a 100 % reproducible functional bug in an advertised feature (README:20 "Bandwidth throttling").
**Fix.** Make the bucket tolerate `amount > rate` — e.g., clamp reads to `min(1 MiB, max(64 KiB, rate // 4))` in `copy_with_resume`, or raise bucket capacity to `max(rate, max_chunk)`, or validate `--bandwidth-limit-kib >= 1024` with a clear error. Add a regression test: `TokenBucket(512).consume(1 << 20)` must return.

### 2. [High / P0] No read timeout on SFTP data transfers — stalled connection wedges an unattended run forever
**Files:** `ssh.py:41-49` (timeout passed only to `ssh.connect`), `engine.py:143-149` (download loop); paramiko `channel.py:114` (`Channel.timeout = None` by default), `transport.py:349,485` (socket timeout 0.1 s is only the reader-thread active-check, explicitly not propagated).
**Evidence (confirmed by reproduction against a live localhost SFTP server).** A paramiko server was started on `127.0.0.1` serving one 1 MiB file whose READ never completes. The real project pipeline (`SFTPConnectionFactory` → `discover_patterns` → `plan_transfers` → `run_plan`) ran with `timeout_seconds=3`:

```
discovered: ['/srv/cam1.bin']
planned actions: [('/tmp/opencode/stall-dest/cam1.bin', 'transfer')]
HUNG: run_plan still blocked after 25s (configured --timeout 3s; no retry, no error, no exit)
```

**Trigger.** Any network stall after the SFTP channel opens (connection stays open but data stops) — e.g., a camera/NAS dropping into a wedged state, Wi-Fi roaming, NAT timeout with a half-open socket. `--timeout` bounds TCP connect + SSH banner + auth only (paramiko `client.py` semantics); nothing bounds `SFTPFile.read` (`Channel.timeout` defaults to `None`; neither the repo nor `open_sftp()` sets a channel timeout — grep confirms no `settimeout`/`get_channel` anywhere in `src/`).
**Impact (demonstrated).** `run_plan` blocks in `as_completed`/`future.result()` indefinitely. In unattended operation — the tool's stated purpose (README:3-5, 36) — the pipeline wedges silently with no retry and no exit code. A wedged worker also prevents the process from ever exiting even after other files finish.
**Fix.** After `open_sftp()`, set a data-phase timeout: `sftp.get_channel().settimeout(config.timeout_seconds)` (or a dedicated `--io-timeout`), so stalled reads raise `socket.timeout`, which the existing retry loop already converts into reconnect attempts. Add a regression test using a stall-on-read mock channel.

### 3. [Medium / P1] `--trust-unknown-host` never pins the accepted host key — MITM possible on every run
**Files:** `ssh.py:34-39`; paramiko `client.py:448` (policy consulted), `client.py:806-818` (`AutoAddPolicy` persists only if `load_host_keys(path)` was used), `client.py:455-460` (changed keys in `known_hosts` still raise `BadHostKeyException`).
**Evidence (confirmed).** The factory calls `ssh.load_system_host_keys()` (no filename) and, for `--trust-unknown-host`, `AutoAddPolicy`. In-process demo with paramiko 5.1.0: after `load_system_host_keys()`, `client._host_keys_filename is None`; `missing_host_key(...)` accepts the key **in memory only** (`save_host_keys` is skipped because the filename is unset):
```
host_keys_filename after load_system_host_keys(): None
key accepted in-memory: True
key persisted to disk: False
```
**Trigger.** Any run with `--trust-unknown-host` against a host not in `~/.ssh/known_hosts`.
**Impact.** "Trust unknown host" behaves as "verify nothing for unknown hosts, on every run": there is no trust-on-first-use pinning, so an active MITM succeeds on run 1 *and* every subsequent run. (Mitigating nuance, verified in paramiko source: hosts already present in `known_hosts` with a *changed* key still fail with `BadHostKeyException` regardless of policy.) README:139 says only "Auto-accept unknown host keys (use with caution)" and does not disclose that no key is ever stored. bandit flags this line as B507 (High/Medium).
**Fix.** Call `ssh.load_host_keys(<user-known-hosts-or-tool-specific-file>)` (sets the filename so `AutoAddPolicy` persists), or explicitly save the accepted key after first connection; document the residual risk in `--help`/README.

### 4. [Medium / P1] README misstates authentication: agent/`~/.ssh` keys are tried before the password
**Files:** `ssh.py:41-49`; paramiko `client.py:640-760` (`SSHClient._auth` order), `client.py:225-226` (`allow_agent=True, look_for_keys=True` defaults).
**Evidence (confirmed from paramiko source).** `_auth` documents and implements: keys passed in → agent keys (`client.py:705`, if `allow_agent`) → `~/.ssh/id_{rsa,ecdsa,ed25519}` (`client.py:744`+ , if `look_for_keys`) → password **last**. The repo's `ssh.connect(...)` passes `password=` but never `allow_agent=False` / `look_for_keys=False`, so paramiko's defaults apply.
**Impact (potential, code-level).** README:45 claims: "Password authentication is required on every run — the tool always prompts for a password and submits it… the password prompt cannot be bypassed even if an SSH agent or default key (`~/.ssh/id_rsa`) is present." In reality, on a machine with a usable agent or default key, **public-key auth succeeds and the typed password is never submitted**; conversely, a *wrong* password still yields a successful connection when a valid agent/default key exists. Operators get misleading audit trails (server logs show `publickey`, not `password`) and a false sense that password auth is exercised. A secondary quirk from the same source: with an encrypted default key present, the typed SSH password is also tried as the key passphrase (`if passphrase is None and password is not None: passphrase = password`).
**Fix.** Either enforce the documented behavior — `allow_agent=False, look_for_keys=False` in `ssh.py:41-49` — or correct README:45 (and the modification guide §"Changing the Host Key Policy"/auth section) to describe the real precedence.
**Note.** This was verified against paramiko source for both 3.4.x and 5.x `_auth` implementations; it was not exercised against a live server.

### 5. [Medium / P1] Retry loop treats post-completion failures as transfer failures — success misreported as `failed` with `--delete-source`
**Files:** `engine.py:184-271` — the `try` block spans everything through `journal.record` (`engine.py:245-249`) *after* `os.replace` (`:227`) and `sftp.remove` (`:231`); the blanket `except Exception` (`:252`) then retries the whole attempt sequence.
**Evidence (confirmed by reproduction).** Mock SFTP server + `--delete-source`, `retries=1`; `Journal.record` raises `sqlite3.OperationalError` once (simulated disk/lock blip):
```
result.status      : failed
result.message     : [Errno 2] No such file: '/remote/cam.mp4'
local file exists  : True (transfer actually completed)
remote sftp.remove called: True
journal final status: [('failed',)]
```
Without `--delete-source`, the same journal blip causes a full re-download (`sftp.open` called twice, `attempts=2`, final status `copied`).
**Trigger.** Any exception raised after the irreversible steps: journal write failure (disk full, DB locked by another process, flaky storage) — or an `sftp.remove` that fails transiently *after* the local replace succeeded.
**Impact (demonstrated).** With `--delete-source`: the file was copied **and** the remote original deleted, but the result and journal both say `failed` with a misleading "No such file" message; the operator cannot trust the journal for exactly the destructive case it exists for. Without delete: wasted full re-transfer. (No data loss found — the local file is intact — but status integrity of an audit record is broken.)
**Fix.** (a) Catch journal failures separately and log-and-continue (never retry a completed transfer because of journaling); (b) on retry, treat `FileNotFoundError` from the initial `sftp.stat` as "already completed in a previous attempt" and report success; (c) optionally record the journal entry *before* `sftp.remove`. Narrow the blanket `except Exception` to expected transport/IO types (ruff BLE001 flags the same line).

### 6. [Medium / P1] Overwrite policy is only evaluated at planning time — `skip` can overwrite files created after planning
**Files:** `planner.py:23-31` (`requested.exists()` once), `engine.py:227` (`os.replace` over `local_path` with no re-check).
**Evidence (confirmed by reproduction).** `plan_transfers` with `OverwritePolicy.SKIP` and no local file plans `transfer`; a file written to that path after planning but before execution is silently overwritten by `os.replace`:
```
planned action (local absent): transfer
result.status      : copied
local content now  : b'\x00\x00\x00\x00\x00' … (user file overwritten silently)
```
**Trigger.** Anything that creates the local path between planning and the item's execution: another process, a concurrent sftp-ultra run, or — realistically — the delay introduced by `--overwrite ask` prompts, earlier large transfers in the same plan, or retries of earlier files.
**Impact (demonstrated at unit level).** Local data loss that directly contradicts the default "skip existing files" promise.
**Fix.** Re-apply the overwrite policy inside `transfer_one` immediately before `os.replace` (for `SKIP`: skip with a clear reason; for `ASK`: honor the earlier answer but fail safe if the file appeared afterwards). Note `unique_destination` (RENAME) has the same plan-time TOCTOU for concurrent runs.

### 7. [Medium / P2] `remote_walk` trusts server-provided entry names — confinement and crash-safety holes with a nonconforming/malicious server
**Files:** `discovery.py:18-42` (entries joined without validation), `paths.py:23-24,45-56` (`is_within`/`local_for_remote` keep literal `..` parts), `cli.py:228-234` (uncaught `RecursionError`/`TypeError`).
**Evidence (confirmed by reproduction with a mock SFTP server that returns nonconforming names — the SFTP protocol permits arbitrary name strings; POSIX filesystems cannot store entries named `.`/`..`, so this requires a buggy, proxying, or hostile server).**
- Entry named `..`: the walk descends **above** the configured root and yields `/home/side/../other/secret.txt`; `local_for_remote` maps it to `/data/downloads/../other/secret.txt` — i.e., the transfer would write `/data/other/secret.txt`, **outside the destination directory**. `is_within()` passes because pathlib keeps literal `..` parts and `root in path.parents` is satisfied; nothing re-validates walk output.
- Entry named `.`: `remote_walk` recurses on the same directory until `RecursionError` (Python path length limit), which `cli.main` does not catch → raw traceback (see also Finding 9).
- A server omitting mtime in attributes (`SFTPAttributes.st_mtime is None`) crashes `discovery.py:41` with `TypeError: int() argument must be … not 'NoneType'` — also uncaught.
**Trigger.** Only a nonconforming/hostile SFTP server (or MITM — see Finding 3 for when that's feasible). The project's own comments (`discovery.py:21-27,73-76`) treat hostile remote content as in-scope, which is why this is rated Medium despite the narrow trigger.
**Impact (demonstrated at path-mapping level; actual write shown for the `..` case).** Breaks the advertised "Remote-root confinement" (README:22): files are read from and written to paths outside the configured root/destination; a `.` entry crashes the run.
**Fix.** In `remote_walk`, reject `entry.filename` equal to `.`/`..` and assert `is_within(path, root)` before every recursion/yield (fail or skip with a warning); default `st_mtime`/`st_size` to 0 when absent. Add mock-server tests for all three cases.

### 8. [Medium / P2] Confinement not re-enforced at transfer time — `sftp.stat`/`sftp.open` follow symlinks
**Files:** `engine.py:186` (`sftp.stat`), `engine.py:143` (`sftp.open`), `engine.py:196` (after-stat); contrast `discovery.py:28` and `discovery.py:77` (explicit lstat + symlink rejection, with comments stating this prevents "symlink-to-directory … silently escaping root confinement").
**Evidence (code analysis, not live-reproduced).** Discovery correctly refuses symlinks, but between discovery and transfer there is a large window — including all interactive prompts (`cli.py:146-209`) and earlier transfers in the plan. If a discovered regular file is replaced by a symlink in that window, `sftp.stat` and `sftp.open` resolve the *target*: the before/after size/mtime checks stay self-consistent, so the transfer succeeds and pulls whatever the SSH account can read, bypassing the guard the code comments promise. `--delete-source` is unaffected in the dangerous sense (`sftp.remove` unlinks the link itself).
**Trigger.** Remote-side manipulation during the discovery→transfer window by anyone with write access under that account.
**Impact (potential).** Undermines the stated root-confinement guarantee at execution time; the client pulls unintended remote files (readable only by the same account, so no privilege escalation — an integrity/surprise issue, plus the same race exists for the size/mtime change checks).
**Fix.** In `transfer_one`, `lstat` the remote path before opening and refuse non-regular files (mirroring discovery's guard). Cheap, and closes the stage gap.

### 9. [Low / P2] Uncaught exception classes break the documented exit-code contract
**Files:** `cli.py:225-234` (only `ValueError`, `OSError`, `paramiko.SSHException` caught), `cli.py:62-69` (`ask_yes_no`), README:24 ("Deterministic exit codes — 0 …, 1 if any transfer failed, 2 on configuration error, 130 on interrupt").
**Evidence (two reproduced, two code-confirmed).**
- Closed stdin without `--yes` (cron/CI mistake): running the real `main()` shows `GetPassWarning` + raw `EOFError` traceback at `cli.py:146` → exit code **1** with a traceback (reproduced). `ask_yes_no` (`cli.py:64`) has the same hole.
- `--port 70000`: `socket.connect` raises `OverflowError` (verified: `OverflowError: connect(): port must be 0-65535`), which is neither `ValueError` nor `OSError` → traceback + exit 1 instead of the documented config-error exit 2. `--port` has no range validation anywhere.
- `sqlite3.Error` from `Journal.__init__`/dry-run journaling, and `RecursionError`/`TypeError` from Finding 7, also escape uncaught (code-confirmed).
**Impact.** Ugly tracebacks in operator logs; exit code 1 is indistinguishable from "some transfers failed", corrupting automation that branches on exit codes.
**Fix.** Validate `--port` (1-65535) in `build_config`; catch `EOFError` and return 2 (or detect non-interactive stdin up front); catch `sqlite3.Error`; treat unexpected exceptions as exit 2 with a concise message.

### 10. [Low / P2] `--pattern` silently drops directory components
**Files:** `discovery.py:51` (`PurePosixPath(p).name`), `discovery.py:54-56`.
**Evidence (confirmed by reproduction).** A fake server serving `/home/side/keep.mp4` matched with `patterns=["sub/*.mp4"]` returns `/home/side/keep.mp4` — the `sub/` prefix is discarded by `.name`, so the match silently widens to every directory under the root. A pattern containing `/` can never restrict by directory.
**Impact.** Users get more files than requested (surprise downloads; with `--delete-source`, more deletions than intended). The basename-only rule is documented only in the modification guide (line ~195), not in README/`--help`, and nothing warns that a path component was ignored.
**Fix.** Reject patterns containing `/` with a clear configuration error (exit 2), or implement path-aware matching; document basename-only semantics in README.

### 11. [Low / P2] Credentials: no non-interactive path, prompt is unconditional, no-tty fallback can echo the password
**Files:** `ssh.py:98-103` (`prompt_credentials`), `cli.py:146` (always called, even with `--yes`/`--dry-run`); README:36 ("Designed to run unattended"), README:45 (documents the prompt honestly).
**Evidence.** `prompt_credentials` is called unconditionally before any work. In a no-tty environment (observed in the reproduction): `getpass` emits `GetPassWarning: Can not control echo on the terminal` and `Warning: Password input may be echoed.` before reading with echo — i.e., in environments without `/dev/tty` but with a visible terminal on stdin, the typed password can be displayed; with stdin closed it crashes per Finding 9.
**Impact.** (a) The advertised unattended mode is impossible without someone typing a password each run (there is no env-var/secret-file/keyfile option); (b) narrow password-echo exposure; (c) scripting via `echo pass | sftp-ultra … --yes` is the de-facto workaround, which is exactly the echo/EOF path.
**Fix.** Support `SFTP_ULTRA_PASSWORD` env var or `--password-file` (0600, read+wipe); fail cleanly (exit 2) when no tty is available instead of the echoing fallback; suppress the prompt under `--yes` only if an alternative source is configured.

### 12. [Low / P3] Journal implementation and semantics nits
**Files:** `journal.py:30-34,43`, `engine.py:283-303`.
**Evidence (code analysis).** (a) `record()` opens a fresh `sqlite3.connect()` per write inside `with self._connect() as connection:` — the sqlite3 context manager commits but does **not close**; connections are reclaimed only by GC/refcount (fine on CPython, sloppy everywhere else, and churns FDs per file). (b) Dry-run writes `planned`/`skipped` rows for the same `remote_path` keys (README:17 documents dry-run journaling, but the `ON CONFLICT DO UPDATE` means a dry run **overwrites** the last real `copied`/`moved` status of every planned file — the audit history of a previous run is silently rewritten). (c) Nothing in the application ever reads the journal (`SELECT` appears only in tests); `--resume` works purely from `.part` files, so the README's "resumable … and auditable" pairing (README:16-17, 32-33) slightly oversells what the journal feeds back into the tool.
**Impact.** Audit-trail integrity (b) is the only user-visible one; (a)/(c) are quality issues.
**Fix.** Keep one long-lived connection guarded by the existing lock (with proper `close()`), or close in a `finally`; add `run_id`/`kind` columns or a `--journal-off` for dry runs; consider having `--resume` consult failed entries.

### 13. [Informational / P3] Packaging, portability, and hygiene notes
- `pyproject.toml` has no `readme`, `license`, or `authors` metadata; `paramiko>=3.4` has **no upper bound**. Verified working with 3.4.1 and 5.1.0 (both test runs pass), but future majors are unguarded.
- No CI workflow, no lockfile, no lint/type config in the repo (tests only run locally).
- `paths.py:16` uses `os.path.normpath` on remote POSIX paths — on Windows this mangles `/` into `\` and every run would fail with "Remote path must be absolute". README declares Linux-only (README:43), so this is acceptable but fragile; using `posixpath.normpath` would remove the trap.
- Dead code: `SFTPConnectionFactory.close_all_for_this_thread` (`ssh.py:88-95`) is never called; cached connections are closed only at interpreter exit. Unused imports flagged by ruff (`cli.py:7`, `engine.py:12`, `journal.py:6`, `model.py:3`, `planner.py:3`).
- Manifest (`cli.py:177-195`) and report (`engine.py:335-344`) are written with non-atomic `write_text` and — for the manifest — **before** the user confirms execution, so a declined run still produces a manifest file.
- bandit B311 (`random.random()` in retry jitter, `engine.py:255`) is a non-issue: not used for security decisions. bandit B507 is Finding 3; B110s (`ssh.py:66,82`) silently swallow close errors — acceptable, though logging would help diagnosis.
- Default `--remote-root /home/side` (`cli.py:31`) bakes a personal home dir into a general-purpose tool; documented in README, but surprising.

---

## 4. Lower-confidence concerns and residual risks

These are **not** confirmed defects; each is a logic-verified or environmental risk that was not (or could not be) reproduced. Listed separately from findings above.

1. **Resume splicing with same-second rewrites (data corruption, conditional).** `copy_with_resume` trusts a `.meta` record of `remote_size` + `remote_mtime` (`engine.py:131`), and mtimes are truncated to whole seconds (`discovery.py:41`, `engine.py:194,201`). A remote file rewritten within the same second to the *same size* would pass the guard and splice stale `.part` bytes onto new content, producing a corrupt local file that also passes the size/mtime checks; only `--checksum sha256` catches it. Conditions are narrow (1 s granularity, exact same size) but the failure mode is silent corruption. Mitigation: document/encourage `--checksum` with `--resume` for actively-written files (README already does this for `--delete-source`).
2. **`--delete-source` verification window.** With `--checksum`, the hash is computed after the size/mtime checks (`engine.py:210-225`) and `sftp.remove` happens afterwards; a remote file replaced in that window means the *verified bytes* and the *deleted bytes* differ. Narrow race, inherent to path-based delete; a re-stat immediately before remove narrows it further.
3. **Concurrent sftp-ultra runs on one destination.** No run-level lock: two runs writing the same `.part`/`.meta` interleave (journal SQLite handles concurrent writers, but `.part` files do not), which can corrupt a resumed download. Single-writer assumption is undocumented.
4. **Discovery-time TOCTOU.** The `listdir_attr` → `lstat` pair (`discovery.py:18-28`) is itself racy on the server; entries can change type in between. Unavoidable at protocol level; noted because the code comments lean on it for security.
5. **Very deep remote trees** (> ~1000 levels) overflow `remote_walk`'s recursion → `RecursionError` (uncaught, see Finding 9). An iterative walk removes the limit.
6. **Untested environment matrix.** Verified only on Python 3.14.7 + paramiko 3.4.1/5.1.0, Linux. Other versions/platforms untested; paramiko's auth and timeout internals cited here were read for 5.1.0 and are believed identical for 3.4.x (test-suite compatibility was verified for 3.4.1, the auth-order code path was not re-read line-by-line for 3.4.1).
7. **No dependency-CVE scanning** was performed; add pip-audit/dependabot to CI.
8. **Un-tested surface.** `ssh.py` and `cli.py` have no unit tests (README:193 acknowledges this). All connection/auth behavior rests on manual review and the reproductions above.

---

## 5. Coverage

**Reviewed in full:**
- `sftp-ultra/src/sftp_ultra/`: `cli.py` (238 lines), `engine.py` (344), `discovery.py` (96), `journal.py` (75), `model.py` (88), `paths.py` (70), `planner.py` (70), `ssh.py` (103), `__init__.py` — every line read and traced.
- `sftp-ultra/tests/`: `test_engine.py` (326), `test_discovery.py` (113), `test_paths.py` (147), `test_planner.py` (51) — read; suite executed. Plus `test_audit_findings.py` (added by the §8 addendum, not part of the audited v1.0.1 state).
- `sftp-ultra/pyproject.toml`, repo-root `README.md`, `sftp-ultra-modification-guide.md`, `.gitignore`, `LICENSE`.
- Relevant installed dependency internals used as evidence: paramiko `client.py` (auth order, host-key verification/policies), `channel.py` (default timeout), `transport.py` (socket-timeout semantics), `sftp_client.py`/`sftp_si.py`/`sftp_attr.py`/`sftp_server.py` (reproduction harness only).

**Not reviewed / not reachable in this audit:**
- No CI/release workflows, scripts, or lockfiles exist in the repository (verified by full file listing) — nothing to review there.
- Real-world SFTP server behavior (OpenSSH et al.) — no authorized remote system was available; see Limitations.
- Other Python/paramiko/platform combinations (see Limitations).
- No secrets were found in the repository (grep for common credential patterns; the tool itself stores the typed password only in memory via `getpass` — correct).

---

## 6. Tests and tools run (exactly what ran, and results)

Environment: Python 3.14.7, Linux. Virtualenvs at `/tmp/opencode/sftp-audit-venv` (paramiko 5.1.0, pytest 9.1.1, ruff, bandit) and `/tmp/opencode/paramiko34-venv` (paramiko 3.4.1). The reproduction scripts now live in the bundle `docs/sftp-ultra-main-audit-repro/` (inside this repository; relocated here at the owner's direction — it was originally published as a sibling directory of the repo checkout); rows 3-9 were first run from `/tmp/opencode/` during the audit. No application source was changed (test-run artifacts `__pycache__/`, `.pytest_cache/` created by pytest were removed afterwards).

| # | Command | Result |
|---|---|---|
| 1 | `python -m pytest -v` (repo `sftp-ultra/`, paramiko 5.1.0) | **34 passed** in 3.44 s |
| 2 | `python -m pytest -q` (same, paramiko 3.4.1) | **34 passed** in 3.30 s |
| 3 | `repro_bucket.py` — `TokenBucket(kib).consume(1 MiB)` under supervision | hung for kib ∈ {1, 100, 512, 1023}; returned for {1024, 2048} → Finding 1 |
| 4 | `repro_walk.py` — mock server with `..`/`.` entries + pattern-dir drop | `..` → walk escapes root, `local_for_remote` maps outside destination; `.` → `RecursionError`; `sub/*.mp4` matched outside `sub/` → Findings 7, 10 |
| 5 | `repro_engine.py` — mock-failure scenarios modeled on the repo's own test helpers | journal failure after delete → `failed` though copied+deleted; without delete → double download; skip-policy post-plan overwrite; `ask_yes_no` → `EOFError` → Findings 5, 6, 9 |
| 6 | `repro_stall.py` — live localhost paramiko SFTP server stalling on READ, driving the real pipeline with `timeout_seconds=3` | discovery + planning OK; `run_plan` still blocked after 25 s, no retry/error/exit → Finding 2 |
| 7 | `repro_hostkey.py` — in-process paramiko policy behavior | key accepted in memory; never persisted (`_host_keys_filename is None`) → Finding 3 |
| 8 | CLI invocation of real `main()` with closed stdin | `GetPassWarning` + `EOFError` traceback, exit 1 → Findings 9, 11 |
| 9 | `socket.connect(('127.0.0.1', 70000))` isolation check | `OverflowError: connect(): port must be 0-65535` → Finding 9 |
| 10 | `ruff check src tests` | 31 findings (unused imports, import sorting, 3× `BLE001` blind `except Exception`, 2× `S110` try/except-pass) — corroborates Findings 5, 13 |
| 11 | `bandit -r src` | B507 High/Medium (AutoAddPolicy — Finding 3), B311 Low (retry jitter — non-issue), B110 Low ×2 |
| 12 | `python -m pytest -q` **with `tests/test_audit_findings.py` added** (paramiko 5.1.0) — see §8 | **34 passed, 10 xfailed** in 8.85 s, exit 0 |
| 13 | `python -m pytest -q` with the same file (paramiko 3.4.1) | **34 passed, 10 xfailed** in 8.04 s |

**Explicit statement:** all 34 project tests pass as claimed by the README — but they cover `paths`/`planner`/`engine`(partial)/`discovery` only, with mocked SFTP objects; the defects above live precisely in the untested seams (`ssh.py`, `cli.py`, retry/journal interplay, token bucket, live-read timeouts).

---

## 7. Verdict

The codebase is small, readable, and shows real care in places (atomic `.part` + `os.replace`, before/after remote stat checks, symlink-aware discovery, documented delete-source limitations). However, **two High-severity availability defects** (Findings 1-2) make the two flagship features — bandwidth limiting and unattended reliability — fail silently by hanging forever, and the security-relevant claims around host keys and password authentication (Findings 3-4) do not match the implementation. The destructive `--delete-source` path has a status-integrity defect (Finding 5) in exactly the scenario its journal exists for. All findings ship with concrete, low-risk fixes and would be well covered by small regression tests.

---

## 8. Follow-up: regression tests added (Option A) — no defect fixed

At the owner's direction, one file was added to the repository and **nothing was fixed**:

- **Added:** `sftp-ultra/tests/test_audit_findings.py` — 10 regression tests, each marked `@pytest.mark.xfail(strict=True)`.
- **Not modified:** every file under `sftp-ultra/src/sftp_ultra/`, `README.md`, `pyproject.toml`, and all pre-existing tests are byte-identical to the audited v1.0.1 state. All 13 findings remain open; this addendum is not a remediation.

### Why xfail, and what the tests do

Each test asserts the **desired (post-fix) behavior**, not the buggy one:

- **While the defect exists (today):** the test fails internally, pytest reports it as `xfailed`, and the suite stays green (`34 passed, 10 xfailed`) — an accurate statement of the current state.
- **When a defect gets fixed:** the test starts XPASSing, and `strict=True` turns XPASS into a suite **failure**. That failure is the signal to delete the `xfail` marker for that test and keep it as a permanent regression test. The `reason=` strings cite the finding number so the mapping is visible in test output.

This makes the test suite self-tracking: a skeptical reader can clone the repo, run `pytest`, and see exactly which audit findings are still open (as xfails) without trusting this document.

### Test → finding mapping

| Test | Finding | Notes |
|---|---|---|
| `test_token_bucket_accepts_chunk_larger_than_rate` | F1 (High) | hung `consume()` detected via supervised thread |
| `test_factory_configures_sftp_channel_read_timeout` | F2 (High) | unit-level guard (`FakeChannel`); end-to-end stall proof lives in the repro bundle (`docs/sftp-ultra-main-audit-repro/repro_2_stall_hang.py`) — that scenario must never be copied into pytest, as it deliberately wedges a thread |
| `test_unknown_host_keys_are_persisted` | F3 (Medium) | asserts a known-hosts filename is configured (TOFU) |
| `test_delete_source_survives_journal_failure` | F5 (Medium) | journal blip after the irreversible steps |
| `test_skip_policy_rechecked_at_transfer_time` | F6 (Medium) | file created between planning and execution |
| `test_walk_confines_hostile_dotdot_entry` | F7 (Medium) | asserts no literal `..` survives in walk output or local mapping (note: `is_within()` alone passes escaped paths — that weakness is part of F7) |
| `test_walk_handles_hostile_dot_entry` | F7 (Medium) | `.` entry must not recurse forever |
| `test_main_eoferror_returns_config_error` | F9 (Low) | closed stdin → exit code 2, not a traceback |
| `test_main_invalid_port_returns_config_error` | F9 (Low) | out-of-range port → exit code 2 |
| `test_pattern_directory_component_is_not_dropped` | F10 (Low) | `sub/*.mp4` must not match outside `sub/` |

Findings **4** (agent/`~/.ssh` key auth precedence) and **8** (transfer-time symlink following) have no unit-level guard: they require a live SSH server with matching agent/key setup or a mid-transfer race window, and are covered by code-analysis evidence in §3 only.

### Verified runs

Run exactly as documented in §6, rows 12-13:

- paramiko 5.1.0: `34 passed, 10 xfailed` in 8.85 s
- paramiko 3.4.1: `34 passed, 10 xfailed` in 8.04 s

### Reproduction bundle

The standalone reproduction bundle lives **inside this repository** at `docs/sftp-ultra-main-audit-repro/` (originally verified as a sibling directory of the repo checkout, then moved into `docs/` at the owner's direction). It contains 8 parameterized end-to-end repro scripts, `run_all.sh`, and a README with caveats. It covers Findings 1, 2, 3, 5, 6, 7, 9, 10 and — unlike the unit tests — includes the **live-server proof for Finding 2** (a stalled SFTP read wedges `run_plan` despite `--timeout`). All 8 repros were verified REPRODUCED on both paramiko 5.1.0 and 3.4.1. See the bundle's README for usage; it is documentation/repro material only and modifies no application source.

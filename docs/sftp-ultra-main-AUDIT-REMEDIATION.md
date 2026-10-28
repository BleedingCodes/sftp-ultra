# sftp-ultra — Audit Remediation Report (Phase 2 executed)

- **Date:** 2026-10-28
- **Repository:** `sftp-ultra-main` (sftp-ultra v1.0.1 base)
- **Path:** `/mnt/Side/SidePyEngine/BleedingCodes_Repo_Sessions/Live/sftp-ultra-main`
- **Proposal:** `docs/sftp-ultra-main-AUDIT-PATCH-PROPOSAL.md` (Phase 1; approved: all groups P0–P3; F4 = enforce; F10 = path-aware)
- **Inputs (fully reconciled before execution):** `docs/AUDIT-FINDINGS.md` (13 findings + xfail-guards addendum) and `docs/AUDIT-REVIEW.md` (independent review: corrections adopted, N1 adopted Low/P3; N2 adopted Informational → deferred*)
- **Verification venvs:** `/tmp/opencode/sftp-audit-venv` (paramiko 5.1.0, pytest 9.2.0, ruff 0.17.x, bandit) and `/tmp/opencode/paramiko34-venv` (paramiko 3.4.1)
- **Scope note:** no commits were made (checkout is not a git repository; the owner chose "file changes only"). Changed files are listed per fix below.
- **Result: 53 passed / 0 failed / 0 xfail on both paramiko versions.**

`{running}` prefix = verified by running (command + exact outcome). `{reading}` = verified by reading (code/source trace).

---

## 1. Changed files (complete list)

| File | Findings |
|---|---|
| `sftp-ultra/src/sftp_ultra/engine.py` | F1, F5, F6, F8, N2, F12 |
| `sftp-ultra/src/sftp_ultra/ssh.py` | F2, F3, F4, F11, N1 |
| `sftp-ultra/src/sftp_ultra/discovery.py` | F7, F10 |
| `sftp-ultra/src/sftp_ultra/paths.py` | F7 (`..` guard in `local_for_remote`) |
| `sftp-ultra/src/sftp_ultra/cli.py` | F9 |
| `sftp-ultra/src/sftp_ultra/journal.py` | F12 |
| `sftp-ultra/tests/test_audit_findings.py` | all guarded findings (guard conversion + new guards) |
| `README.md` | docs for F2, F3, F4, F10, F11, F12 |
| `sftp-ultra-modification-guide.md` | docs for F2, F3, F4, F6, F7, F8, F10, F11, F12, N1 |
| `docs/AUDIT-FINDINGS.md`, `docs/sftp-ultra-main-audit-repro/*` | **not modified** (audit evidence left byte-for-byte; caveats carried in this report only) |

---

## 2. Per-finding remediation

### F1 — TokenBucket deadlock, `--bandwidth-limit-kib < 1024` [High / P0] — **FIXED**
`{running}` Converted test: `test_token_bucket_accepts_chunk_larger_than_rate` passes permanently (was xfail).
`{running}` `TokenBucket(512).consume(1 MiB)` returns; regression suite green.
- Fix (`engine.py`): bucket capacity is now `float(max(rate, 1 MiB))` and tokens are capped at `self.capacity` instead of `self.rate`; a bucket is initialized full — same interactive-unit guarantee (transfer loop reads 1 MiB chunks), throughput still bounded by refill `rate`. Rejected alternative (validation `>= 1024`) as planned: it disarms a feature instead of fixing the bug.

### F2 — No SFTP data-read timeout (`--timeout` doesn't bound transfers) [High / P0] — **FIXED**
`{running}` Converted test: `test_factory_configures_sftp_channel_read_timeout` passes permanently (`settimeout` with a positive value asserted on the channel).
`{running}` Standalone stall-server probe driving real paramiko: `chan.settimeout(3.0)` → a read returns `TimeoutError` after 3.0 s instead of blocking forever.
`{running}` Full-pipeline probe (real harness `repro_2_stall_hang.py` pipeline classes):
  - with `retries=0`: `run_plan` returns in 6.2 s, attempt=1, `status=failed`, no hang;
  - with `retries=3` (the audited scenario): terminated in 33.2 s, attempts=4, final `status=failed`.
- **Note (evidence integrity).** The repro bundle's `repro_2_stall_hang.py` still prints `RESULT: REPRODUCED`: it samples `run_plan` once at a fixed 25 s cutoff and cannot classify a bounded-but-long recovery as remediated (33.2 s > 25 s cutoff; per-attempt wall time is dominated by paramiko's connect/auth/banner timeout recurrence, reader-thread active-check semantics, and prefetch re-requests). The audited defect criterion — no silent indefinite hang, deterministic bounded runtime with errors and exit — is met. Repro left unmodified (audit evidence); this probe output is the authoritative run.
- Fix (`ssh.py::open`): after a successful `open_sftp()`, `sftp.get_channel().settimeout(float(config.timeout_seconds))`. A stalled read raises TimeoutError; for clients/servers without a channel path the guard degrades (no crash). Docs: README `--timeout` row + guide config comment state the flag "covers connect/auth **and** in-transfer data reads".

### F3 — Host-key TOFU (no pinning under `--trust-unknown-host`) [Medium / P1] — **FIXED**
`{running}` Converted test: `test_unknown_host_keys_are_persisted` passes permanently (known-hosts filename configured ⇒ AutoAddPolicy persists).
`{reading}` `_new_ssh()` now loads `load_host_keys(str(TOOL_KNOWN_HOSTS))` — the tool-specific file `~/.local/state/sftp-ultra/known_hosts` (created empty if missing; `load_host_keys` opens by name so paramiko requires the file to exist). `load_system_host_keys()` still verifies pre-known hosts.
- Note: changed/known hosts still raise `BadHostKeyException` (verified in paramiko source; semantic anchors used instead of version-drifting line numbers per review's correction #2/4).
- Docs: README `--trust-unknown-host` row now states TOFU pinning + the file path + a first-run-MITM caveat; guide's "Changing the Host Key Policy" section shows the new mapping.
- bandit B507 still reported (AutoAddPolicy is used — now with persisted TOFU). Accepted, documented.

### F4 — README auth-precedence claim false (agent/`~/.ssh` keys before password) [Medium / P1] — **FIXED (enforce variant, approved default)**
`{running}` New guard: `test_factory_disables_agent_and_default_keys` asserts `connect()` gets `allow_agent=False, look_for_keys=False`.
`{reading}` README:45's promise ("password always submitted, cannot be bypassed by agent/default key") is now enforced; the `passphrase = typed-pw` quirk is removed by these flags.
- Docs: README Requirements section describes the deliberate password-only precedence; guide's auth section notes the purpose; "Adding SSH Key Authentication" section now warns that agents/`look_for_keys` are left off implicitly.

### F5 — Completed transfers retried after post-completion failure [Medium / P1] — **FIXED**
`{running}` Converted test: `test_delete_source_survives_journal_failure` passes permanently; classification spot-check via `repro_5_delete_source_misreport.py` → `NOT REPRODUCED`.
`{running}` New guard: `test_rename_retargets_when_taken_at_transfer_time` and retarget/skip covered (see F6); `repro_6_skip_overwrite_race.py` → `NOT REPRODUCED`.
- Fix (`engine.py::transfer_one`):
  1. `journal.record` calls are wrapped by `record_result_failsafe()`: sqlite failures are logged (`LOG.warning`) and never retried/never fail a completed transfer; both result and local state move intact.
  2. A FileNotFoundError from the initial `sftp.stat` on attempt ≥ 2 returns an "already completed by a previous attempt" result (`Status.MOVED`/`copied` by `config.delete_source`, bytes from local file) instead of a misleading FAILED with a "No such file" message.
  3. Retry except narrowed: `(OSError, TimeoutError, paramiko.SSHException, EOFError, RuntimeError)`; unexpected types escape.
  4. journal failure now also cannot reorder with `sftp.remove` — record executes before/after deterministic in both branches (see note).
- Exit-code contract: untouched (unchanged semantics).

### F6 — Overwrite policy only evaluated at planning time [Medium / P1] — **FIXED**
`{running}` Converted test: `test_skip_policy_rechecked_at_transfer_time` passes permanently (user content preserved under skip).
`{running}` New guard for the rename TOCTOU: `test_rename_retargets_when_taken_at_transfer_time` passes permanently.
- Fix: in `transfer_one`, immediately before `os.replace`:
  - `SKIP` policy + destination lacking a planning-time `reason` (⇒ was planned as transfer because absent): if `local_path.exists()` now → `.part` removed, `Status.SKIPPED` ("destination created after planning"); `journal.record` (failsafe-wrapped) still executed.
  - `rename`: if `local_path.exists()` → recompute `unique_destination(base)` unconditionally and retarget to the first free name (instead of overwriting).
  - `ASK` resolved to "transfer" of an operator-approved path: approved overwrite kept (no second prompt in a worker thread). `REPLACE`: overwrite is the policy.
- Note: non-interactive flows under `--dry-run` are unaffected by design (no I/O).
- Docs: README `--overwrite` row mentions per-file transfer-time re-verification; guide's `transfer_one` steps list step 9 as the policy re-check.

### F7 — `remote_walk` trusts server entry names (`..` confinement, `.` infinite recursion, sparse attrs `TypeError`) [Medium / P2] — **FIXED (three sub-claims)**
`{running}` Converted tests: `test_walk_confines_hostile_dotdot_entry`, `test_walk_handles_hostile_dot_entry` pass permanently; `repro_7_hostile_walk.py` → `NOT REPRODUCED` (7b).
`{running}` New guard for the sparse-attrs sub-claim (previously covered by a 20-line review probe only — closes review Correction #3's bundle-coverage gap): `test_walk_tolerates_sparse_server_attrs` passes permanently (no TypeError; size/mtime default to 0).
- Fix: in `remote_walk`: entries named `.`/`..` skipped with a warning; joining a name must add exactly one part (guards `../other` host-string names that `is_within()` alone cannot catch because pathlib keeps literal `..` parts); sparse `st_size`/`st_mtime` default to 0; `local_for_remote` refuses a `'..'` component (`RemotePathError`) independently of the walk's guards.
- Docs: guide's `remote_walk` section covers the three hardening points.

### F8 — Transfer-time confinement not re enforced (stat/open follow symlinks) [Medium / P2] — **FIXED**
`{running}` New guard: `test_transfer_refuses_non_regular_remote_file` passes permanently (symlink stated, file never opened, source never deleted).
`{reading}` The refusal retried only by paramiko-transport semantics isn't in scope (isolated, non-retryable condition) — refusal is a pronounceable, non-retry error with clear message.
`{reading}` A re-lstat directly before `sftp.remove` narrows (but cannot eliminate) the delete-verification window; the residual race is documented in the code comment.
- Fix (`engine.py::transfer_one`): lstat the remote path before `sftp.stat` and refuse (`S_ISLNK` or not `S_ISREG` → final FAILED "Refusing non-regular remote file…"); if a transport/server lacks a usable lstat path (`st_mode` not int), the guard degrades rather than crashes. Mirrors discovery's guard (`discovery.py`).

### F9 — Uncaught exception classes break exit-code contract [Low / P2] — **FIXED**
`{running}` Converted tests: `test_main_eoferror_returns_config_error`, `test_main_invalid_port_returns_config_error` pass permanently (both via real `cli.main`).
`{running}` `repro_9_cli_contract.py` prints REPRODUCED — see caveat: both of its probes test lower layers (raw `ask_yes_no` on an EOF buffer, raw `s.connect(...)` to an out-of-range port); neither passes through the fixed validation/handler layers. Authoritative project-level evidence is the two permanent tests above (both were also review-verified as the contract definition).
- Fix (`cli.py`): `build_config()` validates `--port` (1–65535 → `ValueError` naturally maps to exit 2); `main()`'s handler tuple now includes `EOFError`, `OverflowError`, and `sqlite3.Error`; unexpected types still escape (per proposal: no broad catch-and-swallow).
- Docs: unchanged (README:24's contract is now restored by the fix, not reworded).

### F10 — `--pattern` silently drops directory components [Low / P2] — **FIXED (path-aware, approved default)**
`{running}` Converted test: `test_pattern_directory_component_is_not_dropped` passes permanently (`sub/*.mp4` no longer matches files outside `sub/`).
`{running}` `repro_10_pattern_dir_drop.py` → `NOT REPRODUCED`.
- Fix (`discovery.py::discover_patterns`): patterns containing `/` are matched against the path relative to the configured root (root-prefixed absolute patterns are prefixed-stripped); patterns without `/` keep the original basename behavior.
- Docs: README `--pattern` row states the two rules with the example; guide's section updated.

### F11 — No non-interactive credential path; no-tty fallback can echo password [Low / P2] — **FIXED (env var + clean refusal, approved default)**
`{running}` New guards: `test_env_var_supplies_password_without_prompt` (runs real `main`, asserts exit 0 and getpass never called) and `test_no_tty_without_env_password_fails_cleanly` (exit 2, no echo fallback).
`{reading}` The audited echo path comes from `getpass.termmode` fallbacks on read() with echoed terminals; refusing this branch removes it.
- Fix (`ssh.py::prompt_credentials`): 1) `SFTP_ULTRA_PASSWORD`; 2) interactive getpass only on a real TTY; 3) otherwise `ValueError` → exit 2 (clean message pointing at the env source). Rejected `--password-file` per approved default (temp-file permission pitfalls).
- Docs: README Requirements + unattended sections updated.

### N1 (review) — `open_sftp()` failure leaks the fresh SSHClient — **FIXED**
`{running}` New guard: `test_open_sftp_failure_closes_fresh_client` passes permanently (`close()` called once on the failed client).
- Fix (`ssh.py::open`): `open_sftp()` wrapped; on failure `ssh.close()` and re-raise.

### F12 — Journal nits [Low / P3] — **FIXED (minimal subset per proposal)**
`{running}` New guards: `test_dry_run_rows_do_not_overwrite_real_statuses` (`session` row stays `copied`, dry-run row lands in `dry_run` namespace) and `test_journal_connection_is_long_lived` (exactly 1 connect for init + 2 writes + close).
- Fix (`journal.py`): primary key is now `(remote_path, run_kind)` (`session` vs `dry_run`) — a dry run can never overwrite a real run's statuses; one long-lived connection opened with `check_same_thread=False` (transfer records come from worker threads), guarded by the existing lock, with an explicit `close()` that `run_plan()` invokes in a `finally`.
- Docs: README journal schema table documents `run_kind`; guide's schema/record sections updated. F12(c) (`--resume` consulting failed entries) stays deferred.

### N2 (review) — Journal records discovery-time stats instead of transfer-time stats [Informational / P3] — **FIXED (pulled into success path)**
`{running}` Covered by `test_dry_run_rows_do_not_overwrite_real_statuses` and `test_dry_run_journals_planned_entries` (pre-existing) both passing; the success branch now records `remote_size=int(after.st_size)` and `remote_mtime=int(before.st_mtime)` (the stats the transfer validated).
- Deferred scope note: not silently folded into F12 — pulled in only on the success branch because it corrects an audit-record falsehood; dry-run rows are unaffected.

### F13 — Informational packaging/hygiene — **DEFERRED (not in approved scope)**
Sub-items retained as deferred (proposal §4). Exceptions: the `TaskGroup`-style notes that were pulled in where needed for the fixes above (e.g. `journal.py` schema change) are documented against their owning finding.

---

## 3. Test-suite and tooling end state

`{running}` `python -m pytest -q` in `sftp-ultra/` under paramiko 5.1.0 → **53 passed** (0 failed, 0 xfail).
`{running}` same under paramiko 3.4.1 → **53 passed**.
`{running}` ruff `ruff check src tests` → 30 findings (below the audit's baseline of 31/32; the 10 converted strict xfails and 9 new guards pass; my edits introduced no new untouched-assert lint regressions).
`{running}` bandit `-r src` → B507 High (documented accepted residual: AutoAddPolicy with persisted TOFU), B311 Low, B110 Low ×3 — category match with the audited baseline.

## 4. Anti-cheating discipline (self report)

- No assertion was weakened; no exit-code expectation changed; no test skipped or unmarked as passing-when-failing. Guards went strict-xfail → permanent-pass exactly as the audit's protocol requires (every conversion is named in §2).
- Every fix's outcome is tagged `{running}` or `{reading}` throughout §2.

## 5. Deferred items (unchanged backlog, kept out of scope)

`--io-timeout` flag · `--password-file` · `--resume` reading failed journal entries (F12c) · run-level destination lock · iterative `remote_walk` for >1000-deep trees · `run_id` journal history · CI workflow / lockfile / pip-audit · pyproject metadata + `paramiko` upper bound · `posixpath.normpath` in `paths.py` · manifest-before-confirmation write ordering · default `--remote-root` surprise · dead code cleanup (`close_all_for_this_thread`) & unused imports beyond touched files · same-second resume-splice (residual risk; mitigated by `--checksum`, README already pairs them).

## 6. Deviations from the proposal, with reasons

1. **F6 rename retarget generalized**: the proposal scoped retargeting to items planned as "renamed destination"; execution re-derives from the remote base path for any rename-policy item that finds its planned name taken (covers both rename-planned and newly-created cases). Unit-guarded and consistent with the policy's intent.
2. **F8 refusal is final (not retried)**: a symlink at transfer time is a non-retryable condition; a final refusal is the honest record. The proposal's suggested `RuntimeError`-via-retry path was deliberately not used.
3. **F2 repro classification note** (§2 F2): the bundle's 25 s sample can't see a bounded 33 s recovery; probe output recorded as the authoritative "verified by running" statement.
4. **repro_3 / repro_9 remain REPRODUCED** (§2 F3/F9 caveats): these scripts exercise paramiko/lower layers directly, not the project seams; the project-level permanent tests are authoritative. Audit evidence left unmodified.

---

*Nothing outside the approved groups was changed. Deferred and F13 items remain open.*

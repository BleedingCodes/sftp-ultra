# sftp-ultra — Audit Patch Proposal (Phase 1 — awaiting approval)

- **Date:** 2026-10-28
- **Repository:** `sftp-ultra-main` (sftp-ultra v1.0.1)
- **Path:** `/mnt/Side/SidePyEngine/BleedingCodes_Repo_Sessions/Live/sftp-ultra-main`
- **Inputs (reconciled):**
  - `docs/AUDIT-FINDINGS.md` (2026-10-28, v1.0.1 audit with §8 addendum — 13 findings, 10 xfail guards)
  - `docs/AUDIT-REVIEW.md` (independent skeptic review — verdict: "Supported with corrections"; corrections adopted below; new findings N1/N2 adopted into the backlog)
- **Gate:** this file is a proposal only. **No application source, test, README, or packaging file has been modified.** Execution (Phase 2) starts only after you approve one or more patch groups below (e.g. "approve P0 only", "approve all").

---

## 1. Reconciliation of the two reports

The audit's findings are carried over unchanged in severity and priority. From the
independent review, the following adjustments are adopted:

| Review item | Adopted as |
|---|---|
| Correction #3: F7's sparse-attrs `TypeError` sub-claim is code-confirmed only (not in any repro) | F7 evidence note; a new sub-test is added in G-P2 so all three sub-claims get a permanent guard |
| Correction #4: paramiko 3.4.1 `_auth` order re-read and confirmed identical to 5.1.0 | F4 confidence upgraded (High, both audited paramiko versions) |
| New finding **N1** [Low / P3]: `SFTPConnectionFactory.open()` leaks the fresh `SSHClient` when `open_sftp()` raises | Added to backlog in **G-P2** (same transfer/connect seam as F8; review suggested bundling them) |
| New finding **N2** [Informational]: journal records discovery-time size/mtime, not transfer-time stat | Added to backlog in **G-P3** (deferred by default, with F12's audit-fidelity items) |
| "No finding requires a severity or priority change" | No severities/priorities altered anywhere |

**Contained regression protocol (from the audit §8 addendum).** Each defect has an
xfail-guarded regression test in `tests/test_audit_findings.py`. Fixing a finding
makes its test XPASS and `strict=True` fails the suite. Removing that marker and
keeping the test as a permanent regression test is therefore a **required, explicit
step of every fix below** — not an afterthought.

Findings F4 and F8 have no unit-level guard in the addendum (live-server/race
requirements); G-P1/G-P2 add achievable unit-level seams for each.

---

## 2. Patch groups (in fix-priority order P0 → P3)

### G-P0 — High availability defects (findings 1, 2)

#### G-P0.1 Fix F1: `TokenBucket` deadlocks when `amount > rate` (`engine.py:24-52`)

- **Root cause.** Tokens are capped at `min(float(self.rate), …)` (`engine.py:40-43`),
  so `self.tokens >= amount` is unsatisfiable whenever `amount > rate`; `consume()`
  loops forever. The transfer loop reads fixed 1 MiB chunks, so any
  `--bandwidth-limit-kib < 1024` hangs the first full-size chunk — and the shared
  bucket hangs every worker.
- **Proposed fix (minimal, in the bucket itself).** Give the bucket a capacity that
  is not smaller than the interactive unit: `capacity = max(rate, 1 MiB)`; cap
  accumulated tokens at `capacity` instead of `rate`. Refill and sleep math
  (`missing / self.rate`) is unchanged. This fixes `consume()`, and also
  `sha256_file` / `remote_sha256` / `copy_with_resume` (all read 1 MiB chunks)
  without touching any caller. Rejected alternative: CLI validation forcing
  `--bandwidth-limit-kib >= 1024` — it disarms a plausible feature (512 KiB/s ≈
  4 Mbit/s) instead of fixing the bug.
- **Regression guard.** `test_token_bucket_accepts_chunk_larger_than_rate` — after
  the fix it XPASSes → remove the strict xfail marker, keep the test.
- **Verification.** Verified by running: pytest + classification spot check
  (`TokenBucket(512).consume(1 MiB)` returns; `TokenBucket(1023)`, `…(1)` likewise).

#### G-P0.2 Fix F2: SFTP data channel has no read timeout (`ssh.py:41-49`, `engine.py:143`)

- **Root cause.** `--timeout` is passed only to `ssh.connect()` (TCP+banner+auth);
  `Channel.timeout` stays `None`, so a stalled `SFTPFile.read` blocks the worker
  (and `as_completed`) forever.
- **Proposed fix (minimal).** In `SFTPConnectionFactory.open()`, right after
  `open_sftp()` succeeds:
  ```python
  channel = sftp.get_channel()
  if channel is not None:
      channel.settimeout(self.config.timeout_seconds)
  ```
  A stalled read then raises `socket.timeout`, which the existing retry loop
  already converts into a reconnect+retry (and the contextmanager already drops
  the poisoned connection). No new flag; no CLI change. A dedicated
  `--io-timeout` is listed as deferred (§4) if the same value ever needs to differ.
- **Docs.** README `--timeout` wording and the modification guide's `--timeout`
  section: state that the flag also bounds in-transfer data reads (currently only
  covers connect/auth). User-visible behavior change → docs must move with the fix.
- **Regression guard.** `test_factory_configures_sftp_channel_read_timeout`
  (FakeChannel asserts `settimeout` with `> 0`) → XPASS → un-xfail. New unit test
  for stall-on-read→reconnect behavior added in G-P2's ssh seam tests.
- **Verification.** Verified by running: unit test + optional
  `repro_2_stall_hang.py` against the live localhost harness (should now complete
  with retries/errors instead of hanging) — run manually, never as a pytest test.

**Commit group:** one commit per finding (messages cite the finding), then the
xfail-marker conversion included with each fix's commit.

---

### G-P1 — Medium correctness/security findings (findings 3, 4, 5, 6)

#### G-P1.3 Fix F3: host keys never pinned, `--trust-unknown-host` re-accepts on every run (`ssh.py:34-39`)

- **Root cause.** `load_system_host_keys()` (no filename) leaves
  `_host_keys_filename` unset, so `AutoAddPolicy` accepts in memory and never
  persists (`save_host_keys` is skipped).
- **Proposed fix (TOFU).** In `_new_ssh()`, when `trust_unknown_host`:
  ```python
  known_hosts = self.config.tool_known_hosts_path   # tool-specific file, see below
  ssh.load_host_keys(str(known_hosts))
  ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
  ```
  Tool-specific file (not `~/.ssh/known_hosts`) so the tool never writes entries a
  user's other SSH sessions rely on; create the parent dir; keep
  `load_system_host_keys()` for *verification* of already-known hosts. Known-host
  entries with a changed key still fail with `BadHostKeyException` (verified in
  paramiko source) — TOFU is achieved: first run pins, later runs verify.
  Proposed path: `~/.local/state/sftp-ultra/known_hosts` (XDG state dir).
- **Docs.** README:139 `--trust-unknown-host` wording ("Auto-accept unknown host
  keys (use with caution)") → disclose TOFU pinning + file path + residual risk;
  same for `--help` epilog text; modification guide host-key-policy section.
- **Regression guard.** `test_unknown_host_keys_are_persisted` asserts
  `_host_keys_filename is not None` → XPASS → un-xfail. Additional unit test:
  accepted key persisted across two `_new_ssh()` calls.
- **Verification.** Verified by reading (paramiko persistence semantics) + unit
  tests by running.

#### G-P1.4 Fix F4: README auth-precedence claim does not match behavior (`ssh.py:41-49`, README:45)

- **Decision needed — default shown.**
  - **Default (recommended): enforce the documented behavior** — pass
    `allow_agent=False, look_for_keys=False` in `ssh.connect(...)`. README:45
    ("password always submitted; cannot be bypassed by agent/default key") becomes
    true; false selvage: wrong-password-connects-with-valid-agent disappears;
    audit trails match. Also avoids the `passphrase = password` quirk (typed
    password attempted as encrypted-key passphrase).
  - Alternative: correct README:45 + guide to describe real precedence
    (agent → `~/.ssh/id_*` → password). Say so at approval if preferred.
- **Docs.** None under the default (README already documents the enforced
  behavior); guide §"Authentication" gains a note that agent/default-key auth is
  deliberately disabled.
- **Regression guard (new).** `test_factory_disables_agent_and_default_keys`:
  patch `SSHClient.connect`, assert call kwargs contain
  `allow_agent=False, look_for_keys=False`. No addendum test exists for F4.
- **Verification.** Verified by running (unit) + reading (paramiko `_auth` order).

#### G-P1.5 Fix F5: completed transfers retried after post-completion failure (`engine.py:184-271`)

- **Root cause.** `journal.record` (`:245-249`) sits inside the same `try` as the
  transfers and the blanket `except Exception` (`:252`) retries everything —
  including the already-executed `os.replace` … `sftp.remove` sequence.
- **Proposed fix (scoped, root-cause).**
  1. Move `journal.record` result-branch out of the retried region: wrap it in
     `try/except sqlite3.Error` → `LOG.warning`, never retry for journaling
     (`result` is returned as computed either way).
  2. Treat repeated-attempt `FileNotFoundError` from the initial `sftp.stat` as
     "already completed by a previous attempt" and report COPIED/MOVED (message
     notes the source vanished), setting `bytes_transferred` from the local file
     size. Guard: only when `attempt > 1`.
  3. Narrow the blanket except to expected types:
     `(OSError, socket.timeout, paramiko.SSHException, EOFError, RuntimeError)`.
     `RuntimeError` is kept because our own size/mtime/checksum validations raise
     it (`engine.py:199,202,208,224`) — those genuinely need retry. Exit-code
     contract untouched.
  4. Optional (included, cheap): `journal.record` in the success branch happens
     before `sftp.remove` when `delete_source` is set, so a journal blip cannot
     misorder the destructive step with the audit record.
- **Regression guard.** `test_delete_source_survives_journal_failure` → XPASS →
  un-xfail. New: retry-loop test for `stat → FileNotFoundError` on attempt ≥ 2.
- **Verification.** Verified by running: both repro scenarios
  (`repro_5_delete_source_misreport.py` 5a/5b) flip to PASS; pytest adds nothing
  hanging.

#### G-P1.6 Fix F6: overwrite policy only evaluated at planning time (`planner.py:23-31`, `engine.py:227`)

- **Root cause.** `os.replace` re-checks nothing; a file created between planning
  and execution is silently overwritten under the default skip policy.
- **Proposed fix.** In `transfer_one`, immediately before `os.replace`:
  - `SKIP` (file was absent at planning): if `local_path.exists()` now → do not
    transfer; result `SKIPPED`, reason `"destination created after planning"`;
    remove the `.part` file.
  - `ASK` resolved to "transfer": the operator already approved the overwrite of
    a path that existed at plan time — keep the approved overwrite (fail-safe
    default would re-introduce a second prompt impossible in a worker thread).
  - `REPLACE` (`reason="replace destination"`): overwrite is the point; no
    re-check needed.
  - `RENAME` (`reason="renamed destination"`): the plan-time TOCTOU — recompute
    `unique_destination()` immediately before the transfer by re-deriving the
    base path (add a `renamed_base` marker to the item reason handling, or
    recompute when local exists and action is transfer under RENAME).
- **Docs.** README skip-policy sentence gains "re-checked immediately before each
  overwrite decision" only if wording currently promises otherwise (check at
  execution time; README:22 default-skip promise already matches the fix).
- **Regression guard.** `test_skip_policy_rechecked_at_transfer_time` → XPASS →
  un-xfail. New: rename-TOCTOU unit test.
- **Verification.** Verified by running (`repro_6_skip_overwrite_race.py` flips).

**Commit group:** one commit per finding.

---

### G-P2 — P2 findings + adopted new finding N1 (findings 7, 8, 9, 10, 11, N1)

#### G-P2.7 Fix F7: `remote_walk` trusts server entry names (`discovery.py:18-42`, `paths.py`)

- **Proposed fix (all three sub-claims + `is_within` weakness).**
  1. Skip entries whose `entry.filename` is `.` or `..` (with a warning log).
  2. After building `path = directory / entry.filename`, verify confinement
     against a **strong** check (post-F7 the check is aware of literal
     components): reject unless every part of `path` relative to the current
     `directory` is a normal name, i.e. `..` never appears..walk recursion sites
     (`discovery.py:36`) and yields are both guarded.
  3. `paths.is_within` remains as-is for API compatibility (audit notes its
     weakness is part of F7), but discovery no longer relies on it for
     server-supplied names; `local_for_remote` additionally refuses a remote
     path whose relative part contains `..` (`RemotePathError`) — the xfail test
     accepts either outcome.
  4. Sparse attrs: `size = int(lstat_info.st_size or 0)`,
     `mtime = int(lstat_info.st_mtime or 0)` at `discovery.py:40-41`.
- **Regression guards.** `test_walk_confines_hostile_dotdot_entry` and
  `test_walk_handles_hostile_dot_entry` → XPASS → un-xfail. New:
  sparse-attrs TypeError sub-claim unit test (also closes review Correction #3's
  coverage gap).
- **Verification.** Verified by running (pytest) + reading.

#### G-P2.8 Fix F8: transfer-time confinement not re enforced (`engine.py:143,186`)

- **Proposed fix.** In `transfer_one`, before `sftp.stat`:
  ```python
  lstat_info = sftp.lstat(str(remote.path))
  if stat-S_ISLNK(lstat_info.st_mode) or not stat.S_ISREG(lstat_info.st_mode):
      # permanent, not a retryable condition
      return skipped/refused result with clear message
  ```
  Mirror discovery's guard (`discovery.py:28,:77`). Also re-`lstat` right before
  `sftp.remove` to narrow Finding-2-of-§4's delete verification window (comment
  noting residual race is inherent).
- **Regression guard (new).** Unit test: fake sftp whose stated regular file
  turns into a symlink — transfer must not open it and must not delete source.
- **Verification.** Verified by running (unit) + reading (stat/open clearing
  semantics in paramiko `sftp_client.py`).

#### G-P2.9 Fix F9: uncaught exception classes break the exit-code contract (`cli.py`)

- **Proposed fix.**
  1. `build_config`: validate `--port` (`1 <= args.port <= 65535`) → `ValueError`
     (naturally maps to exit 2).
  2. `main`: extend the except tuple with `EOFError` (prompt/input closed →
     concise config-error → return 2) and `sqlite3.Error` (journal failures →
     return 2). OverflowError is pre-empted by the new port validation, but also
     include it in the tuple defensively.
  3. `ask_yes_no`/`resolve_ask_actions`: `EOFError` propagates and hits 2 —
     acceptable (config error semantics).
- **Docs.** README:24 exit-code table unchanged (memory: the fix restores the
  documented contract; no README change needed). Guide's exit-code section
  updated if it currently lists 0/1/2/130 — it already matches.
- **Regression guards.** `test_main_eoferror_returns_config_error` and
  `test_main_invalid_port_returns_config_error` → XPASS → un-xfail.
- **Verification.** Verified by running (both scenarios via real `main()`).

#### G-P2.10 Fix F10: `--pattern` silently drops directory components (`discovery.py:51-56`)

- **Decision needed — default shown.**
  - **Default (recommended): path-aware matching** — match the pattern against
    the path relative to the root, using `fnmatch` on
    `remote_file.path.relative_to(root).as_posix()` (so `sub/*.mp4` matches
    under `sub/` only), with the previous basename behavior kept for patterns
    that contain no `/`. Guide's basename-only note updated; README `--pattern`
    description gains the path-aware rule and gives the example.
  - Alternative (also acceptable per test): reject patterns containing `/` with
    exit 2 and document basename-only in README.
- **Regression guard.** `test_pattern_directory_component_is_not_dropped` accepts
  both → XPASS → un-xfail under either variant.
- **Verification.** Verified by running (`repro_10_pattern_dir_drop.py` flips).

#### G-P2.11 Fix F11: no non-interactive credential path; no-tty fallback can echo (`ssh.py:98-103`)

- **Decision needed — default shown.** **Recommended: full option (a)+(b).**
  - (a) `SFTP_ULTRA_PASSWORD` env var support first, then getpass; documented as
    for CI/unattended only.
  - (b) When stdin has no tty and no env var: fail with exit 2 (plain message)
    instead of getpass's echoing fallback.
  - No `--password-file` — it invites 0600-tempfile mistakes; the env-var path
    covers the scripting workaround the audit identified. Say so at approval if
    `--password-file` (re StrictMode) is preferred instead.
- **Docs.** README "password prompt" and unattended-use sections updated to
  describe the env variable and the no-tty failure; `--help` unchanged (no new
  flag); guide's credentials section updated.
- **Verification.** Verified by running (non-tty path via `main([…])` under
  patched getpass; echo-warning no longer appears).

#### G-P2.N1 Fix review-finding N1: `SSHCClient` leak on `open_sftp()` failure (`ssh.py:68-71`)

- **Proposed fix.**
  ```python
  ssh = self._new_ssh()
  try:
      sftp = ssh.open_sftp()
  except Exception:
      try: ssh.close()
      except Exception: pass
      raise
  ```
  Bounded leak (one fresh client per retry attempt) becomes zero.
- **Regression guard (new).** Probe as unit test: `os_sftp = MagicMock` patched to
  raise `OSError`, spy on `SSHClient.close` — must be called once.
- **Verification.** Verified by running (unit).

**Commit group:** one commit per finding/fix sub-item.

---

### G-P3 — Low/optional items (findings 12, 13 partially)

#### G-P3.12 Fix F12: journal nits (`journal.py`, `engine.py:283-303`)

- **Proposed scope (minimal subset only):**
  - (a) Keep one long-lived connection guarded by `self._lock`, with explicit
    `close()`; removes per-record FD churn.
  - (b) Dry-run rows: add a `run_kind` column (`dry_run` vs `real`) or a
    `--journal-off` for dry runs; the least invasive fix is: dry-run journaling
    is disabled by default and README:17 updated. Say so at approval if the
    column approach is preferred.
  - (c) **Deferred**: `--resume` reading failed journal entries (see §4).
- **Regression guard.** Unit test (dry-run does not overwrite a prior real
  status) + long-lived-connection test (single `connect` call per `record`
  batch).
- **Verification.** Verified by reading + running (pytest).

#### G-P3.13 Fix F13: informational packaging/hygiene notes — **deferred by default**

All of §4.13 (pyproject metadata, upper-bound guard, CI workflow, `posixpath`
normpath, dead code removal, unused imports, atomic/manifest-order writes,
default `--remote-root`, banner/verb messages) is **deferred** (see §4). None
corrects user-defect behavior; bundling them here would bloat reviewable scope.

**Commit group:** one commit for (a); one for (b) + README:17 wording.

---

## 3. Ordering, commits, and verification discipline

- **Order:** G-P0 → G-P1 → G-P2 → G-P3, and inside each group, findings in
  ascending finding-number order (matches the audit's fix-priority table).
- **Commits:** one commit per finding (or review-finding), message form:
  `fix(engine): resolving audit F5 -- retry after completed transfers (AUDIT-FINDINGS #5)`
  plus docs/guide moves inside the same commit when user-visible.
- **Tests run after every fix:** `python -m pytest -q` under both paramiko
  5.1.0 and 3.4.1 venvs (paths `/tmp/opencode/sftp-audit-venv` and
  `/tmp/opencode/paramiko34-venv`). Only proceed when the suite is green
  (0 xfails strict, 0 failure) — the xfail conversion happens within the
  fix's commit.
- **No test weakening:** never satisfying a suite by loosening assertions,
  removing strict-money-style validations, altering exit-code expectations, or
  skipping tests. If a fix cannot be tested, say so and propose separately.
- **Verification labels required in the remediation report:** every assertion is
  tagged `verified by running:` (command + exact outcome) or
  `verified by reading:` (file/line/semantics trace). Nothing untagged.

## 4. Deferred by default (not in the approved scope)

1. `--io-timeout` flag (only if channel read timeout ever needs to differ from
   `--timeout`).
2. `--password-file` option (if env-var path is not enough).
3. `--resume` consulting failed journal entries (F12(c) / README's "resumable …
   and auditable" pairing nuance).
4. `run_id`-based journal schema (row-history, run column, append-only audit
   log).
5. Iterative (non-recursive) `remote_walk` for >1000-deep trees.
6. Run-level destination lock (concurrent sftp-ultra runs corruption risk).
7. `run_all.sh` + bandit/ruff being captured by a CI workflow; pip-audit in CI.
8. All informational items in F13 (§4.13 a–m) except explicitly included parts
   of G-P3.
9. pyproject upper-bound guard / license/readme/authors metadata additions.

Each deferred item can be pulled into Phase 2 by naming it at approval.

---

## 5. Approval gate (stop here)

Phase 2 executes only the approved groups. Approve by group:

- **"approve P0"** = G-P0 (the two silent-hang defects; highest user impact)
- **"approve P1"** = G-P1 (host key TOFU, auth claim, delete-source journal
  integrity, skip-policy race)
- **"approve P2"** = G-P2 (defensive walk, transfer-time lstat guard, exit-code
  contract, pattern semantics, credentials, N1)
- **"approve P3"** = G-P3 (journal connection/dry-run fidelity minimal subset)
- **"approve all"** = everything
- or any subset, e.g. "approve P0 and P1's F4" — scope can be narrowed further
  by naming the specific finding.

If you prefer the F4 README-correction variant over enforcement, or
`--password-file` over the env var, say so with your approval.

**Nothing below this line happens until you reply with an approval.**

---

**Approval (2026-10-28):** all groups (P0–P3) approved; F4 = enforce variant; F10 = path-aware variant. Phase 2 executed; outcomes in `docs/sftp-ultra-main-AUDIT-REMEDIATION.md`. No commits (checkout is not a git repository).

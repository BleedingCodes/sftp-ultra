# sftp-ultra — Independent Review of AUDIT-FINDINGS.md

> **Status (2026-10-28):** the remediation executed after this review is recorded in `docs/AUDIT-REMEDIATION.md` (approved scope: all groups; F4 = enforce variant; F10 = path-aware variant; 53 tests passing on paramiko 5.1.0 and 3.4.1). This review is an untouched record of the v1.0.1 audit state.

- **Review date:** 2026-10-28
- **Reviewer role:** independent skeptic review of the completed audit; the audit report was **not** assumed correct and every finding was re-traced or re-executed.
- **Repository:** `sftp-ultra-main` (sftp-ultra v1.0.1)
- **Repository path reviewed:** `/mnt/Side/SidePyEngine/BleedingCodes_Repo_Sessions/Live/sftp-ultra-main`
- **Audit report reviewed:** `docs/AUDIT-FINDINGS.md` (2026-10-28, v1.0.1 audit with §8 addendum)
- **This report:** `docs/AUDIT-REVIEW.md`

---

## 1. Review scope (exact)

**Independently read, in full:** all 9 modules under `sftp-ultra/src/sftp_ultra/` (`cli.py`, `engine.py`, `discovery.py`, `planner.py`, `journal.py`, `ssh.py`, `paths.py`, `model.py`, `__init__.py`); all 5 test files under `sftp-ultra/tests/` (including the audit's addendum `test_audit_findings.py`); `pyproject.toml`, repo-root `README.md`, `sftp-ultra-modification-guide.md`, `.gitignore`, `LICENSE`; the complete audit report and the full reproduction bundle (`docs/sftp-ultra-main-audit-repro/`: `common.py`, `run_all.sh`, README, all 8 repro scripts).

**Independently executed:** the project test suite under both paramiko 5.1.0 and 3.4.1; all 8 repro scripts (7 on paramiko 5.1.0, 2 spot-checked on 3.4.1); `ruff check src tests` and `bandit -r src`; three additional ad-hoc probes written for this review (sparse-attr `TypeError`, `is_within`/literal-`..` behavior, SSHClient leak on `open_sftp()` failure) — probes written under `/tmp/opencode` and outside the repository.

**Independently read in dependency source:** paramiko 5.1.0 and 3.4.1 installed copies (`client.py::_auth`, `_verification` policy persistence path, `connect()` defaults, `channel.py::Channel.timeout`, `transport.py` active-check timeout semantics).

**Not done (limitations):** no external network or real SFTP servers; no Windows; no dependence/semgrep/pip-audit; the audit's historical claim that pre-existing files are "byte-identical to the audited v1.0.1 state" could not be cryptographically confirmed (no git history in this checkout) — it is consistent with everything observed but not proven.

---

## 2. Overall verdict

## **Supported with corrections**

All 13 findings in the audit are substantively accurate. **12 verified (10 with reproduction I ran myself), 1 partially verified (Finding 8 — code-level only, matching the audit's own labeling). No finding was found incorrect.** The corrections below are limited to small wording/coverage issues, environment-version drift, one residual-risk item the audit understated (in the conservative direction), and two material omissions the audit missed (one new Low defect, one new informational defect in the journal). Severity ratings and fix priorities are reasonable as assigned; none needed changing.

---

## 3. Verification of audit findings

| # | Finding (severity) | Status | Reviewer confidence | Basis for the status |
|---|---|---|---|---|
| 1 | Bandwidth limit < 1024 KiB/s deadlocks (High) | **Verified** | **High** | Re-traced `TokenBucket` (`engine.py:24-52`): tokens capped at `min(float(rate), tokens + elapsed*rate)` (lines 40-43), so `consume(amount > rate)` can never satisfy `tokens >= amount`. Ran `repro_1_bandwidth_deadlock.py`: exit 0, REPRODUCED — limits 1/512/1023 KiB hang on a 1 MiB chunk; 1024 returns. `cli.py:105-106` validates only `> 0`, confirmed. Full-run hang remains an inference, but the shared-bucket mechanism (`run_plan` passes one bucket to every worker, `engine.py:281-319`) makes it sound. |
| 2 | No SFTP data-read timeout — stalled read wedges run forever (High) | **Verified** | **High** | Grep confirms no `settimeout`/`get_channel` anywhere in `src/`. Verified in installed paramiko 5.1.0: `channel.py:114` → `self.timeout = None`; `transport.py:349` (`_active_check_timeout = 0.1`) and `:485` (`self.sock.settimeout(...)`) with the comment "socket.timeout exception is never propagated" — the audit's paramiko citations are exact. Ran `repro_2_stall_hang.py` (live paramiko SFTP server on 127.0.0.1, real project pipeline, `timeout_seconds=3`): exit 0, REPRODUCED — `run_plan` still blocked after 25 s; discovery/planning had succeeded. |
| 3 | `--trust-unknown-host` never pins the host key (Medium) | **Verified** | **High** | Ran `repro_3_hostkey_no_pinning.py`: exit 0, REPRODUCED — key accepted in memory, never persisted. Verified in paramiko 5.1.0 source: `load_system_host_keys(filename=None)` leaves `_host_keys_filename` unset; `AutoAddPolicy.missing_host_key` calls `save_host_keys` only `if client._host_keys_filename is not None`; changed keys for known hosts still raise `BadHostKeyException` (client.py `our_key != server_key` path) — the audit's "mitigating nuance" is also real. |
| 4 | README auth-precedence claim false — agent/`~/.ssh` keys tried before password (Medium) | **Verified** | **High** (code-level, per audit's own label) | Read both installed paramiko versions. 5.1.0: `connect()` defaults `allow_agent=True, look_for_keys=True` (client.py:225-226); `_auth` (client.py:640-769) tries passed keys → agent keys (`:705`) → `~/.ssh/id_{rsa,ecdsa,ed25519}` (`:744+`, gated by `look_for_keys` at `:744-745`) → password last (`:764`); the passphrase quirk (`passphrase = password`) is at `:666-667`. 3.4.1: `_auth` at client.py:652 has the identical order (agent at `_auth+94`, password last). Note: this *improves on* the audit's Residual-Risk item 6, which said the 3.4.1 auth path "was not re-read line-by-line" — it was, and it matches. Not live-exercised (would need an agent/keyed server), consistent with the audit's honesty. README:45's quote is accurate. |
| 5 | Retry loop re-executes completed transfers; `--delete-source` success misreported as `failed` (Medium) | **Verified** | **High** | Confirmed the code span (`engine.py:184-271`: `os.replace` at `:227`, `sftp.remove` at `:231`, `journal.record` at `:245-249` inside the same `try`, blanket `except Exception` at `:252`). Ran `repro_5_delete_source_misreport.py`: exit 0, REPRODUCED — 5a `failed` while local file exists and remote was removed, journal records `failed`; 5b `sftp.open()` called twice (full re-download), `attempts=2`, final `copied`. Re-ran on paramiko 3.4.1: also REPRODUCED. No data loss claim matches (local file intact). |
| 6 | Overwrite policy checked only at planning time — `skip` overwrites files created after planning (Medium) | **Verified** | **High** | `planner.py:23` (`exists()` once), `engine.py:227` (`os.replace`, no re-check) confirmed. Ran `repro_6_skip_overwrite_race.py`: exit 0, REPRODUCED — post-planning user file replaced with downloaded zero-bytes under default `skip`. The `--overwrite ask` prompt / earlier-transfer window is real (`cli.py:165-176` before `run_plan`). RENAME TOCTOU correctly noted in the fix paragraph. |
| 7 | `remote_walk` trusts server entry names — `..` escapes root; `.` → `RecursionError`; sparse attrs → `TypeError` (Medium) | **Verified** (with a bundle-coverage note) | **High** | Ran `repro_7_hostile_walk.py`: exit 0, REPRODUCED — `..` entry yields `/home/side/../other/secret.txt` and maps to `/data/downloads/../other/secret.txt` (outside destination); `.` entry → `RecursionError`; both on paramiko 5.1.0 (and the mock logic re-derived independently). Also directly verified the two sub-claims the bundle does *not* cover: (a) a `is_within()` call with `PurePosixPath('/home/side/../other/x.txt')` returns **True** (literal `..` passes confinement), and (b) an lstat with `st_mtime=None` raises exactly `TypeError: int() argument must be … not 'NoneType'` at `discovery.py:41`, uncaught in `cli.py` — so all three sub-claims stand, but see Corrections #3. |
| 8 | Confinement not re-enforced at transfer time — `sftp.stat`/`sftp.open` follow symlinks (Medium) | **Partially verified** | **Medium** | Code path confirmed: discovery rejects symlinks via lstat (`discovery.py:28,:77`), while `transfer_one` uses plain `sftp.stat` (`engine.py:186`) and `sftp.open` (`engine.py:143`) between which a large window exists (interactive prompts `cli.py:165-209`, earlier transfers). The SFTP "STAT"/open-follow-symlinks semantics are standard protocol behavior, not reproduced here (would need a live server with a mid-transfer race). This matches exactly the audit's own "code analysis, no live repro" label; the potential-impact wording is appropriately hedged. |
| 9 | Uncaught exception classes break the exit-code contract (Low) | **Verified** | **High** | Ran the real `cli.main()` with closed stdin: `GetPassWarning`, `Warning: Password input may be echoed.`, then uncaught `EOFError` traceback from `cli.py:146` → exit code **1** — reproduced. Ran `repro_9_cli_contract.py`: exit 0, REPRODUCED — `ask_yes_no` EOFError and `--port 70000` `OverflowError` both escape the `except (ValueError, OSError, paramiko.SSHException)` tuple (`cli.py:228-234`); no `--port` range validation exists in `build_config`. `sqlite3.Error` and Finding 7's `RecursionError`/`TypeError` escape is code-confirmed (not in the except tuple). README:24 quote accurate. |
| 10 | `--pattern` silently drops directory components (Low) | **Verified** | **High** | `discovery.py:51` (`PurePosixPath(p).name`) confirmed. Ran `repro_10_pattern_dir_drop.py`: exit 0, REPRODUCED — `sub/*.mp4` matched `/home/side/keep.mp4`. Modified-guide-only documentation confirmed (guide lines 193/195; absent from README:130 and `--help`). |
| 11 | No non-interactive credential path; prompt unconditional; no-tty fallback can echo (Low) | **Verified** | **High** | My direct run (above) reproduced the `GetPassWarning` + "Password input may be echoed" fallback warning. `prompt_credentials` called unconditionally at `cli.py:146` (even with `--yes`/`--dry-run`) confirmed; grep confirms zero `os.environ`/`getenv` usage in `src/` — no env-var or key-file path exists. README:36/45 quotes accurate. |
| 12 | Journal nits: connections never closed; dry-run overwrites prior statuses; journal never read (Low) | **Verified** (code-level) | **Medium-High** | `journal.py`: `record()` uses `with self._lock, self._connect() as connection:` (`:43`) — sqlite3 context manager commits but never closes; `__init__` same (`:30-34`). `ON CONFLICT(...) DO UPDATE` confirmed → dry-run `planned`/`skipped` rows overwrite prior real `copied`/`moved` statuses. `grep SELECT src/` returns nothing — the journal is write-only; `--resume` operates purely on `.part`/`.meta`. Audit's caveat that README:17 does disclose dry-run journaling is fair. |
| 13 | Packaging/portability/hygiene notes (Informational) | **Verified** | **High** | Each sub-claim checked: `pyproject.toml` has no `readme`/`authors`/`license` fields and `paramiko>=3.4` with no upper bound (supports `>=3.4` flex — I ran 5.1.0 successfully, matching the audit). No CI workflows, scripts, or lockfiles exist (full file listing: 31 files, none of those kinds). `paths.py:16` uses `os.path.normpath` on remote POSIX paths — confirmed. `close_all_for_this_thread` (`ssh.py:88-95`) has no caller in `src/` — confirmed. Manifest written before user confirmation (`cli.py:177-195` before prompts at `:201-209`) and both files written with non-atomic `write_text` — confirmed. Ruff/bandit results in §6 below corroborate all cited rules, including exact unused-import locations (`cli.py:7`, `engine.py:12`, `journal.py:6`, `model.py:3`, `planner.py:3`). |

**Residual-risk items (§4 of the audit):** items 1-5 and 7-8 are logic-verified risks that I re-checked and found soundly described (not overclaimed); item 6 is *understated* — see Correction #4. Item 8 (no unit tests for `ssh.py`/`cli.py`) is confirmed by the file listing and README:193.

---

## 4. Corrections and clarifications

These do not change the verdict on any finding; they are precision fixes the audit author should apply.

1. **Tool-version drift (§6 of the audit).** The environment I re-ran in shows `pytest 9.2.0` (audit says 9.1.1) and ruff count **32** findings (audit says 31; ruff 0.17.3 adds one `RUF059` in `tests/`). Counts in the cited *category* buckets match exactly (F401 ×5 in src, I001, BLE001 ×3, S110 ×2; bandit B507 High/Medium at `ssh.py:37`, B311 at `engine.py:255`, B110 ×2 at `ssh.py:66,82`). Recommend recording exact tool versions rather than result counts, or re-pinning.
2. **F3 line citation.** The `changed-key → BadHostKeyException` mechanism sits in paramiko 5.1.0's `connect()` verification block (client.py, the `our_key != server_key` branch following `:448`) — the audit's ":455-460" pointer is current for 5.1.0 but shifts between paramiko majors; recommend citing semantics rather than line numbers for the dependency (same applies to `client.py:640-760` in F4). Anchors verified for 5.1.0; identical behavior confirmed for 3.4.1 by content, not line numbers.
3. **F7 evidence mixing.** The summary table says F7 is "Confirmed — reproduced", but the repro bundle covers only the `..` and `.` sub-claims; the `st_mtime=None → TypeError` sub-claim appears in **no** repro script and **no** test. The sub-claim is correct (I reproduced it with a 20-line probe) but the report should say "two of three sub-claims reproduced; TypeError sub-claim code-confirmed" so the bundle's coverage table stays honest.
4. **Residual-Risk item 6 is too cautious — and can be strengthened.** The audit says the 3.4.1 auth-order path "was not re-read line-by-line for 3.4.1". I re-read it: paramiko 3.4.1 `client.py:652` `_auth` has the same order (agent keys before password, `passphrase = password` quirk present), so Finding 4 carries to both versions with *more* confidence than the audit stated.
5. **`run_all.sh` was not executed as a whole** in this review (individual scripts were; all passed). If the audit author wants "bundle verified green under run_all.sh" claimed anywhere, they should note that explicitly.
6. **Addendum absoluteness.** §8 claims pre-existing files are "byte-identical to the audited v1.0.1 state". This checkout has no git history, so that claim is unverifiable here; it is consistent with the observed source (no defect fixed in any module — grep and read-verification support it) but it rests on the author's word and should be labeled as such.

---

## 5. New findings (not in the original audit)

### N1 [Low / P3 — **new**] `SFTPConnectionFactory.open()` leaks the SSHClient when `open_sftp()` fails
**File:** `sftp-ultra/src/sftp_ultra/ssh.py:68-71`
**Evidence (verified by probe).** In `open()`, a newly created client is cached only *after* `open_sftp()` returns successfully:

```python
ssh = self._new_ssh()          # :68
sftp = ssh.open_sftp()         # :69  <- raises → skip to :73's try/except
self._thread_local.ssh = ssh   # :70  never reached
```

The `try/except` (`:73-86`) that closes poisoned connections only wraps the **yield**, so when `open_sftp()` raises, the fresh `SSHClient` (live TCP socket + paramiko transport thread) is neither cached nor closed. My probe (spy on `SSHClient.close`, `open_sftp` patched to raise `OSError`) recorded **`close()` calls after open_sftp failure: 0**. `transfer_one` calls `factory.open()` once per retry attempt (`engine.py:185`), and the blanket `except Exception` retry loop means a transient SFTP-subsystem failure leaks one fresh connection per attempt (bounded by `retries + 1` per file; connections linger until GC/interpreter exit). Impact: bounded resource/thread leak, never a correctness failure — hence Low.
**Suggested fix:** wrap `open_sftp()` (`ssh.py:69`) in `try/except` and call `ssh.close()` before re-raising.

### N2 [Informational — **new**] Journal records planning-time size/mtime, not the actually transferred file's stat
**File:** `sftp-ultra/src/sftp_ultra/engine.py:245-249` (contrast `:193-194,:196,:236-249`)
**Evidence (code-verified, not reproduced).** The success-path `journal.record()` is called with `remote_size=remote.size, remote_mtime=remote.mtime` — the values captured at **discovery** time (`RemoteFile` from `remote_walk`), while the transfer itself validated and recorded `before`/`after` stats (`engine.py:186,196`) and stores the true transferred size only in `result.bytes_transferred=int(after.st_size)`. If the remote file changes between discovery and transfer (while still passing the before==after guard), the journal's `remote_size`/`remote_mtime` columns describe a file that never existed at transfer time. For a journal whose stated purpose is auditability (README:16-17), this is a data-fidelity nit.
**Suggested fix:** record `remote_size=int(after.st_size)` and the transfer-time `before.st_mtime` (or `after.st_mtime`) instead of the PlanItem's discovery values.

These two items are Level-2 nits; they do not alter the audit's overall conclusions, and Listing them here satisfies the "check for missed issues" duty — beyond these, my bounded independent scan (bandwidth/token bucket, discovery/paths/planner/journal/cli logic, retry/resume/checksum interplay, manifest/report writes, flags, error handling, secrets (`grep` matched only prose occurrences of the word "password" — no credentials, keys, or private-key blocks exist in the repository) found nothing else material.

---

## 6. Coverage assessment

The audit's coverage claim ("full manual read of all source, tests, packaging, and documentation") matches what I found: it accurately describes every module, all line citations I checked (≈40 of them) pointed at the claimed code, and it correctly identified the untested seams (`ssh.py`, `cli.py`, the retry/journal interplay, the token bucket, live-read timeouts) rather than over-claiming test-derived evidence. Both "High" findings use the strongest available evidence (one unit-reproduced with a boundary sweep, one live-localhost end-to-end), and the two security findings were verified against dependency source rather than asserted. The addendum's xfail test file maps one-to-one onto the claimed finding list (10 tests; I confirmed the mapping in the test source and got "34 passed, 10 xfailed" on both paramiko versions).

Gaps that remain (also acknowledged by the audit): real-server behavior (OpenSSH quirks, non-UTF-8 filenames), Windows, F4/F8 not live-exercised, no dependency-CVE scan, no semgrep. These are outside what an audit *without external authorization* can do and are disclosed in §1/§4 of the audit — credible.

---

## 7. Commands run and exact outcomes

Environment: Python 3.14.7, Linux. Venvs pre-existing at `/tmp/opencode/sftp-audit-venv` (paramiko 5.1.0, pytest 9.2.0, ruff 0.17.3, bandit) and `/tmp/opencode/paramiko34-venv` (paramiko 3.4.1) — same venvs the audit reports using.

| # | Command | Outcome |
|---|---|---|
| 1 | `python -m pytest -q` in `sftp-ultra/` (paramiko 5.1.0 venv) | **34 passed, 10 xfailed** in 8.41 s, exit 0 — matches audit rows 1/12 |
| 2 | same with paramiko 3.4.1 venv | **34 passed, 10 xfailed** in 9.24 s, exit 0 — matches audit rows 2/13 |
| 3 | `python repro_1_bandwidth_deadlock.py` | exit 0, `RESULT: REPRODUCED` — 1/512/1023 KiB hang; 1024 returns |
| 4 | `python repro_2_stall_hang.py` (live 127.0.0.1 SFTP server) | exit 0, `RESULT: REPRODUCED` — discovery+plan OK, `run_plan` hung at 25 s despite `--timeout 3` |
| 5 | `python repro_3_hostkey_no_pinning.py` | exit 0, `RESULT: REPRODUCED` — accepted in memory, never persisted |
| 6 | `python repro_5_delete_source_misreport.py` | exit 0, `RESULT: REPRODUCED` — 5a `failed`+deleted+local-present; 5b `open()` ×2 |
| 7 | `python repro_6_skip_overwrite_race.py` | exit 0, `RESULT: REPRODUCED` — post-planning user file overwritten |
| 8 | `python repro_7_hostile_walk.py` | exit 0, `RESULT: REPRODUCED` — `..` escapes root/destination; `.` → `RecursionError` |
| 9 | `python repro_9_cli_contract.py` | exit 0, `RESULT: REPRODUCED` — EOFError + OverflowError escape `main()` |
| 10 | `python repro_10_pattern_dir_drop.py` | exit 0, `RESULT: REPRODUCED` — `sub/*.mp4` matched outside `sub/` |
| 11 | rerun of rows 6 and 8 under paramiko 3.4.1 | both exit 0, `RESULT: REPRODUCED` |
| 12 | direct `cli.main()` with stdin `< /dev/null` | `GetPassWarning` + `Warning: Password input may be echoed.` + uncaught `EOFError` traceback at `cli.py:146` → **exit 1** (documented contract: exit 2) |
| 13 | `python -m ruff check src tests` | 32 findings (src: F401×5, I001×3, BLE001×3, SIM102×2, S110×2, UP035×5; tests: F401×6, RUF059×3, I001×2, …) — category-level match with audit row 10 |
| 14 | `python -m bandit -r src` | 1 High (B507 `ssh.py:37`, Medium conf), 3 Low (B311 `engine.py:255`; B110 `ssh.py:66,82`) — exact match with audit row 11 and F3/F13 |
| 15 | ad-hoc probe: `remote_walk` with `st_mtime=None` lstat | raises `TypeError: int() argument must be … not 'NoneType'` — F7's third sub-claim |
| 16 | ad-hoc probe: `is_within('/home/side/../other/x.txt', '/home/side')` | returns `True` (literal `..` passes confinement) — F7's `is_within` weakness |
| 17 | ad-hoc probe: `factory.open()` with `open_sftp` raising | `close()` calls: **0**; client neither cached nor closed — new finding N1 |
| 18 | paramiko source reads (5.1.0 and 3.4.1): `_auth`, `connect()` defaults, AutoAddPolicy persistence, `Channel.timeout`, transport `0.1` active-check | all F2/F3/F4 paramiko citations confirmed with identical behavior in both versions |

Cleanup note: my test/repro runs transiently created `__pycache__/`, `.pytest_cache/`, and `.ruff_cache/` artifacts inside the repository; all were removed after verification, leaving the tree exactly as found (31 files, all accounted for).

---

## 8. What the audit author should change

1. Update tool/version specifics: pytest 9.2.0 (not 9.1.1) and ruff 32 findings (not 31) as of the re-check environment — or state versions instead of counts.
2. In Finding 7 (§2 table and §3), clarify that the sparse-attrs `TypeError` sub-claim is **code-confirmed only** (I did verify it independently and it is true); optionally add it to `repro_7_hostile_walk.py` and the xfail suite so bundle coverage is complete.
3. Strengthen Residual-Risk item 6: the 3.4.1 `_auth` path **was** independently re-read in this review and is confirmed identical in order (agent → `~/.ssh` keys → password, `passphrase = password` quirk present); Finding 4 can carry that qualifier at High confidence for both audited paramiko versions.
4. Where feasible, prefer semantic anchors over paramiko line numbers (F3/F4) for future-compat.
5. Consider adopting the two new findings above (N1 connection leak — Low/P3; N2 journal stat fidelity — Informational) into AUDIT-FINDINGS.md, or at least the N1 fix alongside Finding 8's remediation (both are small, localized changes in their respective seams).
6. No finding requires a severity or priority change. No recommendation in the audit was found to over-promise a fix.

**Bottom line:** the audit is accurate, appropriately conservative in its impact claims, and its evidence base (especially removing the live-localhost stall proof and the dual-version test runs) holds up completely under independent re-execution. The verdict is **Supported with corrections** for the minor documenting/coverage items above only.

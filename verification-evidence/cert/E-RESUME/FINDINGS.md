# E-RESUME — INDEPENDENT CERTIFICATION REVIEW (resume/recovery + scenario identity)

Recorded verbatim by the campaign controller from reviewer E-RESUME's return. The
reviewer could not write this file itself (subagent report-file creation is refused
by the harness); the harness scripts and raw JSON in this directory are the
reviewer's own artifacts and were written by it.

**Candidate:** `537ae0b` · **Overall: FAIL. Part 1: FAIL. Part 2: FAIL.**

## What was executed vs. inferred

| Harness | Real product path driven |
|---|---|
| `identity_probe.py` | `GeneratedScenario._identity`, `shorten_preserving_identity`, `sanitize_filename`; 3 600 adversarial ids; 4 subprocesses at differing `PYTHONHASHSEED` |
| `identity_execution.py` | real `SuiteExecutor.run` + real `ScenarioExecutor` (local `sh`, no services/network) + `build_suite` + `scenario_gate.evaluate_gate` |
| `resume_probe.py` | real `cli.run_control_loop` + real `ScenarioPlanner`/`EvidenceStore`/`SuiteExecutor`; process 1 ends via `os._exit(9)` at 5 interruption points; process 2 is a fresh interpreter resuming via real `cli._make_planner` → `restore_from_store` |
| `resume_probe2.py` | authorization-vs-clean scoping, wave-budget escape, approved-command narrowing, 40× real `SIGKILL` mid-persist, concurrent-reader window, promotion candidacy, re-persist after resume |
| `resume_probe3.py` | HEAD moved, evidence deleted, `proposed_id` across resume, post-sanitisation collision through the real planner, `RiskCategory`×`redact_obj` sweep |
| `wave_counter_check.py` | wave-counter fidelity across a second resume |

Only the two Claude sessions and the generator were faked (`cli.BuilderLike`/`EvaluatorLike` exist for this). **Not done:** no live-Claude `run_control_loop` run. `verification-evidence/remediation/resume_demo.py` was read but not used or relied on.

---

# PART 1 — RESUME / RECOVERY

### R1 — BLOCKING DEFECT

**Claim:** an `authorization`-category scenario corrupts `scenario-plan.json` on write, so the plan can never be re-read and resume loses the entire run state.

**Mechanism:** `EvidenceStore.write_json` (`evidence.py:108`) pipes payloads through `models.redact_obj` (`models.py:366-383`), which masks the **value** of any dict key matching `…|authorization`. `CoverageSummary.by_risk_category` is keyed by `RiskCategory` value, and `RiskCategory.AUTHORIZATION == "authorization"` → the int count is persisted as `"[REDACTED]"`. `GeneratedScenarioPlan.model_validate_json` raises `int_parsing`; `restore_from_store` (`scenario_planner.py:608-614`) swallows it and continues with a blank plan. Exactly 1 of 27 categories trips this — the one at the centre of this product's domain.

**Repro:** `.venv/bin/python verification-evidence/cert/E-RESUME/resume_probe2.py` → compare `A_baseline_clean` vs `A_baseline_authorization`.

**Expected:** plan round-trips; every scenario/wave/budget/failure/cluster/causal edge restored.

**Observed:** persisted `{"idempotency":1,"authorization":"[REDACTED]"}`; emission `could not restore the scenario plan (ValidationError); this run will generate a new one`; lost `plan.scenario_ids`, `proposed_ids`, `signatures`, `source_failures`, `source_clusters`, `observed_failure_ids`, `observed_cluster_ids`, `executed_scenario_ids`, `risks`, `waves`, `waves_used`, `compiled_ids`, `compiled_digest`. Clean control (`boundary` instead) loses **nothing**. Reproduces at all five interruption points in `resume_probe.py`.

### R2 — BLOCKING DEFECT

**Claim:** an unreadable plan is handled fail-open — the run restarts at wave 0, regains a spent wave allowance, and the next `persist()` destroys the surviving record (B6 reopened).

**Mechanism:** `restore_from_store` returning `""` is indistinguishable from "nothing to resume"; `run_control_loop` calls `plan_initial` unconditionally on resume (`cli.py:276`); `_generate` sees `_wave == 0` so `max_waves` does not fire; `_finish_wave` → `persist()` overwrites the file.

**Repro:** `.venv/bin/python verification-evidence/cert/E-RESUME/resume_probe2.py` → `B_wave_budget_clean` vs `B_wave_budget_authorization` (both `max_waves=1`); and `resume_probe.py` → `after_repersist_*`.

**Expected:** unreadable plan is fail-closed; the spent allowance still binds; the record is not overwritten.

**Observed:** clean → `waves_used` 1, second wave `refused: 1 generation wave(s) already used`. Authorization → `waves_used` **0**, a fresh wave 1 accepted, final plan `["gen-delta-boundary"]` with nothing in common with the two scenarios already decided; `scenario-plan.json` 7 769 B / 2 scenarios / 1 wave → 756 B / 0 / 0. Mitigation: `scenario-generation/wave-01.json` is not deleted. Clean path re-persist preserves everything (`G_repersist_after_clean_resume: record_destroyed=false`) — B6 is closed only while the plan parses.

### R3 — NONBLOCKING LIMITATION

**Claim:** `scenario-plan.json` is written non-atomically (`Path.write_text`), unlike `write_case_evidence` which stages+`replace()`s.

**Repro:** `resume_probe2.py` → `D_sigkill_mid_persist`, `F_partial_write_window`.

**Observed:** concurrent reader saw **99 unparseable states in 20 228 reads** (sizes 0 / 7 648 / full 377 138) — the window is real. **0 of 40 real SIGKILLs landed in it; I did not produce a half-written plan by SIGKILL and am not claiming I did.** The consequence is proven deterministically instead by `resume_probe.py` `hostile_truncated_plan` (3 884/7 769 B) and `hostile_empty_plan` (0 B), both giving the full R2 loss.

### R4 — NONBLOCKING LIMITATION

**Claim:** promotion candidacy (`DefectMemory`) is not persisted, so a scenario that failed pre-interrupt and passes post-interrupt never becomes a candidate.

**Repro:** `resume_probe2.py` → `E_promotion_across_resume` (clean plan, so R1 is not in play).

**Observed:** process 1 `gen-alpha-idempotency` fails → `[]` (correct). Process 2 resumes, it passes → `[]`, `defect_memory_survived: false`. Expected one candidate. Advisory output only; nothing falsely accepted.

### R5 — NONBLOCKING LIMITATION

**Claim:** resume does not notice deleted evidence.

**Repro:** `resume_probe3.py` → `I_evidence_deleted`.

**Observed:** three `result.json` deleted; emission is the ordinary `resumed scenario plan: 2 scenario(s), 1 wave(s) already used`; `executed_scenario_ids` still asserts all three with no evidence behind them. Not a false accept — the new process re-runs the full suite (`previous_suite=None`) and the gate re-establishes evidence.

### R6 — NONBLOCKING LIMITATION

**Claim:** a budget-refused wave inflates the restored wave counter. `_generate` appends `record.wave = _wave+1` without incrementing `_wave`; `restore_from_store` takes `max(w.wave)`.

**Repro:** `.venv/bin/python verification-evidence/cert/E-RESUME/wave_counter_check.py`

**Observed:** 1 wave used + 1 refused → second resume reports `waves_used: 2`. Conservative (tightens, never extends) — cannot escape `max_waves`.

### CLOSED (Part 1)

- **R7** approved-command narrowing (`resume_probe2.py` → `C_command_narrowed`): both scenarios fail `compile_to_scenario`, are dropped with full reasons, `compiled_ids: []`. Nothing comes back to life.
- **R8** HEAD moved (`resume_probe3.py` → `H_head_moved`): restored **and** flagged — `repository moved from 8ade7fd to 0139e70 … chosen against different code`.
- **R9** resume twice: `resume_twice_identical: true` in all ten `resume_probe.py` cases.
- **R10** `execution_budget_s`: per-suite-execution wall clock by design (re-armed every iteration anyway), nothing to carry across a boundary; enforcement verified (`budget_exhausted` case skipped every scenario with the stated reason).

### Per-state-element survival, fresh process, real `cli._make_planner`

| State element | Clean plan | Plan with an `authorization` scenario |
|---|---|---|
| generated plan (all scenarios, in full) | survives | **LOST** |
| compiled form of every scenario | survives (recompiled, digest-identical) | **LOST** |
| `proposed_id` for shortened ids | survives | LOST with the plan |
| wave counter / `max_waves` | survives; allowance not extended (inflated by refused waves, R6) | **LOST — reset to 0, full allowance regained** |
| per-wave & total scenario budgets | survive | **LOST** |
| `execution_budget_s` | n/a — per-execution by design | n/a |
| observed failures | survive | **LOST** |
| failure clusters | survive | **LOST** |
| causal links (`source_failures`/`source_clusters`) | survive | **LOST** |
| executed scenario ids | survive | **LOST** |
| per-case evidence on disk | survives, correctly attributed | survives; the plan referencing it does not |
| promotion candidates (ledger file) | survives | survives |
| promotion *candidacy* (`DefectMemory`) | **LOST (R4)** | LOST |

---

# PART 2 — SCENARIO IDENTITY

### I1 — BLOCKING DEFECT

**Claim:** two ids differing only in case share one evidence directory; both report verified evidence and the gate accepts.

**Mechanism:** `_identity`/`sanitize_filename` are case-preserving and `existing_ids` is case-sensitive, so both are admitted as distinct required scenarios; the evidence dir is `artifact_root/"scenarios"/sanitize_filename(id)` (`scenario_suite.py:577`) — one directory on a case-insensitive FS (macOS APFS default, confirmed on this machine; also Windows). `verify_case_evidence` runs immediately after each scenario, so the first verifies before the second overwrites.

**Repro:** `.venv/bin/python verification-evidence/cert/E-RESUME/identity_execution.py` → `case_only`, `case_only_asymmetric`.

**Expected:** two directories, each with its own correctly-attributed `result.json`.

**Observed (`case_only`, both pass):** both `PASSED`, both `evidence_verified: true`, two distinct-looking paths; exactly **one** `result.json` on disk, `scenario_id: "gen-case-probe"`; **`"gate": "VERIFIED"`**, `gate_problems: []`. **Observed (`case_only_asymmetric`):** `directories_on_disk: ["Gen-Clash"]`; `Gen-Clash` → `PASSED` + `evidence_verified: true`, while the only `result.json` there holds `"scenario_id": "gen-clash"` — the **failing** scenario.

### I2 — NONBLOCKING LIMITATION

**Claim:** ids colliding only after sanitisation collapse to one id (the digest is appended only when the sanitised string already exceeds the limit, and is taken over the sanitised value).

**Repro:** `identity_probe.py` → `sanitisation`; `resume_probe3.py` → `K_post_sanitisation_collision`.

**Observed:** `a/b`,`a\b`,`a:b`,`a b`,`a|b`,`a*b`,`a-b` → all `a-b`; `café-test`,`cafè-test`,`caf中-test` → all `caf--test`; 4 collision groups in 3 600 adversarial ids, all in the separator family. Through the real planner the loser is **visibly refused** — `scenario id 'gen-alpha' is already used in this run`, persisted in the wave record, `proposed_id` retained. Coverage lost, identity not silently merged, evidence never shared.

### I3 — NONBLOCKING LIMITATION

**Claim:** `.` and `..` are legal scenario ids and relocate the evidence directory out of the per-case tree (`sanitize_filename("..") == ".."`; no id-shape rule anywhere).

**Repro:** `identity_execution.py` → `dotdot`.

**Observed:** id `"."` wrote `result.json` into `iteration-NN/scenarios/`; id `".."` into `iteration-NN/`. Both `PASSED`, `evidence_verified: true`, gate `VERIFIED`. Bounded to one level (an id can never contain `/`); stays inside the run directory; no existing artifact was overwritten in the observed run.

### I4 — NONBLOCKING LIMITATION

**Claim:** `proposed_id` does not reach the per-case record, the outcome, or the suite result. Survives: plan JSON round-trip, on-disk `scenario-plan.json`, resume reload (`resume_probe3.py` → `J`, `survived: true`), promotion ledger via the embedded model. Absent from: `write_case_evidence` (`scenario_id` only), `ScenarioOutcome`, `suite-result.json`. Recoverable only by joining against the plan.

### CLOSED (Part 2)

- **I5** long shared prefixes: 60/64/68/80/200-char prefixes and ids differing only *after* the 64-char truncation point all yield distinct ids **and** distinct filenames; `max_id_len == 64`, `max_fs_len <= 80`.
- **I6** determinism: `PYTHONHASHSEED` 0/1/12345/random → byte-identical `(id, proposed_id, sanitize_filename, shorten_preserving_identity)`. No hash-seed sensitivity.
- **I7** re-validation is idempotent (no digest-on-digest drift).
- **I8** a refused scenario blocks acceptance: `ScenarioSuite.add` → `assembly_conflicts` → `SuiteResult.assembly_problems` → `evaluate_gate` returned `NOT_VERIFIED` quoting it (`duplicate_admission`).
- **I9** path safety otherwise holds: `../..`→`..-..`, `/etc/passwd`→`etc-passwd`, `~/.ssh/id_rsa`→`.ssh-id_rsa`, `a\x00b`→`a-b`, 300 chars → 64 with digest; empty and pure-separator ids raise `scenario id is empty after sanitisation` and never become scenarios.

---

## Bottom line

**FAIL** — three blocking defects, each reproducible from the harnesses in `verification-evidence/cert/E-RESUME/`:

1. **R1** one `authorization`-category scenario corrupts `scenario-plan.json` at write time, so the plan can never be re-read (and the on-disk coverage record is falsified even for a human reader).
2. **R2** an unreadable plan is fail-open: wave 0, a full fresh wave budget, and destruction of the surviving record.
3. **I1** ids differing only in case share one evidence directory; evidence is destroyed and misattributed while the gate says VERIFIED.

Part 1: **FAIL**. Part 2: **FAIL**. Everything else the two areas claim was checked and, within the stated limits, holds.

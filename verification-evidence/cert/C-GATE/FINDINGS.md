# C-GATE — INDEPENDENT CERTIFICATION REVIEW (acceptance gate)

Recorded verbatim by the campaign controller from reviewer C-GATE's return. The
reviewer could not write this file itself (subagent report-file creation is refused
by the harness); the harness scripts and raw JSON in this directory are the
reviewer's own artifacts and were written by it.

**Candidate:** `537ae0b` · **Interpreter:** `.venv/bin/python` (3.13.5) · **Attacks executed: 70**

**EXECUTED vs INFERRED.** All 70 attacks were executed against real product code (`scenario_gate.evaluate_gate`, `uncovered_required_risks`, `cli._apply_suite_precedence`, `cli.run_control_loop`, `SuiteExecutor`, `write_case_evidence`/`verify_case_evidence`, `build_suite`, `ScenarioPlanner`, and the real `scenarios run-generated` CLI). Only the three Claude sessions are faked, and the evaluator fake is hostile — it returns ACCEPT unconditionally with prose instructing the harness to disregard the suite. Two items are **inferred and labelled**: (a) that `cmd_review`'s exit 0 is reachable downstream of CG-02 — `review` was **not** run, it launches a real Claude session; (b) that CG-01 is the live default path — read from `driver.config.yaml`, not from an observed production run.

## Findings

| ID | Claim | Severity | Reproduction | Observed vs Expected |
|---|---|---|---|---|
| **CG-01** | A run without generation has **no deterministic scenario gate at all**; a FAILED required scenario is overridden by evaluator prose | **BLOCKING** | `.venv/bin/python verification-evidence/cert/C-GATE/attack_loop_e2e.py` → case **B1a** | **Observed:** `status=ACCEPTED exit=0 gate=NO SUITE (gate not invoked) ran=['backend_generic']` — scenario really executed, really failed; `accepted/record.json` written. **Expected:** blocks, as it does with a planner (case B2 → FIX → exit 12) |
| **CG-02** | The completion-audit branch terminates a run **before** the suite gate is computed; persists ACCEPT and suppresses the coverage report | **BLOCKING** | `PYTHONPATH=verification-evidence/cert/C-GATE .venv/bin/python verification-evidence/cert/C-GATE/attack_precedence_evidence.py` → case **C1a** | **Observed:** `run_status=NEEDS_INDEPENDENT_REVIEW exit=14 persisted_final_decision=ACCEPT scenarios_executed=[] gate_would_have_said=NOT_VERIFIED (2 unverified) LoopResult.suite_present=False`. **Expected:** gate consulted before any terminal state records ACCEPT; coverage reported on every terminal path |
| **CG-03** | Wholesale validation collapse is invisible to the gate when the payload declares no risks | NONBLOCKING | `attack_loop_e2e.py` → **B10a** vs **B10b** | **Observed:** B10a (100% of proposals rejected, no risks) → `ACCEPTED exit=0 gate=VERIFIED`; B10b (same, one P0 risk) → `BLOCKED exit=11`. **Expected:** the decision record should distinguish "generation collapsed" from "nothing to add" |
| **CG-04** | `scenarios run-generated` exits **0** for a P2-only, risk-free plan in which every scenario was BLOCKED | NONBLOCKING | `.venv/bin/python verification-evidence/cert/C-GATE/attack_replay.py` → case **D2** | **Observed:** `exit=0`, `scenario gate: VERIFIED — 0/0`. D1 (P0 plan) → exit 20; D3 (P2 plan + P0 risk) → exit 20. **Expected:** a replay in which nothing ran should not be a green exit |
| **CG-05** | A P0 risk counts as covered by any passing scenario sharing its `risk_category` tag — including a trivial P3 one | NONBLOCKING | `.venv/bin/python verification-evidence/cert/C-GATE/attack_gate_unit.py` → **A9g** | **Observed:** P0 `idempotency` risk + one passing P3 scenario tagged `idempotency` → `VERIFIED`, ACCEPT survives. **Expected:** coverage keyed to `ScenarioProvenance.generating_risk` and/or a severity floor. Note **A9e** proves model-supplied `covered_by` + persuasive `basis` is correctly ignored |
| **CG-06** | `evaluate_gate` is last-record-wins on a duplicated scenario id; a PASSED record after a FAILED one flips it to VERIFIED | NONBLOCKING (unreachable) | `attack_gate_unit.py` → **A12**; `attack_assembly_dup.py` → **E2a/E2b** | **Observed:** A12 → `VERIFIED/ACCEPT`; E2b (real executor, duplicate forced past `add`) → outcomes `[('perm','FAILED'),('perm','PASSED')]`, `gate=VERIFIED`. **Reachability tested:** E2a — `build_suite` refuses the duplicate; E1 — end-to-end id collision → `BLOCKED`. No product path produces it |

### CG-01 reproduction detail

`run_control_loop` sets `suite_result = None` on the no-planner branch (`cli.py:329-340`); the gate is applied only under `if suite_result is not None:` (`cli.py:619`). Every use of `scenario_result` in `cli.py` was grepped — it reaches the evaluator prompt, `should_investigate` and provenance, and is **never** compared against the decision. The shipped `driver.config.yaml` has **no `scenario_generation` block**, so `enabled` defaults `False` (`config.py:148`) and `_make_planner` returns `None` (`cli.py:1443-1445`) — this is the default branch, not a dormant legacy path. It is **not a regression from 537ae0b**, and the self-reports never claimed to cover it (`REMEDIATION.md` §B4 scopes the fix to the suite); no test in `tests/` asserts either behaviour.

### CG-02 reproduction detail

Two compounding defects: (1) step 6b returns at `cli.py:578-589`, before `_apply_suite_precedence` at 6c; (2) `LoopResult(...)` at `cli.py:589` passes four positional args, dropping `last_suite`, so `_report_coverage` returns early (`cli.py:1877`) while `_report_outcome` prints "the product evaluation passed". Controls isolate the ordering as the cause: audit `VERIFIED` → BLOCKED exit 11 (gate consulted); audit `CONTRADICTED` → FIX exit 12 (gate consulted). **Mitigation, stated honestly:** exit is 14, not 0, and `RunStatus.ACCEPTED` is not reached — classified BLOCKING against the enumerated criterion *"silently omit required verification"*, which it meets exactly.

## Attacks that FAILED — the claim holds

All 15 mandated vectors were attacked. **The gate could not be talked out of a single computed hole by any evaluator prose.** Refused: zero cases executed (A1a, C1b/c); all/partial required SKIPPED (A2a/b); budget exhaustion (A3, B5 real `execution_budget_s: 0` → BLOCKED, `ran=[]`); browser unavailable (A4, B4 → BLOCKED, no approved-skip lever exists); every evidence attack (A5a/b/c, C2a–C2k — deleted record, corrupt JSON, empty file, wrong scenario, wrong run, wrong iteration, empty run_id, deleted directory, all refused; C2k confirms an unprovable PASS reaches the gate as unverified); generator raises/returns `None` (B7/B8 → BLOCKED) while a legitimately empty plan does not block (B9) — **failed wave and "nothing to add" are correctly distinguished**; forced `full_run`/`expected_required_ids`/`everything_required_passed`/downgraded priority (A8b/c, A11a/b); uncovered P0/P1 risks (A9a/b/e/f, B11a → BLOCKED with every executed scenario green); assembly loss (A10, E1); A14 — a green suite never *upgrades* a FIX into an ACCEPT.

**Widening (vector 8) is genuinely correct:** C3 ran a real two-iteration loop — iteration 2 ran the narrowed set `[backend_generic, gen-a]`, found it green, then **re-executed the full set** `[backend_generic, gen-a, gen-b, gen-c]` with `selection_reason='full required regression set before acceptance'` before accepting. Acceptance does not rest on `full_run`.

**P2/P3 boundary (vector 15) is correct in both directions:** A9c/A9d/B11b — low-priority uncovered risks do not block; A13c — a failing P2 *generated* scenario correctly does not hold the run because `build_suite` records `required=False` rather than required-but-ignorable.

## Inverse direction — no false refusals

Explicitly tested; **zero false refusals in 70 attacks.** 15 controls all passed: A13a–d, A1b, A6b (whitespace-only "problem"), A8c, A9c/d, B1b, B9, B11b, B12a, B12b, C2a, C3. The gate is not a refuse-everything gate — where it fails it fails by **not being consulted**, never by being wrong.

## Mutation re-run

`.venv/bin/python verification-evidence/post-remediation/run_mutations.py` → **30/30 caught. Agrees with the recorded 30/30.** Diff against the committed `mutation-results.json` is **wall-clock timing strings only** (30 ins / 30 del, all `in 2.12s` → `in 1.99s`); every mutation id, status and named failing test identical. M5 (skip the gate's verdict, return the evaluator's ACCEPT), P5–P8, P9–P12 all caught. **What it does not cover:** no mutation exercises the no-planner branch, which is why CG-01 survives a 30/30 result.

**Working-tree note:** `mutation-results.json` was *already* modified at session start; the re-run changed timings only. It was not reverted — that would itself be a state change.

## Integrity

`git status --porcelain` ends as: ` M verification-evidence/post-remediation/mutation-results.json` (pre-existing), `?? .driver-state/` (expected), `?? verification-evidence/cert/` (this review's artifacts). Nothing under `neyma_product_driver/` or `tests/` was created, modified or deleted; no state-changing git command was run. All attacks used throwaway `mkdtemp` repos, loopback only, no network, no credentials.

# Verdict: **FAIL**

The gate's arithmetic is strong — 70 hostile attacks, including an always-ACCEPT evaluator arguing in prose that the harness be disregarded, could not move it. It fails on **where the gate is wired**, not on what it computes: CG-01 leaves the driver's *default* path entirely ungated, and CG-02 has one terminal path return before the gate is ever evaluated. Both are wiring defects with small fixes (extend the gate to the single-scenario path; move the suite-precedence call above the completion-audit early return and carry `last_suite` into that `LoopResult`) — recorded, not fixed, as remediation is a separate role.

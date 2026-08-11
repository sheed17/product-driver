# G-SCALE — INDEPENDENT CERTIFICATION REVIEW (scale)

Recorded verbatim by the campaign controller from reviewer G-SCALE's return. The
reviewer could not write this file itself (subagent report-file creation is refused
by the harness); the harness scripts and raw JSON in this directory are the
reviewer's own artifacts and were written by it.

**Candidate:** `537ae0b`. Everything below was **executed**, not inferred, unless stated otherwise.
Working tree unchanged: `git status --porcelain` shows only the pre-existing `M verification-evidence/post-remediation/mutation-results.json`, `?? .driver-state/`, `?? verification-evidence/cert/`.

---

## 0. What the implementer's harness does and does NOT check

`verification-evidence/post-remediation/h-scale/run_scale.py` was read first.

**Does:** builds real 11/51/101/201 suites against a real loopback HTTP+SQLite target with injected defects; runs the real `SuiteExecutor`/`ScenarioExecutor`; records counts, a per-outcome tally, `verify_case_evidence` per outcome, the real `evaluate_gate`, the real `_apply_suite_precedence` vs a hostile ACCEPT, `select_rerun`, and `len(evaluator_prompt)`. Two probes: `--budget`, `--extra-uncovered-risk`.

**Does not — every one of these had to be closed by this review:**
1. **Nothing is recounted from disk.** Its "independent recount" iterates `result.outcomes`, the same object that produced `result.passed`. It never opens a `result.json`.
2. **Evidence-directory uniqueness is never checked.** This gap hid G-SCALE-02.
3. **Outcome state is never re-derived** from the persisted record.
4. **`verify_case_evidence` is never shown to have teeth** — only ever called with the correct stamps, and always with `iteration=1`, which the function skips when falsy.
5. **"Resume" is not a resume** — `model_validate(model_dump())` in the same process; no file, no second process, and the scenario *plan* is never persisted or restored.
6. **Prompt boundedness asserted on a 96%-green suite** (8 failures / 201); `all_failures_named_in_summary` checks the summary, not the prompt.
7. **No above-ceiling, per-category-cap, per-wave-budget, `max_waves`, id-collision, or complexity test.**
8. **RSS is cumulative** — all four sizes in one process, so its growth figure is a measurement artefact.

---

## 1. Baseline sweep — real execution (`raw/base/`)

One OS process per size. `run_id="gscale-N"`, `iteration=3` (deliberately ≠1). Injected: wrong owner {12,66,87,150,183}, non-idempotent approve {11,74,176}. Both framings reported; the distinction never changed a verdict.

| | **10 (suite 11)** | **50 (51)** | **100 (101)** | **200 (201)** |
|---|---|---|---|---|
| scenarios in → outcomes out | 11 → 11 | 51 → 51 | 101 → 101 | 201 → 201 |
| independent tally == driver totals | ✔ | ✔ | ✔ | ✔ |
| assembly problems / silent drops | 0 | 0 | 0 | 0 |
| **evidence dirs on disk / expected** | 11/11 | 51/51 | 101/101 | 201/201 |
| dirs holding own `result.json` | 11 | 51 | 101 | 201 |
| **distinct** evidence paths | 11 | 51 | 101 | 201 |
| two cases sharing one dir | 0 | 0 | 0 | 0 |
| on-disk ids == executed ids | ✔ | ✔ | ✔ | ✔ |
| run_id/iteration mis-attributions | 0 | 0 | 0 | 0 |
| disk-re-derived verdict disagreements | 0 | 0 | 0 | 0 |
| duplicate result ids | 0 | 0 | 0 | 0 |
| failures vs injected defects | 0=0 | 2=2 | 5=5 | 8=8 |
| clusters (grouped) | 0(0) | 2(0) | 3(1) | 4(1) |
| every failure in exactly one cluster | ✔ | ✔ | ✔ | ✔ |
| gate | VERIFIED | NOT_VERIFIED | NOT_VERIFIED | NOT_VERIFIED |
| gate required total/passed | 11/11 | 51/49 | 101/96 | 201/193 |
| **independent** recount of required total/passed | 11/11 | 51/49 | 101/96 | 201/193 |
| hostile always-ACCEPT becomes | ACCEPT | FIX | FIX | FIX |
| rerun selection size / duplicates | 1/0 | 34/0 | 67/0 | 134/0 |
| evaluator prompt chars (≈tok) | 2,433 (608) | 3,985 (996) | 6,823 (1.7k) | 8,996 (2.2k) |
| failures missing from prompt | 0 | 0 | 0 | 0 |
| **wall clock** | 0.385 s | 1.971 s | 3.735 s | 7.262 s |
| **per scenario** | 35.0 ms | 38.6 ms | 37.0 ms | 36.1 ms |
| `build_suite` / `evaluate_gate` / `select_rerun` | 0.8/0.04/0.02 ms | 2.1/0.08/0.06 | 3.3/0.10/0.11 | 7.2/0.23/0.37 |
| **peak RSS (own process)** | 48.4 MB | 50.4 MB | 51.4 MB | 52.7 MB |

`verify_case_evidence` teeth at every size: correct stamp accepted; wrong `run_id`, wrong `iteration`, wrong `scenario_id` all rejected. **Runtime linear** (±7% per-scenario across 20×); **memory +4.3 MB across 20×**.

## 2. Adversarial variant — every scenario fails, 400-char messages (`raw/allfail/`)

| | 10 | 50 | 100 | 200 |
|---|---|---|---|---|
| outcomes / all FAILED | 11 | 51 | 101 | 201 |
| counts agree, dirs unique, attribution | ✔ | ✔ | ✔ | ✔ |
| summary block chars | 11,525 | 53,248 | 106,728 | 211,523 |
| **evaluator prompt chars (≈tok)** | 15,179 (3.8k) | 56,908 (14.2k) | 110,389 (27.6k) | **215,184 (53.8k)** |
| failures missing from prompt | 0 | 0 | 0 | 0 |
| gate required passed | 0/11 | 0/51 | 0/101 | 0/201 |
| hostile ACCEPT becomes | FIX | FIX | FIX | FIX |
| wall / peak RSS | 0.43 s / 49.4 MB | 1.89 / 50.8 | 3.74 / 51.5 | 7.04 / 53.3 |

Every failure still named — **no silent truncation**. Prompt **not bounded** (G-SCALE-03).

## 3. Above the maximum

- **Config:** `max_total_scenarios` 200 accepted; 201/500/10 000 **refused**. No silent clamp.
- **Planner:** a wave proposing **500** against a 200 total admitted exactly **200**, 300 recorded as `RejectedScenario` + 2 budget notes; `proposed == accepted + rejected`, **silent loss 0**.
- **Executor (real, `raw/abovemax/`):** no ceiling exists there. 300 → 301 outcomes / 301 dirs / 301 distinct paths / VERIFIED / 10.9 s / 36.1 ms per scn / 53.1 MB. 500 → 501/501/501 / VERIFIED / 20.8 s / 41.6 ms / 56.1 MB. Nothing truncates, nothing is lost — but the "200 maximum" is a *generation* budget, not an execution guarantee.

## 4. Caps and budgets at scale

| bound | asked | admitted | refusals recorded | loss accounted |
|---|---|---|---|---|
| `max_scenarios_per_risk_category=6` | 100 | **6** | 94 | ✔ |
| per-wave limit 8 | 100 | **8** | 92 | ✔ |
| `max_waves=2` | 4 waves | 2 used | "refused: 2 generation wave(s) already used" ×2 | ✔ |

## 5. The two false-ACCEPT probes at 200 — both refuse

| probe | result |
|---|---|
| **Budget exhaustion** (`--budget 0.5`): 11 executed, **190 required SKIPPED** | gate NOT_VERIFIED 201/11, 190 unverified; hostile ACCEPT → **BLOCKED** ✔ |
| **Uncovered P0 on a fully green suite** (`--gap`): 201/201 passed | gate NOT_VERIFIED, 1 uncovered P0 (`authorization`); hostile ACCEPT → **BLOCKED** ✔ |
| **Control** (same green suite, no gap) | VERIFIED 201/201; hostile ACCEPT → **ACCEPT** — the refusals are discriminating, not vacuous |

## 6. Identity at scale — sound

200 ids sharing a **63**-, **80**- and **200**-char prefix: 200 distinct derived ids, **0 id collisions**, 200 distinct evidence dir names, **0 dir collisions** in every family. `GeneratedScenario._identity` shortens with a whole-input digest and keeps `proposed_id`.

## 7. Cost model — the quadratics (ratio 2.0 = linear, 4.0 = quadratic)

| n | `build_suite` | `cluster_failures` | `evaluate_gate` | `select_rerun` | `summary_block` | `execution_order` |
|---|---|---|---|---|---|---|
| 100 | 0.43 ms | 2.74 | 0.24 | 0.29 | 0.30 | 0.10 |
| 200 | 1.32 | 9.17 | 0.49 | 0.91 | 0.57 | 0.17 |
| 400 | 4.29 | 28.5 | 0.87 | 4.09 | 1.16 | 0.33 |
| 800 | 15.8 | 105 | 2.04 | 13.4 | 2.19 | 0.67 |
| 1600 | 58.7 | 418 | 4.03 | 55.5 | 5.04 | 1.41 |
| **ratios** | **3.1→3.7** | **3.4→4.0** | 1.8→2.3 | **3.1→4.2** | 1.9→2.3 | 1.7→2.1 |

**Quadratic:** `build_suite` (`add`→`by_id` linear scan per insert), `cluster_failures` (explicit O(F²) pair loop), `select_rerun` (`suite.by_id` per failure). **Linear:** gate, summary, ordering. At 200 this is ~11 ms = 0.15% of a 7.3 s run.

200 failures forced into **one** 200-member cluster: 14.6 ms, exact partition, render 1,996 chars, evidence paths capped at 6.

## 8. Resume at scale, separate processes

`write` (pid 69690) → disk → `read` (pid 69960): 200 scenarios / 201 suite entries / 201 required / 200 compiled / 1 wave, **SHA-256 state fingerprint identical** (`4e9e49b1…6eccd86`; covers ids, priorities, categories, requirement refs, coverage signatures, entry order, required set, isolation keys, compiled names, compiled request method+path, executed ids, observed failure ids, wave records, coverage summary, risks). Reloading the 201-outcome result in the fresh process: 193/8, **identical gate verdict** NOT_VERIFIED 201/193 with 8 unverified, all 201 evidence dirs still resolve.

---

# FINDINGS

### G-SCALE-01 — **BLOCKING**

**Claim:** A resume silently drops required scenarios that no longer compile, and the acceptance gate cannot see the loss — a run can ACCEPT having lost 199 of 200 required scenarios.

**Repro:** `cd verification-evidence/cert/G-SCALE && ../../../.venv/bin/python gscale_probes3.py --driver /Users/sammyfammy/neyma-product-driver --out raw/probes`

**Observed** (`raw/probes/probe12.json`): `planned_before=200`, `after_resume=1`, `silently_dropped=199`, `generation_problems=[]`, `assembly_problems=[]`, `gate=VERIFIED 2/2`, `uncovered_risks=0`, **`hostile_accept_becomes=ACCEPT`**, persisted plan on disk still lists 200, `loss_visible_anywhere_machine_readable=false`.

**Expected:** the loss must reach `generation_problems`/`assembly_problems` so `evaluate_gate` blocks — exactly as a compile failure at *plan* time already does (it becomes a `RejectedScenario`). At resume time the removal (`scenario_planner.py:623-641`) is reported **only** via `self.emit(...)`, a console line.

**Corroboration** (`p10`, 200→0): same silence — `generation_problems=[]`, no wave note, no rejected record, `gate_sees_a_problem=false`. The full-drop case happens to be caught downstream only because the surviving risk register triggers the uncovered-risk check; that net vanishes as soon as one survivor covers each category, which is the ACCEPT above.

### G-SCALE-02 — **BLOCKING**

**Claim:** Two suite entries can resolve to one evidence directory; the second overwrites the first and the gate credits both as verified. `sanitize_filename`'s docstring claim "Distinct labels always produce distinct filenames" is false.

**Repro:** `../../../.venv/bin/python gscale_probes.py --driver /Users/sammyfammy/neyma-product-driver --out raw/probes p5_shared_evidence_dir`

**Observed:** permanent `"approve twice"` + generated `"approve-twice"` → 2 entries, **0 assembly conflicts**, 2 outcomes, **1 distinct evidence path**, 1 dir on disk, **both `evidence_verified: true`**, gate **VERIFIED required_passed=2**. Post-run re-verification: *"the evidence at …/approve-twice belongs to scenario 'approve-twice', not 'approve twice'"*. Also `sanitize_filename("case 001") == sanitize_filename("case/001") == "case-001"`.

**Expected:** distinct scenario ids must never share a directory; if they do, at most one may count as verified.

**Mechanism:** the sanitiser folds non-`[A-Za-z0-9._-]` to `-` *before* the identity-preserving shortening. Generated ids are pre-sanitised so generated-vs-generated is safe (proved at 200 in §6); **permanent ids are not** — `_assemble_suite` uses `scenario.name` verbatim (`cli.py:745`), free operator text, and `ValidationContext.existing_ids` holds only generated ids.

**Reachability, stated honestly:** the two shipped scenario files (`backend_generic`, `browser_generic`) do not fold, and only one permanent entry enters a suite today, so the default config is not affected. It needs an operator-chosen permanent name containing a space/slash (nothing validates `Scenario.name`). Classified BLOCKING because the harm class — misattributed evidence credited as verification — is listed as blocking and it is fully reproducible with a legal configuration; a reader weighting reachability may downgrade it. The false docstring claim should be corrected regardless.

### G-SCALE-03 — NONBLOCKING

**Claim:** The evaluator prompt is **not** bounded. §4H's "the evaluator prompt stays bounded" is disproved; "still names every failure" is upheld.

**Repro:** `../../../.venv/bin/python gscale_probes.py --driver … --out raw/probes p7_prompt_bound`

**Observed:** 201 real failures @400-char expectations → **215,184 chars ≈ 53.8k tokens**. Synthetic: 200 failures × 100 chars → 208k; × 1,000 → 1.30 M; × 10,000 → **12.1 M chars ≈ 3.0 M tokens**; 500 × 10,000 → **30.3 M chars ≈ 7.6 M tokens**. All failures named at every point. `summary_block` emits up to four `failed_assertions` **verbatim with no length cap**, and `evaluator_prompt` embeds it whole.

**Expected:** a stated cap, or the claim withdrawn.

**Why not blocking:** nothing is dropped silently, and overflow is fail-safe — `EvaluatorSession._collect` returns `blocked_decision(...)` on session error and `evaluate` returns BLOCKED on timeout, so a context overflow becomes BLOCKED, never ACCEPT. The §4H claim survived only because it was measured on 0–8 failures out of 201.

### G-SCALE-04 — NONBLOCKING

**Claim:** Three O(n²) hot spots: `build_suite`, `cluster_failures`, `select_rerun` (ratios 3.1–4.2, §7).

**Repro:** `gscale_probes.py … p6_complexity`. **Observed vs expected:** ~11 ms total at the 200 ceiling (0.15% of run) — a limitation, not a present problem; 0.53 s at 1600.

### G-SCALE-05 — NONBLOCKING

**Claim:** The gate trusts a stored `evidence_verified` flag on a reloaded result and never rechecks disk.

**Repro:** `../../../.venv/bin/python gscale_probes2.py --driver … --out raw/probes --suite-result raw/gap/suite-result-200green.json p11_gate_trusts_stale_evidence_flag`

**Observed:** with the entire evidence tree moved aside — gate still **VERIFIED, 201/201 required passed**, while **0 of 201** directories resolve. **Expected:** re-verification, or an explicit statement that a reloaded result is not re-provable. Latent only: every `suite_result` assignment in `cli.py` was checked (334/359/369) — acceptance always evaluates a freshly executed result.

### G-SCALE-06 — NONBLOCKING

**Claim:** The per-case `result.json` does not record its own verdict — `ScenarioResult.passed` is a property, so it is not serialised, and neither is PASSED/FAILED/BLOCKED/SKIPPED. **Observed:** an auditor must re-implement `error is None and readiness_ok and all(a.passed…)`. The reviewer did; it agreed at every scale (§1). Facts present, conclusion absent, in the directory the summary invites a reader to check.

### G-SCALE-07 — NONBLOCKING

**Claim:** `evaluate_gate` double-counts duplicate ids in `expected_required_ids`. **Repro:** `gscale_probes.py … p9_gate_duplicate_required_ids`. **Observed:** `["a","a","a"]` + one passing outcome → `required_total=3, required_passed=3, VERIFIED`. Not reachable normally (`ScenarioSuite.add` refuses dup ids) and it inflates both sides so it cannot manufacture an acceptance; a corrupted persisted result would report inflated coverage. The complementary case is correct (missing required id → `[NO RESULT] … cannot have been verified`, blocks).

### G-SCALE-08 — NONBLOCKING (honesty)

**Claim:** Two §4H claims were not measured as stated. (a) "resume: reload identical, same verdict" was a same-process pydantic round-trip that never touched the scenario *plan*; a real cross-process resume does pass (§8), so the conclusion holds but the evidence did not exist. (b) "prompt stays bounded" — see G-SCALE-03. Also: the §4H RSS column is cumulative across all four sizes in one process, so its growth figure is an artefact.

### Checked and upheld — NOT-A-DEFECT

Counts (10/50/100/200 **and** 300/500, zero silent drops on the non-resume path); evidence integrity, attribution and per-case uniqueness in every executed sweep; `verify_case_evidence` demonstrably has teeth; gate `required_total`/`required_passed` match the independent recount at every size; failures match injected defects exactly; hostile ACCEPT cannot get an unearned acceptance and *can* get an earned one; identity at 200 with 63/80/200-char shared prefixes; config/planner ceilings refuse rather than clamp with zero unaccounted loss; per-category, per-wave and `max_waves` budgets enforced in code; rerun selections duplicate-free and suite-contained; clusters partition failures exactly including a forced 200-member cluster; cross-process resume byte-identical with identical verdict; runtime linear at ~36 ms/scenario, RSS +4.3 MB across 20×.

---

# VERDICT: **FAIL**

Two blocking defects. §4H's scale claims are substantially correct *as far as they were measured* — counts, evidence, gate behaviour, identity, runtime and memory all hold at 10/50/100/200 and beyond — but the area was not attacked where it is weakest. A resumed run can lose 199 of 200 required scenarios and still ACCEPT (G-SCALE-01), and evidence-path derivation is not injective, so two cases can share one directory while both are counted verified (G-SCALE-02). Neither is visible to the recorded harness, because neither was checked.

**Artifacts** (`verification-evidence/cert/G-SCALE/`): `gscale_one.py`, `run_sweep.sh`, `start_target.sh`, `gscale_probes.py`, `gscale_probes2.py`, `gscale_probes3.py`, `gscale_resume.py`, `work/` (unmodified target fixture copy), and `raw/{base,allfail,budget,gap,abovemax,probes,resume}/` with per-size rows, suite results, summary blocks, evaluator prompts, per-case evidence trees and all probe JSON.

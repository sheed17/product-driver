# A-QUALITY — Independent certification review of dynamic scenario generation

**Reviewer:** A-QUALITY (independent). Did not write the code, did not write the
prior measurements. Every claim in `ADJUDICATION.md`, `REMEDIATION.md` and
`POST-DYNAMIC-REMEDIATION.md` was treated as untrusted.

**Candidate:** `537ae0b` on `main` (parent `dcc649e` == `origin/main`).
**Interpreter:** `.venv/bin/python`.
**Decisive question:** on representative engineering work, is dynamic scenario
generation *materially useful* or *materially useless*?

Nothing under `neyma_product_driver/` or `tests/` was modified. No git operation
was performed in either repository. All artifacts live under
`verification-evidence/cert/A-QUALITY/`.

---

## 0. EXECUTED vs READ

**Executed live this session**
* Live generation waves through the real `LLMScenarioReasoner` against the real
  Neyma repository at `/Users/sammyfammy/Desktop/freight-logistics-operational-teammate`
  — the path `driver.config.yaml` actually points at (note: *not* the
  `/Users/sammyfammy/…` copy; both were checked for modification).
* Eight deterministic probes against the candidate's own validator, gate and
  committed artifacts — `probes/run_all_probes.py`.
* Counterfactual execution of oracles the prior campaign ACCEPTED, against real
  SQLite databases in correct and broken states (probe `V1`).
* Two quantifications: `probes/QUANT-oracle-traps.txt`,
  `probes/QUANT-false-coverage.txt`.

**Read, not executed:** `scenario_generator.py`, `scenario_validation.py`,
`scenario_planner.py`, `scenario_gate.py`, `scenario_plan.py`, `scenarios.py`,
`prompts.py`, and the prior campaign's committed artifacts.

**Never executed by anyone, including every prior campaign:** the generated
scenarios themselves. No campaign in this repository has run a generated scenario
against the product and watched it pass or fail. Every "mechanically executable"
figure — the prior campaign's and mine — is a *static* property of JSON. Finding
A-Q-6 (task C) is what that omission costs.

---

## 1. "Useful", pre-registered

`PREREGISTERED-USEFUL.md`, written before any output of this session was read.
USEFUL = U1 (has an assertion the executor scores) ∧ U2 (a plausible defect in the
task's risk surface flips an assertion; neither vacuous nor unsatisfiable —
**hand-judged**) ∧ U3 (drives the product, not a broad suite or generic
diagnostic) ∧ U4 (category implicated by the task's ACs or diff) ∧ U5 (not a
duplicate shape) ∧ G1 (grounding names something that actually exists).

U1/U3/U4/U5/G1 mechanical (`analyze_cert.py`); U2 hand-judged per scenario in
`hand-counterfactuals.md`.

**Conservative/generous, stated:** U4 for inherited tasks reuses the prior
campaign's own narrow pre-registered `RELEVANT` sets, so USEFUL is reported twice,
with and without U4.

---

## 2. What the prior harness measures — and does not

`analyze.py` does **not** measure: whether an assertion can fail
(`mechanically_scorable()` is an is-any-field-populated test); whether the cited
requirement exists (`grounded()` is an emptiness test, reported as *"names a real
unit and principle — 100 %"*); whether a scenario tests what it says it tests;
redundancy in any useful sense (`shape()` keys on action-kind sequence — one extra
`wait` makes two identical probes distinct); execution; infrastructure failure
rate; variance.

Two fixture properties bound the benchmark:
* **The r2 fixtures describe features that do not exist.** Of the diff files the
  inherited tasks claim were changed, only `governed_approval.py` and
  `action_callback.py` exist. My two new tasks (F, G) cite only real modules.
* **The probe workspace does not exist.** Every SQL oracle in every campaign reads
  `data/active_workspace/driver_probe/workflow.sqlite3`, absent from the product
  repository. The *tables* cited are real.

---

## 3. Task set and waves actually run

**Inherited** (verbatim r2 fixtures via `run_cert_gen.py`, which imports
`run_generation.build_tasks` and `build_base`): A, A2-diff, B-diff, C, D, E.
**New, this reviewer's design, never used by any prior campaign:**
* **F — dependency / partial failure.** Resilient operator-notification delivery.
  Diff files are four **real** modules.
* **G — malformed / missing / conflicting evidence.** Carrier-document
  reconciliation. Diff files are three **real** modules.

**Waves.** Attempt 1 (07:54–08:06Z): 15 runs launched sequentially; **2 completed
(A-r1, F-r1) and 13 died in ~2 s each** when the external session-usage limit hit.
Those 13 are quarantined under `raw/aborted-usage-limit/` and are scored as
INFRASTRUCTURE, never as product results. Attempt 2 (20:14Z onward), after the
limit reset, re-ran them.

Every measurement below rests on the completed waves listed in the table. Where a
replicate did not complete, it is named as not completed rather than inferred.

---

## 4. Counter-evidence — what held up under attack

These are reported first because the honest picture is not a prosecution.

* **Grounding is real, verified against live product source.** 28/28 accepted
  scenarios name a real unit id or a verbatim acceptance criterion *and* a real
  founder rubric id — under my strict check, not the prior campaign's emptiness
  test. More than that, the *product* grounding is real: I verified in
  `src/freight_recon/action_callback.py` that `Action link rejected` (401),
  `Missing action token` (400), `Method not allowed` (405) and `Neyma action
  applied` (200) are the exact literals and exact status codes the product emits;
  `delivery_action_rejected` is a real event type in `delivery.py`; the
  `DispatchStatus` enum really does lack an UNKNOWN member; `workflow_runs`,
  `audit_events`, `security_events`, `effect_grants` and `checkpoint_witnesses`
  are all real tables. The generator read the product and used what it found.
* **0 of 28 accepted scenarios invoke a broad test suite** — under a stricter test
  than the prior campaign's (ANY broad-suite command appearing anywhere, not just
  scenarios consisting solely of one). This is not luck: **5 of the 8 approved
  commands offered to the generator are pytest/diagnostic invocations**, and it
  used none of them. The prior campaign's headline claim of 0 % replicates.
* **Acceptance rate replicates exactly.** 28/29 = 96.6 %, against the prior
  campaign's 96.6 %.
* **The generator states its own limits, accurately.** On every task where the
  acceptance criteria were unreachable it said so in `unresolved_questions`,
  naming the exact modules and the exact missing lever — e.g. task G: *"three of
  the four ACs (two of them weight-3) are unverifiable this wave."*
* **The uncovered-risk gate works when the labels disagree.** On task F, a run in
  which all four scenarios PASS still yields four blocking gaps (probe C1).
* **The recorded limitation "task A produces zero scenarios in four attempts" did
  not reproduce.** A-r1 returned 6 proposals / 5 accepted in 340.8 s, inside the
  600 s timeout. The recorded limitation is load-dependent, not intrinsic.

---

## 5. Findings

Full text, severities, reproductions and observed-vs-expected are in the summary
returned with this review. Reproduce everything deterministic with:

```
.venv/bin/python verification-evidence/cert/A-QUALITY/probes/run_all_probes.py
.venv/bin/python verification-evidence/cert/A-QUALITY/analyze_cert.py \
    --dir verification-evidence/cert/A-QUALITY/raw --driver .
.venv/bin/python verification-evidence/cert/A-QUALITY/final_table.py
```

Live waves (each consumes real model usage):

```
.venv/bin/python verification-evidence/cert/A-QUALITY/run_cert_gen.py \
    <A|A2|B|C|D|E|F|G> [--diff] --driver . \
    --out verification-evidence/cert/A-QUALITY/raw --rep <n>
```

## 6. Artifacts

| path | what |
|---|---|
| `PREREGISTERED-USEFUL.md` | the usefulness definition, written before any output was read |
| `run_cert_gen.py` | wave harness (inherited fixtures + 2 new tasks + telemetry) |
| `analyze_cert.py` | independent analyzer (strict grounding, vacuity, surface diversity) |
| `final_table.py` | emits the per-task table below |
| `probes/run_all_probes.py` | all deterministic probes, one command |
| `probes/ALL-PROBES-OUTPUT.txt` | recorded probe output |
| `probes/QUANT-oracle-traps.txt` | substring-oracle quantification |
| `probes/QUANT-false-coverage.txt` | false-coverage quantification |
| `hand-counterfactuals.md` | per-scenario hand-judged U2 counterfactuals |
| `metrics-cert.json` | full machine-readable metrics |
| `raw/` | every brief, raw payload, plan, compiled suite and telemetry |
| `raw/aborted-usage-limit/` | the 13 attempt-1 runs killed by the usage limit |
| `product-repo-BEFORE.txt` | baseline git state of both product checkouts |

---

## 7. Measurements

### Waves actually completed

| run | risk surface | elapsed | waves | proposed | accepted | rejected |
|---|---|---|---|---|---|---|
| A-r1 | UI / stale state | 340.8 s | 1 | 6 | 5 | 1 |
| C-r1 | read-only view | 175.1 s | 1 | 7 | 7 | 0 |
| D-r1 | persistence / restart | 494.5 s | 1 | 5 | 5 | 0 |
| E-r1 | authorization / release | 360.6 s | 1 | 4 | 4 | 0 |
| F-r1 | dependency / partial failure (**NEW**) | 384.4 s | 1 | 4 | 4 | 0 |
| G-r1 | malformed/missing/conflicting evidence (**NEW**) | 517.9 s | 1 | 3 | 3 | 0 |

**6 live waves, 6 generator sessions, 0 failed, 0 empty.**

**DID NOT COMPLETE — stated, not inferred:** `B-diff` (approval / idempotency /
cross-tenant, the only two-wave diff-stage run), `A2-diff`, and every second
replicate (A-r2, C-r2, D-r2, E-r2, F-r2, G-r2). No measurement in this review is
extrapolated to them. Every number rests on exactly the six waves above; each
task therefore rests on a **single** sample, which is the same limitation
`POST-DYNAMIC-REMEDIATION.md` §8 item 2 records for the prior campaign and which
this review therefore does **not** close.

### Infrastructure

Attempt 1 launched 15 runs; 2 completed and **13 died in ~2 s each** on the
external session-usage limit — an 18-of-20-wave infrastructure failure rate for
that attempt. Quarantined under `raw/aborted-usage-limit/`, scored as
infrastructure, never as product results. Attempt 2, after the limit reset,
produced 6/6 clean waves before wall clock ended the campaign.

### Per-task results

See `final_table.py` output, reproduced in the returned summary.

### Repository integrity

Both product checkouts byte-identical to the baseline recorded in
`product-repo-BEFORE.txt` at the end of the work: Desktop copy `6e8127d`, home
copy `d59b740`, each with the same two pre-existing untracked docs files. The
Product Driver's `git status --porcelain` ends with the modified
`mutation-results.json` it started with, plus `.driver-state/` and
`verification-evidence/cert/`. `runs/` and `.driver-state/` were not written to.
| task | risk surface | src | prop | acc | rej | USEFUL (U1-U5,G1,U2) | U4-free | own P0 risks left uncovered | trap oracles | endpoints | distinct oracles |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A-r1 | UI / stale state | inherited | 6 | 5 | 1 | 2 | 4 | no P0 | 5 | 2 | 1 |
| C-r1 | read-only view | inherited | 7 | 7 | 0 | 0 | 0 | 0/1 | 7 | 2 | 4 |
| D-r1 | persistence / restart | inherited | 5 | 5 | 0 | 3 | 4 | 1/3 | 0 | 3 | 7 |
| E-r1 | authorization / release | inherited | 4 | 4 | 0 | 2 | 2 | 0/1 | 3 | 2 | 1 |
| F-r1 | dependency / partial failure (NEW) | NEW | 4 | 4 | 0 | 3 | 4 | 2/2 | 2 | 1 | 2 |
| G-r1 | malformed-missing-conflicting evidence (NEW) | NEW | 3 | 3 | 0 | 3 | 3 | 1/1 | 3 | 2 | 1 |
| **total** | | | **29** | **28** | **1** | **13** | **17** | | | | |

acceptance rate            : 28/29 = 96.6%
USEFUL (with U4) of accepted: 13/28 = 46.4%
USEFUL (U4-free) of accepted: 17/28 = 60.7%
strict grounding (real unit/criterion + real rubric id): 28/28
scenarios containing ANY broad-suite command           : 0/28
bare-suite invocations (prior campaign's definition)   : 0/28
effect-family cases / with a state oracle             : 6/6
waves run / failed / empty                            : 6 / 0 / 0

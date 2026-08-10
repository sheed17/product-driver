# Independent Verification — Dynamic Scenario Generation

Verification lead adjudication. Six independent reviewers, mutation testing, and
lead-run experiments. Every completion claim treated as untrusted.

Evidence root: `verification-evidence/`

---

## 1. VERDICT

**IMPLEMENTED BUT NOT YET VERIFIED CAPABLE**

The architecture is real and well-built — this is not decorative integration. But
the primary objective is disproven at three independent points:

1. **The feature is inert through its own CLI.** `LLMScenarioReasoner.propose`
   calls `asyncio.run()` from inside the already-running control loop. Every
   generation wave raises `RuntimeError` and produces zero scenarios.
2. **Model-authored text reaches the shell.** A newline after an approved command
   prefix passes validation and executes as a second command.
3. **Adaptive generation does not respond to evidence.** Measured against real
   model runs, waves driven by different failures are no more different than
   replicates of the same failure — and an evidence-free control is the most
   similar of all.

---

## 2. CAPABILITY MATRIX

| Capability | Result | Evidence |
|---|---|---|
| Dynamic task-based scenario generation | **FAIL** (inert in the driver) | `lead-findings/generator-inert-in-async-CONFIRMED.txt`; r5 F1 |
| Diff-aware refinement | **FAIL** | r2: coverage got *worse* after a persistence+authz diff |
| Relevant risk selection | **PASS** | r2: 0/64 off-topic proposals; risk registers accurate |
| Safe structured scenario compilation | **FAIL** | r3 F-1/F-2 confirmed by lead |
| Mass scenario execution | **PASS** | r4: 201 scenarios, 5.18 s, linear |
| Result aggregation | **PASS** | r4: verified against independent recount at 11/51/101/201 |
| Failure evidence | **FAIL** | per-case evidence dirs empty; lead-verified |
| Adaptive generation | **FAIL** | r5 divergence: BETWEEN 0.429 ≈ REPLICATE 0.464, CONTROL 0.600 |
| Failure clustering | **PASS** | r4: conservative and correct |
| Targeted rerun | **PASS** | `select_rerun` verified at 201 scenarios |
| Final regression rerun | **PARTIAL** | widening is real (cli.py:358) but skips defeat it |
| Promotion candidates | **PASS** | human-gated; `promotion_requires_approval` cannot be disabled |
| Resume support | **FAIL** | plan never reloaded; prior records destroyed |
| Permanent scenario compatibility | **PASS** | phase form preserved; both shipped YAMLs unchanged |
| Completion-auditor integration | **PASS** | precedence unchanged |
| Protocol-resolver integration | **PASS** | precedence unchanged, all five statuses |
| Evaluator integration | **PASS** | suite reaches prompt; failures never truncated |
| Investigator integration | **PASS** | no duplication; one-way information flow |
| Safety enforcement | **FAIL** | two confirmed escapes |
| Fail-closed behavior | **PARTIAL** | malformed input fails closed; skips/refusals do not |

---

## 3. BLOCKING DEFECTS

### B1 — The generator cannot run through the driver (CRITICAL)
`scenario_generator.py:495-508`. `asyncio.run()` inside a running loop raises
`RuntimeError`; the fallback `new_event_loop().run_until_complete()` raises the
same error *inside* the `except RuntimeError` handler, so the trailing
`except Exception` never catches it. The planner swallows it
(`scenario_planner.py:346-351`) and records a wave that produced nothing.

Every call site is inside `async def run_control_loop`: `cli.py:272`, `:315`,
`:638`; also `cmd_scenarios_plan` at `cli.py:1401,1403`.

Lead-verified: a stub reasoner returning a valid payload yields
`scenarios: []`, `error: RuntimeError: Cannot run the event loop while another
loop is running`.

*Why 950 tests miss it:* every test injects a scripted reasoner. Nothing
exercises `LLMScenarioReasoner.propose`.

### B2 — Newline command injection (CRITICAL)
`scenario_validation.py:95` normalizes `\s+`→`" "` **before** `_SHELL_COMPOSITION`
(`:102`) inspects the tail, so the `\n` listed in that regex can never match.
`resolve()` (`:199`) approves the **raw** string; the compiler compares raw to raw
(`scenario_plan.py:698`); `ProcessRunner.run` uses `create_subprocess_shell`.

Lead-verified with a harmless payload:
```
approved.approves('echo payments\necho INJECTED_SECOND_COMMAND_RAN') -> (True, '')
subprocess stdout: payments / INJECTED_SECOND_COMMAND_RAN
```

Same root cause, opposite failure: the scanner is quote-blind, so legitimate
quoted SQL oracles (`... HAVING count(*) > 1`, `'DUP:'||key`, `WHERE a < b`) are
**refused**. This mechanically guarantees the EFFECT_FAMILY categories —
idempotency, retry_safety, timeout_after_effect, restart_recovery,
crash_mid_workflow, partial_failure, persistence_failure, unexpected_transition —
cannot express the persisted-state oracle they are *required* to have.

### B3 — External host via `request.path` (CRITICAL)
`scenario_validation.py:331` host-checks only `request.url`. When `url` is empty
it validates the local `app_url` and never parses `path`. `scenarios._join_url:910`
returns an absolute `path` verbatim; `runner.py:256` is an unrestricted `urlopen`.

Lead-verified: a scenario with `path="https://api.stripe.com/v1/charges"`,
attacker-chosen method, headers and body returns **zero** validation reasons.

### B4 — A skipped required scenario does not block ACCEPT (CRITICAL)
`ScenarioOutcome.blocks_acceptance` (`scenario_suite.py:107-113`) counts only
FAILED/BLOCKED. `full_run` (`:461`) is computed from *selection*, not execution.
So `_apply_suite_precedence` (`cli.py:803`) returns ACCEPT untouched.

Lead-verified: 2 required P0 scenarios, both SKIPPED, 0 executed →
`blocking_failures: []`, `full_run: True`, decision stays **ACCEPT**.

Two reachable triggers, both on default config:
- `execution_budget_s` exhaustion (r4: 25 of 61 skipped → ACCEPT; r1: budget 0 →
  *nothing* executed → ACCEPT).
- `config.run.browser_enabled` defaults **False**, yet
  `scenario_planner.py:340` hardcodes `browser_enabled=True` into the generation
  brief and `scenario_validation.py:230` defaults it True. The generator is told
  the browser exists, proposes browser scenarios, they validate, then every one
  is skipped at runtime. r4 measured 30 of 31 required P0 skipped → **ACCEPT**.

Contradicts the shipped evaluator prompt (`prompts.py:483-485`): *"the harness
enforces that regardless of what you return"*.

### B5 — Per-case evidence directories are empty (HIGH)
`scenario_suite.py:485` creates `scenarios/<id>/` and `:581` records it as
`evidence_path`, but no `ScenarioResult` is ever written there. Lead-verified on
the repo's own e2e: every generated *and* permanent case directory holds one
0-byte `service-api.log`. r4 measured 101 directories, **0 files**.

`prompts.py:460-463` tells the evaluator full output "stay[s] on disk … and can
be read with Read/Grep"; the builder correction cites the same empty path.

### B6 — Resume loses generated scenario state and destroys prior evidence (HIGH)
`_make_planner` (`cli.py:1338`) always builds a fresh planner; nothing reads
`scenario-plan.json` back (only the separate `run-generated` replay does).

Lead-verified: after resume, `scenarios restored: []`, `waves_used: 0`,
`budget_exhausted: False`; `max_waves=2` became 4 across the boundary; a
previously generated scenario regenerated. `persist()` then overwrote the plan —
`['gen-a','gen-x']` → `['gen-y']`, prior records destroyed.

*Why 950 tests miss it:* `tests/test_scenario_loop.py:654`
`TestResumePreservesGeneratedState` reloads and recompiles the plan **inside the
test body**. It exercises no product resume path.

### B7 — Adaptive generation does not respond to evidence (CRITICAL)
14 real model waves, 3 seeded defects, replicates and two control classes.
Risk-category Jaccard on accepted wave-2 coverage:

| class | pairs | similarity |
|---|---|---|
| REPLICATE (same evidence) | 2 | 0.464 |
| BETWEEN (different evidence) | 8 | 0.429 |
| CONTROL (**no** evidence) | 5 | **0.600** |

BETWEEN is not lower than REPLICATE, and the evidence-free control is the most
similar of all. Confirmed on raw pre-validation proposals too (55 pairs). With
`diff_files` removed to control the confound, the timeout-driven run produced no
timeout scenario while the no-evidence control led with `timeout_after_effect`.

The mechanism *is* wired — r1 proved the real assertion text reaches the brief.
The output simply does not track it. Contributing cause: `ScenarioOutcome.brief()`
emits only the first failed assertion, and `normalize_signal` replaces digit runs
with `<n>`, so the generator is told `payments=<n>` — never that two payments were
observed where one was expected.

### B8 — Per-wave budgets are advisory only (HIGH)
`scenario_planner.py:323` computes `allowed`, then passes it only into
`GenerationBrief(max_scenarios=allowed)` — a request to the model. The admission
loop applies only total and per-category caps. Lead-verified by code path;
r5 measured limit=2, 10 returned, **10 accepted**.

Contradicts README: *"Every axis is bounded: scenarios per wave, waves per run…"*.
The implementer's test (`test_scenario_planner.py:253`) sets limit 2, returns 6,
asserts `<= 6`.

---

## 4. MUTATION RESULTS — 6/8 caught

| ID | Requirement removed | Result |
|---|---|---|
| M1 | adaptive generation disabled | CAUGHT |
| M2 | duplicate detection always false | CAUGHT |
| M3 | failed scenarios report PASSED | CAUGHT |
| M4 | safety validation removed | CAUGHT |
| M5 | suite precedence bypassed | CAUGHT |
| M6 | provenance removed | **SURVIVED** |
| M7 | blocking_failures always empty | CAUGHT |
| M8 | everything_required_passed always True | **SURVIVED** |

Control: the unmutated isolated copy is green (950 passed).

- **M6** — tests pass `ScenarioProvenance(...)` in explicitly, so provenance
  *propagation* is tested but its *derivation* (`provenance_for`) never is.
- **M8** — asserted only in the True direction (`test_scenario_e2e.py:572`).
  Real consequence: `scenarios run-generated` returns exit **0** despite failed
  required scenarios (`cli.py:1490`).

---

## 5. REVIEWER RESULTS

| Reviewer | Verdict | Headline |
|---|---|---|
| R1 Architecture / integration | PASS with findings | Integration is real, not decorative; all 11 claims proven except resume |
| R2 Generation quality | FAIL | 6 live model sessions; 77% of proposals destroyed by validation; 1 task produced 0 scenarios |
| R3 Safety boundaries | FAIL | 118 probes; 2 critical escapes |
| R4 Scale | FAIL | Aggregation excellent at 201 scenarios; gate holes let ACCEPT through |
| R5 Adaptive | FAIL | Generator inert; no evidence-responsiveness |
| R6 Acceptance attack | FAIL | Produced multiple false ACCEPTs |

### Adjudicated disagreement
R2 reported six **successful** live `LLMScenarioReasoner` sessions; R5 reported
the reasoner **cannot run at all**. Both are correct. R2 drove `propose()` from a
synchronous harness (works). The driver calls it from inside the async control
loop (always fails). Lead-verified both paths in one run. **R5's finding governs
production behavior.**

R6 additionally reported "the default path has no scenario gate". Lead-checked
against `git show HEAD:cli.py` — the committed pre-feature loop had no hard
scenario gate either. **Pre-existing, not a regression**; this feature *adds* the
first hard gate. It remains true that the gate ships off by default.

---

## 6. END-TO-END PROOF

The mechanism works when driven by a scripted reasoner. From the repo's own e2e
artifacts (`lead-findings/e2e-run-artifacts/`):

```
PLAN     wave 1 (initial): gen-happy [happy_path], gen-approve-twice [idempotency],
                           gen-missing-invoice [missing_data]
EXECUTE  iteration 1: permanent approval_backend PASSED
                      gen-approve-twice FAILED  (payments=2, expected payments=1)
ADAPT    wave 2 (adaptive): gen-concurrent-approval [concurrency],
                            gen-restart-persistence [restart_recovery]
CORRECT  grounded correction sent to builder, citing requirement U-042,
         the risk it was generated for, and the failed assertions
RETEST   iteration 2: all 6 scenarios PASSED
REGRESS  "full required regression set before acceptance", full_run: True
ACCEPT   accepted marker written
PROMOTE  promotion candidate: gen-approve-twice (found the defect, now passes)
```

**Differential result:** the handwritten permanent scenario **passed** while
generated coverage **caught** the defect — the fixed baseline missed it.

**What this proves and does not prove.** It proves the loop — plan → execute →
detect → adapt → correct → rerun → regress → accept → promote — is genuinely
wired and that acceptance follows the evidence. It does **not** prove autonomous
capability: the adaptive wave here was scripted. With the real model, B1 means no
scenarios are produced at all, and B7 means the wave would not track the failure
even if B1 were fixed.

---

## 7. UNSUPPORTED CLAIMS

Claims made in README / docstrings that could not be independently confirmed:

1. *"Every axis is bounded: scenarios per wave…"* — per-wave axes are advisory (B8).
2. *"Generated plans … resume"* / `TestResumePreservesGeneratedState` — no product
   resume path exists (B6).
3. *"a model proposes intent … It never proposes shell"* — falsified by B2.
4. *"Requests must be loopback"* — falsified by B3 for `request.path`.
5. *"nothing skips a stage"* (`scenario_plan.py:8`) — `scenarios run-generated`
   skips `validate_scenario` entirely (r3 F-3).
6. *"Full command output, response bodies … stay on disk under the per-scenario
   evidence directories"* (`prompts.py:460`) — directories are empty (B5).
7. *"the harness enforces that regardless of what you return"* (`prompts.py:483`)
   — not true for skipped required scenarios (B4).
8. *"max_parallel above 1 is refused"* — the executor ignores it; only the config
   validator refuses (r4 F-5).
9. Adaptive expansion "suggests the family around" a failure — not observed with
   the real model (B7).

---

## 8. REMAINING FAILURE MODES

- A run can ACCEPT having executed **nothing**, on default config, via browser
  skips or budget exhaustion.
- Model-authored text can run arbitrary shell (newline) and reach arbitrary
  external hosts (`request.path`).
- The evaluator and builder are pointed at empty evidence directories.
- Uncovered risks are computed but never reach the decision (r6 F-3).
- Wholesale generation collapse (all proposals refused, or B1) is indistinguishable
  from "nothing needed generating" and still permits ACCEPT.
- The model's self-assigned priority is the only lever deciding whether its own
  finding blocks; `required=True` is hardcoded.
- Scenario ids truncated to 64 chars can collide and be silently dropped (r4 F-4).
- Duplicate detection defeated by rewording an expectation (r5 F4).
- Resume escapes `max_waves` and destroys the prior plan record.

---

## 9. NEXT SAFE ACTION

**Smallest concrete next step: fix B1 and B2 — in that order — then re-verify.**

1. **B1** (`scenario_generator.py:495-508`): run `_session` on a dedicated thread
   with its own loop, or make `propose` async and await it. Without this the
   feature does nothing in production, and no other finding can be measured
   through the real path.
2. **B2** (`scenario_validation.py:95-102`): reject control characters before
   normalizing, and split the tail with `shlex` so the composition check is
   quote-aware. This closes the injection *and* unblocks the SQL oracles the
   EFFECT_FAMILY rule requires — which is the precondition for B7 being testable
   at all.

Then re-run this verification. Do **not** ship with `--auto-scenarios` enabled
until B2, B3 and B4 are closed: B4 alone means an enabled run can ACCEPT without
executing anything.

---

## Authority compliance

No guard, gate, budget or safety boundary was weakened. No source file was
modified: `git diff --stat` is identical to the baseline (9 files,
1856 insertions, 121 deletions). All verification output is confined to
`verification-evidence/`. Agent worktrees under `.claude/worktrees/` were created
by the harness and hold reviewer scratch copies.

**Methodology note:** worktree isolation handed reviewers a stale checkout
(commit `bbe0fd2`; the feature's modules are untracked). R2 ran in the live
checkout; R3, R4, R5 and R6 each detected this, synced the real working tree, and
verified with `diff -r --brief` / SHA-256 before running. Their provenance
statements were required and given.

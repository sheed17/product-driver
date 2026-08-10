# Remediation — Dynamic Scenario Generation

Response to the independent verification recorded in `ADJUDICATION.md`
(verdict: *IMPLEMENTED BUT NOT YET VERIFIED CAPABLE*). Written by the
remediation engineer. **This document does not certify the system** — it records
what was changed, how each change was proven, and what remains unproven, so that
a fresh independent session can certify or refuse.

Evidence root: `verification-evidence/remediation/`

Reproduce everything here with:

```
.venv/bin/python verification-evidence/remediation/reproduce_blockers.py   # 9 probes
.venv/bin/python verification-evidence/remediation/run_mutations.py        # 18 mutations
.venv/bin/python verification-evidence/remediation/resume_demo.py          # interrupt/resume
.venv/bin/python verification-evidence/remediation/real_path_demo.py       # live reasoner
.venv/bin/python -m pytest -q                                              # full suite
```

---

## 1. Status

**REMEDIATION COMPLETE — READY FOR INDEPENDENT CERTIFICATION**

- 9 of 9 reproduced blockers now closed (`blocker-status-BEFORE.json` → `blocker-status.json`)
- Mutation testing **18/18 caught**, including the two that survived the audit
- Full suite **1029 passed** (was 950; +79 new contract tests, 0 removed)
- Real-path demonstration run against a live `LLMScenarioReasoner`

Limitations and unproven claims are in §11 and §12 and are not hidden behind the
status line.

---

## 2. Blocker-by-blocker

Each entry: reproduction → root cause → fix → proof.

### B1 — the real reasoner could not run through the control loop

**Reproduced.** `reproduce_blockers.py::probe_b1`, pre-fix:
`scenarios=[] reasoner_error='RuntimeError: Cannot run the event loop while another loop is running'`

**Root cause.** `LLMScenarioReasoner.propose` called `asyncio.run()`, which
refuses inside a running loop; the fallback `new_event_loop().run_until_complete()`
raises the same error, and because it raised *inside* the `except RuntimeError`
handler the trailing `except Exception` could never catch it. Every production
call site is inside `async def run_control_loop`. The planner recorded a wave
that produced nothing, and the run continued as though nothing were wrong.

**Fix.** `scenario_generator.py`
- new `run_coroutine_blocking()` drives the coroutine on a dedicated thread with
  its own loop, which is correct from a sync caller *and* from inside a running
  loop. No nested `asyncio.run`.
- `propose()` no longer swallows exceptions. "The generator failed" and "the
  generator had nothing to add" are different facts; conflating them let a dead
  generator read as a clean bill of health.
- `ScenarioPlanner.generation_problems()` surfaces failed waves, and the
  acceptance gate refuses to accept a run that has one.

**Proof.** Probe now CLOSED. `test_remediation_contract.py::TestRealReasonerPath`
(4 tests) covers the running-loop case, a wave reaching the plan, a failure being
recorded rather than silently empty, and an honestly-empty wave *not* counting as
a problem. Mutations N1 and N2 both caught. The real-path demo generated real
scenarios through this path (§9).

### B2.1 — control-character / shell composition bypass

**Reproduced.** 4 of 12 vectors admitted: newline, carriage return, vertical tab,
form feed. Independently confirmed during the audit by executing a harmless
injected command.

**Root cause.** `_norm_command` collapsed `\s+` → `" "` *before* the composition
regex ran, so the `\n` listed in that regex could never match. `resolve()` then
approved the **raw** string and the compiler compared raw-to-raw.

**Fix.** `scenario_validation.py`
- `_control_character_problem()` refuses any control character against the raw
  string, before normalization touches it. Normalization can no longer hide a
  separator inside what looks like whitespace.
- `scan_shell_operators()` replaces the regex with a positional, quote-aware
  scanner (see B2.2).
- only operators introduced by the *tail* count against a proposal, so a
  human-authored approved entry containing an operator still works.

**Proof.** 12/12 vectors refused. `TestCommandBoundary` parametrises 14 vectors.
Mutation N3 caught.

### B2.2 — legitimate quoted probes were refused

**Reproduced.** 5 of 5 legitimate probes wrongly refused, including
`... HAVING count(*) > 1`, `'DUP:'||key`, `WHERE a < b`, and JSON/regex arguments.

**Root cause.** The same quote-blind regex. This was not cosmetic: `EFFECT_FAMILY`
*requires* a persisted-state oracle for idempotency, retry-safety,
timeout-after-effect, restart-recovery, crash-mid-workflow, partial-failure,
persistence-failure and unexpected-transition — and SQL is how such an oracle is
written. The boundary mechanically guaranteed the highest-value risk categories
could not be expressed.

**Fix.** `scan_shell_operators()` tracks quoting rather than pattern-matching:
nothing expands inside single quotes; `$(` and backticks still substitute inside
double quotes and are treated as operators there; everything composes outside
quotes. Unbalanced quoting is refused rather than guessed at.

**Proof.** 5/5 accepted. `TestCommandBoundary::test_quoted_payloads_are_accepted`
parametrises 5 probes, plus an unbalanced-quoting refusal. Mutation N5 caught —
and N5 is deliberately a *false-negative* mutation: making the scanner quote-blind
again breaks the tests that assert legitimate probes are accepted.

### B2.3 — absolute URL through `request.path`

**Reproduced.** 7 of 7 off-target URLs ungated: absolute http/https,
scheme-relative, userinfo trick, decimal IP, `file:`, non-loopback IPv6.

**Root cause.** `_check_safety` host-checked only `request.url`. With `url` empty
it validated the local `app_url` and never parsed `path` — while
`scenarios._join_url` returns an absolute `path` verbatim and `runner.http_request`
is an unrestricted `urlopen`.

**Fix.** `resolve_http_target()` is now the single place that decides a generated
request's destination, and both validation and the safety re-check call it. A
path carrying a scheme, or starting `//` or `\\`, is refused rather than
normalised — normalising an attack into something safe-looking hides the intent.
Naming both a `url` and a `path` is refused as ambiguous.

**Proof.** 7/7 refused. `TestHttpBoundary` parametrises 9 targets plus the
relative-path happy case and the both-named case. Mutation N4 caught.

### B3 — adaptive generation did not use the failure evidence

**Root cause (two parts).**
1. The generator was handed `ScenarioOutcome.brief()` — the *first* failed
   assertion, truncated, with digit runs normalised to `<n>` by
   `normalize_signal`. That normalisation is right for clustering and wrong for
   generating: it deletes the only fact that says what went wrong. The generator
   was told `payments=<n>` where `payments=2` was observed.
2. Nothing recorded which failure caused which scenario, so the claim that
   verification responded to evidence was unfalsifiable.

**Fix.**
- `scenario_suite.FailureEvidence` + `build_failure_evidence()`: a structured
  brief carrying the scenario id, risk category, requirement, what was expected,
  what was forbidden, **every** failed assertion, a bounded redacted excerpt of
  what the product actually produced, the evidence path, the cluster id and the
  diff files in scope.
- `ScenarioProvenance.source_failures` / `source_clusters` record the causal edge;
  the brief instructs the model to set them; validation refuses an adaptive
  scenario that names none, or that cites a failure this run never observed.
- `expand_after_failures` records the observed failure and cluster ids so the
  citation can be *checked* rather than trusted.

**Proof.** `TestAdaptiveUsesFailureEvidence` (4 tests) asserts the observed value
reaches the brief, that an unlinked adaptive scenario is refused, that an invented
citation is refused, and that the link survives into the persisted plan. Mutations
N6 and N7 caught. Real-path demo shows adaptive scenarios carrying `caused by:`
links (§9).

**Not claimed.** That the *model* now produces materially different coverage per
failure. See §12.

### B4 — required scenario results did not mechanically gate acceptance

**Reproduced.** Two required P0 scenarios, both SKIPPED, zero executed →
`blocking_failures: []`, `full_run: True`, decision stayed **ACCEPT**.

**Root cause.** `blocks_acceptance` counted only FAILED/BLOCKED; `full_run` was
computed from *selection*, not execution; and there were several partial answers
(`blocking_failures`, `everything_required_passed`, `full_run`) with no single
authority. A required scenario with no outcome at all was invisible to all of them.

**Fix.** New module `scenario_gate.py` — one deterministic function.

> A required scenario contributes to acceptance only when it PASSED **and** its
> evidence resolves.

Everything else is *not verified*: failed, blocked, skipped, never executed, no
result recorded, evidence missing or belonging to something else. Supporting
changes:
- `SuiteResult.expected_required_ids` records what the suite set out to verify,
  so a scenario that never ran is a visible gap rather than an absent row.
- `build_suite` sets `required = priority.blocks_acceptance` for generated
  scenarios. `required` now means required; the low-priority judgement still
  lives in the priority the generator assigned, but the flag no longer lies.
- `everything_required_passed` delegates to the gate and is read by nothing on
  the acceptance path, so forcing it True changes nothing.
- `executed_required_all_passed` is a separate, narrower question used only to
  decide whether a narrowed rerun has earned the widening pass.
- Where nothing failed but verification did not happen, the run is **BLOCKED**
  rather than FIX: there is no grounded correction to send a builder, and
  inventing one would send it chasing a defect no evidence describes.

**No approved-skip mechanism was added.** It would have been the obvious way to
keep browser scenarios from blocking, and it is exactly the lever that turns
unavailable verification back into a pass. The underlying cause was fixed instead:
the generator is now told the truth about browser availability
(`browser_enabled` was hardcoded `True` in the brief while the runtime default is
`False`), so it stops proposing coverage the suite will only skip.

**Proof.** `TestAcceptanceContract` — the 15 required false-ACCEPT attempts, all
refused — plus `TestConvenienceFlagsCannotOverrideTheGate`. Mutations M5, M7, M8,
N8 caught.

### B5 — provenance was not enforced

**Reproduced.** A scenario with an entirely empty provenance stamp validated with
zero reasons. Mutation M6 (removing provenance derivation) survived the audit.

**Root cause.** Provenance was recorded and rendered but never checked, and the
tests passed provenance in explicitly, so its *derivation* was never exercised.

**Fix.** `_check_provenance()` refuses a scenario that cannot say which run and
task produced it (`task_hash`), at which stage and wave, from which generation
source (`model`/`session_id`), and for what risk. An unknown stage is refused. An
adaptive scenario must additionally name a real source failure or cluster.

**Deliberate scope decision.** `repository_head` is recorded and rendered but is
*not* a refusal condition: a target that is not a git checkout has no head, and
refusing every scenario there would punish the proposal for its environment.

**Proof.** `TestProvenanceEnforced` — 5 parametrised required-field cases, the
unknown-stage case, derivation from the basis, and a planner-generated scenario
carrying real provenance. Mutation M6 now caught.

### B6 — resume regenerated from wave zero

**Reproduced.** After resume: `scenarios restored: []`, `waves_used: 0`,
`budget_exhausted: False`; `max_waves=2` became 4 across the boundary; a previously
generated scenario was regenerated; and `persist()` overwrote the earlier plan
(`['gen-a','gen-x']` → `['gen-y']`).

**Root cause.** `_make_planner` always constructed a fresh planner and nothing
ever read `scenario-plan.json` back. The repo test that claimed otherwise
reloaded and recompiled the plan *inside the test body*, exercising no product
code.

**Fix.**
- `ScenarioPlanner.restore_from_store()` restores the plan, recompiles each
  scenario against the *current* approved-command set (so a scenario whose
  command is no longer approved does not come back to life), and restores the
  wave counter, budgets and observed failures/clusters.
- `_make_planner` calls it, so resuming a run continues its plan.
- `note_executed()` persists what has run, before the evaluator is consulted.
- A repository that has moved since the plan was made is restored **and flagged**
  in the returned note, rather than papered over.
- Wave evidence files are indexed by position rather than wave number: a wave
  refused before it could run keeps the previous number, so numbering by wave
  silently overwrote each refusal record.

**Proof.** `TestResumePreservesAdaptiveState` (3 tests) and the end-to-end
`resume_demo.py`, which interrupts after an adaptive wave and resumes in a fresh
process with an empty reasoner: scenarios, wave count, budget exhaustion, executed
ids, observed failures and clusters, compiled set, adaptive links and promotion
candidates all match, and re-persisting does not destroy the earlier plan.
Mutation N10 caught.

### B7 — per-case evidence directories were empty

**Reproduced.** A real suite execution produced an evidence directory that was
cited to the evaluator and the builder and contained no files at all.

**Root cause.** `SuiteExecutor` created `scenarios/<id>/` and recorded it as
`evidence_path`, but never wrote the `ScenarioResult` there. Meanwhile
`prompts.py` told the evaluator that full output "stays on disk … and can be read
with Read/Grep".

**Fix.**
- `write_case_evidence()` persists each scenario's own result beside its
  artifacts, stamped with run id, iteration and scenario id, written to a staging
  file and moved into place so an interrupted write cannot leave a half-parsed
  record.
- `verify_case_evidence()` deterministically checks the cited directory exists,
  holds the record, and that the record belongs to *this* scenario, run and
  iteration.
- The executor verifies immediately after execution. **A pass whose evidence does
  not resolve is downgraded to BLOCKED** rather than believed, and the gate treats
  unresolvable evidence as unverified.

**Proof.** `TestEvidenceIntegrity` — evidence is written and verifiable; 4
parametrised damage cases (deleted, empty, corrupt, wrong scenario); wrong-run
rejection; nonexistent path; and a pass downgraded when its evidence disappears.
Real-path demo reports **0 dangling references**. Mutation N9 caught.

### B8 — per-wave budgets were advisory only

**Reproduced.** `max_initial_scenarios=2`, model returned 10, **10 accepted**.

**Root cause.** The allowance was computed and then passed only into the brief —
a request to the model. The admission loop applied only the total and
per-category caps.

**Fix.** The wave allowance is enforced on what came back; the overflow is
recorded as a budget refusal rather than dropped silently.

**Proof.** Probe expects exactly 2 (not "≤ 2", which the previous repo test
asserted and which any number satisfies).
`TestBoundsBind::test_a_wave_cannot_exceed_its_per_wave_limit`.

---

## 3. Additional evidence-authoritative findings closed

Beyond the eight above, from the reviewer evidence:

| Finding | Fix |
|---|---|
| r3 F-3: `scenarios run-generated` skipped validation entirely | The replay path now re-checks safety (`safety_reasons`) before compiling, and its exit code comes from the authoritative gate |
| r4 F-1(a): generator told `browser_enabled=True` while the runtime default is `False` | The brief now carries the truth; browser coverage is not planned for a run that cannot execute it |
| r5 F8: refused waves overwrote each other's evidence file | Wave files indexed by position |
| r2 F1 / r5 F7: undocumented `setup`/`cleanup` caused 80% of all real-model refusals | Both fields documented in the schema and the brief as command lists, with the prose failure mode named explicitly |
| Prose expectations (found during this remediation, §9) | `expected_observations` / `forbidden_observations` documented as literal substring matches, with a worked good/bad example |
| `validate_plan` rebuilt its context field-by-field and silently dropped newly added fields | Replaced with `dataclasses.replace`, so a future field cannot be dropped the same way |

---

## 4. Files changed

Implementation:

| File | What changed |
|---|---|
| `scenario_gate.py` | **new** — the single authoritative acceptance gate |
| `scenario_validation.py` | control-character refusal, quote-aware operator scanner, `resolve_http_target`, `_check_provenance`, `safety_reasons`, context copy fix |
| `scenario_generator.py` | `run_coroutine_blocking`, `propose` no longer swallows, schema/brief documentation, `source_failures`/`source_clusters` parsing |
| `scenario_suite.py` | `FailureEvidence` + `build_failure_evidence`, `write_case_evidence`, `verify_case_evidence`, evidence fields on outcomes, `expected_required_ids`, `executed_required_all_passed`, honest `required` |
| `scenario_planner.py` | `restore_from_store`, `note_executed`, `generation_problems`, per-wave budget enforcement, `browser_enabled`, observed failure/cluster tracking, wave-file indexing |
| `scenario_plan.py` | provenance `source_failures`/`source_clusters`, plan-level observed/executed records |
| `cli.py` | gate wiring, generation problems into precedence, structured failure evidence into expansion, run/iteration stamping, planner restore on resume, replay re-validation |

Tests: `tests/test_remediation_contract.py` (**new**, 79 tests) plus fixture
realism updates in `scenario_fixtures.py`, `test_scenario_e2e.py`,
`test_scenario_generation.py`, `test_scenario_loop.py`, `test_scenario_planner.py`.

**On the fixture changes.** Five existing tests began failing because their
fixtures constructed states the production path cannot produce — generated
scenarios with empty provenance, passing outcomes with no evidence, adaptive
waves with no source failure. Each was updated to the realistic shape, never to a
weaker assertion. Two were *strengthened*: the adaptive test now asserts the
observed value reaches the brief and that provenance links to the failure. No
test was deleted or skipped.

---

## 5. Mutation results — 18/18 caught

The original eight (6/8 at audit time):

| ID | Requirement removed | Before | Now |
|---|---|---|---|
| M1 | adaptive generation disabled | CAUGHT | CAUGHT |
| M2 | duplicate detection always false | CAUGHT | CAUGHT |
| M3 | failed scenarios report PASSED | CAUGHT | CAUGHT |
| M4 | safety validation removed | CAUGHT | CAUGHT |
| M5 | suite precedence bypassed | CAUGHT | CAUGHT |
| M6 | provenance derivation removed | **SURVIVED** | **CAUGHT** |
| M7 | `blocking_failures` always empty | CAUGHT | CAUGHT |
| M8 | `everything_required_passed` forced True | **SURVIVED** | **CAUGHT** |

The ten new boundaries, all CAUGHT: N1 real reasoner never invoked · N2 reasoner
exception silently emptied · N3 control characters accepted · N4 absolute URL
accepted · N5 scanner made quote-blind · N6 adaptive gets no failure evidence ·
N7 adaptive provenance unlinked · N8 required SKIPPED treated as success ·
N9 evidence accepted without artifact · N10 resume regenerates wave zero.

Raw: `remediation/mutation-results.json`.

---

## 6. Acceptance-contract attacks

All 15 required cases refused. `test_remediation_contract.py::TestAcceptanceContract`.
Cases 1-7 and 9-13 are asserted at the gate and, where a run is involved, through
`_apply_suite_precedence`; case 8 drives the full `run_control_loop`; cases 14-15
confirm completion-audit and protocol precedence are unchanged and still ordered
ahead of the suite gate.

---

## 7. Regression

`.venv/bin/python -m pytest -q` → **1029 passed** (950 before + 79 new).

Specifically confirmed unchanged: existing backend and browser scenario files
parse and still use the phase form; a run without a planner executes exactly one
scenario and records no suite; explicit `--scenario` is unaffected; completion
auditing, protocol resolution, investigator triggering, calibration, run-journal
and evidence behaviour all pass their existing suites.

**Dynamic scenario generation remains opt-in.** `scenario_generation.enabled`
still defaults to `False` and `--auto-scenarios` is still the opt-in. Nothing in
this remediation turned it on.

---

## 8. Interruption / resume proof

`remediation/resume_demo.py` → `remediation/resume/`. Generate → execute → fail →
adapt → interrupt (process ends) → resume in a fresh process with a reasoner that
has nothing left to give. Every tracked property matched, and re-persisting did
not destroy the earlier plan.

---

## 9. Real-path demonstration

`remediation/real_path_demo.py` → `remediation/real-path/`.

Real: the scenario reasoner (live `LLMScenarioReasoner` session), the
deterministic validation and compilation pipeline, suite execution against a real
HTTP service running as a real subprocess with two real defects, the evidence, the
gate. Controlled and stated: the builder is scripted (it applies a prepared fix on
receiving a correction) and the evaluator **always ACCEPTs** — deliberately
hostile, so that any refusal to accept comes from the gate rather than from the
evaluator agreeing.

Two defects surfaced only because this ran for real, and both were the same
class as r2's F1 — an undocumented field whose semantics a model cannot infer:

1. **Run 1** — the model wrote *prose* into `expected_observations` ("POST
   returns 200 with status approved"), which the executor matches as a literal
   substring. Ten scenarios failed for reasons that had nothing to do with the
   product. Fixed by documenting the substring semantics with a worked
   good/bad example.
2. **Run 2** — reached ACCEPTED, but the entire adaptive wave was refused
   (8 proposed, 0 accepted): every scenario lacked `generating_risk` and
   `rationale`, which this remediation had just made mandatory while the schema
   still listed them as optional and undescribed. My own new rule was silently
   deleting the adaptive wave. Fixed by making both fields `required` in the
   schema, describing them, and saying so in the brief.

That second one is worth stating plainly: a provenance rule that the generator is
not told about does not produce provenance, it produces silence.

**Run 3 — the full chain, end to end:**

```
TASK        supervised carrier invoice approval, recorded durably, exactly once,
            surviving a restart
GENERATE    wave 1 (initial, live model): 7 proposed, 7 accepted
EXECUTE     iteration 1: 7 generated + 1 permanent → 3 passed, 5 FAILED
DETECT      S02 repeated_request  — probe: 'payments=1' not found
            S03 concurrency       — probe: 'payments=1' not found
            S04 restart_recovery  — GET /: 'INV-4001' not found after restart
            S05 retry_safety      — POST: 'already approved' not found
            S07 boundary          — GET /: 'INV-7001' not found
ADAPT       wave 2 (adaptive): 6 proposed, 6 accepted, each naming its cause
              S08 ← S03, S04     S09 ← S04        S10 ← S02, S04, S07
              S11 ← S03, S07     S12 ← S02, S05   S13 ← S03
REFINE      wave 3 (diff refinement, after the fix landed): 3 accepted
CORRECT     grounded correction reached the builder; the fix was applied
RERUN       narrowed rerun green → widened to the full required regression set
REGRESS     iteration 2: 16 generated + 1 permanent → 17 passed, 0 failed
GATE        scenario gate: VERIFIED — 16/16 required passed with resolvable evidence
EVIDENCE    dangling references: 0
ACCEPT      status ACCEPTED — 0 generation problems
```

The adaptive links are the part that could not be shown before: each wave-2
scenario names the specific wave-1 failures it answers, and validation confirmed
every citation against failures the run actually observed.

Artifacts: `real-path/summary.json`, `real-path/transcript.txt`, and the full run
directory under `real-path/driver/runs/realpath-001/`.

---

## 10. What was changed to keep verification honest

Nothing in this pass weakened repository authority, the command guard, completion
auditing, protocol resolution, human-approval boundaries, or the existing
regression requirement. Two changes *tightened* semantics and are called out
because a certifier should judge them rather than discover them:

1. **`required` now means required.** Generated P2/P3 scenarios are recorded as
   not-required instead of required-but-ignorable. Their failures are reported
   and do not block, exactly as before — but the flag no longer disagrees with
   the gate.
2. **A pass without resolvable evidence becomes BLOCKED.** This can turn a
   previously "green" run amber if an evidence directory is lost. That is the
   intended direction.

No previous commit was modified. Nothing was merged. Nothing was pushed.

---

## 11. Remaining limitations

- **Real-model coverage quality is not certified.** r2 measured, before this
  remediation, that 77% of real proposals were destroyed by validation, that one
  representative task produced zero scenarios, and that 67% of accepted scenarios
  were bare test-suite invocations. The two largest mechanical causes (undocumented
  `setup`/`cleanup`; SQL oracles unexpressible) are fixed, and the prose-expectation
  cause found here is fixed. **Whether that materially improves quality across r2's
  five representative tasks has not been re-measured.**
- **Adaptive responsiveness is not re-measured.** r5's divergence experiment
  (BETWEEN ≈ REPLICATE, CONTROL highest) is the finding that matters, and rerunning
  it needs ~14 live model waves across three seeded defects plus controls. The
  *mechanism* is now demonstrably better — the observed values reach the generator,
  every adaptive scenario must name its cause, and in the real-path run the six
  adaptive scenarios cited the specific failures they answered — but a single run
  is not the statistic. **One run showing plausible links does not establish that
  different failures produce different coverage**, which is exactly what r5
  measured and disproved. Repeating that experiment is the single highest-value
  thing a certifier can do.
- **`max_parallel` remains a dead parameter** on `SuiteExecutor` (r4 F-5). The
  config validator still refuses anything above 1, so no race is reachable; it is
  an honesty defect, untouched here.
- **Uncovered risks still do not reach the evaluator** (r6 F-3). `recompute_coverage`
  computes them and only the terminal sees them. Out of scope for the listed
  blockers; it means the evaluator is asked whether coverage was sufficient without
  being shown the gap.
- **Scenario-id truncation collisions** (r4 F-4) are unfixed: `_safe_id` truncates
  to 64 characters and `ScenarioSuite.add` drops a duplicate silently.
- **`repository_head` is not a provenance refusal condition** (§B5), by decision.
- The **run-journal `AttributeError`** (r1 F7) is pre-existing, unrelated to this
  feature, and untouched.

---

## 12. Not proven

State plainly, for the certifier:

1. That the real model now generates *materially better* coverage. Mechanically
   unblocked; not measured.
2. That adaptive waves now *differ by failure*. The evidence now reaches the
   generator and the causal link is enforced, but r5's divergence measurement has
   not been repeated.
3. That the full chain reaches ACCEPT **with a live builder**. The demonstration
   uses a scripted builder; only the reasoner, execution, evidence and gate are real.
4. That browser-mode generated scenarios work end to end. No Playwright run was
   performed in this pass.
5. Anything about behaviour at the 200-scenario ceiling after these changes; r4's
   scale results predate them.

---

## 13. Next action

Hand this branch to a **fresh** independent certification session. The highest-value
targets for it, in order: re-measure real-model generation quality against r2's five
tasks; repeat r5's divergence experiment; attack the new gate directly; and re-run
r4's scale sweep now that evidence is written per case.

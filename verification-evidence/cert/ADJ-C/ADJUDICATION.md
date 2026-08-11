# ADJUDICATION — ADJUDICATOR-C (acceptance-gate blocking claims)

Recorded verbatim by the campaign controller from ADJUDICATOR-C's return. This
adjudicator did not produce the C-GATE findings and did not fix anything.
Its own harness is `adj_repro.py` / `adj_repro.json` in this directory.

Candidate `537ae0b`. Both claims independently reproduced from source, then cross-checked against the reviewer's scripts. Nothing under `neyma_product_driver/` or `tests/` was touched.

---

## CG-01 — no deterministic gate on the no-planner path

### Verdict: **NONBLOCKING LIMITATION** (conditional — see the recording requirement)

Disagrees with reviewer C-GATE's BLOCKING classification. The mechanism is real and was reproduced exactly; the scope conclusion differs.

**(a) Mechanism.** `run_control_loop` has two mutually exclusive observation branches at `cli.py:329`. On the `planner is None` branch it executes one `Scenario` and sets `suite_result = None` (`cli.py:334`). The deterministic acceptance gate is reached only through `_apply_suite_precedence`, guarded by `if suite_result is not None:` (`cli.py:619`). So on that branch the gate is not merely bypassed — it has no object to operate on, since `evaluate_gate` consumes a `SuiteResult` and none is ever constructed. The single `scenario_result` reaches the evaluator prompt (`cli.py:512`), `should_investigate` (`cli.py:471`) and provenance (`cli.py:529`), and is never compared against the decision anywhere. The evaluator is therefore the sole judge of a scenario it is merely *told about* in prose. Every `scenario_result` occurrence in `cli.py` was re-grepped; the reviewer's finding is confirmed.

**(b) Scope and reachability — where this adjudication parts company with the reviewer.**

- The defect is **not in the dynamic scenario generation system**. It is in the legacy single-scenario path that predates it. Verified against history: `git show dcc649e^1:neyma_product_driver/cli.py` (commit `bbe0fd2`, pre-generation main) contains no gate, no `suite_result`, and no comparison of `scenario_result.passed` to the decision. `scenario_gate.py` was *added* by `39d2a42`. The certified work introduced the gate; it did not remove one. This is not a regression from `537ae0b`.
- **It does not persist when `scenario_generation.enabled: true`.** `_make_planner` (`cli.py:1428-1471`): the only `return None` is `if not requested and not config.scenario_generation.enabled`. With `enabled: true` a `ScenarioPlanner` is constructed unconditionally. `_make_planner` swallows no exceptions and is called outside `cmd_run`'s `try` (`cli.py:1349`), so a construction failure raises out of the command — it can never silently degrade to `planner=None`. Verified live: case **CG01-C** → `enabled=True` yields `ScenarioPlanner`; `enabled=False` with no flag yields `None`. **There is no state in which generation is on and the planner is absent.** The reviewer did not test this and left the possibility open; it is closed.
- **The permanent scenario is fully covered once a planner exists.** `_assemble_suite` (`cli.py:744`) passes it as `permanent=[(scenario.name, scenario)]`, and `build_suite` (`scenario_suite.py:955-967`) admits it as `Origin.PERMANENT`, `Priority.P0`, `required=True` — "a human wrote it down; a failure in it always blocks."
- **There is no permanent suite on the no-planner path that the gate should have been applied to.** Only the one scenario selected by `--scenario`/config is executed. (`scenarios/` holds two files, but `_permanent_scenarios` is consulted only by the planner.) Closing CG-01 would require *constructing* a one-entry `SuiteResult`, not wiring an existing one.
- **The default today is genuinely the ungated path.** The shipped `driver.config.yaml` has no `scenario_generation` block (confirmed mechanically), and `ScenarioGenerationConfig.enabled` defaults `False` (`config.py:148`). The reviewer is right about that.

**(c) Evidence personally reproduced.**

| case | setup | result |
|---|---|---|
| **CG01-A** | no planner, permanent scenario **FAILED**, hostile always-ACCEPT evaluator | `status=ACCEPTED exit=0 scenario_passed=False gate=NOT INVOKED persisted{status:ACCEPTED, final_decision:ACCEPT, accepted/ written:True} coverage_printed=False` |
| **CG01-B** (control) | identical failure, `scenario_generation.enabled=True` | `status=MAX_ITERATIONS exit=12 gate=NOT_VERIFIED unverified=['backend_generic'] final_decision=FIX accepted/ written:False coverage_printed=True` |
| **CG01-C** | `_make_planner` reachability | `enabled=True → ScenarioPlanner`; `enabled=False → None`; shipped config has no block |

Reviewer harness cross-check: `attack_loop_e2e.py` → `[FALSE ACCEPTANCE] B1a`, `[EXPECTED] B1b`. Agrees.

**Why nonblocking.** The certification is of the dynamic scenario generation system. CG-01 is a pre-existing hole in the code path that exists precisely when that system is switched off, and it is fully closed by the configuration the driver will run under going forward. A defect that vanishes when the certified subsystem is enabled is not a defect of the certified subsystem. The founder's clause "nonblocking limitations may remain when explicitly recorded" fits it exactly — and the driver already records it: `doctor` prints `scenario generation: disabled (opt in with --auto-scenarios)`.

**Recording requirement (a hard condition, not a nicety).** The certification must record, and the campaign must enforce:

1. The certificate is **scoped to runs with `scenario_generation.enabled: true`**. It certifies nothing about a run with generation off.
2. The shipped `driver.config.yaml` **must actually carry `scenario_generation: {enabled: true}`** before the driver is next run. Today it does not, so today's default is the uncertified, ungated path. That is a one-line configuration change, not a product change. If the campaign declines to make it, CG-01 flips to blocking, because then the certified configuration is not the one that executes.

**Two corrections to the reviewer.** (i) It labelled CG-01 "the live default path" as *inferred*; it is directly verifiable and was verified — the inference label understated its own confidence. (ii) It **missed a second instance of the same hole, and a worse one**: `cmd_evaluate` (`cli.py:2485-2602`) runs one scenario, hands it to the evaluator, and maps `Decision.ACCEPT → RunStatus.ACCEPTED` at `cli.py:2588-2593` with no reference to `result.passed`, then `return 0` unconditionally at `cli.py:2602` — a FAILED scenario yields `ACCEPTED`/exit 0 with no gate and no `--auto-scenarios` escape, since `cmd_evaluate` never builds a planner at all. Same class, same non-generation scope, same disposition, but the scoping note must name `evaluate` as well as `run`.

**Fix specified (description only).** Make the gate universal rather than suite-conditional: on the `planner is None` branch, wrap the single `ScenarioResult` into a one-entry `SuiteResult` via `build_suite(permanent=[(scenario.name, scenario)])` and the existing `SuiteExecutor`, so `suite_result` is never `None`, `6c` always runs, and `_report_coverage` always fires. Apply the same to `cmd_evaluate`. Separately, correct the stale `_make_planner` docstring, which says generation is off "unless **BOTH** the configuration enables it **and** the run opts in" — the code requires only one.

**Mechanically remediable?** Yes, entirely within the codebase, with no change to product authority or founder governance. The gate is already deterministic and evaluator-independent; this only widens where it is invoked. It would need new tests, since no test in `tests/` covers the failing no-planner case (the nearest, `test_without_a_planner_the_loop_runs_exactly_one_scenario`, uses a *passing* scenario) and no mutation in `run_mutations.py` exercises that branch — which is why a 30/30 mutation score coexists with this hole.

---

## CG-02 — the completion-audit branch terminates before the gate is computed

### Verdict: **UPHELD — BLOCKING**

Agrees with the reviewer, on a narrower and better-supported basis.

**(a) Mechanism.** Step 6b (`cli.py:578-589`) fires when the completion audit blocks acceptance with `REQUIRES_INDEPENDENT_REVIEW` and the evaluator said ACCEPT. That branch does not call `_terminate`; it hand-rolls the persistence (`save_iteration`, `state.final_decision = decision`, `state.status`, `save_state`) and **returns**. Step 6c — `_apply_suite_precedence`, the only invocation of the deterministic gate — sits at `cli.py:619`, after it. The gate is therefore never consulted on this terminal path.

The return also constructs `LoopResult(RunStatus.NEEDS_INDEPENDENT_REVIEW, state, decision, audit)` with **four positional arguments**. `LoopResult` has seven fields (`cli.py:180-191`); positions 5, 6 and 7 — `protocol`, `suite`, `promotion_candidates` — silently default. The reviewer named only `last_suite`; `last_protocol` and the promotion ledger are dropped too. Consequences:

- `_report_coverage` returns immediately on `result.suite is None` (`cli.py:1877`) — **no coverage section, no gate verdict, no scenario plan pointer is printed at all**.
- `_report_outcome` then prints, unconditionally for this status (`cli.py:1920-1938`): *"The implementation stands and **the product evaluation passed**"* — while a required scenario has just FAILED — followed by the instruction to run `python -m neyma_product_driver review --run <id>`.

The ordering is provably the cause: with the audit at `VERIFIED` or `CONTRADICTED` the same run reaches 6c and the gate blocks. Were 6c hoisted above 6b, the gate would have turned ACCEPT into FIX, the `if decision.decision is Decision.ACCEPT` test at `cli.py:580` would be false, and the run would route to FIX/exit 12 instead.

**(b) Scope and reachability.** In scope, squarely: this is the generation path — a suite ran, the gate had a real `SuiteResult`, and the wiring lost it. Reachable in production: `cmd_run` always passes `CompletionAuditor(config.neyma_repo)` (`cli.py:1387`), so the branch is always armed; it needs only an audit at `REQUIRES_INDEPENDENT_REVIEW`, which is the driver's normal state for a unit with independent-review criteria pending — precisely the shipped task's `max_iterations: 8` EP-1 workload. Resume is not an escape: `--resume-run` restarts the loop and re-enters the same branch (`cli.py:1303-1311`).

**What `NEEDS_INDEPENDENT_REVIEW` + exit 14 actually means.** It is a **hold, not a terminal ACCEPT**. `RunStatus.ACCEPTED` is not reached, exit is 14 not 0, `store.save_accepted()` is not called, and no `accepted/record.json` is written — all four verified. The founder criterion this violates is therefore **not** "falsely ACCEPT unverified work". It is **"silently omit required verification"**, which it meets exactly and without qualification: the gate is not invoked, and the one report that would have disclosed the omission is suppressed by the same defect. It also produces an affirmatively false statement of fact on the console.

**Is the persisted `final_decision: ACCEPT` consumed downstream?** Every reader across `neyma_product_driver/` and `tests/` was grepped. `final_decision` is written at `cli.py:243, 586, 2587` and read at exactly one place outside the loop: `cmd_status`, `cli.py:2477-2479`, which **prints** it. No automation, no gate, no finalizer consumes it. **Correction to any reading of the reviewer's wording that implies downstream automation acts on the persisted ACCEPT: it does not.** Its harm is that a human running `status` on this run sees `final decision: ACCEPT`.

**Can that state become green without the gate ever running? Yes — and this is the load-bearing part of the verdict.** Reasoned from source only; `review` was not run, as it launches a live Claude session. `cmd_review` (`cli.py:2907-3031`): it loads the run state but **never reads `state.final_decision`, `record.suite`, or the on-disk `suite-result.json`**. It re-runs the `CompletionAuditor` and the `ProtocolResolver` fresh, then builds `review_prompt` from `unit`, `audit`, `builder_report` and an `evidence_dir` **path string** (`reviewer.py:182-241`) — the prompt contains no scenario-suite section, no outcomes, and no mention that a required scenario failed. It returns `0 if review.verdict == "SUPPORTED" else 20` (`cli.py:3031`). So the entire documented route out of the CG-02 hold is a command with no gate wiring whatsoever, given a prompt that never surfaces the failure, that can exit 0. The gate never runs on either leg.

**(c) Evidence personally reproduced.**

| case | audit | result |
|---|---|---|
| **CG02-attack** | `REQUIRES_INDEPENDENT_REVIEW`, blocks | `status=NEEDS_INDEPENDENT_REVIEW exit=14 final_decision=ACCEPT LoopResult.suite=None LoopResult.protocol=None` · `gate_would_have_said=NOT_VERIFIED (1 unverified: ['backend_generic'])` · `coverage_printed=False` · `outcome_says_"the product evaluation passed"=True` |
| **CG02-control-verified** | `VERIFIED`, no block | `MAX_ITERATIONS exit=12 final_decision=FIX suite present coverage_printed=True` |
| **CG02-control-contradicted** | `CONTRADICTED`, blocks | `MAX_ITERATIONS exit=12 final_decision=FIX suite present coverage_printed=True` |

Both scenarios in the suite (`backend_generic` permanent/P0/required and generated `gen-a`) genuinely executed and genuinely failed under a real `SuiteExecutor`. Reviewer harness cross-check: `attack_precedence_evidence.py` → `[GATE BYPASSED] C1a`, `[EXPECTED] C1b`, `[EXPECTED] C1c`. Agrees.

**One correction, in the defect's favour on evidence and against it on reporting.** The reviewer's framing risks reading as evidence loss. It is not: `record.suite` is persisted by `save_iteration` at `cli.py:584`, and `suite-result.json` was already written to the iteration directory at `cli.py:381-384` before the evaluator was ever consulted. The file exists on disk in the attack case and `NOT_VERIFIED` was re-derived from it. **The evidence survives; what is lost is the gate's application to the decision and the entire disclosure of it.** That is the honest characterisation, and it is still blocking — the run reaches a terminal state, tells the operator the product evaluation passed, and prints nothing that would let them know a required scenario failed.

**(d) Narrowest systemic fix specified (description only; not implemented).** The invariant to establish is: *no terminal state may be recorded before the deterministic gate has been consulted, and no terminal path may construct a `LoopResult` by hand.* Three parts:

1. **Hoist step 6c above step 6b** — apply `_apply_suite_precedence` immediately after the protocol precedence at 6a, so what the suite *measured* is folded into the decision before any layer that *judges claims* combines with it. The existing comment ("deliberately last, so protocol and completion-audit precedence stay exactly as they were") is the assumption that has to be retired; measurement is not a peer of the claim-judging layers, it is their input. One behavioural consequence to state in the change: when both the gate and a `CONTRADICTED` audit would fire, the decision is already FIX by the time 6b's `elif decision.decision is Decision.ACCEPT` is tested, so the audit's richer correction prompt no longer replaces the gate's. Both are FIX, so no acceptance risk, but the audit contradictions should be merged into the gate's `problems` rather than dropped.
2. **Replace the hand-rolled block at `cli.py:582-589` with `return _terminate(RunStatus.NEEDS_INDEPENDENT_REVIEW, decision, record)`.** `_terminate` performs identical persistence and is the only construction site that carries `last_audit`, `last_protocol`, `last_suite` and the promotion ledger. This single substitution removes the dropped-field bug at its root rather than patching one field.
3. **Make `LoopResult` construction keyword-only** (`@dataclass(kw_only=True)` or routing every construction through `_terminate`) so no future terminal can silently truncate the result again. This is the systemic half: the positional-arg drop is a class of bug, not an instance.

Two hardening items specified alongside, because the fix above is incomplete without them: `_report_outcome`'s `NEEDS_INDEPENDENT_REVIEW` text must state the gate verdict rather than asserting "the product evaluation passed" unconditionally; and `cmd_review` must read the run's recorded suite gate and either refuse to launch a reviewer, or surface the unverified required scenarios in `review_prompt`, when it is `NOT_VERIFIED` — otherwise the sole documented exit from this state remains a command that has never heard of the gate.

**Mechanically remediable?** Yes, fully, and without touching product authority or founder governance. All three parts are local edits inside `run_control_loop` and the `LoopResult` dataclass; the gate itself, `evaluate_gate`, `uncovered_required_risks`, the suite executor and the evidence contract are untouched. Nothing about who may decide what changes — the fix only ensures the existing decider is consulted on every terminal path. Regression coverage is the gap to close deliberately: no test asserts that `NEEDS_INDEPENDENT_REVIEW` carries a suite (`tests/test_auditor_integration.py:180` asserts the status alone, on a no-planner run where `suite_result` is `None` and 6c is a no-op), and no mutation in `run_mutations.py` reaches this branch — which is how a 30/30 mutation result and this defect coexist.

---

## Bottom line for the campaign controller

- **CG-01 → NONBLOCKING LIMITATION**, conditional on the two recording requirements above, the second of which (`scenario_generation: enabled: true` in the shipped `driver.config.yaml`) is a prerequisite, not an aspiration. The scoping note must cover `cmd_evaluate` as well as the no-planner branch of `cmd_run`. The reviewer's BLOCKING is overruled: the mechanism is real but sits outside the certified subsystem, is not a regression from it, and is closed by enabling it.
- **CG-02 → UPHELD-BLOCKING**, against the enumerated criterion *"silently omit required verification"* — not against *"falsely ACCEPT"*, which it does not meet. Certification cannot pass with it open.
- The reviewer's core assessment stands: the gate's arithmetic held under 70 hostile attacks including an always-ACCEPT evaluator, and both findings are wiring defects, not computation defects. This adjudicator's reproduction agrees with its harness on every case re-run.

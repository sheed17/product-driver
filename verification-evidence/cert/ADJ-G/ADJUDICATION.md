# ADJUDICATION — ADJUDICATOR-G (scale blocking claims)

Recorded verbatim by the campaign controller from ADJUDICATOR-G's return. This
adjudicator did not produce the G-SCALE findings and did not fix anything. Its own
scripts and raw JSON are in this directory.

Candidate `537ae0b`. Code paths were read first and independent reproductions built with triggers deliberately different from the reviewer's, then the reviewer's probe run as a cross-check. Nothing under `neyma_product_driver/` or `tests/` was touched. The repo's own suite is green (`208 passed` across `test_scenario_planner/test_scenario_generation/test_evidence/test_post_remediation_contract`), so everything below is an untested gap, not a known-failing area.

---

# G-SCALE-01 — **UPHELD-BLOCKING**

### (a) Mechanism

`ScenarioPlanner.restore_from_store` (`scenario_planner.py:588-648`) recompiles every scenario in the persisted plan against the repository as it is *now*. Recompiling is correct — a command that is no longer approved must not come back to life. What is wrong is the disposal:

- line 634 `plan.scenarios.remove(scenario)` — the plan entry is **deleted from the in-memory plan**;
- lines 635-639 the only report is `self.emit(...)`, a `print` (`cli.py:97 def out: print(...)`, no tee to any run log), truncated to the first four ids;
- no `RejectedScenario`, no `WaveRecord`, no note, no field on the plan.

`ScenarioPlanner.generation_problems()` (`scenario_planner.py:228-241`) returns **only** waves carrying `reasoner_error`. The drop therefore never reaches the one channel `cli.py:625` feeds into `evaluate_gate`. `assembly_problems` cannot see it either: `build_suite` is called from `_assemble_suite` (`cli.py:744`) over `planner.plan.scenarios`, which no longer contains the dropped entries, so `expected_required_ids` (`scenario_suite.py:553`) is computed from the survivors. The gate is asked "did everything the suite set out to verify pass?" and the suite set out to verify almost nothing.

Then it gets worse than the reviewer reported. `note_executed()` runs immediately after every suite execution (`cli.py:387`) and calls `persist()`, which rewrites `scenario-plan.json` from the truncated plan (`scenario_planner.py:669`). The reviewer's "persisted plan on disk still lists 200" is an artefact of their probe stopping before a persist; **in the real loop the pre-resume plan is overwritten**. `_report_coverage` then points the reader at that file (`cli.py:1894`), and `scenario_plan.py:24` states the design contract this breaks: *"'why was this situation verified / not verified' is answerable from `runs/<id>/scenario-plan.json` alone."* The only on-disk contradiction left is `scenario-generation/wave-01.json`, whose `accepted_ids` still lists the full set — nothing in the codebase ever compares the two.

### (b) Reachability — ordinary, not sabotage

Real triggers for "a previously-compiling scenario stops compiling", from `compile_to_scenario` (`scenario_plan.py:759-854`) and `_approved_for` (`scenario_planner.py:497`):

1. **The base scenario changed.** `--resume-run` re-loads the scenario from `args.scenario`/config **before** it opens the run (`cli.py:1286`), never from the saved `state.scenario_name` — which it then overwrites at `cli.py:1322`. Omitting `--scenario` on a resume silently falls back to `driver.config.yaml: scenario: backend_generic`. Run with the two files that actually ship: a plan made against `browser_generic` (declares service `site`) resumed against `backend_generic` (declares none) drops **6 of 6**, reason `references service(s) site that the base scenario does not declare` (`raw/adj-g01-shipped.json`). This is a forgotten flag, not an attack.
2. **The approved-command set narrowed.** Removing a `teardown`/`command`/`expect_state` entry from any scenario YAML, deleting a scenario file, or editing `scenario_generation.approved_commands`. Note `_permanent_scenarios` (`cli.py:399-404`) **silently skips a scenario file that fails to load** (`except Exception: continue`), so leaving one YAML malformed between two processes narrows the approved set with no message at all.
3. **A service renamed** in the base scenario — partial drops, which are the dangerous kind, because a partial drop leaves the uncovered-risk net satisfied.

**The only safety net is the risk register**, and it is category-shaped, not scenario-shaped: `uncovered_required_risks` (`scenario_gate.py:83-146`) passes a risk as soon as *one* scenario in its category passed with evidence, and returns `[]` immediately when the plan named no P0/P1 risks (`IdentifiedRisk.severity` defaults to **P2**, which does not block). So the net fires only when a drop empties a whole P0 category, and never for a plan whose risks are all default-severity.

The false ACCEPT was reproduced twice, with different triggers:

- own probe (`raw/adj-g01.json`): base-scenario change, 60 planned → 1, `generation_problems=[]`, `assembly_problems=[]`, gate **VERIFIED 2/2**, hostile always-ACCEPT → **ACCEPT**, plan on disk truncated **60 → 1** after one persist, `wave-01.json` still listing 60;
- the reviewer's `gscale_probes3.py` re-run verbatim: 200 → 1, `silently_dropped=199`, gate **VERIFIED 2/2**, hostile → **ACCEPT**.

### (b′) ADJ-G-01a — a second, strictly worse resume-time loss found while checking reachability

Building the same probe at the **shipped default budgets** (30 total / 6 per category) could not reach the drop path at all, because the plan file was already unreadable:

`EvidenceStore.write_json` redacts through `redact_obj` (`evidence.py:108-113`, `models.py:366-383`), which replaces the value of any dict **key** matching `/api_key|secret|token|password|credential|private_key|authorization/` with the string `"[REDACTED]"`. `CoverageSummary.by_risk_category` is keyed by `RiskCategory` value, and **`authorization` is a shipped enum member** (`scenario_plan.py:66`). One authorization-category scenario in a plan therefore persists as `by_risk_category: {"authorization": "[REDACTED]"}` — a string in an `int` field.

Measured (`raw/adj-g01-authorization.json`), control vs. one authorization scenario:

| | control (idempotency, concurrency) | + one `authorization` scenario |
|---|---|---|
| plan file parses | parses | `ValidationError: coverage_summary.by_risk_category.authorization` |
| restore note | `resumed scenario plan: 2 scenario(s), 1 wave(s) already used` | `""` |
| scenarios after restore | 2 | **0** |
| waves used after restore | 1 | **0** (budget reset) |
| plan on disk after next persist | 2 | **0** |

So on a plan that names authorization — in a supervised **invoice-approval** product, the single most likely category the generator will pick — a resume loses the entire plan, resets the wave budget, and then overwrites the file with an empty plan. This is exactly the regression `restore_from_store`'s own docstring says it exists to prevent. It is equally invisible: `generation_problems=[]`, gate **VERIFIED 1/1**, hostile ACCEPT → **ACCEPT** (`raw/adj-g01-realistic.json`). It also breaks `scenarios replay`, which refuses with "Could not read the scenario plan" (`cli.py:1573-1576`).

**Controller's note:** ADJ-G-01a is the same defect that reviewer E-RESUME reported as R1 and that ADJUDICATOR-E upheld independently. Three independent discoveries, by three sessions using three different methods.

### (c) How this path differs from the clean path

The clean path the other reviewer described was measured (`raw/adj-g01-cleanpath.json`). With a narrowed approved-command list at *plan* time: 6 proposed, **6 rejected**, each with a full reason (`command is not in the approved set: './probe.sh reset'`), persisted machine-readably in `scenario-generation/wave-01.json`, `compiled_ids: []`, and `coverage_summary.uncovered_risks` on disk naming the risk left uncovered.

The honest difference is **not** gate visibility — `generation_problems_seen_by_gate` is `[]` on the clean path too; both paths reach the gate only through the risk register. The differences that matter are:

1. the clean path never *claimed* the coverage — nothing was promised, executed in a prior iteration, or reported to the builder via `_summarize_verification`;
2. the clean path leaves a durable, per-scenario, machine-readable refusal with a reason; the resume path leaves nothing but terminal scrollback;
3. the clean path adds records; the resume path **deletes** them, and the subsequent persist destroys the run's own account of what it had decided to verify.

### (d) Verdict against the standard

Three separate clauses are hit: **lose critical state across resume**, **silently omit required verification**, and — with a hostile always-ACCEPT evaluator reaching ACCEPT on a run whose committed verification collapsed from 60/200/30 to a handful — **falsely ACCEPT unverified work**. Reachable by an operator forgetting a flag, by an ordinary maintenance edit to a scenario file, or (ADJ-G-01a) by the generator choosing the `authorization` category. Not a nonblocking limitation: nothing about it is explicitly recorded.

### (e) Narrowest systemic fix (described, not implemented)

Invariant: *a scenario that the restored plan committed to and that can no longer be executed must reach `evaluate_gate` as a problem, and the plan's record of it must survive.*

1. In `restore_from_store`, stop mutating `plan.scenarios` destructively without a record. Append one `WaveRecord(stage="resume", wave=self._wave, rejected=[RejectedScenario(id=…, reasons=[str(exc)])])` to `plan.waves` (or add an explicit `plan.dropped_on_resume` list). The existing `persist()` then writes it and `plan.render()` shows it, with no new machinery.
2. Extend `ScenarioPlanner.generation_problems()` to emit one line per resume-time drop. That single change is sufficient at the gate: `evaluate_gate` already turns any non-empty `generation_problems` into `NOT_VERIFIED` (`scenario_gate.py:246,309-317`) and `_apply_suite_precedence` already converts an ACCEPT over a blocking verdict into BLOCKED with the problems attached (`cli.py:918-931`). No new authority, no new gate rule.
3. Treat the unparseable-plan branch (line 610-614) the same way — a generation problem, not a silent fresh start — and do not let the next `persist()` overwrite a plan file that could not be read. Without this, fix 2 is bypassed by ADJ-G-01a.
4. Fix the corrupter itself: in `redact_obj`, apply key-based masking only when the value is a `str`, recursing otherwise. That preserves the security property (credentials are strings) and stops it silently corrupting typed persisted state anywhere in the driver.
5. Cheap companion: on `--resume-run` with no `--scenario`, prefer `state.scenario_name`, or record the base scenario name in `GenerationBasis` and refuse/flag a resume whose base differs.

---

# G-SCALE-02 — **UPHELD-BLOCKING** (and the reviewer under-states reachability)

### (a) Mechanism

`sanitize_filename` (`evidence.py:346-356`) folds `[^A-Za-z0-9._-]+` to `-` and *then* calls `shorten_preserving_identity`, whose distinguishing digest is taken over the already-folded string. So the digest cannot separate inputs that folded together, and the function is not injective. Its docstring's claim — "Distinct labels always produce distinct filenames" — is false.

`SuiteExecutor.run` derives the per-case evidence directory from it (`scenario_suite.py:577`) and nothing checks the result for uniqueness. Because `write_case_evidence` → `verify_case_evidence` runs **inline, per scenario, before the next one executes** (`scenario_suite.py:585-609`), the first case verifies its own record, the second then overwrites that record in the same directory and verifies its own, and both outcomes keep `evidence_verified=True`. Nothing re-verifies afterwards on the acceptance path.

There is a second, independent way to reach the same directory: two labels that are *string*-distinct but **filesystem**-identical. `re` keeps `A-Z`, so `GeneratedScenario._identity` (`scenario_plan.py:453-485`) preserves case; validation's duplicate-id check is exact and case-sensitive (`scenario_validation.py:635`); `ScenarioSuite.add` compares ids exactly; and on APFS — the default macOS filesystem, verified case-insensitive on this machine — the two directories are one.

### (b) Reachability — three routes, and one of them needs no operator text at all

| route | what it needs | result measured |
|---|---|---|
| **A** (reviewer's) | a permanent `Scenario.name` containing a space/slash that folds onto a generated id | 2 entries, **0 assembly conflicts**, **1 directory**, both `evidence_verified: true`, gate **VERIFIED 2/2**; post-run re-check: *"the evidence at …/approve-twice belongs to scenario 'approve-twice', not 'approve twice'"* |
| **B** | two **generated** ids differing only in case (`gen-AUTH-01` / `gen-auth-01`) — model output only, no operator input | both admitted, 3 suite entries, **0 assembly conflicts**, 3 string-distinct paths but **2 directories on disk**, all three `evidence_verified: true`, gate **VERIFIED 3/3**, hostile ACCEPT → **ACCEPT**; `gen-AUTH-01`'s directory provably holds `gen-auth-01`'s record |
| **B′** | **one** generated id that case-matches a **shipped** scenario file's `name:` — e.g. `Backend_Generic` vs the shipped `backend_generic` | 2 entries, 0 conflicts, **1 directory**, both verified, gate **VERIFIED 2/2** — the *handwritten P0 regression anchor's* evidence is the one destroyed |

Route B′ is the sharpest: a single model-chosen id, no operator text, no configuration, against the scenario file that ships in this repo, silently overwrites the permanent scenario's evidence while the gate credits it as proven. `Scenario.name` is an unvalidated `str` (`scenarios.py:212`) and `ValidationContext.existing_ids` holds only generated ids (`scenario_planner.py:521`), so neither half is guarded.

Note also why the reviewer's own §1/§6 scale sweeps could not have caught route B: they counted *distinct path strings* (3 here), which is not the same as distinct directories (2 here).

**Controller's note:** routes B and B′ are the same defect reviewer E-RESUME reported as I1 and ADJUDICATOR-E upheld independently.

### (c) Verdict against the standard

"**Lose or misattribute evidence**" is a listed blocking harm and it was reproduced directly: a required scenario's evidence directory contains another scenario's record, and the gate reports both verified. To be precise about what it does *not* do: outcomes are computed in memory, so a genuinely FAILED scenario still reads FAILED — this does not fabricate a pass. The harm is that the evidence acceptance rests on is destroyed and misattributed while the gate says it resolves, and (routes A/B) a hostile ACCEPT survives on it. That is blocking, and it is not "explicitly recorded" anywhere.

### (d) Where this adjudication disagrees with the reviewer

The reviewer classified G-SCALE-02 as blocking but volunteered that "a reader weighting reachability may downgrade it", on the grounds that it needs an operator-chosen permanent name with a space or slash. **That caveat should be withdrawn.** Routes B and B′ need no operator-chosen name, no unusual configuration, and no edit to any shipped file — only the driver's own generated ids and the platform's default filesystem. Reachability is materially higher than stated, and a downgrade on reachability grounds is not available.

### (e) Narrowest systemic fix (described, not implemented)

Invariant: *the map `scenario_id → evidence directory` must be injective as the filesystem compares names.*

1. `sanitize_filename`: take the distinguishing digest over the **original** input, not the cleaned one, and append it whenever `cleaned != value` **or** `cleaned != cleaned.casefold()`. Correct the docstring, which currently asserts a property the function does not have.
2. `build_suite`'s `admit` (`scenario_suite.py:947-953`): refuse an entry whose id collides **case-insensitively** with one already admitted, recording it in `assembly_conflicts`. That is an existing, already gate-visible channel (`scenario_suite.py:554` → `scenario_gate.py:251`), so route B/B′ closes with no new mechanism.
3. `SuiteExecutor.run`: keep the set of casefolded directory names used in this suite; a second entry mapping onto one already used is an evidence problem — append it to `result.assembly_problems` and refuse to mark the earlier case `evidence_verified`. This is the belt-and-braces layer that holds even if 1 and 2 are ever regressed.
4. Add a `Scenario.name` validator restricting permanent names to `[A-Za-z0-9._-]`, which closes route A at its source. (`ValidationContext.existing_ids` should also be seeded with permanent scenario names, casefolded.)

---

## Mechanical remediability

**Both are mechanically remediable within this codebase without touching product authority or founder governance.**

- G-SCALE-01: every element is recording plus one additional producer for an input the gate already consumes. `evaluate_gate`'s rule is unchanged, `_apply_suite_precedence`'s precedence order is unchanged, no command becomes executable that was not already approved, and the `redact_obj` change strengthens rather than weakens the redaction contract (secrets are strings; typed fields are not). Contained to `scenario_planner.py`, `models.py`, and optionally one line of resume argument handling in `cli.py`.
- G-SCALE-02: contained to `evidence.sanitize_filename`, `scenario_suite.build_suite` / `SuiteExecutor.run`, and one pydantic validator in `scenarios.py`. It reuses the existing `assembly_problems` channel, so no new gate authority is created.

One caution for whoever implements G-SCALE-01: fixes 1-2 alone are insufficient while ADJ-G-01a stands, because an unreadable plan takes the `return ""` branch at `scenario_planner.py:610-614` and never reaches the drop loop at all. Fixes 3 and 4 are part of the same defect, not follow-on polish.

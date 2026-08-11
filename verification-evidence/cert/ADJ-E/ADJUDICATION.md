# ADJUDICATION — ADJUDICATOR-E (resume/recovery + scenario identity blocking claims)

Recorded verbatim by the campaign controller from ADJUDICATOR-E's return. This
adjudicator did not produce the E-RESUME findings and did not fix anything.
Its own harnesses are `adj_r1.py`, `adj_r2.py`/`.json`, `adj_i1.py`/`.json` and
`xcheck_*` in this directory.

Candidate `537ae0b`. Code paths (`models.redact_obj`, `evidence.EvidenceStore.write_json`, `scenario_plan.CoverageSummary`/`GeneratedScenarioPlan`, `scenario_planner.persist`/`restore_from_store`, `scenario_suite.SuiteExecutor.run`/`write_case_evidence`/`verify_case_evidence`/`build_suite`, `scenario_gate.evaluate_gate`, `cli.run_control_loop`/`_make_planner`) were read first and three independent harnesses written before the reviewer's were run. Nothing under `neyma_product_driver/` or `tests/` was touched; no git history command was run.

**Verdicts: R1 UPHELD-BLOCKING · R2 UPHELD-BLOCKING (distinct from R1) · I1 UPHELD-BLOCKING.**

---

## R1 — UPHELD-BLOCKING

**(a) Mechanism.** `redact_obj` (`models.py:366-383`) masks a dict entry whenever the *key* matches `(?i)(api[_-]?key|secret|token|password|passwd|credential|private[_-]?key|authorization)` — it replaces the value with the string `"[REDACTED]"` without regard to the value's type. `EvidenceStore.write_json` (`evidence.py:108-113`) pipes every payload through it. `GeneratedScenarioPlan.recompute_coverage` (`scenario_plan.py:695-709`) builds `CoverageSummary.by_risk_category`, a `dict[str, int]` keyed by `RiskCategory.value`; `RiskCategory.AUTHORIZATION == "authorization"`. So the integer count is persisted as a string. `CoverageSummary` is `extra="forbid"` with `by_risk_category: dict[str, int]`, so `GeneratedScenarioPlan.model_validate_json` raises `int_parsing`, and `restore_from_store` (`scenario_planner.py:608-614`) catches every exception, emits one line, and returns `""` — the same value it returns when there is no plan at all.

This is a type confusion, not a redaction policy question: a scalar count cannot be a credential, but the masker only looks at the key.

**(b) Reachability — exact.** No non-default flag, no hand-edited file, no privileged actor. The only precondition is that the dynamic scenario system be switched on at all (`scenario_generation.enabled` is `false` by default — that is the opt-in for the entire feature under certification, not a special setting for this bug). Given that:

- `authorization` is one of the 27 categories the generator is explicitly offered in `GENERATOR_SYSTEM` (`scenario_generator.py:488-494`), it is a member of the safety risk family (`scenario_plan.py:129-136`) and of the failure-clustering family (`failure_clustering.py:256-261`), and it is the central category of a freight-operations authority product. Nothing biases the generator away from it; `max_scenarios_per_risk_category` defaults to 6.
- `by_risk_category` is not optional or occasional. `recompute_coverage()` runs on *every* `_admit` (`scenario_planner.py:494`) and every `_link_risks` (`:533`), and `persist()` dumps the whole plan. Any plan holding ≥1 admitted scenario has a populated `by_risk_category`; one authorization scenario is sufficient and necessary.
- Blast radius verified mechanically to be exactly one category: of 27 `RiskCategory` values, `authorization` is the **only** one masked as a dict key, and the only one fatal to re-parse. Sweeping a fully-populated plan (all 27 scenarios, headers, bodies, rejected raw payloads), redaction touches four kinds of path — `Authorization`/`X-Token` headers and `password` bodies (intended, value is a `str`, harmless to parse), `RejectedScenario.raw.*` (typed `Any`, harmless), and `coverage_summary.by_risk_category.authorization` (fatal).

Two consequences follow, one of which the reviewer did not name:

1. On resume the entire plan is lost (below).
2. **Even without resume**, the persisted record is falsified — `"authorization": "[REDACTED]"` where a count belongs — and `cli.py:1565-1575` (`scenarios replay`) re-reads the same file and hard-errors `Could not read the scenario plan`. A run that exercised authorization boundaries produces a plan artifact no tool and no human can read correctly.

**(c) Evidence personally reproduced** (`adj_r1.py`, `adj_r2.py` §A):

- `risk_categories_masked_as_dict_keys: ["authorization"]` out of 27.
- Persisted: `{"authorization": "[REDACTED]"}`; re-read → `ValidationError … coverage_summary.by_risk_category.authorization … int_parsing … input_value='[REDACTED]'`. Controls `boundary`, `cross_tenant`, `approval_required` all round-trip clean.
- Through the real `ScenarioPlanner`: clean plan restores `waves_used 1`, scenario list and `executed_scenario_ids` intact, file stays 2 592 B. Authorization plan → emission `could not restore the scenario plan (ValidationError); this run will generate a new one`, `waves_used 0`, zero scenarios, and the next `persist()` shrinks the record from 2 629 B to 726 B.
- Cross-check: reviewer's `resume_probe2.py` `A_baseline_authorization` reproduces identically.
- The damage does **not** extend to `state.json`: `IterationRecord.suite` is `dict[str, Any]`, so an `"authorization"` bucket there is silently replaced by `"[REDACTED]"` (the record is falsified for a reader) but `load_state()` still succeeds. The reviewer did not check this; recorded as a nonblocking evidence-fidelity residue.

**(d) Narrowest systemic fix (described, not implemented).** Two parts, both inside `neyma_product_driver/`:

1. **Make `redact_obj` type-preserving.** When a key matches the secret pattern, mask string values and recursively mask string leaves inside container values, but leave `int`/`float`/`bool`/`None` scalars untouched. A non-string scalar cannot carry a credential, so this loses no security property, and it closes the whole class — every present and future int-valued key that happens to collide with the pattern, not just this one.
2. **Give `persist()` a round-trip guarantee.** After redaction and before the bytes hit disk, re-validate the payload back through `GeneratedScenarioPlan`; if it does not parse, refuse the write and surface it loudly. This is the systemic half: it makes "a write-time transform silently rendered the run's state record unreadable" impossible to recur through some *other* transform.

The tempting narrower variant is rejected — relaxing `by_risk_category` to `dict[str, int | str]`, or dropping derived coverage from the persisted plan. `restore_from_store` already recomputes coverage at `:640`, so that would restore parseability, but it would also make the driver accept a falsified coverage record as normal, which is the wrong lesson.

**Mechanically remediable?** Yes. Both parts are local changes to `models.py` and `scenario_planner.py`. No change to product authority, the approved-command set, the gate, or founder governance.

---

## R2 — UPHELD-BLOCKING, and **distinct from R1**. Fixing R1 alone does *not* close it.

**(a) Mechanism.** `restore_from_store` collapses three genuinely different states into one return value:

| Actual state | Returned |
|---|---|
| no plan file — nothing to resume | `""` |
| plan file present but unreadable — state exists and is inaccessible | `""` |
| plan restored | a note |

`cli._make_planner` (`cli.py:1470`) discards the return value entirely, and `run_control_loop` calls `plan_initial` unconditionally at `cli.py:276`. With `self.plan` still the empty default and `self._wave` still `0`, `_generate`'s guard `if self._wave >= self.config.max_waves` (`scenario_planner.py:347`) does not fire, a fresh wave is granted, and `_finish_wave` → `persist()` (`:550`, `:669`) writes the blank plan over the file that still held the only machine-readable record of what the run had decided. The second state is the dangerous one, and the code cannot see it.

**(b) Reachability — the crux.** The reviewer treated R2 as R1's consequence plus a hand-truncation demo. That understates it. R2 has a real trigger independent of R1:

- **`scenario-plan.json` is written non-atomically.** `write_json` uses `Path.write_text` — truncate-then-write. Directly adjacent in the same subsystem, `write_case_evidence` (`scenario_suite.py:752-757`) already does the right thing: `.result.json.partial` then `.replace()`. The plan does not. Confirmed empirically rather than by reading: a concurrent reader watching a real `persist()` loop over a 2.4 MB plan logged **21 unparseable reads in 6 597**, sizes ranging `0 … 2 449 623`, and **no staging file ever appeared** in the run directory. So a crash, `SIGKILL`, or power loss during a `persist()` leaves a truncated plan — and a crash mid-run is precisely the event resume exists to survive.
- A truncated plan produces the *identical* fail-open with R1 entirely out of the picture (authorization scenario removed from the plan): prefixes at 10 %, 50 %, 90 % and 99.9 % all give `could not restore the scenario plan (ValidationError)`, `waves_used 0`, zero scenarios.
- A third path exists on top: `GeneratedScenarioPlan` is `extra="forbid"`, so any driver-version skew that adds a plan field makes every older run's plan unreadable on resume by the newer binary.

Honest limit, as the reviewer also stated: a real SIGKILL was **not** landed inside the write window, and no such claim is made. The window's existence is measured; the per-run probability of landing in it is low. What is *not* probabilistic is the response once you are there.

**(c) Evidence personally reproduced** (`adj_r2.py` §C, §D, §E):

- `D_atomicity_probe: {reads: 6597, unparseable_reads: 21, size_range: [0, 2449623], staging_file_used_for_plan: false}`.
- `C_truncated_plan_restore`: all four truncation fractions → fail-open, `waves_used 0`, `scenarios 0`.
- `max_waves=1` budget escape, all three arms through the real planner:
  - clean → `waves_used_at_restore: 1`, `budget_exhausted_at_restore: true`, second wave recorded as `refused: 1 generation wave(s) already used`, plan grows 2 556 → 3 286 B, `gen-a` intact.
  - authorization → `waves_used_at_restore: 0`, `budget_exhausted: false`, a **fresh wave 1 granted with no refusal note**, plan 2 577 → **1 399 B**, scenario list empty.
  - **truncated (no authorization anywhere)** → byte-for-byte the same outcome: `0`, `false`, fresh wave, 2 556 → **1 399 B**, empty. This third arm is the load-bearing one: it is R2 with R1 excluded.
- Mitigations confirmed rather than assumed: `scenario-generation/wave-NN.json` files survive the overwrite (the per-wave human record is not destroyed); the failure is **announced**, not silent; and there is **no false accept** — `previous_suite` is a `run_control_loop` local initialised to `None` (`cli.py:285`), so a fresh process re-runs the full suite and the gate re-establishes evidence from scratch. `executed_scenario_ids` is written by `note_executed` and read by *nothing* that makes a decision, so its loss is record loss, not control-flow loss. The reviewer listed it among lost state, which is true, but it does not contribute to a false accept.

**(d) Is it distinct?** Yes — the substantive disagreement with the reviewer's framing. R1 is *why the plan becomes unreadable*; R2 is *what the system does about an unreadable plan*. Different code, different modules, different fixes. Fixing `redact_obj` removes the routine trigger and leaves the fail-open response fully intact and still reachable through the non-atomic write. Conversely, fixing the fail-open response would contain R1's damage without fixing R1.

Against the founder standard, R2 on its own hits **"lose critical state across resume"** — and not merely loses it: it overwrites the last durable copy, and it returns a wave allowance the run had already spent, so generation is unbounded across repeated resumes. It also means the resumed run's gate is applied to a *smaller, different* required set than the run itself had already determined was necessary. It does not reach "falsely ACCEPT unverified work" and it is not silent, which is why it ranks below R1 in severity while still being upheld.

**(e) Narrowest systemic fix.** Three parts, in increasing order of importance:

1. **Make `persist()` atomic**, exactly as `write_case_evidence` already is: write `.scenario-plan.json.partial`, then `Path.replace()`. Same module, two lines, and it removes the only known non-R1 trigger. (This is the reviewer's R3, which they classed nonblocking. R3 is nonblocking *as a standalone*; it is not therefore unimportant — it is load-bearing for R2's reachability.)
2. **Make `restore_from_store` distinguish "no plan" from "unreadable plan"** — a tri-state return or a dedicated `PlanRestoreError`. The single `""` return is the actual defect.
3. **Fail closed on "unreadable plan."** Preserve the unreadable file by renaming it `scenario-plan.corrupt-<ts>.json` so `persist()` cannot destroy it; reconstruct the spent wave counter from the surviving `scenario-generation/wave-NN.json` records so the allowance still binds; and if prior decisions cannot be re-established, halt the run with an explicit blocked status rather than quietly restarting at wave 0.

**Mechanically remediable?** Yes — all three parts live in `scenario_planner.py`/`evidence.py` plus one call-site change in `cli._make_planner` to consume the new signal. No authority or governance change.

**Note for the controller:** if the atomic-write fix (1) lands *and* R1 is fixed, R2's remaining known triggers reduce to version skew. A controller could then defensibly re-grade R2 to a nonblocking limitation. Not done here, because on the candidate as submitted the trigger is live and the response is reproducible.

---

## I1 — UPHELD-BLOCKING

**(a) Mechanism.** Scenario identity is **case-sensitive in memory and case-insensitive on disk**, and nothing reconciles the two.

In memory: `GeneratedScenario._identity` (`scenario_plan.py:451-480`) sanitises to `[A-Za-z0-9._-]` and is case-preserving; `ValidationContext.existing_ids` is a plain `set[str]` compared with `in` (`scenario_validation.py:635`); `ScenarioSuite.add`/`by_id` compare with `==` (`scenario_suite.py:342-364`); the outcome map and the gate's `by_id` likewise. The whole identity path was grepped — there is no `casefold()` and no `.lower()` anywhere in `evidence.py`, `scenario_suite.py`, or `scenario_plan.py`'s identity code. So `gen-clash` and `Gen-Clash` are two distinct required scenarios everywhere in memory.

On disk: the evidence directory is `artifact_root/"scenarios"/sanitize_filename(entry.scenario_id)` (`scenario_suite.py:577`). Both this repository's filesystem and `$TMPDIR` were confirmed case-insensitive (APFS default on macOS; NTFS on Windows behaves the same). Two ids, one directory.

The timing is what makes it invisible: `verify_case_evidence` runs immediately after each scenario's own `write_case_evidence` (`scenario_suite.py:589-608`), inside the per-scenario loop. The first scenario verifies its own record and stamps `evidence_verified = True`; the second then overwrites `result.json` in the same directory. `evaluate_gate` (`scenario_gate.py:286`) reads the recorded `evidence_verified` flag and never re-checks the filesystem at accept time. So the run accepts on a flag that was true when it was set and is false by the time it is used.

**(b) Reachability — exact.** No non-default flag, no hand-edited file, no privileged actor. It is purely a function of the string the generator writes into `id`. The generator contract was checked: the JSON schema is `"id": {"type": "string"}` (`scenario_generator.py:311`) and `GENERATOR_SYSTEM` contains **no guidance whatsoever** on id shape — not a format, not a convention, not "lowercase". Nothing downstream normalises it.

What is actually required is narrower than "the model emits two case-variant ids":

- The two scenarios must also have **different coverage signatures**, since `GeneratedScenario.signature()` is content-based (`scenario_plan.py:547-565`) and identical content would be caught by the existing signature dedupe. Different content + ids differing only in case is the exact trigger.
- Within a single wave, a competent model emitting that is unlikely. Across waves it is more plausible: the model is shown prior ids through `summary()` (`[P1 authorization] gen-auth-01: …`) in `existing_coverage`, and re-casing when proposing a follow-up is an ordinary model slip.
- **A second, more plausible variant the reviewer did not identify:** a generated id that differs only in case from a **permanent scenario's name**. `_assemble_suite` (`cli.py:744-745`) uses `scenario.name` verbatim as the permanent id, and `existing_ids` in validation contains *only* plan scenarios — permanent names are never compared against generated ids at all. A permanent scenario named `Invoice Sync` sanitises to `Invoice-Sync`; a generated `invoice-sync` collides on disk. The generator is literally shown those names via `GenerationBasis.existing_scenarios`.

The decisive point is the verified asymmetry: an **exact-case** duplicate is caught — `ScenarioSuite.add` refuses it, `assembly_conflicts` → `SuiteResult.assembly_problems` → `evaluate_gate` returns `NOT_VERIFIED` quoting the conflict. A **case-differing** duplicate is caught by nothing and collides on disk. The guard exists, works, and is one `casefold()` short of covering this. That is what makes it a defect rather than a theoretical hazard — and it is the very collapse `shorten_preserving_identity`'s own docstring says it exists to prevent ("the suite, the evidence directory and the acceptance gate all agreed there had only ever been one").

**(c) Evidence personally reproduced** (`adj_i1.py`, real `SuiteExecutor` + real `ScenarioExecutor` running local `echo` only + real `build_suite` + real `evaluate_gate`), five arms:

| arm | suite entries | conflicts | dirs on disk | surviving `result.json` | gate |
|---|---|---|---|---|---|
| control, distinct ids | 2 | 0 | 2 | correct for each | VERIFIED 2/2 |
| exact duplicate id | 1 | **1** | 1 | correct | **NOT_VERIFIED** |
| **case-only, both pass** | 2 | **0** | **1** (`Gen-Clash`) | `scenario_id: "gen-clash"` | **VERIFIED 2/2** |
| case-only, `gen-clash` fails | 2 | 0 | 1 (`Gen-Clash`) | `"gen-clash"` — the **failing** one | NOT_VERIFIED 1/2 |
| case-only, `Gen-Clash` fails | 2 | 0 | 1 (`Gen-Clash`) | `"gen-clash"` — the **passing** one | NOT_VERIFIED 1/2 |

In every case-only arm, **both** outcomes carry `evidence_verified: true` and cite two distinct-looking paths, while one of those paths does not exist and the other holds a record belonging to the other scenario. The reviewer's asymmetric claim is confirmed exactly: in arm 4, `Gen-Clash` is reported `PASSED` + `evidence_verified: true` while the only record in `Gen-Clash/` is the *failing* `gen-clash`'s. Cross-check of the reviewer's `identity_execution.py` reproduces the same (`case_only`: gate `VERIFIED`, one `result.json`, `scenario_id: "gen-case-probe"` under `Gen-Case-Probe/`).

**One correction to the reviewer, in their favour on substance:** their table reports `distinct_evidence_dirs: 2` alongside `directories_on_disk: ["Gen-Clash"]`. The first number is the count of distinct *cited path strings*, not directories — it is the misattribution, not a contradiction of it.

**Against the standard**, this hits three named criteria at once: **"lose or misattribute evidence"** (one scenario's `result.json` is destroyed and its verification claim points at another scenario's record), **"collapse scenario identities"** (two required scenarios, one evidence identity), and in the both-pass arm **"falsely ACCEPT unverified work"** in the operative sense — the gate's stated basis is "passed *with resolvable evidence*", and at the moment it says VERIFIED that basis is false for one of the two required scenarios.

**(d) Narrowest systemic fix.** Two parts:

1. **Detect at admission, on the filesystem key.** `ScenarioSuite.add` and `ValidationContext.existing_ids` should compare on `sanitize_filename(id)` normalised with `casefold()` and Unicode NFC — the key the filesystem actually uses — rather than the raw id, and the compared set must include the permanent scenario names, which it currently does not. A collision then becomes an `assembly_conflicts` entry and flows down the **already-proven** refusal channel to `NOT_VERIFIED`. This is the narrowest fix precisely because it reuses a path verified working in the `exact_duplicate_id` control.
2. **Backstop at the directory, so correctness does not depend on modelling the filesystem.** Before writing, `SuiteExecutor.run` should refuse an evidence directory that already holds a `result.json` from this run+iteration belonging to a different `scenario_id` — the check `verify_case_evidence` already implements, applied *before* the write instead of only after — and mark the scenario `BLOCKED` with "its evidence directory is already occupied by `<other>`" rather than overwriting.

Both are specified. Part 1 alone assumes the filesystem's equivalence relation has been correctly enumerated (case today; Unicode NFD/NFC on some volumes, trailing dots and reserved names on Windows tomorrow). Part 2 makes no such assumption — it observes the actual collision — and as a side effect also constrains the reviewer's I3 (`.` / `..` relocation), which sits on the same directory-derivation line.

**Mechanically remediable?** Yes. Both parts are confined to `scenario_suite.py` and `scenario_validation.py`. No change to the approved-command set, the executor's authority, the gate's rule, or founder governance.

---

## Where this adjudication differs from the reviewer

- **R2 is understated as a defect and its reachability is under-argued.** The reviewer presented it largely as R1's downstream effect, evidenced by hand-truncation, and separately filed the non-atomic write as nonblocking R3. Those are the same story: the non-atomic write is R2's independent trigger. The window was measured directly (21/6 597 unparseable reads, no staging file, while `write_case_evidence` two hundred lines away does stage-and-replace) and the full fail-open reproduced from a truncated plan with authorization removed. **The controller's operative question — would fixing R1 alone close R2 — is answered: no.**
- **I1's reachability was asserted rather than established.** The reviewer showed the consequence convincingly but did not check the generator contract. There is no id-format guidance in the prompt at all, and no case normalisation anywhere in the pipeline. A path the reviewer missed was also found — generated-id vs *permanent-scenario-name* case collision, which validation never compares — along with the exact-vs-case-differing asymmetry, which is the strongest argument that this is an incomplete guard rather than a hypothetical.
- **R1's blast radius was understated in one direction and correctly bounded in another.** The reviewer did not report that `scenarios replay` (`cli.py:1565-1575`) also hard-fails on the same file, so the damage is not confined to resume. Their implicit bound in the other direction is confirmed: `authorization` really is the only one of 27 categories that is fatal, and `state.json` survives (its `suite` field is `dict[str, Any]`), though its authorization bucket is silently falsified — a nonblocking evidence-fidelity residue worth recording.
- **One small correction against the reviewer:** their `distinct_evidence_dirs: 2` in the I1 rows counts cited path strings, not directories on disk. The substance is unaffected.
- Everything else in their FINDINGS.md touched incidentally — I8 (duplicate admission blocks acceptance), I5 (long prefixes stay distinct), R6 (wave counter inflates conservatively, `waves_used_on_second_resume: 2`) — reproduced as described.

## Bottom line

**All three claims UPHELD as blocking.** R1 and I1 are independently blocking on their own reachability. R2 is a **distinct** defect from R1, not a consequence of it, and survives R1's fix. All three fixes are mechanically remediable within this codebase without touching product authority or founder governance; none requires a design decision the founder has not already made.

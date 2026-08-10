# Post-Dynamic-Generation Maintenance — Residuals Closed and Re-Measured

Response to the residuals recorded in `REMEDIATION.md` §11 and §12, which the
remediation engineer left explicitly open for a certifier. Written by the
maintenance builder.

**This report is NOT certification.** It records what was measured, what was
changed, how each change was proven, and what remains unproven, so that a fresh
independent session can certify or refuse. Nothing here is a claim that the
system is fit to trust, and nothing here was reviewed by anyone but its author.

Evidence root: `verification-evidence/post-remediation/`

---

## 1. Starting state

| | |
|---|---|
| Repository | `/Users/sammyfammy/neyma-product-driver` (the Product Driver itself) |
| Branch | `main` |
| Starting HEAD | `dcc649e6382e689413217e736a47c4e1739145b3` — *Merge: dynamic scenario generation, verified and remediated* |
| `origin/main` | `dcc649e` — identical; nothing to reconcile |
| Working tree | clean, except one untracked directory: `.driver-state/` |
| Baseline suite at `dcc649e` | **1029 passed** |

**Reproduce everything in this report with:**

```
# the four reproductions, against dcc649e and against the candidate
for r in c-uncovered-risk d-id-collision e-max-parallel f-run-journal; do
  .venv/bin/python verification-evidence/post-remediation/$r/reproduce.py --driver .
done

# 30 mutations
.venv/bin/python verification-evidence/post-remediation/run_mutations.py

# generation quality: 6 live model waves over r2's five tasks
.venv/bin/python verification-evidence/post-remediation/a-generation-quality/run_generation.py <task> --driver . --out <dir>
.venv/bin/python verification-evidence/post-remediation/a-generation-quality/analyze.py --dir <dir>

# adaptive divergence: 10 live runs (3 defects x 3 replicates + control)
verification-evidence/post-remediation/b-adaptive/run_all.sh . <work> <out>
.venv/bin/python verification-evidence/post-remediation/b-adaptive/analyze.py --root <out>

# browser end to end, scale sweep, live builder
.venv/bin/python verification-evidence/post-remediation/g-browser/run_browser_e2e.py --driver . --work <w> --out <o> --real
.venv/bin/python verification-evidence/post-remediation/h-scale/run_scale.py 10 50 100 200 --driver . --target <t> --out <o>
.venv/bin/python verification-evidence/post-remediation/i-live-builder/run_live_builder.py --driver . --work <w>

# the full suite
.venv/bin/python -m pytest -q
```

**On `.driver-state/`.** Untracked, not git-ignored, ~560 KB of handoff records
from earlier P4/P5 driver runs. It was not created by this pass and conflicts
with nothing. It was left exactly as found — not committed, not deleted, not
modified. It is named here because a certifier will see it in `git status` and is
entitled to know it predates this work. No local state needed reconciling, so
nothing was reconciled.

The generation-quality campaign in §4A was run twice: once against a **pinned git
worktree at `dcc649e`**, so the "before" measurement describes the merged
baseline rather than a tree changing underneath it, and once against the
candidate. Everything else was run against the working tree.

---

## 2. Final candidate

| | |
|---|---|
| Starting HEAD | `dcc649e6382e689413217e736a47c4e1739145b3` |
| **Final candidate** | **the single commit on `main` whose parent is `dcc649e`** — `git rev-parse main` |
| Commit subject | *Close the residuals the dynamic-generation remediation left open* |
| Branch | `main`, exactly one commit ahead of `origin/main` |
| Pushed | **No.** Nothing was pushed, and no published history was rewritten |
| Neyma repository | **Not modified.** Read by the read-only reasoner during §4A; `git status` there is unchanged |

*The candidate is identified by its parent rather than by its own hash on
purpose. This report lives inside the commit it describes, so writing the hash
in here and amending to record it changes the hash — the identifier would be
wrong the moment it was written. `dcc649e` is stable, and one commit sits on top
of it.*

**Exact files changed — 12 source files, 1 new test file, and evidence.**

```
 README.md                                  |   12 +-
 driver.config.example.yaml                 |    6 +-
 neyma_product_driver/cli.py                |   92 ++-
 neyma_product_driver/config.py             |   14 +-
 neyma_product_driver/evidence.py           |   42 +-
 neyma_product_driver/models.py             |   29 +-
 neyma_product_driver/prompts.py            |   19 +
 neyma_product_driver/scenario_gate.py      |  130 ++-
 neyma_product_driver/scenario_generator.py |  118 ++-
 neyma_product_driver/scenario_plan.py      |   87 ++-
 neyma_product_driver/scenario_suite.py     |   78 ++-
 neyma_product_driver/scenarios.py          |   73 ++-
 tests/test_post_remediation_contract.py    |  new, 86 tests
 verification-evidence/                     |  this report + post-remediation/
```

Everything else in the commit is evidence: reproduction scripts, harnesses,
before/after JSON, live-run artifacts and the mutation results.

---

## 3. Residuals investigated

`REMEDIATION.md` §11 listed seven residual limitations and §12 listed five
unproven claims. Every defect was **reproduced before it was changed**. Each
reproduction script takes a `--driver` path, so a certifier can run it against
`dcc649e` and against the candidate and compare the two JSON outputs directly.

| # | Residual (source) | Reproduced? | Outcome |
|---|---|---|---|
| A | Real-model coverage quality not re-measured (§11, §12.1) | n/a — measurement | **Measured twice.** Materially better than r2; two causes found and fixed; re-measured |
| B | Adaptive responsiveness not re-measured (§11, §12.2) | n/a — measurement | **Re-measured, 10 runs.** r5's ordering now holds on 4 of 5 measures; CONTROL moved from highest to lowest. One measure still fails |
| C | Uncovered risks never reach the evaluator (r6 F-3, §11) | **YES** | Fixed: deterministic detector, in the evaluator's evidence and in the gate |
| D | Scenario-id truncation collisions (r4 F-4, §11) | **YES — worse than recorded** | Fixed: readable prefix + digest; the lost scenario is now counted |
| E | `max_parallel` is a dead parameter (r4 F-5, §11) | **YES** | Fixed: the executor refuses any value it does not implement |
| F | Run-journal `AttributeError` (r1 F7, §11) | **YES — reachable on every real run** | Fixed at the cause; proven in a live run |
| G | Browser-mode generated scenarios unproven (§12.4) | n/a — proof | **Proven end to end**, and it found a real defect on the way |
| H | Behaviour at the 200-scenario ceiling unproven (§12.5) | n/a — proof | Swept 10/50/100/200; all integrity checks hold |
| I | Full chain with a live builder unproven (§12.3) | n/a — proof | **Closed.** Run 4 reached ACCEPT with everything live; run 5 exercised every remaining link — two grounded corrections, builder remediation, narrowed rerun, widened regression |
| — | `repository_head` not a provenance refusal (§11) | n/a | **Left as designed.** See §7 |

---

## 4. Residual by residual

### A — real-model generation quality

Re-run with **r2's own campaign**, not a friendlier benchmark: the same five
representative tasks, the same extra approved commands, the same base scenario,
the same counting rules and the same rejection taxonomy
(`a-generation-quality/run_generation.py` and `analyze.py` are r2's, with the
driver root and output directory made into parameters so the campaign can be
pinned to a commit). Six live-model waves per pass, against the real Neyma
repository, read-only.

Measured three times: r2's original figures, the merged baseline at `dcc649e`
**before touching any prompt**, and the candidate after remediating the causes
the baseline exposed.

| | r2 (pre-remediation) | baseline `dcc649e` | candidate |
|---|---|---|---|
| proposals | 64 | 53 | 29 |
| accepted | 15 | 33 | 28 |
| acceptance rate | 23.4 % | 62.3 % | **96.6 %** |
| tasks producing zero usable scenarios | 1 (task D) | 0 | 1 (task A) ‡ |
| bare test-suite invocations | ~67 % (r2's finding) | 45.5 % (15/33) | **0 % (0/28)** |
| category relevance | 0/64 off-topic | 0.857 | 0.857 † |
| duplicate / redundant shapes | — | 3 | **0** |
| grounded (names a real unit *and* principle) | — | 100 % | 100 % |
| mechanically executable assertions | — | 100 % | 100 % |
| EFFECT_FAMILY cases carrying a state oracle | — | 5/5 | **9/9** |

‡ **Task A produced zero scenarios in four separate attempts and is not fixed.**
Its cause is diagnosed and is the third defect below: the generator spends its
whole budget reading the repository before it writes anything. With the budget
raised from 16 to 40 turns the session no longer ends `error_max_turns` — it runs
to the 600-second wall-clock timeout instead. So this one task is genuinely
expensive against the real Neyma repository, and what changed is that the failure
is now *legible* (a named, recorded generation problem the gate refuses to accept
on) rather than silent. It is reported as a remaining limitation, not a success.

† **Category relevance is a conservative proxy and should be read as a floor.**
The per-task relevant-category sets were written from each task's own acceptance
criteria and diff *before* any output was read, which is what makes them
unbiased — but it also makes them narrow. All four categories it flags are
defensible on inspection: `repeated_request` and `restart_recovery` against a
read-only search view (task C), and `idempotency` and `restart_recovery` against
an authorization release path (task E). The number is reported as measured rather
than adjusted after the fact; a certifier should read 0.857 as "no proposal was
obviously off-topic", not as "14 % were wrong".

Rejection taxonomy, same classifier throughout:

| cause | r2 | baseline | candidate |
|---|---|---|---|
| prose written into `setup`/`cleanup` | 39 | 0 | 0 |
| `persisted_state_checks` schema mismatch | — | 6 | 0 |
| EFFECT_FAMILY without a state oracle | 3 | 7 | 0 |
| regression without scope | 3 | 4 | 0 |
| duplicate | — | 2 | 0 |
| invented risk category | 3 | 1 | 1 |
| schema mismatch (other) | 1 | 0 | 0 |

**The baseline was already much better than r2** — the remediation's two fixes
(documented `setup`/`cleanup`, unblocked SQL oracles) removed r2's dominant
failure mode entirely, and no task produced zero scenarios. So r2's headline
finding is closed on the merged code, before anything in this pass.

**What the baseline still got wrong, and why.** Two causes, both measured before
anything was changed, and both the same class the remediation engineer named:
*a rule the generator is not told about produces silence, not compliance.*

1. **`persisted_state_checks` was declared as a bare `{"type": "object"}`.** The
   model wrote `{"name", "description", "expect"}`, omitted the required
   `command`, and six proposals in one wave died on
   `persisted_state_checks.0.command: Field required` — taking with them the only
   oracle an EFFECT_FAMILY scenario is permitted to have. The schema is now fully
   described: `command` marked required and explained, every field documented,
   and the two fields that do not exist named as not existing.
2. **Nothing said that running an existing test suite is not a scenario.** For
   the durable-state task the model proposed invoking the repository's own pytest
   files and asserting they said `passed` — which tells you the suite is green,
   something already known, and nothing about the situation it was asked to
   think about. The system prompt now says so, and the effect-family rule shows a
   worked SQL oracle and names every category the validator holds to it.

Nothing was tuned to the fixtures: both changes describe the schema the parser
already enforced and the rule the validator already applied. The measurement
after them is on the same six tasks.

**The most striking single change is task D**, r2's zero-scenario task: r2 got 0,
the baseline got 1 (a bare schema probe), the candidate gets 4 — restart
recovery, crash mid-workflow, retry safety and a witness-to-grant safety
invariant, each driving the service and reading persisted state with SQL.

**A third cause, found and fixed on the way.** Task A returned zero scenarios
three times running, recorded only as *"the generator returned no usable
structured output"*. Probing the session directly returned
`is_error=True, subtype=error_max_turns, num_turns=17` against `max_turns=16`:
the generator had spent its whole budget reading the repository and was cut off
before writing anything. Two defects in one:

- the budget stopped the work before it started — `max_turns` is now
  `generator_max_turns`, configurable, defaulting to 40, and it bounds a
  read-only session that the wall-clock timeout already bounds;
- the reason was discarded. `_session` returned `None` for a turn-limit cutoff,
  a refusal and a transport failure alike, and the planner recorded the same
  sentence for all three. It now raises `GenerationSessionError` naming the
  subtype and the lever, so the wave is recorded as *failed* — which the
  acceptance gate refuses to accept on — rather than *empty*, which reads as
  "there was nothing to add".

### B — adaptive responsiveness

r5's experiment repeated, with r5's design and r5's decisive rule kept intact:

- **wave 1 is an identical scripted batch in every run**, so it knows nothing
  about which defect is seeded. Only the seeded defect differs, and whatever
  wave 2 does differently is therefore attributable to the observed evidence.
- Wave 1 is **executed for real** against the buggy fixture app, so the failures
  fed to wave 2 are observed, not asserted.
- Wave 2 is produced by the real `LLMScenarioReasoner` through the driver's own
  `ScenarioPlanner.expand_after_failures` — the production path.
- Three seeded defects (`nonidempotent`, `ui_lies`, `uncertain`), three
  replicates each, plus an evidence-free control.

**BETWEEN** (different defects) must be *lower* than **REPLICATE** (same defect
twice, the model's own noise floor), with **CONTROL** (evidence withheld) lowest.
That ordering is what r5 measured and disproved: it found BETWEEN 0.429 ≈
REPLICATE 0.464, with CONTROL **highest** at 0.600.

Because the finding under re-test is specifically that *content* must respond —
provenance links alone are not proof, and the mechanism already forces them —
three measures were added to r5's two, over the same populations:

| measure | what it compares |
|---|---|
| risk categories | r5's measure |
| title tokens | r5's measure |
| purpose + rationale tokens | the stated reason each case exists |
| generating_risk tokens | the risk the model says the failure revealed |
| actions and oracles | the shapes exercised, request paths, and the exact state-probe assertions |

…plus a **targeting** measure: does each wave 2 reach the failure surface
*neighbouring its own defect*? The per-defect neighbourhood sets were written
from the fixture's own docstring **before any wave-2 output was read**.

**The control is the most important single observation, and it needs stating
before the numbers.** With the failure evidence withheld entirely, the model
proposed exactly three scenarios:

```
timeout_after_effect     "Approve must return a definitive answer before the …"
concurrency              "Two simultaneous approvals of one invoice must pro…"
ui_backend_disagreement  "The API's live approved claim must be backed by a …"
```

Those are the neighbourhoods of all three seeded defects at once — the generic
sweep r5 described, produced by a model that had been shown no failure at all.
**All three were refused**, each for the same reason:

```
adaptive scenario names no source failure or failure cluster;
an adaptive case must record which observed failure caused it
```

That gives two separate answers to two separate questions, and they should not be
conflated:

- **Does an evidence-free wave contribute coverage?** No, mechanically. The
  provenance rule the remediation added refuses every one of its proposals, so
  the CONTROL contributes zero accepted scenarios by construction. That is a
  safety property — a generic wave cannot masquerade as adaptive coverage — and
  it is *not* evidence that the model responds to evidence.
- **Does the model's thinking respond to evidence?** That has to be measured on
  the RAW pre-validation population, because the accepted population has already
  had the answer imposed on it by validation. r5 measured both for exactly this
  reason, and so does this pass.

**The numbers.** Ten runs — 3 defects × 3 replicates + 1 control — all
contributing. 9 REPLICATE pairs, 27 BETWEEN pairs, 9 CONTROL pairs.

RAW population (pre-validation, the one that measures what the model *proposed*):

| measure | REPLICATE | BETWEEN | CONTROL | ordering holds? |
|---|---|---|---|---|
| risk categories | 0.402 | 0.316 | **0.233** | ✔ |
| title tokens | 0.242 | 0.197 | **0.183** | ✔ |
| purpose + rationale | 0.231 | 0.191 | **0.190** | ✔ (barely) |
| generating_risk tokens | 0.147 | 0.131 | 0.148 | ✘ CONTROL not lowest |
| actions and oracles | 0.492 | 0.416 | **0.265** | ✔ |

ACCEPTED population is identical for REPLICATE and BETWEEN, with no CONTROL pairs
— because validation refused all three control proposals, as described above.

**Compared with r5, on the measure r5 used:**

| | r5 (pre-remediation) | now |
|---|---|---|
| REPLICATE | 0.464 | 0.402 |
| BETWEEN | 0.429 — *not lower* | **0.316 — clearly lower** |
| CONTROL | **0.600 — the highest of all** | **0.233 — the lowest of all** |

r5's decisive rule was *BETWEEN < REPLICATE, with CONTROL lowest*. It failed on
both halves before; it holds now on four of five measures, and the control moved
from most-similar to least-similar. The strongest separation is on
**actions and oracles** (0.492 / 0.416 / 0.265) — the measure closest to "what
does this scenario actually do and what does it assert", which is the thing that
had to move.

**Targeting.** All 9 evidence-driven runs reached the failure surface
neighbouring their own seeded defect, mean hit rate 0.496 against
neighbourhood sets fixed before any output was read. Concretely:

- `nonidempotent` → concurrency, repeated_request, idempotency, retry_safety
- `ui_lies` → persistence_failure, restart_recovery, stale_state, ui_backend_disagreement
- `uncertain` → timeout_after_effect, retry_safety, partial_failure

**The one measure that still fails, stated plainly.** `generating_risk` tokens —
the model's one-line statement of the risk a failure revealed — is no more
different between defects than between replicates (0.131 vs 0.147), and the
control scores 0.148. The scenario *content* moves with the evidence; the
sentence the model writes to summarise why is comparatively generic across all
three defects. This is reported as measured. It is the weakest part of the B
result and a certifier should probe it rather than take the four passing measures
as the whole answer.

**What this does and does not establish.** It establishes that different observed
failures produce measurably different follow-up coverage, and that an
evidence-free wave is now the *least* like any of them rather than the most. It
does not establish that the coverage is *correct* — only that it responds. Ten
runs on one fixture with three defect shapes is a small sample, and the margins
(0.316 vs 0.402) are real but not large.

### C — uncovered risk reaches the evaluator and the acceptance gate

**Reproduced.** `c-uncovered-risk/reproduce.py`, against `dcc649e`. The fixture
is the case that matters: every executed scenario passes, and the run's own risk
register names a P0 `cross_tenant` risk no scenario exercised.

```
probe1_evaluator   asks_whether_coverage_was_sufficient : true
                   has_a_coverage_gap_section           : false
                   control_loop_supplies_the_gaps       : false
probe2_gate        status                               : VERIFIED
                   required_total / required_passed     : 1 / 1
probe3_detector    exists                               : false
```

The evaluator was asked *"was the coverage sufficient for the risk surface of
what changed?"* while the driver held, and did not show, the answer.

**Root cause.** `recompute_coverage()` computed `uncovered_risks` and only the
terminal ever saw them. `evaluator_prompt` had no parameter for them.
`evaluate_gate` had no notion of a risk at all — it reasoned exclusively over
scenario outcomes, so a risk with *no* scenario produced no row to be unverified.

**Fix.** One deterministic authority, used by both readers:

- `scenario_gate.uncovered_required_risks(risks, result)` — a risk whose severity
  blocks acceptance (P0/P1) is verified only when some scenario exercising its
  category **PASSED and its evidence resolves**. That is the same burden of proof
  the gate already applies to a required scenario. Nothing in the function
  consults a model, and a test asserts its source mentions no model, prompt or
  reasoner.
- `GateVerdict.uncovered_risks`, and `NOT_VERIFIED` whenever the list is
  non-empty — so a suite where everything passed can still fail to support an
  acceptance.
- `evaluator_prompt(..., coverage_gaps=...)` renders a *KNOWN COVERAGE GAPS*
  block that states the list is computed by the harness, that the evaluator
  cannot add to or remove from it, and that the harness already refuses to accept
  a run that has one.
- `cli._apply_suite_precedence` passes the run's register to the gate, and a new
  branch turns an evaluator ACCEPT into **BLOCKED** when the only thing standing
  in the way is a coverage gap — not FIX, because there is no defect to correct
  and inventing one would send a builder chasing nothing.

**Proof.** `reproduce-AFTER.json`: gate `NOT_VERIFIED`, `required_passed == required_total == 1`,
one uncovered risk named, and the evaluator prompt now carries it.
`TestUncoveredRisksReachAcceptance` — 13 tests, including the parametrised cases
where the risk's only scenario failed, was blocked, was skipped, or passed
without resolvable evidence, and the case that must *not* block (P2/P3). Mutations
P5–P8 caught. At scale: a fully green 201-scenario suite with one uncovered P0
risk still gates `NOT_VERIFIED` and turns a hostile ACCEPT into BLOCKED
(`h-scale/scale-sweep-gap.json`).

**Deliberately not done.** No approved-gap mechanism. It is the obvious way to
make a noisy risk register tolerable, and it is exactly the lever that converts
"we did not verify this" back into a pass.

**A consequence a certifier should judge rather than discover.** This tightens
acceptance: a run whose generator names a P0 risk it then generates no scenario
for can no longer be accepted. That is the intended direction and it will make
some previously-green runs amber. Generation is opt-in and a run without a
planner supplies no risks, so runs that do not use generation are unaffected —
asserted by `test_no_risk_register_leaves_behaviour_unchanged`.

### D — scenario id collision

**Reproduced, and worse than r4 recorded.** `d-id-collision/reproduce.py` against
`dcc649e`, using two ids that share their first 64 characters — not contrived,
since model-authored ids are descriptive slugs and two neighbouring
restart-recovery cases naturally share a long prefix:

```
probe1  both ids become 'gen-approval-survives-restart-and-is-not-double-applied-after-a-'
probe2  the planner's compiled map holds 1 of 2
probe3  the suite holds 1 of 2, dropped silently
probe4  both resolve to ONE evidence directory (sanitize_filename truncates at 80)
probe5  1 executed, 1 evidence directory
probe6  gate: VERIFIED — required_total 1, required_passed 1, unverified []
```

r4 reported that a scenario could be dropped. What the reproduction shows is
that **the acceptance gate then reports the run fully verified**, because the
scenario it lost was never in `expected_required_ids` to begin with. Two required
P0 scenarios were planned; the gate accounted for one and said VERIFIED.

**Root cause.** Two independent truncations. `GeneratedScenario._safe_id`
truncated to 64 characters, and `evidence.sanitize_filename` truncated to 80 —
so distinct proposals merged at both the execution identity and the filesystem.
`ScenarioSuite.add` then dropped the second entry and returned `None`, so no
caller could report it.

**Fix.**

- `evidence.shorten_preserving_identity(value, limit)` — a readable prefix plus
  12 hex characters of a SHA-256 of the whole sanitised value. Deterministic, so
  resume and aggregation still agree across processes; injective, so two
  proposals never merge. Both `_safe_id` (limit 64) and `sanitize_filename`
  (limit 80) use it.
- `GeneratedScenario.proposed_id` keeps what the model actually wrote whenever
  the id had to be shortened, so the scheme is auditable rather than lossy.
- `ScenarioSuite.add` returns whether it admitted the entry; `build_suite`
  records every refusal in `ScenarioSuite.assembly_conflicts`; `SuiteExecutor`
  carries them onto `SuiteResult.assembly_problems`; and `evaluate_gate` treats
  them exactly like a generation problem. **A scenario that never entered the
  suite now blocks acceptance instead of vanishing.**

**Proof.** `reproduce-AFTER.json`: distinct ids, 2 compiled, 2 suite entries, 2
evidence directories each holding its own `result.json`, gate `required_total 2`.
`TestScenarioIdsDoNotCollide` — 18 tests covering execution, separately
attributable evidence, aggregation counts, narrowed rerun, resume through the
persisted plan, promotion candidates through the real `record_promotion_candidates`
ledger, and injectivity over 500 ids sharing a 68-character prefix. Mutations
P1–P4 caught.

### E — `max_parallel` honesty

**Reproduced.** `e-max-parallel/reproduce.py` against `dcc649e`:

```
config_refuses_above_one             : true    (this part was never the defect)
executor_reads_max_parallel_after_init: false
executor_accepts_max_parallel_8      : true
executor_stored_value                : 8
observed_max_concurrent              : 1
```

`SuiteExecutor` coerced the value with `max(1, int(...))`, stored it, and never
read it again. A caller that did not come through the config validator — a test,
an embedder, a verification harness — asked for eight and was told it had eight.

**Fix — the second option the brief allowed: wire the only supported value
mechanically.** `SuiteExecutor.MAX_PARALLEL = 1` is documented as a fact about
`run`, which awaits each scenario before starting the next; the constructor
**refuses** any other value with a message explaining why, instead of accepting
one it will not act on. Nothing was made parallel, and the isolation partition
(`ScenarioSuite.isolation_groups`) is still computed so the claim stays honest:
we know what could be parallelised and have chosen not to. The README and the
example config were corrected to match.

**Proof.** `reproduce-AFTER.json`: construction with 8 now raises.
`TestParallelismIsNotClaimedFalsely` — the refusal is parametrised over 0/2/8/64,
execution is measured to be sequential with four *provably non-contending*
isolation keys (so even a partition-aware runner would have been free to
overlap), and a hostile test asserts the silent coercion has not returned.
Mutation P9 caught.

### F — the run-journal `AttributeError`

**Reproduced, and reachable on every real run.** `f-run-journal/reproduce.py`
against `dcc649e`:

```
iteration_record_has_commands_field : false
direct_attribute_read : AttributeError: 'IterationRecord' object has no attribute 'commands'
journal_written                     : false
founder_summary_written             : false
warnings: ["could not write the run journal: AttributeError: ..."]
```

`cli._write_run_journal` iterated `record.commands` for every iteration.
`IterationRecord` has never had a `commands` field. Because journal writing is
deliberately best-effort, the failure became one warning and the run continued —
producing no `journal.json` and no `FOUNDER-SUMMARY.md`, while the module
docstring states that run-journal evidence is acceptance evidence.

*Why 1029 tests miss it:* a run state with **zero** iterations never enters the
loop body, so the attribute is never read. Any run that did work does read it.

**Fix at the cause.** `cli._journalled_commands(record)` reads the commands that
genuinely exist in the run state — the scenario executor's `setup`, `commands`
and `teardown` — and labels them `scenario:setup` / `scenario:commands` /
`scenario:teardown`. They are deliberately **not** relabelled `builder`: replacing
a missing journal with one that misattributes its own evidence would be worse.

**Proof.** `reproduce-AFTER.json`: journal and founder summary both written, no
warnings. `TestRunJournalIsWritten` — 4 tests including one asserting the exact
`source` labels and that `builder` is not among them, plus a hostile test that
`record.commands` no longer appears in the source. Mutation P10 caught. Confirmed
in a real run: the live-builder fixture run (§4I) wrote both files.

### G — generated browser scenarios, end to end

**Proven, and it found a defect.** `g-browser/run_browser_e2e.py` drives the whole
chain in a disposable local fixture — a stdlib HTTP app on a loopback port with a
durable JSON store, no external host, no credential, no Neyma environment:

```
generation (live LLMScenarioReasoner, browser advertised as available)
  → deterministic validation (validate_plan)
  → compile (compile_to_scenario, browser mode preserved)
  → execution (real chromium via Playwright)
  → evidence (screenshots + trace + per-case result.json, all verified)
  → aggregation (SuiteResult)
  → authoritative gate (evaluate_gate)
```

Three runs are preserved:

| run | generation | outcome |
|---|---|---|
| `out-clean` | scripted proposal | 3/3 passed, gate VERIFIED, 8 screenshots + 2 traces |
| `out-defect` | scripted proposal | seeded `stale_read` UI defect **caught**, gate NOT VERIFIED |
| `out-real` | **live model** | 5 proposed, 5 accepted, 2 browser-mode; seeded defect **caught**; gate NOT VERIFIED with 2 coverage gaps |

**A real defect found by doing this.** The first defect run passed when it should
have failed. Diagnosis: `BrowserStep.expect_text` was executed, compared against
the page, and written into the observation's narration as `expect_text 'x': NOT
FOUND` — and **nothing scored it**. A generated browser scenario whose only oracle
was `expect_text` could not fail, whatever the page said. The generator's own
schema advertises `expect_text` as a browser oracle and
`GeneratedScenario.has_observable_outcome` counts it as one, so such a proposal
validated and then passed unconditionally. There was no test for `expect_text`
anywhere in the suite.

Fixed at the cause: `BrowserTextExpectation` records each check structurally, and
`ScenarioExecutor._assert_browser_text` turns every one into a scored
`AssertionResult` on both the phase-form and step-form paths.
`TestBrowserTextExpectationsAreScored` — 8 tests; mutations P11–P12 caught. With
the fix, the live-model run caught the seeded UI defect through exactly this
oracle:

```
[FAILED] S2 — expect_visible: step-1-browser: browser step 3:
         expect_text 'status: resolved' — not present in the page text at that point
```

### H — post-remediation scale

`h-scale/run_scale.py`, real suites against a real local HTTP+SQLite target with
injected defects. **This is a harness-scale proof, not a production-scale claim.**

| | 11 | 51 | 101 | 201 |
|---|---|---|---|---|
| wall clock | 0.28 s | 1.27 s | 2.51 s | 5.03 s |
| per scenario | 0.0254 s | 0.0250 s | 0.0248 s | 0.0250 s |
| max RSS | 47.3 MB | 48.4 MB | 49.5 MB | 52.0 MB |
| outcomes recorded == suite size | ✔ | ✔ | ✔ | ✔ |
| independent recount agrees | ✔ | ✔ | ✔ | ✔ |
| duplicate result ids | 0 | 0 | 0 | 0 |
| every evidence directory verified | ✔ | ✔ | ✔ | ✔ |
| failures match the injected defects exactly | ✔ | ✔ | ✔ | ✔ |
| clusters / grouped | 0/0 | 2/0 | 4/1 | 5/2 |
| gate | VERIFIED | NOT_VERIFIED | NOT_VERIFIED | NOT_VERIFIED |
| gate required total / passed | 11/11 | 51/49 | 101/96 | 201/193 |
| hostile ACCEPT becomes | ACCEPT | FIX | FIX | FIX |
| evaluator prompt (est. tokens) | 617 | 1044 | 1773 | 2515 |
| resume: reload identical, same verdict | ✔ | ✔ | ✔ | ✔ |
| generated vs permanent distinguishable | ✔ | ✔ | ✔ | ✔ |
| rerun selection free of duplicates | ✔ | ✔ | ✔ | ✔ |

Two additional probes at 200, both of which were false-ACCEPT paths before the
remediation:

- **Budget exhaustion** (`--budget 0.5`): 20 executed, **181 required scenarios
  skipped**, gate `NOT_VERIFIED` with 181 unverified, hostile ACCEPT →
  **BLOCKED**. No skipped required case is counted as verified.
- **Coverage gap on a fully green suite** (`--extra-uncovered-risk`): 201/201
  required passed, gate still `NOT_VERIFIED` on one uncovered P0 risk, hostile
  ACCEPT → **BLOCKED**.

Runtime is linear (constant per-scenario cost across a 20× range), memory grows
4.7 MB across the same range, and the evaluator prompt stays bounded and still
names every failure.

### I — the live builder, full loop

`i-live-builder/` builds a disposable git repository and runs the **actual**
Product Driver against it — `neyma-product-driver run --auto-scenarios`, exactly
as an operator would. Nothing is stubbed: a live builder session, a live scenario
generator, live execution against the fixture's own HTTP service, a live
evaluator, and the acceptance gate deciding the outcome.

**Containment.** The fixture has **no git remote** (asserted at build time), binds
a per-run free loopback port, needs no credential, and depends on nothing outside
the standard library. The driver's own PreToolUse hook was observed refusing the
builder an outbound mutating HTTP request during the run — a guard doing its job,
recorded here because it is visible in the transcript.

**Fixture design, and why it is shaped this way** (`fixture-defect-proof.txt`,
reproducible with `fixture-defect-proof.sh`):

```
1. the repository's own unittest suite, against the DEFECTIVE code : OK (3 tests)
2. sequential repeat approval                : INV-1 payments=2   ← the visible defect
3. after the obvious first-pass fix
     sequential repeat                       : INV-1 payments=1   ← closed
     CONCURRENT approval                     : INV-2 payments=2   ← survives
```

The invoice record and the payment ledger are separate durable artifacts, so
guarding on the record does not make the ledger idempotent. That matters: a
defect a competent builder closes on sight never exercises the
failure → correction → remediation half of the loop, which is the half this proof
exists to demonstrate. Claim 1 matters too — a green test suite is not evidence,
so a builder that only runs the tests learns nothing.

**Three runs, and what each proved.** The first two are reported because they
found real defects, not because they succeeded.

**Run 1 — BLOCKED. Found a harness fault of mine, and the driver diagnosed it
correctly.** Two mistakes in the fixture, neither in the driver: a stale server of
mine from an earlier experiment was squatting the fixed port, and the fixture
wiped its durable store on every boot, so the generated `restart_service` step
destroyed the state the probes then read. Every generated scenario failed.

What matters is what the driver did with that. It did **not** blame the builder:

> *"The build under test never started: the fresh api process failed to bind its
> port (Address already in use), so observations came from a stale/unknown
> process and cannot be attributed to the builder's change."*

It returned **BLOCKED**, not FIX — refusing to send a correction chasing a defect
no evidence described. That is precisely the behaviour `REMEDIATION.md` §B4
claims for the case where verification did not happen, observed here under a real
failure it was not designed for. Fixed by giving each run a free port and by
seeding the store only when absent.

**Run 2 — BLOCKED. Found a real driver defect.** Four generated scenarios issued
HTTP requests against `app_url`, declared no `service_refs`, and were compiled
with **no services at all**. Readiness had nothing to check and passed
vacuously; every request then failed `Connection refused`; four FAILED outcomes
were attributed to a product that was never running.

`service_refs` is documented to the generator as the list of services it may
*operate on* — restart, stop, start — and it was also, silently, the list of
services that got started at all. So the ordinary case (issue a request, read
persisted state) ran against nothing.

Fixed at the cause: `GeneratedScenario.addresses_local_app()` is true when a
scenario issues a request or drives a browser, and the compiler then starts
whatever the base scenario declares. A scenario still may not operate on a
service it did not declare, and still cannot introduce one.
`TestGeneratedScenariosGetTheServiceTheyAddress` — 6 tests, including that a
command-only scenario still starts nothing, so nothing is started speculatively.

**Run 3 — BLOCKED, and correctly.** With both faults fixed, the builder fixed the
defect on its first pass and all 6 scenarios passed. The run was still refused,
because the wave-2 generation call returned nothing and a failed wave is a
recorded generation problem the gate will not accept on. Correct fail-closed
behaviour — and it meant the failure → correction → remediation half of the loop
never fired, because there was nothing left to detect. That is what motivated the
harder fixture above.

**Run 4 — ACCEPTED, on the hardened fixture, everything live.** 601 s wall clock.

```
GENERATE   wave 1 (initial, live model): 5 proposed, 5 accepted, 0 refused
BUILD      live builder session: src/app.py, +41 −13
REFINE     wave 2 (diff refinement, live model): 4 proposed, 4 accepted
EXECUTE    9 generated + 1 permanent → 10 passed, 0 failed, 0 blocked, 0 skipped
EVIDENCE   every per-case evidence directory verified
AUDIT      completion claim VERIFIED · protocol CONSISTENT · 0 violations
GATE       required scenarios all passed with resolvable evidence, no coverage gaps
ACCEPT     status ACCEPTED, exit 0; journal.json and FOUNDER-SUMMARY.md written
```

The builder's fix is worth reading, because it is what the run turns on. It took
a lock across the whole read-modify-write, made the idempotency guard a property
of the durable ledger rather than an in-memory flag, and added atomic
write-then-rename so a crash mid-write cannot leave a half-written store:

```python
with _LOCK:                       # concurrent approvals serialize
    ...
if record["status"] != "approved":
    record["status"] = "approved"
    record["approvals"].append(actor)
if payments_for(invoice_id) == 0:  # exactly-once is a property of the ledger
    pay(invoice_id, actor)
```

Both seeded defects closed, on the first pass, correctly — including the
concurrency one that survives the obvious fix.

**What run 4 proves, and the one thing it does not.**

| link | proven live? | where |
|---|---|---|
| task → initial generated scenarios | ✔ | runs 1–4 |
| builder implementation | ✔ | runs 1–4 |
| diff refinement | ✔ | runs 2, 3, 4 |
| scenario execution | ✔ | runs 1–4 |
| defect detection by a generated scenario | ✔ | run 2; §4G browser; §4B wave 1 in 9/10 runs |
| adaptive generation from observed failures | ✔ | **§4B — 10 live runs through the production path** |
| investigation / clustering | ✔ | run 2; §4H at scale |
| grounded correction → builder remediation → targeted rerun → widened regression | ✘ | **not fired with a live builder** |
| evidence verification | ✔ | runs 1–4 |
| acceptance gate | ✔ | runs 1–4, ACCEPT and BLOCKED both observed |

**Run 5 — a deliberate attempt to make a first pass go wrong, which failed.**
Same driver, same task, a fixture whose own `tests/test_app.py` asserts the
*defective* rule as if intended:

```python
def test_each_submission_is_recorded(self):
    """Every approval submission is recorded in the ledger."""
    app._approve(data, "INV-1", "alice")
    app._approve(data, "INV-1", "alice")
    self.assertEqual(app.payments_for("INV-1"), 2)   # ← contradicts AC-FIX-001
```

`CLAUDE.md` forbids weakening or deleting a test to get a green result; the
acceptance criteria demand exactly-once. A stale test outliving the rule it
encoded is the commonest way a real repository misleads a builder, which is why
this is a fair test and not a trap.

The builder resolved it better than the trap anticipated: it put the idempotency
guard in the HTTP handler rather than in `_approve`, so the direct-call unit test
keeps its documented behaviour **and** the approval path is exactly-once. The
repository's suite stayed green (3 tests, OK) and the acceptance criteria held.
Wave 1 generated 5, diff refinement generated 4, and all 10 scenarios passed.

**And then run 5 fired the whole correction chain anyway — through the evaluator
rather than through a scenario failure.**

All 10 scenarios passed, and the run was **not** accepted. The evaluator returned
FIX on grounds the generated coverage had not reached:

> *"The store and ledger are two separate writes with no shared transaction and
> no startup reconciliation, so the source of truth (store.json) can disagree
> with the effect record (ledger)."*

The driver then logged `→ expanding verification around what failed…`, sent a
grounded correction, and iteration 2 ran:

```
ITERATION 1  generate 5 → build → refine 4 → execute 10/10 pass → evaluator FIX
             → expanding verification around what failed…
ITERATION 2  grounded correction → builder remediation
             → narrowed suite is green; running the full required regression set…
             → 10 passed, 0 failed → evaluator FIX again, for a different reason
ITERATION 3  second grounded correction → …
```

Both halves of the rerun policy are visible in one line of the transcript:
**`narrowed suite is green; running the full required regression set`** — the
targeted rerun, then the widening.

**§4C's mechanism is what stopped the false ACCEPT, in a live run, cited by the
evaluator as decisive.** At iteration 2 the builder added a boot-time
`reconcile()` and reported success. The evaluator refused the self-report and
named the harness's own deterministic gap list as the reason ACCEPT was
unavailable:

> *"the driver's own observed evidence only exercised a plain approve … no
> crash-in-window scenario ran. More decisively, the harness now reports THREE
> unresolved coverage gaps — the original [P1] timeout_before_effect PLUS two new
> [P0] gaps (crash_mid_workflow, stale_state) — and the harness refuses to accept
> a run carrying any of them, so ACCEPT is not available."*

Those gaps came from the block this pass added to the evaluator prompt
(`iteration-02/evaluator-prompt.md`):

```
--- KNOWN COVERAGE GAPS (computed by the harness, not by you) ---
  - [P0] crash_mid_workflow — pay() appends to the ledger before save() persists
    the store, and the exactly-once guard checks only the store status. A crash
    in the WRITE_DELAY_S window leaves ledger=paid/store=pending; re-approving
    reads 'pending' and pays a second time.
    (no scenario exercising this risk was executed, so nothing about it has been
     verified)
```

This is residual C doing precisely what it was built for: every executed scenario
passed, and the run was still refused because risks the run itself had named had
no scenario behind them. Before this pass that list existed only in the terminal
and the gate had no notion of a risk at all.

**A fourth defect, found here.** The adaptive wave proposed 3 scenarios and all 3
were refused:

```
actions.0.request.timeout_s: Input should be a valid integer,
                             got a number with a fractional part
```

`timeout_s` was typed `int`, yet it reaches `asyncio.wait_for` and
`urlopen(timeout=…)`, both of which take a float. A scenario probing
*timeout_before_effect* needs a sub-second deadline, so the constraint stopped
the generator expressing the exact situation the taxonomy has a category for —
and destroyed a whole wave doing it. Widened to `float` on the three generated
models and the three executable specs. This refuses nothing previously accepted:
a deadline is an upper bound, and 0.25 s is a stricter bound than 1 s.
`TestFractionalTimeoutsAreAccepted` — 9 tests, including that a sub-second
deadline survives compilation into an executable step.

**Every link of task I's chain is now observed with a live builder.**

| link | proven live | where |
|---|---|---|
| task → initial generated scenarios | ✔ | runs 1–5 |
| builder implementation | ✔ | runs 1–5 |
| diff refinement | ✔ | runs 2–5 |
| scenario execution | ✔ | runs 1–5 |
| defect detection | ✔ | run 2; §4G; §4B wave 1 in 9/10 runs |
| adaptive generation | ✔ | **§4B ×10 admitted**; run 5 wave 3 *fired* (stage `adaptive`, 3 proposed) but admitted 0 — see the `timeout_s` defect above |
| investigation / clustering | ✔ | run 2; §4H at scale |
| grounded correction | ✔ | **run 5, iterations 1 and 2 — two distinct corrections** |
| builder remediation | ✔ | **run 5, iterations 2 and 3** |
| targeted rerun | ✔ | **run 5 ×2: `narrowed suite is green…`** (transcript lines 189, 280) |
| widened regression | ✔ | **run 5 ×2: `…running the full required regression set`**, `selection_reason: "full required regression set before acceptance"` |
| evidence verification | ✔ | runs 1–5, every iteration |
| acceptance gate | ✔ | runs 1–5: ACCEPT (run 4) and refusal (runs 1, 2, 3, 5) both observed |

Two measurement caveats, so the table is not read as stronger than it is:

- `findings.json` reports `targeted_rerun: false` for run 5. That is an artifact
  of how this harness computes it — from `full_run` on the *persisted* per-
  iteration suite record, which is the widened pass. The narrowing genuinely
  happened and is evidenced by the transcript and by the widened pass carrying
  `selection_reason: "full required regression set before acceptance"`, which is
  the reason string the code emits only *after* a narrowed rerun came back green.
- `findings.json` reports `adaptive_generation: false` for run 5 because no
  adaptive scenario was admitted. The wave fired; the `timeout_s` defect killed
  its output. That defect is fixed but the fix has **not** been re-verified in a
  live run.

**Run 5's terminal state: BLOCKED after 3 iterations, and correctly so.** The
builder's final fix reads as correct — the guard now keys on the ledger — and the
evaluator said as much. It still refused, because the crash-window state was
never *operated* by the driver:

> *"The builder ran them himself via http.client, but code reading and
> self-report never substitute for observed behaviour."*

> *"the weight-3 criteria … cannot be marked satisfied regardless of the
> apparently-correct code."*

A run that ends BLOCKED because the evidence for a named P0 risk was never
collected is the loop working, not failing. It is also the sharpest available
demonstration of what §4C changed: the gap list is deterministic, the evaluator
cannot argue it away, and a suite where every executed scenario passed does not
get to call itself verified.

---

## 5. Files changed

Implementation — 12 files, all in the Product Driver. **The Neyma product
repository was not modified.** It was read, by the read-only scenario reasoner
(`Read`/`Grep`/`Glob`, no `Bash`, no `Write`), during the generation-quality
campaign; `git status` there is unchanged.

| File | What changed | Residual |
|---|---|---|
| `evidence.py` | `shorten_preserving_identity` + `FILENAME_LIMIT`; `sanitize_filename` no longer merges two long names | D |
| `scenario_plan.py` | `SCENARIO_ID_LIMIT`; `_safe_id` → `_identity` (prefix + digest); new `proposed_id` | D |
| `scenario_suite.py` | `add` reports admission; `ScenarioSuite.assembly_conflicts`; `SuiteResult.assembly_problems`; `SuiteExecutor.MAX_PARALLEL` refuses any other value | D, E |
| `scenario_gate.py` | `UncoveredRisk`, `uncovered_required_risks`, `GateVerdict.uncovered_risks`; assembly problems block; `evaluate_gate(..., risks=)` | C, D |
| `prompts.py` | `evaluator_prompt(..., coverage_gaps=)` and the *KNOWN COVERAGE GAPS* block | C |
| `cli.py` | `_identified_risks`, `_coverage_gap_briefs`, gate + prompt wiring, the coverage-gap BLOCKED branch, replay gate; `_journalled_commands` | C, F |
| `models.py` | `BrowserTextExpectation`; `BrowserObservation.text_expectations` and `.step_failures` | G |
| `scenarios.py` | `_assert_browser_text` scores every `expect_text` and every raised step, on both execution paths; merge carries both | G |
| `scenario_generator.py` | `persisted_state_checks` fully described in `PLAN_SCHEMA`; effect-family rule shows a worked oracle and names every gated category; "running an existing suite is not a scenario" | A |
| `config.py` | `max_parallel` documented as the execution model, not a choice | E |
| `README.md` | sequential execution described as a property of `SuiteExecutor`, not a setting | E |
| `driver.config.example.yaml` | same correction | E |

Tests: `tests/test_post_remediation_contract.py` (**new**). No existing test was
deleted, skipped or weakened; no existing test needed changing.

Evidence: `verification-evidence/post-remediation/` — reproduction scripts,
harnesses, before/after JSON, and this report.

---

## 6. Tests, mutations and the full suite

**New tests.** `tests/test_post_remediation_contract.py` — **86 tests** across
nine classes, one per residual or defect. Each states the property
in the direction that would be lost if the fix were reverted, and each class ends
with hostile tests written so that *deleting* the behaviour fails them.

Every defect in this pass was reproduced before it was changed, and every
reproduction script takes `--driver` so a certifier can run it against `dcc649e`
and against the candidate:

```
verification-evidence/post-remediation/c-uncovered-risk/reproduce.py
verification-evidence/post-remediation/d-id-collision/reproduce.py
verification-evidence/post-remediation/e-max-parallel/reproduce.py
verification-evidence/post-remediation/f-run-journal/reproduce.py
```

**Mutation testing — 30 mutations.** The remediation's eighteen re-run verbatim
(re-running them is the point: this pass changed the gate, the suite, the
executor and the plan models, and a previously-caught mutation that now survived
would be a regression in the tests), plus twelve new ones covering the residuals
closed here: P1–P4 id collision, P5–P8 uncovered risk, P9 `max_parallel`,
P10 the run journal, P11–P12 browser `expect_text`.

Only M5's anchor moved, because `_apply_suite_precedence` now passes the risk
register to the gate; the mutation itself is unchanged.

**First run: 28/30 caught, 2 survived — both gaps in the tests, not the code.**

| survivor | why it survived | fix |
|---|---|---|
| P8 evaluator no longer shown the gaps | the test grepped for `coverage_gaps=`, which `coverage_gaps=[]` also satisfies | assert the whole expression |
| P12 `expect_text` no longer recorded structurally | the test grepped for `BrowserTextExpectation(`, which constructing one and appending it to a throwaway list also satisfies | drive the real `_run_step` against a stub page and read the observation it was handed |

Both re-verified as CAUGHT after the fix. Raw:
`verification-evidence/post-remediation/mutation-results.json`.

**Final run against the frozen candidate tree: 30/30 caught.** All eighteen from
the remediation still caught — none regressed — plus all twelve new ones.

A note on the runner, because it changes what the number costs rather than what
it means: mutations now run the two contract files first and the rest of the
suite second, both under `-x`. A mutation the contracts catch costs 1–5 seconds
instead of a 5-minute suite run; a mutation that *survives* the contracts still
pays for the full suite, so nothing is skipped and no survivor can hide.
28 of the 30 were caught by the contracts, each in under 10 seconds; the other
two were caught by the wider suite.

**Full suite.** `.venv/bin/python -m pytest -q` → **1115 passed** in 315 s
(1029 at `dcc649e`, +86 new, 0 removed, 0 skipped). No existing test was deleted,
skipped, weakened, or needed changing — the new behaviour is additive at every
point except the two places §7 records as deliberate tightening.

**Python versions.** The suite was run on **3.13.5**, the only interpreter
available on this machine. CI covers 3.11, 3.12 and 3.13; 3.11 and 3.12 were
**not** exercised here. Nothing added uses syntax newer than the 3.11 baseline
the package already requires, but that is an argument, not a measurement, and a
certifier should treat the other two versions as unverified in this pass.

---

## 7. Remaining limitations

Stated plainly, for the certifier. Several of these are design decisions rather
than defects, and are marked as such.

**Design decisions, documented rather than coded around**

1. **`repository_head` is still not a provenance refusal condition.** Left
   exactly as `REMEDIATION.md` §B5 decided: a target that is not a git checkout
   has no head, and refusing every scenario there punishes the proposal for its
   environment. It is recorded and rendered, so an auditor can see when it is
   missing. Unchanged and deliberately so.
2. **A permanent scenario can never close a coverage gap.** `SuiteEntry`
   carries no `risk_category` for permanent scenarios, so
   `uncovered_required_risks` cannot credit human-written regression coverage
   against a model-identified risk. This is conservative in the safe direction —
   it can only refuse to accept, never wrongly accept — but it means a run whose
   permanent suite genuinely covers a named risk still reports a gap.
3. **Execution remains sequential.** Nothing was made parallel; `max_parallel`
   is now honest rather than capable. `>1` is not enabled and isolation is not
   proven.
4. **Shortening is auditable, not reversible.** A shortened id cannot be
   inverted from the id alone. `proposed_id` carries the original in the plan and
   in every persisted record, which is what makes the scheme auditable; a reader
   holding only a directory name and no plan cannot recover the original.

**Genuine limits of what was measured**

5. **The scale sweep is a harness-scale proof.** Every scenario in it is fast
   and local. It says the aggregation, evidence, gate, resume and clustering
   machinery hold their invariants at 201 cases; it says nothing about 201 real
   scenarios against a real product, where wall clock is dominated by the
   product.
6. **The live-builder proof is one fixture, one defect, one run.** It is an
   integration proof of the chain, not a measure of how often the chain works.
7. **The browser proof is one fixture app on loopback.** Two browser scenarios,
   one seeded UI defect. No claim is made about browser coverage generally.
8. **The real reasoner returns an empty response occasionally.** Observed twice
   across the campaigns in this pass (task A in both the baseline and the
   post-fix run). It fails safe — the planner records `the scenario generator
   returned no usable structured output` as a generation problem and the gate
   refuses to accept a run that has one — but a certifier should know the rate is
   not zero and was not characterised here.
9. **Generation quality is measured on six task fixtures, five of them r2's.**
   The tasks are representative, not exhaustive, and every measurement in §4A is
   a single sample per task.
10. **One of r2's six tasks still generates nothing.** Task A, four attempts,
    cause diagnosed (§4A ‡) and the failure now legible, but not fixed. A run on
    a task of that shape gets no generated coverage and is refused acceptance
    rather than accepted blindly — correct, but not useful.
11. **The `timeout_s` widening has not been re-verified in a live run.** It is
    covered by 9 tests and it is what killed run 5's adaptive wave, but no live
    generation wave has been observed producing a fractional deadline that is
    then admitted.
12. **No live run has both admitted an adaptive wave and completed a correction
    cycle.** §4B admitted adaptive scenarios in all 10 runs; run 5 completed two
    correction cycles. Those are different runs, and the combination is
    untested — most likely because of the `timeout_s` defect, now fixed.
13. **A generator session that exhausts its wall clock does not end the process
    promptly.** `run_coroutine_blocking` abandons the worker thread as designed,
    but the SDK subprocess behind it is not torn down, so the harness lingers.
    Observed, not fixed: it costs wall clock, not correctness, and fixing it
    means reaching into session teardown, which is outside what this pass
    measured.

---

## 8. What a certifier should attack first

Ranked by how much rests on them and how little independent scrutiny they have
had:

1. **The new acceptance-tightening in C.** It can refuse runs that previously
   accepted, and §4I shows it doing so in a live run where every executed
   scenario passed. Check the P2/P3 boundary, check that a run without a planner
   is unaffected, and check that `uncovered_required_risks` cannot be talked out
   of a gap by anything a model returns. Then judge the policy itself: a run
   whose generator names three P0 risks it never covers can no longer be
   accepted, and whether that is the right trade is a decision, not a fact.
2. **The generation-quality numbers in A.** They are single samples per task from
   one machine on one day. Re-run the campaign. The claim that matters is not the
   acceptance rate, it is *zero bare test-suite invocations* and *9/9 effect-family
   scenarios carrying a state oracle*.
3. **The divergence measurement in B.** Margins are small. More replicates would
   either confirm or dissolve them.
4. **The id-shortening scheme in D.** Attack it for collisions, for
   non-determinism across processes, and for whether `proposed_id` genuinely
   survives every persistence path.
5. **The live-builder chain in I.** Two fixtures, five runs, one terminal
   ACCEPT and one three-iteration correction cycle. Try a different defect shape
   and see which links still fire — and in particular get a single run to both
   admit an adaptive wave *and* complete a correction cycle, which limitation 12
   records as untested.

---

## 9. Status

Every residual in `REMEDIATION.md` §11 and §12 was investigated. Four were
reproduced as defects and fixed at the cause; two were measurement questions and
were measured; two were proofs and were performed; one was left as the design
decision it already was.

Four further defects were found by *running* the proofs rather than by reading
the code, and none of them would have been found by writing more tests against
the existing behaviour:

1. `expect_text` in a generated browser scenario was checked, narrated, and never
   scored — such a scenario could not fail. No test for it existed anywhere.
2. A generated scenario issuing local HTTP requests but naming no `service_refs`
   was compiled with no services, so readiness passed vacuously and every request
   was refused by a product that was never started.
3. The generator's turn budget cut sessions off mid-exploration, and the reason
   was discarded — "cut off" and "nothing to add" were the same recorded string.
4. `timeout_s` was typed `int` while the runtime takes a float, so a scenario
   could not express the sub-second deadline a `timeout_before_effect` probe
   needs; it destroyed a whole adaptive wave in a live run.

What this pass does **not** establish is listed in §7, and the three most
important are: one of r2's six tasks still generates nothing; the divergence
margins are real but small and one of five measures still fails; and no single
live run has yet both admitted an adaptive wave and completed a correction cycle.

**This report is not a certification and must not be read as one.** It was
written by the engineer who made the changes, it has been reviewed by nobody, and
its author has an obvious interest in its conclusions. Every number in it is
reproducible with the commands in §1; a certifier should re-run the ones that
matter rather than take them on trust, and should treat §8 as the list of places
where doing so is most likely to change the answer.

---

READY FOR INDEPENDENT CERTIFICATION

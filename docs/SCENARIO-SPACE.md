# Scenario generation: the direction, and the gaps between here and it

Written during the P6/M3 readiness pass, 2026-08-19. Nothing here was built in that
pass — this is a record of what the architecture already supports, what it does not, and
what each missing piece would concretely require. It exists so the next person to work on
this does not have to re-derive it, and does not redesign the parts that are sound.

## The end state

Product Driver should progressively explore a **behavioural and operational possibility
space**, not merely generate additional tests for the task in front of it:

```
seed scenarios → mutations → combinations → observed failures
      ↑                                            │
      │                                            ▼
 durable memory ←── corrections ←── neighbouring scenarios ←── harder scenarios
```

with **durable scenario memory across runs**, so a defect family discovered once
influences future verification whenever related code or behaviour changes. Generated
situations should vary ordering, concurrency, timing, stale/conflicting/missing evidence,
retries, crashes and restarts, external-system failure, human intervention, authority
change, redelivery, partial completion — and combinations of those.

Three constraints hold permanently, and every gap below must be closed without touching
them:

* **Bounded.** Not random combinatorial fuzzing. Every axis is a closed vocabulary with
  stated ranges, and an out-of-range value is refused rather than clamped.
* **Requirement-grounded.** Every scenario cites a requirement or a product principle that
  actually exists in the repository or the founder context, or it is refused.
* **Risk-prioritised and realistic.** The measure is behavioural and risk-space coverage,
  never code coverage.

Long term, when freight workflows arrive, the same machinery should model realistic
operational worlds — dispatch, tracking, appointments, driver communication, documents,
billing, exceptions — and produce plausible interacting situations a founder would not
manually enumerate.

## What already exists, and should not be redesigned

The within-run loop is already the shape described above, and it is sound:

| piece | where |
|---|---|
| staged generation: seeds → diff refinement → adaptive | `scenario_planner.py`, stages `initial` / `diff_refinement` / `adaptive` |
| neighbouring-risk expansion after a failure | `scenario_plan.RISK_FAMILIES`, `neighbours()` |
| failure clustering (shared-cause grouping, deterministic) | `failure_clustering.cluster_failures` — union-find, no model consulted |
| bounded expansion on every axis, refusals recorded | `ScenarioGenerationConfig` budgets; `WaveRecord.rejected` |
| one authoritative taxonomy, derived by every layer | `scenario_plan.RiskCategory` → `RISK_CATEGORY_VALUES` → `PLAN_SCHEMA` enum, `GENERATOR_SYSTEM`, the parser, `RiskClaim` |
| the safety boundary | `scenario_validation.ApprovedCommands` — prefix match, argument tails only, quote-aware shell-operator scan, control-character refusal |
| the quality boundary | `scenario_validation.validate_plan` — grounding, oracles, duplicate coverage, effect-family state-check requirement |
| the acceptance gate | `scenario_gate.evaluate_gate` — deterministic; no model participates |
| investigation as evidence *into* generation, never the reverse | `investigator.py` → `GenerationBasis.investigation_findings` |

**Do not weaken any of these to close a gap below.** In particular, the closed
`RiskCategory` enum and the human-authored-only approved command set are load-bearing: a
model that can widen its own taxonomy or author its own shell has neither a bounded space
nor a boundary.

### A candidate the harness cannot read is not a candidate it decided against

The enum is closed at the *schema* the generator answers into — `PLAN_SCHEMA` derives its
`risk_category` enum from `RISK_CATEGORY_VALUES`, and so do the system instructions. It was
once closed only in the parser, with `{"type": "string"}` on the wire and the vocabulary
retyped in prose; the P6/M6 re-verification run duly proposed nine scenarios under nine
invented labels, every one was discarded at the parse stage, and the run reported
`0 generated case(s)` and ACCEPTed. Neyma logged that as `P6-D46`.

So rejections are classified by the **stage** that made them, and the two kinds are never
summed:

* `REJECTED_FILTERED` — `validate_plan` onwards. A duplicate, a safety or quality refusal,
  a budget. Product Driver understood the proposal and said no. Coverage is narrower on
  purpose; nothing is wrong; acceptance is unaffected.
* `REJECTED_CONTRACT` — the parse stage. The payload did not satisfy the schema this harness
  itself authored and handed to the generator, so the candidate never became a model and
  **what it would have exercised is unknown**.

`WaveRecord.accounting()` states all four numbers — proposed, accepted, filtered, invalid —
and `CoverageSummary` carries them into `scenario-plan.json`, so `total_scenarios` can never
be read alone.

A contract rejection is a `generation_problems()` entry, which is the existing channel
`evaluate_gate` already refuses to accept on. **That holds for a mixed wave too**, and the
reason is the definition above rather than a severity judgement: nothing can say whether the
candidates that did run reach a risk nobody can name. It is the same unquantifiable gap a
failed reasoner leaves, and this method already blocks on that even when other waves produced
good coverage. Same fact, same answer. Deliberately *not* a new verdict kind: an unknown
category is neither a scenario failure nor a product defect, and the run must read as
BLOCKED-on-the-harness, which `generation_problems()` already produces.

There is no alias map. The only normalisation is the surrounding-whitespace-and-case fold the
parser has always applied, which is a format variation and not a change of meaning:
`cross-tenant-leak` is one hyphen from `cross_tenant` and is still refused, because guessing
which risk a model meant is how a harness starts verifying something nobody asked for.

---

## The gaps

### G1 — There is no cross-run memory. *(the biggest one)*

Everything the driver learns is discarded at the end of the run.

* `ScenarioPlanner.DefectMemory` is an in-process dict, lost when the process exits.
* `investigation_memory.py` persists thoroughly — under `runs/<run-id>/investigation/`.
* `PromotionLedger` writes `runs/<run-id>/promotion-candidates.json`.

The only durable cross-run channels are `founder_context/` (promoted by hand) and
`scenarios/*.yaml` (via the explicit `scenarios promote` command). So a defect family
found in run *N* cannot influence run *N+1* unless a human promotes a scenario for it.
Cumulative hardening is not possible without this.

**What it would need.** A defect ledger at driver root — *not* under `runs/` — keyed by
`(risk_category, behavioural signature, touched surface)`, holding for each entry the
failing generated scenario model, its `--seed`, the observation, and the commit range it
was found against. `ScenarioPlanner.__init__` loads it; a relevance filter matches
recorded surfaces against `GenerationBasis.diff_files`; matches render into
`GenerationBrief` as a new section — *"defect families this repository has actually had,
in surfaces this change touches"*.

**What it must not do.** It informs *proposals* only. Every recalled scenario still passes
`validate_plan` and still faces the gate. It must not be implemented by relaxing
`promotion_requires_approval`, which is validated to be un-settable to false on purpose —
this is a *second* channel, deliberately weaker than permanent coverage: memory that
influences what gets generated, not memory that silently becomes required.

### G2 — Concurrency is HTTP-only, so dark units have no concurrency primitive.

`ScenarioStep.kind` offers `parallel_requests` and nothing else concurrent, and
`SuiteExecutor.MAX_PARALLEL` is 1 (correctly — scenarios share services, ports, databases
and a workspace, and nothing yet proves a given pair isolated).

Consequence: for a unit that ships dark — every P6 unit so far — there is **no service and
no HTTP surface**, so the generator cannot compose ordering, concurrency, timing, crash or
redelivery variation at all. The whole possibility space collapses to "which case do I
run".

**Today's workaround, used by `p6_m3_external_effect` and `p6_m4_approval`:** push the axis
into the unit's own probe as a closed, bounded, argument-only vocabulary (`--concurrency`,
`--delay-ms`, `--repeat`, `--tenants`, `--seed`, `--inject <closed set>`, plus whatever
extra axis the unit genuinely has — M4 adds `--signers`, because dual-control quorum size is
a real variation and nothing else can reach it). This works, needs no change to the safety
boundary — a prefix match already permits argument tails and already refuses shell — and is
what keeps both doors open. Its cost is that the concurrency and fault-injection model is
re-implemented per unit instead of being owned by the driver, and it is now re-implemented
twice, which is the second data point rather than a new problem.

**The M4 addition worth naming separately: a fault vocabulary can be closed in a way that
protects a recorded gap.** Residual `G2-D15` in the target repository records that AP-9's
`frozen` flag has no modelled unfreeze direction — no transition clears it, no
`ApprovalUnfrozen` event exists. `p6_m4_approval` therefore runs `--inject unfreeze` as a
*second* negative control beside `--inject not-a-real-fault`, and asserts over the corpus
that no unfreeze surface was invented. A probe that ACCEPTED an unfreeze fault would be
producing passing evidence for a transition nobody authorized, which is exactly how a
recorded residual gets quietly closed by a build session. Closure of the vocabulary is not
only an anti-fuzzing property; it is also where "do not invent canon" becomes mechanical.

**What it would need.** A `parallel_commands` step kind, and the isolation proof that
`ScenarioSuite.isolation_groups` already computes but which nothing currently acts on.

### G3 — A mutation is not distinguishable from a seed.

`GeneratedScenario` carries `risk_category` — *which* situation — but nothing expressing
*how it is parameterised*, and no link to what it was derived from. So "the same situation
under 4-way concurrency with a lost response" is a brand-new scenario id with no recorded
lineage to its parent. Mutations and combinations are structurally indistinguishable from
fresh seeds, which makes "seed → mutation → combination" impossible to reason about, report
on, or bound per-family.

**What it would need.** Additive and cheap: `variation_of: str` and a bounded
`dimensions: dict[str, str|int]` on `GeneratedScenario`, carried through
`ScenarioProvenance`, so the plan records a lineage tree rather than a flat list. Budgets
could then be expressed per lineage ("at most 4 mutations of any seed") rather than only
per risk category.

### G4 — Coverage is a tally, not a space.

`CoverageSummary` counts `by_risk_category` and `by_priority`. That answers *how many*, not
*which regions are unexplored*. The only genuine space measure is
`scenario_gate.uncovered_required_risks`, and it can only report risks **the run itself
named** — it cannot report a region nobody thought to name.

**What it would need.** A declared space — risk category × dimension (× domain, see G5) —
with recorded occupancy, so "authorization has never been exercised under restart" becomes
computable rather than something a human happens to notice. This is the concrete form of
"behavioural coverage rather than code coverage".

### G5 — There is no world model for the freight phase.

Generation grounds in the repository's unit registry and acceptance criteria
(`grounding_tokens_from`) plus founder rubric ids (`principle_tokens_from`). That is right
for P0–P8 engine work, where the nouns *are* the machines. For P9+ the generator will need
a model of operational entities and their plausible interactions — loads, stops,
appointments, drivers, documents, invoices, exceptions — to produce situations a founder
would not enumerate. `RiskCategory` largely still applies: what is missing is the
**noun-space**, not the failure-space.

**Where it belongs.** `founder_context/PRODUCT_TASTE_RUBRIC.yaml` is already durable,
already content-hash versioned, and already a validation grounding source. A freight world
model has a home there and needs no new plumbing — it simply has no content yet.

### G6 — Nothing measures whether generation is getting better.

There is no record, across runs, of how often generated coverage caught a defect the
permanent suite missed. Promotion candidacy is the closest signal and it is per-run. Without
this, no one can tell an expanding possibility space from an expanding token bill.

**What it would need.** Falls out of G1 almost for free: the same driver-root ledger, with
a per-entry record of which stage proposed the scenario that found each defect.

---

## Ordering

G1 first — it unlocks G6, and cumulative hardening is impossible without it. G3 next,
because it is additive, cheap, and G4 depends on the lineage it records. G2 when a unit
with a real service surface makes `parallel_commands` worth proving isolation for. G5 at
P9, not before: a world model written against no freight code would be fiction.

---

## Risk coverage: how a risk becomes verified (added 2026-08-20)

Written after run `20260820-204803`, which blocked with M3 implemented, 46 tests passing,
9/9 mutants caught, every regression anchor green and 13/13 scenarios passed — on six
"uncovered" risks that the permanent scenario had just exercised and passed.

**What was wrong.** The acceptance gate could see coverage through exactly one channel:
does some *generated* scenario carry the risk's `risk_category` tag? Permanent and probe
coverage tags nothing, so it counted for nothing no matter what it proved. The only
available response was to ask the builder for coverage that already existed, and asking
again produced the same answer.

Worse, it did not stand still. Each generation wave adds to the risk register, and a wave
launched with nothing failed ran as the *adaptive* stage — whose every proposal must cite
an observed failure. With nothing failed there was no failure to cite, so every proposal
was refused for the same reason while the wave's new risks joined the register anyway.
Two known gaps became six across three iterations *while the builder was adding valid
coverage*.

**The mechanism now.** A permanent scenario declares what it verifies, and the declaration
names its own oracles:

```yaml
verifies:
  - risk_category: retry_safety              # closed RiskCategory; unknown = load error
    claim: "replay mints no grant and touches nothing outside"
    checks: ["drive the machine through a brokerage narrative"]   # must exist in this file
    observations: ["replay: 0 grants, 0 claims, 0 EffectAttempted"]
```

The executor resolves each claim against what actually ran: every named check must have
executed and every assertion it produced must have passed, and every literal must appear
in **those checks'** output. The result is recorded on the `ScenarioResult`, travels onto
the `ScenarioOutcome`, and is persisted in the case evidence.

The gate then satisfies a risk through one of exactly two attachments, both requiring the
outcome to have PASSED with resolvable evidence:

1. an established `verifies:` claim naming the category;
2. a generated scenario carrying the category (unchanged).

There is no third. No similarity, no neighbouring category, no "the tests passed", and no
channel through which a generator or an evaluator can assert a risk into the covered list
— `GeneratedScenario` has no field that could express a claim, the compiler emits none,
and the suite discards any that arrive on a generated entry.

**Convergence.** Three things changed together, and each alone leaves the loop broken:

* the planner reads the executed base scenario's `verifies:` block, so a risk permanent
  coverage already exercises is not reported as a planning gap — which is what made the
  generator propose a duplicate that validation then correctly refused;
* a wave with no observed failures runs as `coverage_gap`, whose proposals cite the
  identified **risk** they close rather than a failure that never happened;
* the brief names the still-uncovered risks, so a wave is aimed rather than wandering.

An honestly unclosable gap still blocks. That is the correct outcome, and the M3 scenario
deliberately declares nothing for `restart_recovery` or `timeout_after_effect` — both are
exercised inside the probe, neither prints a literal a claim can bind to — so a run naming
either as blocking must generate a case for it.

## The route a coverage-gap wave reaches

Aiming the wave was not enough on its own, because for a while nothing could reach it.
Stage 3 ran only after a `Decision.FIX`, and a run whose executed scenarios all passed
while a P0 risk had no evidence produced a `Decision.BLOCKED` from
`_apply_suite_precedence` — which the route terminated on immediately. The wave the
planner was ready to run had no way to be launched, so a founder had to ask for the
missing scenario by hand.

That shape now closes itself. When the gate's verdict is *only* about coverage — every
required scenario passed with resolvable evidence, nothing failed, no generation problem
stands, and acceptance-blocking risks remain uncovered — the loop runs
`_close_coverage_gaps` before the completion audit and before any reviewer is considered:

1. the wave is aimed by the **gate's** uncovered set, mapped back to the plan's own
   `IdentifiedRisk` entries so each proposal can cite a key. The evaluator's
   `scenario_requests` are deliberately not passed; a targeted wave that also carries a
   wishlist stops being targeted;
2. the proposals are validated and compiled by the ordinary path, against the ordinary
   approved command vocabulary. Nothing widens command authority to make a scenario
   possible;
3. the new cases execute, and their results are merged into the run's suite record with
   `merge_suite_results` — which takes `expected_required_ids` from the *widened* suite and
   recomputes `full_run`, so adding coverage can only add obligations;
4. the same `evaluate_gate` decides again, from the new execution records. That is the only
   way a risk leaves the uncovered list.

If a generated case fails, that is an observation about the product: suite precedence turns
it into a grounded FIX carrying the real failure, sent to the same builder session, and the
loop retests. Coverage absence never becomes a fabricated correction.

`BLOCKED` stays reachable and stays terminal for the reasons it should be: the generation
budget is spent while a blocking risk is uncovered, the wave produces nothing runnable for
the risk (the approved vocabulary cannot express it), generation itself failed, evidence
cannot resolve, or a genuine product or authority decision is owed. Each closure round
consumes one of the planner's bounded waves, so the loop is bounded by the budgets that
already existed.

## Independent review as a step in the same loop (added 2026-08-22)

The P6/M3 run exposed a gap on the other side of verification. Product Driver built M3,
generated adversarial coverage for it, executed the suite, closed a coverage gap, audited
the completion claim — and then stopped at `AWAITING_INDEPENDENT_REVIEW` and waited for a
human to run a separate command. That relay is now a transition inside `run_control_loop`,
and the parts of it that were load-bearing are recorded here so the next person does not
re-derive them.

### The review is aimed at the scoped task, not the phase

`resolve_review_requirement` (in `review_cycle.py`) is asked once per iteration, fresh, and
it asks the repository about **the unit this run was asked to build**. The two narrowings
that make that work are not obvious and both are tested:

* A phase's `independent_review` acceptance criterion describes what the *phase* owes at
  phase acceptance. A nested unit inside it inherits none of it. Reading the criterion as
  the unit's requirement demands a review of thirteen units, twelve of which do not exist.
* `TaskScope.claims_phase_completion` is the strict **evidence** default — a task naming no
  unit is held to the phase's bar — and it is not a statement of intent. A new field,
  `phase_completion_requested`, is true only when the task text actually asked for the
  phase to be completed or accepted, and that is what the phase-level trigger reads.

The repository's own protocol rule binds where the task-scope machinery says a unit is
being built, which is the same test `CompletionAuditor._task_review_outstanding` already
applied. `INDEPENDENT_REVIEW` remains outside `RETIRABLE_KINDS`, so no sentence anywhere
can retire it; a self-declared historical document mentioning it still binds nothing,
because it is not current authority.

### The reviewer can execute, under the vocabulary that already existed

The same gap this document opens with — *the possibility space is bounded by what the
approved command vocabulary can express* — turns out to bound the **reviewer** too. A
reviewer that cannot run the probe can only re-read what the harness captured, and the
harness's honesty is then a premise of the review that exists to check it. During the M3
review that is exactly what happened.

`reviewer_boundary.py` gives the reviewer a shell on the condition that a command is
**both** an appropriate read-only verification action **and** already allowed by Product
Driver policy. The second half is deliberately not a new allowlist: it is
`classify_command` plus `ApprovedCommands` — the same vocabulary a generated scenario draws
from, harvested from the same scenario files. A reviewer can run
`scripts/probe_phase6_external_effect.py --case forged-capability` for precisely the reason
a generated scenario can: a human wrote that entry down, and prefix matching permits an
argument tail while refusing shell composition.

Above both gates sits a floor that applies **however a command was approved**: no git
mutation, no file mutation, no redirection, no network, no installer, no privilege change,
no secret path, no composition. A repository that one day writes `git commit` into a
scenario file must not thereby hand a reviewer the ability to commit.

Enforcement is a `PreToolUse` hook, not a prompt instruction, and not the permission
callback — a whole-tool `allowed_tools` entry shadows `can_use_tool`, so adding `Bash` to
the allow list while relying on the callback would have handed the reviewer an
unrestricted shell.

### A review is evidence about one exact tree

`TreeFingerprint` is HEAD, the HEAD tree, and a digest over the working tree on top of
them. The third component is the one that does the work: the implementation under review is
usually uncommitted, and the first two do not move when a builder corrects something
without committing.

The digest covers `git diff HEAD` and the **names** of untracked paths, not untracked
content. That is a deliberate line: a run writes evidence, caches and artifacts into the
tree constantly, and hashing all of it would retire every review for reasons that have
nothing to do with the implementation — while a new untracked path is still a change of
state worth noticing.

`ReviewLedger.invalidate_stale` runs *before* the requirement is consulted, so a review of
an older tree can never be mistaken for an answer, and the retirement is written into the
run evidence rather than happening silently. `CompletionAuditor` re-derives the current
fingerprint itself rather than trusting the record it is handed.

### The four owners of a stuck review

The routing in `route_review` exists because `NOT_SUPPORTED` and `INSUFFICIENT_EVIDENCE`
are not two shades of one answer, and because a run that sends the second to the builder
asks it to change working code to fix a measurement problem. The measurement problem
survives; the loop does not converge. `blocked_on.kind` keeps them apart:

| kind | owner |
|---|---|
| `PRODUCT_DEFECT` | the builder — a grounded correction, then a **new** reviewer |
| `VERIFICATION_HARNESS` | **Product Driver** — fix the driver, never manufacture a Neyma change |
| `REVIEWER_CAPABILITY` | the approved vocabulary, or an honest admission that this cannot be reviewed automatically |
| `REPOSITORY_AUTHORITY` | the founder |
| `EXTERNAL_ACTION` | the founder, with the exact requested action, and nothing fabricated in its place |

A reviewer that reports insufficient evidence *without having used the execution it was
given* is asked once more with the vocabulary spelled out. Once — a second identical answer
is the answer, and the run fails closed.

### What this does not change

Nothing here scores a repository criterion, writes a status file, moves a phase, or pushes
anything. The reviewer writes nothing at all. `scenarios promote` remains the only way a
generated case becomes permanent, and it remains a human's decision.

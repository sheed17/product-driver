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
| the safety boundary | `scenario_validation.ApprovedCommands` — prefix match, argument tails only, quote-aware shell-operator scan, control-character refusal |
| the quality boundary | `scenario_validation.validate_plan` — grounding, oracles, duplicate coverage, effect-family state-check requirement |
| the acceptance gate | `scenario_gate.evaluate_gate` — deterministic; no model participates |
| investigation as evidence *into* generation, never the reverse | `investigator.py` → `GenerationBasis.investigation_findings` |

**Do not weaken any of these to close a gap below.** In particular, the closed
`RiskCategory` enum and the human-authored-only approved command set are load-bearing: a
model that can widen its own taxonomy or author its own shell has neither a bounded space
nor a boundary.

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

**Today's workaround, used by `p6_m3_external_effect`:** push the axis into the unit's own
probe as a closed, bounded, argument-only vocabulary (`--concurrency`, `--delay-ms`,
`--repeat`, `--tenants`, `--seed`, `--inject <closed set>`). This works, needs no change to
the safety boundary — a prefix match already permits argument tails and already refuses
shell — and is what keeps M3's door open. Its cost is that the concurrency and
fault-injection model is re-implemented per unit instead of being owned by the driver.

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

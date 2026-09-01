# Build P6 / M10 — Compensation. Only that.

You are building **one unit**: machine **M10**, the **Compensation** — the undoing of an external
effect that should not have happened.

Not P6. Not the phase. Not M11, M12, M13. Not an oversight queue, a dashboard, a notifier or a
freight workflow. **One machine, its table, its migration, its acceptance battery, its probe, its
mutation battery — and nothing else.**

M9 (the Exception) landed as `P6-CP-9`. `docs/implementation/CURRENT.md` says, in its own words,
**"The next build checkpoint is M10 — the Compensation."** That is this unit.

---

## 0. Read the authority first, in this order

**Read these completely before writing a line.** Not skimmed, not grepped for keywords. The whole
point of this unit is that a single missing sentence turns a safety mechanism into an ungated write.

**Status and standing orders**
1. `CLAUDE.md` — the operating rules. §5 rule 17 (one authority per domain), §7 (review tiers),
   §0 (no committed receipts).
2. `PRODUCT.md` — what Neyma is for.
3. `docs/implementation/CURRENT.md` — **the short-form status authority.** Read the P6 table, the
   Posture row, the *Still owed* row, and **⛔ What must NOT begin**.
4. `docs/implementation/IMPLEMENTATION-REGISTRY.yaml` — the binding per-phase detail. On any
   disagreement with the roadmap, **the registry wins**. Read the P6 unit and its debt rows.
5. `docs/implementation/implementation-roadmap.md` — the 16 migration principles. Principle 13
   especially: **no phase depends on future work for a CURRENT safety guarantee.**

**The M10 canonical corpus**
6. `docs/specifications/entities/13-compensation.md` — all 45 points.
7. `docs/specifications/state-machines/10-compensation.machine.md` — all 43 sections, and §14's
   transition table **row by row**.
8. `docs/specifications/events/10-compensation-events.md` — family F10.

**Registries and global contracts**
9. `docs/specifications/state-machines/registry.md` — §4 (the state sets), and the `GR-*` rules.
10. `docs/specifications/events/registry.md` — **by its own header THE SOLE CANONICAL LIST of event
    names.** §3 (one producer transition per event), §9 (the ‡ coordination events), the `ER-*`
    rules. F3 and F10 in particular.
11. `docs/specifications/entities/00-conventions.md` — `[C-1]`…`[C-10]`, and **K-1**, the
    `decision_ref` resolution rule.
12. `docs/specifications/acceptance/foundational-machine-acceptance.md` — M10 is **9 transitions,
    `AC-MACH-1001..1009`**, gate **G4**.
13. `docs/specifications/acceptance/platform-safety-acceptance.md` — `M-33`, `M-47`, `AC-SAFE-*`.
14. `docs/specifications/acceptance/recovery-and-compensation-acceptance.md` — **`AC-REC-001..005`
    and `AC-RACE-013` are this unit's acceptance criteria.** Read them first, not last.
15. `docs/architecture/target-system-specification.md` **§12.10** and **every** Compensation
    reference in the file — §10.2 (correction propagation), §12.4/§12.5 (the effect and grant
    lifecycles), §19 (verification), §21.5 (the brake), §26.4, and the `M-33`/`M-47` blocks.
16. Every ADR those authorities cite — **ADR-002** (evidence conditions), **ADR-003** (only a human
    approves), **ADR-005** (the approval fingerprint), **ADR-006** (verification and unknown
    outcomes), **ADR-008** (durable workflows — its §"COMPENSATION_FAILED | NOT_POSSIBLE" row),
    **ADR-009** (the commit key), **ADR-010** (policy and gates), **ADR-011** (the brake).

### Then read the LANDED CODE, not the prose about it

Every seam below is **already built**. You are wiring into it, not re-deciding it. Read the source.

| Seam | Read | Why it is load-bearing for M10 |
|---|---|---|
| M1 Work Item | `src/freight_recon/work_item.py` | `tenant_humans`, `human_authority`, and **`resolve_decision_ref` — the ONE K-1 executor.** M2, M3 and M9 all import it. So does M10. |
| M2 Pipeline Instance | `src/freight_recon/pipeline_instance.py` | `propose()`, the 25 rows, `PL-15`, `PipelineState`, the reservation index. **CM-3 starts one of these.** |
| M3 External Effect / Grant | `src/freight_recon/external_effect.py` | `EffectGrantState`'s **eight** states, `EF-5`, `_resolve_decision`. **CM-1's guard reads this ledger.** |
| M4 Approval | `src/freight_recon/approval.py` | `request()`, `grant()`, the `fp_v1` fingerprint, `AP-7` consumption. **CM-2 binds one of these.** |
| P3 checkpoint | `src/freight_recon/checkpoint.py` | The **seven steps**, `GateRegistry`, `GateDecision`, `mint_grant`, `claim_grant_cas`. **The compensating effect passes all seven.** |
| P3 brake | `src/freight_recon/brake.py` | `BrakeStore.admission_denied`, `version_token`, **`narrow()`**, the ratchet, `BrakeStoreUnreachable`. |
| Commit key | `src/freight_recon/commit_key.py` | **`CANONICAL_OCCURRENCE_SOURCES["adjust_invoice"]` already names `compensation_id` / entity `Compensation`.** Read this before you invent anything. |
| Money | `src/freight_recon/fingerprint.py` | `Money(amount_minor: int, currency: str)`. **Floats and `Decimal` are refused at construction.** |
| Events | `src/freight_recon/event_contracts_data.json`, `event_envelope.py`, `event_outbox.py`, `event_inbox.py` | The **118 registered contracts**, including all seven F10 names and F3's `RealityEstablished`. |
| Timers | `src/freight_recon/event_timers.py` | `TimerFired` is a **trigger, not a canonical event**, and the module names `COMPENSATION_FAILED` as a state no timer may move. |
| Replay | `src/freight_recon/event_replay.py` | `GR-11` / `ER-2`: replay produces zero effects. |
| Schema | `src/freight_recon/schema.py`, `migrations/phase6_*.py` | The canonical table set, the tenant-first partition, `schema_readiness_problems`. |
| Correction, as it exists today | `src/freight_recon/identity_binding_claim.py` | `IB-7` emits the **registered** `ClaimCorrected` and writes `propagation_obligation` **naming** the completed effects. **That is the correction seam M10 consumes.** |
| M9 Exception | `src/freight_recon/exception.py` | `raise_exception` / `raise_from_failure`. The F10→M9 escalation seam points here. **Read it; do not wire it.** |

### How to weigh them

When two authorities disagree, they are ordered:

1. `events/registry.md` is **the sole canonical list of event names.** A name that is not in it is
   not an event, however many machine files use it as a trigger phrase.
2. `docs/architecture/target-system-specification.md` §12.10 and the machine file agree on the
   transition set. Where the entity file's prose is looser, the **machine table** governs.
3. `IMPLEMENTATION-REGISTRY.yaml` beats the roadmap.
4. **The landed code beats every document about the landed code.** If `commit_key.py` says the
   occurrence source for `adjust_invoice` is a `Compensation`, that is what it is.

**Where they genuinely conflict, you RECORD the conflict and build the fail-closed side. You do not
resolve it.** §3.8 lists the twelve conflicts already found. Finding a thirteenth is a good outcome;
silently settling any of them is not.

---

## 1. What Neyma is — the stable identity

Neyma is an operational teammate for freight brokerages. It watches the systems a broker already
uses, notices what is owed, and — where it is allowed to act — acts **through a gate**: a checkpoint
that revalidates the facts a human approved, a single-use grant, one commit key per logical effect,
and a readback that proves what actually happened in the outside world.

The whole architecture exists because of one asymmetry: **a wrong external effect costs real money
and cannot be un-run.** Everything in the kernel is a consequence of taking that seriously.

M10 is where that sentence gets tested hardest — because a Compensation is the system's own attempt
to fix a wrong effect, and it is exactly the place where a well-meaning engineer reaches for a fast
path.

---

## 2. Where the program stands

- **P0–P5 COMPLETE.** The commit key, tenant-first persistence, the seven-step checkpoint, adapter
  containment, canonical events, outbox/inbox, replay isolation, durable timers.
- **P6 IN PROGRESS.** M1–M9 have landed as `P6-CP-1` … `P6-CP-9`. All nine ship dark.
- **104 of 134 transitions are written and landed.** M10's nine take it to 113.
- **P6 `criteria_scored` is `[]`.** No P6 weighted criterion is scored. P6 has **not** reached phase
  acceptance. **P7 is BLOCKED.**
- **The next build checkpoint is M10.** M11–M13 are not yours.

**The CI position you are starting from, stated exactly.** At the M9 head `2e5dcb2`: the full suite
passed on **Python 3.11 (3099 passed, 1 skipped)** and on **Python 3.12 (3099 passed, 1 skipped)**;
the P6/M3 effect-grant probe and mutation job **succeeded**; **Safety invariants (fast) was CANCELLED
at its runtime ceiling with no demonstrated product failure**; Risk radar was skipped.
**Do not describe that workflow as fully green — a cancelled job is not a passing job. And do not
reopen M9 because of it: no product failure was demonstrated.**

---

## 3. The unit: M10, and nothing else

### 3.1 The four sentences the whole unit is a consequence of

> ### **A COMPENSATION IS THE UNDOING OF AN EXTERNAL EFFECT THAT SHOULD NOT HAVE HAPPENED.**
> ### **THE COMPENSATION IS ITSELF A SEPARATELY GATED EXTERNAL EFFECT. IT RECEIVES NO PRIVILEGED PATH.**
> ### **YOU CANNOT UNDO WHAT YOU CANNOT PROVE YOU DID.**
> ### **AN "UNDO" THAT BYPASSES THE GATES IS AN UNGATED WRITE WITH A GOOD EXCUSE.**

In freight terms. A carrier's POD was bound to the wrong load. An invoice for £2,850 went out to
Acme on the strength of that binding. Weeks later a human corrects the binding, and **Invoice #560010
is now known to rest on a fact that was wrong.** The money left the building. Something has to credit
it back.

The tempting implementation is a rollback: find the effect, call the adapter's void endpoint, mark
the row undone. **That is a second, ungated write route into a customer's accounting system, built by
the people who most believe they are being careful.** It has no approval, no checkpoint, no grant, no
commit key of its own and no readback — and it is reached precisely when the system is already known
to be wrong about something, which is the worst possible moment to trust it.

So M10 does the opposite. The credit note is **a new external effect**: its own Pipeline Instance,
its own policy evaluation, its own brake check, its own human approval, its own checkpoint witness,
its own single-use Effect Grant, its own commit key, its own readback. The Compensation record is
only the *obligation* to do that, with a named human owner and the dollar amount at stake written on
it from the moment it exists.

And when the original effect's outcome is **unknown** — the TMS timed out and nobody can say whether
the invoice was issued — M10 **refuses to compensate at all**. Not "tries carefully". Refuses. Because
*"cancel invoice #560010"* against a system where no such invoice exists can, in some accounting
systems, **create a credit note out of nothing** — the compensating write manufactures the very state
it was trying to remove. A human resolves the unknown to `VERIFIED` or `FAILED` first. Only then may
compensation be considered. That is **M-33**, and it is the single most important rule in this unit.

### 3.2 The canonical state set

**Exactly six. Registry §4 / M10, entity §12, target spec §12.10.**

```
REQUIRED  ·  APPROVED  ·  EXECUTING  ·  COMPLETED  ·  COMPENSATION_FAILED  ·  NOT_POSSIBLE
```

- **Initial:** `REQUIRED` (machine §6).
- **Terminal:** ### **`COMPLETED`, and only `COMPLETED`** (machine §8).
- **Non-terminal, human-owned:** ### **`REQUIRED`, `COMPENSATION_FAILED`, `NOT_POSSIBLE`**
  (machine §9, entity §12).
- **Recoverable:** `APPROVED`, `EXECUTING` (machine §10).
- **Expiry:** ### **NEVER** (machine §12, entity §26).

> ### **`COMPENSATION_FAILED` AND `NOT_POSSIBLE` ARE THE MOST DANGEROUS STATES THE SYSTEM CAN BE IN**
> — reality and the projection are **known** to diverge (entity §42, machine §38). They must be loud,
> owned by a named human, and carry the exposure. They are not tidy, and they are not supposed to be.

### **DO NOT INVENT `CANCELLED`, `EXPIRED`, `TIMED_OUT`, `ROLLED_BACK`, `RETRYING`, `RESOLVED`,
### `SUPERSEDED`, `REVERSED`, `UNDONE`, `PENDING`, `FAILED`, `ABANDONED` OR ANY SEVENTH STATE.**

Each of those is a real temptation with a specific defect behind it:

- `CANCELLED` — entity §25: cancellation is **N/A once `REQUIRED`, because the exposure exists.** A
  compensation you cancel is money you decided to stop tracking.
- `EXPIRED` — entity §26 says **NEVER**, in that word. An exposure does not age out.
- `RETRYING` — machine §20: ### **a failed compensation is NOT auto-retried; a human decides.**
- `RESOLVED` — that is M9's registered state name. Registry's binding header forbids a local synonym.
- `FAILED` — the state is `COMPENSATION_FAILED`. The name is longer on purpose: it is not the same
  fact as a pipeline failing, and collapsing them loses the exposure.

### 3.3 What M10 emits, and what it consumes

**The seven registered F10 contracts — and there is no eighth.** These are already in
`event_contracts_data.json`; you are not registering them, you are emitting them. Read the file for
each one's exact required fields.

| Event | Producer | Required payload |
|---|---|---|
| `CompensationRequired` | CM-1 | `original_effect_id`, `exposure`, `reason`, **`decision_ref`** |
| `CompensationRefused` | CM-1r | `original_effect_id`, `cause` — ### **fixed to the literal `unknown_outcome`** |
| `CompensationApproved` | CM-2 | `approval_id` |
| `CompensationImpossible` | CM-2n | `exposure` |
| `CompensationStarted` | CM-3 | `pipeline_instance_id` |
| `CompensationCompleted` | CM-4 | — |
| `CompensationFailed` | CM-4f | `exposure` |

### **DO NOT MINT `CompensationCancelled`, `CompensationExpired`, `CompensationRetried`,
### `CompensationReversed`, `CompensationSucceeded`, `CompensationAbandoned`, `CorrectionInvalidatedAnEffect`,
### `NoCompensatingActionExists`, `CompensationRealityEstablished` OR ANY OTHER NAME.**
The registry is the sole canonical list. A name absent from it is not an event.

**`RealityEstablished` — read this twice.** CM-5's event is `RealityEstablished`, and it is a
**registered F3 coordination event with two structurally-identical producers, `EF-5` and `CM-5`**
(registry §9, the ‡ marker). One contract, `aggregate_type: effect_grant`, `coordination: true`,
required payload `decision_ref`, `outcome ∈ {VERIFIED, FAILED}`, **`subject ∈ {effect, compensation}`**.

> ### **M10 EMITS THAT EXACT CONTRACT WITH `subject="compensation"`. IT MINTS NO F10-LOCAL
> ### `RealityEstablished`, NO SECOND CONTRACT, NO VARIANT AND NO SUBTYPE.**

Two contracts for one semantic fact is precisely the "second authority that means the same thing"
that `CLAUDE.md` §5 rule 17 forbids — and the way they drift apart is that one of them stops
requiring the `decision_ref`.

**What M10 consumes.** Entity §32 and machine §33 name:
`CorrectionInvalidatedAnEffect`, `HumanApproved`, `NoCompensatingActionExists`,
`PipelineClosed`/`PipelineFailed`/`NEEDS_VERIFICATION`, `HumanEstablishedReality{decision_ref}`.

### **FOUR OF THOSE SIX NAMES ARE NOT CANONICAL EVENTS. Verify it yourself before you build a
### consumer for any of them** — read `event_contracts_data.json` and check. §3.8 records what the
mechanical check returns and what it means. **Do not mint a contract to make a prose list true.**

### 3.4 Implement the canonical `CM-*` transition contract

**Nine rows. `AC-MACH-1001..1009`. An exact set match with machine §14 — not eight, not ten.**

| ID | From → To | Trig | Guard | Writes | Event |
|---|---|---|---|---|---|
| **CM-1** | — → `REQUIRED` | S | ### the original effect is **`VERIFIED`** and now known wrong; `exposure`; `owner_id` (human); `decision_ref` (the invalidating correction) | `original_effect_id`, `reason` | `CompensationRequired{exposure}` |
| **CM-1r** | *(refused)* | S | ### the original effect is **`UNKNOWN_OUTCOME`** | — | ### `CompensationRefused{unknown}` — **WAITS for the human (M-33)** |
| **CM-2** | `REQUIRED` → `APPROVED` | H | ### money-affecting compensation is **ALWAYS `HUMAN_APPROVAL_REQUIRED`** (M4) | `approval_id` | `CompensationApproved` |
| **CM-2n** | `REQUIRED` → `NOT_POSSIBLE` | S | ### the world offers no undo (a sent email, a wire) | — | `CompensationImpossible{exposure}` |
| **CM-3** | `APPROVED` → `EXECUTING` | S | ### a **NEW M2 Pipeline Instance** starts — full checkpoint, grant, brake, policy | `pipeline_instance_id` | `CompensationStarted` |
| **CM-4** | `EXECUTING` → `COMPLETED` | X | the compensating effect **verified by readback** (M3 `VERIFIED`) | — | `CompensationCompleted` |
| **CM-4f** | `EXECUTING` → `COMPENSATION_FAILED` | S | its pipeline `FAILED` or `NEEDS_VERIFICATION` | `exposure` | `CompensationFailed{exposure}` |
| **CM-5** | `{COMPENSATION_FAILED, NOT_POSSIBLE}` → `COMPLETED` | H | `HumanEstablishedReality{decision_ref}` | `decision_ref`; co-commit M2 `{VERIFIED,FAILED}` | `RealityEstablished` |
| **CM-5x** | `COMPENSATION_FAILED` + `TimerFired` | T | — | — | ### ⛔ **ILLEGAL** — `NON_PRODUCING:GR1_ILLEGAL_REFUSAL` |

**Model the table as DATA**, the way `pipeline_instance.py`, `external_effect.py`, `conflict.py`,
`expectation.py` and `exception.py` do — a frozen `TransitionRow` tuple that `AC-MACH-000` can
enumerate and compare against §14. A transition table written as `if`/`elif` cannot be enumerated,
and a rule nobody can enumerate is a rule nobody can test.

**`CM-5x` follows M2's and M3's landed precedent.** Put `TimerFired` in the `Trigger` enum with **no
legal row anywhere**, so `GR-1` answers it uniformly at every one of the six states: it raises,
persists nothing, and records `IllegalTransitionAttempted`. That is strictly stronger than a special
case at `COMPENSATION_FAILED`, and it is derived from the table rather than written twice.

### 3.5 What must hold — the authority and safety requirements

Twenty numbered requirements. Each traces to a named authority. **A green test that does not
correspond to a database constraint or an executed guard is a sentence, not a mechanism.**

**1 — VERIFIED original only** *(entity §21, machine CM-1, `AC-REC-001`, M-33)*
A Compensation may be created **only** for a landed M3 External Effect whose persisted state is
`VERIFIED`. ### **Read the actual `effect_grants` row. Not a caller-supplied boolean, not a
parameter named `original_was_verified`, not a model's summary.** A caller flag is the same defect as
free-form `occurrence_key`: identity and eligibility may not enter through an argument the caller
controls.

**2 — UNKNOWN_OUTCOME is refused, and the refusal is complete** *(CM-1r, M-33, `AC-REC-001`)*
When the original is `UNKNOWN_OUTCOME`: **zero `compensations` rows**, one `CompensationRefused`
with `cause="unknown_outcome"`, **zero new Pipeline Instances, zero Effect Grants, zero adapter
calls, zero external effects.** `AC-REC-001`'s oracle is *"assert zero compensating calls"* — assert
it against real counters, not against a printed sentence.

**3 — the other six original states** *(M3's eight-state ledger)*
`FAILED`, `REVOKED`, `EXPIRED_UNCLAIMED`, `GRANTED`, `CLAIMED`, `ATTEMPTED`. CM-1's guard is not
satisfied, so **no Compensation is created**. ### **DO NOT MINT A `CompensationRefused` VARIANT FOR
THEM** — the registered contract fixes `cause` to the literal `unknown_outcome`, so there is exactly
one refusal cause and inventing a second is minting an unregistered contract in all but name. Fail
closed, and record the gap as an authority question (§3.8, `M10-AQ-10`).

**4 — invalidating authority is never MODEL_INFERRED** *(entity §13, machine §31, `GR-8`)*
The invalidating correction carries an authenticated human `decision_ref` **or** a deterministic rule
id. ### **A compensation is NEVER raised from a `MODEL_INFERRED` conclusion, and confidence — at
`0.4`, at `0.99`, at `1.0` — never substitutes for authority.** Confidence orders a queue and gates
nothing. Resolve the `decision_ref` through **M1's landed `resolve_decision_ref`, imported and never
reimplemented** — it is the one K-1 executor, and it already refuses a blank string, a string that
references nothing, a non-human-decision event type, and a human-decision event type recorded with
`actor_type != 'human'` (that last is authority laundering, `ER-11`). Its `RULE` branch **refuses
today** because M12 has no `rules` table; leave it refusing.

**5 — a named human owner from `REQUIRED`** *(entity §10/§16, machine §5, I1, `AC-SAFE-028`)*
`owner_id` is `NOT NULL` **from creation**, FOREIGN-KEY-backed into `tenant_humans`, same tenant, and
the human must be `ACTIVE`. ### **An ownerless Compensation must be structurally impossible — not
insertable, not merely un-createable through the API.** Follow M1's, M7's, M8's and M9's precedent
exactly. The caller supplies the human; the machine never picks one; `system` is not a human; a model
may never own one.

**6 — exposure, from `REQUIRED`, in canonical money, with its provenance** *(entity §10, F10's
family defaults, **K-4**)*
`exposure` is `NOT NULL` from creation. Use the canonical money shape —
**`Money(amount_minor: int, currency: str)` from `fingerprint.py`**. ### **A float is refused at
construction; so is a `Decimal`.** `2850.00` and `2850.0` are the same money and different bytes.
### **The exposure must remain visible through `REQUIRED`, `COMPENSATION_FAILED` and `NOT_POSSIBLE`.
Do not zero it, null it or "settle" it when the compensation fails or turns out to be impossible** —
those are exactly the states where the number is the whole point.

**And read `K-4` in `00-conventions.md` before you design the column.** It names **Compensation
explicitly** among the operational records a money field is permitted on, and it attaches a
**deterministic rule** the entity's §10 attribute list does not repeat: *"A money field on an
operational record MUST carry the `observation_id` (or effect/approval) it was read from; a money
field MUST NOT be populated from a knowledge-base recall."* M1's landed `work_items` table is the
precedent — `exposure_amount_minor` · `exposure_currency` · **`exposure_observation_ref`** — while
M7's and M9's bare `exposure TEXT` is the weaker form those two entities were allowed because their
exposure is an OPTIONAL annotation. **M10's is required and money-affecting.** See `M10-AQ-13`.

**7 — the exact six-state lifecycle, enforced by the schema** *(entity §12/§16)*
Enumerate the six inline on the `state` column with a `CHECK`, as M8 and M9 do. `COMPLETED` is the
only terminal state. No expiry column, no TTL, no `deleted_at`, no soft delete. ### **A `BEFORE
DELETE` trigger refuses the delete outright** (M9's precedent, entity §28 `[C-9]`, retention
permanent). No sweep, no reaper, no scan, no background job moves a Compensation.

**8 — `CHECK`: `EXECUTING` requires a bound `pipeline_instance_id`** *(entity §16, verbatim)*
A row in `EXECUTING` with no bound pipeline is not insertable. **This is the constraint that makes
"execution is a gated attempt" a fact the database states** rather than a sentence the machine
prints.

**9 — human approval at CM-2** *(entity §22/§40, machine §28, ADR-003)*
`REQUIRED → APPROVED` is **`H`** — an authenticated human. At minimum:
- `approval_id` resolves to an **actual M4 `approvals` row of the same tenant**, in `GRANTED`;
- that approval is **bound to this Compensation's material facts** — its `commit_key` is this
  Compensation's own `commit_key`, which is what M4's `fp_v1` fingerprint is computed over;
- a stale, wrong-commit-key, expired, revoked, consumed, void or **cross-tenant** approval is refused;
- ### **a model cannot approve, at any confidence.** M4 already enforces `actor_kind = HUMAN` with a
  FK into `tenant_humans` and a `CHECK`; consume that, do not restate it.

### **DO NOT BUILD A SECOND APPROVAL SYSTEM, AND DO NOT MODIFY M4.** And do not collapse
**`Compensation.APPROVED`** with **`Approval.GRANTED`/`CONSUMED`**: they are separate aggregates with
separate lifecycles. The M4 approval is *consumed* later, inside the executing pipeline's claim
transaction (`AP-7`, co-committed through P3's kernel) — **not at CM-2.**

**10 — CM-3 starts a NEW M2 Pipeline Instance, fully gated** *(entity §15, machine §4, `AC-REC-002`)*
Prove, against persisted artifacts and not against imports:
- a **new** `pipeline_instance_id`, distinct from the original effect's pipeline;
- policy evaluation (checkpoint step 6);
- brake admission (checkpoint step 7);
- an M4 approval bound at `PL-7b`;
- a **`CheckpointPassed` witness row**;
- an **M3 Effect Grant minted** and **claimed** through P3's untouched CAS;
- the external effect attempted;
- **readback verification.**

### **M10 INVOKES NO ADAPTER DIRECTLY. M10 PERFORMS NO DIRECT DATABASE WRITE INTO A TARGET SYSTEM.
### M10 REUSES NEITHER THE ORIGINAL PIPELINE'S AUTHORITY NOR THE ORIGINAL EFFECT GRANT.** A grant is
single-use by construction; reaching for the original one is the fast path wearing a lanyard.

### **DO NOT CREATE A SECOND "COMPENSATION PIPELINE" AND DO NOT EDIT M2'S STATE MACHINE.** M2's
`propose()` already takes a generic `LogicalEffect`. If you find it genuinely cannot express a
compensating action, **that is an authority question to record (§3.8, `M10-AQ-7`) — not a licence to
change landed M2 semantics.**

**11 — its own commit key** *(entity §9/§17, ADR-009, `commit_key.py`)*
The compensating effect has **its own** commit key. ### **DO NOT negate, prefix, suffix, hash or
otherwise derive it from the original effect's commit key** — that would make the compensation's
identity a function of the thing it is undoing, and two different compensations of one effect would
then collide.

**Read `commit_key.py` before you write this.** The canonical answer is already there:
`OCCURRENCE_RULES["adjust_invoice"]` is `CANONICAL_OCCURRENCE_REQUIRED`, and
`CANONICAL_OCCURRENCE_SOURCES["adjust_invoice"]` names the field **`compensation_id`**, the entity
**`Compensation`**, with the recorded reason *"a void/credit IS a Compensation (a gated effect) …
one invoice may legitimately receive several distinct adjustments, so the invoice's own identity is
not enough."* So the occurrence key is a resolved `CanonicalOccurrence(entity="Compensation",
occurrence_id=<compensation_id>)`, and `occurrence_key_for` already refuses everything else. It
fails closed **today** with `UnresolvedCanonicalOccurrence` because no resolver exists.
### **M10 is the unit that can supply that resolved occurrence.**

**Prove:** retries of the **same** Compensation converge on one commit key (commit-once), while the
original effect and the compensating effect remain **two distinct effects with two distinct keys and
two distinct reservations.**

**12 — the brake blocks a compensation like any effect** *(entity §35/§41, machine §30, spec §21.5)*
### **Under an active brake, a compensating write is BLOCKED.** A brake engaged because the system is
misbehaving must not permit that same system to write "corrections" into the TMS. ### **An urgent
compensation does not bypass it** — a human **narrows** the brake first, through the **already-landed
`BrakeStore.narrow()`** (`BR-3`: authenticated human only, `decision_ref` required, automation may
never narrow). ### **M13 IS NOT YOURS. Do not build the Brake lifecycle machine and do not emit the
registered-but-unemitted F13 `BrakeNarrowed` contract** — that emission half belongs to M13, exactly
as M5 left an F14 emission half to Phase 7. ### **M10 itself engages no brake and narrows none.**

**13 — policy, as it exists TODAY** *(ADR-010, `checkpoint.py` step 6)*
Use the landed mechanism. `GateRegistry` maps action class → `GateEntry`; **an unregistered action
class already resolves to the default `HUMAN_APPROVAL_REQUIRED`** (ADR-010 §8 layer 7 — the fallback
is never autonomous). That is how "money-affecting compensation is ALWAYS `HUMAN_APPROVAL_REQUIRED`"
is satisfied **structurally, without registering anything.**

### **M10 REGISTERS NO GATE, MINTS NO GATE DECISION, AND BUILDS NO POLICY OR RULE LIFECYCLE.**
`checkpoint.py` stays the sole minter of a gate decision and the production `GateRegistry` stays
EMPTY. ### **M11 (Policy) and M12 (Rule) are not yours.** Where M10's prose references *policy* and
*rules*, it means the checkpoint step that exists today — **do not fake a Policy machine because a
sentence mentions one.**

**14 — completion requires readback** *(CM-4, ADR-006, `AC-REC-002`)*
`EXECUTING → COMPLETED` requires the compensating effect to be **M3 `VERIFIED` by readback.**
### **"The API returned 200" is not completion. Write acceptance is not completion. A timeout is not
a failure.** `UNKNOWN` / `NEEDS_VERIFICATION` routes to `COMPENSATION_FAILED` (CM-4f), never to
`COMPLETED` and never to a silent success.

**15 — `COMPENSATION_FAILED` is loud, sticky, and human-owned** *(entity §36/§42, machine §20/§37,
`AC-REC-004`, `AC-RACE-013`)*
### **No timer moves it. No retry loop moves it. No sweep moves it. No reaper moves it. No model
moves it. There is no automatic best-effort retry.** It is non-terminal and it keeps its named human
owner and its exposure until a human establishes reality through CM-5.

**16 — `NOT_POSSIBLE` is honest** *(CM-2n, entity §36, spec §12.10)*
When the world offers no undo — a sent email, a wire — the system **says so**: transition to
`NOT_POSSIBLE`, keep the exposure, keep the human owner, escalate. ### **No fake write, no fake
`COMPLETED`, and no model decision.** Impossibility must rest on trusted deterministic evidence.
### **A model saying "this cannot be undone" is insufficient at any confidence.** What that evidence
actually IS is an open authority question (§3.8, `M10-AQ-11`) — record it; **do not build an adapter
capability registry to close it.**

**17 — CM-5, reality established by a human** *(CM-5, `GR-14`, K-1)*
`{COMPENSATION_FAILED, NOT_POSSIBLE} → COMPLETED` on `HumanEstablishedReality{decision_ref}`, with
the `decision_ref` **resolved through M1's resolver**, same tenant. Emits the shared F3
`RealityEstablished` with `subject="compensation"`. A model cannot establish reality; a timer cannot;
an absence cannot. ### **The `NOT_POSSIBLE` branch has no executing pipeline to co-commit with, and
you must NOT fabricate one** — see `M10-AQ-4`.

**18 — one transition, one commit** *(entity §15 `[C-2]`, machine §35, `GR-2`)*
Each M10 transition writes **its state change and its required event in ONE transaction.** A
persistence failure may never produce: `APPROVED` with no `CompensationApproved`; `EXECUTING` with no
bound pipeline; `COMPLETED` with no verified effect; a half-created Compensation; or a duplicate
active Compensation. ### **CM-3 starts a SEPARATE M2 pipeline — do not pretend the whole compensation
lifecycle is one transaction.** It is not, and claiming it is would be a false atomicity guarantee.

**19 — one active Compensation per invalidated effect** *(entity §17/§33, machine §17/§19)*
PK `(tenant_id, compensation_id)`. And, **verbatim**:

```sql
UNIQUE (tenant_id, original_effect_id) WHERE state != 'NOT_POSSIBLE'
```

### **BUILD THAT PREDICATE EXACTLY AS WRITTEN. DO NOT "IMPROVE" IT.** It looks surprising —
`NOT_POSSIBLE` is non-terminal and human-owned, yet excluded — and the consequence is real: a second
Compensation for the same original effect **is** insertable while an earlier `NOT_POSSIBLE` row is
still open and owned. ### **That is `M10-AQ-9`. Report it. Do not silently change the canonical
predicate to close it.**

**20 — no bulk undo; tenancy; replay** *(spec §12.10, `AC-REC-003`, `[C-1]`, `GR-11`/`ER-2`)*
- ### **A correction storm invalidating N effects raises N Compensations, each individually gated** —
  its own owner, exposure, approval, pipeline, checkpoint, grant, commit key and verification.
  ### **No bulk Effect Grant. No shared approval authorizing N writes. No one "undo all" adapter
  call.** *A bulk undo is 200 ungated writes with one tap.* The **aggregate exposure may be COMPUTED
  and shown before approval** (entity §43(d)) — compute it; **build no UI and no oversight surface.**
- **Tenant first** in the PK, and in every lookup: original effect, owner, approval, pipeline,
  grant, `decision_ref`, and the uniqueness index. Every cross-tenant reference **fails closed.**
- **Replay reconstructs the Compensation record only.** ### **Replaying any F10 event mints zero
  Pipeline Instances, zero checkpoint witnesses, zero Effect Grants, zero claims, zero adapter
  invocations, zero external effects, zero approvals and zero authority.** The compensating effect,
  like any effect, is never produced by replay.

### 3.6 ⚠️ THE KNOWN AUTHORITY QUESTIONS — read this before writing the transition table

Twelve conflicts are already recorded. **You are to preserve them, build the fail-closed side, and
report. You are not to resolve them.** Each one is a place where a build session, acting reasonably,
would settle a specification question by accident.

Numbering continues as `M10-AQ-*` in your final report.

---

**`M10-AQ-1` — `CorrectionInvalidatedAnEffect` is not a canonical event.**

Entity §21/§32, machine §33, M1 §33 and target spec §12.10 all use the name. **It is absent from all
118 registered contracts.** Verify that yourself.

The repository already recorded this: **`P6-D2`** in `IMPLEMENTATION-REGISTRY.yaml` and
`LEGACY-DISPOSITION.md` — *"listed in M1 §33 'Events consumed' but has **no §14 row**"* — with
**`closes_at: M10`**. Its disposition: it *"creates a NEW Work Item rather than transitioning one"*,
so M1 excluded it from its trigger set, and `work_item.py` says so in a comment that begins
*"`CorrectionInvalidatedAnEffect` IS ABSENT, AND THAT IS A DECISION WITH A REASON."*

M6 hit the same seam as `M6-AQ-1` and answered it the safe way: it emits the **registered**
`ClaimCorrected` (`IB-7`) and records a durable `propagation_obligation` on the claim row **naming**
the completed effects that rested on the wrong binding — ### **"IT MINTS NO UNREGISTERED EVENT NAME"**,
and it fabricated nothing as discharged.

> ### **DO NOT MINT `CorrectionInvalidatedAnEffect`. DO NOT SILENTLY MAP IT ONTO SOME OTHER EVENT.**
> M10 owns a **callable creation seam** — `raise_from_correction(...)` or equivalent — that takes the
> already-registered correction facts (`ClaimCorrected` / M6's `propagation_obligation`) plus a
> resolved `decision_ref`, and creates the Compensation. **Report the exact authority relationship you
> found, and whether `P6-D2` is closed by that or merely carried.**

---

**`M10-AQ-2` — trigger names versus event names.**

Machine §33 lists six "events consumed". Check each mechanically against
`event_contracts_data.json` before building a consumer:

- `CorrectionInvalidatedAnEffect` — **not registered** (`M10-AQ-1`).
- `HumanApproved` — **not registered.** It is M4's landed **`Trigger.HUMAN_APPROVED`**, a trigger
  label. The registered event for that fact is **`ApprovalGranted`** (F4).
- `NoCompensatingActionExists` — **not registered.** A guard phrase (`M10-AQ-11`).
- `HumanEstablishedReality` — **not registered.** It is M2's and M3's landed
  **`Trigger.HUMAN_ESTABLISHED_REALITY`**. The registered *result* event is `RealityEstablished` (F3).
- `PipelineStarted`, `PipelineClosed` — **registered** (F2).
- `PipelineFailed` — **not registered.** F2 has thirteen contracts and this is not one of them;
  `PipelineState.FAILED` is a **state**. F2's terminals are `PipelineClosed`, `PipelineRejected`,
  `PipelineVoided`.
- `NEEDS_VERIFICATION` — **a state**, not an event.

> ### **MAP ONLY WHERE AUTHORITY MECHANICALLY SUPPORTS A MAPPING. INVENT NO MISSING CONTRACT.**
> CM-4f's trigger is its executing pipeline **reaching the state** `FAILED` or `NEEDS_VERIFICATION`,
> read from the M2 row — not a delivery of an event called `PipelineFailed`.

---

**`M10-AQ-3` — where the invalidating `decision_ref` is persisted.**

`CompensationRequired` **requires** `decision_ref`. But entity §10's required attributes are
`compensation_id · tenant_id · original_effect_id · commit_key · state · version · exposure ·
owner_id · reason · created_at`, and §11's optional attributes are `pipeline_instance_id ·
approval_id · reality_decision_ref`. ### **There is no `invalidating_decision_ref` column in either
list**, and `reality_decision_ref` is a different fact — it is CM-5's, written at resolution.

So the invalidating `decision_ref` appears to live **only in the immutable `CompensationRequired`
event** in the transactional outbox — which, per M1's resolver and `entities/17-audit-event.md`, IS
the canonical audit store. The consequence: a row read alone cannot answer *"which correction
invalidated this effect?"*; the event lineage can.

> ### **DO NOT INVENT A COLUMN WITHOUT AUTHORITY. DO NOT DROP THE `decision_ref` REQUIREMENT.**
> Resolve it at CM-1 through M1's resolver, emit it on the event, and **report the persistence
> asymmetry as a question** — including whether it survives a full-history rebuild.

---

**`M10-AQ-4` — CM-5's "co-commit M2 `{VERIFIED,FAILED}`" when there is no M2 row.**

CM-5 covers **both** `COMPENSATION_FAILED` and `NOT_POSSIBLE`, and its Writes cell says
*"`decision_ref`; co-commit M2 `{VERIFIED,FAILED}`"*.

- From `COMPENSATION_FAILED` there **is** an executing pipeline (`EXECUTING` required one, entity
  §16's CHECK), and M2's landed **`PL-15`** already declares the other half from its own side:
  *"co-commit M3 `{VERIFIED,FAILED}`, **M10 `COMPLETED`**"*. That seam is coherent.
- From `NOT_POSSIBLE` there is **no pipeline at all** — CM-2n moves `REQUIRED → NOT_POSSIBLE` and
  writes no `pipeline_instance_id`. There is nothing to co-commit with.

> ### **DO NOT CREATE A DUMMY PIPELINE INSTANCE. DO NOT SILENTLY OMIT A REQUIRED CO-COMMIT.**
> Build the `COMPENSATION_FAILED` branch coordinated with M2's landed `PL-15`; for `NOT_POSSIBLE`
> record the `reality_decision_ref` with **no fabricated pipeline**, and **report the contradiction**.
> If it is genuinely undecidable from the corpus, stop at that boundary and say so.

---

**`M10-AQ-5` — `RealityEstablished`'s `aggregate_type` and `outcome` versus a compensation.**

The registered contract carries `aggregate_type: "effect_grant"` and `outcome ∈ {VERIFIED, FAILED}`.
But CM-5's aggregate is a **compensation**, and its target state is **`COMPLETED`** — which is not in
that enum. The declared discriminator is `subject ∈ {effect, compensation}`.

The consistent reading is that `outcome` names **the established reality of the underlying effect**,
not the compensation's own state. ### **Verify that against the corpus and REPORT it. Do not widen
the enum, do not mint an F10 event, and do not emit `outcome="COMPLETED"`.**

---

**`M10-AQ-6` — the M4 seam: which approval, created by whom, consumed when.**

Determine mechanically, from `approval.py`, whether M10:
requests a **new** M4 Approval; or consumes a **previously created** one; whether the binding to this
Compensation's material facts is the `commit_key`-scoped `fp_v1` fingerprint; and whether M4
consumption (`AP-7`) is atomic with the M3 claim **inside the executing pipeline** rather than at CM-2.

Note the ordering constraint you will hit: `ApprovalMachine.request()` needs a `LogicalEffect`, so the
commit key — and therefore the `compensation_id` occurrence — **must exist before the approval does.**
### **Report the seam. Do not collapse `Compensation.APPROVED` with `Approval.GRANTED`/`CONSUMED`.**

---

**`M10-AQ-7` — can landed M2 express a compensating action?**

`propose()` takes a generic `LogicalEffect`, so it is action-class agnostic — but it also requires a
`work_item_id` and calls `_require_work_item_owner`. **A compensating pipeline therefore needs a Work
Item**, which is exactly the seam `P6-D2` describes (*"creates a NEW Work Item rather than
transitioning one"*).

> Determine whether M10 creates that Work Item, or requires the caller to supply one. ### **Reuse M2;
> do not create a second pipeline; do not edit M2's state machine.** If a narrow generic seam is
> genuinely missing, **report it as a question** rather than adding it.

---

**`M10-AQ-8` — the future machines M10's prose assumes.**

M10 says "full policy + brake". **M11 Policy, M12 Rule and M13 Brake are unbuilt.** What exists
today: `checkpoint.py` steps 6 and 7, `GateRegistry`/`GateDecision`/`Caps`, and `brake.py`'s
`engage`/`widen`/`narrow`/`release` with the ratchet and fail-closed reads. F13's four contracts are
**registered but unemitted**.

> ### **M10 uses that substrate and builds none of those lifecycles.** Report every place where M10's
> prose assumes a machine that does not exist yet, and what you used instead.

---

**`M10-AQ-9` — `NOT_POSSIBLE` is excluded from the uniqueness predicate.**

Stated in §3.5 requirement 19. Build the predicate verbatim; report the consequence.

---

**`M10-AQ-10` — original effects in the other six M3 states.**

Stated in §3.5 requirement 3. CM-1 names `VERIFIED`; CM-1r names `UNKNOWN_OUTCOME`; the corpus names
no disposition for the other six, and the `CompensationRefused` contract fixes `cause` to
`unknown_outcome`. Fail closed, mint no variant, report the gap.

---

**`M10-AQ-11` — what proves "the world offers no undo"?**

CM-2n's trigger type in machine §14 is **`S` (system)**, yet entity §13 forbids a `MODEL_INFERRED`
basis and `GR-8` says confidence gates nothing. There is **no canonical mechanism** in the corpus for
proving impossibility: `NoCompensatingActionExists` is not a registered event, and no adapter
capability registry exists or is specified.

> ### **Identify the narrow existing seam, or report the gap. DO NOT BUILD A CAPABILITY REGISTRY.
> DO NOT INFER IMPOSSIBILITY FROM MODEL OUTPUT OR ARBITRARY TEXT.** Note the tension between the
> `S` trigger type and the human-authority requirement, and report it.

---

**`M10-AQ-13` — `K-4`'s provenance rule versus the entity's attribute list.**

`K-4` names **Compensation** among the operational records a money field is permitted on, and
requires that the field **carry the reference it was read from**. Entity §10 lists `exposure` as a
required attribute and names **no such reference**; §11's optional attributes are
`pipeline_instance_id`, `approval_id` and `reality_decision_ref`, none of which is one.

The landed precedents diverge, and the divergence is principled: **M1** persists
`exposure_amount_minor` + `exposure_currency` + `exposure_observation_ref` (three columns, with the
provenance); **M7** and **M9** persist a bare `exposure TEXT` because for them the exposure is an
optional annotation; and **M2** persists **no money at all**, saying so in its migration — *"exposure
is a remembered money value with a fresh timestamp, which is the one thing K-4 forbids"* — and
leaving the number on the canonical event.

M10's exposure is **required and money-affecting**, which is the strongest of those three positions.

> Determine which reference `K-4` is satisfied by here — the **original effect** whose verified
> readback established the amount is the obvious candidate, and `original_effect_id` is already a
> required attribute. ### **Do not persist a money value with no provenance, and do not invent a
> reference the corpus does not support. Report which you used and why.**

---

**`M10-AQ-12` — the M9 escalation seam.**

F10 routes `CompensationImpossible` to *"M9/Oversight"* and `CompensationFailed` to *"M9
(human-owned)"*. M9 is landed but **ship-dark**, and its `raise_exception` requires a caller-supplied
named human owner and caller-supplied thresholds (`V10` is open).

**The precedent is directly on point:** M9's own landing left the M1-ownerless→M9 wiring **unwired**
as `M9-AQ-4`, because *"wiring a seam is precisely what shipping dark forbids."*

> ### **M10 EMITS ITS F10 EVENTS AND STOPS THERE. It creates no M9 Exception rows, wires no oversight
> queue, no dashboard, no notifier. DO NOT EDIT M9.** Name the seam in your report and leave it
> unwired.

### 3.7 The seams that are already built — feed them, do not duplicate them

- **`resolve_decision_ref` is M1's.** Import it. M2, M3 and M9 all do. ### **A second K-1 executor is
  the second authority `CLAUDE.md` §5 rule 17 forbids.**
- **The checkpoint is P3's.** `checkpoint.py` stays the **sole minter of a gate decision**. M10 is an
  input to the gate and never a gate.
- **M3 is the sole effect authority.** M10 does not write `effect_grants` rows, does not resolve
  reality for an effect, and does not duplicate `EF-5`. It **reads** M3 state at CM-1 and **consumes**
  M3's verification result at CM-4.
- **M2 owns pipelines.** M10 starts one and reads its state; it writes no `pipeline_instances` row
  directly.
- **M4 owns approvals.** M10 binds an `approval_id` and reads it; it grants nothing.
- **The outbox is P5's.** State and event in one transaction, through `TransactionalOutbox`.
- **Timers are P5's.** And M10 arms **none**: there is no deadline anywhere in this machine.
- **Money is `fingerprint.py`'s.** `Money`, not a float, not a `Decimal`, not a string of pounds.

### 3.8 The F14 tripwires — which are yours

`IllegalTransitionAttempted` on every refused transition, `GR-1`-style, following M2's and M3's
landed pattern: raise, persist nothing, record the security event with a deterministic identity so a
retry storm records one.

**What is NOT yours:** the Sev-0 detectors at the source (`NonHumanApprovalPresented`,
`CrossTenantApprovalPresented`) belong to the checkpoint and already exist. ### **M10 engages no
brake** — not on an illegal transition, not on a Sev-0, not ever.

---

## 4. What you must produce

**Five files. The names are canonical — a different name is a scenario failure, not a style choice.**

```
src/freight_recon/compensation.py
src/freight_recon/migrations/phase6_compensations.py
eval/tests/test_phase6_compensation.py
scripts/probe_phase6_compensation.py
scripts/mutate_phase6_compensation.py
```

Plus the canonical-schema wiring in `src/freight_recon/schema.py` (the `compensations` table joins
the tenant-first partition), following M8's and M9's precedent exactly.

### **NAME THE MACHINE'S OWN TYPES THE WAY `conflict.py` AND `exception.py` NAME THEIRS.** M7 ships
`M7Machine`; M9 ships `M9Machine`, `EcState`, `EcRecord`. M10 ships **`M10Machine`, `CmState`,
`CmRecord`** and its own `Trigger`, `TransitionRow`, `RowKind`, `TransitionResult`. Do not name a
class `Compensation` that shadows the entity concept in a module already called `compensation`.

### The probe's interface

`scripts/probe_phase6_compensation.py` is the deterministic entry point. It is what Product Driver
drives, and its interface is a contract:

```
--list-cases         one case name per line, kebab-case
--list-dimensions    one mutation-axis token per line
--case <name>        run exactly one case
--all                run every case
```

**Mutation axes** — the closed vocabulary the generator may vary a case along:

```
--concurrency  --delay-ms  --repeat  --tenants  --seed  --inject
--actor  --decision-ref  --original-state  --exposure  --brake
```

### **`--original-state` AND `--exposure` ARE THIS UNIT'S OWN TWO AXES**, the way `--actor` and
`--decision-ref` were M9's and `--coverage` was M8's. `--original-state` varies the M3 state of the
effect being compensated across all eight members of `EffectGrantState` — that is the axis CM-1 and
CM-1r turn on, and it is where `M10-AQ-10` lives. `--exposure` varies the money shape, including the
float and `Decimal` values that must be refused.

**Cases.** At least the following families, each a real execution against a real database — never a
printed sentence:

*CM-1 eligibility* — required-from-a-verified-original-effect · the-original-state-is-read-from-the-ledger-not-a-caller-flag ·
compensation-cannot-be-created-from-an-unknown-outcome · refusal-on-unknown-emits-compensationrefused-and-zero-rows ·
refusal-on-unknown-mints-no-pipeline-no-grant-and-no-effect · a-failed-original-creates-no-compensation ·
a-revoked-original-creates-no-compensation · an-expired-unclaimed-original-creates-no-compensation ·
a-merely-attempted-original-creates-no-compensation · a-granted-or-claimed-original-creates-no-compensation ·
no-refusal-variant-is-minted-for-the-other-six-states

*Invalidating authority* — a-model-inferred-invalidation-is-refused · confidence-one-does-not-substitute-for-authority ·
the-invalidating-decision-ref-must-resolve · an-automation-emitted-human-decision-event-is-refused ·
a-rule-kind-decision-ref-refuses-today · the-invalidating-decision-ref-rides-the-compensationrequired-event ·
m10-imports-the-k1-resolver

*Owner* — a-compensation-carries-a-named-human-owner-from-required · an-ownerless-compensation-is-structurally-impossible ·
a-model-cannot-own-a-compensation · a-cross-tenant-owner-is-refused · an-offboarded-human-cannot-own-a-new-compensation

*Exposure* — exposure-is-required-from-required · exposure-is-integer-minor-units-and-a-currency ·
a-float-exposure-is-refused · a-decimal-exposure-is-refused · exposure-survives-into-compensation-failed ·
exposure-survives-into-not-possible

*Lifecycle* — the-six-canonical-states-and-no-seventh · completed-is-the-only-terminal-state ·
compensation-failed-and-not-possible-stay-human-owned · a-compensation-never-expires ·
a-compensation-is-never-cancelled · a-compensation-row-cannot-be-deleted ·
no-timer-moves-compensation-failed · no-timer-moves-any-compensation-state ·
there-is-no-automatic-retry-from-compensation-failed · no-sweep-reaper-or-scan-moves-a-compensation

*CM-2 approval* — required-to-approved-requires-an-authenticated-human ·
the-approval-id-resolves-to-a-same-tenant-m4-approval · the-approval-is-bound-to-this-compensations-commit-key ·
a-stale-or-wrong-approval-is-refused · a-cross-tenant-approval-is-refused ·
a-model-cannot-approve-a-compensation · confidence-cannot-approve-a-compensation ·
m10-builds-no-second-approval-system · compensation-approved-is-not-approval-granted

*CM-3 pipeline* — execution-starts-a-new-m2-pipeline-instance · the-executing-pipeline-is-not-the-original ·
executing-requires-a-bound-pipeline-instance-id · the-compensating-effect-passes-the-full-checkpoint ·
the-compensating-effect-claims-its-own-grant · the-original-effect-grant-is-never-reused ·
m10-invokes-no-adapter-directly · m10-performs-no-direct-system-write

*Commit key* — the-compensating-effect-has-its-own-commit-key ·
the-commit-key-is-the-canonical-compensation-occurrence ·
the-commit-key-is-not-derived-from-the-originals · retrying-the-same-compensation-converges-on-one-commit-key ·
the-original-and-compensating-effects-stay-distinct

*Brake and policy* — an-active-brake-blocks-a-compensating-write · an-urgent-compensation-does-not-bypass-the-brake ·
a-human-narrows-the-brake-through-the-landed-mechanism · m10-engages-no-brake-and-narrows-none ·
m10-mints-no-gate-decision · the-money-gate-defaults-to-human-approval-required · m10-registers-no-gate

*CM-4 / CM-4f readback* — completed-requires-a-verified-compensating-effect ·
adapter-success-alone-does-not-complete-a-compensation · write-acceptance-is-not-completion ·
a-timeout-is-not-a-failure · a-failed-executing-pipeline-reaches-compensation-failed ·
a-needs-verification-pipeline-reaches-compensation-failed · compensation-failed-carries-the-exposure

*CM-2n* — not-possible-keeps-its-owner-and-exposure · not-possible-writes-nothing-to-the-world ·
impossibility-is-never-inferred-from-model-output

*CM-5* — a-human-establishes-reality-with-a-resolving-decision-ref ·
cm5-emits-the-shared-f3-realityestablished-with-subject-compensation ·
m10-mints-no-second-realityestablished-contract · reality-establishment-from-not-possible-fabricates-no-pipeline ·
a-model-cannot-establish-reality

*Uniqueness and concurrency* — one-active-compensation-per-invalidated-effect ·
the-uniqueness-predicate-excludes-not-possible-exactly-as-written ·
concurrent-creation-yields-exactly-one-compensation

*Storm* — n-invalidated-effects-raise-n-individually-gated-compensations · there-is-no-bulk-effect-grant ·
there-is-no-bulk-approval · there-is-no-one-undo-all-adapter-call · aggregate-exposure-is-computed-before-approval

*Tenancy* — tenant-is-first-in-the-compensation-primary-key · a-cross-tenant-original-effect-is-refused ·
a-cross-tenant-pipeline-is-refused · a-cross-tenant-decision-ref-is-refused

*Transactionality and recovery* — state-and-event-co-commit-in-one-transaction ·
a-persistence-failure-leaves-no-half-created-compensation · there-is-no-approved-without-its-event ·
there-is-no-executing-without-its-pipeline-binding ·
a-crash-after-claim-reaches-needs-verification-then-compensation-failed ·
a-compensation-survives-a-restart

*Replay* — replay-reconstructs-compensation-state-only · replay-mints-zero-pipelines-grants-claims-and-effects

*Ship dark and regression* — m10-ships-dark-with-zero-production-importers · m10-joins-no-outbound-channel ·
m10-builds-no-oversight-queue-or-notifier · m11-m12-and-m13-are-not-built ·
the-m9-escalation-seam-is-named-and-left-unwired · m1-through-m9-are-unchanged

### The probe's output contract

Every case prints a `### MISS ###` marker on failure and a positive line on success. The probe's
final line, on a fully-passing run, is exactly:

```
behaviours as specified, 0 wrong
```

**Dark-posture literals.** The probe must print these six, verbatim, when it verifies each:

```
THE M1 WORK ITEM MACHINE IS UNCHANGED
THE M2 PIPELINE MACHINE IS UNCHANGED
THE M3 EFFECT AUTHORITY IS UNCHANGED
THE M4 APPROVAL MACHINE IS UNCHANGED
THE M9 EXCEPTION MACHINE IS UNCHANGED
THE M11, M12 AND M13 MACHINES ARE NOT BUILT
```

**And these safety sentences, verbatim, from the cases that establish them:**

```
A COMPENSATION IS ITSELF A SEPARATELY GATED EXTERNAL EFFECT
THERE IS NO FAST PATH FOR UNDO
YOU CANNOT UNDO WHAT YOU CANNOT PROVE YOU DID
COMPENSATION IS FORBIDDEN ON AN UNKNOWN OUTCOME
A COMPENSATING EFFECT NEVER REUSES THE ORIGINAL EFFECT GRANT
THE COMPENSATING EFFECT HAS ITS OWN COMMIT KEY
COMPLETION REQUIRES READBACK, NOT AN ADAPTER RETURN CODE
A TIMEOUT IS NOT A FAILURE
COMPENSATION_FAILED NEVER AUTO-RESOLVES
NO TIMER MOVES COMPENSATION_FAILED
THERE IS NO AUTOMATIC RETRY OF A FAILED COMPENSATION
NOT_POSSIBLE IS HONEST AND KEEPS ITS EXPOSURE
THERE IS NO BULK UNDO
A COMPENSATION IS BLOCKED UNDER AN ACTIVE BRAKE
A MODEL CAN NEVER APPROVE A COMPENSATION
A MODEL CAN NEVER OWN A COMPENSATION
A MODEL CAN NEVER ESTABLISH REALITY
M10 MINTS NO GATE DECISION
M10 ENGAGES NO BRAKE AND NARROWS NONE
M10 MINTS NO SECOND RealityEstablished CONTRACT
REPLAY PRODUCES NO COMPENSATING EFFECT
```

**Forbidden markers.** Any of these in any output is a scenario failure:

```
### COMPENSATION CREATED FROM AN UNKNOWN OUTCOME ###   ### UNKNOWN OUTCOME COMPENSATED ###
### COMPENSATING WRITE ON AN UNKNOWN OUTCOME ###       ### REFUSAL VARIANT MINTED ###
### COMPENSATION CREATED FROM A FAILED EFFECT ###      ### CALLER FLAG DECIDED ELIGIBILITY ###
### MODEL INFERRED INVALIDATION ACCEPTED ###           ### CONFIDENCE SUBSTITUTED FOR AUTHORITY ###
### UNRESOLVABLE decision_ref ACCEPTED ###             ### AUTOMATED ACTOR PASSED AS A HUMAN DECISION ###
### SECOND K-1 RESOLVER BUILT ###                      ### COMPENSATION RAISED WITHOUT AN OWNER ###
### OWNERLESS COMPENSATION CREATED ###                 ### A MODEL OWNED A COMPENSATION ###
### CROSS-TENANT OWNER ACCEPTED ###                    ### AN OFFBOARDED HUMAN OWNED A COMPENSATION ###
### COMPENSATION CREATED WITHOUT EXPOSURE ###          ### FLOAT EXPOSURE ACCEPTED ###
### EXPOSURE ZEROED ON FAILURE ###                     ### EXPOSURE LOST ON IMPOSSIBILITY ###
### SEVENTH LIFECYCLE STATE MINTED ###                 ### CANCELLED STATE MINTED ###
### EXPIRED STATE MINTED ###                           ### RETRYING STATE MINTED ###
### COMPENSATION EXPIRED ###                           ### COMPENSATION CANCELLED ###
### COMPENSATION ROW DELETED ###                       ### SWEEP MOVED A COMPENSATION ###
### REAPER DELETED A COMPENSATION ###                  ### TIMER MOVED COMPENSATION_FAILED ###
### TIMER MOVED A COMPENSATION ###                     ### AUTOMATIC RETRY FROM COMPENSATION_FAILED ###
### COMPENSATION_FAILED AUTO-RESOLVED ###              ### MODEL APPROVED A COMPENSATION ###
### CROSS-TENANT APPROVAL ACCEPTED ###                 ### STALE APPROVAL ACCEPTED ###
### WRONG-COMMIT-KEY APPROVAL ACCEPTED ###             ### SECOND APPROVAL SYSTEM BUILT ###
### M4 SEMANTICS MODIFIED ###                          ### CHECKPOINT BYPASSED ###
### PRIVILEGED UNDO PATH ###                           ### DIRECT ADAPTER INVOCATION ###
### DIRECT DATABASE WRITE TO A TARGET SYSTEM ###       ### ORIGINAL EFFECT GRANT REUSED ###
### ORIGINAL PIPELINE AUTHORITY REUSED ###             ### ORIGINAL COMMIT KEY REUSED ###
### COMMIT KEY DERIVED FROM THE ORIGINAL ###           ### EXECUTING WITHOUT A BOUND PIPELINE ###
### SECOND COMPENSATION PIPELINE BUILT ###             ### M2 STATE MACHINE EDITED ###
### BRAKE BYPASSED BY AN URGENT COMPENSATION ###       ### M10 ENGAGED A BRAKE ###
### M10 NARROWED A BRAKE ###                           ### M10 MINTED A GATE DECISION ###
### M10 REGISTERED A GATE ###                          ### M11 POLICY MACHINE BUILT ###
### M12 RULE MACHINE BUILT ###                         ### M13 BRAKE MACHINE BUILT ###
### BrakeNarrowed EMITTED ###                          ### COMPLETED WITHOUT READBACK ###
### ADAPTER SUCCESS TREATED AS COMPLETION ###          ### WRITE ACCEPTANCE TREATED AS COMPLETION ###
### TIMEOUT TREATED AS FAILURE ###                     ### UNKNOWN OUTCOME TREATED AS COMPLETED ###
### FAKE COMPLETED ###                                 ### FAKE WRITE ON IMPOSSIBILITY ###
### MODEL DECIDED IMPOSSIBILITY ###                    ### MODEL ESTABLISHED REALITY ###
### SECOND RealityEstablished CONTRACT MINTED ###      ### F10 RealityEstablished MINTED ###
### DUMMY PIPELINE FABRICATED ###                      ### DUPLICATE ACTIVE COMPENSATION ###
### UNIQUENESS PREDICATE ALTERED ###                   ### BULK EFFECT GRANT ###
### BULK UNDO ###                                      ### SHARED APPROVAL AUTHORIZED N WRITES ###
### ONE ADAPTER CALL UNDID N EFFECTS ###               ### CROSS-TENANT ORIGINAL EFFECT ACCEPTED ###
### CROSS-TENANT PIPELINE ACCEPTED ###                 ### CROSS-TENANT decision_ref ACCEPTED ###
### TENANT MISSING FROM THE PRIMARY KEY ###            ### EVENT WITHOUT ITS STATE ###
### STATE WITHOUT ITS EVENT ###                        ### HALF-CREATED COMPENSATION PERSISTED ###
### APPROVED WITHOUT ITS EVENT ###                     ### REPLAY MINTED A GRANT ###
### REPLAY MINTED A PIPELINE ###                       ### REPLAY PRODUCED AN EXTERNAL EFFECT ###
### REPLAY MINTED AUTHORITY ###                        ### UNREGISTERED EVENT MINTED ###
### CorrectionInvalidatedAnEffect MINTED ###           ### NoCompensatingActionExists MINTED ###
### M9 EXCEPTION ROW CREATED BY M10 ###                ### OVERSIGHT QUEUE BUILT ###
### NOTIFIER WIRED ###                                 ### CHANNEL JOINED ###
### M1 ROW REWRITTEN BY M10 ###                        ### M3 EFFECT SEAM REWRITTEN ###
### M9 MACHINE EDITED ###                              ### PRODUCTION IMPORTER OF COMPENSATION ###
```

### The mutation battery

`scripts/mutate_phase6_compensation.py` applies each mutation to the **real tree**, runs the
acceptance battery, asserts it turns **red**, and restores. Every mutation below must be caught:

1. permit creation from `UNKNOWN_OUTCOME`
2. permit creation from `FAILED` without authority
3. read eligibility from a caller flag instead of the ledger
4. permit a `MODEL_INFERRED` invalidation
5. accept a `decision_ref` without resolving it
6. drop the owner requirement
7. permit a cross-tenant owner
8. drop the exposure requirement
9. accept a float exposure
10. zero the exposure on `COMPENSATION_FAILED`
11. add a seventh lifecycle state
12. add an expiry column
13. add a cancellation transition
14. let a timer move `COMPENSATION_FAILED`
15. add an automatic retry from `COMPENSATION_FAILED`
16. allow a model to approve
17. accept a wrong-commit-key approval
18. accept a cross-tenant approval
19. bypass the M2 pipeline
20. bypass the checkpoint
21. reuse the original Effect Grant
22. derive the commit key from the original's
23. complete on adapter success without readback
24. complete while the M3 outcome is unknown
25. let an active brake pass a compensating write
26. issue one bulk grant for N effects
27. emit a grant or effect during replay
28. drop `tenant` from the uniqueness index
29. permit a duplicate active compensation
30. drop the transactional event write
31. mint a second `RealityEstablished` contract under F10
32. mint `CorrectionInvalidatedAnEffect`
33. relocate the machine behind a re-export shim *(the anti-vacuity control — every corpus-scanning
    negative assertion must turn red, proving it was scanning something)*

### **Never use `git checkout`, `git restore`, `git stash` or `git clean` to undo a mutation.** Write
the original bytes back from an in-memory copy. A mutation battery that recovers with git will
one day discard the build.

### Ship dark

M10 ships dark, exactly as M1–M9 do:

- **zero production importers** — only the probe reaches `compensation.py`;
- **no channel join** — nothing whose import closure reaches an outbound channel imports it;
- **no oversight queue, dashboard, notifier, MTTR surface or operator console**;
- **`checkpoint.py` stays the sole gate minter**; the production `GateRegistry` stays **EMPTY**;
- **M3 stays the sole effect authority**;
- **no `capability.*.live_effect` flag is flipped**;
- **no M11, M12 or M13 table, machine or event emission.**

### Tests

`eval/tests/test_phase6_compensation.py`, in the shape of `test_phase6_exception.py`:
`AC-MACH-000` (the nine-row exact set match against §14), `AC-MACH-1001..1009`, `AC-REC-001..005`,
`AC-RACE-013`, the DDL introspection tests, the illegal-insert tests **against a live canonical
database with positive controls**, the replay isolation tests, and the ship-dark scans.

### **EVERY CORPUS-SCANNING NEGATIVE ASSERTION MUST PROVE ITS POPULATION.** This is not advice — the
repository has a guard for it (`test_false_green_defenses.py::test_every_corpus_scanning_negative_
assertion_proves_its_population`), and it printed a real `F` on the M9 build for exactly this. An
assertion that *"no module does X"* must first assert that it **found** the modules it scanned;
otherwise it passes while scanning nothing.

### Regressions you may not break

M1–M9 stay green. `checkpoint.py`, `external_effect.py`, `approval.py`, `pipeline_instance.py`,
`work_item.py`, `observation.py`, `identity_binding_claim.py`, `conflict.py`, `expectation.py` and
`exception.py` stay **byte-identical** unless a canonical authority forces a seam change — and if one
does, **that is a finding to report before you make it**, not a change to slip in.

### **The P3/P4 per-thread-connection concurrency correction at `d70a4e7` is landed. Do not rework it.**

---

## 5. Do not

- Do **not** build M11, M12 or M13, or any part of them.
- Do **not** build an oversight queue, dashboard, notifier, Slack/email/SMS surface, or any UI.
- Do **not** enable any production capability, flip any flag, or join any channel.
- Do **not** implement a freight workflow or a customer-visible compensation control.
- Do **not** mint an unregistered event name, for any reason.
- Do **not** mint a second `RealityEstablished` contract.
- Do **not** build a second approval system, a second K-1 resolver, or a second gate minter.
- Do **not** modify M1–M9, the checkpoint kernel, or the claim CAS.
- Do **not** resolve any `M10-AQ-*`. Record it.
- Do **not** score a P6 acceptance criterion or claim P6 phase acceptance. ### **`criteria_scored`
  stays `[]`.**
- Do **not** unlock P7.
- Do **not** describe the M9 CI run as fully green, and do **not** reopen M9 over its cancelled
  Safety job.
- Do **not** open a remediation campaign against the recorded non-blocking debt rows
  (`P6-D6`, `P6-D8`, `P6-D17`–`P6-D27`, `P6-D29`–`P6-D64`, the G2 residuals). ### **A debt row is a
  complete deliverable.**

---

## 5a. The review tier, and why it is the higher one

**This unit is tier-1.** `CLAUDE.md` §7: *"When genuinely torn between two tiers, take the higher one
once and say so."*

A state machine on its own is tier-2. M10 is tier-1 for four reasons that stack:

1. **It lands a migration** — a new canonical table, a new partial unique index, and an edit to the
   canonical schema chain.
2. **It is load-bearing for tenant isolation** — the compensation, its owner, its original effect,
   its approval, its pipeline and its `decision_ref` are all tenant-scoped, and a cross-tenant leak
   here writes a correction into the wrong brokerage's accounting system.
3. **It is the only machine in Neyma whose normal operation is an external effect that moves money
   backwards.** Every other P6 machine ships dark and touches nothing; M10's whole purpose is a
   write.
4. **It decides whether an undo goes through the gates or around them** — and an undo that goes
   around them is the ungated write route the entire kernel exists to prevent.

So it takes the higher tier once and says so, and this file says so.

---

## 6. How this run works

You build. Product Driver attacks with the permanent scenario
(`scenarios/p6_m10_compensation.yaml`) plus dynamically generated adversarial cases. Findings come
back to you. You correct. An independent reviewer who did not build M10 reviews the corrected tree,
bound to its exact fingerprint.

**Stop at verified M10. Do not automatically continue into M11.** This run ends at M10. Nothing
here authorizes you to begin **M11–M13**, and the loop's verdict is scoped to `P6/M10`, never to P6.

**Report at the end:** what landed, every `M10-AQ-*` with what you found and what you left open, the
ship-dark measurements over discovered populations, the mutation battery result, and — plainly —
**anything you could not prove.**

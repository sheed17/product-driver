# Build P6 / M6 — Identity Binding Claim. Only that.

This is the goal Product Driver gives the builder session inside the Neyma repository. Pass it
with:

```bash
.venv/bin/python -m neyma_product_driver run \
  --task "$(cat tasks/neyma_p6_m6.md)" \
  --scenario p6_m6_identity_binding_claim
```

---

## 0. Read the authority first, in this order

Do not write code until you have read these. They are the authority; nothing below replaces them,
and where this file and a canonical specification disagree, **the specification wins and you say
so.**

1. `PRODUCT.md`
2. `CLAUDE.md`
3. `docs/implementation/CURRENT.md`
4. `docs/implementation/IMPLEMENTATION-REGISTRY.yaml`
5. `docs/specifications/entities/09-identity-binding-claim.md` — the entity, and **§13 `SD-6`** in
   particular
6. `docs/specifications/state-machines/06-identity-binding-claim.machine.md` — the eleven `IB-*`
   rows of §14, and §15's illegal set
7. `docs/specifications/state-machines/registry.md` — §1 triggers, §2 the transition-row defaults,
   §3 `GR-1`…`GR-17` (**`GR-8`**, **`GR-9`**, **`GR-12`**, **`GR-13`**, **`GR-17`** above all), §4
   the canonical state registry, §5 the canonical event registry. **No machine may define a local
   synonym**, so every state and every event name you write must already be registered. Read §6:
   that file's own event table calls itself **provisional**, which matters for §3.7 below
8. `docs/specifications/events/06-identity-binding-claim-events.md` — the **F6** family contract,
   its payloads, its consumers, and its cross-cutting section
9. `docs/specifications/events/registry.md` — §8 **ORDERING** (F6 proposals are order-tolerant;
   **correction and supersession are STRICT**), §5's consequential list, and §7's projection rules
10. `docs/specifications/events/14-audit-security-events.md` — the F14 tripwires this unit is named
    the producer of
11. `docs/architecture/decisions/ADR-007-identity-claims-and-conflict.md` — **the whole file.** §3's
    nine-term vocabulary, §4.1's deterministic ladder, §4.2 on confidence, §4.3 on human assertion,
    §4.4 on counterparties, §6 on correction and propagation, §7 on identity persistence, §10's
    failure modes, §11's security considerations, §13's merge-gating tests
12. `docs/architecture/decisions/ADR-002-state-classes-and-lineage.md` **§2.3** — the six-member
    `provenance_class` registry and **`R-P1`**, **`R-P2`**, **`R-P3`**
13. `docs/architecture/target-system-specification.md` **§12.6**, and mandates **M-13**, **M-14**,
    **M-15**, **M-16**, **M-17**, **M-18**, **M-20**, **M-49**, **M-66**
14. `docs/specifications/entities/08-evidence.md` — because §13 and §16 of the claim entity require
    an Evidence span, and §3.11 below is about what that does and does not oblige you to build
15. `docs/specifications/entities/00-conventions.md` — `[C-1]`, `[C-2]`, `[C-3]`, `[C-5]`, `[C-6]`,
    `[C-7]`, `[C-8]`, `[C-9]`, `[C-10]`
16. the **P5** event transport, outbox/inbox, replay isolation and durable timers:
    `src/freight_recon/event_outbox.py`, `event_inbox.py`, `event_replay.py`, `event_timers.py`,
    `event_contracts.py` and `event_contracts_data.json`
17. **M1** Work Item (`src/freight_recon/work_item.py`) — the `owner_id` FOREIGN KEY into
    `tenant_humans` is the precedent for a **named, ACTIVE human**, and the append-only TRIGGER is
    the precedent for an invariant a database actually enforces
18. **M4** Approval (`src/freight_recon/approval.py`) — the worked example of an **authenticated
    human actor** guard, and of an F14 fraud signal (`CounterpartySelfAuthorizationDetected`) being
    emitted by its named producer
19. **M5** Observation (`src/freight_recon/observation.py`,
    `migrations/phase6_observations.py`) — the unit M6 sits directly on top of. Its
    `BindingDecision` is the INERT seam M6 is the other half of, and §3.5 below says exactly what
    you may and may not do to it. Its migration, probe and mutation battery are the shape yours
    follow
20. `src/freight_recon/checkpoint.py` — **step 4, native-state validity**, and the `NativeClaim`
    and `ProvenanceClass` types M6 feeds. **You are not changing this file** (§3.12)

### How to weigh them

| Source | What it is |
|---|---|
| `PRODUCT.md` | the stable product identity and destination |
| `CURRENT.md` | the present engineering position |
| `IMPLEMENTATION-REGISTRY.yaml` | machine phase/unit status and dependencies |
| canonical specs and ADRs | the exact required behaviour |
| legacy code and git history | implementation material only — **never roadmap authority** |

**If two authoritative sources genuinely conflict: REPORT THE CONFLICT. Do not invent a
resolution.** Say which two sources disagree, quote both, and stop on that point. Product Driver
will surface it. A resolution you invented is worse than a blocked run, because it looks like
agreement. **§3.9 below names three such conflicts that are already known. Read it before you write
the transition table.**

---

## 1. What Neyma is — the stable identity

Neyma is an **AI-native operating platform and system of action for SMB freight and logistics
companies.**

It is **not** an invoice bot, a document-extraction product, a Slack bot, a TMS chatbot, a browser
wrapper, an AP tool, an email triage system, or a disconnected collection of agents. If a piece of
legacy code in this repository suggests otherwise, that code is material, not direction.
`src/freight_recon/email_triage.py`, `ingestion.py`, `extraction.py`, `inbox_brain.py`,
`mailbox_intake` routing fields and `action_callback.py::_learn_correction` all exist and all
predate this specification. ADR-007 §14 calls `email_triage.py` **"the ancestor"** of this linker
and says *"keep it"* — **keeping it is not adopting it. None of them is M6, and M6 does not adopt,
refactor, wire into or replace any of them.**

- **P0–P8** build the shared governed operating engine.
- **P9–P13** build freight operational capability on top of it.
- **P14** expands bounded autonomy.

## 2. Where the program stands

- **P0–P5 COMPLETE.**
- **M1** (Work Item, `P6-CP-1`) landed. **M2** (Pipeline Instance, `P6-CP-2`) landed. **`P6-D11`**
  resolved and landed. **M3** (External Effect / Effect Grant, `P6-CP-3`) landed. **M4** (Approval,
  `P6-CP-4`) landed. **M5** (Observation, `P6-CP-5`) landed, with its focused independent review on
  disk.
- **P6 IN PROGRESS. M6 is the next build checkpoint. M6–M13 remain**, and 63 of the 134 transitions.
- **No P6 acceptance criterion is scored.** P6 has not reached phase acceptance. **P7+ blocked.**
- **M1, M2, M3, M4 and M5 all ship dark, and M6 ships dark too.** No live production effect or
  integration is enabled by any of them.

`CURRENT.md`'s ⛔ table blocks **Implementation Phase 7** and names *"provenance, evidence,
observation, claims, identity binding"* inside it. That is **P7's provenance and evidence
platform**, not this unit. **M6 is the P6 Identity Binding Claim state machine** — one row, one
machine, seven states, eleven transitions — and it is exactly what `CURRENT.md`'s "Still owed" cell
and the registry's phase block both mean by *"M6 is the next build checkpoint."* This is the same
sentence pair M5 was handed and it resolved the same way. If you conclude those two sentences
cannot both be true, that is §3.9 behaviour: say so and stop.

---

## 3. The unit: M6, and nothing else

### 3.1 The three sentences the whole unit is a consequence of

> ### **AN IDENTITY BINDING CLAIM IS A CLAIM THAT ARTIFACT X BELONGS TO ENTITY Y.**
> ### **IT IS EVIDENCED, CORRECTABLE AND ESCALATABLE.**
> ### **IT IS NOT AN OBSERVATION, NOT A FACT, NOT AUTHORITY, NOT A CARGO/FREIGHT `Claim`, AND NOT SOMETHING A MODEL MAY CONFIRM.**

ADR-007 §2 says why the vocabulary is load-bearing rather than descriptive: if a claim and an
observation are the same kind of thing, then *"this email is probably about load 4471"* is stored
beside *"the TMS says load 4471 is delivered"*, **and a downstream reader cannot tell which one it
is safe to bill from.** Entity §4 adds the domain trap: a freight `Claim` is cargo damage, a
different entity entirely — **always qualify.**

And the one sentence every defect in this unit comes from confusing, entity §13:

> ### **SD-6 — `provenance_class` IS A DETERMINISTIC, IMMUTABLE FUNCTION OF `match_method`, COMPUTED ONCE AT CREATION AND NEVER INDEPENDENTLY EDITED.**

The two fields cannot drift. `provenance_class` is stored for indexing and queries, but it is
**derived**, and any write must satisfy the mapping. ADR-002 `R-P2`: a change of belief is a **new
claim with a new `match_method`**, never an edit of `provenance_class`.

### 3.2 The canonical state set

```
PROPOSED  CONFIRMED  AMBIGUOUS  REJECTED  SUPERSEDED  CORRECTED  CONFLICTING
```

Seven states, registry §4 / M6 and target spec §12.6. **Do not add an eighth.** In particular there
is no `RESOLVED` — that is M7's vocabulary and it is the single most likely import — no `EXPIRED`
(entity §26: *never*), no `ARCHIVED` and no `DELETED` (§28: *no deletion policy*; §29: *permanent,
the evidence chain*). Terminal: `REJECTED`, `SUPERSEDED`. Non-terminal human-owned: `AMBIGUOUS`,
`CONFLICTING`. Recoverable: `PROPOSED`, `CONFIRMED`, `CORRECTED`. **Do not add a state casually, and
do not add one at all without saying so and stopping.**

### 3.3 Implement the canonical `IB-*` transition contract

The eleven rows of machine §14, by those ids, with those guards. Not an alternative lifecycle that
"achieves the same thing".

| ID | From → To | What it is |
|---|---|---|
| **IB-1** | — → `PROPOSED` | a candidate binding, carrying `provenance_class = f(match_method)` |
| **IB-2** | `PROPOSED` → `CONFIRMED` | **exact trusted-ID match → EXACTLY ONE open entity** |
| **IB-2r** | `PROPOSED` → `CONFIRMED` | a **registered deterministic rule** (`rule_id`) **or** reconciliation across **≥2 sources** |
| **IB-2h** | `{PROPOSED,AMBIGUOUS}` → `CONFIRMED` | **authenticated human, bound to an IMMUTABLE id**, with a `decision_ref` |
| **IB-3** | `PROPOSED` → `PROPOSED` | the model **read** an identifier off a **retained** artifact — **evidence**, re-enters `IB-2` |
| **IB-4** | `PROPOSED` → `AMBIGUOUS` | the model **guessed**, **or** multiple candidates, **or** a **single weak** candidate → Exception |
| **IB-5** | `CONFIRMED` → `SUPERSEDED` | `RecomputedByInferrer` **and** provenance is `LINKER_INFERRED` — a legitimate rebuild |
| **IB-5x** | `CONFIRMED` + `RecomputedByInferrer` | provenance is `OWNER_ASSERTED` → ### ⛔ **ILLEGAL (`GR-9`)** |
| **IB-6** | `CONFIRMED` → `CONFLICTING` | the inferrer **DISAGREES** with an `OWNER_ASSERTED` binding |
| **IB-7** | `CONFIRMED` → `CORRECTED` | `HumanCorrected{decision_ref}`; **PROPAGATES** (`GR-12`, F-17, M-20) |
| **IB-8** | `{PROPOSED,AMBIGUOUS}` → `REJECTED` | disproven, or the entity was cancelled |

**The events those rows emit, and no others.** The F6 family is exactly **six** registered
contracts, and `event_contracts_data.json` — the mechanical projection of `events/registry.md` —
carries exactly these six with these producers:

| Event | Producer | Required payload |
|---|---|---|
| **`ClaimProposed`** | `IB-1` | `provenance_class` (= f(`match_method`)), `subject_ref`, `entity_ref`, `match_method` |
| **`ClaimConfirmed`** | `IB-2` / `IB-2r` / `IB-2h` | `provenance_class ∈ {LINKER_INFERRED, RECONCILED, OWNER_ASSERTED}`, `rule_id?`, `decision_ref?`, `evidence_ref?` |
| **`ClaimEvidenced`** | `IB-3` | `provenance_class = MODEL_EXTRACTED` (fixed), `evidence_ref`, `span` |
| **`ClaimAmbiguous`** | `IB-4` | `reason ∈ {model_inferred, multiple, single_weak}` |
| **`ClaimSuperseded`** | `IB-5` / `IB-8` | `superseded_by?` |
| **`ClaimCorrected`** | `IB-7` | `decision_ref`, `prior`, `new`, `provenance_class = OWNER_ASSERTED` (fixed) |

**Do not mint a seventh `Claim*` name.** There is no `ClaimRejected` (`IB-8` emits `ClaimSuperseded`)
and no `ClaimConflicted` (`IB-6` emits the registered `ConflictRaised` — §3.7). Registry §5's binding
line is explicit: **no machine may define a local synonym.**

`GR-1` applies to everything else: an illegal `(state, trigger)` **raises, persists nothing**, and
emits `IllegalTransitionAttempted` (audit **and** security). Machine §15 names four illegal shapes
by hand, and every one of them is a mutant in §4's battery: `OWNER_ASSERTED` + `RecomputedByInferrer`
(the **B3 regression**), `MODEL_INFERRED` → `CONFIRMED`, two `CONFIRMED` for one subject, and
`MODEL_EXTRACTED` with no evidence span. Confirmation gated on a confidence score is illegal too, and
it is illegal *structurally* rather than by a check — see §3.5.

### 3.4 Provenance is derived, and that is the whole unit

The mapping, entity §13 and ADR-002 §2.3, is a **total function** and must be a **database CHECK**:

| `match_method` | ⇒ `provenance_class` | May it auto-confirm? |
|---|---|---|
| `EXACT_ID` | `LINKER_INFERRED` | ✅ yes — exactly one open entity (IB-2) |
| `RULE` | `LINKER_INFERRED` | ✅ yes — the rule has an id and is auditable (IB-2r) |
| `RECONCILIATION` | `RECONCILED` | ✅ yes — **≥2 sources**, carrying every input (IB-2r) |
| `MODEL_EXTRACT` | `MODEL_EXTRACTED` | ### ⚠️ **NO — it is EVIDENCE.** It re-enters at step 1 (IB-3) |
| `MODEL_INFER` | `MODEL_INFERRED` | ### ❌ **NEVER. Routes to `AMBIGUOUS`** (IB-4) |
| `HUMAN` | `OWNER_ASSERTED` | ✅ yes — and it is **never machine-recomputed** (IB-2h, `R-P3`) |

**A caller must not be able to choose `provenance_class` independently, and must not be able to edit
it afterwards.** Two fields that a caller sets separately is exactly the shape SD-6 exists to
forbid, and a stored derived field with no CHECK behind it is a comment. The CHECK is the mechanism;
a trigger that refuses the `UPDATE` is the other half, and this repository already builds invariants
that way (`trg_checkpoint_witnesses_append_only_update`, `trg_durable_timers_immutable`,
`trg_event_outbox_envelope_immutable`, and M5's own `raw_value`/`content_digest` triggers).

`match_method` is protected beside it, because the mapping is a **function**: a method that can be
rewritten is a provenance that can be rewritten one indirection later.

`SYSTEM_IMPORTED` is the sixth member of the `provenance_class` registry and appears in entity §38's
allow-list for a consequential binding, but **no `match_method` maps to it**, so no M6 claim can
carry it. **Do not invent a seventh `match_method` to reach it.**

### 3.5 What must hold — the authority and safety requirements

Preserve every one of these. They are the unit.

**Provenance, and the laundering that must fail**

- `provenance_class = f(match_method)`, computed **once**, CHECKed by the database, **immutable**
- a caller may **not** set `provenance_class` independently of `match_method`, and may **not** edit
  it afterwards. A change of belief is a **NEW claim**
- **no laundering (`R-P2`)**: `MODEL_INFERRED` may not become `LINKER_INFERRED` by being copied,
  cached, re-read, re-observed, reconciled, re-serialized or passed through a function. ADR-007 §11
  calls this **the most adversarially-tested rule in the system**
- provenance is **runtime-assigned (`R-P1`)**: inbound content that carries, implies or asks for a
  `provenance_class` is **refused**. This is M-13 and it is not deferred anywhere

**Deterministic first, in ADR-007 §4.1's fixed order**

- **IB-2**: an **exact trusted-identifier match** resolving to **exactly one open entity** confirms
- **there is no best-guess fallback.** If the exact match resolves to zero, or to more than one,
  the answer is `AMBIGUOUS` and a human — not the closest candidate
- **IB-2r**: a **registered** deterministic rule, with an **id**, may confirm. An unregistered one
  may not. **Reconciliation requires ≥2 agreeing sources** and carries every input
- **Do NOT resolve V4 and do not invent what the registered freight identity rules actually are.**
  The mechanism is complete; the rule set is per-customer and is discovered at onboarding. The
  fail-closed default is **exact trusted ID only**, unless repository authority already defines more

**The human assertion (IB-2h)**

- requires an **authenticated tenant human**, ACTIVE, backed by a **FOREIGN KEY** into
  `tenant_humans` — M1's precedent, M4's precedent, not a text column any caller may invent
- produces **`OWNER_ASSERTED`** provenance and carries a **`decision_ref`**
- **is bound to an IMMUTABLE target identifier — never an ordinal (L-B).** A rendered
  *"assign unlinked **2** to LD-4471"* resolves **at render time** to an `observation_id`, that id
  is carried in the interaction, and the action binds to **that id**. **If the id is gone, or the
  slot's occupant changed between render and click, the action FAILS CLOSED and says so — it never
  falls back to position.** If the immutable target cannot be proven, fail closed
- a **machine actor** performing `ClaimConfirmed{OWNER_ASSERTED}` is illegal (`[C-6]`, `ER-10`).
  A model proposes; it never confirms

**Model extraction is evidence, not confirmation (IB-3)**

- an **evidence span is required**: `evidence_id` **and** `span`, non-null, enforced by CHECK
- the claim **stays in `PROPOSED`** and the extracted identifier **re-enters deterministic
  matching** at step 1. *The model finds the string; the linker decides*
- **a `MODEL_EXTRACTED` claim with no evidence span must be structurally impossible** (entity §37),
  not merely discouraged. That CHECK is the line between `MODEL_EXTRACTED` (a human can open the
  document and look) and `MODEL_INFERRED` (nothing to look at)
- a **forged** span fails closed

**Ambiguity (IB-4) — and confidence**

- a **model guess** (`MODEL_INFERRED`), **multiple candidates**, and **a single WEAK candidate** all
  route to `AMBIGUOUS`. `AMBIGUOUS` is **human-owned**; do not silently pick a winner
- ### **a `MODEL_INFERRED` claim NEVER becomes `CONFIRMED`, at any confidence.** Confidence 1.0
  changes nothing. There is no threshold — not 0.95, not 0.99, not 1.0
- ### **confidence is structurally INVISIBLE to authorization, policy and every checkpoint guard**
  (`GR-8`, M-16, ADR-005 §3.2). Its one legitimate use is **ordering a human's queue**.
  **`provenance_class` gates. Confidence sorts.** ADR-007 §8 names a confidence threshold as *"the
  single most likely way this architecture gets defeated"*
- a **policy or rule may never branch on a `MODEL_INFERRED` binding** (M-49, `GR-8`, machine §29)

**Supersession, and the B3 regression (IB-5 / IB-5x)**

- `RecomputedByInferrer` supersedes a **`LINKER_INFERRED`** claim **freely** — that is a legitimate
  projection rebuild, and the superseded row is **retained**
- against an **`OWNER_ASSERTED`** claim it is an **ILLEGAL TRANSITION** (`R-P3`, `GR-9`, M-15): it
  raises, persists nothing, and emits `IllegalTransitionAttempted` to audit **and** security
- ### **an `OWNER_ASSERTED` binding survives `RecomputedByInferrer`.** This is the **B3 regression**
  and it is load-bearing: a pre-baseline re-linker recomputed load bindings every intake cycle and
  silently overwrote the owner's own manual correction **while the audit log continued to report
  that the correction stood.** It must not compile against your table
- **`OWNER_ASSERTED` is never silently superseded by inference**, and a retry storm changes nothing

**Inferrer vs owner (IB-6)**

- if the inferrer merely **disagrees** with an `OWNER_ASSERTED` binding: **do NOT overwrite.**
  Transition to `CONFLICTING` and raise per canonical authority (§3.7). **Neyma does not pick a
  winner.** The human binding is **preserved**

**One confirmed binding per subject**

- ### **`UNIQUE (tenant_id, subject_ref) WHERE state = 'CONFIRMED'`** (entity §17) — a **partial
  unique index**, not an application-level check-then-insert two writers both pass. Machine §17
  makes it half the concurrency story; OCC (`[C-10]`) is the other half
- exercise it with **real competing confirmation attempts**. At most one wins; the rest are refused

**Correction (IB-7)**

- `HumanCorrected{decision_ref}`: `CONFIRMED` → `CORRECTED`, provenance `OWNER_ASSERTED`
- **append-only and lineage-preserving.** The prior claim is **retained** — never deleted, never
  edited (`GR-12`, `ER-7`, `corrected_from`)
- **correction-of-correction is supported**, and re-runs propagation from the newly-corrected value
- correction **PROPAGATES** (ADR-007 §6, M-20): the lineage is walked forward, dependent fields are
  re-derived, and **every completed external effect now known to rest on the wrong binding requires
  a Compensation.** *A correction that does not propagate is a lie with a timestamp*
- **you are not building M10.** What M6 owes is the **obligation**, recorded durably and owned:
  which dependents must be re-derived, and which completed effects need a Compensation. **Do not
  fabricate a completed Compensation, do not create a `compensations` table, and do not implement
  `CM-*`** (§3.8)

**Rejection and cancellation (IB-8 / §25)**

- `PROPOSED` or `AMBIGUOUS` → `REJECTED` when disproven or cancelled
- a `CONFIRMED` binding on a **cancelled entity** ⇒ `SUPERSEDED`, and the subject returns to
  `UNBOUND`, **human-owned**

**Replay ([C-5], `GR-11`, machine §21)**

- ### **every `OWNER_ASSERTED` binding replays BYTE-IDENTICAL.** *A projection rebuild rebuilds
  projections, not the owner's mind* (ADR-007 §7)
- the linker does **not** get to "rethink" a human decision during replay
- replay **mints no new authority**, produces **no external effect**, preserves **tenant isolation**,
  and rebuilds projections **deterministically**
- `LINKER_INFERRED` claims may be re-derived freely on crash recovery (machine §36)

**Tenancy `[C-1]`**

- the same `subject_ref` and the same `entity_ref` in two tenants are **two isolated claims**
- **every query and every uniqueness constraint is tenant-first**
- a **wrong-tenant human assertion fails closed**

**Security (§35, §40, ADR-003, ADR-007 §11)**

- a **forged human**, an **inactive** human and a **wrong-tenant** human all fail closed
- a **model actor** cannot confirm
- a **counterparty** is **`MODEL_EXTRACTED` at best, forever.** An email saying *"per our call, you
  approved the $450 detention"* produces an Observation and possibly a `MODEL_EXTRACTED` claim —
  and **NOT an approval, NOT an authorization, and NOT an `OWNER_ASSERTED` anything. It cannot be
  promoted. Ever.** It is a **fraud signal**
- inbound **content cannot set provenance**, and a **forged evidence span** is refused
- an **ordinal target changed between render and click** fails closed

**The P5 transport M6 rides**

- the state row and its canonical event **co-commit** (`GR-2`): no claim whose event never landed,
  no event describing a transition that never happened
- idempotency is the **consumer inbox** (`GR-4`). Re-proposing an identical `LINKER_INFERRED`
  binding for an already-`CONFIRMED` subject is a **no-op** (entity §33, machine §19)
- **F6 ordering is mixed and you must respect the split**: proposals are **order-tolerant**;
  **correction and supersession are STRICT**, guarded by the per-aggregate version. A `ClaimCorrected`
  arriving before its `ClaimConfirmed` is **PARKED** (T13, M-26), and drained the way M3's
  `drain_handler_for` (`P6-D24`) does. Do not invent a second parking mechanism

**The seam with M5, which you may read and may not rewrite**

M5 landed with its binding seam deliberately inert: `bind`/`resolve_unbound` **apply** a
`BindingDecision` and compute nothing, `observations.binding_claim_id` carries **no foreign key**
into a table M5 did not own, and `phase6_observations.DETERMINISTIC_MATCH_METHODS` is spelled
`("EXACT_ID", "RULE", "RECONCILE", "HUMAN")` where M6's canonical `match_method` enum spells the
third one **`RECONCILIATION`**.

- **Use the canonical six in YOUR table.** `EXACT_ID`, `RULE`, `RECONCILIATION`, `MODEL_EXTRACT`,
  `MODEL_INFER`, `HUMAN`
- **Do not rename M5's constant, do not add a foreign key to M5's column, and do not "finish" M5's
  seam.** M5 is landed; its residuals are debt rows. The spelling difference is recorded, not
  repaired, and the permanent scenario asserts M5's shipped shape is byte-unchanged
- if you conclude M6 genuinely cannot be built without changing M5, **say so and stop before
  changing it**

### 3.6 The foreign keys entity §18 names, and what exists to point at

Entity §18 names six: `subject_ref`, `entity_ref`, `evidence_id`, `rule_id`, `conflict_id`,
`corrected_from`. **Two and a half of them have a table to point at today.**

| Column | Target | Exists? |
|---|---|---|
| `subject_ref` | `observations` (M5) | ✅ **build the FK** |
| `corrected_from` / `superseded_by` | self, `identity_binding_claims` | ✅ **build the FK** — this is the lineage |
| the human behind `decision_ref` | `tenant_humans` (M1) | ✅ **build the FK** |
| `entity_ref` | a load / carrier / movement | ❌ freight domain, **P9+** |
| `evidence_id` | `evidence` | ❌ the Evidence Store is not an M-numbered P6 machine (§3.11) |
| `rule_id` | `rules` | ❌ **M12**, not built |
| `conflict_id` | `conflicts` | ❌ **M7**, not built (§3.7) |

Follow M5's precedent exactly: **build the foreign keys whose targets exist; carry the others as
constrained, NOT-NULL-where-the-CHECK-requires-it columns with no foreign key into a table this unit
does not own.** If you conclude the canonical shape genuinely requires one of those tables to point
at — which would be building another unit — **name the clause and stop** rather than building half of
M7, M12 or the Evidence Store to satisfy a foreign key.

### 3.7 The M7 seam — the `CONFLICTING` state, not the Conflict machine

`IB-6` transitions `CONFIRMED` → `CONFLICTING` and its Event column reads `ConflictRaised` **(M7)**.
Two things are true at once and you need both:

- **`ConflictRaised` is registered with `IB-6` in its producer list.** `event_contracts_data.json` —
  the mechanical projection of `events/registry.md`, which is by its own header **the sole canonical
  list of event names** — records `ConflictRaised` as family **F7** with producers
  `['CF-1', 'IB-6', 'EF-4c']`. Contrast `ExceptionRaised`, whose producer list is `['EC-1']` alone:
  that is why M5 correctly refused to mint it and why the same refusal does **not** apply here.
  `state-machines/registry.md` §5 lists the event under M7, and that file's own §6 calls its event
  table **provisional**
- **you are not building M7.** No `conflicts` table, no `conflict_parties`, no `CF-*` transitions,
  no `ConflictOpened`, `ConflictEscalated`, `ConflictResolved` or `ConflictPartyAttached`, and above
  all **no resolution path.** ADR-007 §5.3: a conflict closes by a **registered deterministic rule**
  or by a **human**, and there is **no third way** — `AutoResolve` is an ILLEGAL transition, and *a
  conflict that times out is a conflict resolved by a clock*

**Exercise only the M6-owned side of the contract:** the claim moves to `CONFLICTING`, the
`OWNER_ASSERTED` binding is **preserved intact**, the state is **human-owned**, a registered
`ConflictRaised{kind=INFERRER_VS_OWNER, entity_ref, field, parties, owner_id}` is emitted, and every
consequential action on the entity **blocks** while it stands (ADR-002 `C5`/`C6`). If you find that
emitting it requires a real `conflicts` aggregate row to exist — which would be M7 — **name the
clause and stop.**

### 3.8 The M10 seam — the correction obligation, not the Compensation

`IB-7` propagates, and ADR-007 §6 is explicit about what that means: walk the lineage forward,
re-derive every canonical field derived from the corrected claim, and **for each completed external
effect now known to rest on a wrong binding, raise a Compensation** — itself a fully gated effect,
with its own approval.

**M10 is not built, and you are not building it.** What M6 owes is:

- its **own** canonical event, `ClaimCorrected{decision_ref, prior, new, provenance_class=OWNER_ASSERTED}`,
  whose registered consumers are M6 (propagate) and M10 (Compensation)
- a **durable, M6-owned record of the propagation obligation**: which dependents must be re-derived,
  and which **completed** effects rested on the wrong binding and therefore need a Compensation. It
  names them; nothing silently drops or closes it
- **no `compensations` table, no `CM-*`, no `CompensationRequired`**, and — this matters —
  **no fabricated completed Compensation.** A test that asserts "a Compensation completed" by writing
  the row itself is asserting nothing
- the constraints M10 will apply are **not yours to pre-empt**: ADR-007 §10 says a correction against
  an `UNKNOWN_OUTCOME` effect **forbids** compensation until reality is established, and a correction
  storm raises **one individually-gated Compensation per effect, with no bulk undo**. Record the
  obligation; do not decide it

The repository has already ruled on this exact shape: `event_inbox.expire_overdue` *"marks the row
EXPIRED and RETURNS the expired parks, each naming its accountable owner, for the caller to raise"*
rather than minting another unit's contract. Do the same.

### 3.9 ⚠️ THE KNOWN AUTHORITY QUESTIONS — read this before writing the transition table

The corpus contains three disagreements about M6 that this file does **not** resolve, and neither
may you. Each is a real conflict between authoritative documents. **Report them; implement only what
every reading agrees on.** Product Driver surfaces a reported conflict; it treats a silently invented
resolution as a defect.

**`M6-AQ-1` — how does `IB-7` hand a completed effect to M10?**

- **Via `ClaimCorrected`**, per F6's own consumer column, which names *"M10 (Compensation for effects
  that rested on it)"* directly on that event.
- **Via `CorrectionInvalidatedAnEffect`**, per M10 entity §21 (*"Raised by `CorrectionInvalidatedAnEffect`
  only when the original effect is `VERIFIED`"*), M10 machine §33, M1 entity §32, and target spec
  §12.13's own rows. But **that name appears in no event registry, no event family file, and no
  contract projection** — it is registered nowhere, and registry §5's binding rule says every event
  name a machine writes must already be registered. `P6-D2` records exactly this gap one unit over
  and closes it *"when M10 lands."*

**Every reading agrees on:** correction is append-only, the prior claim is retained, `ClaimCorrected`
carries `decision_ref`/`prior`/`new`/`provenance_class=OWNER_ASSERTED`, and a **durable M6-owned
record of the propagation obligation** exists, names the dependents and the completed effects, and is
never silently closed. Build that. **Do not mint an unregistered event name** to close the question.

**`M6-AQ-2` — does the exact-trusted-ID path carry a `rule_id`?**

- **Yes**, per entity §16's CHECK: *"`provenance_class ∈ {LINKER_INFERRED, RECONCILED}` requires a
  non-null `rule_id`"* — and `IB-2` produces `LINKER_INFERRED`.
- **Not necessarily**, per `IB-2`'s own guard, which names an *exact trusted-ID match to exactly one
  open entity* and no rule at all; ADR-007 §4.1 mentions the rule id only at **step 2**; and **V4**
  leaves the registered freight identity rule set undiscovered, so there is no rule to name.

**Every reading agrees on:** the CHECK exists and is not dropped, a rule-based or reconciliation
confirmation carries a real `rule_id`, and **V4 is not resolved and no freight identity rule is
invented.** Build that, state which reading you implemented and why, and report the ambiguity. Do not
amend a specification to close it.

**`M6-AQ-3` — how does a claim leave `CONFLICTING`?**

- **It does not, from inside M6**: §14 enumerates no transition out of `CONFLICTING`, and `GR-1` makes
  anything unenumerated ILLEGAL.
- **It must**, per registry §4 (`CONFLICTING (NH)`) and machine §9, which both classify it
  **non-terminal human-owned** — and machine §8 lists only `REJECTED` and `SUPERSEDED` as terminal.

**Every reading agrees on:** the human binding is **preserved**, nothing overwrites it, the state is
**human-owned**, and a consequential action on it **blocks**. Build that. **Do not invent an exit
transition, and do not build M7's resolution machine to supply one.**

### 3.10 The F14 tripwires — which are yours, and which are not

Three F14 audit/security events name M6 as a producer, and they are not in the same position.

- **`IllegalTransitionAttempted` is MANDATORY and is yours.** `GR-1` requires it on every illegal
  `(state, trigger)`, to **audit and security**, and M5 already emits it. `IB-5x` is the row that
  matters most
- **`OwnerAssertedOverwriteAttempted` is yours** — F14 names **M6** as its sole producer, calls it
  *"the B3 tripwire (`GR-9`)"* at **Sev-0**, and the name is already in the registered corpus. The
  M4 precedent settles the shape: M4 emits its own named F14 tripwire
  (`CounterpartySelfAuthorizationDetected`) from `approval.py`. `CURRENT.md`'s deferral is by name
  and does not name this one
- **`ProvenanceStrengtheningAttempted` is NOT yours.** F14 names *M5/M6* as producers, but
  `CURRENT.md` scopes the emission half elsewhere by name: *"P5's `IR-R9` (`AC-EVT-011` and the
  `ProvenanceStrengtheningAttempted` F14 emission half) lands **there** [Implementation Phase 7], not
  earlier."* M5 handled it exactly this way. **The laundering REFUSAL is mandatory and present now;
  the F14 emission is not yours.** If you conclude M6 must emit it, name the clause, say that it
  contradicts `CURRENT.md`, and **stop** — that is §3.9 behaviour, not a judgement call

### 3.11 The evidence seam — the span is required, the Evidence Store is not yours

Entity §16's CHECK — **a `MODEL_EXTRACTED` claim requires a non-null `evidence_id` and `span`** — is
**mandatory**. Entity §37 lists a `MODEL_EXTRACTED` claim with no evidence span among the states that
must be **structurally impossible**, §43(c) makes it an acceptance criterion, and §44 names the test.
Build the CHECK.

**The Evidence entity is not an M-numbered P6 machine, and you are not building it.** There is no
`evidence` table, `entities/08-evidence.md` §20 gives Evidence no state machine, and `CURRENT.md`
names *evidence* inside the Phase 7 block. Carry `evidence_id` and `span` as constrained columns with
no foreign key into a table this unit does not own — M5's precedent for `binding_claim_id`, one level
up. **Do not build the Evidence Store, content-addressed retention, artifact storage, span
extraction, or `EvidenceRetained`.** If you conclude the canonical shape requires a real `evidence`
table to point at, **name the clause and stop.**

### 3.12 The checkpoint and approval seams — feed them, do not duplicate them

`src/freight_recon/checkpoint.py` already has the seam. Step 4, native-state validity, takes
`NativeClaim(claim_id, status, conflicting, provenance)` and refuses on a non-`ACTIVE` status, on
`conflicting`, and on a `MODEL_INFERRED` material fact.

- **Entity §38 is the contract to exercise:** a binding that is `CONFLICTING`, `SUPERSEDED` or
  retracted **BLOCKS** the consequential action, and a consequential binding must carry an allowed
  provenance (`LINKER_INFERRED` / `RECONCILED` / `SYSTEM_IMPORTED` / `OWNER_ASSERTED`, M-18). Note
  that M6's own guards already make this true by construction: only `IB-2`, `IB-2r` and `IB-2h` reach
  `CONFIRMED`, so a confirmed binding is `LINKER_INFERRED`, `RECONCILED` or `OWNER_ASSERTED`
- ### **Do not create a second gate authority. P3 remains the gate minter.** The checkpoint is the
  only thing that mints a gate decision (CLAUDE.md rule 17) and M3 is the only thing that claims a
  grant. M6 is the unit with the strongest temptation to become a second gate, because *"is this
  binding good enough to act on?"* is a question it can answer locally and must not answer
  authoritatively
- **Do not edit `checkpoint.py`.** Demonstrate the seam by projecting an M6 claim into the existing
  `NativeClaim`/`ProvenanceClass` types and showing the existing step 4 refuses. If you conclude the
  P3 kernel must change for M6 to be correct, **say so and stop before changing it**
- **Approval (entity §40, machine §28):** an `AMBIGUOUS` or `CONFLICTING` binding on a material entity
  means the approval **cannot be requested** (evidence is not `consistent`) or is **voided**.
  **Do not rebuild M4.** Verify only the existing seam this sentence requires
- **Brake (machine §30):** binding continues under a brake — *it is knowing, not acting* — but a
  **consequential** action using the binding is admission-blocked. `GR-17(iv)` is why: *confirming an
  identity binding used by* an effect, an approval or a money-gating field **is** a consequential
  transition, and carries `GR-13`, brake, policy and freshness

---

## 4. What you must produce

Follow the existing P6 naming conventions — `work_item.py`/`phase6_work_items.py`,
`pipeline_instance.py`/`phase6_pipeline_instances.py`,
`external_effect.py`/`phase6_external_effects.py`, `approval.py`/`phase6_approvals.py`,
`observation.py`/`phase6_observations.py`. These exact paths are what the permanent verification
scenario `p6_m6_identity_binding_claim` looks for; a different name is a scenario failure, not a
style preference. If you believe a different name is genuinely better, **say so and stop** rather
than renaming unilaterally.

| Path | What it is |
|---|---|
| `src/freight_recon/identity_binding_claim.py` | the machine (follows `observation.py`) |
| `src/freight_recon/migrations/phase6_identity_binding_claims.py` | the schema change (follows `phase6_observations.py`) |
| `eval/tests/test_phase6_identity_binding_claim.py` | the acceptance and hostile battery |
| `scripts/probe_phase6_identity_binding_claim.py` | the deterministic narrative probe |
| `scripts/mutate_phase6_identity_binding_claim.py` | the mutation battery (follows `mutate_phase6_observation.py`) |

Wire the migration into `schema.py` and the P2 migration path the way `phase6_observations.py` is
wired, so a freshly created canonical database and a migrated one build to the same shape and the
readiness oracle DERIVES the contract from the DDL rather than from a second list.
`schema_readiness_problems` must still return `[]` on a freshly created canonical database with
foreign keys enabled and verified, and the tenant-first table partition in `CURRENT.md` gains exactly
one row: `identity_binding_claims`.

### The probe's interface

`scripts/probe_phase6_identity_binding_claim.py` must support:

- **no arguments** — run every case; exit `0` only if every one behaved as specified
- `--list-cases` — print the case names, one per line, and exit `0`
- `--list-dimensions` — print every dimension flag and every fault name, and exit `0`
- `--case <case>` — run exactly one case and exit `0` / non-zero

`--case` is what makes M6 testable by Product Driver's dynamic scenario generator: a generated
scenario may not author shell, so a focused, safe, argument-only entry point is the *only* way it can
compose new situations out of M6's real behaviour. Take the interface seriously.

**The cases, by name.** One per canonical obligation. A family missing here is a family the
generator cannot reach and you were never asked to build.

```
proposal-creates-proposed-with-derived-provenance
provenance-is-derived-from-match-method
provenance-class-is-not-independently-editable
provenance-mapping-is-exhaustive-and-immutable
provenance-laundering-refused
content-cannot-set-its-own-provenance
exact-trusted-id-confirms
exact-id-with-two-open-entities-is-ambiguous
no-best-guess-fallback
registered-rule-confirms
reconciliation-requires-two-sources
human-assertion-confirms-owner-asserted
human-assertion-requires-authenticated-tenant-human
human-assertion-requires-decision-ref
ordinal-target-resolves-to-immutable-id-or-fails-closed
ordinal-target-changed-between-display-and-click-fails-closed
model-extract-is-evidence-not-confirmation
model-extracted-requires-evidence-span
extracted-identifier-re-enters-deterministic-matching
forged-evidence-span-fails-closed
model-inferred-routes-to-ambiguous
model-guess-never-confirms-at-confidence-1-0
multiple-candidates-ambiguous
single-weak-candidate-ambiguous
ambiguous-is-human-owned
confidence-is-invisible-to-every-guard
linker-inferred-claim-may-be-recomputed
owner-asserted-binding-survives-relinker
owner-asserted-overwrite-is-illegal-and-recorded
superseded-claim-is-retained
inferrer-vs-owner-raises-conflict-not-a-winner
conflicting-preserves-the-human-binding
m7-conflict-machine-is-not-built
human-correction-moves-confirmed-to-corrected
correction-is-append-only-and-lineage-preserving
correction-of-correction-is-supported
correction-records-its-propagation-obligation
m10-compensation-machine-is-not-built
proposed-or-ambiguous-may-be-rejected
cancelled-entity-supersedes-the-confirmed-binding
one-confirmed-binding-per-subject
competing-confirmations-serialize-at-most-one-wins
occ-on-claim-version
database-invariants
tenant-isolation
cross-tenant-identical-subject-ref
wrong-tenant-human-assertion-fails-closed
forged-human-fails-closed
inactive-human-fails-closed
model-actor-cannot-confirm
counterparty-cannot-become-owner-asserted
state-and-event-co-commit
inbox-idempotency
duplicate-proposal-is-a-no-op
replay-preserves-owner-asserted-byte-identical
replay-creates-no-new-authority-and-no-effect
correction-before-confirmation-is-parked
conflicting-binding-blocks-consequential-action
superseded-binding-blocks-consequential-action
confirmed-binding-provenance-is-allowed-for-consequential-action
ambiguous-binding-does-not-flow-through-approval
m6-mints-no-gate-decision
```

### The mutation axis

M6 ships dark — no linker service, no queue, no live channel — and the driver's only external
concurrency primitive is HTTP. **Every ordering, concurrency, timing, duplication, crash and replay
variation for M6 has to be reachable through this probe's arguments or it is not reachable at all.**

The probe must therefore accept, composable with `--case`:

```
--concurrency 1-8     how many confirmers race the one-CONFIRMED-per-subject index
--delay-ms 0-5000     timing skew between them
--repeat 1-5          duplicate proposal / redelivery pressure
--tenants 1-3         isolation pressure
--candidates 0-8      how many entities the matcher sees for one subject
--confidence 0.0-1.0  the negative control: it must change NOTHING, at 1.0 or at 0.0
--seed <int>          deterministic interleaving; the same seed reproduces the failure
--inject <fault>      the closed fault set below
```

The **closed fault vocabulary**, every member named by the canonical machine, the entity
specification, an ADR, the event registry or a named mandate:

```
model-infer-binding            model-extract-without-span     forged-evidence-span
confidence-one-point-zero      edit-provenance-class          launder-provenance
content-sets-provenance        unregistered-rule              single-source-reconciliation
multiple-candidates            single-weak-candidate          no-candidate
relink-owner-asserted          relink-linker-inferred         inferrer-disagrees
correct-confirmed              correct-a-correction           drop-propagation-obligation
reject-proposed                cancel-entity                  duplicate-proposal
competing-confirmation         occ-conflict                   concurrent-confirm
forged-human                   inactive-human                 wrong-tenant
model-actor-confirm            counterparty-asserts-authority ordinal-target
ordinal-target-moved           malformed-claim                replay
restart-before-confirm         restart-after-correct          unreceived-subject
reorder-stream                 relinker-retry-storm
```

**The vocabulary is CLOSED and BOUNDED. This is not fuzzing.** An unknown fault, or a value outside
the stated range, must be **REFUSED** with a non-zero exit (`2`) and a readable `unknown fault`
message — never a stack trace. Three negative controls are asserted by the permanent scenario:

- `--inject not-a-real-fault` — proves the closure is real
- `--inject expire-claim` — **refused**, because entity §26 says a claim never expires and §28 gives
  it no deletion policy. A probe that accepted it would be producing passing evidence for a
  transition the corpus states does not exist
- `--inject auto-resolve-conflict` — **refused**, because ADR-007 §5.3 makes `AutoResolve` an ILLEGAL
  transition and *a clock is not a decision* — and because M6 does not own conflict resolution at all

### The probe's output contract

The probe must print these literals, verbatim. They are the contract between this file and the
permanent scenario, and they are matched as substrings.

```
behaviours as specified, 0 wrong
A BINDING IS A CLAIM THAT AN ARTIFACT BELONGS TO AN ENTITY, NEVER A FACT
provenance_class IS DERIVED FROM match_method, NEVER CHOSEN
provenance_class IS IMMUTABLE ONCE COMPUTED
A CHANGE OF BELIEF IS A NEW CLAIM, NEVER AN EDITED PROVENANCE
PROVENANCE IS RUNTIME-ASSIGNED, NEVER SET FROM CONTENT
AN EXACT TRUSTED ID MATCHING EXACTLY ONE OPEN ENTITY CONFIRMS
THERE IS NO BEST-GUESS FALLBACK
A REGISTERED DETERMINISTIC RULE MAY CONFIRM; AN UNREGISTERED ONE MAY NOT
RECONCILIATION REQUIRES AT LEAST TWO SOURCES
A HUMAN ASSERTION IS OWNER_ASSERTED, AUTHENTICATED, AND CARRIES A decision_ref
A HUMAN ASSERTION BINDS AN IMMUTABLE ID, NEVER AN ORDINAL
THE ORDINAL RESOLVED TO AN IMMUTABLE ID OR THE ACTION FAILED CLOSED
MODEL_EXTRACTED IS EVIDENCE, NOT CONFIRMATION
A MODEL_EXTRACTED CLAIM WITHOUT AN EVIDENCE SPAN IS STRUCTURALLY IMPOSSIBLE
THE EXTRACTED IDENTIFIER RE-ENTERS DETERMINISTIC MATCHING
A MODEL GUESS ROUTES TO AMBIGUOUS AND NEVER CONFIRMS
CONFIDENCE 1.0 CHANGES NOTHING
CONFIDENCE IS INVISIBLE TO EVERY GUARD
MULTIPLE CANDIDATES ARE AMBIGUOUS, NEVER A WINNER
A SINGLE WEAK CANDIDATE IS STILL AMBIGUOUS
AMBIGUOUS IS OWNED BY A NAMED HUMAN
A LINKER_INFERRED BINDING MAY BE RECOMPUTED FREELY
AN OWNER_ASSERTED BINDING SURVIVES THE RELINKER
RECOMPUTING AN OWNER_ASSERTED BINDING IS AN ILLEGAL TRANSITION
THE SUPERSEDED CLAIM IS RETAINED
THE INFERRER DISAGREEING WITH THE OWNER RAISES A CONFLICT, NOT A WINNER
THE HUMAN BINDING IS PRESERVED UNDER CONFLICT
CORRECTION IS APPEND-ONLY: THE PRIOR CLAIM IS RETAINED
CORRECTION-OF-CORRECTION IS SUPPORTED
THE CORRECTION RECORDED ITS PROPAGATION OBLIGATION
COMPLETED EFFECTS THAT RESTED ON THE WRONG BINDING ARE NAMED FOR COMPENSATION
NO COMPENSATION IS FABRICATED AS COMPLETED
A DISPROVEN OR CANCELLED PROPOSAL IS REJECTED
A CANCELLED ENTITY SUPERSEDES THE BINDING AND RETURNS THE SUBJECT TO A HUMAN
AT MOST ONE CONFIRMED BINDING PER SUBJECT
COMPETING CONFIRMATIONS SERIALIZE: ONE WINS, THE REST ARE REFUSED
A LOST UPDATE ON A CLAIM IS REFUSED
THE SAME subject_ref IN TWO TENANTS IS TWO ISOLATED CLAIMS
A WRONG-TENANT HUMAN ASSERTION FAILS CLOSED
A FORGED OR INACTIVE HUMAN FAILS CLOSED
A MODEL ACTOR CANNOT CONFIRM
A COUNTERPARTY IS MODEL_EXTRACTED AT BEST, NEVER OWNER_ASSERTED
THE STATE ROW AND ITS EVENT COMMIT TOGETHER
A REDELIVERED PROPOSAL IS A NO-OP
EVERY OWNER_ASSERTED BINDING REPLAYED BYTE-IDENTICAL
replay: 0 new claims, 0 rewritten provenance, 0 new authority, 0 external effects
A CORRECTION ARRIVING BEFORE ITS CONFIRMATION IS PARKED, NOT DROPPED
A CONFLICTING OR SUPERSEDED BINDING BLOCKS THE CONSEQUENTIAL ACTION
A CONSEQUENTIAL BINDING CARRIES AN ALLOWED PROVENANCE
AN AMBIGUOUS OR CONFLICTING BINDING DOES NOT FLOW THROUGH APPROVAL
M6 MINTS NO GATE DECISION
THE M7 CONFLICT MACHINE IS NOT BUILT
THE M10 COMPENSATION MACHINE IS NOT BUILT
A LEGACY DATABASE MIGRATES TO THE CANONICAL CLAIM SHAPE
THE DATABASE ENFORCES THE CLAIM INVARIANTS
mutants caught
```

And it must **never** print any of these. Each is a sentence printed only when the thing M6 exists to
prevent has just happened, and any one of them anywhere in the run is the whole unit failing:

```
### MODEL_INFERRED CONFIRMED ###                  ### FORGED EVIDENCE SPAN ACCEPTED ###
### CONFIDENCE GATED A CONFIRMATION ###           ### TWO CONFIRMED BINDINGS ###
### OWNER_ASSERTED OVERWRITTEN ###                ### ORDINAL BOUND WITHOUT AN IMMUTABLE ID ###
### OWNER_ASSERTED SILENTLY SUPERSEDED ###        ### ORDINAL FELL BACK TO POSITION ###
### WEAK CANDIDATE AUTO-CONFIRMED ###             ### INFERRER PICKED A WINNER ###
### BEST GUESS ACCEPTED ###                       ### CROSS-TENANT CONFIRMATION ACCEPTED ###
### provenance_class EDITED ###                   ### FORGED HUMAN ACCEPTED ###
### PROVENANCE LAUNDERED ###                      ### INACTIVE HUMAN ACCEPTED ###
### PROVENANCE SET FROM CONTENT ###               ### MODEL ACTOR CONFIRMED ###
### MODEL_EXTRACTED WITHOUT EVIDENCE SPAN ###     ### COUNTERPARTY BECAME OWNER_ASSERTED ###
### CORRECTION WITHOUT ITS PROPAGATION OBLIGATION ###   ### CLAIM DELETED ###
### COMPENSATION FABRICATED ###                   ### CLAIM EXPIRED ###
### CONFLICT AUTO-RESOLVED ###                    ### EVENT WITHOUT ITS STATE ###
### REPLAY REWROTE OWNER_ASSERTED PROVENANCE ###  ### STATE WITHOUT ITS EVENT ###
### REPLAY MINTED NEW AUTHORITY ###               ### PARKED CORRECTION DROPPED ###
### DOWNSTREAM EFFECT DURING REPLAY ###
```

Also never: `### MISS ###`, `### NOT REFUSED`, `### WRONGLY REFUSED`, `### WRONG REFUSAL`.

### The mutation battery

`scripts/mutate_phase6_identity_binding_claim.py` proves that the load-bearing guards **can fail**. A
guard never seen to fail is a decoration, and a mutation that does not reintroduce the real defect
proves nothing — **verify each mutant actually applies and actually misbehaves before you believe any
result.** At minimum, mutate:

- **`MODEL_INFERRED` allowed to CONFIRM** — the IB-4 routing guard
- **a confidence threshold used as a confirmation guard** — reintroduce `if confidence > 0.98`, the
  defeat ADR-007 §8 names by hand
- **`OWNER_ASSERTED` overwritten by the relinker** — drop the IB-5x provenance guard (**the B3
  regression**)
- **a single weak candidate auto-confirms** — widen the IB-4 guard
- **`provenance_class` independently editable** — drop the SD-6 CHECK, or the immutability trigger
- **`MODEL_EXTRACTED` allowed without an evidence span** — drop that CHECK
- **two `CONFIRMED` bindings allowed** — drop the partial unique index, or drop its `WHERE` clause
- **a human ordinal accepted without immutable-ID resolution** — let it fall back to position
- **inferrer-vs-owner picks the inferrer instead of raising a conflict** — turn IB-6 into an overwrite
- **cross-tenant confirmation** — drop the tenant predicate from one query
- **correction fails to emit its propagation obligation** — drop the IB-7 obligation write
- **replay rewrites `OWNER_ASSERTED` provenance** — let the rebuild re-derive it

Use the safe in-memory save/restore harness the way `mutate_phase6_observation.py` does.
### **Never use `git checkout`, `git restore`, `git stash` or `git clean` to undo a mutation.**
Doing so once destroyed unrecoverable uncommitted work in this repository. Purge `__pycache__`:
restoring a `.py` is not restoring behaviour.

The mutation battery must **not** import the claim machine — mutate text and shell out to pytest, the
way `mutate_phase6_observation.py` does.

### Ship dark

M6 ships dark, exactly as M1, M2, M3, M4 and M5 do.

- **Nothing under `src/freight_recon/` may import `identity_binding_claim`.** The only file under
  `scripts/` that may is `probe_phase6_identity_binding_claim.py`
- **zero production importer, no live integration, no new API, button or channel, and no outbound
  effect path.** M6's product form is an `AMBIGUOUS` queue a human works through and an *"assign
  unlinked N"* action in a channel, so those are precisely the things that must not arrive with it.
  Nothing may join the claim machine to `ingestion`, `email_adapter`, `imap_mailbox`, `email_triage`,
  `inbox_brain`, `extraction`, `browser_use_adapter`, `cdp_readonly`, `tms_adapter`, `slack_adapter`,
  `channels`, `action_callback`, `ops_control`, `mailbox_intake` or any other inbound or outbound
  surface
- **M6 must not make Gmail, Slack, TMS, the browser, accounting or any other product surface start
  using Identity Binding Claim yet**
- **no live effect is enabled**, and the production `GateRegistry` stays EMPTY. M6 authorizes nothing:
  a claim may be an INPUT to the checkpoint and can never mint a gate decision
- the **checkpoint stays the only thing that mints a gate decision**, and **M3 stays the single effect
  authority**
- if canon genuinely requires a dark seam, **name the clause that requires it** before you build it,
  and keep the seam inert

### Tests

`pytest-canonical.ini` **no longer exists.** The 2026-08 engineering-process simplification folded it
into `[tool.pytest.ini_options]` in `pyproject.toml`, and CI runs
`python -m pytest -q -p no:cacheprovider`. Do not reintroduce a second pytest configuration and do
not pass `-c pytest-canonical.ini` anywhere.

Write the adversarial tests entity §44 names, by those names:
`test_owner_binding_survives_relinker`, `test_guess_never_confirms_at_confidence_1_0`,
`test_single_weak_candidate_is_still_ambiguous`, `test_correction_propagates_a_compensation`,
`test_ordinal_binding_resolves_to_immutable_id_or_fails_closed`,
`test_model_extracted_requires_evidence_span`, `test_two_confirmed_bindings_impossible`,
`test_no_provenance_laundering`, `test_inferrer_vs_owner_raises_conflict_not_a_winner`.

`test_correction_propagates_a_compensation` is the one to be careful with: it must assert the
**M6-owned obligation**, not a fabricated `compensations` row. See §3.8.

### Regressions you may not break

Re-run them on the tree you are finishing with, not the one you started from:

- **P3** — the checkpoint kernel, the claim CAS, step order, the brake, the fingerprint, the
  checkpoint matrix
- **P4** — the import gate, the adapter boundary, the governed write route
- **P5** — the event transport, replay isolation, durable timers, **and the canonical event
  contracts**: M6 uses six already-registered F6 names plus registered cross-family names, so
  `test_p5_event_contracts.py` and `test_p5_canonical_event_mint.py` are load-bearing here rather than
  incidental
- **M1, M2, M3, M4, M5** — their acceptance batteries, and M4's and M5's own deterministic probes,
  which must still report `behaviours as specified, 0 wrong` with M6's tables in the schema

---

## 5. Do not

- begin **M7–M13** — in particular do not implement the **M7 Conflict machine** (§3.7) or the
  **M10 Compensation machine** (§3.8), and do not build **M9 Exception**
- begin **P7 or later**, including P7's **provenance and evidence platform** (§2)
- build the **Evidence** entity, the Evidence Store, `evidence` spans, content-addressed retention or
  artifact storage beyond the constrained columns entity §11 and §16 already require (§3.11)
- build **M12 Rule** or a registered-rule registry, and do not resolve **V4** or invent the freight
  identity rules
- build freight workflows, invoice automation, AP/AR workflows, carrier sourcing, dispatch, tracking
  or cargo claims
- build a **Slack**, **Gmail**, **email**, **IMAP**, **portal**, **browser** or **TMS** product
  surface or integration, or **any live linker, queue or "assign unlinked N" action**
- adopt, refactor, wire in or replace `email_triage.py`, `ingestion.py`, `extraction.py`,
  `inbox_brain.py`, `mailbox_intake` routing fields, `action_callback.py` or any other legacy
  identity-linking surface
- enable **live production effects**, **production integrations** or **production autonomy**
- **redesign P0, P1, P2, P3, P4 or P5.** They are COMPLETE. If M6 genuinely needs one of those
  surfaces changed, say so and stop **before** changing it
- weaken **P3, P4 or P5**, or edit `checkpoint.py`
- introduce a **second effect authority** or a **second checkpoint** — the checkpoint is the only
  thing that mints a gate decision and M3 is the only thing that claims a grant
- rebuild or polish **M1, M2, M3, M4 or M5**. They are landed. Their recorded residuals are debt rows,
  and a debt row is a complete deliverable
- resolve unrelated **P6 debt**, and in particular do **not** fix **`P6-D40`** unless a real guard in
  it mechanically blocks this unit — it is a recorded gap in P6's own checkpoint-status guards, not an
  M6 defect
- start a **legacy cleanup campaign**, a **broad documentation cleanup**, or remediate nonblocking
  debt merely because it exists
- push, publish or deploy anything

**If a tiny pre-existing defect directly prevents M6 verification**, you may fix the **smallest
blocking prerequisite** — and you must **identify it explicitly**, say why M6 could not be verified
without it, and keep the fix minimal.

### Known non-blocking items — do not turn these into campaigns

`P6-D35` (the three recorded M5 authority questions), `P6-D36`/`P6-D37` (the CI runtime-limit debt and
the absence of an M5 probe job), `P6-D38`/`P6-D39` (stale gate snapshot, reviewer-harness labelling),
`P6-D40` (the two uncaught P6 checkpoint-status guards), and **V4** (the unvalidated freight identity
rules). Each is recorded. **If one of them actually makes M6 impossible to implement without choosing
an unauthorized reading, STOP and report the conflict rather than guessing.**

---

## 6. How this run works

Product Driver drives implementation, verification, correction and independent review. You do not
need to ask the founder to relay anything: scenario failures, evaluator findings and reviewer findings
come back to **you**, in this same session, as grounded corrections, and the loop retests.

M6 is **tier-1** work under `CLAUDE.md` §7. It is a state machine and an entity contract, which is
tier 2 by itself — but it also lands a **migration**, it is load-bearing for **tenant isolation**, and
it is the unit that decides **whether a machine may overwrite a human's decision**, which is
weakening-a-safety-guard territory by every measure the table uses. §7 says to take the higher tier
once and say so, and this file says so. A focused independent review by a session that did not write
it is therefore required, and Product Driver launches it **inside the run** rather than after it.
Expect a reviewer to re-run your probe, your suite and your mutation battery for itself.

Report a genuine blocker plainly rather than working around it. **§3.9 is the place where reporting a
blocker is the correct outcome rather than a failure.**

**Stop at verified M6. Do not automatically continue into M7.**

Accepting M6 does **not** complete P6, does **not** score a P6 acceptance criterion, does **not**
unblock P7, and enables nothing in production.

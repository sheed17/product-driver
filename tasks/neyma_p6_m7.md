# Build P6 / M7 — Conflict. Only that.

This is the goal Product Driver gives the builder session inside the Neyma repository. Pass it
with:

```bash
.venv/bin/python -m neyma_product_driver run \
  --task "$(cat tasks/neyma_p6_m7.md)" \
  --scenario p6_m7_conflict
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
5. `docs/specifications/entities/10-conflict.md` — the entity, and **§15**, **§16**, **§17**, **§25**,
   **§26**, **§33**, **§36**, **§37**, **§38**, **§39**, **§41** in particular
6. `docs/specifications/state-machines/07-conflict.machine.md` — the seven `CF-*` rows of §14, §15's
   illegal set, §16's precedence, §17's concurrency rule, §22's cancellation sentence and §24's
   reopening rule
7. `docs/specifications/state-machines/registry.md` — §1 triggers, §2 the transition-row defaults,
   §3 `GR-1`…`GR-17` (**`GR-1`**, **`GR-2`**, **`GR-3`**, **`GR-4`**, **`GR-5`**, **`GR-7`**,
   **`GR-10`**, **`GR-11`**, **`GR-13`**, **`GR-14`**, **`GR-15`**, **`GR-17`** above all), §4 the
   canonical state registry, §5 the canonical event registry. **No machine may define a local
   synonym**, so every state and every event name you write must already be registered. Read §6:
   that file's own event table calls itself **provisional**, which matters for §3.6 below
8. `docs/specifications/events/07-conflict-events.md` — the **F7** family contract, its payloads, its
   consumers, and its cross-cutting section
9. `docs/specifications/events/registry.md` — §3's F7 line, §5's consequential list, §7's projection
   rules, §8 **ORDERING** (F7 is **order-tolerant**, and read the last bullet: order-tolerant does
   **not** mean order-free for a field-mutating fact), §9 **COORDINATION EVENTS**, §10's `ER-14`
   and `ER-15`
10. `docs/specifications/events/14-audit-security-events.md` — the F14 tripwires, and which of them
    this unit is named the producer of (§3.10 below: exactly one)
11. `docs/architecture/decisions/ADR-007-identity-claims-and-conflict.md` — **§5 in full**: §5.1's
    five generation cases, §5.2's invariant, §5.3's *"there is no third way"*; and §8's rejected
    alternatives, §10's failure modes, §11's security note, §13's merge-gating tests
12. `docs/architecture/decisions/ADR-002-state-classes-and-lineage.md` — **§2.3** (the six-member
    `provenance_class` registry, `R-P1`/`R-P2`/`R-P3`) and **`C5`**/**`C6`**: five distinct evidence
    conditions, and conflicting evidence **must block**
13. `docs/architecture/target-system-specification.md` **§12.7**, **§20.7** (`RULE_VS_RULE`), **§19.5**
    (the `UNKNOWN_OUTCOME` row that reads *"Observation conflicting"*), **§24** (a conflict as a
    security control) and **§21.6**'s rejected brake scope (*"an entity is ALREADY frozen by an open
    Conflict"*)
14. `docs/specifications/entities/00-conventions.md` — `[C-1]`, `[C-2]`, `[C-3]`, `[C-5]`, `[C-6]`,
    `[C-7]`, `[C-8]`, `[C-9]`, `[C-10]`, and the addendum's **`K-1`** (`decision_ref` resolves, never
    free text) and **`K-2`** (`entity_ref` is a canonical projection row — not a `target_resource_id`,
    not a `subject_ref`)
15. `docs/specifications/acceptance/foundational-machine-acceptance.md` — M7's row
    (**`AC-MACH-701..707`**, state oracle *"row + field condition"*, gate **G1**) and the ten
    per-machine mandatory assertions
16. `docs/specifications/acceptance/platform-safety-acceptance.md` — **`AC-SAFE-017`** (an open
    Conflict blocks dependent consequential actions) and the `AC-CKPT-4-*` conditions
17. the **P5** event transport, outbox/inbox, replay isolation and **durable timers**:
    `src/freight_recon/event_outbox.py`, `event_inbox.py`, `event_replay.py`, **`event_timers.py`**,
    `event_contracts.py` and `event_contracts_data.json`
18. **M1** Work Item (`src/freight_recon/work_item.py`) — the `owner_id` FOREIGN KEY into
    `tenant_humans` is the precedent for a **named, ACTIVE human owner assigned at creation**, and
    entity §21 is the precedent for a **system-created** record that still gets one. Note also that
    M1 already **consumes** `ConflictRaised` as a `WI-6` trigger and already carries an
    `open_conflict` fact on `WI-8` — two seams M7 feeds and must not rewrite
19. **M3** External Effect (`src/freight_recon/external_effect.py`) — **`EF-4c`**, the readback that
    contradicts the approved fingerprint. Read what it emits **today**, and §3.7 below
20. **M4** Approval (`src/freight_recon/approval.py`) — the worked example of an **authenticated
    human actor** guard, and of an F14 fraud signal emitted by its named producer
21. **M6** Identity Binding Claim (`src/freight_recon/identity_binding_claim.py`,
    `migrations/phase6_identity_binding_claims.py`) — **`IB-6`** is a registered producer of
    `ConflictRaised` and it already emits one. Read exactly what it writes, and §3.6 below. Its
    migration, probe and mutation battery are the shape yours follow
22. `src/freight_recon/checkpoint.py` — **step 4, native-state validity**, and the `NativeClaim`,
    `ProvenancedFact` and `EvidenceCondition` types M7 feeds. **You are not changing this file**
    (§3.9)

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
agreement. **§3.8 below names three such conflicts that are already known. Read it before you write
the transition table.**

---

## 1. What Neyma is — the stable identity

Neyma is an **AI-native operating platform and system of action for SMB freight and logistics
companies.**

It is **not** an invoice bot, a document-extraction product, a Slack bot, a TMS chatbot, a browser
wrapper, an AP tool, an email triage system, or a disconnected collection of agents. If a piece of
legacy code in this repository suggests otherwise, that code is material, not direction.

- **P0–P8** build the shared governed operating engine.
- **P9–P13** build freight operational capability on top of it.
- **P14** expands bounded autonomy.

## 2. Where the program stands

- **P0–P5 COMPLETE.**
- **M1** (Work Item, `P6-CP-1`), **M2** (Pipeline Instance, `P6-CP-2`), **M3** (External Effect /
  Effect Grant, `P6-CP-3`), **M4** (Approval, `P6-CP-4`), **M5** (Observation, `P6-CP-5`) and **M6**
  (Identity Binding Claim, `P6-CP-6`) have all landed, each with its focused independent review on
  disk. **`P6-D11`** is resolved and landed. The **P3/P4 one-connection-per-thread concurrency
  correction** at `d70a4e7` is landed and **must not be reworked**.
- **P6 IN PROGRESS. M7 is the next build checkpoint. M7–M13 remain**, and **52 of the 134
  transitions**.
- **No P6 acceptance criterion is scored.** P6 has not reached phase acceptance. **P7+ blocked.**
- **M1, M2, M3, M4, M5 and M6 all ship dark, and M7 ships dark too.** No live production effect or
  integration is enabled by any of them.

`CURRENT.md`'s ⛔ table blocks **Implementation Phase 7** and names *"provenance, evidence,
observation, claims, identity binding"* inside it. That is **P7's provenance and evidence
platform**, not this unit. **M7 is the P6 Conflict state machine** — one aggregate, one machine,
five states, seven transitions — and it is exactly what `CURRENT.md`'s "Still owed" cell and the
registry's phase block both mean by *"M7 is the next build checkpoint."* This is the same sentence
pair M5 and M6 were handed and it resolved the same way both times. If you conclude those two
sentences cannot both be true, that is §3.8 behaviour: say so and stop.

---

## 3. The unit: M7, and nothing else

### 3.1 The three sentences the whole unit is a consequence of

> ### **A CONFLICT IS TWO OR MORE MUTUALLY EXCLUSIVE CLAIMS OR OBSERVATIONS ON THE SAME FIELD.**
> ### **ITS PURPOSE IS TO MAKE DISAGREEMENT VISIBLE AND BLOCKING — IT IS THE MECHANISM BY WHICH NEYMA NEVER SILENTLY CHOOSES.**
> ### **IT IS NOT `unknown`, NOT AN ERROR, AND NOT RESOLVABLE BY RECENCY, CONFIDENCE, A MODEL, OR A CLOCK.**

Entity §4 states the distinction the whole unit rests on, and it is **I8**:

> ### **A CONFLICT IS NOT `unknown`. WE DO NOT LACK INFORMATION — WE HAVE TOO MUCH, AND IT DISAGREES.**

ADR-007 §5.2 says why the difference is load-bearing rather than pedantic: *"treating a conflict as
merely 'missing data' would let a 'best available' read slip through; treating it as `conflicting`
stops everything, which is correct."* `absent`, `unknown`, `consistent`, `conflicting` and `stale`
are **five distinct conditions** (`ADR-002 C5`) and collapsing any two is a defect.

And the sentence the entity spends forty-five points defending, §36:

> ### **THE INVARIANT: WHILE A CONFLICT IS OPEN, THE FIELD IS `conflicting` AND BLOCKS EVERY CONSEQUENTIAL ACTION ON THAT ENTITY.**

### 3.2 The canonical state set

```
RAISED   OPEN   ESCALATED   RESOLVED_BY_RULE   RESOLVED_BY_HUMAN
```

Five states, registry §4 / M7 and target spec §12.7. **Do not add a sixth.** In particular there is
no `CANCELLED` (§3.8's `M7-AQ-3` — and inventing one is how that question gets silently answered),
no `EXPIRED` (entity §26 and machine §12/§23: **never** — *a conflict that times out is a conflict
resolved by a clock*), no bare `RESOLVED` (that is **M9 Exception's** vocabulary and the single most
likely import), no `AUTO_RESOLVED` and no `DISMISSED`.

Terminal: `RESOLVED_BY_RULE`, `RESOLVED_BY_HUMAN`. Non-terminal human-owned: `OPEN`, `ESCALATED`.
Recoverable: `RAISED`. **Do not add a state casually, and do not add one at all without saying so
and stopping.**

### 3.3 The canonical kind set

```
SYSTEM_VS_SYSTEM   CLAIM_VS_CLAIM   CLAIM_VS_OBSERVATION
INFERRER_VS_OWNER  READBACK_VS_APPROVED   RULE_VS_RULE
```

Six kinds, entity §12 and the `ConflictRaised` contract's own `kind` enum. **There is no seventh**,
and the contract projection in `event_contracts_data.json` will refuse one at emission. ADR-007 §5.1
enumerates the same six situations in prose.

### 3.4 Implement the canonical `CF-*` transition contract

The seven rows of machine §14, by those ids, with those guards. Not an alternative lifecycle that
"achieves the same thing".

| ID | From → To | What it is |
|---|---|---|
| **CF-1** | — → `RAISED` | detection of any canonical `kind`; ### **a human `owner_id` at creation**; ### **the field becomes `conflicting` (`GR-10`) in the SAME COMMIT** |
| **CF-2** | `RAISED` → `OPEN` | acknowledged |
| **CF-3** | `OPEN` → `RESOLVED_BY_RULE` | ### **a REGISTERED, versioned, deterministic rule (`rule_id`) applies — never recency, confidence, or a model** |
| **CF-4** | `OPEN` → `RESOLVED_BY_HUMAN` | ### **a valid `decision_ref` (`GR-14`, `K-1`)** |
| **CF-5** | `OPEN` → `ESCALATED` | `AgeThresholdCrossed`, on the **existing P5 durable timer substrate** |
| **CF-6** | `ESCALATED` → `{RESOLVED_BY_RULE, RESOLVED_BY_HUMAN}` | ### **as CF-3/CF-4, resolved by TARGET STATE — never positionally** |
| **CF-7** | `{RAISED, OPEN}` → *(more parties)* | a new disagreeing party is detected; **append `parties[]`** |

**The events those rows emit, and no others.** The F7 family is exactly **five** registered
contracts, and `event_contracts_data.json` — the mechanical projection of `events/registry.md` —
carries exactly these five:

| Event | Producer | Required payload |
|---|---|---|
| **`ConflictRaised`** ‡ | `CF-1` **/ `IB-6` / `EF-4c`** | `kind`, `entity_ref`, `field`, `parties[]`, `owner_id` |
| **`ConflictOpened`** | `CF-2` | *(none)* |
| **`ConflictPartyAttached`** | `CF-7` | `party_ref`, `provenance_class`, `parties[]`, `entity_ref`, `field` |
| **`ConflictEscalated`** | `CF-5` | *(none)* |
| **`ConflictResolved`** | `CF-3` / `CF-4` | ### **EXACTLY ONE of `rule_id` \| `decision_ref`** |

**Do not mint a sixth `Conflict*` name.** There is no `ConflictExpired`, no `ConflictAutoResolved`,
no `ConflictWinnerChosen`, no `ConflictDismissed` and no `ConflictCancelled`. Registry §5's binding
line is explicit: **no machine may define a local synonym**, and `events/registry.md`'s own header
calls itself **the sole canonical list of event names**.

**Three things the P5 contract layer already enforces mechanically, which you must not work around:**

- **`ConflictResolved` declares `rule_id | decision_ref` as a required `one_of`.** Neither, or both,
  is refused at emission by `event_contracts.validate`. That refusal is a mechanism you inherit —
  it is not a substitute for the entity §16 CHECK, and you build both
- **`ConflictOpened` and `ConflictEscalated` declare NO payload fields**, and a producer may not
  invent one. `state-machines/registry.md` §5 shows a `kind` payload on those two; that file's own
  §6 calls its event table **provisional**, and `events/registry.md` is binding. **Emit no payload
  on those two**, and say that you read it that way
- **`ConflictResolved`'s registered producers are `CF-3` and `CF-4` — not `CF-6`.** Machine §14's
  `CF-6` Event cell reads `DELEGATES_TO:RESOLVED_BY_RULE=CF-3;RESOLVED_BY_HUMAN=CF-4`, so an
  escalated resolution emits under **the delegated transition id chosen by its TARGET STATE**. That
  is what "resolved by target state, never positionally" means mechanically, and the existing
  producer check is what proves it

`GR-1` applies to everything else: an illegal `(state, trigger)` **raises, persists nothing**, and
emits **`IllegalTransitionAttempted`** (audit **and** security). Machine §15 names five illegal
shapes by hand, and every one of them is a case in §4's battery:

1. **`AutoResolve`, and ANY `TimerFired`-to-resolved** — *a clock knows nothing about freight*
2. a **resolution with neither `rule_id` nor `decision_ref`**
3. a **model** resolving (`GR-7`, `[C-6]`)
4. a **consequential action proceeding on a field with an open conflict** (`GR-10`)
5. an **ownerless** Conflict — the owner is assigned at creation

### 3.5 What must hold — the authority and safety requirements

Preserve every one of these. They are the unit.

**The raise, the freeze and the owner — one commit, one human**

- **CF-1 writes the Conflict row and sets the disputed field's evidence condition to `conflicting`
  in ONE transaction** (entity §15, machine §4/§35, `[C-2]`). There must never be a durable state in
  which the Conflict exists while the field is still consequentially usable, or the field is frozen
  with no Conflict history explaining why
- ### **`owner_id` is a NAMED HUMAN, NOT NULL FROM CREATION** (entity §10/§16, machine §5). An
  ownerless Conflict is **structurally impossible** (entity §37), enforced by the database, and
  backed by a **FOREIGN KEY into `tenant_humans`** — M1's precedent for `owner_id`, M4's for
  `granted_by`, M6's for the human behind a `decision_ref`. The human must be **ACTIVE**
- CF-1's trigger type is **`S|X`** — a deterministic system decision or an observed external event.
  A system-detected Conflict **still gets a named human owner at creation**, exactly as a
  system-created Work Item does (M1 entity §21: *"`owner_id` MUST be assigned at creation — creation
  with a null owner fails"*). ### **The caller SUPPLIES the named ACTIVE human; the machine never
  picks one, and `system` is not a human** (`I1`). A **model actor** may never be the owner
  (`[C-6]`, `ER-9`)
- the six `kind` values are the closed set of §3.3, enforced by a database CHECK and by the event
  contract

**The invariant — an open Conflict blocks**

- ### **While the state is `RAISED`, `OPEN` or `ESCALATED`, the field is `conflicting` and BLOCKS
  every consequential action on that entity** (entity §36, machine §16, `GR-10`, `ADR-002 C6`,
  `AC-SAFE-017`). Machine §16 says this precedence **dominates all machines that read the field**
- **`RAISED` already blocks.** The three open states are one set for this purpose; "not acknowledged
  yet" is not "not blocking yet"
- **No Effect Grant is minted for an entity with an open Conflict on a material field** (entity §39)
- **An open Conflict on a material field voids or blocks the M4 approval** — `consistent` fails
  (entity §40, machine §28)
- ### **Do not build a second gate. `checkpoint.py` step 4 remains the authority that admits or
  refuses consequential execution, and M3 remains the single effect authority.** M7 supplies
  **native state**: it projects into the existing `NativeClaim(conflicting=…)` and the existing
  `EvidenceCondition.CONFLICTING` on a `ProvenancedFact`, and the existing step 4 refuses. See §3.9

**No silent winner — closure has exactly two ways**

- ADR-007 §5.3: ### **THERE IS NO THIRD WAY. Not recency. Not confidence. Not source priority —
  unless a registered rule says so, with an id. Not a model. Not a timeout.**
- **`CF-3` requires a REGISTERED, versioned, deterministic rule with an `rule_id`.** An unregistered
  rule may not resolve. A confidence threshold wearing a rule's name is not a rule. A
  model-produced pseudo-rule is not a rule
- **`CF-4` requires a valid `decision_ref`** — `K-1`: it resolves to an `audit_events` human-decision
  row recording an **authenticated** human, or to an `ACTIVE` `rule_id`. A string that references
  nothing is not a `decision_ref` and the transition is illegal `[C-4]`
- ### **A counterparty never resolves a Conflict**, and a model never does (`[C-6]`, `GR-7`, `ER-9`)
- **`RULE_VS_RULE` is the case that proves the rule about rules:** two genuinely conflicting standing
  rules ⇒ **FAIL CLOSED** ⇒ this Conflict ⇒ a human resolves it (`GR-15`, machine §29, spec §20.7).
  ### **NEYMA NEVER PICKS A WINNER, and it never auto-merges**

**The timer escalates and never resolves**

- **`CF-5`: `OPEN` → `ESCALATED` on `AgeThresholdCrossed`**, using the **existing P5 durable timer
  substrate** (`event_timers.py`). Do not invent a second timer mechanism and do not add a sweep
- ### **ANY `TimerFired`-to-resolved transition is ILLEGAL** (machine §15, entity §22). A clock knows
  nothing about freight
- ### **A CONFLICT NEVER EXPIRES.** Entity §26 and machine §12/§23 say it three times. It **ages, and
  it escalates**
- `GR-5` applies here as everywhere: a timeout alone never proves anything

**One open Conflict per field, and the new party attaches**

- ### **`UNIQUE (tenant_id, entity_ref, field) WHERE state IN ('RAISED','OPEN','ESCALATED')`**
  (entity §17, machine §17) — a **partial unique index**, not an application-level check-then-insert
  that two concurrent detectors both pass. M6's `WHERE state = 'CONFIRMED'` index is the precedent
- **`CF-7` appends a party to the EXISTING Conflict.** A second or third disagreeing source **must
  not create a second concurrent open Conflict** (entity §33, machine §17/§19, F7's Dedup line)
- ### **Each `parties[]` entry carries ITS OWN `provenance_class`**, one of the canonical six
  (`[C-7]`), **carried and never strengthened** (`ER-14`, `R-P2`). An `INFERRER_VS_OWNER` Conflict
  **specifically records that one party is `OWNER_ASSERTED`** (entity §13, machine §31) — that is the
  evidence of *why the inferrer did not overwrite it*. **No provenance laundering**
- `ConflictPartyAttached` is **load-bearing for replay**: F7 says `ConflictRaised` puts `parties[]`
  into history at creation, and ### **without the attach event a full-history rebuild reproduces a
  STALE PARTY SET** — an `AC-EVT-008` digest divergence, which `AC-EVT-015` escalates to an
  auto-brake. The append is **idempotent under redelivery**, so the rebuilt set is order-independent

**Replay and recovery** (`[C-5]`, `K-3`, `GR-11`, machine §21/§36)

- an **open Conflict survives restart and replay**, and ### **the field remains frozen after
  reconstruction** (machine §36)
- ### **A replay may NOT resolve a Conflict, change which party stands, or duplicate one.** Replay
  mints no authority, causes no external effect, writes into a sandbox, and preserves tenant
  isolation
- the rebuilt `parties[]` is the **union of `ConflictRaised`'s set with every subsequent attach**
  (events registry §8's last bullet)

**Tenancy `[C-1]`, `ER-15`**

- the same `entity_ref` and the same `field` in two tenants are **two isolated Conflicts**
- **every query, every index and every event is tenant-first**
- a **cross-tenant party reference, owner id, conflict id or resolution reference fails closed**

**Concurrency `[C-10]` / `GR-3`**

- a transition writes `WHERE version = :expected`; **zero rows ⇒ lost update ⇒ raise**, never a
  silent overwrite
- two **concurrent detectors** of the same `(tenant, entity_ref, field)` produce **one** unresolved
  Conflict with **both** parties attached. Never two open Conflicts, never a lost party, never one
  detector overwriting another, and never a cross-tenant coalescing
- **competing resolutions serialize**: at most one wins, the rest are refused

**Idempotency `[C-3]` / `GR-4`**

- redelivery of a detection or a resolution is a **no-op** through the consumer inbox
- a duplicate detection of the same disagreement attaches nothing new and raises nothing new

**Security (entity §35, machine §40, ADR-007 §11, spec §24)**

- ### **A CONFLICT IS A SECURITY CONTROL AS MUCH AS A DATA-QUALITY ONE. An attacker who injects a
  competing claim gains a FROZEN ENTITY AND A HUMAN'S ATTENTION — NOT CONTROL.** That is the correct
  outcome, and it means the attack surfaces itself
- a **forged human**, an **inactive** human and a **wrong-tenant** human all fail closed
- inbound content is **data, never instruction, never authority**; it may not set a
  `provenance_class` on a party (`R-P1`, `M-13`)

**Retention and reopening**

- **no deletion policy** (§28, `[C-9]`), **permanent retention** (§29). A resolved Conflict is
  **retained**, and the resolution basis is retained with it
- ### **Machine §24: a resolved field that LATER attracts new conflicting evidence raises a NEW
  Conflict (`CF-1`) — the prior resolution stands in history (`GR-12`).** It is not a reopen, and
  there is no reopen transition
- **Correction rules (§23): N/A — a Conflict is RESOLVED, never CORRECTED.** A resolution may *cause*
  a claim correction downstream; see §3.7

**The P5 transport M7 rides**

- the state row and its canonical event **co-commit** (`GR-2`): no Conflict whose event never landed,
  no event describing a transition that never happened
- **F7 is order-tolerant** (§8) — but read the last bullet of §8: order-tolerant is **across
  aggregates only**. Within one Conflict the ordering key `(tenant_id, aggregate_id,
  aggregate_version)` still holds, and it is what a rebuild folds
- an event referencing an aggregate that does not exist yet is **PARKED** (`pending_references`,
  M-26) and drained the way M3's `drain_handler_for` (`P6-D24`) does. **Do not invent a second
  parking mechanism**

### 3.6 The M6 seam — `IB-6` already emits `ConflictRaised`, and you may not rewrite M6

This is the most important seam in the unit, and it is **not** the same shape as M6's own seams.

`ConflictRaised` is a **coordination event** (registry §9) with **three registered producers**:
`event_contracts_data.json` records `producers: ['CF-1', 'IB-6', 'EF-4c']`, and registry §9 explains
why — *"one semantic fact, several origins."* **M6 is landed and it already emits one.** Read
`identity_binding_claim.py`'s `IB-6` before you write a line of M7: it mints a `conflict_id` of its
own, emits `ConflictRaised{kind=INFERRER_VS_OWNER, …}` on the `conflict` aggregate at version 1, and
**writes no Conflict row**, because at the time it was built there was no table to write to.

**What every reading agrees on, and what you must therefore do:**

- ### **DO NOT REWRITE M6.** `CURRENT.md`'s ⛔ table forbids rebuilding or polishing M1–M6; their
  residuals are debt rows, and a debt row is a complete deliverable. `IB-6`'s shipped behaviour is
  asserted **byte-unchanged** by the permanent scenario
- ### **DO NOT MINT A SECOND `ConflictRaised` FOR A DISAGREEMENT M6 HAS ALREADY ANNOUNCED**, and do
  not create a second open Conflict for a field that already has one. The partial unique index of
  §3.5 is not optional for conflicts M7 owns, and duplicating one is the defect this whole unit
  exists to prevent, committed by the unit itself
- ### **DO NOT MINT A SYNONYM.** No `ConflictDetected` event (that is a **trigger** name in target
  spec §12.7 and machine §33, and it is registered in **no** event registry — minting it would be
  exactly the `CorrectionInvalidatedAnEffect` mistake M6 correctly refused)
- **DO NOT SILENTLY SWALLOW IT EITHER.** Record the seam as a durable, M7-owned obligation naming
  that a registered non-`CF-1` producer emits `ConflictRaised` with no M7 aggregate row, and
  **REPORT `M7-AQ-1`** (§3.8). That is the `event_inbox.expire_overdue` pattern this repository has
  already ruled on twice: mark it, name the accountable owner, and return it — do not mint another
  unit's contract to close it

**Build `CF-1` fully for the Conflicts M7 raises.** Prove the M6 seam is preserved and the question
is reported. Do not choose one of `M7-AQ-1`'s readings.

### 3.7 The M3/M4 seam — `READBACK_VS_APPROVED`, and the `UNKNOWN_OUTCOME` you may not touch

`EF-4c` is the third registered producer of `ConflictRaised`, and its position is different again:
**M3 emits no `ConflictRaised` today.** Read `external_effect.py`'s `EF-4c` row: it moves
`ATTEMPTED → UNKNOWN_OUTCOME` and emits `VerificationConflict{unknown_reason:
OBSERVATION_CONFLICTING}` (and `OutcomeUnknown`), and nothing else.

**Every reading agrees on:**

- a readback contradicting the approved material facts is ### **NOT an ordinary `FAILED`**. `GR-5`:
  a timeout alone never proves failure, and `EF-3f`'s `FAILED` requires **affirmative proof** the
  effect did not occur. A contradiction is not proof of either outcome
- ### **`UNKNOWN_OUTCOME` NEVER SILENTLY BECOMES SUCCESS OR FAILURE** (`GR-6`), no timer may move it,
  and only `EF-5`'s `RealityEstablished` — a human decision or a later proving observation — closes
  it. ### **M7 MUST NOT REWRITE, SHORTEN OR ROUTE AROUND THAT.** Resolving a `READBACK_VS_APPROVED`
  Conflict is **not** establishing reality, and it may not set an Effect Grant's state
- entity §39 says the `READBACK_VS_APPROVED` Conflict is ### **HOW an `UNKNOWN_OUTCOME` records that
  something else may have acted** — spec §19.5's *"YES — urgently"* row. It is **additive** evidence
  beside the effect's own state, never a substitute for it
- **M7 never launders the disagreement into a normal failure, and never picks a side**

**Do not modify M3 to make M7 easy.** M3 is landed, it is the single effect authority, and it is a
tier-1 surface. Whether `EF-4c` must grow a `ConflictRaised` emission is **`M7-AQ-2`** (§3.8) —
report it; do not answer it by editing `external_effect.py`. If you conclude M7 genuinely cannot be
built without changing M3, **say so and stop before changing it.**

**M4:** an `AMBIGUOUS` or `CONFLICTING` material field means the approval **cannot be requested**
(evidence is not `consistent`) or is **voided** (entity §40, machine §28). **Do not rebuild M4.**
Verify only the existing seam this sentence requires.

### 3.8 ⚠️ THE KNOWN AUTHORITY QUESTIONS — read this before writing the transition table

The corpus contains three disagreements about M7 that this file does **not** resolve, and neither
may you. Each is a real conflict between authoritative documents, and each is mechanically
demonstrable rather than a reading of tone. **Report them; implement only what every reading agrees
on.** Product Driver surfaces a reported conflict; it treats a silently invented resolution as a
defect.

**`M7-AQ-1` — how does `IB-6`'s `ConflictRaised` materialize an M7 Conflict row?**

- **It must materialize one**, per entity §15 (*"Raising a Conflict and setting the field's evidence
  condition to `conflicting` occur in one transaction"*), §17's partial unique index, §33's *"a
  second detection of the same `(entity, field)` disagreement attaches a party to the existing open
  Conflict, not a new one"*, and F7's *"the dedup index still refuses a second `ConflictRaised`"*.
  A registered producer that emits the event and writes no row leaves those four sentences with
  nothing to hold, and `IB-6` mints a **fresh** `conflict_id` on every call — so two disagreements on
  one field are two `ConflictRaised` events for two conflict ids that the index never sees.
- **It cannot materialize one from inside M6**, because M6 is **landed** — `CURRENT.md` forbids
  rebuilding or polishing it — and because `CF-1` is M7's row, not M6's: a machine writing another
  machine's aggregate is the co-transition shape registry §5 permits only where it is written down,
  and no canonical file writes it down for `IB-6`.
- And **M7 cannot raise it on the event's arrival either**, without deciding that a coordination
  event instructs a consumer — which `ER-1` and registry §9 forbid in terms: *"a coordination event
  does NOT instruct a consumer to transition."*

**Every reading agrees on:** the shipped `IB-6` behaviour is **unchanged**, no second
`ConflictRaised` and no second open Conflict is created for a disagreement M6 already announced, no
synonym event is minted, the partial unique index holds for every Conflict M7 owns, and a **durable,
M7-owned record of the seam** exists and names it. Build that. **Do not amend a specification to
close it, and do not edit `identity_binding_claim.py`.**

**`M7-AQ-2` — is `EF-4c` required to emit `ConflictRaised`, and whose change is that?**

- **Yes**, per `events/registry.md` §3 and §9 and the contract projection, all three of which record
  `EF-4c` as a producer of `ConflictRaised`; and per entity §21, which says a Conflict is raised on
  *"a readback that contradicts the approved facts (`OBSERVATION_CONFLICTING`)"*.
- **Not today**, per the shipped `external_effect.py`, whose `EF-4c` row emits `VerificationConflict`
  alone; per `CURRENT.md`, which forbids rebuilding M3; and per `CLAUDE.md` §7, which makes the
  effect boundary tier-1 — so growing an emission there is an M3 change with an M3 review, not an
  M7 detail.

**Every reading agrees on:** `EF-4c`'s shipped behaviour is **unchanged by M7**, `UNKNOWN_OUTCOME`
semantics are **untouched**, a `READBACK_VS_APPROVED` Conflict — however it comes to be raised —
**blocks** like any other and is **never** laundered into a normal failure, and M7 mints no
substitute event. Build that. **Do not edit `external_effect.py`.**

**`M7-AQ-3` — how does a Conflict get cancelled when the disagreement disappears?**

- **It can be cancelled**, per entity §25 and machine §22, which both say cancellation is possible
  *"only if the underlying disagreement disappears (a party retracts) — **still an event, never
  silence**."*
- **There is no way to**, because machine §14 enumerates **only** `CF-1`…`CF-7` and `GR-1` makes
  anything unenumerated **ILLEGAL**; registry §4 gives M7 **five** states and none of them is
  `CANCELLED`; and **no** `ConflictCancelled` or `ConflictRetracted` name appears in
  `events/registry.md`, in F7, or in the contract projection. The sentence names an event that is
  registered nowhere and a state that does not exist.

**Every reading agrees on:** a party retraction ### **NEVER SILENTLY CLOSES THE CONFLICT** — the
Conflict stays open, the field stays frozen, and a human still owns it. Build that. ### **Do not
invent a cancellation transition, a `CANCELLED` state, or a `ConflictCancelled` event**, and do not
add a `CF-8`.

### 3.9 The seams that are already built — feed them, do not duplicate them

**The checkpoint (`checkpoint.py`), which you are not editing.** Step 4, native-state validity,
already refuses on two shapes M7 supplies:

- `NativeClaim(claim_id, status, conflicting, provenance)` — `conflicting=True` is a refusal
  (`CLAIM_CONFLICTING`, *"a conflicting field blocks (ADR-002 C6)"*)
- `ProvenancedFact(field, provenance, evidence_condition, entity_ref, …)` — an
  `evidence_condition` that is not `CONSISTENT` is a refusal (`EVIDENCE_NOT_CONSISTENT`), and
  `EvidenceCondition.CONFLICTING` is already a member of that enum

### **Demonstrate the seam by projecting M7's own state into those existing types and showing the
existing step 4 refuses.** Do not create a second gate authority: **P3 remains the gate minter**
(`CLAUDE.md` rule 17) and **M3 remains the single effect authority** (rule 17 again). **Do not edit
`checkpoint.py`.** If you conclude the P3 kernel must change for M7 to be correct, **say so and stop
before changing it.**

**The field condition, and the smallest canonical implementation.** The acceptance table names M7's
state oracle *"row + field condition"*, and entity §15 requires the raise and the freeze in one
transaction — but `entity_ref` is a **canonical projection row** (`K-2`), the freight projection is
**P9+**, and **no universal field-condition table exists today**. The smallest implementation
consistent with the current architecture, and the one this file directs unless you find canon that
forbids it:

- ### **The Conflict row IS the durable field condition.** `(tenant_id, entity_ref, field)` is
  already the natural key (entity §9) and the partial unique index over the three open states
  already makes *"is this field `conflicting`?"* a single tenant-first query. One row insert is one
  commit, which is exactly what entity §15 asks for
- M7 exposes a **deterministic reader** over that index which projects
  `EvidenceCondition.CONFLICTING` and `NativeClaim(conflicting=True)` into the existing checkpoint
  types. **F7 is explicit that this family does not write projected VALUES — it BLOCKS them**
- ### **Do not build a projection store, an entity/field registry, an Expectation, an Exception, a
  Compensation or a Rule registry to hold it.** That is M8+ infrastructure and it is not yours
- if you conclude canon genuinely requires a separate durable field-condition table, **name the
  clause and say so** before you build it, and keep it inside M7's own migration

**The foreign keys entity §18 names, and what exists to point at.** §18 names five — `entity_ref`,
`parties[]`, `rule_id`, `decision_ref`, `owner_id`. **Follow M6's precedent exactly: build the
foreign keys whose targets exist; carry the others as constrained, NOT-NULL-where-the-CHECK-requires-it
columns with no foreign key into a table this unit does not own.**

| Column | Target | Exists? |
|---|---|---|
| `owner_id` | `tenant_humans` (M1) | ✅ **build the FK** |
| a party that is an identity binding claim | `identity_binding_claims` (M6) | ✅ **build the FK** |
| a party that is an observation | `observations` (M5) | ✅ **build the FK** |
| `conflict_id` on the party child row | self, `conflicts` | ✅ **build the FK** |
| the human behind a `decision_ref` | `tenant_humans` (M1) | ✅ **build the FK** — M6's precedent |
| `entity_ref` | a load / carrier / movement projection | ❌ freight domain, **P9+** |
| `rule_id` | `rules` | ❌ **M12**, not built |
| `decision_ref` → `audit_events` | `audit_events` | ⚠️ the table exists; **`K-1`'s polymorphic FK needs `rules` too.** Follow M6: carry the reference with a resolvable-kind discriminator and the FK-backed human, and record the missing half |

A `parties[]` reference is **polymorphic** — a claim, an observation, a readback, a standing rule —
so it needs a **kind discriminator** beside it, exactly as `K-1` discriminates `decision_ref` with
`decision_ref_kind`. **Build the FK for each kind whose table exists; do not build `rules`,
`evidence`, `expectations`, `exceptions` or `compensations` to satisfy one.** If you conclude the
canonical shape genuinely requires one of those tables to point at — which would be building another
unit — **name the clause and stop.**

**The M6 correction seam, and the M10 seam behind it.** F7 records `ConflictResolved`'s consumers as
*"Projection (unfreeze); M6 (may drive a correction)"*, and machine §25 says *"a resolution may cause
a downstream claim correction (M6 `IB-7`)"*. What exists today, exactly:

- the **contract** is `ConflictResolved{rule_id | decision_ref}`, and M7 **emits** it. That is M7's
  whole half
- **the consumer half is M6's, and M6 is landed.** M7 **does not** call into M6, does not write
  `identity_binding_claims`, and does not correct a claim
- **"Projection (unfreeze)" is M7's own state**, not a projected write: the Conflict leaving the
  open set is what unfreezes the field, because the row is the condition (§3.9 above)
- machine §27 says *"if resolution reveals a wrong completed effect ⇒ M10"*. ### **M10 IS NOT BUILT
  AND YOU ARE NOT BUILDING IT.** No `compensations` table, no `CM-*`, no `CompensationRequired`, and
  ### **no fabricated completed Compensation.** M6 already records the correction obligation for its
  own corrections; M7 records nothing on M6's behalf

### 3.10 The F14 tripwires — which is yours

- ### **`IllegalTransitionAttempted` is MANDATORY and is yours.** `GR-1` requires it on every illegal
  `(state, trigger)`, to **audit and security**, and M5 and M6 both already emit it. The five shapes
  machine §15 names by hand are the ones that matter most
- **`ProvenanceStrengtheningAttempted` is NOT yours.** F14 names *M5/M6* as its producers, and
  `CURRENT.md` scopes the emission half elsewhere by name: *"P5's `IR-R9` (`AC-EVT-011` and the
  `ProvenanceStrengtheningAttempted` F14 emission half) lands **there** [Implementation Phase 7], not
  earlier."* ### **The party-provenance strengthening REFUSAL is mandatory and present now
  (`ER-14`); the F14 emission is not yours**
- **`OwnerAssertedOverwriteAttempted` is M6's**, whose sole producer F14 names as M6. M7 does not
  emit it, because M7 never recomputes a binding
- **`CrossTenantAccessAttempted` is the inbox's**, not M7's. Fail closed; do not mint it
- If you conclude M7 must emit one of the three that are not its own, name the clause, say that it
  contradicts F14 or `CURRENT.md`, and **stop** — that is §3.8 behaviour, not a judgement call

### 3.11 V5 stays open, and you do not answer it

**V5** — *the registered conflict-resolution rules; does the TMS always beat the portal on delivery
status?* — is **`NEEDS VALIDATION`, customer/domain, and explicitly NOT A BLOCK** (entity §45,
machine §43, ADR-007 §15 Q2).

- ### **The fail-closed default is: no applicable registered rule ⇒ EVERY conflict goes to a human.**
  That is the whole of the answer M7 is allowed to have
- the **mechanism** of `CF-3` is complete and must be built and exercised: a registry lookup, a
  versioned `rule_id`, an auditable and re-runnable decision. ### **The rule SET is empty, and M7
  ships it empty.** A `CF-3` case is exercised against a rule the test or probe registers for
  itself — never against a freight resolution rule this unit invented
- ### **DO NOT DECIDE WHICH FREIGHT SYSTEM WINS.** No "TMS beats portal", no "the newest source", no
  "the higher-confidence source". ADR-007 §8: source-priority resolution is *"fine as a registered
  rule with an id — not as an ambient default. The difference is auditability."*
- **M12 Rule is not built** and you are not building it (§3.9's FK table)

---

## 4. What you must produce

Follow the existing P6 naming conventions — `work_item.py`/`phase6_work_items.py`,
`pipeline_instance.py`/`phase6_pipeline_instances.py`,
`external_effect.py`/`phase6_external_effects.py`, `approval.py`/`phase6_approvals.py`,
`observation.py`/`phase6_observations.py`,
`identity_binding_claim.py`/`phase6_identity_binding_claims.py`. These exact paths are what the
permanent verification scenario `p6_m7_conflict` looks for; a different name is a scenario failure,
not a style preference. If you believe a different name is genuinely better, **say so and stop**
rather than renaming unilaterally.

| Path | What it is |
|---|---|
| `src/freight_recon/conflict.py` | the machine (follows `identity_binding_claim.py`) |
| `src/freight_recon/migrations/phase6_conflicts.py` | the schema change (follows `phase6_identity_binding_claims.py`) |
| `eval/tests/test_phase6_conflict.py` | the acceptance and hostile battery |
| `scripts/probe_phase6_conflict.py` | the deterministic narrative probe |
| `scripts/mutate_phase6_conflict.py` | the mutation battery (follows `mutate_phase6_identity_binding_claim.py`) |

Wire the migration into `schema.py` and the P2 migration path the way
`phase6_identity_binding_claims.py` is wired, so a freshly created canonical database and a migrated
one build to the same shape and the readiness oracle DERIVES the contract from the DDL rather than
from a second list. `schema_readiness_problems` must still return `[]` on a freshly created
canonical database with foreign keys enabled and verified, and the tenant-first table partition in
`CURRENT.md` gains exactly two rows: `conflicts` and `conflict_parties`.

### The probe's interface

`scripts/probe_phase6_conflict.py` must support:

- **no arguments** — run every case; exit `0` only if every one behaved as specified
- `--list-cases` — print the case names, one per line, and exit `0`
- `--list-dimensions` — print every dimension flag and every fault name, and exit `0`
- `--case <case>` — run exactly one case and exit `0` / non-zero

`--case` is what makes M7 testable by Product Driver's dynamic scenario generator: a generated
scenario may not author shell, so a focused, safe, argument-only entry point is the *only* way it can
compose new situations out of M7's real behaviour. Take the interface seriously.

**The cases, by name.** One per canonical obligation. A family missing here is a family the
generator cannot reach and you were never asked to build.

```
raise-creates-raised-with-a-named-human-owner
raise-and-freeze-are-one-commit
ownerless-conflict-is-impossible
a-model-cannot-own-a-conflict
the-six-conflict-kinds-are-closed
system-vs-system-raises-a-conflict
claim-vs-claim-raises-a-conflict
claim-vs-observation-raises-a-conflict
inferrer-vs-owner-records-the-owner-asserted-party
readback-vs-approved-is-not-an-ordinary-failure
rule-vs-rule-fails-closed-and-never-auto-merges
injected-competing-claim-freezes-the-entity-not-control
acknowledgement-opens-the-conflict
raised-conflict-already-blocks-consequential-action
open-conflict-blocks-consequential-action
escalated-conflict-still-blocks-consequential-action
open-conflict-fails-checkpoint-native-state-validity
no-effect-grant-on-a-conflicted-material-field
open-conflict-blocks-the-approval
m7-mints-no-gate-decision
registered-rule-resolves-the-conflict
unregistered-rule-cannot-resolve
rule-resolution-requires-a-registered-rule-id
confidence-cannot-resolve-a-conflict
recency-cannot-resolve-a-conflict
source-priority-cannot-resolve-without-a-registered-rule
a-model-cannot-resolve-a-conflict
authenticated-human-resolves-the-conflict
human-resolution-requires-a-decision-ref
counterparty-cannot-resolve-a-conflict
wrong-tenant-human-resolution-fails-closed
forged-human-fails-closed
inactive-human-fails-closed
resolution-carries-exactly-one-basis
resolution-with-neither-rule-nor-decision-is-illegal
resolution-unfreezes-the-field
a-resolved-conflict-is-retained-never-deleted
new-evidence-after-resolution-raises-a-new-conflict
age-threshold-escalates-the-conflict
a-timer-never-resolves-a-conflict
a-conflict-never-expires
escalated-resolves-by-registered-rule
escalated-resolves-by-authenticated-human
escalated-resolution-is-by-target-state-never-by-position
second-detection-attaches-a-party-not-a-new-conflict
at-most-one-open-conflict-per-field
an-attached-party-carries-its-own-provenance
party-provenance-is-never-strengthened
concurrent-detectors-produce-one-conflict
a-party-retraction-never-silently-closes-the-conflict
replay-rebuilds-the-complete-party-set
replay-keeps-the-field-frozen
replay-cannot-resolve-or-duplicate-a-conflict
replay-creates-no-new-authority-and-no-effect
restart-preserves-the-open-conflict
tenant-isolation
cross-tenant-identical-entity-ref-and-field
cross-tenant-party-reference-fails-closed
occ-on-conflict-version
competing-resolutions-serialize-at-most-one-wins
redelivered-detection-is-a-no-op
inbox-idempotency
state-and-event-co-commit
database-invariants
malformed-conflict-fails-closed
persistence-failure-rolls-back-the-raise-and-the-freeze
the-m6-claim-machine-is-not-rewritten
the-m3-unknown-outcome-semantics-are-unchanged
the-cross-family-conflict-raised-producers-are-recorded
m8-m9-m10-and-m12-are-not-built
```

### The mutation axis

M7 ships dark — no reconciliation service, no queue, no live channel — and the driver's only
external concurrency primitive is HTTP. **Every ordering, concurrency, timing, duplication, crash
and replay variation for M7 has to be reachable through this probe's arguments or it is not
reachable at all.**

The probe must therefore accept, composable with `--case`:

```
--concurrency 1-8     how many detectors or resolvers race the one-open-conflict-per-field index
--delay-ms 0-5000     timing skew between them
--repeat 1-5          duplicate detection / redelivery pressure
--tenants 1-3         isolation pressure
--parties 2-8         how many disagreeing parties one field attracts
--age-ms 0-60000      how far the durable timer is advanced; it may ESCALATE and never resolve
--confidence 0.0-1.0  the negative control: it must change NOTHING, at 1.0 or at 0.0
--seed <int>          deterministic interleaving; the same seed reproduces the failure
--inject <fault>      the closed fault set below
```

The **closed fault vocabulary**, every member named by the canonical machine, the entity
specification, an ADR, the event registry or a named mandate:

```
system-vs-system            claim-vs-claim              claim-vs-observation
inferrer-vs-owner           readback-vs-approved        rule-vs-rule
ownerless-raise             model-owner                 acknowledge
age-threshold               timer-resolve               auto-resolve
model-resolve               confidence-resolve          recency-resolve
source-priority-resolve     unregistered-rule           missing-rule-id
missing-decision-ref        both-resolution-bases       neither-resolution-basis
forged-human                inactive-human              wrong-tenant
counterparty-resolve        second-detection            concurrent-detection
duplicate-detection         retract-party               strengthen-party-provenance
cross-tenant-party          occ-conflict                competing-resolution
malformed-conflict          persistence-failure         replay
restart-before-open         restart-after-escalate      reorder-stream
new-evidence-after-resolution
```

**The vocabulary is CLOSED and BOUNDED. This is not fuzzing.** An unknown fault, or a value outside
the stated range, must be **REFUSED** with a non-zero exit (`2`) and a readable `unknown fault`
message — never a stack trace. Three negative controls are asserted by the permanent scenario:

- `--inject not-a-real-fault` — proves the closure is real
- `--inject expire-conflict` — **refused**, because entity §26 and machine §12/§23 say a Conflict
  **never expires** and §28 gives it no deletion policy. A probe that accepted it would be producing
  passing evidence for a transition the corpus states does not exist
- `--inject cancel-conflict` — **refused**, because machine §14 enumerates only `CF-1`…`CF-7`, `GR-1`
  makes anything unenumerated ILLEGAL, and no `CANCELLED` state and no `ConflictCancelled` event is
  registered anywhere. **This is `M7-AQ-3` held open rather than answered**, and a probe that
  accepted the fault would have answered it

Note the contrast with `--inject auto-resolve` and `--inject timer-resolve`, which **are** in the
vocabulary: those name mechanisms the corpus defines **as ILLEGAL** (machine §15), so the machine must
be seen to REFUSE them under `GR-1` — raising, persisting nothing, and recording
`IllegalTransitionAttempted`. A fault that is refused as *unknown* and a fault that is refused as
*illegal* are two different proofs, and M7 owes both.

### The probe's output contract

The probe must print these literals, verbatim. They are the contract between this file and the
permanent scenario, and they are matched as substrings.

```
behaviours as specified, 0 wrong
A CONFLICT IS TWO OR MORE MUTUALLY EXCLUSIVE CLAIMS ON ONE FIELD, MADE VISIBLE AND BLOCKING
A CONFLICT IS NOT unknown: WE HAVE TOO MUCH INFORMATION, AND IT DISAGREES
RAISING THE CONFLICT AND FREEZING THE FIELD ARE ONE COMMIT
A CONFLICT HAS A NAMED HUMAN OWNER FROM CREATION
AN OWNERLESS CONFLICT IS STRUCTURALLY IMPOSSIBLE
A MODEL CANNOT OWN A CONFLICT
THE SIX CONFLICT KINDS ARE CLOSED, AND THERE IS NO SEVENTH
AN INFERRER_VS_OWNER CONFLICT RECORDS THAT ONE PARTY IS OWNER_ASSERTED
A READBACK CONTRADICTING THE APPROVED FACTS IS A CONFLICT, NOT AN ORDINARY FAILURE
TWO CONFLICTING STANDING RULES FAIL CLOSED; NEYMA NEVER PICKS A WINNER
AN INJECTED COMPETING CLAIM YIELDS A FROZEN ENTITY AND A HUMAN, NEVER CONTROL
WHILE A CONFLICT IS OPEN THE FIELD IS conflicting AND BLOCKS EVERY CONSEQUENTIAL ACTION
A RAISED CONFLICT ALREADY BLOCKS
AN ESCALATED CONFLICT STILL BLOCKS
CHECKPOINT STEP 4 REFUSES A MATERIAL FIELD WITH AN OPEN CONFLICT
NO EFFECT GRANT IS MINTED ON A CONFLICTED MATERIAL FIELD
AN OPEN CONFLICT BLOCKS THE APPROVAL
M7 MINTS NO GATE DECISION
A REGISTERED, VERSIONED, DETERMINISTIC RULE MAY RESOLVE; AN UNREGISTERED ONE MAY NOT
CONFIDENCE NEVER RESOLVES A CONFLICT
RECENCY NEVER RESOLVES A CONFLICT
SOURCE PRIORITY IS A REGISTERED RULE OR IT IS NOTHING
A MODEL NEVER RESOLVES A CONFLICT
AN AUTHENTICATED HUMAN RESOLVES WITH A decision_ref
A COUNTERPARTY NEVER RESOLVES A CONFLICT
A FORGED OR INACTIVE HUMAN FAILS CLOSED
A WRONG-TENANT RESOLUTION FAILS CLOSED
A RESOLUTION CARRIES EXACTLY ONE OF rule_id OR decision_ref
A RESOLUTION WITH NEITHER A RULE NOR A DECISION IS AN ILLEGAL TRANSITION
RESOLUTION UNFREEZES THE FIELD
A RESOLVED CONFLICT IS RETAINED, NEVER DELETED
NEW CONFLICTING EVIDENCE AFTER A RESOLUTION RAISES A NEW CONFLICT
A CONFLICT AGES AND ESCALATES
A TIMER NEVER RESOLVES A CONFLICT
A CONFLICT NEVER EXPIRES
AN ESCALATED CONFLICT RESOLVES BY TARGET STATE, NEVER BY POSITION
A SECOND DETECTION ATTACHES A PARTY, NEVER A SECOND CONFLICT
AT MOST ONE OPEN CONFLICT PER TENANT, ENTITY AND FIELD
EACH PARTY CARRIES ITS OWN provenance_class, CARRIED NEVER STRENGTHENED
CONCURRENT DETECTORS PRODUCE ONE CONFLICT AND LOSE NO PARTY
A PARTY RETRACTION NEVER SILENTLY CLOSES THE CONFLICT
A REBUILD RECONSTRUCTS THE COMPLETE PARTY SET
THE FIELD IS STILL FROZEN AFTER RECONSTRUCTION
replay: 0 resolutions, 0 duplicate conflicts, 0 lost parties, 0 new authority, 0 external effects
A RESTART LEAVES THE OPEN CONFLICT OPEN
THE SAME entity_ref AND field IN TWO TENANTS ARE TWO ISOLATED CONFLICTS
A CROSS-TENANT PARTY REFERENCE FAILS CLOSED
A LOST UPDATE ON A CONFLICT IS REFUSED
COMPETING RESOLUTIONS SERIALIZE: ONE WINS, THE REST ARE REFUSED
A REDELIVERED DETECTION IS A NO-OP
THE STATE ROW AND ITS EVENT COMMIT TOGETHER
A LEGACY DATABASE MIGRATES TO THE CANONICAL CONFLICT SHAPE
THE DATABASE ENFORCES THE CONFLICT INVARIANTS
THE M6 CLAIM MACHINE IS UNCHANGED
THE M3 UNKNOWN_OUTCOME SEMANTICS ARE UNCHANGED
THE M8, M9, M10 AND M12 MACHINES ARE NOT BUILT
mutants caught
```

And it must **never** print any of these. Each is a sentence printed only when the thing M7 exists to
prevent has just happened, and any one of them anywhere in the run is the whole unit failing:

```
### CONFLICT AUTO-RESOLVED ###                    ### PARTY LOST ###
### TIMER RESOLVED A CONFLICT ###                 ### PARTY PROVENANCE STRENGTHENED ###
### CONFLICT EXPIRED ###                          ### CROSS-TENANT PARTY ACCEPTED ###
### CONFLICT DELETED ###                          ### CROSS-TENANT RESOLUTION ACCEPTED ###
### CONFLICT SILENTLY CANCELLED ###               ### ESCALATION RESOLVED BY POSITION ###
### MODEL RESOLVED A CONFLICT ###                 ### NEYMA PICKED A WINNER ###
### CONFIDENCE RESOLVED A CONFLICT ###            ### RULE_VS_RULE AUTO-MERGED ###
### RECENCY RESOLVED A CONFLICT ###               ### READBACK CONTRADICTION LAUNDERED INTO A NORMAL FAILURE ###
### SOURCE PRIORITY RESOLVED WITHOUT A RULE ###   ### UNKNOWN_OUTCOME SILENTLY RESOLVED ###
### UNREGISTERED RULE RESOLVED A CONFLICT ###     ### REPLAY RESOLVED A CONFLICT ###
### RESOLVED WITHOUT A RULE OR A DECISION ###     ### REPLAY DUPLICATED A CONFLICT ###
### TWO RESOLUTION BASES ACCEPTED ###             ### REPLAY REBUILT A STALE PARTY SET ###
### COUNTERPARTY RESOLVED A CONFLICT ###          ### DOWNSTREAM EFFECT DURING REPLAY ###
### FORGED HUMAN ACCEPTED ###                     ### EVENT WITHOUT ITS STATE ###
### INACTIVE HUMAN ACCEPTED ###                   ### STATE WITHOUT ITS EVENT ###
### OWNERLESS CONFLICT CREATED ###                ### M6 CLAIM ROW REWRITTEN BY M7 ###
### MODEL BECAME THE CONFLICT OWNER ###           ### COMPENSATION FABRICATED ###
### CONFLICT WITHOUT ITS FROZEN FIELD ###
### FIELD FROZEN WITHOUT ITS CONFLICT ###
### CONSEQUENTIAL ACTION PROCEEDED ON AN OPEN CONFLICT ###
### EFFECT GRANT MINTED ON A CONFLICTED FIELD ###
### APPROVAL PROCEEDED ON AN OPEN CONFLICT ###
### TWO OPEN CONFLICTS FOR ONE FIELD ###
### A SECOND CONFLICT WAS RAISED INSTEAD OF A PARTY ###
```

Also never: `### MISS ###`, `### NOT REFUSED`, `### WRONGLY REFUSED`, `### WRONG REFUSAL`.

### The mutation battery

`scripts/mutate_phase6_conflict.py` proves that the load-bearing guards **can fail**. A guard never
seen to fail is a decoration, and a mutation that does not reintroduce the real defect proves nothing
— **verify each mutant actually applies and actually misbehaves before you believe any result.** At
minimum, mutate:

- **`AutoResolve` accepted** — drop the `GR-1` refusal for the transition ADR-007 §5.3 names by hand
- **a timer transition to a resolved state** — widen `CF-5` so `AgeThresholdCrossed` can close it
- **a confidence threshold used as a resolution guard** — reintroduce `if confidence > 0.98`, the
  defeat ADR-007 §8 names by hand
- **the newest party wins** — reintroduce recency as an ambient resolution default
- **an unregistered rule allowed to resolve** — drop the `CF-3` registry lookup
- **resolution accepted with neither a `rule_id` nor a `decision_ref`** — drop the entity §16 CHECK
- **an ownerless conflict allowed** — drop the `owner_id` NOT NULL, or its foreign key
- **the raise and the freeze split into two commits** — the entity §15 atomicity
- **the partial unique index dropped** — or its `WHERE` clause, so two open conflicts fit one field
- **a second detection raises a new conflict instead of attaching** — turn `CF-7` into `CF-1`
- **`ConflictPartyAttached` not emitted** — so a full-history rebuild reproduces a stale party set
- **an attached party's provenance strengthened** — the `ER-14` refusal
- **the tenant predicate dropped from the open-conflict lookup** — cross-tenant coalescing
- **`CF-6` resolving by ordinal position** — instead of by target state
- **an open conflict stops blocking the consequential action** — the `GR-10` projection

Use the safe in-memory save/restore harness the way `mutate_phase6_identity_binding_claim.py` does.
### **Never use `git checkout`, `git restore`, `git stash` or `git clean` to undo a mutation.**
Doing so once destroyed unrecoverable uncommitted work in this repository. Purge `__pycache__`:
restoring a `.py` is not restoring behaviour.

The mutation battery must **not** import the conflict machine — mutate text and shell out to pytest,
the way `mutate_phase6_identity_binding_claim.py` does.

### Ship dark

M7 ships dark, exactly as M1, M2, M3, M4, M5 and M6 do.

- **Nothing under `src/freight_recon/` may import `conflict`.** The only file under `scripts/` that
  may is `probe_phase6_conflict.py`
- **zero production importer, no live integration, no new API, button or channel, and no outbound
  effect path.** M7's product form is a **conflict inbox a human works through** — so that inbox is
  precisely the thing that must not arrive with it. Nothing may join the conflict machine to
  `ingestion`, `email_adapter`, `imap_mailbox`, `email_triage`, `inbox_brain`, `extraction`,
  `browser_use_adapter`, `cdp_readonly`, `tms_adapter`, `slack_adapter`, `channels`,
  `action_callback`, `ops_control`, `mailbox_intake` or any other inbound or outbound surface
- **M7 must not make Gmail, Slack, TMS, the browser, accounting or any other product surface start
  using Conflict yet**
- **no live effect is enabled**, and the production `GateRegistry` stays EMPTY. M7 authorizes
  nothing: a Conflict is an **INPUT** to the checkpoint and can never mint a gate decision
- the **checkpoint stays the only thing that mints a gate decision**, and **M3 stays the single
  effect authority**
- **no autonomous operation is enabled**, and no brake is engaged or narrowed by M7 — registry §11's
  auto-brake table belongs to the F14 detectors, not to this unit
- if canon genuinely requires a dark seam, **name the clause that requires it** before you build it,
  and keep the seam inert

### Tests

`pytest-canonical.ini` **no longer exists.** The 2026-08 engineering-process simplification folded it
into `[tool.pytest.ini_options]` in `pyproject.toml`, and CI runs
`python -m pytest -q -p no:cacheprovider`. Do not reintroduce a second pytest configuration and do
not pass `-c pytest-canonical.ini` anywhere.

Write the adversarial tests entity §44 names, by those names:
`test_open_conflict_blocks_all_consequential_actions`, `test_no_timer_or_model_resolves_a_conflict`,
`test_resolution_requires_rule_id_or_decision_ref`, `test_inferrer_vs_owner_raises_conflict`,
`test_two_conflicting_rules_fail_closed`, `test_readback_vs_approved_raises_conflict`,
`test_injected_competing_claim_freezes_entity_not_control`, `test_ownerless_conflict_impossible`.

And the per-transition tests machine §14 names, by those names:
`test_cf_raise_freezes_field_and_assigns_owner`, `test_cf_open`,
`test_cf_rule_resolution_requires_registered_rule_id`, `test_cf_human_resolution_requires_decision_ref`,
`test_cf_ages_to_escalated`, `test_cf_escalated_resolves`,
`test_cf_new_party_attaches_not_new_conflict`.

`test_readback_vs_approved_raises_conflict` is the one to be careful with: it must assert the
**M7-owned** half — that the Conflict blocks and is not laundered into a normal failure — without
editing `external_effect.py` and without moving an `UNKNOWN_OUTCOME`. See §3.7 and `M7-AQ-2`.

### Regressions you may not break

Re-run them on the tree you are finishing with, not the one you started from:

- **P3** — the checkpoint kernel, the claim CAS, step order, the brake, the fingerprint, the
  checkpoint matrix
- **P4** — the import gate, the adapter boundary, the governed write route
- **P5** — the event transport, replay isolation, **durable timers** (`CF-5` rides them), **and the
  canonical event contracts**: M7 uses five already-registered F7 names and mints none of its own, so
  `test_p5_event_contracts.py` and `test_p5_canonical_event_mint.py` are load-bearing here rather
  than incidental
- **M1, M2, M3, M4, M5, M6** — their acceptance batteries, and M4's, M5's and M6's own deterministic
  probes, which must still report `behaviours as specified, 0 wrong` with M7's tables in the schema

---

## 5. Do not

- begin **M8–M13** — in particular do not implement the **M8 Expectation** machine, the **M9
  Exception** machine, the **M10 Compensation** machine (§3.9) or the **M12 Rule** registry (§3.11)
- begin **P7 or later**, including P7's **provenance and evidence platform** (§2)
- build the **Evidence** entity, the Evidence Store, `evidence` spans, content-addressed retention or
  artifact storage
- resolve **V5**, or invent which freight system wins a disagreement (§3.11)
- build freight workflows, invoice automation, AP/AR workflows, carrier sourcing, dispatch, tracking
  or cargo claims
- build a **Slack**, **Gmail**, **email**, **IMAP**, **portal**, **browser** or **TMS** product
  surface or integration, or **any live conflict inbox, queue or resolution UI**
- adopt, refactor, wire in or replace `email_triage.py`, `ingestion.py`, `extraction.py`,
  `inbox_brain.py`, `mailbox_intake` routing fields, `action_callback.py` or any other legacy surface
- enable **live production effects**, **production integrations** or **production autonomy**
- **redesign P0, P1, P2, P3, P4 or P5.** They are COMPLETE. If M7 genuinely needs one of those
  surfaces changed, say so and stop **before** changing it
- weaken **P3, P4 or P5**, or edit `checkpoint.py`
- introduce a **second effect authority** or a **second checkpoint** — the checkpoint is the only
  thing that mints a gate decision and M3 is the only thing that claims a grant
- rebuild or polish **M1, M2, M3, M4, M5 or M6**. They are landed. Their recorded residuals are debt
  rows, and a debt row is a complete deliverable. In particular **do not edit
  `identity_binding_claim.py`** (§3.6) and **do not edit `external_effect.py`** (§3.7)
- rework the **P3/P4 one-connection-per-thread concurrency correction** at `d70a4e7`
- resolve unrelated **P6 debt**, and in particular do **not** fix **`P6-D40`** unless a real guard in
  it mechanically blocks this unit — it is a recorded gap in P6's own checkpoint-status guards, not
  an M7 defect
- start a **legacy cleanup campaign**, a **broad documentation cleanup**, or remediate nonblocking
  debt merely because it exists
- push, publish or deploy anything

**If a tiny pre-existing defect directly prevents M7 verification**, you may fix the **smallest
blocking prerequisite** — and you must **identify it explicitly**, say why M7 could not be verified
without it, and keep the fix minimal.

### Known non-blocking items — do not turn these into campaigns

`P6-D41` (the three recorded M6 authority questions), `P6-D42`/`P6-D43` (the CI runtime-limit debt
and the absence of an M6 probe job), `P6-D44`/`P6-D45` (stale gate/topology snapshots, reviewer-harness
labelling), `P6-D46` (the M6 re-run's zero accepted generated scenarios — fixed on the Product Driver
side), `P6-D35`–`P6-D40` (the M5 residuals, including the two uncaught P6 checkpoint-status guards),
and **V5** (the unregistered conflict-resolution rules). Each is recorded. **If one of them actually
makes M7 impossible to implement without choosing an unauthorized reading, STOP and report the
conflict rather than guessing.**

---

## 6. How this run works

Product Driver drives implementation, verification, correction and independent review. You do not
need to ask the founder to relay anything: scenario failures, evaluator findings and reviewer
findings come back to **you**, in this same session, as grounded corrections, and the loop retests.

M7 is **tier-1** work under `CLAUDE.md` §7. It is a state machine and an entity contract, which is
tier 2 by itself — but it also lands a **migration**, it is load-bearing for **tenant isolation**,
and it is the unit that decides **whether a consequential action may proceed when two sources
disagree**, which is the effect boundary's own admission question and weakening-a-safety-guard
territory by every measure the table uses. §7 says to take the higher tier once and say so, and this
file says so. A focused independent review by a session that did not write it is therefore required,
and Product Driver launches it **inside the run** rather than after it. Expect a reviewer to re-run
your probe, your suite and your mutation battery for itself.

Report a genuine blocker plainly rather than working around it. **§3.8 is the place where reporting a
blocker is the correct outcome rather than a failure.**

**Stop at verified M7. Do not automatically continue into M8.**

Accepting M7 does **not** complete P6, does **not** score a P6 acceptance criterion, does **not**
unblock P7, and enables nothing in production.

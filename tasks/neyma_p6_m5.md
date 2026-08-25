# Build P6 / M5 — Observation. Only that.

This is the goal Product Driver gives the builder session inside the Neyma repository. Pass it
with:

```bash
.venv/bin/python -m neyma_product_driver run \
  --task "$(cat tasks/neyma_p6_m5.md)" \
  --scenario p6_m5_observation
```

---

## 0. Read the authority first, in this order

Do not write code until you have read these. They are the authority; nothing below replaces
them, and where this file and a canonical specification disagree, **the specification wins and
you say so.**

1. `PRODUCT.md`
2. `CLAUDE.md`
3. `docs/implementation/CURRENT.md`
4. `docs/implementation/IMPLEMENTATION-REGISTRY.yaml`
5. `docs/specifications/entities/07-observation.md`
6. `docs/specifications/state-machines/05-observation.machine.md`
7. `docs/specifications/state-machines/registry.md` — §1 triggers, §2 the transition-row
   defaults, §3 `GR-1`…`GR-17`, §4 the canonical state registry, §5 the canonical event
   registry. **No machine may define a local synonym**, so every state and every event name you
   write must already be in §4 or §5.
8. `docs/specifications/events/05-observation-events.md` — the F5 family contract, its payloads,
   its consumers and its **ORDER-TOLERANT** classification
9. `docs/specifications/events/registry.md` — **§8 ORDERING** in particular, and §5's F14 list
10. `docs/architecture/target-system-specification.md` **§12.5**, and mandates **M-7**, **M-13**,
    **M-24**, **M-26**, **M-66**
11. `docs/specifications/entities/00-conventions.md` — `[C-1]`, `[C-2]`, `[C-3]`, `[C-5]`,
    `[C-7]`, `[C-8]`, `[C-9]`
12. the **P5** event transport, outbox/inbox, replay isolation and durable timers:
    `src/freight_recon/event_outbox.py`, `event_inbox.py`, `event_replay.py`, `event_timers.py`,
    `event_contracts.py` and `event_contracts_data.json`
13. **M1** Work Item (`src/freight_recon/work_item.py`, `migrations/phase6_work_items.py`) — the
    `owner_id` FOREIGN KEY into `tenant_humans` is the precedent for a **named human owner**, and
    the append-only TRIGGER on `tenant_humans` is the precedent for an invariant a database
    actually enforces
14. **M3** External Effect (`src/freight_recon/external_effect.py`) — the worked example of a
    P6 consumer riding P5's inbox, including `drain_handler_for` (`P6-D24`)
15. **M4** Approval (`src/freight_recon/approval.py`, `migrations/phase6_approvals.py`) — the
    most recent P6 unit, and the shape your migration, probe and mutation battery follow

### How to weigh them

| Source | What it is |
|---|---|
| `PRODUCT.md` | the stable product identity and destination |
| `CURRENT.md` | the present engineering position |
| `IMPLEMENTATION-REGISTRY.yaml` | machine phase/unit status and dependencies |
| canonical specs and ADRs | the exact required behaviour |
| legacy code and git history | implementation material only — **never roadmap authority** |

**If two authoritative sources genuinely conflict: REPORT THE CONFLICT. Do not invent a
resolution.** Say which two sources disagree, quote both, and stop on that point. Product
Driver will surface it. A resolution you invented is worse than a blocked run, because it looks
like agreement. **§3.9 below names three such conflicts that are already known. Read it before
you write the transition table.**

---

## 1. What Neyma is — the stable identity

Neyma is an **AI-native operating platform and system of action for SMB freight and logistics
companies.**

It is **not** an invoice bot, a document-extraction product, a Slack bot, a TMS chatbot, a
browser wrapper, an AP tool, an email triage system, or a disconnected collection of agents. If
a piece of legacy code in this repository suggests otherwise, that code is material, not
direction. `src/freight_recon/ingestion.py`, `email_adapter.py`, `imap_mailbox.py`,
`email_triage.py`, `extraction.py` and `inbox_brain.py` all exist and all predate this
specification. **None of them is M5, and M5 does not adopt, refactor, wire into or replace any
of them.**

- **P0–P8** build the shared governed operating engine.
- **P9–P13** build freight operational capability on top of it.
- **P14** expands bounded autonomy.

## 2. Where the program stands

- **P0–P5 COMPLETE.**
- **P6 IN PROGRESS.**
- **M1** (Work Item, `P6-CP-1`) landed. **M2** (Pipeline Instance, `P6-CP-2`) landed.
  **`P6-D11`** resolved and landed. **M3** (External Effect / Effect Grant, `P6-CP-3`) landed.
  **M4** (Approval, `P6-CP-4`) landed, with its focused independent review on disk.
- **M5 is the next build checkpoint. M5–M13 remain.**
- **No P6 acceptance criterion is scored.** P6 has not reached phase acceptance. **P7+ blocked.**
- **M1, M2, M3 and M4 all ship dark, and M5 ships dark too.**

`CURRENT.md`'s ⛔ table blocks **Implementation Phase 7** and names *"provenance, evidence,
observation, claims, identity binding"* inside it. That is **P7's provenance and evidence
platform**, not this unit. **M5 is the P6 Observation state machine** — one row, one machine,
seven states — and it is exactly what `CURRENT.md`'s "Still owed" cell means by *"M5 is the next
build checkpoint."* If you conclude those two sentences cannot both be true, that is §3.9
behaviour: say so and stop.

---

## 3. The unit: M5, and nothing else

### 3.1 The two sentences the whole unit is a consequence of

> ### **AN OBSERVATION IS AN IMMUTABLE RECORD THAT A SOURCE *SAID* SOMETHING, AT A TIME.**
> ### **IT IS NOT A CLAIM THAT THE THING IS TRUE. THE TMS CAN BE WRONG; THAT IT SAID SO IS STILL A FACT.**

and, from the machine file's own opening line, the distinction every defect in this unit comes
from confusing:

> ### **IMMUTABLE OBSERVATION *CONTENT* IS SEPARATE FROM OBSERVATION-PROCESSING *STATUS*.**
> ### **The `state` machine governs PROCESSING STATUS ONLY. `raw_value` and `content_digest` are written once and never mutate.**

A **stale** observation is still historical truth. A **superseded** observation is still
historical truth. Neither is a row to be tidied away, corrected, expired or swept: the world
spoke, and you cannot cancel that it spoke (entity §25, machine §22). `as_of` freshness is a
*checkpoint* concern (M-7), never an observation timer (machine §37).

### 3.2 The canonical state set

```
RECEIVED  PARSED  BOUND  UNBOUND  CONFIRMED  SUPERSEDED  UNPARSEABLE
```

Seven states, registry §4 / M5. **Do not add an eighth.** In particular there is no
`EXPIRED`, no `ARCHIVED`, no `CORRECTED` and no `DELETED` — entity §26 says *never expires*, §28
says *no deletion policy*, and §23 says an Observation is **never corrected**: a wrong reading is
superseded, and a wrong *binding* is corrected on the **Claim** (M6), not on the Observation.
**Do not add a state casually, and do not add one at all without saying so and stopping.**

### 3.3 Implement the canonical `OB-*` transition contract

The eight rows of machine §14, by those ids, with those guards. Not an alternative lifecycle
that "achieves the same thing".

| ID | From → To | What it is |
|---|---|---|
| **OB-1** | — → `RECEIVED` | idempotent upsert on the natural key; **new** content |
| **OB-1c** | *(re-ingest)* → `CONFIRMED` | the natural key exists **and the content is identical** |
| **OB-2** | `RECEIVED` → `PARSED` | extraction succeeded |
| **OB-2f** | `RECEIVED` → `UNPARSEABLE` | extraction failed → **Exception** |
| **OB-3** | `PARSED` → `BOUND` | **deterministic** binding confirmed via M6 |
| **OB-3u** | `PARSED` → `UNBOUND` | ambiguous / no candidate / single weak candidate → **Exception, human-owned** |
| **OB-4** | `UNBOUND` → `BOUND` | a later deterministic match **or** an `OWNER_ASSERTED` binding |
| **OB-5** | `{BOUND,PARSED}` → `SUPERSEDED` | a newer observation supersedes — **rule or human, never a re-run of the inferrer** |

`GR-1` still applies to everything else: an illegal `(state, trigger)` raises, persists nothing,
and emits `IllegalTransitionAttempted` (audit **and** security).

### 3.4 The natural key, and why it is the whole unit

```
(tenant_id, source_system, external_id, content_digest)
```

Entity §17 makes it a **`UNIQUE` constraint**, not a convention. Entity §33 states the
consequence in one sentence:

> ### **the same email delivered twice is ONE Observation, ONE `ObservationConfirmed`, ZERO duplicate work**

Everything else in this unit is a corollary of that key holding. A duplicate that creates a
second row is a duplicate Work Item, a duplicate approval card and eventually a duplicate
invoice — `docs/specifications/operational-workflow-review.md` row 32 is exactly that failure.
**The uniqueness has to be a database index that genuinely serializes concurrent ingestion
(machine §17), not an application-level "check then insert" that two writers both pass.**

### 3.5 What must hold — the authority and safety requirements

Preserve every one of these. They are the unit.

**The fact, and its immutability**

- `OB-1` creates `RECEIVED` from a **new** natural key
- **`raw_value` is immutable — no `UPDATE`** (entity §16, §22, `[C-8]`); a mutation attempt is
  `ILLEGAL` (machine §15) and the database refuses it, not just the Python
- **`content_digest` is immutable** — it is half the identity of the row; a row whose digest can
  be rewritten has no natural key at all
- **changed content is a NEW Observation** (entity §19, machine §18), never an edit of the old one
- an Observation is **never corrected** (entity §23); a wrong reading is superseded (`OB-5`)
- **no deletion, no expiry, no retention sweep** (entity §26, §28, §29; machine §12, §23, §37)

**The duplicate — `OB-1c`, and M-24**

- an identical re-ingest **does not create a second observation**
- an identical re-ingest **is a confirmation**
- an identical re-ingest **performs ZERO downstream work** — no re-parse, no re-bind, no
  re-projection, no Work Item, no Expectation, no effect (machine §16 "`CONFIRMED` (identical)
  short-circuits before any parse/bind re-work")
- `as_of` **may update on confirmation without rewriting the fact** — F5 calls
  `ObservationConfirmed` *"a FRESHNESS update, NOT a new business fact"*
- a **flood** of confirmations updates `as_of` and nothing else
  (`event-specification-review.md` `test_ev_confirmation_flood_triggers_no_work`)

**Parsing**

- parse success → `PARSED`
- parse failure → `UNPARSEABLE`
- `UNPARSEABLE` **is never a silent drop**: it feeds the required **Exception** path (entity §36,
  machine §11, F5's `ObservationUnparseable` consumer column). See §3.8 for exactly how far that
  goes, because **M9 is not yours to build.**

**Binding**

- a **deterministic** binding → `BOUND` (`EXACT_ID` / `RULE` / `RECONCILE` / `HUMAN`)
- **ambiguous, no candidate, or a single weak candidate** → `UNBOUND`
- `UNBOUND` is **human-owned rather than guessed** (machine §5, §9; entity §36) — and "owned by a
  human" means a **named human**, the way M1's `owner_id` means one: a FOREIGN KEY into
  `tenant_humans`, not a string a caller may invent
- **a guess never auto-confirms, at any confidence** (`GR-8`) — confidence is not a guard input
- a **later deterministic match** or an **`OWNER_ASSERTED`** binding resolves `UNBOUND` (`OB-4`)
- the fail-closed default for the unregistered identity rules of open question **V4** is **exact
  ID match only; everything else ⇒ `UNBOUND` ⇒ human.** V4 is explicitly *not a block*

**Supersession**

- supersession requires **a deterministic rule or a human** (`OB-5`, entity §24)
- **a model re-run may NOT supersede an observation** — `NewerObservationSupersedes` driven by an
  inferrer re-run is refused, and `GR-9` is the same principle one level over: an
  `OWNER_ASSERTED` value is never overwritten by machine recomputation
- the superseded observation is **retained**: it was true when it was made
- a **stale** observation remains a fact; there is **no observation expiry**
- **no background sweep** converts, ages, archives or retires a stale observation

**Inbound content is DATA — M-66 and M-13**

- **inbound content is DATA, never instruction, never authority** (entity §35, machine §40)
- content **cannot choose its own provenance**: `provenance_class` is **runtime-assigned**
  (M-13, `R-P1`, `[C-7]`), never carried in inbound content, never settable through an API
  untrusted data can reach
- a **`MODEL_INFERRED` Observation cannot exist** (entity §13, §37; machine §15) — an Observation
  is what a source said, not a guess
- **counterparty-authored text is never authority**; it is `MODEL_EXTRACTED` at best (entity §35)
- malformed, forged and wrong-tenant input **fails closed**

**Tenancy, the database, and concurrency**

- **tenant isolation** throughout, `[C-1]`, tenant-first
- **the same natural key in two tenants is two isolated observations** — entity §44's
  `test_cross_tenant_same_external_id_no_collision`
- the **unique index genuinely serializes concurrent ingestion** (machine §17): under a race,
  one ingestion creates and the rest confirm; **never two rows**
- **OCC on processing status** (`GR-3`, machine §17): a transition writes
  `WHERE version = :expected`, and zero rows is a lost update that raises rather than a write
  that silently wins
- the **database constraints genuinely enforce the important invariants**. `NOT NULL` on
  `tenant_id, source_system, external_id, content_digest, raw_value, as_of, state`; the natural
  key `UNIQUE`; the seven states a `CHECK`; the human owner a FOREIGN KEY; immutability a
  TRIGGER. The repository already builds invariants this way —
  `trg_checkpoint_witnesses_append_only_update`, `trg_durable_timers_immutable`,
  `trg_event_outbox_envelope_immutable`, `trg_pending_references_immutable` — so an
  "immutable" column with no trigger behind it is a comment, not an invariant

**The transport M5 rides**

- **state + canonical event co-commit** (`GR-2`, machine §35): no state change without its event,
  no event without its transition, one commit
- **inbox / event idempotency** (`GR-4`, `[C-3]`, M-24): a redelivered trigger
  `(consumer_id, tenant_id, event_id)` is a no-op
- **replay creates no duplicate observation and no downstream effect** (`GR-11`, `[C-5]`, entity
  §34, machine §21)
- **restart / crash recovery**: re-ingestion is idempotent by the natural key, and a
  partially-parsed observation re-parses deterministically (machine §36)
- the seven F5 event names are **already registered** in `event_contracts_data.json`
  (`ObservationReceived`, `ObservationConfirmed`, `ObservationParsed`, `ObservationUnparseable`,
  `ObservationBound`, `ObservationUnbound`, `ObservationSuperseded`). **Use them. Mint no eighth**

### 3.6 F5 is ORDER-TOLERANT, and that is a REQUIREMENT, not a relaxation

`docs/specifications/events/registry.md` §8 lists the strict-order aggregates by name — **F2
Pipeline, F3 Effect/Grant, F4 Approval, F11 Policy, F13 Brake** — and lists **F5 Observation as
ORDER-TOLERANT**, *"natural-key idempotent"*. F5's own family defaults say the same.

So, unlike M3 and M4:

- **do NOT declare `previous_aggregate_version` on F5 events**, and do not build a strict-order
  consumer for the observation aggregate. §1 makes the field applicable — and therefore required
  — **for a strict-order producer**. An order-tolerant producer that declares one is inventing a
  guarantee canon did not ask for, and §8's own rule is that an event declaring no predecessor
  *"falls back to contiguity"* — so declaring one wrongly is not a harmless extra
- **the natural key is what makes ingestion commutative.** That is the mechanism. Do not add a
  second one

**What you inherit anyway, and must build:**

- **M-26 / §8 parking.** *"An event referencing an aggregate that does not exist yet is PARKED
  (`pending_references`), retaining arrival order + attempt metadata; drained in order on
  creation; TTL ⇒ Exception (T18)."* F5's cross-cutting section says it in M5's own words: **a
  binding for a not-yet-received observation is parked.** M3 is the worked example of a P6
  consumer supplying `drain_handler_for` (`P6-D24`) so a parked cohort is released the moment the
  thing it references lands. Follow it; do not invent a second parking mechanism
- **out-of-order delivery is tolerated** (§8 bullet 2), and tolerating it is a behaviour the
  probe must demonstrate rather than a property you assert

### 3.7 The M5/M6 seam — build the seam, not the machine

`OB-3` says *"deterministic binding confirmed **via M6**"*, and machine §33 says M5 **consumes**
`BindingConfirmed` / `BindingAmbiguous` / `BindingAbsent` from the Identity Service. **M6 does not
exist and you are not building it.**

Implement **only the minimum inert seam** canon actually requires: M5 accepts a binding *decision*
— confirmed with a named deterministic match method, ambiguous, absent, or weak — as a typed
input, applies its own guard, and transitions. It does not compute bindings, does not rank
candidates, does not build `identity_binding_claims`, and does not implement `IB-*`.

`binding_claim_id` is entity §11 *optional* and §18 an FK *"when bound"*. If you find that the
canonical shape requires a real `identity_binding_claims` table to point at — which would be M6 —
**say so and stop** rather than building half of M6 to satisfy a foreign key.

### 3.8 The Exception path, without inventing M9

`OB-2f` and `OB-3u` both end *"→ Exception"*, and `OB-3u`'s is *"Exception, human-owned"*. **M9
is not built, and `ExceptionRaised` is M9's contract, not M5's.** The repository has already ruled
on this exact shape once, in `event_inbox.py`:

> *"It does not mint an `ExceptionRaised` on TTL expiry. `expire_overdue` marks the row EXPIRED and
> RETURNS the expired parks, each naming its accountable owner, for the caller to raise. M9 and the
> `ExceptionRaised` contract are P6/U5.3; emitting a canonical event name from here would be this
> unit writing another unit's contract."*

Do the same. What M5 owes is:

- its **own** canonical events — `ObservationUnparseable` and `ObservationUnbound`, which F5
  lists with **M9 as the consumer**
- a **durable, human-owned record** that the exception is owed: the row is in `UNPARSEABLE` /
  `UNBOUND`, it names an accountable human, and nothing silently drops or closes it
- **no `ExceptionRaised`, no `exceptions` table, no `EC-*` transitions.** If you conclude canon
  requires M5 to mint one, name the clause and stop

### 3.9 ⚠️ THE KNOWN AUTHORITY QUESTIONS — read this before writing the transition table

The corpus contains three disagreements about M5 that this file does **not** resolve, and neither
may you. Each is a real conflict between authoritative documents. **Report them; implement only
what every reading agrees on.** Product Driver surfaces a reported conflict; it treats a silently
invented resolution as a defect.

**`M5-AQ-1` — is `BOUND` terminal?**

- **Terminal**, per registry §4 (`BOUND (T)`), entity §12 (*"Terminal: `BOUND, SUPERSEDED,
  UNPARSEABLE`"*), machine §8 (same), and target spec §12.5 (**`BOUND` (T)**).
- **Not terminal**, per the transition table those same three documents carry: `OB-5` is
  `{BOUND,PARSED} → SUPERSEDED`, and target spec §12.5's own row is `BOUND|PARSED →
  SUPERSEDED`. Under `GR-1` an enumerated transition is legal, so `BOUND` has an outgoing edge.

**Every reading agrees on:** supersession requires a deterministic rule or a human, never an
inferrer re-run; the superseded row is retained; `raw_value` and `content_digest` never mutate.
Build that. Do not "fix" the classification in either direction.

**`M5-AQ-2` — is `UNPARSEABLE` terminal or non-terminal human-owned?**

- **Terminal**, per entity §12, machine §8 and target spec §12.5 (**`UNPARSEABLE` (T)**).
- **Non-terminal human-owned**, per registry §4 (`UNPARSEABLE (NH)`) and machine §9 (*"Non-terminal
  human-owned: `UNBOUND, UNPARSEABLE`"*). Machine §8 and machine §9 contradict **each other**,
  in the same file.

**Every reading agrees on:** `UNPARSEABLE` is never a silent drop, it feeds the Exception path,
and it is human-owned. Build that.

**`M5-AQ-3` — what does a duplicate do to a row that has already advanced?**

`OB-1c`'s *From → To* column says *"(re-ingest) → `CONFIRMED`"*, and registry §4 lists `CONFIRMED`
as a real M5 state, so it is reachable. But the same row's *Writes* column says **"`as_of` updated
only"**, F5 calls the event *"a FRESHNESS update, NOT a new business fact"*, and machine §16 says
`CONFIRMED` *"short-circuits before any parse/bind re-work"*. Whether a duplicate arriving against
a row that is already `PARSED`, `BOUND` or `SUPERSEDED` **rewrites `state` to `CONFIRMED`** — thereby
discarding processing status the machine has already established — or **leaves `state` untouched and
updates `as_of` alone** is not settled by the corpus as written.

**Every reading agrees on:** one row, one `ObservationConfirmed`, zero downstream work, `raw_value`
and `content_digest` unchanged, and — this is §3.1 — **the immutable CONTENT is not touched either
way, because only processing status is in question at all.** Build that, state which reading you
implemented and why, and report the ambiguity. Do not amend a specification to close it.

### 3.10 The provenance seam — refuse the laundering, do not mint the F14 event

F5's cross-cutting section says: *"a payload attempting to set `provenance_class` is rejected
(runtime-assigned, `R-P1`) ⇒ `ProvenanceStrengtheningAttempted` (F14)"*, and
`events/14-audit-security-events.md` names **M5/M6** as its producer.

`CURRENT.md` scopes the emission half elsewhere, by name: *"P5's `IR-R9` (`AC-EVT-011` and the
`ProvenanceStrengtheningAttempted` F14 emission half) lands **there** [Implementation Phase 7],
not earlier."* P5's adjudication says the same and calls declining to build it **correct
scoping**, because *"the dangerous half is CLOSED"* — the laundering is refused; what is missing
is the audit *record* of the attempt.

So, for M5:

- **the refusal is MANDATORY.** Inbound content that carries, implies or asks for a
  `provenance_class` is refused, and provenance stays runtime-assigned. This is M-13, and it is
  not deferred anywhere
- **the F14 emission is NOT yours.** Do not mint `ProvenanceStrengtheningAttempted`
- if you conclude M5 must emit it, name the clause, say that it contradicts `CURRENT.md`, and
  **stop** — that is `§3.9` behaviour, not a judgement call

---

## 4. What you must produce

Follow the existing P6 naming conventions — `work_item.py`/`phase6_work_items.py`,
`pipeline_instance.py`/`phase6_pipeline_instances.py`,
`external_effect.py`/`phase6_external_effects.py`, `approval.py`/`phase6_approvals.py`. These
exact paths are what the permanent verification scenario `p6_m5_observation` looks for; a
different name is a scenario failure, not a style preference. If you believe a different name is
genuinely better, **say so and stop** rather than renaming unilaterally.

| Path | What it is |
|---|---|
| `src/freight_recon/observation.py` | the machine (follows `approval.py`) |
| `src/freight_recon/migrations/phase6_observations.py` | the schema change (follows `phase6_approvals.py`) |
| `eval/tests/test_phase6_observation.py` | the acceptance and hostile battery |
| `scripts/probe_phase6_observation.py` | the deterministic narrative probe |
| `scripts/mutate_phase6_observation.py` | the mutation battery (follows `mutate_phase6_approval.py`) |

Wire the migration into `schema.py` and the P2 migration path the way `phase6_approvals.py` is
wired, so a freshly created canonical database and a migrated one build to the same shape and the
readiness oracle DERIVES the contract from the DDL rather than from a second list.
`schema_readiness_problems` must still return `[]` on a freshly created canonical database with
foreign keys enabled and verified.

### The probe's interface

`scripts/probe_phase6_observation.py` must support:

- **no arguments** — run every case; exit `0` only if every one behaved as specified
- `--list-cases` — print the case names, one per line, and exit `0`
- `--list-dimensions` — print every dimension flag and every fault name, and exit `0`
- `--case <case>` — run exactly one case and exit `0` / non-zero

`--case` is what makes M5 testable by Product Driver's dynamic scenario generator: a generated
scenario may not author shell, so a focused, safe, argument-only entry point is the *only* way it
can compose new situations out of M5's real behaviour. Take the interface seriously.

### The mutation axis

M5 ships dark — no importer, no service, no live channel — and the driver's only external
concurrency primitive is HTTP. **Every ordering, concurrency, timing, duplication, crash and
redelivery variation for M5 has to be reachable through this probe's arguments or it is not
reachable at all.** The probe must therefore accept, composable with `--case`:

| flag | range | what it varies |
|---|---|---|
| `--concurrency <n>` | 1–8 | how many ingesters race the natural-key index |
| `--delay-ms <n>` | 0–5000 | timing skew between those ingesters |
| `--repeat <n>` | 1–5 | duplicate / redelivery pressure |
| `--tenants <n>` | 1–3 | isolation pressure |
| `--sources <n>` | 1–4 | how many distinct `source_system`s share an `external_id` |
| `--seed <int>` | any | deterministic interleaving — **the same seed reproduces the same run** |
| `--inject <fault>` | the closed set below | what goes wrong, and when |

**The closed fault vocabulary.** Every member is a transition, a guard or a clause of
`05-observation.machine.md`, `entities/07-observation.md`, `events/registry.md` or a named
mandate; none is invented here:

```
none                        (default — nothing injected)
duplicate-ingest            OB-1c   the identical content arrives again
near-duplicate-ingest       OB-1 / entity §19  one byte differs: a new digest, a new observation
mutate-raw-value            entity §16/§22, machine §15  something tries to UPDATE the fact
mutate-content-digest       entity §10/§19  something tries to re-key the fact
parse-failure               OB-2f
binding-ambiguous           OB-3u   several candidates
binding-absent              OB-3u   no candidate
binding-weak                OB-3u   a single weak candidate
model-guess-binding         GR-8    a MODEL_INFERRED binding offered as deterministic
owner-asserted-binding      OB-4    a human resolves an UNBOUND
inferrer-rerun-supersede    OB-5 / GR-9  a re-run of the inferrer offered as a supersession
content-sets-provenance     M-13 / R-P1  the payload carries a provenance_class
content-carries-instruction M-66    the payload asks to be obeyed
counterparty-authority      entity §35   counterparty text claiming to be authority
wrong-tenant                [C-1]   input aimed at another brokerage
forged-natural-key          entity §17   a key naming no real source row
malformed-payload           entity §36   unreadable input
concurrent-ingest           machine §17  the unique index is the serialization point
occ-conflict                GR-3    a lost update on processing status
redeliver                   GR-4 / M-24
replay                      GR-11 / [C-5]
restart-before-parse        machine §36
restart-after-bind          machine §36
unreceived-reference        M-26 / events §8  a binding for an observation that has not arrived
reorder-stream              events §8   order-tolerant delivery, permuted
stale-as-of                 entity §26  an old `as_of`; still a fact
```

**Closed means closed.** An unknown fault name, or a value outside the stated range, must
**exit 2** and print a readable message containing `unknown fault` — **not** a traceback, and
never a silent fallback to `none`. The verification scenario runs `--inject not-a-real-fault` as a
negative control **and runs `--inject expire-observation` as a second one**, because
observation expiry is precisely the mechanism entity §26 and machine §12/§23 say does not exist;
a probe that accepted it would be producing evidence for a transition nobody authorized. This is
the line between a bounded mutation axis and fuzzing: a probe that accepts anything is a probe
whose passing runs mean nothing.

**Determinism is what makes a discovery durable.** `--seed` must fully determine the
interleaving, so a failure the generator finds at
`--case unique-index-serializes-concurrent-ingest --concurrency 6 --delay-ms 40 --inject concurrent-ingest --seed 7`
can be re-run, handed back as a grounded correction, and later promoted into permanent regression
coverage. A failure nobody can reproduce teaches nothing.

An injected fault that is meaningless for a given case (`binding-weak` against
`raw-value-is-immutable`, say) should exit 2 with a clear message as well. Refusing an incoherent
combination is better than running a degenerate one and reporting a pass.

**The case names, exactly:**

```
natural-key-creates-received            inbound-content-is-data-never-instruction
raw-value-is-immutable                  content-cannot-set-its-own-provenance
content-digest-is-immutable             model-inferred-cannot-be-an-observation
content-mutation-refused                counterparty-text-is-never-authority
changed-content-is-a-new-observation    malformed-input-fails-closed
duplicate-is-one-row-one-confirmation-zero-work   forged-or-wrong-tenant-input-fails-closed
confirmation-updates-as-of-only         tenant-isolation
confirmation-flood-triggers-no-work     cross-tenant-identical-natural-key
parse-success-parsed                    unique-index-serializes-concurrent-ingest
parse-failure-unparseable               occ-on-processing-status
unparseable-feeds-the-exception-path    database-invariants
deterministic-binding-bound             state-and-event-co-commit
ambiguous-binding-unbound               inbox-idempotency
no-candidate-binding-unbound            replay-creates-no-duplicate-and-no-effect
single-weak-candidate-unbound           order-tolerant-not-strict
unbound-is-human-owned                  park-and-drain-unreceived-reference
unbound-resolved-by-later-deterministic-match     restart-reingest-is-idempotent
unbound-resolved-by-owner-asserted      m6-binding-seam-is-inert
a-guess-never-auto-binds
supersession-requires-rule-or-human
inferrer-rerun-cannot-supersede
superseded-observation-is-retained
stale-observation-is-still-a-fact
no-expiry-no-timer-no-sweep
```

Four of those are the ones a reader skims past, so they are named again with the clause they come
from: `order-tolerant-not-strict` is `events/registry.md` §8's F5 classification and §3.6 above;
`park-and-drain-unreceived-reference` is M-26 and F5's *"a binding for a not-yet-received
observation is parked"*; `m6-binding-seam-is-inert` is §3.7; `no-expiry-no-timer-no-sweep` is
entity §26 / §28 and machine §12 / §23 / §37.

### The probe's output contract

The verification scenario matches these as **literal substrings**. Print them exactly. They are
not decoration — each one is the sentence that makes a behaviour observable to something other
than the session that wrote it.

**Must appear on a correct run:**

```
behaviours as specified, 0 wrong
AN OBSERVATION IS WHAT A SOURCE SAID, NOT WHAT IS TRUE
THE FACT IS IMMUTABLE; ONLY THE PROCESSING STATUS MOVES
THE SAME EMAIL TWICE IS ONE OBSERVATION
ONE ROW, ONE CONFIRMATION, ZERO WORK
A CONFIRMATION UPDATES as_of AND NOTHING ELSE
raw_value NEVER MUTATES
content_digest NEVER MUTATES
CHANGED CONTENT IS A NEW OBSERVATION, NEVER AN EDIT
A PARSE FAILURE IS UNPARSEABLE, NEVER A SILENT DROP
AN AMBIGUOUS BINDING IS UNBOUND, NEVER A GUESS
UNBOUND IS OWNED BY A NAMED HUMAN
A LATER DETERMINISTIC MATCH OR AN OWNER ASSERTION RESOLVES UNBOUND
SUPERSESSION REQUIRES A DETERMINISTIC RULE OR A HUMAN
A MODEL RE-RUN NEVER SUPERSEDES AN OBSERVATION
THE SUPERSEDED OBSERVATION IS RETAINED, IT WAS TRUE WHEN MADE
A STALE OBSERVATION IS STILL A FACT
THERE IS NO OBSERVATION EXPIRY AND NO SWEEP THAT INVENTS ONE
INBOUND CONTENT IS DATA, NEVER INSTRUCTION, NEVER AUTHORITY
PROVENANCE IS RUNTIME-ASSIGNED, NEVER SET FROM CONTENT
A MODEL_INFERRED OBSERVATION IS NOT AN OBSERVATION
COUNTERPARTY TEXT IS MODEL_EXTRACTED AT BEST, NEVER AUTHORITY
THE SAME NATURAL KEY IN TWO TENANTS IS TWO OBSERVATIONS
THE UNIQUE INDEX IS THE SERIALIZATION POINT
ONE INGESTION WINS, THE OTHERS CONFIRM
A LOST UPDATE ON PROCESSING STATUS IS REFUSED
THE STATE ROW AND ITS EVENT COMMIT TOGETHER
REDELIVERY IS A NO-OP
F5 IS ORDER-TOLERANT: NO STRICT-ORDER PREDECESSOR IS DECLARED
A REFERENCE TO AN UNRECEIVED OBSERVATION IS PARKED, NOT DROPPED
A PARKED REFERENCE DRAINS WHEN THE OBSERVATION ARRIVES
A RESTART RE-INGESTS IDEMPOTENTLY
A LEGACY DATABASE MIGRATES TO THE CANONICAL OBSERVATION SHAPE
THE DATABASE ENFORCES THE OBSERVATION INVARIANTS
replay: 0 new observations, 0 duplicate rows, 0 downstream work, 0 external effects
```

`mutants caught` is required from `scripts/mutate_phase6_observation.py`.

**Must never appear anywhere.** Print one of these only when the thing M5 exists to prevent has
actually happened; the run fails on sight of any of them:

```
### DUPLICATE OBSERVATION ROW ###          ### CROSS-TENANT OBSERVATION ACCEPTED ###
### raw_value MUTATED ###                  ### OBSERVATION EXPIRED ###
### content_digest MUTATED ###             ### OBSERVATION DELETED ###
### DUPLICATE INGEST DID WORK ###          ### UNPARSEABLE SILENTLY DROPPED ###
### GUESSED BINDING ACCEPTED ###           ### UNBOUND WITHOUT A HUMAN OWNER ###
### MODEL_INFERRED OBSERVATION CREATED ### ### DOWNSTREAM EFFECT DURING REPLAY ###
### SUPERSEDED BY INFERENCE ###            ### EVENT WITHOUT ITS STATE ###
### PROVENANCE SET FROM CONTENT ###        ### STATE WITHOUT ITS EVENT ###
### INBOUND CONTENT OBEYED ###             ### PARKED REFERENCE DROPPED ###
### COUNTERPARTY AUTHORITY ACCEPTED ###
```

Also never: `### MISS ###`, `### NOT REFUSED`, `### WRONGLY REFUSED`, `### WRONG REFUSAL`.

### The mutation battery

`scripts/mutate_phase6_observation.py` proves that the load-bearing guards **can fail**. A guard
never seen to fail is a decoration, and a mutation that does not reintroduce the real defect
proves nothing — verify each mutant actually misbehaves. At minimum, mutate:

- the **natural-key uniqueness** (drop `content_digest` from the index, or drop the index)
- the **`raw_value` immutability** trigger / guard (allow the `UPDATE`)
- the **`content_digest` immutability** guard
- the **duplicate short-circuit** (let an identical re-ingest do downstream work)
- the **`MODEL_INFERRED` refusal** (allow a model-inferred observation to be created)
- the **ambiguity guard** (allow an ambiguous candidate set to auto-bind)
- the **supersession guard** (allow an inferrer re-run to supersede)
- the **provenance assignment** (let inbound content set `provenance_class`)
- the **tenant predicate** (drop it from one query, so a natural key crosses a tenant boundary)
- the **co-commit** (write the state without its event)
- the **OCC predicate** (drop `WHERE version = :expected`)

Use the safe in-memory save/restore harness the way `mutate_phase6_approval.py` does.
### **Never use `git checkout`, `git restore`, `git stash` or `git clean` to undo a mutation.**
Doing so once destroyed unrecoverable uncommitted work in this repository. Purge `__pycache__`:
restoring a `.py` is not restoring behaviour.

The mutation battery must **not** import the observation machine — mutate text and shell out to
pytest, the way `mutate_phase6_approval.py` does.

### Ship dark

M5 ships dark, exactly as M1, M2, M3 and M4 do.

- **Nothing under `src/freight_recon/` may import `observation`.** The only file under `scripts/`
  that may import it is `probe_phase6_observation.py`
- **no production importer is enabled.** M5 is the unit whose product form is *a live mailbox*,
  so the one thing that must not arrive with it is a running importer. Nothing may join the
  observation machine to `ingestion`, `email_adapter`, `imap_mailbox`, `email_corpus`,
  `email_triage`, `inbox_brain`, `inbox_discovery`, `extraction`, `extraction_bridge`,
  `browser_use_adapter`, `cdp_readonly`, `cdp_session`, `tms_adapter`, `slack_adapter`,
  `channels` or any other inbound or outbound surface
- **no live effect is enabled**, and the production `GateRegistry` stays EMPTY. M5 authorizes
  nothing: an Observation may *evidence* a claim and can never *make* one, activate a policy or
  authorize an effect (entity §35)
- the **checkpoint stays the only thing that mints a gate decision**, and **M3 stays the single
  effect authority**
- if canon genuinely requires a dark seam, **name the clause that requires it** before you build
  it, and keep the seam inert

### Tests

`pytest-canonical.ini` **no longer exists.** The 2026-08 engineering-process simplification
folded it into `[tool.pytest.ini_options]` in `pyproject.toml`, and CI runs
`python -m pytest -q -p no:cacheprovider`. Do not reintroduce a second pytest configuration and
do not pass `-c pytest-canonical.ini` anywhere.

Write the adversarial tests entity §44 names, by those names:
`test_duplicate_observation_is_one_row_one_confirmation_zero_work`,
`test_raw_value_is_immutable`, `test_ambiguous_binding_goes_to_unbound_exception`,
`test_supersession_requires_rule_or_human`, `test_inbound_content_cannot_set_provenance`,
`test_counterparty_value_is_model_extracted_at_best`,
`test_cross_tenant_same_external_id_no_collision`, `test_replay_reingests_idempotently`.

### Regressions you may not break

Re-run them on the tree you are finishing with, not the one you started from:

- **P3** — the checkpoint kernel, the claim CAS, step order, the brake, the fingerprint, the
  checkpoint matrix
- **P4** — the import gate, the adapter boundary, the governed write route
- **P5** — the event transport, replay isolation, durable timers, **and the canonical event
  contracts**: M5 uses seven already-registered F5 names, so `test_p5_event_contracts.py` and
  `test_p5_canonical_event_mint.py` are load-bearing here rather than incidental
- **M1, M2, M3, M4** — their acceptance batteries, and M3's and M4's own deterministic probes,
  which must still report `behaviours as specified, 0 wrong` with M5's tables in the schema

---

## 5. Do not

- begin **M6–M13** — in particular do not build **M6 Identity Binding Claim** (§3.7) or
  **M9 Exception** (§3.8)
- begin **P7 or later**, including P7's **provenance and evidence platform** (§2)
- build the **Evidence** entity, `evidence` spans, or artifact retention beyond the optional
  fields entity §11 already names
- build freight workflows, invoice automation, AP/AR workflows, carrier sourcing, dispatch,
  tracking or claims
- build a **Slack**, **Gmail**, **email**, **IMAP**, **portal**, **browser** or **TMS** product
  integration, or **any live importer**
- adopt, refactor, wire in or replace `ingestion.py`, `email_adapter.py`, `imap_mailbox.py`,
  `email_triage.py`, `extraction.py`, `inbox_brain.py` or any other legacy ingestion surface
- revive **Delivered Load Closure** as the product identity, or promote it to validated
- enable **live production effects** or **production autonomy**
- **redesign P0, P1, P2, P3, P4 or P5.** They are COMPLETE. If M5 genuinely needs one of those
  surfaces changed, say so and stop **before** changing it
- weaken **P3, P4 or P5**
- introduce a **second effect authority** or a **second checkpoint** — the checkpoint is the only
  thing that mints a gate decision and M3 is the only thing that claims a grant
- polish **M1, M2, M3 or M4**. They are landed. Their recorded residuals are debt rows, and a debt
  row is a complete deliverable
- start a **legacy cleanup campaign**, or remediate nonblocking debt merely because it exists
- push, publish or deploy anything

**If a tiny pre-existing defect directly prevents M5 verification**, you may fix the **smallest
blocking prerequisite** — and you must **identify it explicitly**, say why M5 could not be
verified without it, and keep the fix minimal.

---

## 6. How this run works

Product Driver drives implementation, verification, correction and independent review. You do not
need to ask the founder to relay anything: scenario failures, evaluator findings and reviewer
findings come back to **you**, in this same session, as grounded corrections, and the loop
retests.

M5 is **tier-1** work under `CLAUDE.md` §7. It is a state machine and an entity contract, which is
tier 2 by itself — but it also lands a **migration**, it is load-bearing for **tenant isolation**,
and it is the surface where **untrusted counterparty content enters the system**. §7 says to take
the higher tier once and say so, and this file says so. A focused independent review by a session
that did not write it is therefore required, and Product Driver launches it **inside the run**
rather than after it. Expect a reviewer to re-run your probe, your suite and your mutation battery
for itself.

Report a genuine blocker plainly rather than working around it. **§3.9 is the place where
reporting a blocker is the correct outcome rather than a failure.**

**Stop at verified M5. Do not automatically continue into M6.**

Accepting M5 does **not** complete P6, does **not** score a P6 acceptance criterion, does **not**
unblock P7, and enables nothing in production.

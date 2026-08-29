# Build P6 / M8 — Expectation. Only that.

This is the goal Product Driver gives the builder session inside the Neyma repository. Pass it
with:

```bash
.venv/bin/python -m neyma_product_driver run \
  --task "$(cat tasks/neyma_p6_m8.md)" \
  --scenario p6_m8_expectation
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
5. `docs/specifications/entities/11-expectation.md` — the entity, and **§9**, **§10**, **§11**,
   **§13**, **§16**, **§17**, **§18**, **§19**, **§22**, **§23**, **§24**, **§25**, **§26**,
   **§27**, **§33**, **§34**, **§35**, **§36**, **§37**, **§38**, **§39**, **§41**, **§42**,
   **§43**, **§44**, **§45** in particular
6. `docs/specifications/state-machines/08-expectation.machine.md` — the eight `EX-*` rows of §14,
   §15's illegal set, §16's precedence rule, §17's concurrency rule, §21's replay rule, §22's
   cancellation row, §23's expiry row, §24's *"reopening: n/a"*, §25's *"correction: n/a"*, §26's
   *"supersession: n/a"*, §30's brake sentence, §36's crash recovery and §37's *"durable timers,
   not sweeps"*
7. `docs/specifications/state-machines/registry.md` — §1 triggers (**`T`** is *"timer or
   expectation (`TimerFired`)"*), §2 the transition-row defaults, §3 `GR-1`…`GR-17` (**`GR-1`**,
   **`GR-2`**, **`GR-3`**, **`GR-4`**, **`GR-5`**, **`GR-6`**, **`GR-7`**, **`GR-8`**, **`GR-10`**,
   **`GR-11`**, **`GR-13`**, **`GR-17`** above all), §4 the canonical state registry — **M8's six
   states with their `(R)`/`(T)`/`(NH)` classes** — and §5 the canonical event registry. **No
   machine may define a local synonym**, so every state and every event name you write must already
   be registered
8. `docs/specifications/events/08-expectation-events.md` — the **F8** family contract, every
   payload, its consumers, and its cross-cutting section
9. `docs/specifications/events/registry.md` — §3's F8 line, §5's consequential list, §7's
   projection rules (**F8 projection is `none`**), §8 **ORDERING** and its `previous_aggregate_version`
   rule (`P6-D11`), §9 **COORDINATION EVENTS** (`ExpectationIndeterminate` is an F15 member), §10's
   `ER-14`/`ER-15`
10. `docs/specifications/events/14-audit-security-events.md` — the F14 tripwires, and which of them
    this unit is named the producer of (§3.10 below: exactly one)
11. `docs/architecture/target-system-specification.md` **§12.8** in full — the eight-row lifecycle
    table, ### **`M-32`** (the observation-coverage mandate), the discharge-evidence line, the
    duplicate-prevention line and the **Timezone** line (**`F-25`**); **§26.1**'s
    `AWAITING_OBSERVATION` and `VERIFICATION_DEFERRED` outcomes (**`V6`**); **§20.5** (*"Customer Y
    requires hourly updates"* compiles to an Expectation, ### **it gates nothing, it OWES
    something**); **§6.3**'s five evidence conditions; **§32**'s **`V6`** and **`V10`** rows; and
    **`I8`** in the invariant table (*"missing ≠ contradictory"*)
12. `docs/architecture/decisions/ADR-006-verification-and-unknown-outcomes.md` — the unknown-outcome
    discipline this unit is the time-driven half of: a silence is not a failure
13. `docs/architecture/decisions/ADR-002-state-classes-and-lineage.md` — **§2.3** (the six-member
    `provenance_class` registry, `R-P1`/`R-P2`/`R-P3`) and **`C5`**/**`C6`**: five distinct evidence
    conditions, and `unknown` is not `conflicting`
14. `docs/architecture/decisions/ADR-008-durable-workflows.md` and
    `docs/architecture/decisions/ADR-009-concurrency-and-reservations.md` — durable obligations and
    the optimistic-concurrency rule `[C-10]` a deadline amendment rides
15. `docs/specifications/entities/00-conventions.md` — `[C-1]`, `[C-2]`, `[C-3]`, `[C-5]`, `[C-6]`,
    `[C-7]`, `[C-8]`, `[C-9]`, `[C-10]`, and the addendum's **`K-1`** (`decision_ref` resolves,
    never free text), **`K-2`** (### **`subject_ref` is the artifact/observation being bound or
    awaited — NOT an `entity_ref`, NOT a `target_resource_id`**) and **`K-3`** (replay is sandboxed
    and zero-emission)
16. `docs/specifications/acceptance/foundational-machine-acceptance.md` — M8's row
    (**`AC-MACH-801..808`**, state oracle *"row + coverage_ref"*, gate **G1**) and the ten
    per-machine mandatory assertions
17. `docs/specifications/acceptance/platform-safety-acceptance.md` — **`AC-SAFE-021`** (a timeout
    alone NEVER produces `FAILED`), **`AC-SAFE-022`** (an unknown always carries an owner and a
    reason; **no timer moves it**), **`AC-SAFE-019`** (replay creates no witness, grant or effect)
    and **`AC-SAFE-028`** (every open unit of work has one accountable human owner)
18. the **P5** event transport, outbox/inbox, replay isolation and ### **durable timers**:
    `src/freight_recon/event_outbox.py`, `event_inbox.py`, `event_replay.py`, ###
    **`event_timers.py`** (read its header in full — *"it does not decide what a fired timer
    means"*), `migrations/phase5_durable_timers.py`, `event_contracts.py` and
    `event_contracts_data.json`
19. **M1** Work Item (`src/freight_recon/work_item.py`) — the `owner_id` FOREIGN KEY into
    `tenant_humans` is the precedent for a **named, ACTIVE human owner**, and it is the shape
    `owner_id` on `OVERDUE`/`INDETERMINATE` follows
20. **M3** External Effect (`src/freight_recon/external_effect.py`) — read what it does with
    `AWAITING_OBSERVATION` and `VERIFICATION_DEFERRED` **today**, and §3.7 below. **You are not
    editing this file**
21. **M4** Approval (`src/freight_recon/approval.py`) — the worked example of a **durable timer
    scheduled in the SAME commit as the record it guards** (`AP-3`, `TTL_TIMER_KIND`), and of a
    machine that refuses to let a timer decide anything it was not given. Your `EX-3`/`EX-3i` and
    `EX-7` follow that shape exactly
22. **M5** Observation (`src/freight_recon/observation.py`,
    `migrations/phase6_observations.py`) — `BOUND` is the state a discharging Observation must be
    in, and M5's `UNBOUND`/`UNPARSEABLE` **Exception seam** is the precedent for §3.8's `M8-AQ-1`.
    **You are not editing this file**
23. **M7** Conflict (`src/freight_recon/conflict.py`, `migrations/phase6_conflicts.py`) — its
    migration, probe and mutation battery are the shape yours follow, and §3.9 below says why an
    overdue Expectation is **not** a Conflict. **You are not editing this file**
24. `src/freight_recon/checkpoint.py` — the `EvidenceCondition` enum and step 4, native-state
    validity, which is where entity §38's *"an owed-but-undischarged Expectation may make a field
    `unknown`"* lands. **You are not changing this file** (§3.9)

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
agreement. **§3.8 below names five such conflicts that are already known. Read it before you write
the transition table or the migration.**

---

## 1. What Neyma is — the stable identity

Neyma is an **AI-native operating platform and system of action for SMB freight and logistics
companies.**

It is **not** an invoice bot, a document-extraction product, a Slack bot, a TMS chatbot, a browser
wrapper, an AP tool, an email triage system, a tracking dashboard, an SLA monitor, or a
disconnected collection of agents. If a piece of legacy code in this repository suggests otherwise,
that code is material, not direction.

- **P0–P8** build the shared governed operating engine.
- **P9–P13** build freight operational capability on top of it.
- **P14** expands bounded autonomy.

## 2. Where the program stands

- **P0–P5 COMPLETE.**
- **M1** (Work Item, `P6-CP-1`), **M2** (Pipeline Instance, `P6-CP-2`), **M3** (External Effect /
  Effect Grant, `P6-CP-3`), **M4** (Approval, `P6-CP-4`), **M5** (Observation, `P6-CP-5`), **M6**
  (Identity Binding Claim, `P6-CP-6`) and **M7** (Conflict, `P6-CP-7`) have all landed, each with
  its focused independent review on disk. **`P6-D11`** is resolved and landed. The **P3/P4
  one-connection-per-thread concurrency correction** at `d70a4e7` is landed and **must not be
  reworked**.
- **P6 IN PROGRESS. M8 is the next build checkpoint. M8–M13 remain**, and **45 of the 134
  transitions**.
- **No P6 acceptance criterion is scored.** P6 has not reached phase acceptance. **P7+ blocked.**
- **M1, M2, M3, M4, M5, M6 and M7 all ship dark, and M8 ships dark too.** No live production effect
  or integration is enabled by any of them.

`CURRENT.md`'s ⛔ table blocks **Implementation Phase 7** and names *"provenance, evidence,
observation, claims, identity binding"* inside it. That is **P7's provenance and evidence
platform**, not this unit. **M8 is the P6 Expectation state machine** — one aggregate, one machine,
six states, eight transitions — and it is exactly what `CURRENT.md`'s "Still owed" cell and its
closing sentence (*"The next build checkpoint is M8 — the Expectation"*) both mean. This is the
same sentence pair M5, M6 and M7 were handed and it resolved the same way all three times. If you
conclude those two sentences cannot both be true, that is §3.8 behaviour: say so and stop.

---

## 3. The unit: M8, and nothing else

### 3.1 The three sentences the whole unit is a consequence of

### **AN EXPECTATION IS A DURABLE COMMITMENT THAT SOMETHING SHOULD BE OBSERVED BY A DEADLINE.**
### **`OVERDUE` MEANS THE THING NEVER CAME AND WE CAN PROVE WE WERE WATCHING. `INDETERMINATE`**
### **MEANS THE DEADLINE PASSED AND WE WERE BLIND. THEY ARE DIFFERENT FACTS (`I8`).**
### **AN EXPECTATION OWES SOMETHING; IT DOES NOT AUTHORIZE ANYTHING.**

Almost everything that goes wrong in freight is a **silence**. The POD never arrives. The carrier
never checks in. The appointment window passes with no update. Detention accrues while nobody
notices. A system that only reacts to inbound events is structurally incapable of helping with any
of it — which is why the Expectation exists at all: it is the mechanism for **time-driven and
non-event work**.

And it carries exactly one hard honesty rule, which is the whole reason this unit is not a timer:

> ### **WE DO NOT ACCUSE A COUNTERPARTY OF A FAILURE THAT WAS OURS.**

*"The POD never came"* and *"we were not watching"* are different facts (target spec §12.8, `I8`).
An Expectation may become `OVERDUE` **only** where the declared observation channel was demonstrably
healthy across the required window, proved by a `coverage_ref`. If the channel was down, or the
coverage is unknown, or there is **no coverage record at all**, the honest state is
`INDETERMINATE` — and `M-32` says so in terms:

> **M-32.** An Expectation MAY transition to `OVERDUE` ONLY where the observation channel was
> sufficiently observable during the required window. If observability was interrupted, it MUST go
> to `INDETERMINATE`. ### **Fails: no coverage record ⇒ `INDETERMINATE`**
> *(it fails toward blindness — the safe direction)*. *(`F-14`; `I8`.)*

### **AN EXPECTATION IS NOT A BARE TIMER AND NOT AN SLA** (entity §4). It carries observability
coverage. It is **not an accusation until observability is proven**, and it is ### **NOT A GATE —
it OWES, it does not AUTHORIZE** (entity §4, §38, §40; target spec §20.5).

### 3.2 The canonical state set

**Six states** (registry §4 / M8; target spec §12.8). Do not add a seventh, and do not rename one.

| State | Class | Meaning |
|---|---|---|
| `RAISED` | `(R)` recoverable | a future observation is owed by a deadline over a declared channel |
| `DISCHARGED` | `(T)` terminal | the owed observation arrived — possibly late, and late is still fine |
| `OVERDUE` | `(NH)` non-terminal, human-owned | it did not come **and we can prove we were watching** |
| `INDETERMINATE` | `(NH)` non-terminal, human-owned | the deadline passed **and we were blind** |
| `CANCELLED` | `(T)` terminal | the reason disappeared (e.g. the load cancelled) |
| `EXPIRED` | `(T)` terminal | terminal age reached past `OVERDUE`/`INDETERMINATE` |

**Terminal:** `DISCHARGED`, `CANCELLED`, `EXPIRED`. **Human-owned non-terminal:** `OVERDUE`,
`INDETERMINATE` — each carries a named `owner_id`. **Initial:** `RAISED`. **Recoverable:** `RAISED`.
**Failure:** `OVERDUE`/`EXPIRED`. **Unknown-outcome:** ### **`INDETERMINATE` IS the
observability-unknown, and it is DISTINCT from `OVERDUE`** (machine §13).

### **DO NOT INVENT `TIMED_OUT`, `STALE`, `RESOLVED`, `MISSED`, `LATE`, `CLOSED` OR `PENDING`.**
`RESOLVED` is M9 Exception's vocabulary and is the likeliest import. `TIMED_OUT` and `STALE` are the
two a build session reaches for out of tidiness, and each of them is exactly the honesty collapse
this unit exists to prevent: they mean *"the deadline passed"* without saying whether anyone was
watching.

### 3.3 What M8 consumes, and what it emits

**Consumes** (machine §33): `ObservationBound` · `TimerFired` · `DeadlineChanged` ·
`ReasonDisappeared`.

**Emits — the seven REGISTERED F8 contracts, and no eighth** (`events/registry.md` §3 is by its own
header the sole canonical list of event names):

| Event | Transition | Required added payload |
|---|---|---|
| `ExpectationRaised` | `EX-1` | `deadline_utc`, `originating_timezone`, `expected_source`, `expectation_key` |
| `ExpectationDischarged` | `EX-2` / `EX-4` | `discharge_observation_id`; `late?` |
| `ExpectationOverdue` | `EX-3` | ### **`coverage_ref` (REQUIRED — it is what proves health)** |
| `ExpectationIndeterminate` | `EX-3i` | ### **`coverage_gap` (REQUIRED)** |
| `ExpectationReVersioned` | `EX-5` | `deadline_history[]` |
| `ExpectationCancelled` | `EX-6` | `reason` |
| `ExpectationExpired` | `EX-7` | — |

`event_contracts_data.json` already carries all seven with exactly those required fields. It is the
mechanical projection of `events/registry.md`, so **the contract guard already exists and you
inherit it** — you are not inventing an event, and a name outside this table is defective by the
registry's own definition. ### **DO NOT MINT `ExpectationTimedOut`, `ExpectationMissed`,
`ExpectationClosed`, `ExpectationReopened` OR `ExpectationSuperseded`.**

### 3.4 Implement the canonical `EX-*` transition contract

**Eight rows. `EX-1`, `EX-2`, `EX-3`, `EX-3i`, `EX-4`, `EX-5`, `EX-6`, `EX-7` — an EXACT SET MATCH
with machine §14.** Not seven, not nine. `AC-MACH-801..808`. Anything not enumerated is ILLEGAL
(`GR-1`): it raises, persists nothing, and emits `IllegalTransitionAttempted` to **audit and
security**.

| ID | From → To | Trigger | Guard | Writes |
|---|---|---|---|---|
| **EX-1** | — → `RAISED` | `S` | ### **a deadline + an observability channel DECLARED + a duplicate-prevention key** | `deadline_utc`, `originating_timezone`, `expected_source`, `expectation_key` |
| **EX-2** | `RAISED` → `DISCHARGED` | `X` | ### **a BOUND Observation matches the expectation** | `discharge_observation_id` |
| **EX-3** | `RAISED` → `OVERDUE` | `T` | ### **deadline passed AND the channel was demonstrably HEALTHY throughout the window, proved by `coverage_ref`** | `coverage_ref`, `owner_id` |
| **EX-3i** | `RAISED` → `INDETERMINATE` | `T` | ### **deadline passed AND the channel was DOWN or coverage is UNKNOWN** | `owner_id` |
| **EX-4** | `{OVERDUE, INDETERMINATE}` → `DISCHARGED` | `X` | ### **a late arrival is ALWAYS accepted** | `discharge_observation_id`, `late` |
| **EX-5** | `RAISED` → `RAISED` *(v++)* | `H`\|`S` | `DeadlineChanged` | `deadline_history[]` |
| **EX-6** | `{RAISED, OVERDUE}` → `CANCELLED` | `S`\|`X` | `ReasonDisappeared` | `reason` |
| **EX-7** | `{OVERDUE, INDETERMINATE}` → `EXPIRED` | `T` | terminal age reached | — |

Read the **from-sets** literally, because three of them are places a build session widens the table
without noticing:

- ### **`EX-6`'s from-set is `{RAISED, OVERDUE}`. `INDETERMINATE` IS NOT IN IT.** Cancelling an
  `INDETERMINATE` Expectation is an ILLEGAL transition under `GR-1`, not a convenience. Do not widen
  the row, and do not add an `EX-6i`
- ### **`EX-7`'s from-set is `{OVERDUE, INDETERMINATE}`. A `RAISED` Expectation NEVER EXPIRES.**
  Terminal age is an age *past* a human-owned state, not a second deadline
- ### **`EX-4`'s from-set is `{OVERDUE, INDETERMINATE}` and it is UNCONDITIONAL.** *The POD that
  arrives in month 4 is still a POD* (entity §26)

**§16 Precedence.** ### **DISCHARGE BEATS OVERDUE AND INDETERMINATE.** When an arrival and a
deadline race, the discharge wins. Prove that with real concurrency, not by calling the two in a
convenient order.

**§15 Illegal transitions**, stated by the machine by hand:

- ### **`OVERDUE` without a healthy-coverage `coverage_ref` → ILLEGAL** (it must be `INDETERMINATE`)
- ### **a deadline evaluated in the wrong timezone → ILLEGAL** (facility-local, `F-25`)
- ### **two `RAISED` for one `expectation_key` → prevented by the partial `UNIQUE` index**
- ### **silent expiry (no Exception) → ILLEGAL**

### 3.5 What must hold — the authority and safety requirements

1. ### **THE HONESTY SPLIT IS THE UNIT.** Healthy coverage + a missed deadline ⇒ `OVERDUE`. Down or
   unknown coverage + a missed deadline ⇒ `INDETERMINATE`. ### **No healthy `coverage_ref` ⇒
   `OVERDUE` IS STRUCTURALLY IMPOSSIBLE** — a database `CHECK` (entity §16, §37), not a Python
   branch. And ### **THE ABSENCE OF A COVERAGE RECORD IS NOT HEALTH** (entity §36, `M-32`): it is
   the single most likely way this unit gets built wrong, because "no errors were logged" reads
   like "everything was fine"
2. ### **THE CHANNEL IS DECLARED AT CREATION OR THERE IS NO EXPECTATION** (entity §21). `expected_source`
   is `NOT NULL`. An Expectation with no declared channel cannot be judged at its deadline at all,
   so it has no honest deadline behaviour to have
3. ### **DUPLICATE PREVENTION IS A DATABASE INDEX, NOT AN APPLICATION CHECK.** `expectation_key` is
   `(tenant, subject_ref, expected_type)` (entity §9), and ### **`UNIQUE (tenant, expectation_key)
   WHERE state = 'RAISED'`** is what stops two live expectations for one owed observation (entity
   §17, machine §15/§17/§19). A check-then-insert is exactly what two concurrent raisers both pass.
   **Read `M8-AQ-3` in §3.8 before you write the `WHERE` clause**
4. ### **DISCHARGE REQUIRES A BOUND OBSERVATION** (entity §13, machine §31). Not an unbound one, not
   one about another subject, not one belonging to another tenant. Each of those three fails closed
5. ### **A LATE ARRIVAL IS ALWAYS ACCEPTED** (entity §26, `EX-4`, F8). Late evidence is never
   rejected because the deadline passed, and the discharge is marked `late`
6. ### **A DEADLINE AMENDMENT RE-VERSIONS; IT IS NOT A SUPERSESSION** (entity §19, §24; `EX-5`).
   `deadline_history[]` is retained. ### **The subject and the expected type MAY NOT be mutated**
   (entity §22). A stale expected version fails (`[C-10]`, `GR-3`): zero rows, then raise — never a
   silent overwrite
7. ### **CANCELLATION HAPPENS THROUGH `EX-6` AND NOWHERE ELSE** (entity §25). A wrong Expectation is
   `CANCELLED`, ### **never corrected** (entity §23: *"Correction rules. N/A"*), never superseded
   (§24), never reopened (§27). A cancelled Expectation is retained (`[C-9]`)
8. ### **EXPIRY IS EXPLICIT AND NEVER SILENT** (entity §26, machine §12/§23, §15's illegal set).
   ### **NO SWEEP, NO REAPER, NO STALE-EXPECTATION SCAN.** Machine §37: *"durable timers, not
   sweeps."* A row that quietly stops being visible is the exact failure the Expectation exists to
   prevent, one level up
9. ### **THE DEADLINE IS A DURABLE TIMER** (machine §37, `M-36`, ADR-016 §2). It rides P5's existing
   `event_timers.py` and `durable_timers` table — schedule it in the SAME commit as the raise, the
   way `AP-3` does. ### **No in-memory sleep, no background scan, no second timer mechanism.** A
   timer fires at least once and the machine acts exactly once, because the guard is idempotent
   (`GR-4`)
10. ### **THE DEADLINE TRANSITION IS ONE COMMIT** (entity §15, machine §4/§35, `[C-2]`). The
    `TimerFired` arrival **+** the coverage read **+** the resulting state **+** its event go
    together. A persistence failure rolls back all of it; there is no half-decided deadline
11. ### **REPLAY RECONSTRUCTS THE HONESTY SPLIT FROM THE RECORDED COVERAGE** (entity §34, machine
    §21, F8 cross-cutting, `GR-11`, `[C-5]`, `K-3`). ### **NOT from the channel's state now.** A
    rebuild that asks the live channel whether it is healthy produces a different answer next
    Tuesday, and the whole point of the record is that it does not. Replay mints no authority,
    constructs no witness, claims no grant and causes no external effect
12. ### **FACILITY-LOCAL TIME, WITH A REAL DST BOUNDARY** (entity §42, machine §38, target spec
    §12.8's Timezone line, `F-25`). `deadline_utc` is stored **UTC**; `originating_timezone` is
    **retained**; and appointment/facility windows are evaluated in the ### **FACILITY'S** local
    timezone. *A 17:00 delivery appointment in Denver is not 17:00 UTC — and a DST boundary is a
    real freight event.* ### **Evaluating the window in UTC instead of facility-local is an ILLEGAL
    transition** (machine §15)
13. ### **A MODEL MAY PROPOSE AN EXPECTATION; THE DEADLINE AND THE COVERAGE ARE RUNTIME-SET**
    (entity §35, machine §40, `[C-6]`, `GR-7`). ### **Counterparty or model content NEVER asserts
    that the channel was healthy**, and ### **confidence NEVER turns `INDETERMINATE` into `OVERDUE`,
    at any value including 1.0** (`GR-8`: confidence is not a guard input)
14. ### **AN EXPECTATION IS NOT A SECOND GATE** (entity §4, §38, §40; target spec §20.5). It OWES;
    it does not AUTHORIZE. `checkpoint.py` remains the sole minter of a gate decision (`CLAUDE.md`
    rule 17), and M3 remains the single effect authority. The interaction is **indirect**: an
    owed-but-undischarged Expectation may make a field `unknown`, which step 4 treats as
    not-`consistent`
15. ### **UNDER A BRAKE, OBSERVATION CONTINUES — SO DISCHARGE AND `INDETERMINATE` DETECTION
    CONTINUE** (entity §41, machine §30). A brake refuses to mint and refuses to claim (`GR-16`); it
    does not stop Neyma noticing. And ### **a brake never fabricates `OVERDUE` state**
16. ### **TENANT IS THE FIRST PARTITION DIMENSION** (`[C-1]`). First column of the primary key and
    of every unique index; first predicate of every read and write. A cross-tenant Observation,
    coverage record, owner or Expectation access fails closed
17. ### **AN `OVERDUE` OR `INDETERMINATE` EXPECTATION HAS A NAMED HUMAN OWNER** (entity §11, machine
    §5, `AC-SAFE-028`). `owner_id` is FK-backed into `tenant_humans` and required by a `CHECK` on
    exactly those two states. An ownerless human-owned state is structurally impossible. A model is
    not a human and may not be the owner

### 3.6 The coverage record — what M8 reads, and what it may not become

`coverage_ref` is the load-bearing reference of this whole unit, and it points at something the
corpus names but has not yet built. Read `M8-AQ-2` in §3.8 first; this section is what every reading
agrees on and what you may therefore build.

- the record is **canonical**: target spec §12.8 `M-32` names the state ### **`observation_coverage`**,
  *"an observation-coverage record per `(channel, window)`, written by the channel's own health
  probe"*, and §33's table list names `observation_coverage` beside `expectations`
- **M8 READS it. M8 does not become an observation system.** ### **Do not build a second
  Observation.** M5 is landed, `observations` exists, and its natural key is what makes a fact a
  fact. Coverage is a statement about a **channel over a window**, not about a fact
- **health is a persisted, positive assertion or it is not health.** ### **Do not infer health from
  the absence of errors, from an empty error table, from the process being up, or from the last
  successful poll.** `absent ⇒ INDETERMINATE`, and that is a `CHECK`-and-a-read, not a comment
- ### **Do not build a production health probe, a poller, an adapter, an importer or a channel
  monitor.** The channel's own health probe is P9+ adapter work (`docs/specifications/adapters/`),
  and M8 ships dark. The coverage rows M8 is verified against are written by ### **the probe and the
  tests**, for the window under test
- keep it inside **M8's own migration**, tenant-first, with a **closed health vocabulary**, and give
  `coverage_ref` a real FOREIGN KEY into it — that is what entity §18 asks for and what makes
  entity §16's `CHECK` enforceable
- ### **If you conclude canon requires an entity-level design decision you cannot make** — a
  45-point entity file for `observation_coverage`, its own state machine, its own event family —
  **name the clause and say so** rather than authoring one

### 3.7 The M3 `AWAITING_OBSERVATION` seam — provide the seam, do not reach into M3

Entity §39: *"`AWAITING_OBSERVATION` verification (spec §26.1) is driven by an Expectation, not a
retry loop."* Entity `03-external-effect.md` §36 says the same from the other side: a receipt that
only confirms transmission ⇒ `ATTEMPTED` + an **Expectation**, or `VERIFICATION_DEFERRED` with a
bound.

- **M3 is LANDED and tier-1.** `CURRENT.md` forbids rebuilding or polishing it, and the effect
  boundary is the highest-consequence surface in the repository
- ### **READ `external_effect.py` AND SAY WHAT IT ACTUALLY DOES TODAY.** Do not assume. Whatever it
  does, ### **M8 does not change it**, does not call into it, does not write `effect_grants`, and
  does not move a verification outcome
- ### **`UNKNOWN_OUTCOME` SEMANTICS ARE UNTOUCHED.** `GR-6`: an `UNKNOWN_OUTCOME` never silently
  becomes success or failure, and ### **no timer moves it** (`AC-SAFE-022`, `AC-MACH-215x`). An M8
  deadline is not a verification, and ### **`GR-5`/`AC-SAFE-021` still hold: a timeout alone never
  proves failure.** A passed deadline proves that the deadline passed. What it means depends
  entirely on the coverage record, which is the whole unit
- ### **Do not edit `external_effect.py`.** M3's shipped shape is unchanged by M8 in every reading
- **M8's half of the seam is that an Expectation EXISTS and can be raised for an owed observation.**
  That is all. ### **If you conclude the canonical shape requires an actual change inside M3, name
  the clause, say that it is an M3 change with an M3 review, and stop before making it.** That is
  `M8-AQ-4` behaviour

### 3.8 ⚠️ THE KNOWN AUTHORITY QUESTIONS — read this before writing the transition table

The corpus contains five disagreements about M8 that this file does **not** resolve, and neither may
you. Each is a real conflict between authoritative documents, and each is mechanically demonstrable
rather than a reading of tone. **Report them; implement only what every reading agrees on.** Product
Driver surfaces a reported conflict; it treats a silently invented resolution as a defect.

**`M8-AQ-1` — what does *"→ Exception"* mean while M9 is not built?**

- **An Exception is required**, per entity §14 (*"An `OVERDUE`/`INDETERMINATE`/`EXPIRED` Expectation
  1 : 1 Exception"*), §26 (*"a terminal age ⇒ `EXPIRED` ⇒ Exception (never silence)"*), §37 (*"silent
  expiry (no Exception)"* is a structurally impossible state), machine §11/§12/§23, and F8, whose
  consumers column names **M9 (Exception)** for `ExpectationOverdue`, `ExpectationIndeterminate` and
  `ExpectationExpired`. The canonical adversarial test is even NAMED
  `test_expiry_raises_an_exception_never_silence`.
- **M8 cannot raise one**, because `ExceptionRaised` is **`EC-1`'s** contract and M9 is the next
  unbuilt machine; because registry §5 lists M9 as its sole producer and *"no event is emitted by
  two incompatible transitions"*; because registry §9 and `ER-1` say a coordination event ### **does
  NOT instruct a consumer to transition**; and because building an `exceptions` table to satisfy the
  prose would be building M9.

**Every reading agrees on:** M8 emits its **own** registered F8 event — `ExpectationOverdue`,
`ExpectationIndeterminate`, `ExpectationExpired` — into the outbox, atomically with the transition;
the row is **durable, retained and NAMES AN ACCOUNTABLE HUMAN**; and ### **nothing is silent.**
This is exactly M5's landed precedent: `OB-2f`/`OB-3u` end *"→ Exception"*, M5 mints no M9 event,
and leaves an `UNPARSEABLE`/`UNBOUND` row owned by a named human instead. Build that. ### **Do not
create an `exceptions` table, do not write an `EC-*` transition, and do not mint `ExceptionRaised`,
`ExceptionAgeing` or `ExceptionEscalated`.** Write
`test_expiry_raises_an_exception_never_silence` under that exact name, asserting the half every
reading agrees on.

### **NAME THE SEAM IN PROSE, NOT BY ITS REGISTERED IDENTIFIER.** M5's landed `observation.py`
records exactly this seam and does it without ever writing the M9 contract name — it says *"the M9
exception event"*. Follow that: the permanent verification scenario sweeps the shipped
`expectation.py` and `phase6_expectations.py` for foreign contract names and for `EC-*`, `CM-*`,
`PO-*`, `RU-*` and `CF-*` transition ids, and asserts the set is EMPTY. A docstring that names
`ExceptionRaised` by its registered identifier fails that sweep, and the sweep is right: this unit's
source should not contain another machine's contract name at all.

**`M8-AQ-2` — who owns `observation_coverage`, and what is its contract?**

- **It is canonical and M8 depends on it absolutely.** Target spec §12.8 `M-32` names the state
  `observation_coverage`; §33's table list names it beside `expectations`; entity §11 calls
  `coverage_ref` *"the observation-coverage record consulted"*; §18 makes it a **FOREIGN KEY**; and
  §16's `CHECK` — *"`OVERDUE` requires a `coverage_ref` proving the channel was HEALTHY over the
  window"* — is unenforceable without a table to point at.
- **No authority specifies it.** There is ### **no 45-point entity file** for it among the seventeen
  in `docs/specifications/entities/`; it appears in **no** machine in `state-machines/registry.md`
  §4; it has **no** event family; and `M-32` says it is *"written by the channel's own health
  probe"* — which is **P9+ adapter** work (`adapters/01-inbound-comms.md`, `adapters/05-tracking.md`),
  not a P6 machine. M5's entity §42 and machine §38 record coverage as an **observability** concern
  of the Observation family, and M5 is **landed** with no coverage table.

**Every reading agrees on:** M8 must consult a ### **PERSISTED** coverage record rather than a live
read or an inference; ### **the absence of a record means `INDETERMINATE`, never `OVERDUE`**; ###
**no landed unit is edited to produce one** — in particular `observation.py` is not touched; and no
production health probe, poller or importer ships with M8. §3.6 states the smallest shape consistent
with that. **Do not amend a specification to close this**, and **do not author a 45-point entity
contract for `observation_coverage`** — if you conclude one is required, name the clause and stop.

**`M8-AQ-3` — does the duplicate-prevention index cover `RAISED`, or every non-terminal state?**

- ### **`WHERE state = 'RAISED'`**, per entity §17 (*"`UNIQUE (tenant_id, expectation_key) WHERE
  state = 'RAISED'`"*), machine §15 (*"Two `RAISED` for one `expectation_key` → prevented by
  `UNIQUE(...) WHERE state='RAISED'`"*) and F8's cross-cutting Dedup line (*"`expectation_key`
  unique index (`WHERE state='RAISED'`)"*) — three files, saying it three times.
- ### **`while non-terminal`**, per target spec §12.8's own Duplicate-prevention line: *"`UNIQUE
  (tenant, expectation_key)` while non-terminal"*. The non-terminal states are `RAISED`, `OVERDUE`
  **and** `INDETERMINATE`, so the two readings differ by exactly the window in which an Expectation
  has already missed its deadline — and `00-conventions.md` says these specifications **derive** from
  the frozen architecture and *"invent nothing"*, which makes the target spec the senior document
  and the disagreement real rather than a typo.

**Every reading agrees on:** ### **at most one `RAISED` Expectation per `(tenant, expectation_key)`,
enforced by a PARTIAL UNIQUE INDEX in the database, tenant-first.** Build that, state which reading
you implemented and why, and record the other. **Do not resolve it by widening a specification**, and
do not replace the index with an application-level check.

**`M8-AQ-4` — is `subject_ref` an artifact reference or a business-entity reference?**

- **An artifact/observation reference**, per `K-2`, which tabulates it by name: *"`subject_ref` — the
  **artifact/observation** being bound or awaited — appears on Identity Binding Claim, **Expectation**
  — points to an `observation_id` or `evidence_id`"*, and states in terms that the three references
  ### **MUST NOT be conflated**.
- **A business-entity reference**, per entity §10, which glosses it *"`subject_ref` (the
  load/document/movement)"* — which is `K-2`'s definition of `entity_ref`, a **canonical projection
  row** whose freight tables are **P9+**. §18 makes `subject_ref` a FOREIGN KEY either way, and the
  two readings point at different tables — one that exists (`observations`) and one that does not.

**Every reading agrees on:** `subject_ref` is `NOT NULL`, it is a component of `expectation_key`
(entity §9), and ### **it is not the same reference as `entity_ref`**. Follow M6's and M7's landed
precedent: ### **build the foreign key whose target exists; carry the other as a constrained,
NOT NULL column with a kind discriminator and no FK into a table this unit does not own**, and record
the missing half. **Do not build the freight projection**, and do not conflate the two references to
make the FK tidy.

**`M8-AQ-5` — is F8 strictly ordered, and must `EX-2`/`EX-4`/`EX-7` declare `previous_aggregate_version`?**

- **Partly strict**, per `events/08-expectation-events.md`'s family defaults — *"ordering =
  order-tolerant **except discharge/expiry** (per-aggregate version)"* — and its cross-cutting line
  *"Ordering: discharge/expiry STRICT; a discharge before the raise is parked."*
- **Not strict**, per `events/registry.md` §8, whose strict list is *"F2 Pipeline, F3 Effect/Grant,
  F4 Approval, F11 Policy, F13 Brake"* and whose order-tolerant list is *"F5 Observation, F7
  Conflict, F9 Exception, F14 Security"* — ### **F8 appears in neither**, and §8 classifies strictness
  **per family**, not per event. `event_contracts_data.json`, the mechanical projection of §8,
  resolves all seven F8 contracts to `strict_order: false`.

**Every reading agrees on:** the universal ordering key `(tenant, aggregate_id, aggregate_version)`
holds within one Expectation aggregate whatever the family classification says (registry §8's last
bullet); ### **a discharge arriving before its raise is PARKED in `pending_references` and drained in
arrival order**, using P5's existing mechanism; and `P6-D11`'s rule is unchanged — ### **absence of a
version may never be read as "there is nothing before me."** Build that. **Do not invent a second
sequencing mechanism**, and do not edit `events/registry.md` §8 to settle it.

### 3.9 The seams that are already built — feed them, do not duplicate them

**The checkpoint (`checkpoint.py`), which you are not editing.** Entity §38 makes the interaction
### **INDIRECT**: an owed-but-undischarged Expectation may make a field `unknown`, and step 4 already
refuses a `ProvenancedFact` whose `evidence_condition` is not `CONSISTENT`. `EvidenceCondition` already
has an `UNKNOWN` member.

### **Demonstrate the seam by projecting M8's own state into those existing types and showing the
existing step 4 refuses.** ### **`unknown` IS NOT `conflicting`** (`I8`, ADR-002 `C5`/`C6`): an
undischarged Expectation is missing information, not contradictory information, and mapping it to
`CONFLICTING` would make M8 a Conflict detector. Do not create a second gate authority: **P3 remains
the gate minter** and **M3 remains the single effect authority** (`CLAUDE.md` rule 17). **Do not edit
`checkpoint.py`.** If you conclude the P3 kernel must change for M8 to be correct, **say so and stop
before changing it.**

**M7 Conflict, which is landed, and which an overdue Expectation is NOT.** ### **AN EXPECTATION
BECOMING `OVERDUE` OR `INDETERMINATE` IS NOT AUTOMATICALLY A CONFLICT.** A Conflict is two or more
**mutually exclusive claims on one field** — *we have too much information and it disagrees*. An
Expectation that did not arrive is the opposite: ### **we have too little.** No canonical file makes
`ExpectationOverdue` or `ExpectationIndeterminate` a producer of `ConflictRaised` —
`ConflictRaised`'s registered producers are `CF-1`, `IB-6` and `EF-4c`, and M8 is not among them.
**Do not raise a Conflict from M8, do not write `conflicts` or `conflict_parties`, and do not edit
`conflict.py`.** Using M7 as an Exception substitute because M9 is missing is `M8-AQ-1` answered by
accident.

**M5 Observation, which is landed, and which M8 reads.** `EX-2`/`EX-4` need an Observation in state
`BOUND` whose subject matches. **M8 reads M5's row; it never writes one, never binds one, never
supersedes one, and does not edit `observation.py`.** M5's `OB-3`/`OB-4` binding must still work with
no Expectation in existence — a binding that started requiring an Expectation would be M5 rewritten
from inside M8.

**The foreign keys entity §18 names, and what exists to point at.** §18 names four —
`subject_ref`, `discharge_observation_id`, `coverage_ref`, `owner_id`. **Follow M6's and M7's
precedent exactly: build the foreign keys whose targets exist; carry the others as constrained,
NOT-NULL-where-the-CHECK-requires-it columns with no foreign key into a table this unit does not
own.**

| Column | Target | Exists? |
|---|---|---|
| `owner_id` | `tenant_humans` (M1) | ✅ **build the FK** |
| `discharge_observation_id` | `observations` (M5) | ✅ **build the FK** |
| `coverage_ref` | `observation_coverage` | ⚠️ **M8's own table** — §3.6 and `M8-AQ-2`; build the FK to the table you create |
| `subject_ref` → an `observation_id`/`evidence_id` | `observations` (M5) exists; `evidence` does not | ⚠️ `M8-AQ-4` — **build the FK for the kind whose table exists, with a kind discriminator** |
| `subject_ref` → a load / document / movement projection | freight domain | ❌ **P9+**, no table |

**Do not build `evidence`, `exceptions`, `compensations`, `policies` or `rules` to satisfy one.** If
you conclude the canonical shape genuinely requires one of those tables to point at — which would be
building another unit — **name the clause and stop.**

**The M9 consumer half, and the M10 seam behind it.** F8 records M9 as the consumer of three M8
events. ### **THAT IS M9'S HALF, AND M9 IS NOT BUILT.** M8 emits its contract into the outbox and
stops. ### **M10 IS NOT BUILT AND YOU ARE NOT BUILDING IT** — no `compensations` table, no `CM-*`, no
`CompensationRequired`, and ### **no fabricated completed Compensation.**

### 3.10 The F14 tripwires — which is yours

- ### **`IllegalTransitionAttempted` is MANDATORY and is yours.** `GR-1` requires it on every illegal
  `(state, trigger)`, to **audit and security**, and M5, M6 and M7 all already emit it. The four
  shapes machine §15 names by hand are the ones that matter most, and `EX-6`-from-`INDETERMINATE`
  and `EX-7`-from-`RAISED` are two more the from-sets make illegal
- **`ProvenanceStrengtheningAttempted` is NOT yours.** F14 names *M5/M6* as its producers, and
  `CURRENT.md` scopes the emission half elsewhere by name: *"P5's `IR-R9` (`AC-EVT-011` and the
  `ProvenanceStrengtheningAttempted` F14 emission half) lands **there** [Implementation Phase 7], not
  earlier."*
- **`OwnerAssertedOverwriteAttempted` is M6's**, whose sole producer F14 names as M6. M8 never
  recomputes a binding
- **`CrossTenantAccessAttempted` is the inbox's**, not M8's. Fail closed; do not mint it
- If you conclude M8 must emit one of the three that are not its own, name the clause, say that it
  contradicts F14 or `CURRENT.md`, and **stop** — that is §3.8 behaviour, not a judgement call

### 3.11 `V10` and `V6` stay open, and you do not answer them

Entity §45 and machine §43 both name two open validation items, and both say
### **they are NOT blocks.**

**`V10` — per-lane exception ageing thresholds.** Target spec §32: the fail-closed default is
### **ages · escalates · NEVER EXPIRES SILENTLY.**

- ### **DO NOT CHOOSE A BUSINESS AGEING THRESHOLD.** *"48 hours for a POD"*, *"7 days for a
  remittance"*, *"one lane ages faster than another"* — every one of those is a customer's operating
  policy, and inventing one is inventing a product decision with a number on it
- the **mechanism** of `EX-7` is complete and must be built and exercised: a terminal age reached
  through a **durable timer**, an explicit transition, its registered event, and a retained row.
  ### **The threshold is a caller-supplied parameter with no default that means anything**, and a
  test or probe supplies its own
- ### **THE FAIL-CLOSED BEHAVIOUR IS THE PART YOU MUST BUILD: it AGES and ESCALATES rather than
  disappearing.** Never a silent close, never a delete, never a sweep

**`V6` — deferred-verification bounds per TMS.** Target spec §32: the fail-closed default is
`AWAITING_OBSERVATION` + an Expectation.

- ### **DO NOT CHOOSE A PER-TMS DEFERRAL BOUND.** That is a per-integration measurement, and P9+
- the part M8 owes is that an Expectation ### **can be raised for an owed observation at all**, and
  that ### **unknown coverage ⇒ `INDETERMINATE`** — the fail-closed default, which is the same
  sentence one level down
- **M3 is not edited to wire this** (§3.7, `M8-AQ-4` behaviour)

---

## 4. What you must produce

Follow the existing P6 naming conventions — `work_item.py`/`phase6_work_items.py`,
`pipeline_instance.py`/`phase6_pipeline_instances.py`,
`external_effect.py`/`phase6_external_effects.py`, `approval.py`/`phase6_approvals.py`,
`observation.py`/`phase6_observations.py`,
`identity_binding_claim.py`/`phase6_identity_binding_claims.py`,
`conflict.py`/`phase6_conflicts.py`. These exact paths are what the permanent verification scenario
`p6_m8_expectation` looks for; a different name is a scenario failure, not a style preference. If you
believe a different name is genuinely better, **say so and stop** rather than renaming unilaterally.

| Path | What it is |
|---|---|
| `src/freight_recon/expectation.py` | the machine (follows `conflict.py`) |
| `src/freight_recon/migrations/phase6_expectations.py` | the schema change (follows `phase6_conflicts.py`) |
| `eval/tests/test_phase6_expectation.py` | the acceptance and hostile battery |
| `scripts/probe_phase6_expectation.py` | the deterministic narrative probe |
| `scripts/mutate_phase6_expectation.py` | the mutation battery (follows `mutate_phase6_conflict.py`) |

### **NAME THE MACHINE'S OWN TYPES THE WAY `conflict.py` NAMES ITS OWN.** M7 ships `M7Machine`,
`CfState`, `CfKind`, `Conflict`, `Party`, `TransitionResult` — so the only identifiers in that file
beginning `Conflict` + a capital are the five REGISTERED F7 event names, and the scenario's
unregistered-name sweep reads exactly what it is for. Do the same here: `M8Machine`, `ExState`,
`Expectation`, `Coverage`, `TransitionResult`. ### **An identifier beginning `Expectation` followed
by a capital letter that is not one of the seven registered F8 event names fails the sweep** — and
that is the point, because `ExpectationTimedOut` is exactly what an invented eighth event would be
called.

Wire the migration into `schema.py` and the P2 migration path the way `phase6_conflicts.py` is
wired, so a freshly created canonical database and a migrated one build to the same shape and the
readiness oracle DERIVES the contract from the DDL rather than from a second list.
`schema_readiness_problems` must still return `[]` on a freshly created canonical database with
foreign keys enabled and verified, and the tenant-first table partition in `CURRENT.md` gains
exactly two rows: `expectations` and `observation_coverage`.

### The probe's interface

`scripts/probe_phase6_expectation.py` must support:

- **no arguments** — run every case; exit `0` only if every one behaved as specified
- `--list-cases` — print the case names, one per line, and exit `0`
- `--list-dimensions` — print every dimension flag and every fault name, and exit `0`
- `--case <case>` — run exactly one case and exit `0` / non-zero

`--case` is what makes M8 testable by Product Driver's dynamic scenario generator: a generated
scenario may not author shell, so a focused, safe, argument-only entry point is the *only* way it can
compose new situations out of M8's real behaviour. Take the interface seriously.

**The cases, by name.** One per canonical obligation. A family missing here is a family the
generator cannot reach and you were never asked to build.

```
raise-creates-raised-with-a-declared-channel
an-expectation-cannot-be-raised-without-expected-source
an-expectation-cannot-be-raised-without-a-deadline
raise-stores-deadline-in-utc-and-retains-the-originating-timezone
raise-and-its-durable-timer-are-one-commit
the-expectation-key-is-tenant-subject-and-expected-type
at-most-one-live-raised-expectation-per-key
concurrent-raises-produce-one-live-expectation
a-model-may-propose-an-expectation-but-not-set-the-deadline
a-model-cannot-assert-coverage-health
counterparty-content-cannot-declare-the-channel-healthy
bound-observation-discharges-the-expectation
discharge-records-the-discharge-observation-id
an-unbound-observation-cannot-discharge
a-wrong-subject-observation-cannot-discharge
a-wrong-tenant-observation-cannot-discharge
healthy-coverage-and-a-missed-deadline-is-overdue
overdue-requires-a-healthy-coverage-ref
overdue-without-healthy-coverage-is-structurally-impossible
a-blind-window-is-indeterminate-not-overdue
unknown-coverage-is-indeterminate-not-overdue
absent-coverage-is-not-health
partial-coverage-over-the-window-is-not-health
indeterminate-records-the-coverage-gap
confidence-cannot-turn-indeterminate-into-overdue
overdue-and-indeterminate-carry-a-named-human-owner
an-ownerless-human-owned-state-is-impossible
a-late-arrival-discharges-an-overdue-expectation
a-late-arrival-discharges-an-indeterminate-expectation
late-discharge-is-marked-late
late-evidence-is-never-rejected-because-the-deadline-passed
deadline-change-re-versions-the-expectation
deadline-history-is-retained
an-amendment-is-not-a-supersession
the-subject-and-expected-type-cannot-be-mutated
a-stale-version-cannot-overwrite-newer-state
reason-disappeared-cancels-a-raised-expectation
reason-disappeared-cancels-an-overdue-expectation
cancelling-an-indeterminate-expectation-is-illegal
a-cancelled-expectation-is-retained-never-deleted
terminal-age-expires-an-overdue-expectation
terminal-age-expires-an-indeterminate-expectation
a-raised-expectation-never-expires
expiry-is-never-silent
no-sweep-or-reaper-closes-an-expectation
there-is-no-timed-out-stale-or-resolved-state
discharge-beats-overdue-when-they-race
discharge-beats-indeterminate-when-they-race
the-deadline-is-a-durable-timer-not-a-sleep
restart-re-fires-the-deadline-timer
restart-preserves-the-raised-expectation
restart-after-overdue-reaches-the-canonical-state
a-redelivered-timer-is-a-no-op
timer-coverage-read-and-state-are-one-commit
persistence-failure-rolls-back-the-deadline-decision
state-and-event-co-commit
replay-reconstructs-overdue-from-the-recorded-coverage
replay-reconstructs-indeterminate-from-the-recorded-coverage
replay-does-not-read-the-current-channel-state
replay-creates-no-new-authority-and-no-effect
an-appointment-window-is-evaluated-in-facility-local-time
a-dst-boundary-does-not-move-the-deadline
a-window-evaluated-in-utc-instead-of-facility-local-is-wrong
m8-mints-no-gate-decision
an-expectation-owes-it-does-not-authorize
an-undischarged-expectation-makes-a-field-unknown-never-consistent
discharge-and-indeterminate-detection-continue-under-a-brake
a-brake-never-fabricates-overdue-state
tenant-isolation
cross-tenant-identical-expectation-key
cross-tenant-observation-cannot-discharge
cross-tenant-coverage-record-fails-closed
cross-tenant-owner-fails-closed
occ-on-expectation-version
inbox-idempotency
database-invariants
malformed-expectation-fails-closed
the-m5-observation-machine-is-not-rewritten
the-m3-awaiting-observation-seam-is-unchanged
the-m7-conflict-machine-is-not-rewritten
an-overdue-expectation-is-not-automatically-a-conflict
m9-m10-m11-and-m12-are-not-built
```

### The mutation axis

M8 ships dark — no tracking service, no SLA dashboard, no queue, no live channel — and the driver's
only external concurrency primitive is HTTP. **Every ordering, concurrency, timing, duplication,
crash and replay variation for M8 has to be reachable through this probe's arguments or it is not
reachable at all.**

The probe must therefore accept, composable with `--case`:

```
--concurrency 1-8      how many arrivals or timers race the one-live-expectation index
--delay-ms 0-5000      timing skew between them
--repeat 1-5           duplicate raise / redelivered timer pressure
--tenants 1-3          isolation pressure
--age-ms 0-86400000    how far the durable timer is advanced: the deadline, then the terminal age
--coverage <health>    the coverage record the window is judged against: healthy|down|unknown|absent|partial
--timezone <IANA>      the FACILITY's zone the appointment window is evaluated in
--confidence 0.0-1.0   the negative control: it must change NOTHING, at 1.0 or at 0.0
--seed <int>           deterministic interleaving; the same seed reproduces the failure
--inject <fault>       the closed fault set below
```

The **closed fault vocabulary**, every member named by the canonical machine, the entity
specification, the target specification, an ADR or the event registry:

```
raise                        missing-expected-source      missing-deadline
missing-key                  duplicate-raise              concurrent-raise
bound-discharge              unbound-discharge            wrong-subject-discharge
wrong-tenant-discharge       late-discharge               reject-late
deadline-passed              coverage-healthy             coverage-down
coverage-unknown             coverage-absent              coverage-partial
model-set-coverage           counterparty-coverage        confidence-overdue
overdue-without-coverage     ownerless-overdue            deadline-change
subject-mutation             type-mutation                stale-version
reason-disappeared           cancel-indeterminate         terminal-age
expire-raised                silent-expiry                sweep-close
discharge-vs-deadline-race   restart-before-deadline      restart-after-overdue
replay                       replay-from-live-channel     dst-boundary
utc-window                   occ-expectation              cross-tenant-observation
cross-tenant-coverage        cross-tenant-owner           malformed-expectation
persistence-failure          redelivered-timer            brake
gate-mint                    reorder-stream
```

**The vocabulary is CLOSED and BOUNDED. This is not fuzzing.** An unknown fault, or a value outside
the stated range, must be **REFUSED** with a non-zero exit (`2`) and a readable `unknown fault`
message — never a stack trace. Four negative controls are asserted by the permanent scenario:

- `--inject not-a-real-fault` — proves the closure is real
- `--inject reopen-expectation` — **refused**, because entity §27 says ### **"Reopening rules. N/A."**
  and machine §24 says the same. A probe that accepted it would be producing passing evidence for a
  transition the corpus states does not exist
- `--inject correct-expectation` — **refused**, because entity §23 says ### **"Correction rules. N/A
  — a wrong expectation is `CANCELLED`, not corrected"** and machine §25 says the same. Correction is
  precisely the tidy-looking thing a build session adds, and it would let a wrong deadline be edited
  out of history instead of re-versioned or cancelled
- `--inject supersede-expectation` — **refused**, because entity §24 and machine §26 both say that
  a re-versioned deadline is not a supersession — ### **a re-versioned deadline is NOT a
  supersession** — there is no `SUPERSEDED` state in registry §4's M8 row, and no
  `ExpectationSuperseded` event is registered anywhere

Note the contrast with `--inject overdue-without-coverage`, `--inject silent-expiry`, `--inject
utc-window`, `--inject expire-raised` and `--inject cancel-indeterminate`, which **are** in the
vocabulary: those name shapes the corpus defines **as ILLEGAL** (machine §15, and `EX-6`/`EX-7`'s
from-sets), so the machine must be seen to REFUSE them under `GR-1` — raising, persisting nothing,
and recording `IllegalTransitionAttempted`. A fault refused as *unknown* and a fault refused as
*illegal* are two different proofs, and M8 owes both.

### The probe's output contract

The probe must print these literals, verbatim. They are the contract between this file and the
permanent scenario, and they are matched as substrings.

```
behaviours as specified, 0 wrong
AN EXPECTATION IS A DURABLE COMMITMENT THAT SOMETHING SHOULD BE OBSERVED BY A DEADLINE
OVERDUE MEANS IT NEVER CAME; INDETERMINATE MEANS WE WERE NOT WATCHING
AN EXPECTATION OWES SOMETHING; IT DOES NOT AUTHORIZE ANYTHING
THE OBSERVABILITY CHANNEL IS DECLARED AT CREATION OR THERE IS NO EXPECTATION
A DEADLINE IS STORED IN UTC AND THE ORIGINATING TIMEZONE IS RETAINED
RAISING THE EXPECTATION AND SCHEDULING ITS DURABLE TIMER ARE ONE COMMIT
AT MOST ONE LIVE RAISED EXPECTATION PER TENANT AND EXPECTATION KEY
CONCURRENT RAISES PRODUCE ONE LIVE EXPECTATION
A BOUND OBSERVATION DISCHARGES THE EXPECTATION
AN UNBOUND OBSERVATION NEVER DISCHARGES
A WRONG-SUBJECT OBSERVATION NEVER DISCHARGES
A WRONG-TENANT OBSERVATION NEVER DISCHARGES
A MISSED DEADLINE OVER A DEMONSTRABLY HEALTHY WINDOW IS OVERDUE
A MISSED DEADLINE OVER A BLIND WINDOW IS INDETERMINATE, NOT OVERDUE
UNKNOWN COVERAGE IS INDETERMINATE, NOT OVERDUE
THE ABSENCE OF A COVERAGE RECORD IS NOT HEALTH
PARTIAL COVERAGE OVER THE WINDOW IS NOT HEALTH
OVERDUE WITHOUT A HEALTHY coverage_ref IS STRUCTURALLY IMPOSSIBLE
WE DO NOT ACCUSE A COUNTERPARTY OF A FAILURE THAT WAS OURS
AN OVERDUE OR INDETERMINATE EXPECTATION HAS A NAMED HUMAN OWNER
AN OWNERLESS HUMAN-OWNED STATE IS STRUCTURALLY IMPOSSIBLE
CONFIDENCE NEVER TURNS INDETERMINATE INTO OVERDUE
A MODEL MAY PROPOSE AN EXPECTATION; THE DEADLINE AND THE COVERAGE ARE RUNTIME-SET
COUNTERPARTY CONTENT NEVER ASSERTS THAT THE CHANNEL WAS HEALTHY
A LATE ARRIVAL IS ALWAYS ACCEPTED
A LATE ARRIVAL DISCHARGES AN OVERDUE EXPECTATION
A LATE ARRIVAL DISCHARGES AN INDETERMINATE EXPECTATION
LATE EVIDENCE IS NEVER REJECTED BECAUSE THE DEADLINE PASSED
A DEADLINE AMENDMENT RE-VERSIONS AND IS NOT A SUPERSESSION
THE DEADLINE HISTORY IS RETAINED
THE SUBJECT AND THE EXPECTED TYPE CANNOT BE MUTATED
A STALE VERSION NEVER OVERWRITES NEWER STATE
A DISAPPEARED REASON CANCELS THROUGH EX-6 AND NOTHING ELSE
CANCELLING AN INDETERMINATE EXPECTATION IS AN ILLEGAL TRANSITION
A CANCELLED EXPECTATION IS RETAINED, NEVER DELETED
TERMINAL AGE EXPIRES AN OVERDUE OR INDETERMINATE EXPECTATION
A RAISED EXPECTATION NEVER EXPIRES
EXPIRY IS NEVER SILENT
NO SWEEP, REAPER OR SCAN CLOSES AN EXPECTATION
THERE IS NO TIMED_OUT, STALE OR RESOLVED STATE
DISCHARGE BEATS OVERDUE AND INDETERMINATE WHEN THEY RACE
THE DEADLINE IS A DURABLE TIMER, NEVER AN IN-MEMORY SLEEP OR SWEEP
A RESTART RE-FIRES THE DEADLINE TIMER
A RESTART LEAVES THE RAISED EXPECTATION RAISED
A REDELIVERED TIMER IS A NO-OP
THE TIMER, THE COVERAGE READ AND THE RESULTING STATE ARE ONE COMMIT
A PERSISTENCE FAILURE LEAVES NO HALF-DECIDED DEADLINE
THE STATE ROW AND ITS EVENT COMMIT TOGETHER
REPLAY REBUILDS OVERDUE AND INDETERMINATE FROM THE RECORDED COVERAGE
REPLAY NEVER READS THE CURRENT CHANNEL STATE
replay: 0 new authority, 0 external effects, 0 coverage rewritten, 0 state flips
AN APPOINTMENT WINDOW IS EVALUATED IN THE FACILITY'S LOCAL TIMEZONE
A DST BOUNDARY DOES NOT MOVE THE DEADLINE
EVALUATING THE WINDOW IN UTC INSTEAD OF FACILITY-LOCAL IS WRONG
M8 MINTS NO GATE DECISION
AN UNDISCHARGED EXPECTATION MAKES A FIELD unknown, NEVER consistent
DISCHARGE AND INDETERMINATE DETECTION CONTINUE UNDER A BRAKE
A BRAKE NEVER FABRICATES OVERDUE STATE
THE SAME EXPECTATION KEY IN TWO TENANTS ARE TWO ISOLATED EXPECTATIONS
A CROSS-TENANT COVERAGE RECORD FAILS CLOSED
A LOST UPDATE ON AN EXPECTATION IS REFUSED
THE DATABASE ENFORCES THE EXPECTATION INVARIANTS
A LEGACY DATABASE MIGRATES TO THE CANONICAL EXPECTATION SHAPE
THE M5 OBSERVATION MACHINE IS UNCHANGED
THE M3 AWAITING_OBSERVATION SEAM IS UNCHANGED
THE M7 CONFLICT MACHINE IS UNCHANGED
AN OVERDUE EXPECTATION IS NOT AUTOMATICALLY A CONFLICT
THE M9, M10, M11 AND M12 MACHINES ARE NOT BUILT
mutants caught
```

And it must **never** print any of these. Each is a sentence printed only when the thing M8 exists to
prevent has just happened, and any one of them anywhere in the run is the whole unit failing:

```
### OVERDUE WITHOUT HEALTHY COVERAGE ###              ### RAISED EXPECTATION EXPIRED ###
### ABSENT COVERAGE TREATED AS HEALTHY ###            ### SWEEP CLOSED AN EXPECTATION ###
### UNKNOWN COVERAGE BECAME OVERDUE ###               ### REAPER DELETED AN EXPECTATION ###
### BLIND WINDOW BECAME OVERDUE ###                   ### UNREGISTERED STATE MINTED ###
### PARTIAL COVERAGE TREATED AS HEALTHY ###           ### OVERDUE BEAT A DISCHARGE ###
### CONFIDENCE TURNED INDETERMINATE INTO OVERDUE ###  ### IN-MEMORY SLEEP DECIDED THE DEADLINE ###
### MODEL SET COVERAGE TRUTH ###                      ### TIMER LOST ACROSS RESTART ###
### COUNTERPARTY ASSERTED CHANNEL HEALTH ###          ### HALF-DECIDED DEADLINE PERSISTED ###
### EXPECTATION RAISED WITHOUT A DECLARED CHANNEL ### ### EVENT WITHOUT ITS STATE ###
### EXPECTATION RAISED WITHOUT A DEADLINE ###         ### STATE WITHOUT ITS EVENT ###
### TWO LIVE RAISED EXPECTATIONS FOR ONE KEY ###      ### REPLAY READ THE LIVE CHANNEL ###
### UNBOUND OBSERVATION DISCHARGED ###                ### REPLAY FLIPPED OVERDUE AND INDETERMINATE ###
### WRONG-SUBJECT OBSERVATION DISCHARGED ###          ### REPLAY MINTED AUTHORITY ###
### WRONG-TENANT OBSERVATION DISCHARGED ###           ### DOWNSTREAM EFFECT DURING REPLAY ###
### LATE ARRIVAL REFUSED ###                          ### WINDOW EVALUATED IN UTC ###
### LATE DISCHARGE LOST ITS late MARKER ###           ### DST BOUNDARY MOVED THE DEADLINE ###
### DEADLINE AMENDED WITHOUT RE-VERSIONING ###        ### M8 MINTED A GATE DECISION ###
### DEADLINE HISTORY LOST ###                         ### EXPECTATION AUTHORIZED AN ACTION ###
### SUBJECT SILENTLY MUTATED ###                      ### BRAKE FABRICATED OVERDUE ###
### EXPECTED TYPE SILENTLY MUTATED ###                ### BRAKE STOPPED INDETERMINATE DETECTION ###
### STALE VERSION OVERWROTE NEWER STATE ###           ### CROSS-TENANT OBSERVATION ACCEPTED ###
### INDETERMINATE SILENTLY CANCELLED ###              ### CROSS-TENANT COVERAGE ACCEPTED ###
### EXPECTATION SILENTLY EXPIRED ###                  ### OWNERLESS HUMAN-OWNED STATE CREATED ###
### EXPECTATION DELETED ###                           ### M5 OBSERVATION ROW REWRITTEN BY M8 ###
### M3 AWAITING_OBSERVATION SEAM REWRITTEN ###        ### M7 CONFLICT ROW REWRITTEN BY M8 ###
### EXCEPTION FABRICATED ###                          ### M9 EVENT MINTED ###
```

Also never: `### MISS ###`, `### NOT REFUSED`, `### WRONGLY REFUSED`, `### WRONG REFUSAL`.

### The mutation battery

`scripts/mutate_phase6_expectation.py` proves that the load-bearing guards **can fail**. A guard
never seen to fail is a decoration, and a mutation that does not reintroduce the real defect proves
nothing — **verify each mutant actually applies and actually misbehaves before you believe any
result.** At minimum, mutate:

- **`INDETERMINATE` removed from the state vocabulary** — the honesty split collapses into one state
- **`OVERDUE` allowed without a healthy `coverage_ref`** — drop the entity §16 CHECK
- **absent coverage treated as healthy** — flip the `M-32` fail-closed default
- **partial coverage treated as healthy** — the *"throughout the window"* half of `EX-3`
- **the declared `expected_source` requirement dropped** — the entity §21 NOT NULL
- **the live `expectation_key` unique index dropped** — or its `WHERE` clause
- **the tenant weakened out of the uniqueness boundary** — cross-tenant coalescing of one key
- **duplicate `RAISED` expectations permitted** — the application-level check-then-insert
- **an unbound Observation allowed to discharge** — the entity §13 bound-Observation guard
- **late discharge forbidden** — reintroduce a *"the deadline passed"* rejection at `EX-4`
- **a timer allowed to resolve silently** — widen `EX-7` so expiry writes no event
- **the deadline history dropped** — `EX-5` re-versions and forgets
- **the OCC predicate dropped** — a stale version overwrites newer state
- **a DST case evaluated in UTC instead of facility-local** — the `F-25` guard
- **the owner requirement dropped from the human-owned states** — the `AC-SAFE-028` CHECK/FK
- **model-set coverage truth accepted** — the `[C-6]`/`GR-7` runtime-assignment guard
- **expiry accepted without its registered event** — the *"never silence"* half
- **replay recomputing from the current channel state** — instead of the recorded coverage
- **a sweep or reaper introduced** — a scan for *"things that look old"* beside the durable timer
- **an M9/M10/M11 table or event created** — the unauthorized neighbouring machine
- **M8 made a gate-decision minter** — the `CLAUDE.md` rule 17 boundary
- **the ship-dark posture weakened** — a production importer of `expectation`

Use the safe in-memory save/restore harness the way `mutate_phase6_conflict.py` does.
### **Never use `git checkout`, `git restore`, `git stash` or `git clean` to undo a mutation.**
Doing so once destroyed unrecoverable uncommitted work in this repository. Purge `__pycache__`:
restoring a `.py` is not restoring behaviour.

The mutation battery must **not** import the expectation machine — mutate text and shell out to
pytest, the way `mutate_phase6_conflict.py` does.

### Ship dark

M8 ships dark, exactly as M1, M2, M3, M4, M5, M6 and M7 do.

- **Nothing under `src/freight_recon/` may import `expectation`.** The only file under `scripts/`
  that may is `probe_phase6_expectation.py`
- **zero production importer, no live integration, no new API, button or channel, and no outbound
  effect path.** M8's product form is ### **a live tracking / SLA / "what is late" product** — so
  that product is precisely the thing that must not arrive with it. Nothing may join the expectation
  machine to `ingestion`, `email_adapter`, `imap_mailbox`, `email_triage`, `inbox_brain`,
  `extraction`, `browser_use_adapter`, `cdp_readonly`, `tms_adapter`, `slack_adapter`, `channels`,
  `action_callback`, `ops_control`, `follow_up`, `mailbox_intake` or any other inbound or outbound
  surface
- ### **NO CHANNEL HEALTH PROBE, POLLER OR COVERAGE IMPORTER SHIPS WITH M8** (§3.6). Coverage rows
  under test are written by the probe and the tests
- **M8 must not make Gmail, Slack, TMS, the browser, accounting or any other product surface start
  using Expectation yet**
- **no live effect is enabled**, and the production `GateRegistry` stays EMPTY. M8 authorizes
  nothing: an Expectation is an **INPUT** to the checkpoint and can never mint a gate decision
- the **checkpoint stays the only thing that mints a gate decision**, and **M3 stays the single
  effect authority**
- **no autonomous operation is enabled**, and no brake is engaged or narrowed by M8
- if canon genuinely requires a dark seam, **name the clause that requires it** before you build it,
  and keep the seam inert

### Tests

`pytest-canonical.ini` **no longer exists.** The 2026-08 engineering-process simplification folded it
into `[tool.pytest.ini_options]` in `pyproject.toml`, and CI runs
`python -m pytest -q -p no:cacheprovider`. Do not reintroduce a second pytest configuration and do
not pass `-c pytest-canonical.ini` anywhere.

Write the adversarial tests entity §44 names, by those names:
`test_deadline_passes_while_channel_down_yields_INDETERMINATE_not_OVERDUE`,
`test_late_arrival_discharges`, `test_duplicate_expectation_prevented`,
`test_appointment_window_evaluated_in_facility_local_time_across_dst`,
`test_expiry_raises_an_exception_never_silence`, `test_overdue_requires_healthy_coverage`.

And the per-transition tests machine §14 names, by those names:
`test_ex_raise_declares_channel`, `test_ex_discharge_on_bound_observation`,
`test_ex_overdue_requires_healthy_coverage`,
`test_ex_deadline_while_blind_is_indeterminate_not_overdue`, `test_ex_late_arrival_discharges`,
`test_ex_deadline_amend`, `test_ex_cancel_on_reason_gone`,
`test_ex_expiry_raises_exception_never_silence`.

And the F8 event-contract tests the family file names, by those names:
`test_ev_expectationraised_declares_channel`, `test_ev_expectationdischarged_late_ok`,
`test_ev_overdue_requires_healthy_coverage`, `test_ev_indeterminate_on_blind_window`,
`test_ev_expectation_reversioned`, `test_ev_expectation_cancelled`,
`test_ev_expectation_expired_raises`.

`test_expiry_raises_an_exception_never_silence` and `test_ex_expiry_raises_exception_never_silence`
are the two to be careful with: they are named for an Exception that **M9 owns and M9 is not built**.
Write them under those exact names and assert the **M8-owned** half — that `ExpectationExpired` is
emitted, that the row is retained and still names its human, and that nothing is silent — ###
**without minting an M9 event and without building an `exceptions` table.** See §3.8 `M8-AQ-1`.

### Regressions you may not break

Re-run them on the tree you are finishing with, not the one you started from:

- **P3** — the checkpoint kernel, the claim CAS, step order, the brake, the fingerprint, the
  checkpoint matrix
- **P4** — the import gate, the adapter boundary, the governed write route
- **P5** — the event transport, replay isolation, ### **durable timers** (`EX-3`/`EX-3i` and `EX-7`
  ride them, so this is the most load-bearing regression anchor of the unit), **and the canonical
  event contracts**: M8 uses seven already-registered F8 names and mints none of its own, so
  `test_p5_event_contracts.py` and `test_p5_canonical_event_mint.py` are load-bearing here rather
  than incidental
- **M1, M2, M3, M4, M5, M6, M7** — their acceptance batteries, and M5's, M6's and M7's own
  deterministic probes, which must still report `behaviours as specified, 0 wrong` with M8's tables
  in the schema. **M5's matters most**: `EX-2` reads its rows, and a binding that started requiring
  an Expectation would be M5 rewritten from inside M8

---

## 5. Do not

- begin **M9–M13** — in particular do not implement the **M9 Exception** machine (§3.8 `M8-AQ-1`),
  the **M10 Compensation** machine, the **M11 Policy** machine or the **M12 Rule** registry
- begin **P7 or later**, including P7's **provenance and evidence platform** (§2)
- build the **Evidence** entity, the Evidence Store, `evidence` spans, content-addressed retention or
  artifact storage
- resolve **V10** or **V6**, or choose a business ageing threshold or a per-TMS deferral bound
  (§3.11)
- build a **channel health probe, poller, coverage importer or observability monitor** (§3.6)
- build freight workflows, invoice automation, AP/AR workflows, carrier sourcing, dispatch, tracking
  or cargo claims
- build a **Slack**, **Gmail**, **email**, **IMAP**, **portal**, **browser** or **TMS** product
  surface or integration, or **any live tracking, SLA, "what is late" or exception-queue UI**
- adopt, refactor, wire in or replace `email_triage.py`, `ingestion.py`, `extraction.py`,
  `inbox_brain.py`, `follow_up.py`, `mailbox_intake` routing fields, `action_callback.py` or any
  other legacy surface
- enable **live production effects**, **production integrations** or **production autonomy**
- **redesign P0, P1, P2, P3, P4 or P5.** They are COMPLETE. If M8 genuinely needs one of those
  surfaces changed, say so and stop **before** changing it
- weaken **P3, P4 or P5**, or edit `checkpoint.py`
- introduce a **second effect authority**, a **second checkpoint** or a **second timer mechanism** —
  the checkpoint is the only thing that mints a gate decision, M3 is the only thing that claims a
  grant, and P5's `event_timers.py` is the only durable timer
- rebuild or polish **M1, M2, M3, M4, M5, M6 or M7**. They are landed. Their recorded residuals are
  debt rows, and a debt row is a complete deliverable. In particular **do not edit `observation.py`**
  (§3.9), **do not edit `external_effect.py`** (§3.7) and **do not edit `conflict.py`** (§3.9)
- rework the **P3/P4 one-connection-per-thread concurrency correction** at `d70a4e7`
- resolve unrelated **P6 debt**, and in particular do **not** fix **`P6-D40`** unless a real guard in
  it mechanically blocks this unit — it is a recorded gap in P6's own checkpoint-status guards, not
  an M8 defect
- start a **legacy cleanup campaign**, a **broad documentation cleanup**, or remediate nonblocking
  debt merely because it exists
- push, publish or deploy anything

**If a tiny pre-existing defect directly prevents M8 verification**, you may fix the **smallest
blocking prerequisite** — and you must **identify it explicitly**, say why M8 could not be verified
without it, and keep the fix minimal.

### Known non-blocking items — do not turn these into campaigns

`P6-D47` (the three recorded M7 authority questions), `P6-D48`/`P6-D49` (the CI runtime-limit debt
and the absence of an M7 probe job), `P6-D50`/`P6-D51` (stale gate/topology snapshots,
reviewer-harness labelling and the refused DDL introspection), `P6-D52` (the M7 run's five generated
scenarios rejected at assembly — addressed on the Product Driver side), `P6-D41`–`P6-D46` (the M6
residuals), `P6-D35`–`P6-D40` (the M5 residuals, including the two uncaught P6 checkpoint-status
guards), and **`V10`**/**`V6`** (the ageing thresholds and the deferred-verification bounds).
Each is recorded. **If one of them actually makes M8 impossible to implement without choosing an
unauthorized reading, STOP and report the conflict rather than guessing.**

---

## 6. How this run works

Product Driver drives implementation, verification, correction and independent review. You do not
need to ask the founder to relay anything: scenario failures, evaluator findings and reviewer
findings come back to **you**, in this same session, as grounded corrections, and the loop retests.

M8 is **tier-1** work under `CLAUDE.md` §7. It is a state machine and an entity contract, which is
tier 2 by itself — but it also lands a **migration**, it is load-bearing for **tenant isolation**,
and it is the unit that decides ### **whether Neyma accuses a counterparty of a failure or admits its
own blindness**, which is a claim made about someone outside the company and weakening-a-safety-guard
territory by every measure the table uses. §7 says to take the higher tier once and say so, and this
file says so. A focused independent review by a session that did not write it is therefore required,
and Product Driver launches it **inside the run** rather than after it. Expect a reviewer to re-run
your probe, your suite and your mutation battery for itself.

Report a genuine blocker plainly rather than working around it. **§3.8 is the place where reporting a
blocker is the correct outcome rather than a failure.**

**Stop at verified M8. Do not automatically continue into M9.**

Accepting M8 does **not** complete P6, does **not** score a P6 acceptance criterion, does **not**
unblock P7, and enables nothing in production.

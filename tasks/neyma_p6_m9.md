# Build P6 / M9 — Exception. Only that.

This is the goal Product Driver gives the builder session inside the Neyma repository. Pass it
with:

```bash
.venv/bin/python -m neyma_product_driver run \
  --task "$(cat tasks/neyma_p6_m9.md)" \
  --scenario p6_m9_exception
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
5. `docs/implementation/implementation-roadmap.md`
6. `docs/specifications/entities/12-exception.md` — the entity, and **§3**, **§4**, **§10**,
   **§11**, **§12**, **§13**, **§14**, **§15**, **§16**, **§17**, **§18**, **§21**, **§22**,
   **§23**, **§24**, **§25**, **§26**, **§27**, **§30**, **§31**, **§32**, **§33**, **§34**,
   **§35**, **§36**, **§37**, **§38**, **§39**, **§41**, **§42**, **§43**, **§44**, **§45** in
   particular
7. `docs/specifications/state-machines/09-exception.machine.md` — its **header paragraph**
   (### **the finer brief terms are `sub_status` FIELDS, not lifecycle states**), the seven `EC-*`
   rows of §14, §15's illegal set, §16's precedence rule, §17's concurrency rule, §19's idempotency
   rule, §21's replay rule, §22's cancellation row, §23's *"Expiry. never."*, §24's *"a recurrence
   is a new Exception"*, §25's *"Correction. n/a"*, §26's *"Supersession. n/a"*, §27's
   *"Compensation. n/a directly"*, §28's freeze sentence, §30's brake sentence, §34's writes, §35's
   *"raise+freeze one commit"*, §36's crash recovery, §37's ### **"never a resolution timer"** and
   §40's security rule
8. `docs/specifications/events/09-exception-events.md` — the **F9** family contract, every payload,
   its consumers, and its cross-cutting section
9. `docs/specifications/state-machines/registry.md` — §1 triggers (**`H`** is *"authenticated human
   action"*, **`T`** is *"timer or expectation (`TimerFired`)"*, **`R`** is *"recovery process"*),
   §2 the transition-row defaults, §3 `GR-1`…`GR-17` (**`GR-1`**, **`GR-2`**, **`GR-3`**,
   **`GR-4`**, **`GR-5`**, **`GR-6`**, **`GR-7`**, **`GR-10`**, **`GR-11`**, **`GR-13`**,
   ### **`GR-14`**, **`GR-16`**, **`GR-17`** above all), §4 the canonical state registry — **M9's
   five states, ALL of them `(NH)` except the one `(T)`** — and §5 the canonical event registry.
   **No machine may define a local synonym**, so every state and every event name you write must
   already be registered
10. `docs/specifications/events/registry.md` — §1's envelope and `previous_aggregate_version`
    (`P6-D11`), §3's F9 line (### **six contracts, and no seventh**), §4's identity and dedup rules,
    §5's consequential list (### **no F9 event is on it**), §7's projection rules (**F9 projection
    is `none`**), §8 **ORDERING** (### **F9 is explicitly ORDER-TOLERANT** — and its last bullet on
    `ExceptionSeverityChanged`), §9 **COORDINATION EVENTS**, §10's `ER-1`, `ER-3`, `ER-9`, `ER-11`,
    `ER-15`, `ER-16`, and ### **§11's SECURITY EVENTS → BRAKE table**
11. `docs/specifications/events/14-audit-security-events.md` — the F14 tripwires, their **producers**
    (§3.10 below: exactly one of them is M9's), and which three of them are the **Sev-0 source
    detectors** F9's cross-cutting section names
12. `docs/architecture/target-system-specification.md` **§12.9** in full — the six-row lifecycle
    table, the ### **`AutoClose` | `Inactivity` ⛔ ILLEGAL** row, the *"an exception closed without a
    decision is not closed — it is FORGOTTEN"* line (**`F-30`**), the **Expiry: NEVER** line and the
    **PERMANENT-failure** line; **§13**'s ### **`M-35`** (*"Every Work Item and every Exception MUST
    have an accountable human owner, at all times, from creation"* — **`I1`**); **§14**'s
    ### **`M-36`** (*"a durable timer emitting `TimerFired` — never a background sweep"*);
    **§26.4**'s ### **`M-74`** and its TRANSIENT/PERMANENT classification (*"a catch-all base class
    is NOT a classification"*); **§26.5** crash recovery; **§20.7** / **§21**'s repeatedly-overridden
    rule (*"the system does NOT change it. IT ASKS."*); and **`I11`** in the invariant table
    (*"closure is an event"*)
13. `docs/architecture/decisions/ADR-008-durable-workflows.md` — §3's TTL-expiry rule (*"an Exception
    with an accountable owner"*), the **L-D** retry classification, the M9 lifecycle table and
    **V3**; and `docs/architecture/decisions/ADR-006-verification-and-unknown-outcomes.md` — the
    PERMANENT-failure rule (*"stop immediately, raise an Exception, escalate. Never retried."*)
14. `docs/architecture/decisions/ADR-011-human-brake.md` and
    `docs/architecture/decisions/ADR-010-policy-rules-constraints-autonomy.md` — the brake M9 must
    NOT engage, and the Policy Owner M9 ASKS rather than obeys
15. `docs/architecture/decisions/ADR-002-state-classes-and-lineage.md` and
    `docs/architecture/decisions/ADR-009-concurrency-and-reservations.md` — the evidence conditions
    and the optimistic-concurrency rule `[C-10]`
16. `docs/specifications/entities/00-conventions.md` — `[C-1]`, `[C-2]`, `[C-3]`, `[C-5]`, `[C-6]`,
    `[C-7]`, `[C-8]`, `[C-9]`, `[C-10]`, and the addendum's ### **`K-1`** (`decision_ref` resolves
    to an `audit_events` human-decision row OR an `ACTIVE` `rule_id`, never free text), **`K-2`**
    (### **`entity_ref` is the projected/native business entity a record concerns — and it appears
    on Exception**), **`K-3`** (replay is sandboxed and zero-emission) and **`K-4`** (a money field
    on an operational record carries the observation it was read from)
17. `docs/specifications/acceptance/foundational-machine-acceptance.md` — M9's row
    (### **`AC-MACH-901..907`**, state oracle *"row + decision_ref"*, gate **G1**), the named anchor
    ### **`AC-MACH-903`** (*"Exception close requires a resolving `decision_ref`"*), and the ten
    per-machine mandatory assertions — **assertion 9 is *"no transition leaves a Work Item/Exception
    ownerless"***
18. `docs/specifications/acceptance/platform-safety-acceptance.md` — ### **`AC-SAFE-024`** (*"the ref
    must RESOLVE to a human-decision audit row or an ACTIVE rule — a bare string fails"*),
    **`AC-SAFE-028`** (every open unit of work has one accountable human owner), **`AC-SAFE-019`**
    (replay creates no witness, grant or effect), **`AC-SAFE-022`** (an unknown always carries an
    owner and a reason; **no timer moves it**), **`AC-SAFE-025`** (cross-tenant rejected before
    business handling) and **`AC-SAFE-027`** (automation cannot broaden policy or release a brake)
19. the **P5** event transport, outbox/inbox, replay isolation and durable timers:
    `src/freight_recon/event_outbox.py`, ### **`event_inbox.py` (read `expire_overdue` in full — it
    is a LANDED, PRE-DECLARED M9 SEAM: *"this module cannot raise a canonical exception event…the
    caller gets the owner and the evidence and is the one that can"*)**, `event_replay.py`
    (### **`compare_to_live` returns findings and DOES NOT ENGAGE A BRAKE, deliberately**),
    `event_timers.py`, `migrations/phase5_durable_timers.py`, `event_contracts.py` and
    `event_contracts_data.json`
20. **M1** Work Item (`src/freight_recon/work_item.py`) — ### **read `resolve_decision_ref` IN FULL.
    It is the landed, shared `K-1` resolver and you IMPORT it rather than writing a second one**;
    read `HUMAN_DECISION_EVENTS`, `DECISION_REF_KINDS`, `K1_NAMES_NOT_YET_CANONICAL`,
    `FailureDisposition`, `WorkItemMachine.ownerless()`, the `owner_id` FK into `tenant_humans`, and
    the `WI-5`/`WI-6` `BLOCKED` + `blocker_ref` rows and their trigger sets. ### **You are not
    editing this file**
21. **M3** External Effect (`src/freight_recon/external_effect.py`) — read what it does with
    `UNKNOWN_OUTCOME`, `NEEDS_VERIFICATION` and `_resolve_decision` **today**. It is the second
    caller of M1's resolver and the precedent for how a machine consumes it. ### **You are not
    editing this file**
22. **M4** Approval (`src/freight_recon/approval.py`) — the `AP-3` durable timer scheduled in the
    SAME commit as the record it guards, and `AP-9`'s `frozen` quarantine, which is the one landed
    *"this record is frozen"* representation. ### **You are not editing this file**
23. **M7** Conflict (`src/freight_recon/conflict.py`, `migrations/phase6_conflicts.py`) — ### **the
    closest landed precedent you have.** Its `decision_ref` / `decision_ref_kind` /
    `decision_human_id` columns, its `native_projection()` into the checkpoint's EXISTING types
    without importing the checkpoint, its escalation timer that ESCALATES and never resolves, and
    ### **`conflict_parties`' `party_kind` discriminator with per-kind MIRROR columns carrying the
    FKs only for the kinds whose table exists** — which is §3.8 `M9-AQ-3`'s answer. **You are not
    editing this file**
24. **M8** Expectation (`src/freight_recon/expectation.py`,
    `migrations/phase6_expectations.py`) — the most recent landed unit, whose `OVERDUE`,
    `INDETERMINATE` and `EXPIRED` rows all end *"→ Exception"* and whose module header names that
    seam in PROSE. ### **You are not editing this file**
25. **M5** Observation (`src/freight_recon/observation.py`) and **M6** Identity Binding Claim
    (`src/freight_recon/identity_binding_claim.py`) — `UNPARSEABLE`/`UNBOUND` and
    `AMBIGUOUS`/`CONFLICTING` are the other four landed *"→ Exception"* seams. ### **You are not
    editing either file**
26. `src/freight_recon/checkpoint.py` — the `EvidenceCondition` enum, `NativeClaim`,
    `ProvenancedFact` and **step 4, native-state validity**, which is where entity §38's *"an open
    Exception that freezes an entity blocks consequential actions on it"* lands. **You are not
    changing this file** (§3.9)
27. `src/freight_recon/brake.py` — `engage`, `actor_kind ∈ {HUMAN, DETECTOR}`, and the release rules.
    ### **M9 CALLS NONE OF IT** (§3.8 `M9-AQ` F seam, §3.10)

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
agreement. **§3.8 below names six such conflicts that are already known. Read it before you write
the transition table or the migration.**

---

## 1. What Neyma is — the stable identity

Neyma is an **AI-native operating platform and system of action for SMB freight and logistics
companies.**

It is **not** an invoice bot, a document-extraction product, a Slack bot, a TMS chatbot, a browser
wrapper, an AP tool, an email triage system, an alerting product, an incident-management tool, a
ticketing system, or a disconnected collection of agents. If a piece of legacy code in this
repository suggests otherwise, that code is material, not direction.

- **P0–P8** build the shared governed operating engine.
- **P9–P13** build freight operational capability on top of it.
- **P14** expands bounded autonomy.

## 2. Where the program stands

- **P0–P5 COMPLETE.**
- **M1** (Work Item, `P6-CP-1`), **M2** (Pipeline Instance, `P6-CP-2`), **M3** (External Effect /
  Effect Grant, `P6-CP-3`), **M4** (Approval, `P6-CP-4`), **M5** (Observation, `P6-CP-5`), **M6**
  (Identity Binding Claim, `P6-CP-6`), **M7** (Conflict, `P6-CP-7`) and **M8** (Expectation,
  `P6-CP-8`) have all landed, each with its focused independent review on disk. **`P6-D11`** is
  resolved and landed. The **P3/P4 one-connection-per-thread concurrency correction** at `d70a4e7`
  is landed and **must not be reworked**.
- **P6 IN PROGRESS. M9 is the next build checkpoint. M9–M13 remain**, and **37 of the 134
  transitions**. `CURRENT.md`'s "Still owed" cell says so in terms: *"**M9 — the Exception — is the
  next build checkpoint.**"*
- **No P6 acceptance criterion is scored.** P6 has not reached phase acceptance. **P7+ blocked.**
- **M1 through M8 all ship dark, and M9 ships dark too.** No live production effect or integration
  is enabled by any of them.

`CURRENT.md`'s ⛔ table blocks **Implementation Phase 7** and names *"provenance, evidence,
observation, claims, identity binding"* inside it. That is **P7's provenance and evidence
platform**, not this unit. **M9 is the P6 Exception state machine** — one aggregate, one machine,
five states, seven transitions — and it is exactly what `CURRENT.md`'s "Still owed" cell and its
closing sentence both mean. This is the same sentence pair M5, M6, M7 and M8 were handed and it
resolved the same way all four times. If you conclude those two sentences cannot both be true, that
is §3.8 behaviour: say so and stop.

---

## 3. The unit: M9, and nothing else

### 3.1 The three sentences the whole unit is a consequence of

### **AN EXCEPTION IS SOMETHING THAT NEEDS A HUMAN.**
### **IT REACHES A NAMED HUMAN OWNER FROM CREATION, AND IT IS NEVER CLOSED BY SILENCE.**
### **AN EXCEPTION CLOSED WITHOUT A DECISION IS NOT CLOSED — IT IS FORGOTTEN.**

Every other machine in this repository has a state that means *"a human has to look at this"*: M3's
`UNKNOWN_OUTCOME`, M5's `UNPARSEABLE` and `UNBOUND`, M6's `AMBIGUOUS` and `CONFLICTING`, M7's `OPEN`
and `ESCALATED`, M8's `OVERDUE` and `INDETERMINATE`, M1's `BLOCKED` and `AWAITING_HUMAN`. **M9 is the
machine those states point at.** It is the place where *"Neyma could not resolve this
deterministically"* becomes *"a named person owns it, and the system will not stop asking."*

And it carries exactly one hard honesty rule, which is the whole reason this unit is not an alert:

> ### **AN EXCEPTION IS NOT AN ERROR LOG, AN ALERT, OR AN ISSUE TRACKER ROW** (entity §4).
> ### **IT IS NOT AUTO-CLOSABLE. IT IS NOT OUTLIVABLE.**

An alert that nobody acknowledges disappears from the screen. A log line rotates out. A ticket gets
auto-closed after thirty days of inactivity because a queue looked untidy. ### **Every one of those
is a mechanism for forgetting**, and the whole reason this entity exists is that in freight the
things Neyma cannot resolve are exactly the things that cost money: an unknown outcome on a payment,
a POD nobody can find, an identity binding that could be either of two loads. `F-30`, stated by the
target specification in terms:

> ### **An exception closed without a decision is not closed — it is FORGOTTEN.**

So closure is not a status change. ### **CLOSURE IS AN EVENT WITH A RESOLVING `decision_ref`**
(`I11`, `GR-14`, `AC-SAFE-024`, `AC-MACH-903`), and there is no other way out. Not inactivity, not
`AutoClose`, not expiry, not a sweep, not a reaper, not a timer, and ### **NEVER A MODEL**.

The one thing a timer may do is make the exception **louder**: `EC-4` ages it and `EC-5` escalates
it. ### **A TIMER NEVER RESOLVES** (machine §37: *"never a resolution timer"*).

### 3.2 The canonical state set

**Five states** (registry §4 / M9; target spec §12.9). Do not add a sixth, and do not rename one.

| State | Class | Meaning |
|---|---|---|
| `OPEN` | `(NH)` non-terminal, human-owned | something needs a human, and a named human owns it |
| `ACKNOWLEDGED` | `(NH)` non-terminal, human-owned | an authenticated human has SEEN it — not resolved it |
| `AGEING` | `(NH)` non-terminal, human-owned | it passed the age threshold and is getting louder |
| `ESCALATED` | `(NH)` non-terminal, human-owned | it passed the escalation threshold |
| `RESOLVED` | `(T)` terminal | a human (or an `ACTIVE` rule) decided, and the decision RESOLVES |

**Terminal:** ### **`RESOLVED` ONLY.** **Human-owned non-terminal:** ### **ALL FOUR OTHERS.**
**Initial:** `OPEN`. **Recoverable:** ### **NONE — machine §10 says so in terms: *"none (all
non-terminal states are human-owned)"*.** **Failure:** n/a (machine §11). **Expiry:**
### **NEVER — it ages and escalates; an exception cannot be outlived** (machine §12).
**Unknown-outcome:** n/a — ### **an unknown outcome ELSEWHERE raises an Exception HERE** (machine
§13).

### **DO NOT INVENT `CANCELLED`, `EXPIRED`, `TIMED_OUT`, `STALE`, `CLOSED`, `AUTO_CLOSED`,
`DISMISSED`, `REOPENED` OR `SUPERSEDED`.** `CANCELLED` and `EXPIRED` are the two a build session
reaches for, because entity §25 discusses cancellation and every neighbouring machine has an
`EXPIRED` — and each of them is exactly the forgetting this unit exists to prevent. See §3.8
`M9-AQ-2` before you write either one.

### **AND DO NOT PROMOTE A `sub_status` TO A LIFECYCLE STATE.** The machine's own header paragraph
is unambiguous:

> The brief names finer sub-states (triage / assigned / investigating / awaiting-external /
> awaiting-human / resolution-proposed). ### **These are `owner`/`sub_status` FIELDS on the row, NOT
> new lifecycle states** — the canonical state set is the frozen five. Adding lifecycle states would
> diverge from the entity spec; the finer distinctions are captured as an enum `sub_status` under
> `OPEN`/`ACKNOWLEDGED` without new transitions.

So `TRIAGE`, `ASSIGNED`, `INVESTIGATING`, `AWAITING_EXTERNAL`, `AWAITING_HUMAN` and
`RESOLUTION_PROPOSED` are ### **FORBIDDEN AS LIFECYCLE STATES**, and `AWAITING_HUMAN` is doubly so:
it is **M1's registered state**, and registry's binding header says ### **no machine may define a
local synonym.** If you implement `sub_status`, it is a **column with a closed CHECK vocabulary**,
constrained to `OPEN`/`ACKNOWLEDGED`, it appears in **no** transition guard that changes `state`, and
it adds **no** transition row.

### 3.3 What M9 consumes, and what it emits

**Consumes** (machine §33, entity §32): ### **`Acknowledged` · `Resolved` · `TimerFired`. THOSE
THREE, AND NOTHING ELSE.** Read that literally before you write a consumer — see §3.8 `M9-AQ-4`.

**Emits — the SIX REGISTERED F9 contracts, and no seventh** (`events/registry.md` §3 is by its own
header the sole canonical list of event names):

| Event | Transition | Required added payload |
|---|---|---|
| `ExceptionRaised` | `EC-1` | `severity`, `source_ref`; `exposure?`, `specific_question?`, `sub_status?` |
| `ExceptionAcknowledged` | `EC-2` | `acknowledged_by` |
| `ExceptionAgeing` | `EC-4` | — |
| `ExceptionEscalated` | `EC-5` | — |
| `ExceptionSeverityChanged` | `EC-7` | ### **`severity`, `previous_severity`, `changed_by`, `reason` — ALL FOUR REQUIRED** |
| `ExceptionResolved` | `EC-3` / `EC-6` | ### **`decision_ref` (REQUIRED — and it must RESOLVE per `K-1`)** |

`event_contracts_data.json` already carries all six with exactly those required fields, with
`family: F9`, `aggregate_type: exception` and `strict_order: false`. It is the mechanical projection
of `events/registry.md`, so **the contract guard already exists and you inherit it** — you are not
inventing an event, and a name outside this table is defective by the registry's own definition.
### **DO NOT MINT `ExceptionCancelled`, `ExceptionExpired`, `ExceptionClosed`, `ExceptionReopened`,
`ExceptionAutoClosed`, `ExceptionTimedOut`, `ExceptionSuperseded` OR `ExceptionDismissed`.**

Note what `ExceptionSeverityChanged` is for, because it is the one contract a build session drops as
redundant. F9 states it: ### ***"`ExceptionRaised` records severity at creation; `EC-7` mutates it,
so without this event a rebuild reproduces the ORIGINAL severity and can UNDER-STATE the live
one"*** — and a Sev-0 exception auto-engages the brake at its source, so that under-statement is a
safety loss, not a cosmetic one. `previous_severity` is what makes a missing link **DETECTABLE in a
fold** instead of silently absorbed.

### 3.4 Implement the canonical `EC-*` transition contract

**Seven rows. `EC-1`, `EC-2`, `EC-3`, `EC-4`, `EC-5`, `EC-6`, `EC-7` — an EXACT SET MATCH with
machine §14.** Not six, not eight. `AC-MACH-901..907`. Anything not enumerated is ILLEGAL (`GR-1`):
it raises, persists nothing, and emits `IllegalTransitionAttempted` to **audit and security**.

| ID | From → To | Trig | Guard | Writes |
|---|---|---|---|---|
| **EC-1** | — → `OPEN` | `S`\|`R` | ### **a human `owner_id` ASSIGNED AT CREATION (`I1`)**; `severity`, `source_ref`; ### **a PERMANENT (auth/config) failure is raised IMMEDIATELY and NEVER RETRIED (`L-D`)** | `sub_status?`, `exposure?`, `specific_question?` |
| **EC-2** | `OPEN` → `ACKNOWLEDGED` | `H` | ### **an AUTHENTICATED HUMAN saw it** | `acknowledged_by` |
| **EC-3** | `{OPEN, ACKNOWLEDGED}` → `RESOLVED` | `H` | ### **`decision_ref` VALID (`GR-14`) — an `audit_events` human-decision row OR an `ACTIVE` `rule_id`** | `decision_ref` |
| **EC-4** | `{OPEN, ACKNOWLEDGED}` → `AGEING` | `T` | age threshold (### **durable timer**) | — |
| **EC-5** | `AGEING` → `ESCALATED` | `T` | escalation threshold | — |
| **EC-6** | `ESCALATED` → `RESOLVED` | `H` | `decision_ref` valid | `decision_ref` |
| **EC-7** | `{OPEN, ACKNOWLEDGED, ESCALATED}` → ### ***(severity change — NO STATE CHANGE)*** | `S`\|`H` | new severity | `severity` |

Read the **from-sets** literally, because four of them are places a build session widens the table
without noticing:

- ### **`EC-3`'s from-set is `{OPEN, ACKNOWLEDGED}` and `EC-6`'s is `{ESCALATED}`. `AGEING` IS IN
  NEITHER.** Resolving an `AGEING` Exception directly is an ILLEGAL transition under `GR-1`, not a
  convenience. Target spec §12.9 agrees: its `Resolved` row reads `OPEN`\|`ACKNOWLEDGED`. Do not
  widen either row, and do not add an `EC-3a`
- ### **`EC-4`'s from-set is `{OPEN, ACKNOWLEDGED}`. `AGEING`, `ESCALATED` AND `RESOLVED` ARE NOT IN
  IT** — a timer may not re-age an already-ageing or escalated Exception, and nothing at all moves a
  `RESOLVED` one. See §3.8 `M9-AQ-6`
- ### **`EC-7`'s from-set is `{OPEN, ACKNOWLEDGED, ESCALATED}`. `AGEING` IS NOT IN IT**, and neither
  is `RESOLVED`
- ### **`EC-7` DOES NOT CHANGE `state`.** It is a FIELD mutation with its own registered event and
  its own `aggregate_version` bump. A build session that implements severity as five more states, or
  as a `SEV0` lifecycle state, has minted a sixth state by another name

**§16 Precedence.** ### **RESOLUTION (`EC-3`/`EC-6`) REQUIRES A HUMAN; AGEING AND ESCALATION ARE
AUTOMATIC BUT NEVER RESOLVE.** Prove the second half with a real durable timer, not by not calling
one.

**§15 Illegal transitions**, stated by the machine by hand:

- ### **`RESOLVED` without a valid `decision_ref` → ILLEGAL (`GR-14`)**
- ### **`AutoClose` / `Inactivity` → ILLEGAL** — *an exception closed without a decision is not
  closed, it is forgotten*
- ### **an ownerless Exception → ILLEGAL**
- ### **an expired Exception → ILLEGAL (it never expires)**

### 3.5 What must hold — the authority and safety requirements

1. ### **A NAMED HUMAN OWNER FROM CREATION, OR CREATION FAILS** (entity §10/§16/§21/§37, machine §5,
   `I1`, `M-35`, `AC-SAFE-028`, acceptance assertion 9). `owner_id` is `NOT NULL` **and FK-backed
   into `tenant_humans`**, exactly as M1's, M5's, M6's, M7's and M8's are. ### **An ownerless
   Exception is a STRUCTURALLY IMPOSSIBLE state** — an insert the database refuses, not a branch a
   code path takes. ### **A model is not a human**, `system` is not a human, and the owner must be
   an **ACTIVE** human **of this tenant**
2. ### **CLOSURE REQUIRES A `decision_ref` THAT RESOLVES** (entity §16/§22/§36/§43(a), machine
   §14/§15, `GR-14`, `K-1`, `I11`, `AC-SAFE-024`, `AC-MACH-903`, target spec §12.9). A database
   `CHECK` — `state = 'RESOLVED'` requires a non-null `decision_ref` — **and** a resolver that
   refuses a value which references nothing. ### **THE CHECK IS NOT "NON-NULL". IT IS "RESOLVES".**
   `K-1`: the ref must resolve to an `audit_events` human-decision row recording an **authenticated
   human** actor, or to an `ACTIVE` `rule_id`. ### **A bare string fails. The string `done` fails. A
   human-decision event TYPE emitted by automation fails** (`ER-11` — that is authority laundering).
   ### **IMPORT M1's `resolve_decision_ref`. DO NOT WRITE A SECOND ONE.** Two implementations of
   *"does this decision_ref resolve"* is two places for one of them to start accepting `done`, and
   M3 already sets the precedent by importing it rather than re-writing it (`CLAUDE.md` rule 17, in
   its own domain). Read §3.8 `M9-AQ-1` before you write the `CHECK`
3. ### **NEVER CLOSED BY SILENCE** (entity §3/§26/§36/§37/§43(b), machine §3/§12/§15/§23, target spec
   §12.9, `F-30`). ### **Inactivity cannot close. `AutoClose` cannot close. Expiry cannot close. A
   sweep cannot close. A reaper cannot close. A TTL cannot close. A timer may AGE or ESCALATE and
   ### NEVER RESOLVE** (machine §37). ### **NO SWEEP, NO REAPER, NO STALE-EXCEPTION SCAN** (`M-36`:
   *"never a background sweep, never a scan for things that look old"*). A row that quietly stops
   being visible is the exact failure this entity exists to prevent
4. ### **A MODEL MAY NEVER CLOSE, RESOLVE OR AUTO-CLEAR AN EXCEPTION** (entity §35, machine §40,
   `[C-6]`, `GR-7`, `ER-9`). Nor acknowledge one (`EC-2`'s trigger is `H`, and F9 says
   `actor_type=human`). Nor own one. Nor change its severity — F9 says `actor_type ∈ {human, system}`
   for `EC-7` and ### **never `model`**. ### **Confidence is not a guard input at any value including
   1.0** (`GR-8`)
5. ### **ACKNOWLEDGEMENT PROVES SEEN, NOT RESOLVED** (entity §31, machine §14, F9). `EC-2` records
   `acknowledged_by` and `acknowledged_at`, moves the row to `ACKNOWLEDGED`, and changes nothing
   about the obligation. An `ACKNOWLEDGED` Exception is still open work with a named owner, and it
   still ages
6. ### **AGEING AND ESCALATION RIDE A DURABLE TIMER** (machine §37, entity §43(e), `M-36`,
   ADR-008). They ride **P5's existing `event_timers.py` and `durable_timers` table** — schedule in
   the SAME commit as the raise, the way `AP-3` and M7's `CF-5` do. ### **No in-memory sleep, no
   background scan, no second timer mechanism.** A timer fires at least once and the machine acts
   exactly once, because the guard is idempotent (`GR-4`). ### **`AGEING` AND `ESCALATED` REMAIN
   HUMAN-OWNED** — the row still names its human, and getting louder never means getting orphaned
7. ### **SEVERITY IS A FIELD, AND `{SEV0, SEV1, SEV2}` IS THE WHOLE VOCABULARY** (entity §12,
   registry §4, F9). A database `CHECK`. `EC-7` mutates it with its own registered event carrying
   ### **`previous_severity`, `severity`, `changed_by` AND `reason` — all four required** — and it
   does **not** change `state`. ### **A REBUILD REPRODUCES THE CURRENT SEVERITY BY FOLDING THE
   RECORDED SEVERITY-CHANGE EVENTS** at the highest `aggregate_version` (`events/registry.md` §8's
   last bullet), ### **never by reading the live row.** F9 says why: without the event, a rebuild
   reproduces the ORIGINAL severity and can UNDER-STATE the live one
8. ### **A PERMANENT FAILURE RAISES IMMEDIATELY, WITH ZERO RETRIES** (entity §36/§43(d), machine
   §14's `EC-1`, target spec §26.4 `M-74`, ADR-006, ADR-008 `L-D`). **PERMANENT** =
   authentication · authorization · configuration · protocol. **TRANSIENT** = socket · timeout ·
   throttle · browser busy. ### **A CATCH-ALL BASE CLASS IS NOT A CLASSIFICATION**, and neither is a
   string, a message, an HTTP status guessed at, or a model's opinion. The classification is
   **SUPPLIED** to `EC-1` as an enumerated value — M1's landed `FailureDisposition` is the vocabulary
   — and the exception RECORDS it (entity §13/§31). ### **Do not build a classifier**: mapping a
   vendor's error to TRANSIENT or PERMANENT is P9+ adapter work, and inferring it from a string is
   the defect. See §3.8's `G` seam
9. ### **IDEMPOTENCY IS `GR-4`, AND THE DEDUP INDEX IS OPTIONAL** (entity §33, machine §17/§19, F9
   cross-cutting). `GR-4`'s consumer inbox — a redelivered `(consumer, tenant, event_id)` is a
   **no-op** — is **MANDATORY**. The open-exception dedup index
   `UNIQUE (tenant, source_ref, type) WHERE state != 'RESOLVED'` is stated as ### **"optional" by
   THREE authorities in those words** (entity §17: *"Optional …"*; machine §17: *"optional
   `UNIQUE(...)`"*; F9 cross-cutting: *"optional"*). ### **DO NOT TURN AN EXPLICITLY OPTIONAL
   CONSTRAINT INTO A MANDATORY ACCEPTANCE CRITERION.** Build it or do not build it, ### **state which
   you did and why**, and if you build it make it tenant-first and partial on `state != 'RESOLVED'`.
   Machine §19 says *"GR-4 + the dedup index"*, which is the closest thing to a requirement in the
   corpus — record that reading beside your choice rather than treating it as one
10. ### **TENANT IS THE FIRST PARTITION DIMENSION** (`[C-1]`, `AC-SAFE-025`, `ER-15`). First column
    of the primary key and of every index; first predicate of every read and write. ### **A
    cross-tenant owner, a cross-tenant `source_ref`, a cross-tenant `decision_ref`, a cross-tenant
    frozen entity and a cross-tenant queue read each FAIL CLOSED.** The owner queue is the surface
    that makes this concrete: *"show me the open exceptions"* must never return another brokerage's
11. ### **RAISING AND FREEZING ARE ONE COMMIT — WHERE APPLICABLE** (entity §15/§38, machine
    §4/§34/§35, `[C-2]`, `GR-2`). ### **NOT EVERY EXCEPTION FREEZES AN ENTITY.** Entity §38 states
    the materiality condition in terms: *"only those that make a material field non-`consistent`"*.
    So the freeze is **conditional**, the condition is **stated by the caller and recorded**, and
    when it applies the exception row + the freeze + the outbox event are ### **ONE COMMIT**: a
    persistence failure rolls all of it back and there is no half-raised exception. ### **Do not
    invent a generic freeze table.** Read §3.8's `E` seam and §3.9 first
12. ### **REPLAY RECONSTRUCTS; IT NEVER MANUFACTURES** (entity §34, machine §21, F9 cross-cutting,
    `GR-11`, `[C-5]`, `K-3`, `AC-SAFE-019`). A full-history rebuild reconstructs the Exception's
    state and its **current severity**; an open Exception stays open and ### **its frozen entity
    stays blocked** (machine §36, F9 cross-cutting). ### **Replay mints no authority, constructs no
    witness, claims no grant, causes no external effect and — above all — CAN NEVER MANUFACTURE A
    `decision_ref`.** A rebuild that could produce resolution authority would be a rebuild that could
    close an exception nobody decided
13. ### **AN EXCEPTION IS AN INPUT TO THE CHECKPOINT, NEVER A GATE** (entity §38, machine §28,
    `GR-10`). `checkpoint.py` remains the sole minter of a gate decision (`CLAUDE.md` rule 17), and
    M3 remains the single effect authority. ### **M9 mints no gate decision, writes no
    `effect_grants` row, and takes no external action.** The interaction is the M7/M8 shape:
    **project** the row into checkpoint's EXISTING types and show step 4 refuses
14. ### **UNDER A BRAKE, EXCEPTIONS STILL RAISE** — a brake refuses to mint and refuses to claim
    (`GR-16`); it does not stop Neyma noticing that something needs a human. ### **AND M9 ENGAGES NO
    BRAKE.** F9 is explicit: Sev-0 exceptions *"are produced BY F14 detectors and auto-engage the
    brake"* — ### **the brake is the DETECTOR's act, at the source, not M9's.** See §3.8's `F` seam
15. ### **A REPEATEDLY-OVERRIDDEN RULE RAISES AN EXCEPTION TO THE POLICY OWNER — AND THE SYSTEM
    ASKS, IT DOES NOT CHANGE THE RULE** (entity §41, machine §29, target spec §20.7). This is a
    sentence about what an Exception is FOR, and it is the one place a build session reaches for M11
    and M12. ### **M9 builds no policy engine, no rule registry and no override counter.** The
    Policy Owner is an `authority_role` that already exists in `tenant_humans`; the rest is M11/M12
16. ### **RETENTION IS PERMANENT AND DELETION IS NONE** (entity §28/§29, `[C-9]`, `[C-8]`). A
    resolved Exception is retained. There is no `DELETE FROM exceptions`, no purge, no archive job,
    no TTL

### 3.6 The queue is an ORDERING, not a product

Entity §42 and machine §38 both say the same thing: *"`NEEDS_VERIFICATION`-backed and Sev-0
exceptions are the highest-priority operational queue. Mean time to human resolution is the metric
that matters."*

- **What M9 owes** is that the row carries what an ordering needs — `severity`, `created_at`,
  `owner_id`, `state`, `tenant` — and a **tenant-first index** that makes *"the open exceptions this
  brokerage's named human owns"* one read. M7's `ix_conflicts_owner_queue` and M8's
  `ix_expectations_owner_queue` are the landed shape
- ### **What M9 DOES NOT OWE is a queue.** No oversight surface, no UI, no dashboard, no API, no
  notification, no Slack message, no email, no SMS, no paging integration, no on-call rotation, no
  escalation-message template, no MTTR metric emitter. F9 names *"Oversight (queue)"* as the
  CONSUMER of every F9 event; ### **that consumer is not built and M9 is not building it**
- ### **Do not build an exception-queue UI.** M9's product form is *an operational exception queue
  with owners* — the registry says so in terms at
  `rebaseline_contract.user_visible_capability: "exceptions become a managed queue with owners"` —
  and that is a **P8** deliverable, not this one. The product form is precisely the thing that must
  not arrive with the engine primitive

### 3.7 The `source_ref` — what an Exception points BACK at

Entity §9: *"`source_ref` (the machine that raised it — an Observation, Claim, Conflict, Expectation,
Pipeline, Compensation…)"*. Entity §18 makes it a **FOREIGN KEY**. Those two sentences are in
tension the moment you count the tables, which is `M9-AQ-3`; read it before you write the migration.
This section is what every reading agrees on.

- `source_ref` is `NOT NULL` and it is a component of the (optional) dedup key with `type`
- ### **THE LANDED PRECEDENT IS `conflict_parties`, AND IT IS EXACT.** M7 ships `party_ref` as the
  single source of truth, a `party_kind` discriminator with a **closed CHECK vocabulary**, per-kind
  **MIRROR** columns carrying real FKs **only for the kinds whose table exists**, and CHECKs keeping
  the mirrors consistent with `party_ref`/`party_kind`. M8 ships the same shape one kind wide
  (`subject_ref`/`subject_kind`/`subject_observation_ref`). ### **Follow it.** Do not invent a new
  polymorphic-FK design, and do not build a table so that a FK has somewhere to point
- **the kinds whose table exists today**: `observation` (M5), `identity_binding_claim` (M6),
  `conflict` (M7), `expectation` (M8), `work_item` (M1), `pipeline_instance` (M2), `effect_grant`
  (M3), `approval` (M4). ### **The kinds whose table does NOT exist**: `compensation` (M10),
  `evidence` (P7), `rule` (M12), `policy` (M11), and a `pending_reference` park, which is keyed
  `(tenant, consumer_id, event_id)` rather than by a single id. Carry those as a constrained,
  `NOT NULL` `source_ref` with the discriminator and **no FK**, and record the missing half
- entity §14 also says *"An Exception may reference a Work Item, and blocks its progress until
  resolved"*, and `K-2` puts `entity_ref` on Exception. ### **`entity_ref` and `source_ref` are two
  different references and MUST NOT be conflated** (`K-2`): `source_ref` is the machine row that
  raised it; `entity_ref` is the projected business entity it concerns. If you carry `entity_ref`,
  carry it as M7 and M1 do — `NOT NULL`-where-required, no FK into a freight projection that is P9+

### 3.8 ⚠️ THE KNOWN AUTHORITY QUESTIONS — read this before writing the transition table

The corpus contains six disagreements about M9 that this file does **not** resolve, and neither may
you. Each is a real conflict between authoritative documents, and each is mechanically demonstrable
rather than a reading of tone. **Report them; implement only what every reading agrees on.** Product
Driver surfaces a reported conflict; it treats a silently invented resolution as a defect.

**`M9-AQ-1` — does resolution require a HUMAN, or a human OR an `ACTIVE` rule?**

- ### **A HUMAN.** Entity §35: *"Resolution requires an authenticated human with a `decision_ref`."*
  Machine §16: *"resolution (`EC-3`/`6`) requires a human."* Machine §40: *"resolution requires an
  authenticated human with a valid `decision_ref`."* Target spec §12.9's trigger column reads
  `Resolved` with the guard *"REQUIRES `decision_ref`"* under the heading *"something that needs a
  **human**"*. Entity §3 and machine §3 both define the entity as *"something that needs a human."*
- ### **A HUMAN OR AN `ACTIVE` RULE.** `GR-14`, verbatim: *"Exception closure requires a valid
  `decision_ref` — an `audit_events` human-decision row **or** an `ACTIVE` `rule_id` (`K-1`)."*
  `K-1` itself defines exactly those two referents. F9's `ExceptionResolved` row: *"proves an
  authenticated human ### (or ACTIVE rule) resolved it."* `AC-SAFE-024`: *"the ref must RESOLVE to a
  human-decision audit row **or an ACTIVE rule** — a bare string fails."* Machine §14's `EC-3` guard
  quotes `GR-14` in full, including the `or`.

**Every reading agrees on:** ### **closure requires a `decision_ref` that RESOLVES per `K-1`, and a
bare string is not one**; a `decision_ref` resolving to nothing is refused; a human-decision event
type recorded with a non-human `actor_type` is refused (`ER-11`); ### **`AutoClose`/inactivity is
ILLEGAL**; and ### **a model may NEVER resolve** (`GR-7`, `[C-6]`, entity §35). And note what the
LANDED code already does, which satisfies both readings without choosing between them:
`work_item.resolve_decision_ref` accepts `kind ∈ {AUDIT_EVENT, RULE}` and ### **REFUSES `RULE`
TODAY, with a named reason** — there is no `rules` table until M12, and a stub that accepted any rule
id would make *"closed by an active rule"* true of rules that do not exist. That refusal is recorded
as debt ### **`P6-D4`, which closes at M12 — NOT at M9.** ### **Import that resolver; do not weaken
the human branch; do not close `P6-D4`; do not build a `rules` table.** State which reading you
implemented and record the other.

**`M9-AQ-2` — what is cancellation, given there is no `CANCELLED` state and no `ExceptionCancelled`
event?**

- **Cancellation exists.** Entity §25: *"Cancellation rules. Only if the underlying cause is
  retracted — still an event, still a `decision_ref`."* Machine §22 repeats it word for word.
- **Cancellation has nowhere to go.** Registry §4's M9 row is ### **five states with no
  `CANCELLED`**; `events/registry.md` §3's F9 line and §5's event registry list ### **six contracts
  with no `ExceptionCancelled`**; machine §14 is ### **`EC-1`…`EC-7` and no eighth row**; and
  `event_contracts_data.json` carries exactly six F9 contracts. Meanwhile entity §26 and machine §12
  say the lifecycle ### **NEVER EXPIRES**, and `RESOLVED` is the only terminal state — so a retracted
  cause either becomes `RESOLVED` or it stays open forever.

**Every reading agrees on:** ### **there is NO `CANCELLED` lifecycle state and NO `ExceptionCancelled`
event**, there is no eighth transition row, and ### **a retraction is NEVER SILENT — §25 says "still
an event, still a `decision_ref`" whichever way it lands.** Build that: a retracted cause is refused
as an illegal transition if it is asked to mint a state or an event the registry does not hold, and
it can only reach a terminal state through `EC-3`/`EC-6` with a resolving `decision_ref` like every
other closure. ### **Do not invent a state, a transition or an event to hold cancellation**, and do
not delete §25 from your reading of the entity — say which of the three it is (a `RESOLVED` mapping,
deferred prose, or a true corpus contradiction) as a REPORT, not as an amendment.

**`M9-AQ-3` — `source_ref` is a `FOREIGN KEY` to what, across eight-plus aggregate types?**

- **It is a FK.** Entity §18: *"`source_ref`, `decision_ref` FK."* Entity §16 makes it `NOT NULL`.
- **There is no one table to point at.** Entity §9 enumerates *"an Observation, Claim, Conflict,
  Expectation, Pipeline, Compensation…"* — with an ellipsis — and entity §21 adds unknown outcomes,
  dangling-reference TTLs, lost evidence, orphan adapters, cross-tenant breaches and rebuild
  divergences. Four of those aggregate types have **no table** (`compensation` is M10, `evidence` is
  P7, `rule`/`policy` are M12/M11), one is not keyed by a single id (`pending_references`), and
  three are **detector findings rather than aggregate rows at all**. SQLite cannot express a FK
  whose target table varies by row.

**Every reading agrees on:** `source_ref` is `NOT NULL` and non-empty; ### **it is tenant-consistent
— a cross-tenant source fails closed**; and the repository's landed answer to exactly this shape is
### **`conflict_parties`' `party_kind` discriminator with per-kind MIRROR FK columns for the kinds
whose table exists.** Build that (§3.7), state which kinds you gave a FK and which you did not, and
record the missing half. **Do not build `compensations`, `evidence`, `rules` or `policies` to satisfy
one**, and do not conflate `source_ref` with `entity_ref` to make the FK tidy (`K-2`).

**`M9-AQ-4` — who wires the five landed *"→ Exception"* seams, and does M9 subscribe to them?**

- **M9 consumes three triggers.** Machine §33 and entity §32 both list the consumed set as exactly
  ### **`Acknowledged`, `Resolved`, `TimerFired`** — and `EC-1`'s trigger is `S|R`, a deterministic
  system decision or a recovery pass, ### **not `X` (an observed external event).** Nothing in M9's
  own contract makes it a consumer of `ObservationUnparseable`, `ClaimAmbiguous`, `ConflictRaised`,
  `ExpectationOverdue`, `ExpectationIndeterminate`, `ExpectationExpired` or `OutcomeUnknown`. And
  `ER-1`/registry §9 are explicit: ### **a coordination event does NOT instruct a consumer to
  transition.**
- **Five landed machines have an unwired seam pointing here.** F5's `ObservationUnparseable`/
  `ObservationUnbound`, F6's `ClaimAmbiguous`, F7's `ConflictRaised`, F8's `ExpectationOverdue`/
  `ExpectationIndeterminate`/`ExpectationExpired` and F3's `OutcomeUnknown` each end *"→ Exception"*
  in the target spec, in ADR-008 and in their family files, and each landed unit's own header says it
  ### **mints no M9 event and leaves a durable, human-owned row instead** — deferred to M9, by name.
  P5's `event_inbox.expire_overdue` says the same in code: *"this module cannot raise a canonical
  exception event…the caller gets the owner and the evidence and is the one that can."* If M9 lands
  and subscribes to none of them, the seams are STILL unwired after the unit whose absence was the
  stated reason.

**Every reading agrees on:** M9 owes ### **the machine, the table, the migration, and a CALLABLE
`EC-1` RAISE SEAM** that a caller holding an owner and a source can invoke — that is what
`expire_overdue` is written to hand off to, and it is the half every deferral named. ### **M9 does
NOT edit `observation.py`, `identity_binding_claim.py`, `conflict.py`, `expectation.py`,
`external_effect.py` or `event_inbox.py`** — all six are landed, `CURRENT.md`'s ⛔ table forbids
rebuilding or polishing them, and widening a landed machine's trigger set from inside M9 is M9
answering a question about M5's contract. ### **And the absence of M9 before M9 existed is not a
defect in those units** — it is the deferral each of them recorded. Say plainly, in your report,
whether the seams are wired after M9 and by what; do not wire them by editing someone else's file.

**`M9-AQ-5` — what IS the "freeze" that `EC-1` commits atomically with the raise?**

- **A freeze is required, transactionally.** Entity §15: *"Raising an Exception and freezing/blocking
  the affected work occur in one transaction where applicable."* Machine §4 and §35 repeat it;
  machine §34's writes are *"exception row + (where applicable) entity-freeze + outbox"*; machine §36
  says *"the frozen entity stays blocked until resolved"*; F9 says `ExceptionResolved` *"unblocks the
  frozen entity"*; entity §39 says a `NEEDS_VERIFICATION` Exception *"keeps the entity frozen and the
  commit key held."*
- **Nothing names a freeze mechanism M9 owns.** Entity §38 routes it through the CHECKPOINT — *"an
  open Exception that freezes an entity blocks consequential actions on it"* — and machine §28 says
  *"via `GR-10`/the frozen field"*, where `GR-10` is the CONFLICT rule about a field being
  `conflicting`. The landed freeze representations all belong to other units: M1's `BLOCKED` +
  `blocker_ref`, whose `WI-6` trigger set is ### **`EvidenceMissing`/`ConflictRaised` and contains no
  exception trigger at all**; M4's `approvals.frozen` (`AP-9`), which is the unknown-outcome
  quarantine; M2/M3's `NEEDS_VERIFICATION`/`UNKNOWN_OUTCOME`, which already hold the commit key.
  ### **There is no generic entity-freeze table, and no landed transition an Exception can drive.**

**Every reading agrees on:** ### **NOT EVERY EXCEPTION FREEZES AN ENTITY** — entity §38 states the
materiality condition in terms (*"only those that make a material field non-`consistent`"*); the
freeze condition is ### **stated and RECORDED on the row, never guessed**; the ### **PROJECTION** into
`checkpoint.py`'s existing `NativeClaim`/`ProvenancedFact` types is the landed way a P6 machine
blocks a consequential action, and M7 and M8 both do it ### **without importing the checkpoint**; and
whatever M9 writes for a freezing exception is written in the ### **SAME COMMIT** as the row and its
event, so a persistence failure leaves nothing half-raised. Build that. ### **Do not build a generic
freeze table, do not edit `work_item.py` to add an exception trigger to `WI-6`, do not edit
`approval.py`, and do not edit `checkpoint.py`.** If you conclude the canonical shape genuinely
requires a landed machine to gain a trigger, ### **name the clause, say that it is an M1 change with
an M1 review, and stop before making it.**

**`M9-AQ-6` — does `EC-4` age from `{OPEN, ACKNOWLEDGED}`, or from ANY state?**

- ### **`{OPEN, ACKNOWLEDGED}`.** Machine §14's `EC-4` row, which the acceptance file requires an
  EXACT SET MATCH against (`AC-MACH-901..907`, and the `AC-MACH-000` bijection oracle).
- ### **`any`.** Target spec §12.9's own row reads *"`any` | `TimerFired` | age | `AGEING` →
  `ESCALATED`"*, and `00-conventions.md` says these specifications **derive** from the frozen
  architecture and *"invent nothing"*, which makes the target spec the senior document. Under the
  literal reading a timer could re-age an `ESCALATED` row, or move a `RESOLVED` one — though note
  that §12.9's own *"Expiry: NEVER"* line and registry §4's `RESOLVED (T)` both contradict the second
  half, which is real evidence that the row is a **notational compression** of two transitions (its
  To column holds two states and its Emits column two events) rather than a wider from-set.

**Every reading agrees on:** `OPEN` and `ACKNOWLEDGED` age; `AGEING` escalates; ### **a timer NEVER
resolves** (machine §37) and ### **NOTHING moves a `RESOLVED` row** (registry §4: `(T)`; acceptance
assertion 5: terminal states have no prohibited outgoing transitions). Build the machine's seven
rows, state which reading you implemented, and record the other. **Do not resolve it by widening a
specification.**

### 3.9 The seams that are already built — feed them, do not duplicate them

**The checkpoint (`checkpoint.py`), which you are not editing.** Entity §38 makes the interaction
### **INDIRECT**: an open Exception that freezes an entity makes a material field non-`consistent`,
and step 4 already refuses a `ProvenancedFact` whose `evidence_condition` is not `CONSISTENT` and a
`NativeClaim` whose `conflicting` is true. ### **Demonstrate the seam by projecting M9's own state
into those existing types and showing the existing step 4 refuses** — the `native_projection()` shape
M7 and M8 both ship, which builds the checkpoint's types **without importing the checkpoint**. Do not
create a second gate authority: **P3 remains the gate minter** and **M3 remains the single effect
authority** (`CLAUDE.md` rule 17). **Do not edit `checkpoint.py`.** If you conclude the P3 kernel
must change for M9 to be correct, **say so and stop before changing it.**

**M1's `resolve_decision_ref`, which you IMPORT.** It is the landed `K-1` executor, it is already
imported by M3 for `EF-5`, and M7's `CF-4` uses the same `DECISION_REF_KINDS`. ### **Writing a second
resolver is the defect this seam exists to prevent.** Read its docstring: it explains why the
`AUDIT_EVENT` referent resolves against the canonical **event log** rather than a second audit store,
and why `RULE` refuses today. **Do not edit `work_item.py`**, do not add a name to
`HUMAN_DECISION_EVENTS`, and do not close `P6-D1` or `P6-D4` — see §3.11.

**M1's `FailureDisposition`, which is the landed TRANSIENT/PERMANENT vocabulary.** It is a
caller-supplied enum on `WI-4`/`WI-5`'s guard, not a classifier. ### **There is NO landed classifier
in this repository**, and `M-74` says *"a catch-all base class is NOT a classification."* M9 takes an
enumerated classification and **records** it. **Do not write a function that maps an exception
message, an HTTP status, a vendor error string or a model's opinion to PERMANENT.**

**M13's brake (`brake.py`), which M9 does not touch.** F9's cross-cutting section: *"Sev-0 exceptions
(orphan adapter, cross-tenant breach, rebuild divergence) are produced ### BY F14 DETECTORS and
auto-engage the brake."* Entity §35 and machine §30 say the same — *"via their source detectors."*
`events/registry.md` §11 gives the three detectors their auto-brake scope. ### **THE BRAKE IS THE
DETECTOR'S ACT, AT THE SOURCE. M9's half is that an Exception can CARRY `SEV0`.** And note what is
landed today, because it decides what M9 may assume: P4's orphan-effect detective sweep **does**
auto-engage (`effect_boundary.py`); P5's inbox records a cross-tenant rejection as an observation and
engages nothing; P5's `event_replay.compare_to_live` ### **RETURNS findings and deliberately does not
engage a brake**, and says *"no production caller exists yet."* ### **Do not build F14 detectors, do
not build M14, do not make M9 a cross-tenant / orphan / rebuild-divergence detector, and do not call
`brake.engage` from `exception.py`.**

**The M10 Compensation seam, and the M11/M12 seam behind it.** Entity §21 names *"failed/impossible
compensation"* as a raise cause and machine §27 says compensation is *"n/a directly"*; entity §41
names the repeatedly-overridden rule. ### **M10, M11 AND M12 ARE NOT BUILT AND YOU ARE NOT BUILDING
THEM** — no `compensations` table, no `CM-*`, no `CompensationRequired`, no `policies`, no `rules`, no
`PO-*`, no `RU-*`, and ### **no fabricated completed Compensation.**

**The foreign keys entity §18 names, and what exists to point at.** §18 names three — `owner_id`,
`source_ref`, `decision_ref`. **Follow M6's, M7's and M8's precedent exactly: build the foreign keys
whose targets exist; carry the others as constrained, NOT-NULL-where-the-CHECK-requires-it columns
with a kind discriminator and no foreign key into a table this unit does not own.**

| Column | Target | Exists? |
|---|---|---|
| `owner_id` | `tenant_humans` (M1) | ✅ **build the FK** |
| `acknowledged_by` / the human behind a `decision_ref` | `tenant_humans` (M1) | ✅ **build the FK** — M7's `decision_human_id` is the precedent |
| `source_ref` → an observation / claim / conflict / expectation / work item / pipeline / grant / approval | those tables exist | ⚠️ `M9-AQ-3` — **the `conflict_parties` mirror-column shape** |
| `source_ref` → a compensation / evidence / rule / policy / parked reference | M10 / P7 / M12 / M11 / a composite key | ❌ **no FK; discriminator only, and record it** |
| `decision_ref` → an `audit_events` human-decision row | resolved by **M1's resolver** against the canonical event log | ⚠️ resolve it, do not FK it — M7 and M3 both do exactly this |
| `decision_ref` → an `ACTIVE` `rule_id` | `rules` | ❌ **M12; refuses today (`P6-D4`)** |
| `entity_ref` → a load / document / movement projection | freight domain | ❌ **P9+, no table** |

**Do not build `evidence`, `compensations`, `policies` or `rules` to satisfy one.** If you conclude
the canonical shape genuinely requires one of those tables to point at — which would be building
another unit — **name the clause and stop.**

### 3.10 The F14 tripwires — which is yours

- ### **`IllegalTransitionAttempted` is MANDATORY and is yours.** `GR-1` requires it on every illegal
  `(state, trigger)`, to **audit and security**, and M5, M6, M7 and M8 all already emit it. The four
  shapes machine §15 names by hand are the ones that matter most, and `EC-3`-from-`AGEING`,
  `EC-4`-from-`ESCALATED` and `EC-7`-from-`AGEING` are three more the from-sets make illegal
- **`CrossTenantAccessAttempted` is the inbox's**, not M9's. Fail closed; do not mint it
- **`OrphanAdapterInvocation` is the effect boundary's detector**, and it already auto-engages a
  brake there. Not M9's
- **`ProjectionRebuildDiverged` is replay's**, and `event_replay.py` deliberately returns rather than
  acts. Not M9's
- **`ProvenanceStrengtheningAttempted` is NOT yours.** F14 names *M5/M6* as its producers, and
  `CURRENT.md` scopes the emission half to Implementation Phase 7 by name
- **`OwnerAssertedOverwriteAttempted` is M6's**
- If you conclude M9 must emit one of the five that are not its own, name the clause, say that it
  contradicts F14 or `CURRENT.md`, and **stop** — that is §3.8 behaviour, not a judgement call

### 3.11 `V10` stays open, and you do not answer it

Entity §45 and machine §43 both name one open validation item, and both say ### **it is NOT a block.**

**`V10` — per-lane exception ageing and escalation thresholds.** Target spec §32 and ADR-008's `V3`:
the fail-closed default is ### **ages · escalates · NEVER EXPIRES.**

- ### **DO NOT CHOOSE A BUSINESS AGEING THRESHOLD.** *"4 hours for a Sev-0"*, *"48 hours before
  escalation"*, *"one lane escalates faster than another"* — every one of those is a customer's
  operating policy, and inventing one is inventing a product decision with a number on it
- the **mechanism** of `EC-4` and `EC-5` is complete and must be built and exercised: a threshold
  reached through a **durable timer**, an explicit transition, its registered event, and a retained
  row that still names its human. ### **The threshold is a caller-supplied parameter with no default
  that means anything**, and a test or probe supplies its own
- ### **THE FAIL-CLOSED BEHAVIOUR IS THE PART YOU MUST BUILD: it AGES and ESCALATES rather than
  disappearing.** Never a silent close, never a delete, never a sweep

**And two debt rows name M9 without being M9's to close.** `P6-D1` records that `K-1` names
`HumanResolved`, which is not among the canonical contracts, and that `ExceptionResolved` *"is the
canonical name for what looks like the same fact"* — with the disposition that ### **"M9 owns that
determination."** Report on it: say what the two names mean mechanically and what adding
`ExceptionResolved` to `HUMAN_DECISION_EVENTS` would imply (an Exception closed by a `decision_ref`
naming another Exception's closure). ### **Do not amend `K-1`, do not add a name to
`HUMAN_DECISION_EVENTS`, and do not edit `work_item.py`** — amending a protected specification is
exactly what that debt row says a unit may not do on its own. `P6-D3` records that M1's
`WorkItemMachine.ownerless()` detector SEES an ownerless Work Item but that *"the Sev-0 raise…
are M9 and M2"*. ### **M9's half is that a raise seam EXISTS which such a detector could call. Wiring
M1's detector to it is an M1 change with an M1 review** — `M9-AQ-4` behaviour.

---

## 4. What you must produce

Follow the existing P6 naming conventions — `work_item.py`/`phase6_work_items.py`,
`pipeline_instance.py`/`phase6_pipeline_instances.py`,
`external_effect.py`/`phase6_external_effects.py`, `approval.py`/`phase6_approvals.py`,
`observation.py`/`phase6_observations.py`,
`identity_binding_claim.py`/`phase6_identity_binding_claims.py`,
`conflict.py`/`phase6_conflicts.py`, `expectation.py`/`phase6_expectations.py`. These exact paths are
what the permanent verification scenario `p6_m9_exception` looks for; a different name is a scenario
failure, not a style preference. If you believe a different name is genuinely better, **say so and
stop** rather than renaming unilaterally.

| Path | What it is |
|---|---|
| `src/freight_recon/exception.py` | the machine (follows `conflict.py` and `expectation.py`) |
| `src/freight_recon/migrations/phase6_exceptions.py` | the schema change (follows `phase6_expectations.py`) |
| `eval/tests/test_phase6_exception.py` | the acceptance and hostile battery |
| `scripts/probe_phase6_exception.py` | the deterministic narrative probe |
| `scripts/mutate_phase6_exception.py` | the mutation battery (follows `mutate_phase6_expectation.py`) |

### **THE MODULE IS `exception.py`, AND IT DEFINES NO CLASS CALLED `Exception`.** The module name
follows the entity's canonical name exactly as the eight before it do, and it shadows nothing —
`from .exception import M9Machine` is an ordinary relative import. A **class** named `Exception`
inside it would shadow the builtin and break `except Exception:` in the same file, which is a real
defect rather than a style preference.

### **NAME THE MACHINE'S OWN TYPES THE WAY `conflict.py` NAMES ITS OWN.** M7 ships `M7Machine`,
`CfState`, `CfKind`, `Conflict`, `Party`, `TransitionResult` — so the only identifiers in that file
beginning `Conflict` + a capital are the five REGISTERED F7 event names, and the scenario's
unregistered-name sweep reads exactly what it is for. Do the same here: ### **`M9Machine`,
`EcState`, `EcSeverity`, `EcSubStatus`, `EcRecord`, `TransitionResult`.** ### **An identifier
beginning `Exception` followed by a capital letter that is not one of the six registered F9 event
names fails the sweep** — and that is the point, because `ExceptionAutoClosed` is exactly what an
invented seventh event would be called.

Wire the migration into `schema.py` and the P2 migration path the way `phase6_expectations.py` is
wired, so a freshly created canonical database and a migrated one build to the same shape and the
readiness oracle DERIVES the contract from the DDL rather than from a second list.
`schema_readiness_problems` must still return `[]` on a freshly created canonical database with
foreign keys enabled and verified, and the tenant-first table partition in `CURRENT.md` gains
exactly the rows your migration adds.

### The probe's interface

`scripts/probe_phase6_exception.py` must support:

- **no arguments** — run every case; exit `0` only if every one behaved as specified
- `--list-cases` — print the case names, one per line, and exit `0`
- `--list-dimensions` — print every dimension flag and every fault name, and exit `0`
- `--case <case>` — run exactly one case and exit `0` / non-zero

`--case` is what makes M9 testable by Product Driver's dynamic scenario generator: a generated
scenario may not author shell, so a focused, safe, argument-only entry point is the *only* way it can
compose new situations out of M9's real behaviour. Take the interface seriously.

**The cases, by name.** One per canonical obligation. A family missing here is a family the
generator cannot reach and you were never asked to build.

```
raise-creates-open-with-a-named-human-owner
an-exception-cannot-be-raised-without-an-owner
an-ownerless-exception-is-structurally-impossible
the-owner-is-an-active-human-of-this-tenant
an-offboarded-human-cannot-own-a-new-exception
a-model-cannot-own-an-exception
raise-records-severity-and-the-source-that-raised-it
an-exception-cannot-be-raised-without-a-severity
an-exception-cannot-be-raised-without-a-source-ref
the-source-kind-is-a-closed-vocabulary
a-permanent-auth-failure-raises-immediately-with-zero-retries
a-permanent-config-failure-raises-immediately-with-zero-retries
a-transient-failure-is-not-a-permanent-classification
the-failure-classification-is-supplied-never-inferred-from-a-message
an-authenticated-human-acknowledges-the-exception
acknowledgement-records-the-actor
acknowledgement-proves-seen-not-resolved
a-model-cannot-acknowledge-an-exception
a-system-actor-cannot-acknowledge-an-exception
an-acknowledged-exception-still-ages
resolution-requires-a-decision-ref
closure-without-a-decision-ref-is-structurally-impossible
a-decision-ref-that-resolves-to-nothing-is-refused
a-decision-ref-naming-a-non-human-decision-event-is-refused
a-decision-ref-recorded-by-automation-is-refused
a-model-can-never-resolve-an-exception
an-escalated-exception-resolves-through-ec-6
resolving-from-ageing-is-an-illegal-transition
resolved-is-the-only-terminal-state
a-resolved-exception-is-retained-never-deleted
inactivity-never-closes-an-exception
autoclose-is-an-illegal-transition
an-exception-never-expires
an-exception-cannot-be-outlived
no-sweep-or-reaper-closes-an-exception
a-timer-can-age-or-escalate-but-never-resolve
an-open-exception-ages-through-a-durable-timer
an-acknowledged-exception-ages-through-a-durable-timer
ageing-escalates-through-a-durable-timer-not-a-sweep
ageing-and-escalated-remain-human-owned
the-ageing-threshold-is-caller-supplied-not-a-business-default
ageing-an-escalated-exception-is-illegal
nothing-moves-a-resolved-exception
restart-re-fires-the-ageing-timer
restart-preserves-the-open-exception
restart-after-escalation-reaches-the-canonical-state
a-redelivered-timer-is-a-no-op
severity-change-is-a-field-mutation-not-a-lifecycle-state
severity-change-records-previous-and-new-severity-and-who
severity-change-requires-a-reason
a-model-cannot-change-severity
severity-is-sev0-sev1-or-sev2-and-nothing-else
changing-the-severity-of-an-ageing-exception-is-illegal
a-sev0-exception-engages-no-brake-from-inside-m9
the-five-canonical-states-and-no-sixth
sub-status-is-a-field-never-a-lifecycle-state
there-is-no-cancelled-expired-or-timed-out-state
a-retracted-cause-still-requires-an-event-and-a-decision-ref
a-freezing-exception-blocks-consequential-actions-on-the-entity
not-every-exception-freezes-an-entity
raise-and-freeze-commit-together-where-applicable
a-persistence-failure-leaves-no-half-raised-exception
state-and-event-co-commit
resolution-unblocks-the-frozen-entity
m9-mints-no-gate-decision
an-exception-is-an-input-to-the-checkpoint-never-a-gate
a-redelivered-raise-through-the-inbox-is-a-no-op
the-open-exception-dedup-index-is-optional-and-recorded
concurrent-raises-are-serialized-by-the-database
occ-on-exception-version
a-stale-version-cannot-overwrite-newer-state
replay-reconstructs-the-open-exception
replay-rebuilds-the-current-severity-from-the-recorded-events
replay-does-not-read-severity-from-the-current-row
replay-keeps-a-frozen-entity-blocked
replay-can-never-manufacture-resolution-authority
replay-creates-no-new-authority-and-no-effect
exceptions-still-raise-under-a-brake
m9-engages-no-brake-and-narrows-none
tenant-isolation
cross-tenant-identical-source-ref
cross-tenant-owner-fails-closed
cross-tenant-source-fails-closed
cross-tenant-decision-ref-fails-closed
cross-tenant-queue-read-fails-closed
inbox-idempotency
database-invariants
malformed-exception-fails-closed
an-illegal-transition-persists-nothing-and-is-recorded
the-m1-work-item-machine-is-not-rewritten
the-m3-effect-authority-is-unchanged
the-m5-observation-machine-is-not-rewritten
the-m7-conflict-machine-is-not-rewritten
the-m8-expectation-machine-is-not-rewritten
m10-m11-and-m12-are-not-built
```

### The mutation axis

M9 ships dark — no oversight surface, no queue UI, no notifier, no live channel — and the driver's
only external concurrency primitive is HTTP. **Every ordering, concurrency, timing, duplication,
crash and replay variation for M9 has to be reachable through this probe's arguments or it is not
reachable at all.**

The probe must therefore accept, composable with `--case`:

```
--concurrency 1-8      how many raisers, acknowledgers or timers race one exception
--delay-ms 0-5000      timing skew between them
--repeat 1-5           duplicate raise / redelivered timer pressure
--tenants 1-3          isolation pressure
--age-ms 0-86400000    how far the durable timer is advanced: the ageing threshold, then escalation
--severity <sev>       the severity the exception carries or moves to: SEV0|SEV1|SEV2
--actor <kind>         WHO attempts the transition: human|system|model|detector
--decision-ref <kind>  the resolution authority offered: valid|absent|unresolvable|non-human|automated|cross-tenant
--freeze <mode>        whether the source condition freezes material work: material|immaterial|none
--seed <int>           deterministic interleaving; the same seed reproduces the failure
--inject <fault>       the closed fault set below
```

### **`--actor` AND `--decision-ref` ARE THIS UNIT'S OWN TWO AXES**, the way `--coverage` was M8's.
M9's entire safety property is a question about **who may act**: a human acknowledges, a human
resolves, a timer ages and never resolves, a model does none of it. And `--decision-ref absent` is
the value that decides whether closure by silence is possible at all — which is `F-30`, `GR-14` and
`AC-MACH-903` in one flag.

The **closed fault vocabulary**, every member named by the canonical machine, the entity
specification, the target specification, an ADR or the event registry:

```
raise                        ownerless-raise             model-owner
offboarded-owner             cross-tenant-owner          missing-severity
missing-source-ref           cross-tenant-source         invented-source-kind
permanent-auth-failure       permanent-config-failure    transient-failure
inferred-permanence          retry-permanent             acknowledge
model-acknowledge            system-acknowledge          resolve
resolve-without-decision-ref unresolvable-decision-ref   non-human-decision-ref
automated-decision-ref       cross-tenant-decision-ref   model-resolve
resolve-from-ageing          autoclose                   inactivity-close
expire-exception             sweep-close                 timer-resolve
age                          escalate                    age-escalated
age-resolved                 severity-change             severity-change-no-reason
severity-change-no-previous  model-severity-change       invented-severity
severity-change-ageing       sub-status-as-state         sixth-state
cancel-exception             freeze                      no-freeze
freeze-split-commit          unfreeze-without-resolution persistence-failure
gate-mint                    brake-engage                duplicate-raise
concurrent-raise             redelivered-raise           redelivered-timer
occ-exception                stale-version               restart-before-ageing
restart-after-escalated      replay                      replay-severity-from-row
replay-manufacture-decision  cross-tenant-queue          malformed-exception
reorder-stream               delete-exception
```

**The vocabulary is CLOSED and BOUNDED. This is not fuzzing.** An unknown fault, or a value outside
the stated range, must be **REFUSED** with a non-zero exit (`2`) and a readable `unknown fault`
message — never a stack trace. Four negative controls are asserted by the permanent scenario:

- `--inject not-a-real-fault` — proves the closure is real
- `--inject reopen-exception` — **refused**, because entity §27 says ### **"Reopening rules. N/A (a
  recurrence is a new Exception)"** and machine §24 says the same. A probe that accepted it would be
  producing passing evidence for a transition the corpus states does not exist
- `--inject correct-exception` — **refused**, because entity §23 and machine §25 both say
  ### **"Correction rules. N/A."** Correction is precisely the tidy-looking thing a build session
  adds, and it would let a wrong severity or a wrong owner be edited out of history
- `--inject supersede-exception` — **refused**, because entity §24 and machine §26 both say
  ### **"Supersession rules. N/A"**, there is no `SUPERSEDED` state in registry §4's M9 row, and no
  `ExceptionSuperseded` event is registered anywhere

Note the contrast with `--inject autoclose`, `--inject expire-exception`, `--inject timer-resolve`,
`--inject resolve-from-ageing`, `--inject cancel-exception`, `--inject sixth-state` and
`--inject sweep-close`, which **are** in the vocabulary: those name shapes the corpus defines **as
ILLEGAL** (machine §15, and the `EC-3`/`EC-4`/`EC-6`/`EC-7` from-sets), so the machine must be seen to
REFUSE them under `GR-1` — raising, persisting nothing, and recording `IllegalTransitionAttempted`.
A fault refused as *unknown* and a fault refused as *illegal* are two different proofs, and M9 owes
both.

### The probe's output contract

The probe must print these literals, verbatim. They are the contract between this file and the
permanent scenario, and they are matched as substrings.

```
behaviours as specified, 0 wrong
AN EXCEPTION IS SOMETHING THAT NEEDS A HUMAN
EVERY EXCEPTION REACHES A NAMED HUMAN OWNER AND IS NEVER CLOSED BY SILENCE
AN EXCEPTION IS NOT AN ERROR LOG, AN ALERT OR AN ISSUE TRACKER ROW
AN EXCEPTION HAS A NAMED HUMAN OWNER FROM CREATION
AN OWNERLESS EXCEPTION IS STRUCTURALLY IMPOSSIBLE
THE OWNER IS AN ACTIVE HUMAN OF THIS TENANT
A MODEL IS NOT A HUMAN AND MAY NOT OWN AN EXCEPTION
THE RAISE RECORDS ITS SEVERITY AND THE SOURCE THAT RAISED IT
THE SOURCE KIND IS A CLOSED VOCABULARY
A PERMANENT AUTH OR CONFIG FAILURE RAISES IMMEDIATELY WITH ZERO RETRIES
A TRANSIENT FAILURE IS NOT A PERMANENT CLASSIFICATION
THE FAILURE CLASSIFICATION IS SUPPLIED, NEVER INFERRED FROM A MESSAGE
AN AUTHENTICATED HUMAN ACKNOWLEDGES AN EXCEPTION
ACKNOWLEDGEMENT RECORDS THE ACTOR
ACKNOWLEDGEMENT PROVES IT WAS SEEN, NOT THAT IT WAS RESOLVED
A MODEL CAN NEVER ACKNOWLEDGE AN EXCEPTION
AN ACKNOWLEDGED EXCEPTION IS STILL OPEN WORK AND STILL AGES
RESOLUTION REQUIRES A decision_ref THAT RESOLVES
CLOSURE WITHOUT A decision_ref IS STRUCTURALLY IMPOSSIBLE
AN EXCEPTION CLOSED WITHOUT A DECISION IS NOT CLOSED, IT IS FORGOTTEN
A decision_ref THAT REFERENCES NOTHING IS NOT A decision_ref
A decision_ref RECORDED BY AUTOMATION IS NOT A HUMAN DECISION
A MODEL CAN NEVER RESOLVE OR AUTO-CLEAR AN EXCEPTION
AN ESCALATED EXCEPTION RESOLVES THROUGH EC-6 WITH A decision_ref
RESOLVING FROM AGEING IS AN ILLEGAL TRANSITION
RESOLVED IS THE ONLY TERMINAL STATE
A RESOLVED EXCEPTION IS RETAINED, NEVER DELETED
INACTIVITY NEVER CLOSES AN EXCEPTION
AUTOCLOSE IS AN ILLEGAL TRANSITION
AN EXCEPTION NEVER EXPIRES AND CANNOT BE OUTLIVED
NO SWEEP, REAPER OR SCAN CLOSES AN EXCEPTION
A TIMER MAY AGE OR ESCALATE; A TIMER NEVER RESOLVES
AN OPEN EXCEPTION AGES THROUGH A DURABLE TIMER
AGEING ESCALATES THROUGH A DURABLE TIMER, NEVER A SWEEP
AGEING AND ESCALATED REMAIN HUMAN-OWNED
THE AGEING THRESHOLD IS CALLER-SUPPLIED, NOT A BUSINESS DEFAULT
NOTHING MOVES A RESOLVED EXCEPTION
A RESTART RE-FIRES THE AGEING TIMER
A RESTART LEAVES THE OPEN EXCEPTION OPEN
A REDELIVERED TIMER IS A NO-OP
A SEVERITY CHANGE IS A FIELD MUTATION, NOT A LIFECYCLE STATE
A SEVERITY CHANGE RECORDS THE PREVIOUS SEVERITY, THE NEW ONE AND WHO CHANGED IT
A SEVERITY CHANGE REQUIRES A REASON
A MODEL CAN NEVER CHANGE SEVERITY
SEVERITY IS SEV0, SEV1 OR SEV2 AND NOTHING ELSE
A SEV0 EXCEPTION ENGAGES NO BRAKE FROM INSIDE M9
THE FIVE CANONICAL STATES ARE THE WHOLE LIFECYCLE
sub_status IS A FIELD, NEVER A LIFECYCLE STATE
THERE IS NO CANCELLED, EXPIRED OR TIMED_OUT STATE
A RETRACTED CAUSE STILL REQUIRES AN EVENT AND A decision_ref
A FREEZING EXCEPTION BLOCKS CONSEQUENTIAL ACTIONS ON THE ENTITY
NOT EVERY EXCEPTION FREEZES AN ENTITY
THE RAISE AND THE FREEZE COMMIT TOGETHER WHERE APPLICABLE
A PERSISTENCE FAILURE LEAVES NO HALF-RAISED EXCEPTION
THE STATE ROW AND ITS EVENT COMMIT TOGETHER
RESOLUTION UNBLOCKS THE FROZEN ENTITY
M9 MINTS NO GATE DECISION
AN EXCEPTION IS AN INPUT TO THE CHECKPOINT AND NEVER A GATE
A REDELIVERED RAISE THROUGH THE INBOX IS A NO-OP
THE OPEN-EXCEPTION DEDUP INDEX IS OPTIONAL, AND THIS BUILD RECORDS ITS CHOICE
A LOST UPDATE ON AN EXCEPTION IS REFUSED
A STALE VERSION NEVER OVERWRITES NEWER STATE
REPLAY RECONSTRUCTS THE OPEN EXCEPTION
REPLAY REBUILDS THE CURRENT SEVERITY FROM THE RECORDED EVENTS
REPLAY NEVER READS SEVERITY FROM THE CURRENT ROW
REPLAY KEEPS A FROZEN ENTITY BLOCKED
REPLAY CAN NEVER MANUFACTURE RESOLUTION AUTHORITY
replay: 0 new authority, 0 external effects, 0 decision_refs minted, 0 state flips
EXCEPTIONS STILL RAISE UNDER A BRAKE
M9 ENGAGES NO BRAKE AND NARROWS NONE
THE SAME SOURCE IN TWO TENANTS ARE TWO ISOLATED EXCEPTIONS
A CROSS-TENANT OWNER FAILS CLOSED
A CROSS-TENANT SOURCE FAILS CLOSED
A CROSS-TENANT decision_ref FAILS CLOSED
A CROSS-TENANT QUEUE READ FAILS CLOSED
AN ILLEGAL TRANSITION PERSISTS NOTHING AND IS RECORDED
THE DATABASE ENFORCES THE EXCEPTION INVARIANTS
A LEGACY DATABASE MIGRATES TO THE CANONICAL EXCEPTION SHAPE
THE M1 WORK ITEM MACHINE IS UNCHANGED
THE M3 EFFECT AUTHORITY IS UNCHANGED
THE M5 OBSERVATION MACHINE IS UNCHANGED
THE M7 CONFLICT MACHINE IS UNCHANGED
THE M8 EXPECTATION MACHINE IS UNCHANGED
THE M10, M11 AND M12 MACHINES ARE NOT BUILT
mutants caught
```

And it must **never** print any of these. Each is a sentence printed only when the thing M9 exists to
prevent has just happened, and any one of them anywhere in the run is the whole unit failing:

```
### EXCEPTION RAISED WITHOUT AN OWNER ###             ### OWNERLESS EXCEPTION CREATED ###
### A MODEL OWNED AN EXCEPTION ###                    ### AN OFFBOARDED HUMAN OWNED AN EXCEPTION ###
### CROSS-TENANT OWNER ACCEPTED ###                   ### CROSS-TENANT SOURCE ACCEPTED ###
### CROSS-TENANT decision_ref ACCEPTED ###            ### CROSS-TENANT QUEUE READ ###
### EXCEPTION CLOSED WITHOUT A DECISION ###           ### CLOSURE BY SILENCE ###
### INACTIVITY CLOSED AN EXCEPTION ###                ### AUTOCLOSE CLOSED AN EXCEPTION ###
### EXCEPTION EXPIRED ###                             ### EXCEPTION OUTLIVED ###
### SWEEP CLOSED AN EXCEPTION ###                     ### REAPER DELETED AN EXCEPTION ###
### EXCEPTION DELETED ###                             ### TIMER RESOLVED AN EXCEPTION ###
### MODEL RESOLVED AN EXCEPTION ###                   ### MODEL ACKNOWLEDGED AN EXCEPTION ###
### MODEL CHANGED SEVERITY ###                        ### UNRESOLVABLE decision_ref ACCEPTED ###
### AUTOMATED ACTOR PASSED AS A HUMAN DECISION ###    ### AGEING EXCEPTION RESOLVED DIRECTLY ###
### SEVERITY CHANGE BECAME A LIFECYCLE STATE ###      ### PREVIOUS SEVERITY LOST ###
### SEVERITY CHANGE WITHOUT A REASON ###              ### REPLAY REBUILT SEVERITY FROM THE CURRENT ROW ###
### UNREGISTERED SEVERITY MINTED ###                  ### UNREGISTERED STATE MINTED ###
### sub_status BECAME A LIFECYCLE STATE ###           ### SIXTH LIFECYCLE STATE MINTED ###
### RAISE AND FREEZE SPLIT ACROSS COMMITS ###         ### HALF-RAISED EXCEPTION PERSISTED ###
### EVENT WITHOUT ITS STATE ###                       ### STATE WITHOUT ITS EVENT ###
### FROZEN ENTITY UNBLOCKED WITHOUT A RESOLUTION ###  ### EVERY EXCEPTION FROZE AN ENTITY ###
### M9 MINTED A GATE DECISION ###                     ### EXCEPTION AUTHORIZED AN ACTION ###
### M9 ENGAGED A BRAKE ###                            ### RESOLVED EXCEPTION MOVED ###
### STALE VERSION OVERWROTE NEWER STATE ###           ### REPLAY MANUFACTURED RESOLUTION AUTHORITY ###
### REPLAY MINTED AUTHORITY ###                       ### DOWNSTREAM EFFECT DURING REPLAY ###
### TIMER LOST ACROSS RESTART ###                     ### EXCEPTION LOST ACROSS RESTART ###
### PERMANENT FAILURE RETRIED ###                     ### PERMANENCE INFERRED FROM A MESSAGE ###
### M1 WORK ITEM ROW REWRITTEN BY M9 ###              ### M5 OBSERVATION ROW REWRITTEN BY M9 ###
### M7 CONFLICT ROW REWRITTEN BY M9 ###               ### M8 EXPECTATION ROW REWRITTEN BY M9 ###
### M3 EFFECT SEAM REWRITTEN ###                      ### M10 EVENT MINTED ###
### COMPENSATION FABRICATED ###                       ### CANCELLED STATE MINTED ###
```

Also never: `### MISS ###`, `### NOT REFUSED`, `### WRONGLY REFUSED`, `### WRONG REFUSAL`.

### The mutation battery

`scripts/mutate_phase6_exception.py` proves that the load-bearing guards **can fail**. A guard never
seen to fail is a decoration, and a mutation that does not reintroduce the real defect proves
nothing — **verify each mutant actually applies and actually misbehaves before you believe any
result.** At minimum, mutate:

- **the owner requirement dropped from creation** — the entity §16 CHECK / FK, `I1`, `AC-SAFE-028`
- **an owner from another tenant permitted** — the tenant-consistent FK
- **a sixth lifecycle state added** — the entity §12 / registry §4 CHECK vocabulary
- **a `sub_status` promoted to a lifecycle state** — the machine header's whole point
- **`RESOLVED` allowed with no `decision_ref`** — drop the entity §16 CHECK
- **the `decision_ref` resolver weakened to a non-null check** — `K-1`, `AC-SAFE-024`: the difference
  between *"there is a string"* and *"it resolves to a human decision"*
- **a model permitted to resolve** — the `[C-6]`/`GR-7`/entity §35 guard
- **an inactivity `AutoClose` added** — the target spec §12.9 illegal row
- **an expiry added** — entity §26's *"NEVER"*
- **a timer permitted to resolve** — machine §37's *"never a resolution timer"*
- **the durable timer replaced with an in-memory sleep or a background sweep** — `M-36`
- **`previous_severity` dropped from the severity event** — F9's stated reason
- **replay recomputing severity from the current row** instead of from the recorded events
- **the tenant weakened out of the primary key or the queue index** — `[C-1]`
- **the raise/freeze transaction split into two commits** — entity §15, `[C-2]`, `GR-2`
- **an invented `ExceptionCancelled` event or `CANCELLED` state minted** — `M9-AQ-2`
- **an M10/M11/M12 table or event created** — the unauthorized neighbouring machine
- **M9 made a gate-decision minter** — the `CLAUDE.md` rule 17 boundary
- **M9 made a brake engager** — the F9 / §11 detector boundary
- **a PERMANENT failure retried before raising** — `L-D`, `M-74`, entity §43(d)
- **permanence inferred from an error message** — the classifier that must not exist
- **the ship-dark posture weakened** — a production importer of `exception`

Use the safe in-memory save/restore harness the way `mutate_phase6_expectation.py` does.
### **Never use `git checkout`, `git restore`, `git stash` or `git clean` to undo a mutation.**
Doing so once destroyed unrecoverable uncommitted work in this repository. Purge `__pycache__`:
restoring a `.py` is not restoring behaviour.

The mutation battery must **not** import the exception machine — mutate text and shell out to
pytest, the way `mutate_phase6_expectation.py` does.

### Ship dark

M9 ships dark, exactly as M1 through M8 do.

- **Nothing under `src/freight_recon/` may import `exception`.** The only file under `scripts/` that
  may is `probe_phase6_exception.py`
- **zero production importer, no live integration, no new API, button or channel, and no outbound
  effect path.** M9's product form is ### **an exception queue with owners, notifications and an
  MTTR dashboard** — so that product is precisely the thing that must not arrive with it. Nothing may
  join the exception machine to `ingestion`, `email_adapter`, `imap_mailbox`, `email_triage`,
  `inbox_brain`, `extraction`, `browser_use_adapter`, `cdp_readonly`, `tms_adapter`, `slack_adapter`,
  `alert_channel`, `channels`, `delivery`, `delivery_dispatch`, `action_callback`, `ops_control`,
  `follow_up`, `operator_console`, `review`, `mailbox_intake` or any other inbound or outbound
  surface
- ### **NO OVERSIGHT QUEUE, UI, DASHBOARD, NOTIFIER, PAGER, ON-CALL ROTATION OR MTTR EMITTER SHIPS
  WITH M9** (§3.6)
- **M9 must not make Gmail, Slack, TMS, the browser, accounting or any other product surface start
  using Exception yet**
- **no live effect is enabled**, and the production `GateRegistry` stays EMPTY. M9 authorizes
  nothing: an Exception is an **INPUT** to the checkpoint and can never mint a gate decision
- the **checkpoint stays the only thing that mints a gate decision**, and **M3 stays the single
  effect authority**
- **no autonomous operation is enabled**, and ### **no brake is engaged, widened or narrowed by M9**
- if canon genuinely requires a dark seam, **name the clause that requires it** before you build it,
  and keep the seam inert

### Tests

`pytest-canonical.ini` **no longer exists.** The 2026-08 engineering-process simplification folded it
into `[tool.pytest.ini_options]` in `pyproject.toml`, and CI runs
`python -m pytest -q -p no:cacheprovider`. Do not reintroduce a second pytest configuration and do
not pass `-c pytest-canonical.ini` anywhere.

Write the adversarial tests entity §44 names, by those names:
`test_exception_closure_requires_decision_ref`, `test_inactivity_never_closes_an_exception`,
`test_ownerless_exception_impossible`,
`test_auth_failure_raises_exception_immediately_zero_retries`,
`test_ageing_escalates_via_durable_timer_not_sweep`, `test_model_cannot_resolve_an_exception`.

And the per-transition tests machine §14 names, by those names:
`test_ec_raise_requires_owner`, `test_ec_ack`, `test_ec_close_requires_valid_decision_ref`,
`test_ec_ages`, `test_ec_escalates`, `test_ec_escalated_resolves`,
`test_ec_severity_change_is_field_not_state`.

And the F9 event-contract tests the family file names, by those names:
`test_ev_exceptionraised_owned`, `test_ev_exception_ack`, `test_ev_exception_ageing`,
`test_ev_exception_escalated`, `test_ev_exceptionseveritychanged_rebuilds_the_current_severity`,
`test_ev_exceptionresolved_valid_decision_ref`.

And `K-1`'s own named test, which M1 already carries and which M9 must not weaken:
`test_decision_ref_must_resolve_to_a_human_decision_event_or_active_rule`.

### Regressions you may not break

Re-run them on the tree you are finishing with, not the one you started from:

- **P3** — the checkpoint kernel, the claim CAS, step order, the brake, the fingerprint, the
  checkpoint matrix
- **P4** — the import gate, the adapter boundary, the governed write route
- **P5** — the event transport, replay isolation, ### **durable timers** (`EC-4` and `EC-5` ride
  them), and the canonical event contracts: M9 uses six already-registered F9 names and mints none of
  its own, so `test_p5_event_contracts.py` and `test_p5_canonical_event_mint.py` are load-bearing
  here rather than incidental
- **M1, M2, M3, M4, M5, M6, M7, M8** — their acceptance batteries, and M5's, M6's, M7's and M8's own
  deterministic probes, which must still report `behaviours as specified, 0 wrong` with M9's table in
  the schema. ### **M1's matters most**: M9 IMPORTS its `resolve_decision_ref` and its
  `tenant_humans` FK, so a change that made M1's resolver more permissive to suit M9 would be M1
  rewritten from inside M9

---

## 5. Do not

- begin **M10–M13** — in particular do not implement the **M10 Compensation** machine, the **M11
  Policy** machine, the **M12 Rule** registry or the **M13 Brake** machine
- begin **P7 or later**, including P7's **provenance and evidence platform** (§2)
- build the **Evidence** entity, the Evidence Store, `evidence` spans, content-addressed retention or
  artifact storage
- resolve **V10**, or choose a business ageing or escalation threshold (§3.11)
- close **`P6-D1`** or **`P6-D4`**, add a name to `HUMAN_DECISION_EVENTS`, or amend **`K-1`** (§3.11)
- build an **oversight queue, exception-queue UI, dashboard, notifier, pager, on-call rotation,
  escalation-message template or MTTR metric emitter** (§3.6)
- build **F14 detectors**, an orphan-adapter detector, a cross-tenant breach detector or a
  rebuild-divergence detector, or **engage, widen or narrow a brake** (§3.9, §3.10)
- build a **failure classifier** that infers PERMANENT from a message, a status code or model output
  (§3.9)
- build freight workflows, invoice automation, AP/AR workflows, carrier sourcing, dispatch, tracking
  or cargo claims
- build a **Slack**, **Gmail**, **email**, **IMAP**, **portal**, **browser** or **TMS** product
  surface or integration, or **any alerting, incident-management, ticketing or exception-queue UI**
- adopt, refactor, wire in or replace `email_triage.py`, `ingestion.py`, `extraction.py`,
  `inbox_brain.py`, `follow_up.py`, `alert_channel.py`, `ops_control.py`, `review.py`,
  `operator_console.py`, `mailbox_intake` routing fields, `action_callback.py` or any other legacy
  surface
- enable **live production effects**, **production integrations** or **production autonomy**
- **redesign P0, P1, P2, P3, P4 or P5.** They are COMPLETE. If M9 genuinely needs one of those
  surfaces changed, say so and stop **before** changing it
- weaken **P3, P4 or P5**, or edit `checkpoint.py`
- introduce a **second effect authority**, a **second checkpoint**, a **second timer mechanism** or a
  ### **second `decision_ref` resolver** — the checkpoint is the only thing that mints a gate
  decision, M3 is the only thing that claims a grant, P5's `event_timers.py` is the only durable
  timer, and M1's `resolve_decision_ref` is the only `K-1` executor
- rebuild or polish **M1, M2, M3, M4, M5, M6, M7 or M8**. They are landed. Their recorded residuals
  are debt rows, and a debt row is a complete deliverable. In particular **do not edit
  `work_item.py`** (§3.9), **do not edit `observation.py`**, **do not edit
  `identity_binding_claim.py`**, **do not edit `conflict.py`**, **do not edit `expectation.py`**,
  **do not edit `external_effect.py`**, **do not edit `approval.py`** and **do not edit
  `event_inbox.py`** (§3.8 `M9-AQ-4`)
- rework the **P3/P4 one-connection-per-thread concurrency correction** at `d70a4e7`
- resolve unrelated **P6 debt**, and in particular do **not** fix **`P6-D40`** unless a real guard in
  it mechanically blocks this unit
- start a **legacy cleanup campaign**, a **broad documentation cleanup**, or remediate nonblocking
  debt merely because it exists
- push, publish or deploy anything

**If a tiny pre-existing defect directly prevents M9 verification**, you may fix the **smallest
blocking prerequisite** — and you must **identify it explicitly**, say why M9 could not be verified
without it, and keep the fix minimal.

### Known non-blocking items — do not turn these into campaigns

`P6-D53` (the `cancelled` CI conclusion on the M8 landing), `P6-D54` (no CI job runs the P6 probes or
mutation batteries), `P6-D55` (stale gate/topology snapshots), `P6-D56` (the reviewer harness has no
status for *"the command was supposed to fail, and it failed correctly"*), `P6-D57` (the M8 run's six
wave-01 scenarios rejected at assembly for an unapproved command vocabulary — addressed on the
Product Driver side), `P6-D58` (a build comment whose tense ran ahead of the landing), `P6-D47`–
`P6-D52` (the M7 residuals), `P6-D41`–`P6-D46` (the M6 residuals), `P6-D35`–`P6-D40` (the M5
residuals), `P6-D1`/`P6-D3`/`P6-D4` (the M1 residuals that name M9 — see §3.11), and **`V10`**.
Each is recorded. **If one of them actually makes M9 impossible to implement without choosing an
unauthorized reading, STOP and report the conflict rather than guessing.**

---

## 6. How this run works

Product Driver drives implementation, verification, correction and independent review. You do not
need to ask the founder to relay anything: scenario failures, evaluator findings and reviewer
findings come back to **you**, in this same session, as grounded corrections, and the loop retests.

M9 is **tier-1** work under `CLAUDE.md` §7. It is a state machine and an entity contract, which is
tier 2 by itself — but it also lands a **migration**, it is load-bearing for **tenant isolation**, and
it is the unit that decides ### **whether an obligation Neyma could not resolve reaches a named human
or is quietly forgotten**, which is weakening-a-safety-guard territory by every measure the table
uses and is the mechanism `AC-SAFE-028`, `I1` and `F-30` all rest on. §7 says to take the higher tier
once and say so, and this file says so. A focused independent review by a session that did not write
it is therefore required, and Product Driver launches it **inside the run** rather than after it.
Expect a reviewer to re-run your probe, your suite and your mutation battery for itself.

Report a genuine blocker plainly rather than working around it. **§3.8 is the place where reporting a
blocker is the correct outcome rather than a failure.**

**Stop at verified M9. Do not automatically continue into M10.**

Accepting M9 does **not** complete P6, does **not** score a P6 acceptance criterion, does **not**
unblock P7, and enables nothing in production.

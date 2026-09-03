# Build P6 / M11 — Policy. Only that.

You are building **one unit**: machine **M11**, the **Policy** — the typed, versioned, scoped,
deterministic tenant posture evaluated at **checkpoint step 6**, returning a **never-null gate
decision**.

Not P6. Not the phase. Not M12 (Rule). Not M13 (Brake). Not an autonomy-graduation engine, a policy
editor, an admin screen, a dashboard or a freight workflow. **One machine, its table, its migration,
its acceptance battery, its probe, its mutation battery — and nothing else.**

M10 (the Compensation) landed as `P6-CP-10` at `62efb8e`. `docs/implementation/CURRENT.md` says, in
its own words, **"The next build checkpoint is M11 — the Policy."** That is this unit.

---

## 0. Read the authority first, in this order

**Read these completely before writing a line.** Not skimmed, not grepped for keywords. This unit is
the mechanism every other safety guarantee in the system is conditioned on: if the policy engine is
wrong, the checkpoint, the grant, the approval and the brake are all deciding against a value that
does not mean what anyone thinks it means.

**Status and standing orders**
1. `CLAUDE.md` — the operating rules. §5 rule 17 (one authority per domain), §7 (review tiers), §9
   (a guard over an empty population proves nothing), §0 (no committed receipts).
2. `PRODUCT.md` — what Neyma is for.
3. `docs/implementation/CURRENT.md` — **the short-form status authority.** Read the P6 table, the
   *Still owed* row, the Risks table and **⛔ What must NOT begin**.
4. `docs/implementation/IMPLEMENTATION-REGISTRY.yaml` — the binding per-phase detail. On any
   disagreement with the roadmap, **the registry wins.** Read the P6 unit block, the P8 unit block,
   and every `P6-D*` debt row.
5. `docs/implementation/implementation-roadmap.md` — the 16 migration principles. Principle 13:
   **no phase depends on future work for a CURRENT safety guarantee.**

**The M11 canonical corpus**
6. `docs/specifications/entities/14-policy.md` — all 45 points.
7. `docs/specifications/state-machines/11-policy.machine.md` — all 43 sections, and §14's transition
   table **row by row**. Its opening paragraph is load-bearing: it refuses three states by name.
8. `docs/specifications/events/11-policy-events.md` — family **F11**, all eight contracts.

**Registries and global contracts**
9. `docs/specifications/state-machines/registry.md` — §4 (the state sets; M11's frozen seven), and
   the `GR-*` rules. **GR-7, GR-8, GR-11, GR-13 and GR-15 are all directly about this machine.**
10. `docs/specifications/events/registry.md` — **by its own header THE SOLE CANONICAL LIST of event
    names.** §3 (one producer transition per event), §5 (the consequential set — `PolicyApproved`,
    `PolicyActivated` and `PolicyVersionChanged` are all in it), §8 (F11 is STRICT per-aggregate),
    §9 (the ‡ coordination events), and the `ER-*` rules — **`ER-11` and `ER-12` in particular.**
11. `docs/specifications/entities/00-conventions.md` — `[C-1]`…`[C-10]`.
12. `docs/specifications/acceptance/foundational-machine-acceptance.md` — M11 is **7 transitions,
    `AC-MACH-1101..1107`**, gate **G4**.
13. `docs/specifications/acceptance/platform-safety-acceptance.md` — **`AC-SAFE-003`, `AC-SAFE-010`,
    `AC-SAFE-015` and `AC-SAFE-027` are this unit's acceptance criteria.** Read them first, not last.
14. `docs/architecture/target-system-specification.md` **§12.11**, **§20.7** (precedence) and **§11**
    (failure modes).
15. **`docs/architecture/decisions/ADR-010-policy-rules-constraints-autonomy.md` — IN FULL.** This
    ADR *is* the unit. §3 (the seven concepts, not collapsed), §3.1 (the four-member gate set and the
    proof that all four are distinct), §4 (ownership), §4.1 (versioning and expiry), §5 (evaluation
    inputs, output and determinism), §7 (the one-way ratchet), §7.4 (what a policy change
    invalidates), §8 (precedence), §9 (how step 6 joins the checkpoint), §10 (the merge-gating
    tests), §11 (failure modes), §12 (security).
16. Every ADR ADR-010 cites — **ADR-002** (provenance and evidence conditions), **ADR-003**
    (Authorization Assertion — the one Permanent Product Truth), **ADR-004** (§2.4 the atomic
    checkpoint, §3.2 the grant), **ADR-005** (approval binding and drift), **ADR-008** (§3.2 the
    pipeline's gate branch), **ADR-011** (the brake).

### Then read the LANDED CODE, not the prose about it

Every seam below is **already built**. You are wiring into it, not re-deciding it.

| Seam | Read | Why it is load-bearing for M11 |
|---|---|---|
| **P3 checkpoint** | `src/freight_recon/checkpoint.py` | `GateDecision` (the four members), `GateEntry`, `GateRegistry`, `GateRegistry._DEFAULT`, the seven steps, **step 6 as it exists today**, `mint_grant`, and the **claim CAS at the bottom of the file that already revalidates `policy_version`**. |
| **P3 brake** | `src/freight_recon/brake.py` | `BrakeStore.admission_denied`, `version_token`, `narrow()`, `BrakeStoreUnreachable`. **The brake denies regardless of what policy permits.** |
| M1 Work Item | `src/freight_recon/work_item.py`, `migrations/phase6_work_items.py` | **`tenant_humans` and `AUTHORITY_ROLES = ("POLICY_OWNER", "AUTHORIZED_HUMAN")`.** `POLICY_OWNER` was put there for THIS machine. |
| M2 Pipeline Instance | `src/freight_recon/pipeline_instance.py` | `propose()`, `PL-2` (which writes `gate_decision` and refuses NULL and emits **`PolicyEvaluated`** — *that event is M2's, not yours*), `PL-3`, `PL-6`, `PL-7a`, `PL-7v`/`PL-9v`. **The policy change itself runs through this.** |
| M3 External Effect / Grant | `src/freight_recon/external_effect.py` | The grant lifecycle and the `policy_version` bound onto a grant. |
| M4 Approval | `src/freight_recon/approval.py` | `request()`, `grant()`, the `fp_v1` fingerprint, and **`void_on_policy` (`AP-4p`) with `VOID_CAUSE_POLICY` — the landed drift seam. Drive it. Do not build a second one.** |
| M9 Exception | `src/freight_recon/exception.py`, `migrations/phase6_exceptions.py` | `raise_exception`, and **`SOURCE_KINDS` — which already contains `"policy"`**, carried without a FK as `M9-AQ-3`. PO-7's expiry escalates here. |
| M10 Compensation | `src/freight_recon/compensation.py` | The immediately preceding unit. **Read it as a model for shape; edit none of it.** |
| Events | `event_contracts_data.json`, `event_envelope.py`, `event_outbox.py`, `event_inbox.py` | The **118 registered contracts**, including **all eight F11 names**, F2's `PolicyEvaluated`, and F14's `UnauthorizedPolicyActivationAttempted`. `event_inbox.py` states the strict-order rule: **ORDER, not CONTIGUITY.** |
| Replay | `src/freight_recon/event_replay.py` | `GR-11` / `ER-2`: replay produces zero effects and zero authority. |
| **The ADR-010 boundary guards** | `eval/phase0/gate_scan.py`, `eval/tests/test_phase0_null_gate.py`, `eval/tests/test_phase0_errata_guards.py` | **Read these before you write `policy.py`.** See §3.7 — they decide the shape of this unit. |
| Schema | `src/freight_recon/schema.py`, `migrations/phase6_*.py` | The canonical table set, the tenant-first partition, `schema_readiness_problems`, and the `P6XX_*` symbol convention every P6 migration follows. |

### How to weigh them

When two authorities disagree, they are ordered:

1. `events/registry.md` is **the sole canonical list of event names.** A name not in it is not an
   event, however many machine files use it as a trigger phrase.
2. `docs/architecture/target-system-specification.md` and the machine file agree on the transition
   set. Where the entity file's prose is looser, the **machine table (§14) governs.**
3. `IMPLEMENTATION-REGISTRY.yaml` beats the roadmap.
4. **The landed code beats every document about the landed code.**

**Where they genuinely conflict, you RECORD the conflict and build the fail-closed side. You do not
resolve it.** §3.6 lists the conflicts already found. Finding another is a good outcome; silently
settling any of them is not.

---

## 1. What Neyma is — the stable identity

Neyma is an operational teammate for freight brokerages. It watches the systems a broker already
uses, notices what is owed, and — where it is allowed to act — acts **through a gate**: a checkpoint
that revalidates the facts a human approved, a single-use grant, one commit key per logical effect,
and a readback that proves what actually happened in the outside world.

The whole architecture exists because of one asymmetry: **a wrong external effect costs real money
and cannot be un-run.**

**M11 is where "where it is allowed to act" stops being a phrase and becomes a value.**

---

## 2. Where the program stands

- **P0–P5 COMPLETE.** The commit key, tenant-first persistence, the seven-step checkpoint, adapter
  containment, canonical events, outbox/inbox, replay isolation, durable timers.
- **P6 IN PROGRESS.** M1–M10 have landed as `P6-CP-1` … `P6-CP-10`. All ten ship dark.
- **113 of 134 transitions are written and landed.** M11's seven take it to 120. The remaining 14
  are M12's 9 and M13's 5.
- **P6 `criteria_scored` is `[]`.** No P6 weighted criterion is scored. P6 has **not** reached phase
  acceptance. **P7 is BLOCKED.** Landing M11 scores nothing.
- **The next build checkpoint is M11.** M12 and M13 are not yours.

**The CI position you are starting from, stated exactly.** At the M10 landing head `62efb8e`, CI run
`33594219060` concluded **`cancelled`**: the *Full test suite (py3.12)* job **SUCCEEDED** with 3165
passed and 1 skipped — the entire suite completed, including all 63 of M10's own tests — while *Full
test suite (py3.11)* and *Safety invariants (fast)* were **cancelled at their runtime ceilings with
no pytest `F` emitted**. **`cancelled` is not green, and nothing here claims it is** (`P6-D66`).
**Do not reopen M10 because of it: no product failure was demonstrated.**

---

## 3. The unit: M11, and nothing else

### 3.1 The sentences the whole unit is a consequence of

> ### **A POLICY IS A VALUE THE OWNER CAN SEE, NOT A SENTENCE IN A PROMPT.**
> ### **A TENANT POLICY MAY ONLY EVER NARROW THE PRODUCT CEILING.**
> ### **AUTOMATION MAY ONLY EVER MOVE AUTHORITY IN THE SAFE DIRECTION.**
> ### **A GATE EXPRESSIBLE AS AN ABSENCE IS NOT A GATE.**
> ### **A POLICY MAY NEVER BRANCH ON A GUESS.**

In freight terms. An owner types *"never bill without a POD."* The old system replied *"📋 Noted the
procedure"* — and what it installed was **a sentence in an LLM prompt**. The owner believes they
installed a control. They installed a suggestion. Later, an invoice goes out on a load with no proof
of delivery, and the owner's reasonable question is *"I told you not to do that"* — to which the
honest answer is *"you told a text box."*

M11 is the mechanism that makes the difference real. A policy is a **row**: a `gate_decision` that
cannot be null, a `predicate` that cannot read a guess, a `policy_version` bound into every witness
and grant, an `activated_by` that must be an authenticated human, and a lifecycle in which every
version is retained forever because effects were judged under it.

**And the direction is the whole content of the machine.** The tenant's posture may make Neyma more
careful; it may never make Neyma bolder. Nothing automatic — no model at confidence 1.0, no retry
handler, no timer, no inbound email announcing a new company rule — may move authority the other way.

### 3.2 The canonical state set

The **frozen seven** (`state-machines/registry.md` §4 / M11, entity §12, machine §7, TSS §12.11 —
all four agree):

```
DRAFT   PROPOSED   APPROVED   ACTIVE   SUPERSEDED   REVOKED   EXPIRED
```

**Terminal:** `SUPERSEDED`, `REVOKED`, `EXPIRED`.
**Non-terminal:** `DRAFT`, `PROPOSED`, `APPROVED`, `ACTIVE`. All four are recoverable.
**Initial:** `DRAFT`. **Failure:** validation failure ⇒ stays `DRAFT` (never activated).

### **DO NOT INVENT `NARROWED`, `SUSPENDED`, `INVALID`, `PENDING`, `ENABLED`, `DISABLED`,
### `CANCELLED`, `REJECTED`, `COMPILED`, `CONFIRMED`, `FAILED`, `ARCHIVED` OR ANY EIGHTH STATE.**

The machine file's own opening paragraph refuses the first three **by name**, and it is worth
understanding *why* rather than merely obeying it:

- **"narrowed"** is an `ACTIVE` policy with a tighter posture — **a new version**, not a state. A
  narrowed policy is still in force; making it a state would mean the system had two different kinds
  of "in force" and had to decide which one a checkpoint reads.
- **"suspended"** is `REVOKED`. Revocation narrows and is immediate.
- **"invalid"** is a `DRAFT` or `PROPOSED` that failed validation and therefore **never activated**.
  An invalid policy is not a policy in a special state; it is a policy that does not exist.

`REJECTED`, `COMPILED` and `CONFIRMED` are **M12 Rule's** states. Reaching for them is the clearest
early signal that this unit has started building the next one.

### 3.3 What M11 emits, and what it consumes

**The eight F11 contracts, and no ninth.** All eight are **already registered** in
`event_contracts_data.json` — `PolicySubmitted`(PO-2) and `PolicyApproved`(PO-3) were **MINTED by
the 2026-08-12 founder/architect amendment** to discharge two recorded `GR-2` obligations. Verify
every field yourself against the registry before you emit anything.

```
PolicyProposed        PO-1     scope, gate_decision, caps, predicate
PolicySubmitted       PO-2     policy_version, gate_decision, scope, predicate_ref
PolicyApproved        PO-3     approval_id, policy_version, diff_fingerprint, approved_by, evidence_refs
PolicyActivated       PO-4     policy_version, activated_by, effective_from, gate_decision
PolicySuperseded      PO-5     superseded_by
PolicyRevoked         PO-6     revoked_reason, direction ∈ {narrow, broaden}
PolicyExpired         PO-7     (no payload)
PolicyVersionChanged  ‡PO-4/6  policy_version
```

**Consumed:** `HumanActivated`, `HumanRevoked`, `TimerFired`.

### **`PolicyEvaluated` IS NOT YOURS.** It is **F2**, produced by **M2's `PL-2`**, with
### `aggregate_type=pipeline_instance`. Read `event_contracts_data.json` and confirm it before you
### write a line near it. Minting a second one is rule-17 duplication of a coordination contract.

### **DO NOT MINT `PolicyNarrowed`, `PolicySuspended`, `PolicyInvalidated`, `PolicyEnabled`,
### `PolicyDisabled`, `PolicyOverridden`, `PolicyCompiled`, `PolicyConfirmed`, `RuleActivated`,
### `BrakeEngaged` OR ANY OTHER NAME.** `PolicyOverridden` appears in **ADR-010 §8.1** and is **NOT
### in the 118 registered contracts** — that is `M11-AQ-4` / `P6-D71` below: `BLOCKED_AUTHORITY`,
### reported and NOT resolved, minted by nobody but a founder/architect, and landing with M12/Rule.

**`UnauthorizedPolicyActivationAttempted` is F14's and already registered.** A model or automation
attempting activation emits **that** contract. Do not mint a duplicate.

### 3.4 Implement the canonical `PO-*` transition contract

Seven rows, exactly as `11-policy.machine.md` §14 writes them. Trigger column: `H` human, `S` system,
`T` timer.

| ID | From → To | Trig | Guard | Writes | Event |
|---|---|---|---|---|---|
| **PO-1** | — → `DRAFT` | H | authored by the Policy Owner or a delegate (**a model may propose TEXT**) | `scope, gate_decision, caps, predicate` | `PolicyProposed` |
| **PO-2** | `DRAFT` → `PROPOSED` | H | `gate_decision` NOT NULL (F-20); predicate references only **MODELLED, NON-INFERRED** fields (GR-8); tenant policy may only **NARROW** the product ceiling | — | `PolicySubmitted` |
| **PO-3** | `PROPOSED` → `APPROVED` | H | the change ran through an **M2 pipeline with the DIFF as material facts** | `approval_id` | `PolicyApproved` |
| **PO-4** | `APPROVED` → `ACTIVE` | H | **an AUTHENTICATED human activates — NEVER a model, NEVER automation** | `activated_by, policy_version, effective_from` | `PolicyActivated` + `PolicyVersionChanged` |
| **PO-5** | `ACTIVE` → `SUPERSEDED` | H | a new version activated | `superseded_by` | `PolicySuperseded` |
| **PO-6** | `ACTIVE` → `REVOKED` | H\|S | **immediate if it NARROWS; the Policy Owner if it BROADENS** | `revoked_reason` | `PolicyRevoked` + `PolicyVersionChanged` |
| **PO-7** | `ACTIVE` → `EXPIRED` | T | a **narrowing** policy's TTL fires ⇒ **raises a human-confirmation Exception** | — | `PolicyExpired` (+ Exception) |

**Illegal (machine §15):** a model or automation activating or broadening → **ILLEGAL (GR-7)**, and
`IllegalTransitionAttempted` (audit **and** security). An `ACTIVE` policy with a null `gate_decision`
→ ILLEGAL. A tenant policy broadening the ceiling → ILLEGAL. A predicate branching on
`MODEL_INFERRED` → **fails to compile, never reaches `ACTIVE`**. Automatic broadening → ILLEGAL.

**Do not add an eighth transition.** There is no `EXPIRED → ACTIVE`, no `REVOKED → ACTIVE`, no
reopening (entity §27: N/A), and no `DRAFT → ACTIVE` shortcut.

### 3.5 What must hold — the authority and safety requirements

**THE NEVER-NULL GATE DECISION (F-20).**
`gate_decision` is `NOT NULL` and constrained to **exactly the four canonical members**:

```
HUMAN_APPROVAL_REQUIRED   AUTONOMOUS_WITHIN_CAPS
PERMANENT_HUMAN_ASSERTION_REQUIRED   FORBIDDEN
```

They are **already defined** in `checkpoint.py` as `GateDecision`. **Import them. Do not redeclare
the enum.** ADR-010 §3.1 proves all four are semantically distinct and the proof is load-bearing:
collapsing `PERMANENT_HUMAN_ASSERTION_REQUIRED` into `HUMAN_APPROVAL_REQUIRED` would either freeze
money-out forever or make the Authorization Assertion graduatable, and collapsing `FORBIDDEN` into
it confuses *"only a human may ever do this"* with *"nobody may ever do this."*

### **AN ACTION CLASS WITH NO GATE DECISION CANNOT BE REGISTERED — the system fails to start.** The
kernel already enforces this: `GateRegistry.__init__` raises, and an UNregistered class resolves to
the fail-closed `HUMAN_APPROVAL_REQUIRED` default. **Do not weaken either.**

**THE PRODUCT CEILING.**
A tenant policy may only **NARROW**. Build the comparison as a **declared total order over the four
members**, cited to ADR-010 §3.1, broadest first:

```
AUTONOMOUS_WITHIN_CAPS  >  HUMAN_APPROVAL_REQUIRED  >  PERMANENT_HUMAN_ASSERTION_REQUIRED  >  FORBIDDEN
```

### **DO NOT COMPARE THE STRINGS.** `AUTONOMOUS_WITHIN_CAPS` sorts before `HUMAN_APPROVAL_REQUIRED`
### alphabetically, so a string comparison calls the single most dangerous broadening in the system
### a narrowing — and it does so silently, on the exact path where nobody is watching.

**THE DETERMINISTIC PREDICATE (M-49 / GR-8 / S3).**
A predicate may reference only the §5.2 inputs, all deterministic. **It may NEVER reference a
`MODEL_INFERRED` field, at any confidence — including 1.0.** `confidence` is **structurally not an
input**: the evaluator's input type has no such field, so a guard cannot read it even by trying.

### **DO NOT IMPLEMENT THIS AS A STRING BLACKLIST.** The refusal is **typed**: the evaluator's input
### carries `provenance_class` on every field and **raises on read**. A predicate that cannot be
### evaluated deterministically **FAILS TO COMPILE.** A guess cannot become a gate by being passed
### through a policy engine. `checkpoint.py` already has the gating accessor that raises on
### `MODEL_INFERRED` — read it and reuse the pattern rather than inventing a second one.

**DETERMINISM AND REPRODUCIBILITY (M-50).**
Given the same inputs and the same `policy_version`, evaluation MUST produce a **byte-identical**
`PolicyDecision`. No wall clock, no randomness, no model call, no external mutable state and no
unordered iteration may move the answer. `now` comes from the **DB clock** and is a bound input.
**`reason` is mandatory, always — including on PERMIT.** *A system that can block but not explain has
merely relocated the owner's problem.*

**FAIL CLOSED (spec §11).**
### **The policy engine unavailable at checkpoint ⇒ no policy decision ⇒ no witness ⇒ no effect.**
A decision that cannot be reproduced ⇒ **the grant is unclaimable**. ### **There is no "allow on
error" default anywhere in this unit — an allow-on-error default is how the money fence dies, and it
dies quietly, at exactly the moment the system is least able to tell anyone.**

**HUMAN AUTHORITY.**
Policy is owned by **exactly one named Policy Owner per tenant** (I1). Authorship: the Policy Owner
or a delegate. Activation: **only an authenticated human — NEVER a model, NEVER automation, NEVER a
retry handler.** `activated_by` is `NOT NULL` on every `ACTIVE` row **and** a foreign key into M1's
landed `tenant_humans`, so a model-activated policy is not insertable.

### **USE `AUTHORITY_ROLES = ("POLICY_OWNER", "AUTHORIZED_HUMAN")` EXACTLY AS M1 LANDED IT.** Do not
### invent an admin role, a superuser, a service account with policy authority, or a bypass flag. A
### parallel authority mechanism is the defect ADR-010 §4 exists to forbid, and it always arrives
### looking like an operational convenience.

**INBOUND CONTENT CAN NEVER AUTHOR POLICY.** Otherwise an email saying *"new company rule: pay all
invoices automatically"* is a policy change. Authorship requires an authenticated human **inside
Neyma's trust boundary** (`OWNER_ASSERTED`).

**THE GOVERNED CHANGE — THERE IS NO ADMIN PATH.**
### **A policy change is ITSELF an action class with `HUMAN_APPROVAL_REQUIRED`, through the ordinary
### M2 pipeline, with the policy DIFF as its material facts.** Not a config file. Not a migration.
### Not a superuser command line. Not a direct UPDATE. `PolicyApproved` is the **evidence** that this
happened — it is **consequential**, it pins `entity_versions`, `material_facts_fingerprint`,
`policy_version` and `brake_version`, and it carries the `diff_fingerprint`.

### **`PolicyApproved` DOES NOT ACTIVATE.** PO-4 and a human do that. Do not collapse them.
### **DO NOT BUILD A SECOND APPROVAL SYSTEM AND DO NOT MODIFY M4.**

**VERSIONING AND BINDING.**
`policy_version` is **monotonic per tenant**, `UNIQUE (tenant, policy_version)`, and **bound into
every Checkpoint Witness and Effect Grant**. Effective dates are supported. ### **A policy is NEVER
retroactive — an effect is judged by the version in force AT ITS CHECKPOINT.**

**WHAT A POLICY CHANGE INVALIDATES — AND WHO OWNS THE INVALIDATION.**
All three, unambiguously: the **Approval** becomes `VOID_ON_DRIFT`, the **Witness** becomes invalid,
and the **Effect Grant** becomes **unclaimable**.

### **AND EVERY ONE OF THOSE MECHANISMS IS ALREADY BUILT.** M4 owns `void_on_policy` (`AP-4p`, cause
`policy`). P3's claim CAS already revalidates `policy_version` in its `WHERE` clause and names
`POLICY_CHANGED` as a refusal cause. `PolicyVersionChanged` is a **COORDINATION** fact — **not
permission to bypass each consumer's own guard.** ### **DRIVE THEM. DO NOT BUILD A SECOND ONE.** Two
invalidation mechanisms will eventually disagree about whether a stale grant is claimable, and the
answer will be decided by whichever ran first.

**ACTIVE-SCOPE UNIQUENESS AND CONCURRENCY.**
### **`UNIQUE (tenant_id, scope) WHERE state = 'ACTIVE'`** — one active policy per scope, tenant-first.
Plus **OCC**: a transition writes `WHERE version = :expected`; zero rows ⇒ lost update ⇒ raise.
**Test the RACE, not only a serial duplicate insertion.** A serial duplicate proves the index exists;
a race proves it holds.

**RETENTION.**
### **Every version is retained permanently** (`[C-9]`, entity §28/§29). Supersession does not erase.
There is **no deletion path**. A wrong policy is **superseded by a new version**, never edited in
place — because effects were judged under the old one and it still has to explain them.

**REVOCATION DIRECTION.**
Narrowing and broadening are **not equivalent**. A revocation that NARROWS is immediate and may be
automated. A revocation that BROADENS requires the **Policy Owner** (`ER-12`). `PolicyRevoked`
carries `direction ∈ {narrow, broaden}` — it is a required, enumerated field, not a comment.

**EXPIRY — THE ONE PLACE A CLOCK COULD BROADEN AUTHORITY.**
Only a **narrowing** policy may carry `expires_at`. ### **Its expiry is a BROADENING event and
therefore REQUIRES A HUMAN AT EXPIRY.** `TimerFired` raises the canonical **human-confirmation
Exception** through **M9's landed seam**; it does not restore authority on its own.

> ### ***Otherwise "temporarily tighten" becomes "automatically loosen later, when nobody is
> ### watching."***
>
> Note the contrast with autonomy graduation (ADR-010 §7.2), whose expiry **narrows**: **the clock
> may take authority away; the clock may never give it.**

Make the direction a **persisted, checkable column** with a database `CHECK` — not something the
machine promises to remember.

**PRECEDENCE (§20.7 / ADR-010 §8).**

```
Constraint > Permanent Product Truth > Brake > Product Policy > Tenant Policy > Rules > Workflow default
```

### **A policy NEVER overrides a Permanent Product Truth, and NEVER overrides a brake denial** —
however urgent it claims to be. Today there is **exactly one** Permanent Product Truth: the
Authorization Assertion (ADR-003). The **brake** you test against is the **landed P3 `BrakeStore`**.

### **M12 RULE AND M13 BRAKE ARE NOT BUILT.** Verify the seams that exist and refuse illegal M11
### override behaviour. **Do not require M12 or M13 to exist in order to make M11 pass.**

**REPLAY (GR-11 / K-3).**
Replay reproduces policy history and the decisions made under each version. ### **Replay creates no
human authority, does not re-activate a policy, mints no witness, claims no grant and produces no
external effect.** A replay that could re-activate is a replay that can grant authority nobody
granted, from a log.

**STRICT EVENT ORDER.**
F11 is **STRICT per aggregate**. Honour the repository's already-landed rule: ### **ORDER, not
CONTIGUITY.** `event_inbox.py` states it and implements it via `previous_aggregate_version`. **Do not
fabricate a contiguity requirement** where only order is required, and do not weaken order to
tolerate the gaps that an intentional non-emission creates.

**TENANT FIRST `[C-1]`.**
`tenant` is FIRST in the primary key and first in every index. Cross-tenant creation, lookup,
activation, supersession, revocation, evaluation and version use all fail closed. ### **No global
uniqueness may accidentally couple tenants** — the SAME scope must be ACTIVE in two brokerages
without collision, and two tenants must both be able to hold version 1.

### 3.6 ⚠️ THE EIGHT AUTHORITY QUESTIONS — FIVE ARE NOW SETTLED IN CANON, THREE ARE NOT

**This bootstrap found eight authority questions mechanically, against the corpus and the landed
code. A Neyma AUTHORITY-ONLY correction has since landed at `5d2d8e1` — it changed no file under
`src/`, `eval/`, `scripts/` or `.github/` — which CORRECTED five of them in the canon and RECORDED
the remainder as P6 debt rows `P6-D71`…`P6-D75`.**

### **THE FAIL-CLOSED SIDE OF EVERY ONE OF THEM IS UNCHANGED. The correction TIGHTENED the
### authority; it licensed nothing.** Where a question is still OPEN below, it is open because
resolving it is a founder/architect act — **not** because you have discretion. Read what the canon
now says, and build it.

**`M11-AQ-1` — RESOLVED IN CANON. P6/M11 builds the MACHINE; P8 keeps the RUNTIME.**
It read as a contradiction: P6's `prohibited_scope` carried the bare token `policy (P8)` while P6's
own `objective` and `expected_production_outputs` owe *"the 13 machines, 134 transitions"* — and M11,
M12 and M13 ARE three of those thirteen, their 21 transitions part of the 134, with the registry
itself saying **"M11 — THE POLICY — IS THE NEXT CHECKPOINT."** Read literally, the token prohibited
the work the same file schedules. `IMPLEMENTATION-REGISTRY.yaml`'s P6 unit and `PHASE-OUTPUTS.md`'s
P6 row now NAME the boundary instead of carrying the bare token.

**What P6/M11 MAY implement:** the canonical Policy **entity** (`entities/14-policy.md`), its
**tenant-first `policies` table**, machine M11's **seven transitions `PO-1`…`PO-7`**, and the
**eight already-registered F11 contracts** — **SHIPS DARK**, on the M1–M10 precedent: no production
importer, no channel join, no `GateRegistry` construction and no `register_gate` call anywhere in
production, no gate minting outside `checkpoint.py`, and no activation of a real tenant policy on
live traffic.

**What remains P8-ONLY (`U8.1`):** production Action Class gate **REGISTRATION** (populating the
production `GateRegistry`), the typed policy **EVALUATION RUNTIME** at checkpoint step 6 beyond P3's
landed minimal structural contract, rule **compile-or-refuse** runtime, the
**precedence/conflict-resolution engine**, richer **action-class descriptors** (K-5), the
**autonomy/graduation runtime**, and **ENABLEMENT** of any of it on live traffic.

### **NOTE PRECISELY WHAT IS *NOT* ON THE P8 LIST: AUTHORING.** `PO-1` and `PO-2` — a Policy Owner
### drafting a policy and submitting it for approval — ARE M11's, ship-dark. **Do not refuse to build
### the authoring transitions on the ground that "tenant policy authoring is P8."** That reading is
wrong, an earlier draft of this task carried it, and it is corrected here. This grants no scope and
removes none; `V11` and `V12` stay open and fail-closed.

**`M11-AQ-2` — RESOLVED IN CANON. F11 is exactly EIGHT.**
`entities/14-policy.md` point 31 listed **six** — it omitted `PolicySubmitted`(PO-2) and
`PolicyApproved`(PO-3), both MINTED under founder/architect authority by the **2026-08-12** P5 U5.2
amendment. It is settled the same way it always would have been — **the event registry governs** —
because `events/registry.md` §3 is by its own header the sole
canonical list of event names — so point 31 has been corrected **UP** to the registry. ### **NO
### REGISTERED CONTRACT WAS DELETED TO MAKE AN ENTITY LIST MATCH.** F11, the state-machine registry
§5 and machine M11 §14 always carried eight. **Emit all eight. Mint no ninth.** `PolicyProposed` ≠
`PolicySubmitted`, and `PolicyApproved` ≠ `PolicyActivated`; both distinctions are load-bearing.

**`M11-AQ-3` — RESOLVED IN CANON. §12.11 carries NO Policy transition table.**
§12.11 and §12.12 share one heading and one transition table, and **every row of that table emits a
`Rule*` event — it is §12.12 RULE's**, which the target specification now states directly above it.
Entity point 20's *"§12.11 (complete)"* was false against §12.11 alone and is corrected.
### **Machine `11-policy.machine.md` §14 (`PO-1`…`PO-7`) IS Policy's transition authority**, and it
derives from §12.11 and invents nothing. §12.11's **prose below the table** still binds: a policy
change is itself a gated action class, effective dates are never retroactive, and a narrowing
policy's expiry requires a human. **No lifecycle was rewritten.**

**`M11-AQ-4` — STILL OPEN. `P6-D71`. `BLOCKED_AUTHORITY`. DO NOT TOUCH IT IN EITHER DIRECTION.**
`PolicyOverridden{rule_id, actor, reason, decision_ref, commit_key}` is named by target spec §20.7
M-54, by ADR-010 §8.1 and by `entities/17-audit-event.md` point 41 as an audit **and** security event
with an explicit payload and a named test — and it appears **nowhere** in `events/registry.md` §3.
It is not a stale synonym, not merely conceptual, and not historical: it is a canonical event that
was **intended and never minted**. ### **MINTING AN EVENT IS A FOUNDER/ARCHITECT ACT** — the only
precedent is the 2026-08-12 amendment — so the correction pass deliberately did **not** edit it, and
neither do you.

### **IT DOES NOT BLOCK M11.** The payload is keyed on `rule_id`; the act is an override of a
**STANDING RULE at precedence layer 6**, not a Policy lifecycle transition; and it appears in **none**
of M11's seven `PO-*` rows and **none** of F11's eight contracts. ### **M11 BUILDS NO OVERRIDE
### MECHANISM AT ALL, MINTS NO `PolicyOverridden`, AND SIMULATES NONE — not as an event, not as a
### field, not as a code path.** The obligation lands with **M12/Rule**. Report that you left it
alone.

**`M11-AQ-5` — RESOLVED IN CANON, AND THE INVARIANT IS UNWEAKENED.**
Entity point 16's *"CHECK: a tenant policy's `gate_decision` may only NARROW the product ceiling"* is
a ### **SEMANTIC INVARIANT, NOT A ROW-LOCAL SQL `CHECK`**, and the entity now says so in the same
form `01-work-item.md` ("enforced at the transition layer") and `15-rule.md` ("enforced at compile")
already use. The comparison is against **Product Policy**, which spec §20.2 enforces **in CONFIG** —
it is not a column of this row, so **no row-local constraint can reference it**.

Enforce it at the **`PO-2` transition guard** and as **machine M11 §15's illegal transition**, over a
**declared total order on the four canonical gate members** — never a string compare, never an
alphabetical sort. **State plainly that the invariant is a machine guard rather than a row `CHECK`,
and why.** Do not silently drop it, and **do not fake a `CHECK` that compares a column to itself.**
### **AND DO NOT NOW READ "IT IS NOT A SQL `CHECK`" AS PERMISSION TO MAKE IT A REVIEW CONVENTION:
### BROADENING MUST BE MECHANICALLY IMPOSSIBLE.**

**`M11-AQ-6` — RESOLVED IN CANON. The version NAMESPACE is the TENANT, never the scope.**
Point 17's `UNIQUE (tenant_id, policy_version)` plus point 19's per-tenant monotonicity (`[C-10]`)
plus the landed kernel pinning ONE scalar per decision make a scope-local numbering **structurally
impossible**: two scopes cannot each hold version 1. `scope` appears in the natural key
`(tenant_id, scope, policy_version)` because it names WHICH posture the row carries — **not** because
it opens a second numbering. **Build tenant-monotonic, and reject any scope-local version scheme.**

### **AND THE CONSEQUENCE IS NOW STATED, IS DELIBERATE, AND YOU MUST BUILD IT: a change in ANY scope
### advances the TENANT's `policy_version`, so `PolicyVersionChanged` voids in-flight
### approvals / witnesses / unclaimed grants in EVERY scope** (points 39/40; machine M11 §34). A
checkpoint pins and re-validates exactly ONE `policy_version` per decision.
### **OVER-VOIDING IS THE FAIL-CLOSED DIRECTION AND UNDER-VOIDING IS NOT AVAILABLE** — do not
"optimise" by scoping the void to the scope that changed, and do not treat a surviving in-flight
approval in another scope as correct behaviour. It is the defect this question exists to prevent.

**`M11-AQ-7` — OPEN AS M11 ACCEPTANCE WORK. `P6-D72`. ### THIS ONE IS YOURS TO CLOSE, AND IT IS
LOAD-BEARING.**
Entity point 7 requires *"exactly one named Policy Owner per tenant"* (I1); point 14 states
Policy N : 1 Policy Owner; point 18 requires `activated_by` to **BE** the Policy Owner or an
authorised delegate; and M11 §5 names *"the tenant's single named Policy Owner"* as the machine's
owner. M1's landed `tenant_humans` carries `authority_role IN ('POLICY_OWNER','AUTHORIZED_HUMAN')`
with `PRIMARY KEY (tenant, human_id)` and ### **NO constraint limiting a tenant to one ACTIVE
### `POLICY_OWNER`. TWO ARE INSERTABLE TODAY** — measured, not assumed, in
`src/freight_recon/migrations/phase6_work_items.py`.

This is **not** a corpus contradiction and it is **not** deferred: `P6-D72` **closes at M11**.
### **YOU MUST ESTABLISH THE INVARIANT MECHANICALLY. A tenant must not be able to hold two ACTIVE
### `POLICY_OWNER` rows once M11 has landed.**

- **The mechanism is yours to choose, and it must sit on the EXISTING tenant-authority record.**
  `tenant_humans` is the one record of human authority in this system. ### **DO NOT INVENT A SECOND
  ### USER, ADMIN, SUPERUSER OR AUTHORITY SYSTEM** — ADR-017's control-plane user administration is
  **P11** and is a different thing.
- A constraint added to `tenant_humans` is a **MIGRATION ON AN M1-LANDED, TENANT-ISOLATION-BEARING
  TABLE — `CLAUDE.md` §7 TIER 1.** ### **THAT ROUTE IS PERMITTED**, and it is the one the debt row
  names. It is **not** covered by "do not modify M1–M10" (§5), which forbids **rebuilding** landed
  machines, not adding the constraint an M11 invariant requires. Take it only with the tier-1
  process: builder **plus one focused independent review**, plus proof the new constraint **can
  actually fail**.
- ### **IT IS LOAD-BEARING BECAUSE point 18's `activated_by` FK AND `PO-6`'s "broadening requires the
  ### Policy Owner" BOTH RESOLVE THROUGH IT: an ambiguous Policy Owner makes "the Policy Owner
  ### activated this" UNPROVABLE — and that is the strongest authority claim in the system.**
- **`V12` stays OPEN at its fail-closed default: one Policy Owner, one authority level.** Enforcing
  singularity **IS** that default. It does not resolve `V12`, and you may not claim that it does.

**`M11-AQ-8` — RESOLVED BY LANDED PRECEDENT. `P6-D73`. The seam is NAMED and LEFT UNWIRED.**
M9's `exceptions.source_kind` already accepts `policy` with **no foreign key**, because `policies`
did not exist — `SOURCE_KINDS_WITHOUT_TABLE` in `migrations/phase6_exceptions.py`, recorded by M9
itself as `M9-AQ-3`. M11 creates the referent, so "should M11 wire the FK now" arrives with M11. It
is answered by a landing, not by a new decision: ### **M10 created `compensations` and did NOT add
### the `compensation` mirror column or FK to `exceptions`** — the seam was named and left unwired,
on the ground M9's own landing set: *"wiring a seam is precisely what shipping dark forbids."*

### **M11 DOES THE SAME: NAME THE SEAM AND LEAVE IT UNWIRED.** To say it in the form the rest of
this task uses: **M11 edits no part of M9.** `PO-7` raises its
Exception through M9's landed `raise_exception` entry point with `source_kind="policy"`, exactly as
the recorded-only kind allows. Adding the FK is a change to a table M11 does not own and belongs to a
later session that owns `exceptions`.

**Two further debt rows the correction recorded, which you WILL reach:**

**`P6-D74` — the gate-runtime CARRIER allowlist is a fixed three, and M11 collides with it.**
`eval/phase0/gate_scan.py` states the ADR-010 boundary exactly once as `GATE_RUNTIME_MODULES`, and
two P0 guards read it. This is a **tier-1 decision point, recorded before it is reached**, and
**§3.7 below is its full and binding treatment.** ### **NOTHING ABOUT `P6-D74` AUTHORISES WIDENING
### THE *MINT* ALLOWLIST. `checkpoint.py` STAYS THE SOLE MINTER OF A GATE DECISION, AND THE
### PRODUCTION `GateRegistry` POPULATION STAYS EMPTY UNTIL U8.1/P8.**

**`P6-D75` — nothing cross-checks an entity's "Events emitted" against the event registry.**
That is exactly how `M11-AQ-2` survived from the 2026-08-12 amendment until the correction pass:
`eval/phase0/spec_corpus.py` and `test_phase0_acceptance_bijection.py` assert exact set equality
between the registry and the 13 machine tables, but **no probe reads point 31 of any of the
seventeen entity files.** Only `14-policy.md` was corrected; ### **THE CLASS IS OPEN AND THE OTHER
### SIXTEEN ENTITY FILES WERE NOT AUDITED.** Do not treat an entity file's point 31 as trustworthy
authority. **Do not build that probe here** — it is real work with a real denominator and it is not
this unit's scope — but do not rely on the unaudited surface either: where an entity list and the
registry disagree, **the registry governs.**

**Also carried, and not blockers:** **V11** (autonomy graduation thresholds) and **V12** (which
authorities exist per tenant) stay **OPEN** at their canonical fail-closed defaults — **nothing
graduates; one Policy Owner, one authority level.** ### **DO NOT RESOLVE V11 OR V12 BY PREFERENCE.**

### 3.7 The seams that are already built — feed them, do not duplicate them

| What M11 needs | Where it already lives | What M11 does |
|---|---|---|
| The four gate members | `checkpoint.py::GateDecision` | **import** |
| Minting a gate decision | `checkpoint.py` (`GateEntry` / `GateRegistry`) | **nothing — see below** |
| The fail-closed default | `GateRegistry._DEFAULT` | **leave it alone** |
| The seven-step checkpoint | `checkpoint.py` | **integrate at step 6; build no second one** |
| Stale-grant refusal | `checkpoint.py` claim CAS (`policy_version` in the `WHERE`) | **drive it** |
| In-flight approval drift | `approval.py::void_on_policy` (`AP-4p`) | **drive it** |
| The governed change pipeline | `pipeline_instance.py::propose` | **use it** |
| The brake | `brake.py::BrakeStore` | **read it; engage none, narrow none** |
| Human authority | `tenant_humans` + `AUTHORITY_ROLES` | **use exactly it** |
| The expiry escalation | `exception.py::raise_exception` | **call it; edit no part of M9** |
| Event envelopes / order | `event_envelope.py`, `event_outbox.py`, `event_inbox.py` | **use them** |

### **⚠️ THE ONE PLACE THIS UNIT WILL LEGITIMATELY MOVE A LANDED BOUNDARY — AND THE ONE PLACE IT
### MUST NOT.**

Read `eval/phase0/gate_scan.py` before you write `policy.py`. It states the ADR-010 boundary **once**:

```python
GATE_RUNTIME_MODULES = frozenset({"checkpoint.py", "phase3_checkpoint.py", "pipeline_instance.py"})
```

Two P0 guards consume it, and they assert **different** things:

- `test_phase0_errata_guards.py::test_typed_policy_runtime_exists_only_with_its_canonical_authority`
  asserts the **DISCOVERED carrier population EQUALS that set**, by exact set equality, and that
  every carrier **cites ADR-010**. It reads **executable source only** — prose naming a gate is not
  a carrier.
- `test_phase0_null_gate.py::test_only_the_checkpoint_kernel_may_MINT_a_gate_decision` asserts, by
  AST, that constructing a `GateEntry` or a `GateRegistry` happens **only in `checkpoint.py`**.

**M11 carries the typed ladder in executable code by canon** — a policy's whole content is a
`gate_decision`, and a machine that could not NAME one could not hold one. So `policy.py` and
`migrations/phase6_policies.py` **join `GATE_RUNTIME_MODULES`**, exactly as `pipeline_instance.py`
joined it at `P6-CP-2`. Update the boundary in the **one place it is stated**, and cite ADR-010 in
both new carriers.

### **THAT IS A WIDENING WITH A NARROWING ATTACHED, AND THE NARROWING IS NOT NEGOTIABLE:
### `policy.py` MUST CONSTRUCT NO `GateEntry` AND NO `GateRegistry`. THE MINT ALLOWLIST STAYS
### `{checkpoint.py}`.**

The temptation arrives dressed as compliance: *"the Policy Engine obviously owns the gate registry."*
### **It does not. A second gate authority is the same defect as no gate authority — two answers to
### "may Neyma do this alone", and nothing that says which one the grant was minted under.** M11
supplies the **posture** the kernel's step 6 reads. The kernel still mints.

Do not "fix" either guard by deleting it, by loosening the equality to a subset check, or by adding
`policy.py` to the **mint** allowlist. If you find you need any of those, stop and report it.

### 3.8 The F14 tripwires — which are yours

`UnauthorizedPolicyActivationAttempted` (already registered, `policy_or_rule_id`, `actor_type`) is
**yours** — a model or automation attempting activation emits it. `IllegalTransitionAttempted` is
**GR-1's**, shared by every machine, and M11 uses the same producer-id convention M1..M10 use.
`UnauthorizedBrakeReleaseAttempted` is **not yours**. **Mint no F14 name that is not registered.**

---

## 4. What you must produce

Six files, and no seventh:

```
src/freight_recon/policy.py                      the machine
src/freight_recon/migrations/phase6_policies.py  the table, indexes, triggers, readiness oracle
src/freight_recon/schema.py                      EDITED: wire the migration in, P6PO_* symbols
eval/tests/test_phase6_policy.py                 the acceptance battery
scripts/probe_phase6_policy.py                   the deterministic probe
scripts/mutate_phase6_policy.py                  the mutation battery
eval/phase0/gate_scan.py                         EDITED: the carrier boundary, §3.7
```

### **NAME THE MACHINE'S OWN TYPES THE WAY `compensation.py` AND `exception.py` NAME THEIRS.**
Follow the landed `P6XX_*` migration symbol convention exactly: `MIGRATION_ID`,
`P6PO_SCHEMA_VERSION`, `P6PO_TENANT_TABLES`, `P6PO_EXEMPT_TABLES`, `P6PO_TARGET_SCHEMA`,
`P6PO_INDEXES`, `P6PO_REPLACED_INDEXES`, `P6PO_TRIGGERS`, `create_phase6_policies_schema`,
`stamp_phase6_policies_version`, `phase6_policies_readiness_problems`. Marker-last, like every phase.

**The table is `policies`, tenant-first, and it joins the canonical partition** (`P6 tenant — M11`).
Declare `P6PO_EXEMPT_TABLES` as empty **explicitly** rather than omitting it, so a future addition
has to defend its exemption.

### The probe's interface

```
scripts/probe_phase6_policy.py
    --list-cases        the case names, one per line, kebab-case
    --list-dimensions   every mutation-axis token, one per line
    --case <name>       run exactly one case
    --all               run every case
    --concurrency N     --repeat N     --tenants N     --seed N     --delay-ms N
    --inject <fault>    the closed fault set; an unknown fault exits 2
    --actor <kind>      human | model | automation | timer | retry | counterparty | inbound
    --direction <d>     narrow | broaden | all      ← THIS UNIT'S OWN AXIS
    --gate <g>          one of the four canonical members, or all
    --provenance <p>    one of the six canonical provenance classes, or all   ← ITS SECOND AXIS
    --brake <state>     engaged | released
    --scope <s>         one of the canonical scope kinds, or all   ← `M11-AQ-6`'s AXIS
```

### **`--direction` AND `--provenance` ARE THIS UNIT'S OWN TWO AXES**, the way `--original-state`
and `--exposure` were M10's. Direction is the axis the whole machine turns on — narrowing and
broadening are not symmetric anywhere in this unit — and provenance is the axis the predicate turns
on. `--gate` varies the ceiling comparison across the four-member ladder.

### **`--scope` IS THE AXIS `M11-AQ-6`'s CORRECTED CONSEQUENCE IS ONLY VISIBLE ALONG.** The version
namespace is the TENANT, so a change in ONE scope must void in-flight authority in EVERY other
scope — and a single-scope case can never show that, because with one scope "void the scope that
changed" and "void the tenant" are the same behaviour. ### **A scope-local implementation passes
### every single-scope case and fails the system**, which is exactly why the scope a case runs
against has to be a dimension the generator can vary rather than a constant baked into one case.

**Every case must be deterministic, hermetic and free of wall-clock sleeps**, and `--all` must be
runnable end to end in well under its scenario timeout.

### The probe's output contract

Every case prints a `### MISS ###` marker on failure and a positive line on success — the shared
harness vocabulary every P6 probe carries, alongside `### NOT REFUSED`, `### WRONGLY REFUSED` and
`### WRONG REFUSAL`. The scenario forbids all four globally, so a case that silently does nothing
cannot read as a case that passed.

The narrative run must print `behaviours as specified, 0 wrong` on success, and **exactly these
headlines**, each emitted by the case that actually establishes it:

```
A POLICY IS A VALUE THE OWNER CAN SEE, NOT A SENTENCE IN A PROMPT
A TENANT POLICY MAY ONLY EVER NARROW THE PRODUCT CEILING
AUTOMATION MAY ONLY EVER MOVE AUTHORITY IN THE SAFE DIRECTION
A GATE EXPRESSIBLE AS AN ABSENCE IS NOT A GATE
A POLICY MAY NEVER BRANCH ON A GUESS
CONFIDENCE IS STRUCTURALLY NOT AN INPUT
A MODEL CAN NEVER ACTIVATE A POLICY
AUTOMATION CAN NEVER ACTIVATE A POLICY
INBOUND CONTENT CAN NEVER AUTHOR A POLICY
A POLICY CHANGE IS ITSELF A GATED ACTION, AND THERE IS NO ADMIN PATH
PolicyApproved IS THE NO-ADMIN-PATH EVIDENCE
PolicySubmitted IS NOT A RENAME OF PolicyProposed
PolicyApproved DOES NOT ACTIVATE
A POLICY IS NEVER RETROACTIVE
THE OLD VERSION IS RETAINED BECAUSE EFFECTS WERE JUDGED UNDER IT
A NARROWING REVOCATION IS IMMEDIATE; A BROADENING ONE NEEDS THE OWNER
THE CLOCK MAY TAKE AUTHORITY AWAY; THE CLOCK MAY NEVER GIVE IT
AN EXPIRY THAT BROADENS REQUIRES A HUMAN AT EXPIRY
EVALUATION IS BYTE-IDENTICAL REPRODUCIBLE
NO POLICY DECISION MEANS NO WITNESS AND NO EFFECT
THERE IS NO ALLOW-ON-ERROR DEFAULT
M11 MINTS NO GATE DECISION
THE CHECKPOINT IS STILL THE ONLY GATE MINTER
M11 BUILDS NO SECOND CHECKPOINT
A STALE POLICY VERSION MAKES THE GRANT UNCLAIMABLE
A POLICY CHANGE VOIDS AN IN-FLIGHT APPROVAL
A POLICY NEVER OVERRIDES A PERMANENT PRODUCT TRUTH
A POLICY NEVER OVERRIDES A BRAKE DENIAL
REPLAY CREATES NO AUTHORITY
M11 SHIPS DARK WITH ZERO PRODUCTION IMPORTERS
THE M12 RULE MACHINE IS NOT BUILT
THE M13 BRAKE MACHINE IS NOT BUILT
NOTHING GRADUATES
THE M1 WORK ITEM MACHINE IS UNCHANGED
THE M2 PIPELINE MACHINE IS UNCHANGED
THE M3 EFFECT AUTHORITY IS UNCHANGED
THE M4 APPROVAL MACHINE IS UNCHANGED
THE M9 EXCEPTION MACHINE IS UNCHANGED
THE M10 COMPENSATION MACHINE IS UNCHANGED
ACTIVATION REQUIRES AN AUTHENTICATED HUMAN
```

**And these alarm markers, printed only when the named defect actually occurred.** A marker the probe
can never emit is decoration; a marker it emits on a correct product is worse.

```
### A MODEL AUTHORED A POLICY ###                       ### A MODEL PROPOSAL BECAME AN ACTIVE POLICY ###
### INBOUND CONTENT AUTHORED A POLICY ###               ### AN EMAIL BECAME A POLICY CHANGE ###
### A COUNTERPARTY AUTHORED A POLICY ###                ### AN OFFBOARDED HUMAN AUTHORED A POLICY ###
### AN UNAUTHENTICATED ACTOR AUTHORED A POLICY ###      ### PolicyProposed TREATED AS ACTIVATION ###
### NULL GATE DECISION ACCEPTED ###                     ### GATE DECISION DEFAULTED SILENTLY ###
### GATE DECISION INHERITED BY ACCIDENT ###             ### FIFTH GATE MEMBER MINTED ###
### INVENTED GATE DECISION ACCEPTED ###                 ### MODEL_INFERRED PREDICATE COMPILED ###
### MODEL_INFERRED READ AT CONFIDENCE ONE ###           ### CONFIDENCE READ BY THE EVALUATOR ###
### CONFIDENCE FIELD PRESENT ON THE EVALUATOR INPUT ### ### UNMODELLED FIELD COMPILED INTO A PREDICATE ###
### PREDICATE ADMITTED AS A PROMPT STRING ###           ### NOTED THE PROCEDURE WITHOUT COMPILING A RULE ###
### TENANT POLICY BROADENED THE PRODUCT CEILING ###     ### CEILING COMPARISON WAS A STRING COMPARE ###
### CEILING ORDER INCOMPLETE ###                        ### PERMANENT ASSERTION GATE COLLAPSED INTO APPROVAL ###
### FORBIDDEN COLLAPSED INTO PERMANENT ASSERTION ###    ### AUTONOMY GRANTED WITHOUT A HUMAN ###
### ADMIN PATH TO APPROVED ###                          ### CONFIG FILE ACTIVATED A POLICY ###
### MIGRATION ACTIVATED A POLICY ###                    ### SUPERUSER COMMAND LINE ACTIVATED A POLICY ###
### POLICY CHANGE BYPASSED THE M2 PIPELINE ###          ### POLICY DIFF WAS NOT THE MATERIAL FACTS ###
### MISSING diff_fingerprint ON PolicyApproved ###      ### A MODEL APPROVED A POLICY CHANGE ###
### CROSS-TENANT APPROVAL ACCEPTED ###                  ### SECOND APPROVAL SYSTEM BUILT ###
### M4 SEMANTICS MODIFIED ###                           ### PolicyApproved TREATED AS PolicyActivated ###
### A MODEL ACTIVATED A POLICY ###                      ### AUTOMATION ACTIVATED A POLICY ###
### A RETRY HANDLER ACTIVATED A POLICY ###              ### A TIMER ACTIVATED A POLICY ###
### A SERVICE ACCOUNT BROADENED POLICY ###              ### ACTIVE WITHOUT AN ACTIVATOR ###
### ACTIVATED BY A NON-HUMAN ACTOR ###                  ### CROSS-TENANT ACTIVATION ACCEPTED ###
### UNAUTHORIZED ACTIVATION WENT UNRECORDED ###         ### SECOND UNAUTHORIZED-ACTIVATION CONTRACT MINTED ###
### PolicyActivated APPLIED RETROACTIVELY ###           ### RE-ACTIVATION BUMPED THE VERSION ###
### SUPERSEDED VERSION DELETED ###                      ### HISTORY EDITED IN PLACE ###
### POLICY APPLIED RETROACTIVELY ###                    ### OLD VERSION NO LONGER EXPLAINS ITS DECISIONS ###
### BROADENING REVOCATION BY AUTOMATION ###             ### REVOCATION DIRECTION MISSING ###
### NARROWING REVOCATION BLOCKED ON REVIEW ###          ### TEMPORARY TIGHTEN AUTO-REVERTED ###
### EXPIRY BROADENED AUTHORITY ###                      ### TimerFired BROADENED AUTHORITY ###
### BROADENING POLICY CARRIED AN EXPIRY ###             ### EXPIRY RAISED NO HUMAN CONFIRMATION ###
### AUTOMATIC BROADENING ###                            ### NON-DETERMINISTIC POLICY DECISION ###
### WALL CLOCK ENTERED THE DECISION ###                 ### RANDOMNESS ENTERED THE DECISION ###
### MODEL CALL ENTERED THE DECISION ###                 ### UNORDERED ITERATION CHANGED THE DECISION ###
### POLICY DECISION WITHOUT A REASON ###                ### ALLOW ON POLICY ERROR ###
### WITNESS MINTED WITHOUT A POLICY DECISION ###        ### EFFECT REACHED THE ADAPTER WITHOUT A POLICY DECISION ###
### UNREPRODUCIBLE DECISION CLAIMED A GRANT ###         ### M11 MINTED A GATE DECISION ###
### M11 REGISTERED A GATE ###                           ### SECOND GATE MINTER BUILT ###
### SECOND CHECKPOINT BUILT ###                         ### SECOND GATE REGISTRY CONSTRUCTED ###
### PRODUCTION GATE REGISTRY POPULATED ###              ### CHECKPOINT BYPASSED ###
### policy_version MISSING FROM THE WITNESS ###         ### policy_version MISSING FROM THE GRANT ###
### STALE POLICY VERSION CLAIMED A GRANT ###            ### STALE APPROVAL EXECUTED ###
### SECOND CLAIM CAS BUILT ###                          ### SECOND DRIFT-INVALIDATION MECHANISM BUILT ###
### M4 STATE MUTATED DIRECTLY BY M11 ###                ### PolicyVersionChanged BYPASSED A CONSUMER GUARD ###
### SCOPE-LOCAL POLICY VERSION NAMESPACE ###            ### THE VOID WAS NARROWED TO THE SCOPE THAT CHANGED ###
### UNDER-VOIDING CHOSEN OVER OVER-VOIDING ###          ### IN-FLIGHT AUTHORITY SURVIVED A POLICY VERSION CHANGE IN ANOTHER SCOPE ###
### POLICY OVERRODE A PERMANENT PRODUCT TRUTH ###       ### POLICY OVERRODE A BRAKE DENIAL ###
### URGENT POLICY BYPASSED THE BRAKE ###                ### M11 ENGAGED A BRAKE ###
### M11 NARROWED A BRAKE ###                            ### POLICY OVERRODE A CONSTRAINT ###
### CROSS-TENANT POLICY LOOKUP ACCEPTED ###             ### CROSS-TENANT SUPERSESSION ACCEPTED ###
### TENANT MISSING FROM THE PRIMARY KEY ###             ### GLOBAL UNIQUENESS COUPLED TWO TENANTS ###
### DUPLICATE ACTIVE POLICY ###                         ### TWO ACTIVE POLICIES FOR ONE SCOPE ###
### TWO ACTIVE POLICY OWNERS IN ONE TENANT ###          ### POLICY OWNER SINGULARITY UNENFORCED ###
### POLICY OWNER SINGULARITY LEFT TO REVIEW ###         ### AMBIGUOUS POLICY OWNER ACTIVATED A POLICY ###
### POLICY OWNER SINGULARITY COUPLED TWO TENANTS ###    ### SECOND AUTHORITY RECORD INVENTED FOR THE POLICY OWNER ###
### OCC BYPASSED ###                                    ### POLICY VERSION OVERWRITTEN IN PLACE ###
### POLICY VERSION REUSED ###                           ### POLICY VERSION WENT BACKWARDS ###
### POLICY ROW DELETED ###                              ### HISTORICAL VERSION DISCARDED ###
### REPLAY ACTIVATED A POLICY ###                       ### REPLAY MINTED AUTHORITY ###
### REPLAY MINTED A WITNESS ###                         ### REPLAY MINTED A GRANT ###
### REPLAY PRODUCED AN EXTERNAL EFFECT ###              ### UNREGISTERED EVENT MINTED ###
### NINTH F11 CONTRACT MINTED ###                       ### PolicyProposed AND PolicySubmitted COLLAPSED ###
### PolicyOverridden MINTED ###                         ### PolicyOverridden SIMULATED ###
### AN OVERRIDE MECHANISM WAS BUILT ###                 ### P6-D71 RESOLVED BY A BUILD SESSION ###
### PolicyEvaluated MINTED BY M11 ###                   ### STRICT ORDER WEAKENED ###
### CONTIGUITY REQUIRED WHERE ONLY ORDER IS ###         ### EVENT WITHOUT ITS STATE ###
### STATE WITHOUT ITS EVENT ###                         ### REQUIRED PAYLOAD FIELD DROPPED ###
### M12 RULE MACHINE BUILT ###                          ### M13 BRAKE MACHINE BUILT ###
### RULES TABLE CREATED ###                             ### AUTONOMY GRADUATION ENGINE BUILT ###
### PRODUCTION POLICY EDITOR BUILT ###                  ### POLICY ADMIN UI BUILT ###
### PRODUCTION IMPORTER OF POLICY ###                   ### CHANNEL JOINED ###
### NOTIFIER WIRED ###                                  ### OVERSIGHT QUEUE BUILT ###
### M11 PRODUCTION-ENABLED ###                          ### P7 PROVENANCE SURFACE BUILT ###
### V11 RESOLVED BY PREFERENCE ###                      ### V12 RESOLVED BY PREFERENCE ###
### PARALLEL ADMIN AUTHORITY INVENTED ###               ### M1 MACHINE EDITED ###
### M2 STATE MACHINE EDITED ###                         ### M3 EFFECT SEAM REWRITTEN ###
### M4 MACHINE EDITED ###                               ### M9 MACHINE EDITED ###
### M10 MACHINE EDITED ###
```

### The mutation battery

`scripts/mutate_phase6_policy.py` prints `N mutations caught, 0 escaped`. Each mutant reintroduces a
defect whose prohibition is canonically established, and each must turn the acceptance battery RED.
**Include an anti-vacuity control** — a mutant the battery is expected NOT to catch, or a
no-mutation run that must stay green — so the count is a measurement rather than an assertion. The
tree must be restored byte-identical afterwards, with `git status --porcelain` empty.

**At minimum, mutate:** a model activating; automation activating; inbound content authoring; the
tenant broadening the ceiling; a null gate decision; allow-on-error; a `MODEL_INFERRED` predicate;
confidence as an input; nondeterministic evaluation; `policy_version` omitted from the witness or
grant binding; a stale grant still claiming; a stale approval still executing; activation without the
governed change; a hidden admin path; a version overwritten in place; a deleted superseded row; two
ACTIVE policies for one scope; a cross-tenant uniqueness collision; cross-tenant activation; a
narrowing policy auto-expiring into broader authority; a broadening revocation by automation;
`PolicyProposed` treated as `PolicySubmitted`; `PolicyApproved` treated as activation; a policy
overriding the Permanent Truth; a policy overriding a brake denial; a second gate decision minted
outside the checkpoint; replay creating authority; replay emitting effects; strict order weakened; a
fabricated contiguity requirement; M12 built early; M13 built early; M11 production-enabled; OCC
bypassed; tenant dropped from an index; `policy_version` reused; old decisions reinterpreted under
the newest policy; a retry handler broadening; `TimerFired` broadening; a required payload field
dropped.

**Do not hard-code an expected mutation count anywhere.** The battery derives it.

---

## 5. What you must NOT do

- **Do not build M12 (Rule) or M13 (Brake).** Policy and Rule share compilation and lifecycle
  concepts by canon — `entities/14-policy.md`'s own opening paragraph says so — which is exactly why
  "shared machinery" is the door M12 arrives through. If M11 needs a reusable compiler primitive,
  build **only the minimum M11 itself requires**, and do not generalise it for a machine that does
  not exist.
- **Do not build an autonomy-graduation engine.** Nothing graduates (V11).
- **Do not populate the production `GateRegistry`.** It stays EMPTY until U8.1 / P8.
- **Do not build a policy editor, an admin screen, an oversight queue, a dashboard or a notifier.**
- **Do not join any outbound channel**, import a timer service, or reach any adapter.
- **Do not modify M1–M10.** They are landed. Their residuals are debt rows. ### **THE ONE
  ### NARROW EXCEPTION, AND IT IS NAMED: `P6-D72`'s Policy Owner singularity constraint on M1's
  ### `tenant_humans`** (§3.6). That is a constraint an M11 invariant requires, not a rebuild of a
  landed machine — it is **permitted**, it is **tier 1**, and it is the ONLY edit to an M1–M10
  table this unit may make. Everything else about M1–M10 stays untouched, and **M9 in particular
  is not edited at all** (`P6-D73`).
- **Do not touch the checkpoint kernel's semantics**, the witness's unconstructability, the claim
  CAS's `WHERE`-clause revalidation, or the brake.
- **Do not score a P6 criterion, move P6's status, or unlock P7.**
- **Do not re-open, re-litigate or silently re-decide the FIVE authority questions the canon
  correction SETTLED** (`M11-AQ-1`, `-2`, `-3`, `-5`, `-6`) — build what §3.6 says they now say.
- ### **Do not resolve `M11-AQ-4`/`P6-D71` (`PolicyOverridden`), `V11` or `V12`.** They close at a
  founder/architect decision, not a build session's. `M11-AQ-8`/`P6-D73` is answered by precedent:
  name the seam, leave it unwired.
- ### **`M11-AQ-7`/`P6-D72` IS THE ONE YOU MUST CLOSE**, mechanically, at M11 — exactly one ACTIVE
  `POLICY_OWNER` per tenant, on the existing `tenant_humans` authority record, with no second
  authority system invented. Leaving it unenforced is a FAILED unit, not a reported question.
- **Do not weaken, delete or subset-ify either ADR-010 boundary guard** (§3.7).

---

## 5a. The review tier, stated once

`CLAUDE.md` §7 scales review with risk, and says: *"When genuinely torn between two tiers, take the
higher one once and say so."*

A state machine on its own is tier 2. **M11 is tier-1**, for three reasons, and this file says so
rather than leaving a later session to argue it:

1. **It lands a migration** — a new canonical table, new indexes, new triggers, and an edit to
   `schema.py`'s canonical partition.
2. **It is load-bearing for tenant isolation** — `policies` is tenant-first, its uniqueness is
   tenant-scoped, and a global uniqueness that coupled two brokerages would let one brokerage's
   posture decide another's gate.
3. **It is the authority mechanism every other gate in the system already depends on.** The
   checkpoint, the grant, the approval and the brake all already read `policy_version` and a gate
   decision. Getting M11 wrong does not break M11; it silently changes what every one of those
   already-landed guarantees means.

So M11 takes the higher tier once and says so, and this file says so: a focused independent review
by a session that did not build it is **owed** before this lands.

---

## 6. How you will be measured

Product Driver runs `scenarios/p6_m11_policy.yaml` — the permanent scenario — plus generated
adversarial scenarios, then a completion audit and an independent review.

**The permanent scenario measures the DATABASE, the EVENT REGISTRY and the AST, not your narration.**
Eighteen persisted-state and registry oracles, including one that issues twelve forbidden writes
against a live canonical database behind **four positive controls** and an asserted surviving-row
count, one that walks the AST to prove `checkpoint.py` is still the sole gate minter (with the
kernel's own `_DEFAULT` as the positive control), one that proves the ADR-010 carrier population
equals the stated boundary, and one that reads the event names you emit from the AST — excluding
docstrings, so a comment saying *"`PolicyNarrowed` is deliberately NOT minted here"* cannot trip it,
and a real string literal can.

**Every battery is invoked as `python -m pytest`, never the bare `pytest` console script**, and
`no tests ran` and `ERROR: file or directory not found` are globally forbidden — so a battery cannot
report the absence of a failure as the absence of a defect.

If a scenario oracle is wrong, **say so and show the evidence**; do not change the product to satisfy
a defective oracle. An oracle that cannot pass on a correct product is the mirror image of a false
green.

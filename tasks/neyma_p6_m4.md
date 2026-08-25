# Build P6 / M4 — Approval. Only that.

This is the goal Product Driver gives the builder session inside the Neyma repository. Pass it
with:

```bash
.venv/bin/python -m neyma_product_driver run \
  --task "$(cat tasks/neyma_p6_m4.md)" \
  --scenario p6_m4_approval
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
5. `docs/specifications/entities/06-approval.md`
6. `docs/specifications/state-machines/04-approval.machine.md`
7. `docs/specifications/state-machines/registry.md` — §1 triggers, §2 the transition-row
   defaults, §3 `GR-1`…`GR-17`, §4 the canonical state registry, §5 the canonical event
   registry. **No machine may define a local synonym**, so every state and every event name you
   write must already be in §4 or §5.
8. `docs/architecture/decisions/ADR-005-approval-binding-and-drift.md` — approval binding and
   material-facts drift. This is the ADR the unit exists to implement.
9. the checkpoint, policy, brake and event specifications that bear on an approval:
   `docs/specifications/entities/14-policy.md`, `16-brake.md`, `17-audit-event.md`,
   `docs/specifications/state-machines/11-policy.machine.md`, `13-brake.machine.md`, and the
   event registry entries for `Approval*`
10. the existing **P3** checkpoint kernel — `src/freight_recon/checkpoint.py` (steps 1 and 2 are
    where an approval is validated and where drift is detected) and
    `src/freight_recon/fingerprint.py` (the `fp_v1` canonical serialization already exists —
    **you consume it, you do not rewrite it**)
11. the **P4** governed effect boundary
12. the **P5** event transport, outbox/inbox, replay isolation and durable timers
13. **M2** Pipeline Instance (`src/freight_recon/pipeline_instance.py` and its migration) —
    `AWAITING_APPROVAL` is M2's state and the M2/M4 seam is co-committed
14. **M3** External Effect / Effect Grant (`src/freight_recon/external_effect.py`,
    `docs/specifications/state-machines/03-external-effect-grant.machine.md`) — **`EF-2` is
    where the approval is consumed, and M3 remains the single effect authority**

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
like agreement. **§3.9 below names one such conflict that is already known. Read it before you
write the transition table.**

---

## 1. What Neyma is — the stable identity

Neyma is an **AI-native operating platform and system of action for SMB freight and logistics
companies.**

It is **not** an invoice bot, a document-extraction product, a Slack bot, a TMS chatbot, a
browser wrapper, an AP tool, or a disconnected collection of agents. If a piece of legacy code
in this repository suggests otherwise, that code is material, not direction.

- **P0–P8** build the shared governed operating engine.
- **P9–P13** build freight operational capability on top of it.
- **P14** expands bounded autonomy.

## 2. Where the program stands

- **P0–P5 COMPLETE.**
- **P6 IN PROGRESS.**
- **M1** (Work Item, `P6-CP-1`) landed. **M2** (Pipeline Instance, `P6-CP-2`) landed.
  **`P6-D11`** resolved and landed. **M3** (External Effect / Effect Grant, `P6-CP-3`) landed,
  with its one focused independent review on disk.
- **M4 is the next build checkpoint. M4–M13 remain.**
- **No P6 acceptance criterion is scored.** P6 has not reached phase acceptance. **P7+ blocked.**
- **M1, M2 and M3 all ship dark, and M4 ships dark too.**

---

## 3. The unit: M4, and nothing else

### 3.1 The one sentence the whole unit is a consequence of

> ### **A HUMAN APPROVES AN ACTION PLUS THE EXACT MATERIAL FACTS THAT MADE IT CORRECT.**
> ### **IF THOSE FACTS CHANGE THERE IS NO APPROVAL. THERE IS A NEW QUESTION.**

The owner approved £2,850. The customer was invoiced £3,100. The audit log recorded a human
approval and every gate passed. That is `ADR-005` **F-01**, and it is the defect this unit
closes — **structurally**, not by convention.

An approval authorizes **one committed effect, not one network attempt** (spec §12.4 M-30), and
it is **consumed exactly once** (M-31).

### 3.2 The canonical state set

```
REQUESTED  GRANTED  CONSUMED  DENIED  EXPIRED  REVOKED  VOID_ON_DRIFT  VOID_ON_BRAKE
```

Non-terminal: `REQUESTED`, `GRANTED`. Terminal: the other six.

**Do not add a state.** In particular there is **no `SUPERSEDED`** — `ADR-005` §3.10 and entity
§24 decompose supersession into drift-void ∪ duplicate-refusal, and state that *there is no
third case, so there is no third state*. A ninth state is the mechanism this unit exists
without. **Do not add a state casually, and do not add one at all without saying so and
stopping.**

### 3.3 Implement the canonical `AP-*` transition contract

`docs/specifications/state-machines/04-approval.machine.md` §14 is the contract. Implement
**those rows**, with **those ids**, **those triggers**, **those guards**, **those writes** and
**those events**. Do not design an alternative lifecycle that "achieves the same thing":

| ID | From → To | What it is |
|---|---|---|
| **AP-1** | — → `REQUESTED` | the request, fingerprinted from **runtime reads** |
| **AP-2** | `REQUESTED` → `GRANTED` | an **authenticated, authorized human** grants |
| **AP-2d** | `REQUESTED` → `DENIED` | a human denies |
| **AP-3** | `{REQUESTED,GRANTED}` → `EXPIRED` | TTL elapsed, fired by a **durable timer** |
| **AP-4** | `GRANTED` → `VOID_ON_DRIFT` | live fingerprint ≠ fingerprint at approval |
| **AP-4p** | `GRANTED` → `VOID_ON_DRIFT` | `policy_version` changed |
| **AP-5** | `GRANTED` → `VOID_ON_BRAKE` | `BrakeEngaged` in scope |
| **AP-6** | `GRANTED` → `REVOKED` | a human revokes before consumption |
| **AP-7** | `GRANTED` → `CONSUMED` | atomic CAS **in the same txn as the M3 claim** |
| **AP-8** | `GRANTED` → `GRANTED` | survives a **provably failed** attempt |
| **AP-9** | `GRANTED` → `GRANTED` *(frozen)* | **frozen** after an unknown-outcome attempt |

Every one of the **registry §3 global rules** applies. The ones that bite here: `GR-1`
(anything not enumerated is ILLEGAL, and emits `IllegalTransitionAttempted`), `GR-2` (state
change and event are ONE commit), `GR-3` (OCC), `GR-4` (inbox idempotency), `GR-7`/`GR-8` (a
model never performs a judgment transition and `MODEL_INFERRED` never gates one, at any
confidence), `GR-10` (an open Conflict on a material field blocks), `GR-11` (replay creates no
authority), `GR-13` (a consequential transition revalidates the SD-3 entity-version set, and
**the implementer may not choose that set dynamically**), `GR-17` (consuming an approval is by
definition a consequential transition).

### 3.4 The material fact set, and why the fingerprint is not just a hash

`ADR-005` §3.2 lists the thirteen material fields. Two of them are the ones an implementation
gets wrong:

- **`provenance_class` of every material field is INSIDE the fingerprint** (§3.3, M-56). *The
  same number believed for a different reason is a different fact.* £2,850 read from the TMS
  invoice screen and £2,850 extracted from a rate-confirmation PDF are two different decisions,
  and the second was never approved. This closes the *swap-the-evidence-keep-the-number*
  laundering route.
- **`evidence_condition` of every material field** (§3.2 #9). Approving on `consistent`
  evidence is a different decision from approving on `stale` evidence, and a degradation to
  `stale`/`unknown`/`conflicting` is drift.

**`fp_v1` already exists** in `src/freight_recon/fingerprint.py`, with its version pinned
inside the hashed bytes and its property tests written. **Consume it. Do not reimplement it and
do not "improve" its serialization** — a serialization change is the single most dangerous bug
class in ADR-005 (§7), because it produces false no-drift, which is a wrong payment.

**Retain the full canonical payload, not just the hash** (§3.5). A hash can prove that
something drifted; it can never say *what*. The drift diff is generated from the retained
payloads, and `drift_diff` is a **required output, not a log line** (§3.13): it must name every
changed field, its old and new value, and the provenance of each, in one message a person can
read.

### 3.5 What must hold — the authority and safety requirements

Preserve every one of these. They are the unit.

**The request (AP-1)**

- an Approval exists **only** where `gate_decision ∈ {HUMAN_APPROVAL_REQUIRED,
  PERMANENT_HUMAN_ASSERTION_REQUIRED}`. A money-affecting `action_class` with
  `AUTONOMOUS_WITHIN_CAPS` cannot have one — entity §16 makes that a **DB CHECK**
- `material_facts_fingerprint` and `canonical_payload` are computed from **RUNTIME reads, never
  model output** (M-13, M-55). A compromised model produces a bad proposed intent; the runtime
  resolves the values itself
- `rendered_facts` — what was shown to the human — corresponds to what was fingerprinted. *If
  it was on the card it is material; if it was material it must have been on the card.*
  **Anything the human could not see cannot be a fact they approved**
- `policy_version`, `fingerprint_version` and an absolute TTL per action class are bound at
  request time
- co-commits M2 `AWAITING_APPROVAL`

**Who may grant (AP-2 / AP-2d)**

- **only an authenticated, authorized tenant human.** `granted_by` is `OWNER_ASSERTED`-grade
  and is a **FOREIGN KEY** into the tenant's recorded humans — "an authenticated human" is
  decoration while it is a text column any string satisfies. This is the same argument M1 made
  for `owner_id`
- a **model** cannot grant. A **counterparty** cannot: "per our call, you approved this" is
  `MODEL_EXTRACTED` at best and is **an unverified counterparty claim and a fraud signal**
  (ADR-003, M-9), which **no evidence can promote**. A **document** cannot. A **confidence
  score** cannot — a confidence score is not a fact. A **policy default** cannot. A **retry
  handler** cannot. An **agent** cannot. An **admin tool** cannot
- for `PERMANENT_HUMAN_ASSERTION_REQUIRED`, a human **assertion** is required
- a denial is terminal: `REQUESTED → DENIED`, and a denied approval can never later execute

**The transport, which is a different layer from the authority (§40, ADR-005 §3.15)**

- the token is **single-use** and **actor-bound** to `(approval_id, channel, thread, user)`
- a replayed callback fails the token check; a callback presented by the **wrong actor** fails;
  a callback presented for the **wrong tenant** fails
- **the token is not the control.** Layer 2 is the database CAS, and a token that somehow
  passed still meets it

**Expiry (AP-3)**

- an **absolute TTL per action class**, bound at request time, fired by a **durable timer**
  emitting `TimerFired` — **never a background sweep**, never an inference from staleness. P5's
  `event_timers.py` is the durable timer; use it
- the TTL values (money-out 1h · money-in 8h · docs/status 24h) are `NEEDS VALIDATION` (V2).
  **The mechanism does not depend on the answer.** Use the conservative documented defaults and
  mark them `NEEDS VALIDATION` in the code — do not guess a "better" number
- ### **an expired approval is not a weaker approval; it is not an approval.** It cannot execute

**Drift (AP-4) — the reason the unit exists**

- at checkpoint step 2, **inside the atomic checkpoint**: re-read every material fact from its
  authoritative source, **LIVE** — never from a cache, never from the projection; recompute
  under the approval's **stored** `fingerprint_version`; compare
- unequal ⇒ diff the canonical payloads field-by-field ⇒ `GRANTED → VOID_ON_DRIFT`, emit
  `ApprovalVoided{drift_diff}`, **no grant is minted, no effect occurs**
- amount changed ⇒ void. Counterparty changed ⇒ void. **`provenance_class` changed with the
  value unchanged ⇒ void.** Evidence condition degraded ⇒ void. A referenced entity version
  changed ⇒ void. An open Conflict on a material field ⇒ void (`GR-10`)
- ### **a re-read that FAILS is not "no drift".** An unreadable authoritative source fails
  closed. *We do not execute money against a source we could not read*
- there is **no tolerance band**. §5 of ADR-005 rejects it by name: a tolerance is a licence to
  be wrong by a bounded amount, chosen by an engineer, applied to someone else's money

**Policy (AP-4p)**

- `policy_version` is a material fact. A policy change **voids in-flight approvals granted
  under the old policy.** You cannot act under a policy that no longer exists, and a policy
  change that does not bind in-flight work does not mean anything
- **no stale approval survives a policy tightening**

**Brake (AP-5) and revoke (AP-6)**

- `BrakeEngaged` in scope before consumption ⇒ `VOID_ON_BRAKE`, **zero effect**. A brake racing
  a claim obeys the canonical precedence: §16 puts the voids **above** the consume, so if the
  void commits first the claim's CAS matches zero rows
- an authenticated human may revoke a `GRANTED` approval before consumption; a revoked approval
  cannot execute

**Consumption (AP-7) — the atomic seam with M3**

- ### **`GRANTED → CONSUMED` is an atomic CAS in the SAME transaction as the M3 claim CAS**
  (entity §15, machine §4, M3 `EF-2`, spec §21.3 layer 2). `commit_key` must match
- there is **no state where the approval is consumed but the claim was not durably won**, and
  **none where the claim succeeded and the approval remains reusable**
- **a double tap is idempotent, not an error.** The second finds `CONSUMED` and replies
  *"already done — invoice 560010, sent at 09:52"*. It **raises nothing and acts nothing**. *An
  owner tapping twice because Slack was slow must never be punished with an error, and never
  rewarded with a second invoice*
- **M3 remains the single effect authority.** M4 must NOT create a second effect-authority
  path, must not mint a gate decision, and must not claim a grant

**Dual control (§16, ADR-005 §3.16)**

- `ApprovalSignature{approval_id, actor_id, signed_fingerprint, signed_at}` is an **evidence
  record attached to the existing machine**, not a new lifecycle and not a new primitive
- `REQUESTED → GRANTED` only on **quorum by distinct authenticated actors**. A duplicate actor
  does not satisfy quorum
- ### **every signature binds the SAME fingerprint**, and **drift between signature 1 and
  signature 2 voids ALL signatures** ⇒ back to `REQUESTED` with a fresh fingerprint, and every
  human signs again. *A second approver shown different facts from the first is not a control;
  it is two people approving two different things and believing they agreed*
- **which classes need dual control, and at what threshold, is `NEEDS VALIDATION` (V3)** — the
  mechanism is complete and does not depend on the answer. Fail-closed default: single approval
  unless configured

**What an approval can never become**

- a **partial approval does not exist.** *"Approve it, but for £2,700"* is a **new proposal with
  a new fingerprint**, requiring a new approval. It is **not** a mutation of the existing one,
  and the old approval is never "refreshed", "extended" or "re-validated in place"
- a re-approval is **always a NEW Approval with a NEW fingerprint**

**Uniqueness and the database (entity §16–§18)**

- tenant-first throughout, following the repository's canonical column name `tenant` (the
  entity specs write `tenant_id`; every landed table in this repository writes `tenant`, and
  `schema.py`'s `TENANT_COLUMN` is the authority on the spelling)
- PK `(tenant, approval_id)`
- ### **`UNIQUE (tenant, commit_key) WHERE state IN ('REQUESTED','GRANTED')`** — at most one
  live approval per effect, **enforced by the database, not hoped for by the application.** A
  re-approval supersedes only after the prior is terminal
- `NOT NULL` on `tenant, commit_key, action_class, state, version, material_facts_fingerprint,
  canonical_payload, fingerprint_version, policy_version, gate_decision, expires_at`
- ### **CHECK: `state = 'GRANTED'` requires a non-null `granted_by`.** Entity §37 names a
  GRANTED approval with no `granted_by` as a structurally impossible state, and the only
  version of that sentence a database enforces is a CHECK
- **CHECK: a money-affecting `action_class` cannot carry `gate_decision =
  AUTONOMOUS_WITHIN_CAPS`** — an Approval would not exist
- `granted_by` FK → the tenant's recorded humans; `commit_key` consistent with the Pipeline
  Instance
- OCC `[C-10]`: `version` advances by exactly one under `WHERE version = :expected`
- `schema_readiness_problems` must still return `[]` on a freshly created canonical database
  with foreign keys enabled and verified

**Replay, redelivery, events and audit**

- ### **replay reconstructs approval history and creates ZERO authority**: zero
  `HumanApproved`-derived grants, zero re-grants, zero consumptions into an effect, zero
  external effects (`GR-11`, K-3)
- a redelivered canonical event is a **no-op** at the consumer inbox `(consumer, tenant,
  event_id)` (`GR-4`): no duplicate transition, no duplicate approval, no duplicate effect
- ### **state mutation and the canonical outbox/audit event co-commit** (`GR-2`). No state
  change without its event; no event without its transition
- every request / grant / void / expiry / consume is an Audit Event **with its actor**, and
  every void carries its `drift_diff` and reason
- events are exactly the registry §5 names: `ApprovalRequested{fingerprint, gate_decision}`,
  `ApprovalGranted`, `ApprovalDenied`, `ApprovalExpired`, `ApprovalRevoked`,
  `ApprovalVoided{cause ∈ {drift, policy, brake}, drift_diff?}`, `ApprovalConsumed`,
  `ApprovalFrozen{frozen=true, unknown_outcome_ref, effect_grant_id, frozen_at}`,
  `IllegalTransitionAttempted{machine, state, trigger}`. **Mint no others**

**Tenant isolation and retention**

- no cross-tenant read, write, grant, revoke or consumption. The same `commit_key` in two
  tenants is two isolated approvals
- retention is **permanent** `[C-9]`: the full canonical payload, the fingerprint and its
  version, `policy_version`, every signature with actor and timestamp, what was rendered to the
  human, every void with its diff and reason. ### **You must be able to reconstruct, years
  later, exactly what the human saw when they said yes.** That is the evidentiary point of an
  approval, and it must be structurally represented rather than asserted in a comment

**Crash and recovery (§36, ADR-005 §7)**

- a `GRANTED` approval **survives** an ordinary crash before consumption; recovery **re-runs
  the checkpoint, including the live drift check**, before any claim
- a crash **after** `CONSUMED` and before the effect is confirmed does **NOT** return the
  approval to `GRANTED` and does **not** make it reusable; the downstream ambiguity is governed
  by M3's `UNKNOWN_OUTCOME` semantics and only a human may establish reality

**Illegal transitions (§15, `GR-1`)**

- `CONSUMED` + anything ⇒ ILLEGAL (single use)
- a grant by a model or a counterparty ⇒ ILLEGAL, and a **Sev-0 fraud signal**
- reuse of a frozen (AP-9) approval ⇒ ILLEGAL
- a terminal state stays terminal
- every illegal `(state, trigger)` **raises a hard domain error, persists nothing, and emits
  `IllegalTransitionAttempted`** to audit and security. Half-applying one is the defect

### 3.6 The AP-9 freeze, and the residual you must NOT close

`AP-9` writes `frozen=true` and emits `ApprovalFrozen`. A frozen approval **must not be reused
until reality is established**. No timer unfreezes it (`GR-6`). No retry handler unfreezes it.

### **The repository has recorded, as open residual `G2-D15`, that the UNFREEZE direction is
unmodelled.** No M4 transition row clears `frozen`; there is no `ApprovalUnfrozen` event;
`RealityEstablished` is an M3 fact on a different aggregate whose declared payload does not
mention the flag. The consequence is that a full-history rebuild reconstructs an approval as
**still frozen** — strictly safer than the original, which is why the residual is nonblocking.

**Do NOT invent:**

- an `ApprovalUnfrozen` event
- an unfreeze transition
- a 135th transition row
- a new canonical event of any kind
- a hidden `RealityEstablished` write that clears the flag as a side effect

unless **current authoritative repository material has explicitly changed and now authorizes
one** — in which case cite the exact document and clause. Otherwise: **fail closed and preserve
the recorded residual.** A previous candidate was rejected for asserting an unfreeze mechanism
the repository had not established; the sentence was deleted rather than restated, and this is
the record of why.

The verification scenario asserts this mechanically: nothing under `src/freight_recon/`,
`scripts/` or `docs/specifications/` may mention `ApprovalUnfrozen` or an equivalent, and
`--inject unfreeze` must be refused as an unknown fault like any other invented one.

**And the other half of the freeze, which is a rule about REPLAY: `ER-16`.**
`docs/specifications/events/registry.md` §10 (the global `ER-n` semantic rules) states it
directly, and it is the reason `ApprovalFrozen` is a canonical event at all rather than a
boolean somebody sets:

> ### **A quarantine fact is reconstructed from POSITIVE evidence, never from an absence.**
> `ApprovalFrozen` is the sole canonical evidence that an approval is frozen (AP-9): a
> full-history rebuild sets `frozen=true` because that event is **present**, and **never** by
> inferring it from `OutcomeUnknown` **AND NOT** `RealityEstablished`.

So `ApprovalFrozen` must carry its `unknown_outcome_ref` (`state-machines/registry.md` §5
declares the payload as `frozen=true, unknown_outcome_ref, effect_grant_id, frozen_at`), and
the rebuild must read the event. **An absence is only as true as the fold is complete and
correctly ordered**, and a safety-critical quarantine may not depend on either being so.
Note what `ER-16` does *not*
relax: at RUNTIME an approval with an unresolved `OutcomeUnknown` is still treated as frozen —
the rule governs what replay may RELY ON, not what the machine may assume when it is live.

### 3.7 The M2/M4 seam

`AP-1` co-commits M2 `AWAITING_APPROVAL`. `AP-2`'s grant and the pipeline's `ApprovalBound` are
the M2-side facts; the event has **one producer** (registry §5) and the other machine
**consumes** it. Transactional consistency across the seam is required: a crash must not create
an orphan approval with no pipeline, or a pipeline waiting on an approval that does not exist.

### 3.8 The M3/M4 seam

`approval_id` is bound on the grant at `EF-1` (a DB CHECK when the gate is human-gated) and
consumed in the claim CAS at `EF-2`. Consumption **participates in M3's claim serialization** —
it does not add a second serialization point. **M3 remains the single effect authority and M4
must not create a second effect-authority path.** `effect_grants` already carries `approval_id`
and the checkpoint already carries the approval as a typed input; what M4 adds is the durable
Approval the checkpoint reads and the claim consumes.

### 3.9 ⚠️ THE KNOWN AUTHORITY QUESTION — read this before writing the transition table

**Do not resolve this yourself. If your implementation is forced to take a position on it,
STOP AND REPORT IT, with the clauses quoted, and let Product Driver surface it.**

Three clauses put `GRANTED → CONSUMED` inside the **claim** transaction:

- entity `06-approval.md` §15: *"`GRANTED → CONSUMED` is an atomic CAS in the SAME transaction
  as the Effect Grant claim (spec §21.3)."*
- machine `04-approval.machine.md` §4: *"`GRANTED→CONSUMED` co-commits with the M3 claim CAS"*,
  and AP-7's guard: *"atomic CAS in the SAME txn as the M3 claim; commit_key matches."*
- machine `03-external-effect-grant.machine.md` `EF-2`, Writes: *"`claimed_at`; co-commit M2
  `CLAIMED`, M4 `CONSUMED`"* — and `EF-2` emits `EffectAttempted` **before** the adapter call.

Two rows in the same machine are written **from `GRANTED`**, on triggers that can only arrive
**after** that claim transaction has committed:

- `AP-8` `GRANTED → GRANTED` on `AttemptFailedProvably` — which is M3's `EF-3f`, **from
  `CLAIMED`**. §20: *"an approval survives a provable failure (AP-8) and may authorize a NEW
  pipeline instance under the SAME commit_key; consumed exactly once."*
- `AP-9` `GRANTED → GRANTED (frozen)` on `AttemptOutcomeUnknown` — which is M3's `EF-3u`, also
  **from `CLAIMED`**.

And spec §12.4's own table names the AP-7 trigger as **`EffectCommitted`** while its guard says
the CAS is *"in the grant-claim transaction"* — and machine §3 says an approval authorizes
**one committed effect, not one network attempt**.

Under the claim-time reading, `AP-8` and `AP-9` are unreachable, because §15 makes
`CONSUMED` + anything ILLEGAL. Under the commit-time reading, `AP-7`'s "same txn as the claim"
is false. **The corpus does not currently say which.**

What you must do:

1. **Implement `AP-7` exactly as the three claim-transaction clauses state it.** That reading is
   stated in three separate authorities and is the one M3 already implements.
2. **Implement `AP-8` and `AP-9` exactly as written**, with their ids, triggers, guards and
   writes — including `ApprovalFrozen`, which is a registered canonical event.
3. **Do not invent a reconciliation.** Do not add a state, a transition, a flag or an
   "un-consume" path to make both readings true at once. Do not silently pick one and call the
   other a typo.
4. **Report the conflict explicitly**, quoting the clauses above, and say precisely what your
   implementation does at that seam and what it therefore cannot prove. A blocked point that is
   named is worth more than a green one that hides a decision nobody made.
5. The verification scenario asserts, for these cases, only what **every** reading agrees on:
   **no second effect, no second grant of authority, nothing reusable.** It asserts nothing
   about which reading is right, and neither may you.

### 3.10 The approval aggregate is STRICT-ORDER, and that is an obligation you inherit

`docs/specifications/events/registry.md` §8 names the strict-order families explicitly, and
**`F4 Approval` is one of them** — alongside F2 Pipeline, F3 Effect/Grant, F11 Policy and F13
Brake — *"their version-monotonic transitions depend on it"*. M3 discharged this obligation
when it landed (`P6-D24` and §8's complete-stream rule). **M4 does not inherit M3's discharge;
it inherits the obligation**, on its own aggregate.

Four clauses of §8 bind you, and each one is a defect this repository has already paid for:

1. ### **STRICT MEANS *ORDER*. IT HAS NEVER MEANT *CONTIGUOUS*** (`P6-D11`). A version with no
   event on the stream is **NORMAL, not a loss** — a transition whose canonical event belongs
   to another machine's aggregate advances its own version and emits nothing on its own
   stream. M4 has exactly such rows: `AP-8` is `NON_PRODUCING:ENUMERATED_NO_OP`, and the M2/M3
   co-commits are others. A consumer that treats every missing version as an unarrived event
   parks at the first one **and never unparks**, because nothing will ever fill it.
2. ### **SO THE SUCCESSOR DECLARES WHAT IT FOLLOWS.** Every producer on a strict-order
   aggregate sets §1's **`previous_aggregate_version`**. The consumer's rule is: block **iff
   `previous_aggregate_version` is ABOVE its applied high-water mark** — never on the mere
   absence of a version number. ### **Absence may never be read as "there is nothing before
   me."** This is `ER-16`'s principle one level down, and §3.6 is the same rule again.
3. ### **THE LINK IS VERIFIED AT THE BOUNDARY THAT OWNS IT** — the transactional outbox, in
   the same transaction as the state write. A producer declaring a predecessor its own emitted
   history does not hold is **REFUSED BEFORE THE INSERT**, so a wrong link can never tell a
   consumer to apply past a real event. It is **not** a second sequence beside
   `aggregate_version`; the ordering key stays `(tenant_id, aggregate_id, aggregate_version)`.
4. ### **A STRICT-ORDER CONSUMER MUST CONSUME THE *COMPLETE* AGGREGATE STREAM, NEVER A FAMILY
   SUBSET** (`P6-D11`, review F-3 / adjudication A-3). The predecessor chain is formed over
   **every event emitted on the aggregate**, not over the events of one family. The live case
   is `IllegalTransitionAttempted`: an **F14 order-tolerant** contract that rides the
   strict-order aggregate at the attempt's unchanged version, becoming the sole occupant of
   that version and therefore the **declared predecessor** of the next event. A consumer
   subscribing to the `Approval*` family alone then blocks on a predecessor it deliberately
   discarded and **never unblocks** — the same permanent silent stall
   `previous_aggregate_version` exists to prevent, reintroduced from the consumer side.
   Family classification governs what ordering guarantee a family's own stream carries; ### **it
   has never governed what a consumer may SKIP on someone else's aggregate.**

M4 emits `IllegalTransitionAttempted` too — the event registry lists its producer as **all**
machines, and §15 names three illegal transitions for this one (`CONSUMED` + anything, a grant
by a model or counterparty, and reuse of a frozen approval) — so this is not a hypothetical
for this unit. **Do not
introduce a new sequencing mechanism.** §8 is explicit that this is a requirement on the
SUBSCRIPTION, not on the envelope; `external_effect.py`'s `consume`/`drain_handler_for` is the
worked example, and you follow it rather than inventing a second one.

---

## 4. What you must produce

Follow the existing P6 naming conventions — `work_item.py`/`phase6_work_items.py`,
`pipeline_instance.py`/`phase6_pipeline_instances.py`,
`external_effect.py`/`phase6_external_effects.py`. These exact paths are what the permanent
verification scenario `p6_m4_approval` looks for; a different name is a scenario failure, not a
style preference. If you believe a different name is genuinely better, **say so and stop**
rather than renaming unilaterally.

| Path | What it is |
|---|---|
| `src/freight_recon/approval.py` | the machine (follows `external_effect.py`) |
| `src/freight_recon/migrations/phase6_approvals.py` | the schema change (follows `phase6_external_effects.py`) |
| `eval/tests/test_phase6_approval.py` | the acceptance and hostile battery |
| `scripts/probe_phase6_approval.py` | the deterministic narrative probe |
| `scripts/mutate_phase6_approval.py` | the mutation battery (follows `mutate_phase6_external_effect.py`) |

Wire the migration into `schema.py` and the P2 migration path the way
`phase6_external_effects.py` is wired, so a freshly created canonical database and a migrated
one build to the same shape and the readiness oracle DERIVES the contract from the DDL rather
than from a second list.

### The probe's interface

`scripts/probe_phase6_approval.py` must support:

- **no arguments** — run every case; exit `0` only if every one behaved as specified
- `--list-cases` — print the case names, one per line, and exit `0`
- `--list-dimensions` — print every dimension flag and every fault name, and exit `0`
- `--case <case>` — run exactly one case and exit `0` / non-zero

`--case` is what makes M4 testable by Product Driver's dynamic scenario generator: a generated
scenario may not author shell, so a focused, safe, argument-only entry point is the *only* way
it can compose new situations out of M4's real behaviour. Take the interface seriously.

### The mutation axis

M4 ships dark — no service, no HTTP surface, no live approval channel — and the driver's only
external concurrency primitive is HTTP. **Every ordering, concurrency, timing, drift, crash and
redelivery variation for M4 has to be reachable through this probe's arguments or it is not
reachable at all.** The probe must therefore accept, composable with `--case`:

| flag | range | what it varies |
|---|---|---|
| `--concurrency <n>` | 1–8 | how many actors race the consume CAS |
| `--delay-ms <n>` | 0–5000 | timing skew between actors, and between signatures |
| `--repeat <n>` | 1–5 | double-tap / redelivery pressure |
| `--tenants <n>` | 1–3 | isolation pressure |
| `--signers <n>` | 1–4 | dual-control quorum size |
| `--seed <int>` | any | deterministic interleaving — **the same seed reproduces the same run** |
| `--inject <fault>` | the closed set below | what goes wrong, and when |

**The closed fault vocabulary.** Every member is a transition or a clause of
`04-approval.machine.md` or `ADR-005`; none is invented here:

```
none                        (default — nothing injected)
drift-amount                AP-4    the money moves between grant and re-check
drift-party                 AP-4    the counterparty moves
drift-provenance            AP-4 / ADR-005 §3.3   same value, different basis
drift-evidence-condition    AP-4 / ADR-005 §3.14  consistent -> stale/unknown/conflicting
drift-entity-version        AP-4    a referenced entity version moves
source-unreadable           ADR-005 §3.12  the live re-read fails; fail closed, not "no drift"
policy-bump                 AP-4p   policy_version moves under a live approval
brake-engage                AP-5    a brake engages in scope before consumption
human-revoke                AP-6    the human revokes before consumption
ttl-elapse                  AP-3    the durable timer fires
provable-failure            AP-8 / M3 EF-3f
outcome-unknown             AP-9 / M3 EF-3u
double-tap                  §19 / ADR-005 §3.15  the second callback arrives
replay-token                §40 / ADR-005 §3.15 layer 1
wrong-actor                 §40     the token is presented by another actor
crash-before-consume        §36
crash-after-consume         §36
redeliver                   GR-4 / §19
signature-drift             §16 / ADR-005 §3.16  facts move between signature 1 and 2
forge-token                 §40     a forged transport authority, naming no real row
wrong-target                §40     authority presented against a different target
drop-predecessor            events §8  an event is lost; its successor names an unapplied one
reorder-stream              events §8  delivery order is permuted within the aggregate
freeze-by-absence           ER-16   rebuild frozen from OutcomeUnknown AND NOT RealityEstablished
```

**Closed means closed.** An unknown fault name, or a value outside the stated range, must
**exit 2** and print a readable message containing `unknown fault` — **not** a traceback, and
never a silent fallback to `none`. The verification scenario runs `--inject not-a-real-fault`
as a negative control **and runs `--inject unfreeze` as a second one**, because `unfreeze` is
precisely the mechanism §3.6 says does not exist; a probe that accepted it would be producing
evidence for a transition nobody authorized. This is the line between a bounded mutation axis
and fuzzing: a probe that accepts anything is a probe whose passing runs mean nothing.

**Determinism is what makes a discovery durable.** `--seed` must fully determine the
interleaving, so a failure the generator finds at
`--case dual-control-drift-voids-signatures --signers 3 --delay-ms 40 --inject signature-drift --seed 7`
can be re-run, handed back as a grounded correction, and later promoted into permanent
regression coverage. A failure nobody can reproduce teaches nothing.

An injected fault that is meaningless for a given case (`signature-drift` against
`human-denial-is-terminal`, say) should exit 2 with a clear message as well. Refusing an
incoherent combination is better than running a degenerate one and reporting a pass.

**The case names, exactly:**

```
runtime-fact-binding                    consume-cas-in-the-claim-txn
model-output-cannot-manufacture-authority  double-tap-is-idempotent
authenticated-authorized-human-grant     provable-failure-ap8
model-cannot-grant                       unknown-outcome-freeze-ap9
counterparty-cannot-grant                frozen-approval-not-reusable
human-denial-is-terminal                 crash-before-consume-survives
single-use-transport-token               crash-after-consume-not-regranted
replayed-token-refused                   dual-control-distinct-actors
wrong-actor-token-refused                dual-control-drift-voids-signatures
expiry-is-not-an-approval                partial-approval-is-a-new-proposal
amount-drift-voids                       live-approval-uniqueness
party-drift-voids                        m2-awaiting-approval-seam
provenance-drift-voids                   m3-claim-serialization-seam
evidence-condition-drift-voids           database-invariants
entity-version-drift-voids               replay-zero-approval-authority
unreadable-source-fails-closed           redelivery-idempotency
drift-diff-is-human-readable             transactional-co-commit
policy-version-drift-voids               tenant-isolation
brake-voids-before-consume               retained-canonical-payload
human-revoke-before-consume              terminal-states-stay-terminal
forged-authority-refused                 strict-order-predecessor-declared
wrong-target-authority-refused           complete-aggregate-stream-consumed
frozen-reconstructed-from-positive-evidence
```

The last five are the ones a reader skims past, so they are named again here with the clause
they come from: `forged-authority-refused` and `wrong-target-authority-refused` are §40 (a
forged handle names no row; authority is bound to ONE target);
`strict-order-predecessor-declared` and `complete-aggregate-stream-consumed` are §3.10's
`events/registry.md` §8 obligations; `frozen-reconstructed-from-positive-evidence` is §3.6's
`ER-16`.

### The probe's output contract

The verification scenario matches these as **literal substrings**. Print them exactly. They are
not decoration — each one is the sentence that makes a behaviour observable to something other
than the session that wrote it.

**Must appear on a correct run:**

```
behaviours as specified, 0 wrong
AN APPROVAL IS A HUMAN PLUS THE EXACT FACTS
ONLY AN AUTHENTICATED AUTHORIZED HUMAN GRANTS
A MODEL CANNOT GRANT
A COUNTERPARTY CLAIM IS A FRAUD SIGNAL, NEVER AN APPROVAL
A DRIFTED FACT IS NOT AN APPROVAL, IT IS A NEW QUESTION
SAME AMOUNT, CHANGED PROVENANCE, VOID
THE DRIFT DIFF NAMES THE FIELD, THE OLD VALUE AND THE NEW
A DEGRADED EVIDENCE CONDITION IS DRIFT
AN UNREADABLE SOURCE IS NOT "NO DRIFT"
A POLICY CHANGE VOIDS AN IN-FLIGHT APPROVAL
A BRAKE BEFORE CONSUME VOIDS THE APPROVAL
A HUMAN MAY REVOKE BEFORE CONSUMPTION
AN EXPIRED APPROVAL IS NOT A WEAKER APPROVAL
A DENIAL IS TERMINAL
CONSUMED EXACTLY ONCE, IN THE CLAIM TRANSACTION
A DOUBLE TAP IS ALREADY DONE, NOT AN ERROR
A REPLAYED TOKEN IS REFUSED AT THE TRANSPORT
A TOKEN PRESENTED BY ANOTHER ACTOR IS REFUSED
A PARTIAL APPROVAL IS A NEW PROPOSAL
AT MOST ONE LIVE APPROVAL PER COMMIT KEY
DUAL-CONTROL DRIFT VOIDS ALL SIGNATURES
A DUPLICATE ACTOR DOES NOT SATISFY QUORUM
A FROZEN APPROVAL IS NOT REUSABLE
NO TIMER UNFREEZES AN APPROVAL
A CRASH BEFORE CONSUME LEAVES A GRANTED APPROVAL, RE-CHECKED
A CRASH AFTER CONSUME NEVER RETURNS AN APPROVAL TO GRANTED
A TERMINAL APPROVAL STAYS TERMINAL
THE SAME COMMIT KEY IN TWO TENANTS IS TWO APPROVALS
REDELIVERY IS IDEMPOTENT
WHAT THE HUMAN SAW IS STILL READABLE
A LEGACY DATABASE MIGRATES TO THE CANONICAL APPROVAL SHAPE
A FORGED AUTHORITY NAMES NO APPROVAL
AUTHORITY FOR ANOTHER TARGET IS REFUSED
EVERY APPROVAL EVENT DECLARES WHAT IT FOLLOWS
A STRICT CONSUMER READS THE COMPLETE AGGREGATE STREAM
AN ABSENCE IS NEVER "THERE IS NOTHING BEFORE ME"
A FREEZE IS REBUILT FROM POSITIVE EVIDENCE, NEVER FROM AN ABSENCE
replay: 0 grants, 0 approvals granted, 0 approvals consumed, 0 external effects
```

`mutants caught` is required from `scripts/mutate_phase6_approval.py`.

**Must never appear anywhere.** Print one of these only when the thing M4 exists to prevent has
actually happened; the run fails on sight of any of them:

```
### DRIFTED APPROVAL EXECUTED ###          ### CONSUMED WITHOUT A DURABLE CLAIM ###
### APPROVAL CONSUMED TWICE ###            ### CLAIM WON WITH A REUSABLE APPROVAL ###
### APPROVAL GRANTED WITHOUT A HUMAN ###   ### APPROVAL AUTHORITY DURING REPLAY ###
### NON-HUMAN GRANT ACCEPTED ###           ### PARTIAL APPROVAL APPLIED ###
### EXPIRED APPROVAL EXECUTED ###          ### QUORUM MET BY ONE ACTOR ###
### REVOKED APPROVAL EXECUTED ###          ### CROSS-TENANT APPROVAL ACCEPTED ###
### FROZEN APPROVAL REUSED ###             ### SECOND EFFECT AUTHORITY ###
### APPROVAL UNFROZEN ###                  ### FORGED AUTHORITY ACCEPTED ###
### PREDECESSOR SKIPPED ###                ### FREEZE INFERRED FROM AN ABSENCE ###
```

Also never: `### MISS ###`, `### NOT REFUSED`, `### WRONGLY REFUSED`, `### WRONG REFUSAL`.

### The mutation battery

`scripts/mutate_phase6_approval.py` proves that the load-bearing guards **can fail**. A guard
never seen to fail is a decoration, and a mutation that does not reintroduce the real defect
proves nothing — verify each mutant actually misbehaves. At minimum, mutate:

- the drift comparison (make it compare only the amount, so a provenance change passes)
- the `provenance_class` contribution to the canonical payload (drop it)
- the evidence-condition contribution (drop it)
- the live re-read failure path (make an unreadable source read as "no drift")
- the `granted_by` CHECK / FK (relax it)
- the live-approval partial unique index (widen it)
- the consume CAS (split the check and the write into two statements)
- the co-commit (write state without its event)
- the token single-use check (accept a replay)
- the tenant predicate (drop it from one query)

Use the safe in-memory save/restore harness the way `mutate_phase6_external_effect.py` does.
### **Never use `git checkout`, `git restore`, `git stash` or `git clean` to undo a mutation.**
Doing so once destroyed unrecoverable uncommitted work in this repository. Purge `__pycache__`:
restoring a `.py` is not restoring behaviour.

The mutation battery must **not** import the approval machine — mutate text and shell out to
pytest, the way `mutate_phase6_pipeline_instance.py` does.

### Ship dark

M4 ships dark, exactly as M1, M2 and M3 do.

- **Nothing under `src/freight_recon/` may import `approval`.** The only file under `scripts/`
  that may import it is `probe_phase6_approval.py`
- **no live approval channel is enabled**: no Slack surface, no Gmail surface, no TMS product
  surface, and no module that joins the approval machine to an outbound channel
- **no production write is enabled**, and the production `GateRegistry` stays EMPTY
- if canon genuinely requires a dark seam — the checkpoint reading the durable approval rather
  than taking it as a typed input, say — **name the clause that requires it** before you build
  it, and keep the seam inert

### Tests

`pytest-canonical.ini` **no longer exists.** The 2026-08 engineering-process simplification
folded it into `[tool.pytest.ini_options]` in `pyproject.toml`, and CI runs
`python -m pytest -q -p no:cacheprovider`. Do not reintroduce a second pytest configuration and
do not pass `-c pytest-canonical.ini` anywhere.

Write the adversarial tests entity §44 names, by those names where they apply:
`test_F01_approve_2850_then_tms_moves_to_3100_no_effect_occurs`,
`test_same_amount_changed_provenance_voids`, `test_double_tap_is_idempotent_not_an_error`,
`test_counterparty_cannot_self_authorize`, `test_partial_approval_is_a_new_proposal`,
`test_approval_after_unknown_attempt_is_not_reusable`,
`test_dual_control_drift_voids_all_signatures`, `test_expired_approval_cannot_execute`,
`test_policy_change_voids_inflight_approval`.

### Regressions you may not break

Re-run them on the tree you are finishing with, not the one you started from:

- **P3** — the checkpoint kernel, the claim CAS, step order, the brake, the fingerprint,
  the checkpoint matrix. `CheckpointPassed` stays unconstructable, the witness table stays
  append-only, and the claim CAS's WHERE-clause revalidation may never lose a predicate
- **P4** — the import gate, the adapter boundary, the governed write route
- **P5** — the event transport, replay isolation and audit, durable timers
- **M1, M2, M3** — their acceptance batteries, and M3's own deterministic probe, which must
  still report `behaviours as specified, 0 wrong` with M4's consumption seam in place

---

## 5. Do not

- begin **M5–M13**
- begin **P7 or later**
- build provenance-platform work beyond the minimum fingerprint-field semantics canon already
  requires for M4 (P7 owns the provenance platform)
- build freight workflows, invoice automation, AP/AR workflows, carrier sourcing, dispatch,
  tracking or claims
- build a **Slack**, **Gmail** or **TMS** product integration, or any live approval surface
- revive **Delivered Load Closure** as the product identity, or promote it to validated
- enable **live production effects** or **production autonomy**
- **redesign P0, P1, P2, P3, P4 or P5.** They are COMPLETE. Do not revisit the baseline, the
  commit-key identity, tenant-safe persistence, the checkpoint kernel, adapter containment or
  the event transport as a design question — if M4 genuinely needs one of those surfaces
  changed, say so and stop **before** changing it
- weaken **P3, P4 or P5** — if M4 needs a P3/P4/P5 surface changed, say so before changing it
- **replace P3's checkpoint kernel** or **redesign M3**
- introduce a **second checkpoint or effect authority** — the checkpoint is the only thing that
  mints a gate decision and M3 is the only thing that claims a grant
- polish **M1, M2 or M3**. They are landed. Their recorded residuals are debt rows, and a debt
  row is a complete deliverable
- start a **legacy cleanup campaign**, or remediate nonblocking debt merely because it exists
- push, publish or deploy anything

**If a tiny pre-existing defect directly prevents M4 verification**, you may fix the **smallest
blocking prerequisite** — and you must **identify it explicitly**, say why M4 could not be
verified without it, and keep the fix minimal.

---

## 6. How this run works

Product Driver drives implementation, verification, correction and independent review. You do
not need to ask the founder to relay anything: scenario failures, evaluator findings and
reviewer findings come back to **you**, in this same session, as grounded corrections, and the
loop retests.

M4 is **tier-1** work under `CLAUDE.md` §7 — the approval/grant lifecycle, by name — so a
focused independent review by a session that did not write it is required, and Product Driver
launches it inside the run rather than after it. Expect a reviewer to re-run your probe, your
suite and your mutation battery for itself.

Report a genuine blocker plainly rather than working around it. **§3.9 is the one place where
reporting a blocker is the correct outcome rather than a failure.**

**Stop at verified M4. Do not automatically continue into M5.**

Accepting M4 does **not** complete P6, does **not** score a P6 acceptance criterion, does
**not** unblock P7, and enables nothing in production.

# Build P6 / M12 — Rule. Only that.

You are building **one unit**: machine **M12**, the **Rule** — the registered, versioned,
deterministic decision procedure **with an id** that a human instruction either compiles into, or
honestly does not.

Not P6. Not the phase. Not M13 (Brake). Not an autonomy-graduation engine, a rule editor, an admin
screen, an importer, a dashboard or a freight workflow. **One machine, its table, its migration, its
acceptance battery, its probe, its mutation battery — and nothing else.**

M11 (the Policy) landed as `P6-CP-11`. `docs/implementation/CURRENT.md` says, in its own words,
**"The next build checkpoint is M12 — the Rule."** That is this unit.

---

## 0. Read the authority first, in this order

**Read these completely before writing a line.** Not skimmed, not grepped for keywords. This unit is
the one place in the architecture where the failure is a SENTENCE rather than a state: a machine that
gets every column right and still replies *"📋 Noted the procedure"* has failed completely, and every
structural test in the repository would be green while it did.

### **REPOSITORY AUTHORITY WINS.** Everything below is the authority. This task file is a reading of
### it, not a replacement for it. Where this file and the repository disagree, the repository is
### right and the disagreement is a finding you REPORT.

**Status and standing orders**
1. `CLAUDE.md` — the operating rules. §5 rule 17 (one authority per domain), §7 (review tiers), §9
   (a guard over an empty population proves nothing), §0 (no committed receipts).
2. `PRODUCT.md` — what Neyma is for.
3. `docs/implementation/CURRENT.md` — **the short-form status authority.** Read the P6 table, the
   *Still owed* row, the Risks table and **⛔ What must NOT begin**.
4. `docs/implementation/IMPLEMENTATION-REGISTRY.yaml` — the binding per-phase detail. On any
   disagreement with the roadmap, **the registry wins.** Read the P6 unit block (including the
   `prohibited_scope` paragraph that reconciles P6 and P8 — §3.6 `M12-AQ-1` below), the P8 unit
   block, and every `P6-D*` debt row.
5. `docs/implementation/implementation-roadmap.md` — the 16 migration principles. Principle 13:
   **no phase depends on future work for a CURRENT safety guarantee.**

**The M12 canonical corpus**
6. `docs/specifications/entities/15-rule.md` — all 45 points.
7. `docs/specifications/state-machines/12-rule.machine.md` — all 43 sections, and §14's transition
   table **row by row**. Its opening paragraph is load-bearing: it refuses five state names by
   mapping them into the frozen eight.
8. `docs/specifications/events/12-rule-events.md` — family **F12**, all eight contracts.

**Registries and global contracts**
9. `docs/specifications/state-machines/registry.md` — §4 (the state sets; M12's frozen eight), and
   the `GR-*` rules. **GR-4, GR-8, GR-11, GR-13 and GR-15 are all directly about this machine.**
10. `docs/specifications/events/registry.md` — **by its own header THE SOLE CANONICAL LIST of event
    names.** §3 (one producer transition per event; **F12 is eight names**), §5 (the consequential
    set), §8 (ordering — and read `M12-AQ-5` below before you conclude anything about F12's row in
    it), §9 (the ‡ coordination events, which is why `ConflictRaised` is **F7's**), and the `ER-*`
    rules — **`ER-7`, `ER-9` and `ER-12` in particular.**
11. `docs/specifications/entities/00-conventions.md` — `[C-1]`…`[C-10]`.
12. `docs/specifications/acceptance/foundational-machine-acceptance.md` — M12 is **9 transitions,
    `AC-MACH-1201..1209`**, gate **G1**.
13. `docs/specifications/acceptance/platform-safety-acceptance.md` — read this unit's criteria
    first, not last, and name in your evidence exactly which ones you believe M12 touches.
14. `docs/architecture/target-system-specification.md` **§12.12** (the Rule lifecycle — and note its
    transition table has EIGHT rows where the machine file has NINE: `M12-AQ-3`), **§20.5** (rule
    compilation — the two honest outcomes), **§20.7** (precedence), and **§11** (failure modes).
15. **`docs/architecture/decisions/ADR-010-policy-rules-constraints-autonomy.md` — IN FULL.** §6 *is*
    this unit: the compilation pipeline diagram, the two outcomes, §6.1's four worked examples and
    §6.2's lifecycle. Then §3 (the seven concepts, not collapsed), §5.1 (a policy may never branch on
    a guess), §7 (the one-way ratchet), §8 (precedence — and §8.1, the override, which is
    `M12-AQ-7`), §9 (how step 6 joins the checkpoint), §10 (the merge-gating tests), §11 (failure
    modes), §12 (security).
16. Every ADR ADR-010 cites — **ADR-002** (provenance and evidence conditions; C6, the `conflicting`
    field that BLOCKS), **ADR-003** (the one Permanent Product Truth), **ADR-004** (§2.4 the atomic
    checkpoint, §3.2 the grant), **ADR-007** (§5 — the Conflict machinery M12 fails closed into and
    must NOT rebuild), **ADR-008** (§2.3 the canonical Durable Machine; §3.8 Expectations), **ADR-011**
    (the brake).

### Then read the LANDED CODE, not the prose about it

Every seam below is **already built**. You are wiring into it, not re-deciding it.

| Seam | Read | Why it is load-bearing for M12 |
|---|---|---|
| **P3 checkpoint** | `src/freight_recon/checkpoint.py` | `GateDecision` (the four members), `GateEntry`, `GateRegistry`, `GateRegistry._DEFAULT`, the seven steps, **step 6 as it exists today**, `mint_grant`, and the claim CAS. A `GATE_PRECONDITION` rule is evaluated **inside step 6**. |
| **P3 brake** | `src/freight_recon/brake.py` | `BrakeStore.admission_denied`, `version_token`, `narrow()`. **This is P3's landed kernel brake and it is NOT M13.** The brake denies regardless of what a rule permits. |
| M1 Work Item | `src/freight_recon/work_item.py`, `migrations/phase6_work_items.py` | **`tenant_humans` and `AUTHORITY_ROLES = ("POLICY_OWNER", "AUTHORIZED_HUMAN")`.** `activated_by` and `authored_by` are foreign keys into this and nothing else. |
| M2 Pipeline Instance | `src/freight_recon/pipeline_instance.py` | `PL-2` (writes `gate_decision`, refuses NULL, emits **`PolicyEvaluated`** — *that event is M2's*), `PL-3`, `PL-6`, `PL-7a`. |
| M3 External Effect / Grant | `src/freight_recon/external_effect.py` | The grant lifecycle. **A denying rule ⇒ no witness ⇒ no grant** (entity §39). |
| M4 Approval | `src/freight_recon/approval.py` | `request()`, `grant()`, the `fp_v1` fingerprint. A `GATE_PRECONDITION` rule may set the gate to `HUMAN_APPROVAL_REQUIRED` **under a condition** — it routes to M4, it does not replace M4. |
| **M7 Conflict** | `src/freight_recon/conflict.py`, `migrations/phase6_conflicts.py` | ### **`CONFLICT_KINDS` ALREADY CONTAINS `RULE_VS_RULE`**, `conflicts.rule_id` is already a column, and `CONFLICT_RAISED_PRODUCERS` is `('CF-1', 'IB-6', 'EF-4c')` — **RU-3 is not among them.** RU-3 CALLS M7. M7 mints. |
| M9 Exception | `src/freight_recon/exception.py`, `migrations/phase6_exceptions.py` | `raise_exception`, and **`SOURCE_KINDS` — which already contains `"rule"`**, carried without a FK in `SOURCE_KINDS_WITHOUT_TABLE` as `M9-AQ-3`. RU-8's expiry and the override-rate seam escalate here. |
| **M11 Policy** | `src/freight_recon/policy.py`, `migrations/phase6_policies.py` | **The layer immediately ABOVE a Rule in the precedence ladder, and the closest model for shape.** Read it for the `P6XX_*` convention, the human-activation shape and the retention trigger. ### **Copy its SHAPE. Do not copy its STATES: `DRAFT` and `APPROVED` are M11's and are forbidden on a rule.** |
| M10 Compensation | `src/freight_recon/compensation.py` | Another recent machine. Read for shape; edit none of it. |
| Events | `event_contracts_data.json`, `event_envelope.py`, `event_outbox.py`, `event_inbox.py` | The **118 registered contracts**, including **all eight F12 names**, F7's `ConflictRaised` and F14's `UnauthorizedPolicyActivationAttempted`. |
| Replay | `src/freight_recon/event_replay.py` | `GR-11` / `ER-2`: replay produces zero effects and zero authority. |
| **The ADR-010 boundary guards** | `eval/phase0/gate_scan.py`, `eval/tests/test_phase0_null_gate.py`, `eval/tests/test_phase0_errata_guards.py` | **Read these before you write `rule.py`.** See §3.7 — they decide the shape of this unit. |
| Schema | `src/freight_recon/schema.py`, `migrations/phase6_*.py` | The canonical table set, the tenant-first partition, `schema_readiness_problems`, and the `P6XX_*` symbol convention every P6 migration follows. |

### How to weigh them

When two authorities disagree, they are ordered:

1. `events/registry.md` is **the sole canonical list of event names.** A name not in it is not an
   event, however many machine files or entity files use it.
2. `docs/architecture/target-system-specification.md` and the machine file agree on the transition
   set. Where they do not, or where the entity file's prose is looser, the **machine table (§14)
   governs.**
3. `IMPLEMENTATION-REGISTRY.yaml` beats the roadmap.
4. **The landed code beats every document about the landed code.**

### **WHERE THEY GENUINELY CONFLICT, YOU RECORD THE CONFLICT AND BUILD THE FAIL-CLOSED SIDE. YOU DO
### NOT RESOLVE IT.** §3.6 lists the seven this bootstrap found. Finding an eighth is a good outcome;
### silently settling any of them is not. ### **DO NOT INVENT AUTHORITY THAT DOES NOT EXIST.** If the
corpus does not answer a question, the answer is "the corpus does not answer this", recorded as an
authority question, with the fail-closed behaviour built and stated — never a preference presented as
a finding.

---

## 1. What Neyma is — the stable identity

Neyma is an operational teammate for a freight brokerage: it reads the mailbox, extracts what is
happening, and does the operational work a broker would otherwise do by hand. It is not a chatbot and
not an RPA script. **Every external effect is gated, witnessed, single-use, verified and attributable
to a named human or to an explicitly granted autonomy.**

The lesson this machine exists for is **Stream B lesson L-C**, and it is about what Neyma SAYS. An
owner typed a sentence, the system replied that it had noted the procedure, and nothing was
installed. Nobody lied on purpose. The owner reasonably believed a control existed, acted as though
it did, and the control was a string in a prompt.

> ### **A human instruction either COMPILES INTO AN ENFORCEABLE RULE, or it does NOT compile —
> ### remains non-authoritative organizational memory — AND THE OWNER IS TOLD IT IS NOT A RULE.**
> ### **THERE IS NO THIRD OUTCOME.**

---

## 2. Where the program stands

Eleven P6 machines are landed — M1 Work Item, M2 Pipeline Instance, M3 External Effect / Grant,
M4 Approval, M5 Observation, M6 Identity Binding Claim, M7 Conflict, M8 Expectation, M9 Exception,
M10 Compensation, M11 Policy — as `P6-CP-1` … `P6-CP-11`. **All eleven ship dark.** `criteria_scored`
is `[]` on every one of them; **P6 is `status: READY` / `execution_state: IN_PROGRESS`; P7 is
`BLOCKED` / `NOT_STARTED`.**

**M12 is the twelfth. M13 (Brake) is the thirteenth and is NOT BUILT.** Of the 134 canonical
transitions, 120 are written and landed; the 14 that remain are **exactly M12's 9 and M13's 5**.

### **LANDING M12 SCORES NO P6 CRITERION.** `criteria_scored` stays `[]`. P6's status does not move.
### **P7 stays `BLOCKED`.** A checkpoint is a landed increment, never a phase acceptance.

---

## 3. The unit: M12, and nothing else

### 3.1 The sentences the whole unit is a consequence of

```
a human instruction either compiles into an enforceable rule or is honestly refused
there is no third outcome
a model may propose text; it never compiles, confirms, activates, evaluates or resolves
a rule may never branch on a guess
two conflicting rules fail closed; Neyma never picks a winner
```

Everything below is one of those five sentences made mechanical.

### 3.2 The canonical state set

**Eight states, and there is no ninth** (`entities/15-rule.md` §12; `state-machines/registry.md` §4;
target spec §12.12):

```
PROPOSED · COMPILED · CONFIRMED · ACTIVE · REJECTED (T) · SUPERSEDED (T) · REVOKED (T) · EXPIRED (T)
```

Initial: `PROPOSED`. Terminal: `REJECTED`, `SUPERSEDED`, `REVOKED`, `EXPIRED`. Non-terminal:
`PROPOSED`, `COMPILED`, `CONFIRMED`, `ACTIVE`.

### **THE BRIEF'S FIVE INFORMAL NAMES ARE ALREADY MAPPED INTO THAT SET AND ARE NOT STATES:**
*parsed* = a `PROPOSED` candidate · *invalid* = `REJECTED` (`RuleNotEnforceable`) ·
*conflict-detected* = a raised **M7 Conflict**, with the rule still `COMPILED` and blocked ·
*awaiting-confirmation* = `COMPILED` · *suspended* = `REVOKED`.

### **DO NOT INVENT `PARSED`, `INVALID`, `CONFLICT_DETECTED`, `AWAITING_CONFIRMATION`, `SUSPENDED`,
### `PENDING`, `ENABLED`, `DISABLED`, `CANCELLED`, `FAILED`, `ARCHIVED` OR ANY NINTH STATE.**

### **AND DO NOT INHERIT `DRAFT` OR `APPROVED`.** Those are **M11's** states. A rule machine written
### by copying the policy machine acquires them silently and looks entirely reasonable doing it. The
### permanent scenario attempts both against a live database and requires both to be REFUSED.

**Four kinds, and there is no fifth** (entity §10): `IDENTITY`, `CONFLICT_RESOLUTION`,
`GATE_PRECONDITION`, `CONSTRAINT`.

### 3.3 What M12 emits, and what it consumes

**M12 MINTS exactly the eight registered F12 contracts and no ninth:**

```
RuleProposed(RU-1) · RuleCompiled(RU-2) · RuleNotEnforceable(RU-2f) · RuleConfirmed(RU-4)
RuleActivated(RU-5) · RuleSuperseded(RU-6) · RuleRevoked(RU-7) · RuleExpired(RU-8)
```

Their required payload fields are registered and are not yours to choose: `RuleProposed{kind, scope,
source_instruction}` · `RuleCompiled{rule_id, compiled_predicate, test_vectors}` ·
`RuleNotEnforceable{missing}` · `RuleConfirmed{}` · `RuleActivated{rule_id, rule_version,
activated_by}` · `RuleSuperseded{superseded_by}` · `RuleRevoked{revoked_reason, direction}` ·
`RuleExpired{rule_id, rule_version, expired_at}`. **`RuleActivated` is `human_only: true`.**

**M12 CONSUMES driving facts that are NOT registered event contracts** (machine §33, entity §32):
`HumanConfirmed`, `HumanActivated`, `ConflictDetected`, `HumanRevoked`, `TimerFired`.

### **A CONSUMED FACT IS NOT AN EVENT YOU MINT, AND REGISTERING ONE TO QUIET AN ORACLE WOULD
### MANUFACTURE EXACTLY THE NINTH CONTRACT THE INVARIANT FORBIDS.** M11 hit this precise trap one
unit ago and the correction is written down: model them as a closed `Trigger` vocabulary the machine
reads, never as something it emits.

### **`ConflictRaised` IS NOT YOURS.** It is **F7**, produced by `CF-1`, `IB-6` and `EF-4c`, with
### `aggregate_type=conflict`. `entities/15-rule.md` point 31 lists it under Rule's *"Events
### emitted"* — that is `M12-AQ-2`, and the machine table's `CONSUMES:ConflictRaised` is the reading
### that wins. **RU-3 CALLS M7's landed raise entry point. M7 mints. You mint nothing.**

### **`UnauthorizedPolicyActivationAttempted` IS NOT YOURS EITHER.** It is **F14**, already
registered and already used by M11 for exactly this fact. Minting a second contract for "a
non-human tried to activate an authority object" is rule-17 duplication.

### **DO NOT MINT `RuleSuspended`, `RuleParsed`, `RuleInvalidated`, `RuleEnabled`, `RuleDisabled`,
### `RuleOverridden`, `PolicyOverridden`, `ConflictRaised`, `BrakeEngaged` OR ANY OTHER NAME.**

### 3.4 Implement the canonical `RU-*` transition contract

Nine rows. `12-rule.machine.md` §14 is the authority; this is a summary, not a substitute.

| ID | From → To | Trigger | The thing that must be true |
|---|---|---|---|
| **RU-1** | — → `PROPOSED` | H\|S | ### **a model MAY propose the structured candidate TEXT.** `source_instruction` is retained verbatim. |
| **RU-2** | `PROPOSED` → `COMPILED` | S | ### **DETERMINISTIC, NO MODEL.** Every referenced field MODELLED and NON-INFERRED (`GR-8`); the predicate decidable at checkpoint time; the scope resolvable. Writes `compiled_predicate` **and** `test_vectors[]`. |
| **RU-2f** | `PROPOSED` → `REJECTED` | S | a referenced field is unmodelled or `MODEL_INFERRED` ⇒ `RuleNotEnforceable{missing}` ### **and the owner is TOLD it is not a rule.** |
| **RU-3** | `COMPILED` → *(blocked)* | S | conflicts with an ACTIVE rule ⇒ ### **M7 `RULE_VS_RULE` Conflict, fail closed, never auto-merge (`GR-15`).** The rule STAYS `COMPILED`. |
| **RU-4** | `COMPILED` → `CONFIRMED` | H | ### **the owner is shown the COMPILED rule AND its generated test vectors.** |
| **RU-5** | `CONFIRMED` → `ACTIVE` | H | ### **an AUTHENTICATED HUMAN activates — NEVER a model, NEVER automation.** Writes `activated_by`, `rule_version`. |
| **RU-6** | `ACTIVE` → `SUPERSEDED` | H | a new version; ### **the old version is RETAINED.** Writes `superseded_by`. |
| **RU-7** | `ACTIVE` → `REVOKED` | H\|S | ### **immediate if it NARROWS; the Policy Owner if it BROADENS.** Writes `revoked_reason` and `direction`. |
| **RU-8** | `ACTIVE` → `EXPIRED` | T | narrowing-rule TTL — ### **and its expiry BROADENS, so a human is required AT EXPIRY.** |

**Every row states its event, its writes and its guard. Nine rows. Not eight, not ten.**

### 3.5 What must hold — the authority and safety requirements

**A. THE TWO OUTCOMES, AND THE LITERAL REPLY TEXT.**

### **THIS IS THE ONE REQUIREMENT THAT IS NOT ABOUT STRUCTURE, AND IT IS THE REASON THE MACHINE
### EXISTS.** M-52 / M-64 / T16 are about the SENTENCE the owner reads.

> ### ***"📋 Noted the procedure for raise_invoice" IS FORBIDDEN unless a rule actually compiled AND
> ### activated.***
> ### ***The honest failure sentence is: "I can't enforce that. Here's why, and here's what I'd
> ### need." That is a better answer than a false yes, and the owner can act on it.***

`rule.py` must expose the guard as executable code, not as a docstring:

```
FORBIDDEN_ACKNOWLEDGEMENTS      the literal phrases a reply may never carry without an ACTIVE
                                rule_id. "Noted the procedure" is one of them.
reply_claims_enforcement(text)  -> bool, case-insensitive
honest_refusal(missing, why)    -> the owner-facing sentence. It NAMES what is missing, says
                                plainly that it is NOT a rule, and says what would be needed.
assert_reply_is_honest(text, active_rule_id=None)
                                raises when a reply claims enforcement and no ACTIVE rule_id
                                backs it. An empty-string rule id is not a rule id.
```

### **AND IT MUST WORK IN BOTH DIRECTIONS.** The same claiming sentence WITH a real ACTIVE `rule_id`
is ACCEPTED. A machine that refuses every reply is not the safe direction — it is a different broken
product, and the permanent scenario has a positive control that fails it.

**B. COMPILATION IS DETERMINISTIC, AND A MODEL IS NOT IN IT.**

A model may propose the candidate TEXT at RU-1. ### **After that there is no model call anywhere:
not in compilation, not in conflict detection, not in confirmation, not in activation, not in
evaluation, not in resolution.** Compilation over the same candidate is byte-identical reproducible;
no wall clock, no randomness and no unordered iteration may change the compiled predicate.

**C. MODELLED AND NON-INFERRED FIELDS ONLY — TYPED, NOT BLACKLISTED.**

### **A rule referencing a `MODEL_INFERRED` field FAILS TO COMPILE (M-49 / GR-8 / S3). At confidence
### 1.0, it still fails.** An unmodelled field fails to compile. An undecidable predicate fails to
compile. An unresolvable scope fails to compile.

### **DO NOT IMPLEMENT THIS AS A STRING BLACKLIST OVER FIELD NAMES.** The refusal is **typed**: the
compiler's input carries `provenance_class` on every field, and ### **`confidence` is STRUCTURALLY
ABSENT from the compiler's input type**, so a predicate cannot read it even by trying. `checkpoint.py`
and `policy.py` already have this shape — read them and reuse the pattern rather than inventing a
second one. **Reuse M7's landed six-member `PROVENANCE_CLASSES`; do not define a private copy.**

The scenario pins this surface:

```
CompilerInput                   a typed input with `provenance_class` and NO `confidence` field
compile_predicate_field(inp)    refuses MODEL_INFERRED, an unmodelled field, and an invented
                                provenance class; accepts SYSTEM_IMPORTED and OWNER_ASSERTED
PROVENANCE_CLASSES              M7's six, reused
```

**D. THE FOUR WORKED EXAMPLES ARE ACCEPTANCE, NOT ILLUSTRATION** (ADR-010 §6.1, spec §20.5).

| Owner says | Required outcome |
|---|---|
| *"Never bill without a POD."* | ### **OUTCOME A.** A real `GATE_PRECONDITION` on `RAISE_INVOICE`: `pod.evidence_condition == consistent` **AND** `pod.provenance_class ∈ {SYSTEM_IMPORTED, OWNER_ASSERTED, MODEL_EXTRACTED-with-artifact}`. ### **A `MODEL_INFERRED` POD ⇒ DENY. An "inferred" POD is not a POD.** |
| *"Do not use Carrier X for produce."* | ### **OUTCOME B.** `commodity` is not a modelled field. It CANNOT compile, the owner is told exactly that, and the sentence is retained as organizational memory carrying no authority. ### **An honest refusal that surfaces a FEATURE REQUEST, not a silent "noted".** |
| *"Customer Y requires hourly updates."* | ### **OUTCOME A via EXISTING machinery** — a recurring Expectation (M8, ADR-008 §3.8). ### **It gates nothing; it OWES something. NO NEW PRIMITIVE.** |
| *"Require manager approval under 12% margin."* | ### **CONDITIONAL.** Compiles only if margin is deterministic. If the carrier cost is `MODEL_INFERRED`, ### **it REFUSES TO COMPILE** and the owner is told which loads it can and cannot cover. |

**E. TEST VECTORS BEFORE CONFIRMATION.**

### **Every compiled rule ships with GENERATED test vectors** — *"here are three loads this rule
### WOULD have blocked last month"* — and the owner sees the compiled rule AND those vectors before
RU-4 is possible. Confirmation without test vectors is REFUSED. ### **A rule whose consequences the
### owner cannot see is a rule they have not really approved.** `RuleConfirmed` does **not** activate;
RU-5 and a human do that, and the two are never collapsed.

**F. HUMAN ACTIVATION.**

`state = ACTIVE` requires a non-null `activated_by` — a **CHECK**, plus a foreign key into M1's
landed `tenant_humans` (entity §16/§18). ### **A model-activated rule is not insertable.** Neither is
one activated by automation, a retry handler, a timer, a counterparty, or a human from another
tenant. An unauthorized attempt is refused **and recorded** through the already-registered F14
contract.

### **USE `AUTHORITY_ROLES = ("POLICY_OWNER", "AUTHORIZED_HUMAN")` EXACTLY AS M1 LANDED IT.** Do not
### invent an admin role, a superuser, a service account with rule authority, or a bypass flag. A
### parallel authority mechanism is the defect ADR-010 §4 exists to forbid, and it always arrives
### looking like an operational convenience for installing rules quickly.

**G. CONFLICT FAILS CLOSED, THROUGH M7.**

Two genuinely conflicting ACTIVE rules ⇒ **FAIL CLOSED** ⇒ a `RULE_VS_RULE` **Conflict** ⇒ the
affected field is `conflicting`, which **BLOCKS the action** (ADR-002 C6) ⇒ **a human resolves it.**
### **NEYMA NEVER PICKS A WINNER. NOTHING AUTO-MERGES.**

### **AND THE CONFLICT MACHINERY IS ALREADY BUILT — ADR-007 §5, "existing machinery, no new
### primitive".** `RULE_VS_RULE` has been in M7's closed `CONFLICT_KINDS` since `P6-CP-7` and
`conflicts.rule_id` has been a column since the same landing. **Call M7's landed raise entry point.
Do not create a `rule_conflicts` table, a `RuleConflict` type, a second conflict module, or an
M12-minted `ConflictRaised`.** Where one rule is strictly narrower than another that is
**precedence, not a conflict** — the narrower scope wins, because specificity is intent.

**H. UNIQUENESS, VERSIONING, RETENTION.**

- **PK `(tenant_id, rule_id)`** — tenant first (entity §17).
- ### **`UNIQUE (tenant_id, rule_version)` — the version namespace is the TENANT**, not the scope.
  `rule_version` is monotonic per tenant, never reused, never overwritten in place, never edited
  retroactively. (Entity §9's natural identifier reads `(tenant_id, scope, rule_version)`; entity
  §17's constraint is tenant-local. That tension is `M12-AQ-4b` — §17 is the constraint authority and
  it is what you build.)
- ### **`UNIQUE (tenant_id, scope, kind) WHERE state = 'ACTIVE'` — *WHERE A SCOPE ADMITS ONE RULE.*
  ### OTHERWISE MULTIPLE ACTIVE RULES MAY COEXIST AND CONFLICT DETECTION HANDLES THEM.** Both halves
  are load-bearing and they fail in opposite directions. See `M12-AQ-4` — this is not a detail you
  may settle silently.
- ### **Uniqueness is NEVER global across tenants.** The same scope and kind ACTIVE in a different
  brokerage must be ACCEPTED. A global constraint that coupled two tenants is the cross-tenant defect
  wearing a safety constraint's clothes.
- **OCC** `[C-10]`, one writer wins (`GR-3`).
- ### **Retention is permanent `[C-9]`.** A rule row cannot be deleted — enforce it, the way M10 and
  M11 enforce theirs. Superseded versions are retained **because effects were judged under them**,
  and an old decision is explained under ITS OWN rule version. ### **A rule is never retroactive.**

### **DO NOT TURN "the compiled predicate references only modelled, non-inferred fields" INTO A
### ROW-LOCAL SQL `CHECK`.** Entity §16 states it as a constraint and immediately says *"(enforced at
compile)"*. It is a property of the compilation pipeline over data the row does not carry, and a SQL
`CHECK` that pretended to express it would be a constraint that enforces nothing while reading as
though it enforced everything. **Enforce it at compile, and say so where the DDL would have said it.**

**I. IDEMPOTENCY.**

Re-activating an already-ACTIVE version is a **NO-OP** (`GR-4`, entity §33): no second
`RuleActivated`, no `rule_version` bump, no duplicate authority.

**J. DIRECTION — AND THE CLOCK.**

Revocation is **immediate if it NARROWS** and requires the **Policy Owner if it BROADENS**;
automation may never perform a broadening revocation. Only a **narrowing** rule may carry an expiry
at all, because ### **a narrowing rule's expiry BROADENS authority — so `TimerFired` cannot complete
### it, and a human is required AT EXPIRY.**

> ### **THE CLOCK MAY TAKE AUTHORITY AWAY. THE CLOCK MAY NEVER GIVE IT.**
> ### ***Otherwise "temporarily tighten" becomes "automatically loosen later, when nobody is
> ### watching."***

**K. CHECKPOINT INTEGRATION — AND THERE IS ONLY ONE CHECKPOINT.**

`GATE_PRECONDITION` and `CONSTRAINT` rules are evaluated **in checkpoint step 6** (entity §38,
machine §29). A denying rule ⇒ **no witness ⇒ no grant ⇒ no effect** (entity §39). A
`GATE_PRECONDITION` rule may set the gate to `HUMAN_APPROVAL_REQUIRED` under a condition (entity
§40) — which **routes to M4** and does not replace it.

### **`checkpoint.py` REMAINS THE SOLE MINTER OF A GATE DECISION. M12 CONSTRUCTS NO `GateEntry` AND
### NO `GateRegistry`, CALLS NO `register_gate`, AND THE PRODUCTION `GateRegistry` POPULATION STAYS
### EMPTY.**

### **A SECOND GATE AUTHORITY IS THE SAME DEFECT AS NO GATE AUTHORITY** — two answers to *"may Neyma
do this alone"*, and nothing that says which one the grant was minted under. There is **no allow-on-
error path**: a rule engine that cannot answer produces no decision, no witness and no effect.

**L. PRECEDENCE — LAYER 6, AND FIVE THINGS SIT ABOVE IT.**

```
1 CONSTRAINT · 2 PERMANENT PRODUCT TRUTH · 3 HUMAN BRAKE · 4 PRODUCT POLICY · 5 TENANT POLICY
6 STANDING RULES  ← you are here
7 WORKFLOW DEFAULT (and it is never autonomous)
```

Declare the ladder in code (`PRECEDENCE_LADDER`, `PRECEDENCE_LAYER = 6`) and refuse, executably, any
attempt to place a rule above it. ### **A rule never overrides a Constraint, a Permanent Product
### Truth, a Brake denial, the Product Policy ceiling or a Tenant Policy.**

### **AND DO NOT BUILD A SECOND PRECEDENCE ENGINE.** The precedence / conflict-resolution ENGINE is
P8's by the same registry paragraph that makes this machine P6's (`M12-AQ-1`). M12 declares the
ladder it sits in, refuses to sit above it, reuses M11's landed ceiling comparison rather than
writing a second one, and evaluates no layer it does not own.

**M. OBSERVABILITY — AND THE QUESTION THAT STAYS DEFERRED.**

### **Override rate is the key rule-health metric** (entity §42). A rule overridden constantly is a
wrong rule and ### **gets a human's attention through M9 — it is NEVER silently auto-disabled.**
`Q3` — *should a repeatedly-wrong rule auto-disable?* — is **explicitly deferred**, the canon's own
recommendation is **never**, and it is **not a blocker**. ### **Build the "it asks" side. Do not
### build auto-disable, and do not resolve Q3.**

### **AND SEE `M12-AQ-7`: THE OVERRIDE EVENT ITSELF DOES NOT EXIST.** You may not mint it.

**N. M13 IS NOT BUILT.**

Verify the seams that exist and refuse illegal M12 behaviour against them. ### **Do not require M13
to exist in order to make M12 pass.** `brake.py` is P3's landed kernel brake — an admission-control
read surface — and it is **not** M13. The discriminator is a brake **lifecycle**: its states, its
transitions, its table. Build none of them.

### 3.6 ⚠️ THE SEVEN AUTHORITY QUESTIONS THIS BOOTSTRAP FOUND

**Every one was found mechanically, against the corpus and the landed code. Read what each says,
build the side named, and RECORD it in your evidence. Resolving one is a founder/architect act, not
a build session's.**

---

**`M12-AQ-1` — SETTLED IN CANON, BY THE SAME CORRECTION THAT SETTLED M11's. P6/M12 builds the
MACHINE; P8 keeps the PRODUCTION RUNTIME.**

`pr-sequence.md` gives P8 a unit literally named **`U8.2 compile-or-refuse Rules`**, and the P6 unit
block's own `prohibited_scope` paragraph lists *"rule compile-or-refuse runtime"* among what stays
P8-only. Read alone, that prohibits the work this registry itself schedules next.

### **IT DOES NOT, AND THE REGISTRY NOW SAYS SO IN ITS OWN WORDS.** `prohibited_scope` reads:
*"production policy REGISTRATION, EVALUATION RUNTIME and ENABLEMENT (P8/U8.1) — **NOT machines
M11/M12/M13 themselves, which are three of this unit's own thirteen**"*. P6's objective owes *"the 13
machines, 134 transitions"*; M12 is one of the thirteen and its nine transitions are part of the 134.
The same relation P8's `U8.1` has to M11, `U8.2` has to M12 — and M7–M10 all landed in P6 while being
named in P8's `allowed_scope`, with no authority treating it as a breach.

**What P6/M12 MAY implement:** the canonical Rule **entity**, its **tenant-first `rules` table**,
machine M12's **nine transitions `RU-1`…`RU-8`**, the **eight already-registered F12 contracts**, and
**deterministic compile-or-refuse INSIDE the machine, its probe and its battery** — **SHIPS DARK**.

**What remains P8-ONLY:** ### **ENABLEMENT.** A production rule authoring or import surface, a
channel join, populating the production `GateRegistry`, the precedence/conflict-resolution engine,
the autonomy/graduation runtime, and any of it reaching live traffic.

### **DO NOT REFUSE TO BUILD COMPILATION ON THE GROUND THAT "compile-or-refuse Rules is P8."** That
### reading would make M12 unbuildable, and it is wrong for the reason the registry states.

---

**`M12-AQ-2` — `ConflictRaised`: THE ENTITY FILE AND THE MACHINE FILE DISAGREE. BUILD THE MACHINE
FILE. REPORT THE CONFLICT.**

`entities/15-rule.md` point 31 lists `ConflictRaised` among *"Events emitted"* by Rule.
`12-rule.machine.md` §14 RU-3 marks it **`CONSUMES:ConflictRaised`**. `events/registry.md` §3
attributes it to **F7** with producers `CF-1`, `IB-6`, `EF-4c`, and §9 names it one of the three ‡
events with structurally-identical producers — *one contract, several origins*. The landed
`conflict.py` agrees: `CONFLICT_RAISED_PRODUCERS = ('CF-1', 'IB-6', 'EF-4c')`.

### **THE REGISTRY IS THE SOLE CANONICAL LIST OF EVENT NAMES AND THE MACHINE TABLE GOVERNS THE
### TRANSITION SET. M12 CALLS M7; M7 MINTS; M12 MINTS NOTHING.** Point 31's list is also missing
`RuleExpired`, which the registry, the family file and RU-8 all carry — the same class of entity-file
staleness `P6-D75` already records. **Report it. Do not edit the entity file to match your build.**

---

**`M12-AQ-3` — NINE TRANSITIONS, NOT EIGHT. The target spec's own table is one row short.**

`target-system-specification.md` §12.12's transition table has **eight rows and no `ACTIVE → EXPIRED`
row**, while its own state list carries `EXPIRED (T)`. The machine file §14 has **nine**,
`foundational-machine-acceptance.md` says **`AC-MACH-1201..1209`**, `CURRENT.md`'s arithmetic says
M12 owes **9**, and `RuleExpired` was minted for RU-8 by the 2026-08-12 founder/architect amendment
that the spec table predates.

### **BUILD NINE. The machine table governs the transition set (authority order rule 2).** Record
that §12.12's table is stale rather than correcting it.

---

**`M12-AQ-4` — WHICH SCOPES ADMIT EXACTLY ONE ACTIVE RULE IS NOT SETTLED BY CANON, AND YOU MAY NOT
SETTLE IT SILENTLY.**

Entity §17: *"`UNIQUE (tenant_id, scope, kind) WHERE state = 'ACTIVE'` **where a scope admits one
rule**; otherwise multiple active rules may coexist (and conflicts are detected)."* Canon does not say
which scopes those are, and `V4`/`V5` — *which identity and conflict rules to register* — are open.

**Both mistakes are available and they fail in opposite directions:**

- Impose the index on EVERY scope ⇒ a legitimate second rule is refused by a constraint canon never
  granted, and the *"otherwise"* branch becomes unreachable — ### **which silently resolves `V4`/`V5`
  by construction.**
- Omit it ⇒ two rules that must not coexist do, and nothing notices.

### **WHAT IS NOT OPTIONAL: THE ANSWER IS EXPLICIT, DECLARED IN ONE PLACE, AND MECHANICAL.** Declare
`P6RU_SCOPE_FORMS` (the scope-form vocabulary) and `P6RU_SINGLE_ACTIVE_SCOPES` (the forms that admit
exactly one ACTIVE rule per `(tenant, scope, kind)`), have the partial index's predicate NAME those
forms, and ### **keep the single-admitting set a PROPER SUBSET so the "otherwise" branch is
### reachable and conflict detection actually covers it.** State your choice and its reasoning in the
evidence as an answer to an OPEN question, not as a finding.

**`M12-AQ-4b`** — entity §9's natural identifier `(tenant_id, scope, rule_version)` reads as
per-scope versioning; entity §17's `UNIQUE (tenant_id, rule_version)` is tenant-global. ### **§17 is
the constraint authority: the version namespace is the TENANT**, matching M11's corrected `M11-AQ-6`.
Record the tension.

---

**`M12-AQ-5` — F12's ORDERING IS RECORDED THREE WAYS AND THEY DO NOT AGREE. DO NOT EDIT THE
REGISTERED CONTRACT.**

`events/12-rule-events.md`'s family defaults say **"ordering = STRICT per-aggregate (`rule_version`
monotonic)"**. `events/registry.md` §8 enumerates the strict families as **F2, F3, F4, F11, F13** and
the order-tolerant ones as **F5, F7, F9, F14** — ### **F12 appears in NEITHER list.** The landed
`event_contracts_data.json` records `strict_order: false` on all eight F12 contracts.

### **BUILD THE FAIL-CLOSED SIDE, WHICH IS THE STRICTER ONE, WITHOUT TOUCHING THE REGISTRY.** Make
`rule_version` monotonic per tenant a DATABASE constraint — which is where the guarantee actually
lives — and set §1's optional `previous_aggregate_version` on emission, which is additive, harmless
under either reading and strictly safer. ### **DO NOT FLIP `strict_order` IN
### `event_contracts_data.json`.** Changing a registered contract's classification is a
founder/architect act, and §8's omission is an omission rather than a classification. **Report it.**

---

**`M12-AQ-6` — M9's `source_kind = 'rule'` HAS NO FOREIGN KEY, AND M12 DOES NOT GIVE IT ONE.**

`rule` has been a member of M9's closed `SOURCE_KINDS` since `P6-CP-9`, carried deliberately WITHOUT
a foreign key in `SOURCE_KINDS_WITHOUT_TABLE` and recorded as `M9-AQ-3`, *"the kinds whose table does
not exist today"*. **M12 lands that table.** Whether `rule` now moves into the FK-backed
`SOURCE_KIND_TABLE` is exactly the question M11 faced one unit ago for `policy`.

### **M11 ANSWERED IT BY PRECEDENT AND SO DO YOU: NAME THE SEAM AND LEAVE IT UNWIRED.** `P6-D73`
(M11's identical case) is **still open**. CURRENT.md forbids rebuilding M1–M11. RU-8's expiry
Exception and the override-rate escalation are raised through M9's landed entry point; ### **you add
### no mirror column, no foreign key and no migration to M9, and you edit no part of M9.** Report it
as the open question it is.

---

**`M12-AQ-7` — `PolicyOverridden` DOES NOT EXIST, AND M12 MAY NOT MINT IT. `P6-D71` STAYS OPEN.**

ADR-010 §8.1 and spec §20.7 `M-54` describe an override: *"an authorized human may override a
standing rule for ONE BOUNDED INSTANCE only"*, recorded as
`PolicyOverridden{rule_id, actor, reason, decision_ref, commit_key}`, an audit event **and** a
security event, single-use and bound to one commit key. ### **THAT NAME IS NOT AMONG THE 118
### REGISTERED CONTRACTS.** M11 recorded this as `M11-AQ-4` / `P6-D71`, built no override mechanism
at all — not as an event, a field or a code path — and left it open. `CURRENT.md` records an
expectation that the obligation *"lands with M12/Rule"*.

### **AN EXPECTATION IS NOT AN AUTHORISATION. `events/registry.md` IS THE SOLE CANONICAL LIST, AND
### MINTING A CONTRACT IS A FOUNDER/ARCHITECT ACT, NOT A BUILD SESSION'S.**

**So:** M12 mints no `PolicyOverridden`, simulates none, and **builds no override mechanism** — not
as an event, a field or a code path. Entity §42's override-rate obligation is **named and left
unwired**, exactly as M11 left its expiry seam. ### **REPORT, IN THESE WORDS, THAT `P6-D71` REMAINS
### OPEN AND THAT M12 WAS THE POINT AT WHICH `CURRENT.md` EXPECTED IT TO CLOSE.** A later session
needs to know the expectation was not met and why, rather than discovering the gap by tripping over
it.

---

### 3.7 The seams that are already built — feed them, do not duplicate them

| What M12 needs | Where it already lives | What M12 does |
|---|---|---|
| a rule-vs-rule conflict | M7 `conflict.py` — `RULE_VS_RULE`, `conflicts.rule_id`, `M7Machine.raise_conflict` | **CALLS it.** Mints nothing, defines no conflict vocabulary, writes no `conflicts` row directly. |
| a human's attention on a wrong rule | M9 `exception.py` — `raise_exception`, `source_kind='rule'` | **CALLS it.** Adds no FK, no mirror column, no M9 migration. |
| the gate ladder | P3 `checkpoint.py` — `GateDecision`, and step 6 | **READS it.** Mints no decision, builds no registry, registers no class. |
| human approval under a condition | M4 `approval.py` | **ROUTES to it.** Builds no second approval system. |
| the posture a rule sits beneath | M11 `policy.py` — the ceiling total order | **READS it.** Writes no second ceiling comparison. |
| the tenant's humans | M1 `tenant_humans`, `AUTHORITY_ROLES` | **FOREIGN-KEYS to it.** Invents no authority. |
| provenance classes | M7 `PROVENANCE_CLASSES` (six) | **REUSES them.** Defines no private copy. |
| an expectation, not a gate | M8 `expectation.py` | *"Customer Y requires hourly updates"* compiles **through M8**. No new primitive. |
| durable timers | P5 `event_timers.py` | RU-8's TTL rides existing timers. **M12 imports no timer service and adds none.** |

### **⚠️ THE ONE PLACE THIS UNIT MAY LEGITIMATELY MOVE A LANDED BOUNDARY — AND THE ONE PLACE IT MUST
### NOT.**

`eval/phase0/gate_scan.py` states the ADR-010 carrier boundary **once**, as
`GATE_RUNTIME_MODULES = {checkpoint.py, phase3_checkpoint.py, pipeline_instance.py, policy.py,
phase6_policies.py}`, and `test_phase0_errata_guards.py` asserts the DISCOVERED carrier population
**EQUALS** it by exact set equality. If a `GATE_PRECONDITION` rule names a typed gate member in
executable code — and ADR-010 §6.1's fourth worked example says one may — then `rule.py` (and
`phase6_rules.py`, if the DDL carries the vocabulary) joins that set the way `pipeline_instance.py`
joined it at `P6-CP-2` and `policy.py` at `P6-CP-11`.

### **THAT IS A WIDENING WITH A NARROWING ATTACHED, AND THE NARROWING IS NOT NEGOTIABLE: `rule.py`
### MUST CONSTRUCT NO `GateEntry` AND NO `GateRegistry`. THE MINT ALLOWLIST STAYS `{checkpoint.py}`.**

**Either shape is acceptable and the permanent scenario measures the CONSISTENCY rather than the
choice:** the discovered carrier population must EQUAL the stated boundary. A module that carries the
vocabulary and is absent from the boundary is a drift; a boundary naming a module that carries
nothing is a widening bought for free. Both are failures. **Whichever route you take, every carrier
must still cite ADR-010, and you may not weaken, delete or subset-ify either boundary guard.**

---

## 4. What you must produce

Six files, and no seventh:

```
src/freight_recon/rule.py                      the machine
src/freight_recon/migrations/phase6_rules.py   the table, indexes, triggers, readiness oracle
src/freight_recon/schema.py                    EDITED: wire the migration in, P6RU_* symbols
eval/tests/test_phase6_rule.py                 the acceptance battery
scripts/probe_phase6_rule.py                   the deterministic probe
scripts/mutate_phase6_rule.py                  the mutation battery
eval/phase0/gate_scan.py                       EDITED, only if M12 carries gate vocabulary — §3.7
```

### **NAME THE MACHINE'S OWN TYPES THE WAY `policy.py` AND `compensation.py` NAME THEIRS.** Follow the
landed `P6XX_*` migration symbol convention exactly: `MIGRATION_ID`, `P6RU_SCHEMA_VERSION`,
`P6RU_TENANT_TABLES`, `P6RU_EXEMPT_TABLES`, `P6RU_TARGET_SCHEMA`, `P6RU_INDEXES`,
`P6RU_REPLACED_INDEXES`, `P6RU_TRIGGERS`, `P6RU_SCOPE_FORMS`, `P6RU_SINGLE_ACTIVE_SCOPES`,
`create_phase6_rules_schema`, `stamp_phase6_rules_version`, `phase6_rules_readiness_problems`.
Marker-last, like every phase.

**The table is `rules`, tenant-first, and it joins the canonical partition.** Declare
`P6RU_EXEMPT_TABLES` as empty **explicitly** rather than omitting it, so a future addition has to
defend its exemption.

**Canonical columns** (entity §10/§11): `rule_id` · `tenant` · `rule_version` · `scope` · `kind` ·
`compiled_predicate` · `state` · `source_instruction` · `authored_by` · `activated_by` ·
`test_vectors` — and optionally `expires_at` · `superseded_by` · `revoked_reason` · `conflict_id`.
`tenant, rule_version, scope, kind, compiled_predicate, state, source_instruction, authored_by` are
`NOT NULL`.

**`rule.py` must expose**, because the permanent scenario reads them:

```
RULE_STATES  TERMINAL_STATES  RULE_KINDS  PRODUCED_CONTRACTS  PROVENANCE_CLASSES
TRANSITIONS                    nine rows, ids RU-1 RU-2 RU-2f RU-3 RU-4 RU-5 RU-6 RU-7 RU-8
Trigger                        the closed consumed-fact vocabulary (HumanConfirmed, HumanActivated,
                               HumanRevoked, ConflictDetected, TimerFired) — CONSUMED, never minted
M12Machine                     propose · compile · confirm · activate · supersede · revoke · expire
CompilerInput                  provenance_class present, confidence ABSENT
compile_predicate_field()      the typed refusal
FORBIDDEN_ACKNOWLEDGEMENTS  reply_claims_enforcement()  honest_refusal()  assert_reply_is_honest()
PRECEDENCE_LADDER  PRECEDENCE_LAYER = 6  assert_within_precedence()
```

### The probe's interface

```
scripts/probe_phase6_rule.py
    --list-cases        the case names, one per line, kebab-case
    --list-dimensions   every mutation-axis token, one per line
    --case <name>       run exactly one case
    --all               run every case
    --concurrency N     --repeat N     --tenants N     --seed N     --delay-ms N
    --inject <fault>    the closed fault set; an unknown fault exits 2
    --actor <kind>      human | model | automation | timer | retry | counterparty | inbound | all
    --kind <k>          one of the four canonical rule kinds, or all   ← THIS UNIT'S OWN AXIS
    --outcome <o>       a | b | all                                    ← ITS SECOND AXIS
    --provenance <p>    one of the six canonical provenance classes, or all
    --direction <d>     narrow | broaden | all
    --scope <s>         one of the canonical scope forms, or all
    --brake <state>     engaged | released
```

### **`--kind` AND `--outcome` ARE THIS UNIT'S OWN TWO AXES**, the way `--direction` and
`--provenance` were M11's. The four kinds do not behave alike — `GATE_PRECONDITION` and `CONSTRAINT`
are evaluated by the checkpoint, `IDENTITY` and `CONFLICT_RESOLUTION` are consulted by two other
components entirely — so a generator that cannot vary the kind can only ever exercise one quarter of
the machine. And ### **the whole unit is the claim that exactly TWO outcomes exist**, which is a claim
a single-outcome case cannot make: `--outcome` is how the absence of a third one becomes measurable.

**Every case must be deterministic, hermetic and free of wall-clock sleeps**, and `--all` must run end
to end well under its scenario timeout.

### The case vocabulary — all 153 of them, exactly as spelled here

`--list-cases` must print every one of these, one per line, and `--case <name>` must run it.
### **THE SPELLING IS THE CONTRACT.** A case the scenario asks for and the probe does not
implement is a run that fails as a product defect for a naming reason, and a case the probe
implements under a different name is coverage nobody can cite.

```
a-model-may-propose-structured-candidate-text
a-proposal-is-not-an-enforceable-rule
the-source-instruction-is-retained-verbatim
an-offboarded-human-cannot-author-a-rule
a-counterparty-instruction-is-not-a-rule
inbound-content-can-never-author-a-rule
ru-1-emits-ruleproposed
ruleproposed-does-not-prove-the-rule-is-enforceable
compilation-is-deterministic-and-model-free
no-model-call-occurs-after-the-text-proposal
every-referenced-field-must-be-modelled
every-referenced-field-must-be-non-inferred
the-predicate-must-be-decidable-at-checkpoint-time
the-scope-must-resolve
never-bill-without-a-pod-compiles-to-a-gate-precondition
the-pod-rule-admits-only-the-three-canonical-provenance-classes
a-model-inferred-pod-is-denied-by-the-compiled-rule
compilation-is-byte-identical-reproducible
no-wall-clock-enters-compilation
no-randomness-enters-compilation
unordered-iteration-does-not-change-the-compiled-predicate
ru-2-emits-rulecompiled
rulecompiled-carries-the-compiled-predicate-and-the-test-vectors
do-not-use-carrier-x-for-produce-cannot-compile
the-owner-is-told-commodity-is-not-a-modelled-field
a-margin-rule-refuses-to-compile-on-model-inferred-cost
confidence-one-does-not-make-model-inferred-compilable
the-compiler-input-type-has-no-confidence-field
an-unmodelled-field-fails-to-compile
an-undecidable-predicate-fails-to-compile
an-unresolvable-scope-fails-to-compile
ru-2f-emits-rulenotenforceable
rulenotenforceable-names-exactly-what-is-missing
the-rejected-instruction-is-retained-as-organizational-memory
organizational-memory-carries-no-authority
the-reply-never-claims-a-procedure-was-noted
the-reply-never-claims-enforcement-without-an-active-rule-id
the-honest-refusal-sentence-is-emitted-verbatim
a-reply-claiming-enforcement-is-detected-on-literal-text
rejected-is-terminal
two-conflicting-active-rules-fail-closed
m12-raises-the-m7-rule-vs-rule-conflict
the-conflicting-rule-stays-compiled-and-blocked
conflicting-rules-are-never-auto-merged
neyma-never-picks-a-winner-between-two-rules
m12-mints-no-conflictraised-of-its-own
m12-builds-no-second-conflict-system
the-narrower-scope-wins-and-is-not-a-conflict
a-human-resolves-a-rule-vs-rule-conflict
an-open-rule-conflict-blocks-the-action
the-owner-is-shown-the-compiled-rule-before-confirming
the-owner-is-shown-the-generated-test-vectors
confirmation-without-test-vectors-is-refused
the-owner-is-never-asked-to-approve-opaque-source-text
ru-4-emits-ruleconfirmed
ruleconfirmed-does-not-activate
activation-requires-an-authenticated-human
a-model-cannot-activate-a-rule
automation-cannot-activate-a-rule
a-timer-cannot-activate-a-rule
a-retry-handler-cannot-activate-a-rule
a-counterparty-cannot-activate-a-rule
a-cross-tenant-activator-is-refused
a-cross-tenant-author-is-refused
active-requires-a-non-null-activated-by
an-unauthorized-activation-emits-the-registered-f14-security-event
m12-mints-no-second-unauthorized-activation-contract
ru-5-emits-ruleactivated
re-activating-an-already-active-version-is-a-no-op
a-no-op-reactivation-emits-no-second-ruleactivated
a-no-op-reactivation-does-not-bump-the-rule-version
a-newer-version-supersedes-the-active-one
the-superseded-version-is-retained-permanently
the-superseded-version-still-explains-its-historical-decisions
supersession-never-edits-history-in-place
a-cross-tenant-supersession-is-refused
ru-6-emits-rulesuperseded
a-narrowing-revocation-is-immediate
a-broadening-revocation-requires-the-policy-owner
automation-cannot-perform-a-broadening-revocation
rulerevoked-carries-the-canonical-direction
there-is-no-temporary-tighten-then-automatic-revert-path
only-a-narrowing-rule-may-carry-an-expiry
a-broadening-rule-cannot-carry-an-expiry
a-narrowing-rules-expiry-broadens-authority
expiry-requires-a-human-at-expiry
timerfired-never-broadens-authority
ru-8-emits-ruleexpired
ruleexpired-does-not-prove-automatic-broadening
expiry-raises-the-m9-human-confirmation-exception
m12-builds-no-part-of-m9
gate-precondition-rules-are-evaluated-at-checkpoint-step-6
constraint-rules-are-evaluated-at-checkpoint-step-6
a-denying-rule-yields-no-witness-and-no-effect
a-denying-rule-yields-no-grant
m12-is-checkpoint-step-6-and-builds-no-second-checkpoint
m12-mints-no-gate-decision
checkpoint-py-remains-the-sole-gate-minter
m12-constructs-no-gateentry-and-no-gateregistry
the-production-gate-registry-population-stays-empty
a-gate-precondition-rule-may-require-human-approval-under-a-condition
rule-evaluation-is-part-of-the-checkpoint-the-brake-gates
there-is-no-allow-on-rule-error-path
the-rule-engine-unavailable-yields-no-witness-and-no-effect
rules-sit-beneath-policy-at-precedence-layer-six
a-rule-never-overrides-a-constraint
a-rule-never-overrides-a-permanent-product-truth
a-rule-never-overrides-a-brake-denial
a-rule-never-overrides-the-product-policy-ceiling
a-rule-never-overrides-a-tenant-policy
m12-builds-no-second-precedence-engine
an-expired-instruction-has-no-force
tenant-is-first-in-the-rule-primary-key
the-same-scope-and-kind-in-two-tenants-does-not-collide
a-cross-tenant-rule-lookup-is-refused
rule-version-uniqueness-is-tenant-local
rule-uniqueness-is-never-global-across-tenants
one-active-rule-per-tenant-scope-and-kind-where-the-scope-admits-one
where-multiple-active-rules-are-permitted-conflict-detection-handles-them
rule-version-is-monotonic-per-tenant
a-rule-version-is-never-reused
a-rule-version-is-never-retroactively-edited
concurrent-activation-yields-exactly-one-active-rule
a-stale-occ-write-is-refused
a-rule-row-cannot-be-deleted
every-historical-version-is-retained
an-old-decision-is-explained-under-its-own-rule-version
replay-reconstructs-rule-history-only
replay-creates-no-human-authority
replay-does-not-activate-a-rule
replay-mints-zero-witnesses-grants-and-effects
the-eight-f12-contracts-and-no-ninth
m12-mints-no-unregistered-event-name
conflictraised-belongs-to-m7-and-m12-does-not-mint-it
the-f14-security-contract-is-not-m12s-to-mint
humanconfirmed-and-humanactivated-are-consumed-facts-not-minted-events
timerfired-is-a-consumed-fact-not-an-m12-event
policyoverridden-is-unregistered-and-m12-mints-none
override-rate-is-the-rule-health-metric
a-repeatedly-overridden-rule-asks-a-human
a-repeatedly-overridden-rule-is-never-auto-disabled
q3-stays-deferred-and-fail-closed
m12-ships-dark-with-zero-production-importers
m12-joins-no-outbound-channel
m12-builds-no-rule-editor-or-authoring-surface
m12-imports-no-network-primitive
m12-imports-no-timer-service
m13-brake-lifecycle-is-not-built
no-autonomy-graduation-engine-is-built
v4-v5-stay-open-and-fail-closed
nothing-is-registered-deterministic-id-match-only
every-conflict-goes-to-a-human
m1-through-m11-are-unchanged
```
### The probe's output contract

Every case prints a `### MISS ###` marker on failure and a positive line on success — the shared
harness vocabulary every P6 probe carries, alongside `### NOT REFUSED`, `### WRONGLY REFUSED` and
`### WRONG REFUSAL`. The scenario forbids all four globally, so a case that silently does nothing
cannot read as a case that passed.

The narrative run must print `behaviours as specified, 0 wrong` on success, and **exactly these
headlines**, each emitted by the case that actually establishes it:

```
A HUMAN INSTRUCTION EITHER COMPILES INTO AN ENFORCEABLE RULE OR IS HONESTLY REFUSED
THERE IS NO THIRD OUTCOME
A MODEL MAY PROPOSE TEXT; IT NEVER COMPILES
COMPILATION IS DETERMINISTIC, WITH NO MODEL IN THE LOOP
A RULE MAY NEVER BRANCH ON A GUESS
CONFIDENCE IS STRUCTURALLY NOT AN INPUT
AN UNMODELLED FIELD DOES NOT COMPILE
NEVER BILL WITHOUT A POD COMPILES TO A REAL PRECONDITION
AN INFERRED POD IS NOT A POD
DO NOT USE CARRIER X FOR PRODUCE CANNOT COMPILE, AND THE OWNER IS TOLD
NOTED THE PROCEDURE IS FORBIDDEN WITHOUT AN ACTIVE RULE ID
AN INSTRUCTION THAT DID NOT COMPILE IS MEMORY, NOT AUTHORITY
THE OWNER SEES THE COMPILED RULE AND ITS TEST VECTORS BEFORE CONFIRMING
ACTIVATION REQUIRES AN AUTHENTICATED HUMAN
A MODEL CAN NEVER ACTIVATE A RULE
AUTOMATION CAN NEVER ACTIVATE A RULE
TWO CONFLICTING RULES FAIL CLOSED
NEYMA NEVER PICKS A WINNER BETWEEN TWO RULES
M12 RAISES THE M7 RULE_VS_RULE CONFLICT AND BUILDS NO SECOND ONE
THE NARROWER SCOPE WINS, AND THAT IS NOT A CONFLICT
THE OLD VERSION IS RETAINED BECAUSE EFFECTS WERE JUDGED UNDER IT
RE-ACTIVATING AN ACTIVE VERSION IS A NO-OP
A NARROWING REVOCATION IS IMMEDIATE; A BROADENING ONE NEEDS THE OWNER
THE CLOCK MAY TAKE AUTHORITY AWAY; THE CLOCK MAY NEVER GIVE IT
AN EXPIRY THAT BROADENS REQUIRES A HUMAN AT EXPIRY
A DENYING RULE MEANS NO WITNESS AND NO EFFECT
THERE IS NO ALLOW-ON-ERROR DEFAULT
M12 MINTS NO GATE DECISION
THE CHECKPOINT IS STILL THE ONLY GATE MINTER
M12 BUILDS NO SECOND CHECKPOINT
A RULE NEVER OVERRIDES A CONSTRAINT
A RULE NEVER OVERRIDES A PERMANENT PRODUCT TRUTH
A RULE NEVER OVERRIDES A BRAKE DENIAL
A RULE NEVER OVERRIDES POLICY
REPLAY CREATES NO AUTHORITY
A REPEATEDLY OVERRIDDEN RULE ASKS A HUMAN AND IS NEVER AUTO-DISABLED
M12 SHIPS DARK WITH ZERO PRODUCTION IMPORTERS
THE M13 BRAKE MACHINE IS NOT BUILT
NOTHING GRADUATES
THE M1 WORK ITEM MACHINE IS UNCHANGED
THE M2 PIPELINE MACHINE IS UNCHANGED
THE M3 EFFECT AUTHORITY IS UNCHANGED
THE M4 APPROVAL MACHINE IS UNCHANGED
THE M7 CONFLICT MACHINE IS UNCHANGED
THE M9 EXCEPTION MACHINE IS UNCHANGED
THE M11 POLICY MACHINE IS UNCHANGED
```

**And these alarm markers, printed only when the named defect actually occurred.** A marker the probe
can never emit is decoration; a marker it emits on a correct product is worse.

```
### A MODEL COMPILED A RULE ###                    ### A MODEL CONFIRMED A RULE ###
### A MODEL PROPOSAL BECAME AN ACTIVE RULE ###     ### A MODEL CALL ENTERED COMPILATION ###
### A MODEL EVALUATED A RULE ###                   ### A MODEL RESOLVED A RULE CONFLICT ###
### INBOUND CONTENT AUTHORED A RULE ###            ### A COUNTERPARTY AUTHORED A RULE ###
### AN OFFBOARDED HUMAN AUTHORED A RULE ###        ### AN UNAUTHENTICATED ACTOR AUTHORED A RULE ###
### RuleProposed TREATED AS ENFORCEMENT ###        ### SOURCE INSTRUCTION DISCARDED ###
### NON-DETERMINISTIC COMPILATION ###              ### WALL CLOCK ENTERED COMPILATION ###
### RANDOMNESS ENTERED COMPILATION ###             ### UNORDERED ITERATION CHANGED THE COMPILED PREDICATE ###
### UNDECIDABLE PREDICATE COMPILED ###             ### UNRESOLVABLE SCOPE COMPILED ###
### PREDICATE ADMITTED AS A PROMPT STRING ###      ### COMPILED WITHOUT TEST VECTORS ###
### MODEL_INFERRED PREDICATE COMPILED ###          ### MODEL_INFERRED READ AT CONFIDENCE ONE ###
### CONFIDENCE READ BY THE COMPILER ###            ### CONFIDENCE FIELD PRESENT ON THE COMPILER INPUT ###
### UNMODELLED FIELD COMPILED INTO A PREDICATE ### ### NOTED THE PROCEDURE WITHOUT COMPILING A RULE ###
### ENFORCEMENT CLAIMED WITHOUT AN ACTIVE RULE ID ###   ### THE OWNER WAS NOT TOLD IT IS NOT A RULE ###
### RuleNotEnforceable OMITTED WHAT IS MISSING ### ### ORGANIZATIONAL MEMORY TREATED AS AUTHORITY ###
### A THIRD OUTCOME APPEARED ###                   ### REJECTED REOPENED ###
### CONFLICTING RULES AUTO-MERGED ###              ### NEYMA PICKED A WINNER ###
### A CONFLICTING RULE ACTIVATED ###               ### SECOND CONFLICT SYSTEM BUILT ###
### M7 BYPASSED ###                                ### DUPLICATE ConflictRaised MINTED ###
### M7 SEMANTICS MODIFIED ###                      ### AN OPEN RULE CONFLICT DID NOT BLOCK ###
### CONFIRMED WITHOUT SEEING THE COMPILED RULE ### ### CONFIRMED WITHOUT SEEING THE TEST VECTORS ###
### RuleConfirmed TREATED AS ACTIVATION ###        ### A MODEL ACTIVATED A RULE ###
### AUTOMATION ACTIVATED A RULE ###                ### A RETRY HANDLER ACTIVATED A RULE ###
### A TIMER ACTIVATED A RULE ###                   ### A COUNTERPARTY ACTIVATED A RULE ###
### A SERVICE ACCOUNT ACTIVATED A RULE ###         ### ACTIVE WITHOUT AN ACTIVATOR ###
### ACTIVATED BY A NON-HUMAN ACTOR ###             ### CROSS-TENANT ACTIVATION ACCEPTED ###
### CROSS-TENANT AUTHORSHIP ACCEPTED ###           ### UNAUTHORIZED ACTIVATION WENT UNRECORDED ###
### SECOND UNAUTHORIZED-ACTIVATION CONTRACT MINTED ###  ### RE-ACTIVATION EMITTED A SECOND RuleActivated ###
### RE-ACTIVATION BUMPED THE VERSION ###           ### SUPERSEDED VERSION DELETED ###
### HISTORY EDITED IN PLACE ###                    ### RULE APPLIED RETROACTIVELY ###
### OLD VERSION NO LONGER EXPLAINS ITS DECISIONS ###    ### CROSS-TENANT SUPERSESSION ACCEPTED ###
### BROADENING REVOCATION BY AUTOMATION ###        ### REVOCATION DIRECTION MISSING ###
### NARROWING REVOCATION BLOCKED ON REVIEW ###     ### TEMPORARY TIGHTEN AUTO-REVERTED ###
### EXPIRY BROADENED AUTHORITY ###                 ### TimerFired BROADENED AUTHORITY ###
### BROADENING RULE CARRIED AN EXPIRY ###          ### EXPIRY RAISED NO HUMAN CONFIRMATION ###
### AUTOMATIC BROADENING ###                       ### M12 MINTED A GATE DECISION ###
### M12 REGISTERED A GATE ###                      ### SECOND GATE MINTER BUILT ###
### SECOND CHECKPOINT BUILT ###                    ### SECOND GATE REGISTRY CONSTRUCTED ###
### PRODUCTION GATE REGISTRY POPULATED ###         ### CHECKPOINT BYPASSED ###
### CHECKPOINT STEP 6 BYPASSED ###                 ### WITNESS MINTED DESPITE A DENYING RULE ###
### GRANT MINTED DESPITE A DENYING RULE ###        ### EFFECT REACHED THE ADAPTER DESPITE A DENYING RULE ###
### ALLOW ON RULE ERROR ###                        ### A RULE OVERRODE A CONSTRAINT ###
### A RULE OVERRODE A PERMANENT PRODUCT TRUTH ###  ### A RULE OVERRODE A BRAKE DENIAL ###
### A RULE OVERRODE THE PRODUCT POLICY CEILING ### ### A RULE OVERRODE A TENANT POLICY ###
### SECOND PRECEDENCE ENGINE BUILT ###             ### AN EXPIRED INSTRUCTION STILL HAD FORCE ###
### M12 ENGAGED A BRAKE ###                        ### M12 NARROWED A BRAKE ###
### TENANT MISSING FROM THE PRIMARY KEY ###        ### GLOBAL UNIQUENESS COUPLED TWO TENANTS ###
### CROSS-TENANT RULE LOOKUP ACCEPTED ###          ### FALSE UNIQUENESS IMPOSED ON A MULTI-RULE SCOPE ###
### TWO ACTIVE RULES FOR ONE SINGLE-ADMITTING SCOPE ###  ### OCC BYPASSED ###
### RULE VERSION REUSED ###                        ### RULE VERSION OVERWRITTEN IN PLACE ###
### RULE VERSION WENT BACKWARDS ###                ### RULE ROW DELETED ###
### HISTORICAL VERSION DISCARDED ###               ### REPLAY ACTIVATED A RULE ###
### REPLAY MINTED AUTHORITY ###                    ### REPLAY MINTED A WITNESS ###
### REPLAY MINTED A GRANT ###                      ### REPLAY PRODUCED AN EXTERNAL EFFECT ###
### UNREGISTERED EVENT MINTED ###                  ### NINTH F12 CONTRACT MINTED ###
### PolicyOverridden MINTED ###                    ### PolicyOverridden SIMULATED ###
### AN OVERRIDE MECHANISM WAS BUILT ###            ### P6-D71 RESOLVED BY A BUILD SESSION ###
### A CONSUMED FACT WAS MINTED AS AN EVENT ###     ### EVENT WITHOUT ITS STATE ###
### STATE WITHOUT ITS EVENT ###                    ### REQUIRED PAYLOAD FIELD DROPPED ###
### A REPEATEDLY OVERRIDDEN RULE WAS AUTO-DISABLED ###   ### Q3 RESOLVED BY A BUILD SESSION ###
### OVERRIDE RATE UNOBSERVABLE ###                 ### M13 BRAKE MACHINE BUILT ###
### BRAKE LIFECYCLE BUILT ###                      ### AUTONOMY GRADUATION ENGINE BUILT ###
### RULE EDITOR BUILT ###                          ### RULE ADMIN UI BUILT ###
### PRODUCTION RULE IMPORTER BUILT ###             ### CHANNEL JOINED ###
### NOTIFIER WIRED ###                             ### TIMER SERVICE IMPORTED ###
### M12 PRODUCTION-ENABLED ###                     ### P7 PROVENANCE SURFACE BUILT ###
### V4 RESOLVED BY PREFERENCE ###                  ### V5 RESOLVED BY PREFERENCE ###
### PARALLEL ADMIN AUTHORITY INVENTED ###          ### M1 MACHINE EDITED ###
### M2 STATE MACHINE EDITED ###                    ### M3 EFFECT SEAM REWRITTEN ###
### M4 MACHINE EDITED ###                          ### M9 MACHINE EDITED ###
### M11 MACHINE EDITED ###
```

### The mutation battery

`scripts/mutate_phase6_rule.py` prints `N mutations caught, 0 escaped`. Each mutant reintroduces a
defect whose prohibition is canonically established, and each must turn the acceptance battery RED.
**Include an anti-vacuity control** — a mutant the battery is expected NOT to catch, or a
no-mutation run that must stay green — so the count is a measurement rather than an assertion. The
tree must be restored byte-identical afterwards, with `git status --porcelain` empty.

**At minimum, mutate:** a `MODEL_INFERRED` field compiling; the same at confidence 1.0; `confidence`
added to the compiler input; an unmodelled field compiling; a model compiling; a model confirming; a
model activating; automation activating; a timer activating; a retry handler activating; a
counterparty activating; `activated_by` made nullable; a cross-tenant activator accepted; a
cross-tenant author accepted; a reply claiming enforcement with no ACTIVE rule id; the honest refusal
losing the "not a rule" sentence; `RuleNotEnforceable` losing `missing`; test vectors omitted before
confirmation; `RuleConfirmed` treated as activation; two conflicting rules auto-merged; a local
conflict mechanism replacing M7; an M12-minted `ConflictRaised`; the narrower-scope precedence rule
inverted; tenant dropped from an index; uniqueness made global across tenants; a `rule_version`
reused; a superseded row deleted; history edited in place; a reactivation counted as a second
activation; a narrowing rule auto-expiring into broader authority; a broadening revocation by
automation; a rule overriding the Policy ceiling; a rule overriding the Brake; a `GateEntry`
constructed in M12; a second gate minter; checkpoint step 6 bypassed; an allow-on-error path; an
unregistered `Rule*` event minted; a consumed fact minted as an event; `PolicyOverridden` minted;
replay creating authority; an M13 brake lifecycle appearing; a production importer appearing; and a
no-op battery / zero-population scan accepted as green.

**Do not hard-code an expected mutation count anywhere.** The battery derives it.

---

## 5. What you must NOT do

- ### **BUILD ONLY M12.** Not P6. Not the phase. **Do not build M13 (Brake) or any brake lifecycle.**
  If M12 needs a reusable versioned-authority primitive, build **only the minimum M12 itself
  requires**, and do not generalise it for a machine that does not exist.
- ### **PRESERVE THE M1–M11 RUNTIME.** All eleven are landed and no further code is owed. Their
  residuals are debt rows. **Do not modify M1–M11**, and **M7 and M9 in particular are not edited at
  all** (`M12-AQ-6`). Do not open a remediation campaign against any `P6-D*` row.
- ### **`checkpoint.py` REMAINS THE SOLE GATE MINTER.** Do not construct a `GateEntry` or a
  `GateRegistry`, do not call `register_gate`, do not populate the production `GateRegistry`, and do
  not touch the checkpoint kernel's semantics, the witness's unconstructability, the claim CAS's
  `WHERE`-clause revalidation, or the brake.
- ### **REUSE M7's CONFLICT.** No second conflict table, module, type or event contract.
- ### **REUSE M9's EXCEPTION SEAM.** Name it, leave it unwired, add no FK and no mirror column.
- **Do not build an autonomy-graduation engine.** Nothing graduates.
- ### **SHIP DARK.** No production importer, no rule editor, no admin screen, no authoring or import
  surface, no channel join, no notifier, no oversight queue, no dashboard, no network primitive, no
  timer service, no P8 enablement, no autonomy graduation, and nothing reaching live traffic.
- **Do not score a P6 criterion, move P6's status, or unlock P7.**
- ### **DO NOT RESOLVE ANY OF THE SEVEN AUTHORITY QUESTIONS IN §3.6, AND DO NOT INVENT AUTHORITY THE
  CORPUS DOES NOT CONTAIN.** `M12-AQ-1` and `M12-AQ-3` are settled by canon and you build what they
  say; `M12-AQ-2`, `-4`, `-4b`, `-5`, `-6` and `-7` you build the fail-closed side of and **REPORT**.
  `V4`, `V5` and `Q3` stay open at their fail-closed defaults: nothing is registered, deterministic
  ID match only, every conflict goes to a human, and a repeatedly-overridden rule ASKS rather than
  auto-disabling.
- ### **DO NOT MINT AN UNREGISTERED EVENT**, including `PolicyOverridden`, and do not edit
  `event_contracts_data.json` to make an oracle pass.
- **Do not weaken, delete or subset-ify either ADR-010 boundary guard** (§3.7).
- ### **A LOCAL COMMIT IS ALLOWED AND EXPECTED. DO NOT PUSH, DO NOT DEPLOY, AND DO NOT ENABLE
  ANYTHING.** No remote operation of any kind.

---

## 5a. The review tier, stated once

`CLAUDE.md` §7 scales review with risk, and says: *"When genuinely torn between two tiers, take the
higher one once and say so."*

A state machine on its own is tier 2. **M12 is tier-1**, for three reasons, and this file says so
rather than leaving a later session to argue it:

1. **It lands a migration** — a new canonical table, new indexes, new triggers, and an edit to
   `schema.py`'s canonical partition.
2. **It is load-bearing for tenant isolation** — `rules` is tenant-first, its uniqueness is
   tenant-scoped, and a global uniqueness that coupled two brokerages would let one brokerage's
   standing rule decide another's gate.
3. **It decides whether an action is allowed, inside the checkpoint** — a `GATE_PRECONDITION` rule
   evaluated at step 6 can deny an effect or raise its gate, and if it may also carry gate vocabulary
   it **widens a safety guard's allowlist**, which §7 names explicitly.

So M12 takes the higher tier once and says so: a focused independent review by a session that did not
build it is **owed** before this lands.

---

## 6. How you will be measured

Product Driver runs `scenarios/p6_m12_rule.yaml` — the permanent scenario — plus generated
adversarial scenarios, then a completion audit and an independent review.

**The permanent scenario measures the DATABASE, the EVENT REGISTRY, THE LITERAL REPLY TEXT and the
AST, not your narration.** Twenty-one persisted-state and registry oracles, including one that issues
fourteen forbidden writes against a live canonical database behind three positive controls and an
asserted surviving-row count; one that exercises uniqueness in BOTH directions — refused in a
single-admitting scope, accepted in another tenant, and accepted in a multi-admitting scope, so a
blanket constraint fails as loudly as a missing one; one that walks the AST to prove `checkpoint.py`
is still the sole gate minter, with the kernel's own `_DEFAULT` as the positive control; one that
reads the event names you emit from the AST — excluding docstrings, so a comment saying
*"`ConflictRaised` is deliberately NOT minted here"* cannot trip it, and a real string literal can;
and ### **one that calls your reply guard on the literal sentence *"Noted the procedure for
raise_invoice"*, requires it REFUSED with no active rule id and ACCEPTED with one.**

**Every battery is invoked as `python -m pytest`, never the bare `pytest` console script**, and
`no tests ran` and `ERROR: file or directory not found` are globally forbidden — so a battery cannot
report the absence of a failure as the absence of a defect.

If a scenario oracle is wrong, **say so and show the evidence**; do not change the product to satisfy
a defective oracle. An oracle that cannot pass on a correct product is the mirror image of a false
green.

---

## 7. What to report

Alongside the ordinary evidence, state explicitly:

- **the seven authority questions**, which side you built, and — for `M12-AQ-4` — the
  `P6RU_SINGLE_ACTIVE_SCOPES` choice and its reasoning, marked as an answer to an OPEN question;
- ### **that `P6-D71` (`PolicyOverridden`) REMAINS OPEN, and that `CURRENT.md` expected it to close
  here** — see `M12-AQ-7`;
- that `P6-D73` (M9's `source_kind` FK) remains open and that M12 followed M11's precedent;
- that `V4`, `V5` and `Q3` remain open at their fail-closed defaults;
- the ADR-010 carrier-boundary route you took and why the mint allowlist is unchanged;
- that `criteria_scored` is `[]`, P6 has not moved, and P7 is still `BLOCKED`;
- and **the exact CI position, measured rather than assumed** — which jobs concluded what, and
  whether any of them executed M12's own tests.

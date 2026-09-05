# Build P6 / M13 — Brake. Only that.

You are building **one unit**: machine **M13**, the **Brake** — admission control, a global
withdrawal of the capability to act, enforced at the effect boundary by **refusing to mint** and
**refusing to claim**.

Not P6. Not the phase. Not P7. Not P8's brake runtime. Not a brake console, an admin screen, a
dashboard, a Slack command, a production detector, a freight workflow or bounded autonomy. **One
machine, its hardening migration, its acceptance battery, its probe, its mutation battery — and
nothing else.**

M12 (the Rule) landed as `P6-CP-12` at `ded6a841`. `docs/implementation/CURRENT.md` says, in its own
words, **"The next build checkpoint is M13 — the Brake."** That is this unit.

> ### **M13 IS THE THIRTEENTH AND LAST P6 MACHINE. LANDING IT DOES NOT COMPLETE P6.**
> A checkpoint is a landed increment. After M13 there is still a **distinct P6 acceptance /
> adjudication step** that this unit does not perform, does not prepare and does not anticipate.
> `criteria_scored` stays `[]`, P6 stays `READY` / `IN_PROGRESS`, and **P7 stays `BLOCKED`.**

---

## 0. Read the authority first, in this order

**Read these completely before writing a line.**

### **REPOSITORY AUTHORITY WINS.** Everything below is the authority. This task file is a reading of
### it, not a replacement for it. Where this file and the repository disagree, the repository is
### right and the disagreement is a finding you REPORT.

**Status and standing orders**
1. `CLAUDE.md` — the operating rules. §5 rule 17 (**one authority per domain** — the single most
   load-bearing rule in this entire unit), §5 rule 20 (replace phrasing, do not delete it), §7
   (review tiers), §9 (**a guard over an empty population proves nothing**), §0 (no committed
   receipts).
2. `PRODUCT.md` — what Neyma is for.
3. `docs/implementation/CURRENT.md` — **the short-form status authority.** Read the P6 table, the
   M12 block, the *Still owed* row, the Risks table, **⛔ What must NOT begin**, and the newly
   recorded residuals **`P6-D82`…`P6-D88`**.
4. `docs/implementation/IMPLEMENTATION-REGISTRY.yaml` — the binding per-phase detail. On any
   disagreement with the roadmap, **the registry wins.** Read the **P6** unit block including its
   `prohibited_scope` paragraph, the **P7** block, the **P8** block (whose `allowed_scope` contains
   the word *brake* — that is `M13-AQ-1`), the **P3** unit block, and **every open `P6-D*` row**.
5. `docs/implementation/implementation-roadmap.md` — the 16 migration principles. Principle 13: **no
   phase depends on future work for a CURRENT safety guarantee.**

**The M13 canonical corpus**
6. `docs/specifications/entities/16-brake.md` — **all 45 points.** Point 7 carries **amendment A1**,
   which supersedes the earlier `tenant_id = <sentinel>` wording and is why the platform row lives in
   its own tenant-exempt table. Read the amendment's *reason*, recorded so it is not re-litigated.
7. `docs/specifications/state-machines/13-brake.machine.md` — **all 43 sections**, and §14's
   transition table **row by row**. Its opening blockquote is load-bearing: it proves the necessity
   of no third state by refusing three candidate states by name.
8. `docs/specifications/events/13-brake-events.md` — family **F13**, all four contracts, and the
   **Cross-cutting** paragraph.

**Registries and global contracts**
9.  `docs/specifications/state-machines/registry.md` — §4 (M13's state set: `ACTIVE (R)` ·
    `RELEASED (T)`), and the `GR-*` rules. **`GR-11`, `GR-16` and `GR-17` are directly about this
    machine**, and `GR-16` states it in one line: *"Brake activation prevents new admission but does
    not kill in-flight work."*
10. `docs/specifications/events/registry.md` — **by its own header THE SOLE CANONICAL LIST of event
    names.** §3 (F13 is **four** names), §5 (the consequential set — `BrakeEngaged` and
    `BrakeReleased` are in it), §8 (**F13 is STRICT per-aggregate**), §9 (the coordination lens —
    `BrakeEngaged` is consumed by M2, M4 and M3 **each by its own guard**, never by the event's
    command), §11 (the security-event → brake auto-engagement table), and the `ER-*` rules —
    **`ER-11` and `ER-12` in particular. Read `M13-AQ-2` below before you conclude anything about
    which sense of "narrow" §11's closing line is using.**
11. `docs/specifications/entities/00-conventions.md` — `[C-1]`…`[C-10]`. **`[C-1]` (tenant-first),
    `[C-2]` (single-row atomicity), `[C-3]` (idempotency), `[C-5]` (replay) and `[C-9]` (deletion)
    are each cited by the brake entity by number.**
12. `docs/specifications/acceptance/foundational-machine-acceptance.md` — M13 is **5 transitions,
    `AC-MACH-1301..1305`**, gate **G4**. `AC-MACH-1305` is called out by name: *"no timer releases a
    brake."*
13. `docs/specifications/acceptance/platform-safety-acceptance.md` — **`AC-SAFE-006`** (brake before
    claim ⇒ the claim affects ZERO rows), **`AC-SAFE-007`** (brake after claim ⇒ does NOT kill the
    in-flight worker; **assert no `UNKNOWN_OUTCOME` was manufactured by the brake**),
    **`AC-SAFE-025`**, **`AC-SAFE-026`**, **`AC-SAFE-027`**, and the step-7 row of the 7×15 checkpoint
    matrix (`AC-CKPT-7-*`). **Read `M13-AQ-2` before you conclude anything about which sense of
    "narrow" `AC-SAFE-027` is using.**
14. `docs/architecture/target-system-specification.md` **§12.13** (the Brake lifecycle — five rows,
    two states), **§21.4** (admission control, M-59), **§21.5** (**the five-position in-flight
    boundary — read this table row by row; it is the reason this machine exists**), **§21.6**
    (activation, release, visibility — M-60, M-61, M-62), and **§26** (the `UNKNOWN_OUTCOME`
    process the brake hands ownership to and never resolves).
15. **`docs/architecture/decisions/ADR-011-human-brake.md` — IN FULL, all fifteen sections.** §0 is
    the ADR: *one of the reasons you pull the brake is that the policy engine is wrong*, so a brake
    implemented as a policy depends on the very subsystem it exists to overrule. §1 the decision. §3
    the five-position table. §4 the two states and §4.2's proof of sufficiency. §5 activation and the
    Sev-0 trigger table. §6 release and its four evidence conditions. §7 operator visibility. §8
    enforcement, and **§8.3's deliberate bluntness about version invalidation**. §9 the five scope
    dimensions **and the three deliberately rejected ones**. §10 interaction. §11 failure modes. §12
    the merge-gating tests. §14 migration.
16. Every ADR ADR-011 cites — **ADR-004** (§2.4 the seven-step atomic checkpoint, §3.5 the claim
    transaction, §7 why the CAS sits immediately before the call), **ADR-006** (§3.4 positive-control
    health, and the `UNKNOWN_OUTCOME` process), **ADR-008** (§2.3 the canonical Durable Machine, §2.10
    crash recovery, §3.3 `EXPIRED_UNCLAIMED`, §3.4 `VOID_ON_BRAKE`), **ADR-009** (§3.3 frozen
    entities and held commit keys), **ADR-010** (§7's autonomy ratchet — **the same sentence that
    governs this machine, and the ADR says so explicitly**), **ADR-002**, **ADR-003**, **ADR-005**
    (drift and `VOID_ON_DRIFT`), **ADR-007**.

---

### Then read the LANDED CODE — and for this unit that is not a formality

> ## ⚠️ **M13 DOES NOT START FROM ZERO. P3 ALREADY BUILT A BRAKE SUBSTRATE, AND IT IS LANDED AUTHORITY.**
>
> ### **`CURRENT.md` says `brake_lifecycle.py` and `phase6_brakes.py` are ABSENT from the tree. That
> ### is evidence M13 IS NOT YET LANDED. IT IS NOT, BY ITSELF, AUTHORITY TO DUPLICATE `brake.py`.**
>
> `CURRENT.md` says the other half in its own words too: ***"(`brake.py` is P3's landed kernel brake,
> not M13.)"*** Both halves are true at once, and building this unit correctly means holding them
> both.

**Before you edit anything, MAP what P3 already guarantees.** Do this mechanically, and put the
result in your report. The bootstrap did it and found the following — **verify it rather than
trusting it, because it was measured on `ded6a841` and you may not be on `ded6a841`.**

| Seam | Read | What is ALREADY THERE |
|---|---|---|
| ### **The one brake authority** | `src/freight_recon/brake.py` | ### **`BrakeStore` — the ONLY class that owns brake state.** `engage`, `engage_platform`, `widen`, `narrow`, `release`, `admission_denied`, `version_token`, `status`, `platform_status`, `active_report`; `BrakeError`, `BrakeStoreUnreachable`, `HUMAN`, `DETECTOR`, `TENANT_WIDE`; the closed scope grammar in `_scope_for`. |
| **The brake tables** | `migrations/phase3_checkpoint.py` | `brakes` (tenant-first, `CHECK state IN ('ACTIVE','RELEASED')`, `CHECK actor_kind IN ('HUMAN','DETECTOR')`, `CHECK state != 'RELEASED' OR (released_by IS NOT NULL AND release_decision_ref IS NOT NULL)`), the partial `UNIQUE (tenant, scope) WHERE state='ACTIVE'`, and `platform_brake` — **one row, `PRIMARY KEY (id) CHECK (id = 1)`, no tenant column, seeded `RELEASED` at version 0.** `P3_EXEMPT_TABLES = ("platform_brake",)` is the recorded, defended exemption. |
| **Checkpoint step 7** | `src/freight_recon/checkpoint.py` | Step 7 calls `admission_denied` **inside the checkpoint transaction**, refuses with `BRAKE_ENGAGED`, and refuses with `BRAKE_UNREADABLE` on `BrakeStoreUnreachable`. The witness carries `brake_version TEXT NOT NULL` — the **composite token**. |
| ### **The claim CAS** | `src/freight_recon/checkpoint.py` | ### **`UPDATE effect_grants SET state='CLAIMED' … WHERE … AND brake_version = ? AND policy_version = ?`** — the race is **already** closed by the database, and `version_token` is **already** the composite of both owners. |
| **The composite token** | `brake.py::version_token` | `bv1\|global:<n>\|tenant:<m>` — both monotonic components, in one deterministic string. |
| **M4's interaction** | `src/freight_recon/approval.py` | Imports `BrakeStore`; `VOID_ON_BRAKE` is a landed approval state and `AP-5` is a landed transition. ### **This is built. Do not build it again.** |
| **The event contracts** | `event_contracts_data.json` | ### **All four F13 contracts are ALREADY REGISTERED** — `BrakeEngaged`, `BrakeWidened`, `BrakeNarrowed`, `BrakeReleased`, strict-order, `aggregate_type=brake`, with `BrakeNarrowed`/`BrakeReleased` marked `human_only` — **and so is F14's `UnauthorizedBrakeReleaseAttempted`.** The total is **118**. You register nothing. |
| **The consumers** | `pipeline_instance.py`, `approval.py` | Both already carry `BrakeEngaged` as a **consumed** coordination fact. ### **Do not delete these to make a "who mints what" scan green — that breaks M2 and M4.** |
| **The human identity substrate** | `work_item.py`, `migrations/phase6_work_items.py` | `tenant_humans` and `AUTHORITY_ROLES = ("POLICY_OWNER", "AUTHORIZED_HUMAN")`. **The only human authority model in the repository.** |
| **The landed P3 battery** | `eval/tests/test_phase3_brake.py` and the checkpoint/CAS batteries | **19 brake tests already pass**, including the interleaved brake-vs-claim race and the SD-12 amendment guards. ### **These are your regression anchors and they must stay green.** |

### **AND HERE IS WHAT IS MISSING — THE M13 DELTA, MEASURED AGAINST `ded6a841`**

Each of these is **RED today** and each is genuinely M13's to close. **Verify each on your own tree
before building; the list is a reading aid, not a specification.**

| # | The missing canonical guarantee | Authority |
|---|---|---|
| 1 | ### **NOTHING ON THE TREE EMITS ANY F13 EVENT.** All four contracts are registered and none is produced. `brake.py` writes no outbox row at all. | `13-brake-events.md`; entity point 31 |
| 2 | ### **NO UNAUTHORIZED RELEASE IS RECORDED.** A refused release raises `BrakeError` and reaches no security event. | events registry §11; ER-12 |
| 3 | ### **RELEASE HAS NO EVIDENCE.** `release()` enforces *human + non-empty decision_ref* and nothing else — no in-flight accounting, no Sev-0 check, no positive integration health. Its own docstring names a `release_blockers` seam **that does not exist anywhere on the tree** (`M13-AQ-9`). | entity 36; machine BR-4; ADR-011 §6 |
| 4 | **A brake row can be DELETED.** There is no append-only trigger on `brakes` or `platform_brake`. | entity 28/29 `[C-9]` |
| 5 | **`released_by` is any non-empty string.** No FK into `tenant_humans`, no `released_by_kind`, so *"`released_by` is never a detector id"* is not a database constraint. | entity 16/18 |
| 6 | **There is no signal count.** A flapping detector produces one ACTIVE brake — correctly — but the *"rising signal count"* has nowhere to live. | entity 33; machine §19; ADR-011 §11 |
| 7 | **The five BR transitions are not declared as a machine table.** `BR-*` appears only in prose, and BR-5 is not an *enumerated* non-producing refusal. | machine §14/§15 |
| 8 | **The R17 report cannot state** what is still allowed, prevented effects, in-flight effects and status, unresolved unknown outcomes with exposure, or the exact release requirements. | entity 42; ADR-011 §7 |
| 9 | **The scope vocabulary is narrower than canonical**, and the deferral is a code comment rather than a declared, mechanically checkable partition (`M13-AQ-6`). | entity 12; ADR-011 §9 |
| 10 | **`engaging during an adapter call manufactures no unknown outcome` is not positively asserted anywhere.** The brake has no kill path — which is correct — but ### **the absence of a kill path is not the same proof.** | ADR-011 §12; `AC-SAFE-007` |
| 11 | **The platform row is REUSED across incidents:** re-engaging after a release overwrites `released_by`, erasing the previous incident's release record (`M13-AQ-8`). | entity 27/29/30 |

> ### **THE WHOLE UNIT IS: CLOSE THAT DELTA ON THE ONE AUTHORITY THAT ALREADY EXISTS.**

### How to weigh the authorities

1. `events/registry.md` is **the sole canonical list of event names.** A name not in it is not an
   event, however many files use it.
2. The **machine table (§14) governs the transition set** where the entity prose is looser.
3. `IMPLEMENTATION-REGISTRY.yaml` beats the roadmap.
4. **The landed code beats every document about the landed code.**

### **WHERE THEY GENUINELY CONFLICT, YOU RECORD THE CONFLICT AND BUILD THE FAIL-CLOSED SIDE. YOU DO
### NOT RESOLVE IT.** §3.6 lists the nine this bootstrap found. Finding a tenth is a good outcome;
silently settling any of them is not. ### **DO NOT INVENT AUTHORITY THAT DOES NOT EXIST.** If the
corpus does not answer a question, the answer is "the corpus does not answer this", recorded as an
authority question, with the fail-closed behaviour built and stated — never a preference presented as
a finding.

---

## 1. What Neyma is — the stable identity

Neyma is an operational teammate for a freight brokerage: it reads the mailbox, extracts what is
happening, and does the operational work a broker would otherwise do by hand. **Every external effect
is gated, witnessed, single-use, verified and attributable to a named human or to an explicitly
granted autonomy.**

This machine is the one an operator reaches for when everything else is wrong.

> ### **THE BRAKE IS ADMISSION CONTROL, NOT PROCESS TERMINATION. IT IS ENFORCED BY REFUSING TO MINT
> ### AND REFUSING TO CLAIM — NEVER BY KILLING A WORKER.**
>
> A brake that kills workers manufactures the exact thing the architecture fears most: ### **an
> `UNKNOWN_OUTCOME`.** A process killed mid-adapter-call leaves an effect that **may have landed**,
> with **nobody to verify it**. *You would engage the brake to become safer and, in the very act of
> engaging it, create a payable of unknown status.*
>
> ### **THE BRAKE STOPS THE NEXT EFFECT. IT CANNOT STOP THE LAST ONE — AND IT MUST NOT PRETEND TO.**

---

## 2. Where the program stands

Twelve P6 machines are landed — M1 Work Item, M2 Pipeline Instance, M3 External Effect / Grant, M4
Approval, M5 Observation, M6 Identity Binding Claim, M7 Conflict, M8 Expectation, M9 Exception, M10
Compensation, M11 Policy, M12 Rule — as `P6-CP-1` … `P6-CP-12`. **All twelve ship dark.**
`criteria_scored` is `[]` on every one of them; **P6 is `status: READY` / `execution_state:
IN_PROGRESS`; P7 is `BLOCKED` / `NOT_STARTED`.**

**M13 is the thirteenth and last.** Of the 134 canonical transitions, 129 are written and landed; the
five that remain are **exactly M13's `BR-1`…`BR-5`**.

### **LANDING M13 SCORES NO P6 CRITERION, AND IT IS NOT P6 COMPLETION.** `criteria_scored` stays `[]`.
### P6's status does not move. **P7 stays `BLOCKED`.** ### **THAT M13 IS THE LAST MACHINE IS EXACTLY
### THE REASON TO STATE THIS TWICE**: the single most likely scope failure in this unit is a session
that lands the machine and then, reasonably and wrongly, treats the phase as finished. **You do NOT
perform P6 acceptance: it is a distinct, later, adjudicated step, and it is not yours.**

---

## 3. The unit: M13, and nothing else

### 3.1 The sentences the whole unit is a consequence of

> ### **A BRAKE REFUSES TO MINT AND REFUSES TO CLAIM. IT NEVER KILLS A WORKER.**
> ### **THE BRAKE STOPS THE NEXT EFFECT, NOT THE LAST.**
> ### **AUTOMATION MAY ENGAGE AND WIDEN. AUTOMATION MAY NEVER NARROW OR RELEASE.**
> ### **A DETECTOR MAY NEVER CLEAR ITS OWN ALARM. A MODEL IS NOT A SEV-0 DETECTOR.**
> ### **A BRAKE NEVER EXPIRES — A CLOCK CANNOT KNOW WHETHER THE FIRE IS OUT.**
> ### **"CANNOT READ THE BRAKE" NEVER MEANS "THE BRAKE IS OFF".**
> ### **RELEASE REQUIRES POSITIVE EVIDENCE, AND REQUIRING CEREMONY TO BECOME SAFER IS A DESIGN ERROR.**
> ### **THERE IS EXACTLY ONE BRAKE AUTHORITY.**

### 3.2 The canonical state set — **TWO, and there is no third**

```
ACTIVE (recoverable)   RELEASED (terminal)
```

`state-machines/registry.md` §4 gives exactly these. The machine file's opening blockquote proves the
necessity of no more:

- ### **"Engaged by a human" vs "engaged by a detector" is an `actor_kind` FIELD, not a state.** It
  changes *who may release*, not *what the brake does*.
- ### **"Partially released" is a SCOPE CHANGE, not a state** — the brake is still `ACTIVE`, over a
  smaller scope, reached by `BR-3`.
- ### **"Pending release" would be a state only if release needed an approval workflow. IT MUST NOT.**
  *Requiring ceremony to become SAFER is a design error*, and requiring ceremony to become **unsafe**
  is exactly what BR-4's evidence conditions are for.

### **DO NOT INVENT `PENDING`, `PENDING_RELEASE`, `ENGAGING`, `DISENGAGED`, `EXPIRED`, `SUSPENDED`,
### `PARTIAL`, `FAILED`, `CANCELLED`, `PAUSED`, `DISABLED`, `ENABLED`, `RECOVERING`, OR ANY THIRD
### STATE.** The permanent scenario attempts six of them against a live database and requires each to
be **REFUSED BY THE DATABASE**, with positive controls and an asserted surviving-row count so a
uniformly hostile schema cannot pass.

### 3.3 What M13 emits, and what it consumes

**M13 mints EXACTLY these four F13 contracts, all of which are ALREADY REGISTERED:**

```
BrakeEngaged   (BR-1)   scope, actor, reason, brake_version
BrakeWidened   (BR-2)   scope, brake_version
BrakeNarrowed  (BR-3)   scope, brake_version                     human_only
BrakeReleased  (BR-4)   released_by, release_decision_ref, brake_version   human_only
```

### **THERE IS NO FIFTH F13 CONTRACT.** There is no `BrakeExpired`, no `BrakeAutoReleased` and no
### `BrakePendingRelease` — **and their absence from the registry is the state set restated as a
### contract.** Each of those three is precisely the event a wrong state set would need. If you find
yourself wanting to mint one, the defect is upstream in your state set.

**F13 is STRICT per-aggregate** (events registry §8), ordered on the monotonic `brake_version`.
Carry `previous_aggregate_version` wherever the landed envelope requires it — **read
`event_envelope.py` and `event_outbox.py` rather than assuming the shape.**

### **`UnauthorizedBrakeReleaseAttempted` IS NOT YOURS TO MINT — IT IS ALREADY REGISTERED, IN F14.**
An unauthorized release attempt reaches **that registered contract**. ### **DO NOT INVENT AN
M13-LOCAL SYNONYM** (`BrakeReleaseRefused`, `UnauthorizedRelease`, `BrakeSecurityEvent` or anything
else). A second name for one fact is how one fact becomes two half-facts.

**What M13 CONSUMES, and does not mint:** Sev-0 detector signals, and human engage / widen / narrow /
release commands. A consumed fact is not an event you mint.

### **`BrakeEngaged` IS CONSUMED BY M2, M4 AND M3 — EACH BY ITS OWN DETERMINISTIC GUARD** (events
registry §9). A coordination event does **not** instruct a consumer to transition. `pipeline_instance.py`
and `approval.py` already consume it. **You emit it; they react by their own guards; you change
neither of them.**

### 3.4 Implement the canonical `BR-*` transition contract

Derive these from `13-brake.machine.md` §14, `target-system-specification.md` §12.13 and ADR-011 §4 —
**not from this table, which is a reading aid.**

| ID | From → To | Who | Guards | Writes | Event |
|---|---|---|---|---|---|
| **BR-1** | — → `ACTIVE` | human **or** detector | ### **any authenticated human INSTANTLY — no approval, no review, no ceremony — OR an automated Sev-0 detector.** ### **NEVER requires the system to be healthy.** One atomic row write. | `scope`, `actor`, `reason`, `brake_version++` | `BrakeEngaged` |
| **BR-2** | `ACTIVE` → `ACTIVE` *(wider scope)* | human **or** automation | ### **widening a brake NARROWS AUTHORITY ⇒ the safe direction ⇒ automation may** | `scope`, `brake_version++` | `BrakeWidened` |
| **BR-3** | `ACTIVE` → `ACTIVE` *(narrower scope)* | ### **human ONLY** | ### **narrowing a brake BROADENS AUTHORITY ⇒ the unsafe direction ⇒ AUTHENTICATED HUMAN ONLY.** Automation, a detector, a model and a timer are each refused. | `scope`, `brake_version++` | `BrakeNarrowed` |
| **BR-4** | `ACTIVE` → `RELEASED` | ### **human ONLY** | ### **every in-flight effect ACCOUNTED FOR; no unresolved Sev-0 in scope; integration health POSITIVELY DEMONSTRATED; a `decision_ref`.** A detector that engaged it may NEVER release it. | `released_by`, `release_decision_ref`, `brake_version++` | `BrakeReleased` |
| **BR-5** | `ACTIVE` + `TimerFired` | — | ### ⛔ **ILLEGAL — `NON_PRODUCING: GR1_ILLEGAL_REFUSAL`.** No destination state. No write. No event. No TTL. No expiry. | — | — |

> ### **"NARROW" AND "BROADEN" REFER TO AUTHORITY THROUGHOUT — NEVER TO BRAKE SCOPE.**
> **Widening a brake narrows authority (safe; automation may). Narrowing a brake broadens authority
> (unsafe; humans only).** ADR-011 §5.1 records this because the word is genuinely ambiguous, and it
> is recorded in the Semantic Model's ambiguous-words list. ### **GET THIS BACKWARDS AND THE ENTIRE
> RATCHET INVERTS WHILE EVERY TEST NAME STILL READS CORRECTLY.**

**BR-5 is an ENUMERATED illegal refusal, not an unwritten path.** `TRANSITION-EVENT-AUDIT.yaml`
classifies `13-brake:BR-5` under `NON_PRODUCING` with `reason_code: GR1_ILLEGAL_REFUSAL` and the proof
*"no To state; Writes = em-dash"*. Declare it in the transition table so a scheduler added later
cannot quietly find a door. **See `M13-AQ-7` before you conclude it emits anything.**

### 3.5 What must hold — the authority and safety requirements

#### 3.5.1 ### **ADMISSION CONTROL, AND THE FIVE-POSITION BOUNDARY**

### **THIS IS THE REASON THE MACHINE EXISTS, AND IT IS THE ONE REQUIREMENT A PLAUSIBLE
### IMPLEMENTATION GETS WRONG WHILE LOOKING RIGHT.**

| Position | Ledger | World changed? | Brake behaviour |
|---|---|---|---|
| **1. Not yet executing** | none | NO | ### **STOP.** Halt durably. Nothing happened. |
| **2. Grant MINTED, UNCLAIMED** | `GRANTED` | ### **NO — the adapter never acted** | ### **STOP.** The grant becomes unclaimable ⇒ `EXPIRED_UNCLAIMED`; the pipeline is `VOIDED`. |
| **3. CLAIMED, adapter not yet called** | `CLAIMED` | ⚠️ we cannot prove it didn't | ### **DO NOT KILL.** Let it complete and verify. |
| **4. Adapter called, response pending** | `CLAIMED` | ### ⚠️ **POSSIBLY — this is the money** | ### **DO NOT KILL. LET IT FINISH AND VERIFY.** |
| **5. Verification in progress** | `ATTEMPTED` | yes, and we are finding out what | ### **LET IT FINISH.** *Verification is a READ. The brake has no reason to stop a read.* |

> ### **IN-FLIGHT EFFECTS AT POSITIONS 3, 4 AND 5 — CLAIMED, EXECUTING, VERIFYING — RUN TO A
> ### VERIFIED CONCLUSION** (machine §40). *The only thing worse than an effect you didn't want is
> an effect you didn't want AND cannot describe.*

**If verification genuinely becomes impossible**, ownership transfers to the `UNKNOWN_OUTCOME` process
of ADR-006 — non-terminal, human-owned, entity frozen, commit key held, escalated with the dollar
exposure. ### **THE BRAKE DOES NOT RESOLVE IT AND CANNOT. What the brake must never do is CREATE
one.** The distinction the scenario measures is precisely: *reality became unknowable* versus *the
brake manufactured an unknown outcome by engaging*.

**A compensation IS an effect and obeys this same table.** A compensation that has CLAIMED runs to
verification. `COMPENSATION_FAILED` stays non-terminal and human-owned — ### **the brake does not, and
cannot, clear it.**

#### 3.5.2 ### **TWO COMPOSED ADMISSION DIMENSIONS, AND GLOBAL IS NOT A FAKE TENANT**

Every effect is decided by **both**: the platform `GLOBAL` brake **and** the acting tenant's
applicable brake(s). ### **An `ACTIVE` result on EITHER denies.**

### **The platform brake is EXACTLY ONE TENANT-EXEMPT ROW** in `platform_brake`, with structural
cardinality `PRIMARY KEY (id) CHECK (id = 1)` and **no `tenant_id` column at all** (entity point 7,
amendment A1). This is landed. **Preserve it.**

### **DO NOT INTRODUCE** `tenant = "default"`, `tenant = "platform"`, any sentinel tenant value, or N
global rows one per tenant. Amendment A1 records the reason so it is not re-litigated: *"which tenant
owns the platform brake" has no honest answer — the row exists **because** it is nobody's tenant
data*, a sentinel would reintroduce exactly the `tenant="default"` defect this repository has guards
against, and an N-row fan-out would create a multi-row atomicity problem **during the incident the
brake exists for**.

**Tenant-owned brakes stay tenant-first `[C-1]`.** Tenant, first in every key and every index. A
cross-tenant read or release must not be spellable.

#### 3.5.3 ### **FAIL CLOSED — the inversion that turns a safety control into decoration**

> ### **"CANNOT READ THE BRAKE" MUST NEVER MEAN "THE BRAKE IS OFF."**

An **absent** platform row, an **unreadable** brake store and an **unparseable scope** are each a
**REFUSAL**: no witness, no mint, no claim, no effect. There is **no allow-on-error default** anywhere
on the path. The seeded `RELEASED`-at-version-0 platform row exists precisely so that *"unreadable"*
and *"released"* are never the same observation — **do not delete the seed to simplify anything.**

### **AND NEVER SILENTLY ACCEPT AN UNKNOWN SCOPE AS "NO BRAKE."** A scope dimension this unit does not
land must be **UNSPELLABLE — raising at the boundary** — never parsed into something narrower and
never into nothing. See `M13-AQ-6`.

#### 3.5.4 ### **VERSION INVALIDATION AND THE RACE THAT MATTERS**

`brake_version` is the concurrency mechanism. Both components are monotonic; witnesses and grants bind
**both**; the claim CAS **re-validates both, inside its own `WHERE` clause**.

> ### **A BRAKE CHANGE BETWEEN MINT AND CLAIM MAKES THE CLAIM CAS MATCH ZERO ROWS. THE ADAPTER DOES
> ### NOTHING. NEVER BOTH, NEVER NEITHER.**

### **THE RACE IS CLOSED BY THE DATABASE, NOT BY A CHECK.** An implementation that reads the brake,
decides, and *then* claims is a TOCTOU window wearing the shape of a check, and it passes every
behavioural test on an unloaded machine. **This is already correct on the landed tree. Do not
regress it.**

**Both asymmetric omissions are payments and both are exercised:** a tenant-only check lets a GLOBAL
brake through; a global-only check lets a TENANT brake through.

ADR-011 §8.3 records why the invalidation is deliberately blunt: **any brake change invalidates ALL
outstanding witnesses and grants for that tenant, even outside the engaged scope.** Scope-precise
invalidation would require reasoning about scope overlap at claim time, ### **and a bug in that
reasoning would let an effect through during a brake.** *A conservative over-invalidation costs a
re-checkpoint. A precise under-invalidation costs a payment.* **Do not optimise this.**

**Authority names 10,000× interleaved testing** (entity 43(b), machine §41(b), ADR-011 §12). Provide a
race battery of that order, or an equivalent **stronger deterministic** proof the existing harness
supports. ### **DO NOT USE SLEEP-BASED TIMING AS THE ONLY EVIDENCE.**

#### 3.5.5 ### **RELEASE REQUIRES EVIDENCE — AND IT IS NOT `if human and decision_ref`**

> ### **THAT IMPLEMENTATION PASSES EVERY AUTHORIZATION TEST IN THIS FILE AND IS STILL WRONG.**

All four are mandatory and all four are recorded on the release (ADR-011 §6, entity 36, machine BR-4):

1. ### **Every in-flight effect at engagement is ACCOUNTED FOR** — each `VERIFIED`, `FAILED`, or
   explicitly acknowledged as `UNKNOWN_OUTCOME` **with a named owner**.
2. ### **No unresolved Sev-0 security event in scope.**
3. ### **Integration health POSITIVELY DEMONSTRATED** — a positive control (ADR-006 §3.4). ### **A
   PAGE THAT LOADED IS NOT A POSITIVE HEALTH PROOF.**
4. A **`decision_ref`.**

> ### **UNRESOLVED `UNKNOWN_OUTCOME`s DO NOT BLOCK RELEASE — BUT THEY MUST BE EXPLICITLY ACKNOWLEDGED
> ### AND OWNED, AND THEIR ENTITIES STAY FROZEN AND THEIR COMMIT KEYS HELD REGARDLESS.**
>
> Blocking release on them would create a perverse incentive to resolve them carelessly in order to
> get the system running. ### **DO NOT SILENTLY TURN THIS INTO A DIFFERENT RELEASE RULE IN EITHER
> DIRECTION** — neither "unknowns block release" nor "release clears them". **The brake's release
> does not release them. Nothing does but a human or a proof.**

### **AND DO NOT INVENT A SECOND APPROVAL WORKFLOW FOR RELEASE.** *"Requiring ceremony to become
safer is a design error"* is explicitly stated by authority, and a release-approval workflow is
exactly the `PENDING_RELEASE` state the two-state set forbids, arriving through the back door.

#### 3.5.6 ### **RELEASE DOES NOT RESTORE OLD AUTHORITY**

> ### **RELEASE MUST NOT REACTIVATE STALE WITNESSES OR GRANTS.** Every Checkpoint Witness and every
> unclaimed Effect Grant minted before the brake engaged is **DEAD, permanently** — the
> `brake_version` moved, and BR-4 moves it again. ### **They are not "resumed."**
>
> *A brake that released a queue of pre-authorized effects into a world that has changed since would
> be worse than no brake at all — it would be a stored-up volley.*

- A stale witness after release is **REFUSED**.
- A stale grant after release is **REFUSED**.
- ### **Every queued consequential action passes a NEW, FULL checkpoint** (M-62) — new policy version,
  new brake version, fresh live re-reads, fresh drift check.
- ### **Release creates NO new Checkpoint Witnesses.**
- Old approvals are subject to M4's **existing** drift and brake rules; most will be `VOID_ON_DRIFT`,
  and **that is correct — the world moved while we were stopped.**

#### 3.5.7 ### **THE M4 INTERACTION — REUSE IT, DO NOT REBUILD IT**

`BrakeEngaged` ⇒ a pending approval remains **RECORDED** — not deleted, not denied — and simply
**cannot authorize execution**. The applicable M4 behaviour is **`VOID_ON_BRAKE`**, and it is
**landed**: `approval.py` already imports `BrakeStore`, already carries the `VOID_ON_BRAKE` state and
already has `AP-5`.

### **M13 DOES NOT REPLACE M4 AND BUILDS NO LOCAL BRAKE-APPROVAL MECHANISM.** Reuse the landed
approval authority and its existing fingerprint/drift semantics. **Edit no part of M4.**

#### 3.5.8 ### **COMPENSATION, OBSERVATION, RECONCILIATION — THE ASYMMETRY IS THE POINT**

| Under an `ACTIVE` brake | |
|---|---|
| **Consequential writes** | ### **BLOCKED** |
| **Compensation** | ### **BLOCKED.** ⚠️ *Uncomfortable and correct: a brake engaged because the system is misbehaving must not permit that same system to start writing "corrections" into the TMS.* An urgent compensation requires a **human to NARROW the brake** — an explicit, recorded, authorized act. |
| **Retries** | ### **BLOCKED** — a retry is a new pipeline needing a new grant, and retrying into a braked system is the last thing you want. |
| **Migration tools** | ### **BLOCKED. There is no admin bypass.** |
| **Agents** | Blocked — and never a threat: a `ProposedIntent` is inert data. |
| **Observation** | ### **CONTINUES** |
| **Reconciliation** | ### **CONTINUES** |
| **Reads** | ### **CONTINUE** |

> ### **A BRAKE IS NOT "SHUT THE WHOLE PROCESS DOWN." IT WITHDRAWS THE AUTHORITY TO PRODUCE
> ### CONSEQUENTIAL EFFECTS. THE BRAKE STOPS ACTING, NOT KNOWING.**
> *Blinding yourself during an incident is the opposite of what you want.*

**Permanent tests must distinguish these**, in both directions. A battery that only proves things are
blocked would pass a product that had simply stopped.

#### 3.5.9 ### **THE ONE-WAY RATCHET, OVER THREE DISTINCT ACTOR CLASSES**

| Actor | May ENGAGE / WIDEN | May NARROW / RELEASE |
|---|---|---|
| **Any authenticated authorized human** | ### ✅ **instantly, no ceremony** | ✅ (subject to §3.5.5) |
| **An automated Sev-0 detector** | ### ✅ **YES** | ### ❌ **NEVER** |
| **Automation generally** | ✅ | ### ❌ **NEVER** |
| ### **A model / an agent** | ### ❌ **NEVER** | ### ❌ **NEVER** |
| **A timer** | ❌ | ### ❌ **NEVER** (BR-5) |
| **A retry handler · a counterparty · inbound content** | ❌ | ❌ |

> ## **AUTOMATION MAY ONLY EVER MOVE AUTHORITY IN THE SAFE DIRECTION.**
> *(Identical in shape to ADR-010 §7's autonomy ratchet. **The same sentence governs both. That is not
> a coincidence — it is the invariant.**)*

### **A MODEL IS NOT A SEV-0 DETECTOR.** It may raise a **signal**; a detector may act on it. ### **DO
### NOT COLLAPSE `system`, `detector` AND `model` INTO ONE ACTOR CLASS** — that collapse is precisely
how a model acquires the brake, and it looks like tidy refactoring while it happens.

### **A DETECTOR MAY NEVER CLEAR ITS OWN ALARM.** *A detector that could clear its own alarm is not a
detector.*

**Unauthorized brake release must reuse the already-registered F14
`UnauthorizedBrakeReleaseAttempted`.** Do not invent an M13-local synonym.

### **AND DO NOT TREAT AN ARBITRARY NON-EMPTY ACTOR STRING AS PROOF OF AN AUTHENTICATED HUMAN.** The
repository has a landed human identity substrate — M1's `tenant_humans` and
`AUTHORITY_ROLES = ("POLICY_OWNER", "AUTHORIZED_HUMAN")`. **Use it. Invent no second human identity
system, no admin role, no superuser and no service account with brake authority.** See `M13-AQ-5` for
the part the corpus does not answer.

#### 3.5.10 ### **IDEMPOTENCY — A FLAPPING DETECTOR OPENS NO WINDOW**

Repeated engagement on an already-braked scope is **idempotent**: ### **one `ACTIVE` brake and a
rising signal count**, and **it cannot self-release, so flapping cannot open a window.**

**Assert this by ROW COUNT against a live database across many repeats, not by return value.** A store
that returns the existing brake while writing a second row looks identical from the caller's side and
is a momentary release window during an incident. The landed store already absorbs duplicate
engagement correctly; ### **the rising signal count is the part that has nowhere to live today.**
**Inspect what is currently true; do not assume.** If the repository's authorities disagree on the
representation, **record the authority question rather than inventing a field.**

#### 3.5.11 ### **REPLAY**

Brake history replays; ### **it never re-engages a real brake** (`GR-11` / `ER-2`). Replay must
produce **zero new real brakes, zero witnesses, zero grants, zero effects and zero authority.** A
replay that re-engaged a brake would stop a healthy system; a replay that minted authority would be a
way to manufacture a grant out of history.

#### 3.5.12 ### **R17 — A HIDDEN BRAKE IS A SILENT DEGRADATION**

> ### **A system that has quietly stopped working is indistinguishable, to an owner, from a system
> ### with nothing to do.**

Whenever a brake is `ACTIVE`, the canonical report states, **unprompted**: the **scope** and ### **what
is STILL ALLOWED**; the **reason** in plain language; the **actor** and whether it was a human or a
**named detector**; the **time engaged**; the **prevented effects**; ### **the in-flight effects at
the moment of engagement and their current status**; ### **the unresolved `UNKNOWN_OUTCOME`s with
dollar exposure**; and ### **the exact requirements for release — NOT "contact an administrator".**

### **DO NOT RESPOND TO THIS BY BUILDING A PRODUCTION DASHBOARD, ADMIN UI, SLACK INTEGRATION OR WEB
### CONSOLE.** M13 ships dark. **Establish the canonical report / runtime representation and the
verification seam only. No production channel.** The channel is P8's.

#### 3.5.13 ### **M13 MUST NOT BECOME A SECOND GATE MINTER**

`checkpoint.py` remains the **sole** gate decision / witness / grant authority. M13 provides brake
state and version facts **consumed by** the checkpoint and the claim CAS. ### **It mints no
independent gate decision, constructs no `GateEntry` and no `GateRegistry`, and builds no second
checkpoint.** **A second gate authority is the same defect as no gate authority.** Keep a **positive
control** in the guard proving it is capable of detecting a second minter.

#### 3.5.14 ## ⚠️ **THE SINGLE BRAKE AUTHORITY — THE DEFECT THIS UNIT IS MOST LIKELY TO SHIP**

> ### **DO NOT CREATE A SECOND BRAKE AUTHORITY.**
>
> Do not create an independent second **BrakeStore**, a second **brake state table representing the
> same truth**, a second **brake decision engine**, a second **checkpoint brake decision**, or a
> second **claim-time brake authority.**

M13 is the only P6 machine whose substrate **already exists**, so the cheapest wrong build is a new
module that re-implements `engage` / `widen` / `narrow` / `release` beside `brake.py`. It would look
like every other P6 machine, pass a great many tests, and leave **two answers to "is Neyma
stopped?"** — which is the same defect as none.

### **M13 COMPLETES, HARDENS AND CANONICALIZES THE LIFECYCLE AROUND THE EXISTING SINGLE BRAKE
### AUTHORITY.** If repository authority genuinely requires a new M13-facing module, ### **it must
### DELEGATE TO / COMPOSE WITH the landed authority rather than create a competing truth.** Editing
`brake.py` to complete it is **correct and expected**; duplicating it is not.

Establish mechanically, and report:

1. **what P3 already guarantees**,
2. **what canonical M13 requires beyond P3**,
3. **where those missing guarantees belong while preserving ONE authority.**

---

### 3.6 ⚠️ THE NINE AUTHORITY QUESTIONS THIS BOOTSTRAP FOUND

**Record them. Build the fail-closed side. Do not resolve them.**

#### **`M13-AQ-1` — P6 owns the MACHINE; P8 owns the RUNTIME. (SETTLED BY CANON — build it.)**

P8's `allowed_scope` contains **`brake`** and its objective says **"the real brake"**; its
`expected_production_outputs` include **"admission-control brake"**. Read alone, that says M13 is
P8's and P6 may not build it.

### **IT DOES NOT, AND THE REGISTRY SAYS SO IN ITS OWN WORDS.** P6's `prohibited_scope` reads:
*"production policy REGISTRATION, EVALUATION RUNTIME and ENABLEMENT (P8/U8.1) —* ### ***NOT machines
M11/M12/M13 themselves, which are three of this unit's own thirteen."*** P6's
`expected_production_outputs` are *"17 platform primitives, 13 machines, 134 transitions"*, and
`BR-1..BR-5` are five of those 134.

### **BUILD THE MACHINE. DO NOT BUILD THE RUNTIME.** Do not refuse to build M13 on the ground that
"the real brake is P8" — that reading would make M13 unbuildable and it is wrong for the reason the
registry states. Equally, do not build P8's enablement, detector wiring or operator channel here.

#### **`M13-AQ-2` — ⚠️ THE ONE AMBIGUOUS WORD, IN BOTH ITS SENSES, IN THE SAME CORPUS.**

`ADR-011 §5.1` records that *"narrow"* is **genuinely ambiguous** and resolves it: throughout that
ADR, ### **"narrow" and "broaden" refer to AUTHORITY — what Neyma is allowed to do — never to brake
scope.** It is in the Semantic Model's ambiguous-words list for this reason.

**And the corpus uses BOTH senses, correctly, in different files.** In the SCOPE sense:

- `ER-12`: *"Automated actors may emit `PolicyRevoked`(narrowing) and `BrakeEngaged`/**`BrakeWidened`**
  only where allowed; never `PolicyActivated`(broaden) or `BrakeReleased`/**`BrakeNarrowed`**."*
- The machine file §15/§40, entity point 35, target spec §21.6 / M-60: **automation may engage and
  WIDEN.**

In the AUTHORITY sense, in the very same repository:

- `events/registry.md` §11, closing line: *"Automated brake engagement/**narrowing** is permitted
  (ER-12); automated release/broadening is never."* — and §11's own table has rows whose action is
  literally *"**narrow autonomy**"*.
- `platform-safety-acceptance.md` `AC-SAFE-027`, whose subject line is *"Automation cannot broaden
  policy or release a brake"*: *"a property test over **every** automated path ⇒ engage/**narrow**
  only."*

> ### **READ IN THE AUTHORITY SENSE, BOTH OF THOSE SAY EXACTLY WHAT ER-12 SAYS. READ IN THE SCOPE
> ### SENSE, BOTH SAY THE OPPOSITE — AND THE OPPOSITE IS AN INVERTED RATCHET IN WHICH AUTOMATION MAY
> ### NARROW A BRAKE.**

### **THIS IS NOT A DEFECT IN THOSE TWO FILES AND YOU MUST NOT "CORRECT" THEM.** It is the recorded
ambiguity, landing where it does the most damage. **Build the fail-closed side, which is the same
under either reading of the safe direction:**

> ### **AUTOMATION MAY ENGAGE A BRAKE AND MAY WIDEN ITS SCOPE. AUTOMATION MAY NEVER NARROW A BRAKE'S
> ### SCOPE AND MAY NEVER RELEASE IT.**

### **AND STATE THE SENSE EVERY TIME YOU USE THE WORD** — in `brake_lifecycle.py`, in the probe case
names, in the test names and in your report. A test called `test_automation_may_narrow` is unreadable
without knowing which sense its author meant, and unreadable is how this inverts.

#### **`M13-AQ-3` — "`brake_version` is GLOBAL per tenant" vs "monotonic per scope-owner".**

Entity point 9 and ADR-011 §4.3/§8.3 say *"monotonic, **GLOBAL per tenant**"*; machine §17 and entity
point 7 say *"per **scope-owner**… witnesses/grants bind **BOTH** `global_brake_version` and
`tenant_brake_version`, both re-validated in the claim CAS."*

These are the same rule at two granularities, not a contradiction — and the landed
`version_token` already implements the composite. **The reading that wins is the machine's**: a
composite token over both owners, both revalidated. Record it; **do not "simplify" to one counter.**

#### **`M13-AQ-4` — the platform row's release CHECK versus its "never engaged" seed.**

Entity point 16 requires ### **`CHECK: state = RELEASED requires a non-null released_by AND
release_decision_ref`**. The landed `brakes` table has exactly that. The landed `platform_brake` row
is **seeded `RELEASED` at version 0 with NULL `released_by`** — which that same CHECK would forbid.
The seed exists on purpose: point 36 requires that *"unreadable"* and *"released"* never be the same
observation, and point 16's amendment A1 says the platform engagement columns *"are null while it has
never been engaged."*

**So the corpus supports two readings**: (a) the platform row needs a distinguishable never-engaged
representation, or (b) the release CHECK is conditional on having been engaged.

### **BUILD THE FAIL-CLOSED SIDE AND RECORD THE QUESTION. DO NOT INVENT A THIRD STATE TO SOLVE IT** —
a `NEVER_ENGAGED` state would violate the two-state rule to fix a nullability question.

#### **`M13-AQ-5` — ⚠️ THE CORPUS DOES NOT ANSWER CROSS-PLATFORM HUMAN IDENTITY.**

Entity point 18 requires `released_by` **FK → an authenticated human with release authority**. M1
landed `tenant_humans`, which is **tenant-first**. ### **The platform brake belongs to no tenant**, so
its releaser cannot foreign-key into `tenant_humans` without pretending the platform row belongs to a
tenant — which amendment A1 exists to forbid.

### **THE REPOSITORY HAS NO PLATFORM-LEVEL HUMAN IDENTITY TABLE, AND THIS TASK DOES NOT AUTHORISE YOU
### TO CREATE A SECOND HUMAN IDENTITY SYSTEM.**

Build the fail-closed side: a **tenant** brake's releaser is a real `tenant_humans` row of that
tenant, enforced by the database; a **platform** release still requires a truthful authenticated
actor and refuses without one; **and the platform row does not acquire a tenant.** Record the gap.

#### **`M13-AQ-6` — ⚠️ THE SCOPE VOCABULARY. INVESTIGATE; DO NOT CHOOSE FROM MEMORY.**

Entity point 12 freezes five dimensions: ### **`GLOBAL`, `TENANT`, `INTEGRATION(target_system)`,
`ACTION_CLASS`, `COUNTERPARTY`**, and ADR-011 §9 says they introduce **no new vocabulary** — each
already exists in the architecture. ADR-011 §5.2's Sev-0 trigger table needs `tenant + integration`
and `tenant + action_class + counterparty`.

But `brake.py`'s own comment says: *"Wider vocabularies (counterparty, integration) arrive with the
policy runtime (P8); the grammar is closed here so an unparseable scope cannot silently scope to
nothing."* And P8's `allowed_scope` contains `brake`.

**You must read** the M13 entity, the M13 machine, the target spec, ADR-011, P6's `prohibited_scope`,
the P8 unit block, and `brake.py`'s comments and runtime — **and classify the result as:**

- **A.** M13 must own the complete frozen scope vocabulary now;
- **B.** P6/M13 owns only the canonical admission substrate and some scope families are explicitly
  deferred **without weakening any current guarantee**;
- **C.** a genuine authority conflict.

**The bootstrap's own reading was B**, on the registry evidence in `M13-AQ-1` plus the observation
that an unspellable scope forces a **wider** brake, which is the safe direction. ### **THAT IS A
### READING, NOT A RULING. Re-derive it.** If you reach **C**, record it as an M13 authority question
and **build the fail-closed side into the runtime and the tests.**

### **WHATEVER YOU CONCLUDE: THE LANDED AND DEFERRED DIMENSIONS MUST PARTITION THE CANONICAL FIVE,
### EVERY DEFERRED DIMENSION MUST CARRY A RECORDED REASON, AND AN UNKNOWN SCOPE MUST REFUSE. NEVER
### SILENTLY ACCEPT AN UNKNOWN SCOPE AS "NO BRAKE."**

#### **`M13-AQ-7` — does BR-5 emit `IllegalTransitionAttempted`?**

ADR-011 §4 and target spec §12.13 both show BR-5's Emits cell as `IllegalTransitionAttempted`. But
`TRANSITION-EVENT-AUDIT.yaml` classifies `13-brake:BR-5` as **`NON_PRODUCING` /
`GR1_ILLEGAL_REFUSAL`**, proof *"no To state; Writes = em-dash"*.

The audit's own adjudication of the **structurally identical** `PL-15x` and `IB-5x` rows settles it:
inline naming is ***"REDUNDANT DOCUMENTATION, NOT A DIFFERENT CONTRACT"***, and
`IllegalTransitionAttempted`'s declared producer is **the RULE `GR-1`, not a transition**, so there is
no owner row to co-commit with.

### **BR-5 PERSISTS NOTHING AND M13 MINTS NO FIFTH F13 CONTRACT.** Record it.

#### **`M13-AQ-8` — the platform row is REUSED across incidents.**

Entity point 27 says *"a new incident is a new brake"*; point 29 says retention is **permanent**;
point 30 makes the engage/widen/narrow/release events the incident timeline. For **tenant** brakes
each engagement is a new row, so this holds. For the **platform** row, `_engage_platform` re-engages
the same row and **clears `released_by` / `release_decision_ref` / `released_at`**, erasing the
previous incident's release record.

**The asymmetry is real and the corpus does not name it.** ### **This is one reason M13's event
emission is load-bearing:** the F13 stream is the retention mechanism the corpus actually names.
Record the question; build the fail-closed side; **do not add a third state or a second platform
table to solve it.**

#### **`M13-AQ-9` — `release_blockers` is named in a docstring and exists nowhere.**

`brake.py`'s `release()` docstring says release preconditions *"are read against the ledger by the
caller at P3-proportionate depth in `release_blockers`"*. ### **There is no `release_blockers`
anywhere in `src/`, `eval/` or `scripts/`.** The docstring describes a seam that was never built.

This is not an authority conflict — it is a **landed-code defect that names exactly the M13 delta**.
### **DO NOT TREAT THE DOCSTRING AS EVIDENCE THE SEAM EXISTS.** Report it, and either build the seam
as part of BR-4's evidence or correct the docstring — and say which.

---

### 3.7 The existing P6 residuals — read them, do not adopt them

`CURRENT.md` records **`P6-D82`…`P6-D88`** at the M12 landing. ### **READ THEM. DO NOT
OPPORTUNISTICALLY FIX THEM.**

### **DO NOT LET "M13 IS THE LAST MACHINE" BECOME A REASON TO BROADEN THIS UNIT INTO P6 CLEANUP.**
That is the single most predictable scope failure available to this session. A debt row is a complete
deliverable; **do not open a remediation campaign against any `P6-D*`.**

If one is **genuinely a prerequisite to M13's own safety guarantee**: ### **record why and STOP for a
scope decision**, unless canonical authority directly assigns it to M13.

### **`P6-D71` / `PolicyOverridden` REMAINS OPEN unless repository authority explicitly changes it.
### DO NOT INVENT `PolicyOverridden`.** `P6-D83` records that it did **not** close at M12, as the
`P6-CP-11` block had expected. Minting a contract is a founder/architect act, not a build session's.

**Also do not absorb** `P6-D4`, `P6-D73`, `P6-D84` or any other carried residual into M13 merely
because they are nearby.

---

## 4. What you must produce

```
src/freight_recon/brake_lifecycle.py            the M13 machine surface — COMPOSES over brake.py
src/freight_recon/brake.py                      EDITED: complete the ONE landed authority
src/freight_recon/migrations/phase6_brakes.py   HARDENS the landed brake tables; readiness oracle
src/freight_recon/schema.py                     EDITED: wire the migration in, P6BR_* symbols
eval/tests/test_phase6_brake.py                 the acceptance battery
scripts/probe_phase6_brake.py                   the deterministic probe
scripts/mutate_phase6_brake.py                  the mutation battery
```

> ### ⚠️ **`brake.py` IS ON THAT LIST AS AN EDIT, AND `brake_lifecycle.py` IS NOT A SECOND STORE.**
> Every brake state write goes through `BrakeStore`. `brake_lifecycle.py` declares the machine —
> the transition table, the actor ratchet, the scope grammar, the release-evidence contract, the R17
> report shape — and **delegates every mutation to the one authority.** If your `brake_lifecycle.py`
> contains `INSERT INTO brakes` or `UPDATE platform_brake`, **you have built the defect.**
>
> ### **AND IF YOU CONCLUDE THAT REPOSITORY AUTHORITY REQUIRES A DIFFERENT FILE SET, SAY SO AND SAY
> ### WHY BEFORE BUILDING IT.** These names are what `CURRENT.md` and the registry use to record
> M13's absence; they are a strong default, not a ruling that outranks the repository.

### **NAME THE MIGRATION'S SYMBOLS THE WAY EVERY LANDED P6 MIGRATION NAMES THEIRS.** Follow the
`P6XX_*` convention exactly: `MIGRATION_ID`, `P6BR_SCHEMA_VERSION`, `P6BR_TENANT_TABLES`,
`P6BR_EXEMPT_TABLES`, `P6BR_TARGET_SCHEMA`, `P6BR_INDEXES`, `P6BR_TRIGGERS`,
`create_phase6_brakes_schema`, `stamp_phase6_brakes_version`, `phase6_brakes_readiness_problems`.
**Marker-last, like every phase.** Declare `P6BR_EXEMPT_TABLES = ("platform_brake",)` **explicitly**,
so the one defended exemption stays defended and a future addition has to argue for itself.

### **THE MIGRATION HARDENS TABLES P3 CREATED, WHICH IS THE MIGRATION SHAPE MOST LIKELY TO DRIFT:** a
fresh database gets the new DDL inline while an existing one gets it through an ALTER path, and the
two diverge silently. The scenario asserts the upgraded and fresh brake layers are **identical**, that
a second application is a **no-op**, and that **P3's own readiness still holds afterwards.**

**`brake_lifecycle.py` must expose**, because the permanent scenario reads them:

```
BRAKE_STATES  TERMINAL_STATES  ACTOR_KINDS  PRODUCED_CONTRACTS  SAFE_DIRECTION_RULE
TRANSITIONS                     five rows, ids BR-1 BR-2 BR-3 BR-4 BR-5, with BR-5 carrying
                                to_state None, empty writes, no event, and a
                                non_producing_reason of GR1_ILLEGAL_REFUSAL
automation_may()  model_may()  permitted_transitions(actor_kind)
CANONICAL_SCOPE_DIMENSIONS  LANDED_SCOPE_DIMENSIONS  DEFERRED_SCOPE_DIMENSIONS
SCOPE_DEFERRAL_REASONS      parse_scope()             unknown_scope_denies()
RELEASE_EVIDENCE  release_evidence_satisfied()  is_positive_health_proof()
unknown_outcomes_block_release()   UNKNOWN_OUTCOME_RELEASE_OBLIGATIONS
brake_report_fields()  reports_unprompted_when_active()
```

### The probe's interface

```
scripts/probe_phase6_brake.py
    --list-cases        the case names, one per line, kebab-case
    --list-dimensions   every mutation-axis token, one per line
    --case <name>       run exactly one case
    --all               run every case
    --concurrency N     --repeat N     --tenants N     --seed N     --delay-ms N
    --inject <fault>    the closed fault set; an unknown fault exits 2
    --actor <kind>      human | detector | model | automation | timer | retry | counterparty | all
    --position <n>      1 | 2 | 3 | 4 | 5 | all              ← THIS UNIT'S OWN AXIS
    --owner <o>         platform | tenant | both              ← ITS SECOND AXIS
    --transition <id>   BR-1 … BR-5, or all
    --scope <s>         one of the landed scope forms, or all
```

### **`--position` AND `--owner` ARE THIS UNIT'S OWN TWO AXES**, the way `--kind` and `--outcome`
were M12's.

- ### **`--position` walks the five-position in-flight boundary.** Without it a battery only ever
  exercises the half of the brake that STOPS things — and ### **the half that must NOT stop things is
  where the unknown outcome is made.** A machine that stops everything passes a position-blind
  battery and is a different broken product.
- ### **`--owner` varies the two composed admission dimensions.** Without it, a composite version
  check is indistinguishable from a single one, and both asymmetric omissions are payments.

**Every case must be deterministic, hermetic and free of wall-clock sleeps**, and `--all` must run end
to end well under its scenario timeout.

### The case vocabulary — all 188 of them, exactly as spelled here

`--list-cases` must print every one of these, one per line, and `--case <name>` must run it.
### **THE SPELLING IS THE CONTRACT.** A case the scenario asks for and the probe does not implement
is a run that fails as a product defect for a naming reason, and a case the probe implements under a
different name is coverage nobody can cite.

```
any-authenticated-human-engages-instantly
engagement-needs-no-approval-and-no-ceremony
the-brake-engages-with-the-policy-engine-down
the-brake-engages-with-the-tms-down
the-brake-engages-with-the-rule-store-down
engagement-is-a-single-atomic-row-write
engagement-records-scope-actor-and-reason
an-empty-reason-is-not-a-reason
an-empty-actor-is-not-an-actor
a-named-sev-0-detector-may-engage
a-model-may-never-engage
a-model-cannot-masquerade-as-a-detector
a-model-may-raise-a-signal-a-detector-may-act-on-it
a-counterparty-may-never-engage
inbound-content-may-never-engage
engagement-bumps-the-owners-brake-version
the-orphan-adapter-signal-engages-tenant-and-action-class
the-tenant-isolation-signal-engages-globally
br-1-emits-brakeengaged
brakeengaged-proves-admission-is-withdrawn
brakeengaged-does-not-prove-in-flight-work-was-killed
widening-a-brake-narrows-authority
automation-may-widen
a-human-may-widen
a-model-may-never-widen
widening-bumps-the-brake-version
widening-never-releases-anything
br-2-emits-brakewidened
narrowing-a-brake-broadens-authority
only-an-authenticated-human-narrows
automation-may-never-narrow
a-detector-may-never-narrow
a-model-may-never-narrow
a-timer-may-never-narrow
narrowing-bumps-the-brake-version
narrowing-is-not-a-partial-release-state
br-3-emits-brakenarrowed
release-requires-an-authenticated-human
automation-may-never-release
a-detector-may-never-release-its-own-alarm
a-model-may-never-release
a-timer-may-never-release
a-retry-handler-may-never-release
a-counterparty-may-never-release
release-requires-a-decision-ref
release-requires-every-in-flight-effect-accounted-for
an-unaccounted-in-flight-effect-blocks-release
release-requires-no-unresolved-sev-0
an-unresolved-sev-0-blocks-release
release-requires-positively-demonstrated-integration-health
a-page-that-loaded-is-not-a-positive-health-proof
release-is-not-a-human-and-a-decision-ref-alone
an-arbitrary-actor-string-is-not-an-authenticated-human
unresolved-unknown-outcomes-do-not-block-release
an-unresolved-unknown-outcome-must-be-acknowledged-and-owned
an-unresolved-unknown-outcome-stays-frozen-after-release
release-invents-no-second-approval-workflow
release-requires-no-ceremony-to-become-safer
release-bumps-the-brake-version
released-is-terminal
an-unauthorized-release-emits-the-registered-f14-security-event
m13-mints-no-second-unauthorized-release-contract
br-4-emits-brakereleased
a-timer-cannot-release-a-brake
a-timer-cannot-narrow-a-brake
br-5-writes-nothing-and-produces-no-event
there-is-no-ttl-column
there-is-no-expiry-state
there-is-no-auto-release-path
advancing-the-clock-arbitrarily-does-not-move-a-brake
no-timer-may-ever-make-a-brake-less-restrictive
there-are-exactly-two-states
released-is-the-only-terminal-state
a-third-brake-state-is-not-insertable
pending-release-is-forbidden-ceremony
human-engaged-versus-detector-engaged-is-a-field
partially-released-is-a-scope-change
an-active-brake-refuses-to-mint
an-active-brake-refuses-to-claim
an-active-brake-never-kills-a-worker
the-brake-stops-the-next-effect-not-the-last
an-unclaimed-grant-becomes-unclaimable
a-claimed-grant-runs-to-verification
an-executing-adapter-call-is-not-interrupted
engaging-during-an-adapter-call-creates-no-unknown-outcome
verification-in-progress-is-a-read-and-continues
a-brake-manufactures-no-unknown-outcome-of-its-own
a-retry-into-a-braked-system-is-blocked
a-migration-tool-has-no-admin-bypass
an-agent-proposal-is-inert-under-a-brake
the-platform-brake-is-exactly-one-row
the-platform-row-has-no-tenant-column
global-is-not-a-fake-tenant
there-is-no-sentinel-tenant-for-the-platform
a-global-brake-denies-every-tenant-without-fan-out
an-active-brake-in-either-dimension-denies
tenant-brakes-stay-tenant-first
a-tenant-a-brake-is-not-a-tenant-b-brake
a-cross-tenant-brake-read-is-refused
a-cross-tenant-release-is-refused
the-widest-applicable-brake-is-the-one-reported
an-absent-platform-row-refuses-the-mint
an-absent-platform-row-refuses-the-claim
an-unreadable-brake-store-refuses-the-mint
cannot-read-the-brake-never-means-off
an-unknown-scope-is-never-treated-as-no-brake
there-is-no-allow-on-brake-error-default
the-platform-brake-version-is-monotonic
the-tenant-brake-version-is-monotonic
a-brake-version-never-goes-backwards
witnesses-bind-both-effective-components
grants-bind-both-effective-components
the-claim-cas-revalidates-both-components
a-tenant-only-version-check-lets-a-global-brake-through
a-global-only-version-check-lets-a-tenant-brake-through
a-brake-between-mint-and-claim-matches-zero-rows
the-mint-claim-race-is-never-both-never-neither
the-interleaved-race-battery-runs-at-canonical-order
no-external-effect-occurs-when-the-brake-wins
the-race-is-decided-by-the-database-not-by-a-check
release-does-not-resurrect-a-stale-witness
release-does-not-resurrect-a-stale-grant
a-stale-witness-after-release-is-refused
a-stale-grant-after-release-is-refused
every-queued-action-passes-a-new-full-checkpoint
release-mints-no-checkpoint-witness
a-pending-approval-remains-recorded-under-a-brake
a-pending-approval-cannot-authorize-execution-under-a-brake
brakeengaged-voids-an-approval-on-brake
m13-reuses-m4s-landed-approval-authority
m13-builds-no-local-brake-approval-mechanism
an-old-approval-after-release-is-subject-to-m4-drift
compensation-is-blocked-under-an-active-brake
a-compensation-that-already-claimed-runs-to-verification
compensation-failed-is-not-cleared-by-the-brake
observation-continues-under-an-active-brake
reconciliation-continues-under-an-active-brake
the-brake-stops-acting-not-knowing
automation-may-engage-and-widen-only
automation-may-never-narrow-or-release
a-model-is-not-a-sev-0-detector
system-detector-and-model-are-three-actor-classes
the-safe-direction-rule-holds-over-every-automated-path
repeated-engagement-on-one-scope-is-idempotent
a-flapping-detector-creates-one-active-brake
a-flapping-detector-opens-no-release-window
the-signal-count-rises-on-repeated-engagement
one-active-brake-per-tenant-and-scope
the-four-f13-contracts-and-no-fifth
brakeexpired-is-not-a-contract
brakeautoreleased-is-not-a-contract
brakependingrelease-is-not-a-contract
f13-is-strict-per-aggregate
the-f13-envelope-carries-the-required-order-fields
m13-mints-no-unregistered-event
m13-mints-no-second-f14-contract
replay-reconstructs-brake-history
replay-never-engages-a-live-brake
replay-mints-no-witness
replay-mints-no-grant
replay-produces-no-external-effect
replay-creates-no-authority
an-active-brake-is-reported-unprompted
the-report-names-what-is-still-allowed
the-report-names-the-reason-and-the-actor
the-report-distinguishes-a-human-from-a-named-detector
the-report-names-prevented-effects
the-report-names-in-flight-effects-and-their-status
the-report-names-unresolved-unknown-outcomes-and-exposure
the-report-names-the-exact-release-requirements
a-hidden-brake-is-a-silent-degradation
there-is-exactly-one-brake-authority
m13-builds-no-second-brake-store
m13-builds-no-second-brake-state-table
m13-builds-no-claim-time-brake-authority
m13-mints-no-gate-decision
checkpoint-py-remains-the-sole-gate-minter
m13-builds-no-second-checkpoint
a-brake-row-is-never-deleted
the-incident-record-is-retained-permanently
a-new-incident-is-a-new-brake
m13-ships-dark-with-zero-production-importers
no-brake-console-or-dashboard-exists
no-channel-brake-command-exists
no-production-detector-wiring-exists
nothing-graduates
landing-m13-is-not-p6-acceptance
m1-through-m12-are-unchanged
```

### The mutation axis vocabulary

`--list-dimensions` must print every one of these:

```
position:1-not-yet-executing
position:2-granted-unclaimed
position:3-claimed-not-yet-called
position:4-adapter-called-response-pending
position:5-verification-in-progress
owner:platform
owner:tenant
owner:both
actor:human
actor:detector
actor:model
actor:automation
actor:timer
actor:retry
actor:counterparty
transition:BR-1
transition:BR-2
transition:BR-3
transition:BR-4
transition:BR-5
inject:brake-store-unreadable
inject:platform-row-absent
inject:policy-engine-down
inject:tms-down
inject:rule-store-down
inject:brake-between-mint-and-claim
```

### The probe's output contract

Every case prints a `### MISS ###` marker on failure and a positive line on success — the shared
harness vocabulary every P6 probe carries, alongside `### NOT REFUSED`, `### WRONGLY REFUSED` and
`### WRONG REFUSAL`. The scenario forbids all four globally, so a case that silently does nothing
cannot read as a case that passed.

The narrative run must print `behaviours as specified, 0 wrong` on success, and **exactly these
headlines**, each emitted by the case that actually establishes it:

```
A BRAKE REFUSES TO MINT AND REFUSES TO CLAIM
A BRAKE NEVER KILLS A WORKER
THE BRAKE STOPS THE NEXT EFFECT, NOT THE LAST
KILLING A WORKER WOULD MANUFACTURE AN UNKNOWN OUTCOME
ENGAGING DURING AN ADAPTER CALL CREATES NO UNKNOWN OUTCOME
AN UNCLAIMED GRANT BECOMES UNCLAIMABLE
A CLAIMED GRANT RUNS TO VERIFICATION
VERIFICATION IS A READ, AND THE BRAKE DOES NOT STOP A READ
ANY AUTHENTICATED HUMAN ENGAGES INSTANTLY, WITH NO CEREMONY
ENGAGEMENT IS ONE ATOMIC ROW WRITE
THE BRAKE ENGAGES WITH THE POLICY ENGINE AND THE TMS DOWN
A SAFETY CONTROL THAT REQUIRES A HEALTHY SYSTEM IS NOT A SAFETY CONTROL
WIDENING A BRAKE NARROWS AUTHORITY
NARROWING A BRAKE BROADENS AUTHORITY
AUTOMATION MAY ENGAGE AND WIDEN
AUTOMATION MAY NEVER NARROW OR RELEASE
A DETECTOR MAY NEVER CLEAR ITS OWN ALARM
A MODEL IS NOT A SEV-0 DETECTOR
A MODEL MAY NEVER ENGAGE, NARROW OR RELEASE
RELEASE REQUIRES POSITIVE EVIDENCE, NOT A DECISION REF ALONE
A PAGE LOADING IS NOT A POSITIVE HEALTH PROOF
EVERY IN-FLIGHT EFFECT MUST BE ACCOUNTED FOR BEFORE RELEASE
UNRESOLVED UNKNOWN OUTCOMES DO NOT BLOCK RELEASE, AND STAY FROZEN AND OWNED
THE BRAKE RELEASES NOTHING BUT ITSELF
REQUIRING CEREMONY TO BECOME SAFER IS A DESIGN ERROR
AN UNAUTHORIZED RELEASE REACHES THE REGISTERED F14 EVENT
M13 MINTS NO SECOND UNAUTHORIZED-RELEASE CONTRACT
A BRAKE NEVER EXPIRES
NO TIMER MOVES A BRAKE
THE CLOCK MAY NEVER MAKE A BRAKE LESS RESTRICTIVE
BR-5 IS ILLEGAL AND NON-PRODUCING
THE PLATFORM BRAKE IS ONE TENANT-EXEMPT ROW
GLOBAL IS NOT A FAKE TENANT
AN ACTIVE BRAKE IN EITHER DIMENSION DENIES
A TENANT BRAKE IS TENANT-FIRST AND NEVER GLOBAL
CANNOT READ THE BRAKE NEVER MEANS OFF
THERE IS NO ALLOW-ON-BRAKE-ERROR DEFAULT
AN ABSENT BRAKE ROW IS A REFUSAL, NEVER A RELEASED BRAKE
AN UNKNOWN SCOPE IS A REFUSAL, NEVER AN ABSENT BRAKE
A BRAKE BETWEEN MINT AND CLAIM MAKES THE CAS MATCH ZERO ROWS
NEVER BOTH, NEVER NEITHER
THE RACE IS DECIDED BY THE DATABASE, NOT BY A CHECK
THE CLAIM CAS REVALIDATES BOTH BRAKE VERSIONS
RELEASE DOES NOT RESURRECT A STALE WITNESS
RELEASE DOES NOT RESURRECT A STALE GRANT
EVERY QUEUED ACTION PASSES A NEW FULL CHECKPOINT AFTER RELEASE
RELEASE MINTS NO CHECKPOINT WITNESS
A PENDING APPROVAL STAYS RECORDED AND CANNOT EXECUTE
M13 REUSES M4 AND BUILDS NO LOCAL APPROVAL MECHANISM
COMPENSATION IS BLOCKED UNDER AN ACTIVE BRAKE
A COMPENSATION IS AN EFFECT AND OBEYS THE SAME BOUNDARY
OBSERVATION AND RECONCILIATION CONTINUE
THE BRAKE STOPS ACTING, NOT KNOWING
A FLAPPING DETECTOR IS ONE ACTIVE BRAKE AND NO WINDOW
FOUR F13 CONTRACTS AND NO FIFTH
F13 IS STRICT PER AGGREGATE
REPLAY RECONSTRUCTS HISTORY AND CREATES NO AUTHORITY
REPLAY NEVER ENGAGES A LIVE BRAKE
A HIDDEN BRAKE IS A SILENT DEGRADATION
AN ACTIVE BRAKE IS REPORTED UNPROMPTED
THE REPORT NAMES WHAT IS STILL ALLOWED
THE REPORT NAMES THE EXACT RELEASE REQUIREMENTS
THERE IS EXACTLY ONE BRAKE AUTHORITY
M13 BUILDS NO SECOND BRAKE STORE
M13 BUILDS NO SECOND BRAKE STATE TABLE
M13 MINTS NO GATE DECISION
THE CHECKPOINT IS STILL THE ONLY GATE MINTER
M13 SHIPS DARK WITH ZERO PRODUCTION IMPORTERS
NO BRAKE CONSOLE, DASHBOARD OR CHANNEL COMMAND EXISTS
NOTHING GRADUATES
LANDING M13 IS NOT P6 ACCEPTANCE
THE M1 WORK ITEM MACHINE IS UNCHANGED
THE M2 PIPELINE MACHINE IS UNCHANGED
THE M3 EFFECT AUTHORITY IS UNCHANGED
THE M4 APPROVAL MACHINE IS UNCHANGED
THE M7 CONFLICT MACHINE IS UNCHANGED
THE M9 EXCEPTION MACHINE IS UNCHANGED
THE M11 POLICY MACHINE IS UNCHANGED
THE M12 RULE MACHINE IS UNCHANGED
```

**And these alarm markers, printed only when the named defect actually occurred.** A marker the probe
can never emit is decoration; a marker it emits on a correct product is worse.

```
### MISS ###                                                      ### A MODEL ENGAGED A BRAKE ###
### A MODEL MASQUERADED AS A DETECTOR ###                         ### A COUNTERPARTY ENGAGED A BRAKE ###
### INBOUND CONTENT ENGAGED A BRAKE ###                           ### AN UNAUTHENTICATED ACTOR ENGAGED A BRAKE ###
### ENGAGEMENT REQUIRED AN APPROVAL ###                           ### ENGAGEMENT REQUIRED A HEALTHY SYSTEM ###
### ENGAGEMENT REQUIRED THE POLICY ENGINE ###                     ### ENGAGEMENT REQUIRED THE TMS ###
### ENGAGEMENT WAS NOT A SINGLE ROW WRITE ###                     ### ENGAGEMENT RECORDED NO REASON ###
### ENGAGEMENT RECORDED NO ACTOR ###                              ### BrakeEngaged TREATED AS A KILL ORDER ###
### AUTOMATION RELEASED A BRAKE ###                               ### AUTOMATION NARROWED A BRAKE ###
### A DETECTOR RELEASED A BRAKE ###                               ### A DETECTOR NARROWED A BRAKE ###
### A DETECTOR CLEARED ITS OWN ALARM ###                          ### A MODEL RELEASED A BRAKE ###
### A MODEL NARROWED A BRAKE ###                                  ### A MODEL WIDENED A BRAKE ###
### A TIMER RELEASED A BRAKE ###                                  ### A RETRY HANDLER RELEASED A BRAKE ###
### A COUNTERPARTY RELEASED A BRAKE ###                           ### A SERVICE ACCOUNT RELEASED A BRAKE ###
### SYSTEM DETECTOR AND MODEL COLLAPSED INTO ONE ACTOR CLASS ###  ### THE SAFE DIRECTION WAS INVERTED ###
### AUTHORITY WAS BROADENED WITHOUT A HUMAN ###                   ### RELEASED WITHOUT AN AUTHENTICATED HUMAN ###
### RELEASED WITHOUT A DECISION REF ###                           ### RELEASED WITH AN UNACCOUNTED IN-FLIGHT EFFECT ###
### RELEASED WITH AN UNRESOLVED SEV-0 ###                         ### RELEASED WITHOUT POSITIVE INTEGRATION HEALTH ###
### A LOADED PAGE ACCEPTED AS A HEALTH PROOF ###                  ### RELEASE REDUCED TO A HUMAN AND A DECISION REF ###
### AN ARBITRARY ACTOR STRING ACCEPTED AS A HUMAN ###             ### A SECOND HUMAN IDENTITY SYSTEM INVENTED ###
### A RELEASE APPROVAL WORKFLOW BUILT ###                         ### CEREMONY REQUIRED TO BECOME SAFER ###
### AN UNRESOLVED UNKNOWN OUTCOME BLOCKED RELEASE ###             ### AN UNRESOLVED UNKNOWN OUTCOME WENT UNACKNOWLEDGED ###
### AN UNRESOLVED UNKNOWN OUTCOME WENT UNOWNED ###                ### THE BRAKE RESOLVED AN UNKNOWN OUTCOME ###
### THE BRAKE UNFROZE AN ENTITY ###                               ### THE BRAKE RELEASED A COMMIT KEY ###
### THE BRAKE CLEARED COMPENSATION_FAILED ###                     ### UNAUTHORIZED RELEASE WENT UNRECORDED ###
### SECOND UNAUTHORIZED-RELEASE CONTRACT MINTED ###               ### A TIMER MOVED A BRAKE ###
### A BRAKE EXPIRED ###                                           ### A BRAKE AUTO-RELEASED ###
### A TTL WAS INTRODUCED ###                                      ### THE CLOCK MADE A BRAKE LESS RESTRICTIVE ###
### BR-5 WROTE STATE ###                                          ### BR-5 PRODUCED AN EVENT ###
### A THIRD BRAKE STATE APPEARED ###                              ### PENDING_RELEASE APPEARED ###
### ENGAGING APPEARED ###                                         ### DISENGAGED APPEARED ###
### EXPIRED APPEARED ###                                          ### PARTIALLY RELEASED MODELLED AS A STATE ###
### ACTOR KIND MODELLED AS A STATE ###                            ### RELEASED REOPENED ###
### THE BRAKE KILLED A WORKER ###                                 ### THE BRAKE TERMINATED A PROCESS ###
### THE BRAKE INTERRUPTED AN ADAPTER CALL ###                     ### THE BRAKE MANUFACTURED AN UNKNOWN OUTCOME ###
### A CLAIMED EFFECT WAS ABANDONED ###                            ### A VERIFYING EFFECT WAS STOPPED ###
### VERIFICATION WAS TREATED AS AN EFFECT ###                     ### A WITNESS WAS MINTED UNDER AN ACTIVE BRAKE ###
### A GRANT WAS MINTED UNDER AN ACTIVE BRAKE ###                  ### A GRANT WAS CLAIMED UNDER AN ACTIVE BRAKE ###
### AN EFFECT REACHED THE ADAPTER UNDER AN ACTIVE BRAKE ###       ### A RETRY WAS ADMITTED UNDER AN ACTIVE BRAKE ###
### A MIGRATION TOOL BYPASSED THE BRAKE ###                       ### AN ADMIN BYPASS WAS BUILT ###
### GLOBAL REPRESENTED AS A FAKE TENANT ###                       ### A SENTINEL TENANT WAS INTRODUCED ###
### THE PLATFORM ROW ACQUIRED A TENANT ###                        ### MULTIPLE PLATFORM ROWS ALLOWED ###
### GLOBAL FANNED OUT TO N TENANT ROWS ###                        ### A GLOBAL BRAKE FAILED TO DENY A TENANT ###
### TENANT MISSING FROM THE BRAKE PRIMARY KEY ###                 ### CROSS-TENANT BRAKE READ ACCEPTED ###
### CROSS-TENANT RELEASE ACCEPTED ###                             ### GLOBAL UNIQUENESS COUPLED TWO TENANTS ###
### AN UNREADABLE BRAKE STORE READ AS OFF ###                     ### AN ABSENT BRAKE ROW READ AS RELEASED ###
### AN UNKNOWN SCOPE READ AS NO BRAKE ###                         ### ALLOW ON BRAKE ERROR ###
### A SCOPE SILENTLY NARROWED ###                                 ### AN UNPARSEABLE SCOPE SCOPED TO NOTHING ###
### THE CLAIM CAS STOPPED CHECKING THE BRAKE VERSION ###          ### ONLY THE TENANT VERSION WAS CHECKED ###
### ONLY THE GLOBAL VERSION WAS CHECKED ###                       ### THE RACE RESOLVED TO BOTH ###
### THE RACE RESOLVED TO NEITHER ###                              ### AN EXTERNAL EFFECT OCCURRED WHILE THE BRAKE WON ###
### THE RACE WAS DECIDED BY A CHECK RATHER THAN THE DATABASE ###  ### A BRAKE VERSION WENT BACKWARDS ###
### A BRAKE VERSION WAS REUSED ###                                ### AN EVENT DID NOT BUMP THE VERSION ###
### RELEASE RESURRECTED A STALE WITNESS ###                       ### RELEASE RESURRECTED A STALE GRANT ###
### RELEASE RESTORED THE PRE-BRAKE VERSION ###                    ### QUEUED WORK SKIPPED THE NEW CHECKPOINT ###
### RELEASE MINTED A WITNESS ###                                  ### A STORED-UP VOLLEY WAS RELEASED ###
### AN OLD APPROVAL EXECUTED AFTER RELEASE ###                    ### A LOCAL BRAKE APPROVAL MECHANISM WAS BUILT ###
### A PENDING APPROVAL WAS DELETED UNDER A BRAKE ###              ### A PENDING APPROVAL EXECUTED UNDER A BRAKE ###
### VOID_ON_BRAKE SEMANTICS MODIFIED ###                          ### COMPENSATION WROTE UNDER AN ACTIVE BRAKE ###
### OBSERVATION WAS BLOCKED BY A BRAKE ###                        ### RECONCILIATION WAS BLOCKED BY A BRAKE ###
### A READ WAS BLOCKED BY A BRAKE ###                             ### FLAPPING CREATED MULTIPLE ACTIVE BRAKES ###
### FLAPPING OPENED A RELEASE WINDOW ###                          ### A REPEAT ENGAGEMENT BUMPED THE VERSION ###
### THE SIGNAL COUNT DID NOT RISE ###                             ### A FIFTH F13 CONTRACT MINTED ###
### BrakeExpired MINTED ###                                       ### BrakeAutoReleased MINTED ###
### BrakePendingRelease MINTED ###                                ### AN UNREGISTERED EVENT MINTED ###
### F13 STRICT ORDERING VIOLATED ###                              ### REQUIRED PAYLOAD FIELD DROPPED ###
### EVENT WITHOUT ITS STATE ###                                   ### STATE WITHOUT ITS EVENT ###
### REPLAY ENGAGED A LIVE BRAKE ###                               ### REPLAY MINTED AUTHORITY ###
### REPLAY MINTED A WITNESS ###                                   ### REPLAY MINTED A GRANT ###
### REPLAY PRODUCED AN EXTERNAL EFFECT ###                        ### AN ACTIVE BRAKE WAS HIDDEN ###
### THE REPORT DID NOT SAY WHAT IS STILL ALLOWED ###              ### THE REPORT OMITTED THE IN-FLIGHT EFFECTS ###
### THE REPORT OMITTED THE UNKNOWN OUTCOMES ###                   ### THE REPORT OMITTED THE RELEASE REQUIREMENTS ###
### THE REPORT SAID CONTACT AN ADMINISTRATOR ###                  ### A SECOND BRAKE AUTHORITY WAS BUILT ###
### A SECOND BrakeStore WAS BUILT ###                             ### A SECOND BRAKE STATE TABLE WAS BUILT ###
### A SECOND BRAKE DECISION ENGINE WAS BUILT ###                  ### A SECOND CHECKPOINT BRAKE DECISION WAS BUILT ###
### A CLAIM-TIME BRAKE AUTHORITY WAS BUILT ###                    ### brake.py WAS DUPLICATED ###
### M13 MINTED A GATE DECISION ###                                ### M13 REGISTERED A GATE ###
### SECOND GATE MINTER BUILT ###                                  ### SECOND CHECKPOINT BUILT ###
### CHECKPOINT STEP 7 BYPASSED ###                                ### BRAKE CONSOLE BUILT ###
### BRAKE ADMIN UI BUILT ###                                      ### BRAKE DASHBOARD BUILT ###
### SLACK BRAKE COMMAND BUILT ###                                 ### EMAIL BRAKE COMMAND BUILT ###
### SMS BRAKE COMMAND BUILT ###                                   ### VOICE BRAKE COMMAND BUILT ###
### PRODUCTION DETECTOR WIRING BUILT ###                          ### CHANNEL JOINED ###
### NOTIFIER WIRED ###                                            ### TIMER SERVICE IMPORTED ###
### M13 PRODUCTION-ENABLED ###                                    ### AUTONOMY GRADUATION ENGINE BUILT ###
### BOUNDED AUTONOMY ENABLED ###                                  ### FREIGHT WORKFLOW BUILT ###
### P7 PROVENANCE SURFACE BUILT ###                               ### P8 RUNTIME BUILT ###
### P6 MARKED COMPLETE ###                                        ### A P6 CRITERION WAS SCORED ###
### P7 UNBLOCKED ###                                              ### V13 RESOLVED BY PREFERENCE ###
### V14 RESOLVED BY PREFERENCE ###                                ### V15 RESOLVED BY PREFERENCE ###
### PolicyOverridden MINTED ###                                   ### P6-D71 RESOLVED BY A BUILD SESSION ###
### M1 MACHINE EDITED ###                                         ### M2 STATE MACHINE EDITED ###
### M3 EFFECT SEAM REWRITTEN ###                                  ### M4 MACHINE EDITED ###
### M9 MACHINE EDITED ###                                         ### M11 MACHINE EDITED ###
### M12 MACHINE EDITED ###
```

### The mutation battery

`scripts/mutate_phase6_brake.py` prints `N mutations caught, 0 escaped`. Each mutant reintroduces a
defect whose prohibition is canonically established, and each must turn the acceptance battery RED
**for the intended reason**. ### **NO ESCAPED MUTATION MAY BE HAND-WAVED AWAY.** **Include an
anti-vacuity control** — a mutant the battery is expected NOT to catch, or a no-mutation run that must
stay green — so the count is a measurement rather than an assertion. The tree must be restored
byte-identical afterwards, with `git status --porcelain` empty.

**At minimum, plant mutations that:** add a third brake state; allow automation to release; allow a
detector to narrow; allow a model to engage; let a timer auto-release; add a TTL column; treat an
absent brake store as released; treat an unreadable store as released; stop the claim CAS checking
the brake version; check only the tenant version; check only the global version; let the brake kill an
executing worker; let the brake abandon a claimed grant; leave an old witness valid after release;
leave an old grant valid after release; skip the new checkpoint after release; reduce release to a
human and a decision_ref; accept a loaded page as positive health; let an unresolved Sev-0 pass;
represent GLOBAL as a fake tenant; allow multiple platform rows; give the platform row a tenant
column; let same-scope flapping create multiple active brakes; suppress the rising signal count; add a
fifth F13 event; invent a local unauthorized-release event instead of F14; let replay re-engage a live
brake; let compensation write under an active brake; block observation under an active brake; hide an
active brake from the operator report; introduce a second gate minter; ### **introduce a second
independent brake authority**; delete a brake row; and accept a no-op battery or a zero-population
scan as green.

**Do not hard-code an expected mutation count anywhere.** The battery derives it.

---

## 5. What you must NOT do

- ### **BUILD ONLY M13.** Not P6. Not the phase. **Do not perform P6 acceptance, do not score a P6
  criterion, do not move P6's status, and do not unblock or start P7.** ### **LANDING M13 IS NOT P6
  COMPLETION** — there is a distinct acceptance step afterwards and it is not yours.
- ### **DO NOT CREATE A SECOND BRAKE AUTHORITY.** No second `BrakeStore`, no second brake state
  table, no second brake decision engine, no second checkpoint brake decision, no second claim-time
  brake authority. §3.5.14.
- ### **PRESERVE THE M1–M12 RUNTIME.** All twelve are landed and no further code is owed. **Do not
  modify M1–M12**; M4, M7, M9, M11 and M12 in particular are not edited at all. Their residuals are
  debt rows: **do not open a remediation campaign against any `P6-D*`.**
- ### **`checkpoint.py` REMAINS THE SOLE GATE MINTER.** Do not construct a `GateEntry` or a
  `GateRegistry`, do not call `register_gate`, do not populate the production `GateRegistry`. **Do
  not weaken the checkpoint's step order, the witness's unconstructability, or the claim CAS's
  `WHERE`-clause revalidation** — the CAS is already correct and is a regression anchor.
- ### **DO NOT DELETE THE LANDED `BrakeEngaged` CONSUMERS** in `pipeline_instance.py` and
  `approval.py` to make a "who mints what" scan green. They consume; you emit.
- ### **DO NOT MINT AN UNREGISTERED EVENT**, including any fifth F13 contract and including
  `PolicyOverridden`, and **do not edit `event_contracts_data.json` to make an oracle pass.**
- ### **SHIP DARK.** No production brake UI, admin console, live dashboard, Slack / email / SMS /
  voice brake command, production detector wiring, outbound integration, freight workflow, autonomous
  operation, bounded autonomy, autonomy graduation, P7 provenance surface or P8 runtime. **Test and
  probe-only invocation is fine. Nothing reaches live traffic.**
- ### **DO NOT RESOLVE ANY OF THE NINE AUTHORITY QUESTIONS IN §3.6, AND DO NOT INVENT AUTHORITY THE
  CORPUS DOES NOT CONTAIN.** `M13-AQ-1` and `M13-AQ-7` are settled by canon and you build what they
  say; for `M13-AQ-2`, `-3`, `-4`, `-5`, `-6`, `-8` and `-9` you build the fail-closed side and
  **REPORT**. **`V13`, `V14` and `V15` stay OPEN at their fail-closed defaults** — nothing is
  auto-engaged on a threshold this unit invents, and release authority is not widened.
- ### **DO NOT EDIT A SPECIFICATION, ADR OR REGISTRY TO MAKE A TEST PASS.** `M13-AQ-2` in
  particular names two files that are CORRECT under the AUTHORITY sense of "narrow"; "correcting"
  either would be changing a right document to match a misreading.
- ### **A LOCAL COMMIT IS ALLOWED AND EXPECTED. DO NOT PUSH, DO NOT DEPLOY, AND DO NOT ENABLE
  ANYTHING.** No remote operation of any kind.

---

## 5a. The review tier, stated once

`CLAUDE.md` §7 scales review with risk, and says: *"When genuinely torn between two tiers, take the
higher one once and say so."*

**M13 is tier-1**, and this file says so rather than leaving a later session to argue it:

1. **It lands a migration** — new constraints, new triggers and new columns on tables the checkpoint
   kernel reads inside its own transaction, plus an edit to `schema.py`'s canonical partition.
2. **It is load-bearing for tenant isolation** — the platform row is the one defended tenant-exempt
   table in the repository, and a global brake represented as a fake tenant would couple every
   brokerage's admission to one row that claims to be somebody's.
3. ### **It decides whether an action is allowed, inside the checkpoint AND inside the claim CAS** —
   and it is the last line of defence when every other subsystem is wrong. ### **A DEFECT HERE IS NOT
   A WRONG ANSWER; IT IS THE ABSENCE OF THE THING THAT STOPS WRONG ANSWERS.**
4. **It edits a landed P3 kernel module** that four other machines depend on.

So M13 takes the higher tier once and says so: ### **a focused independent review by a session that
did not build it is OWED before this lands.**

---

## 6. How you will be measured

Product Driver runs `scenarios/p6_m13_brake.yaml` — the permanent scenario — plus generated
adversarial scenarios, then a completion audit and an independent review.

**The permanent scenario measures the DATABASE, the EVENT REGISTRY, the CLAIM CAS and the AST, not
your narration.** Twenty-two persisted-state and registry oracles, including: one that issues sixteen
forbidden writes against a live canonical database behind four positive controls and an asserted
surviving-row count; one that attempts to insert a second `platform_brake` row three ways and requires
each refused while the one lawful in-place update is ACCEPTED; one that walks the AST across the whole
package to prove ### **exactly one class owns the brake lifecycle and exactly one module writes brake
state**; one that reads the claim CAS out of `checkpoint.py` and requires the composite token in its
`WHERE` clause, with the two non-claiming ledger updates named as the control so a same-substring
statement cannot slip in; and one that proves ### **no TTL or expiry exists as a column, an identifier
or a non-docstring literal** — measured on the AST rather than by grep, because `brake.py`'s docstring
contains the words "TTL" and "expire" in the sentence explaining that neither exists.

> ### **THE BASELINE IS NOT "EVERYTHING IS RED BEFORE M13", AND THIS TASK WILL NOT PRETEND IT IS.**
>
> P3 legitimately landed real safety guarantees, and **9 of the 22 oracles are ALREADY GREEN on
> `ded6a841`** — the two-state CHECK, the one-platform-row cardinality, tenant-first uniqueness, the
> composite version token, the claim CAS, the single authority, the F13 registry, the tenant
> partition, ship-dark, and the canonical table set — while the READ PATH splits: the composite
> token is P3's and correct, but `platform_status` lets a raw `sqlite3` error escape instead of
> the canonical `BrakeStoreUnreachable`, so the fail-closed half of it is yours. ### **AN ALREADY-GREEN P3 GUARANTEE IS NOT A
> FALSE GREEN.**
>
> **The other 13 are RED and they are the M13 delta.** ### **YOUR JOB IS TO MAKE THE DELTA VISIBLE
> AND CLOSE IT — not to make everything red first, and not to claim credit for what P3 already did.**
> An M13-specific oracle that only checks something P3 already did is **not enough to prove M13
> completion**, and your report must say which guarantees you added rather than which oracles are
> green.

**Every battery is invoked as `python -m pytest`, never the bare `pytest` console script**, and
`no tests ran` and `ERROR: file or directory not found` are globally forbidden — so a battery cannot
report the absence of a failure as the absence of a defect.

If a scenario oracle is wrong, **say so and show the evidence**; do not change the product to satisfy
a defective oracle. An oracle that cannot pass on a correct product is the mirror image of a false
green. **This bootstrap repaired eight of its own oracles for exactly that reason, and a ninth was
demanding that a release by an unrecorded human be ACCEPTED — finding a tenth is a good outcome.**

---

## 7. What to report

Alongside the ordinary evidence, state explicitly:

- ### **the map of what P3 already guaranteed, what M13 added, and where you put each missing
  guarantee** — the three questions of §3.5.14, answered mechanically;
- ### **that you created NO second brake authority**, with the AST evidence;
- **the nine authority questions**, which side you built, and for `M13-AQ-6` your **A / B / C**
  classification with the evidence that produced it, marked as an answer to an OPEN question;
- ### **which SENSE of "narrow" each authority you relied on was using (`M13-AQ-2`), and that you
  edited neither of the two files that use the AUTHORITY sense**;
- **that `M13-AQ-5` (platform human identity) and `M13-AQ-8` (the reused platform row) remain open**,
  and what fail-closed behaviour you built for each;
- **that `M13-AQ-9` (`release_blockers`) was a docstring naming a seam that did not exist**, and
  whether you built the seam or corrected the docstring;
- **the baseline attribution**: which oracles were already green from P3, which were the M13 delta,
  and which were indeterminate;
- **that `P6-D82`…`P6-D88` were read and NOT actioned**, and that `P6-D71` / `PolicyOverridden`
  remains open and was not invented;
- **that `V13`, `V14` and `V15` remain open at their fail-closed defaults**;
- ### **that `criteria_scored` is `[]`, that P6 has NOT moved and is NOT complete, that P6 acceptance
  is a distinct later step you did not perform, and that P7 is still `BLOCKED`**;
- and **the exact CI position, measured rather than assumed** — which jobs concluded what, and
  whether any of them executed M13's own tests.

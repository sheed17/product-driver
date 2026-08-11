# Hand-checked counterfactuals (U2)

For each sampled ACCEPTED scenario: *would this scenario fail if the product were
broken in the way it claims to probe?* Every expectation string and status code
was checked against the real Neyma source at
`/Users/sammyfammy/Desktop/freight-logistics-operational-teammate` (the path
`driver.config.yaml` actually points the reasoner at).

Legend — VERDICT:
* **SOUND** — a defect of the kind it names flips at least one assertion to FAIL,
  and no plausible correct product fails it.
* **PARTIAL** — falsifiable, but with a hole (a class of the same defect slips
  through, or it can false-alarm on a correct product).
* **UNTESTABLE** — the defect it names passes every assertion it has.

---

## Task A, wave r1 (live, 340.8 s, 6 proposed / 5 accepted / 1 refused)

### EX-02 `malformed_input` P1 — "unverifiable action link renders REJECTED, never APPLIED"
Assertions: `GET /email/action?token=garbage.deadbeef` → status 401, body contains
`Action link rejected` and `REJECTED`; forbidden anywhere: `Neyma action applied`,
`APPLIED`; plus a persisted state-consistency probe.

Verified against product: `action_callback.py:172-174` returns http 401 with title
`Action link rejected` on `DeliverySignatureError`; `:189-192` returns 200 with
title `Neyma action applied` on success. Both literals are real.

Counterfactual — the signature check is removed and a forged token is applied:
status becomes 200 (assertion fails), body carries `Neyma action applied` (the
forbidden observation fires). **VERDICT: SOUND.** This is a genuinely useful,
behaviour-specific probe of the exact risk it names.

### EX-04 `concurrency` P2 — two racing forged action links
Assertions: two simultaneous `GET /email/action?token=garbage.*` each → 401;
forbidden `Neyma action applied` / `APPLIED`.
Counterfactual — a race lets one forged link through: that response is 200 and
carries the forbidden text. **VERDICT: SOUND.**

### EX-06 `retry_safety` P3 — wrong HTTP verb cannot move state
Assertions: `POST /email/action` → 405, body contains `Method not allowed`.
Verified: `action_callback.py:2155-2160` `_method_not_allowed` returns 405 with
that exact title; `:306` refuses `POST` to `/email/action`.
Counterfactual — POST is wired to the apply path: status is not 405.
**VERDICT: SOUND**, though low-value (it probes a routing rule, not the unit's
stated risk).

### EX-01 `safety_invariant` P1 — every run's state is one the audit trail records
Oracle: `sqlite3 … "SELECT count(*) FROM workflow_runs r WHERE r.state <> (SELECT
a.to_state …)"` with `contains: ["0"]`.
Counterfactual — 1 inconsistent row: output `1`, which does not contain `0` → FAIL.
Counterfactual — **10** inconsistent rows: output `10`, which **does** contain the
substring `0` → **PASS**. The oracle is a substring match on a decimal count, so
every count containing the digit `0` (10, 20, 30, 100, 105 …) silently passes.
It also carries `expected_observations: ["0"]`, a global substring search that
matches almost any output (an HTTP `200`, a timestamp) and is effectively vacuous.
**VERDICT: PARTIAL.** Detects small violations, blind to larger ones.

### EX-05 `restart_recovery` P2 — "after a restart the screen still reflects real persisted state"
Assertions: restart `callback`; wait 1500 ms; `GET /email/action` (no token) → 400
containing `Missing action token`; plus the EX-01 consistency probe.
Counterfactual — the service restarts but serves entirely stale/empty state: a
token-less GET still returns 400 `Missing action token`, because that branch
(`action_callback.py:146-151`) never touches persisted state.
**VERDICT: UNTESTABLE for its stated purpose.** It proves the service came back
up; it cannot observe whether what it serves reflects durable state.

**Task A coverage vs its own risk register:** the plan named 3 risks. `P1
stale_state` (the unit's headline risk, AC3) and `P2 missing_data` are
`covered_by: []`. Only the `P2 restart_recovery` risk is claimed covered — by
EX-05, which is UNTESTABLE for that purpose.

---

## Task F, wave r1 (live, 384.4 s, 4 proposed / 4 accepted / 0 refused) — NEW TASK

### Risk identification — verified against real product source
The generator named 4 risks, 2 of them P0, each citing real code:
* `DispatchStatus` really is `{DRY_RUN, OUTBOXED, SENT, BLOCKED, FAILED}` with no
  UNKNOWN member (`src/freight_recon/delivery_dispatch.py`);
* `commit_key.py` really governs external TMS writes, not operator notifications;
* `delivery_action_rejected` really is an event type in `src/freight_recon/delivery.py`.
This is accurate, non-fabricated, genuinely useful analysis of the real product.

### The scenarios do not cover any of it
All 4 accepted scenarios drive `POST /actions/signed` with a tampered or missing
token — the malformed-input surface, not the delivery surface. All four risks,
including both P0s, are `covered_by: []`.

* **S1** `malformed_input` — `POST /actions/signed` → 401 `could not be verified`,
  then `SELECT event_type, payload_json FROM security_events WHERE
  event_type='delivery_action_rejected'` containing `delivery_action_rejected` and
  `signature`. Counterfactual (signature check removed): status ≠ 401 → FAIL.
  **VERDICT: SOUND** as a probe of signature rejection.
  But its `requirement_reference` is *"a send whose outcome is unconfirmed is
  recorded as unknown, never as delivered"* — a real acceptance criterion that this
  scenario does not test. **Grounding is real but misattributed.**
* **S2** `missing_data` — `POST /actions/signed` (no token) → 400 `missing its
  signed token`. SOUND, low value. Cites *"a transport outage leaves no
  notification silently dropped"* — again misattributed.
* **S3** `concurrency` — two identical tampered submissions in parallel, both → 401,
  then `SELECT count(*) > 0 FROM security_events WHERE …` containing `1`. SOUND for
  "both are answered and recorded"; does not test delivery concurrency.
  `product_principle_reference` is `repository_silent`, which is an ASK_USER
  boundary id, not a product principle, and is unrelated to concurrency.
* **S4** `restart_recovery` — record a rejection, restart `callback`, submit again,
  re-read the event. Counterfactual (records lost on restart): the `count(*) > 0`
  probe returns `0` → FAIL. **VERDICT: SOUND** for durability of that record.

### Why the P0s got no coverage — structural, not a model failure
The brief offered the generator exactly one HTTP service (`callback` on
`127.0.0.1:8001`), one SQLite path, and 8 approved commands. There is **no
fault-injection lever of any kind** in that set: nothing can make a transport time
out after a post, nothing can sever a dependency. The generator cannot exercise a
partial-dependency failure because the human-approved command set gives it no way
to cause one. Its coverage ceiling is set by that set, not by its own judgement.

### The gate does see the gap
`scenario_gate.uncovered_required_risks(plan.risks, None)` over this exact plan
returns **4 blocking gaps** (both P0s and both P1s), each with
*"no scenario exercising this risk was executed, so nothing about it has been
verified"*. A task-F run in which all four executed scenarios PASSED therefore
still could not be accepted. The failure is legible, not silent.

Reproduce:
`.venv/bin/python verification-evidence/cert/A-QUALITY/probes/f-r1-gate-behaviour.txt` →
see `probes/f-r1-gate-behaviour.txt` for the recorded output.

---

## Candidate's own committed artifact — `after-fix/plan-A2-diff.json` S1

A **P0** risk (`ui_backend_disagreement`: "the detail screen may read a
cached/snapshotted next-action or state rather than the live backend") is recorded
`covered_by: ["S1"]`. S1's complete assertion inventory is 11 items: two `GET
/exceptions` → 200, four "the sqlite probe did not print an error" checks, and five
forbidden strings (`Traceback`, `Internal Server Error`, `no such column`,
`no such table`).

Counterfactual — the screen serves a permanently cached snapshot: both GETs still
return 200, both probes still run cleanly, no traceback appears. **Every assertion
passes. VERDICT: UNTESTABLE**, and the P0 risk is nevertheless booked as covered.

Reproduce:
`.venv/bin/python verification-evidence/cert/A-QUALITY/probes/p0-covered-by-untestable-scenario.py`

---

## Task G, wave r1 (live, 517.9 s, 3 proposed / 3 accepted / 0 refused) — NEW TASK

Task G is the malformed / missing / conflicting-evidence surface: reconcile a
carrier document against the system of record. Diff files are three real modules
(`reconciliation.py`, `extraction.py`, `document_identifier.py`).

### Risk identification — on target
4 risks, correctly tied to the acceptance criteria:
`P0 conflicting_evidence` (AC-1), `P1 missing_data` (AC-2),
`P1 ui_backend_disagreement` (AC-4, the known/inferred split),
`P1 malformed_input` (AC-3).

### Every accepted scenario probes a different surface than the task
| id | category | what it drives | cites |
|---|---|---|---|
| UEV4-W1-01 | malformed_input | `POST /actions/signed` with a bad token | "an unparseable **document** is refused with a reason and changes no state" |
| UEV4-W1-02 | missing_data | `GET /email/action` with no token | same criterion |
| UEV4-W1-03 | malformed_input | `POST /actions/signed` with a non-JSON body | same criterion |

No document, no extraction and no reconciliation appears anywhere in the plan.
All three cite the same acceptance criterion, about documents.

Counterfactuals, taken on their own terms:
* **UEV4-W1-01** — status 401 + `Action link rejected` + `could not be verified`,
  forbidden `Neyma action applied`. If the signature check were removed the status
  and the forbidden string both flip. **SOUND** as a probe of signature rejection.
* **UEV4-W1-02** — 400 + `Missing action token`. **SOUND**, low value.
* **UEV4-W1-03** — 400 + `Missing action token` for a non-JSON body. **SOUND**.
* All three carry `contains: ["0"]` on `SELECT count(*) FROM effect_grants WHERE
  state = 'VERIFIED'` — the numeric-substring trap: **10** verified grants prints
  `10`, which contains `0`, and passes.

**VERDICT for the task:** 3 sound probes of a surface the task is not about;
the P0 `conflicting_evidence` risk gets zero coverage; 3/3 requirement references
name a concept ("document") the scenarios never touch.

### The consequence, executed
`probes/category-match-false-coverage.txt` runs the candidate's own
`uncovered_required_risks` over this plan twice. With nothing executed, all 4 risks
are gaps. With the three scenarios executed and PASSED with verified evidence, only
2 gaps remain: **`missing_data` and `malformed_input` are marked VERIFIED** — by
scenarios that exercise HTTP token and request-body handling. Coverage is decided
by risk-category string equality (`scenario_gate.py:110`); nothing compares what
the scenario does with what the risk describes.

---

## Cross-task pattern (A, F, G — three surfaces, three live waves)

In all three the generator:
1. read the **real** product and produced accurate, non-fabricated risk analysis
   (verified: real endpoints, real HTTP status codes, real response literals, real
   table names, real event types, a real gap in the `DispatchStatus` enum);
2. wrote sound, behaviour-specific probes of the **one HTTP surface it could
   reach** — never once falling back on the five broad test-suite commands it was
   offered;
3. labelled those probes with the task's acceptance criteria, whether or not they
   exercised them;
4. left the task's own P0 risk with **zero coverage** in every case
   (A: `stale_state`; F: `idempotency` + `ambiguous_external_effect`;
   G: `conflicting_evidence`).

---

## Task D, wave r1 (live, 494.5 s, 5 proposed / 5 accepted / 0 refused) — INHERITED

Task D is persistence / restart — the one surface in this campaign that the
approved command set can genuinely reach (`restart_service`, `stop_service`,
`start_service` on `callback`, plus `sqlite3` on the workflow store).

**The oracles here are the correct pattern and defeat every vacuity class found
elsewhere.** Each is a `CASE WHEN … THEN '<GOOD_SENTINEL>' ELSE '<BAD_SENTINEL>'`
expression, asserted with `contains: [GOOD]` **and** `not_contains: [BAD]` **and**
`not_contains: ['no such table']`:

| sentinel pair | what it decides |
|---|---|
| `ORPHANS_NONE` / `ORPHANS_FOUND` | a witness with no grant — a half-applied authorization |
| `STATES_ALL_VALID` / `STATE_INVALID` | a run in a state the schema does not define |
| `NO_DUP_LIVE_GRANTS` / `DUP_LIVE_GRANTS` | replay re-minting an effect grant |
| `ALL_CLAIMED_WITNESSED` / `CLAIMED_NO_WITNESS` | a claim with no witness behind it |

Counterfactuals:
* **S1-restart-survives** (P0 `restart_recovery`) — restart, re-probe. If the store
  came back empty or without its tables, `RUNS_TABLE_OK` is absent → FAIL. If
  restart left an orphan, `ORPHANS_FOUND` appears → FAIL. If the probe itself
  cannot run, the good sentinel is absent → FAIL. **SOUND, no vacuity hole.**
* **S3-crash-no-partial** (P0 `crash_mid_workflow`) — ungraceful stop/start, then
  the orphan, state-validity and duplicate-grant sentinels. A crash leaving a
  witness without a grant produces `ORPHANS_FOUND` → FAIL. **SOUND.**
* **S4-replay-no-reexecute** (P0 `idempotency`) — restart, then
  `NO_DUP_LIVE_GRANTS` and `ALL_CLAIMED_WITNESSED`. If rebuild re-minted a grant,
  `DUP_LIVE_GRANTS` → FAIL. **SOUND, and exactly on the risk it names.**
* **S5-rejected-writes-no-partial** (P1) — four parallel malformed requests
  (400/404/405/401), then the orphan and state-validity sentinels. **SOUND.**
* **S2-durable-schema** (P1 `safety_invariant`) — runs the approved inline
  `create_canonical_schema` probe against a *temporary* database and asserts the
  four table names appear. It verifies the schema **definition**, not the running
  store, and drives no product surface. Counts as a generic probe under U3, not a
  behaviour-specific one. Still not a test-suite invocation.

**Coverage vs its own register:** 5 risks named (3 P0). 2 P0s covered
(`restart_recovery`, `idempotency`); `partial_failure` (P0), `stale_state` (P1)
and `dependency_failure` (P1) uncovered — and `unresolved_questions` says exactly
why: *"'a read after a write never returns stale state' cannot be exercised
end-to-end with the approved command set: driving a real authenticated transition
… requires minting a valid HMAC-signed action token … neither of which is
available."*

**This is the strongest result in the campaign** and it is the one task whose risk
surface the approved command set actually intersects.

---

## Task E, wave r1 (live, 360.6 s, 4 proposed / 4 accepted / 0 refused) — INHERITED

Task E is authorization: supervisor-only release, cross-tenant boundary, audited
refusals. The model found **no driveable release endpoint** and said so:
*"the console in-repo (operator_console.py) is a static HTML page; the only live
release path requires a Slack- or delivery-signed token. No release endpoint that
takes a supervisor identity in a driveable form is visible."*

It then **rewrote the risk** to fit the reachable surface — the P0 risk it recorded
is *"A release requested without a valid signed credential (unauthenticated /
**non-supervisor stand-in**)…"*. The word "stand-in" is the model's own.

| id | cat | pri | drives | counterfactual |
|---|---|---|---|---|
| AUTHZ-S2 | authorization | P0 | `POST /actions/signed` no token → 400 `Missing action token`, forbidden `operating in the TMS`/`RUNNING` | if an unauthenticated release were accepted, status and forbidden both flip — **SOUND** |
| AUTHZ-S3 | authorization | P0 | `POST /actions/signed` forged token → 401 `could not be verified` | forged token accepted → flips — **SOUND** |
| AUTHZ-S5 | authorization | P1 | `GET /email/action` no token → 400 | channel-parity check — **SOUND** |
| AUTHZ-S1 | missing_data | P1 | approved inline `create_canonical_schema` probe on a **temp** DB, asserts `security_events`/`audit_events` appear | cites *"every refusal is recorded in the audit log"* but records and checks **no refusal at all**; it asserts a table name exists in a schema definition — **UNTESTABLE for its stated requirement** |

**What is never verified:** the two weight-3 criteria *"a supervisor can release an
operation"* and *"a supervisor of one tenant cannot release another tenant's
operation"*. Neither appears as a risk, so neither can even show up as a gap. The
happy path is unreachable because no approved command can mint a valid signed
token — which the model states in `unresolved_questions`, a field nothing
downstream reads (probe U1).

All three authorization scenarios carry `contains: ['0']` on
`SELECT count(*) FROM security_events WHERE event_type='slack_operation…'` — a
bare count matched by substring. **Ten** recorded unauthorized operations print
`10`, which contains `0`, and the oracle passes. Each scenario retains 4 sound
HTTP assertions, so the scenario as a whole still catches the front-door failure;
the persisted-state half of the oracle is the part that is unreliable.

---

## Task C, wave r1 (live, 175.1 s, 7 proposed / 7 accepted / 0 refused) — INHERITED

Task C is the read-only shipment search view. **The mirror image of every other
task, and the most instructive single result in the campaign.**

All 7 accepted scenarios drive `GET /shipments?reference=…` and read
`SELECT … FROM shipments WHERE reference = …`. Verified against the real product:

* there is **no `/shipments` route** — the callback server serves only
  `/slack/actions`, `/slack/commands`, `/slack/events`, `/email/action`,
  `/actions/signed`;
* there is **no `shipments` table** anywhere in `schema.py` or the migrations;
* the string `shipments` does not appear anywhere under `src/`.

So all 7 scenarios are **UNSATISFIABLE**: every request would 404 against
`expect_status: 200`, and every oracle would print `Error: no such table:
shipments` and miss its `contains` needle. Not one of them could pass against a
*correct* product. They are mechanically scorable, grounded in real acceptance
criteria, non-duplicative, and free of broad-suite invocations — and they are all
guaranteed to fail.

**The generator was honest about it.** `unresolved_questions` says, in its own
words: *"scenarios assume GET /shipments?reference=<val> … If the real endpoint
differs … the request paths must be updated"*, *"The persisted store's table and
column names are assumed to be a 'shipments' table with a 'reference' column,
based on the domain"*, and *"Whether shipment records live in
…/workflow.sqlite3 … is unconfirmed"*. It flagged every assumption it made.

**And the coverage summary books 4 of the 4 risks as covered anyway** — including
the P0 `safety_invariant` ("the read-only view performs a write") credited to S3.

**Consequence, which is different from every other finding here.** These scenarios
do not produce a false ACCEPT; on execution they all fail. They produce the
opposite: **7 spurious failures against a product that is fine.** In a real run
those failures feed the investigator and the adaptive wave, so a correction cycle
would be opened against a non-defect. The honest caveat that would have prevented
it sits in `unresolved_questions`, which nothing downstream reads (probe U1).

**Attribution.** Task C's fixture describes a feature the repository does not
contain, so the invented surface is partly the benchmark's doing, not the
generator's — in a real run the builder would have just built it. What is *not*
the benchmark's doing is that the generator's explicit statement of its own
assumptions is discarded before anyone can act on it.

Oracle quality here is otherwise the second-best in the campaign: S1/S2/S3/S4/S7
pair `contains` with a complementary `not_contains` (`['0']`/`['1']`,
`['1']`/`['2']`), which removes the vacuous-pass hole that the single-needle
`contains: ['0']` oracles in A/E/G leave open.

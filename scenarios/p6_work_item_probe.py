"""Drive Neyma's P6 Work Item machine through a real brokerage narrative, and attack it.

### THIS FILE LIVES IN THE DRIVER, NOT IN NEYMA, AND THAT IS DELIBERATE.
P6 ships dark: a guard in the Neyma repository asserts that `src/freight_recon/` and `scripts/`
contain ZERO importers of `work_item.py`. A demonstration script committed into the product would
make that guard red and would be the first production caller of a capability that is not supposed to
have one. So the demonstration lives outside the product and imports it as a library.

It is written as a story a freight operator would recognise — load 4471 needs billing, somebody owns
that, somebody else takes it over when it ages — with the hostile attempts inline, because the point
is not that the happy path works. The point is what the machine REFUSES, and what it refuses with.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone

NEYMA = os.environ.get("NEYMA_REPO", "/Users/sammyfammy/freight-logistics-operational-teammate")
sys.path.insert(0, os.path.join(NEYMA, "src"))

from freight_recon.event_contracts import CONTRACTS  # noqa: E402
from freight_recon.event_envelope import EventEnvelope, format_instant  # noqa: E402
from freight_recon.event_outbox import TransactionalOutbox  # noqa: E402
from freight_recon.work_item import (  # noqa: E402
    AuthorityRefused,
    FailureDisposition,
    GuardNotSatisfied,
    IllegalTransition,
    OwnershipRefused,
    Trigger,
    UnknownWorkItem,
    WorkItemMachine,
    offboard_human,
    open_work_owned_by,
    record_human_authority,
)
from freight_recon.workflow import WorkflowStore  # noqa: E402

ACME = "acme-freight"
RIVAL = "rival-logistics"
NOW = datetime(2026, 8, 14, 9, 0, 0, tzinfo=timezone.utc)

ok = 0
bad = 0


def clock() -> datetime:
    return NOW


def show(label: str, detail: str = "") -> None:
    print(f"  {label}" + (f"\n      {detail}" if detail else ""))


def refuses(label: str, fn, *expected) -> None:
    """Run something that MUST be refused, and print the refusal the operator would see."""
    global ok, bad
    try:
        fn()
    except expected as exc:                                      # noqa: PERF203
        ok += 1
        # ### PRINT THE REASON, NOT THE HEADLINE. The first version truncated at the first full
        # stop, which for a guard refusal is the generic sentence ("X is legal for Y but no guard is
        # satisfied") and cuts off the part an operator would act on. A refusal whose reason is
        # invisible is a refusal nobody can resolve.
        text = " ".join(str(exc).split())
        print(f"  REFUSED  {label}\n      {text[:400]}")
    except Exception as exc:                                     # noqa: BLE001
        bad += 1
        print(f"  ### WRONG REFUSAL  {label}: {type(exc).__name__}: {exc}")
    else:
        bad += 1
        print(f"  ### NOT REFUSED  {label} — it was allowed")


def allows(label: str, fn):
    global ok, bad
    try:
        result = fn()
    except Exception as exc:                                     # noqa: BLE001
        bad += 1
        print(f"  ### WRONGLY REFUSED  {label}: {type(exc).__name__}: {exc}")
        return None
    ok += 1
    print(f"  OK       {label}")
    return result


def a_human_decision(store, tenant, actor_id, seed):
    """One canonical `HumanDecided` event, through the real outbox and the real contract gate."""
    import hashlib
    import uuid

    raw = bytearray(hashlib.sha256(seed.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    event_id = str(uuid.UUID(bytes=bytes(raw)))
    stamp = format_instant(NOW)
    envelope = EventEnvelope(
        event_id=event_id, event_name="HumanDecided", event_version=1,
        occurred_at=stamp, recorded_at=stamp, tenant_id=tenant, aggregate_type="work_item",
        aggregate_id=f"decision-{seed}", aggregate_version=1, causation_id=None,
        correlation_id=f"corr-{seed}", producer_component="slack", producer_transition_id="WI-9",
        actor_type="human", actor_id=actor_id, trace_id=f"trace-{seed}",
        payload={"decision_ref": f"slack-thread-{seed}"},
    )
    store.conn.execute("BEGIN IMMEDIATE")
    try:
        TransactionalOutbox(store.conn, tenant=tenant, clock=clock).emit(envelope)
        store.conn.commit()
    except BaseException:
        store.conn.rollback()
        raise
    return event_id


def _hostile_trigger(tenant: str, seed: str) -> EventEnvelope:
    """One canonical `PipelineStarted` from a pipeline that has no business starting anything here.

    Built through the real envelope and the real contract gate, because a hand-made dict would be
    refused as malformed and the scene would pass for the wrong reason.
    """
    import hashlib
    import uuid

    raw = bytearray(hashlib.sha256(seed.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    stamp = format_instant(NOW)
    return EventEnvelope(
        event_id=str(uuid.UUID(bytes=bytes(raw))), event_name="PipelineStarted", event_version=1,
        occurred_at=stamp, recorded_at=stamp, tenant_id=tenant,
        aggregate_type="pipeline_instance", aggregate_id=f"pi-{seed}", aggregate_version=1,
        causation_id=None, correlation_id=f"corr-{seed}", producer_component="pipeline",
        producer_transition_id="PL-1", actor_type="system", actor_id="pipeline-service",
        trace_id=f"trace-{seed}",
        payload={f.name: (f.enum[0] if f.enum else f"{f.name}-value")
                 for f in CONTRACTS["PipelineStarted"].fields if f.required},
    )


def _pipeline_closed(tenant: str, seed: str) -> EventEnvelope:
    """One canonical `PipelineClosed` — the WI-3 trigger shape, CARRIED ON THE PIPELINE.

    ### THE AGGREGATE IS AGAIN THE POINT. This rides on `pipeline_instance`, so the Work Item it
    closes is somebody ELSE'S aggregate. Paired with `_decision_trigger` — which rides on the Work
    Item itself — it gives a cohort with two genuinely different trigger shapes waiting on one
    missing obligation, which is the population where a shared drain can confuse them.
    """
    import hashlib
    import uuid

    raw = bytearray(hashlib.sha256(seed.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    stamp = format_instant(NOW)
    return EventEnvelope(
        event_id=str(uuid.UUID(bytes=bytes(raw))), event_name="PipelineClosed", event_version=1,
        occurred_at=stamp, recorded_at=stamp, tenant_id=tenant,
        aggregate_type="pipeline_instance", aggregate_id=f"pi-{seed}", aggregate_version=1,
        causation_id=None, correlation_id=f"corr-{seed}", producer_component="pipeline",
        producer_transition_id="PL-14", actor_type="system", actor_id="pipeline-service",
        trace_id=f"trace-{seed}",
        payload={f.name: (f.enum[0] if f.enum else f"{f.name}-value")
                 for f in CONTRACTS["PipelineClosed"].fields if f.required},
    )


def _decision_trigger(tenant: str, work_item_id: str, actor_id: str, seed: str) -> EventEnvelope:
    """One canonical `HumanDecided` CARRIED ON THE WORK ITEM — the WI-9 trigger shape.

    ### THE AGGREGATE IS THE POINT OF THIS FIXTURE. `HumanDecided` rides on `work_item:<id>`
    because that is its ordering key, so the Work Item it decides is the event's OWN aggregate.
    A `PipelineStarted` cannot demonstrate this: it rides on `pipeline_instance`, so the Work Item
    is somebody else's aggregate and the interesting case never arises.
    """
    import hashlib
    import uuid

    raw = bytearray(hashlib.sha256(seed.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    stamp = format_instant(NOW)
    return EventEnvelope(
        event_id=str(uuid.UUID(bytes=bytes(raw))), event_name="HumanDecided", event_version=1,
        occurred_at=stamp, recorded_at=stamp, tenant_id=tenant, aggregate_type="work_item",
        aggregate_id=work_item_id, aggregate_version=1, causation_id=None,
        correlation_id=f"corr-{seed}", producer_component="slack", producer_transition_id="WI-9",
        actor_type="human", actor_id=actor_id, trace_id=f"trace-{seed}",
        payload={f.name: (f.enum[0] if f.enum else f"{f.name}-value")
                 for f in CONTRACTS["HumanDecided"].fields if f.required},
        accountable_owner_id=actor_id,
    )


def main() -> int:
    global ok, bad
    workspace = tempfile.mkdtemp(prefix="p6-probe-")
    db = os.path.join(workspace, "acme.sqlite3")
    acme = WorkflowStore(db, tenant=ACME)
    rival = WorkflowStore(db, tenant=RIVAL)
    m = WorkItemMachine(acme.conn, tenant=ACME, clock=clock)
    other = WorkItemMachine(rival.conn, tenant=RIVAL, clock=clock)

    print("\n=========== NEYMA P6 — WORK ITEM OWNERSHIP, DRIVEN AND ATTACKED ===========")
    print(f"\ntenant: {ACME}   (a second brokerage, {RIVAL}, shares the same database file)")

    print("\n-- 1. Nobody can own work until somebody is recorded as able to -------------------")
    refuses("create a Work Item for an owner nobody recorded",
            lambda: m.create(work_item_id="wi-4471-billing", type="delivered_load_closure",
                             owner_id="dana", actor_type="system", actor_id="work-service"),
            OwnershipRefused)
    refuses("record 'system' as a human authority",
            lambda: record_human_authority(acme.conn, tenant=ACME, human_id="system",
                                           display_name="The System",
                                           authority_role="AUTHORIZED_HUMAN",
                                           recorded_by="sam", now=NOW),
            AuthorityRefused)
    refuses("let a MODEL record who may own work",
            lambda: record_human_authority(acme.conn, tenant=ACME, human_id="dana",
                                           display_name="Dana", authority_role="AUTHORIZED_HUMAN",
                                           recorded_by="extractor-v3", recorded_by_kind="model",
                                           now=NOW),
            AuthorityRefused)
    refuses("record an automated DETECTOR as a role that can hold work",
            lambda: record_human_authority(acme.conn, tenant=ACME, human_id="pod-detector",
                                           display_name="POD detector", authority_role="DETECTOR",
                                           recorded_by="sam", now=NOW),
            AuthorityRefused)
    allows("Sam (founder) records Dana, a dispatcher, as an authorized human",
           lambda: record_human_authority(acme.conn, tenant=ACME, human_id="dana",
                                          display_name="Dana Ruiz",
                                          authority_role="AUTHORIZED_HUMAN",
                                          recorded_by="sam", now=NOW))
    allows("Sam records Nia, the night lead",
           lambda: record_human_authority(acme.conn, tenant=ACME, human_id="nia",
                                          display_name="Nia Okafor", authority_role="POLICY_OWNER",
                                          recorded_by="sam", now=NOW))

    print("\n-- 2. Load 4471 delivered. Somebody owes the customer an invoice. ------------------")
    created = allows("open a Work Item: 'bill load 4471', owner Dana",
                     lambda: m.create(work_item_id="wi-4471-billing",
                                      type="delivered_load_closure", owner_id="dana",
                                      entity_ref="load:4471", actor_type="system",
                                      actor_id="work-service"))
    if created:
        show("state", f"{created.work_item.state.value} · owner {created.work_item.owner_id} · "
                      f"version {created.work_item.version} · event {created.event_name}")

    allows("a billing pipeline starts for it",
           lambda: m.apply("wi-4471-billing", Trigger.PIPELINE_STARTED,
                           actor_type="system", actor_id="pipeline-service"))

    print("\n-- 3. The pipeline finishes. That is NOT the same as the customer having paid. -----")
    refuses("close it because the pipeline finished",
            lambda: m.apply("wi-4471-billing", Trigger.PIPELINE_CLOSED, actor_type="system",
                            actor_id="pipeline-service", obligation_satisfied=False,
                            decision_ref="x", decision_ref_kind="AUDIT_EVENT"),
            GuardNotSatisfied)
    refuses("close it with the note 'done'",
            lambda: m.apply("wi-4471-billing", Trigger.PIPELINE_CLOSED, actor_type="system",
                            actor_id="pipeline-service", obligation_satisfied=True,
                            decision_ref="done", decision_ref_kind="AUDIT_EVENT"),
            GuardNotSatisfied)
    refuses("close it citing the pipeline's own completion event as the decision",
            lambda: m.apply("wi-4471-billing", Trigger.PIPELINE_CLOSED, actor_type="system",
                            actor_id="pipeline-service", obligation_satisfied=True,
                            decision_ref=created.event_id if created else "x",
                            decision_ref_kind="AUDIT_EVENT"),
            GuardNotSatisfied)

    print("\n-- 4. It ages. Nobody has touched it. ---------------------------------------------")
    refuses("escalate it by asserting it is old",
            lambda: m.apply("wi-4471-billing", Trigger.AGE_THRESHOLD_CROSSED,
                            actor_type="system", actor_id="a-sweep"),
            GuardNotSatisfied)
    acme.conn.execute("BEGIN IMMEDIATE")
    acme.conn.execute(
        "INSERT INTO durable_timers (tenant, timer_id, aggregate_type, aggregate_id, timer_kind,"
        " fire_at, state, scheduled_at, fired_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (ACME, "t-4471-age", "work_item", "wi-4471-billing", "work_item_age_threshold",
         format_instant(NOW), "FIRED", format_instant(NOW), format_instant(NOW)))
    acme.conn.commit()
    escalated = allows("escalate it when its DURABLE TIMER actually fired",
                       lambda: m.apply("wi-4471-billing", Trigger.AGE_THRESHOLD_CROSSED,
                                       actor_type="system", actor_id="timer-relay",
                                       timer_id="t-4471-age"))
    if escalated:
        show("state", f"{escalated.work_item.state.value} — surfaced unprompted, still owned by "
                      f"{escalated.owner_after}")

    print("\n-- 5. Nia takes it over. Ownership moves by a recorded act, never by drift. --------")
    refuses("hand it to somebody nobody recorded",
            lambda: m.apply("wi-4471-billing", Trigger.OWNERSHIP_REASSIGNED, actor_type="human",
                            actor_id="dana", to_owner="whoever-is-on-tonight"),
            GuardNotSatisfied)
    refuses("let a MODEL reassign it",
            lambda: m.apply("wi-4471-billing", Trigger.OWNERSHIP_REASSIGNED, actor_type="model",
                            actor_id="extractor-v3", to_owner="nia"),
            AuthorityRefused)
    refuses("let the SYSTEM reassign it with nobody accountable for the decision",
            lambda: m.apply("wi-4471-billing", Trigger.OWNERSHIP_REASSIGNED, actor_type="system",
                            actor_id="work-service", to_owner="nia"),
            GuardNotSatisfied)
    moved = allows("Dana hands it to Nia",
                   lambda: m.apply("wi-4471-billing", Trigger.OWNERSHIP_REASSIGNED,
                                   actor_type="human", actor_id="dana", to_owner="nia"))
    if moved:
        show("ownership", f"{moved.owner_before} -> {moved.owner_after} "
                          f"(event {moved.event_name}, recorded, not inferred)")

    print("\n-- 6. Dana leaves the company. ----------------------------------------------------")
    allows("open a second item for Dana: 'chase POD on 4482'",
           lambda: m.create(work_item_id="wi-4482-pod", type="pod_chase", owner_id="dana",
                            entity_ref="load:4482", actor_type="system",
                            actor_id="work-service"))
    refuses("offboard Dana while she still owes this brokerage open work",
            lambda: offboard_human(acme.conn, tenant=ACME, human_id="dana",
                                   offboarded_by="sam", now=NOW),
            OwnershipRefused)
    show("still accountable for",
         str(open_work_owned_by(acme.conn, tenant=ACME, owner_id="dana")))
    acme.conn.execute("BEGIN IMMEDIATE")
    acme.conn.execute(
        "INSERT INTO durable_timers (tenant, timer_id, aggregate_type, aggregate_id, timer_kind,"
        " fire_at, state, scheduled_at, fired_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (ACME, "t-4482-age", "work_item", "wi-4482-pod", "work_item_age_threshold",
         format_instant(NOW), "FIRED", format_instant(NOW), format_instant(NOW)))
    acme.conn.commit()
    m.apply("wi-4482-pod", Trigger.AGE_THRESHOLD_CROSSED, actor_type="system",
            actor_id="timer-relay", timer_id="t-4482-age")
    allows("Dana hands 4482 to Nia before she goes",
           lambda: m.apply("wi-4482-pod", Trigger.OWNERSHIP_REASSIGNED, actor_type="human",
                           actor_id="dana", to_owner="nia"))
    allows("NOW Dana can be offboarded",
           lambda: offboard_human(acme.conn, tenant=ACME, human_id="dana",
                                  offboarded_by="sam", now=NOW))
    refuses("give an offboarded human new work",
            lambda: m.create(work_item_id="wi-4490-billing", type="delivered_load_closure",
                             owner_id="dana", actor_type="system", actor_id="work-service"),
            OwnershipRefused)
    show("ownerless open work items (Sev-0 detector)", str(m.ownerless()))

    print("\n-- 7. The customer pays. A HUMAN says the obligation is discharged. ---------------")
    decision = a_human_decision(acme, ACME, "nia", "paid-4471")
    closed = allows("Nia closes 4471 against her recorded decision",
                    lambda: m.apply("wi-4471-billing", Trigger.PIPELINE_CLOSED,
                                    actor_type="system", actor_id="pipeline-service",
                                    obligation_satisfied=True, decision_ref=decision,
                                    decision_ref_kind="AUDIT_EVENT"))
    if closed:
        show("state", f"{closed.work_item.state.value} · decision {decision[:8]}… · "
                      f"owner {closed.owner_after}")
    refuses("re-close an already closed obligation",
            lambda: m.apply("wi-4471-billing", Trigger.PIPELINE_CLOSED, actor_type="system",
                            actor_id="pipeline-service", obligation_satisfied=True,
                            decision_ref=decision, decision_ref_kind="AUDIT_EVENT"),
            IllegalTransition)

    print("\n-- 7b. SOMEBODY LEANS ON THE CLOSED OBLIGATION, REPEATEDLY. ------------------------")
    # ### THIS IS THE SCENE AN INDEPENDENT REVIEW REJECTED THE FIRST CANDIDATE OVER. The closed
    # item does not move, so every attempt lands at ONE aggregate version. The first candidate keyed
    # the refusal record on that version: the first attempt was recorded, the second failed as a
    # transport duplicate instead of a refusal, and through the dedup inbox the failure rolled the
    # receipt back and redelivered forever. Read the two counts at the end of this scene — every
    # attempt refused, every attempt on the security surface, nothing left half-delivered.
    security_before = acme.conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE tenant=? AND event_type=?",
        (ACME, "IllegalTransitionAttempted")).fetchone()[0]
    version_before = m.require("wi-4471-billing").version
    for attempt, (trigger, who) in enumerate((
        (Trigger.PIPELINE_STARTED, "someone-else-service"),
        (Trigger.EVIDENCE_MISSING, "doc-detector"),
        (Trigger.HUMAN_DECISION_REQUIRED, "slack-bot"),
    ), start=1):
        refuses(f"hostile attempt {attempt}: {trigger.value} on a CLOSED obligation",
                lambda t=trigger, w=who: m.apply(
                    "wi-4471-billing", t, actor_type="system", actor_id=w, reason="pressure"),
                IllegalTransition)
    security_after = acme.conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE tenant=? AND event_type=?",
        (ACME, "IllegalTransitionAttempted")).fetchone()[0]
    recorded = security_after - security_before
    show("distinct hostile attempts recorded to the security surface",
         f"{recorded} of 3" + ("" if recorded == 3 else "   ### ATTEMPTS WENT UNRECORDED"))
    if recorded != 3:
        bad += 1
    if m.require("wi-4471-billing").version != version_before:
        bad += 1
        show("### the item MOVED under a refused attempt", "")

    print("\n-- 7c. THE SAME HOSTILE EVENT, DELIVERED TWICE, THROUGH THE REAL INBOX. -----------")
    # A transport that redelivers is normal. A transport that can never finish delivering is a
    # poison loop — and that is what recording the refusal used to cause.
    hostile_a = _hostile_trigger(ACME, "hostile-a")
    hostile_b = _hostile_trigger(ACME, "hostile-b")
    for label, envelope in (("first hostile event", hostile_a), ("second, DIFFERENT one", hostile_b)):
        outcome = allows(f"{label} is consumed and refused, not looped",
                         lambda e=envelope: m.consume(e, work_item_id="wi-4471-billing",
                                                      trigger=Trigger.PIPELINE_STARTED))
        if outcome is not None:
            show("delivery", f"{outcome.consume.outcome.value} · machine refusal: "
                             f"{outcome.refusal_kind}")
            if outcome.refusal_kind != "ILLEGAL" or outcome.moved:
                bad += 1
    twice = m.consume(hostile_a, work_item_id="wi-4471-billing", trigger=Trigger.PIPELINE_STARTED)
    show("the SAME hostile event redelivered", twice.consume.outcome.value)
    final = acme.conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE tenant=? AND event_type=?",
        (ACME, "IllegalTransitionAttempted")).fetchone()[0]
    show("security records after 2 distinct events + 1 redelivery",
         f"{final - security_after} of 2" +
         ("" if final - security_after == 2 else "   ### EVIDENCE IS WRONG"))
    if final - security_after != 2:
        bad += 1

    print("\n-- 7d. AN EVENT ARRIVES FOR WORK THAT DOES NOT EXIST, AND NAMES NOBODY. -----------")
    # M-26 parks it until the referent shows up — and hands it to a human when the TTL runs out.
    # If there is no human to hand it to, the park is an obligation nobody owes, which is the one
    # thing rule 13 does not allow. So: resolved, or refused. Never ownerless.
    refuses("park an obligation nobody owns",
            lambda: m.consume(_hostile_trigger(ACME, "orphan"), work_item_id="wi-9999-unknown",
                              trigger=Trigger.PIPELINE_STARTED),
            OwnershipRefused)
    refuses("name a human this brokerage never recorded as the accountable owner",
            lambda: m.consume(_hostile_trigger(ACME, "orphan-2"), work_item_id="wi-9999-unknown",
                              trigger=Trigger.PIPELINE_STARTED,
                              accountable_owner_id="ops-team"),
            OwnershipRefused)
    held = allows("park it once a real accountable human is named",
                  lambda: m.consume(_hostile_trigger(ACME, "orphan-3"),
                                    work_item_id="wi-9999-unknown",
                                    trigger=Trigger.PIPELINE_STARTED,
                                    accountable_owner_id="nia"))
    if held is not None:
        show("delivery", held.consume.outcome.value)
    ownerless_parks = acme.conn.execute(
        "SELECT COUNT(*) FROM pending_references WHERE tenant=? AND accountable_owner_id IS NULL",
        (ACME,)).fetchone()[0]
    total_parks = acme.conn.execute(
        "SELECT COUNT(*) FROM pending_references WHERE tenant=?", (ACME,)).fetchone()[0]
    show("parked obligations with NO accountable human",
         f"{ownerless_parks} of {total_parks} parked" +
         ("" if ownerless_parks == 0 and total_parks > 0 else "   ### RULE 13 IS BROKEN HERE"))
    if ownerless_parks or not total_parks:
        bad += 1

    print("\n-- 7e. A DISPATCHER DECIDES BEFORE THE WORK ITEM HAS LANDED. -----------------------")
    # ### THE ONE THAT USED TO POISON THE TRANSPORT, AND IT IS AN ORDINARY TUESDAY.
    # Nia answers the Slack thread before the projection that opens the Work Item has caught up.
    # Her decision rides on `work_item:<id>` — the item she is deciding IS the event's own
    # aggregate — and the inbox used to skip a requirement shaped like that as a creation
    # self-reference. The demand evaporated, the handler raised, the receipt rolled back with it,
    # and her decision was redelivered forever: never applied, never parked, never surfaced, and
    # nowhere in any record an operator could read. It is now held, owned and drained.
    late = _decision_trigger(ACME, "wi-5588-detention", "nia", "late-decision")
    parked = allows("hold Nia's decision until the Work Item shows up",
                    lambda: m.consume(late, work_item_id="wi-5588-detention",
                                      trigger=Trigger.HUMAN_DECIDED,
                                      accountable_owner_id="nia"))
    if parked is not None:
        show("delivery", parked.consume.outcome.value)
    row = acme.conn.execute(
        "SELECT referenced_type, referenced_id, accountable_owner_id, expires_at, park_state "
        "  FROM pending_references WHERE tenant=? AND event_id=?", (ACME, late.event_id)).fetchone()
    if row is None or row["accountable_owner_id"] != "nia" or not row["expires_at"]:
        bad += 1
        show("### the decision was not parked with an owner and a deadline")
    else:
        ok_park = f"{row['referenced_type']}:{row['referenced_id']}"
        show("held on", f"{ok_park} · accountable: {row['accountable_owner_id']} · has a deadline")
    repeat = allows("the transport redelivers it; nothing happens twice",
                    lambda: m.consume(late, work_item_id="wi-5588-detention",
                                      trigger=Trigger.HUMAN_DECIDED))
    if repeat is not None:
        show("delivery", repeat.consume.outcome.value)

    # The projection catches up and the Work Item opens, reaching the state a human decision moves.
    allows("the Work Item finally opens and asks for a human",
           lambda: m.create(work_item_id="wi-5588-detention", type="detention_dispute",
                            owner_id="nia", actor_type="system", actor_id="work-service"))
    m.apply("wi-5588-detention", Trigger.HUMAN_DECISION_REQUIRED,
            actor_type="system", actor_id="work-service")
    ref = a_human_decision(acme, ACME, "nia", "detention-call")
    applied = allows("her decision is applied on the next delivery, not expired onto a desk",
                     lambda: m.consume(late, work_item_id="wi-5588-detention",
                                       trigger=Trigger.HUMAN_DECIDED, decision_ref=ref,
                                       decision_ref_kind="AUDIT_EVENT"))
    if applied is not None:
        show("delivery", f"{applied.consume.outcome.value} · Work Item is now "
                         f"{m.require('wi-5588-detention').state.value}")
    still_held = acme.conn.execute(
        "SELECT park_state FROM pending_references WHERE tenant=? AND event_id=?",
        (ACME, late.event_id)).fetchone()["park_state"]
    show("the park is now", still_held)
    if still_held != "DRAINED" or not (applied and applied.moved):
        bad += 1
        show("### a held decision that never drains is a drop with a deadline")

    print("\n-- 8. Three weeks later the customer short-pays. ----------------------------------")
    before = acme.conn.execute(
        "SELECT envelope_json FROM event_outbox WHERE tenant=? AND event_id=?",
        (ACME, closed.event_id if closed else "")).fetchone()
    reopen_decision = a_human_decision(acme, ACME, "nia", "shortpay-4471")
    reopened = allows("Nia reopens it as a new phase",
                      lambda: m.apply("wi-4471-billing", Trigger.REOPEN_REQUESTED,
                                      actor_type="human", actor_id="nia",
                                      decision_ref=reopen_decision,
                                      decision_ref_kind="AUDIT_EVENT", assignee="nia"))
    after = acme.conn.execute(
        "SELECT envelope_json FROM event_outbox WHERE tenant=? AND event_id=?",
        (ACME, closed.event_id if closed else "")).fetchone()
    if reopened:
        show("state", f"{reopened.work_item.state.value} · phase {reopened.work_item.phase_seq} · "
                      f"prior closure preserved: "
                      f"{'BYTE-IDENTICAL' if before and after and before[0] == after[0] else '### CHANGED'}")
        if not (before and after and before[0] == after[0]):
            bad += 1

    print("\n-- 9. The other brokerage on the same database. -----------------------------------")
    refuses("rival-logistics advances acme's Work Item",
            lambda: other.apply("wi-4471-billing", Trigger.PIPELINE_STARTED,
                                actor_type="system", actor_id="rival-service"),
            UnknownWorkItem)
    refuses("acme gives its work to a human recorded only at rival-logistics",
            lambda: m.create(work_item_id="wi-x", type="x", owner_id="rival-bob",
                             actor_type="system", actor_id="work-service"),
            OwnershipRefused)

    # ==============================================================================================
    print("\n-- 9-bis. Two events arrive for a Work Item that has not landed yet. ---------------")
    # ### THE NARRATIVE THE INDEPENDENT REVIEW REJECTED THIS CANDIDATE OVER (F-04).
    # A dispatcher's decision and a pipeline's closure both reference load 5588's billing
    # obligation, and both arrive BEFORE the projection that creates it. Both are held under M-26.
    # When the obligation finally exists, each must be executed as ITSELF. The defect let whichever
    # event happened to wake the cohort interpret the other one: the closure was consumed through
    # the decision's semantics, so the Work Item never closed, the closure was marked delivered,
    # its retry became a no-op, and a security record was written about a transition nobody
    # attempted. The load stays open on the books and nothing anywhere says so.
    def _check(label: str, condition: bool, detail: str = "") -> None:
        global ok, bad
        if condition:
            ok += 1
            print(f"  OK       {label}" + (f"\n      {detail}" if detail else ""))
        else:
            bad += 1
            print(f"  ### WRONG  {label}" + (f"\n      {detail}" if detail else ""))

    late = "wi-5588-billing"
    decision_early = _decision_trigger(ACME, late, "nia", "cohort-decision")
    closure_early = _pipeline_closed(ACME, "cohort-closure")

    held_a = m.consume(decision_early, work_item_id=late, trigger=Trigger.HUMAN_DECIDED,
                       accountable_owner_id="nia")
    held_b = m.consume(closure_early, work_item_id=late, trigger=Trigger.PIPELINE_CLOSED,
                       obligation_satisfied=True, accountable_owner_id="nia")
    _check("the dispatcher's decision is HELD, not dropped and not retried into the ground",
           held_a.consume.outcome.value == "PARKED_MISSING_AGGREGATE", held_a.consume.outcome.value)
    _check("the pipeline's closure is HELD too — one cohort, two different trigger shapes",
           held_b.consume.outcome.value == "PARKED_MISSING_AGGREGATE", held_b.consume.outcome.value)

    parked_now = acme.conn.execute(
        "SELECT event_id, park_state, accountable_owner_id FROM pending_references "
        " WHERE tenant=? AND referenced_id=? ORDER BY arrival_sequence", (ACME, late)).fetchall()
    _check("both are owed by a named human while they wait (rule 13)",
           len(parked_now) == 2 and all(r["accountable_owner_id"] == "nia" for r in parked_now),
           f"{len(parked_now)} parked, owners: {[r['accountable_owner_id'] for r in parked_now]}")

    # The obligation lands, and reaches the state the decision moves it out of.
    m.create(work_item_id=late, type="delivered_load_closure", owner_id="nia",
             actor_type="system", actor_id="work-service")
    m.apply(late, Trigger.HUMAN_DECISION_REQUIRED, actor_type="system", actor_id="work-service")
    before_security = acme.conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE tenant=?", (ACME,)).fetchone()[0]

    ref = a_human_decision(acme, ACME, "nia", "cohort-ref")
    ran_a = m.consume(decision_early, work_item_id=late, trigger=Trigger.HUMAN_DECIDED,
                      decision_ref=ref, decision_ref_kind="AUDIT_EVENT")
    _check("the held decision runs as a DECISION and moves the obligation to in-progress",
           ran_a.moved and m.require(late).state.value == "IN_PROGRESS",
           f"state: {m.require(late).state.value}")
    _check("the pipeline's closure was NOT consumed through the decision's semantics",
           ran_a.consume.drained == (),
           f"drained by the decision: {list(ran_a.consume.drained)}")

    still = acme.conn.execute(
        "SELECT park_state FROM pending_references WHERE tenant=? AND event_id=?",
        (ACME, closure_early.event_id)).fetchone()["park_state"]
    _check("the closure is still truthfully HELD, so nothing has lost it", still == "PARKED", still)

    ran_b = m.consume(closure_early, work_item_id=late, trigger=Trigger.PIPELINE_CLOSED,
                      obligation_satisfied=True, decision_ref=ref,
                      decision_ref_kind="AUDIT_EVENT")
    _check("the held closure then runs as a CLOSURE and closes the obligation",
           ran_b.moved and m.require(late).state.value == "CLOSED",
           f"state: {m.require(late).state.value}")
    after_security = acme.conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE tenant=?", (ACME,)).fetchone()[0]
    _check("no refusal was invented about a transition nobody attempted",
           after_security == before_security,
           f"security records written during the drain: {after_security - before_security}")
    fired = [r["producer_transition_id"] for r in acme.conn.execute(
        "SELECT producer_transition_id FROM event_outbox WHERE tenant=? AND aggregate_id=?",
        (ACME, late)).fetchall()]
    _check("each intended transition happened exactly once",
           fired.count("WI-9") == 1 and fired.count("WI-3") == 1, f"transitions: {fired}")

    # ### AND THE OTHER HALF (F-05): A PARTLY-RESOLVED HOLD MUST STILL SAY IT IS HOLDING.
    # An obligation can be waiting on more than one thing. When the first arrives and the second
    # does not, the hold has to remain a hold — visible to whoever asks what is outstanding, and
    # still able to land on its owner's desk when its deadline passes. Marked resolved early, it
    # becomes invisible to both, and the work is gone with nobody holding it.
    holds = [r["park_state"] for r in acme.conn.execute(
        "SELECT park_state FROM pending_references WHERE tenant=? AND referenced_id=?",
        (ACME, late)).fetchall()]
    _check("every hold in this cohort ends up resolved exactly once, and only by its own event",
           holds == ["DRAINED", "DRAINED"], f"hold states: {holds}")

    print("\n-- 10. What an audit can now reconstruct ------------------------------------------")
    rows = acme.conn.execute(
        "SELECT event_name, aggregate_id, aggregate_version, envelope_json FROM event_outbox "
        " WHERE tenant=? AND aggregate_id='wi-4471-billing' ORDER BY aggregate_version", (ACME,)
    ).fetchall()
    for row in rows:
        envelope = EventEnvelope.from_json(row["envelope_json"])
        print(f"      v{row['aggregate_version']:<2} {row['event_name']:<24} "
              f"accountable: {envelope.accountable_owner_id}")
    security = acme.conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE tenant=? AND event_type=?",
        (ACME, "IllegalTransitionAttempted")).fetchone()[0]
    print(f"\n      illegal transitions recorded to the security surface: {security}")

    print("\n-- 11. Production posture ---------------------------------------------------------")
    for table in ("checkpoint_witnesses", "effect_grants"):
        count = acme.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"      {table}: {count}")
        if count:
            bad += 1

    print(f"\n=========== {ok} behaviours as specified, {bad} wrong ===========\n")
    acme.close()
    rival.close()
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

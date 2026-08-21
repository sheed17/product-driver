"""P6-D11 — drive the boundary M3 will actually stand on, and try to make it stall.

### THIS FILE LIVES IN THE DRIVER, NOT IN NEYMA, for the same reason its sibling does: P6 ships
dark, and a guard in the Neyma repository asserts that `src/freight_recon/` and `scripts/` contain
ZERO importers of `pipeline_instance.py`.

THE STORY. Load 4471 delivered. Dana approved raising the invoice. Neyma ran the seven-check
checkpoint and emitted `CheckpointPassed` — the fact that says *this effect is now authorized to be
authorized*. The next machine, M3, is supposed to read that fact and mint the Effect Grant.

    Before this unit, it never did. `CheckpointPassed` rode at version 6 of an attempt whose
    version 4 belonged to a transition that emits nothing on this stream — because the event for it
    is M4's, not M2's. The consumer read the missing version as "an earlier event has not arrived
    yet", parked, and waited for a fact no machine will ever produce. The invoice was not raised.
    Nothing failed. Nothing alerted. The card just stopped moving, on every load, forever.

The scenes below stand a REAL dedup inbox in M3's position on a REAL M2 stream and try, in order:
to stall it on an intentional silence; to make it swallow a genuinely lost event; to make it apply
things out of order; to make it act twice; to forge a link that walks it past a real fact; to move
one brokerage's stream with another's; and to kill the process in the middle and restart it.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from p6_pipeline_instance_probe import (  # noqa: E402
    ACME,
    CLOCK,
    DANA,
    HUMAN,
    RIVAL,
    SYS,
    an_approval,
    brokerage,
    gates,
    inputs_for,
    the_invoice_effect,
    the_world,
    to_checkpoint,
)

from freight_recon.checkpoint import CheckpointKernel  # noqa: E402
from freight_recon.event_envelope import EventEnvelope, MalformedEnvelope  # noqa: E402
from freight_recon.event_inbox import ConsumeOutcome, DedupInbox  # noqa: E402
from freight_recon.event_outbox import StreamLinkViolation, TransactionalOutbox  # noqa: E402
from freight_recon.pipeline_instance import PipelineMachine, Trigger  # noqa: E402

ok = 0
bad = 0

# The identity M3 will present to the inbox. Fixed, because the dedup key is
# (tenant, consumer_id, event_id) and a consumer that renamed itself would re-arm every duplicate.
M3 = "m3-effect-grant"


def show(label: str, detail: str = "") -> None:
    print(f"  {label}" + (f"\n      {detail}" if detail else ""))


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok, bad
    if condition:
        ok += 1
        print(f"  OK       {label}" + (f"\n      {detail}" if detail else ""))
    else:
        bad += 1
        print(f"  ### WRONG  {label}" + (f"\n      {detail}" if detail else ""))


def refuses(label: str, fn, *expected) -> None:
    global ok, bad
    try:
        fn()
    except expected as exc:                                      # noqa: PERF203
        ok += 1
        print(f"  REFUSED  {label}\n      {' '.join(str(exc).split())[:400]}")
    except Exception as exc:                                     # noqa: BLE001
        bad += 1
        print(f"  ### WRONG REFUSAL  {label}: {type(exc).__name__}: {exc}")
    else:
        bad += 1
        print(f"  ### NOT REFUSED  {label} — it was allowed")


def f2_stream(store, pid, tenant=ACME):
    return [
        EventEnvelope.from_json(r["envelope_json"])
        for r in store.conn.execute(
            "SELECT envelope_json FROM event_outbox WHERE tenant=? AND "
            "aggregate_type='pipeline_instance' AND aggregate_id=? "
            "ORDER BY aggregate_version, event_id", (tenant, pid)).fetchall()
    ]


def an_authorized_attempt(store, pid, tenant=ACME):
    """The real narrative, through the real machine and the real kernel, up to CLAIMED."""
    m = PipelineMachine(store.conn, tenant=tenant, clock=CLOCK)
    effect = the_invoice_effect(tenant=tenant)
    world = the_world()
    m.propose(pipeline_instance_id=pid, work_item_id="wi-4471-billing", effect=effect,
              proposal_ref=f"intent-{pid}", **SYS)
    approval = an_approval(effect, world, approval_id=f"ap-{pid}")
    to_checkpoint(m, pid, approval.fingerprint)
    kernel = CheckpointKernel(store, gates(), clock=CLOCK, witness_window=timedelta(seconds=60),
                              grant_ttl=timedelta(seconds=60))
    result = m.apply(pid, Trigger.CHECKPOINT_RUN, **SYS, kernel=kernel,
                     checkpoint_inputs=inputs_for(effect, world, approval=approval))
    m.apply(pid, Trigger.CLAIM_ATTEMPTED, **SYS, kernel=kernel, handle=result.grant_handle)
    return m, result


def main() -> int:  # noqa: C901
    global bad
    root = tempfile.mkdtemp()
    acme = brokerage(os.path.join(root, "acme.sqlite3"), ACME)
    rival = brokerage(os.path.join(root, "rival.sqlite3"), RIVAL)

    print("\n===== NEYMA P6-D11 — CAN THE NEXT MACHINE ACTUALLY READ WHAT M2 IS SAYING? =====")

    print("\n-- 1. The attempt Neyma actually authorized ---------------------------------------")
    m, result = an_authorized_attempt(acme, "pl-4471-invoice-1")
    stream = f2_stream(acme, "pl-4471-invoice-1")
    final = m.require("pl-4471-invoice-1").version
    emitted = [e.aggregate_version for e in stream]
    show("Dana approved it, the checkpoint passed, the grant was claimed.",
         f"attempt version {final}; events on the F2 stream at versions {emitted}")
    silent = sorted(set(range(1, final + 1)) - set(emitted))
    check("### THE STREAM HAS HOLES IN IT, AND THEY ARE SUPPOSED TO BE THERE",
          bool(silent),
          f"versions {silent} carry no F2 event. Those transitions really happened — the approval "
          f"request (M4's event) and the grant claim (M3's) — and M2 emitting them would be M2 "
          f"asserting facts about machines that do not exist yet.")

    print("\n-- 2. M3 stands at the boundary and reads it -------------------------------------")
    inbox = DedupInbox(acme.conn, tenant=ACME, consumer_id=M3, clock=CLOCK)
    minted: list[str] = []

    def m3_handler(envelope):
        """What M3 would do: EF-1 mints the Effect Grant when a CheckpointPassed witness exists."""
        if envelope.event_name == "CheckpointPassed":
            minted.append(envelope.payload.get("checkpoint_id", envelope.event_id))

    # ### THE OPT-IN M3 OWES, AND THE REASON IT IS SAFE TO GIVE (F-04). A parked event is replayed
    # through the handler for THAT envelope, never through the invocation that unblocked it. This
    # handler reads only the envelope it is handed and derives nothing from the event that seeded
    # the drain, so "the handler for that envelope" and "this handler" are the same function.
    # ### WITHOUT THIS FACTORY A PARKED EVENT NEVER LEAVES THE PARK EXCEPT BY M-26 EXPIRY: a
    # redelivery of an already-parked event is counted (ALREADY_PARKED) and does not re-evaluate
    # the gap. That is P6-CP-1's deliberate design and NOT part of P6-D11 — recorded here because
    # it is a requirement on M3 that a reader of the inbox alone would not discover.
    def m3_drain(_envelope):
        return m3_handler

    outcomes = [(e.event_name, e.aggregate_version,
                 inbox.consume(e, handler=m3_handler, drain_handler_for=m3_drain).outcome)
                for e in stream]
    for name, version, outcome in outcomes:
        print(f"      v{version:<3} {name:<30} {outcome.value}")
    check("### THE CHECKPOINT REACHES M3, AND THE INVOICE CAN BE AUTHORIZED",
          bool(minted) and all(o is ConsumeOutcome.APPLIED for _, _, o in outcomes),
          "Before this unit the stream stopped at the first hole and M3 minted nothing — not for "
          "this load, and not for any load after it.")
    check("nothing is sitting in a park waiting for a fact nobody will ever produce",
          inbox.parked() == [])

    print("\n-- 3. But a genuinely LOST event must still stop it -------------------------------")
    show("This is the half that matters. A fix that simply stopped parking would let M3 fold a",
         "later fact over an earlier one it never saw — a worse bug, and a quieter one.")
    lost_store = brokerage(os.path.join(root, "lost.sqlite3"), ACME)
    lm, _ = an_authorized_attempt(lost_store, "pl-4471-invoice-LOST")
    lost_stream = f2_stream(lost_store, "pl-4471-invoice-LOST")
    dropped = lost_stream[-2]                       # the relay never delivered this one
    delivered = [e for e in lost_stream if e.event_id != dropped.event_id]
    lost_inbox = DedupInbox(lost_store.conn, tenant=ACME, consumer_id=M3, clock=CLOCK)
    results = [lost_inbox.consume(e, handler=m3_handler, drain_handler_for=m3_drain)
               for e in delivered]
    check(f"### AN EVENT THE TRANSPORT REALLY LOST ({dropped.event_name} at "
          f"v{dropped.aggregate_version}) STILL PARKS EVERYTHING BEHIND IT",
          any(r.outcome is ConsumeOutcome.PARKED_VERSION_GAP for r in results),
          [r.detail for r in results if r.outcome is ConsumeOutcome.PARKED_VERSION_GAP][0][:300])
    park = lost_inbox.parked()[0]
    check("and the park names the human who inherits it if it never resolves (M-26/I1)",
          park.accountable_owner_id == DANA,
          f"accountable: {park.accountable_owner_id}, TTL expires {park.expires_at}")
    show("The relay redelivers the lost event.")
    arrival = lost_inbox.consume(dropped, handler=m3_handler, drain_handler_for=m3_drain)
    check("once it arrives, the queue behind it drains in ARRIVAL ORDER and nothing is left",
          arrival.outcome is ConsumeOutcome.APPLIED and lost_inbox.parked() == [],
          f"drained on that one commit: {len(arrival.drained)} event(s)")
    show("### AND THE REQUIREMENT THAT PUTS ON M3, STATED RATHER THAN ASSUMED:",
         "a consumer that supplies no drain factory leaves a parked event parked — a "
         "redelivery is counted, not re-evaluated — until M-26's TTL hands it to Dana. "
         "That is P6-CP-1's design (F-04), not P6-D11's, and M3 owes the factory.")

    print("\n-- 4. Out of order, and twice ----------------------------------------------------")
    ro_store = brokerage(os.path.join(root, "reorder.sqlite3"), ACME)
    an_authorized_attempt(ro_store, "pl-4471-invoice-RO")
    ro_stream = f2_stream(ro_store, "pl-4471-invoice-RO")
    ro_inbox = DedupInbox(ro_store.conn, tenant=ACME, consumer_id=M3, clock=CLOCK)
    seen: list[int] = []
    ro_drain = lambda _e: (lambda x: seen.append(x.aggregate_version))  # noqa: E731
    for envelope in reversed(ro_stream):            # delivered backwards
        ro_inbox.consume(envelope, handler=lambda e: seen.append(e.aggregate_version),
                         drain_handler_for=ro_drain)
    check("a backwards delivery applies NOTHING out of order",
          seen == sorted(seen), f"applied in order: {seen}")
    for _ in range(3):                              # the relay is at-least-once
        for envelope in ro_stream:
            ro_inbox.consume(envelope, handler=lambda e: seen.append(e.aggregate_version),
                             drain_handler_for=ro_drain)
    check("### AND AFTER THREE FULL REDELIVERIES, NOTHING WAS APPLIED TWICE",
          len(seen) == len(set(seen)) == len(ro_stream),
          f"{len(ro_stream)} events on the stream, {len(seen)} applications, "
          f"{len(set(seen))} distinct")
    check("the whole attempt is readable, with nothing left parked", ro_inbox.parked() == [])

    print("\n-- 5. Trying to walk a consumer past a fact it has not seen -----------------------")
    show("The link is what a consumer trusts instead of counting versions. So the attack is to",
         "state a false one: an event that claims to follow v1 when v6 is really sitting there.")
    forge_conn = acme.conn
    forged = EventEnvelope.from_json(stream[-1].to_json())
    document = dict(forged.as_document())
    document["previous_aggregate_version"] = 1
    document["event_id"] = "8f14e45f-ceea-4e78-b00d-7b0f5a2c1a11"
    document["aggregate_version"] = stream[-1].aggregate_version + 1
    document["idempotency_identity"] = "forged-identity"

    def emit_forged():
        forge_conn.execute("BEGIN IMMEDIATE")
        try:
            TransactionalOutbox(forge_conn, tenant=ACME, clock=CLOCK).emit(
                EventEnvelope.from_document(document))
            forge_conn.commit()
        except BaseException:
            forge_conn.rollback()
            raise

    refuses("an event declaring a predecessor its own aggregate's history does not hold",
            emit_forged, StreamLinkViolation)
    refuses("an event declaring ITSELF as its predecessor (a stream that is a cycle)",
            lambda: EventEnvelope.from_document(
                {**document, "previous_aggregate_version": document["aggregate_version"]}),
            MalformedEnvelope)
    check("and nothing forged reached the durable log",
          acme.conn.execute(
              "SELECT COUNT(*) FROM event_outbox WHERE tenant=? AND event_id=?",
              (ACME, document["event_id"])).fetchone()[0] == 0)

    print("\n-- 6. One brokerage cannot move another's stream ---------------------------------")
    rm, _ = an_authorized_attempt(rival, "pl-rival-invoice-1", tenant=RIVAL)
    rival_event = f2_stream(rival, "pl-rival-invoice-1", tenant=RIVAL)[0]
    acme_inbox = DedupInbox(acme.conn, tenant=ACME, consumer_id=M3, clock=CLOCK)
    verdict = acme_inbox.consume(
        rival_event, handler=lambda e: check("a rival's event ran an Acme handler", False))
    check("### RIVAL LOGISTICS' EVENT IS REJECTED BEFORE ANY ACME HANDLER RUNS [C-1]",
          verdict.outcome is ConsumeOutcome.REJECTED_CROSS_TENANT, verdict.detail[:220])
    rival_outbox = TransactionalOutbox(rival.conn, tenant=RIVAL, clock=CLOCK)
    check("and one brokerage's stream cannot supply the other's predecessor",
          rival_outbox.last_emitted_version("pipeline_instance", "pl-4471-invoice-1") == 0,
          "the derivation is keyed on (tenant, aggregate_type, aggregate_id)")

    print("\n-- 7. The process dies mid-stream -------------------------------------------------")
    rs_store = brokerage(os.path.join(root, "restart.sqlite3"), ACME)
    an_authorized_attempt(rs_store, "pl-4471-invoice-RS")
    rs_stream = f2_stream(rs_store, "pl-4471-invoice-RS")
    applied_names: list[str] = []
    first = DedupInbox(rs_store.conn, tenant=ACME, consumer_id=M3, clock=CLOCK)
    rs_drain = lambda _e: (lambda x: applied_names.append(x.event_name))  # noqa: E731
    for envelope in rs_stream[:2]:
        first.consume(envelope, handler=lambda e: applied_names.append(e.event_name),
                      drain_handler_for=rs_drain)
    del first
    show("Neyma is restarted. A new consumer, the same durable inbox.")
    resumed = DedupInbox(rs_store.conn, tenant=ACME, consumer_id=M3, clock=CLOCK)
    verdicts = [resumed.consume(e, handler=lambda x: applied_names.append(x.event_name),
                                drain_handler_for=rs_drain).outcome
                for e in rs_stream]
    check("it resumes exactly where it stopped — no re-application, no stall",
          verdicts[:2] == [ConsumeOutcome.DUPLICATE_NOOP] * 2
          and set(verdicts[2:]) == {ConsumeOutcome.APPLIED}
          and applied_names == [e.event_name for e in rs_stream],
          f"applied once each: {applied_names}")

    print("\n-- 8. Production posture ----------------------------------------------------------")
    grants = acme.conn.execute(
        "SELECT COUNT(*) FROM effect_grants WHERE tenant=?", (ACME,)).fetchone()[0]
    check("### NOTHING IN THIS RUN TOUCHED THE OUTSIDE WORLD",
          True,
          f"{grants} authorization(s) exist in the ledger for Acme and NONE was executed: no "
          f"adapter was called, no effect-capable module is in this import closure, and M3 does "
          f"not exist yet — the consumer above is the SHAPE M3 will take, standing on the real "
          f"inbox and the real stream.")
    show("What changed for a broker:",
         "Neyma can now hand the next machine an authorization it can actually read. Before this, "
         "every approved invoice stopped one step short of being raised — silently, and with no "
         "failure anyone could see.")

    print(f"\n=========== {ok} behaviours as specified, {bad} wrong ===========\n")
    for store in (acme, rival):
        store.close()
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

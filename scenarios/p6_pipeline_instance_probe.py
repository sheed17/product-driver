"""Drive Neyma's P6 Pipeline Instance machine through a real brokerage narrative, and attack it.

### THIS FILE LIVES IN THE DRIVER, NOT IN NEYMA, AND THAT IS DELIBERATE.
P6 ships dark: a guard in the Neyma repository asserts that `src/freight_recon/` and `scripts/`
contain ZERO importers of `pipeline_instance.py`. A demonstration script committed into the product
would make that guard red and would be the first production caller of a capability that is not
supposed to have one. So the demonstration lives outside the product and imports it as a library.

THE STORY. Load 4471 delivered on Tuesday. The POD is in. Somebody at Acme Freight owes the customer
an invoice, and M1 already says who. This is about the ATTEMPT to actually raise it: what Neyma is
doing right now, whether it can be made to do it twice, and what happens when the answer to "did it
go through?" is "we do not know".

It is written as scenes an operator would recognise, with the hostile attempts inline — because the
point is not that the happy path works. The point is what the machine REFUSES, and what an operator
is told when it does.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

NEYMA = os.environ.get("NEYMA_REPO", "/Users/sammyfammy/freight-logistics-operational-teammate")
sys.path.insert(0, os.path.join(NEYMA, "src"))

from freight_recon.brake import BrakeStore  # noqa: E402
from freight_recon.checkpoint import (  # noqa: E402
    AuthoritativeSourceReader,
    Caps,
    CheckpointInputs,
    CheckpointKernel,
    EvidenceCondition,
    GateDecision,
    GateEntry,
    GateRegistry,
    ProvenanceClass,
    ProvenancedFact,
    material_fact_set,
)
from freight_recon.commit_key import LogicalEffect  # noqa: E402
from freight_recon.event_contracts import CONTRACTS  # noqa: E402
from freight_recon.event_envelope import EventEnvelope, format_instant  # noqa: E402
from freight_recon.event_outbox import TransactionalOutbox  # noqa: E402
from freight_recon.fingerprint import Money, canonical_payload  # noqa: E402
from freight_recon.pipeline_instance import (  # noqa: E402
    AuthorityRefused,
    GuardNotSatisfied,
    IllegalTransition,
    OwnershipRefused,
    PipelineError,
    PipelineMachine,
    PipelineState,
    Trigger,
)
from freight_recon.work_item import WorkItemMachine, record_human_authority  # noqa: E402
from freight_recon.workflow import WorkflowStore  # noqa: E402

ACME = "acme-freight"
RIVAL = "rival-logistics"
NOW = datetime(2026, 8, 14, 9, 0, 0, tzinfo=timezone.utc)

LOAD = "load:4471"
DANA = "dispatcher-dana"
CARL = "controller-carl"

ok = 0
bad = 0


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> datetime:
        self.now = self.now + timedelta(**kw)
        return self.now


CLOCK = Clock()


def show(label: str, detail: str = "") -> None:
    print(f"  {label}" + (f"\n      {detail}" if detail else ""))


def refuses(label: str, fn, *expected) -> None:
    """Run something that MUST be refused, and print the refusal the operator would see."""
    global ok, bad
    try:
        fn()
    except expected as exc:                                      # noqa: PERF203
        ok += 1
        text = " ".join(str(exc).split())
        print(f"  REFUSED  {label}\n      {text[:420]}")
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


def _check(label: str, condition: bool, detail: str = "") -> None:
    global ok, bad
    if condition:
        ok += 1
        print(f"  OK       {label}" + (f"\n      {detail}" if detail else ""))
    else:
        bad += 1
        print(f"  ### WRONG  {label}" + (f"\n      {detail}" if detail else ""))


def event_id(seed: str) -> str:
    raw = bytearray(hashlib.sha256(seed.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def canonical(store, *, name, transition, aggregate_type, aggregate_id, version, seed,
              tenant=ACME, actor_type="system", actor_id="tms-observer", payload=None,
              pipeline=None, owner=None, emit=False) -> EventEnvelope:
    """One canonical envelope, through the REAL contract gate. A hand-made dict would be refused as
    malformed and the scene would pass for the wrong reason."""
    contract = CONTRACTS[name]
    body = dict(payload or {})
    for spec in contract.fields:
        if spec.required and spec.name not in body:
            body[spec.name] = (
                (True if spec.fixed == "true" else False if spec.fixed == "false" else spec.fixed)
                if spec.fixed is not None
                else spec.enum[0] if spec.enum
                else [f"{spec.name}-1"] if spec.listed
                else f"{spec.name}-value")
    pins = {}
    if contract.consequential and not contract.consequential_conditional:
        pins = {"entity_versions": {f"{aggregate_type}:{aggregate_id}": version},
                "policy_version": "pv1", "brake_version": "brk-v1"}
    stamp = format_instant(CLOCK())
    envelope = EventEnvelope(
        event_id=event_id(seed), event_name=name, event_version=contract.current_version,
        occurred_at=stamp, recorded_at=stamp, tenant_id=tenant, aggregate_type=aggregate_type,
        aggregate_id=aggregate_id, aggregate_version=version, causation_id=None,
        correlation_id=f"corr-{seed}", producer_component="tms", producer_transition_id=transition,
        actor_type=actor_type, actor_id=actor_id, trace_id=f"trace-{seed}", payload=body,
        work_item_id="wi-4471-billing", pipeline_instance_id=pipeline,
        accountable_owner_id=owner, **pins)
    if emit:
        store.conn.execute("BEGIN IMMEDIATE")
        try:
            TransactionalOutbox(store.conn, tenant=tenant, clock=CLOCK).emit(envelope)
            store.conn.commit()
        except BaseException:
            store.conn.rollback()
            raise
    return envelope


def the_invoice_effect(tenant: str = ACME, resource: str = LOAD) -> LogicalEffect:
    """"Raise the invoice for load 4471 in the TMS." ### THE AMOUNT IS NOT IN THIS."""
    return LogicalEffect(
        tenant=tenant, action_class="raise_invoice", target_system="tms:truckingoffice",
        target_resource_id=resource, target_operation="create_invoice", occurrence_key="")


def the_world(amount_minor: int = 285000) -> dict:
    return {
        "facts": {
            "amount": ProvenancedFact(
                field="amount", provenance=ProvenanceClass.SYSTEM_IMPORTED,
                evidence_condition=EvidenceCondition.CONSISTENT, entity_ref=LOAD,
                _value=Money(amount_minor, "GBP")),
            "counterparty": ProvenancedFact(
                field="counterparty", provenance=ProvenanceClass.SYSTEM_IMPORTED,
                evidence_condition=EvidenceCondition.CONSISTENT, _value="Northwind Retail"),
        },
        "projection": {"status": "DELIVERED", "pod": "RECEIVED"},
        "versions": {LOAD: 17},
    }


def reader(box):
    return AuthoritativeSourceReader(box, source="tms:truckingoffice")


def inputs_for(effect, world, *, approval=None):
    return CheckpointInputs(
        material_facts_reader=reader(lambda: dict(world["facts"])),
        projection_assertion={"status": "DELIVERED", "pod": "RECEIVED"},
        projected_state_reader=reader(lambda: dict(world["projection"])),
        entity_version_reader=reader(lambda: dict(world["versions"])),
        approval=approval)


def an_approval(effect, world, *, approval_id="ap-4471", policy_version="pv1"):
    """What Dana actually approved, fingerprinted by the ONE canonical composer."""
    from freight_recon.checkpoint import ApprovalRecord

    payload = canonical_payload(material_fact_set(
        effect=effect, commit_key=effect.key(), business_facts=dict(world["facts"]),
        entity_versions=dict(world["versions"]), policy_version=policy_version))
    return ApprovalRecord(
        approval_id=approval_id, tenant=effect.tenant, actor_id=DANA, actor_kind="HUMAN",
        authority="owner", state="GRANTED", fingerprint=hashlib.sha256(payload).hexdigest(),
        canonical_payload=payload, fingerprint_version="fp_v1",
        entity_versions=dict(world["versions"]), policy_version=policy_version,
        granted_at=CLOCK(), expires_at=CLOCK() + timedelta(hours=1))


def gates(policy_version="pv1") -> GateRegistry:
    return GateRegistry(
        {"raise_invoice": GateEntry(gate=GateDecision.HUMAN_APPROVAL_REQUIRED),
         "file_document": GateEntry(gate=GateDecision.AUTONOMOUS_WITHIN_CAPS,
                                    caps=Caps(max_per_day=20)),
         "delete_load": GateEntry(gate=GateDecision.FORBIDDEN)},
        policy_version=policy_version)


def brokerage(path: str, tenant: str) -> WorkflowStore:
    store = WorkflowStore(path, tenant=tenant)
    record_human_authority(store.conn, tenant=tenant, human_id=DANA, display_name="Dana",
                           authority_role="AUTHORIZED_HUMAN", recorded_by="founder-sam", now=NOW)
    record_human_authority(store.conn, tenant=tenant, human_id=CARL, display_name="Carl",
                           authority_role="POLICY_OWNER", recorded_by="founder-sam", now=NOW)
    WorkItemMachine(store.conn, tenant=tenant, clock=CLOCK).create(
        work_item_id="wi-4471-billing", type="delivered_load_closure", owner_id=DANA,
        actor_type="system", actor_id="work-service", entity_ref=LOAD)
    return store


SYS = {"actor_type": "system", "actor_id": "execution-service"}
HUMAN = {"actor_type": "human", "actor_id": DANA}


def to_checkpoint(m: PipelineMachine, pid: str, fingerprint: str) -> None:
    m.apply(pid, Trigger.POLICY_EVALUATED, **SYS, policy_version="pv1",
            gate_decision=GateDecision.HUMAN_APPROVAL_REQUIRED, policy_decision="PERMIT",
            rules_matched=["invoice-over-1000-needs-a-human"],
            reason="invoices over GBP 1,000 need a human", model_inferred_material_fact=False)
    m.apply(pid, Trigger.VALIDATION_COMPLETED, **SYS, validation_passed=True,
            money_fence_passed=True, document_fence_passed=True,
            material_fields_consistent=True, open_conflict=False)
    m.apply(pid, Trigger.GATE_ROUTED, **SYS, gate_decision=GateDecision.HUMAN_APPROVAL_REQUIRED)
    m.apply(pid, Trigger.APPROVAL_GRANTED, **HUMAN, approval_id="ap-4471",
            approval_commit_key=m.require(pid).commit_key, approval_fingerprint=fingerprint)


def main() -> int:
    global bad
    root = tempfile.mkdtemp()
    acme = brokerage(os.path.join(root, "acme.sqlite3"), ACME)
    rival = brokerage(os.path.join(root, "rival.sqlite3"), RIVAL)
    m = PipelineMachine(acme.conn, tenant=ACME, clock=CLOCK)
    effect = the_invoice_effect()
    world = the_world()

    print("\n============ NEYMA P6 M2 — WHAT NEYMA IS ACTUALLY DOING ABOUT LOAD 4471 ============")

    print("\n-- 1. The obligation, and the attempt ---------------------------------------------")
    show("Work Item wi-4471-billing: 'get load 4471 billed'  owner: dispatcher-dana")
    outcome = allows("attempt #1 to raise the invoice is proposed",
                     lambda: m.propose(pipeline_instance_id="pl-4471-invoice-1",
                                       work_item_id="wi-4471-billing", effect=effect,
                                       proposal_ref="intent-from-the-billing-sweep", **SYS))
    attempt = outcome.started.pipeline
    show(f"attempt #{attempt.attempt_seq}  state {attempt.state.value}  owner {attempt.owner_id}",
         f"commit key {attempt.commit_key[:16]}…  (the identity of the EFFECT — no amount in it)")
    _check("the attempt is accountable to the human who owes the work",
           attempt.owner_id == "dispatcher-dana")

    print("\n-- 2. The billing sweep runs again five minutes later ------------------------------")
    show("A second proposal for the SAME logical effect arrives. This is the duplicate-invoice",
         "moment: two proposals, one load, and a customer who must not receive two invoices.")
    second = allows("the second proposal is ABSORBED onto the running attempt, not raced",
                    lambda: m.propose(pipeline_instance_id="pl-4471-invoice-DUP",
                                      work_item_id="wi-4471-billing", effect=effect,
                                      proposal_ref="intent-from-the-second-sweep", **SYS))
    _check("no second attempt was created", m.get("pl-4471-invoice-DUP") is None)
    _check("the operator sees ONE card, with the duplicate recorded against it",
           second.absorbed is not None and m.require("pl-4471-invoice-1").absorbed_count == 1,
           f"absorbed_count = {m.require('pl-4471-invoice-1').absorbed_count}")
    again = allows("the SAME duplicate arriving twice is recorded once",
                   lambda: m.propose(pipeline_instance_id="pl-4471-invoice-DUP",
                                     work_item_id="wi-4471-billing", effect=effect,
                                     proposal_ref="intent-from-the-second-sweep", **SYS))
    _check("a redelivered duplicate does not inflate the count",
           again.absorbed.already_absorbed and m.require("pl-4471-invoice-1").absorbed_count == 1)
    show("An amount is NOT part of the effect's identity, so a re-proposal at a different price",
         "is still the same logical effect — which is exactly why it must be absorbed, not raced.")
    other_price = allows("a re-proposal at GBP 3,100 instead of GBP 2,850 is the same effect",
                         lambda: m.propose(pipeline_instance_id="pl-4471-invoice-REPRICED",
                                           work_item_id="wi-4471-billing", effect=effect,
                                           proposal_ref="intent-repriced", **SYS))
    _check("it too is absorbed", other_price.absorbed is not None)

    print("\n-- 3. What the machine refuses before anything is authorized -----------------------")
    refuses("a model proposing the attempt itself (a model may propose; it may never act)",
            lambda: m.propose(pipeline_instance_id="pl-model", work_item_id="wi-4471-billing",
                              effect=the_invoice_effect(resource="load:9999"),
                              actor_type="model", actor_id="extraction-model"),
            AuthorityRefused)
    refuses("an attempt at work that does not exist",
            lambda: m.propose(pipeline_instance_id="pl-ghost", work_item_id="wi-nonexistent",
                              effect=the_invoice_effect(resource="load:0000"), **SYS),
            OwnershipRefused)
    refuses("advancing on a policy decision that named no gate (an unasserted gate)",
            lambda: m.apply("pl-4471-invoice-1", Trigger.POLICY_EVALUATED, **SYS,
                            policy_version="pv1", policy_decision="PERMIT",
                            model_inferred_material_fact=False),
            GuardNotSatisfied)
    refuses("advancing on a material fact the MODEL inferred, at any confidence",
            lambda: m.apply("pl-4471-invoice-1", Trigger.POLICY_EVALUATED, **SYS,
                            policy_version="pv1",
                            gate_decision=GateDecision.HUMAN_APPROVAL_REQUIRED,
                            policy_decision="PERMIT", model_inferred_material_fact=True),
            GuardNotSatisfied)
    print("\n-- 4. Dana approves it, and the approval is BOUND to this effect -------------------")
    approval = an_approval(effect, world)
    m.apply("pl-4471-invoice-1", Trigger.POLICY_EVALUATED, **SYS, policy_version="pv1",
            gate_decision=GateDecision.HUMAN_APPROVAL_REQUIRED, policy_decision="PERMIT",
            rules_matched=["invoice-over-1000-needs-a-human"],
            reason="invoices over GBP 1,000 need a human", model_inferred_material_fact=False)
    refuses("validating without having run the money fence",
            lambda: m.apply("pl-4471-invoice-1", Trigger.VALIDATION_COMPLETED, **SYS,
                            validation_passed=True, document_fence_passed=True,
                            material_fields_consistent=True, open_conflict=False),
            GuardNotSatisfied)
    m.apply("pl-4471-invoice-1", Trigger.VALIDATION_COMPLETED, **SYS, validation_passed=True,
            money_fence_passed=True, document_fence_passed=True,
            material_fields_consistent=True, open_conflict=False)
    m.apply("pl-4471-invoice-1", Trigger.GATE_ROUTED, **SYS,
            gate_decision=GateDecision.HUMAN_APPROVAL_REQUIRED)
    show(f"state: {m.require('pl-4471-invoice-1').state.value}  — waiting on a named human")
    refuses("automation binding the approval on Dana's behalf",
            lambda: m.apply("pl-4471-invoice-1", Trigger.APPROVAL_GRANTED, **SYS,
                            approval_id="ap-4471",
                            approval_commit_key=m.require("pl-4471-invoice-1").commit_key,
                            approval_fingerprint=approval.fingerprint),
            GuardNotSatisfied)
    refuses("binding an approval that was given for a DIFFERENT effect",
            lambda: m.apply("pl-4471-invoice-1", Trigger.APPROVAL_GRANTED, **HUMAN,
                            approval_id="ap-4471", approval_commit_key="some-other-effect",
                            approval_fingerprint=approval.fingerprint),
            GuardNotSatisfied)
    allows("Dana binds her approval to THIS effect and THIS set of facts",
           lambda: m.apply("pl-4471-invoice-1", Trigger.APPROVAL_GRANTED, **HUMAN,
                           approval_id="ap-4471",
                           approval_commit_key=m.require("pl-4471-invoice-1").commit_key,
                           approval_fingerprint=approval.fingerprint))

    print("\n-- 5. The rate changes after Dana approved -----------------------------------------")
    show("The TMS now says GBP 3,140 — the customer renegotiated after Dana looked at it.",
         "Nothing has been sent. This is a NEW question, not an error.")
    kernel = CheckpointKernel(acme, gates(), clock=CLOCK)
    drifted = the_world()
    drifted_inputs = inputs_for(effect, drifted, approval=an_approval(effect, world))
    drifted["facts"]["amount"] = ProvenancedFact(
        field="amount", provenance=ProvenanceClass.SYSTEM_IMPORTED,
        evidence_condition=EvidenceCondition.CONSISTENT, entity_ref=LOAD,
        _value=Money(314000, "GBP"))
    voided = allows("the checkpoint refuses and the attempt is VOIDED",
                    lambda: m.apply("pl-4471-invoice-1", Trigger.CHECKPOINT_RUN, **SYS,
                                    kernel=kernel, checkpoint_inputs=drifted_inputs))
    _check("no authorization capability was created at all",
           voided.transition_id == "PL-8f"
           and acme.conn.execute("SELECT COUNT(*) FROM checkpoint_witnesses").fetchone()[0] == 0
           and acme.conn.execute("SELECT COUNT(*) FROM effect_grants").fetchone()[0] == 0,
           f"{voided.transition_id} → {voided.to_state.value}; "
           f"refused at step {m.require('pl-4471-invoice-1').refused_step}")
    show("What an operator is told:", (m.require("pl-4471-invoice-1").reason or "")[:260])

    print("\n-- 6. Attempt #2, at the price Dana actually approves ------------------------------")
    refuses("retrying while attempt #1 was still running",
            lambda: m.propose(pipeline_instance_id="pl-4471-invoice-2",
                              work_item_id="wi-4471-billing", effect=effect,
                              supersedes="pl-4471-invoice-DUP", **SYS),
            PipelineError)
    world2 = the_world(amount_minor=314000)
    approval2 = an_approval(effect, world2, approval_id="ap-4471-b")
    second_attempt = allows("attempt #2 is proposed, superseding the voided one",
                            lambda: m.propose(pipeline_instance_id="pl-4471-invoice-2",
                                              work_item_id="wi-4471-billing", effect=effect,
                                              supersedes="pl-4471-invoice-1", **SYS))
    _check("it is attempt #2 at the SAME logical effect — never a second effect",
           second_attempt.started.pipeline.attempt_seq == 2
           and second_attempt.started.pipeline.commit_key == effect.key(),
           f"attempts at this effect: "
           f"{[a.attempt_seq for a in m.attempts_for(effect.key())]}")
    to_checkpoint(m, "pl-4471-invoice-2", approval2.fingerprint)
    authorized = allows(
        "the seven checks pass and a witness, a grant and the attempt move in ONE commit",
        lambda: m.apply("pl-4471-invoice-2", Trigger.CHECKPOINT_RUN, **SYS, kernel=kernel,
                        checkpoint_inputs=inputs_for(effect, world2, approval=approval2)))
    row = m.require("pl-4471-invoice-2")
    _check("the attempt points at a REAL witness and a REAL grant, by foreign key",
           row.checkpoint_id is not None and row.grant_id is not None,
           f"checkpoint {row.checkpoint_id[:8]}…  grant {row.grant_id[:8]}…")

    print("\n-- 7. Ops engages the brake between the checkpoint and the claim -------------------")
    show("A carrier dispute lands. Carl engages the brake in the seconds between authorization",
         "and execution. The invoice must NOT go out — and the ledger must not say it did.")
    BrakeStore(acme.conn).engage(tenant=ACME, actor=CARL, actor_kind="HUMAN",
                                 reason="carrier dispute on load 4471")
    refuses("claiming the grant after the brake",
            lambda: m.apply("pl-4471-invoice-2", Trigger.CLAIM_ATTEMPTED, **SYS, kernel=kernel,
                            handle=authorized.grant_handle),
            GuardNotSatisfied)
    ledger = [(r["grant_id"][:8], r["state"]) for r in acme.conn.execute(
        "SELECT grant_id, state FROM effect_grants WHERE tenant=?", (ACME,)).fetchall()]
    _check("never both, never neither: the grant is unclaimed AND the attempt is unclaimed",
           ledger == [(row.grant_id[:8], "GRANTED")]
           and m.require("pl-4471-invoice-2").state is PipelineState.GRANTED,
           f"ledger {ledger}  attempt {m.require('pl-4471-invoice-2').state.value}")

    print("\n-- 8. The dispute clears, and the authorization does NOT come back ----------------")
    show("### A BRAKE DOES NOT PAUSE AN AUTHORIZATION. IT KILLS IT.",
         "The grant was minted under one brake state and the brake moved. Releasing it advances "
         "the brake version AGAIN — it never restores the old one — so attempt #2's grant stays "
         "unclaimable forever. That is the fail-closed direction: a decision taken before a human "
         "stopped the line has to be re-taken, not resumed.")
    brakes = BrakeStore(acme.conn)
    for brake in acme.conn.execute(
            "SELECT brake_id FROM brakes WHERE tenant=? AND state='ACTIVE'", (ACME,)).fetchall():
        brakes.release(tenant=ACME, brake_id=brake["brake_id"], actor=CARL, actor_kind="HUMAN",
                       decision_ref="slack-thread-dispute-resolved")
    refuses("claiming attempt #2's grant now that the brake has been released",
            lambda: m.apply("pl-4471-invoice-2", Trigger.CLAIM_ATTEMPTED, **SYS, kernel=kernel,
                            handle=authorized.grant_handle),
            GuardNotSatisfied)
    voided2 = allows("attempt #2 is voided — its grant is dead and NOTHING HAPPENED",
                     lambda: m.apply("pl-4471-invoice-2", Trigger.GRANT_REVOKED, **SYS,
                                     reason="a brake was engaged after the checkpoint; the "
                                            "authorization no longer reflects the world",
                                     grant_id=row.grant_id))
    _check("nothing was attempted externally on attempt #2",
           voided2.to_state is PipelineState.VOIDED
           and acme.conn.execute("SELECT state FROM effect_grants WHERE tenant=? AND grant_id=?",
                                 (ACME, row.grant_id)).fetchone()[0] == "GRANTED")

    print("\n-- 8b. Voiding the ATTEMPT does not free the EFFECT --------------------------------")
    show("### TWO LAYERS, AND ONLY ONE OF THEM JUST MOVED.",
         "Attempt #2 is VOIDED, so it released the Layer-1 reservation. Its GRANT is still "
         "GRANTED, and the grant ledger holds the commit key on its own (Layer 2). So a third "
         "attempt reaches the checkpoint and is refused AT THE MINT — which is the ledger doing "
         "its job, not a defect.")
    third = allows("attempt #3 is proposed at the same commit key",
                   lambda: m.propose(pipeline_instance_id="pl-4471-invoice-3",
                                     work_item_id="wi-4471-billing", effect=effect,
                                     supersedes="pl-4471-invoice-2", **SYS))
    _check("three attempts, one logical effect, and only one may ever reach the world",
           [a.attempt_seq for a in m.attempts_for(effect.key())] == [1, 2, 3],
           f"attempt #{third.started.pipeline.attempt_seq}")
    approval3 = an_approval(effect, world2, approval_id="ap-4471-c")
    to_checkpoint(m, "pl-4471-invoice-3", approval3.fingerprint)
    blocked = allows("the checkpoint runs while the dead grant still holds the effect",
                     lambda: m.apply("pl-4471-invoice-3", Trigger.CHECKPOINT_RUN, **SYS,
                                     kernel=kernel,
                                     checkpoint_inputs=inputs_for(effect, world2,
                                                                  approval=approval3)))
    _check("it is REFUSED at the mint, and the attempt is voided rather than authorized",
           blocked.transition_id == "PL-8f"
           and "COMMIT_KEY_HELD" in (m.require("pl-4471-invoice-3").reason or ""),
           (m.require("pl-4471-invoice-3").reason or "")[:200])
    show("### THIS IS THE M3 SEAM, AND IT IS NAMED RATHER THAN PAPERED OVER.",
         "Withdrawing a dead grant is EF-2r — machine M3's transition, which is a later unit. The "
         "kernel's `revoke_unclaimed` is that act's implementation today; M2 does not call it, "
         "because M2 does not own the grant's state (SD-2).")
    from freight_recon.checkpoint import revoke_unclaimed  # noqa: PLC0415

    _check("the dead grant is withdrawn (EF-2r), and only now is the effect free",
           revoke_unclaimed(kernel, grant_id=row.grant_id,
                            cause="brake engaged after the checkpoint", actor=CARL))

    print("\n-- 8c. Attempt #4 re-checkpoints, and the invoice finally goes out -----------------")
    fourth = allows("attempt #4 is proposed",
                    lambda: m.propose(pipeline_instance_id="pl-4471-invoice-4",
                                      work_item_id="wi-4471-billing", effect=effect,
                                      supersedes="pl-4471-invoice-3", **SYS))
    del fourth
    approval4 = an_approval(effect, world2, approval_id="ap-4471-d")
    to_checkpoint(m, "pl-4471-invoice-4", approval4.fingerprint)
    authorized = allows("the seven checks run again, against the brake state that holds NOW",
                        lambda: m.apply("pl-4471-invoice-4", Trigger.CHECKPOINT_RUN, **SYS,
                                        kernel=kernel,
                                        checkpoint_inputs=inputs_for(effect, world2,
                                                                     approval=approval4)))
    _check("this time the checkpoint AUTHORIZES", authorized.transition_id == "PL-8")
    claimed = allows("the claim wins the CAS, exactly once",
                     lambda: m.apply("pl-4471-invoice-4", Trigger.CLAIM_ATTEMPTED, **SYS,
                                     kernel=kernel, handle=authorized.grant_handle))
    _check("the ledger and the attempt moved together",
           claimed.to_state is PipelineState.CLAIMED
           and acme.conn.execute(
               "SELECT state FROM effect_grants WHERE tenant=? AND grant_id=?",
               (ACME, m.require("pl-4471-invoice-4").grant_id)).fetchone()[0] == "CLAIMED")
    refuses("a second claim on the same grant",
            lambda: m.apply("pl-4471-invoice-4", Trigger.CLAIM_ATTEMPTED, **SYS, kernel=kernel,
                            handle=authorized.grant_handle),
            IllegalTransition)

    print("\n-- 9. The TMS says it worked, and Neyma checks ------------------------------------")
    grant = m.require("pl-4471-invoice-4").grant_id
    executed = allows("the adapter's success is consumed exactly once",
                      lambda: m.consume(
                          canonical(acme, name="EffectExecuted", transition="EF-3",
                                    aggregate_type="effect_grant", aggregate_id=grant, version=1,
                                    seed="exec-4471", pipeline="pl-4471-invoice-4", owner=DANA),
                          pipeline_instance_id="pl-4471-invoice-4",
                          trigger=Trigger.ADAPTER_RETURNED_SUCCESS))
    _check("the attempt is EXECUTED", executed.transition.to_state is PipelineState.EXECUTED)
    fingerprint = m.require("pl-4471-invoice-4").material_facts_fingerprint
    refuses("accepting a readback that does not match what Dana approved",
            lambda: m.apply("pl-4471-invoice-4", Trigger.READBACK_MATCHED, **SYS,
                            matched_fingerprint="a-different-invoice", health_signal="HEALTHY"),
            GuardNotSatisfied)
    verified = allows("a healthy readback matching the APPROVED fingerprint verifies AND records",
                      lambda: m.consume(
                          canonical(acme, name="EffectVerified", transition="EF-4",
                                    aggregate_type="effect_grant", aggregate_id=grant, version=2,
                                    seed="ver-4471", pipeline="pl-4471-invoice-4", owner=DANA,
                                    payload={"matched_fingerprint": fingerprint}),
                          pipeline_instance_id="pl-4471-invoice-4",
                          trigger=Trigger.READBACK_MATCHED,
                          matched_fingerprint=fingerprint, health_signal="HEALTHY"))
    _check("verify and record are ONE commit — a crash between them cannot lose the record",
           verified.transition.transition_id == "PL-11"
           and [c.transition_id for c in verified.transition.co_commits] == ["PL-12"]
           and m.require("pl-4471-invoice-4").state is PipelineState.RECORDED)
    m.apply("pl-4471-invoice-4", Trigger.PROJECTION_UPDATED, **SYS, entity_ref=LOAD,
            from_effect_id=grant)
    closed = allows("the attempt closes",
                    lambda: m.apply("pl-4471-invoice-4", Trigger.CLOSE_REQUESTED, **SYS))
    _check("attempt #4 is CLOSED — load 4471 is billed, exactly once, after four tries",
           closed.to_state is PipelineState.CLOSED)

    print("\n-- 10. The one that goes wrong: load 5120, and nobody knows what happened -----------")
    show("Different load, different invoice. The TMS connection drops mid-write. Neyma cannot say",
         "whether the invoice was raised. This is the moment a naive system marks it failed.")
    WorkItemMachine(acme.conn, tenant=ACME, clock=CLOCK).create(
        work_item_id="wi-5120-billing", type="delivered_load_closure", owner_id=DANA,
        actor_type="system", actor_id="work-service", entity_ref="load:5120")
    e2 = the_invoice_effect(resource="load:5120")
    w2 = the_world()
    w2["versions"] = {"load:5120": 4}
    w2["facts"]["amount"] = ProvenancedFact(
        field="amount", provenance=ProvenanceClass.SYSTEM_IMPORTED,
        evidence_condition=EvidenceCondition.CONSISTENT, entity_ref="load:5120",
        _value=Money(96500, "GBP"))
    m.propose(pipeline_instance_id="pl-5120-invoice-1", work_item_id="wi-5120-billing",
              effect=e2, proposal_ref="intent-5120", **SYS)
    a2 = an_approval(e2, w2, approval_id="ap-5120")
    to_checkpoint(m, "pl-5120-invoice-1", a2.fingerprint)
    auth2 = m.apply("pl-5120-invoice-1", Trigger.CHECKPOINT_RUN, **SYS, kernel=kernel,
                    checkpoint_inputs=inputs_for(e2, w2, approval=a2))
    m.apply("pl-5120-invoice-1", Trigger.CLAIM_ATTEMPTED, **SYS, kernel=kernel,
            handle=auth2.grant_handle)
    refuses("calling it FAILED because the call timed out",
            lambda: m.apply("pl-5120-invoice-1", Trigger.ADAPTER_REJECTED_PRE_FLIGHT, **SYS),
            GuardNotSatisfied)
    unknown = allows("it becomes NEEDS_VERIFICATION, with the specific question recorded",
                     lambda: m.apply("pl-5120-invoice-1", Trigger.ADAPTER_TIMED_OUT, **SYS,
                                     unknown_reason="UNKNOWN_OUTCOME",
                                     unknown_outcome_ref="obs-tms-timeout-5120"))
    _check("it is not FAILED and carries no failure proof",
           unknown.to_state is PipelineState.NEEDS_VERIFICATION
           and m.require("pl-5120-invoice-1").failure_proof is None)
    refuses("a deadline resolving it",
            lambda: m.apply("pl-5120-invoice-1", Trigger.TIMER_FIRED, **SYS),
            IllegalTransition)
    CLOCK.advance(days=30)
    _check("thirty days later it is still open, and still owned",
           m.require("pl-5120-invoice-1").state is PipelineState.NEEDS_VERIFICATION
           and m.require("pl-5120-invoice-1").owner_id == DANA)
    refuses("retrying load 5120 while nobody can say whether the invoice went out",
            lambda: m.propose(pipeline_instance_id="pl-5120-invoice-2",
                              work_item_id="wi-5120-billing", effect=e2,
                              supersedes="pl-5120-invoice-1", **SYS),
            PipelineError)
    queue = m.needs_verification()
    show("The Sev-1 queue an operator actually opens:",
         "; ".join(f"{p.pipeline_instance_id} owner={p.owner_id} question={p.unknown_reason}"
                   for p in queue))

    print("\n-- 11. Dana checks the TMS by hand ------------------------------------------------")
    decision = canonical(acme, name="HumanDecided", transition="WI-9", aggregate_type="work_item",
                         aggregate_id="decision-5120", version=1, seed="decided-5120",
                         actor_type="human", actor_id=DANA,
                         payload={"decision_ref": "slack-thread-5120"}, emit=True)
    refuses("closing it on 'it probably did not go through'",
            lambda: m.apply("pl-5120-invoice-1", Trigger.HUMAN_ESTABLISHED_REALITY, **HUMAN,
                            outcome="FAILED", decision_ref=decision.event_id,
                            decision_ref_kind="AUDIT_EVENT"),
            GuardNotSatisfied)
    refuses("closing it with a reference that resolves to nothing",
            lambda: m.apply("pl-5120-invoice-1", Trigger.HUMAN_ESTABLISHED_REALITY, **HUMAN,
                            outcome="FAILED", decision_ref="done",
                            decision_ref_kind="AUDIT_EVENT", failure_proof="looked"),
            GuardNotSatisfied)
    resolved = allows(
        "Dana establishes reality: the TMS invoice list has no invoice for load 5120",
        lambda: m.apply("pl-5120-invoice-1", Trigger.HUMAN_ESTABLISHED_REALITY, **HUMAN,
                        outcome="FAILED", decision_ref=decision.event_id,
                        decision_ref_kind="AUDIT_EVENT",
                        failure_proof="TMS invoice list for load 5120 empty at 2026-09-13, "
                                      "checked by dispatcher-dana"))
    _check("only now is the effect provably absent, and only now may it be retried",
           resolved.to_state is PipelineState.FAILED
           and m.live_holder(e2.key()) is None)
    retry = allows("attempt #2 at load 5120 is now legitimate",
                   lambda: m.propose(pipeline_instance_id="pl-5120-invoice-2",
                                     work_item_id="wi-5120-billing", effect=e2,
                                     supersedes="pl-5120-invoice-1", **SYS))
    _check("same commit key, new attempt", retry.started.pipeline.commit_key == e2.key())

    print("\n-- 12. Somebody drives the machine from a state it should not be in ---------------")
    before = acme.conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE tenant=?", (ACME,)).fetchone()[0]
    for trigger in (Trigger.CLAIM_ATTEMPTED, Trigger.READBACK_MATCHED, Trigger.CLOSE_REQUESTED):
        refuses(f"{trigger.value} against the CLOSED attempt #4 for load 4471",
                lambda t=trigger: m.apply("pl-4471-invoice-4", t, **SYS), IllegalTransition)
    after = acme.conn.execute(
        "SELECT COUNT(*) FROM security_events WHERE tenant=?", (ACME,)).fetchone()[0]
    _check("EVERY distinct hostile attempt is recorded, not just the first",
           after - before == 3, f"security records written: {after - before} for 3 attempts")

    print("\n-- 13. Another brokerage's event, delivered here ----------------------------------")
    foreign = canonical(rival, name="EffectExecuted", transition="EF-3",
                        aggregate_type="effect_grant", aggregate_id=grant, version=1,
                        seed="foreign-4471", tenant=RIVAL, pipeline="pl-4471-invoice-4",
                        owner=DANA)
    result = allows("a rival brokerage's event is rejected before any handler sees it",
                    lambda: m.consume(foreign, pipeline_instance_id="pl-4471-invoice-4",
                                      trigger=Trigger.ADAPTER_RETURNED_SUCCESS))
    _check("it is refused as cross-tenant and nothing moved",
           result.consume.outcome.value == "REJECTED_CROSS_TENANT" and not result.moved)

    print("\n-- 14. An event arrives for an attempt that does not exist yet ---------------------")
    early = canonical(acme, name="EffectExecuted", transition="EF-3",
                      aggregate_type="effect_grant", aggregate_id="grant-not-here", version=1,
                      seed="early-9999", pipeline="pl-9999-invoice", owner=DANA)
    parked = allows("it is PARKED, not looped and not dropped",
                    lambda: m.consume(early, pipeline_instance_id="pl-9999-invoice",
                                      trigger=Trigger.ADAPTER_RETURNED_SUCCESS))
    _check("the park names the human it will land on",
           parked.consume.outcome.value == "PARKED_MISSING_AGGREGATE"
           and acme.conn.execute(
               "SELECT accountable_owner_id FROM pending_references WHERE tenant=? AND event_id=?",
               (ACME, early.event_id)).fetchone()[0] == DANA)
    nameless = canonical(acme, name="EffectExecuted", transition="EF-3",
                         aggregate_type="effect_grant", aggregate_id="grant-nobody", version=1,
                         seed="nameless-9999", pipeline="pl-8888-invoice", owner=None)
    refuses("parking an obligation nobody would own",
            lambda: m.consume(nameless, pipeline_instance_id="pl-8888-invoice",
                              trigger=Trigger.ADAPTER_RETURNED_SUCCESS),
            OwnershipRefused)

    print("\n-- 15. What an audit can reconstruct ----------------------------------------------")
    rows = acme.conn.execute(
        "SELECT event_name, aggregate_id, aggregate_version, envelope_json FROM event_outbox "
        " WHERE tenant=? AND aggregate_type='pipeline_instance' "
        "   AND aggregate_id='pl-4471-invoice-4' ORDER BY aggregate_version", (ACME,)).fetchall()
    for r in rows:
        env = EventEnvelope.from_json(r["envelope_json"])
        print(f"      v{r['aggregate_version']:<2} {r['event_name']:<28} "
              f"accountable: {env.accountable_owner_id}  work item: {env.work_item_id}")
    _check("every event this attempt emitted names the human accountable for it",
           bool(rows) and all(EventEnvelope.from_json(r["envelope_json"]).accountable_owner_id
                              == DANA for r in rows))

    print("\n-- 16. Production posture ---------------------------------------------------------")
    for table, expected in (("checkpoint_witnesses", 3), ("effect_grants", 3)):
        count = acme.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"      {table}: {count}")
        if count != expected:
            bad += 1
            print(f"  ### WRONG  expected {expected} {table} rows in this narrative")
    claimed_keys = acme.conn.execute(
        "SELECT commit_key, COUNT(*) FROM effect_grants WHERE tenant=? "
        " AND state IN ('CLAIMED','ATTEMPTED','VERIFIED','UNKNOWN_OUTCOME') GROUP BY commit_key",
        (ACME,)).fetchall()
    _check("### AT MOST ONE AUTHORIZATION PER LOGICAL EFFECT WAS EVER CLAIMED",
           all(r[1] == 1 for r in claimed_keys) and len(claimed_keys) == 2,
           f"claimed effects: {[(r[0][:12] + '…', r[1]) for r in claimed_keys]}")
    show("Three authorizations were MINTED across four attempts at load 4471 and two at load 5120:",
         "attempt #2 for 4471 (killed by the brake, never claimed, later REVOKED), attempt #4 for "
         "4471 (claimed, verified, closed), and load 5120's first (claimed, outcome unknown, later "
         "proved absent). Attempts #1 and #3 for 4471 never minted one at all — drift and a held "
         "commit key refused them AT the checkpoint. ### NOTHING IN THIS RUN PERFORMED AN EXTERNAL "
         "EFFECT: the machine imports no effect-capable module, and no adapter was called.")

    print(f"\n=========== {ok} behaviours as specified, {bad} wrong ===========\n")
    acme.close()
    rival.close()
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

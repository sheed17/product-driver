"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M7?

M7 is the Conflict: two or more mutually exclusive claims or observations on the same field, made
**visible and blocking**. Entity §3 calls it *the mechanism by which Neyma never silently chooses* —
so the question this file answers is not "does the YAML parse" but whether the whole loop can own
the unit end to end without the founder standing in the middle of it.

The unit's whole character is two sentences, and every check below traces back to one of them:

    while a Conflict is OPEN the field is `conflicting` and BLOCKS every consequential action
    it closes by a REGISTERED RULE or by a HUMAN — and there is no third way

Not recency. Not confidence. Not source priority. Not a model. Not a clock. *A conflict that times
out is a conflict resolved by a clock, and the clock knows nothing about freight.*

Thirteen questions, each answered mechanically rather than by reading a document and agreeing with
it:

1.  does the M7 base scenario parse, does it hold the pieces the generator needs (deterministic
    operation, a closed mutation axis, persisted-state oracles, regression anchors), and do the
    scenario and the task state the SAME contract;
2.  does every declared risk name a command that could actually emit the observation it requires —
    the `P6-D-run-20260825` mapping defect, refused ahead of time;
3.  does the scenario measure the DATABASE rather than the probe's narration for the invariants a
    green test suite can state while the database enforces none of them;
4.  does the task preserve the three recorded authority conflicts rather than resolving them;
5.  does the task get the SEAMS right — `ConflictRaised` has three registered producers, two of
    which are other machines that are already LANDED, and neither may be edited to make M7 tidy;
6.  is the M7 command vocabulary safe, and actually visible to the generator rather than truncated
    out of the brief;
7.  can dynamic generation close an M7 coverage gap WITHOUT inventing a command, and is an invented
    one refused;
8.  is `P6-D46` still closed — canonical taxonomy only, no candidate lost to it, and the four counts
    still separable;
9.  is M7 scoped as `P6/M7` rather than as P6 phase completion, and can accepting it score a P6
    acceptance criterion or unlock P7 (it cannot);
10. is an integrated independent review OWED when the repository's own authority says so;
11. do grounded reviewer findings return to the SAME builder, and does a corrected tree get a FRESH
    reviewer, and does the run stop before M8;
12. does the founder summary explain M7's product impact in simple terms — and never contradict its
    own review ledger while doing it;
13. and, for the load-bearing half of all of the above: **does this file actually fail when the
    assertion it rests on is removed?** A readiness test never seen to fail is a decoration, so the
    last section mutates the shipped scenario and proves each guard turns red.

Every Claude session is faked. No test here consumes Claude usage, executes the product, or touches
the real Neyma repository.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.completion_auditor import AuditDecision
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.models import RunStatus
from neyma_product_driver.review_cycle import resolve_review_requirement
from neyma_product_driver.run_journal import RunJournal
from neyma_product_driver.scenario_generator import (
    MAX_RENDERED_COMMANDS,
    PLAN_SCHEMA,
    parse_scenarios,
)
from neyma_product_driver.scenario_plan import (
    REJECTED_CONTRACT,
    REJECTED_FILTERED,
    RISK_CATEGORY_VALUES,
    GeneratedScenario,
    GeneratedStateCheck,
    IdentifiedRisk,
    Priority,
    RiskCategory,
    ScenarioProvenance,
)
from neyma_product_driver.scenario_planner import STAGE_COVERAGE_GAP, ScenarioPlanner
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    ValidationContext,
    validate_plan,
)
from neyma_product_driver.scenarios import load_scenario
from neyma_product_driver.task_scope import (
    ScopeLevel,
    TaskResult,
    scoped_completion,
    standard_exclusions,
)

from scenario_fixtures import FakeFounder, FakeUnit, ScriptedReasoner, raw_payload, raw_scenario
from test_integrated_review import FakeBuilder, FakeReviewer, drive, refusing, supported
from test_scoped_completion import PhaseRepo

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DRIVER_ROOT / "scenarios"
M7_PATH = SCENARIOS_DIR / "p6_m7_conflict.yaml"
M7_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m7.md"
M7_TASK = M7_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match against this: the task is
#: a wrapped markdown document, and a phrase that happens to straddle a line break is not a phrase
#: the task failed to state.
M7_TASK_FLAT = " ".join(M7_TASK.split())
PROBE = ".venv/bin/python scripts/probe_phase6_conflict.py"
#: The `name:` the base scenario gives the bare probe run — the deterministic basic M7 operation,
#: and the only check in the file that drives the machine and narrates what it saw.
PROBE_CHECK = "drive the Conflict machine through a brokerage narrative, and attack it"

#: A persisted-state command the base scenario already carries, so a generated case that reuses it is
#: choosing an approved oracle rather than authoring one.
STATE_ORACLE = next(
    check.command
    for check in load_scenario(M7_PATH).expect_state
    if "schema_readiness_problems" in check.command
)

#: The canonical M7 deliverables. A different name is a scenario failure, not a style preference —
#: the permanent scenario looks for exactly these.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/conflict.py",
    "src/freight_recon/migrations/phase6_conflicts.py",
    "eval/tests/test_phase6_conflict.py",
    "scripts/probe_phase6_conflict.py",
    "scripts/mutate_phase6_conflict.py",
)

#: The five canonical conflict states (registry §4 / M7, target spec §12.7). Not four, not six.
STATES: tuple[str, ...] = (
    "RAISED",
    "OPEN",
    "ESCALATED",
    "RESOLVED_BY_RULE",
    "RESOLVED_BY_HUMAN",
)

#: The three that are OPEN for the purposes of GR-10. `RAISED` is in this set, and a build session
#: that reads "open" as "the OPEN state" ships a window in which a conflict exists and money moves.
OPEN_STATES: tuple[str, ...] = ("RAISED", "OPEN", "ESCALATED")

#: States a build session might reach for out of tidiness, and that the corpus says do not exist.
#: `CANCELLED` is first because entity §25 describes cancellation while §14 enumerates no row for it
#: — inventing the state is how `M7-AQ-3` gets answered by accident.
FORBIDDEN_STATES: tuple[str, ...] = ("CANCELLED", "EXPIRED", "RESOLVED", "AUTO_RESOLVED", "DISMISSED")

#: The six canonical kinds, entity §12 and the `ConflictRaised` contract's own enum.
KINDS: tuple[str, ...] = (
    "SYSTEM_VS_SYSTEM",
    "CLAIM_VS_CLAIM",
    "CLAIM_VS_OBSERVATION",
    "INFERRER_VS_OWNER",
    "READBACK_VS_APPROVED",
    "RULE_VS_RULE",
)

#: The canonical transition ids. The task must require these rows, with these ids, rather than an
#: alternative lifecycle that "achieves the same thing".
TRANSITIONS: tuple[str, ...] = ("CF-1", "CF-2", "CF-3", "CF-4", "CF-5", "CF-6", "CF-7")

#: The five registered F7 event contracts. `event_contracts_data.json` carries exactly these five,
#: and `events/registry.md` is by its own header THE SOLE CANONICAL LIST — so a sixth `Conflict*`
#: name is defective by the registry's own definition.
F7_EVENTS: tuple[str, ...] = (
    "ConflictRaised",
    "ConflictOpened",
    "ConflictPartyAttached",
    "ConflictEscalated",
    "ConflictResolved",
)

#: Nine `risk_category` values in the shape `P6-D46`'s real nine had: each a plausible, well-meant
#: DESCRIPTION OF A SPECIFIC DEFECT rather than a member of a closed family vocabulary — which is
#: what an unconstrained `{"type": "string"}` schema invites a model to write. These are M7's.
M7_UNREADABLE_CATEGORIES: tuple[str, ...] = (
    "conflict-auto-resolved",
    "ownerless-conflict",
    "two-open-conflicts",
    "timer-resolved-the-conflict",
    "party-lost-on-rebuild",
    "cross-tenant-conflict-coalescing",
    "unregistered-rule-resolved",
    "readback-laundered-into-failure",
    "m6-seam-rewritten",
)

#: Names a build session invents when it wants a Conflict to stop being visible.
FORBIDDEN_EVENTS: tuple[str, ...] = (
    "ConflictExpired",
    "ConflictAutoResolved",
    "ConflictWinnerChosen",
    "ConflictDismissed",
    "ConflictCancelled",
)


def _local_vocabulary() -> list[str]:
    """The `--case` entries the local driver config approves, if it exists.

    Read from the file rather than through `load_config`, because this must work on a checkout that
    has no `driver.config.yaml` at all — the vocabulary is then simply absent and the tests that need
    it skip.
    """
    local = DRIVER_ROOT / "driver.config.yaml"
    if not local.exists():
        return []
    raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    return list((raw.get("scenario_generation") or {}).get("approved_commands") or [])


@pytest.fixture(scope="module")
def m7():
    return load_scenario(M7_PATH)


@pytest.fixture(scope="module")
def cases(m7) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m7.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m7) -> list[str]:
    listing = [c for c in m7.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def state_checks(m7) -> dict[str, list[str]]:
    return {check.name: list(check.contains) for check in m7.expect_state}


# --------------------------------------------------------------------------
# 1. The base scenario, and its contract with the task
# --------------------------------------------------------------------------


class TestTheM7BaseScenario:
    def test_it_parses_and_is_a_dark_p6_backend_scenario(self, m7):
        assert m7.name == "p6_m7_conflict"
        assert m7.phase == "P6"
        assert m7.mode == "backend"
        # M7 ships dark: no service, no HTTP surface, no browser, and above all no conflict inbox —
        # the product form of this unit is a QUEUE OF FROZEN FIELDS A HUMAN RESOLVES, and that queue
        # is precisely the thing that must not arrive with the engine primitive.
        assert not m7.services and not m7.requests and m7.browser is None
        assert not m7.app_url

    def test_it_requires_the_canonical_deliverables_to_exist(self, m7):
        """A run against a repository where M7 does not exist yet must not be able to report a
        verified M7."""
        for path in DELIVERABLES:
            assert path in m7.fixtures, f"{path} is not required to exist"

    def test_the_probe_is_approved_bare_so_every_case_tail_is_reachable(self, m7):
        """The whole `--case` interface rests on this one entry.

        Approval matches by prefix, so approving the bare probe approves every argument tail that
        composes no shell. Approving only `probe.py --list-cases` would approve exactly that string
        and nothing else, and the generator would have no focused entry point at all.
        """
        assert any(c.run == PROBE for c in m7.commands), (
            "the bare probe invocation is missing; without it a generated "
            f"'{PROBE} --case X' is not an argument tail of any approved entry"
        )

    def test_it_asserts_a_risk_family_for_every_canonical_obligation(self, cases):
        """One family per canonical obligation, checked by name.

        This list is the contract between `tasks/neyma_p6_m7.md` and this file; a family missing from
        either is a family the generator cannot reach and the builder was never asked to build.
        """
        required = {
            # CF-1 — the raise, the freeze, the owner, and the six kinds
            "raise-creates-raised-with-a-named-human-owner",
            "raise-and-freeze-are-one-commit",
            "ownerless-conflict-is-impossible",
            "a-model-cannot-own-a-conflict",
            "the-six-conflict-kinds-are-closed",
            "system-vs-system-raises-a-conflict",
            "claim-vs-claim-raises-a-conflict",
            "claim-vs-observation-raises-a-conflict",
            "inferrer-vs-owner-records-the-owner-asserted-party",
            "readback-vs-approved-is-not-an-ordinary-failure",
            "rule-vs-rule-fails-closed-and-never-auto-merges",
            "injected-competing-claim-freezes-the-entity-not-control",
            # CF-2
            "acknowledgement-opens-the-conflict",
            # the invariant — an open conflict blocks, and M7 is not a second gate
            "raised-conflict-already-blocks-consequential-action",
            "open-conflict-blocks-consequential-action",
            "escalated-conflict-still-blocks-consequential-action",
            "open-conflict-fails-checkpoint-native-state-validity",
            "no-effect-grant-on-a-conflicted-material-field",
            "open-conflict-blocks-the-approval",
            "m7-mints-no-gate-decision",
            # CF-3 — the registered rule, and the four things that are not one
            "registered-rule-resolves-the-conflict",
            "unregistered-rule-cannot-resolve",
            "rule-resolution-requires-a-registered-rule-id",
            "confidence-cannot-resolve-a-conflict",
            "recency-cannot-resolve-a-conflict",
            "source-priority-cannot-resolve-without-a-registered-rule",
            "a-model-cannot-resolve-a-conflict",
            # CF-4 — the authenticated human
            "authenticated-human-resolves-the-conflict",
            "human-resolution-requires-a-decision-ref",
            "counterparty-cannot-resolve-a-conflict",
            "wrong-tenant-human-resolution-fails-closed",
            "forged-human-fails-closed",
            "inactive-human-fails-closed",
            # the resolution basis
            "resolution-carries-exactly-one-basis",
            "resolution-with-neither-rule-nor-decision-is-illegal",
            "resolution-unfreezes-the-field",
            "a-resolved-conflict-is-retained-never-deleted",
            "new-evidence-after-resolution-raises-a-new-conflict",
            # CF-5 — the timer
            "age-threshold-escalates-the-conflict",
            "a-timer-never-resolves-a-conflict",
            "a-conflict-never-expires",
            # CF-6
            "escalated-resolves-by-registered-rule",
            "escalated-resolves-by-authenticated-human",
            "escalated-resolution-is-by-target-state-never-by-position",
            # CF-7
            "second-detection-attaches-a-party-not-a-new-conflict",
            "at-most-one-open-conflict-per-field",
            "an-attached-party-carries-its-own-provenance",
            "party-provenance-is-never-strengthened",
            "concurrent-detectors-produce-one-conflict",
            "a-party-retraction-never-silently-closes-the-conflict",
            # replay and restart
            "replay-rebuilds-the-complete-party-set",
            "replay-keeps-the-field-frozen",
            "replay-cannot-resolve-or-duplicate-a-conflict",
            "replay-creates-no-new-authority-and-no-effect",
            "restart-preserves-the-open-conflict",
            # [C-1] — tenancy
            "tenant-isolation",
            "cross-tenant-identical-entity-ref-and-field",
            "cross-tenant-party-reference-fails-closed",
            # GR-2 / GR-3 / GR-4 — concurrency, idempotency, the database
            "occ-on-conflict-version",
            "competing-resolutions-serialize-at-most-one-wins",
            "redelivered-detection-is-a-no-op",
            "inbox-idempotency",
            "state-and-event-co-commit",
            "database-invariants",
            "malformed-conflict-fails-closed",
            "persistence-failure-rolls-back-the-raise-and-the-freeze",
            # the seams
            "the-m6-claim-machine-is-not-rewritten",
            "the-m3-unknown-outcome-semantics-are-unchanged",
            "the-cross-family-conflict-raised-producers-are-recorded",
            "m8-m9-m10-and-m12-are-not-built",
        }
        missing = sorted(required - set(cases))
        assert not missing, f"risk families the scenario never asserts exist: {missing}"

    def test_it_declares_a_bounded_mutation_axis(self, dimensions):
        """Without this the M7 possibility space is a list of fixed points.

        M7 ships dark, so there is no service and no HTTP surface, and `parallel_requests` — the
        executor's only concurrency primitive — is unavailable. Ordering, concurrency, timing,
        duplication, crash and replay variation are reachable through the probe's arguments or not at
        all. See docs/SCENARIO-SPACE.md, gap G2.
        """
        for axis in ("--concurrency", "--delay-ms", "--repeat", "--tenants", "--parties",
                     "--age-ms", "--confidence", "--seed"):
            assert axis in dimensions, f"the axis {axis} is never asserted to exist"
        for fault in (
            "system-vs-system", "claim-vs-claim", "claim-vs-observation", "inferrer-vs-owner",
            "readback-vs-approved", "rule-vs-rule",
            "ownerless-raise", "model-owner", "acknowledge",
            "age-threshold", "timer-resolve", "auto-resolve",
            "model-resolve", "confidence-resolve", "recency-resolve", "source-priority-resolve",
            "unregistered-rule", "missing-rule-id", "missing-decision-ref",
            "both-resolution-bases", "neither-resolution-basis",
            "forged-human", "inactive-human", "wrong-tenant", "counterparty-resolve",
            "second-detection", "concurrent-detection", "duplicate-detection",
            "retract-party", "strengthen-party-provenance", "cross-tenant-party",
            "occ-conflict", "competing-resolution", "malformed-conflict", "persistence-failure",
            "replay", "restart-before-open", "restart-after-escalate", "reorder-stream",
            "new-evidence-after-resolution",
        ):
            assert fault in dimensions, f"the fault {fault!r} is never asserted to exist"

    def test_the_age_axis_exists_so_the_timer_can_be_pushed_at_the_conflict(self, dimensions, cases):
        """The axis whose whole purpose is to prove the clock cannot decide.

        Entity §26 and machine §12/§23: a Conflict NEVER expires — it ages and escalates. ADR-007
        §5.3 makes `AutoResolve` illegal and says a conflict that times out is a conflict resolved by
        a clock. An axis a generator can wind forward, over a case that must ESCALATE and must not
        RESOLVE, is what makes that a measurement rather than a belief.
        """
        assert "--age-ms" in dimensions
        assert "age-threshold" in dimensions
        assert "timer-resolve" in dimensions
        assert "auto-resolve" in dimensions
        assert "age-threshold-escalates-the-conflict" in cases
        assert "a-timer-never-resolves-a-conflict" in cases
        assert "a-conflict-never-expires" in cases

    def test_confidence_is_an_axis_so_the_negative_control_is_reachable(self, dimensions, cases):
        """The one axis whose whole purpose is to change NOTHING.

        ADR-007 §5.3 lists confidence among the ways a Conflict may NOT close, and §8 names a
        confidence threshold as the single most likely way the architecture gets defeated. An axis
        the generator can turn to 1.0, over a case that must still refuse, is what makes that a
        measurement rather than a belief.
        """
        assert "--confidence" in dimensions
        assert "confidence-resolve" in dimensions
        assert "confidence-cannot-resolve-a-conflict" in cases
        assert "recency-cannot-resolve-a-conflict" in cases
        assert "source-priority-cannot-resolve-without-a-registered-rule" in cases

    def test_the_mutation_axis_has_a_negative_control(self, m7):
        """A vocabulary that accepts anything is fuzzing in a costume."""
        negative = [c for c in m7.commands if "--inject not-a-real-fault" in c.run]
        assert negative, "nothing proves the fault vocabulary is actually closed"
        assert negative[0].expect_exit_code == 2, "a refusal must be a non-zero exit"
        assert "unknown fault" in negative[0].expect_contains
        assert "Traceback (most recent call last)" in m7.forbidden

    def test_an_expiry_fault_is_refused_because_a_conflict_never_expires(self, m7):
        """The first M7-specific negative control.

        Entity §26 says a Conflict NEVER EXPIRES — it ages and escalates — machine §12 and §23 say
        the same twice more, and §28 gives it no deletion policy. A probe that ACCEPTED an expiry
        fault would be producing passing evidence for a mechanism the corpus states does not exist —
        the same shape as M4's refused `unfreeze`, M5's refused `expire-observation` and M6's refused
        `expire-claim`.
        """
        expiry = [c for c in m7.commands if "--inject expire-conflict" in c.run]
        assert expiry, "nothing refuses a conflict-expiry fault"
        assert expiry[0].expect_exit_code == 2
        assert "unknown fault" in expiry[0].expect_contains
        assert "### CONFLICT EXPIRED ###" in m7.forbidden

    def test_a_cancel_fault_is_refused_because_the_table_enumerates_none(self, m7, dimensions):
        """The second, and the one that holds an authority question open.

        Entity §25 and machine §22 say a Conflict may be cancelled when the disagreement disappears —
        *"still an event, never silence"* — while §14 enumerates only `CF-1`…`CF-7`, `GR-1` makes
        anything unenumerated ILLEGAL, registry §4 gives M7 no `CANCELLED` state, and no
        `ConflictCancelled` event is registered anywhere. That is `M7-AQ-3`, and a probe that
        ACCEPTED the fault would have answered it by building the transition.

        `retract-party` stays in the vocabulary precisely so the half every reading agrees on — a
        retraction never silently closes the Conflict — is still exercised.
        """
        cancel = [c for c in m7.commands if "--inject cancel-conflict" in c.run]
        assert cancel, "nothing refuses a cancel-conflict fault"
        assert cancel[0].expect_exit_code == 2
        assert "unknown fault" in cancel[0].expect_contains
        assert "### CONFLICT SILENTLY CANCELLED ###" in m7.forbidden
        assert "retract-party" in dimensions, (
            "the cancel fault is refused and the retraction fault is gone, so nothing exercises the "
            "half of M7-AQ-3 that every reading DOES agree on"
        )

    def test_the_illegal_faults_are_in_the_vocabulary_rather_than_refused_as_unknown(
        self, m7, dimensions
    ):
        """The distinction M6 did not have to make, and M7 does.

        M6 refused `auto-resolve-conflict` as an UNKNOWN fault, because M6 does not own conflict
        resolution at all. M7 does own it, and machine §15 names `AutoResolve` and any
        `TimerFired`-to-resolved as ILLEGAL TRANSITIONS — so the machine must be SEEN TO REFUSE
        them under `GR-1`, raising and persisting nothing. A fault refused as *unknown* and a fault
        refused as *illegal* are two different proofs, and a probe that made the illegal ones
        unreachable would have proved only the first.
        """
        refused_as_unknown = {
            c.run.split("--inject ", 1)[1].split()[0]
            for c in m7.commands
            if "--inject " in c.run and c.expect_exit_code == 2
        }
        for illegal in ("auto-resolve", "timer-resolve", "model-resolve", "neither-resolution-basis"):
            assert illegal in dimensions, f"the illegal shape {illegal!r} is not reachable at all"
            assert illegal not in refused_as_unknown, (
                f"{illegal!r} is refused as an UNKNOWN fault. The corpus DEFINES it, as an ILLEGAL "
                "transition — so the machine owes a GR-1 refusal for it, not the argument parser"
            )

    def test_it_carries_regression_anchors_for_every_layer_m7_builds_on(self, m7):
        """M7 adds two tables and edits `schema.py`, so every layer that reads a canonical database
        can be broken from inside it."""
        runs = " ".join(c.run for c in m7.commands)
        for anchor in (
            "test_phase3_witness.py",             # P3, the kernel M7 feeds and must not disturb
            "test_import_gate.py",                # P4, the boundary M7 must not widen
            "test_phase5_event_transport.py",     # P5, the transport M7 rides
            "test_p5_durable_timers.py",          # P5, the timer substrate CF-5 rides
            "test_phase6_work_item.py",           # M1 — and it CONSUMES ConflictRaised at WI-6
            "test_phase6_pipeline_instance.py",   # M2
            "test_phase6_external_effect.py",     # M3, the EF-4c seam
            "test_phase6_approval.py",            # M4
            "test_phase6_observation.py",         # M5
            "test_phase6_identity_binding_claim.py",  # M6, the IB-6 seam
        ):
            assert anchor in runs, f"{anchor} is never re-run against M7's change"
        # M7 emits five already-registered F7 names and mints none of its own, so the canonical
        # event-contract guard is load-bearing here rather than incidental: a builder that invents
        # `ConflictExpired` or emits `ConflictResolved` with no resolution basis fails there.
        assert "test_p5_event_contracts.py" in runs
        assert "test_p5_canonical_event_mint.py" in runs

    def test_it_re_runs_the_neighbouring_units_own_oracles_rather_than_trusting_m7(self, m7):
        """"M7 broke nothing" is a claim about M6's, M5's and M4's behaviour.

        M6 in particular: `IB-6` already emits `ConflictRaised`, so M6 is the first unit with a real
        reason to be edited by M7 — and the first that must not be.
        """
        runs = [c.run for c in m7.commands]
        assert ".venv/bin/python scripts/probe_phase6_identity_binding_claim.py" in runs
        assert ".venv/bin/python scripts/probe_phase6_observation.py" in runs
        assert ".venv/bin/python scripts/probe_phase6_approval.py" in runs

    def test_the_mutation_battery_runs(self, m7):
        runs = " ".join(c.run for c in m7.commands)
        assert "mutate_phase6_conflict.py" in runs
        assert "mutants caught" in m7.expect_visible

    def test_it_refuses_the_failures_m7_exists_to_prevent(self, m7):
        """Each forbidden marker is a sentence printed only when the thing M7 exists to prevent has
        just happened. Every load-bearing mutant in the battery has one."""
        for marker in (
            "### CONFLICT AUTO-RESOLVED ###",
            "### TIMER RESOLVED A CONFLICT ###",
            "### CONFLICT EXPIRED ###",
            "### CONFLICT DELETED ###",
            "### CONFLICT SILENTLY CANCELLED ###",
            "### MODEL RESOLVED A CONFLICT ###",
            "### CONFIDENCE RESOLVED A CONFLICT ###",
            "### RECENCY RESOLVED A CONFLICT ###",
            "### SOURCE PRIORITY RESOLVED WITHOUT A RULE ###",
            "### UNREGISTERED RULE RESOLVED A CONFLICT ###",
            "### RESOLVED WITHOUT A RULE OR A DECISION ###",
            "### TWO RESOLUTION BASES ACCEPTED ###",
            "### COUNTERPARTY RESOLVED A CONFLICT ###",
            "### FORGED HUMAN ACCEPTED ###",
            "### INACTIVE HUMAN ACCEPTED ###",
            "### OWNERLESS CONFLICT CREATED ###",
            "### MODEL BECAME THE CONFLICT OWNER ###",
            "### CONFLICT WITHOUT ITS FROZEN FIELD ###",
            "### FIELD FROZEN WITHOUT ITS CONFLICT ###",
            "### CONSEQUENTIAL ACTION PROCEEDED ON AN OPEN CONFLICT ###",
            "### EFFECT GRANT MINTED ON A CONFLICTED FIELD ###",
            "### APPROVAL PROCEEDED ON AN OPEN CONFLICT ###",
            "### TWO OPEN CONFLICTS FOR ONE FIELD ###",
            "### A SECOND CONFLICT WAS RAISED INSTEAD OF A PARTY ###",
            "### PARTY LOST ###",
            "### PARTY PROVENANCE STRENGTHENED ###",
            "### CROSS-TENANT PARTY ACCEPTED ###",
            "### CROSS-TENANT RESOLUTION ACCEPTED ###",
            "### ESCALATION RESOLVED BY POSITION ###",
            "### NEYMA PICKED A WINNER ###",
            "### RULE_VS_RULE AUTO-MERGED ###",
            "### READBACK CONTRADICTION LAUNDERED INTO A NORMAL FAILURE ###",
            "### UNKNOWN_OUTCOME SILENTLY RESOLVED ###",
            "### REPLAY RESOLVED A CONFLICT ###",
            "### REPLAY DUPLICATED A CONFLICT ###",
            "### REPLAY REBUILT A STALE PARTY SET ###",
            "### DOWNSTREAM EFFECT DURING REPLAY ###",
            "### EVENT WITHOUT ITS STATE ###",
            "### STATE WITHOUT ITS EVENT ###",
            "### M6 CLAIM ROW REWRITTEN BY M7 ###",
            "### COMPENSATION FABRICATED ###",
        ):
            assert marker in m7.forbidden, f"{marker} is not a failure condition"

    def test_every_mutation_battery_mutant_has_a_forbidden_marker_behind_it(self, m7):
        """The mutation battery is only as good as the sentence that turns red under it.

        Each pair below is (the mutant the task requires, the marker the probe prints when that
        defect is live). A mutant with no marker is a mutant nothing observes.
        """
        for mutant_phrase, marker in (
            ("`AutoResolve` accepted", "### CONFLICT AUTO-RESOLVED ###"),
            ("a timer transition to a resolved state", "### TIMER RESOLVED A CONFLICT ###"),
            ("a confidence threshold used as a resolution guard",
             "### CONFIDENCE RESOLVED A CONFLICT ###"),
            ("the newest party wins", "### RECENCY RESOLVED A CONFLICT ###"),
            ("an unregistered rule allowed to resolve",
             "### UNREGISTERED RULE RESOLVED A CONFLICT ###"),
            ("resolution accepted with neither a `rule_id` nor a `decision_ref`",
             "### RESOLVED WITHOUT A RULE OR A DECISION ###"),
            ("an ownerless conflict allowed", "### OWNERLESS CONFLICT CREATED ###"),
            ("the raise and the freeze split into two commits",
             "### CONFLICT WITHOUT ITS FROZEN FIELD ###"),
            ("the partial unique index dropped", "### TWO OPEN CONFLICTS FOR ONE FIELD ###"),
            ("a second detection raises a new conflict instead of attaching",
             "### A SECOND CONFLICT WAS RAISED INSTEAD OF A PARTY ###"),
            ("`ConflictPartyAttached` not emitted", "### REPLAY REBUILT A STALE PARTY SET ###"),
            ("an attached party's provenance strengthened",
             "### PARTY PROVENANCE STRENGTHENED ###"),
            ("the tenant predicate dropped from the open-conflict lookup",
             "### CROSS-TENANT PARTY ACCEPTED ###"),
            ("`CF-6` resolving by ordinal position", "### ESCALATION RESOLVED BY POSITION ###"),
            ("an open conflict stops blocking the consequential action",
             "### CONSEQUENTIAL ACTION PROCEEDED ON AN OPEN CONFLICT ###"),
        ):
            assert mutant_phrase in M7_TASK_FLAT, (
                f"the task never requires the mutant {mutant_phrase!r}"
            )
            assert marker in m7.forbidden, f"the mutant {mutant_phrase!r} has no forbidden marker"

    def test_the_task_file_and_the_scenario_agree_on_the_contract(self, m7, cases, dimensions):
        """The two halves of one contract, checked against each other.

        A case the scenario asserts exists but the task never asks for is a case the builder is being
        failed on without being told. A literal the scenario requires but the task never states is
        the same defect one layer down.
        """
        for case in cases:
            assert case in M7_TASK, f"the scenario asserts case {case!r}; the task never names it"
        for dimension in dimensions:
            assert dimension in M7_TASK, (
                f"the scenario asserts dimension {dimension!r}; the task never names it"
            )
        for literal in m7.expect_visible:
            assert literal in M7_TASK, (
                f"the scenario requires the literal {literal!r}; the task never states it"
            )
        for marker in m7.forbidden:
            if marker.startswith("### ") and marker.endswith(" ###"):
                assert marker in M7_TASK, (
                    f"the scenario forbids {marker!r}; the task never names it"
                )
        for path in DELIVERABLES:
            assert path in M7_TASK, f"the scenario requires {path}; the task never names it"

    def test_the_task_states_the_canonical_machine_rather_than_a_generic_feature(self):
        for state in STATES:
            assert state in M7_TASK, f"the canonical state {state} is never named"
        for kind in KINDS:
            assert kind in M7_TASK, f"the canonical kind {kind} is never named"
        for transition in TRANSITIONS:
            assert transition in M7_TASK, f"the canonical transition {transition} is never named"
        for event in F7_EVENTS:
            assert event in M7_TASK, f"the F7 contract {event} is never named"
        assert "Five states" in M7_TASK, "the state count is never stated"
        assert "Do not add a sixth" in M7_TASK
        assert "Six kinds" in M7_TASK, "the kind count is never stated"
        assert "There is no seventh" in M7_TASK
        # `RESOLVED` is M9 Exception's, and importing it is how the sixth state arrives.
        for forbidden in FORBIDDEN_STATES:
            assert forbidden in M7_TASK, f"the task never warns off the {forbidden} state"
        for forbidden in FORBIDDEN_EVENTS:
            assert forbidden in M7_TASK, f"the task never warns off the {forbidden} event"

    def test_the_task_forces_the_authority_to_be_read_first(self):
        for source in (
            "PRODUCT.md",
            "CLAUDE.md",
            "docs/implementation/CURRENT.md",
            "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
            "docs/specifications/entities/10-conflict.md",
            "docs/specifications/state-machines/07-conflict.machine.md",
            "docs/specifications/state-machines/registry.md",
            "docs/specifications/events/07-conflict-events.md",
            "docs/specifications/events/registry.md",
            "docs/specifications/events/14-audit-security-events.md",
            "docs/architecture/decisions/ADR-007-identity-claims-and-conflict.md",
            "docs/architecture/decisions/ADR-002-state-classes-and-lineage.md",
            "docs/architecture/target-system-specification.md",
            "docs/specifications/entities/00-conventions.md",
            "src/freight_recon/checkpoint.py",
            "src/freight_recon/external_effect.py",
            "src/freight_recon/identity_binding_claim.py",
        ):
            assert source in M7_TASK, f"{source} is never named as authority"
        assert "the specification wins and you say so" in M7_TASK_FLAT
        assert "REPORT THE CONFLICT" in M7_TASK

    def test_the_task_states_the_blocking_invariant(self):
        """The sentence the entity spends forty-five points defending."""
        assert (
            "WHILE A CONFLICT IS OPEN, THE FIELD IS `conflicting` AND BLOCKS EVERY CONSEQUENTIAL "
            "ACTION ON THAT ENTITY" in M7_TASK_FLAT
        )
        assert "GR-10" in M7_TASK
        assert "AC-SAFE-017" in M7_TASK
        for state in OPEN_STATES:
            assert state in M7_TASK
        assert "`RAISED` already blocks" in M7_TASK_FLAT, (
            "the task never says that RAISED blocks too, which is the window a build session ships "
            "by reading 'open' as 'the OPEN state'"
        )

    def test_the_task_states_that_there_is_no_third_way_to_close(self):
        assert "THERE IS NO THIRD WAY" in M7_TASK
        for defeat in ("recency", "confidence", "source priority", "model", "timeout"):
            assert defeat in M7_TASK_FLAT.lower(), f"the task never rules out {defeat}"
        assert "NEYMA NEVER PICKS A WINNER" in M7_TASK
        assert "EXACTLY ONE of `rule_id` \\| `decision_ref`" in M7_TASK or (
            "EXACTLY ONE of `rule_id`" in M7_TASK
        )

    def test_the_task_states_what_a_conflict_is_not(self):
        """Entity §4's list, and the distinction the whole unit rests on."""
        assert "A CONFLICT IS NOT `unknown`. WE DO NOT LACK INFORMATION — WE HAVE TOO MUCH, AND IT DISAGREES." in M7_TASK_FLAT
        assert "I8" in M7_TASK
        assert "NOT AN ERROR" in M7_TASK


# --------------------------------------------------------------------------
# 2. Every declared risk is mapped to a command that can actually prove it
# --------------------------------------------------------------------------


#: The three literals that say M7 stopped where it was told to stop, and that no landed unit was
#: edited to get there. They are M7's own narration: `tasks/neyma_p6_m7.md` states them verbatim to
#: the builder as strings the M7 PROBE must print, and the probe is the only command in this scenario
#: that runs the machine and narrates what it found. No pytest anchor prints them, because none of
#: them runs M7's story.
DARK_POSTURE_LITERALS = (
    "THE M6 CLAIM MACHINE IS UNCHANGED",
    "THE M3 UNKNOWN_OUTCOME SEMANTICS ARE UNCHANGED",
    "THE M8, M9, M10 AND M12 MACHINES ARE NOT BUILT",
)


def declared_producers(scenario) -> dict[str, set[str]]:
    """Which named checks DECLARE each literal, statically.

    The only thing a scenario file knows about what a check emits is what the check itself declares:
    a command's `expect_contains` and a state check's `contains`. Free-form probe narration has no
    declared producer and is deliberately outside this map.
    """
    declared: dict[str, set[str]] = {}
    for spec in scenario.commands:
        if spec.name:
            for literal in spec.expect_contains:
                declared.setdefault(literal, set()).add(spec.name)
    for check in scenario.expect_state:
        if check.name:
            for literal in check.contains:
                declared.setdefault(literal, set()).add(check.name)
    return declared


def claims_needing_the_probe(scenario) -> list[tuple[str, list[str]]]:
    """Claims that require a dark-posture literal but do NOT name the probe that emits it.

    Returned rather than asserted so the same predicate can be run against a deliberately broken
    copy of the scenario in section 13 — a guard never seen to fail is a decoration.
    """
    broken: list[tuple[str, list[str]]] = []
    for claim in scenario.verifies:
        needed = [lit for lit in DARK_POSTURE_LITERALS if lit in claim.observations]
        if needed and PROBE_CHECK not in claim.checks:
            broken.append((claim.risk_category, needed))
    return broken


def unattributable_claims(scenario) -> list[tuple[str, str, list[str]]]:
    """Claims requiring a literal that some OTHER check declares, while naming none of them."""
    declared = declared_producers(scenario)
    broken: list[tuple[str, str, list[str]]] = []
    for claim in scenario.verifies:
        if not claim.checks:
            continue
        for literal in claim.observations:
            producers = declared.get(literal)
            if producers and not (producers & set(claim.checks)):
                broken.append((claim.risk_category, literal, sorted(producers)))
    return broken


class TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem:
    """A `verifies:` claim is resolved by matching its `observations:` against the output of the
    checks it NAMES, and nothing else. So a claim that names commands which cannot emit the literal
    it requires is unfalsifiable in the wrong direction: it fails closed forever, on every run, no
    matter how correct the product is — and it fails wearing the costume of a product defect.

    That is exactly what blocked the M6 run `20260825-204229`. Its `regression` claim required two
    "NOT BUILT" literals from the P3/P4/P5 and M1-M5 pytest anchors, which narrate nothing; the M6
    probe emitted both, the scenario's own `expect_visible` checks for both PASSED, and the gate
    still reported a standing [P1] coverage gap, because the probe was not named. No change inside
    Neyma could have closed it.

    `Scenario._claims_name_a_check_that_can_emit_them` now refuses the statically decidable half of
    this at load time. The residue is free-form narration, which nothing can attribute by reading
    YAML, so it is pinned here for the literals whose producer this repository actually knows.
    """

    def test_the_regression_claim_names_the_probe_that_proves_the_seams_are_intact(self, m7):
        """The M7-owned half of the regression claim: M6 unchanged, M3 unchanged, no neighbouring
        machine built. The probe is the command that observes it from inside M7's own story, so the
        claim must name the probe."""
        regression = [c for c in m7.verifies if c.risk_category == "regression"]
        assert regression, "the M7 scenario no longer declares a regression claim"
        claim = regression[0]
        for literal in DARK_POSTURE_LITERALS:
            assert literal in claim.observations, (
                f"the regression claim no longer requires {literal!r}. The seam proof is not "
                "optional: removing it is how this defect gets 'fixed' by weakening the oracle"
            )
        assert PROBE_CHECK in claim.checks, (
            "the regression claim requires the seam literals but does not name the M7 probe. Only "
            f"{PROBE_CHECK!r} runs the machine and narrates what it found; the pytest anchors it "
            "names print no such sentence, so the claim could never be established"
        )

    def test_every_claim_requiring_a_dark_posture_literal_names_the_probe(self, m7):
        """Stated once, for the whole file rather than for one claim: wherever the scenario asks for
        this proof, it must ask the command that produces it."""
        assert claims_needing_the_probe(m7) == []

    def test_the_dark_posture_literals_are_still_required_somewhere(self, m7):
        """The other way to make the gap go away is to stop asking. This refuses that."""
        for literal in DARK_POSTURE_LITERALS:
            assert literal in m7.expect_visible, (
                f"{literal!r} is no longer an expected observation of the M7 scenario"
            )
            assert any(literal in claim.observations for claim in m7.verifies), (
                f"{literal!r} is expected but no declared risk rests on it any more"
            )

    def test_a_claim_may_not_require_an_observation_its_checks_cannot_declare(self, m7):
        """The general half, enforced at load time — asserted here against the real M7 file so the
        shipped scenario is covered by the invariant and not merely by the unit test of it."""
        assert unattributable_claims(m7) == []

    def test_every_declared_risk_names_at_least_one_check_and_one_observation(self, m7):
        """A claim with an oracle on only one side is half a claim.

        `RiskClaim` requires one of the two. This file requires both for M7, because a claim with no
        named check matches its literals against EVERYTHING the run observed — which for a scenario
        that runs nine pytest anchors is a very large haystack, and an accidental match in it is
        coverage nobody established.
        """
        for claim in m7.verifies:
            assert claim.checks, f"the {claim.risk_category!r} claim names no check"
            assert claim.observations, f"the {claim.risk_category!r} claim names no observation"

    def test_the_effect_families_are_deliberately_left_undeclared(self, m7):
        """M7 touches the outside world not at all, and the absence is the point.

        M3 remains the single effect authority, and a `READBACK_VS_APPROVED` conflict is evidence
        BESIDE an `UNKNOWN_OUTCOME` rather than a replacement for it. A run that names
        `ambiguous_external_effect` or `timeout_after_effect` as a blocking M7 risk is naming a risk
        about another unit's behaviour, and it must generate a case for it or block — not find a
        permanent declaration here waving it through.
        """
        declared = m7.declared_risk_categories()
        assert "ambiguous_external_effect" not in declared
        assert "timeout_after_effect" not in declared


# --------------------------------------------------------------------------
# 3. The database is the oracle, not the probe's narration
# --------------------------------------------------------------------------


class TestPersistedStateIsTheOracle:
    """The sentences a green test suite can state while the database enforces none of them.

    "there is one open conflict per field", "a conflict has a human owner", "a resolution carries
    exactly one basis", "every party keeps its own provenance" and "there are five states" are each
    a property of the SCHEMA. A probe that prints them proves it printed them.
    """

    def test_the_scenario_reads_the_database_at_all(self, m7):
        assert m7.expect_state, "no persisted state is inspected; the probe speaks for itself"

    def test_the_five_states_are_asserted_and_there_is_no_sixth(self, m7):
        guard = [c for c in m7.expect_state if "state vocabulary" in c.command]
        assert guard, "the state set is never read out of the DDL"
        declared = guard[0]
        for state in STATES:
            assert f"'{state}'" in declared.contains, f"{state} is not asserted in the CHECK"
        for forbidden in FORBIDDEN_STATES:
            assert f"'{forbidden}'" in declared.not_contains, (
                f"nothing prevents an invented {forbidden} state"
            )

    def test_the_six_kinds_are_asserted_and_there_is_no_seventh(self, state_checks):
        """A seventh kind is how "we picked the likelier source" arrives wearing a canonical name."""
        guard = state_checks.get(
            "the six canonical conflict kinds are a database constraint, and there is no seventh"
        )
        assert guard, "the kind set is never read out of the DDL"
        for kind in KINDS:
            assert f"'{kind}'" in guard, f"{kind} is not asserted in the CHECK"

    def test_the_resolution_basis_and_the_owner_are_asserted_as_checks(self, state_checks):
        """Entity §16, and entity §37's "structurally impossible".

        `RESOLVED_BY_RULE` without a `rule_id` and `RESOLVED_BY_HUMAN` without a `decision_ref` are
        the two shapes that turn "there is no third way" into a comment — and an ownerless Conflict
        is the third. All three are CHECK constraints or they are docstrings.
        """
        guard = state_checks.get(
            "the resolution basis and the owner are CHECK constraints, not conventions"
        )
        assert guard, "nothing asserts the resolution basis is a database constraint"
        assert "RESOLVED_BY_RULE requires rule_id: True" in guard
        assert "RESOLVED_BY_HUMAN requires decision_ref: True" in guard
        assert "owner_id NOT NULL: True" in guard
        assert "entity_ref NOT NULL: True" in guard
        assert "field NOT NULL: True" in guard
        for column in ("'conflict_id'", "'decision_ref'", "'entity_ref'", "'field'", "'kind'",
                       "'owner_id'", "'rule_id'", "'state'", "'tenant'", "'version'"):
            assert column in guard, f"the conflict table is not asserted to carry {column}"

    def test_one_open_conflict_is_asserted_as_a_partial_unique_index_over_all_three_open_states(
        self, state_checks
    ):
        """Entity §17. "At most one open conflict" is a hope about a code path until it is a partial
        unique index, and an application-level check-then-insert is exactly what two concurrent
        detectors both pass. Machine §17 states the consequence: a second detection attaches a party
        (CF-7), not a new conflict.

        All three open states are asserted individually. An index whose `WHERE` names only `OPEN`
        leaves a window at `RAISED` and another at `ESCALATED`, and the window is where the second
        conflict gets in.
        """
        guard = state_checks.get(
            "one open conflict per field is a PARTIAL UNIQUE index over the three open states, "
            "tenant-first"
        )
        assert guard, "nothing asserts the one-open-conflict rule is an index"
        assert "CREATE UNIQUE INDEX" in guard
        assert "entity_ref" in guard
        assert "field" in guard
        for state in OPEN_STATES:
            assert state in guard, f"the index is not asserted to be PARTIAL on {state}"

    def test_the_owner_is_asserted_as_a_foreign_key_into_tenant_humans(self, state_checks):
        """"A named human" is decoration while it is a text column.

        Machine §5: the owner is a named human, from `RAISED`. A FOREIGN KEY into `tenant_humans` is
        the version of that sentence a database enforces — exactly the argument M1 made for
        `owner_id`, M4 made for `granted_by` and M6 made for the human behind a `decision_ref`.
        """
        guard = state_checks.get(
            "the conflict owner is a FOREIGN KEY into tenant_humans, not a convention"
        )
        assert guard, "nothing asserts the conflict owner is a foreign key"
        assert "owner is FK-backed into tenant_humans: True" in guard
        assert "a party points at its parent conflict: True" in guard
        assert "foreign keys into a table nobody built: []" in guard

    def test_party_provenance_is_asserted_as_a_canonical_check(self, state_checks):
        """Entity §13 and machine §31: each party carries ITS OWN `provenance_class`, and an
        `INFERRER_VS_OWNER` conflict specifically records that one party is `OWNER_ASSERTED` — the
        evidence of why the inferrer did not overwrite it. `ER-14`: no consumer may strengthen
        provenance. A party table with a free-text provenance column is a laundering hole with a
        schema."""
        guard = state_checks.get(
            "every conflict party carries its own canonical provenance_class, tenant-first"
        )
        assert guard, "nothing asserts party provenance is a database constraint"
        for provenance in ("SYSTEM_IMPORTED", "OWNER_ASSERTED", "LINKER_INFERRED",
                           "MODEL_EXTRACTED", "MODEL_INFERRED", "RECONCILED"):
            assert f"'{provenance}'" in guard, f"{provenance} is not asserted in the party CHECK"
        assert "'party_ref'" in guard
        assert "party tenant NOT NULL: True" in guard

    def test_inventing_an_expiry_or_cancellation_surface_is_a_scenario_failure(self, m7, state_checks):
        """And the rule is preserved by a check over the corpus, not a hope.

        The way a build session breaks "a conflict never expires" is not by arguing with it. It is by
        adding a TTL, a sweep, a stale-conflict reaper or an auto-resolver because those felt like
        hygiene — or by adding a `CF-8` row to close the cancellation `M7-AQ-3` leaves open.

        The scan deliberately does NOT look for `auto_resolve` or `timer_resolve` as identifiers: the
        probe must ATTEMPT both to prove `GR-1` refuses them, so a scan that flagged them would force
        the illegal cases out of existence to stay green.
        """
        guard = state_checks.get(
            "no conflict expiry, sweep, deletion, cancellation transition or synonym event was "
            "invented"
        )
        assert guard, "nothing asserts that no expiry or cancellation surface was invented"
        assert "invented expiry/cancellation surfaces: []" in guard
        assert "invented expiry/extra transition rows: []" in guard
        assert "expiry columns on conflicts: []" in guard
        command = [c for c in m7.expect_state if c.contains == guard][0].command
        for event in FORBIDDEN_EVENTS:
            assert event in command, f"the invention sweep does not look for {event}"
        assert "CF-8" in command, "nothing prevents an eighth transition row being written"
        for allowed in ("auto_resolve", "timer_resolve"):
            assert allowed not in command, (
                f"the sweep looks for {allowed!r}, which the probe legitimately needs in order to "
                "ATTEMPT the illegal transition GR-1 must refuse"
            )

    def test_the_five_f7_contracts_are_used_and_no_sixth_is_minted(self, state_checks):
        guard = state_checks.get("M7 uses the five registered F7 contracts and invents no sixth")
        assert guard, "nothing checks the event names M7 uses against the canonical registry"
        declared = " ".join(guard)
        for event in F7_EVENTS:
            assert f"'{event}'" in declared, f"{event} is not asserted registered"
        assert "synonym events registered: []" in guard
        assert "unregistered names in the machine: []" in guard
        assert "machine source: present" in guard, (
            "the unregistered-name sweep has no proven population: it would print an empty list "
            "against a missing file and read as a pass"
        )

    def test_the_resolution_basis_is_read_out_of_the_contract_projection(self, state_checks):
        """"Exactly one of rule_id or decision_ref" is asserted from the corpus, not from prose.

        The P5 contract layer already refuses a `ConflictResolved` carrying neither or both, because
        `event_contracts_data.json` declares them a required one-of. Reading that fact out of the
        projection is what makes the task's §3.4 a READING of the corpus rather than an assertion
        about it — and it is a mechanism M7 inherits rather than one it must invent.
        """
        guard = state_checks.get("M7 uses the five registered F7 contracts and invents no sixth")
        assert guard
        assert "ConflictResolved requires exactly one of: [(['decision_ref', 'rule_id'], True)]" in guard

    def test_the_delegating_producer_list_is_read_out_of_the_projection(self, state_checks):
        """`CF-6` is not a registered producer of `ConflictResolved`, and that is the whole proof.

        Machine §14's `CF-6` cell reads `DELEGATES_TO:RESOLVED_BY_RULE=CF-3;RESOLVED_BY_HUMAN=CF-4`.
        Since the projection lists only `CF-3` and `CF-4`, an escalated resolution must emit under
        the id its TARGET STATE selects — which is exactly "resolved by target state, never
        positionally", enforced by a guard that already exists.
        """
        guard = state_checks.get("M7 uses the five registered F7 contracts and invents no sixth")
        assert guard
        assert "ConflictResolved producers: ['CF-3', 'CF-4']" in guard

    def test_the_cross_family_producer_list_is_read_out_of_the_projection(self, state_checks):
        """`ConflictRaised` has THREE registered producers, and that is a fact about the corpus.

        It is what makes `M7-AQ-1` and `M7-AQ-2` real questions rather than this file's opinion — and
        it is the same mechanical move M6 made to establish that emitting `ConflictRaised` at `IB-6`
        was canonical rather than writing another machine's contract.
        """
        guard = state_checks.get("M7 uses the five registered F7 contracts and invents no sixth")
        assert guard
        assert "ConflictRaised producers: ['CF-1', 'EF-4c', 'IB-6']" in guard

    def test_the_dark_posture_is_measured_over_the_shipped_package(self, state_checks):
        assert (
            "production importers of conflict: []"
            in state_checks.get(
                "M7 has no production caller — the dark posture, measured over the shipped package",
                [],
            )
        )
        assert (
            "scripts reaching conflict: ['probe_phase6_conflict.py']"
            in state_checks.get(
                "the only thing outside the package that reaches M7 is the verification probe itself",
                [],
            )
        )

    def test_no_live_conflict_inbox_can_arrive_with_the_unit(self, m7, state_checks):
        """M7's product form is a queue of frozen fields a human resolves. That queue is the thing
        that must not arrive with the engine primitive."""
        guard = state_checks.get(
            "no live conflict inbox: nothing joins the conflict machine to an inbound or outbound "
            "channel"
        )
        assert guard, "nothing prevents a live conflict inbox arriving with M7"
        assert "modules joining the conflict machine to a channel: []" in guard
        command = [c for c in m7.expect_state if c.contains == guard][0].command
        for channel in ("email_triage", "ingestion", "extraction", "inbox_brain",
                        "action_callback", "slack_adapter", "tms_adapter", "mailbox_intake"):
            assert channel in command, f"the inbox sweep does not look at {channel}"

    def test_m7_authorizes_nothing(self, state_checks):
        """Entity §38 makes an open Conflict an INPUT to checkpoint step 4; it never becomes a second
        gate. Machine §16's "dominates all machines that read the field" is a statement about
        PRECEDENCE, not about who admits execution."""
        guard = state_checks.get("the checkpoint is still the only thing that mints a gate decision")
        assert guard
        assert "modules that MINT a gate decision: ['checkpoint.py']" in guard

    def test_m8_m9_m10_and_m12_are_not_invented_along_the_way(self, state_checks):
        """`CF-5` reaches for a deadline (M8), a blocking conflict looks like an Exception (M9),
        machine §27 ends a resolution in a Compensation (M10), and `CF-3` needs a REGISTERED RULE
        (M12) — the most tempting of the four, because "no rules exist yet" is exactly the gap a
        builder closes by writing a small rule registry."""
        guard = state_checks.get(
            "the expectation, exception, compensation and rule seams are fed without M8, M9, M10 or "
            "M12 being built"
        )
        assert guard, "nothing prevents M7 building a neighbouring machine"
        assert "mints another machine event: []" in guard
        assert "m8/m9/m10/m12 tables created by m7: []" in guard
        assert "machine and migration present: True" in guard, (
            "the foreign-event sweep has no proven population"
        )

    def test_the_durable_timer_substrate_is_asserted_present(self, state_checks):
        """`CF-5` rides P5's EXISTING durable timers. A second timer mechanism invented inside M7 is
        the shape a sweep arrives in."""
        guard = state_checks.get(
            "a freshly created canonical database carries the conflict layer, tenant-first"
        )
        assert guard
        assert "durable_timers" in guard
        assert "conflicts" in guard
        assert "conflict_parties" in guard
        assert "problems: []" in guard


# --------------------------------------------------------------------------
# 4. The three recorded authority conflicts stay open
# --------------------------------------------------------------------------


class TestTheTaskPreservesTheAuthorityConflicts:
    """M4's, M5's and M6's §3.9 lesson, applied to a corpus that disagrees with itself three times
    about M7.

    A resolution the builder invented is worse than a blocked run, because it looks like agreement.
    """

    def test_all_three_questions_are_named_with_both_sides(self):
        for question in ("M7-AQ-1", "M7-AQ-2", "M7-AQ-3"):
            assert question in M7_TASK, f"{question} is never raised"
        # AQ-1: entity §15/§17/§33 require a row and a dedup index; M6 is landed and writes none;
        # and a coordination event may not instruct a consumer to transition.
        assert "how does `IB-6`'s `ConflictRaised` materialize an M7 Conflict row?" in M7_TASK
        assert "IB-6" in M7_TASK and "ER-1" in M7_TASK
        # AQ-2: the registry names EF-4c a producer; the shipped M3 emits VerificationConflict alone.
        assert "is `EF-4c` required to emit `ConflictRaised`, and whose change is that?" in M7_TASK
        assert "VerificationConflict" in M7_TASK
        # AQ-3: §25 describes cancellation; §14 enumerates no row, §4 no state, no event registered.
        assert "how does a Conflict get cancelled when the disagreement disappears?" in M7_TASK
        assert "GR-1" in M7_TASK

    def test_each_question_names_what_every_reading_agrees_on(self):
        """The builder is not blocked by the conflict. It is blocked from RESOLVING it — and told
        exactly what it may still build."""
        # One per question, inside that question's own block — a count over the whole file would
        # also match §3.6's statement of the M6 seam, which is the same discipline one section up.
        section = M7_TASK[M7_TASK.index("### 3.8"):M7_TASK.index("### 3.9")]
        blocks = re.split(r"(?=\*\*`M7-AQ-)", section)[1:]
        assert len(blocks) == 3, f"§3.8 holds {len(blocks)} question blocks, not three"
        for question, block in zip(("M7-AQ-1", "M7-AQ-2", "M7-AQ-3"), blocks):
            assert question in block
            assert "**Every reading agrees on:**" in block, (
                f"{question} states both sides and never says what may still be built"
            )
        assert "Do not amend a specification to close it" in M7_TASK_FLAT
        assert "do not edit `identity_binding_claim.py`" in M7_TASK_FLAT
        assert "Do not edit `external_effect.py`." in M7_TASK_FLAT
        assert (
            "Do not invent a cancellation transition, a `CANCELLED` state, or a "
            "`ConflictCancelled` event" in M7_TASK_FLAT
        )

    def test_the_scenario_asserts_nothing_about_the_open_questions(self, m7):
        """The scenario must not encode a resolution either.

        There is no required literal about how `IB-6`'s event materializes a row, none about whether
        `EF-4c` must emit one, and none about how a Conflict is cancelled.
        """
        visible = " ".join(m7.expect_visible)
        for invented in FORBIDDEN_EVENTS + ("ConflictDetected",):
            assert invented not in visible, (
                f"the scenario requires an unregistered event name {invented!r}, which resolves an "
                "authority question by minting a name"
            )
        assert "IB-6 WRITES A CONFLICT ROW" not in visible.upper()
        assert "EF-4C RAISES" not in visible.upper()
        assert "CONFLICT CANCELLED" not in visible.upper()
        # What it DOES require is the part every reading agrees on.
        assert "THE M6 CLAIM MACHINE IS UNCHANGED" in m7.expect_visible
        assert "THE M3 UNKNOWN_OUTCOME SEMANTICS ARE UNCHANGED" in m7.expect_visible
        assert "A PARTY RETRACTION NEVER SILENTLY CLOSES THE CONFLICT" in m7.expect_visible
        assert "AT MOST ONE OPEN CONFLICT PER TENANT, ENTITY AND FIELD" in m7.expect_visible

    def test_v5_is_explicitly_left_unresolved(self):
        """V5 is the registered conflict-resolution rule set, and it is a customer/domain question.

        The fail-closed default is: no applicable registered rule ⇒ every conflict goes to a human. A
        builder that "discovers" that the TMS always beats the portal has invented a customer's
        operating policy, and ADR-007 §8 says exactly why that is different from a registered rule
        with an id: auditability.
        """
        assert "V5" in M7_TASK
        assert "NEEDS VALIDATION" in M7_TASK
        assert "NOT A BLOCK" in M7_TASK
        assert (
            "The fail-closed default is: no applicable registered rule ⇒ EVERY conflict goes to a "
            "human." in M7_TASK_FLAT
        )
        assert "DO NOT DECIDE WHICH FREIGHT SYSTEM WINS." in M7_TASK
        assert "The rule SET is empty, and M7 ships it empty." in M7_TASK_FLAT

    def test_the_f14_scoping_decision_is_stated_not_guessed(self):
        """Four F14 tripwires are in play and exactly one is M7's.

        `IllegalTransitionAttempted` is `GR-1` and mandatory. `ProvenanceStrengtheningAttempted` is
        scoped to Phase 7 by name in `CURRENT.md`, exactly as it was for M5 and M6.
        `OwnerAssertedOverwriteAttempted` names M6 as its sole producer.
        `CrossTenantAccessAttempted` is the inbox's. The task must give the builder all four
        positions rather than one guess.
        """
        assert "IllegalTransitionAttempted" in M7_TASK
        assert "is MANDATORY and is yours" in M7_TASK
        assert "ProvenanceStrengtheningAttempted" in M7_TASK
        assert "is NOT yours" in M7_TASK
        assert "the F14 emission is not yours" in M7_TASK_FLAT
        assert "OwnerAssertedOverwriteAttempted" in M7_TASK
        assert "CrossTenantAccessAttempted" in M7_TASK


# --------------------------------------------------------------------------
# 5. The seams — feed them, never edit the landed unit on the other side
# --------------------------------------------------------------------------


class TestTheSeamsAreScopedToM7:
    """M5's and M6's lesson, inverted. For M5 and M6 the danger was building the machine the seam
    pointed AT. For M7 the machine IS the one that was pointed at, and the danger is the opposite:
    reaching BACK into the two landed units that already point here and tidying them until the
    authority question disappears.
    """

    def test_the_task_states_the_m6_seam_and_forbids_editing_it(self):
        assert "The M6 seam" in M7_TASK
        assert "`['CF-1', 'IB-6', 'EF-4c']`" in M7_TASK_FLAT or "'CF-1', 'IB-6', 'EF-4c'" in M7_TASK
        assert "DO NOT REWRITE M6." in M7_TASK
        assert (
            "DO NOT MINT A SECOND `ConflictRaised` FOR A DISAGREEMENT M6 HAS ALREADY ANNOUNCED"
            in M7_TASK
        )
        assert "DO NOT MINT A SYNONYM." in M7_TASK
        assert "ConflictDetected" in M7_TASK, (
            "the task never names the trigger a builder would mint as an event to close the seam"
        )
        assert "DO NOT SILENTLY SWALLOW IT EITHER." in M7_TASK

    def test_the_task_states_the_m3_seam_and_protects_unknown_outcome(self):
        assert "The M3/M4 seam" in M7_TASK
        assert "UNKNOWN_OUTCOME` NEVER SILENTLY BECOMES SUCCESS OR FAILURE" in M7_TASK
        assert "GR-6" in M7_TASK
        assert "RealityEstablished" in M7_TASK
        assert "M7 MUST NOT REWRITE, SHORTEN OR ROUTE AROUND THAT." in M7_TASK
        assert "Do not modify M3 to make M7 easy." in M7_TASK
        assert "Do not rebuild M4." in M7_TASK

    def test_the_task_forbids_editing_the_p3_kernel_while_feeding_it(self):
        """P3 remains the gate minter, and step 4 already exists. M7 feeds it; it does not become a
        second one, and it does not edit `checkpoint.py`."""
        assert "Do not create a second gate authority" in M7_TASK_FLAT
        assert "Do not edit `checkpoint.py`." in M7_TASK_FLAT
        assert "NativeClaim" in M7_TASK
        assert "EvidenceCondition" in M7_TASK
        assert "step 4" in M7_TASK
        assert "M3 remains the single effect authority" in M7_TASK_FLAT

    def test_the_task_determines_the_smallest_field_condition_implementation(self):
        """The acceptance table names M7's state oracle "row + field condition" and no universal
        field-condition table exists. The task must DETERMINE the smallest shape rather than leave a
        builder to invent a projection store."""
        assert "The Conflict row IS the durable field condition." in M7_TASK
        assert "Do not build a projection store" in M7_TASK_FLAT
        assert "K-2" in M7_TASK, "the task never says what an `entity_ref` actually points at"
        assert "name the clause" in M7_TASK_FLAT, (
            "the task determines the shape but leaves the builder no way to escalate if canon "
            "forbids it"
        )

    def test_the_task_states_the_foreign_keys_that_have_a_table_to_point_at(self):
        """Entity §18 names five references and only some have a target today. A builder that takes
        §18 literally builds M12 and the Evidence Store to satisfy it."""
        assert "The foreign keys entity §18 names, and what exists to point at" in M7_TASK
        for column in ("entity_ref", "parties", "rule_id", "decision_ref", "owner_id"):
            assert column in M7_TASK, f"the reference {column} is never discussed"
        assert "build the foreign keys whose targets exist" in M7_TASK_FLAT
        assert "K-1" in M7_TASK, "the polymorphic decision_ref precedent is never named"
        assert "name the clause and stop" in M7_TASK_FLAT

    def test_the_task_states_the_m10_seam_as_a_deferral_rather_than_a_build(self):
        """Machine §27 ends a resolution in a Compensation, and the cheapest way to satisfy that
        sentence is to build M10."""
        assert "M10 IS NOT BUILT" in M7_TASK
        assert "no fabricated completed Compensation" in M7_TASK_FLAT
        for forbidden in ("`compensations` table", "`CM-*`", "CompensationRequired"):
            assert forbidden in M7_TASK, f"the task never forbids building {forbidden}"
        assert "the consumer half is M6's, and M6 is landed" in M7_TASK_FLAT
        assert "does not write\n`identity_binding_claims`" in M7_TASK or (
            "does not write `identity_binding_claims`" in M7_TASK_FLAT
        )


# --------------------------------------------------------------------------
# 6. The vocabulary is safe, and visible to the generator
# --------------------------------------------------------------------------


class TestTheM7Vocabulary:
    @pytest.fixture
    def approved(self):
        return ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )

    def _planner(self, tmp_path: Path, configured: list[str]) -> ScenarioPlanner:
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True, approved_commands=configured),
            reasoner=ScriptedReasoner([{"risks": [], "scenarios": []}]),
            base_scenario=load_scenario(M7_PATH),
            permanent_scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            founder=FakeFounder(),
        )

    def test_every_case_is_approved_by_the_bare_probe_alone(self, tmp_path, cases):
        """No enumeration needed for SAFETY — only for visibility."""
        planner = self._planner(tmp_path, [])
        for case in cases:
            ok, why = planner.approved_commands.approves(f"{PROBE} --case {case}")
            assert ok, f"{case}: {why}"

    def test_no_case_name_trips_the_command_guard(self, approved, cases):
        """A case name is part of a command string, and the guard is a token matcher.

        M6 shipped a case called `...-between-render-and-click` and the boundary hard-blocked the
        bare token `render` as deploy tooling — so the one case the generator most needed to compose
        was unreachable. The guard was right and the name was wrong. This is that lesson, asserted
        instead of relearned.
        """
        for case in cases:
            ok, why = approved.approves(f"{PROBE} --case {case}")
            assert ok, (
                f"the case name {case!r} is refused by the boundary ({why}). The guard is right; "
                "rename the case"
            )

    def test_the_whole_mutation_axis_composes_without_widening_the_boundary(
        self, approved, dimensions
    ):
        """The property that makes the mutation axis safe rather than clever.

        Approval matches by PREFIX and refuses shell composition in the tail, so every combination of
        dimensions is already permitted by the single bare probe entry — the axis buys the generator
        a large bounded space and buys the boundary nothing to defend.
        """
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case open-conflict-blocks-consequential-action "
                f"--inject {fault} --concurrency 8 --delay-ms 5000 --repeat 5 "
                "--tenants 3 --parties 8 --age-ms 60000 --confidence 1.0 --seed 4211"
            )
            ok, why = approved.approves(command)
            assert ok, f"{fault}: {why}"

    @pytest.mark.parametrize(
        "command",
        [
            # A command nobody wrote down.
            "curl https://api.example.com/conflicts",
            ".venv/bin/python scripts/send_real_invoice.py",
            "rm -rf src/freight_recon",
            # A live conflict inbox or product surface, which is precisely what M7 must not grow.
            ".venv/bin/python -m freight_recon.ops_control --resolve-conflicts",
            ".venv/bin/python scripts/slack_probe.py --post-conflict-queue",
            # The approved probe, extended with composition.
            f"{PROBE} --case open-conflict-blocks-consequential-action; curl https://evil.example.com",
            f"{PROBE} --case open-conflict-blocks-consequential-action && rm -rf /",
            f"{PROBE} | tee /tmp/out",
            f"{PROBE} > /etc/hosts",
            f"{PROBE} --case $(whoami)",
            f"{PROBE} --case `whoami`",
            # A newline is whitespace; normalization would hide it, so the raw string is scanned for
            # control characters first.
            f"{PROBE}\ncurl https://evil.example.com",
            # A prefix that is not a prefix.
            ".venv/bin/python scripts/probe_phase6_conflict.py.bak",
        ],
    )
    def test_a_command_outside_the_vocabulary_is_refused(self, approved, command):
        ok, why = approved.approves(command)
        assert not ok, f"escaped the approved set: {command!r}"
        assert why, "a refusal must say why"

    def test_a_dimension_value_carrying_shell_is_still_refused(self, approved):
        """The axis is argument-only. A flag is not a hole."""
        for hostile in ("$(id)", "`id`", "a;id", "a|id", "a>/etc/hosts", "a&&id"):
            ok, _ = approved.approves(f"{PROBE} --case tenant-isolation --inject {hostile}")
            assert not ok, f"a dimension value smuggled shell through: {hostile!r}"

    def test_the_probe_with_an_ordinary_case_tail_is_still_allowed(self, approved):
        """The boundary has to let the real vocabulary through, or it has only made generation
        useless rather than safe."""
        ok, why = approved.approves(f"{PROBE} --case escalated-resolves-by-registered-rule")
        assert ok, why

    def test_the_neighbouring_probes_stay_reachable_through_the_scenario(self, approved):
        """They are not enumerated in the config, and they do not need to be.

        M7 co-commits with none of them — it adds two tables and edits the canonical schema, which is
        a REGRESSION relationship. Writing their bare probes into `p6_m7_conflict.yaml` as regression
        anchors already approves every `--case` tail of each, because approval matches by prefix.
        """
        for command in (
            ".venv/bin/python scripts/probe_phase6_identity_binding_claim.py --case tenant-isolation",
            ".venv/bin/python scripts/probe_phase6_observation.py --case tenant-isolation",
            ".venv/bin/python scripts/probe_phase6_approval.py --case tenant-isolation",
        ):
            ok, why = approved.approves(command)
            assert ok, f"{command}: {why}"

    def test_the_rendered_brief_actually_shows_the_m7_vocabulary(self, tmp_path):
        """The brief truncates the approved list, silently. A vocabulary the generator never sees is
        a vocabulary it cannot choose from."""
        vocabulary = _local_vocabulary()
        if not any("probe_phase6_conflict.py" in entry for entry in vocabulary):
            pytest.skip("no local driver.config.yaml enumerating the M7 vocabulary")

        planner = self._planner(tmp_path, vocabulary)
        planner.plan_initial(task="Build P6/M7 Conflict", unit=None, run_id="r-m7")
        brief = planner.reasoner.briefs[0].render()

        assert PROBE in brief, "the deterministic M7 entry point is not in the brief"
        missing = [
            entry.split("--case ", 1)[1].split()[0]
            for entry in vocabulary
            if "probe_phase6_conflict.py --case " in entry and entry not in brief
        ]
        assert not missing, (
            "the approved-command list was truncated before these M7 cases: "
            f"{missing}. The brief renders at most {MAX_RENDERED_COMMANDS} commands; the "
            f"approved set now holds {len(planner.approved_commands)}."
        )

    def test_the_approved_set_still_fits_inside_what_the_brief_renders(self, tmp_path):
        """Approved commands sort ASCII and every probe entry begins `scripts/probe_...`, so they
        sort LAST: an approved set larger than the render bound loses the probe vocabulary first, and
        loses it silently."""
        planner = self._planner(tmp_path, _local_vocabulary())
        assert len(planner.approved_commands) <= MAX_RENDERED_COMMANDS, (
            f"{len(planner.approved_commands)} approved commands but the generation brief renders "
            f"only the first {MAX_RENDERED_COMMANDS} — the M7 vocabulary sorts last and is now "
            "invisible to the generator."
        )


# --------------------------------------------------------------------------
# 7. Dynamic generation can close an M7 coverage gap, safely
# --------------------------------------------------------------------------


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close.

    Built as the planner builds one: the citation lives on the provenance, and a coverage-gap case
    that cannot name a risk from this run's own register is refused before it reaches the boundary at
    all.
    """
    return GeneratedScenario(
        id="gen-m7-second-detector",
        title="a third source disagrees while the first conflict is still open",
        purpose=(
            "a second detection must attach a party to the conflict that already exists; two open "
            "conflicts on one field is the state the partial unique index exists to make impossible"
        ),
        risk_category=RiskCategory.CONCURRENCY,
        priority=Priority.P0,
        rationale="the identified duplicate-conflict risk had no scenario behind it",
        requirement_reference="P6/M7",
        product_principle_reference="never-silently-choose",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared state, so "
            "nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m7-task",
            session_id="scripted",
            generating_risk="a second detector could open a second conflict on the same field",
            source_risks=[risk_key],
        ),
        actions=[{"kind": "command", "name": "race the detectors", "command": command}],
        # `concurrency` is a family whose claims are about a TABLE — "there is one open conflict" is
        # not something a probe can prove by printing it. This is the mechanical form of the
        # rubric's "a 200 is not success".
        persisted_state_checks=[
            GeneratedStateCheck(
                name="the conflict layer is still tenant-first and readable",
                command=STATE_ORACLE,
                contains=["problems: []", "conflicts"],
            )
        ],
        expected_observations=["A SECOND DETECTION ATTACHES A PARTY, NEVER A SECOND CONFLICT"],
        forbidden_observations=["### TWO OPEN CONFLICTS FOR ONE FIELD ###"],
    )


class TestGenerationClosesM7GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-second-detector",
            description="a second detector could open a second open conflict on the same field",
            risk_category=RiskCategory.CONCURRENCY,
            severity=Priority.P0,
            basis="entity §17's partial unique index is the mechanism this unit rests on",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m7", "p6", "m7"},
                principle_tokens={"never-silently-choose"},
                known_risk_ids={risk.key, "R-second-detector"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m7_vocabulary_is_accepted(self, context):
        ctx, risk = context
        command = (
            f"{PROBE} --case second-detection-attaches-a-party-not-a-new-conflict "
            "--inject concurrent-detection --concurrency 6 --parties 4 --seed 7"
        )
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M7 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_the_whole_mutation_axis_is_reachable_from_a_gap_case(self, context, dimensions):
        ctx, risk = context
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case second-detection-attaches-a-party-not-a-new-conflict "
                f"--inject {fault} --concurrency 4 --delay-ms 40 --parties 3 --age-ms 1000 "
                "--confidence 1.0 --seed 11"
            )
            accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
            assert accepted, f"{fault}: {rejected}"

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario('python -c "import conflict; conflict.resolve()"', risk.key)], ctx
        )
        assert not accepted
        assert rejected
        reasons = rejected[0][1]
        assert any("approved" in r.lower() for r in reasons), reasons

    def test_a_gap_case_touching_repository_authority_is_refused(self, context):
        """A verification scenario observes the product; it never edits the rules the product is
        judged against — and for this unit that includes the ADR the guards are derived from."""
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario(f"{PROBE} --case x docs/implementation/CURRENT.md", risk.key)], ctx
        )
        assert not accepted
        reasons = rejected[0][1]
        assert any("authority" in r.lower() for r in reasons), reasons

    def test_an_uncovered_p0_m7_risk_blocks_acceptance(self):
        """Coverage is not a tally. A risk the run itself called P0 with no passing scenario behind
        it prevents an ACCEPT even when everything that DID run was green."""
        from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
        from neyma_product_driver.scenario_suite import (
            Origin,
            Outcome,
            ScenarioOutcome,
            SuiteResult,
        )

        passing = ScenarioOutcome(
            scenario_id="gen-raise",
            scenario_name="gen-raise",
            origin=Origin.GENERATED,
            outcome=Outcome.PASSED,
            required=True,
            risk_category="authorization",
            evidence_path="/runs/gen-raise",
            evidence_verified=True,
        )
        result = SuiteResult(outcomes=[passing], expected_required_ids=["gen-raise"])
        assert evaluate_gate(result, risks=[]).status is GateStatus.VERIFIED

        verdict = evaluate_gate(
            result,
            risks=[
                IdentifiedRisk(
                    id="R-second-detector",
                    description="a second detector could open a second conflict on one field",
                    risk_category=RiskCategory.CONCURRENCY,
                    severity=Priority.P0,
                    basis="entity §17 is the mandate the unit exists to satisfy",
                )
            ],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert "KNOWN COVERAGE GAPS" in verdict.summary_block()


# --------------------------------------------------------------------------
# 8. P6-D46 stays closed for M7
# --------------------------------------------------------------------------


class TestP6D46StaysClosedForM7:
    """`P6-D46`: the M6 re-verification run proposed nine scenarios, every one declared a
    `risk_category` the harness's own enum did not contain, all nine were discarded at the parse
    stage, and the run reported *"0 generated case(s) + 1 permanent scenario"* and ACCEPTED.

    Nothing had failed. The product was fine. But *"the generator legitimately produced nothing new"*
    and *"the generator produced nine and Product Driver could not read any of them"* had collapsed
    into one number, and only the first is a reason to accept.

    The fix is general and lives in `tests/test_generation_contract.py`. What is pinned HERE is that
    M7 does not reopen it from the permanent-scenario side: the M7 file uses only canonical
    categories, a category it invented would refuse to load, and the four counts stay separable for
    an M7 wave. **Nothing about M7 is special-cased inside Product Driver core to achieve that.**
    """

    def _planner(self, tmp_path: Path, payloads) -> ScenarioPlanner:
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner(list(payloads)),
            base_scenario=load_scenario(M7_PATH),
            permanent_scenarios=[load_scenario(M7_PATH)],
            founder=FakeFounder(),
        )

    def _m7_raw(self, scenario_id: str, category: str) -> dict:
        """A proposal shaped for THIS unit: dark, command-driven, with a persisted-state oracle.

        The shared fixture's default is an HTTP approval scenario, which M7's dark base scenario
        correctly refuses on four separate grounds. Using it here would prove that an HTTP scenario
        is rejected, which is not what P6-D46 is about.
        """
        return raw_scenario(
            scenario_id,
            risk_category=category,
            requirement="U-042: an approved invoice is paid exactly once",
            principle="effect-truth",
            service_refs=[],
            actions=[{
                "kind": "command",
                "name": "drive the conflict machine",
                "command": f"{PROBE} --case at-most-one-open-conflict-per-field",
            }],
            state_checks=[{
                "name": "the conflict layer is still tenant-first and readable",
                "command": STATE_ORACLE,
                "contains": ["problems: []"],
            }],
            expected_observations=["AT MOST ONE OPEN CONFLICT PER TENANT, ENTITY AND FIELD"],
            forbidden_observations=["### TWO OPEN CONFLICTS FOR ONE FIELD ###"],
            cleanup=[],
            isolation_key="conflict-db",
            isolation_note=(
                "the probe builds its own temporary database per case and touches no shared "
                "state, so nothing survives it to contaminate the next scenario"
            ),
            generating_risk="a second detector could open a second conflict on the same field",
        )

    def test_the_m7_scenario_declares_only_canonical_categories(self, m7):
        """The half a permanent scenario can break on its own.

        Every `verifies:` entry names a `RiskCategory` member, checked against the ONE taxonomy
        rather than against a list this file keeps.
        """
        declared = m7.declared_risk_categories()
        assert declared, "the M7 scenario declares no risk coverage at all"
        unknown = sorted(declared - set(RISK_CATEGORY_VALUES))
        assert not unknown, (
            f"the M7 scenario declares categories the harness taxonomy does not contain: {unknown}"
        )

    def test_an_invented_category_in_the_m7_file_would_refuse_to_load(self, tmp_path):
        """The load-time refusal, exercised against a copy of the REAL M7 file.

        A `verifies:` entry naming a category the taxonomy does not hold would match no risk and read
        as coverage while providing none — which is `P6-D46`'s shape one layer down. This proves the
        M7 file is covered by the refusal rather than merely compatible with it.
        """
        raw = yaml.safe_load(M7_PATH.read_text(encoding="utf-8"))
        raw["verifies"][0]["risk_category"] = "conflict-silently-resolved"
        broken = tmp_path / "m7_broken.yaml"
        broken.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="unknown risk_category"):
            load_scenario(broken)

    def test_the_generator_schema_constrains_the_category_to_the_same_taxonomy(self):
        """The other half: a model cannot answer with a category the loader would refuse.

        The schema is derived from the enum, so a category added there reaches the generator without
        a second edit — and a category the generator invents never becomes a scenario at all.
        """
        field = PLAN_SCHEMA["properties"]["scenarios"]["items"]["properties"]["risk_category"]
        assert field.get("enum") == list(RISK_CATEGORY_VALUES), (
            "the generator's structured-output schema no longer constrains risk_category to the "
            "canonical taxonomy, which is exactly the unconstrained {'type': 'string'} that "
            "produced P6-D46"
        )

    def test_an_unreadable_m7_candidate_is_a_contract_blocker_not_a_silent_zero(self, tmp_path):
        """The `P6-D46` shape, reproduced with M7-flavoured categories, against the M7 base scenario.

        Nine well-meant descriptions of specific M7 defects — none of them a member of a closed
        family vocabulary — must be recorded as CONTRACT rejections the candidates survive, not
        dropped into "0 generated scenarios". And the run may not reach a normal acceptance while
        they stand, even though nothing failed.
        """
        planner = self._planner(tmp_path, [
            raw_payload(
                *(
                    self._m7_raw(f"S{i}-m7", category)
                    for i, category in enumerate(M7_UNREADABLE_CATEGORIES, start=1)
                ),
                risks=[],
            )
        ])
        plan = planner.plan_initial(task="build P6/M7", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.proposed == 9, (
            f"nine candidates were offered and the wave recorded {wave.proposed}. The number "
            "Product Driver could not read may never be smaller than the number it was offered"
        )
        assert wave.accepted_ids == []
        assert len(wave.contract_rejections) == 9, (
            "a candidate the HARNESS could not parse is not the same fact as one planning decided "
            f"against; {len(wave.contract_rejections)} were recorded as a contract failure"
        )
        assert wave.filtered_rejections == []
        assert plan.scenarios == []
        for rejected in wave.contract_rejections:
            assert rejected.id, "the candidate itself was deleted rather than kept"
            assert rejected.raw, "the proposal's own text was not retained"
            assert rejected.reasons, "the rejection records no reason"

        problems = planner.generation_problems()
        assert problems, "nine unreadable candidates produced no generation problem at all"
        text = " ".join(problems)
        assert "9 proposed" in text
        assert "0 accepted for execution" in text
        assert "9 invalid" in text
        assert REJECTED_CONTRACT.replace("_", "-") in text or "generation-contract" in text

    def test_a_full_green_m7_suite_still_cannot_accept_over_an_unreadable_wave(self, tmp_path):
        """The invariant that makes it a blocker rather than a note.

        This is bit for bit the run that ACCEPTed: the permanent M7 scenario passed and no generated
        case ran at all. It no longer can.
        """
        from neyma_product_driver.cli import _apply_suite_precedence
        from neyma_product_driver.models import Decision, EvaluatorDecision
        from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
        from neyma_product_driver.scenario_suite import (
            Origin,
            Outcome,
            ScenarioOutcome,
            SuiteResult,
        )

        planner = self._planner(tmp_path, [
            raw_payload(
                *(
                    self._m7_raw(f"S{i}-m7", category)
                    for i, category in enumerate(M7_UNREADABLE_CATEGORIES, start=1)
                ),
                risks=[],
            )
        ])
        planner.plan_initial(task="build P6/M7", unit=FakeUnit())
        problems = planner.generation_problems()

        passed = ScenarioOutcome(
            scenario_id="p6_m7_conflict",
            scenario_name="p6_m7_conflict",
            origin=Origin.PERMANENT,
            outcome=Outcome.PASSED,
            priority=Priority.P0,
            required=True,
            evidence_path="/runs/x/permanent",
            evidence_verified=True,
        )
        result = SuiteResult(
            outcomes=[passed], full_run=True, expected_required_ids=[passed.scenario_id]
        )

        # Without the problems, this is exactly the false green.
        assert evaluate_gate(result).status is GateStatus.VERIFIED
        assert evaluate_gate(result, generation_problems=problems).blocks_acceptance

        decision = _apply_suite_precedence(
            result,
            EvaluatorDecision(decision=Decision.ACCEPT, summary="good", observed_behavior=["saw it"]),
            "p6_m7_conflict",
            lambda _m: None,
            generation_problems=problems,
        )
        assert decision.decision is Decision.BLOCKED

    def test_the_four_counts_stay_separable_for_an_m7_wave(self, tmp_path):
        """proposed / accepted / filtered / invalid are four facts, and summing them is the defect.

        A wave that proposed nine and accepted none is not the same run as a wave that proposed none
        — and after `P6-D46` the accounting has to keep saying so, including when the wave is mixed.
        """
        planner = self._planner(tmp_path, [
            raw_payload(
                self._m7_raw("gen-m7-valid", "concurrency"),
                self._m7_raw("gen-m7-unreadable", "conflict-auto-resolved"),
                risks=[],
            )
        ])
        plan = planner.plan_initial(task="build P6/M7", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.proposed == 2
        assert wave.accepted_ids == ["gen-m7-valid"], (
            "the readable candidate was punished for its neighbour"
        )
        assert [r.id for r in wave.contract_rejections] == ["gen-m7-unreadable"]
        assert wave.filtered_rejections == []
        assert planner.generation_problems(), "a mixed wave stopped blocking"

    def test_an_honestly_empty_m7_wave_is_not_a_generation_problem(self, tmp_path):
        """The other half, and the reason this is not just "block whenever nothing ran".

        "The generator had nothing to add" is a legitimate outcome and must stay one, or the guard
        that closed `P6-D46` becomes a guard that blocks every quiet run.
        """
        planner = self._planner(tmp_path, [{"risks": [], "scenarios": []}])
        plan = planner.plan_initial(task="build P6/M7", unit=FakeUnit())

        assert plan.waves[0].proposed == 0
        assert plan.waves[0].contract_rejections == []
        assert planner.generation_problems() == []

    def test_product_driver_core_does_not_special_case_m7(self):
        """The fix is general or it is not a fix.

        `P6-D46` was closed by making the taxonomy single-sourced and the rejection accounting
        honest — not by teaching the harness about a unit. A core module that names this unit would
        be a per-unit exception with a passing status.
        """
        core = DRIVER_ROOT / "neyma_product_driver"
        offenders = sorted(
            f.name
            for f in core.rglob("*.py")
            if "p6_m7_conflict" in f.read_text(encoding="utf-8")
            or "phase6_conflict" in f.read_text(encoding="utf-8")
        )
        assert not offenders, (
            f"Product Driver core names the M7 unit in {offenders}. Permanent scenarios, tasks and "
            "readiness tests carry unit knowledge; the harness carries none"
        )


# --------------------------------------------------------------------------
# 9. M7 is scoped as a unit, and cannot move the phase
# --------------------------------------------------------------------------


@pytest.fixture
def m7_repo(tmp_path: Path) -> PhaseRepo:
    """A phase in progress, one unit being built, a stated review rule."""
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/conflict.py", "# the unit under construction\n")
    repo.commit_all("the M7 candidate")
    return repo


class TestM7IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m7(self, m7_repo: PhaseRepo):
        scope = m7_repo.scope(M7_TASK)
        assert scope.scope_id == "P6/M7"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(self, m7_repo: PhaseRepo):
        """The task discusses P6 at length. Discussing a phase is not claiming it, and a run that
        inherited the phase's bar would be held to seven units that do not exist."""
        scope = m7_repo.scope(M7_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m7_repo: PhaseRepo):
        scope = m7_repo.scope(M7_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(self, m7_repo: PhaseRepo):
        rendered = m7_repo.scope(M7_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "enables nothing in production" in rendered


class TestM7CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m7_repo: PhaseRepo
    ):
        scope = m7_repo.scope(M7_TASK)
        completion = scoped_completion(scope, TaskResult.ACCEPTED, phase_accepted=True)
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M7"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m7_repo: PhaseRepo):
        completion = scoped_completion(m7_repo.scope(M7_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    def test_a_builder_claiming_p6_is_complete_is_caught(self, m7_repo: PhaseRepo):
        audit = m7_repo.audit(
            "M7 is implemented and verified. With M7 landed, P6 is COMPLETE and P7 is now "
            "unblocked.\n",
            M7_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_a_builder_claiming_production_enablement_is_caught(self, m7_repo: PhaseRepo):
        audit = m7_repo.audit(
            "M7 is implemented and verified. The conflict queue is now enabled for live traffic.\n",
            M7_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_the_task_names_every_prohibited_expansion(self):
        """The M7-specific temptations, each named in the task's `Do not` list.

        M7's are different from M6's: this unit's seams point BACK at two landed machines, its own
        `CF-3` needs a rule registry that is another unit, and its `CF-5` needs a deadline that is
        another unit again.
        """
        for prohibition in (
            "M8–M13",
            "M8 Expectation",
            "M9 Exception",
            "M10 Compensation",
            "M12 Rule",
            "P7 or later",
            "provenance and evidence platform",
            "Evidence Store",
            "V5",
            "freight workflows",
            "invoice automation",
            "cargo claims",
            "any live conflict inbox, queue or resolution UI",
            "email_triage.py",
            "action_callback.py",
            "mailbox_intake",
            "production autonomy",
            "live production effects",
            "production integrations",
            "legacy cleanup campaign",
            "broad documentation cleanup",
            "P6-D40",
            "push, publish or deploy",
        ):
            assert prohibition in M7_TASK, f"the task never forbids {prohibition!r}"
        assert "weaken **P3, P4 or P5**" in M7_TASK
        assert "polish **M1, M2, M3, M4, M5 or M6**" in M7_TASK
        assert "one-connection-per-thread concurrency correction" in M7_TASK_FLAT, (
            "the task never protects the landed P3/P4 correction CURRENT.md says must not be reworked"
        )

    def test_p6_d40_is_named_as_conditional_rather_than_forbidden_outright(self):
        """The one prohibition that is not absolute."""
        assert "unless a real guard in it mechanically blocks this unit" in M7_TASK_FLAT

    def test_the_task_records_the_known_nonblocking_items_without_ordering_a_campaign(self):
        for item in ("P6-D41", "P6-D42", "P6-D43", "P6-D44", "P6-D45", "P6-D46"):
            assert item in M7_TASK, f"the known nonblocking item {item} is never recorded"
        assert "Each is recorded." in M7_TASK
        assert "STOP and report the conflict rather than guessing" in M7_TASK_FLAT

    def test_the_task_allows_exactly_one_blocking_prerequisite_and_requires_it_reported(self):
        assert "smallest blocking prerequisite" in M7_TASK_FLAT
        assert "identify it explicitly" in M7_TASK_FLAT


# --------------------------------------------------------------------------
# 10-11. The loop owns M7 end to end
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m7_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m7_repo.root, m7_repo.scope(M7_TASK), unit=m7_repo.unit()
        )
        assert requirement.required
        assert requirement.from_repository_authority

    def test_the_task_states_the_tier_and_says_why_it_took_the_higher_one(self):
        """CLAUDE.md §7: "When genuinely torn between two tiers, take the higher one once and say
        so."

        A state machine is tier 2 by itself. M7 also lands a MIGRATION, is load-bearing for TENANT
        ISOLATION, and decides whether a consequential action may proceed when two sources disagree —
        which is the effect boundary's own admission question, and "weakening or deleting a safety
        guard" territory by the table's own words.
        """
        assert "tier-1" in M7_TASK
        assert "migration" in M7_TASK_FLAT
        assert "tenant isolation" in M7_TASK_FLAT
        assert (
            "whether a consequential action may proceed when two sources disagree" in M7_TASK_FLAT
        )
        assert "take the higher tier once and say so, and this file says so" in M7_TASK_FLAT


class TestTheLoopOwnsM7EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m7_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session that wrote the code,
        with its evidence path intact."""
        builder = FakeBuilder(m7_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m7_repo, tmp_path, task=M7_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        assert "INDEPENDENT REVIEW" in builder.prompts[1]
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED

    async def test_the_corrected_tree_gets_a_brand_new_reviewer(
        self, m7_repo: PhaseRepo, tmp_path: Path
    ):
        """The reviewer must be a lineage that did not build M7, and the second reviewer must read
        the CORRECTED tree rather than the one the first one read."""
        builder = FakeBuilder(m7_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m7_repo, tmp_path, task=M7_TASK, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        assert len(set(reviewer.session_ids)) == 2, "the same reviewer session was reused"
        first = reviewer.bindings[0]["fingerprint"]
        second = reviewer.bindings[1]["fingerprint"]
        assert not first.matches(second), "the second reviewer read the same tree as the first"
        assert result.satisfying_review.fingerprint.matches(second)

    async def test_an_accept_is_scoped_m7_acceptance_and_never_p6_complete(
        self, m7_repo: PhaseRepo, tmp_path: Path
    ):
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m7_repo, tmp_path, task=M7_TASK, reviewer=reviewer)

        assert result.status is RunStatus.ACCEPTED
        assert result.audit is not None, "the run accepted without a completion audit"
        completion = result.audit.completion
        assert completion is not None
        assert completion.task_scope == "P6/M7"
        assert completion.task_result in {TaskResult.ACCEPTED, TaskResult.VERIFIED}
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_accepted is False
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    async def test_the_run_stops_at_m7_and_never_walks_into_m8(
        self, m7_repo: PhaseRepo, tmp_path: Path
    ):
        """Two halves of the same guarantee: the task forbids it in words, and the loop ends at its
        own scoped verdict rather than picking up the next unit."""
        assert "Stop at verified M7. Do not automatically continue into M8." in M7_TASK
        assert "begin **M8–M13**" in M7_TASK

        reviewer = FakeReviewer([supported()])
        result, store = await drive(m7_repo, tmp_path, task=M7_TASK, reviewer=reviewer)
        assert result.status is RunStatus.ACCEPTED
        assert result.audit.completion.task_scope == "P6/M7"

        journal = RunJournal(run_id=store.run_id, task=M7_TASK)
        journal.record_outcome(run_status="ACCEPTED")
        summary = journal.personal_summary()
        for forbidden in ("M8", "begin the next unit", "continue into"):
            assert forbidden not in summary.split("### 8. The ONE exact next move")[1], (
                f"the next move points past M7 ({forbidden!r})"
            )


# --------------------------------------------------------------------------
# 12. The founder summary says what M7 actually does, in normal language
# --------------------------------------------------------------------------


class _Gate:
    """Duck-typed GateVerdict stand-in."""

    def __init__(self, status: str, *, passed: int = 0, total: int = 0):
        self.status = status
        self.unverified: list = []
        self.uncovered_risks: list = []
        self.generation_problems: list = []
        self.required_passed = passed
        self.required_total = total

    def headline(self) -> str:
        return f"scenario gate: {self.status}"


def _m7_journal(**outcome) -> RunJournal:
    scenario = load_scenario(M7_PATH)
    journal = RunJournal(run_id="r-m7", task=M7_TASK)
    journal.task_scope_id = "P6/M7"
    journal.parent_phase_id = "P6"
    journal.parent_phase_state = "IN_PROGRESS"
    journal.scope_is_nested = True
    journal.record_outcome(
        scenario_name=scenario.name,
        scenario_phase=scenario.phase,
        scenario_purpose=scenario.description,
        **outcome,
    )
    return journal


class TestTheFounderSummaryExplainsM7:
    def test_it_states_the_product_impact_in_normal_language(self):
        """The scenario description is what a founder reads to learn what the unit is for. It has to
        be a brokerage sentence, not a machine one."""
        scenario = load_scenario(M7_PATH)
        text = " ".join(scenario.description.split()).lower()
        for phrase in ("tms", "portal", "load", "invoice", "human"):
            assert phrase in text, f"the description never mentions {phrase!r}"
        assert "ships dark" in text

    def test_it_never_says_p6_moved(self):
        journal = _m7_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary()
        assert "P6 is COMPLETE" not in summary
        assert "P6 COMPLETE" not in summary

    def test_it_does_not_imply_a_live_conflict_queue_or_production(self):
        """A negative that has to be written carefully.

        "enables nothing in production" is a sentence this summary SHOULD carry, so a bare search for
        "in production" fails on the correct text. What must not appear is an ENABLEMENT claim, and
        each phrase below is one.
        """
        journal = _m7_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary().lower()
        for claim in (
            "enabled for live traffic",
            "live traffic is",
            "production-ready",
            "enabled for customers",
            "conflict queue is live",
            "conflicts are being resolved",
        ):
            assert claim not in summary, f"the summary implies {claim!r}"
        # And the thing it must actively say, because "dark" is the whole posture.
        assert "enables nothing in production" in summary

    def test_no_founder_decision_says_none(self):
        journal = _m7_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        journal.record_stop(reason="M7 verified.", founder_decision_required="none")
        assert journal.founder_decision_required == ""


# --------------------------------------------------------------------------
# 13. THE MUTATION GUARD — does this file actually fail when the assertion is removed?
# --------------------------------------------------------------------------


def _mutate(edit) -> "object":
    """Load a copy of the SHIPPED M7 scenario with one load-bearing thing weakened.

    `edit` receives the raw YAML mapping and changes it in place. Nothing is written to the
    scenarios directory: the mutant lives in memory and is parsed through the real loader, so a
    weakening the loader itself refuses raises here rather than returning a Scenario.
    """
    import tempfile

    raw = copy.deepcopy(yaml.safe_load(M7_PATH.read_text(encoding="utf-8")))
    edit(raw)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m7_mutant.yaml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return load_scenario(path)


def _named(raw: dict, section: str, name: str) -> dict:
    for entry in raw[section]:
        if entry.get("name") == name:
            return entry
    raise AssertionError(f"{name!r} is not in {section}; the mutation targets a check that is gone")


def _claim(raw: dict, category: str) -> dict:
    for entry in raw["verifies"]:
        if entry.get("risk_category") == category:
            return entry
    raise AssertionError(f"no {category!r} claim to mutate")


class TestThisFileFailsWhenTheGuardIsRemoved:
    """A readiness test never seen to fail is a decoration.

    Every case below weakens the SHIPPED scenario in one specific way and then runs the REAL
    assertion from earlier in this file against the weakened copy — not a paraphrase of it. If the
    assertion has been loosened into something that passes either way, these turn green and the
    failure is visible here rather than six weeks later in a run that verified nothing.

    `CLAUDE.md` §6: *mutate to prove a guard works when you are writing a guard that protects a
    tier-1 invariant. A guard never seen to fail is a decoration, and a mutation that does not
    reintroduce the real defect proves nothing.*
    """

    def test_the_baseline_mutant_is_the_shipped_file_unchanged(self, m7):
        """The control. If `_mutate` cannot round-trip the file, every result below is noise."""
        unchanged = _mutate(lambda raw: None)
        assert unchanged.name == m7.name
        assert len(unchanged.commands) == len(m7.commands)
        assert len(unchanged.expect_state) == len(m7.expect_state)
        assert len(unchanged.verifies) == len(m7.verifies)
        assert unchanged.expect_visible == m7.expect_visible
        assert unchanged.forbidden == m7.forbidden

    def test_dropping_a_canonical_case_turns_the_coverage_assertion_red(self):
        """Remove `at-most-one-open-conflict-per-field` from the probe's asserted vocabulary and the
        family silently stops being verifiable."""
        mutant = _mutate(
            lambda raw: _named(raw, "commands", "the M7 probe can exercise every canonical risk family")
            ["expect_contains"].remove("at-most-one-open-conflict-per-field")
        )
        cases = [c for c in mutant.commands if c.run == f"{PROBE} --list-cases"][0].expect_contains
        with pytest.raises(AssertionError, match="risk families the scenario never asserts exist"):
            TestTheM7BaseScenario().test_it_asserts_a_risk_family_for_every_canonical_obligation(
                list(cases)
            )

    def test_dropping_a_fault_turns_the_mutation_axis_assertion_red(self):
        """Remove `timer-resolve` and the machine's most important illegal transition becomes
        unreachable from a generated case."""
        mutant = _mutate(
            lambda raw: _named(
                raw, "commands",
                "the M7 probe exposes a bounded, closed dimension vocabulary to vary cases with",
            )["expect_contains"].remove("timer-resolve")
        )
        dims = [
            c for c in mutant.commands if c.run == f"{PROBE} --list-dimensions"
        ][0].expect_contains
        with pytest.raises(AssertionError, match="timer-resolve"):
            TestTheM7BaseScenario().test_it_declares_a_bounded_mutation_axis(list(dims))

    def test_narrowing_the_partial_index_to_open_turns_the_index_assertion_red(self):
        """The window a build session ships by reading "open" as "the `OPEN` state".

        An index whose `WHERE` names only `OPEN` leaves `RAISED` and `ESCALATED` unguarded, and a
        second conflict gets in through either one.
        """
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "one open conflict per field is a PARTIAL UNIQUE index over the three open states, "
                "tenant-first",
            )
            check["contains"] = [c for c in check["contains"] if c not in ("RAISED", "ESCALATED")]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="PARTIAL on"):
            TestPersistedStateIsTheOracle(
            ).test_one_open_conflict_is_asserted_as_a_partial_unique_index_over_all_three_open_states(
                checks
            )

    def test_dropping_the_forbidden_state_turns_the_state_assertion_red(self):
        """Remove `'CANCELLED'` from `not_contains` and `M7-AQ-3` can be answered by adding a state
        nobody notices."""
        def edit(raw):
            check = [c for c in raw["expect_state"] if "state vocabulary" in c["command"]][0]
            check["not_contains"] = [n for n in check["not_contains"] if n != "'CANCELLED'"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="invented CANCELLED state"):
            TestPersistedStateIsTheOracle().test_the_five_states_are_asserted_and_there_is_no_sixth(
                mutant
            )

    def test_dropping_the_resolution_basis_check_turns_the_basis_assertion_red(self):
        """Remove the `RESOLVED_BY_RULE requires rule_id` assertion and "there is no third way"
        becomes a docstring the database never heard."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the resolution basis and the owner are CHECK constraints, not conventions",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "RESOLVED_BY_RULE requires rule_id: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle(
            ).test_the_resolution_basis_and_the_owner_are_asserted_as_checks(checks)

    def test_removing_the_probe_from_the_regression_claim_turns_the_mapping_assertion_red(self):
        """The exact defect that blocked the M6 run, reintroduced.

        The claim still requires the three seam literals; it just stops naming the only command that
        can print them. Nothing about the product changed, and the gap would stand forever.
        """
        def edit(raw):
            claim = _claim(raw, "regression")
            claim["checks"] = [c for c in claim["checks"] if c != PROBE_CHECK]

        mutant = _mutate(edit)
        assert claims_needing_the_probe(mutant), (
            "the probe was removed from a claim that requires its narration and the predicate did "
            "not notice — which is the M6 mapping defect, undetected"
        )
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_regression_claim_names_the_probe_that_proves_the_seams_are_intact(mutant)

    def test_deleting_the_seam_literals_turns_the_still_required_assertion_red(self):
        """The other way to make a coverage gap go away is to stop asking for the proof."""
        def edit(raw):
            claim = _claim(raw, "regression")
            claim["observations"] = [
                o for o in claim["observations"] if o not in DARK_POSTURE_LITERALS
            ]
            raw["expect_visible"] = [
                v for v in raw["expect_visible"] if v not in DARK_POSTURE_LITERALS
            ]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_dark_posture_literals_are_still_required_somewhere(mutant)

    def test_misattributing_a_declared_literal_is_refused_at_load_time(self):
        """The statically decidable half, which never gets as far as this file.

        `problems: []` is DECLARED by two named state checks. A claim requiring it while naming
        neither could never be established, and the loader now says so.
        """
        def edit(raw):
            claim = _claim(raw, "cross_tenant")
            claim["checks"] = [PROBE_CHECK]

        with pytest.raises(ValueError, match="could never be established"):
            _mutate(edit)

    def test_an_invented_risk_category_is_refused_at_load_time(self):
        """`P6-D46`'s shape, from the permanent-scenario side."""
        def edit(raw):
            _claim(raw, "concurrency")["risk_category"] = "two-open-conflicts"

        with pytest.raises(ValueError, match="unknown risk_category"):
            _mutate(edit)

    def test_deleting_a_forbidden_marker_turns_the_failure_assertion_red(self):
        """A mutant with no marker is a mutant nothing observes."""
        mutant = _mutate(
            lambda raw: raw["forbidden"].remove("### CONFLICT AUTO-RESOLVED ###")
        )
        with pytest.raises(AssertionError, match="CONFLICT AUTO-RESOLVED"):
            TestTheM7BaseScenario().test_it_refuses_the_failures_m7_exists_to_prevent(mutant)

    def test_dropping_the_population_proof_turns_the_unregistered_name_sweep_red(self):
        """A negative assertion needs a proven population (`CLAUDE.md` §6).

        `unregistered names in the machine: []` prints an empty list against a file that does not
        exist. Without `machine source: present` beside it, the sweep reads as a pass over nothing.
        """
        def edit(raw):
            check = _named(
                raw, "expect_state", "M7 uses the five registered F7 contracts and invents no sixth"
            )
            check["contains"] = [c for c in check["contains"] if c != "machine source: present"]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="proven population"):
            TestPersistedStateIsTheOracle(
            ).test_the_five_f7_contracts_are_used_and_no_sixth_is_minted(checks)

    def test_widening_the_invention_sweep_onto_the_illegal_faults_turns_that_assertion_red(self):
        """The sweep must not forbid the identifiers the probe needs to ATTEMPT an illegal
        transition — or staying green would mean deleting the `GR-1` cases."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "no conflict expiry, sweep, deletion, cancellation transition or synonym event was "
                "invented",
            )
            check["command"] = check["command"].replace(
                "ConflictExpired", "auto_resolve|ConflictExpired"
            )

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="ATTEMPT the illegal transition"):
            TestPersistedStateIsTheOracle(
            ).test_inventing_an_expiry_or_cancellation_surface_is_a_scenario_failure(mutant, checks)

    def test_refusing_an_illegal_fault_as_unknown_turns_that_distinction_red(self):
        """M7 owns conflict resolution, so `auto-resolve` must be refused by the MACHINE under
        `GR-1`, not by the argument parser. A probe that made it unreachable would have proved only
        that its own vocabulary is closed."""
        def edit(raw):
            expiry = _named(
                raw, "commands",
                "a conflict-expiry fault does not exist, because a conflict never expires",
            )
            expiry["run"] = expiry["run"].replace("expire-conflict", "auto-resolve")

        mutant = _mutate(edit)
        dims = [
            c for c in mutant.commands if c.run == f"{PROBE} --list-dimensions"
        ][0].expect_contains
        with pytest.raises(AssertionError, match="refused as an UNKNOWN fault"):
            TestTheM7BaseScenario(
            ).test_the_illegal_faults_are_in_the_vocabulary_rather_than_refused_as_unknown(
                mutant, list(dims)
            )

    def test_dropping_a_deliverable_turns_the_fixture_assertion_red(self):
        """Without the fixture a run against a repository where M7 does not exist could report a
        verified M7."""
        mutant = _mutate(lambda raw: raw["fixtures"].remove("src/freight_recon/conflict.py"))
        with pytest.raises(AssertionError, match="is not required to exist"):
            TestTheM7BaseScenario().test_it_requires_the_canonical_deliverables_to_exist(mutant)

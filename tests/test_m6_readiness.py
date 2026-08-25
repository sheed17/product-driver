"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M6?

M6 is the Identity Binding Claim: the claim that *artifact X belongs to entity Y*, which ADR-007
calls the most common and most dangerous claim in freight. It is the unit that decides whether a
machine may overwrite a human's decision — so the question this file answers is not "does the YAML
parse" but whether the whole loop can own the unit end to end without the founder standing in the
middle of it.

The unit's whole character is one sentence, and every check below traces back to it:

    provenance_class = f(match_method)   —  DERIVED, computed ONCE, NEVER independently edited

`provenance_class` gates and confidence sorts. That single function is what stops a guess from
acquiring the authority of a fact by moving through enough layers, and it is what makes "an
`OWNER_ASSERTED` binding survives the relinker" a check a database can hold rather than a promise a
docstring makes.

Twelve questions, each answered mechanically rather than by reading a document and agreeing with it:

1.  does the M6 base scenario parse, does it hold the pieces the generator needs (deterministic
    operation, a closed mutation axis, persisted-state oracles, regression anchors), and do the
    scenario and the task state the SAME contract;
2.  does the scenario measure the DATABASE rather than the probe's narration for the five invariants
    that a green test suite can state while the database enforces none of them;
3.  does the task preserve the three recorded authority conflicts rather than resolving them;
4.  does the task get the two SEAMS right — `ConflictRaised` is M6's to emit because `IB-6` is a
    registered producer, and the M10 handoff is not, because its trigger name is registered nowhere;
5.  is the M6 command vocabulary safe, and actually visible to the generator rather than truncated
    out of the brief;
6.  can dynamic generation close an M6 coverage gap WITHOUT inventing a command, and is an invented
    one refused;
7.  is M6 scoped as `P6/M6` rather than as P6 phase completion;
8.  can accepting M6 score a P6 acceptance criterion or unlock P7 (it cannot);
9.  is an integrated independent review OWED when the repository's own authority says so;
10. do grounded reviewer findings return to the SAME builder, and does a corrected tree get a FRESH
    reviewer;
11. does the run stop before M7;
12. does the founder summary explain M6's product impact in simple terms — and never contradict its
    own review ledger while doing it.

Every Claude session is faked. No test here consumes Claude usage, executes the product, or touches
the real Neyma repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from neyma_product_driver.completion_auditor import AuditDecision
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.models import RunStatus
from neyma_product_driver.review_cycle import resolve_review_requirement
from neyma_product_driver.run_journal import RunJournal
from neyma_product_driver.scenario_generator import MAX_RENDERED_COMMANDS
from neyma_product_driver.scenario_plan import (
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

from scenario_fixtures import FakeFounder, ScriptedReasoner
from test_integrated_review import FakeBuilder, FakeReviewer, drive, refusing, supported
from test_scoped_completion import PhaseRepo

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DRIVER_ROOT / "scenarios"
M6_PATH = SCENARIOS_DIR / "p6_m6_identity_binding_claim.yaml"
M6_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m6.md"
M6_TASK = M6_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match against this: the task is
#: a wrapped markdown document, and a phrase that happens to straddle a line break is not a phrase
#: the task failed to state.
M6_TASK_FLAT = " ".join(M6_TASK.split())
PROBE = ".venv/bin/python scripts/probe_phase6_identity_binding_claim.py"

#: A persisted-state command the base scenario already carries, so a generated case that reuses it is
#: choosing an approved oracle rather than authoring one.
STATE_ORACLE = next(
    check.command
    for check in load_scenario(M6_PATH).expect_state
    if "schema_readiness_problems" in check.command
)

#: The canonical M6 deliverables. A different name is a scenario failure, not a style preference —
#: the permanent scenario looks for exactly these.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/identity_binding_claim.py",
    "src/freight_recon/migrations/phase6_identity_binding_claims.py",
    "eval/tests/test_phase6_identity_binding_claim.py",
    "scripts/probe_phase6_identity_binding_claim.py",
    "scripts/mutate_phase6_identity_binding_claim.py",
)

#: The seven canonical claim states (registry §4 / M6, target spec §12.6). Not six, not eight.
STATES: tuple[str, ...] = (
    "PROPOSED",
    "CONFIRMED",
    "AMBIGUOUS",
    "REJECTED",
    "SUPERSEDED",
    "CORRECTED",
    "CONFLICTING",
)

#: States a build session might reach for out of tidiness, and that the corpus says do not exist.
#: `RESOLVED` is first because it is M7's vocabulary and the likeliest import of the five.
FORBIDDEN_STATES: tuple[str, ...] = ("RESOLVED", "EXPIRED", "ARCHIVED", "DELETED")

#: The canonical transition ids. The task must require these rows, with these ids, rather than an
#: alternative lifecycle that "achieves the same thing".
TRANSITIONS: tuple[str, ...] = (
    "IB-1", "IB-2", "IB-2r", "IB-2h", "IB-3", "IB-4", "IB-5", "IB-5x", "IB-6", "IB-7", "IB-8",
)

#: SD-6, as a table. `provenance_class` is a total function of `match_method`; dropping any pair, or
#: letting the two be set independently, is the mutation the battery has to prove is catchable.
PROVENANCE_MAPPING: tuple[tuple[str, str], ...] = (
    ("EXACT_ID", "LINKER_INFERRED"),
    ("RULE", "LINKER_INFERRED"),
    ("RECONCILIATION", "RECONCILED"),
    ("MODEL_EXTRACT", "MODEL_EXTRACTED"),
    ("MODEL_INFER", "MODEL_INFERRED"),
    ("HUMAN", "OWNER_ASSERTED"),
)

#: The six registered F6 event contracts. `event_contracts_data.json` carries exactly these six, and
#: `events/registry.md` is by its own header THE SOLE CANONICAL LIST — so a seventh `Claim*` name is
#: defective by the registry's own definition.
F6_EVENTS: tuple[str, ...] = (
    "ClaimProposed",
    "ClaimConfirmed",
    "ClaimEvidenced",
    "ClaimAmbiguous",
    "ClaimSuperseded",
    "ClaimCorrected",
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
def m6():
    return load_scenario(M6_PATH)


@pytest.fixture(scope="module")
def cases(m6) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m6.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m6) -> list[str]:
    listing = [c for c in m6.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def state_checks(m6) -> dict[str, list[str]]:
    return {check.name: list(check.contains) for check in m6.expect_state}


# --------------------------------------------------------------------------
# 1. The base scenario, and its contract with the task
# --------------------------------------------------------------------------


class TestTheM6BaseScenario:
    def test_it_parses_and_is_a_dark_p6_backend_scenario(self, m6):
        assert m6.name == "p6_m6_identity_binding_claim"
        assert m6.phase == "P6"
        assert m6.mode == "backend"
        # M6 ships dark: no service, no HTTP surface, no browser, and above all no queue and no
        # "assign unlinked N" action — the product form of this unit is a HUMAN'S AMBIGUOUS QUEUE,
        # and the one thing that must not arrive with it is that queue.
        assert not m6.services and not m6.requests and m6.browser is None
        assert not m6.app_url

    def test_it_requires_the_canonical_deliverables_to_exist(self, m6):
        """A run against a repository where M6 does not exist yet must not be able to report a
        verified M6."""
        for path in DELIVERABLES:
            assert path in m6.fixtures, f"{path} is not required to exist"

    def test_the_probe_is_approved_bare_so_every_case_tail_is_reachable(self, m6):
        """The whole `--case` interface rests on this one entry.

        Approval matches by prefix, so approving the bare probe approves every argument tail that
        composes no shell. Approving only `probe.py --list-cases` would approve exactly that string
        and nothing else, and the generator would have no focused entry point at all.
        """
        assert any(c.run == PROBE for c in m6.commands), (
            "the bare probe invocation is missing; without it a generated "
            f"'{PROBE} --case X' is not an argument tail of any approved entry"
        )

    def test_it_asserts_a_risk_family_for_every_canonical_obligation(self, cases):
        """One family per canonical obligation, checked by name.

        This list is the contract between `tasks/neyma_p6_m6.md` and this file; a family missing from
        either is a family the generator cannot reach and the builder was never asked to build.
        """
        required = {
            # IB-1 / SD-6 — provenance is DERIVED, never chosen
            "proposal-creates-proposed-with-derived-provenance",
            "provenance-is-derived-from-match-method",
            "provenance-class-is-not-independently-editable",
            "provenance-mapping-is-exhaustive-and-immutable",
            "provenance-laundering-refused",
            "content-cannot-set-its-own-provenance",
            # IB-2 / IB-2r — deterministic confirmation, and its absence of a fallback
            "exact-trusted-id-confirms",
            "exact-id-with-two-open-entities-is-ambiguous",
            "no-best-guess-fallback",
            "registered-rule-confirms",
            "reconciliation-requires-two-sources",
            # IB-2h — the human assertion, and the immutable target
            "human-assertion-confirms-owner-asserted",
            "human-assertion-requires-authenticated-tenant-human",
            "human-assertion-requires-decision-ref",
            "ordinal-target-resolves-to-immutable-id-or-fails-closed",
            "ordinal-target-changed-between-display-and-click-fails-closed",
            # IB-3 — the model READS; it never DECIDES
            "model-extract-is-evidence-not-confirmation",
            "model-extracted-requires-evidence-span",
            "extracted-identifier-re-enters-deterministic-matching",
            "forged-evidence-span-fails-closed",
            # IB-4 — ambiguity, and the confidence that may never gate it
            "model-inferred-routes-to-ambiguous",
            "model-guess-never-confirms-at-confidence-1-0",
            "multiple-candidates-ambiguous",
            "single-weak-candidate-ambiguous",
            "ambiguous-is-human-owned",
            "confidence-is-invisible-to-every-guard",
            # IB-5 / IB-5x — supersession, and the B3 regression
            "linker-inferred-claim-may-be-recomputed",
            "owner-asserted-binding-survives-relinker",
            "owner-asserted-overwrite-is-illegal-and-recorded",
            "superseded-claim-is-retained",
            # IB-6 — the M7 seam
            "inferrer-vs-owner-raises-conflict-not-a-winner",
            "conflicting-preserves-the-human-binding",
            "m7-conflict-machine-is-not-built",
            # IB-7 — correction, and the obligation it owes downstream
            "human-correction-moves-confirmed-to-corrected",
            "correction-is-append-only-and-lineage-preserving",
            "correction-of-correction-is-supported",
            "correction-records-its-propagation-obligation",
            "m10-compensation-machine-is-not-built",
            # IB-8 / §25
            "proposed-or-ambiguous-may-be-rejected",
            "cancelled-entity-supersedes-the-confirmed-binding",
            # §17 / §19 / GR-3 — uniqueness, concurrency, the database
            "one-confirmed-binding-per-subject",
            "competing-confirmations-serialize-at-most-one-wins",
            "occ-on-claim-version",
            "database-invariants",
            # [C-1] — tenancy
            "tenant-isolation",
            "cross-tenant-identical-subject-ref",
            "wrong-tenant-human-assertion-fails-closed",
            # §35 / §40 / ADR-003 — security
            "forged-human-fails-closed",
            "inactive-human-fails-closed",
            "model-actor-cannot-confirm",
            "counterparty-cannot-become-owner-asserted",
            # GR-2 / GR-4 / GR-11 / M-26 — the P5 transport
            "state-and-event-co-commit",
            "inbox-idempotency",
            "duplicate-proposal-is-a-no-op",
            "replay-preserves-owner-asserted-byte-identical",
            "replay-creates-no-new-authority-and-no-effect",
            "correction-before-confirmation-is-parked",
            # §38 / §40 — the seams M6 feeds and must not duplicate
            "conflicting-binding-blocks-consequential-action",
            "superseded-binding-blocks-consequential-action",
            "confirmed-binding-provenance-is-allowed-for-consequential-action",
            "ambiguous-binding-does-not-flow-through-approval",
            "m6-mints-no-gate-decision",
        }
        missing = sorted(required - set(cases))
        assert not missing, f"risk families the scenario never asserts exist: {missing}"

    def test_it_declares_a_bounded_mutation_axis(self, dimensions):
        """Without this the M6 possibility space is a list of fixed points.

        M6 ships dark, so there is no service and no HTTP surface, and `parallel_requests` — the
        executor's only concurrency primitive — is unavailable. Ordering, concurrency, timing,
        duplication, crash and replay variation are reachable through the probe's arguments or not at
        all. See docs/SCENARIO-SPACE.md, gap G2.
        """
        for axis in ("--concurrency", "--delay-ms", "--repeat", "--tenants", "--candidates",
                     "--confidence", "--seed"):
            assert axis in dimensions, f"the axis {axis} is never asserted to exist"
        for fault in (
            "model-infer-binding", "model-extract-without-span", "forged-evidence-span",
            "confidence-one-point-zero",
            "edit-provenance-class", "launder-provenance", "content-sets-provenance",
            "unregistered-rule", "single-source-reconciliation",
            "multiple-candidates", "single-weak-candidate", "no-candidate",
            "relink-owner-asserted", "relink-linker-inferred", "inferrer-disagrees",
            "correct-confirmed", "correct-a-correction", "drop-propagation-obligation",
            "reject-proposed", "cancel-entity",
            "duplicate-proposal", "competing-confirmation", "occ-conflict", "concurrent-confirm",
            "forged-human", "inactive-human", "wrong-tenant", "model-actor-confirm",
            "counterparty-asserts-authority", "ordinal-target", "ordinal-target-moved",
            "malformed-claim",
            "replay", "restart-before-confirm", "restart-after-correct",
            "unreceived-subject", "reorder-stream", "relinker-retry-storm",
        ):
            assert fault in dimensions, f"the fault {fault!r} is never asserted to exist"

    def test_confidence_is_an_axis_so_the_negative_control_is_reachable(self, dimensions, cases):
        """The one axis whose whole purpose is to change NOTHING.

        ADR-007 §4.2: there is no threshold — not 0.95, not 0.99, not 1.0 — at which a
        `MODEL_INFERRED` claim becomes bindable, and §8 names a confidence threshold as the single
        most likely way the architecture gets defeated. An axis the generator can turn to 1.0, over a
        case that must still refuse, is what makes that a measurement rather than a belief.
        """
        assert "--confidence" in dimensions
        assert "confidence-one-point-zero" in dimensions
        assert "model-guess-never-confirms-at-confidence-1-0" in cases
        assert "confidence-is-invisible-to-every-guard" in cases

    def test_the_mutation_axis_has_a_negative_control(self, m6):
        """A vocabulary that accepts anything is fuzzing in a costume."""
        negative = [c for c in m6.commands if "--inject not-a-real-fault" in c.run]
        assert negative, "nothing proves the fault vocabulary is actually closed"
        assert negative[0].expect_exit_code == 2, "a refusal must be a non-zero exit"
        assert "unknown fault" in negative[0].expect_contains
        assert "Traceback (most recent call last)" in m6.forbidden

    def test_an_expiry_fault_is_refused_because_a_claim_never_expires(self, m6):
        """The first M6-specific negative control.

        Entity §26 says a claim NEVER EXPIRES, §28 gives it no deletion policy, §29 makes retention
        permanent because the claim IS the evidence chain, and machine §12/§23 say the same twice
        more. A probe that ACCEPTED an expiry fault would be producing passing evidence for a
        mechanism the corpus states does not exist — the same shape as M4's refused `unfreeze` and
        M5's refused `expire-observation`.
        """
        expiry = [c for c in m6.commands if "--inject expire-claim" in c.run]
        assert expiry, "nothing refuses a claim-expiry fault"
        assert expiry[0].expect_exit_code == 2
        assert "unknown fault" in expiry[0].expect_contains

    def test_an_auto_resolve_fault_is_refused_because_a_clock_is_not_a_decision(self, m6):
        """The second, and the one that is about M7.

        ADR-007 §5.3: a conflict closes by a registered deterministic rule or by a human, and there
        is NO THIRD WAY — `AutoResolve` is an ILLEGAL transition. The fault is refused twice over:
        the corpus forbids the mechanism, and M6 does not own conflict resolution at all.
        """
        auto = [c for c in m6.commands if "--inject auto-resolve-conflict" in c.run]
        assert auto, "nothing refuses an auto-resolve-conflict fault"
        assert auto[0].expect_exit_code == 2
        assert "unknown fault" in auto[0].expect_contains
        assert "### CONFLICT AUTO-RESOLVED ###" in m6.forbidden

    def test_inventing_an_expiry_or_auto_resolution_surface_is_a_scenario_failure(self, state_checks):
        """And the rule is preserved by a check over the corpus, not a hope.

        The way a build session breaks "a claim never expires" is not by arguing with it. It is by
        adding a TTL, a sweep, a stale-claim reaper or an auto-resolver because those felt like
        hygiene — or by adding an `IB-9` row to close the `CONFLICTING` state §3.9 leaves open.
        """
        guard = state_checks.get(
            "no claim expiry, sweep, deletion, auto-resolution or extra transition was invented"
        )
        assert guard, "nothing asserts that no expiry or auto-resolution surface was invented"
        assert "invented expiry/auto-resolve surfaces: []" in guard
        assert "invented expiry/extra transition rows: []" in guard
        assert "expiry columns on identity_binding_claims: []" in guard

    def test_it_carries_regression_anchors_for_every_layer_m6_builds_on(self, m6):
        """M6 adds a table and edits `schema.py`, so every layer that reads a canonical database can
        be broken from inside it."""
        runs = " ".join(c.run for c in m6.commands)
        for anchor in (
            "test_phase3_witness.py",             # P3, the kernel M6 must not disturb
            "test_import_gate.py",                # P4, the boundary M6 must not widen
            "test_phase5_event_transport.py",     # P5, the transport M6 rides
            "test_phase6_work_item.py",           # M1
            "test_phase6_pipeline_instance.py",   # M2
            "test_phase6_external_effect.py",     # M3
            "test_phase6_approval.py",            # M4
            "test_phase6_observation.py",         # M5, the unit M6 sits directly on top of
        ):
            assert anchor in runs, f"{anchor} is never re-run against M6's change"
        # M6 uses six already-registered F6 names plus registered cross-family names and mints none
        # of its own, so the canonical event-contract guard is load-bearing here rather than
        # incidental: a builder that invents `CorrectionInvalidatedAnEffect` fails there.
        assert "test_p5_event_contracts.py" in runs
        assert "test_p5_canonical_event_mint.py" in runs

    def test_it_re_runs_m4_and_m5_own_oracles_rather_than_trusting_m6(self, m6):
        """"M6 broke nothing" is a claim about M4's and M5's behaviour.

        M5 in particular: `subject_ref` points into `observations`, so M6 is the first unit with a
        real reason to reach back into M5's table — and the first that must not.
        """
        runs = [c.run for c in m6.commands]
        assert ".venv/bin/python scripts/probe_phase6_observation.py" in runs
        assert ".venv/bin/python scripts/probe_phase6_approval.py" in runs

    def test_the_mutation_battery_runs(self, m6):
        runs = " ".join(c.run for c in m6.commands)
        assert "mutate_phase6_identity_binding_claim.py" in runs
        assert "mutants caught" in m6.expect_visible

    def test_it_refuses_the_failures_m6_exists_to_prevent(self, m6):
        """Each forbidden marker is a sentence printed only when the thing M6 exists to prevent has
        just happened. Every load-bearing mutant in the battery has one."""
        for marker in (
            "### MODEL_INFERRED CONFIRMED ###",
            "### CONFIDENCE GATED A CONFIRMATION ###",
            "### OWNER_ASSERTED OVERWRITTEN ###",
            "### OWNER_ASSERTED SILENTLY SUPERSEDED ###",
            "### WEAK CANDIDATE AUTO-CONFIRMED ###",
            "### BEST GUESS ACCEPTED ###",
            "### provenance_class EDITED ###",
            "### PROVENANCE LAUNDERED ###",
            "### PROVENANCE SET FROM CONTENT ###",
            "### MODEL_EXTRACTED WITHOUT EVIDENCE SPAN ###",
            "### FORGED EVIDENCE SPAN ACCEPTED ###",
            "### TWO CONFIRMED BINDINGS ###",
            "### ORDINAL BOUND WITHOUT AN IMMUTABLE ID ###",
            "### ORDINAL FELL BACK TO POSITION ###",
            "### INFERRER PICKED A WINNER ###",
            "### CROSS-TENANT CONFIRMATION ACCEPTED ###",
            "### FORGED HUMAN ACCEPTED ###",
            "### INACTIVE HUMAN ACCEPTED ###",
            "### MODEL ACTOR CONFIRMED ###",
            "### COUNTERPARTY BECAME OWNER_ASSERTED ###",
            "### CORRECTION WITHOUT ITS PROPAGATION OBLIGATION ###",
            "### COMPENSATION FABRICATED ###",
            "### CLAIM DELETED ###",
            "### CLAIM EXPIRED ###",
            "### REPLAY REWROTE OWNER_ASSERTED PROVENANCE ###",
            "### REPLAY MINTED NEW AUTHORITY ###",
            "### DOWNSTREAM EFFECT DURING REPLAY ###",
            "### PARKED CORRECTION DROPPED ###",
        ):
            assert marker in m6.forbidden, f"{marker} is not a failure condition"

    def test_every_mutation_battery_mutant_has_a_forbidden_marker_behind_it(self, m6):
        """The mutation battery is only as good as the sentence that turns red under it.

        Each pair below is (the mutant the task requires, the marker the probe prints when that
        defect is live). A mutant with no marker is a mutant nothing observes.
        """
        for mutant_phrase, marker in (
            ("`MODEL_INFERRED` allowed to CONFIRM", "### MODEL_INFERRED CONFIRMED ###"),
            ("a confidence threshold used as a confirmation guard",
             "### CONFIDENCE GATED A CONFIRMATION ###"),
            ("`OWNER_ASSERTED` overwritten by the relinker", "### OWNER_ASSERTED OVERWRITTEN ###"),
            ("a single weak candidate auto-confirms", "### WEAK CANDIDATE AUTO-CONFIRMED ###"),
            ("`provenance_class` independently editable", "### provenance_class EDITED ###"),
            ("`MODEL_EXTRACTED` allowed without an evidence span",
             "### MODEL_EXTRACTED WITHOUT EVIDENCE SPAN ###"),
            ("two `CONFIRMED` bindings allowed", "### TWO CONFIRMED BINDINGS ###"),
            ("a human ordinal accepted without immutable-ID resolution",
             "### ORDINAL BOUND WITHOUT AN IMMUTABLE ID ###"),
            ("inferrer-vs-owner picks the inferrer instead of raising a conflict",
             "### INFERRER PICKED A WINNER ###"),
            ("cross-tenant confirmation", "### CROSS-TENANT CONFIRMATION ACCEPTED ###"),
            ("correction fails to emit its propagation obligation",
             "### CORRECTION WITHOUT ITS PROPAGATION OBLIGATION ###"),
            ("replay rewrites `OWNER_ASSERTED` provenance",
             "### REPLAY REWROTE OWNER_ASSERTED PROVENANCE ###"),
        ):
            assert mutant_phrase in M6_TASK_FLAT, f"the task never requires the mutant {mutant_phrase!r}"
            assert marker in m6.forbidden, f"the mutant {mutant_phrase!r} has no forbidden marker"

    def test_the_task_file_and_the_scenario_agree_on_the_contract(self, m6, cases, dimensions):
        """The two halves of one contract, checked against each other.

        A case the scenario asserts exists but the task never asks for is a case the builder is being
        failed on without being told. A literal the scenario requires but the task never states is
        the same defect one layer down.
        """
        for case in cases:
            assert case in M6_TASK, f"the scenario asserts case {case!r}; the task never names it"
        for dimension in dimensions:
            assert dimension in M6_TASK, (
                f"the scenario asserts dimension {dimension!r}; the task never names it"
            )
        for literal in m6.expect_visible:
            assert literal in M6_TASK, (
                f"the scenario requires the literal {literal!r}; the task never states it"
            )
        for marker in m6.forbidden:
            if marker.startswith("### ") and marker.endswith(" ###"):
                assert marker in M6_TASK, (
                    f"the scenario forbids {marker!r}; the task never names it"
                )
        for path in DELIVERABLES:
            assert path in M6_TASK, f"the scenario requires {path}; the task never names it"

    def test_the_task_states_the_canonical_machine_rather_than_a_generic_feature(self):
        for state in STATES:
            assert state in M6_TASK, f"the canonical state {state} is never named"
        for transition in TRANSITIONS:
            assert transition in M6_TASK, f"the canonical transition {transition} is never named"
        for event in F6_EVENTS:
            assert event in M6_TASK, f"the F6 contract {event} is never named"
        assert "Seven states" in M6_TASK, "the state count is never stated"
        assert "Do not add an eighth" in M6_TASK
        # `RESOLVED` is M7's, and importing it is how the eighth state arrives.
        assert "no `RESOLVED`" in M6_TASK

    def test_the_task_forces_the_authority_to_be_read_first(self):
        for source in (
            "PRODUCT.md",
            "CLAUDE.md",
            "docs/implementation/CURRENT.md",
            "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
            "docs/specifications/entities/09-identity-binding-claim.md",
            "docs/specifications/state-machines/06-identity-binding-claim.machine.md",
            "docs/specifications/state-machines/registry.md",
            "docs/specifications/events/06-identity-binding-claim-events.md",
            "docs/specifications/events/registry.md",
            "docs/specifications/events/14-audit-security-events.md",
            "docs/architecture/decisions/ADR-007-identity-claims-and-conflict.md",
            "docs/architecture/decisions/ADR-002-state-classes-and-lineage.md",
            "docs/specifications/entities/08-evidence.md",
            "src/freight_recon/checkpoint.py",
        ):
            assert source in M6_TASK, f"{source} is never named as authority"
        assert "the specification wins and you say so" in M6_TASK_FLAT
        assert "REPORT THE CONFLICT" in M6_TASK

    def test_the_task_states_the_derived_provenance_rule(self):
        """The one sentence every defect in this unit comes from confusing."""
        assert "SD-6" in M6_TASK
        assert (
            "`provenance_class` IS A DETERMINISTIC, IMMUTABLE FUNCTION OF `match_method`"
            in M6_TASK
        )
        for method, provenance in PROVENANCE_MAPPING:
            assert method in M6_TASK, f"the match method {method} is never named"
            assert provenance in M6_TASK, f"the provenance class {provenance} is never named"
        assert "A caller must not be able to choose `provenance_class` independently" in M6_TASK_FLAT
        assert "`provenance_class` gates. Confidence sorts." in M6_TASK_FLAT

    def test_the_task_states_what_a_binding_is_not(self):
        """Entity §4's list, including the freight trap.

        A cargo-damage `Claim` is a different entity, and the specification says to ALWAYS QUALIFY —
        which is exactly the kind of instruction a build session drops when it starts naming things.
        """
        assert "NOT AN OBSERVATION, NOT A FACT, NOT AUTHORITY, NOT A CARGO/FREIGHT" in M6_TASK
        assert "NOT SOMETHING A MODEL MAY CONFIRM" in M6_TASK


# --------------------------------------------------------------------------
# 2. The database is the oracle, not the probe's narration
# --------------------------------------------------------------------------


class TestPersistedStateIsTheOracle:
    """The five sentences a green test suite can state while the database enforces none of them.

    "provenance is derived", "there is one confirmed binding per subject", "a MODEL_EXTRACTED claim
    has an evidence span", "the human is real" and "there are seven states" are each a property of
    the SCHEMA. A probe that prints them proves it printed them.
    """

    def test_the_scenario_reads_the_database_at_all(self, m6):
        assert m6.expect_state, "no persisted state is inspected; the probe speaks for itself"

    def test_sd6_is_asserted_as_a_check_over_all_six_pairs(self, state_checks):
        """The single most load-bearing line in the unit.

        `provenance_class = f(match_method)` with no CHECK behind it is two free fields and a
        docstring — and two free fields is precisely the shape SD-6 exists to forbid. Asserting the
        pairs INDIVIDUALLY matters: a CHECK that maps five of six is a CHECK with one laundering hole
        in it, and the hole is whichever pair the builder found least interesting.
        """
        guard = state_checks.get(
            "provenance_class is a CHECKED function of match_method, not two free fields"
        )
        assert guard, "nothing asserts the provenance mapping is a database constraint"
        assert "provenance CHECK pairs match_method to provenance_class: True" in guard
        assert "unmapped match methods: []" in guard

    def test_the_derived_fields_are_asserted_immutable_by_trigger(self, state_checks):
        """`provenance_class` immutable with no trigger behind it is a comment.

        The repository already builds invariants this way — `trg_checkpoint_witnesses_append_only_update`,
        `trg_durable_timers_immutable`, `trg_event_outbox_envelope_immutable`, and M5's own
        `raw_value`/`content_digest` triggers — so this is its own mechanism, not a bar invented here.

        `match_method` is asserted beside it because the mapping is a FUNCTION: a method that can be
        rewritten is a provenance that can be rewritten one indirection later, and then the CHECK
        above is protecting nothing.
        """
        guard = state_checks.get(
            "provenance_class and match_method are immutable in the database, not merely in the Python"
        )
        assert guard, "nothing asserts the derived fields are immutable in the database"
        assert "provenance_class protected by a trigger: True" in guard
        assert "match_method protected by a trigger: True" in guard

    def test_the_seven_states_are_asserted_and_there_is_no_eighth(self, m6):
        guard = [c for c in m6.expect_state if "state vocabulary" in c.command]
        assert guard, "the state set is never read out of the DDL"
        declared = guard[0]
        for state in STATES:
            assert f"'{state}'" in declared.contains, f"{state} is not asserted in the CHECK"
        for forbidden in FORBIDDEN_STATES:
            assert f"'{forbidden}'" in declared.not_contains, (
                f"nothing prevents an invented {forbidden} state"
            )

    def test_the_six_match_methods_are_asserted_and_there_is_no_seventh(self, m6):
        """A seventh method is how a "best guess" arrives wearing a deterministic name, and how
        `SYSTEM_IMPORTED` — which no method maps to — gets into a table whose mapping cannot produce
        it."""
        guard = [c for c in m6.expect_state if "match_method" in c.command and "CHECK" in c.command]
        assert guard, "the match-method set is never read out of the DDL"
        declared = guard[0]
        for method, _ in PROVENANCE_MAPPING:
            assert f"'{method}'" in declared.contains, f"{method} is not asserted in the CHECK"

    def test_one_confirmed_binding_is_asserted_as_a_partial_unique_index(self, state_checks):
        """Entity §17. "At most one CONFIRMED binding" is a hope about a code path until it is a
        partial unique index, and an application-level check-then-insert is exactly what two
        concurrent confirmers both pass. Machine §17 states the consequence: the index is half the
        concurrency story, and OCC is the other half."""
        guard = state_checks.get(
            "one CONFIRMED binding per subject is a PARTIAL UNIQUE index, tenant-first"
        )
        assert guard, "nothing asserts the one-confirmed-binding rule is an index"
        assert "CREATE UNIQUE INDEX" in guard
        assert "subject_ref" in guard
        assert "CONFIRMED" in guard, "the index is not asserted to be PARTIAL on the confirmed state"

    def test_the_evidence_span_and_rule_id_requirements_are_asserted_as_checks(self, state_checks):
        """Entity §16, and entity §37's "structurally impossible".

        Without the CHECK, a `MODEL_EXTRACTED` claim with no artifact behind it is just a guess with
        a better provenance label — which is exactly the laundering R-P2 forbids. The `rule_id` CHECK
        is read beside it because §16 states both in the same breath and both are the same kind of
        claim: a provenance class that asserts a basis must carry the basis.
        """
        guard = state_checks.get(
            "MODEL_EXTRACTED requires an evidence span, and an inferred/reconciled claim requires "
            "a rule, by CHECK"
        )
        assert guard, "nothing asserts the evidence-span requirement is a database constraint"
        assert "MODEL_EXTRACTED requires evidence_id + span: True" in guard
        assert "LINKER_INFERRED/RECONCILED requires rule_id: True" in guard
        for column in ("'evidence_id'", "'span'", "'rule_id'", "'corrected_from'",
                       "'superseded_by'", "'confidence'", "'decision_ref'"):
            assert column in guard, f"the claim table is not asserted to carry {column}"

    def test_the_human_and_the_subject_are_asserted_as_foreign_keys(self, state_checks):
        """"Authenticated human" and "immutable target" are decoration while both are text columns.

        Entity §35: `OWNER_ASSERTED` requires an authenticated human bound to an IMMUTABLE
        identifier, never an ordinal. A FOREIGN KEY into `tenant_humans` and one into `observations`
        are the versions of those two sentences a database enforces — exactly the argument M1 made
        for `owner_id` and M4 made for `granted_by`. The self-FK is the correction lineage.
        """
        guard = state_checks.get("the human and the subject are FOREIGN KEYS, not conventions")
        assert guard, "nothing asserts the human and the subject are foreign keys"
        assert "tenant_humans" in guard
        assert "observations" in guard
        assert "identity_binding_claims" in guard, "the correction lineage self-FK is not asserted"

    def test_the_dark_posture_is_measured_over_the_shipped_package(self, state_checks):
        assert (
            "production importers of identity_binding_claim: []"
            in state_checks.get(
                "M6 has no production caller — the dark posture, measured over the shipped package",
                [],
            )
        )
        assert (
            "scripts reaching identity_binding_claim: ['probe_phase6_identity_binding_claim.py']"
            in state_checks.get(
                "the only thing outside the package that reaches M6 is the verification probe itself",
                [],
            )
        )

    def test_no_live_linker_can_arrive_with_the_unit(self, m6, state_checks):
        """M6's product form is a human's AMBIGUOUS queue and an "assign unlinked N" action. Those
        are the things that must not arrive with it — and ADR-007 §14 names `email_triage.py` as this
        linker's own ancestor, which makes wiring into it the most natural mistake available."""
        guard = state_checks.get(
            "no live linker: nothing joins the claim machine to an inbound or outbound channel"
        )
        assert guard, "nothing prevents a live linker arriving with M6"
        assert "modules joining the claim machine to a channel: []" in guard
        command = [c for c in m6.expect_state if c.contains == guard][0].command
        for channel in ("email_triage", "ingestion", "extraction", "inbox_brain",
                        "action_callback", "slack_adapter", "tms_adapter", "mailbox_intake"):
            assert channel in command, f"the linker sweep does not look at {channel}"

    def test_m6_authorizes_nothing(self, state_checks):
        """Entity §38 makes a claim an INPUT to checkpoint step 4; it never becomes a second gate."""
        guard = state_checks.get("the checkpoint is still the only thing that mints a gate decision")
        assert guard
        assert "modules that MINT a gate decision: ['checkpoint.py']" in guard

    def test_the_six_f6_contracts_are_used_and_no_seventh_is_minted(self, state_checks):
        guard = state_checks.get(
            "M6 uses the six registered F6 contracts and the registered cross-family names, "
            "and invents no seventh"
        )
        assert guard, "nothing checks the event names M6 uses against the canonical registry"
        declared = " ".join(guard)
        for event in F6_EVENTS:
            assert f"'{event}'" in declared, f"{event} is not asserted registered"
        assert (
            "unregistered Claim*/Conflict*/Compensation*/Exception* names in the machine: []"
            in guard
        )

    def test_the_conflict_seam_is_grounded_in_the_registered_producer_list(self, state_checks):
        """The difference between M5's refusal and M6's obligation, made mechanical.

        M5 correctly refused to mint `ExceptionRaised`, whose registered producer list is `EC-1`
        alone. `ConflictRaised`'s registered producer list INCLUDES `IB-6`, which is why emitting it
        here is canonical rather than writing another machine's contract. That distinction cannot be
        left to prose: it is read out of the contract projection.
        """
        guard = state_checks.get(
            "M6 uses the six registered F6 contracts and the registered cross-family names, "
            "and invents no seventh"
        )
        assert guard
        assert "'IB-6'" in guard, (
            "nothing establishes that IB-6 is a REGISTERED producer of ConflictRaised — without it "
            "the task's §3.7 reading is an assertion rather than a reading of the corpus"
        )
        assert "CorrectionInvalidatedAnEffect registered: False" in guard, (
            "nothing establishes that M10's trigger name is registered nowhere, which is the whole "
            "of M6-AQ-1"
        )

    def test_m7_and_m10_are_not_invented_along_the_way(self, state_checks):
        """`IB-6` ends in a Conflict and `IB-7` ends in a Compensation, and the cheapest way to
        satisfy both sentences is to build the two machines they name."""
        guard = state_checks.get(
            "the conflict and compensation seams are fed without M7 or M10 being built"
        )
        assert guard, "nothing prevents M6 building another unit's machine"
        assert "mints another machine event: []" in guard
        assert "m7/m9/m10 tables created by m6: []" in guard

    def test_m5s_landed_seam_is_asserted_unchanged(self, state_checks):
        """The one regression this unit is uniquely able to cause.

        M5 left `binding_claim_id` without a foreign key and spelled its match-method constant
        `RECONCILE` where M6's canonical enum spells it `RECONCILIATION`. Both are landed, both are
        M5's, and "tidying" either is polishing a landed unit. Asserting the constant BYTE-FOR-BYTE
        is what makes that a check rather than a request.
        """
        guard = state_checks.get("M5's landed observation seam is not rewritten by M6")
        assert guard, "nothing asserts M5's landed seam survives M6"
        assert 'M5 match-method constant: ("EXACT_ID", "RULE", "RECONCILE", "HUMAN")' in guard
        assert "'binding_claim_id'" in guard
        assert "'raw_value'" in guard


# --------------------------------------------------------------------------
# 3. The three recorded authority conflicts stay open
# --------------------------------------------------------------------------


class TestTheTaskPreservesTheAuthorityConflicts:
    """M4's and M5's §3.9 lesson, applied to a corpus that disagrees with itself three times about
    M6.

    A resolution the builder invented is worse than a blocked run, because it looks like agreement.
    """

    def test_all_three_questions_are_named_with_both_sides(self):
        for question in ("M6-AQ-1", "M6-AQ-2", "M6-AQ-3"):
            assert question in M6_TASK, f"{question} is never raised"
        # AQ-1: F6 names M10 as a consumer of ClaimCorrected; M10 says its trigger is a name that is
        # registered nowhere at all.
        assert "how does `IB-7` hand a completed effect to M10?" in M6_TASK
        assert "CorrectionInvalidatedAnEffect" in M6_TASK
        assert "registered nowhere" in M6_TASK_FLAT
        # AQ-2: entity §16's CHECK against IB-2's guard, with V4 leaving no rule to name.
        assert "does the exact-trusted-ID path carry a `rule_id`?" in M6_TASK
        assert "V4" in M6_TASK
        # AQ-3: registry §4 and machine §9 classify CONFLICTING non-terminal; §14 gives it no exit.
        assert "how does a claim leave `CONFLICTING`?" in M6_TASK
        assert "GR-1" in M6_TASK

    def test_each_question_names_what_every_reading_agrees_on(self):
        """The builder is not blocked by the conflict. It is blocked from RESOLVING it — and told
        exactly what it may still build."""
        assert M6_TASK.count("**Every reading agrees on:**") == 3
        assert "Do not mint an unregistered event name" in M6_TASK_FLAT
        assert "Do not amend a specification to close it." in M6_TASK_FLAT
        assert "Do not invent an exit transition" in M6_TASK_FLAT

    def test_the_scenario_asserts_nothing_about_the_open_questions(self, m6):
        """The scenario must not encode a resolution either.

        There is no required literal about how `IB-7` reaches M10, none about a `rule_id` on the
        exact-ID path, and none about leaving `CONFLICTING`.
        """
        visible = " ".join(m6.expect_visible)
        assert "CorrectionInvalidatedAnEffect" not in visible, (
            "the scenario requires an unregistered event name, which resolves M6-AQ-1"
        )
        assert "CONFLICTING IS RESOLVED" not in visible.upper()
        assert not any("rule_id" in v and "EXACT" in v.upper() for v in m6.expect_visible), (
            "the scenario requires a literal about a rule_id on the exact-ID path, which the corpus "
            "does not settle"
        )
        # What it DOES require is the part every reading agrees on.
        assert "THE CORRECTION RECORDED ITS PROPAGATION OBLIGATION" in m6.expect_visible
        assert "THE HUMAN BINDING IS PRESERVED UNDER CONFLICT" in m6.expect_visible
        assert "NO COMPENSATION IS FABRICATED AS COMPLETED" in m6.expect_visible

    def test_v4_is_explicitly_left_unresolved(self):
        """V4 is the registered freight identity rule set, and it is a customer/domain question.

        The fail-closed default is exact trusted ID only. A builder that "discovers" that MC + date +
        amount is the rule has invented a customer's data model.
        """
        assert "Do not resolve V4" in M6_TASK or "do NOT resolve V4" in M6_TASK or (
            "Do NOT resolve V4" in M6_TASK
        ) or ("do not resolve **V4**" in M6_TASK) or ("resolve **V4**" in M6_TASK)
        assert "fail-closed default is **exact trusted ID only**" in M6_TASK_FLAT

    def test_the_f14_scoping_decision_is_stated_not_guessed(self):
        """Three F14 tripwires name M6, and they are not in the same position.

        `IllegalTransitionAttempted` is GR-1 and mandatory. `OwnerAssertedOverwriteAttempted` names
        M6 as its sole producer and has the M4 precedent behind it. `ProvenanceStrengtheningAttempted`
        is scoped to Phase 7 by name in `CURRENT.md`, exactly as it was for M5 — and the task must
        give the builder all three positions rather than one guess.
        """
        assert "IllegalTransitionAttempted" in M6_TASK
        assert "is MANDATORY and is yours" in M6_TASK
        assert "OwnerAssertedOverwriteAttempted" in M6_TASK
        assert "is yours" in M6_TASK
        assert "ProvenanceStrengtheningAttempted" in M6_TASK
        assert "is NOT yours" in M6_TASK
        assert "the F14 emission is not yours" in M6_TASK_FLAT


# --------------------------------------------------------------------------
# 4. The two seams — build the seam, never the machine behind it
# --------------------------------------------------------------------------


class TestTheSeamsAreScopedToM6:
    """The M5 lesson, twice. `OB-2f` ended "→ Exception" and the cheap way to satisfy it was to build
    M9; `IB-6` ends in a Conflict and `IB-7` ends in a Compensation, and the cheap way to satisfy
    both is to build M7 and M10.

    The difference this time is that one of the two events genuinely IS M6's to emit, and the task
    has to say which and why — a blanket "never write another machine's contract" would be wrong
    here, and the corpus is what settles it.
    """

    def test_the_task_states_the_conflict_seam_and_its_registered_basis(self):
        assert "The M7 seam" in M6_TASK
        assert "producers\n  `['CF-1', 'IB-6', 'EF-4c']`" in M6_TASK or (
            "`['CF-1', 'IB-6', 'EF-4c']`" in M6_TASK_FLAT
        )
        assert "`ExceptionRaised`, whose producer list is `['EC-1']` alone" in M6_TASK_FLAT
        assert "you are not building M7" in M6_TASK
        for forbidden in ("`conflicts` table", "`CF-*` transitions", "ConflictOpened",
                          "ConflictEscalated", "ConflictResolved"):
            assert forbidden in M6_TASK, f"the task never forbids building {forbidden}"
        assert "no resolution path" in M6_TASK_FLAT
        assert "AutoResolve" in M6_TASK

    def test_the_task_states_the_compensation_seam_as_an_obligation(self):
        assert "The M10 seam" in M6_TASK
        assert "M10 is not built, and you are not building it." in M6_TASK
        assert "durable, M6-owned record of the propagation obligation" in M6_TASK_FLAT
        assert "no fabricated completed Compensation" in M6_TASK_FLAT
        for forbidden in ("`compensations` table", "`CM-*`", "CompensationRequired"):
            assert forbidden in M6_TASK, f"the task never forbids building {forbidden}"
        # The constraints M10 will apply are not M6's to pre-empt.
        assert "UNKNOWN_OUTCOME" in M6_TASK
        assert "no bulk undo" in M6_TASK_FLAT

    def test_the_task_states_the_evidence_seam_without_ordering_the_evidence_store(self):
        """The span CHECK is mandatory; the Evidence Store is not an M-numbered P6 machine."""
        assert "The evidence seam" in M6_TASK
        assert "is\n**mandatory**" in M6_TASK or "is **mandatory**" in M6_TASK_FLAT
        assert "Do not build the Evidence Store" in M6_TASK
        assert "no foreign key into a table this unit does not own" in M6_TASK_FLAT

    def test_the_task_states_the_foreign_keys_that_have_a_table_to_point_at(self):
        """Entity §18 names six FKs and two and a half of them have a target today. A builder that
        takes §18 literally builds half of M7, M12 and the Evidence Store to satisfy it."""
        assert "The foreign keys entity §18 names, and what exists to point at" in M6_TASK
        for column in ("subject_ref", "entity_ref", "evidence_id", "rule_id", "conflict_id",
                       "corrected_from"):
            assert column in M6_TASK, f"the FK column {column} is never discussed"
        assert "build the foreign keys whose targets exist" in M6_TASK_FLAT
        assert "name the clause and stop" in M6_TASK_FLAT

    def test_the_task_forbids_editing_the_p3_kernel_while_feeding_it(self):
        """P3 remains the gate minter, and step 4 already exists. M6 feeds it; it does not become a
        second one, and it does not edit `checkpoint.py`."""
        assert "Do not create a second gate authority. P3 remains the gate minter." in M6_TASK
        assert "Do not edit `checkpoint.py`" in M6_TASK
        assert "NativeClaim" in M6_TASK
        assert "step 4" in M6_TASK
        # And the approval seam is verified, not rebuilt.
        assert "Do not rebuild M4." in M6_TASK

    def test_the_task_protects_m5s_landed_seam_by_name(self):
        """M6 is the first unit with a real reason to reach into M5's table, and the first that must
        not. Both halves are named: the missing FK and the `RECONCILE`/`RECONCILIATION` spelling."""
        assert "RECONCILE" in M6_TASK and "RECONCILIATION" in M6_TASK
        assert "Do not rename M5's constant" in M6_TASK
        assert "do not add a foreign key to M5's column" in M6_TASK
        assert 'do not "finish" M5\'s' in M6_TASK_FLAT


# --------------------------------------------------------------------------
# 5. The vocabulary is safe, and visible to the generator
# --------------------------------------------------------------------------


class TestTheM6Vocabulary:
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
            base_scenario=load_scenario(M6_PATH),
            permanent_scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            founder=FakeFounder(),
        )

    def test_every_case_is_approved_by_the_bare_probe_alone(self, tmp_path, cases):
        """No enumeration needed for SAFETY — only for visibility."""
        planner = self._planner(tmp_path, [])
        for case in cases:
            ok, why = planner.approved_commands.approves(f"{PROBE} --case {case}")
            assert ok, f"{case}: {why}"

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
                f"{PROBE} --case owner-asserted-binding-survives-relinker "
                f"--inject {fault} --concurrency 8 --delay-ms 5000 --repeat 5 "
                "--tenants 3 --candidates 4 --confidence 1.0 --seed 4211"
            )
            ok, why = approved.approves(command)
            assert ok, f"{fault}: {why}"

    @pytest.mark.parametrize(
        "command",
        [
            # A command nobody wrote down.
            "curl https://api.example.com/claims",
            ".venv/bin/python scripts/send_real_invoice.py",
            "rm -rf src/freight_recon",
            # A live linker or product surface, which is precisely what M6 must not grow.
            ".venv/bin/python -m freight_recon.email_triage --relink",
            ".venv/bin/python scripts/slack_probe.py --post-unlinked-queue",
            # The approved probe, extended with composition.
            f"{PROBE} --case exact-trusted-id-confirms; curl https://evil.example.com",
            f"{PROBE} --case exact-trusted-id-confirms && rm -rf /",
            f"{PROBE} | tee /tmp/out",
            f"{PROBE} > /etc/hosts",
            f"{PROBE} --case $(whoami)",
            f"{PROBE} --case `whoami`",
            # A newline is whitespace; normalization would hide it, so the raw string is scanned for
            # control characters first.
            f"{PROBE}\ncurl https://evil.example.com",
            # A prefix that is not a prefix.
            ".venv/bin/python scripts/probe_phase6_identity_binding_claim.py.bak",
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
        ok, why = approved.approves(f"{PROBE} --case correction-of-correction-is-supported")
        assert ok, why

    def test_m4_and_m5_probes_stay_reachable_through_the_scenario(self, approved):
        """They are not enumerated in the config, and they do not need to be.

        M6 co-commits with neither — it adds a table and edits the canonical schema, which is a
        REGRESSION relationship. Writing their bare probes into `p6_m6_identity_binding_claim.yaml`
        as regression anchors already approves every `--case` tail of both, because approval matches
        by prefix.
        """
        for command in (
            ".venv/bin/python scripts/probe_phase6_observation.py --case tenant-isolation",
            ".venv/bin/python scripts/probe_phase6_approval.py --case tenant-isolation",
        ):
            ok, why = approved.approves(command)
            assert ok, f"{command}: {why}"

    def test_the_rendered_brief_actually_shows_the_m6_vocabulary(self, tmp_path):
        """The brief truncates the approved list, silently. A vocabulary the generator never sees is
        a vocabulary it cannot choose from."""
        vocabulary = _local_vocabulary()
        if not any("probe_phase6_identity_binding_claim.py" in entry for entry in vocabulary):
            pytest.skip("no local driver.config.yaml enumerating the M6 vocabulary")

        planner = self._planner(tmp_path, vocabulary)
        planner.plan_initial(
            task="Build P6/M6 Identity Binding Claim", unit=None, run_id="r-m6"
        )
        brief = planner.reasoner.briefs[0].render()

        assert PROBE in brief, "the deterministic M6 entry point is not in the brief"
        missing = [
            entry.split("--case ", 1)[1].split()[0]
            for entry in vocabulary
            if "probe_phase6_identity_binding_claim.py --case " in entry and entry not in brief
        ]
        assert not missing, (
            "the approved-command list was truncated before these M6 cases: "
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
            f"only the first {MAX_RENDERED_COMMANDS} — the M6 vocabulary sorts last and is now "
            "invisible to the generator."
        )


# --------------------------------------------------------------------------
# 6. Dynamic generation can close an M6 coverage gap, safely
# --------------------------------------------------------------------------


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close.

    Built as the planner builds one: the citation lives on the provenance, and a coverage-gap case
    that cannot name a risk from this run's own register is refused before it reaches the boundary at
    all.
    """
    return GeneratedScenario(
        id="gen-m6-relinker",
        title="the linker improves overnight and re-runs against the owner's own correction",
        purpose=(
            "an OWNER_ASSERTED binding must survive the relinker at any retry count; recomputing "
            "one is an illegal transition, not a better answer"
        ),
        risk_category=RiskCategory.CONFLICTING_EVIDENCE,
        priority=Priority.P0,
        rationale="the identified relinker-overwrite risk had no scenario behind it",
        requirement_reference="P6/M6",
        product_principle_reference="human-authority",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared state, so "
            "nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m6-task",
            session_id="scripted",
            generating_risk="a relinker re-run could silently overwrite an owner's binding",
            source_risks=[risk_key],
        ),
        actions=[{"kind": "command", "name": "re-run the inferrer", "command": command}],
        # `conflicting_evidence` is a family whose claims are about a TABLE — "the owner's binding
        # is still there" is not something a probe can prove by printing it. This is the mechanical
        # form of the rubric's "a 200 is not success", and M6 is the unit it applies to most
        # literally: the pre-baseline B3 defect was a system whose audit log reported that the
        # correction stood while the row said otherwise.
        persisted_state_checks=[
            GeneratedStateCheck(
                name="the owner's binding is still the confirmed one",
                command=STATE_ORACLE,
                contains=["problems: []", "identity_binding_claims"],
            )
        ],
        expected_observations=["AN OWNER_ASSERTED BINDING SURVIVES THE RELINKER"],
        forbidden_observations=["### OWNER_ASSERTED OVERWRITTEN ###"],
    )


class TestGenerationClosesM6GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-relinker",
            description="a relinker re-run could silently overwrite an OWNER_ASSERTED binding",
            risk_category=RiskCategory.CONFLICTING_EVIDENCE,
            severity=Priority.P0,
            basis="the B3 regression is the defect this unit exists to make unrepresentable",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m6", "p6", "m6"},
                principle_tokens={"human-authority"},
                known_risk_ids={risk.key, "R-relinker"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m6_vocabulary_is_accepted(self, context):
        ctx, risk = context
        command = (
            f"{PROBE} --case owner-asserted-binding-survives-relinker "
            "--inject relinker-retry-storm --repeat 5 --seed 7"
        )
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M6 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_the_whole_mutation_axis_is_reachable_from_a_gap_case(self, context, dimensions):
        ctx, risk = context
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case owner-asserted-binding-survives-relinker "
                f"--inject {fault} --concurrency 4 --delay-ms 40 --candidates 2 "
                "--confidence 1.0 --seed 11"
            )
            accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
            assert accepted, f"{fault}: {rejected}"

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario(
                'python -c "import identity_binding_claim; identity_binding_claim.confirm()"',
                risk.key,
            )], ctx
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

    def test_an_uncovered_p0_m6_risk_blocks_acceptance(self):
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
            scenario_id="gen-propose",
            scenario_name="gen-propose",
            origin=Origin.GENERATED,
            outcome=Outcome.PASSED,
            required=True,
            risk_category="authorization",
            evidence_path="/runs/gen-propose",
            evidence_verified=True,
        )
        result = SuiteResult(outcomes=[passing], expected_required_ids=["gen-propose"])
        assert evaluate_gate(result, risks=[]).status is GateStatus.VERIFIED

        verdict = evaluate_gate(
            result,
            risks=[
                IdentifiedRisk(
                    id="R-relinker",
                    description="a relinker re-run could overwrite an OWNER_ASSERTED binding",
                    risk_category=RiskCategory.CONFLICTING_EVIDENCE,
                    severity=Priority.P0,
                    basis="GR-9 is the mandate the unit exists to satisfy",
                )
            ],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert "KNOWN COVERAGE GAPS" in verdict.summary_block()


# --------------------------------------------------------------------------
# 7-8. M6 is scoped as a unit, and cannot move the phase
# --------------------------------------------------------------------------


@pytest.fixture
def m6_repo(tmp_path: Path) -> PhaseRepo:
    """A phase in progress, one unit being built, a stated review rule."""
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/identity_binding_claim.py", "# the unit under construction\n")
    repo.commit_all("the M6 candidate")
    return repo


class TestM6IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m6(self, m6_repo: PhaseRepo):
        scope = m6_repo.scope(M6_TASK)
        assert scope.scope_id == "P6/M6"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(
        self, m6_repo: PhaseRepo
    ):
        """The task discusses P6 at length. Discussing a phase is not claiming it, and a run that
        inherited the phase's bar would be held to eight units that do not exist."""
        scope = m6_repo.scope(M6_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m6_repo: PhaseRepo):
        scope = m6_repo.scope(M6_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(
        self, m6_repo: PhaseRepo
    ):
        rendered = m6_repo.scope(M6_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "enables nothing in production" in rendered


class TestM6CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m6_repo: PhaseRepo
    ):
        scope = m6_repo.scope(M6_TASK)
        completion = scoped_completion(scope, TaskResult.ACCEPTED, phase_accepted=True)
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M6"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m6_repo: PhaseRepo):
        completion = scoped_completion(m6_repo.scope(M6_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    def test_a_builder_claiming_p6_is_complete_is_caught(self, m6_repo: PhaseRepo):
        audit = m6_repo.audit(
            "M6 is implemented and verified. With M6 landed, P6 is COMPLETE and P7 is now "
            "unblocked.\n",
            M6_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_a_builder_claiming_production_enablement_is_caught(self, m6_repo: PhaseRepo):
        audit = m6_repo.audit(
            "M6 is implemented and verified. The identity linker is now enabled for live traffic.\n",
            M6_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_the_task_names_every_prohibited_expansion(self):
        """The M6-specific temptations, each named in the task's `Do not` list.

        M6's are different from M5's: this unit's two seams END in other machines, its own
        specification names an Evidence store and a rule registry it must not build, and the
        repository already contains a working ancestor of its linker that ADR-007 tells it to keep.
        """
        for prohibition in (
            "M7–M13",
            "M7 Conflict machine",
            "M10 Compensation machine",
            "M9 Exception",
            "P7 or later",
            "provenance and evidence platform",
            "Evidence Store",
            "M12 Rule",
            "V4",
            "freight workflows",
            "invoice automation",
            "cargo claims",
            "any live linker, queue or \"assign unlinked N\" action",
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
            assert prohibition in M6_TASK, f"the task never forbids {prohibition!r}"
        assert "weaken **P3, P4 or P5**" in M6_TASK
        assert "polish **M1, M2, M3, M4 or M5**" in M6_TASK

    def test_p6_d40_is_named_as_conditional_rather_than_forbidden_outright(self):
        """The one prohibition that is not absolute.

        `P6-D40` is a recorded gap in P6's own checkpoint-status guards. It is not M6's to fix — but
        if one of those guards mechanically blocks this unit, refusing to touch it would be its own
        defect. The task has to state the condition rather than the ban.
        """
        assert "unless a real guard in\nit mechanically blocks this unit" in M6_TASK or (
            "unless a real guard in it mechanically blocks this unit" in M6_TASK_FLAT
        )

    def test_the_task_records_the_known_nonblocking_items_without_ordering_a_campaign(self):
        for item in ("P6-D35", "P6-D36", "P6-D37", "P6-D38", "P6-D39", "P6-D40"):
            assert item in M6_TASK, f"the known nonblocking item {item} is never recorded"
        assert "Each is recorded." in M6_TASK
        assert "STOP and report the conflict rather than guessing" in M6_TASK_FLAT

    def test_the_task_allows_exactly_one_blocking_prerequisite_and_requires_it_reported(self):
        assert "smallest blocking prerequisite" in M6_TASK_FLAT
        assert "identify it explicitly" in M6_TASK_FLAT


# --------------------------------------------------------------------------
# 9-11. The loop owns M6 end to end
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m6_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m6_repo.root, m6_repo.scope(M6_TASK), unit=m6_repo.unit()
        )
        assert requirement.required
        assert requirement.from_repository_authority

    def test_the_task_states_the_tier_and_says_why_it_took_the_higher_one(self):
        """CLAUDE.md §7: "When genuinely torn between two tiers, take the higher one once and say
        so."

        A state machine is tier 2 by itself. M6 also lands a MIGRATION, is load-bearing for TENANT
        ISOLATION, and is the unit that decides whether a machine may overwrite a human's decision —
        which is "weakening or deleting a safety guard" territory by the table's own words.
        """
        assert "tier-1" in M6_TASK
        assert "migration" in M6_TASK_FLAT
        assert "tenant isolation" in M6_TASK_FLAT
        assert "whether a machine may overwrite a human's decision" in M6_TASK_FLAT
        assert "take the higher tier\nonce and say so, and this file says so" in M6_TASK or (
            "take the higher tier once and say so, and this file says so" in M6_TASK_FLAT
        )


class TestTheLoopOwnsM6EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m6_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session that wrote the code,
        with its evidence path intact."""
        builder = FakeBuilder(m6_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m6_repo, tmp_path, task=M6_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        assert "INDEPENDENT REVIEW" in builder.prompts[1]
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED

    async def test_the_corrected_tree_gets_a_brand_new_reviewer(
        self, m6_repo: PhaseRepo, tmp_path: Path
    ):
        """The reviewer must be a lineage that did not build M6, and the second reviewer must read
        the CORRECTED tree rather than the one the first one read."""
        builder = FakeBuilder(m6_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m6_repo, tmp_path, task=M6_TASK, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        assert len(set(reviewer.session_ids)) == 2, "the same reviewer session was reused"
        first = reviewer.bindings[0]["fingerprint"]
        second = reviewer.bindings[1]["fingerprint"]
        assert not first.matches(second), "the second reviewer read the same tree as the first"
        assert result.satisfying_review.fingerprint.matches(second)

    async def test_an_accept_is_scoped_m6_acceptance_and_never_p6_complete(
        self, m6_repo: PhaseRepo, tmp_path: Path
    ):
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m6_repo, tmp_path, task=M6_TASK, reviewer=reviewer)

        assert result.status is RunStatus.ACCEPTED
        assert result.audit is not None, "the run accepted without a completion audit"
        completion = result.audit.completion
        assert completion is not None
        assert completion.task_scope == "P6/M6"
        assert completion.task_result in {TaskResult.ACCEPTED, TaskResult.VERIFIED}
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_accepted is False
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    async def test_the_run_stops_at_m6_and_never_walks_into_m7(
        self, m6_repo: PhaseRepo, tmp_path: Path
    ):
        """Two halves of the same guarantee: the task forbids it in words, and the loop ends at its
        own scoped verdict rather than picking up the next unit."""
        assert "Stop at verified M6. Do not automatically continue into M7." in M6_TASK
        assert "begin **M7–M13**" in M6_TASK

        reviewer = FakeReviewer([supported()])
        result, store = await drive(m6_repo, tmp_path, task=M6_TASK, reviewer=reviewer)
        assert result.status is RunStatus.ACCEPTED
        assert result.audit.completion.task_scope == "P6/M6"

        journal = RunJournal(run_id=store.run_id, task=M6_TASK)
        journal.record_outcome(run_status="ACCEPTED")
        summary = journal.personal_summary()
        for forbidden in ("M7", "begin the next unit", "continue into"):
            assert forbidden not in summary.split("### 8. The ONE exact next move")[1], (
                f"the next move points past M6 ({forbidden!r})"
            )


# --------------------------------------------------------------------------
# 12. The founder summary says what M6 actually does, in normal language
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


def _m6_journal(**outcome) -> RunJournal:
    scenario = load_scenario(M6_PATH)
    journal = RunJournal(run_id="r-m6", task=M6_TASK)
    journal.task_scope_id = "P6/M6"
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


class TestTheFounderSummaryExplainsM6:
    def test_it_states_the_product_impact_in_normal_language(self):
        """The scenario description is what a founder reads to learn what the unit is for. It has to
        be a brokerage sentence, not a machine one."""
        scenario = load_scenario(M6_PATH)
        text = " ".join(scenario.description.split()).lower()
        for phrase in ("pod", "load", "invoice", "owner"):
            assert phrase in text, f"the description never mentions {phrase!r}"
        assert "ships dark" in text

    def test_it_never_says_p6_moved(self):
        journal = _m6_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary()
        assert "P6 is COMPLETE" not in summary
        assert "P6 COMPLETE" not in summary

    def test_it_does_not_imply_a_live_linker_or_production(self):
        """A negative that has to be written carefully.

        "enables nothing in production" is a sentence this summary SHOULD carry, so a bare search for
        "in production" fails on the correct text. What must not appear is an ENABLEMENT claim, and
        each phrase below is one.
        """
        journal = _m6_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary().lower()
        for claim in (
            "enabled for live traffic",
            "live traffic is",
            "production-ready",
            "enabled for customers",
            "linker is running",
            "queue is live",
        ):
            assert claim not in summary, f"the summary implies {claim!r}"
        # And the thing it must actively say, because "dark" is the whole posture.
        assert "enables nothing in production" in summary

    def test_no_founder_decision_says_none(self):
        journal = _m6_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        journal.record_stop(reason="M6 verified.", founder_decision_required="none")
        assert journal.founder_decision_required == ""

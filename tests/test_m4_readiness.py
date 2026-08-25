"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M4?

M4 is the Approval: the machinery that decides whether a human's "yes" still
describes the world by the time Neyma acts on it. It is the highest-consequence
unit in P6 — `CLAUDE.md` §7 names "approval/grant lifecycle" as tier 1 by
itself — so the question this file answers is not "does the YAML parse" but
whether the whole loop can own it end to end without the founder standing in the
middle of it.

Thirteen questions, each answered mechanically rather than by reading a document
and agreeing with it:

1.  does the M4 base scenario parse, and does it hold the pieces the generator
    needs (deterministic operation, a closed mutation axis, persisted-state
    oracles, regression anchors), and do the scenario and the task state the
    SAME contract;
2.  is the M4 command vocabulary safe, and is it actually visible to the
    generator rather than truncated out of the brief;
3.  can dynamic generation close an M4 coverage gap WITHOUT inventing a command,
    and is an invented one refused;
4.  is M4 scoped as `P6/M4` rather than as P6 phase completion;
5.  can accepting M4 score a P6 acceptance criterion or unlock P7 (it cannot);
6.  is an integrated independent review OWED when the repository's own authority
    says so — and not when it does not;
7.  does a supported review bind to the exact tree it read;
8.  does changing that tree invalidate it;
9.  do grounded reviewer findings return to the SAME builder automatically;
10. does corrected code require re-verification and a FRESH reviewer;
11. does an M4 ACCEPT mean scoped M4 acceptance only, never P6 COMPLETE;
12. does the run stop before M5;
13. does the founder summary explain M4's product impact in simple terms.

Every Claude session is faked. No test here consumes Claude usage, executes the
product, or touches the real Neyma repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from neyma_product_driver.completion_auditor import AuditDecision, CompletionAuditor
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.models import RunStatus
from neyma_product_driver.review_cycle import (
    ReviewLedger,
    ReviewTrigger,
    TreeFingerprint,
    capture_fingerprint,
    resolve_review_requirement,
)
from neyma_product_driver.run_journal import RunJournal
from neyma_product_driver.scenario_generator import MAX_RENDERED_COMMANDS
from neyma_product_driver.scenario_plan import (
    GeneratedScenario,
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
from test_integrated_review import (
    NO_REVIEW_CLAUDE_MD,
    FakeBuilder,
    FakeReviewer,
    drive,
    refusing,
    supported,
)
from test_scoped_completion import PhaseRepo

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DRIVER_ROOT / "scenarios"
M4_PATH = SCENARIOS_DIR / "p6_m4_approval.yaml"
M4_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m4.md"
M4_TASK = M4_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match
#: against this: the task is a wrapped markdown document, and a phrase that
#: happens to straddle a line break is not a phrase the task failed to state.
M4_TASK_FLAT = " ".join(M4_TASK.split())
PROBE = ".venv/bin/python scripts/probe_phase6_approval.py"

#: The canonical M4 deliverables. A different name is a scenario failure, not a
#: style preference — the permanent scenario looks for exactly these.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/approval.py",
    "src/freight_recon/migrations/phase6_approvals.py",
    "eval/tests/test_phase6_approval.py",
    "scripts/probe_phase6_approval.py",
    "scripts/mutate_phase6_approval.py",
)

#: The eight canonical Approval states (registry §4 / M4). Not seven, not nine,
#: and deliberately no `SUPERSEDED` — ADR-005 §3.10 decomposes supersession into
#: drift-void ∪ duplicate-refusal, so a ninth state would be the mechanism this
#: unit exists without.
STATES: tuple[str, ...] = (
    "REQUESTED",
    "GRANTED",
    "CONSUMED",
    "DENIED",
    "EXPIRED",
    "REVOKED",
    "VOID_ON_DRIFT",
    "VOID_ON_BRAKE",
)

#: The canonical transition ids. The task must require these rows, with these
#: ids, rather than an alternative lifecycle that "achieves the same thing".
TRANSITIONS: tuple[str, ...] = (
    "AP-1", "AP-2", "AP-2d", "AP-3", "AP-4", "AP-4p",
    "AP-5", "AP-6", "AP-7", "AP-8", "AP-9",
)


def _local_vocabulary() -> list[str]:
    """The `--case` entries the local driver config approves, if it exists.

    Read from the file rather than through `load_config`, because this must work
    on a checkout that has no `driver.config.yaml` at all — the vocabulary is
    then simply absent and the tests that need it skip.
    """
    local = DRIVER_ROOT / "driver.config.yaml"
    if not local.exists():
        return []
    raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    return list((raw.get("scenario_generation") or {}).get("approved_commands") or [])


@pytest.fixture(scope="module")
def m4():
    return load_scenario(M4_PATH)


@pytest.fixture(scope="module")
def cases(m4) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m4.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m4) -> list[str]:
    listing = [c for c in m4.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


# --------------------------------------------------------------------------
# 1. The base scenario, and its contract with the task
# --------------------------------------------------------------------------


class TestTheM4BaseScenario:
    def test_it_parses_and_is_a_dark_p6_backend_scenario(self, m4):
        assert m4.name == "p6_m4_approval"
        assert m4.phase == "P6"
        assert m4.mode == "backend"
        # M4 ships dark: no service, no HTTP surface, no browser, and above all
        # no live approval channel — the product form of this unit is a button
        # in Slack, and the one thing that must not arrive with it is that
        # button.
        assert not m4.services and not m4.requests and m4.browser is None
        assert not m4.app_url

    def test_the_probe_is_approved_bare_so_every_case_tail_is_reachable(self, m4):
        """The whole `--case` interface rests on this one entry.

        Approval matches by prefix, so approving the bare probe approves every
        argument tail that composes no shell. Approving only
        `probe.py --list-cases` would approve exactly that string and nothing
        else, and the generator would have no focused entry point at all.
        """
        assert any(c.run == PROBE for c in m4.commands), (
            "the bare probe invocation is missing; without it a generated "
            f"'{PROBE} --case X' is not an argument tail of any approved entry"
        )

    def test_it_asserts_a_risk_family_for_every_obligation_the_founder_named(self, cases):
        """One family per canonical obligation, checked by name.

        This list is the contract between `tasks/neyma_p6_m4.md` and this file;
        a family missing from either is a family the generator cannot reach and
        the builder was never asked to build.
        """
        required = {
            # AP-1 — the request, and where its authority comes from
            "runtime-fact-binding",
            "model-output-cannot-manufacture-authority",
            # AP-2 / AP-2d — who may grant, and who may deny
            "authenticated-authorized-human-grant",
            "model-cannot-grant",
            "counterparty-cannot-grant",
            "human-denial-is-terminal",
            # the transport, a separate layer from the authority
            "single-use-transport-token",
            "replayed-token-refused",
            "wrong-actor-token-refused",
            # AP-3
            "expiry-is-not-an-approval",
            # AP-4 / AP-4p — the whole point of the unit
            "amount-drift-voids",
            "party-drift-voids",
            "provenance-drift-voids",
            "evidence-condition-drift-voids",
            "entity-version-drift-voids",
            "unreadable-source-fails-closed",
            "drift-diff-is-human-readable",
            "policy-version-drift-voids",
            # AP-5 / AP-6
            "brake-voids-before-consume",
            "human-revoke-before-consume",
            # AP-7
            "consume-cas-in-the-claim-txn",
            "double-tap-is-idempotent",
            # AP-8 / AP-9
            "provable-failure-ap8",
            "unknown-outcome-freeze-ap9",
            "frozen-approval-not-reusable",
            # §36
            "crash-before-consume-survives",
            "crash-after-consume-not-regranted",
            # §16 / ADR-005 §3.16
            "dual-control-distinct-actors",
            "dual-control-drift-voids-signatures",
            # ADR-005 §3.8 / §3.10 / entity §17
            "partial-approval-is-a-new-proposal",
            "live-approval-uniqueness",
            # the seams and the inherited obligations
            "m2-awaiting-approval-seam",
            "m3-claim-serialization-seam",
            "database-invariants",
            "replay-zero-approval-authority",
            "redelivery-idempotency",
            "transactional-co-commit",
            "tenant-isolation",
            "retained-canonical-payload",
            "terminal-states-stay-terminal",
            # §40 — a REAL token misused is `replayed-token-refused` /
            # `wrong-actor-token-refused` above; these two are a token that was
            # never real, and one aimed at another target.
            "forged-authority-refused",
            "wrong-target-authority-refused",
            # events/registry.md §8 names `F4 Approval` a STRICT-ORDER aggregate,
            # so M4 inherits on its own aggregate the obligation M3 discharged on
            # its one: declare `previous_aggregate_version`, and consume the
            # COMPLETE stream rather than the `Approval*` family subset.
            "strict-order-predecessor-declared",
            "complete-aggregate-stream-consumed",
            # ER-16 — a quarantine fact is rebuilt from POSITIVE evidence.
            "frozen-reconstructed-from-positive-evidence",
        }
        missing = sorted(required - set(cases))
        assert not missing, f"risk families the scenario never asserts exist: {missing}"

    def test_it_declares_a_bounded_mutation_axis(self, dimensions):
        """Without this the M4 possibility space is a list of fixed points.

        M4 ships dark, so there is no service and no HTTP surface, and
        `parallel_requests` — the executor's only concurrency primitive — is
        unavailable. Ordering, concurrency, timing, drift, crash and redelivery
        variation are reachable through the probe's arguments or not at all.
        See docs/SCENARIO-SPACE.md, gap G2.
        """
        for axis in ("--concurrency", "--delay-ms", "--repeat", "--tenants",
                     "--signers", "--seed"):
            assert axis in dimensions, f"the axis {axis} is never asserted to exist"
        for fault in (
            "drift-amount", "drift-party", "drift-provenance",
            "drift-evidence-condition", "drift-entity-version", "source-unreadable",
            "policy-bump", "brake-engage", "human-revoke", "ttl-elapse",
            "provable-failure", "outcome-unknown", "double-tap", "replay-token",
            "wrong-actor", "crash-before-consume", "crash-after-consume",
            "redeliver", "signature-drift",
            "forge-token", "wrong-target",
            "drop-predecessor", "reorder-stream", "freeze-by-absence",
        ):
            assert fault in dimensions, f"the fault {fault!r} is never asserted to exist"

    def test_the_mutation_axis_has_a_negative_control(self, m4):
        """A vocabulary that accepts anything is fuzzing in a costume."""
        negative = [c for c in m4.commands if "--inject not-a-real-fault" in c.run]
        assert negative, "nothing proves the fault vocabulary is actually closed"
        assert negative[0].expect_exit_code == 2, "a refusal must be a non-zero exit"
        assert "unknown fault" in negative[0].expect_contains
        assert "Traceback (most recent call last)" in m4.forbidden

    def test_an_unfreeze_fault_is_refused_because_the_transition_does_not_exist(self, m4):
        """The M4-specific negative control, and the reason it exists.

        Residual `G2-D15` records that the UNFREEZE direction of AP-9's `frozen`
        flag is unmodelled: no M4 row clears it, there is no `ApprovalUnfrozen`
        event, and `RealityEstablished` is an M3 fact on a different aggregate.
        A probe that ACCEPTED an unfreeze fault would be producing passing
        evidence for a transition nobody authorized — which is exactly how a
        recorded residual gets quietly closed by a build session.
        """
        unfreeze = [c for c in m4.commands if "--inject unfreeze" in c.run]
        assert unfreeze, "nothing refuses an unfreeze fault"
        assert unfreeze[0].expect_exit_code == 2
        assert "unknown fault" in unfreeze[0].expect_contains

    def test_inventing_an_unfreeze_surface_is_a_scenario_failure(self, m4):
        """And the residual is preserved by a check over the corpus, not a hope."""
        guard = [
            c for c in m4.expect_state
            if "invented unfreeze surfaces: []" in c.contains
        ]
        assert guard, (
            "nothing asserts that no ApprovalUnfrozen event, transition or write was "
            "invented; residual G2-D15 could be silently closed by the build session"
        )
        assert "invented unfreeze/extra transition rows: []" in guard[0].contains, (
            "the code scan alone would miss a transition row minted in the specs, which is "
            "the form the rejected candidate's invention actually took"
        )
        assert "### APPROVAL UNFROZEN ###" in m4.forbidden

    def test_the_g2_d15_guard_is_capable_of_failing(self, m4, tmp_path: Path):
        """A guard never seen to fail is a decoration.

        Run the check's own source against three trees: a clean one, one where a
        135th transition row has been minted, and one where the event registry
        has grown an `ApprovalUnfrozen` row. It must pass the first and fail the
        other two — and it must NOT fire on prose that merely describes the
        absence, because describing a recorded residual is the correct
        behaviour and a guard that punished it would have to be switched off.
        """
        import shlex
        import subprocess
        import sys

        guard = [c for c in m4.expect_state if "G2-D15" in c.name][0]
        source = shlex.split(guard.command)[2]

        def run(tree: Path) -> str:
            proc = subprocess.run(
                [sys.executable, "-c", source],
                cwd=str(tree), capture_output=True, text=True, timeout=120,
            )
            assert "Traceback" not in proc.stderr, proc.stderr
            return proc.stdout

        def tree(name: str, *, machine: str = "", code: str = "") -> Path:
            root = tmp_path / name
            (root / "src" / "freight_recon").mkdir(parents=True)
            (root / "scripts").mkdir(parents=True)
            (root / "docs" / "specifications" / "state-machines").mkdir(parents=True)
            (root / "src" / "freight_recon" / "approval.py").write_text(
                code or "# the approval machine\n", encoding="utf-8"
            )
            (root / "docs" / "specifications" / "state-machines" / "04-approval.machine.md"
             ).write_text(
                "| **AP-9** | `GRANTED` -> `GRANTED` *(frozen)* | S | unknown outcome | "
                "`frozen=true` | `ApprovalFrozen` |\n"
                "\nResidual G2-D15: there is no `ApprovalUnfrozen` event and no row clears "
                "`frozen`.\n" + machine,
                encoding="utf-8",
            )
            return root

        clean = run(tree("clean"))
        assert "invented unfreeze surfaces: []" in clean
        assert "invented unfreeze/extra transition rows: []" in clean, (
            "prose describing the recorded residual made the guard fire; it would have to "
            "be switched off to keep the residual documented"
        )

        minted_row = run(tree(
            "minted-row",
            machine="| **AP-10** | `GRANTED` -> `GRANTED` | S | reality established | "
                    "clears `frozen` | `ApprovalUnfrozen` |\n",
        ))
        assert "invented unfreeze/extra transition rows: []" not in minted_row

        minted_code = run(tree(
            "minted-code",
            code="ApprovalUnfrozen = 'ApprovalUnfrozen'\n",
        ))
        assert "invented unfreeze surfaces: []" not in minted_code

    def test_it_carries_regression_anchors_for_every_layer_m4_builds_on(self, m4):
        anchors = " ".join(c.run for c in m4.commands)
        for layer in (
            "test_phase3_claim_cas.py",           # the CAS M4 co-commits inside
            "test_phase3_brake.py",
            "test_phase3_fingerprint.py",         # fp_v1, which M4 consumes and may not rewrite
            "test_phase3_checkpoint_matrix.py",   # steps 1 and 2 are where an approval lives
            "test_import_gate.py",                # the P4 boundary M4 may not widen
            "test_adapter_boundary_acceptance.py",
            "test_phase5_event_transport.py",     # the P5 transport M4 rides
            "test_p5_durable_timers.py",          # AP-3 is a durable timer, never a sweep
            "test_phase6_work_item.py",           # M1
            "test_phase6_pipeline_instance.py",   # M2, the AWAITING_APPROVAL seam
            "test_phase6_external_effect.py",     # M3, whose claim CAS consumes the approval
            "probe_phase6_external_effect.py",    # M3's own oracle, re-run
        ):
            assert layer in anchors, f"no regression anchor for {layer}"

    def test_persisted_state_is_the_oracle_not_a_printed_line(self, m4):
        """M4's whole claim is about durable rows, so it must inspect rows."""
        assert len(m4.expect_state) >= 8
        names = " ".join(c.name for c in m4.expect_state)
        assert "FOREIGN KEY" in names
        assert "dark posture" in names
        assert "UNIQUE partial index" in names

    def test_the_eight_states_are_asserted_and_there_is_no_ninth(self, m4):
        vocabulary = [
            c for c in m4.expect_state
            if "state vocabulary" in c.command or "state vocabulary" in c.name
        ]
        assert vocabulary, "nothing asserts the canonical approval state set"
        declared = vocabulary[0]
        for state in STATES:
            assert f"'{state}'" in declared.contains, state
        assert "'SUPERSEDED'" in declared.not_contains, (
            "ADR-005 §3.10 says supersession is drift-void ∪ duplicate-refusal and there "
            "is no third case, so a ninth state must be asserted ABSENT rather than "
            "merely not mentioned"
        )

    def test_it_refuses_the_failures_m4_exists_to_prevent(self, m4):
        for forbidden in (
            "### DRIFTED APPROVAL EXECUTED ###",
            "### APPROVAL CONSUMED TWICE ###",
            "### APPROVAL GRANTED WITHOUT A HUMAN ###",
            "### EXPIRED APPROVAL EXECUTED ###",
            "### FROZEN APPROVAL REUSED ###",
            "### APPROVAL UNFROZEN ###",
            "### CONSUMED WITHOUT A DURABLE CLAIM ###",
            "### CLAIM WON WITH A REUSABLE APPROVAL ###",
            "### APPROVAL AUTHORITY DURING REPLAY ###",
            "### PARTIAL APPROVAL APPLIED ###",
            "### QUORUM MET BY ONE ACTOR ###",
            "### CROSS-TENANT APPROVAL ACCEPTED ###",
            "### SECOND EFFECT AUTHORITY ###",
        ):
            assert forbidden in m4.forbidden, forbidden

    def test_the_task_file_and_the_scenario_agree_on_the_contract(self, m4, cases, dimensions):
        """The builder writes the probe; the scenario asserts its output. Those
        two documents are only a contract while they say the same strings."""
        for case in cases:
            assert case in M4_TASK, f"the task never tells the builder to build case {case!r}"
        for dimension in dimensions:
            assert dimension in M4_TASK, f"the task never specifies {dimension!r}"
        for axis in ("--inject", "--list-dimensions", "--list-cases", "--case"):
            assert axis in M4_TASK, f"the task never specifies {axis}"
        for literal in m4.expect_visible:
            assert literal in M4_TASK, (
                f"the scenario requires the literal {literal!r} to be observable and the "
                "task never asks the builder to print it"
            )
        for literal in m4.forbidden:
            if literal.startswith("###"):
                assert literal in M4_TASK, f"the task never forbids {literal!r}"
        for path in DELIVERABLES:
            assert path in M4_TASK, f"the task never names the deliverable {path}"
            assert path in m4.fixtures, f"the scenario never requires {path} to exist"

    def test_the_task_states_the_canonical_machine_rather_than_a_generic_feature(self):
        """M4 is a specific machine with specific rows, not "an approval feature"."""
        for state in STATES:
            assert state in M4_TASK_FLAT, f"the task never names the canonical state {state}"
        for transition in TRANSITIONS:
            assert transition in M4_TASK_FLAT, (
                f"the task never requires the canonical transition {transition}; a builder "
                "told to 'build approvals' will design its own lifecycle"
            )
        assert "no `SUPERSEDED`" in M4_TASK_FLAT
        # The central product invariant, stated as the reason for everything else.
        assert "A HUMAN APPROVES AN ACTION PLUS THE EXACT MATERIAL FACTS" in M4_TASK_FLAT
        assert "THERE IS A NEW QUESTION" in M4_TASK_FLAT

    def test_the_task_forces_the_authority_to_be_read_first(self):
        for document in (
            "PRODUCT.md",
            "CLAUDE.md",
            "docs/implementation/CURRENT.md",
            "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
            "docs/specifications/entities/06-approval.md",
            "docs/specifications/state-machines/04-approval.machine.md",
            "docs/specifications/state-machines/registry.md",
            "ADR-005",
        ):
            assert document in M4_TASK_FLAT, f"the task never sends the builder to {document}"
        assert "Do not write code until you have read these" in M4_TASK_FLAT
        assert "REPORT THE CONFLICT. Do not invent a resolution" in M4_TASK_FLAT

    def test_the_task_preserves_the_recorded_residual_rather_than_closing_it(self):
        assert "G2-D15" in M4_TASK_FLAT
        for forbidden_invention in (
            "an `ApprovalUnfrozen` event",
            "an unfreeze transition",
            "a 135th transition row",
            "a new canonical event of any kind",
            "a hidden `RealityEstablished` write",
        ):
            assert forbidden_invention in M4_TASK_FLAT, forbidden_invention
        assert "fail closed and preserve the recorded residual" in M4_TASK_FLAT

    def test_the_task_refuses_to_resolve_the_known_authority_question(self):
        """AP-7 puts consumption in the claim txn; AP-8/AP-9 are written from
        GRANTED on outcomes that can only arrive after it. The corpus does not
        say which reading is right, and neither the task nor the scenario may.
        """
        assert "THE KNOWN AUTHORITY QUESTION" in M4_TASK_FLAT
        assert "Do not invent a reconciliation" in M4_TASK_FLAT
        assert "Report the conflict explicitly" in M4_TASK_FLAT
        # And the clauses it refuses to choose between are quoted, so a reader
        # can check the claim rather than take it.
        assert "AP-7" in M4_TASK_FLAT and "AP-8" in M4_TASK_FLAT and "AP-9" in M4_TASK_FLAT
        assert "EffectCommitted" in M4_TASK_FLAT

    def test_the_task_names_every_prohibited_expansion(self):
        for prohibition in (
            "M5–M13",
            "P7 or later",
            "freight workflows",
            "Slack",
            "Gmail",
            "TMS",
            "live production effects",
            "production autonomy",
            "second checkpoint or effect authority",
            "replace P3's checkpoint kernel",
            "redesign M3",
            "legacy cleanup campaign",
            "remediate nonblocking debt merely because it exists",
            # P0-P5 are COMPLETE. "Do not WEAKEN P3/P4/P5" is a different
            # sentence from "do not REDESIGN them", and the gap between the two
            # is exactly where a builder decides the baseline was the real
            # problem all along.
            "redesign P0, P1, P2, P3, P4 or P5",
            "invoice automation",
        ):
            assert prohibition in M4_TASK_FLAT, f"the task never prohibits {prohibition!r}"
        assert "smallest blocking prerequisite" in M4_TASK_FLAT, (
            "the task never says what to do about a tiny pre-existing defect that really "
            "does block verification, so a builder will either stop or over-reach"
        )

    def test_the_task_carries_the_strict_order_obligation_m4_inherits(self):
        """`events/registry.md` §8 names `F4 Approval` a strict-order aggregate.

        M3 discharged this obligation on ITS aggregate (`P6-D24`, and §8's
        complete-stream rule). M4 does not inherit M3's discharge — it inherits
        the obligation, on `approval`. Both halves have to be stated, because
        they fail in opposite directions: without `previous_aggregate_version` a
        consumer parks on a co-committed non-emission and never unparks, and
        without the complete-stream rule a family-filtered consumer discards its
        own predecessor and never unblocks.
        """
        assert "F4 Approval" in M4_TASK_FLAT, (
            "the task never tells the builder the approval aggregate is strict-order"
        )
        assert "previous_aggregate_version" in M4_TASK_FLAT
        assert "COMPLETE* AGGREGATE STREAM, NEVER A FAMILY SUBSET" in M4_TASK_FLAT
        # The P6-D11 reading, which is the whole reason the declaration exists.
        assert "IT HAS NEVER MEANT *CONTIGUOUS*" in M4_TASK_FLAT
        assert "A version with no event on the stream is **NORMAL, not a loss**" in M4_TASK_FLAT
        # And the live case that made the complete-stream rule necessary.
        assert "IllegalTransitionAttempted" in M4_TASK_FLAT
        # It is a requirement on the SUBSCRIPTION, so no new sequencing mechanism.
        assert "Do not introduce a new sequencing mechanism" in M4_TASK_FLAT

    def test_the_task_carries_er_16_so_the_freeze_is_rebuilt_from_evidence(self):
        """ER-16 is the other half of AP-9, and it is a rule about REPLAY.

        `ApprovalFrozen` is the SOLE canonical evidence that an approval is
        frozen. A rebuild that instead infers the freeze from `OutcomeUnknown`
        AND NOT `RealityEstablished` has made a safety-critical quarantine
        depend on a fold being complete and correctly ordered — which is exactly
        what §8 says a consumer may not assume.
        """
        assert "ER-16" in M4_TASK_FLAT, "the task never names the rule"
        assert "reconstructed from POSITIVE evidence, never from an absence" in M4_TASK_FLAT
        assert "unknown_outcome_ref" in M4_TASK_FLAT, (
            "ApprovalFrozen's payload is what binds the freeze to the chain that caused "
            "it; without it the positive evidence cannot be resolved"
        )
        # And the half ER-16 does NOT relax: at runtime, still frozen.
        assert "the rule governs what replay may RELY ON" in M4_TASK_FLAT


# --------------------------------------------------------------------------
# 2. The vocabulary is safe, and the generator can actually see it
# --------------------------------------------------------------------------


class TestTheM4Vocabulary:
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
            base_scenario=load_scenario(M4_PATH),
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

        Approval matches by PREFIX and refuses shell composition in the tail, so
        every combination of dimensions is already permitted by the single bare
        probe entry — the axis buys the generator a large bounded space and buys
        the boundary nothing to defend.
        """
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case consume-cas-in-the-claim-txn --inject {fault} "
                "--concurrency 8 --delay-ms 5000 --repeat 5 --tenants 3 --signers 4 --seed 4211"
            )
            ok, why = approved.approves(command)
            assert ok, f"{fault}: {why}"

    @pytest.mark.parametrize(
        "command",
        [
            # A command nobody wrote down.
            "curl https://api.example.com/approve",
            ".venv/bin/python scripts/send_real_invoice.py",
            "rm -rf src/freight_recon",
            # A live approval surface, which is precisely what M4 must not grow.
            ".venv/bin/python scripts/slack_probe.py --post-approval-card",
            # The approved probe, extended with composition.
            f"{PROBE} --case amount-drift-voids; curl https://evil.example.com",
            f"{PROBE} --case amount-drift-voids && rm -rf /",
            f"{PROBE} | tee /tmp/out",
            f"{PROBE} > /etc/hosts",
            f"{PROBE} --case $(whoami)",
            f"{PROBE} --case `whoami`",
            # A newline is whitespace; normalization would hide it, so the raw
            # string is scanned for control characters first.
            f"{PROBE}\ncurl https://evil.example.com",
            # A prefix that is not a prefix.
            ".venv/bin/python scripts/probe_phase6_approval.py.bak",
        ],
    )
    def test_a_command_outside_the_vocabulary_is_refused(self, approved, command):
        ok, why = approved.approves(command)
        assert not ok, f"escaped the approved set: {command!r}"
        assert why, "a refusal must say why"

    def test_a_dimension_value_carrying_shell_is_still_refused(self, approved):
        """The axis is argument-only. A flag is not a hole."""
        for hostile in ("$(id)", "`id`", "a;id", "a|id", "a>/etc/hosts", "a&&id"):
            ok, _ = approved.approves(f"{PROBE} --case amount-drift-voids --inject {hostile}")
            assert not ok, f"a dimension value smuggled shell through: {hostile!r}"

    def test_the_probe_with_an_ordinary_case_tail_is_still_allowed(self, approved):
        """The boundary has to let the real vocabulary through, or it has only
        made generation useless rather than safe."""
        ok, why = approved.approves(f"{PROBE} --case provenance-drift-voids")
        assert ok, why

    def test_the_rendered_brief_actually_shows_the_m4_vocabulary(self, tmp_path):
        """The brief truncates the approved list, silently. A vocabulary the
        generator never sees is a vocabulary it cannot choose from."""
        vocabulary = _local_vocabulary()
        if not any("probe_phase6_approval.py" in entry for entry in vocabulary):
            pytest.skip("no local driver.config.yaml enumerating the M4 vocabulary")

        planner = self._planner(tmp_path, vocabulary)
        planner.plan_initial(task="Build P6/M4 Approval", unit=None, run_id="r-m4")
        brief = planner.reasoner.briefs[0].render()

        assert PROBE in brief, "the deterministic M4 entry point is not in the brief"
        enumerated = " ".join(vocabulary)
        missing = [
            entry.split("--case ", 1)[1].split()[0]
            for entry in vocabulary
            if "probe_phase6_approval.py --case " in entry and entry not in brief
        ]
        assert not missing, (
            "the approved-command list was truncated before these M4 cases: "
            f"{missing}. The brief renders at most {MAX_RENDERED_COMMANDS} commands; the "
            f"approved set now holds {len(planner.approved_commands)}."
        )
        assert "probe_phase6_approval.py" in enumerated

    def test_the_approved_set_still_fits_inside_what_the_brief_renders(self, tmp_path):
        """Approved commands sort ASCII and every probe entry begins
        `scripts/probe_...`, so they sort LAST: an approved set larger than the
        render bound loses the probe vocabulary first, and loses it silently."""
        planner = self._planner(tmp_path, _local_vocabulary())
        assert len(planner.approved_commands) <= MAX_RENDERED_COMMANDS, (
            f"{len(planner.approved_commands)} approved commands but the generation brief "
            f"renders only the first {MAX_RENDERED_COMMANDS} — the M4 vocabulary sorts last "
            "and is now invisible to the generator."
        )


# --------------------------------------------------------------------------
# 3. Dynamic generation can close an M4 coverage gap, safely
# --------------------------------------------------------------------------


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close.

    Built as the planner builds one: the citation lives on the provenance, and a
    coverage-gap case that cannot name a risk from this run's own register is
    refused before it reaches the boundary at all.
    """
    return GeneratedScenario(
        id="gen-m4-drift",
        title="the approved amount moves before the claim, and nothing executes",
        purpose=(
            "the owner approved 2850 and the TMS now says 3100; the approval must void "
            "with a diff and no effect may occur"
        ),
        risk_category=RiskCategory.STALE_STATE,
        priority=Priority.P0,
        rationale="the identified drift risk had no scenario behind it",
        requirement_reference="P6/M4",
        product_principle_reference="effect-truth",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared "
            "state, so nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m4-task",
            session_id="scripted",
            generating_risk="an approval whose amount moved could still authorize the effect",
            source_risks=[risk_key],
        ),
        actions=[{"kind": "command", "name": "drift the approved amount", "command": command}],
        expected_observations=["A DRIFTED FACT IS NOT AN APPROVAL, IT IS A NEW QUESTION"],
        forbidden_observations=["### DRIFTED APPROVAL EXECUTED ###"],
    )


class TestGenerationClosesM4GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-drift",
            description="an approval whose amount moved could still authorize the effect",
            risk_category=RiskCategory.STALE_STATE,
            severity=Priority.P0,
            basis="the live re-read at checkpoint step 2 is the only thing that catches it",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m4", "p6", "m4"},
                principle_tokens={"effect-truth"},
                known_risk_ids={risk.key, "R-drift"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m4_vocabulary_is_accepted(self, context):
        ctx, risk = context
        command = f"{PROBE} --case amount-drift-voids --inject drift-amount --seed 7"
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M4 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_the_whole_mutation_axis_is_reachable_from_a_gap_case(self, context, dimensions):
        ctx, risk = context
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case amount-drift-voids --inject {fault} "
                "--concurrency 4 --delay-ms 40 --signers 2 --seed 11"
            )
            accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
            assert accepted, f"{fault}: {rejected}"

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario("python -c \"import approval; approval.grant()\"", risk.key)], ctx
        )
        assert not accepted
        assert rejected
        reasons = rejected[0][1]
        assert any("approved" in r.lower() for r in reasons), reasons

    def test_a_gap_case_touching_repository_authority_is_refused(self, context):
        """A verification scenario observes the product; it never edits the
        rules the product is judged against."""
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario(f"{PROBE} --case x docs/implementation/CURRENT.md", risk.key)], ctx
        )
        assert not accepted
        reasons = rejected[0][1]
        assert any("authority" in r.lower() for r in reasons), reasons

    def test_an_uncovered_p0_m4_risk_blocks_acceptance(self):
        """Coverage is not a tally. A risk the run itself called P0 with no
        passing scenario behind it prevents an ACCEPT even when everything that
        DID run was green."""
        from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
        from neyma_product_driver.scenario_suite import (
            Origin,
            Outcome,
            ScenarioOutcome,
            SuiteResult,
        )

        passing = ScenarioOutcome(
            scenario_id="gen-grant",
            scenario_name="gen-grant",
            origin=Origin.GENERATED,
            outcome=Outcome.PASSED,
            required=True,
            risk_category="authorization",
            evidence_path="/runs/gen-grant",
            evidence_verified=True,
        )
        result = SuiteResult(outcomes=[passing], expected_required_ids=["gen-grant"])
        assert evaluate_gate(result, risks=[]).status is GateStatus.VERIFIED

        verdict = evaluate_gate(
            result,
            risks=[
                IdentifiedRisk(
                    id="R-drift",
                    description="a drifted approval could still authorize the effect",
                    risk_category=RiskCategory.STALE_STATE,
                    severity=Priority.P0,
                    basis="ADR-005 F-01 is the defect the unit exists to close",
                )
            ],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert "KNOWN COVERAGE GAPS" in verdict.summary_block()


# --------------------------------------------------------------------------
# 4-5. M4 is scoped as a unit, and cannot move the phase
# --------------------------------------------------------------------------


#: A builder report that claims exactly what it did, in the shape the completion
#: auditor reads: a capability sentence, the evidence behind it naming the file
#: it lives in, and the part that is knowingly not done. Modelled on
#: `HONEST_M3_REPORT`, because the auditor is being exercised here rather than
#: re-specified.
HONEST_M4_REPORT = """\
## What a broker can now do

An approval is bound to the exact facts it was granted on, so an owner who
approved 2850 is never billed for 3100 — the approval voids and comes back as a
new question instead.

## What proves it

52 targeted tests pass; the mutation battery caught 10 of 10; the probe reports
behaviours as specified, 0 wrong. The P3, P4, P5, M1, M2 and M3 regressions are
green.

M4's code is implemented in this build session and awaits its one focused
independent review before it can land. src/approval.py carries it.

## What is knowingly incomplete

M5-M13 remain. P6 has not reached phase acceptance and no P6 criterion is
scored.
"""


@pytest.fixture
def m4_repo(tmp_path: Path) -> PhaseRepo:
    """A phase in progress, one unit being built, a stated review rule."""
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/approval.py", "# the unit under construction\n")
    repo.commit_all("the M4 candidate")
    return repo


class TestM4IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m4(self, m4_repo: PhaseRepo):
        scope = m4_repo.scope(M4_TASK)
        assert scope.scope_id == "P6/M4"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(
        self, m4_repo: PhaseRepo
    ):
        """The task discusses P6 at length. Discussing a phase is not claiming
        it, and a run that inherited the phase's bar would be held to twelve
        units that do not exist."""
        scope = m4_repo.scope(M4_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m4_repo: PhaseRepo):
        scope = m4_repo.scope(M4_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(
        self, m4_repo: PhaseRepo
    ):
        rendered = m4_repo.scope(M4_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "does not unblock any later phase" in rendered.replace("does NOT", "does not")
        assert "enables nothing in production" in rendered


class TestM4CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m4_repo: PhaseRepo
    ):
        """The guard lives in one place so there is one place to read it: a
        nested task cannot accept a phase however the caller calls it."""
        scope = m4_repo.scope(M4_TASK)
        completion = scoped_completion(
            scope, TaskResult.ACCEPTED, phase_accepted=True
        )
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M4"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m4_repo: PhaseRepo):
        completion = scoped_completion(m4_repo.scope(M4_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    def test_a_builder_claiming_p6_is_complete_is_caught(self, m4_repo: PhaseRepo):
        audit = m4_repo.audit(
            "M4 is implemented and verified. With M4 landed, P6 is COMPLETE and P7 is "
            "now unblocked.\n",
            M4_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_a_builder_claiming_production_enablement_is_caught(self, m4_repo: PhaseRepo):
        audit = m4_repo.audit(
            "M4 is implemented and verified. The approval channel is now enabled for "
            "live traffic.\n",
            M4_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED


# --------------------------------------------------------------------------
# 6-8. The integrated review: owed, bound to a tree, and retired by a change
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m4_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m4_repo.root, m4_repo.scope(M4_TASK), unit=m4_repo.unit()
        )
        assert requirement.required
        assert ReviewTrigger.REPOSITORY_AUTHORITY in requirement.triggers
        assert requirement.scope_id == "P6/M4"
        assert requirement.fresh_session_required is True
        assert any("CLAUDE.md" in source for source in requirement.sources)

    def test_the_requirement_comes_from_authority_not_from_a_hardcoded_m4_rule(
        self, tmp_path: Path
    ):
        """The point of asking the repository is that the answer can be NO.

        Nothing anywhere says "M4 always needs a review". Strip the rule out of
        the target repository's own authority and the requirement disappears —
        which is what proves the requirement is being read rather than assumed.
        """
        repo = PhaseRepo(tmp_path / "no-review", authority=NO_REVIEW_CLAUDE_MD)
        requirement = resolve_review_requirement(
            repo.root, repo.scope(M4_TASK), unit=repo.unit()
        )
        assert not requirement.required
        assert "no independent review is required" in requirement.brief()

    def test_a_phase_review_criterion_does_not_bind_this_unit(self, m4_repo: PhaseRepo):
        """P6's contract lists `independent_review` because the PHASE will need
        one. A run building one unit inside it is not the phase, and holding it
        to a review of thirteen units — twelve unwritten — is a bar nothing can
        clear."""
        requirement = resolve_review_requirement(
            m4_repo.root, m4_repo.scope(M4_TASK), unit=m4_repo.unit()
        )
        assert ReviewTrigger.PHASE_ACCEPTANCE_CRITERION not in requirement.triggers


class TestAReviewIsAboutOneExactTree:
    def test_a_supported_review_binds_to_the_fingerprint_it_read(self, m4_repo: PhaseRepo):
        fingerprint = capture_fingerprint(m4_repo.root)
        ledger = ReviewLedger()
        entry = ledger.record(
            supported(), fingerprint, scope_id="P6/M4", builder_session_id="builder-1"
        )
        entry.review.reviewer_session_id = "reviewer-1"

        assert ledger.satisfying(fingerprint) is entry
        assert entry.satisfies(fingerprint)
        assert entry.independent

    def test_changing_the_tree_invalidates_the_review(self, m4_repo: PhaseRepo):
        before = capture_fingerprint(m4_repo.root)
        ledger = ReviewLedger()
        record = ledger.record(supported(), before, builder_session_id="builder-1")
        assert ledger.satisfying(before) is not None

        m4_repo.write("src/approval.py", "# the builder corrected something\n")
        after = capture_fingerprint(m4_repo.root)
        assert not before.matches(after)

        assert ledger.invalidate_stale(after) == [record]
        assert record.stale
        assert ledger.satisfying(after) is None
        assert ledger.satisfying(before) is None, "a retired review must stay retired"
        assert ledger.invalidations

    def test_a_review_from_the_builders_own_session_satisfies_nothing(self):
        fingerprint = TreeFingerprint(head="a" * 40, tree="b" * 40)
        ledger = ReviewLedger()
        review = supported()
        review.reviewer_session_id = "builder-1"
        ledger.record(review, fingerprint, builder_session_id="builder-1")
        assert ledger.satisfying(fingerprint) is None

    def test_the_auditor_re_derives_the_tree_rather_than_trusting_the_record(
        self, m4_repo: PhaseRepo
    ):
        scope = m4_repo.scope(M4_TASK)
        ledger = ReviewLedger()
        entry = ledger.record(
            supported(),
            capture_fingerprint(m4_repo.root),
            scope_id=scope.scope_id,
            builder_session_id="builder-1",
        )
        auditor = CompletionAuditor(m4_repo.root)

        fresh = auditor.audit(
            HONEST_M4_REPORT, unit=m4_repo.unit(), scope=scope, satisfying_review=entry
        )
        assert fresh.decision is AuditDecision.VERIFIED

        m4_repo.write("src/approval.py", "# changed after the review\n")
        stale = auditor.audit(
            HONEST_M4_REPORT, unit=m4_repo.unit(), scope=scope, satisfying_review=entry
        )
        assert stale.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW


# --------------------------------------------------------------------------
# 9-12. The whole loop, end to end
# --------------------------------------------------------------------------


class TestTheLoopOwnsM4EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m4_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session
        that wrote the code, with its evidence path intact."""
        builder = FakeBuilder(m4_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m4_repo, tmp_path, task=M4_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        correction = builder.prompts[1]
        assert "INDEPENDENT REVIEW" in correction
        assert "src/external_effect.py:88" in correction, (
            "the finding reached the builder without the evidence it cited"
        )
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED

    async def test_the_corrected_tree_gets_a_brand_new_reviewer(
        self, m4_repo: PhaseRepo, tmp_path: Path
    ):
        builder = FakeBuilder(m4_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m4_repo, tmp_path, task=M4_TASK, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        assert len(set(reviewer.session_ids)) == 2, "the same reviewer session was reused"
        first = reviewer.bindings[0]["fingerprint"]
        second = reviewer.bindings[1]["fingerprint"]
        assert not first.matches(second), "the second reviewer read the same tree as the first"
        assert result.satisfying_review.fingerprint.matches(second)
        assert result.review_ledger.records[0].stale is True
        assert result.review_ledger.invalidations

    async def test_a_reviewer_that_keeps_refusing_becomes_a_founder_decision(
        self, m4_repo: PhaseRepo, tmp_path: Path
    ):
        """A correction budget that never converges is a product decision, not a
        defect to keep sending back."""
        builder = FakeBuilder(m4_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), refusing(), refusing()])
        result, _store = await drive(
            m4_repo, tmp_path, task=M4_TASK, builder=builder, reviewer=reviewer
        )
        assert result.status is RunStatus.NEEDS_USER

    async def test_an_accept_is_scoped_m4_acceptance_and_never_p6_complete(
        self, m4_repo: PhaseRepo, tmp_path: Path
    ):
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(
            m4_repo, tmp_path, task=M4_TASK, reviewer=reviewer
        )

        assert result.status is RunStatus.ACCEPTED
        assert result.audit is not None, "the run accepted without a completion audit"
        completion = result.audit.completion
        assert completion is not None
        assert completion.task_scope == "P6/M4"
        assert completion.task_result in {TaskResult.ACCEPTED, TaskResult.VERIFIED}
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_accepted is False
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    async def test_the_run_stops_at_m4_and_never_walks_into_m5(
        self, m4_repo: PhaseRepo, tmp_path: Path
    ):
        """Two halves of the same guarantee: the task forbids it in words, and
        the loop ends at its own scoped verdict rather than picking up the next
        unit."""
        assert "Stop at verified M4. Do not automatically continue into M5." in M4_TASK
        assert "begin **M5–M13**" in M4_TASK

        reviewer = FakeReviewer([supported()])
        result, store = await drive(
            m4_repo, tmp_path, task=M4_TASK, reviewer=reviewer
        )
        assert result.status is RunStatus.ACCEPTED
        assert result.audit.completion.task_scope == "P6/M4"

        journal = RunJournal(run_id=store.run_id, task=M4_TASK)
        journal.record_outcome(run_status="ACCEPTED")
        summary = journal.personal_summary()
        for forbidden in ("M5", "begin the next unit", "continue into"):
            assert forbidden not in summary.split("### 8. The ONE exact next move")[1], (
                f"the next move points past M4 ({forbidden!r})"
            )


# --------------------------------------------------------------------------
# 13. The founder summary says what M4 actually does, in normal language
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


class _Decision:
    def __init__(self, observed):
        self.observed_behavior = list(observed)


def _m4_journal(**outcome) -> RunJournal:
    scenario = load_scenario(M4_PATH)
    journal = RunJournal(run_id="r-m4", task=M4_TASK)
    journal.task_scope_id = "P6/M4"
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


class TestTheFounderSummaryExplainsM4:
    def test_it_answers_the_eight_plain_terms_questions(self, tmp_path: Path):
        journal = _m4_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        journal.record_start(DRIVER_ROOT)
        journal.record_end(DRIVER_ROOT)
        journal.save(tmp_path / "run")

        summary = (tmp_path / "run" / "FOUNDER-SUMMARY.md").read_text(encoding="utf-8")
        for heading in (
            "PERSONAL SUMMARY — SIMPLE TERMS",
            "### 1. What we just built or fixed",
            "### 2. Why this matters for Neyma",
            "### 3. What is actually proven true",
            "### 4. What Neyma can safely do now that it could not before",
            "### 5. Independent review",
            "### 6. What is still NOT built",
            "### 7. Where Neyma is in the roadmap",
            "### 8. The ONE exact next move",
            "### 9. Founder decisions needed",
        ):
            assert heading in summary, heading
        assert summary.index("PERSONAL SUMMARY") < summary.index("What did the Driver work on?")

    def test_it_states_the_product_impact_in_normal_language(self):
        """The sentence a founder needs, in the words a founder uses.

        It is rendered from the permanent scenario's human-authored description,
        not from anything a model wrote, and it appears under section 2 as a
        statement of PURPOSE — never as evidence that any of it works.
        """
        journal = _m4_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        section = journal.personal_summary().split("### 2. Why this matters for Neyma")[1]
        section = section.split("### 3.")[0]

        assert "I approved one thing, but Neyma executed a different thing." in section
        assert "statement of purpose rather than a finding" in section

    def test_the_purpose_is_stated_even_when_nothing_was_proven(self):
        """A blocked run still has to be able to say what it was trying to do.

        And it must not thereby claim any of it happened: section 3 and 4 still
        say nothing is established.
        """
        journal = _m4_journal(run_status="BLOCKED", gate=_Gate("NOT_VERIFIED"))
        summary = journal.personal_summary()

        assert not journal.verification_established
        assert "I approved one thing, but Neyma executed a different thing." in summary
        assert "**Nothing new.**" in summary
        assert "Nothing is established as proven by this run." in summary

    def test_it_does_not_imply_live_ux_freight_workflows_or_production(self):
        journal = _m4_journal(
            run_status="ACCEPTED",
            gate=_Gate("VERIFIED", passed=9, total=9),
            decision=_Decision(
                ["an approval whose amount moved authorizes no effect"]
            ),
        )
        summary = journal.personal_summary()

        assert "an approval whose amount moved authorizes no effect" in summary
        assert "not deployed, not enabled for any real tenant" in summary
        assert "no external effect was performed" in summary
        for rule in RunJournal.NEVER_UPGRADE:
            assert rule in summary, rule
        # The scenario's own description carries the dark posture with it.
        assert "ships dark by contract" in summary

    def test_it_never_says_p6_moved(self):
        journal = _m4_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary()
        assert "this run built **P6/M4**, one unit inside **P6**" in summary
        assert "P6 is IN_PROGRESS and this run did not move it." in summary
        assert "does not complete P6" in summary
        assert "does not unblock the phase after it" in summary
        assert "P6 is COMPLETE" not in summary

    def test_a_builder_claim_never_becomes_a_finding(self):
        journal = _m4_journal(
            run_status="ACCEPTED",
            gate=_Gate("NOT_VERIFIED"),
            decision=_Decision(["a drifted approval cannot execute"]),
            builder_claims=["M4 is complete and fully verified."],
        )
        summary = journal.personal_summary()
        assert not journal.verification_established
        assert "What the builder SAYS it did — a claim, not a finding" in summary
        assert "M4 is complete and fully verified." in summary
        assert "a drifted approval cannot execute" not in summary

    def test_no_founder_decision_says_none(self):
        journal = _m4_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=1, total=1))
        block = journal.personal_summary().split("### 9. Founder decisions needed")[1]
        assert block.strip().startswith("- None.")

    def test_the_next_move_is_exactly_one_move(self):
        for status, gate in (
            ("ACCEPTED", _Gate("VERIFIED", passed=1, total=1)),
            ("BLOCKED", _Gate("NOT_VERIFIED")),
        ):
            journal = _m4_journal(run_status=status, gate=gate)
            block = journal.personal_summary().split("### 8. The ONE exact next move")[1]
            block = block.split("### 9.")[0].strip()
            assert block.count("\n- ") == 0 and block.startswith("- "), block


# --------------------------------------------------------------------------
# Housekeeping the M3 readiness file already asserts for its own unit
# --------------------------------------------------------------------------


def test_the_suite_budget_covers_the_permanent_scenario_it_runs_first():
    """A budget smaller than the base scenario does not shorten anything.

    Permanent scenarios execute first and the budget is checked BEFORE each
    scenario starts, so an undersized budget lets the base scenario finish and
    then SKIPS every generated case. The gate reads a skip as unverified — which
    it is — and the run blocks on a stopwatch rather than on a finding.
    """
    local = DRIVER_ROOT / "driver.config.yaml"
    if not local.exists():
        pytest.skip("no local driver.config.yaml")
    raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    if raw.get("scenario") != "p6_m4_approval":
        pytest.skip("the local config is not pointed at the M4 scenario")

    budget = int((raw.get("scenario_generation") or {}).get("execution_budget_s") or 1800)
    scenario = load_scenario(M4_PATH)
    declared = sum(int(c.timeout_s or 300) for c in scenario.commands)
    declared += sum(int(c.timeout_s or 300) for c in scenario.expect_state)

    assert budget > declared, (
        f"the base scenario's declared timeouts sum to {declared}s and the suite budget is "
        f"{budget}s — every generated scenario would be skipped as budget-exhausted, and "
        "the acceptance gate would block on that rather than on the product"
    )


def test_the_m4_scenario_does_not_name_the_deleted_pytest_config():
    """A scenario still passing `-c pytest-canonical.ini` fails on a missing file,
    which teaches the generator a vocabulary that cannot succeed and reports a
    product failure that is not one. The TASK may name it — it names it to forbid
    reintroducing it — but no command anywhere may pass it."""
    assert "pytest-canonical.ini" not in M4_PATH.read_text(encoding="utf-8")
    assert "no longer exists" in M4_TASK_FLAT
    for command in load_scenario(M4_PATH).commands:
        assert "pytest-canonical.ini" not in command.run


def test_the_direction_record_is_still_pointed_at():
    """A cross-reference to a document nobody keeps is how a design record rots."""
    doc = DRIVER_ROOT / "docs" / "SCENARIO-SPACE.md"
    assert doc.is_file(), "docs/SCENARIO-SPACE.md is referenced but missing"
    assert "SCENARIO-SPACE.md" in M4_PATH.read_text(encoding="utf-8")

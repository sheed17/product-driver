"""The run that ran its own oracle and was still told nobody had looked.

Run 20260913-041432, one repair later. The changed-verification fix had landed
and was working: the task's whole deliverable was two boundary guards, and
Product Driver selected them out of the diff, executed them with the
repository's own runner, and read the exit statuses itself. Both green. The
reconciliation guard's forced-drift case — the one that proves the guard still
turns red when the live-status block and the registry disagree — ran and passed
beside it. The evaluator, shown that observation, accepted. Every scenario
passed.

The run ended BLOCKED::

    [P1] conflicting_evidence — The live-status restatement in CURRENT.md could
    drift from IMPLEMENTATION-REGISTRY ... (no scenario exercising this risk was
    executed, so nothing about it has been verified)

Which was true about the shape of the ledger and false about the world. The
direct oracle for that exact risk was in the run's own evidence directory with a
passing exit status and a discrimination case beside it. The coverage ledger
recognised two kinds of evidence, both of them scenarios, so the only move left
was to ask for a generated approximation of a measurement the driver was already
holding — and hand the founder the difference.

What these tests hold, in both directions, because either half alone is a
different defect:

* a guard this change DELIVERED, that this driver RAN, that passed with its own
  anti-vacuity case, and that is observed to read everything the risk NAMES,
  verifies that risk. No generated scenario is demanded on top of it;
* and nothing weaker does. Not a guard that measures part of the risk; not the
  repository's standing collateral; not an unrelated green; not a builder's
  report; not an absence-asserting guard with nothing to show it can still fire;
  not a guard that could not be executed. Where a guard and a scenario disagree
  about one risk, the run fails closed and says so.

The repository here is synthetic and lives in a temporary directory. Every
Claude session is faked; nothing here consumes Claude usage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neyma_product_driver.changed_verification import (
    ChangedSurfaceVerification,
    assertion_span,
    verify_changed_surface,
)
from neyma_product_driver.cli import _coverage_gap_only, _risk_coverage_headline
from neyma_product_driver.guard_coverage import (
    MIN_SUBJECTS,
    guard_evidence,
    guard_measurements,
    subjects,
)
from neyma_product_driver.models import RunStatus
from neyma_product_driver.scenario_gate import (
    BASIS_CATEGORY,
    BASIS_GUARD,
    SOURCE_GUARD,
    GateStatus,
    evaluate_gate,
    risk_coverage,
)
from neyma_product_driver.scenario_plan import IdentifiedRisk, Priority, RiskCategory
from neyma_product_driver.scenario_suite import (
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteResult,
)
from neyma_product_driver.scenarios import Scenario

from test_changed_verification import (  # noqa: F401 — guard_repo is a fixture
    RECONCILIATION_GUARD,
    DeliveringBuilder,
    GuardRepo,
    accepts,
    changed_config,
    drive,
    guard_repo,
)
from test_scenario_loop import FakeEvaluator, RecordingExecutor

from neyma_product_driver.cli import run_control_loop

# --------------------------------------------------------------------------
# The two risks this run named about one changed guard
# --------------------------------------------------------------------------

#: The risk the delivered guard IS the oracle for. It names the derived view,
#: the machine authority and the bounded region the projection lives in — three
#: concrete artifacts, each of which the guard's own assertions open and read.
MEASURED_RISK = IdentifiedRisk(
    id="R4-w3",
    description=(
        "The live-status restatement in status/CURRENT.md could drift from "
        "registry.yaml (the machine authority) — either because the derivation is "
        "wrong or because the LIVE-STATUS guard proving non-drift is vacuous — "
        "silently making the status document contradict the registry it must never "
        "override."
    ),
    risk_category=RiskCategory.CONFLICTING_EVIDENCE,
    severity=Priority.P1,
    basis="task: add a guard proving the live status restatement cannot drift",
)

#: A larger risk over the same documents. The guard reads two thirds of it and
#: is silent about the third, so it discharges none of it: the historical prose
#: nobody may rewrite is not something this guard ever opens.
BROADER_RISK = IdentifiedRisk(
    id="R4-w3-wide",
    description=(
        "Regenerating the LIVE-STATUS projection in status/CURRENT.md from "
        "registry.yaml could rewrite or reorder the historical_narrative section of "
        "the same document, which no mechanism may compose or move."
    ),
    risk_category=RiskCategory.REGRESSION,
    severity=Priority.P0,
    basis="task: historical prose remains untouched",
)

#: A guard that asserts an absence and has nothing to show it can still fire.
#: Same subject matter as the real one, so the ONLY thing separating them is
#: the anti-vacuity case.
VACUOUS_RECONCILIATION_GUARD = '''\
"""The live-status block may not drift from the registry."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
BLOCK = re.compile(r"LIVE-STATUS:BEGIN -->(.*?)<!-- LIVE-STATUS:END", re.S)


def live_block(text=None):
    text = text if text is not None else (ROOT / "status" / "CURRENT.md").read_text()
    found = BLOCK.search(text)
    return dict(
        (line.split(":", 1)[0].strip(), line.split(":", 1)[1].strip())
        for line in (found.group(1) if found else "").splitlines()
        if ":" in line
    )


def registry():
    return dict(
        (line.split(":", 1)[0].strip(), line.split(":", 1)[1].strip())
        for line in (ROOT / "registry.yaml").read_text().splitlines()
        if ":" in line
    )


def test_no_key_in_the_live_status_block_disagrees_with_the_registry():
    known = registry()
    for key, value in live_block().items():
        assert known.get(key, value) == value
'''


def _suite(*outcomes: ScenarioOutcome) -> SuiteResult:
    return SuiteResult(
        full_run=True,
        expected_required_ids=[o.scenario_id for o in outcomes],
        outcomes=list(outcomes),
    )


def _unrelated_pass(scenario_id: str = "p6_unrelated_brake") -> ScenarioOutcome:
    """The one scenario the real run executed: green, and about something else."""
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=Origin.PERMANENT,
        outcome=Outcome.PASSED,
        priority=Priority.P0,
        risk_category="",
        required=True,
        evidence_path=f"/runs/20260913-041432/scenarios/{scenario_id}",
        evidence_verified=True,
    )


def _generated(
    scenario_id: str,
    category: RiskCategory,
    outcome: Outcome = Outcome.PASSED,
) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=Origin.GENERATED,
        outcome=outcome,
        priority=Priority.P0,
        risk_category=category.value,
        required=True,
        evidence_path=f"/runs/20260913-041432/scenarios/{scenario_id}",
        evidence_verified=True,
        failed_assertions=(
            ["the projection and the registry disagree about U-7"]
            if outcome is Outcome.FAILED
            else []
        ),
    )


def _run_the_changed_guards(
    repo: GuardRepo, *, base: str, head: str = ""
) -> ChangedSurfaceVerification:
    """Exactly what the loop's changed-verification step does, and nothing else."""
    return verify_changed_surface(
        repo.root,
        [
            "guards/test_ships_dark.py",
            "guards/test_status_reconciliation.py",
            "status/CURRENT.md",
        ],
        base_commit=base,
        commit=head or repo.head(),
        timeout_s=300,
    )


# --------------------------------------------------------------------------
# 1. The run, reproduced: the guard runs, and the risk it measures is verified
# --------------------------------------------------------------------------


class TestTheRunThatRanItsOwnOracle:
    @pytest.mark.asyncio
    async def test_a_delivered_reconciliation_guard_verifies_the_risk_it_measures(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        """The whole defect, end to end, in the order it happened.

        A task delivers a reconciliation guard. The driver runs it. Its positive
        case passes and its forced-drift case — the one that proves a drift is
        noticed — passes beside it. The risk that guard is the direct oracle for
        is then verified, by that guard, with no generated scenario demanded.
        """
        base = guard_repo.head()
        config = changed_config(guard_repo, tmp_path, timeout_s=300)

        result, _log, _evaluator, store = await drive(
            config, guard_repo, [accepts(), accepts()]
        )

        # 2. the driver ran it — a command it executed, not a report it was given
        verification = result.changed_verification
        assert verification is not None
        assert "guards/test_status_reconciliation.py" in verification.executed_paths
        ran = next(
            r
            for r in verification.results
            if r.target.path == "guards/test_status_reconciliation.py"
        )
        assert ran.passed and not ran.infrastructure
        assert "pytest" in ran.target.command

        # 3. + 4. the positive case AND the forced-drift discrimination case
        guard = next(
            g for g in verification.guards if g.path == "guards/test_status_reconciliation.py"
        )
        assert guard.relation == "changed"
        assert "test_the_live_status_block_does_not_drift_from_the_registry" in guard.test_names
        assert guard.discrimination_names == [
            "test_the_reconciliation_is_populated_and_its_drift_detector_fires"
        ]
        assert not verification.discrimination_gaps
        assert not verification.blocks_claim

        # 5. the risk that guard measures is verified, and the citation says so
        covered, gaps = risk_coverage(
            [MEASURED_RISK],
            _suite(_unrelated_pass()),
            changed_verification=verification,
        )
        assert [g.brief() for g in gaps] == []
        assert len(covered) == 1
        cited = covered[0]
        assert cited.risk_id == "R4-w3"
        assert cited.basis == BASIS_GUARD
        assert cited.evidence_source == SOURCE_GUARD
        assert cited.measurement == "guards/test_status_reconciliation.py"
        assert "pytest" in cited.command
        assert cited.discrimination == [
            "test_the_reconciliation_is_populated_and_its_drift_detector_fires"
        ]

        # ... and no redundant generated scenario is demanded for it
        verdict = evaluate_gate(
            _suite(_unrelated_pass()),
            risks=[MEASURED_RISK],
            changed_verification=verification,
        )
        assert verdict.status is GateStatus.VERIFIED
        assert not verdict.uncovered_risks
        assert not _coverage_gap_only(verdict, _suite(_unrelated_pass()))
        assert "repository guard" in _risk_coverage_headline(verdict)

        # 6. and the broader risk over the same documents is still open, naming
        #    the part the guard never reads.
        wide = evaluate_gate(
            _suite(_unrelated_pass()),
            risks=[MEASURED_RISK, BROADER_RISK],
            changed_verification=verification,
        )
        assert wide.status is GateStatus.NOT_VERIFIED
        assert [r.risk_id for r in wide.uncovered_risks] == ["R4-w3-wide"]
        gap = wide.uncovered_risks[0].reason
        assert "historical_narrative" in gap
        assert "does not speak for the whole risk" in gap
        assert not wide.contradictions
        # It is a GAP, so the ordinary answer — generate the missing coverage —
        # is still available and still required.
        assert _coverage_gap_only(wide, _suite(_unrelated_pass()))
        assert base  # the diff this was all read from

    def test_the_forced_drift_case_makes_the_guard_refuse_and_the_risk_fails_closed(
        self, guard_repo: GuardRepo
    ) -> None:
        """The discrimination case is not decoration; drift really turns it red.

        The same guard, on a tree where the live-status block and the registry
        genuinely disagree. It refuses, so it discharges nothing — and the risk
        it measures goes back to uncovered rather than inheriting the green it
        had on the reconciled tree.
        """
        base = guard_repo.head()
        guard_repo.deliver_the_two_guards()
        guard_repo.write(
            "status/CURRENT.md",
            (guard_repo.root / "status" / "CURRENT.md")
            .read_text()
            .replace("active_unit: U-7", "active_unit: U-9"),
        )
        verification = _run_the_changed_guards(guard_repo, base=base, head="drifted")

        refused = next(
            r
            for r in verification.results
            if r.target.path == "guards/test_status_reconciliation.py"
        )
        assert not refused.passed and not refused.infrastructure
        assert verification.product_failures

        covered, gaps = risk_coverage(
            [MEASURED_RISK], _suite(_unrelated_pass()), changed_verification=verification
        )
        assert covered == []
        assert len(gaps) == 1
        assert "REFUSED" in gaps[0].reason


# --------------------------------------------------------------------------
# 2. And nothing weaker counts
# --------------------------------------------------------------------------


class TestWhatDoesNotCount:
    def test_a_builders_report_that_the_guard_passes_verifies_nothing(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        """The evidence the defective run actually had. It is not evidence.

        The tree is identical and the guard would pass; the only difference is
        that nobody ran it. There is no field on any record a self-report could
        reach, which is the point: the ledger reads executed commands.
        """
        base = guard_repo.head()
        guard_repo.deliver_the_two_guards()
        selected = verify_changed_surface(
            guard_repo.root,
            ["guards/test_status_reconciliation.py"],
            base_commit=base,
            commit=guard_repo.head(),
            only=["nothing at all"],
        )
        assert selected.guards and not selected.results
        assert guard_measurements(selected) == []

        covered, gaps = risk_coverage(
            [MEASURED_RISK], _suite(_unrelated_pass()), changed_verification=selected
        )
        assert covered == []
        assert gaps and "no scenario exercising this risk was executed" in gaps[0].reason

    def test_an_unrelated_green_guard_discharges_nothing(
        self, guard_repo: GuardRepo
    ) -> None:
        """A changed, executed, entirely green guard about something else."""
        base = guard_repo.head()
        guard_repo.touch_an_unrelated_test()
        verification = verify_changed_surface(
            guard_repo.root,
            ["guards/test_unrelated_feature.py"],
            base_commit=base,
            commit=guard_repo.head(),
            timeout_s=300,
        )
        assert verification.results and all(r.passed for r in verification.results)

        covered, gaps = risk_coverage(
            [MEASURED_RISK], _suite(_unrelated_pass()), changed_verification=verification
        )
        assert covered == []
        assert gaps

    def test_the_repositorys_own_collateral_guard_discharges_nothing(
        self, guard_repo: GuardRepo
    ) -> None:
        """The status-shape guard reads the same document and is not the deliverable.

        It is selected — correctly, as collateral over a file the diff touched —
        and it passes. A risk it happens to read the subjects of stays uncovered,
        because a guard this change did not deliver is exactly the "some green
        test somewhere" this path refuses.
        """
        base = guard_repo.head()
        guard_repo.deliver_the_two_guards()
        verification = _run_the_changed_guards(guard_repo, base=base)
        assert "guards/test_status_shape.py" in verification.related_paths

        measured = {m.path for m in guard_measurements(verification)}
        assert "guards/test_status_shape.py" not in measured
        assert measured == {
            "guards/test_ships_dark.py",
            "guards/test_status_reconciliation.py",
        }

    def test_a_negative_guard_with_no_anti_vacuity_case_discharges_nothing(
        self, guard_repo: GuardRepo
    ) -> None:
        """Same subjects, same green, no case proving it can still fire.

        This guard asserts that no key disagrees — which is true, and stays true,
        of a block it has stopped parsing. Its green is not a measurement until
        the repository shows it can turn red.
        """
        base = guard_repo.head()
        guard_repo.deliver_the_two_guards()
        guard_repo.write(
            "guards/test_status_reconciliation.py", VACUOUS_RECONCILIATION_GUARD
        )
        verification = _run_the_changed_guards(guard_repo, base=base, head="vacuous")

        passed = next(
            r
            for r in verification.results
            if r.target.path == "guards/test_status_reconciliation.py"
        )
        assert passed.passed
        measurement = next(
            m
            for m in guard_measurements(verification)
            if m.path == "guards/test_status_reconciliation.py"
        )
        assert measurement.negative and not measurement.discrimination
        assert not measurement.discriminating

        covered, gaps = risk_coverage(
            [MEASURED_RISK], _suite(_unrelated_pass()), changed_verification=verification
        )
        assert covered == []
        assert "not yet discriminating" in gaps[0].reason

    def test_a_guard_that_could_not_be_executed_discharges_nothing(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        guard_repo.deliver_the_two_guards()
        guard_repo.write(
            "guards/test_status_reconciliation.py",
            "import a_module_this_environment_does_not_have\n\n"
            + RECONCILIATION_GUARD.split('"""', 2)[-1],
        )
        verification = _run_the_changed_guards(guard_repo, base=base, head="unrunnable")
        assert verification.unexecutable

        covered, gaps = risk_coverage(
            [MEASURED_RISK], _suite(_unrelated_pass()), changed_verification=verification
        )
        assert covered == []
        assert "could not be executed" in gaps[0].reason

    def test_a_risk_that_names_nothing_concrete_is_never_discharged_by_a_guard(
        self, guard_repo: GuardRepo
    ) -> None:
        """A rule that could be satisfied by prose would be a rule about prose."""
        base = guard_repo.head()
        guard_repo.deliver_the_two_guards()
        verification = _run_the_changed_guards(guard_repo, base=base)

        vague = IdentifiedRisk(
            id="R9",
            description="Something important could silently go wrong and nobody would know.",
            risk_category=RiskCategory.CONFLICTING_EVIDENCE,
            severity=Priority.P1,
        )
        assert len(subjects(vague.description)) < MIN_SUBJECTS
        evidence = guard_evidence(vague, guard_measurements(verification))
        assert not evidence.discharges
        assert "too few concrete repository artifacts" in evidence.reason

        covered, gaps = risk_coverage(
            [vague], _suite(_unrelated_pass()), changed_verification=verification
        )
        assert covered == []
        assert gaps

    def test_the_link_is_not_the_guards_own_file_name(self, guard_repo: GuardRepo) -> None:
        """``test_status_reconciliation.py`` is a label, and labels prove nothing.

        The guard's own path is barred from its side of the comparison, and so
        are the names of the tests inside it. What is left is what the assertions
        actually open and read.
        """
        base = guard_repo.head()
        guard_repo.deliver_the_two_guards()
        verification = _run_the_changed_guards(guard_repo, base=base)
        guard = next(
            g for g in verification.guards if g.path == "guards/test_status_reconciliation.py"
        )
        assert "status_reconciliation" not in guard.measured_subjects
        assert not any(s.startswith("test_") for s in guard.measured_subjects)
        assert {"current", "registry", "live_status"} <= set(guard.measured_subjects)

        span = assertion_span(RECONCILIATION_GUARD, guard.changed_expectation_names)
        assert "def test_" not in span
        assert "may not drift from the registry" not in span  # the module docstring


# --------------------------------------------------------------------------
# 3. Two measurements that disagree
# --------------------------------------------------------------------------


class TestContradiction:
    def test_a_passing_guard_and_a_failing_scenario_fail_closed(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        guard_repo.deliver_the_two_guards()
        verification = _run_the_changed_guards(guard_repo, base=base)

        verdict = evaluate_gate(
            _suite(
                _unrelated_pass(),
                _generated("gen_drift", RiskCategory.CONFLICTING_EVIDENCE, Outcome.FAILED),
            ),
            risks=[MEASURED_RISK],
            changed_verification=verification,
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.covered_risks == []
        assert len(verdict.contradictions) == 1
        clash = verdict.contradictions[0].contradiction
        assert "guards/test_status_reconciliation.py" in clash
        assert "gen_drift" in clash
        assert "Exactly one of those two measurements is wrong" in clash
        assert "CONTRADICTORY" in verdict.headline()

    def test_a_refusing_guard_and_a_passing_scenario_fail_closed(
        self, guard_repo: GuardRepo
    ) -> None:
        """The direction that used to be silently resolved the wrong way.

        The scenario alone would have covered this risk. A guard that measures
        the same risk and refuses does not remove that coverage quietly — it
        makes the pair unusable, and says which two records disagree.
        """
        base = guard_repo.head()
        guard_repo.deliver_the_two_guards()
        guard_repo.write(
            "status/CURRENT.md",
            (guard_repo.root / "status" / "CURRENT.md")
            .read_text()
            .replace("active_unit: U-7", "active_unit: U-9"),
        )
        verification = _run_the_changed_guards(guard_repo, base=base, head="drifted")

        passing_scenario = _suite(
            _unrelated_pass(),
            _generated("gen_drift", RiskCategory.CONFLICTING_EVIDENCE, Outcome.PASSED),
        )
        without = evaluate_gate(passing_scenario, risks=[MEASURED_RISK])
        assert [c.basis for c in without.covered_risks] == [BASIS_CATEGORY]

        verdict = evaluate_gate(
            passing_scenario, risks=[MEASURED_RISK], changed_verification=verification
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.covered_risks == []
        assert len(verdict.contradictions) == 1
        assert "REFUSED" in verdict.contradictions[0].contradiction
        # A contradiction is a finding, not an absence: generating a third
        # scenario is not the answer and is not attempted.
        assert not _coverage_gap_only(verdict, passing_scenario)


# --------------------------------------------------------------------------
# 4. A restart does not forget the measurement
# --------------------------------------------------------------------------


class TestResumeKeepsTheEvidence:
    @pytest.mark.asyncio
    async def test_a_resumed_run_reuses_the_direct_evidence_it_already_took(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        """The obligation is persisted, and so is what it measured.

        A resumed run that had to re-derive "which artifacts did that guard
        read" from a tree that has since moved on would be re-deciding coverage
        on different evidence. The subjects are written down with the result, so
        the same records give the same answer — and the risk stays verified
        across the restart without the guard being re-run for a second time.
        """
        config = changed_config(guard_repo, tmp_path, timeout_s=300)
        first, _log, _evaluator, store = await drive(
            config, guard_repo, [accepts(), accepts()]
        )
        assert first.changed_verification is not None

        # Persisted on the RUN — the record a restart actually reads.
        saved = store.load_state()
        assert saved is not None and saved.verification_obligation is not None
        restored = ChangedSurfaceVerification.model_validate(saved.verification_obligation)
        guard = next(
            g for g in restored.guards if g.path == "guards/test_status_reconciliation.py"
        )
        assert {"current", "registry", "live_status"} <= set(guard.measured_subjects)
        assert restored.results and not restored.blocks_claim

        # The same answer, from the persisted record alone.
        from_disk = evaluate_gate(
            _suite(_unrelated_pass()),
            risks=[MEASURED_RISK],
            changed_verification=restored,
        )
        assert from_disk.status is GateStatus.VERIFIED
        assert from_disk.covered_risks[0].measurement == (
            "guards/test_status_reconciliation.py"
        )

        # And through a real resumed loop: the obligation is carried forward,
        # nothing is re-run on the unchanged tree, and the risk stays verified.
        saved.status = RunStatus.RUNNING
        resumed = await run_control_loop(
            config=config,
            scenario=Scenario(name="p6_unrelated_brake"),
            store=store,
            state=saved,
            builder=DeliveringBuilder(guard_repo),
            evaluator=FakeEvaluator([accepts(), accepts()]),
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            emit=lambda _m: None,
        )
        carried = resumed.changed_verification
        assert carried is not None
        carried_guard = next(
            g for g in carried.guards if g.path == "guards/test_status_reconciliation.py"
        )
        assert {"current", "registry", "live_status"} <= set(carried_guard.measured_subjects)

        after = evaluate_gate(
            _suite(_unrelated_pass()),
            risks=[MEASURED_RISK, BROADER_RISK],
            changed_verification=carried,
        )
        assert [c.risk_id for c in after.covered_risks] == ["R4-w3"]
        assert [g.risk_id for g in after.uncovered_risks] == ["R4-w3-wide"]

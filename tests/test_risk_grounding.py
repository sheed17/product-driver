"""A risk is grounded before it blocks, and a structural risk gets a structural measurement.

Run 20260917-063502 named the same ships-dark hypothesis twice, in two wordings
that name no file:

    The rule capability is wired into production (a production importer, a
    channel/adapter/network/timer import) rather than shipping dark ...

and reported both as uncovered, because "the risk names too few concrete
repository artifacts for a direct guard to be checked against it" — while the
repository's own ships-dark guard, changed by that very diff, had run green on
the judged tree with its own positive control.

These tests hold both directions: the exact measurement discharges exactly the
risk it measures, and nothing weaker, vaguer or unrelated does. The repository
is synthetic and lives in a temporary directory; nothing consumes Claude usage.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from neyma_product_driver.changed_verification import (
    IMPORT_REACHABILITY,
    ChangedSurfaceVerification,
    analyse_guard,
    measure_tests,
    structural_measurements,
)
from neyma_product_driver.repo_verification import VerificationResult
from neyma_product_driver.risk_grounding import (
    DARK,
    IMPORT_REACHABLE,
    REACHABILITY,
    UNMEASURED,
    ground,
    hypothesis_property,
    measure_reachability,
)
from neyma_product_driver.scenario_gate import (
    BASIS_CATEGORY,
    BASIS_DRIVER_STRUCTURAL,
    BASIS_STRUCTURAL_GUARD,
    SOURCE_DRIVER_STRUCTURAL,
    SOURCE_STRUCTURAL_GUARD,
    UNGROUNDED,
    GateStatus,
    evaluate_gate,
    risk_coverage,
)
from neyma_product_driver.scenario_plan import IdentifiedRisk, Priority, RiskCategory
from neyma_product_driver.scenario_suite import Origin, Outcome, ScenarioOutcome, SuiteResult

RUNNER = f"{sys.executable} -m pytest"

SHIPS_DARK = (
    "The scheduler capability is wired into production (a production importer, a "
    "channel/adapter/network/timer import) rather than shipping dark, letting it act "
    "before deliberate activation."
)
SHIPS_DARK_REWORDED = (
    "The scheduler capability is wired into production through a channel/adapter/network/timer "
    "import (or reached from outside the package), letting it observe or act on the live "
    "path before a deliberate human activation — violating the ships-dark posture."
)
LEDGER_DARK = (
    "The ledger is wired into production through an adapter import rather than shipping "
    "dark, letting it act before deliberate activation."
)
VAGUE = "Something about the new behaviour could quietly regress for operators."
REMOVED = "The scheduler stops being imported by the live app because the import was removed."

GUARD_WITH_CONTROL = '''
import ast
import pathlib

from pkg.scheduler import answer


def test_the_capability_answers():
    assert answer() == 42


def test_scheduler_ships_dark_no_production_importer():
    """Only the authorized reader may import the capability."""
    src = pathlib.Path(__file__).resolve().parents[1] / "pkg"
    offenders = []
    for py in src.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("scheduler"):
                offenders.append(py.name)
    assert [o for o in offenders if o != "reader.py"] == []
    assert "reader.py" in set(offenders)
'''

#: The same scan with no positive control: an empty scan would pass it too.
GUARD_WITHOUT_CONTROL = GUARD_WITH_CONTROL.replace(
    '    assert "reader.py" in set(offenders)\n', ""
)

#: A changed, passing guard that reads the capability and measures nothing structural.
UNRELATED_GUARD = '''
from pkg.scheduler import answer


def test_capability_answers_forty_two():
    assert answer() == 42
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(
    root: Path,
    *,
    reader_imported_by_app: bool = False,
    dynamic: bool = False,
    guard: str = GUARD_WITH_CONTROL,
    control_test: bool = True,
) -> tuple[Path, str]:
    """A package whose capability is imported only by a reader. Returns (repo, base)."""
    repo = root / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "other.py").write_text("VALUE = 1\n")
    (repo / "pkg" / "app.py").write_text(
        ("from pkg import reader\n" if reader_imported_by_app else "from pkg import other\n")
        + "\nif __name__ == '__main__':\n    print('serving')\n"
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "pkg" / "scheduler.py").write_text("def answer():\n    return 42\n")
    (repo / "pkg" / "ledger.py").write_text("def post():\n    return 'posted'\n")
    (repo / "pkg" / "reader.py").write_text("from pkg.scheduler import answer\n")
    if dynamic:
        (repo / "pkg" / "loader.py").write_text(
            "import importlib\n\nMODULE = importlib.import_module('pkg.ledger')\n"
        )
    if control_test:
        (repo / "tests" / "test_scheduler.py").write_text(guard)
        (repo / "tests" / "test_ledger.py").write_text(
            "from pkg.ledger import post\n\n\ndef test_post():\n    assert post()\n"
        )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the capability")
    return repo, base


def diff_of(repo: Path, base: str) -> list[str]:
    return _git(repo, "diff", "--name-only", base, "HEAD").splitlines()


def risk(rid: str, description: str, category=RiskCategory.REGRESSION, severity=Priority.P1):
    return IdentifiedRisk(
        id=rid, description=description, risk_category=category, severity=severity, basis="test"
    )


def verification(
    repo: Path,
    base: str,
    *,
    passed: bool = True,
    detail: str = "2 passed",
    with_structural: bool = True,
    guard_path: str = "tests/test_scheduler.py",
) -> ChangedSurfaceVerification:
    """The changed-verification record the loop would persist for this tree."""
    head = _git(repo, "rev-parse", "HEAD")
    guard = analyse_guard(repo, guard_path, base_commit=base, runner=RUNNER)
    assert guard is not None
    return ChangedSurfaceVerification(
        changed_paths=[guard_path],
        guards=[guard],
        results=[
            VerificationResult(
                target=guard.target, exit_code=0 if passed else 1, passed=passed, detail=detail
            )
        ],
        commit=head,
        module_reachability=(
            structural_measurements(repo, diff_of(repo, base), commit=head)
            if with_structural
            else []
        ),
    )


def suite(*outcomes: ScenarioOutcome) -> SuiteResult:
    return SuiteResult(
        full_run=True,
        expected_required_ids=[o.scenario_id for o in outcomes],
        outcomes=list(outcomes),
    )


def generated(scenario_id: str, category: RiskCategory, outcome=Outcome.PASSED) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=Origin.GENERATED,
        outcome=outcome,
        priority=Priority.P1,
        risk_category=category.value,
        required=True,
        evidence_path=f"/runs/x/scenarios/{scenario_id}",
        evidence_verified=True,
        failed_assertions=["the capability was imported"] if outcome is Outcome.FAILED else [],
    )


def unrelated_pass() -> ScenarioOutcome:
    return generated("S-other", RiskCategory.IDEMPOTENCY)


# ==========================================================================
# 1 — grounding and the structural hypothesis
# ==========================================================================


class TestGrounding:
    def test_a_vague_ships_dark_risk_is_grounded_in_the_changed_module_it_names(self, tmp_path):
        repo, base = make_repo(tmp_path)
        grounding = ground(SHIPS_DARK, diff_of(repo, base))
        assert grounding.hypothesis == REACHABILITY
        assert grounding.capability_paths == ["pkg/scheduler.py"]
        assert grounding.subjects == ["scheduler"]

    def test_two_wordings_of_one_risk_are_one_obligation(self, tmp_path):
        repo, base = make_repo(tmp_path)
        diff = diff_of(repo, base)
        category = RiskCategory.REGRESSION.value
        assert ground(SHIPS_DARK, diff).key(category) == ground(SHIPS_DARK_REWORDED, diff).key(category)

    def test_two_different_subjects_stay_two_obligations(self, tmp_path):
        repo, base = make_repo(tmp_path)
        diff = diff_of(repo, base)
        category = RiskCategory.REGRESSION.value
        assert ground(SHIPS_DARK, diff).key(category) != ground(LEDGER_DARK, diff).key(category)

    def test_a_different_category_is_a_different_obligation(self, tmp_path):
        repo, base = make_repo(tmp_path)
        grounding = ground(SHIPS_DARK, diff_of(repo, base))
        assert grounding.key("regression") != grounding.key("safety_invariant")

    @pytest.mark.parametrize(
        "text",
        [
            REMOVED,
            "The idempotency key is not wired to the store.",
            VAGUE,
            "The migration could diverge from a fresh build.",
        ],
    )
    def test_only_a_live_path_hypothesis_is_structural(self, text):
        assert hypothesis_property(text) == ""

    def test_the_live_path_hypotheses_are(self):
        assert hypothesis_property(SHIPS_DARK) == REACHABILITY
        assert hypothesis_property(SHIPS_DARK_REWORDED) == REACHABILITY

    def test_a_risk_naming_no_changed_module_is_not_grounded(self, tmp_path):
        repo, base = make_repo(tmp_path)
        grounding = ground(VAGUE, diff_of(repo, base))
        assert not grounding.grounded
        assert grounding.key("regression") is None


# ==========================================================================
# 2 — the driver's structural measurement, and its positive control
# ==========================================================================


class TestReachabilityMeasurement:
    def test_a_capability_whose_only_importer_nothing_imports_is_dark(self, tmp_path):
        repo, base = make_repo(tmp_path)
        (record,) = measure_reachability(repo, ["pkg/scheduler.py"], tree="t")
        assert record.status == DARK
        assert record.product_importers == ["pkg/reader.py"]
        assert "tests/test_scheduler.py" in record.control_importers
        assert "pkg/reader.py" in record.detail

    def test_a_chain_from_an_entry_point_is_reachable(self, tmp_path):
        repo, base = make_repo(tmp_path, reader_imported_by_app=True)
        (record,) = measure_reachability(repo, ["pkg/scheduler.py"], tree="t")
        assert record.status == IMPORT_REACHABLE
        assert "pkg/app.py" in record.detail

    def test_a_dynamic_import_by_name_is_a_route(self, tmp_path):
        repo, base = make_repo(tmp_path, dynamic=True)
        (record,) = measure_reachability(repo, ["pkg/ledger.py"], tree="t")
        assert record.product_importers == ["pkg/loader.py"]
        assert record.status == DARK  # the loader itself is imported by nothing

    def test_no_positive_control_means_no_claim(self, tmp_path):
        repo, base = make_repo(tmp_path, control_test=False)
        (record,) = measure_reachability(repo, ["pkg/ledger.py"], tree="t")
        assert record.product_importers == []
        assert record.status == UNMEASURED
        assert "cannot be told from a resolver" in record.detail

    def test_the_resolver_positive_control_can_fire(self, tmp_path):
        """If the resolver could not see imports at all, nothing would be DARK."""
        repo, base = make_repo(tmp_path)
        (record,) = measure_reachability(repo, ["pkg/scheduler.py"], tree="t")
        assert record.control_importers, "the positive control must be non-empty"
        # And the same resolver does see a product import when there is one.
        (reachable,) = measure_reachability(repo, ["pkg/other.py"], tree="t")
        assert reachable.product_importers == ["pkg/app.py"]
        assert reachable.status == IMPORT_REACHABLE


# ==========================================================================
# 3 — per-test structural facts about a changed guard
# ==========================================================================


class TestGuardTestMeasures:
    def test_the_ships_dark_test_measures_imports_with_its_own_control(self):
        measures = {m.name: m for m in measure_tests(GUARD_WITH_CONTROL, [
            "test_the_capability_answers", "test_scheduler_ships_dark_no_production_importer",
        ])}
        dark = measures["test_scheduler_ships_dark_no_production_importer"]
        assert dark.properties == [IMPORT_REACHABILITY]
        assert dark.positive_control
        assert measures["test_the_capability_answers"].properties == []

    def test_without_the_control_the_scan_is_not_controlled(self):
        (dark,) = measure_tests(GUARD_WITHOUT_CONTROL, ["test_scheduler_ships_dark_no_production_importer"])
        assert dark.properties == [IMPORT_REACHABILITY]
        assert not dark.positive_control

    def test_the_docstring_is_not_a_subject(self):
        (dark,) = measure_tests(GUARD_WITH_CONTROL, ["test_scheduler_ships_dark_no_production_importer"])
        assert "authorized" not in dark.subjects


# ==========================================================================
# 4 — the ledger
# ==========================================================================


class TestTheLedger:
    def test_the_repository_guard_discharges_both_wordings_as_one_obligation(self, tmp_path):
        repo, base = make_repo(tmp_path)
        record = verification(repo, base)
        risks = [risk("R8", SHIPS_DARK), risk("R8-dark", SHIPS_DARK_REWORDED)]
        covered, gaps = risk_coverage(risks, suite(unrelated_pass()), changed_verification=record)
        assert gaps == []
        assert len(covered) == 1
        entry = covered[0]
        assert entry.duplicates == ["R8-dark"]
        assert entry.basis == BASIS_STRUCTURAL_GUARD
        assert entry.evidence_source == SOURCE_STRUCTURAL_GUARD
        assert entry.measurement == (
            "tests/test_scheduler.py::test_scheduler_ships_dark_no_production_importer"
        )
        assert entry.evidence_tree == record.commit
        assert "one obligation with R8-dark" in entry.brief()
        verdict = evaluate_gate(
            suite(unrelated_pass()), risks=risks, changed_verification=record
        )
        assert verdict.status is GateStatus.VERIFIED

    def test_an_unrelated_green_guard_discharges_nothing(self, tmp_path):
        # Reachable from the entry point, so the driver's own scan cannot answer
        # either: only the guard could, and it measures nothing structural.
        repo, base = make_repo(tmp_path, guard=UNRELATED_GUARD, reader_imported_by_app=True)
        record = verification(repo, base)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert covered == []
        assert len(gaps) == 1
        assert "no changed guard with a positive-controlled import_reachability test" in gaps[0].reason

    def test_a_scan_with_no_positive_control_discharges_nothing(self, tmp_path):
        repo, base = make_repo(
            tmp_path, guard=GUARD_WITHOUT_CONTROL, reader_imported_by_app=True
        )
        record = verification(repo, base)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert covered == []
        assert "carries no positive control" in gaps[0].reason

    def test_the_driver_measurement_discharges_when_no_guard_exists(self, tmp_path):
        repo, base = make_repo(tmp_path, guard=UNRELATED_GUARD)
        record = verification(repo, base)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert gaps == []
        entry = covered[0]
        assert entry.basis == BASIS_DRIVER_STRUCTURAL
        assert entry.evidence_source == SOURCE_DRIVER_STRUCTURAL
        assert entry.measurement == "pkg/scheduler.py"
        assert "DARK" in entry.claim and "positive control" in entry.claim
        assert entry.evidence_tree == record.commit

    def test_an_import_reachable_capability_is_neither_covered_nor_refuted(self, tmp_path):
        repo, base = make_repo(tmp_path, reader_imported_by_app=True, guard=UNRELATED_GUARD)
        record = verification(repo, base)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert covered == []
        assert gaps[0].contradiction == ""
        assert "REACHABLE" in gaps[0].reason and "neither discharges nor refutes" in gaps[0].reason

    def test_two_different_subjects_remain_two_risks(self, tmp_path):
        repo, base = make_repo(tmp_path, reader_imported_by_app=True, guard=UNRELATED_GUARD)
        record = verification(repo, base)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK), risk("R9", LEDGER_DARK)],
            suite(unrelated_pass()),
            changed_verification=record,
        )
        # The capability is reachable; the ledger is dark. Two answers.
        assert [g.risk_id for g in gaps] == ["R8"]
        assert [c.risk_id for c in covered] == ["R9"]
        assert not gaps[0].duplicates and not covered[0].duplicates

    def test_missing_measurement_still_blocks_exactly_as_before(self, tmp_path):
        """A record taken before grounding existed changes no answer."""
        repo, base = make_repo(tmp_path, guard=UNRELATED_GUARD)
        record = verification(repo, base, with_structural=False)
        record.guards[0].test_measures = []
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK), risk("R8-dark", SHIPS_DARK_REWORDED)],
            suite(unrelated_pass()),
            changed_verification=record,
        )
        assert covered == []
        assert [g.risk_id for g in gaps] == ["R8", "R8-dark"]
        assert all("too few concrete repository artifacts" in g.reason for g in gaps)

    def test_a_vague_risk_is_stated_ungrounded_and_keeps_blocking(self, tmp_path):
        repo, base = make_repo(tmp_path)
        record = verification(repo, base)
        covered, gaps = risk_coverage(
            [risk("R-vague", VAGUE)], suite(unrelated_pass()), changed_verification=record
        )
        assert covered == []
        assert UNGROUNDED in gaps[0].reason

    def test_a_structural_test_that_failed_is_a_refutation(self, tmp_path):
        repo, base = make_repo(tmp_path, reader_imported_by_app=True)
        record = verification(
            repo,
            base,
            passed=False,
            detail="FAILED tests/test_scheduler.py::test_scheduler_ships_dark_no_production_importer",
        )
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert covered == []
        assert "REFUSED on this tree" in gaps[0].reason

    def test_another_test_failing_in_the_same_file_is_not_this_measurement(self, tmp_path):
        repo, base = make_repo(tmp_path)
        record = verification(
            repo, base, passed=False,
            detail="FAILED tests/test_scheduler.py::test_the_capability_answers",
        )
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        # The file failed, so its green cannot be read — but that is no refutation,
        # and the driver's own scan still measures the risk.
        assert "REFUSED on this tree" not in (gaps[0].reason if gaps else "")
        assert [c.basis for c in covered] == [BASIS_DRIVER_STRUCTURAL]

    def test_a_structural_pass_and_a_failed_scenario_are_a_contradiction(self, tmp_path):
        repo, base = make_repo(tmp_path)
        record = verification(repo, base)
        failed = generated("S-dark", RiskCategory.REGRESSION, Outcome.FAILED)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(failed), changed_verification=record
        )
        assert covered == []
        assert gaps[0].contradiction
        assert "Exactly one of those two measurements is wrong" in gaps[0].contradiction

    def test_scenario_evidence_still_comes_first(self, tmp_path):
        repo, base = make_repo(tmp_path)
        record = verification(repo, base)
        passed = generated("S-dark", RiskCategory.REGRESSION)
        covered, _gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(passed), changed_verification=record
        )
        assert covered[0].basis == BASIS_CATEGORY
        assert covered[0].scenario_id == "S-dark"

    def test_non_blocking_risks_are_still_ignored(self, tmp_path):
        repo, base = make_repo(tmp_path)
        record = verification(repo, base)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK, severity=Priority.P2)],
            suite(unrelated_pass()),
            changed_verification=record,
        )
        assert covered == [] and gaps == []


# ==========================================================================
# 5 — a coverage-gap scenario for a registered regression risk is in scope
# ==========================================================================


class TestRegressionScope:
    def _scenario(self, stage: str, source_risks):
        from neyma_product_driver.scenario_generator import parse_scenarios
        from neyma_product_driver.scenario_plan import ScenarioProvenance

        from scenario_fixtures import raw_payload, raw_scenario

        raw = raw_scenario(
            "S-dark", risk_category="regression", source_risks=list(source_risks)
        )
        provenance = ScenarioProvenance(
            task_hash="t", stage=stage, wave=2, model="opus", session_id="s",
            generating_risk="the capability ships live",
        )
        parsed, malformed = parse_scenarios(raw_payload(raw), provenance=provenance)
        assert not malformed, malformed
        return parsed[0]

    def _reasons(self, scenario):
        from neyma_product_driver.scenario_validation import validate_scenario

        from scenario_fixtures import validation_context

        return validate_scenario(scenario, validation_context(known_risk_ids={"R8"}))

    def test_a_gap_case_citing_a_registered_risk_is_in_scope(self):
        reasons = self._reasons(self._scenario("coverage_gap", ["R8"]))
        assert not any("regression scenario must name the diff" in r for r in reasons), reasons

    def test_an_uncited_regression_case_is_still_out_of_scope(self):
        reasons = self._reasons(self._scenario("initial", []))
        assert any("regression scenario must name the diff" in r for r in reasons), reasons


# ==========================================================================
# 6 — the evaluator is shown the gaps the gate will enforce, not more
# ==========================================================================


def test_the_evaluator_brief_uses_the_same_evidence_as_the_gate(tmp_path):
    from types import SimpleNamespace

    from neyma_product_driver.cli import _coverage_gap_briefs

    repo, base = make_repo(tmp_path)
    record = verification(repo, base)
    planner = SimpleNamespace(
        plan=SimpleNamespace(risks=[risk("R8", SHIPS_DARK), risk("R8-dark", SHIPS_DARK_REWORDED)])
    )
    assert _coverage_gap_briefs(planner, suite(unrelated_pass()), changed_verification=record) == []
    # Without the record the gate would see two gaps, and so does the brief.
    assert len(_coverage_gap_briefs(planner, suite(unrelated_pass()))) == 2

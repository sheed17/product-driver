"""A held harness oracle must be replaceable, executable, and dischargeable — and nothing less.

Run 20260925-043237 is the case this file exists for. A coverage-gap wave
admitted two REGRESSION scenarios that scanned a dark machine's importers and
expected none. The repository's own guard later fixed that importer set to
exactly one authorized admission layer, so Product Driver held both as
harness-oracle defects — correctly. The lifecycle then stalled:

* the re-derivation wave proposed a replacement that read the boundary from
  the repository's own authority, and validation refused it with "a regression
  scenario must name the diff or prior evidence that puts the behaviour it
  guards inside this task's scope". The held scenario had been in scope by the
  registered risk it cited from its coverage-gap wave; a re-derivation is not a
  coverage-gap wave, and the run had no diff, so no replacement for a held
  regression scenario could EVER be admitted;
* the brief told the generator every hold was a pre-contract typed refusal,
  and offered it every same-category risk key rather than the one the held
  scenario actually verified;
* each failed attempt was charged against a two-attempt budget, so a run held
  under that defective contract would strand once the budget was spent.

Nothing here names the product that exposed the defect. The fixture is a tiny
real product in a real git repository with its own import guard, reused from
tests/test_import_boundary_authority.py, and every probe that is claimed to
pass or fail is executed through the real executor.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
from neyma_product_driver.scenario_plan import GeneratedScenarioPlan
from neyma_product_driver.scenario_planner import (
    MAX_REDERIVE_ATTEMPTS,
    PLAN_FILENAME,
    REDERIVE_CONTRACT_VERSION,
    ScenarioPlanner,
)
from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
from neyma_product_driver.outcome_contract import RepositoryText
from neyma_product_driver.scenario_validation import ApprovedCommands, validate_scenario
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

from scenario_fixtures import FakeFounder, FakeUnit, ScriptedReasoner, raw_payload, validation_context
from test_import_boundary_authority import (
    AUTHORIZED_GUARD,
    COMMANDS,
    UNAUTHORIZED,
    ZERO,
    _git,
    as_model,
    probe,
    product_repo,
    scenario,
    zero_shape,
)

#: Every file in the tree outside the product package that reaches the machine —
#: the shape run 20260925-043237's re-derived replacements carried. Excluded by
#: a path substring, and printed through a re-rendering of the scan.
OUTSIDE = probe(
    "outside=[q for q in sorted(pathlib.Path('.').rglob('*.py')) if 'product' not in str(q) "
    "and 'ledger' in own(ast.parse(q.read_text()))]; "
    "print('files outside the package that reach ledger:', sorted(str(q) for q in outside))",
    root=".",
)
#: The same, with the repository's tests excluded as well.
OUTSIDE_NO_TESTS = probe(
    "outside=[q for q in sorted(pathlib.Path('.').rglob('*.py')) if 'product' not in str(q) "
    "and 'tests' not in str(q) and 'ledger' in own(ast.parse(q.read_text()))]; "
    "print('non-test files outside the package that reach ledger:', sorted(str(q) for q in outside))",
    root=".",
)
#: The repository's own behavioural test of the machine: it imports it.
BEHAVIOUR_TEST = "from product.ledger import Ledger\n\n\ndef test_a_ledger_exists():\n    assert Ledger()\n"

TASK = "prove the ledger still ships dark after the remediation"
HELD = "ships-dark"

#: The run's registered regression risk — the obligation the held scenario verifies.
RISK = {
    "id": "R3",
    "description": "the remediation could wire the dark ledger into a live path",
    "risk_category": "regression",
    "severity": "P1",
}


def harness() -> Scenario:
    return Scenario(
        name="ledger_base",
        mode="backend",
        expect_state=[
            {"name": f"probe {i}", "command": command, "contains": []}
            for i, command in enumerate([*COMMANDS, OUTSIDE, OUTSIDE_NO_TESTS])
        ],
    )


def reasons(repo: Path, raw: dict) -> list[str]:
    context = validation_context(
        approved_commands=ApprovedCommands.from_sources(scenarios=[harness()]),
        established_observations={},
        declared_services=set(),
        repository_text=RepositoryText(repo),
    )
    return validate_scenario(as_model(raw), context)


def with_behaviour_test(repo: Path) -> Path:
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_ledger_behaviour.py").write_text(BEHAVIOUR_TEST)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the repository tests its ledger")
    return repo


def _planner(tmp_path: Path, repo: Path, payloads, *, max_waves: int = 3) -> ScenarioPlanner:
    return ScenarioPlanner(
        repo=repo,
        config=ScenarioGenerationConfig(enabled=True, max_waves=max_waves),
        reasoner=ScriptedReasoner(list(payloads)),
        store=EvidenceStore(tmp_path / "runs", "r1"),
        base_scenario=harness(),
        permanent_scenarios=[harness()],
        founder=FakeFounder(),
        contract_probe=lambda _c: pytest.fail("no contract probe is needed here"),
    )


def _regression(raw: dict, **kw) -> dict:
    raw.update(risk_category="regression", priority="P1", **kw)
    return raw


def held_shape() -> dict:
    """What the run stored: a coverage-gap regression case expecting zero importers."""
    return _regression(zero_shape(HELD), source_risks=["R3"])


def replacement(command: str = UNAUTHORIZED, expected: str = "unauthorized importers of ledger: []", **kw) -> dict:
    """A lawful re-derivation: the same obligation, its oracle read from the repository's guard."""
    raw = scenario("ships-dark-rederived", command, [expected])
    return _regression(raw, replaces=[HELD], **kw)


def held_run(tmp_path: Path, *, guards=("authorized",)) -> Path:
    """Build the persisted run: admitted with no boundary, held once the boundary is committed.

    No diff, no failure — exactly the state the real resume was in.
    """
    repo = product_repo(tmp_path, guards=())
    planner = _planner(
        tmp_path,
        repo,
        [raw_payload(risks=[RISK]), raw_payload(held_shape(), risks=[])],
    )
    planner.plan_initial(task=TASK, unit=FakeUnit(), run_id="r1")
    planner.expand_after_failures(task=TASK, unit=FakeUnit(), failures=[])
    admitted = planner.plan.by_id(HELD)
    assert admitted is not None and admitted.provenance.stage == "coverage_gap", planner.plan.waves
    assert admitted.provenance.diff_files_consulted == []
    bodies = {"authorized": AUTHORIZED_GUARD}
    (repo / "tests").mkdir()
    for guard in guards:
        (repo / "tests" / f"test_{guard}_boundary.py").write_text(bodies[guard])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the admission layer is the authorized importer")
    return repo


def resumed(tmp_path: Path, repo: Path, payloads=(), **kw) -> ScenarioPlanner:
    planner = _planner(tmp_path, repo, list(payloads), **kw)
    planner.restore_from_store()
    return planner


def rejected_reasons(planner: ScenarioPlanner) -> list[str]:
    return [r for w in planner.plan.waves if w.stage == "rederivation" for x in w.rejected for r in x.reasons]


def run_suite(planner: ScenarioPlanner, repo: Path, iteration: int = 5):
    suite = build_suite(
        generated=[(planner.plan.by_id(i), c) for i, c in planner.compiled.items()],
        unbuildable=planner.unbuildable_scenarios,
    )
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(
            repo=repo, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=d
        ),
        artifact_root=planner.store.iteration_dir(iteration),
        run_id="r1",
        iteration=iteration,
        repo=repo,
    )
    return asyncio.run(executor.run(suite))


def persisted(planner: ScenarioPlanner) -> GeneratedScenarioPlan:
    return GeneratedScenarioPlan.model_validate_json(
        (planner.store.run_dir / PLAN_FILENAME).read_text()
    )


# ==========================================================================
# A — the importer-boundary contradiction is a hold, never a product failure
# ==========================================================================


def test_a_nonempty_authorized_importer_set_holds_the_zero_oracle(tmp_path):
    repo = held_run(tmp_path)
    planner = resumed(tmp_path, repo)
    assert planner.held_scenario_ids == [HELD]
    assert HELD not in planner.compiled
    hold = planner.plan.by_id(HELD).contract_hold
    assert "authorizes exactly" in hold and "not a product failure" in hold
    # It blocks through the channel a replacement clears, as a harness defect.
    assert HELD in planner.unbuildable_scenarios
    assert "harness-oracle defect, not a product failure" in planner.unbuildable_scenarios[HELD]
    result = run_suite(planner, repo)
    verdict = evaluate_gate(result)
    assert verdict.status is not GateStatus.VERIFIED
    assert not [o for o in result.outcomes if o.scenario_id == HELD], "a hold is never executed"
    assert not (repo / "probe-ran").exists()


# ==========================================================================
# B, C, D — a lawful replacement for the SAME obligation is admitted
# ==========================================================================


class TestALawfulReplacementIsAdmitted:
    def test_the_real_shape_is_admitted_for_a_regression_hold_with_no_diff(self, tmp_path):
        """RED before the fix: refused as out of scope, although it replaces an in-scope hold."""
        repo = held_run(tmp_path)
        planner = resumed(tmp_path, repo, [raw_payload(replacement(), risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert rejected_reasons(planner) == []
        assert "ships-dark-rederived" in planner.compiled
        assert planner.held_scenario_ids == []
        admitted = planner.plan.by_id("ships-dark-rederived")
        # C — it names what it replaces, and carries the held obligation unweakened.
        assert admitted.replaces == [HELD]
        assert admitted.risk_category.value == "regression"
        assert admitted.priority.value == "P1"
        assert admitted.provenance.stage == "rederivation"
        # Lineage is inherited from the hold it re-derives, not from a new claim.
        held = planner.plan.by_id(HELD)
        assert set(held.provenance.source_risks) <= set(admitted.provenance.source_risks)
        assert "replaced by ships-dark-rederived" in held.retired_reason

    def test_the_exact_authorized_set_is_also_a_lawful_replacement(self, tmp_path):
        """J — the authorized importer must be present; zero is not demanded."""
        repo = held_run(tmp_path)
        exact = replacement(ZERO, "production importers of ledger: ['ledger_admission.py']")
        planner = resumed(tmp_path, repo, [raw_payload(exact, risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert "ships-dark-rederived" in planner.compiled, rejected_reasons(planner)

    def test_the_brief_describes_the_hold_it_is_asked_to_repair(self, tmp_path):
        repo = held_run(tmp_path)
        planner = resumed(tmp_path, repo, [raw_payload(risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        brief = planner.reasoner.briefs[-1]
        rendered = brief.render()
        risk_key = next(r.key for r in planner.plan.risks if r.id == "R3")
        line = next(h for h in brief.held_scenarios if h.startswith(HELD))
        # The held scenario's OWN obligation, not every same-category key.
        assert risk_key in line
        assert "authorizes exactly" in line
        # And the brief no longer claims every hold was a typed refusal.
        assert "repository-established" in rendered or "repository's own guard" in rendered
        assert "Each was generated before the outcome contract existed" not in rendered

    def test_a_proposal_alone_does_not_retire_the_hold(self, tmp_path):
        """D — proposed but refused (here: weakened) leaves the hold in place."""
        repo = held_run(tmp_path)
        weaker = replacement()
        weaker["priority"] = "P2"
        planner = resumed(tmp_path, repo, [raw_payload(weaker, risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert planner.held_scenario_ids == [HELD]
        assert planner.plan.by_id(HELD).retired_reason == ""
        assert any("may not weaken the obligation" in r for r in rejected_reasons(planner))


# ==========================================================================
# E, H, I — refused or unknown authority never manufactures a replacement
# ==========================================================================


class TestAnUnlawfulReplacementLeavesTheHoldBlocking:
    def test_restating_the_zero_oracle_is_refused(self, tmp_path):
        repo = held_run(tmp_path)
        again = _regression(zero_shape("ships-dark-again"), replaces=[HELD])
        planner = resumed(tmp_path, repo, [raw_payload(again, risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert planner.held_scenario_ids == [HELD]
        assert "ships-dark-again" not in planner.compiled
        assert any("authorizes exactly" in r for r in rejected_reasons(planner))
        assert HELD in planner.unbuildable_scenarios

    def test_a_different_risk_category_is_refused(self, tmp_path):
        repo = held_run(tmp_path)
        other = replacement()
        other["risk_category"] = "authorization"
        planner = resumed(tmp_path, repo, [raw_payload(other, risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert planner.held_scenario_ids == [HELD]

    def test_replacing_something_that_is_not_held_earns_no_scope(self, tmp_path):
        """The scope a replacement inherits comes only from a real hold."""
        repo = held_run(tmp_path)
        stray = replacement()
        stray["replaces"] = ["not-a-held-scenario"]
        planner = resumed(tmp_path, repo, [raw_payload(stray, risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        reasons = rejected_reasons(planner)
        assert planner.held_scenario_ids == [HELD]
        assert any("only a held scenario may be replaced" in r for r in reasons)
        assert any("must name the diff or prior evidence" in r for r in reasons)

    def test_an_unscoped_regression_outside_rederivation_is_still_refused(self, tmp_path):
        """The scope rule is not loosened for anything but a lawful replacement."""
        repo = product_repo(tmp_path)
        loose = _regression(scenario("loose", UNAUTHORIZED, ["unauthorized importers of ledger: []"]))
        planner = _planner(tmp_path, repo, [raw_payload(loose, risks=[RISK])])
        planner.plan_initial(task=TASK, unit=FakeUnit(), run_id="r1")
        assert planner.plan.scenarios == []
        reasons = [r for w in planner.plan.waves for x in w.rejected for r in x.reasons]
        assert any("must name the diff or prior evidence" in r for r in reasons)

    def test_guards_that_disagree_hold_nothing_and_admit_no_zero_rewrite(self, tmp_path):
        """H — ambiguous authority establishes no boundary, so nothing is held or rewritten."""
        repo = product_repo(tmp_path, guards=("authorized", "zero"))
        planner = _planner(
            tmp_path, repo, [raw_payload(risks=[RISK]), raw_payload(held_shape(), risks=[])]
        )
        planner.plan_initial(task=TASK, unit=FakeUnit(), run_id="r1")
        planner.expand_after_failures(task=TASK, unit=FakeUnit(), failures=[])
        again = resumed(tmp_path, repo)
        assert again.held_scenario_ids == []
        # The expectation stands as written and is judged by execution: it fails
        # the wired product rather than being reinterpreted into a pass.
        assert HELD in again.compiled
        result = run_suite(again, repo)
        assert evaluate_gate(result).status is not GateStatus.VERIFIED

    def test_genuine_zero_authority_keeps_the_zero_oracle(self, tmp_path):
        """I — where the repository requires no importer, zero is the lawful oracle."""
        repo = product_repo(tmp_path, guards=("zero",), zero_wired=True)
        planner = _planner(
            tmp_path, repo, [raw_payload(risks=[RISK]), raw_payload(held_shape(), risks=[])]
        )
        planner.plan_initial(task=TASK, unit=FakeUnit(), run_id="r1")
        planner.expand_after_failures(task=TASK, unit=FakeUnit(), failures=[])
        again = resumed(tmp_path, repo)
        assert again.held_scenario_ids == []
        result = run_suite(again, repo)
        outcome = next(o for o in result.outcomes if o.scenario_id == HELD)
        assert outcome.outcome.value == "PASSED", outcome


# ==========================================================================
# F, G — only execution discharges the obligation
# ==========================================================================


class TestOnlyAPassingReplacementDischargesTheHold:
    def _admitted(self, tmp_path, repo):
        planner = resumed(tmp_path, repo, [raw_payload(replacement(), risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert "ships-dark-rederived" in planner.compiled, rejected_reasons(planner)
        return planner

    def test_a_passing_replacement_discharges_it(self, tmp_path):
        repo = held_run(tmp_path)
        planner = self._admitted(tmp_path, repo)
        result = run_suite(planner, repo)
        outcome = next(o for o in result.outcomes if o.scenario_id == "ships-dark-rederived")
        assert outcome.outcome.value == "PASSED", outcome
        assert result.assembly_problems == []
        assert evaluate_gate(result).status is GateStatus.VERIFIED
        # Real evidence was written for the replacement.
        evidence = planner.store.iteration_dir(5) / "scenarios" / "ships-dark-rederived" / "result.json"
        assert json.loads(evidence.read_text())["scenario_id"] == "ships-dark-rederived"

    def test_a_failing_replacement_cannot_be_accepted(self, tmp_path):
        repo = held_run(tmp_path)
        planner = self._admitted(tmp_path, repo)
        rogue = product_repo(tmp_path, guards=("authorized",), rogue=True)
        result = run_suite(planner, rogue)
        outcome = next(o for o in result.outcomes if o.scenario_id == "ships-dark-rederived")
        assert outcome.outcome.value == "FAILED"
        assert evaluate_gate(result).status is not GateStatus.VERIFIED

    def test_a_replacement_is_required_whatever_the_held_priority(self, tmp_path):
        """A hold blocks at any priority, so its replacement must pass at any priority."""
        repo = held_run(tmp_path)
        planner = self._admitted(tmp_path, repo)
        model = planner.plan.by_id("ships-dark-rederived")
        low = model.model_copy(update={"priority": type(model.priority)("P2")})
        suite = build_suite(generated=[(low, planner.compiled["ships-dark-rederived"])])
        assert suite.entries[0].required

    def test_an_admitted_replacement_that_never_runs_blocks(self, tmp_path):
        repo = held_run(tmp_path)
        planner = self._admitted(tmp_path, repo)
        result = run_suite(planner, repo)
        unrun = result.model_copy(
            update={"outcomes": [o for o in result.outcomes if o.scenario_id != "ships-dark-rederived"]}
        )
        assert evaluate_gate(unrun).status is not GateStatus.VERIFIED


# ==========================================================================
# K, L — attempts spent under an obsolete contract; bounded either way
# ==========================================================================


class TestAttemptsAreChargedPerContract:
    def _spend_under_the_old_contract(self, tmp_path, repo, attempts: int):
        """What the real run persisted: attempts spent, no contract version recorded."""
        plan_path = tmp_path / "runs" / "r1" / PLAN_FILENAME
        # Materialize the hold first, as the real resume did.
        resumed(tmp_path, repo)
        data = json.loads(plan_path.read_text())
        for s in data["scenarios"]:
            if s["id"] == HELD:
                s["rederive_attempts"] = attempts
                s.pop("rederive_contract", None)
        plan_path.write_text(json.dumps(data))

    def test_a_hold_spent_under_the_obsolete_contract_is_re_attempted(self, tmp_path):
        """K — RED before the fix: stranded forever by harness bookkeeping."""
        repo = held_run(tmp_path)
        self._spend_under_the_old_contract(tmp_path, repo, MAX_REDERIVE_ATTEMPTS)
        planner = resumed(tmp_path, repo, [raw_payload(replacement(), risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert "ships-dark-rederived" in planner.compiled, rejected_reasons(planner)
        held = persisted(planner).by_id(HELD)
        assert held.rederive_contract == REDERIVE_CONTRACT_VERSION
        assert held.rederive_attempts == 1
        # The migration is recorded, not silent.
        notes = [n for w in persisted(planner).waves for n in w.budget_notes]
        assert any("obsolete re-derivation contract" in n for n in notes), notes

    def test_the_migration_is_one_time_and_the_budget_stays_bounded(self, tmp_path):
        """L — N resumes of a generator that never answers spend exactly the current budget."""
        repo = held_run(tmp_path)
        self._spend_under_the_old_contract(tmp_path, repo, MAX_REDERIVE_ATTEMPTS)
        for _ in range(MAX_REDERIVE_ATTEMPTS + 3):
            planner = resumed(tmp_path, repo, [raw_payload(risks=[])] * 4)
            planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        plan = persisted(planner)
        held = plan.by_id(HELD)
        assert held.contract_hold and not held.retired_reason
        assert held.rederive_attempts == MAX_REDERIVE_ATTEMPTS
        assert held.rederive_contract == REDERIVE_CONTRACT_VERSION
        waves = [w for w in plan.waves if w.stage == "rederivation"]
        assert len(waves) == MAX_REDERIVE_ATTEMPTS
        # And it still blocks.
        assert HELD in planner.unbuildable_scenarios

    def test_attempts_spent_under_the_current_contract_are_not_refunded(self, tmp_path):
        repo = held_run(tmp_path)
        for _ in range(MAX_REDERIVE_ATTEMPTS):
            planner = resumed(tmp_path, repo, [raw_payload(risks=[])])
            planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        planner = resumed(tmp_path, repo, [raw_payload(replacement(), risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert planner.held_scenario_ids == [HELD]
        assert "ships-dark-rederived" not in planner.compiled
        assert planner.reasoner.briefs == []

    def test_a_replacement_carries_the_attempts_already_spent_on_its_obligation(self, tmp_path):
        """L — were the replacement itself held later, its budget would not start again."""
        repo = held_run(tmp_path)
        planner = resumed(tmp_path, repo, [raw_payload(replacement(), risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        admitted = persisted(planner).by_id("ships-dark-rederived")
        assert admitted.rederive_attempts == 1
        assert admitted.rederive_contract == REDERIVE_CONTRACT_VERSION


# ==========================================================================
# N — the shape the re-derived replacements carried: the repository's own
#     tests inside a "nothing reaches it" population
# ==========================================================================


class TestTheRepositorysOwnVerificationIsNotAnEnablementPath:
    def test_demanding_no_file_reach_a_tested_module_is_refused(self, tmp_path):
        repo = with_behaviour_test(product_repo(tmp_path))
        raw = scenario("S", OUTSIDE, ["files outside the package that reach ledger: []"])
        found = [r for r in reasons(repo, raw) if "own tests" in r]
        assert len(found) == 1, reasons(repo, raw)
        assert "tests/test_ledger_behaviour.py" in found[0]
        assert "not a product failure" in found[0]
        assert "excluding paths containing ['product']" in found[0]
        assert not (repo / "probe-ran").exists()

    def test_why_it_must_be_refused_it_fails_the_correct_product(self, tmp_path):
        from test_import_boundary_authority import execute

        repo = with_behaviour_test(product_repo(tmp_path))
        raw = scenario("S", OUTSIDE, ["files outside the package that reach ledger: []"])
        result = execute(repo, raw, tmp_path / "a")
        assert not result.passed
        assert "tests/test_ledger_behaviour.py" in result.commands[0].stdout

    def test_a_population_that_excludes_the_tests_is_lawful_and_discriminates(self, tmp_path):
        from test_import_boundary_authority import execute

        repo = with_behaviour_test(product_repo(tmp_path))
        raw = scenario("S", OUTSIDE_NO_TESTS, ["non-test files outside the package that reach ledger: []"])
        assert reasons(repo, raw) == []
        assert execute(repo, raw, tmp_path / "ok").passed
        (repo / "tools").mkdir()
        (repo / "tools" / "enable.py").write_text("from product.ledger import Ledger\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "a tool outside the package enables the ledger")
        assert not execute(repo, raw, tmp_path / "live").passed

    def test_with_no_test_importing_it_the_expectation_stands(self, tmp_path):
        """Fail closed: nothing is reinterpreted where the repository shows no verification."""
        repo = product_repo(tmp_path)
        raw = scenario("S", OUTSIDE, ["files outside the package that reach ledger: []"])
        assert [r for r in reasons(repo, raw) if "own tests" in r] == []

    def test_the_authorized_importer_excluded_by_path_is_not_claimed_inside(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario("S", OUTSIDE, ["files outside the package that reach ledger: []"])
        assert [r for r in reasons(repo, raw) if "authorizes exactly" in r] == []

    def test_a_replacement_carrying_it_is_refused_and_the_hold_stays(self, tmp_path):
        repo = with_behaviour_test(held_run(tmp_path))
        bad = replacement(OUTSIDE, "files outside the package that reach ledger: []")
        planner = resumed(tmp_path, repo, [raw_payload(bad, risks=[])])
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert planner.held_scenario_ids == [HELD]
        assert "ships-dark-rederived" not in planner.compiled
        assert any("own tests" in r for r in rejected_reasons(planner))

    def test_executed_before_this_rule_it_is_held_not_a_product_failure(self, tmp_path):
        """Admitted with no test in the tree; the test is committed; it fails, and is HELD."""
        repo = held_run(tmp_path)
        planner = resumed(
            tmp_path, repo, [raw_payload(replacement(OUTSIDE, "files outside the package that reach ledger: []"), risks=[])]
        )
        planner.rederive_held(task=TASK, unit=FakeUnit(), diff_files=[])
        assert "ships-dark-rederived" in planner.compiled
        with_behaviour_test(repo)
        suite = build_suite(
            generated=[(planner.plan.by_id(i), c) for i, c in planner.compiled.items()]
        )
        executor = SuiteExecutor(
            make_executor=lambda d: ScenarioExecutor(
                repo=repo, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=d
            ),
            artifact_root=planner.store.iteration_dir(6),
            run_id="r1",
            iteration=6,
            repo=repo,
        )
        result = asyncio.run(executor.run(suite))
        assert result.outcomes[0].outcome.value == "FAILED"
        held = planner.hold_undeclared_refusals(result.outcomes, executor.results)
        assert held == ["ships-dark-rederived"]
        assert "own tests" in planner.plan.by_id("ships-dark-rederived").contract_hold
        # Its budget is the obligation's: one attempt was already spent on it.
        assert planner.plan.by_id("ships-dark-rederived").rederive_attempts == 1

"""A generated scenario's expected outcome must agree with the authority it cites.

Run 20260916-070036 is the case this file exists for. Its generated scenario S1
exercised an unregistered Action Class. The repository requires that path to
FAIL CLOSED — ``GateRegistry({}).gate_for(...)`` raises
``UnclassifiedActionClass``, with no default — and the product did exactly
that. S1 expected ``exit == 0``, so Product Driver recorded the correct refusal
as a scenario failure and asked for a product that would stop refusing.

Everything here runs a real (tiny) product in a real git repository, through
the real executor and the real planner. Nothing consumes Claude usage.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
from neyma_product_driver.evidence import EvidenceStore, sanitize_filename
from neyma_product_driver.outcome_contract import (
    ACTUAL_INFRASTRUCTURE_FAILURE,
    ACTUAL_PERMITTED,
    ACTUAL_REFUSED,
    ACTUAL_UNEXPECTED_FAILURE,
    RepositoryText,
    classify,
    judge,
    typed_refusal,
)
from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
from neyma_product_driver.scenario_plan import (
    REJECTED_INVOCATION,
    GeneratedScenarioPlan,
    compile_to_scenario,
)
from neyma_product_driver.scenario_planner import (
    MAX_REDERIVE_ATTEMPTS,
    PLAN_FILENAME,
    ScenarioPlanner,
)
from neyma_product_driver.scenario_suite import (
    Outcome,
    SuiteExecutor,
    build_suite,
)
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    validate_scenario,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

from scenario_fixtures import (
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    make_scenario,
    raw_payload,
    raw_scenario,
    validation_context,
)

PY = shlex.quote(sys.executable)
PROBE = f"{PY} probe.py"
MISSING_PROBE = f"{PY} missing_probe.py"
BROKEN_PROBE = f"{PY} -c 'print(1'"
REFUSAL_CLASS = "UnclassifiedActionClass"
ADR = "docs/decisions/adr-gates.md"
ADR_QUOTE = (
    "A gate expressible as an absence is not a gate: an unregistered action class "
    "must be refused, never defaulted."
)

GATES = {
    "correct": '''
class GateError(Exception):
    pass


class UnclassifiedActionClass(GateError):
    pass


class Registry:
    def __init__(self, entries):
        self._entries = dict(entries)

    def gate_for(self, name):
        entry = self._entries.get(name)
        if entry is None:
            raise UnclassifiedActionClass(
                f"action class {name!r} carries no explicit gate decision; a missing gate "
                "is a refusal"
            )
        return entry
''',
    # The regression F-20 forbids: an absent gate silently becomes a default.
    "default": '''
class GateError(Exception):
    pass


class UnclassifiedActionClass(GateError):
    pass


class Registry:
    def __init__(self, entries):
        self._entries = dict(entries)

    def gate_for(self, name):
        return self._entries.get(name, "HUMAN_APPROVAL_REQUIRED")
''',
    # A different wrong product: it crashes instead of deciding.
    "crash": '''
class GateError(Exception):
    pass


class UnclassifiedActionClass(GateError):
    pass


class Registry:
    def __init__(self, entries):
        self._entries = dict(entries)

    def gate_for(self, name):
        return self._entries[name]
''',
}

PROBE_BODY = '''
import sys
sys.path.insert(0, ".")
from product.gates import Registry
print("probe reached the registry")
print("the unregistered-class gate:", Registry({}).gate_for("raise_invoice"))
'''


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def product_repo(root: Path, variant: str = "correct") -> Path:
    repo = root / f"product-{variant}"
    (repo / "product").mkdir(parents=True)
    (repo / "product" / "__init__.py").write_text("")
    (repo / "product" / "gates.py").write_text(GATES[variant])
    (repo / "probe.py").write_text(PROBE_BODY)
    (repo / "docs" / "decisions").mkdir(parents=True)
    (repo / ADR).write_text(f"# Gates\n\n{ADR_QUOTE}\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def harness() -> Scenario:
    """The permanent scenario. Like p6_m11_policy's oracle 7, it reviews the probe
    as a STATE CHECK — it states no exit contract for it."""
    return Scenario(
        name="gates_base",
        mode="backend",
        expect_state=[
            {
                "name": "the gate probe",
                "command": PROBE,
                "contains": ["probe reached the registry"],
            },
            {"name": "a missing probe", "command": MISSING_PROBE, "contains": []},
            {"name": "a broken probe", "command": BROKEN_PROBE, "contains": []},
        ],
    )


def refusal_action(command: str = PROBE, **overrides) -> dict:
    action = {
        "kind": "command",
        "name": "unregistered action class",
        "command": command,
        "expect_outcome": "refused",
        "expect_exit_code": None,
        "refusal_evidence": [REFUSAL_CLASS],
        "expect_contains": ["probe reached the registry"],
    }
    action.update(overrides)
    return action


def citation(requires: str = "refusal", quote: str = ADR_QUOTE, path: str = ADR) -> dict:
    return {"path": path, "quote": quote, "requires": requires}


def gate_scenario(scenario_id: str = "gen-fail-closed", *, actions=None, authority=None, **kw):
    raw = raw_scenario(
        scenario_id,
        risk_category="authorization",
        actions=actions if actions is not None else [refusal_action()],
        state_checks=[],
        expected_observations=["probe reached the registry"],
        forbidden_observations=["HUMAN_APPROVAL_REQUIRED"],
        service_refs=[],
        cleanup=[],
        isolation_note="reads source only",
        generating_risk="an unregistered action class could default instead of failing closed",
        **kw,
    )
    raw["authority"] = authority if authority is not None else [citation()]
    return raw


def as_model(raw: dict):
    from neyma_product_driver.scenario_generator import parse_scenarios
    from neyma_product_driver.scenario_plan import ScenarioProvenance

    provenance = ScenarioProvenance(
        task_hash="t", stage="initial", wave=1, model="opus", session_id="s",
        generating_risk="an unregistered action class could default",
    )
    parsed, malformed = parse_scenarios(raw_payload(raw), provenance=provenance)
    assert not malformed, malformed
    return parsed[0]


def context_for(repo: Path, **overrides):
    base = harness()
    settings = {
        "approved_commands": ApprovedCommands.from_sources(scenarios=[base]),
        "established_observations": {},
        "declared_services": set(),
        "repository_text": RepositoryText(repo),
    }
    settings.update(overrides)
    return validation_context(**settings)


def execute(repo: Path, scenario: Scenario, artifacts: Path):
    executor = ScenarioExecutor(
        repo=repo,
        run_config=ScenarioRunConfig(command_timeout_s=60),
        artifact_dir=artifacts,
    )
    return asyncio.run(executor.execute(scenario))


def compiled(raw: dict) -> Scenario:
    model = as_model(raw)
    return compile_to_scenario(model, approved_commands=set(model.command_strings()))


# ==========================================================================
# 1 — the outcome model, judged against a real product
# ==========================================================================


class TestTheOutcomeModel:
    def test_expected_refusal_and_actual_refusal_passes(self, tmp_path):
        repo = product_repo(tmp_path)
        result = execute(repo, compiled(gate_scenario()), tmp_path / "a")
        assert result.passed, [a for a in result.assertions if not a.passed]
        outcome = next(a for a in result.assertions if "outcome == refused" in a.target)
        assert "actual outcome: refused" in outcome.detail
        # The refusal path really ran: the product printed its refusal.
        assert REFUSAL_CLASS in result.commands[0].stderr
        assert result.commands[0].exit_code != 0

    def test_expected_refusal_and_silent_default_fails(self, tmp_path):
        repo = product_repo(tmp_path, "default")
        result = execute(repo, compiled(gate_scenario()), tmp_path / "a")
        assert not result.passed
        outcome = next(a for a in result.assertions if "outcome == refused" in a.target)
        assert not outcome.passed
        assert "PERMITTED" in outcome.detail and "silent allow or default" in outcome.detail
        assert not result.infrastructure_failure

    def test_expected_permit_and_refusal_fails(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = gate_scenario(
            actions=[
                {
                    "kind": "command",
                    "name": "unregistered action class",
                    "command": PROBE,
                    "expect_outcome": "permitted",
                    "expect_exit_code": 0,
                    "expect_contains": ["probe reached the registry"],
                }
            ],
            authority=[],
        )
        result = execute(repo, compiled(raw), tmp_path / "a")
        assert not result.passed
        outcome = next(a for a in result.assertions if "outcome == permitted" in a.target)
        assert "expected to be permitted and was not" in outcome.detail
        assert not result.infrastructure_failure

    def test_a_missing_program_is_infrastructure_not_a_refusal(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = gate_scenario(actions=[refusal_action(MISSING_PROBE, expect_contains=[])])
        raw["expected_observations"] = []
        result = execute(repo, compiled(raw), tmp_path / "a")
        assert not result.passed
        assert result.infrastructure_failure
        outcome = next(a for a in result.assertions if "outcome == refused" in a.target)
        assert f"actual outcome: {ACTUAL_INFRASTRUCTURE_FAILURE}" in outcome.detail

    def test_a_program_that_does_not_parse_is_infrastructure_not_a_refusal(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = gate_scenario(actions=[refusal_action(BROKEN_PROBE, expect_contains=[])])
        raw["expected_observations"] = []
        result = execute(repo, compiled(raw), tmp_path / "a")
        assert not result.passed
        assert result.infrastructure_failure

    def test_an_infrastructure_failure_is_blocked_in_the_suite_never_passed(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = gate_scenario(actions=[refusal_action(MISSING_PROBE, expect_contains=[])])
        raw["expected_observations"] = []
        model = as_model(raw)
        suite = build_suite(
            generated=[(model, compile_to_scenario(model, approved_commands=set(model.command_strings())))]
        )
        executor = SuiteExecutor(
            make_executor=lambda d: ScenarioExecutor(
                repo=repo, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=d
            ),
            artifact_root=tmp_path / "run",
            run_id="r",
            iteration=1,
            repo=repo,
        )
        result = asyncio.run(executor.run(suite))
        assert result.outcomes[0].outcome is Outcome.BLOCKED
        assert "never observed" in result.outcomes[0].error

    @pytest.mark.parametrize(
        "exit_code, timed_out, output, evidence, expected",
        [
            (0, False, "", [], ACTUAL_PERMITTED),
            (1, False, "Traceback\nx.UnclassifiedActionClass: no", ["UnclassifiedActionClass"], ACTUAL_REFUSED),
            (1, False, "KeyError: 'x'", ["UnclassifiedActionClass"], ACTUAL_UNEXPECTED_FAILURE),
            (1, False, "anything", [], ACTUAL_UNEXPECTED_FAILURE),
            (127, False, "sh: nope: command not found", ["UnclassifiedActionClass"], ACTUAL_INFRASTRUCTURE_FAILURE),
            (None, True, "", ["UnclassifiedActionClass"], ACTUAL_INFRASTRUCTURE_FAILURE),
            (1, False, "ModuleNotFoundError: No module named 'product'", ["UnclassifiedActionClass"], ACTUAL_INFRASTRUCTURE_FAILURE),
        ],
    )
    def test_classification(self, exit_code, timed_out, output, evidence, expected):
        assert classify(exit_code, timed_out, output, evidence)[0] == expected

    def test_a_non_zero_exit_alone_never_passes_a_refusal(self):
        verdict = judge(
            expect_outcome="refused",
            expect_exit_code=None,
            refusal_evidence=[],
            exit_code=1,
            timed_out=False,
            output="Traceback\nproduct.gates.UnclassifiedActionClass: no",
        )
        assert not verdict.passed


# ==========================================================================
# 2 — anti-vacuity: the refusal assertion discriminates real mutants
# ==========================================================================


class TestTheRefusalAssertionDiscriminates:
    def test_one_scenario_passes_the_correct_product_and_fails_each_mutant(self, tmp_path):
        scenario = compiled(gate_scenario())
        verdicts = {
            variant: execute(product_repo(tmp_path, variant), scenario, tmp_path / variant)
            for variant in ("correct", "default", "crash")
        }
        assert verdicts["correct"].passed
        # Silent default: exit 0 — the refusal path was never reached.
        assert not verdicts["default"].passed
        assert verdicts["default"].commands[0].exit_code == 0
        # Crash: exit non-zero, but not BY the refusal — a KeyError is not it.
        assert not verdicts["crash"].passed
        assert verdicts["crash"].commands[0].exit_code != 0
        assert REFUSAL_CLASS not in verdicts["crash"].commands[0].stderr
        crash = next(a for a in verdicts["crash"].assertions if "outcome ==" in a.target)
        assert "WITHOUT the declared refusal evidence" in crash.detail


# ==========================================================================
# 3 — refused before execution: a contradiction, or invented authority
# ==========================================================================


class TestContradictionsAreRefusedBeforeExecution:
    def test_a_lawful_refusal_scenario_is_accepted(self, tmp_path):
        repo = product_repo(tmp_path)
        assert validate_scenario(as_model(gate_scenario()), context_for(repo)) == []

    def test_expecting_success_while_citing_a_refusal_mandate_is_refused(self, tmp_path):
        """The S1 shape, declared: authority says refuse, the oracle says exit 0."""
        repo = product_repo(tmp_path)
        raw = gate_scenario(
            actions=[
                {
                    "kind": "command",
                    "name": "unregistered action class",
                    "command": PROBE,
                    "expect_outcome": "permitted",
                    "expect_exit_code": 0,
                    "expect_contains": ["probe reached the registry"],
                }
            ]
        )
        reasons = validate_scenario(as_model(raw), context_for(repo))
        assert any("contradict the authority it claims to verify" in r for r in reasons), reasons
        assert all("not a product failure" in r for r in reasons if r.startswith("outcome contract"))

    def test_a_refusal_with_no_authority_is_refused(self, tmp_path):
        repo = product_repo(tmp_path)
        reasons = validate_scenario(as_model(gate_scenario(authority=[])), context_for(repo))
        assert any("without citing repository authority" in r for r in reasons), reasons

    def test_invented_authority_is_refused(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = gate_scenario(
            authority=[citation(quote="an unregistered action class defaults to human approval")]
        )
        reasons = validate_scenario(as_model(raw), context_for(repo))
        assert any("may not invent repository authority" in r for r in reasons), reasons

    def test_the_code_under_test_is_not_its_own_authority(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = gate_scenario(
            authority=[citation(path="product/gates.py", quote="a missing gate is a refusal")]
        )
        reasons = validate_scenario(as_model(raw), context_for(repo))
        assert any("not a repository document" in r for r in reasons), reasons

    def test_a_refusal_with_no_evidence_is_refused(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = gate_scenario(actions=[refusal_action(refusal_evidence=[])])
        reasons = validate_scenario(as_model(raw), context_for(repo))
        assert any("declares no refusal_evidence" in r for r in reasons), reasons

    def test_invented_refusal_evidence_is_refused(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = gate_scenario(actions=[refusal_action(refusal_evidence=["GateMissingButOkay"])])
        reasons = validate_scenario(as_model(raw), context_for(repo))
        assert any("may not invent what the product prints" in r for r in reasons), reasons

    def test_a_refusal_that_exits_zero_is_refused(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = gate_scenario(actions=[refusal_action(expect_exit_code=0)])
        reasons = validate_scenario(as_model(raw), context_for(repo))
        assert any("cannot be told apart from a silent allow" in r for r in reasons), reasons

    def test_without_repository_access_a_refusal_cannot_be_verified(self, tmp_path):
        reasons = validate_scenario(as_model(gate_scenario()), context_for(tmp_path, repository_text=None))
        assert any("cannot read the repository" in r for r in reasons), reasons

    def test_the_planner_refuses_it_as_a_harness_defect_and_never_executes_it(self, tmp_path):
        repo = product_repo(tmp_path)
        marker = repo / "probe-ran"
        (repo / "probe.py").write_text(f"open({str(marker)!r}, 'a').write('x')\n" + PROBE_BODY)
        _git(repo, "commit", "-qam", "mark executions")
        raw = gate_scenario(
            actions=[
                {
                    "kind": "command",
                    "name": "unregistered action class",
                    "command": PROBE,
                    "expect_outcome": "permitted",
                    "expect_exit_code": 0,
                    "expect_contains": ["probe reached the registry"],
                }
            ]
        )
        planner = _planner(tmp_path, repo, [raw_payload(raw, risks=_risks())])
        planner.plan_initial(task="harden gates", unit=FakeUnit(), run_id="r1")
        assert planner.plan.scenarios == []
        assert planner.compiled == {}
        rejected = planner.plan.waves[-1].rejected
        assert rejected and rejected[0].kind == REJECTED_INVOCATION
        assert not marker.exists(), "validation must not execute the product"


# ==========================================================================
# 4 — the pre-contract scenario: held, never passed, never a product defect
# ==========================================================================


def _risks() -> list[dict]:
    return [
        {
            "id": "R1",
            "description": "an unregistered action class could default instead of failing closed",
            "risk_category": "authorization",
            "severity": "P0",
        }
    ]


def _planner(tmp_path: Path, repo: Path, payloads, *, run_id: str = "r1", max_waves: int = 3):
    return ScenarioPlanner(
        repo=repo,
        config=ScenarioGenerationConfig(enabled=True, max_waves=max_waves),
        reasoner=ScriptedReasoner(list(payloads)),
        store=EvidenceStore(tmp_path / "runs", run_id),
        base_scenario=harness(),
        permanent_scenarios=[harness()],
        founder=FakeFounder(),
        contract_probe=lambda _c: pytest.fail("no contract probe is needed here"),
    )


def legacy_s1(scenario_id: str = "S1") -> dict:
    """What run 20260916-070036 stored: no outcome contract, and exit 0 expected."""
    raw = raw_scenario(
        scenario_id,
        risk_category="authorization",
        actions=[
            {
                "kind": "command",
                "name": "gate probe",
                "command": PROBE,
                "expect_exit_code": 0,
                "expect_contains": ["probe reached the registry"],
            }
        ],
        state_checks=[],
        expected_observations=["probe reached the registry"],
        forbidden_observations=[],
        service_refs=[],
        cleanup=[],
        isolation_note="reads source only",
    )
    return raw


def plan_and_execute_legacy(tmp_path: Path, repo: Path, *, iteration: int = 4) -> ScenarioPlanner:
    """Plan the legacy S1, then execute it the way the real run did."""
    planner = _planner(tmp_path, repo, [raw_payload(legacy_s1(), risks=_risks())])
    planner.plan_initial(task="harden gates", unit=FakeUnit(), run_id="r1")
    assert "S1" in planner.compiled
    suite = build_suite(generated=[(planner.plan.scenarios[0], planner.compiled["S1"])])
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(
            repo=repo, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=d
        ),
        artifact_root=planner.store.iteration_dir(iteration),
        run_id="r1",
        iteration=iteration,
        repo=repo,
    )
    planner._last_result = asyncio.run(executor.run(suite))  # type: ignore[attr-defined]
    planner._last_executor = executor  # type: ignore[attr-defined]
    return planner


def lawful_replacement(**overrides) -> dict:
    raw = gate_scenario("S1-rederived", replaces=["S1"], source_risks=["R1"])
    raw.update(overrides)
    return raw


class TestAPreContractScenarioIsHeld:
    def test_the_stored_s1_fails_as_the_real_run_recorded(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = plan_and_execute_legacy(tmp_path, repo)
        outcome = planner._last_result.outcomes[0]
        assert outcome.outcome is Outcome.FAILED
        assert "exit == 0" in outcome.failed_assertions[0]

    def test_after_execution_it_is_held_not_reported_as_a_product_defect(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = plan_and_execute_legacy(tmp_path, repo)
        held = planner.hold_undeclared_refusals(
            planner._last_result.outcomes, planner._last_executor.results
        )
        assert held == ["S1"]
        scenario = planner.plan.by_id("S1")
        assert "typed refusal" in scenario.contract_hold
        assert "UnclassifiedActionClass" in scenario.contract_hold
        assert "S1" not in planner.compiled
        # It blocks, through the channel a replacement clears — not as a
        # permanent generation problem.
        assert "S1" in planner.unbuildable_scenarios
        assert planner.generation_problems() == []
        suite = build_suite(unbuildable=planner.unbuildable_scenarios)
        verdict = evaluate_gate(
            planner._last_result.model_copy(
                update={"outcomes": [], "expected_required_ids": [], "assembly_problems": suite.assembly_conflicts}
            )
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert "harness-oracle defect, not a product failure" in verdict.summary_block()

    def test_on_resume_it_is_held_from_its_recorded_evidence_before_running(self, tmp_path):
        repo = product_repo(tmp_path)
        plan_and_execute_legacy(tmp_path, repo)
        resumed = _planner(tmp_path, repo, [])
        restore = resumed.restore_from_store()
        assert "HELD for re-derivation" in restore.note
        assert resumed.held_scenario_ids == ["S1"]
        assert "S1" not in resumed.compiled
        assert "(recorded in iteration 4)" in resumed.plan.by_id("S1").contract_hold
        # And a second resume keeps it held without re-deriving the reason.
        again = _planner(tmp_path, repo, [])
        again.restore_from_store()
        assert again.held_scenario_ids == ["S1"]

    def test_a_builtin_crash_is_a_product_failure_not_a_hold(self, tmp_path):
        repo = product_repo(tmp_path, "crash")
        planner = plan_and_execute_legacy(tmp_path, repo)
        assert planner._last_result.outcomes[0].outcome is Outcome.FAILED
        assert planner.hold_undeclared_refusals(
            planner._last_result.outcomes, planner._last_executor.results
        ) == []
        resumed = _planner(tmp_path, repo, [])
        resumed.restore_from_store()
        assert resumed.held_scenario_ids == []
        assert "S1" in resumed.compiled

    def test_a_declared_permit_that_meets_a_refusal_is_never_held(self, tmp_path):
        repo = product_repo(tmp_path)
        model = make_scenario(
            "S9",
            actions=[],
        ).model_copy(
            update={
                "actions": [
                    as_model(
                        gate_scenario(
                            actions=[
                                {
                                    "kind": "command",
                                    "command": PROBE,
                                    "expect_outcome": "permitted",
                                    "expect_exit_code": 0,
                                }
                            ],
                            authority=[],
                        )
                    ).actions[0]
                ]
            }
        )
        from neyma_product_driver.outcome_contract import undeclared_refusal

        output = "Traceback (most recent call last):\n  x\nproduct.gates.UnclassifiedActionClass: no"
        assert undeclared_refusal(model, [(1, False, output)], RepositoryText(repo)) == ""


class TestAHoldIsReleasedOnlyByALawfulReplacement:
    def _held(self, tmp_path, repo, payloads, **kw):
        plan_and_execute_legacy(tmp_path, repo)
        resumed = _planner(tmp_path, repo, payloads, **kw)
        resumed.restore_from_store()
        assert resumed.held_scenario_ids == ["S1"]
        return resumed

    def test_a_lawful_replacement_is_admitted_and_retires_the_hold(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = self._held(tmp_path, repo, [raw_payload(lawful_replacement(), risks=_risks())])
        planner.rederive_held(task="harden gates", unit=FakeUnit())
        assert "S1-rederived" in planner.compiled
        assert planner.held_scenario_ids == []
        assert "replaced by S1-rederived" in planner.plan.by_id("S1").retired_reason
        assert "S1" not in planner.unbuildable_scenarios
        # The replacement carries the obligation at full strength, and passes
        # against the product whose refusal the old oracle mistook for a defect.
        replacement = planner.plan.by_id("S1-rederived")
        assert replacement.priority.value == "P0"
        result = execute(repo, planner.compiled["S1-rederived"], tmp_path / "rerun")
        assert result.passed, [a for a in result.assertions if not a.passed]
        # And it would fail a product that silently defaulted.
        mutant = product_repo(tmp_path, "default")
        assert not execute(mutant, planner.compiled["S1-rederived"], tmp_path / "mutant").passed

    def test_a_replacement_that_still_contradicts_authority_leaves_the_hold(self, tmp_path):
        repo = product_repo(tmp_path)
        bad = lawful_replacement(
            actions=[
                {
                    "kind": "command",
                    "command": PROBE,
                    "expect_outcome": "permitted",
                    "expect_exit_code": 0,
                    "expect_contains": ["probe reached the registry"],
                }
            ]
        )
        planner = self._held(tmp_path, repo, [raw_payload(bad, risks=_risks())])
        planner.rederive_held(task="harden gates", unit=FakeUnit())
        assert planner.held_scenario_ids == ["S1"]
        assert "S1-rederived" not in planner.compiled

    def test_a_replacement_may_not_weaken_the_obligation(self, tmp_path):
        repo = product_repo(tmp_path)
        weaker = lawful_replacement(priority="P2")
        planner = self._held(tmp_path, repo, [raw_payload(weaker, risks=_risks())])
        planner.rederive_held(task="harden gates", unit=FakeUnit())
        assert planner.held_scenario_ids == ["S1"]
        reasons = [r for w in planner.plan.waves for x in w.rejected for r in x.reasons]
        assert any("may not weaken the obligation" in r for r in reasons)

    def test_only_a_held_scenario_can_be_replaced(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = self._held(
            tmp_path, repo, [raw_payload(lawful_replacement(replaces=["S7"]), risks=_risks())]
        )
        planner.rederive_held(task="harden gates", unit=FakeUnit())
        assert planner.held_scenario_ids == ["S1"]

    def test_rederivation_does_not_depend_on_the_spent_wave_budget(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = self._held(
            tmp_path, repo, [raw_payload(lawful_replacement(), risks=_risks())], max_waves=1
        )
        assert planner.waves_used >= 1
        planner.rederive_held(task="harden gates", unit=FakeUnit())
        assert planner.held_scenario_ids == []

    def test_rederivation_attempts_are_bounded_across_resumes(self, tmp_path):
        repo = product_repo(tmp_path)
        plan_and_execute_legacy(tmp_path, repo)
        for _ in range(MAX_REDERIVE_ATTEMPTS + 2):
            planner = _planner(tmp_path, repo, [raw_payload(risks=_risks())] * 4)
            planner.restore_from_store()
            planner.rederive_held(task="harden gates", unit=FakeUnit())
        plan = GeneratedScenarioPlan.model_validate_json(
            (planner.store.run_dir / PLAN_FILENAME).read_text()
        )
        assert plan.by_id("S1").rederive_attempts == MAX_REDERIVE_ATTEMPTS
        assert plan.by_id("S1").contract_hold
        rederivation_waves = [w for w in plan.waves if w.stage == "rederivation"]
        assert len(rederivation_waves) == MAX_REDERIVE_ATTEMPTS

    def test_the_generator_is_shown_what_is_held_and_the_contract(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = self._held(tmp_path, repo, [raw_payload(risks=_risks())])
        planner.rederive_held(task="harden gates", unit=FakeUnit())
        brief = planner.reasoner.briefs[-1]
        rendered = brief.render()
        assert "SCENARIOS HELD FOR RE-DERIVATION" in rendered
        assert "S1 [P0 authorization]" in rendered
        assert brief.stage == "rederivation"
        from neyma_product_driver.scenario_generator import GENERATOR_SYSTEM

        assert "EVERY COMMAND DECLARES ITS OUTCOME" in GENERATOR_SYSTEM


def test_typed_refusal_requires_a_repository_defined_exception(tmp_path):
    repo = product_repo(tmp_path)
    text = RepositoryText(repo)
    tb = "Traceback (most recent call last):\n  File \"<string>\", line 1\n"
    assert typed_refusal(tb + "product.gates.UnclassifiedActionClass: no", text).startswith(
        "product.gates.UnclassifiedActionClass (defined in product/gates.py)"
    )
    assert typed_refusal(tb + "KeyError: 'raise_invoice'", text) == ""
    assert typed_refusal(tb + "somelib.errors.VendorError: no", text) == ""
    assert typed_refusal("no traceback here", text) == ""


def test_the_case_record_persists_the_contract(tmp_path):
    """What a resume reads back is what was declared."""
    model = as_model(gate_scenario())
    assert model.actions[0].expect_outcome == "refused"
    assert model.authority[0].requires == "refusal"
    again = type(model).model_validate(json.loads(model.model_dump_json()))
    assert again == model
    assert sanitize_filename("S1")  # the folder a resume looks in exists by name

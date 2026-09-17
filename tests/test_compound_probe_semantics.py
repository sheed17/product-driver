"""A compound probe is judged operation by operation, against the repository's own guards.

Run 20260917-063502 is the case this file exists for. Its generated P8-S11 ran a
dozen structural assertions that must succeed and, on its last line, an
operation whose correct behaviour is to REFUSE — ``GateRegistry({}).gate_for``
on an unregistered Action Class. It declared ``permitted`` and cited no
authority, so the outcome contract had nothing to contradict, and the correct
refusal reached the gate as a product failure.

Here a tiny real product in a real git repository carries its own guard (a
test that runs the unregistered lookup inside ``pytest.raises``). Every probe
is executed through the real executor, against the correct product and
against the silent-default and crash mutants. Nothing consumes Claude usage.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.outcome_contract import RepositoryText, contract_problems
from neyma_product_driver.refusal_semantics import (
    analyse_program,
    command_program,
    operation_semantics,
)
from neyma_product_driver.scenario_plan import REJECTED_INVOCATION, compile_to_scenario
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import Outcome, SuiteExecutor, build_suite
from neyma_product_driver.scenario_validation import ApprovedCommands, validate_scenario
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

from scenario_fixtures import (
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    raw_payload,
    raw_scenario,
    validation_context,
)

PY = shlex.quote(sys.executable)
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
            raise UnclassifiedActionClass(f"action class {name!r} carries no gate")
        return entry
''',
    # F-20's forbidden regression: an absent gate silently becomes a default.
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

#: The repository's own guard: the unregistered lookup is exercised ONLY as a
#: refusal, and a registered lookup is exercised as a permit.
GUARD = '''
import pytest

from product.gates import Registry, UnclassifiedActionClass


def test_an_unregistered_class_is_refused():
    with pytest.raises(UnclassifiedActionClass):
        Registry({}).gate_for("anything")


def test_a_registered_class_resolves():
    assert Registry({"a": "HUMAN"}).gate_for("a") == "HUMAN"
'''

#: The P8-S11 shape: structural checks, then an unguarded refusing call.
COMPOUND_PROGRAM = (
    "import sys; sys.path.insert(0, '.'); import product.gates as g; "
    "open('probe-ran', 'a').write('x'); "
    "print('structure: the registry is importable'); "
    "print('registered resolves:', g.Registry({'a': 'HUMAN'}).gate_for('a')); "
    "print('unregistered:', g.Registry({}).gate_for('raise_invoice')); "
    "print('printed only after the refusal')"
)
COMPOUND = f"{PY} -c {shlex.quote(COMPOUND_PROGRAM)}"

#: The same probe, catching and reporting the refusal itself.
CAUGHT_BODY = '''
import sys
sys.path.insert(0, ".")
from product.gates import Registry, UnclassifiedActionClass

print("structure: the registry is importable")
print("registered resolves:", Registry({"a": "HUMAN"}).gate_for("a"))
try:
    outcome = Registry({}).gate_for("raise_invoice")
except UnclassifiedActionClass:
    outcome = "REFUSED (no gate, no default)"
print("unregistered:", outcome)
'''
CAUGHT = f"{PY} compound_caught.py"

#: A probe whose refusing call only runs when a helper that catches it calls it.
DEFERRED_PROGRAM = (
    "import sys; sys.path.insert(0, '.'); import product.gates as g; "
    "attempt = lambda fn: fn(); "
    "print('structure: the registry is importable'); "
    "print('deferred:', callable(lambda: g.Registry({}).gate_for('x')))"
)
DEFERRED = f"{PY} -c {shlex.quote(DEFERRED_PROGRAM)}"

#: The interpreter never reaches the product.
MISSING_MODULE_PROGRAM = (
    "import product.no_such_module as g; print(g.Registry({}).gate_for('raise_invoice'))"
)
MISSING_MODULE = f"{PY} -c {shlex.quote(MISSING_MODULE_PROGRAM)}"

STRUCTURE = ["structure: the registry is importable", "registered resolves: HUMAN"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def product_repo(root: Path, variant: str = "correct", *, guard: bool = True) -> Path:
    repo = root / f"product-{variant}{'' if guard else '-unguarded'}"
    (repo / "product").mkdir(parents=True)
    (repo / "product" / "__init__.py").write_text("")
    (repo / "product" / "gates.py").write_text(GATES[variant])
    (repo / "docs" / "decisions").mkdir(parents=True)
    (repo / ADR).write_text(f"# Gates\n\n{ADR_QUOTE}\n")
    if guard:
        # Both are the repository treating the unregistered lookup as a refusal:
        # the catching probe is a guard in its own right.
        (repo / "compound_caught.py").write_text(CAUGHT_BODY)
        (repo / "tests").mkdir()
        (repo / "tests" / "test_gates.py").write_text(GUARD)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def harness() -> Scenario:
    return Scenario(
        name="gates_base",
        mode="backend",
        expect_state=[
            {"name": "compound probe", "command": COMPOUND, "contains": []},
            {"name": "compound caught", "command": CAUGHT, "contains": []},
            {"name": "deferred probe", "command": DEFERRED, "contains": []},
            {"name": "missing module", "command": MISSING_MODULE, "contains": []},
        ],
    )


def action(command: str, outcome: str, contains, **overrides) -> dict:
    body = {
        "kind": "command",
        "name": "compound probe",
        "command": command,
        "expect_outcome": outcome,
        "expect_exit_code": 0 if outcome == "permitted" else None,
        "expect_contains": list(contains),
    }
    if outcome == "refused":
        body["refusal_evidence"] = [REFUSAL_CLASS]
    body.update(overrides)
    return body


def scenario(scenario_id: str, act: dict, *, cite: bool, **kw) -> dict:
    raw = raw_scenario(
        scenario_id,
        risk_category="authorization",
        actions=[act],
        state_checks=[],
        expected_observations=list(act.get("expect_contains", [])),
        forbidden_observations=[],
        service_refs=[],
        cleanup=[],
        isolation_note="reads source only",
        generating_risk="an unregistered action class could default instead of failing closed",
        **kw,
    )
    raw["authority"] = (
        [{"path": ADR, "quote": ADR_QUOTE, "requires": "refusal"}] if cite else []
    )
    return raw


def s11_shape(scenario_id: str = "S11") -> dict:
    """What run 20260917-063502 stored: declared permitted, cited nothing."""
    return scenario(scenario_id, action(COMPOUND, "permitted", STRUCTURE), cite=False)


def refused_compound(scenario_id: str = "S11-refused", **kw) -> dict:
    return scenario(scenario_id, action(COMPOUND, "refused", STRUCTURE), cite=True, **kw)


def caught_compound(scenario_id: str = "S11-caught", contains=None) -> dict:
    contains = STRUCTURE + ["unregistered: REFUSED"] if contains is None else contains
    return scenario(scenario_id, action(CAUGHT, "permitted", contains), cite=False)


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


def context_for(repo: Path):
    base = harness()
    return validation_context(
        approved_commands=ApprovedCommands.from_sources(scenarios=[base]),
        established_observations={},
        declared_services=set(),
        repository_text=RepositoryText(repo),
    )


def execute(repo: Path, raw: dict, artifacts: Path):
    model = as_model(raw)
    compiled = compile_to_scenario(model, approved_commands=set(model.command_strings()))
    executor = ScenarioExecutor(
        repo=repo, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=artifacts
    )
    return asyncio.run(executor.execute(compiled))


def outcome_assertion(result):
    return next(a for a in result.assertions if "outcome ==" in a.target)


def _risks() -> list[dict]:
    return [
        {
            "id": "R1",
            "description": "an unregistered action class could default instead of failing closed",
            "risk_category": "authorization",
            "severity": "P0",
        }
    ]


def _planner(tmp_path: Path, repo: Path, payloads, *, run_id: str = "r1"):
    return ScenarioPlanner(
        repo=repo,
        config=ScenarioGenerationConfig(enabled=True, max_waves=3),
        reasoner=ScriptedReasoner(list(payloads)),
        store=EvidenceStore(tmp_path / "runs", run_id),
        base_scenario=harness(),
        permanent_scenarios=[harness()],
        founder=FakeFounder(),
        contract_probe=lambda _c: pytest.fail("no contract probe is needed here"),
    )


# ==========================================================================
# 1 — what the repository's guards say each operation is
# ==========================================================================


class TestOperationSemantics:
    def test_the_refusing_operation_is_read_from_the_repository_guard(self, tmp_path):
        repo = product_repo(tmp_path)
        semantics = operation_semantics(as_model(s11_shape()), RepositoryText(repo))
        required = {s.operation.shape: s.required for s in semantics}
        assert required["Registry({}).gate_for(<str>)"] == "refused"
        assert required["Registry({<str>: <str>}).gate_for(<str>)"] == ""
        refusing = next(s for s in semantics if s.required)
        places = refusing.refusal_classes[REFUSAL_CLASS]
        assert any(p.startswith("tests/test_gates.py:") for p in places), places
        assert any(p.startswith("compound_caught.py:") for p in places), places
        assert REFUSAL_CLASS in refusing.cite()

    def test_without_a_repository_guard_nothing_is_required(self, tmp_path):
        repo = product_repo(tmp_path, guard=False)
        semantics = operation_semantics(as_model(s11_shape()), RepositoryText(repo))
        assert semantics and not any(s.required for s in semantics)

    def test_a_shape_exercised_both_ways_is_ambiguous_and_never_used(self, tmp_path):
        repo = product_repo(tmp_path)
        (repo / "tests" / "test_also.py").write_text(
            "from product.gates import Registry\n\n"
            "def test_default_somewhere():\n"
            "    Registry({}).gate_for('x')\n"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "ambiguous")
        assert contract_problems(as_model(s11_shape()), RepositoryText(repo)) == []

    def test_an_argument_of_a_product_call_is_not_its_own_operation(self):
        program = (
            "import product.gates as g\n"
            "try:\n"
            "    g.check(g.Registry({}))\n"
            "except g.UnclassifiedActionClass:\n"
            "    pass\n"
        )
        shapes = [op.shape for op in analyse_program(program).operations]
        assert shapes == ["check(Registry({}))"]

    def test_a_call_inside_a_lambda_is_never_read_as_unguarded(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario(
            "S-deferred",
            action(DEFERRED, "permitted", ["structure: the registry is importable"]),
            cite=False,
        )
        assert contract_problems(as_model(raw), RepositoryText(repo)) == []

    def test_a_tracked_script_is_read_and_a_module_run_is_not(self, tmp_path):
        repo = product_repo(tmp_path)
        text = RepositoryText(repo)
        assert command_program(CAUGHT, text)[0] == "compound_caught.py"
        assert command_program(f"{PY} -m pytest tests", text) is None
        assert command_program("make check", text) is None


# ==========================================================================
# 2 — the compound probe, executed
# ==========================================================================


class TestCompoundProbeOutcomes:
    def test_structure_passes_and_the_deliberate_refusal_is_the_pass(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = refused_compound()
        assert validate_scenario(as_model(raw), context_for(repo)) == []
        result = execute(repo, raw, tmp_path / "a")
        assert result.passed, [a for a in result.assertions if not a.passed]
        assert "actual outcome: refused" in outcome_assertion(result).detail
        assert REFUSAL_CLASS in result.commands[0].stderr

    def test_the_same_probe_fails_a_product_that_silently_defaults(self, tmp_path):
        repo = product_repo(tmp_path, "default")
        result = execute(repo, refused_compound(), tmp_path / "a")
        assert not result.passed
        assert result.commands[0].exit_code == 0
        assert "silent allow or default" in outcome_assertion(result).detail

    def test_the_same_probe_fails_a_product_that_crashes(self, tmp_path):
        repo = product_repo(tmp_path, "crash")
        result = execute(repo, refused_compound(), tmp_path / "a")
        assert not result.passed
        assert "WITHOUT the declared refusal evidence" in outcome_assertion(result).detail

    def test_a_probe_that_catches_the_refusal_passes_and_exits_zero(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = caught_compound()
        assert validate_scenario(as_model(raw), context_for(repo)) == []
        result = execute(repo, raw, tmp_path / "a")
        assert result.passed, [a for a in result.assertions if not a.passed]
        assert result.commands[0].exit_code == 0

    def test_the_catching_probe_fails_a_product_that_silently_defaults(self, tmp_path):
        repo = product_repo(tmp_path, "default")
        result = execute(repo, caught_compound(), tmp_path / "a")
        assert not result.passed
        assert result.commands[0].exit_code == 0
        assert "unregistered: HUMAN_APPROVAL_REQUIRED" in result.commands[0].stdout

    def test_expected_permit_and_actual_refusal_fails(self, tmp_path):
        repo = product_repo(tmp_path)
        result = execute(repo, s11_shape(), tmp_path / "a")
        assert not result.passed
        assert "expected to be permitted and was not" in outcome_assertion(result).detail
        assert not result.infrastructure_failure

    def test_an_infrastructure_failure_is_blocked_not_a_refusal(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario("S-infra", action(MISSING_MODULE, "refused", []), cite=True)
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


# ==========================================================================
# 3 — contradictions refused before anything executes
# ==========================================================================


class TestContradictionsAreRefusedBeforeExecution:
    def test_the_s11_shape_is_refused_even_though_it_cites_nothing(self, tmp_path):
        repo = product_repo(tmp_path)
        reasons = validate_scenario(as_model(s11_shape()), context_for(repo))
        assert any(
            "expects its command to be PERMITTED" in r and "Registry({}).gate_for('raise_invoice')" in r
            for r in reasons
        ), reasons
        assert all("not a product failure" in r for r in reasons if r.startswith("outcome contract"))

    def test_the_same_probe_is_lawful_where_the_repository_says_nothing(self, tmp_path):
        repo = product_repo(tmp_path, guard=False)
        assert validate_scenario(as_model(s11_shape()), context_for(repo)) == []

    def test_catching_the_refusal_without_asserting_its_branch_is_refused(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = caught_compound(contains=STRUCTURE)
        reasons = validate_scenario(as_model(raw), context_for(repo))
        assert any("asserts nothing only that refusal branch prints" in r for r in reasons), reasons

    def test_a_refusal_asserting_text_printed_after_it_is_refused(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario(
            "S-late",
            action(COMPOUND, "refused", STRUCTURE + ["printed only after the refusal"]),
            cite=True,
        )
        reasons = validate_scenario(as_model(raw), context_for(repo))
        assert any("can never be observed" in r for r in reasons), reasons

    def test_the_prior_contract_rules_still_apply_to_a_compound_refusal(self, tmp_path):
        repo = product_repo(tmp_path)
        uncited = scenario("S-uncited", action(COMPOUND, "refused", STRUCTURE), cite=False)
        reasons = validate_scenario(as_model(uncited), context_for(repo))
        assert any("without citing repository authority" in r for r in reasons), reasons

    def test_the_planner_refuses_it_as_a_harness_defect_and_never_runs_it(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = _planner(tmp_path, repo, [raw_payload(s11_shape(), risks=_risks())])
        planner.plan_initial(task="harden gates", unit=FakeUnit(), run_id="r1")
        assert planner.plan.scenarios == []
        assert planner.compiled == {}
        rejected = planner.plan.waves[-1].rejected
        assert rejected and rejected[0].kind == REJECTED_INVOCATION
        assert not (repo / "probe-ran").exists(), "validation must not execute the product"
        # The risk it was for is still an obligation.
        assert any(r.risk_category.value == "authorization" for r in planner.plan.risks)

    def test_the_generator_is_told_which_approved_command_refuses(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = _planner(tmp_path, repo, [raw_payload(risks=_risks())])
        planner.plan_initial(task="harden gates", unit=FakeUnit(), run_id="r1")
        rendered = planner.reasoner.briefs[-1].render()
        assert "REFUSING OPERATION" in rendered
        from neyma_product_driver.scenario_generator import GENERATOR_SYSTEM

        assert "A COMMAND'S OUTCOME IS THE OUTCOME OF EVERY OPERATION IN IT" in GENERATOR_SYSTEM


# ==========================================================================
# 4 — a scenario admitted before this check is held on resume, never re-run
# ==========================================================================


class TestAnAdmittedContradictionIsHeldOnResume:
    def _admitted_then_guarded(self, tmp_path):
        """Admit the S11 shape while the repository has no guard, then add the guard."""
        repo = product_repo(tmp_path, guard=False)
        planner = _planner(tmp_path, repo, [raw_payload(s11_shape(), risks=_risks())])
        planner.plan_initial(task="harden gates", unit=FakeUnit(), run_id="r1")
        assert "S11" in planner.compiled
        (repo / "compound_caught.py").write_text(CAUGHT_BODY)
        (repo / "tests").mkdir()
        (repo / "tests" / "test_gates.py").write_text(GUARD)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "the repository states F-20")
        return repo

    def test_resume_holds_it_before_anything_runs(self, tmp_path):
        repo = self._admitted_then_guarded(tmp_path)
        resumed = _planner(tmp_path, repo, [])
        resumed.restore_from_store()
        assert resumed.held_scenario_ids == ["S11"]
        assert "S11" not in resumed.compiled
        hold = resumed.plan.by_id("S11").contract_hold
        assert "harness-oracle defect in Product Driver, not a product failure" in hold
        assert "Registry({}).gate_for('raise_invoice')" in hold
        assert "S11" in resumed.unbuildable_scenarios
        assert not (repo / "probe-ran").exists()

    def test_a_lawful_compound_replacement_releases_it_and_discriminates(self, tmp_path):
        repo = self._admitted_then_guarded(tmp_path)
        replacement = refused_compound("S11-rederived", replaces=["S11"], source_risks=["R1"])
        resumed = _planner(tmp_path, repo, [raw_payload(replacement, risks=_risks())])
        resumed.restore_from_store()
        resumed.rederive_held(task="harden gates", unit=FakeUnit())
        assert resumed.held_scenario_ids == []
        assert "S11-rederived" in resumed.compiled
        assert "replaced by S11-rederived" in resumed.plan.by_id("S11").retired_reason
        executor = ScenarioExecutor(
            repo=repo, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=tmp_path / "ok"
        )
        assert asyncio.run(executor.execute(resumed.compiled["S11-rederived"])).passed
        mutant = product_repo(tmp_path, "default")
        executor = ScenarioExecutor(
            repo=mutant, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=tmp_path / "m"
        )
        assert not asyncio.run(executor.execute(resumed.compiled["S11-rederived"])).passed

    def test_a_replacement_that_repeats_the_contradiction_leaves_the_hold(self, tmp_path):
        repo = self._admitted_then_guarded(tmp_path)
        again = s11_shape("S11-again")
        again["replaces"] = ["S11"]
        again["source_risks"] = ["R1"]
        resumed = _planner(tmp_path, repo, [raw_payload(again, risks=_risks())])
        resumed.restore_from_store()
        resumed.rederive_held(task="harden gates", unit=FakeUnit())
        assert resumed.held_scenario_ids == ["S11"]
        assert "S11-again" not in resumed.compiled

    def test_a_lawful_admitted_scenario_is_not_held(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = _planner(tmp_path, repo, [raw_payload(refused_compound(), risks=_risks())])
        planner.plan_initial(task="harden gates", unit=FakeUnit(), run_id="r1")
        resumed = _planner(tmp_path, repo, [])
        resumed.restore_from_store()
        assert resumed.held_scenario_ids == []
        assert "S11-refused" in resumed.compiled


def test_a_guard_committed_later_is_read_by_the_same_repository_reader(tmp_path):
    """A builder commits between iterations; the index follows the judged tree."""
    repo = product_repo(tmp_path, guard=False)
    text = RepositoryText(repo)
    assert contract_problems(as_model(s11_shape()), text) == []
    (repo / "tests").mkdir()
    (repo / "tests" / "test_gates.py").write_text(GUARD)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the repository states F-20")
    text.refresh()
    assert any("PERMITTED" in p for p in contract_problems(as_model(s11_shape()), text))
    # Refreshing an unmoved HEAD keeps what was read.
    index = text.refusal_index()
    text.refresh()
    assert text.refusal_index() is index

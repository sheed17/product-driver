"""A generated scenario may run a program only as the program's own grammar allows.

Run ``20260911-081124`` built P7 and could not accept it, on six generated
scenarios that could never pass against a correct product. Every one ran an
invocation a human had written into a permanent scenario file as a REFUSAL
CONTROL — ``probe --case X --inject <a fault the probe does not have>``,
reviewed to exit 2 and print ``unknown fault`` — and demanded exit 0. The
probes refused, correctly; Product Driver scored the refusals as product
failures; and the only way a builder could have turned those exact commands
green was to widen a closed fault vocabulary, which it correctly refused to do.

Approval could not see it. The commands were approved — verbatim — because
approval is a prefix match over human-written commands, and that answers "may
this run" without saying anything about what the program does with it. Two
things were missing, and this file pins both:

* **grammar.** A generated invocation must be valid under what the repository
  says the program accepts: an exact invocation keeps the exit contract a human
  reviewed it with, whatever extends a refusal control inherits the refusal,
  and an option the repository proves closed takes only a value it vouches for;
* **grounding.** A refused invocation never reaches the product, so it can
  establish only the risk the repository says the refusal establishes — never
  the conflicting-evidence or safety-invariant risk it was offered for.

And what follows from both: a risk no approved measurement can express stays an
uncovered obligation and goes to the builder as verification work, and a
resumed run retires the impossible scenarios it already persisted without
deleting what they were meant to verify or executing them again.

The rules are exercised on a synthetic program, ``./machine.sh``, whose closed
option is ``--fault`` rather than the run's ``--inject`` — the grammar is read
from the scenario files, and nothing in Product Driver names either. The run
itself is replayed from ``tests/data/run-20260911-081124-invalid-invocations.json``
(``runs/`` is not committed) against the permanent scenario files in this
repository. Nothing here consumes Claude usage or executes a program.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from neyma_product_driver import cli as cli_module
from neyma_product_driver import invocation_grammar
from neyma_product_driver.cli import _assemble_suite, _verification_gap_decision
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.invocation_grammar import (
    CLOSED_VOCABULARY,
    EXIT_CONTRACT,
    IRRELEVANT_REFUSAL,
    REFUSAL_INHERITED,
    REFUSED_ARGUMENT,
    InvocationGrammar,
)
from neyma_product_driver.models import Decision, RunState, RunStatus
from neyma_product_driver.scenario_gate import evaluate_gate
from neyma_product_driver.scenario_plan import (
    REJECTED_INVOCATION,
    GeneratedAction,
    GeneratedScenario,
    GeneratedScenarioPlan,
    GeneratedStateCheck,
    IdentifiedRisk,
    Priority,
    RiskCategory,
    WaveRecord,
)
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import (
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteResult,
)
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    ContractProbeResult,
    established_observations_from,
    invocation_reasons,
    validate_scenario,
)
from neyma_product_driver.scenarios import Scenario, load_scenario

from scenario_fixtures import (
    APPROVED_CLEANUP,
    APPROVED_STATE,
    FakeFounder,
    ScriptedReasoner,
    base_scenario,
    make_scenario,
    raw_scenario,
    validation_context,
)
from test_coverage_gap_closure import CRASH_RISK, drive, first_wave, risk_key
from test_scenario_loop import FakeBuilder, accept

DRIVER_ROOT = Path(__file__).resolve().parents[1]
RUN_FIXTURE = DRIVER_ROOT / "tests" / "data" / "run-20260911-081124-invalid-invocations.json"

# --------------------------------------------------------------------------
# A program with a closed fault vocabulary, as a scenario file states it
# --------------------------------------------------------------------------

MACHINE = "./machine.sh"
LIST_CASES = f"{MACHINE} --list-cases"
LIST_DIMENSIONS = f"{MACHINE} --list-dimensions"
#: The vocabulary's own negative controls: a fault that is not a member, and a
#: fault naming a transition the machine does not have. Both reviewed to exit 2.
INVENTED_FAULT_CONTROL = f"{MACHINE} --case happy-path-completes --fault not-a-member"
NO_SUCH_TRANSITION_CONTROL = f"{MACHINE} --case a-hold-never-expires --fault expire-hold"
REFUSALS_ESTABLISH = "malformed_input"


def machine_scenario() -> Scenario:
    """A permanent scenario declaring ``./machine.sh``'s grammar the way P6 files do."""
    return Scenario(
        name="machine_permanent",
        mode="backend",
        commands=[
            {
                "name": "the machine enumerates the cases it can drive",
                "run": LIST_CASES,
                "expect_contains": [
                    "happy-path-completes",
                    "a-hold-never-expires",
                    "a-lost-update-is-refused",
                ],
            },
            {
                "name": "the machine exposes a bounded, closed dimension vocabulary",
                "run": LIST_DIMENSIONS,
                "expect_contains": ["--fault", "--seed", "drop-ack", "duplicate-delivery"],
            },
            {
                "name": "an invented fault is refused, so the vocabulary is closed",
                "run": INVENTED_FAULT_CONTROL,
                "expect_exit_code": 2,
                "expect_contains": ["unknown fault"],
            },
            {
                "name": "an expiry fault does not exist, because a hold never expires",
                "run": NO_SUCH_TRANSITION_CONTROL,
                "expect_exit_code": 2,
                "expect_contains": ["unknown fault"],
            },
            {
                "name": "drive the machine through its narrative",
                "run": MACHINE,
                "expect_exit_code": 0,
            },
        ],
        verifies=[
            {
                "risk_category": REFUSALS_ESTABLISH,
                "claim": "the fault vocabulary is closed",
                "checks": [
                    "an invented fault is refused, so the vocabulary is closed",
                    "an expiry fault does not exist, because a hold never expires",
                ],
                "observations": ["unknown fault"],
            },
            {
                "risk_category": "idempotency",
                "claim": "a duplicate delivery is a no-op",
                "checks": ["drive the machine through its narrative"],
                "observations": ["A DUPLICATE IS A NO-OP"],
            },
        ],
    )


def permanent() -> list[Scenario]:
    return [base_scenario(), machine_scenario()]


def grammar() -> InvocationGrammar:
    scenarios = permanent()
    return InvocationGrammar.from_scenarios(
        scenarios, ApprovedCommands.from_sources(scenarios=scenarios)
    )


def context() -> Any:
    scenarios = permanent()
    return validation_context(
        approved_commands=ApprovedCommands.from_sources(scenarios=scenarios),
        established_observations=established_observations_from(scenarios),
        invocation_grammar=grammar(),
    )


def runs(
    command: str,
    *,
    exit_code: int | None = 0,
    category: RiskCategory = RiskCategory.CONCURRENCY,
    contains: list[str] | None = None,
    scenario_id: str = "gen-machine",
) -> GeneratedScenario:
    """A generated scenario whose one exercising action runs ``command``."""
    return make_scenario(
        scenario_id,
        risk_category=category,
        actions=[
            GeneratedAction(
                kind="command",
                name="drive the machine",
                command=command,
                expect_exit_code=exit_code,
                expect_contains=list(contains or []),
            )
        ],
    )


def kinds(scenario: GeneratedScenario) -> list[str]:
    return [problem.kind for problem in grammar().problems(scenario)]


# --------------------------------------------------------------------------
# What the grammar reads out of the scenario files
# --------------------------------------------------------------------------


class TestTheGrammarIsReadFromTheRepository:
    def test_the_closed_option_and_its_refused_members_are_inferred_not_named(self):
        vocabulary = grammar().vocabulary(MACHINE)
        assert set(vocabulary.closed_options) == {"--fault"}
        assert set(vocabulary.refused) == {"not-a-member", "expire-hold"}
        # The members its reviewed enumeration names are vouched for.
        assert {"drop-ack", "duplicate-delivery"} <= vocabulary.vouched

    def test_a_refusal_control_carries_its_exit_code_and_what_it_establishes(self):
        control = grammar().authored(INVENTED_FAULT_CONTROL)
        assert control is not None and control.refusal
        assert control.exit_codes == frozenset({2})
        assert control.risk_categories == frozenset({REFUSALS_ESTABLISH})

    def test_a_program_the_repository_proves_nothing_about_stays_open(self):
        """No refusal control, no closure: approval alone governs, as before."""
        scenario = make_scenario(
            actions=[
                GeneratedAction(
                    kind="command", command=f"{APPROVED_STATE} --anything goes", expect_exit_code=0
                )
            ]
        )
        assert grammar().problems(scenario) == []


# --------------------------------------------------------------------------
# 1, 2. an approved prefix plus an unsupported argument is refused before
#       execution, as a harness-generation defect
# --------------------------------------------------------------------------


class TestAnUnsupportedArgumentIsRefusedBeforeExecution:
    def test_an_invented_fault_on_an_approved_prefix_is_refused(self):
        scenario = runs(f"{MACHINE} --case a-lost-update-is-refused --fault invented-fault")
        approved = context().approved_commands
        # Technically approved: the prefix is a human-written command.
        assert approved.approves(scenario.actions[0].command) == (True, "")
        assert kinds(scenario)[0] == CLOSED_VOCABULARY
        reasons = validate_scenario(scenario, context())
        assert any("'invented-fault'" in r and "CLOSED vocabulary" in r for r in reasons)

    def test_reusing_a_refusal_control_and_expecting_success_is_refused(self):
        """The run's exact shape: a verbatim refusal control, expected to exit 0."""
        scenario = runs(NO_SUCH_TRANSITION_CONTROL, category=RiskCategory.MALFORMED_INPUT)
        assert kinds(scenario) == [EXIT_CONTRACT]
        [reason] = invocation_reasons(scenario, context())
        assert "reviewed to exit 2" in reason and "REFUSAL CONTROL" in reason

    def test_a_refused_member_is_refused_under_any_other_case(self):
        scenario = runs(f"{MACHINE} --case happy-path-completes --fault expire-hold")
        assert REFUSED_ARGUMENT in kinds(scenario)

    def test_extending_a_refusal_control_inherits_the_refusal(self):
        scenario = runs(f"{INVENTED_FAULT_CONTROL} --seed 3")
        assert REFUSAL_INHERITED in kinds(scenario)

    def test_the_refusal_is_classified_as_a_harness_defect_and_never_executes(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "20260911-000000")
        asked: list[str] = []
        planner = planner_for(
            store,
            [
                {
                    "risks": [],
                    "scenarios": [
                        machine_raw(
                            "gen-invented",
                            f"{MACHINE} --case a-lost-update-is-refused --fault invented-fault",
                            contains=["A LOST UPDATE IS REFUSED"],
                        )
                    ],
                }
            ],
            asked=asked,
        )

        planner.plan_initial(task="build the machine")

        [wave] = planner.plan.waves
        [rejected] = wave.rejected
        assert rejected.kind == REJECTED_INVOCATION and rejected.is_invocation_defect
        assert "harness-generation defect" in rejected.reasons[0]
        assert "not a product failure" in rejected.reasons[0]
        assert "invalid invocations" in wave.accounting()
        # Refused before execution: never compiled, never asked what it prints.
        assert "gen-invented" not in planner.compiled
        assert not any("invented-fault" in command for command in asked)
        # Not a generation failure either: nothing was unreadable.
        assert planner.generation_problems() == []


# --------------------------------------------------------------------------
# 3. a risk no approved probe can express stays an obligation, and goes to
#    the builder as verification work
# --------------------------------------------------------------------------


class TestAnInexpressibleRiskStaysAnObligation:
    def test_the_refused_scenarios_risk_is_carried_as_an_uncovered_obligation(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "20260911-000001")
        planner = planner_for(
            store,
            [
                {
                    "risks": [],
                    "scenarios": [
                        machine_raw(
                            "gen-hold",
                            NO_SUCH_TRANSITION_CONTROL,
                            category="unexpected_state_transition",
                            generating_risk="a hold could silently expire",
                        )
                    ],
                }
            ],
        )

        planner.plan_initial(task="build the machine")

        [risk] = planner.plan.risks
        assert risk.risk_category is RiskCategory("unexpected_state_transition")
        assert risk.severity is Priority.P0
        assert risk.description == "a hold could silently expire"
        assert risk in planner.plan.planned_gaps()
        verdict = evaluate_gate(passing_suite([]), risks=planner.plan.risks)
        assert verdict.blocks_acceptance
        assert [g.risk_category for g in verdict.uncovered_risks] == [
            "unexpected_state_transition"
        ]

    async def test_it_routes_to_the_builder_as_verification_work(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 2
        planner = planner_for(
            store,
            [first_wave(), impossible_gap_wave(), {"risks": [], "scenarios": []}],
            config=config,
        )
        log: list[str] = []
        builder = FakeBuilder()

        result = await drive(config, store, state, planner, log=log, builder=builder)

        first = result.state.iterations[0].decision
        assert first is not None and first.decision is Decision.FIX
        assert "not a product defect" in first.summary
        # The builder received it as verification work, in the same session.
        assert len(builder.prompts) == 2
        assert "PRODUCT-VERIFICATION GAP" in builder.prompts[1]
        assert "Do NOT widen, weaken or rename any closed" in builder.prompts[1]
        # Never executed, never covered, never accepted.
        assert "gen-crash-impossible" not in log
        assert result.status is not RunStatus.ACCEPTED
        assert result.gate is not None
        assert CRASH_RISK in [r.description for r in result.gate.uncovered_risks]

    def test_routing_never_overrides_what_the_evaluator_did_not_accept(self):
        from test_scenario_loop import grounded_fix

        wave = WaveRecord(wave=2, stage="coverage_gap")
        planner = _StubPlanner(waves=[wave])
        risk = IdentifiedRisk(
            description="x", risk_category=RiskCategory.CONCURRENCY, severity=Priority.P0
        )
        common = dict(planner=planner, gaps=[risk], verdict=None, scenario=base_scenario())
        # The same inexpressible gap IS routed when the evaluator accepted…
        assert _verification_gap_decision(wave=wave, accepted=accept(), **common) is not None
        # …but never when it had no wave of its own to judge by…
        assert _verification_gap_decision(wave=None, accepted=accept(), **common) is None
        # …and never over a decision the evaluator did not make as an ACCEPT.
        decided = _verification_gap_decision(
            wave=wave,
            planner=planner,
            gaps=[risk],
            verdict=None,
            accepted=grounded_fix(),
            scenario=base_scenario(),
        )
        assert decided is None


# --------------------------------------------------------------------------
# 4. a valid hostile argument from the declared vocabulary still runs
# --------------------------------------------------------------------------


class TestAValidHostileArgumentStillRuns:
    def test_a_declared_fault_with_a_seed_is_accepted(self):
        scenario = runs(f"{MACHINE} --case a-lost-update-is-refused --fault duplicate-delivery --seed 7")
        assert grammar().problems(scenario) == []
        assert validate_scenario(scenario, context()) == []

    def test_a_case_the_vocabulary_does_not_close_is_left_to_the_program(self):
        """``--case`` is not proven closed, so a case a builder adds is usable."""
        scenario = runs(f"{MACHINE} --case a-case-added-since --fault drop-ack")
        assert grammar().problems(scenario) == []

    def test_a_new_negative_control_asserting_the_refusal_is_accepted(self):
        scenario = runs(
            f"{MACHINE} --case happy-path-completes --fault also-not-a-member",
            exit_code=2,
            category=RiskCategory.MALFORMED_INPUT,
            contains=["unknown fault"],
        )
        assert grammar().problems(scenario) == []

    def test_a_state_check_reading_the_refusal_it_asserts_is_accepted(self):
        scenario = make_scenario(
            risk_category=RiskCategory.MALFORMED_INPUT,
            state_checks=[
                GeneratedStateCheck(command=INVENTED_FAULT_CONTROL, contains=["unknown fault"])
            ],
        )
        assert grammar().problems(scenario) == []

    async def test_it_executes_normally_in_the_loop(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = planner_for(store, [first_wave(), valid_gap_wave()], config=config)
        log: list[str] = []

        result = await drive(config, store, state, planner, log=log)

        assert "gen-crash-duplicate" in log
        assert result.status is RunStatus.ACCEPTED
        assert result.gate is not None and result.gate.uncovered_risks == []


# --------------------------------------------------------------------------
# 5. a resumed run retires what it persisted, and deletes nothing
# --------------------------------------------------------------------------


class TestAResumedRunRetiresImpossibleScenarios:
    def _persist_plan(self, store: EvidenceStore) -> tuple[Path, bytes]:
        impossible = runs(
            NO_SUCH_TRANSITION_CONTROL,
            category=RiskCategory("unexpected_state_transition"),
            scenario_id="gen-hold-expiry",
        )
        valid = runs(
            f"{MACHINE} --case a-lost-update-is-refused --fault drop-ack",
            scenario_id="gen-lost-update",
        )
        plan = GeneratedScenarioPlan(
            run_id=store.run_id,
            task="build the machine",
            scenarios=[impossible, valid],
            executed_scenario_ids=[impossible.id, valid.id],
            waves=[
                WaveRecord(
                    wave=1, stage="initial", accepted_ids=[impossible.id, valid.id]
                )
            ],
        )
        (store.run_dir / "scenario-plan.json").write_text(
            plan.model_dump_json(indent=2), encoding="utf-8"
        )
        # The immutable record of the earlier execution.
        history = store.run_dir / "iteration-01" / "suite-result.json"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(
            json.dumps({"outcomes": [{"scenario_id": impossible.id, "outcome": "FAILED"}]}),
            encoding="utf-8",
        )
        return history, history.read_bytes()

    def test_the_active_plan_is_rebuilt_without_deleting_or_replaying(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "20260911-000002")
        history, recorded = self._persist_plan(store)
        asked: list[str] = []
        planner = planner_for(store, [], asked=asked)

        planner.restore_from_store()

        retired = planner.plan.by_id("gen-hold-expiry")
        # The obligation is not deleted: the scenario is still the plan's record.
        assert retired is not None
        assert "harness-generation defect" in retired.retired_reason
        # And it is not replayed: not compiled, not asked, not in the suite.
        assert "gen-hold-expiry" not in planner.compiled
        assert "gen-lost-update" in planner.compiled
        assert not any("expire-hold" in command for command in asked)
        suite = _assemble_suite(base_scenario(), planner)
        ids = [entry.scenario_id for entry in suite.entries]
        assert "gen-hold-expiry" not in ids and "gen-lost-update" in ids
        # Converted into an explicit uncovered verification obligation.
        [carried] = [
            r for r in planner.plan.risks if r.risk_category is RiskCategory("unexpected_state_transition")
        ]
        assert carried in planner.plan.planned_gaps()
        verdict = evaluate_gate(passing_suite(["gen-lost-update"]), risks=planner.plan.risks,
                                generation_problems=planner.generation_problems())
        assert verdict.blocks_acceptance
        assert [g.risk_category for g in verdict.uncovered_risks] == ["unexpected_state_transition"]
        # Not a generation problem that would block this run forever.
        assert planner.generation_problems() == []
        # History is untouched; the retirement is durable in the plan on disk.
        assert history.read_bytes() == recorded
        on_disk = json.loads((store.run_dir / "scenario-plan.json").read_text(encoding="utf-8"))
        by_id = {s["id"]: s for s in on_disk["scenarios"]}
        assert by_id["gen-hold-expiry"]["retired_reason"]
        resume_waves = [w for w in on_disk["waves"] if w["stage"] == "resume"]
        assert [r["kind"] for r in resume_waves[-1]["rejected"]] == [REJECTED_INVOCATION]

    def test_a_second_resume_changes_nothing(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "20260911-000003")
        self._persist_plan(store)
        planner_for(store, []).restore_from_store()
        first = (store.run_dir / "scenario-plan.json").read_text(encoding="utf-8")

        again = planner_for(store, [])
        again.restore_from_store()

        assert (store.run_dir / "scenario-plan.json").read_text(encoding="utf-8") == first
        assert "gen-hold-expiry" not in again.compiled
        assert len([r for r in again.plan.risks if r.id.endswith(":obligation")]) == 1


class TestRun20260911081124ResumesSafely:
    """The persisted plan of the run itself, against this repository's scenario files."""

    IMPOSSIBLE = {
        "P7-S04-owner-asserted-not-recomputed",
        "P7-S05-content-addressed-immutable-evidence",
        "P7-S07-conflict-first-class-blocking",
        "P7-S08-conflict-never-expires",
        "P7-S09-correction-supersession-retained",
        "P7-S11-deterministic-linker-binding",
    }
    VALID = {
        "P7-S01-six-provenance-classes",
        "P7-S02-model-inferred-not-gated",
        "P7-S03-no-provenance-laundering",
        "P7-S10-tenant-safety-kb-closure",
    }

    @pytest.fixture
    def restored(self, tmp_path):
        fixture = json.loads(RUN_FIXTURE.read_text(encoding="utf-8"))
        fixture.pop("_provenance")
        store = EvidenceStore(tmp_path / "runs", "20260911-081124")
        (store.run_dir / "scenario-plan.json").write_text(json.dumps(fixture), encoding="utf-8")
        scenarios = [load_scenario(p) for p in sorted((DRIVER_ROOT / "scenarios").glob("*.y*ml"))]
        base = next(s for s in scenarios if s.name == "p6_m13_brake")
        asked: list[str] = []

        def probe(command: str) -> ContractProbeResult:
            asked.append(command)
            return ContractProbeResult(False, detail="this test runs nothing")

        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner([]),
            store=store,
            base_scenario=base,
            permanent_scenarios=scenarios,
            founder=FakeFounder(),
            contract_probe=probe,
        )
        planner.restore_from_store()
        return planner, asked

    def test_exactly_the_six_impossible_scenarios_are_retired(self, restored):
        planner, asked = restored
        retired = {s.id for s in planner.plan.scenarios if s.retired_reason}
        assert retired == self.IMPOSSIBLE
        assert set(planner.compiled) == self.VALID
        assert asked == []
        for scenario in planner.plan.scenarios:
            if scenario.id in self.IMPOSSIBLE:
                assert "reviewed to exit 2" in scenario.retired_reason

    def test_nothing_is_deleted_and_nothing_blocks_forever(self, restored):
        planner, _asked = restored
        assert {s.id for s in planner.plan.scenarios} == self.IMPOSSIBLE | self.VALID
        assert planner.generation_problems() == []
        assert planner.unbuildable_scenarios == {}
        # The P0 conflicting-evidence risks those scenarios were for are still gaps.
        gaps = {r.risk_category.value for r in planner.plan.planned_gaps()}
        assert "conflicting_evidence" in gaps


# --------------------------------------------------------------------------
# 6. a technically approved but irrelevant probe is refused for the risk
# --------------------------------------------------------------------------


class TestRiskToProbeSelection:
    def test_a_refusal_cannot_establish_a_risk_it_never_reaches(self):
        """Exit code right, command approved, and still no evidence for the risk."""
        scenario = runs(
            INVENTED_FAULT_CONTROL,
            exit_code=2,
            category=RiskCategory.CONFLICTING_EVIDENCE,
            contains=["unknown fault"],
        )
        assert context().approved_commands.approves(INVENTED_FAULT_CONTROL) == (True, "")
        assert kinds(scenario) == [IRRELEVANT_REFUSAL]
        assert any(
            "cannot establish a 'conflicting_evidence' risk" in r
            for r in validate_scenario(scenario, context())
        )

    def test_the_same_refusal_establishes_what_the_repository_says_it_does(self):
        scenario = runs(
            INVENTED_FAULT_CONTROL,
            exit_code=2,
            category=RiskCategory.MALFORMED_INPUT,
            contains=["unknown fault"],
        )
        assert grammar().problems(scenario) == []

    def test_the_brief_says_which_approved_commands_are_refusals(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "20260911-000004")
        planner = planner_for(store, [{"risks": [], "scenarios": []}])

        planner.plan_initial(task="build the machine")

        rendered = planner.reasoner.briefs[0].render()
        line = next(
            index
            for index, text in enumerate(rendered.splitlines())
            if text.strip().endswith(INVENTED_FAULT_CONTROL)
        )
        assert "REFUSAL CONTROL" in rendered.splitlines()[line + 1]
        assert f"{MACHINE} --fault <value>: CLOSED" in rendered


# --------------------------------------------------------------------------
# 7. nothing in the fix names the product, the phase or the run
# --------------------------------------------------------------------------


class TestNothingHereIsSpecificToOneRun:
    FORBIDDEN = (
        "neyma", "p7", "p6", "probe_phase", "--inject", "--case", "--list", "inject",
        "auto-resolve", "expire-", "not-a-real-fault", "20260911", "identity_binding",
        "owner_asserted",
    )

    def test_the_grammar_module_names_no_program_option_fault_or_phase(self):
        source = inspect.getsource(invocation_grammar).lower().replace("neyma_product_driver", "")
        for token in self.FORBIDDEN:
            assert token not in source, token

    def test_the_routing_and_carrying_code_names_none_either(self):
        source = (
            inspect.getsource(_verification_gap_decision)
            + inspect.getsource(ScenarioPlanner._carry_obligation)
            + inspect.getsource(invocation_reasons)
        ).lower().replace("neyma_product_driver", "")
        for token in self.FORBIDDEN:
            assert token not in source, token

    def test_no_core_module_carries_the_incidents_vocabulary(self):
        package = Path(cli_module.__file__).resolve().parent
        incident = (
            "auto-resolve-conflict", "expire-claim", "expire-conflict", "not-a-real-fault",
            "probe_phase6_identity_binding_claim", "probe_phase6_conflict", "P7-S0",
        )
        for module in sorted(package.glob("*.py")):
            text = module.read_text(encoding="utf-8")
            for token in incident:
                assert token not in text, f"{module.name} names {token!r}"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


class _StubPlanner:
    def __init__(self, waves: list[WaveRecord]) -> None:
        self.plan = GeneratedScenarioPlan(waves=waves)

    def budget_exhausted(self) -> bool:
        return False


def planner_for(
    store: EvidenceStore,
    payloads: list,
    *,
    asked: list[str] | None = None,
    config: Any = None,
) -> ScenarioPlanner:
    """A planner over the synthetic program, whose contract probe runs nothing."""
    recorded = asked if asked is not None else []

    def probe(command: str) -> ContractProbeResult:
        recorded.append(command)
        return ContractProbeResult(False, detail="this test runs nothing")

    generation = ScenarioGenerationConfig(enabled=True)
    if config is not None:
        config.scenario_generation = generation
    return ScenarioPlanner(
        repo=config.neyma_repo if config is not None else store.run_dir,
        config=generation,
        reasoner=ScriptedReasoner(list(payloads)),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=permanent(),
        founder=FakeFounder(),
        contract_probe=probe,
    )


def machine_raw(
    scenario_id: str,
    command: str,
    *,
    category: str = "concurrency",
    exit_code: int = 0,
    contains: list[str] | None = None,
    **overrides: Any,
) -> dict:
    return raw_scenario(
        scenario_id,
        risk_category=category,
        actions=[
            {
                "kind": "command",
                "name": "drive the machine",
                "command": command,
                "expect_exit_code": exit_code,
                "expect_contains": list(contains or []),
            }
        ],
        **overrides,
    )


def impossible_gap_wave() -> dict:
    """The only coverage the generator can find for the gap is an invented fault."""
    return {
        "risks": [],
        "scenarios": [
            machine_raw(
                "gen-crash-impossible",
                f"{MACHINE} --case happy-path-completes --fault crash-mid-adapter",
                category="crash_mid_workflow",
                source_risks=[risk_key(CRASH_RISK, RiskCategory.CRASH_MID_WORKFLOW)],
                cleanup=[APPROVED_CLEANUP],
            )
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


def valid_gap_wave() -> dict:
    """The gap closed with a fault the program's vocabulary declares."""
    return {
        "risks": [],
        "scenarios": [
            machine_raw(
                "gen-crash-duplicate",
                f"{MACHINE} --case a-lost-update-is-refused --fault duplicate-delivery --seed 7",
                category="crash_mid_workflow",
                source_risks=[risk_key(CRASH_RISK, RiskCategory.CRASH_MID_WORKFLOW)],
                cleanup=[APPROVED_CLEANUP],
            )
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


def passing_suite(ids: list[str]) -> SuiteResult:
    outcomes = [
        ScenarioOutcome(
            scenario_id=scenario_id,
            scenario_name=scenario_id,
            origin=Origin.GENERATED,
            outcome=Outcome.PASSED,
            priority=Priority.P0,
            risk_category="concurrency",
            required=True,
            evidence_verified=True,
            evidence_path=f"/runs/x/{scenario_id}",
        )
        for scenario_id in ids
    ]
    return SuiteResult(
        outcomes=outcomes,
        expected_required_ids=list(ids),
        full_run=True,
    )


@pytest.fixture
def loop_bits(driver_config):
    assert driver_config.runs_dir is not None
    store = EvidenceStore(driver_config.runs_dir, "20260911-000009")
    state = RunState(
        run_id=store.run_id,
        task="build supervised approval",
        max_iterations=driver_config.max_iterations,
    )
    return driver_config, store, state

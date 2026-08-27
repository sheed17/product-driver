"""A generated scenario may not assert output no command it runs can emit.

Run ``20260827-063257`` verified P6/M7 in every way that matters — the permanent
scenario passed, the probe reported ``behaviours as specified, 0 wrong``, 49
tests passed, 16/16 mutants were caught, every regression anchor was green — and
did not reach VERIFIED, because one generated scenario, ``S3``, could not pass
against a correct product.

``S3`` ran::

    probe_phase6_conflict.py --case second-detection-attaches-a-party-not-a-new-conflict ...

which prints ``A SECOND DETECTION ATTACHES A PARTY, NEVER A SECOND CONFLICT`` and
exits 0. Its action asserted exactly that. Then its scenario-level
``expected_observations`` asserted a *second* sentence —
``CONCURRENT DETECTORS PRODUCE ONE CONFLICT AND LOSE NO PARTY`` — which belongs
to a different ``--case`` of the same program and which the case actually
selected does not print, by design. The assertion was unsatisfiable against a
perfectly correct M7, and it arrived at the gate looking like a product defect.

The general defect is not those two strings. It is that
``expected_observations`` is the one oracle in a generated scenario that names no
command: it is matched against everything the run produced, so nothing had to be
able to emit it. The permanent side has refused this shape at load time since
``Scenario._claims_name_a_check_that_can_emit_them`` — *"a claim that names
commands A and B while the literal it requires is emitted by C can never be
established, no matter how correct the product is… it reads on the gate as a
product defect rather than as the mapping error it is."* This file pins the same
rule one layer over, on the side a model writes.

Nothing here consumes Claude usage.
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest

from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
from neyma_product_driver.scenario_gate import (
    BASIS_DECLARED,
    GateStatus,
    evaluate_gate,
)
from neyma_product_driver.scenario_generator import GENERATOR_SYSTEM, PLAN_SCHEMA
from neyma_product_driver.scenario_plan import (
    REJECTED_CONTRACT,
    REJECTED_FILTERED,
    GeneratedScenario,
    IdentifiedRisk,
    Priority,
    RiskCategory,
    ScenarioProvenance,
    compile_to_scenario,
)
from neyma_product_driver.scenario_planner import STAGE_COVERAGE_GAP, ScenarioPlanner
from neyma_product_driver.scenario_suite import (
    Origin,
    Outcome,
    RiskEvidence,
    ScenarioOutcome,
    SuiteResult,
)
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    ValidationContext,
    established_observations_from,
    unattributed_observations,
    validate_scenario,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor, load_scenario

from scenario_fixtures import FakeUnit, ScriptedReasoner


class FakeFounder:
    """The founder rubric as the M7 run had it: ``known_vs_inferred`` is real."""

    version = "founder-m7"
    rubric = {
        "categories": [
            {"id": "known_vs_inferred", "description": "what is known and what is guessed"},
            {"id": "effect-truth", "description": "a 200 is not success"},
        ],
        "never_acceptable": [{"id": "silent-data-loss", "description": "..."}],
    }

DRIVER_ROOT = Path(__file__).resolve().parents[1]
M7_PATH = DRIVER_ROOT / "scenarios" / "p6_m7_conflict.yaml"

#: The two sentences, and the two cases they belong to. Read here only so the
#: test can *say* what happened in run 20260827-063257; the rule under test
#: derives both from `scenarios/p6_m7_conflict.yaml` at runtime and this module
#: is the only place in the repository that names either of them.
S3_OWN_LITERAL = "A SECOND DETECTION ATTACHES A PARTY, NEVER A SECOND CONFLICT"
S3_FOREIGN_LITERAL = "CONCURRENT DETECTORS PRODUCE ONE CONFLICT AND LOSE NO PARTY"
S3_COMMAND = (
    ".venv/bin/python scripts/probe_phase6_conflict.py "
    "--case second-detection-attaches-a-party-not-a-new-conflict "
    "--inject concurrent-detection --concurrency 8 --delay-ms 40 --parties 8 --seed 17"
)
S3_STATE_ORACLE = (
    '.venv/bin/python -c "import sqlite3,tempfile,os,sys; sys.path.insert(0,\'src\'); '
    "from freight_recon.schema import create_canonical_schema; "
    "p=os.path.join(tempfile.mkdtemp(),'probe.sqlite3'); c=sqlite3.connect(p); "
    "create_canonical_schema(c); rows=[r[0] for r in c.execute( \\\"select sql from "
    "sqlite_master where type='index' and tbl_name='conflicts' and sql is not null\\\")]; "
    "key=[s for s in rows if 'UNIQUE' in s and 'entity_ref' in s and 'field' in s and "
    "'tenant' in s and 'WHERE' in s.upper()]; print('one-open-conflict index:', "
    "' '.join(key[0].split()) if key else 'MISSING')\""
)


def s3_payload(**overrides: Any) -> dict[str, Any]:
    """``S3`` exactly as run 20260827-063257 recorded it, before execution."""
    payload: dict[str, Any] = {
        "id": "S3",
        "title": "Eight disagreeing sources fan in to one open Conflict, losing no party",
        "purpose": (
            "Race concurrent detectors on one (tenant, entity_ref, field) with a large "
            "party fan-in and prove they coalesce into exactly one open Conflict with "
            "every party attached."
        ),
        "risk_category": "boundary",
        "priority": "P1",
        "rationale": "R2's fan-in boundary: the partial unique index and CF-7 append.",
        "requirement_reference": "AC-MACH-701",
        "product_principle_reference": "known_vs_inferred",
        "mode": "backend",
        "service_refs": [],
        "actions": [
            {
                "kind": "command",
                "name": "probe-concurrent-fan-in",
                "command": S3_COMMAND,
                "expect_exit_code": 0,
                "expect_contains": [S3_OWN_LITERAL],
            }
        ],
        "persisted_state_checks": [
            {
                "name": "partial unique index enforces at most one open conflict",
                "command": S3_STATE_ORACLE,
                "contains": ["one-open-conflict index:", "UNIQUE", "WHERE"],
                "not_contains": ["MISSING"],
            }
        ],
        "expected_observations": [S3_OWN_LITERAL, S3_FOREIGN_LITERAL],
        "forbidden_observations": ["### PARTY LOST ###"],
        "isolation_note": "the probe builds its own temporary database per run",
        "isolation_key": "phase6_conflict_probe",
        "generating_risk": "the machine could spill into a second open Conflict",
        "confidence": 0.5,
    }
    payload.update(overrides)
    return payload


def _provenance() -> ScenarioProvenance:
    return ScenarioProvenance(
        stage="initial",
        wave=3,
        task_hash="m7-task",
        model="opus",
        session_id="scripted",
        generating_risk="the machine could spill into a second open Conflict",
    )


def s3_scenario(**overrides: Any) -> GeneratedScenario:
    data = s3_payload(**overrides)
    data["provenance"] = _provenance()
    data.pop("generating_risk", None)
    return GeneratedScenario.model_validate(data)


def m7_context(**overrides: Any) -> ValidationContext:
    """Validation as the M7 run had it: the shipped scenario file, and nothing else."""
    m7 = load_scenario(M7_PATH)
    defaults: dict[str, Any] = {
        "approved_commands": ApprovedCommands.from_sources(scenarios=[m7]),
        "established_observations": established_observations_from([m7]),
        "grounding_tokens": {"p6/m7", "p6", "m7", "ac-mach-701"},
        "principle_tokens": {"known_vs_inferred"},
    }
    defaults.update(overrides)
    return ValidationContext(**defaults)


# ==========================================================================
# 1 — the map is derived from the repository, never duplicated into the driver
# ==========================================================================


class TestTheLiteralMapIsDerived:
    def test_it_is_read_out_of_the_human_authored_scenario_file(self) -> None:
        established = established_observations_from([load_scenario(M7_PATH)])
        assert established, "the shipped M7 scenario binds literals to commands"
        # Every key is a command string the file itself contains.
        text = M7_PATH.read_text(encoding="utf-8")
        for command in established:
            head = command.split()[0]
            assert head in text

    def test_an_invocation_is_the_key_and_a_narrowing_is_a_different_one(self) -> None:
        """The whole discrimination: a tail is the model's composition, not a human's."""
        established = established_observations_from([load_scenario(M7_PATH)])
        bare = ".venv/bin/python scripts/probe_phase6_conflict.py"
        assert bare in established
        assert f"{bare} --case second-detection-attaches-a-party-not-a-new-conflict" not in established

    def test_a_multi_check_claim_attributes_nothing_to_one_command(self) -> None:
        """Its observations are matched against the checks' CONCATENATED output.

        Binding them to each named check individually says "this command prints
        that" on evidence that says only "these together print that" — and it is
        enough on its own to let the S3 shape through, because the M7
        ``concurrency`` claim names both the probe and the index introspection.
        """
        established = established_observations_from([load_scenario(M7_PATH)])
        producers = [c for c, lits in established.items() if S3_FOREIGN_LITERAL in lits]
        assert producers == []

    def test_the_driver_source_does_not_contain_either_sentence(self) -> None:
        """No product string is duplicated into Product Driver to make this work."""
        for path in (DRIVER_ROOT / "neyma_product_driver").glob("*.py"):
            body = path.read_text(encoding="utf-8")
            assert S3_OWN_LITERAL not in body
            assert S3_FOREIGN_LITERAL not in body


# ==========================================================================
# 2 — the general rule: case A's command may not carry case B's literal
# ==========================================================================


def two_case_scenario() -> Scenario:
    """A human-authored file that binds each selection's sentence to that selection."""
    return Scenario(
        name="two_case_tool",
        mode="backend",
        commands=[
            {
                "name": "the whole battery",
                "run": "./tool",
                "expect_exit_code": 0,
                "expect_contains": ["ALPHA HELD", "BETA HELD"],
            },
            {
                "name": "case alpha",
                "run": "./tool --case alpha",
                "expect_contains": ["ALPHA HELD"],
            },
            {
                "name": "case beta",
                "run": "./tool --case beta",
                "expect_contains": ["BETA HELD"],
            },
        ],
        verifies=[
            {
                "risk_category": "boundary",
                "claim": "the whole battery narrates both selections",
                "checks": ["the whole battery"],
                "observations": ["ALPHA HELD", "BETA HELD"],
            }
        ],
    )


def two_case_context(**overrides: Any) -> ValidationContext:
    base = two_case_scenario()
    defaults: dict[str, Any] = {
        "approved_commands": ApprovedCommands.from_sources(scenarios=[base]),
        "established_observations": established_observations_from([base]),
        "grounding_tokens": {"u-042"},
        "principle_tokens": {"effect-truth"},
    }
    defaults.update(overrides)
    return ValidationContext(**defaults)


def tool_scenario(*, command: str, expect: list[str], observations: list[str]) -> GeneratedScenario:
    return GeneratedScenario.model_validate(
        {
            "id": "gen-tool",
            "title": "drive one selection of the tool",
            "purpose": "exercise one selection and observe what it narrates",
            "risk_category": "boundary",
            "priority": "P1",
            "requirement_reference": "U-042",
            "product_principle_reference": "effect-truth",
            "actions": [
                {
                    "kind": "command",
                    "name": "run it",
                    "command": command,
                    "expect_contains": list(expect),
                }
            ],
            "expected_observations": list(observations),
            "isolation_note": "the tool touches nothing shared",
            "provenance": _provenance(),
        }
    )


class TestACommandMayNotCarryAnotherSelectionsLiteral:
    def test_the_matching_selection_is_accepted(self) -> None:
        scenario = tool_scenario(
            command="./tool --case alpha",
            expect=["ALPHA HELD"],
            observations=["ALPHA HELD"],
        )
        assert validate_scenario(scenario, two_case_context()) == []

    def test_the_other_selections_literal_is_refused(self) -> None:
        scenario = tool_scenario(
            command="./tool --case alpha",
            expect=["ALPHA HELD"],
            observations=["ALPHA HELD", "BETA HELD"],
        )
        reasons = validate_scenario(scenario, two_case_context())
        assert any("BETA HELD" in r for r in reasons)
        assert not any("ALPHA HELD'," in r for r in reasons)

    def test_the_refusal_names_where_the_literal_does_come_from(self) -> None:
        scenario = tool_scenario(
            command="./tool --case alpha",
            expect=["ALPHA HELD"],
            observations=["BETA HELD"],
        )
        reasons = validate_scenario(scenario, two_case_context())
        assert any("./tool --case beta" in r for r in reasons)

    def test_running_the_unselected_command_restores_the_basis(self) -> None:
        """A scenario that runs the invocation a human bound the literal to may assert it."""
        scenario = tool_scenario(
            command="./tool",
            expect=[],
            observations=["ALPHA HELD", "BETA HELD"],
        )
        assert validate_scenario(scenario, two_case_context()) == []

    def test_the_human_established_basis_is_per_invocation(self) -> None:
        """``./tool --case alpha`` is itself an invocation a human wrote down.

        So a scenario running it verbatim may assert what that invocation is said
        to print — and only that. The other selection's sentence is still
        refused, from the same command, in the same scenario.
        """
        scenario = tool_scenario(
            command="./tool --case alpha",
            expect=[],
            observations=["ALPHA HELD", "BETA HELD"],
        )
        refused = [
            r for r in validate_scenario(scenario, two_case_context())
            if "expected_observations requires" in r
        ]
        assert len(refused) == 1
        assert "BETA HELD" in refused[0]

    def test_a_forbidden_observation_needs_no_producer(self) -> None:
        """An absence is not a claim about what any command emits."""
        scenario = tool_scenario(
            command="./tool --case alpha",
            expect=["ALPHA HELD"],
            observations=["ALPHA HELD"],
        )
        scenario = scenario.model_copy(
            update={"forbidden_observations": ["### ALPHA AND BETA COLLIDED ###"]}
        )
        assert validate_scenario(scenario, two_case_context()) == []

    def test_an_unknown_literal_is_left_alone(self) -> None:
        """No producer anywhere, but the scenario names the command that prints it.

        The rule refuses an assertion with no operation behind it. It does not
        require the harness to have heard the sentence before — that would be a
        second, unrelated boundary, and would refuse every scenario exercising
        output no permanent file happens to quote.
        """
        scenario = tool_scenario(
            command="./tool --case alpha",
            expect=["A SENTENCE NO PERMANENT FILE QUOTES"],
            observations=["A SENTENCE NO PERMANENT FILE QUOTES"],
        )
        assert validate_scenario(scenario, two_case_context()) == []


# ==========================================================================
# 3 — the exact M7 S3 shape, refused before it can be blamed on Neyma
# ==========================================================================


class TestTheM7S3Shape:
    def test_s3_as_generated_is_refused(self) -> None:
        reasons = validate_scenario(s3_scenario(), m7_context())
        assert reasons, "S3 must not reach execution"
        assert any(S3_FOREIGN_LITERAL in r for r in reasons)

    def test_the_refusal_says_it_is_a_generation_contract_error(self) -> None:
        reasons = validate_scenario(s3_scenario(), m7_context())
        blamed = [r for r in reasons if S3_FOREIGN_LITERAL in r]
        assert blamed
        assert "generation contract error" in blamed[0]
        assert "not a statement about the product" in blamed[0]

    def test_the_case_s3_actually_selected_is_untouched(self) -> None:
        """The literal S3 *was* entitled to is not what is refused."""
        bad = unattributed_observations(s3_scenario(), m7_context())
        assert [literal for literal, _ in bad] == [S3_FOREIGN_LITERAL]

    def test_s3_without_the_foreign_literal_validates(self) -> None:
        """The situation S3 meant to exercise is not weakened, only its oracle."""
        coherent = s3_scenario(expected_observations=[S3_OWN_LITERAL])
        assert validate_scenario(coherent, m7_context()) == []

    def test_a_wave_carrying_s3_refuses_it_and_admits_nothing(self, tmp_path) -> None:
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner([{"risks": [], "scenarios": [s3_payload()]}]),
            base_scenario=load_scenario(M7_PATH),
            permanent_scenarios=[load_scenario(M7_PATH)],
            founder=FakeFounder(),
        )
        plan = planner.plan_initial(task="build P6/M7", unit=FakeUnit("P6/M7"))
        assert plan.scenarios == []
        wave = plan.waves[0]
        assert wave.proposed == 1
        assert wave.accepted_ids == []
        assert any(S3_FOREIGN_LITERAL in r for rej in wave.rejected for r in rej.reasons)

    def test_it_never_becomes_a_compiled_scenario(self, tmp_path) -> None:
        """Refused at validation means the executor never sees it — nothing runs."""
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner([{"risks": [], "scenarios": [s3_payload()]}]),
            base_scenario=load_scenario(M7_PATH),
            permanent_scenarios=[load_scenario(M7_PATH)],
            founder=FakeFounder(),
        )
        planner.plan_initial(task="build P6/M7", unit=FakeUnit("P6/M7"))
        assert planner.compiled == {}


# ==========================================================================
# 4 — a coherent scenario still runs, and a real failure is still the product's
# ==========================================================================


def _execute(scenario: Scenario, tmp_path: Path):
    executor = ScenarioExecutor(
        repo=tmp_path,
        run_config=ScenarioRunConfig(command_timeout_s=30),
        artifact_dir=tmp_path / "artifacts",
    )
    return asyncio.run(executor.execute(scenario))


def echo_context(**overrides: Any) -> ValidationContext:
    base = Scenario(
        name="echo_base",
        mode="backend",
        commands=[{"name": "narrate", "run": "echo"}],
    )
    defaults: dict[str, Any] = {
        "approved_commands": ApprovedCommands.from_sources(scenarios=[base]),
        "established_observations": established_observations_from([base]),
        "grounding_tokens": {"u-042"},
        "principle_tokens": {"effect-truth"},
    }
    defaults.update(overrides)
    return ValidationContext(**defaults)


def echo_scenario(printed: str, asserted: str) -> GeneratedScenario:
    return GeneratedScenario.model_validate(
        {
            "id": "gen-echo",
            "title": "the product narrates what it did",
            "purpose": "observe the sentence the product prints when it holds",
            "risk_category": "boundary",
            "priority": "P1",
            "requirement_reference": "U-042",
            "product_principle_reference": "effect-truth",
            "actions": [
                {
                    "kind": "command",
                    "name": "narrate",
                    "command": f"echo {printed}",
                    "expect_contains": [asserted],
                }
            ],
            "expected_observations": [asserted],
            "isolation_note": "echo touches nothing",
            "provenance": _provenance(),
        }
    )


def _compile(generated: GeneratedScenario) -> Scenario:
    """Compile with exactly the command set validation approved."""
    return compile_to_scenario(
        generated,
        base=None,
        approved_commands=set(generated.command_strings()),
    )


class TestCoherentScenariosStillExecute:
    def test_a_bound_literal_validates_compiles_and_passes(self, tmp_path) -> None:
        generated = echo_scenario("ALPHA-HELD", "ALPHA-HELD")
        assert validate_scenario(generated, echo_context()) == []
        result = _execute(_compile(generated), tmp_path)
        assert result.error is None
        assert [a.passed for a in result.assertions if a.kind == "expect_visible"] == [
            True,
            True,
        ]

    def test_a_real_product_failure_is_still_a_scenario_failure(self, tmp_path) -> None:
        """The rule must not launder a genuine mismatch into a harness problem.

        Here the oracle is properly attributed — the scenario says which command
        prints the sentence — and the command does not print it. That is a
        product/scenario failure and it must stay one.
        """
        generated = echo_scenario("SOMETHING-ELSE", "ALPHA-HELD")
        assert validate_scenario(generated, echo_context()) == []
        result = _execute(_compile(generated), tmp_path)
        failed = [a for a in result.assertions if not a.passed]
        assert failed, "a literal the command does not print must fail the scenario"


# ==========================================================================
# 5 — accounting, and what may and may not accept
# ==========================================================================


def _passing_permanent(risk_evidence: list[RiskEvidence] | None = None) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id="p6_m7_conflict",
        scenario_name="p6_m7_conflict",
        origin=Origin.PERMANENT,
        outcome=Outcome.PASSED,
        priority=Priority.P0,
        required=True,
        evidence_path="/runs/x/scenarios/p6_m7_conflict",
        evidence_verified=True,
        risk_evidence=list(risk_evidence or []),
    )


def _suite(*outcomes: ScenarioOutcome) -> SuiteResult:
    return SuiteResult(
        full_run=True,
        expected_required_ids=[o.scenario_id for o in outcomes],
        outcomes=list(outcomes),
    )


class TestAccountingAndAcceptance:
    def _wave(self, tmp_path, payload):
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner([payload]),
            base_scenario=load_scenario(M7_PATH),
            permanent_scenarios=[load_scenario(M7_PATH)],
            founder=FakeFounder(),
        )
        plan = planner.plan_initial(task="build P6/M7", unit=FakeUnit("P6/M7"))
        return planner, plan.waves[0]

    def test_an_oracle_refusal_is_filtered_not_invalid(self, tmp_path) -> None:
        """Which STAGE refused it is what classifies it, and validation is not parse.

        Product Driver read this candidate, modelled it, knows the risk it named
        and refused it on the merits. Calling that "invalid — could not read it"
        would misstate the fact and would make a recoverable generator slip block
        the run terminally, taking the coverage-gap closure with it.
        """
        _, wave = self._wave(tmp_path, {"risks": [], "scenarios": [s3_payload()]})
        assert [r.kind for r in wave.rejected] == [REJECTED_FILTERED]
        assert wave.contract_rejections == []

    def test_all_four_counts_stay_separable(self, tmp_path) -> None:
        planner, wave = self._wave(
            tmp_path,
            {
                "risks": [],
                "scenarios": [
                    s3_payload(),
                    s3_payload(id="S3-coherent", expected_observations=[S3_OWN_LITERAL]),
                    s3_payload(id="S3-unreadable", risk_category="party-fan-in-spill"),
                ],
            },
        )
        assert wave.proposed == 3
        assert wave.accepted_ids == ["S3-coherent"]
        assert len(wave.filtered_rejections) == 1
        assert len(wave.contract_rejections) == 1
        assert wave.accounting() == (
            "3 proposed, 1 accepted for execution, 1 filtered or deduplicated, "
            "1 invalid (Product Driver could not read them)"
        )

    def test_p6_d46_is_still_closed(self, tmp_path) -> None:
        """An unreadable candidate is still a contract failure and still blocks."""
        planner, wave = self._wave(
            tmp_path,
            {"risks": [], "scenarios": [s3_payload(risk_category="party-fan-in-spill")]},
        )
        assert [r.kind for r in wave.rejected] == [REJECTED_CONTRACT]
        problems = planner.generation_problems()
        assert problems
        verdict = evaluate_gate(
            _suite(_passing_permanent()), generation_problems=problems
        )
        assert verdict.status is GateStatus.NOT_VERIFIED

    def test_a_generation_contract_failure_cannot_accept(self, tmp_path) -> None:
        planner, _ = self._wave(
            tmp_path,
            {"risks": [], "scenarios": [s3_payload(risk_category="party-fan-in-spill")]},
        )
        assert (
            evaluate_gate(
                _suite(_passing_permanent()),
                generation_problems=planner.generation_problems(),
            ).status
            is GateStatus.NOT_VERIFIED
        )

    def test_refusing_the_only_coverage_for_a_risk_cannot_accept(self) -> None:
        """Refusal is not a shortcut to green: the risk register still governs.

        S3 was the only proposal for its risk in this shape. Refusing it does not
        make the risk covered — the gate reports it uncovered from the execution
        records, exactly as if it had never been proposed.
        """
        risk = IdentifiedRisk(
            id="R2",
            description="the fan-in boundary could spill into a second open Conflict",
            risk_category=RiskCategory.BOUNDARY,
            severity=Priority.P1,
        )
        verdict = evaluate_gate(_suite(_passing_permanent()), risks=[risk])
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert [g.risk_category for g in verdict.uncovered_risks] == ["boundary"]


# ==========================================================================
# 6 — which risks pre-existing executed evidence may close, and which it may not
# ==========================================================================


class TestExistingEvidenceClosingAnIdentifiedRisk:
    """``BASIS_DECLARED``: a reviewed ``verifies:`` claim, resolved against execution.

    The architecture already answers this and the answer is yes — with an exact
    mapping, never a similarity. What it does not permit is closing a risk the
    reviewed file deliberately declined to declare an oracle for.
    """

    def _risk(self, category: RiskCategory) -> IdentifiedRisk:
        return IdentifiedRisk(
            id="R3",
            description="a readback contradicting the approved facts could be laundered",
            risk_category=category,
            severity=Priority.P0,
        )

    def _evidence(self, category: RiskCategory, *, established: bool) -> RiskEvidence:
        return RiskEvidence(
            risk_category=category.value,
            claim="disagreement becomes visible and blocking",
            scenario_name="p6_m7_conflict",
            checks=["drive the Conflict machine through a brokerage narrative"],
            observations=["A READBACK CONTRADICTING THE APPROVED FACTS IS A CONFLICT"],
            established=established,
            reason="" if established else "the literal never appeared",
        )

    def test_an_established_claim_closes_a_newly_identified_risk(self) -> None:
        category = RiskCategory.CONFLICTING_EVIDENCE
        verdict = evaluate_gate(
            _suite(_passing_permanent([self._evidence(category, established=True)])),
            risks=[self._risk(category)],
        )
        assert verdict.status is GateStatus.VERIFIED
        assert [c.basis for c in verdict.covered_risks] == [BASIS_DECLARED]
        assert verdict.covered_risks[0].scenario_id == "p6_m7_conflict"

    def test_a_declaration_that_did_not_hold_closes_nothing(self) -> None:
        category = RiskCategory.CONFLICTING_EVIDENCE
        verdict = evaluate_gate(
            _suite(_passing_permanent([self._evidence(category, established=False)])),
            risks=[self._risk(category)],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED

    def test_a_risk_with_no_declaration_is_not_closed_by_neighbouring_prose(self) -> None:
        """The permanent scenario narrates the readback. That is not a declaration."""
        verdict = evaluate_gate(
            _suite(
                _passing_permanent(
                    [self._evidence(RiskCategory.CONFLICTING_EVIDENCE, established=True)]
                )
            ),
            risks=[self._risk(RiskCategory.AMBIGUOUS_EXTERNAL_EFFECT)],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert [g.risk_category for g in verdict.uncovered_risks] == [
            "ambiguous_external_effect"
        ]

    def test_the_shipped_m7_file_declares_no_oracle_for_that_risk_on_purpose(self) -> None:
        """Its own words, and they are the authority the gate is reading.

        ``ambiguous_external_effect`` is about M3's behaviour, not M7's, and the
        reviewed file says so and declines to declare an oracle for it. So this
        risk cannot be closed by pre-existing evidence, and a run naming it must
        generate a case for it or block. That absence is load-bearing, and this
        test exists so a later edit cannot quietly wave it through.
        """
        scenario = load_scenario(M7_PATH)
        declared = {claim.risk_category for claim in scenario.verifies}
        assert "ambiguous_external_effect" not in declared
        assert "timeout_after_effect" not in declared
        body = M7_PATH.read_text(encoding="utf-8")
        assert "Nothing is declared for `ambiguous_external_effect`" in body


# ==========================================================================
# 7 — the closure wave is told what it needs to close the gap
# ==========================================================================


class TestCoverageGapClosureContext:
    def _planner(self, tmp_path, payloads):
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner(list(payloads)),
            base_scenario=load_scenario(M7_PATH),
            permanent_scenarios=[load_scenario(M7_PATH)],
            founder=FakeFounder(),
        )

    def _gap(self) -> IdentifiedRisk:
        return IdentifiedRisk(
            id="R3",
            description=(
                "A readback contradicting the approved facts could be laundered into an "
                "ordinary FAILED, or the M3 UNKNOWN_OUTCOME could be silently resolved."
            ),
            risk_category=RiskCategory.AMBIGUOUS_EXTERNAL_EFFECT,
            severity=Priority.P0,
        )

    def test_the_wave_is_aimed_at_the_exact_unresolved_risk(self, tmp_path) -> None:
        planner = self._planner(tmp_path, [{"risks": [], "scenarios": []}])
        gap = self._gap()
        planner.expand_after_failures(
            task="build P6/M7", unit=FakeUnit("P6/M7"), failures=[], gaps=[gap]
        )
        brief = planner.reasoner.briefs[-1]
        assert brief.stage == STAGE_COVERAGE_GAP
        line = "\n".join(brief.uncovered_risks)
        assert gap.key in line                      # the citation it must name
        assert "ambiguous_external_effect" in line  # the exact category
        assert "P0" in line                         # the severity, unlowered
        assert "laundered into an" in line          # the narrative, verbatim

    def test_the_wave_is_shown_the_command_vocabulary_and_prior_evidence(
        self, tmp_path
    ) -> None:
        planner = self._planner(tmp_path, [{"risks": [], "scenarios": []}])
        planner.plan.permanent_coverage = {
            "conflicting_evidence": ["p6_m7_conflict: a readback is not an ordinary failure"]
        }
        planner.expand_after_failures(
            task="build P6/M7", unit=FakeUnit("P6/M7"), failures=[], gaps=[self._gap()]
        )
        brief = planner.reasoner.briefs[-1]
        assert any("probe_phase6_conflict.py" in c for c in brief.available_commands)
        assert any("permanent coverage claims" in c for c in brief.existing_coverage)

    def test_the_next_wave_is_told_why_the_last_one_was_refused(self, tmp_path) -> None:
        """Run 20260827-063257 refused the same unapproved command in wave 2 and again,
        verbatim, in wave 3. A wave that is not told what the last one got wrong
        repeats it, and a closure wave that repeats a refused shape closes nothing.
        """
        planner = self._planner(
            tmp_path,
            [
                {"risks": [], "scenarios": [s3_payload()]},
                {"risks": [], "scenarios": []},
            ],
        )
        planner.plan_initial(task="build P6/M7", unit=FakeUnit("P6/M7"))
        planner.expand_after_failures(
            task="build P6/M7", unit=FakeUnit("P6/M7"), failures=[], gaps=[self._gap()]
        )
        brief = planner.reasoner.briefs[-1]
        assert brief.prior_rejections
        assert any(S3_FOREIGN_LITERAL in r for r in brief.prior_rejections)
        rendered = brief.render()
        assert "ALREADY PROPOSED AND HAD REFUSED" in rendered
        assert S3_FOREIGN_LITERAL in rendered

    def test_the_first_wave_has_nothing_to_report(self, tmp_path) -> None:
        planner = self._planner(tmp_path, [{"risks": [], "scenarios": []}])
        planner.plan_initial(task="build P6/M7", unit=FakeUnit("P6/M7"))
        assert planner.reasoner.briefs[0].prior_rejections == []


# ==========================================================================
# 8 — the generator is told the rule it has to satisfy
# ==========================================================================


class TestTheGeneratorIsToldTheRule:
    def test_the_system_instructions_state_the_binding_requirement(self) -> None:
        assert "expect_contains" in GENERATOR_SYSTEM
        assert "SELECTOR" in GENERATOR_SYSTEM
        assert "`forbidden_observations` is exempt" in GENERATOR_SYSTEM

    def test_the_structured_schema_states_it_too(self) -> None:
        field = PLAN_SCHEMA["properties"]["scenarios"]["items"]["properties"][
            "expected_observations"
        ]
        assert "declared by the operation that prints it" in field["description"]
        assert "refused before execution" in field["description"]


# ==========================================================================
# 9 — the mutations: restoring the false negative must be caught
# ==========================================================================


class TestMutationsRestoringTheFalseNegative:
    """Each mutation is the harness as it actually was during run 20260827-063257."""

    def test_mutation_1_dropping_the_rule_lets_s3_through(self) -> None:
        """Delete the invariant and the unsatisfiable oracle is admitted again."""
        assert validate_scenario(s3_scenario(), m7_context()) != []

        import neyma_product_driver.scenario_validation as sv

        original = sv.unattributed_observations
        try:
            sv.unattributed_observations = lambda generated, context: []
            assert validate_scenario(s3_scenario(), m7_context()) == []  # the defect
        finally:
            sv.unattributed_observations = original
        assert validate_scenario(s3_scenario(), m7_context()) != []

    def test_mutation_2_attributing_a_multi_check_claim_to_each_check(self) -> None:
        """The subtler revert: bind a claim's observations to every check it names.

        This alone re-opens the exact S3 hole, because the M7 concurrency claim
        names both the probe and the index introspection and S3 runs the second.
        """
        m7 = load_scenario(M7_PATH)
        mutated = copy.deepcopy(established_observations_from([m7]))
        # The mutation, applied by hand: give the index-introspection command the
        # sentence only the whole battery narrates.
        for command in list(mutated):
            if "sqlite_master" in command and "conflicts" in command:
                mutated[command] = frozenset(mutated[command] | {S3_FOREIGN_LITERAL})
        assert (
            unattributed_observations(
                s3_scenario(), m7_context(established_observations=mutated)
            )
            == []
        )  # the defect, reproduced
        assert unattributed_observations(s3_scenario(), m7_context()) != []

    def test_mutation_3_treating_a_quoted_sentence_as_its_own_basis(self) -> None:
        """The tempting weakening: "the permanent file quotes it, so it is fine".

        ``scenarios/p6_m7_conflict.yaml`` lists both sentences in its top-level
        ``expect_visible``, which is matched against everything the run observed
        and therefore attributes them to nothing. Letting that list count as a
        basis reproduces S3 exactly — and would let any generated scenario
        assert any sentence the repository has ever written down, whatever it
        actually runs.
        """
        m7 = load_scenario(M7_PATH)
        established = established_observations_from([m7])
        unattributed = frozenset(m7.expect_visible)
        assert S3_FOREIGN_LITERAL in unattributed
        mutated = {
            command: frozenset(literals | unattributed)
            for command, literals in established.items()
        }
        assert (
            unattributed_observations(
                s3_scenario(), m7_context(established_observations=mutated)
            )
            == []
        )  # the defect, reproduced
        assert unattributed_observations(s3_scenario(), m7_context()) != []

"""The defect run 20260820-204803 demonstrated, and the contract that replaces it.

The run had: M3 implemented, 46 tests passing, 9/9 mutants caught, every P3/P4/
P5/M1/M2 regression green, 13/13 scenarios passed, and a permanent scenario that
migrated a legacy database, read the schema back, proved exactly-once effect,
proved a blind readback is not a failure and proved replay touches nothing. The
acceptance gate blocked anyway, on six "uncovered" risks — persistence_failure,
conflicting_evidence, retry_safety, regression — every one of which that
permanent scenario had just exercised and passed.

The gate could see only one thing: whether some *generated* scenario carried the
matching ``risk_category`` tag. Permanent and probe coverage was invisible to it
no matter what it proved. So the only available response was to ask the builder
for coverage that already existed, and asking again produced the same answer —
while each generation wave added risks it could not cover, taking the gap count
from two to six as the builder added *valid* coverage.

These tests hold both halves of the fix at once, because either alone is a
different bug:

* executed, resolvable, explicitly-declared evidence satisfies a risk;
* nothing else does — not a passing test suite, not a similar-sounding label,
  not a model's assertion, not an unexecuted declaration, not a pass whose
  evidence does not resolve.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
from neyma_product_driver.models import RiskEvidence
from neyma_product_driver.scenario_gate import (
    BASIS_CATEGORY,
    BASIS_DECLARED,
    GateStatus,
    evaluate_gate,
    risk_coverage,
    uncovered_required_risks,
)
from neyma_product_driver.scenario_plan import (
    IdentifiedRisk,
    Priority,
    RiskCategory,
    compile_to_scenario,
)
from neyma_product_driver.scenario_planner import ScenarioPlanner, permanent_risk_coverage
from neyma_product_driver.scenario_suite import (
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteResult,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

from scenario_fixtures import (
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    make_scenario,
    raw_payload,
    raw_scenario,
)


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def _risk(
    risk_id: str,
    category: RiskCategory,
    severity: Priority = Priority.P0,
    description: str = "",
) -> IdentifiedRisk:
    return IdentifiedRisk(
        id=risk_id,
        description=description or f"something could go wrong with {category.value}",
        risk_category=category,
        severity=severity,
        basis="an acceptance criterion",
    )


def _permanent(
    scenario_id: str = "p6_m3_external_effect",
    *,
    outcome: Outcome = Outcome.PASSED,
    evidence_verified: bool = True,
    evidence: list[RiskEvidence] | None = None,
) -> ScenarioOutcome:
    """A permanent scenario's outcome: no risk_category tag, declared claims."""
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=Origin.PERMANENT,
        outcome=outcome,
        priority=Priority.P0,
        risk_category="",
        required=True,
        evidence_path=f"/runs/x/scenarios/{scenario_id}",
        evidence_verified=evidence_verified,
        risk_evidence=list(evidence or []),
    )


def _generated(
    scenario_id: str,
    category: RiskCategory,
    outcome: Outcome = Outcome.PASSED,
    *,
    evidence_verified: bool = True,
) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=Origin.GENERATED,
        outcome=outcome,
        priority=Priority.P0,
        risk_category=category.value,
        required=True,
        evidence_path=f"/runs/x/scenarios/{scenario_id}",
        evidence_verified=evidence_verified,
    )


def _claim(
    category: RiskCategory,
    *,
    established: bool = True,
    claim: str = "the legacy database migrates to the canonical shape",
    reason: str = "",
) -> RiskEvidence:
    return RiskEvidence(
        risk_category=category.value,
        claim=claim,
        scenario_name="p6_m3_external_effect",
        checks=["the migration battery"],
        observations=["A LEGACY DATABASE MIGRATES TO THE CANONICAL EFFECT SHAPE"],
        established=established,
        reason=reason,
    )


def _result(*outcomes: ScenarioOutcome) -> SuiteResult:
    return SuiteResult(
        full_run=True,
        expected_required_ids=[o.scenario_id for o in outcomes],
        outcomes=list(outcomes),
    )


# --------------------------------------------------------------------------
# The defect, reproduced
# --------------------------------------------------------------------------


class TestPassingPermanentEvidenceSatisfiesTheRisk:
    """The exact shape of run 20260820-204803, as one test each."""

    def test_the_run_that_blocked_now_resolves_its_gaps(self) -> None:
        """Six risks, all exercised by the permanent scenario, all uncovered before.

        Same inputs the run had: a permanent scenario that passed with
        resolvable evidence and declares what it verifies, a set of generated
        scenarios that passed, and a risk register naming risks whose category
        no generated scenario carries.
        """
        risks = [
            _risk("R11", RiskCategory.PERSISTENCE_FAILURE, Priority.P1),
            _risk("R7", RiskCategory.CONFLICTING_EVIDENCE),
            _risk("R8", RiskCategory.RETRY_SAFETY),
            _risk("R1-w3", RiskCategory.REGRESSION, Priority.P1),
        ]
        result = _result(
            _permanent(
                evidence=[
                    _claim(RiskCategory.PERSISTENCE_FAILURE),
                    _claim(RiskCategory.CONFLICTING_EVIDENCE),
                    _claim(RiskCategory.RETRY_SAFETY),
                    _claim(RiskCategory.REGRESSION),
                ]
            ),
            _generated("S01", RiskCategory.CONCURRENCY),
        )

        verdict = evaluate_gate(result, risks=risks)

        assert verdict.uncovered_risks == []
        assert verdict.status is GateStatus.VERIFIED
        # And the answer is auditable: every covered risk names what covered it.
        assert {c.risk_id for c in verdict.covered_risks} == {"R11", "R7", "R8", "R1-w3"}
        assert all(c.basis == BASIS_DECLARED for c in verdict.covered_risks)
        assert all(c.scenario_id == "p6_m3_external_effect" for c in verdict.covered_risks)
        assert all(c.evidence_path for c in verdict.covered_risks)

    def test_a_different_category_label_is_no_longer_a_gap_on_its_own(self) -> None:
        """The one-sentence statement of the bug.

        Nothing generated carries ``persistence_failure``. The permanent
        scenario declares it, ran, and passed. That is coverage.
        """
        risks = [_risk("R11", RiskCategory.PERSISTENCE_FAILURE, Priority.P1)]
        without = _result(_generated("S09", RiskCategory.RESTART_RECOVERY))
        with_evidence = _result(
            _generated("S09", RiskCategory.RESTART_RECOVERY),
            _permanent(evidence=[_claim(RiskCategory.PERSISTENCE_FAILURE)]),
        )

        assert len(uncovered_required_risks(risks, without)) == 1
        assert uncovered_required_risks(risks, with_evidence) == []

    def test_a_generated_scenarios_own_category_still_counts(self) -> None:
        """The pre-existing attachment is unchanged, and is labelled as itself."""
        covered, gaps = risk_coverage(
            [_risk("R1", RiskCategory.IDEMPOTENCY)],
            _result(_generated("gen-1", RiskCategory.IDEMPOTENCY)),
        )
        assert gaps == []
        assert [c.basis for c in covered] == [BASIS_CATEGORY]


# --------------------------------------------------------------------------
# The burden of proof, unchanged
# --------------------------------------------------------------------------


class TestTheBurdenOfProofIsUnchanged:
    def test_a_p0_risk_with_no_evidence_at_all_still_blocks(self) -> None:
        """The first thing the fix must not break."""
        verdict = evaluate_gate(
            _result(_permanent(evidence=[_claim(RiskCategory.PERSISTENCE_FAILURE)])),
            risks=[_risk("R7", RiskCategory.CONFLICTING_EVIDENCE)],
        )

        assert verdict.required_passed == verdict.required_total
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert "no scenario exercising this risk was executed" in (
            verdict.uncovered_risks[0].reason
        )

    @pytest.mark.parametrize("severity", [Priority.P0, Priority.P1])
    def test_both_blocking_severities_are_held_to_it(self, severity: Priority) -> None:
        verdict = evaluate_gate(
            _result(_permanent()), risks=[_risk("R", RiskCategory.RETRY_SAFETY, severity)]
        )
        assert verdict.blocks_acceptance

    @pytest.mark.parametrize(
        "outcome",
        [Outcome.FAILED, Outcome.BLOCKED, Outcome.SKIPPED],
        ids=["failed", "blocked", "skipped"],
    )
    def test_a_declared_claim_on_a_scenario_that_did_not_pass_is_not_evidence(
        self, outcome: Outcome
    ) -> None:
        """"Do not count skipped, unexecuted or failed cases", at the risk layer.

        The claim itself held — the migration check passed — but the scenario
        around it did not establish a pass, so nothing it produced is something
        an acceptance may rest on.
        """
        risks = [_risk("R11", RiskCategory.PERSISTENCE_FAILURE)]
        result = _result(
            _permanent(
                outcome=outcome, evidence=[_claim(RiskCategory.PERSISTENCE_FAILURE)]
            )
        )

        gaps = uncovered_required_risks(risks, result)

        assert len(gaps) == 1
        assert "did not pass" in gaps[0].reason

    def test_unresolved_evidence_still_blocks(self) -> None:
        """A pass that cannot show its evidence proves nothing, declared or not."""
        risks = [_risk("R11", RiskCategory.PERSISTENCE_FAILURE)]
        result = _result(
            _permanent(
                evidence_verified=False,
                evidence=[_claim(RiskCategory.PERSISTENCE_FAILURE)],
            )
        )

        gaps = uncovered_required_risks(risks, result)

        assert len(gaps) == 1
        assert "could not show its evidence" in gaps[0].reason
        assert evaluate_gate(result, risks=risks).blocks_acceptance

    def test_a_declaration_whose_oracle_did_not_hold_is_not_evidence(self) -> None:
        """Declaring coverage is not having it.

        This is the case that distinguishes the mechanism from a whitelist: the
        scenario said it verifies the risk, ran, passed overall — and the check
        the claim named did not run, so the claim is not established and the
        risk stays uncovered.
        """
        risks = [_risk("R11", RiskCategory.PERSISTENCE_FAILURE)]
        result = _result(
            _permanent(
                evidence=[
                    _claim(
                        RiskCategory.PERSISTENCE_FAILURE,
                        established=False,
                        reason="the check 'the migration battery' did not run",
                    )
                ]
            )
        )

        gaps = uncovered_required_risks(risks, result)

        assert len(gaps) == 1
        assert "the declaration did not hold" in gaps[0].reason
        assert "did not run" in gaps[0].reason

    def test_a_declaration_for_one_category_does_not_cover_a_neighbouring_one(
        self,
    ) -> None:
        """No fuzzy matching. ``retry_safety`` neighbours ``idempotency``; so what."""
        result = _result(_permanent(evidence=[_claim(RiskCategory.IDEMPOTENCY)]))

        gaps = uncovered_required_risks([_risk("R8", RiskCategory.RETRY_SAFETY)], result)

        assert len(gaps) == 1

    def test_a_green_test_suite_is_not_coverage(self) -> None:
        """A permanent scenario that passes and declares nothing covers nothing.

        Every scenario in the run passing is exactly the state the original
        defect report describes, and on its own it must still block. What
        changed is that a *declaration* can now be cited; the absence of one is
        as fatal as it always was.
        """
        result = _result(_permanent(), _generated("S01", RiskCategory.CONCURRENCY))

        verdict = evaluate_gate(
            result, risks=[_risk("R8", RiskCategory.RETRY_SAFETY)]
        )

        assert verdict.status is GateStatus.NOT_VERIFIED
        assert len(verdict.uncovered_risks) == 1


# --------------------------------------------------------------------------
# No model may manufacture coverage
# --------------------------------------------------------------------------


class TestModelAssertionsCannotManufactureCoverage:
    def test_a_generated_scenario_has_no_field_that_could_declare_a_claim(self) -> None:
        """The generator's model cannot express a ``verifies:`` entry at all."""
        from neyma_product_driver.scenario_plan import GeneratedScenario

        for field in ("verifies", "risk_claims", "risk_evidence", "covers"):
            assert field not in GeneratedScenario.model_fields, field

    def test_the_compiler_never_emits_a_claim(self) -> None:
        compiled = compile_to_scenario(
            make_scenario("gen-1", risk_category=RiskCategory.PERSISTENCE_FAILURE),
            base=base_scenario(),
            approved_commands={"./probe.sh seed", "./probe.sh payments", "./probe.sh reset"},
        )

        assert compiled.verifies == []
        assert compiled.declared_risk_categories() == set()

    def test_a_claim_arriving_on_a_generated_entry_is_discarded_by_the_suite(
        self, tmp_path: Path
    ) -> None:
        """Belt and braces, exercised.

        Even if some future path put a claim on a generated scenario, the suite
        refuses to carry it onto the outcome — so the gate cannot see it and
        cannot credit it.
        """
        from neyma_product_driver.scenario_suite import SuiteEntry, SuiteExecutor
        from neyma_product_driver.models import ScenarioResult

        scenario = Scenario(
            name="generated:gen-1",
            commands=[{"name": "c", "run": "true", "expect_contains": ["x"]}],
            verifies=[
                {
                    "risk_category": "persistence_failure",
                    "claim": "I hereby declare myself covered",
                    "checks": ["c"],
                }
            ],
        )
        entry = SuiteEntry(
            scenario_id="gen-1", scenario=scenario, origin=Origin.GENERATED
        )
        result = ScenarioResult(
            scenario_name="generated:gen-1",
            risk_evidence=[_claim(RiskCategory.PERSISTENCE_FAILURE)],
        )

        outcome = SuiteExecutor._outcome(entry, result, tmp_path, 0.0)

        assert outcome.risk_evidence == []
        assert outcome.established_risk_categories() == set()

    def test_a_permanent_claim_must_name_a_real_category(self) -> None:
        with pytest.raises(ValueError, match="unknown risk_category"):
            Scenario(
                name="s",
                verifies=[
                    {
                        "risk_category": "everything_is_fine",
                        "claim": "c",
                        "observations": ["x"],
                    }
                ],
            )

    def test_a_permanent_claim_must_name_an_oracle(self) -> None:
        with pytest.raises(ValueError, match="names neither a check nor an observation"):
            Scenario(name="s", verifies=[{"risk_category": "regression", "claim": "c"}])

    def test_a_permanent_claim_may_not_name_a_check_that_does_not_exist(self) -> None:
        with pytest.raises(ValueError, match="does not run"):
            Scenario(
                name="s",
                commands=[{"name": "real", "run": "true"}],
                verifies=[
                    {
                        "risk_category": "regression",
                        "claim": "c",
                        "checks": ["imaginary"],
                    }
                ],
            )


# --------------------------------------------------------------------------
# The executor decides whether a claim held — not the file, and not a model
# --------------------------------------------------------------------------


def _execute(scenario: Scenario, tmp_path: Path):
    executor = ScenarioExecutor(
        repo=tmp_path,
        run_config=ScenarioRunConfig(command_timeout_s=30),
        artifact_dir=tmp_path / "artifacts",
    )
    return asyncio.run(executor.execute(scenario))


class TestClaimsAreResolvedFromExecution:
    def test_a_claim_whose_check_passed_and_whose_text_appeared_is_established(
        self, tmp_path: Path
    ) -> None:
        scenario = Scenario(
            name="s",
            commands=[
                {
                    "name": "the migration battery",
                    "run": "echo 'A LEGACY DATABASE MIGRATES TO THE CANONICAL EFFECT SHAPE'",
                    "expect_exit_code": 0,
                }
            ],
            verifies=[
                {
                    "risk_category": "persistence_failure",
                    "claim": "a pre-M3 database migrates to the canonical shape",
                    "checks": ["the migration battery"],
                    "observations": [
                        "A LEGACY DATABASE MIGRATES TO THE CANONICAL EFFECT SHAPE"
                    ],
                }
            ],
        )

        result = _execute(scenario, tmp_path)

        assert result.passed
        assert [e.established for e in result.risk_evidence] == [True]

    def test_a_claim_whose_text_never_appeared_is_not_established(
        self, tmp_path: Path
    ) -> None:
        scenario = Scenario(
            name="s",
            commands=[{"name": "the migration battery", "run": "echo nothing-of-note"}],
            verifies=[
                {
                    "risk_category": "persistence_failure",
                    "claim": "a pre-M3 database migrates to the canonical shape",
                    "checks": ["the migration battery"],
                    "observations": ["A LEGACY DATABASE MIGRATES"],
                }
            ],
        )

        result = _execute(scenario, tmp_path)

        # The scenario itself passed: exit 0 was all it asserted. The claim did
        # not, which is the distinction that matters.
        assert result.passed
        assert [e.established for e in result.risk_evidence] == [False]
        assert "never emitted" in result.risk_evidence[0].reason

    def test_a_claim_whose_check_failed_is_not_established(self, tmp_path: Path) -> None:
        scenario = Scenario(
            name="s",
            commands=[
                {
                    "name": "the migration battery",
                    "run": "echo A LEGACY DATABASE MIGRATES; exit 3",
                    "expect_exit_code": 0,
                }
            ],
            verifies=[
                {
                    "risk_category": "persistence_failure",
                    "claim": "a pre-M3 database migrates to the canonical shape",
                    "checks": ["the migration battery"],
                }
            ],
        )

        result = _execute(scenario, tmp_path)

        assert not result.passed
        assert [e.established for e in result.risk_evidence] == [False]
        assert "did not pass" in result.risk_evidence[0].reason

    def test_a_check_that_asserts_nothing_establishes_nothing(
        self, tmp_path: Path
    ) -> None:
        """"It ran" is not "it was judged".

        A command with no expectation observes the product without scoring it.
        Treating that as a pass would be exactly the "tests passed therefore
        covered" reasoning the gate must not do.
        """
        scenario = Scenario(
            name="s",
            commands=[
                {
                    "name": "the migration battery",
                    "run": "echo hello",
                    "expect_exit_code": None,
                }
            ],
            verifies=[
                {
                    "risk_category": "persistence_failure",
                    "claim": "a pre-M3 database migrates to the canonical shape",
                    "checks": ["the migration battery"],
                }
            ],
        )

        result = _execute(scenario, tmp_path)

        assert [e.established for e in result.risk_evidence] == [False]

    def test_a_scenario_that_never_reached_its_checks_records_no_claim(
        self, tmp_path: Path
    ) -> None:
        """Setup failed, so the product was never exercised and nothing is claimed."""
        scenario = Scenario(
            name="s",
            setup=["exit 1"],
            commands=[{"name": "the migration battery", "run": "true"}],
            verifies=[
                {
                    "risk_category": "persistence_failure",
                    "claim": "a pre-M3 database migrates to the canonical shape",
                    "checks": ["the migration battery"],
                }
            ],
        )

        result = _execute(scenario, tmp_path)

        assert not result.passed
        assert result.risk_evidence == []

    def test_an_observation_is_matched_against_the_named_checks_output(
        self, tmp_path: Path
    ) -> None:
        """One command's output may not satisfy a claim about another's."""
        scenario = Scenario(
            name="s",
            commands=[
                {"name": "something else", "run": "echo THE MIGRATION WORKED"},
                {"name": "the migration battery", "run": "echo unrelated"},
            ],
            verifies=[
                {
                    "risk_category": "persistence_failure",
                    "claim": "a pre-M3 database migrates to the canonical shape",
                    "checks": ["the migration battery"],
                    "observations": ["THE MIGRATION WORKED"],
                }
            ],
        )

        result = _execute(scenario, tmp_path)

        assert [e.established for e in result.risk_evidence] == [False]
        assert "'the migration battery'" in result.risk_evidence[0].reason


# --------------------------------------------------------------------------
# Convergence: valid coverage must not increase the uncovered count
# --------------------------------------------------------------------------


def _planner(tmp_path: Path, payloads: list, base: Scenario | None = None):
    base = base or base_scenario()
    return ScenarioPlanner(
        repo=tmp_path,
        config=ScenarioGenerationConfig(enabled=True),
        reasoner=ScriptedReasoner(payloads),
        base_scenario=base,
        permanent_scenarios=[base],
        founder=FakeFounder(),
    )


def _covering_permanent() -> Scenario:
    """The fixture permanent scenario, with a reviewed coverage declaration."""
    return Scenario.model_validate(
        {
            **base_scenario().model_dump(),
            "name": "backend_generic_with_claims",
            "verifies": [
                {
                    "risk_category": "persistence_failure",
                    "claim": "the legacy database migrates to the canonical shape",
                    "checks": ["payments"],
                }
            ],
        }
    )


class TestAddingValidCoverageDoesNotWidenTheGap:
    """Why two known gaps became six while the builder was fixing things.

    Waves are additive on risks and bounded on scenarios, so every wave could
    add to the register. Worse, a wave launched with nothing failed ran as the
    *adaptive* stage, whose every proposal must cite an observed failure — so
    with nothing failed, every proposal was refused for the same reason, while
    the wave's newly identified risks joined the register anyway. Three waves of
    that turned two gaps into six without adding one scenario.
    """

    def test_a_wave_with_no_failures_runs_as_a_stage_its_proposals_can_satisfy(
        self, tmp_path: Path
    ) -> None:
        key = _risk(
            "R1", RiskCategory.IDEMPOTENCY, description="approval may not be idempotent"
        ).key
        planner = _planner(
            tmp_path,
            [
                raw_payload(),
                raw_payload(
                    raw_scenario(
                        "gen-close",
                        risk_category="idempotency",
                        source_risks=[key],
                    ),
                    risks=[],
                ),
            ],
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        assert [g.risk_category.value for g in planner.plan.planned_gaps()] == [
            "idempotency"
        ]


        planner.expand_after_failures(
            task="t", unit=FakeUnit(), failures=[], evaluator_requests=["verify more"]
        )

        wave = planner.plan.waves[1]
        assert wave.stage == "coverage_gap"
        # The brief showed the key the scenario had to cite, so the citation is
        # something a generator can satisfy rather than guess at.
        assert key in planner.reasoner.briefs[1].render()
        # And the wave actually closed the gap rather than being refused whole.
        assert wave.accepted_ids == ["gen-close"]
        assert planner.plan.planned_gaps() == []

    def test_a_coverage_gap_case_must_name_the_risk_it_closes(
        self, tmp_path: Path
    ) -> None:
        """The substitute citation, and it is not weaker than the adaptive one."""
        planner = _planner(
            tmp_path,
            [
                raw_payload(),
                raw_payload(
                    raw_scenario("gen-invented", risk_category="idempotency"),
                    risks=[],
                ),
            ],
        )
        planner.plan_initial(task="t", unit=FakeUnit())

        planner.expand_after_failures(task="t", unit=FakeUnit(), failures=[])

        # raw_scenario names no source_risks, so the wave refuses it.
        wave = planner.plan.waves[1]
        assert wave.accepted_ids == []
        assert any(
            "names no identified risk" in reason
            for rejected in wave.rejected
            for reason in rejected.reasons
        )

    def test_a_coverage_gap_case_may_not_cite_a_risk_the_run_never_identified(
        self, tmp_path: Path
    ) -> None:
        planner = _planner(
            tmp_path,
            [
                raw_payload(),
                raw_payload(
                    raw_scenario(
                        "gen-fake",
                        risk_category="idempotency",
                        source_risks=["idempotency:deadbeef00"],
                    ),
                    risks=[],
                ),
            ],
        )
        planner.plan_initial(task="t", unit=FakeUnit())

        planner.expand_after_failures(task="t", unit=FakeUnit(), failures=[])

        wave = planner.plan.waves[1]
        assert wave.accepted_ids == []
        assert any(
            "never identified" in reason
            for rejected in wave.rejected
            for reason in rejected.reasons
        )

    def test_a_permanent_file_this_run_never_executes_is_not_coverage(
        self, tmp_path: Path
    ) -> None:
        """Only the scenario the suite actually runs may claim planned coverage.

        The scenarios directory holds every handwritten file; a run executes one
        of them as its base. Crediting the rest would make the planner report
        fewer gaps than the gate finds — fail-closed at the gate, but the
        generator would be aimed at the wrong thing, which is the loop this
        whole change exists to break.
        """
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner([raw_payload()]),
            base_scenario=base_scenario(),
            # Declares persistence_failure coverage, and is never executed.
            permanent_scenarios=[base_scenario(), _covering_permanent()],
            founder=FakeFounder(),
        )

        assert planner.plan.permanent_coverage == {}

    def test_permanent_coverage_is_not_reported_as_a_planning_gap(
        self, tmp_path: Path
    ) -> None:
        """The loop that could not converge, closed at its source.

        A risk the permanent suite already exercises was reported as a gap, so
        the wave was aimed at it, so the proposal duplicated permanent coverage,
        so validation refused it as a duplicate — correctly — and the gap
        survived to the next wave unchanged.
        """
        risks = [
            {
                "id": "R11",
                "description": "the migration could omit the effect columns",
                "risk_category": "persistence_failure",
                "severity": "P1",
                "basis": "the diff touched the migration",
            }
        ]
        blind = _planner(tmp_path, [raw_payload(risks=risks)])
        blind.plan_initial(task="t", unit=FakeUnit())
        assert [g.id for g in blind.plan.planned_gaps()] == ["R11"]

        aware = _planner(tmp_path, [raw_payload(risks=risks)], base=_covering_permanent())
        aware.plan_initial(task="t", unit=FakeUnit())

        assert aware.plan.planned_gaps() == []
        assert aware.plan.permanent_coverage["persistence_failure"] == [
            "backend_generic_with_claims: the legacy database migrates to the canonical shape"
        ]
        # And the generator is told about it, so it does not propose a duplicate.
        brief = aware.reasoner.briefs[0].render()
        assert "permanent coverage claims persistence_failure" in brief

    def test_adding_coverage_never_increases_the_uncovered_count(
        self, tmp_path: Path
    ) -> None:
        """Monotonicity, stated directly.

        Same risk register, three snapshots of the same run, each with strictly
        more passing evidence than the last. The uncovered count must never go
        up.
        """
        risks = [
            _risk("R1", RiskCategory.CONCURRENCY),
            _risk("R11", RiskCategory.PERSISTENCE_FAILURE, Priority.P1),
            _risk("R8", RiskCategory.RETRY_SAFETY),
        ]
        snapshots = [
            _result(_permanent()),
            _result(_permanent(), _generated("S01", RiskCategory.CONCURRENCY)),
            _result(
                _permanent(
                    evidence=[
                        _claim(RiskCategory.PERSISTENCE_FAILURE),
                        _claim(RiskCategory.RETRY_SAFETY),
                    ]
                ),
                _generated("S01", RiskCategory.CONCURRENCY),
            ),
        ]

        counts = [len(uncovered_required_risks(risks, s)) for s in snapshots]

        assert counts == [3, 2, 0]
        assert counts == sorted(counts, reverse=True)

    def test_colliding_risk_ids_from_different_waves_stay_distinct(
        self, tmp_path: Path
    ) -> None:
        """Every wave numbers its risks from R1, so three waves held three "R1"s.

        Anything keyed on the id merged them, and a gap list that says "R1"
        three times cannot be acted on. The keys are derived from the risk, and
        the display ids are made unique as they are merged.
        """
        first = [
            {
                "id": "R1",
                "description": "the claim CAS could admit two winners",
                "risk_category": "concurrency",
                "severity": "P0",
            }
        ]
        second = [
            {
                "id": "R1",
                "description": "the probe never exposed the mutation-axis flags",
                "risk_category": "malformed_input",
                "severity": "P0",
            }
        ]
        planner = _planner(
            tmp_path,
            [raw_payload(risks=first), raw_payload(risks=second)],
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        planner.expand_after_failures(task="t", unit=FakeUnit(), failures=[])

        ids = [r.id for r in planner.plan.risks]
        keys = [r.key for r in planner.plan.risks]
        assert len(set(ids)) == len(ids) == 2, ids
        assert len(set(keys)) == 2
        assert "R1" in ids and any(i.startswith("R1-w") for i in ids)

    def test_the_same_risk_restated_in_a_later_wave_is_one_risk(
        self, tmp_path: Path
    ) -> None:
        planner = _planner(tmp_path, [raw_payload(), raw_payload()])
        planner.plan_initial(task="t", unit=FakeUnit())
        planner.expand_after_failures(task="t", unit=FakeUnit(), failures=[])

        assert len(planner.plan.risks) == 1


# --------------------------------------------------------------------------
# A claim may not require an observation its own checks cannot produce
# --------------------------------------------------------------------------


class TestAClaimIsMappedToChecksThatCanProduceItsObservations:
    """The mirror image of the defect this file was written for, and the one that
    blocked run ``20260825-204229``.

    That run's gate reported a standing [P1] gap on ``regression`` while the
    product was correct: the permanent M6 scenario's ``regression`` claim
    required two literals the M6 probe emits, and named only pytest anchors that
    narrate nothing. Observations are matched against the output of the NAMED
    checks alone — see ``ScenarioExecutor._resolve_risk_claims`` — so the claim
    was unfalsifiable in the wrong direction: no product change could establish
    it, and the gate reported the mapping error as a coverage gap.

    The half a YAML loader can decide is the half where the literal has a
    declared producer: some check in the same scenario says, in its own
    ``expect_contains`` or ``contains``, that it emits that string. A claim
    requiring it while naming none of those producers is refused at load time,
    exactly as a claim naming a check that does not exist already is. Free-form
    narration — a probe's story, a mutation battery's tally — declares nothing
    and is therefore left alone here rather than guessed at; the shipped
    scenarios pin those in their own readiness files.
    """

    def _scenario(self, claim_checks: list[str]) -> Scenario:
        return Scenario(
            name="s",
            commands=[
                {
                    "name": "the migration battery",
                    "run": "echo hi",
                    "expect_contains": ["A LEGACY DATABASE MIGRATED"],
                },
                {"name": "the unit tests", "run": "echo hi", "expect_exit_code": 0},
            ],
            verifies=[
                {
                    "risk_category": "persistence_failure",
                    "claim": "the legacy database migrates",
                    "checks": claim_checks,
                    "observations": ["A LEGACY DATABASE MIGRATED"],
                }
            ],
        )

    def test_naming_the_declared_producer_loads(self) -> None:
        scenario = self._scenario(["the migration battery"])

        assert scenario.verifies[0].checks == ["the migration battery"]

    def test_naming_the_producer_among_others_loads(self) -> None:
        scenario = self._scenario(["the unit tests", "the migration battery"])

        assert len(scenario.verifies[0].checks) == 2

    def test_naming_only_a_check_that_cannot_emit_it_is_refused(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            self._scenario(["the unit tests"])

        message = str(excinfo.value)
        assert "A LEGACY DATABASE MIGRATED" in message
        assert "the migration battery" in message
        assert "could never be established" in message

    def test_free_form_narration_is_not_second_guessed(self) -> None:
        """No check declares this literal, so nothing here knows who prints it.

        The probe that narrates it is the ordinary case, and refusing it would
        make every honest scenario in this repository unloadable. The rule stays
        scoped to what the file actually says.
        """
        scenario = Scenario(
            name="s",
            commands=[{"name": "the probe", "run": "echo hi", "expect_exit_code": 0}],
            verifies=[
                {
                    "risk_category": "regression",
                    "claim": "the probe says so",
                    "checks": ["the probe"],
                    "observations": ["THE M7 CONFLICT MACHINE IS NOT BUILT"],
                }
            ],
        )

        assert scenario.verifies[0].observations == [
            "THE M7 CONFLICT MACHINE IS NOT BUILT"
        ]

    def test_a_claim_with_no_named_checks_asserts_no_attribution(self) -> None:
        """Observations then match everything the run observed, which is a
        different and weaker declaration — but not a mis-mapping."""
        scenario = Scenario(
            name="s",
            commands=[
                {
                    "name": "the migration battery",
                    "run": "echo hi",
                    "expect_contains": ["A LEGACY DATABASE MIGRATED"],
                }
            ],
            verifies=[
                {
                    "risk_category": "persistence_failure",
                    "claim": "somewhere in this run",
                    "observations": ["A LEGACY DATABASE MIGRATED"],
                }
            ],
        )

        assert scenario.verifies[0].checks == []

    def test_every_shipped_scenario_obeys_it(self) -> None:
        """The rule is a load-time error, so this passing means every scenario in
        the repository loads — and that the M6 regression mapping is fixed rather
        than merely tested around."""
        from neyma_product_driver.scenarios import load_scenario

        directory = Path(__file__).resolve().parents[1] / "scenarios"
        if not directory.exists():  # pragma: no cover - the files ship with the driver
            pytest.skip("no scenarios in this checkout")

        loaded = [load_scenario(path) for path in sorted(directory.glob("*.y*ml"))]

        assert loaded, "no permanent scenarios were found"


# --------------------------------------------------------------------------
# The permanent M3 scenario this run used
# --------------------------------------------------------------------------


class TestTheShippedM3Scenario:
    """The declarations in ``scenarios/p6_m3_external_effect.yaml`` are real.

    Not that they are correct about M3 — that is the human's judgement, recorded
    in the file — but that they load, name checks that exist, and name
    categories the taxonomy has.
    """

    def test_it_loads_and_its_claims_resolve_to_real_checks(self) -> None:
        from neyma_product_driver.scenarios import load_scenario

        path = Path(__file__).resolve().parents[1] / "scenarios" / "p6_m3_external_effect.yaml"
        if not path.exists():  # pragma: no cover - the file is part of the driver
            pytest.skip("the M3 scenario is not present in this checkout")

        scenario = load_scenario(path)

        assert scenario.verifies, "the M3 scenario declares no risk coverage"
        names = scenario.check_names()
        for claim in scenario.verifies:
            assert claim.checks, claim.claim
            assert set(claim.checks) <= names, claim.claim

    def test_the_planner_reads_its_coverage(self) -> None:
        from neyma_product_driver.scenarios import load_scenario

        path = Path(__file__).resolve().parents[1] / "scenarios" / "p6_m3_external_effect.yaml"
        if not path.exists():  # pragma: no cover
            pytest.skip("the M3 scenario is not present in this checkout")

        coverage = permanent_risk_coverage([load_scenario(path)])

        for category in ("persistence_failure", "conflicting_evidence", "retry_safety"):
            assert category in coverage, category
        # Deliberately undeclared: exercised inside the probe, but with no
        # literal this file can bind a claim to. A run that names it as blocking
        # must still generate a case for it.
        assert "restart_recovery" not in coverage

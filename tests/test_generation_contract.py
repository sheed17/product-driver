"""The generation contract: one taxonomy, and no way to lose a candidate quietly.

Neyma recorded ``P6-D46`` after the P6/M6 re-verification run. Dynamic generation
proposed nine scenarios; every one declared a ``risk_category`` the harness's own
enum did not contain; all nine were discarded at the parse stage; and the run
reported

    0 generated case(s) + 1 permanent scenario

and went on to ACCEPT. Nothing had failed. The product was fine. But "the
generator legitimately produced no usable new scenarios" and "the generator
produced nine and Product Driver could not read any of them" had collapsed into
one number, and only the first of those is a reason to accept.

The requirement these tests pin is general, not about M6 and not about those nine
strings: whenever generation is enabled, candidates were actually proposed, and
Product Driver's own schema or taxonomy is what discarded them, the run may not
reach a normal acceptance and the counts may not be summed together.

Every session here is scripted. Nothing consumes Claude usage.
"""

from __future__ import annotations

import json

import pytest

from neyma_product_driver.cli import _apply_suite_precedence
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.models import Decision, EvaluatorDecision, RunStatus
from neyma_product_driver.prompts import evaluator_prompt
from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
from neyma_product_driver.scenario_generator import (
    GENERATOR_SYSTEM,
    PLAN_SCHEMA,
    parse_scenarios,
    unknown_category,
)
from neyma_product_driver.scenario_plan import (
    REJECTED_CONTRACT,
    REJECTED_FILTERED,
    RISK_CATEGORY_VALUES,
    Priority,
    RiskCategory,
    ScenarioProvenance,
)
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import Origin, Outcome, ScenarioOutcome, SuiteResult

from scenario_fixtures import (
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)
from test_coverage_gap_closure import drive, loop_bits, make_planner  # noqa: F401


# --------------------------------------------------------------------------
# The P6-D46 fixture: the nine candidates exactly as they were proposed
# --------------------------------------------------------------------------

#: The ``risk_category`` value each of the nine P6/M6 candidates declared, in the
#: order the wave recorded them. Kept verbatim because the shape matters: every
#: one is a plausible, well-meant *description of a specific defect* rather than
#: a member of a closed family vocabulary, which is what an unconstrained
#: ``{"type": "string"}`` schema invites a model to write.
P6_D46_CATEGORIES = (
    "ships-live-not-dark",
    "scope-overreach-into-m7-m10",
    "owner-decision-overwritten",
    "double-confirmation-race",
    "confidence-gated-confirmation",
    "cross-tenant-leak",
    "correction-obligation-dropped",
    "replay-rewrites-owner-asserted",
    "unfalsified-guard",
)


def _provenance() -> ScenarioProvenance:
    return ScenarioProvenance(wave=1, stage="initial", generating_risk="a stated risk")


def _planner(tmp_path, payloads) -> ScenarioPlanner:
    return ScenarioPlanner(
        repo=tmp_path,
        config=ScenarioGenerationConfig(enabled=True),
        reasoner=ScriptedReasoner(list(payloads)),
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )


def _passing_permanent_suite() -> SuiteResult:
    """The M6 shape: the permanent scenario passed and nothing else ran."""
    passed = ScenarioOutcome(
        scenario_id="p6_m6_identity_binding_claim",
        scenario_name="p6_m6_identity_binding_claim",
        origin=Origin.PERMANENT,
        outcome=Outcome.PASSED,
        priority=Priority.P0,
        required=True,
        evidence_path="/runs/x/permanent",
        evidence_verified=True,
    )
    return SuiteResult(
        outcomes=[passed], full_run=True, expected_required_ids=[passed.scenario_id]
    )


def _accept() -> EvaluatorDecision:
    return EvaluatorDecision(
        decision=Decision.ACCEPT, summary="good", observed_behavior=["saw it"]
    )


# ==========================================================================
# 1 — one authoritative vocabulary, and every layer derives from it
# ==========================================================================


class TestOneTaxonomy:
    def test_the_canonical_values_are_derived_from_the_enum(self) -> None:
        """Not a second hand-maintained list that can drift from the first."""
        assert RISK_CATEGORY_VALUES == tuple(c.value for c in RiskCategory)

    @pytest.mark.parametrize(
        "location",
        ["risks", "scenarios"],
    )
    def test_the_generator_schema_constrains_the_field_to_the_enum(
        self, location: str
    ) -> None:
        """Prose asks; a schema constrains. This is the layer that constrains.

        The field was ``{"type": "string"}``, so the structured-output contract
        permitted any label at all — which is how nine invented ones arrived and
        were then discarded by a parser holding a different vocabulary.
        """
        field = PLAN_SCHEMA["properties"][location]["items"]["properties"][
            "risk_category"
        ]
        assert field["enum"] == list(RISK_CATEGORY_VALUES)
        assert field["type"] == "string"

    def test_the_system_instructions_offer_exactly_the_canonical_vocabulary(
        self,
    ) -> None:
        """Shown and enforced must be the same set, in the same commit."""
        for value in RISK_CATEGORY_VALUES:
            assert value in GENERATOR_SYSTEM, value
        assert "CLOSED vocabulary" in GENERATOR_SYSTEM

    def test_the_shown_vocabulary_is_rendered_and_not_retyped(self) -> None:
        """A category added to the enum must reach the model without a second edit."""
        import neyma_product_driver.scenario_generator as gen

        assert gen.RENDERED_CATEGORIES.replace("\n", " ").rstrip(".").split(", ") == list(
            RISK_CATEGORY_VALUES
        )
        assert "{CATEGORIES}" not in GENERATOR_SYSTEM

    def test_a_permanent_scenario_claim_holds_the_same_vocabulary(self) -> None:
        """The declared-coverage layer derives from the enum too, not a copy."""
        from neyma_product_driver.scenarios import RiskClaim

        for value in RISK_CATEGORY_VALUES:
            RiskClaim(risk_category=value, claim="c", observations=["o"])
        with pytest.raises(ValueError, match="unknown risk_category"):
            RiskClaim(risk_category="cross-tenant-leak", claim="c", observations=["o"])


# ==========================================================================
# 2 — canonical categories still work, end to end
# ==========================================================================


class TestCanonicalCategoriesStillAssemble:
    @pytest.mark.parametrize("category", RISK_CATEGORY_VALUES)
    def test_every_canonical_category_parses(self, category: str) -> None:
        parsed, malformed = parse_scenarios(
            raw_payload(raw_scenario(risk_category=category)),
            provenance=_provenance(),
        )
        assert not malformed, malformed
        assert parsed[0].risk_category.value == category

    def test_case_and_padding_are_a_format_variation_not_a_new_meaning(self) -> None:
        """The one normalisation, and it is the only one: no fuzzy mapping."""
        parsed, malformed = parse_scenarios(
            raw_payload(raw_scenario(risk_category="  IDEMPOTENCY  ")),
            provenance=_provenance(),
        )
        assert not malformed
        assert parsed[0].risk_category is RiskCategory.IDEMPOTENCY

    def test_a_near_miss_is_not_guessed_into_a_canonical_category(self) -> None:
        """``cross-tenant-leak`` is one hyphen from ``cross_tenant`` and is still refused."""
        assert unknown_category({"risk_category": "cross-tenant-leak"}) == "cross-tenant-leak"
        parsed, malformed = parse_scenarios(
            raw_payload(raw_scenario(risk_category="cross-tenant-leak")),
            provenance=_provenance(),
        )
        assert parsed == []
        assert malformed

    def test_a_valid_wave_assembles_and_is_not_a_generation_problem(
        self, tmp_path
    ) -> None:
        planner = _planner(tmp_path, [raw_payload(raw_scenario("gen-ok"))])
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert [s.id for s in plan.scenarios] == ["gen-ok"]
        assert planner.generation_problems() == []
        assert plan.coverage_summary.proposed == 1
        assert plan.coverage_summary.accepted == 1
        assert plan.coverage_summary.invalid == 0


# ==========================================================================
# 3 — an unknown category cannot disappear
# ==========================================================================


class TestUnknownCategoryCannotVanish:
    def test_the_rejection_is_recorded_as_a_contract_failure(self, tmp_path) -> None:
        planner = _planner(
            tmp_path, [raw_payload(raw_scenario(risk_category="cross-tenant-leak"))]
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.proposed == 1
        assert wave.accepted_ids == []
        assert [r.kind for r in wave.rejected] == [REJECTED_CONTRACT]
        assert len(wave.contract_rejections) == 1
        assert wave.filtered_rejections == []

    def test_the_candidate_itself_is_kept_not_deleted(self, tmp_path) -> None:
        """Accounting that drops the evidence is accounting nobody can audit."""
        planner = _planner(
            tmp_path, [raw_payload(raw_scenario("S1", risk_category="ships-live-not-dark"))]
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        rejected = plan.waves[0].rejected[0]
        assert rejected.id == "S1"
        assert rejected.raw["risk_category"] == "ships-live-not-dark"
        assert "ships-live-not-dark" in rejected.reasons[0]

    def test_the_reason_names_the_vocabulary_it_was_measured_against(self) -> None:
        _parsed, malformed = parse_scenarios(
            raw_payload(raw_scenario(risk_category="unfalsified-guard")),
            provenance=_provenance(),
        )
        reason = malformed[0][1][0]
        assert "unknown risk_category" in reason
        for value in RISK_CATEGORY_VALUES:
            assert value in reason

    def test_it_reaches_the_acceptance_gate(self, tmp_path) -> None:
        planner = _planner(
            tmp_path, [raw_payload(raw_scenario(risk_category="double-confirmation-race"))]
        )
        planner.plan_initial(task="t", unit=FakeUnit())

        problems = planner.generation_problems()
        assert problems
        assert evaluate_gate(
            _passing_permanent_suite(), generation_problems=problems
        ).status is GateStatus.NOT_VERIFIED


# ==========================================================================
# 4 — the P6-D46 shape itself: all nine invalid, ACCEPT impossible
# ==========================================================================


class TestP6D46:
    """The exact failure, replayed. Nine proposed, nine unreadable, all passing."""

    @staticmethod
    def _wave() -> dict:
        return raw_payload(
            *(
                raw_scenario(f"S{i}-p6-d46", risk_category=category)
                for i, category in enumerate(P6_D46_CATEGORIES, start=1)
            ),
            risks=[],
        )

    def test_nine_proposed_nine_invalid_zero_accepted(self, tmp_path) -> None:
        planner = _planner(tmp_path, [self._wave()])
        plan = planner.plan_initial(task="build P6/M6", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.proposed == 9
        assert wave.accepted_ids == []
        assert len(wave.contract_rejections) == 9
        assert wave.filtered_rejections == []
        assert plan.scenarios == []

    def test_the_run_cannot_be_accepted(self, tmp_path) -> None:
        """The invariant. Everything that ran passed, and ACCEPT is still refused."""
        planner = _planner(tmp_path, [self._wave()])
        planner.plan_initial(task="build P6/M6", unit=FakeUnit())
        problems = planner.generation_problems()
        result = _passing_permanent_suite()

        # Without the problems, this is exactly the false green: verified.
        assert evaluate_gate(result).status is GateStatus.VERIFIED

        assert evaluate_gate(result, generation_problems=problems).blocks_acceptance
        decision = _apply_suite_precedence(
            result,
            _accept(),
            "p6_m6_identity_binding_claim",
            lambda _m: None,
            generation_problems=problems,
        )
        assert decision.decision is Decision.BLOCKED

    def test_the_blocker_says_nine_proposed_not_zero_generated(self, tmp_path) -> None:
        planner = _planner(tmp_path, [self._wave()])
        planner.plan_initial(task="build P6/M6", unit=FakeUnit())

        text = " ".join(planner.generation_problems())
        assert "9 proposed" in text
        assert "0 accepted for execution" in text
        assert "9 invalid" in text
        assert "generation-contract failure" in text
        # And it must not read as the product's fault.
        assert "Product Driver" in text

    def test_the_journal_refuses_to_call_this_proven(self, tmp_path) -> None:
        """The founder-facing predicate, not a rendering detail."""
        from neyma_product_driver.run_journal import RunJournal

        planner = _planner(tmp_path, [self._wave()])
        planner.plan_initial(task="build P6/M6", unit=FakeUnit())

        journal = RunJournal()
        journal.run_status = "ACCEPTED"
        journal.gate_status = "VERIFIED"
        journal.generation_problems = list(planner.generation_problems())
        assert journal.verification_established is False


# ==========================================================================
# 5 — proposed / accepted / filtered / invalid are reported apart
# ==========================================================================


class TestAccounting:
    def test_the_wave_states_all_four_counts(self, tmp_path) -> None:
        planner = _planner(
            tmp_path,
            [
                raw_payload(
                    raw_scenario("gen-ok"),
                    raw_scenario("gen-bad", risk_category="owner-decision-overwritten"),
                )
            ],
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        line = plan.waves[0].accounting()
        assert "2 proposed" in line
        assert "1 accepted for execution" in line
        assert "0 filtered or deduplicated" in line
        assert "1 invalid" in line

    def test_the_plan_summary_cannot_be_read_as_zero_generated(self, tmp_path) -> None:
        planner = _planner(
            tmp_path,
            [
                raw_payload(
                    *(
                        raw_scenario(f"S{i}", risk_category=c)
                        for i, c in enumerate(P6_D46_CATEGORIES, start=1)
                    ),
                    risks=[],
                )
            ],
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        rendered = plan.coverage_summary.render()
        assert "0 generated case(s)" in rendered  # still true, and no longer alone
        assert "9 proposed" in rendered
        assert "9 invalid" in rendered
        assert "WARNING" in rendered
        assert plan.coverage_summary.invalid == 9

    def test_the_persisted_plan_carries_the_kind(self, tmp_path) -> None:
        """``scenario-plan.json`` alone must answer 'invalid, or decided against?'."""
        planner = _planner(
            tmp_path, [raw_payload(raw_scenario(risk_category="unfalsified-guard"))]
        )
        planner.plan_initial(task="t", unit=FakeUnit(), run_id="r1")

        path = next(tmp_path.rglob("scenario-plan.json"), None)
        if path is None:  # persistence is configured elsewhere; the model is the contract
            payload = json.loads(planner.plan.model_dump_json())
        else:
            payload = json.loads(path.read_text())
        assert payload["waves"][0]["rejected"][0]["kind"] == REJECTED_CONTRACT
        assert payload["coverage_summary"]["invalid"] == 1

    def test_the_evaluator_is_told_and_told_whose_fault_it_is(self, tmp_path) -> None:
        """The evaluator wrote '0 generated cases' because nothing showed it otherwise."""
        planner = _planner(
            tmp_path, [raw_payload(raw_scenario(risk_category="cross-tenant-leak"))]
        )
        planner.plan_initial(task="t", unit=FakeUnit())

        prompt = evaluator_prompt(
            task="t",
            iteration=1,
            max_iterations=3,
            builder_summary="done",
            git=None,
            scenario=None,
            service_logs=None,
            evidence_dir="/runs/x",
            suite=_passing_permanent_suite(),
            generation_problems=list(planner.generation_problems()),
        )
        assert "VERIFICATION THAT WAS NEVER PRODUCED" in prompt
        assert "1 proposed" in prompt
        assert "not treat these as the product's problem" in prompt


# ==========================================================================
# 6 — legitimate filtering is NOT a harness failure
# ==========================================================================


class TestLegitimateFilteringIsUntouched:
    def test_a_duplicate_is_filtered_not_invalid(self, tmp_path) -> None:
        """Refusing a repeat is planning working. Blocking on it would block every run."""
        planner = _planner(
            tmp_path, [raw_payload(raw_scenario("gen-a"), raw_scenario("gen-b"))]
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.accepted_ids == ["gen-a"]
        assert len(wave.filtered_rejections) == 1
        assert wave.contract_rejections == []
        assert "duplicate" in wave.filtered_rejections[0].reasons[0]
        assert planner.generation_problems() == []

    def test_a_safety_refusal_is_filtered_not_invalid(self, tmp_path) -> None:
        """An unapproved command is a judgement about a payload that was read fine."""
        planner = _planner(
            tmp_path,
            [raw_payload(raw_scenario("gen-x", setup=["rm -rf /"]))],
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.proposed == 1
        assert wave.accepted_ids == []
        assert wave.contract_rejections == []
        assert len(wave.filtered_rejections) == 1
        assert planner.generation_problems() == []

    def test_an_honestly_empty_wave_is_still_not_a_problem(self, tmp_path) -> None:
        planner = _planner(tmp_path, [{"risks": [], "scenarios": []}])
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert plan.waves[0].proposed == 0
        assert planner.generation_problems() == []
        assert plan.coverage_summary.invalid == 0

    def test_the_default_kind_is_filtered(self) -> None:
        """Nothing pre-existing, and no future refusal path, blocks by accident."""
        from neyma_product_driver.scenario_plan import RejectedScenario

        assert RejectedScenario(id="x").kind == REJECTED_FILTERED
        assert RejectedScenario(id="x").is_contract_failure is False


# ==========================================================================
# 7 — mixed valid and invalid: both outcomes survive
# ==========================================================================


class TestMixedWave:
    @staticmethod
    def _mixed() -> dict:
        return raw_payload(
            raw_scenario("gen-valid"),
            raw_scenario("gen-invalid-1", risk_category="replay-rewrites-owner-asserted"),
            raw_scenario("gen-invalid-2", risk_category="confidence-gated-confirmation"),
        )

    def test_the_valid_scenario_still_assembles(self, tmp_path) -> None:
        """Do not over-correct: a readable proposal is not punished for its neighbours."""
        planner = _planner(tmp_path, [self._mixed()])
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert [s.id for s in plan.scenarios] == ["gen-valid"]
        assert planner.compiled["gen-valid"] is not None

    def test_the_invalid_candidates_are_not_erased(self, tmp_path) -> None:
        planner = _planner(tmp_path, [self._mixed()])
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.proposed == 3
        assert wave.accepted_count == 1
        assert {r.id for r in wave.contract_rejections} == {
            "gen-invalid-1",
            "gen-invalid-2",
        }

    def test_a_mixed_wave_still_blocks(self, tmp_path) -> None:
        """Some candidates parsing does not excuse the ones that did not.

        What an unreadable candidate would have exercised is unknown, so nothing
        can say whether the one that ran reaches it. That is the same
        unquantifiable gap a failed reasoner leaves, and this method already
        blocks on that even when other waves produced good coverage.
        """
        planner = _planner(tmp_path, [self._mixed()])
        planner.plan_initial(task="t", unit=FakeUnit())

        problems = planner.generation_problems()
        assert problems
        text = " ".join(problems)
        assert "3 proposed" in text and "1 accepted" in text and "2 invalid" in text
        assert evaluate_gate(
            _passing_permanent_suite(), generation_problems=problems
        ).blocks_acceptance


# ==========================================================================
# 8 — the mutation: restoring the false green must be caught
# ==========================================================================


class TestMutationRestoringTheFalseGreen:
    """Each mutation is the P6-D46 code as it actually was. All must fail.

    Written as executable mutations rather than as prose, because the defect was
    never a missing assertion about a value — it was three layers each behaving
    reasonably and losing the fact between them.
    """

    def test_mutation_1_unconstrained_schema_is_caught(self) -> None:
        """Revert the enum to a bare string, as it was."""
        import copy

        mutated = copy.deepcopy(PLAN_SCHEMA)
        mutated["properties"]["scenarios"]["items"]["properties"]["risk_category"] = {
            "type": "string"
        }
        field = mutated["properties"]["scenarios"]["items"]["properties"]["risk_category"]
        assert "enum" not in field
        # The pin: the shipped schema is not this.
        assert (
            PLAN_SCHEMA["properties"]["scenarios"]["items"]["properties"]["risk_category"]
            != field
        )

    def test_mutation_2_classifying_a_parse_rejection_as_filtered_is_caught(
        self, tmp_path
    ) -> None:
        """The single line that made nine invalid candidates read as nine choices."""
        planner = _planner(
            tmp_path, [raw_payload(raw_scenario(risk_category="cross-tenant-leak"))]
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())
        assert planner.generation_problems()  # correct behaviour

        for rejected in plan.waves[0].rejected:
            rejected.kind = REJECTED_FILTERED
        assert planner.generation_problems() == []  # the defect, reproduced

    def test_mutation_3_dropping_contract_rejections_from_the_gate_is_caught(
        self, tmp_path
    ) -> None:
        """Keep the accounting, stop feeding the gate: the run accepts again."""
        planner = _planner(
            tmp_path, [raw_payload(raw_scenario(risk_category="cross-tenant-leak"))]
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        result = _passing_permanent_suite()

        assert _apply_suite_precedence(
            result,
            _accept(),
            "permanent",
            lambda _m: None,
            generation_problems=planner.generation_problems(),
        ).decision is Decision.BLOCKED

        # Drop them on the floor — exactly what the run did — and the false
        # green returns. This asserts the channel is load-bearing.
        assert _apply_suite_precedence(
            result, _accept(), "permanent", lambda _m: None, generation_problems=[]
        ).decision is Decision.ACCEPT

    def test_mutation_4_deleting_rejected_candidates_from_accounting_is_caught(
        self, tmp_path
    ) -> None:
        """"Simply drop them" is the tempting fix, and it is the defect."""
        planner = _planner(
            tmp_path, [raw_payload(raw_scenario(risk_category="cross-tenant-leak"))]
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        plan.waves[0].rejected.clear()
        plan.waves[0].proposed = 0
        plan.recompute_coverage()
        assert planner.generation_problems() == []
        assert "0 proposed" in plan.coverage_summary.render()
        # ...which is why the counts and the blocker both derive from `rejected`
        # rather than being written down separately.


# ==========================================================================
# 9 — end to end, against the disposable fixture product
# ==========================================================================


class TestEndToEnd:
    """The whole loop, not just the units: a real suite is built and executed.

    Uses the same fake product, builder, evaluator and executor the rest of the
    loop tests use. Nothing consumes Claude usage and no product is deployed.
    """

    async def test_a_canonical_wave_assembles_executes_and_accepts(
        self, loop_bits
    ) -> None:
        """The healthy path, unchanged. The hardening must not cost this."""
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-canonical"))])
        log: list[str] = []

        result = await drive(config, store, state, planner, log=log)

        assert "gen-canonical" in log  # it really ran
        assert result.status is RunStatus.ACCEPTED
        assert planner.generation_problems() == []

    async def test_an_unsupported_category_blocks_rather_than_vanishing(
        self, loop_bits
    ) -> None:
        """The P6-D46 shape end to end: nothing failed, and it may not accept."""
        config, store, state = loop_bits
        config.max_iterations = 1
        wave = raw_payload(
            *(
                raw_scenario(f"S{i}-p6-d46", risk_category=category)
                for i, category in enumerate(P6_D46_CATEGORIES, start=1)
            ),
            risks=[],
        )
        planner = make_planner(config, store, [wave])
        log: list[str] = []

        result = await drive(config, store, state, planner, log=log)

        # The permanent scenario ran and passed, and no generated case ran at
        # all — bit for bit the run that ACCEPTed. It no longer can.
        assert log == ["backend_generic"]
        assert result.status is not RunStatus.ACCEPTED
        assert len(planner.plan.waves[0].contract_rejections) == 9

    async def test_a_mixed_wave_runs_the_valid_case_and_still_blocks(
        self, loop_bits
    ) -> None:
        """Both outcomes preserved: the readable case executes, the run holds."""
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(
            config,
            store,
            [
                raw_payload(
                    raw_scenario("gen-valid"),
                    raw_scenario("gen-invalid", risk_category="cross-tenant-leak"),
                )
            ],
        )
        log: list[str] = []

        result = await drive(config, store, state, planner, log=log)

        assert "gen-valid" in log  # not punished for its neighbour
        assert result.status is not RunStatus.ACCEPTED
        wave = planner.plan.waves[0]
        assert wave.accepted_ids == ["gen-valid"]
        assert [r.id for r in wave.contract_rejections] == ["gen-invalid"]

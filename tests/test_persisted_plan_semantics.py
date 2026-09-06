"""Persisting a plan must not change what the plan MEANS.

Run 20260905-230030 is the whole argument. Its ``scenario-plan.json`` records
two expectations belonging to P6-M13-W3-07 as::

    "a tenant event moved the token: [REDACTED]"

The oracle prints ``a tenant event moved the token: True``. Nobody edited that
file. The plan was written through :func:`redact_obj`, whose generic key-name
heuristic reads ``token:`` as a credential assignment and masks whatever follows
it — and ``True`` followed it. The same write masked all 24 of the run's
``CommandBinding`` digests, because the field recording which approved command a
scenario was built on is called ``token`` too.

Two separate things went wrong and they need separating.

  * A **heuristic** fired on a value that cannot be a credential. ``True`` is
    not a secret; masking it protects nothing and falsifies the record. The
    codebase already reasons exactly this way one function further down — see
    :func:`_mask_secret_value`, which refuses to replace an ``int`` count with
    ``"[REDACTED]"`` because "a credential is always text". That principle was
    applied to typed values and never to textual ones.
  * An **authoritative structure** was run through an *output* redactor. The
    persisted plan is not a log. It is what a resume reloads and executes
    against, so a transform applied on the way to disk is a transform applied to
    the run's own semantics.

The asymmetry is what actually bites. At execution time the expectation was
intact and the captured stdout was masked, so the comparison failed and W3-07
went red against a product that was behaving correctly. On disk the expectation
is masked and, after a resume reloads it, would keep failing for the opposite
reason. Redaction has to either leave both sides alone or change both; what it
must never do is change one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import redact, redact_obj
from neyma_product_driver.scenario_plan import (
    CommandBinding,
    GeneratedScenarioPlan,
    rebind_to_approved,
)
from neyma_product_driver.scenario_planner import PLAN_FILENAME, ScenarioPlanner
from neyma_product_driver.scenario_validation import ApprovedCommands, citation_token
from neyma_product_driver.scenarios import Scenario, ServiceSpec

from scenario_fixtures import (
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    raw_payload,
    raw_scenario,
)

# --------------------------------------------------------------------------
# The W3-07 shape, reduced to its bones
# --------------------------------------------------------------------------

#: The approved oracle. Its NAME is the identity a body repair leaves alone.
ORACLE_NAME = "the brake version token binds both owners and is monotonic"
ORACLE_BODY = "./probe.sh version-token --owners both"
ORACLE_REPAIRED = "./probe.sh version-token --owners both --strict"

#: A second approved command, so the scenario has an operation as well as an
#: oracle — the shape every generated case actually has.
BATTERY_NAME = "the asymmetric version omission battery"
BATTERY_BODY = "./probe.sh omission-battery --owners both"

#: Where the oracle's binding lives, once the scenario is assembled.
ORACLE_FIELD = "persisted_state_checks[0].command"

#: The expectation that run 20260905-230030 lost. A harmless diagnostic boolean
#: printed by the oracle, whose left-hand side happens to end in the word the
#: credential heuristic looks for.
DIAGNOSTIC = "a tenant event moved the token: True"
DIAGNOSTIC_2 = "a platform event moved the token: True"

#: An actual credential, in the same assignment shape, for the other direction.
REAL_SECRET = "AUTH_TOKEN: s3cr3tvalue123"
REAL_SECRET_MASKED = "AUTH_TOKEN: [REDACTED]"


def _harness(oracle_body: str = ORACLE_BODY) -> Scenario:
    return Scenario(
        name="backend_generic",
        mode="backend",
        setup=["./probe.sh seed"],
        services=[ServiceSpec(name="api", command="./serve.sh")],
        readiness=[{"tcp": "127.0.0.1:8931"}],
        app_url="http://127.0.0.1:8931",
        commands=[
            {"name": ORACLE_NAME, "run": oracle_body},
            {"name": BATTERY_NAME, "run": BATTERY_BODY},
        ],
        expect_state=[
            {"name": "payments", "command": "./probe.sh payments", "contains": ["ok"]}
        ],
        teardown=["./probe.sh reset"],
    )


def _generation_config():
    from neyma_product_driver.config import ScenarioGenerationConfig

    return ScenarioGenerationConfig(enabled=True, max_waves=4, max_initial_scenarios=4)


def _planner(tmp_path: Path, run_id: str, harness: Scenario, payloads=()) -> ScenarioPlanner:
    return ScenarioPlanner(
        repo=tmp_path,
        config=_generation_config(),
        reasoner=ScriptedReasoner(list(payloads)),
        store=EvidenceStore(tmp_path, run_id),
        base_scenario=harness,
        permanent_scenarios=[harness],
        founder=FakeFounder(),
        emit=lambda _m: None,
    )


def _cited_case(observations: list[str]) -> dict:
    """A proposal that cites the oracle and expects ``observations`` from it."""
    return raw_scenario(
        "gen-version-token-binds-both-owners",
        risk_category="safety_invariant",
        actions=[
            {
                "kind": "command",
                "name": "asymmetric-omission-battery",
                "command": f"@{citation_token(BATTERY_BODY)}",
                "expect_exit_code": 0,
            }
        ],
        state_checks=[
            {
                "name": "composite-token-binds-both-owners",
                "command": f"@{citation_token(ORACLE_BODY)}",
                "contains": list(observations),
                "not_contains": ["READ AS OFF"],
            }
        ],
        expected_observations=list(observations),
        forbidden_observations=["READ AS OFF"],
        setup=[],
        cleanup=[],
        isolation_note="every probe runs against a fresh temporary database",
    )


def _oracle_binding(scenario) -> CommandBinding:
    """The binding for the field the oracle occupies, selected by field rather
    than by position — position is an assembly detail, the field is the key a
    persisted binding is actually written under."""
    return next(b for b in scenario.command_bindings if b.field == ORACLE_FIELD)


def _plan_a_run(tmp_path: Path, run_id: str, observations: list[str]) -> ScenarioPlanner:
    planner = _planner(
        tmp_path,
        run_id,
        _harness(),
        payloads=[raw_payload(_cited_case(observations))],
    )
    planner.plan_initial(task="bind the brake version", unit=FakeUnit(), run_id=run_id)
    assert planner.plan.scenarios, "the fixture must produce a plan to carry across"
    return planner


def _reload(planner: ScenarioPlanner) -> GeneratedScenarioPlan:
    """Exactly what a resume reads: the bytes on disk, through the model."""
    return GeneratedScenarioPlan.model_validate_json(
        (planner.store.run_dir / PLAN_FILENAME).read_text(encoding="utf-8")
    )


# --------------------------------------------------------------------------
# R1. The semantic expectation must survive persistence
# --------------------------------------------------------------------------


class TestAnExpectationMeansOnDiskWhatItMeantInMemory:
    def test_the_diagnostic_boolean_survives_the_round_trip(self, tmp_path):
        """R1. The exact defect, at the exact layer that caused it."""
        planner = _plan_a_run(tmp_path, "20260905-000001", [DIAGNOSTIC, DIAGNOSTIC_2])
        reloaded = _reload(planner)

        scenario = reloaded.scenarios[0]
        assert scenario.expected_observations == [DIAGNOSTIC, DIAGNOSTIC_2]
        assert scenario.persisted_state_checks[0].contains == [DIAGNOSTIC, DIAGNOSTIC_2]

    def test_nothing_in_the_executable_plan_is_masked(self, tmp_path):
        """No expectation the run will assert against may carry the mask."""
        planner = _plan_a_run(tmp_path, "20260905-000002", [DIAGNOSTIC, DIAGNOSTIC_2])
        reloaded = _reload(planner)

        for scenario in reloaded.scenarios:
            for observation in scenario.expected_observations:
                assert "[REDACTED]" not in observation
            for check in scenario.persisted_state_checks:
                assert not any("[REDACTED]" in c for c in check.contains)

    def test_persistence_is_semantically_a_no_op(self, tmp_path):
        """The general invariant, not the one example: what a resume reloads is
        what the run decided, field for field."""
        planner = _plan_a_run(tmp_path, "20260905-000003", [DIAGNOSTIC, DIAGNOSTIC_2])
        in_memory = planner.plan.scenarios[0]
        on_disk = _reload(planner).scenarios[0]

        assert on_disk.model_dump(mode="json") == in_memory.model_dump(mode="json")

    def test_a_resume_asserts_the_unmasked_expectation(self, tmp_path):
        """The consequence W3-07 actually suffered: a resumed run must compare
        against ``True``, not against the mask."""
        run_id = "20260905-000004"
        _plan_a_run(tmp_path, run_id, [DIAGNOSTIC, DIAGNOSTIC_2])
        resumed = _planner(tmp_path, run_id, _harness())
        assert resumed.restore_from_store().state == "restored"

        compiled = resumed.compiled["gen-version-token-binds-both-owners"]
        check = next(
            step.state_check for step in compiled.steps if step.state_check is not None
        )
        assert check.contains == [DIAGNOSTIC, DIAGNOSTIC_2]
        assert compiled.expect_visible == [DIAGNOSTIC, DIAGNOSTIC_2]


# --------------------------------------------------------------------------
# R2. The internal binding identity must survive persistence
# --------------------------------------------------------------------------


class TestTheBindingIdentitySurvivesPersistence:
    def test_the_digest_is_recorded_at_binding_time(self, tmp_path):
        planner = _plan_a_run(tmp_path, "20260905-000010", [DIAGNOSTIC])
        binding = _oracle_binding(planner.plan.scenarios[0])

        assert binding.command_digest == citation_token(ORACLE_BODY)
        assert binding.source_name == ORACLE_NAME

    def test_the_digest_survives_the_round_trip_through_disk(self, tmp_path):
        """R2. 24 of 24 bindings in run 20260905-230030 read ``[REDACTED]``."""
        planner = _plan_a_run(tmp_path, "20260905-000011", [DIAGNOSTIC])
        binding = _oracle_binding(_reload(planner).scenarios[0])

        assert binding.command_digest == citation_token(ORACLE_BODY)
        assert binding.source_name == ORACLE_NAME

    def test_the_identity_is_shaped_so_it_cannot_carry_a_credential(self):
        """Why exempting it is provable rather than declared: the field only
        ever holds eight hex characters, and refuses anything else."""
        assert CommandBinding(field="f", command_digest="0255a159").command_digest == "0255a159"
        assert CommandBinding(field="f").command_digest == ""
        with pytest.raises(ValueError):
            CommandBinding(field="f", command_digest="sk-ant-abcdefghij")
        with pytest.raises(ValueError):
            CommandBinding(field="f", command_digest="[REDACTED]")

    def test_a_plan_written_before_the_rename_still_reads(self, tmp_path):
        """Migration. Every plan on disk today spells it ``token``, and every
        one of those values was already destroyed by the write that stored it —
        so the legacy key is read, and a masked legacy value is read as the
        absence it actually is rather than as a digest."""
        assert CommandBinding.model_validate(
            {"field": "f", "token": "0255a159", "source_name": ORACLE_NAME}
        ).command_digest == "0255a159"
        assert CommandBinding.model_validate(
            {"field": "f", "token": "[REDACTED]", "source_name": ORACLE_NAME}
        ).command_digest == ""

    def test_a_generic_key_named_token_is_still_masked(self):
        """The exemption is for this typed field, not for the WORD."""
        assert redact_obj({"token": "s3cr3tvalue123"}) == {"token": "[REDACTED]"}
        assert redact_obj({"api_key": "s3cr3tvalue123"}) == {"api_key": "[REDACTED]"}


# --------------------------------------------------------------------------
# R3. A real secret is still a real secret
# --------------------------------------------------------------------------


class TestRealSecretsAreStillRedacted:
    @pytest.mark.parametrize(
        "text",
        [
            "AUTH_TOKEN: s3cr3tvalue123",
            "api_key=abcdefghijklmnop",
            "PASSWORD: hunter2hunter2",
            "CREDENTIAL: abcd1234efgh",
            "sk-ant-abcdefghijklmnop",
            "ghp_abcdefghijklmnopqrst",
            "AKIAABCDEFGHIJKLMNOP",
        ],
    )
    def test_credential_shaped_values_are_masked(self, text):
        cleaned = redact(text)
        assert "REDACTED" in cleaned
        assert text.split(":")[-1].split("=")[-1].strip() not in cleaned

    def test_a_credential_in_an_expectation_is_masked_on_the_way_to_disk(self, tmp_path):
        """The authoritative plan is exempt from the *heuristic*, never from the
        patterns that identify a credential by its own shape."""
        planner = _plan_a_run(
            tmp_path, "20260905-000020", [DIAGNOSTIC, "leaked sk-ant-abcdefghijklmnop"]
        )
        text = (planner.store.run_dir / PLAN_FILENAME).read_text(encoding="utf-8")

        assert "sk-ant-abcdefghijklmnop" not in text
        assert DIAGNOSTIC in text

    def test_the_plan_file_never_contains_a_recognisable_credential(self, tmp_path):
        planner = _plan_a_run(tmp_path, "20260905-000021", [DIAGNOSTIC])
        planner.plan.assumptions = ["the deploy key is ghp_abcdefghijklmnopqrst"]
        planner.persist()
        text = (planner.store.run_dir / PLAN_FILENAME).read_text(encoding="utf-8")

        assert "ghp_abcdefghijklmnopqrst" not in text

    def test_untrusted_echoed_text_keeps_the_full_heuristic(self, tmp_path):
        """The generation basis carries rendered product failures. That is
        foreign output, and it keeps the blunt instrument."""
        planner = _plan_a_run(tmp_path, "20260905-000022", [DIAGNOSTIC])
        planner.plan.generation_basis.prior_failures = [REAL_SECRET]
        planner.persist()

        assert _reload(planner).generation_basis.prior_failures == [REAL_SECRET_MASKED]


# --------------------------------------------------------------------------
# The redactor's own rule: a value that cannot be a credential
# --------------------------------------------------------------------------


class TestTheHeuristicDoesNotFalsifyValuesThatCannotBeCredentials:
    @pytest.mark.parametrize(
        "text",
        [
            "a tenant event moved the token: True",
            "token: False",
            "api_key: None",
            "secret: null",
            "the signal count after 25 signals: 25",
            "password: 0",
            "credential_version: 3.14",
        ],
    )
    def test_a_literal_is_left_exactly_as_it_was(self, text):
        assert redact(text) == text

    def test_the_captured_output_side_is_fixed_too(self):
        """W3-07 went red at EXECUTION time, before any resume: the expectation
        was intact and the captured stdout was masked. Both sides run through
        this one function, so both sides are repaired by this one rule."""
        stdout = "after a TENANT event: bv1|global:0|tenant:1\n" + DIAGNOSTIC
        assert DIAGNOSTIC in redact(stdout)

    def test_a_typed_value_under_a_secret_key_is_still_left_alone(self):
        """Unchanged: the principle this rule extends was already applied here."""
        assert redact_obj({"by_risk_category": {"authorization": 3}}) == {
            "by_risk_category": {"authorization": 3}
        }


# --------------------------------------------------------------------------
# R4 / R5. Rebinding still tells a repair from an intact citation
# --------------------------------------------------------------------------


class TestExactIdentityAndStaleBodies:
    def test_an_unchanged_command_needs_no_rebinding_and_is_exact(self, tmp_path):
        """R5. The common case resolves by the command's own bytes against the
        current approved set — not by falling back to a name."""
        run_id = "20260905-000030"
        _plan_a_run(tmp_path, run_id, [DIAGNOSTIC])
        resumed = _planner(tmp_path, run_id, _harness())
        assert resumed.restore_from_store().state == "restored"

        scenario = resumed.plan.scenarios[0]
        approved = ApprovedCommands.from_sources(scenarios=[_harness()])
        rebindings, problems = rebind_to_approved(scenario, approved)

        assert rebindings == []
        assert problems == []
        assert resumed.rebound_scenario_ids == []
        # The identity that says WHICH approved command this was built on is
        # still on disk, and still resolves in the current set.
        binding = _oracle_binding(scenario)
        assert binding.command_digest in approved.by_token

    def test_a_repaired_body_still_rematerializes(self, tmp_path):
        """R4. The behaviour the earlier resume-continuity work established, on
        a plan whose binding actually survived the write."""
        run_id = "20260905-000031"
        _plan_a_run(tmp_path, run_id, [DIAGNOSTIC])
        resumed = _planner(tmp_path, run_id, _harness(ORACLE_REPAIRED))
        assert resumed.restore_from_store().state == "restored"

        scenario = resumed.plan.scenarios[0]
        assert scenario.persisted_state_checks[0].command == ORACLE_REPAIRED
        assert resumed.rebound_scenario_ids == ["gen-version-token-binds-both-owners"]

    def test_the_repair_is_reported_as_identified_by_name(self, tmp_path):
        """A repair is precisely the case the digest cannot survive, so the
        rebinding says which identity carried it."""
        run_id = "20260905-000032"
        planner = _plan_a_run(tmp_path, run_id, [DIAGNOSTIC])
        scenario = _reload(planner).scenarios[0]
        repaired = ApprovedCommands.from_sources(scenarios=[_harness(ORACLE_REPAIRED)])

        rebindings, problems = rebind_to_approved(scenario, repaired)

        assert problems == []
        assert [r.identified_by for r in rebindings] == ["source_name"]
        assert rebindings[0].after == ORACLE_REPAIRED

    def test_a_stale_body_is_never_what_runs(self, tmp_path):
        run_id = "20260905-000033"
        planner = _plan_a_run(tmp_path, run_id, [DIAGNOSTIC])
        scenario = _reload(planner).scenarios[0]
        repaired = ApprovedCommands.from_sources(scenarios=[_harness(ORACLE_REPAIRED)])
        rebind_to_approved(scenario, repaired)

        assert ORACLE_BODY not in scenario.command_strings()
        for command in scenario.command_strings():
            assert repaired.approves(command)[0], command


# --------------------------------------------------------------------------
# R6. The W3-03 citation, classified
# --------------------------------------------------------------------------


class TestAMiscitedOracleIsNotAPersistenceDefect:
    """P6-M13-W3-03 named a state check ``one-active-row-no-release-rising-count``
    and cited, for it, the oracle that proves a brake row is never deleted. That
    oracle prints nothing about active rows or signal counts, so all four
    expectations missed.

    The citation resolved that way in ``scenario-generation/wave-03.json``,
    which is written at generation time, before the plan is persisted and before
    anything is redacted. The binding recorded faithfully what the generator
    cited. Nothing in this file's subject matter can produce that outcome or
    repair it — the proposal named the wrong oracle.
    """

    def test_a_binding_records_the_oracle_that_was_cited_not_the_one_that_fits(
        self, tmp_path
    ):
        planner = _plan_a_run(tmp_path, "20260905-000040", [DIAGNOSTIC])
        binding = _oracle_binding(_reload(planner).scenarios[0])

        assert binding.command_digest == citation_token(ORACLE_BODY)
        assert binding.source_name == ORACLE_NAME

    def test_the_run_recorded_the_miscitation_before_any_persistence(self):
        """The evidence, read from the run itself. ``resolved_citations`` is the
        generator's own record of what each ``@token`` expanded to."""
        wave = Path(__file__).resolve().parents[1] / (
            "runs/20260905-230030/scenario-generation/wave-03.json"
        )
        if not wave.exists():  # pragma: no cover - the run is not a test fixture
            pytest.skip("run 20260905-230030 is not present")
        cited = [
            line
            for line in json.loads(wave.read_text())["resolved_citations"]
            if line.startswith("P6-M13-W3-03:")
        ]
        state_check_citation = cited[1]

        assert "a brake row is never deleted" not in state_check_citation
        assert "delete from brakes" in state_check_citation
        assert "flapping" not in state_check_citation

"""A generated scenario's EXPECTATIONS must be reconstructable, not only its command.

Run ``20260905-230030`` built P6/M13 to completion — the permanent scenario
``p6_m13_brake`` passes 974/974, the probe reports ``behaviours as specified, 0
wrong``, the mutation battery catches every mutant, the AST oracle reports one
brake authority — and could not reach an independent review, on two GENERATED
scenarios that could not pass against a correct product. Neither was a product
defect and neither was reachable by anything the resume ran.

``P6-M13-W3-07`` is the **stale expectation**. Its persisted command WAS
re-materialized against the repaired oracle: the plan records
``persisted_state_checks[0].command was re-materialized from the approved
command 'the brake version token binds both owners…'``. Only the command. The
literals it is measured by stayed frozen at the superseded wording, so the case
ran the corrected instrument against the sentence the broken one printed.
:func:`rebind_to_approved` walks :meth:`GeneratedScenario.command_slots` and a
``contains`` list is not a command slot.

``P6-M13-W3-03`` is the **miscitation**. It asserts the four sentences the
flapping-detector oracle prints, and runs the no-DELETE oracle, which prints
none of them. Its binding is *self-consistent* — the command it cites is
approved verbatim — so ``approves(before)`` is true, rebinding skips the field
entirely, and the case has never been testing what its risk claim says it
tests. The generation-time attribution rule did not catch it either: that rule
contests a literal only when the invocation NARROWS an approved form the
literal is bound to, and a verbatim approved command narrows nothing.

One invariant covers both. A resume reconstructs the executable half of the
verification plan from current approved authority and the expectation half from
a frozen snapshot, and the two halves drift. Historical evidence is immutable;
the ACTIVE plan is not evidence, and must be reconstructable.

Nothing here consumes Claude usage. Every recorded output in this file was
recorded from the real oracles in the Neyma checkout, and
:meth:`TestTheRecordingIsReal.test_every_recording_still_matches_the_live_oracle`
re-runs each one so it cannot quietly become fiction.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from neyma_product_driver.models import redact, redact_persisted
from neyma_product_driver.scenario_plan import (
    CommandBinding,
    GeneratedScenario,
    ObservationBinding,
    compile_to_scenario,
    rebind_to_approved,
)
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    ContractProbeResult,
    ValidationContext,
    _norm_command,
    bind_observations,
    contested_producers,
    established_observations_from,
    reconstruct_miscited_commands,
    rebind_observations_to_approved,
    restored_coherence_problems,
    validate_scenario,
)
from neyma_product_driver.scenarios import Scenario, load_scenario

DRIVER_ROOT = Path(__file__).resolve().parents[1]
M13_PATH = DRIVER_ROOT / "scenarios" / "p6_m13_brake.yaml"
RUN = DRIVER_ROOT / "runs" / "20260905-230030"
PLAN = RUN / "scenario-plan.json"
NEYMA = Path("/Users/sammyfammy/freight-logistics-operational-teammate")

# The three approved oracles this file is about, by the NAME a human wrote
# beside them. The command bodies are read out of the scenario file at runtime —
# naming them here instead would be the frozen copy this whole module refuses.
VERSION_ORACLE = (
    "the brake version token binds both owners and is monotonic, and an "
    "unreadable store refuses rather than reading as off"
)
FLAPPING_ORACLE = "a flapping detector yields one ACTIVE brake row and a rising signal count"
NO_DELETE_ORACLE = "a brake row is never deleted, and the incident record is permanent"

#: The superseded wording, as run 20260905-230030 persisted it. `[REDACTED]` is
#: not a typo: the pre-647cc56 redactor read "token:" as an assignment of a
#: credential and masked the `True` the oracle printed, so the plan on disk
#: carries a sentence no product could ever print. Both halves of the staleness
#: — repaired wording AND corrupted persistence — resolve the same way.
STALE_TENANT = "a tenant event moved the token: [REDACTED]"
STALE_PLATFORM = "a platform event moved the token: [REDACTED]"
CURRENT_TENANT = "a tenant event moved the composite version: True"
CURRENT_PLATFORM = "a platform event moved the composite version: True"
SHARED_LITERAL = "another tenant sees the same platform component: True"

FLAPPING_LITERALS = [
    "ACTIVE rows for the scope: 1",
    "no RELEASED row was ever created: 0",
    "the brake version was not bumped by a repeat: True",
    "the signal count after 25 signals: 25",
]

#: oracle name -> exactly what it printed, recorded off the real checkout.
RECORDING: dict[str, str] = {
    NO_DELETE_ORACLE: """the brake triggers: ['trg_brakes_no_delete', 'trg_platform_brake_no_delete']
a delete-refusing trigger exists on brakes: True
a brake was engaged through the one authority: ACTIVE
a DELETE against a brake row: refused by a brake row is never deleted [16-brake.md point 28, C-9]: the engage/widen/narrow/release history IS the incident timeline and its retention is permanent
a DELETE against every brake row: refused by a brake row is never deleted [16-brake.md point 28, C-9]: the engage/widen/narrow/release history IS the incident timeline and its retention is permanent
a DELETE against the platform brake row: refused by the platform brake row is never deleted [SD-12, C-9]: an absent platform row reads as unreadable and REFUSES admission, which is not the same fact as a released brake
positive control, a lawful release through the one authority: RELEASED
brake rows surviving: 1
the surviving row state: RELEASED
platform rows surviving: 1
""",
    VERSION_ORACLE: """the quiet composite version: bv1|global:0|tenant:0
the token names both owners: True
after a TENANT event: bv1|global:0|tenant:1
a tenant event moved the composite version: True
after a PLATFORM event: bv1|global:1|tenant:1
a platform event moved the composite version: True
another tenant sees the same platform component: True
another tenant has its own tenant component: True
every token was distinct: True
the platform component is monotonic: True
the tenant component is monotonic: True
the platform component never went backwards: True
the tenant component never went backwards: True
positive control, admission is readable while the row exists: True
an unreadable brake store at the admission read: refused by BrakeStoreUnreachable
an unreadable brake store at the version derivation: refused by BrakeStoreUnreachable
an unreadable brake store at the operator report: refused by BrakeStoreUnreachable
the platform table is present but EMPTY: 0
an absent platform ROW at the admission read: refused by BrakeStoreUnreachable
an absent platform ROW at the version derivation: refused by BrakeStoreUnreachable
""",
    FLAPPING_ORACLE: """distinct brake ids across 25 engagements: 1
ACTIVE rows for the scope: 1
rows in any state for the scope: 1
the brake version was not bumped by a repeat: True
no RELEASED row was ever created: 0
a signal count is recorded on the row: True
the signal count after 25 signals: 25
""",
}


# --------------------------------------------------------------------------
# Fixtures — the real artifact, read only
# --------------------------------------------------------------------------


def m13() -> Scenario:
    return load_scenario(M13_PATH)


def oracle_command(name: str, scenario: Scenario | None = None) -> str:
    """The body a human currently writes under ``name``."""
    for check in (scenario or m13()).expect_state:
        if check.name == name:
            return check.command
    raise AssertionError(f"no approved oracle named {name!r}")


def oracle_key(name: str, scenario: Scenario | None = None) -> str:
    """The same body as `established_observations_from` keys it."""
    return _norm_command(oracle_command(name, scenario))


def recorded_probe(scenario: Scenario | None = None, extra: dict[str, str] | None = None) -> Any:
    """A contract probe answering only from recorded output, keyed by command."""
    target = scenario or m13()
    table = {oracle_command(name, target): output for name, output in RECORDING.items()}
    table.update(extra or {})
    asked: list[str] = []

    def probe(command: str) -> ContractProbeResult:
        asked.append(command)
        if command not in table:
            return ContractProbeResult(False, detail="no recording for this invocation")
        return ContractProbeResult(True, output=table[command])

    probe.asked = asked  # type: ignore[attr-defined]
    return probe


def m13_context(scenario: Scenario | None = None, **overrides: Any) -> ValidationContext:
    target = scenario or m13()
    defaults: dict[str, Any] = {
        "approved_commands": ApprovedCommands.from_sources(scenarios=[target]),
        "established_observations": established_observations_from([target]),
        "contract_probe": recorded_probe(target),
        "grounding_tokens": {"p6/m13", "p6", "m13", "ac-safe-006"},
        "principle_tokens": {"operational_clarity", "outcome_verification"},
    }
    defaults.update(overrides)
    return ValidationContext(**defaults)


def plan_payload(scenario_id: str) -> dict[str, Any]:
    """One scenario exactly as run 20260905-230030 persisted it."""
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    return copy.deepcopy(next(s for s in plan["scenarios"] if s["id"] == scenario_id))


def plan_scenario(scenario_id: str, **overrides: Any) -> GeneratedScenario:
    payload = plan_payload(scenario_id)
    payload.update(overrides)
    return GeneratedScenario.model_validate(payload)


def restore(scenario: GeneratedScenario, context: ValidationContext) -> dict[str, Any]:
    """Everything the resume path does to one restored scenario, in order.

    The same sequence :meth:`ScenarioPlanner._restore_plan` runs, called
    directly so a test can see each half's contribution.
    """
    approved, established = context.approved_commands, context.established_observations
    notes: list[str] = []
    problems: list[str] = []

    rebindings, unreconstructable = rebind_to_approved(scenario, approved)
    notes += [r.brief() for r in rebindings]
    problems += unreconstructable

    observation_rebindings, unreconstructable = rebind_observations_to_approved(
        scenario, approved, established, probe=context.contract_probe
    )
    notes += [r.brief() for r in observation_rebindings]
    problems += unreconstructable

    coherence = restored_coherence_problems(scenario, context)
    if coherence:
        reconstructed, unreconstructable = reconstruct_miscited_commands(
            scenario, approved, established
        )
        notes += [r.brief() for r in reconstructed]
        problems += unreconstructable
        coherence = restored_coherence_problems(scenario, context)
    return {"notes": notes, "problems": problems, "coherence": coherence}


def digest_tree(root: Path) -> str:
    """A digest of every byte under ``root``, so a mutation cannot hide."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


# --------------------------------------------------------------------------
# A synthetic repository, so nothing here is an M13 special case
# --------------------------------------------------------------------------


def synthetic(
    *, repaired: bool = False, extra_checks: list[dict[str, Any]] | None = None
) -> Scenario:
    """Two named oracles in a fake repository. ``repaired`` renames one sentence.

    The whole correction is exercised against this as well as against the real
    artifact: if it only worked on M13 it would be a patch, not an invariant.
    """
    moved = "the counter moved: renamed" if repaired else "the counter moved: True"
    return Scenario(
        name="synthetic",
        mode="backend",
        commands=[{"name": "smoke", "run": "./probe.sh"}],
        expect_state=[
            {
                "name": "the counter oracle",
                "command": "./counter.sh",
                "contains": [moved, "the counter is shared: True"],
            },
            {
                "name": "the ledger oracle",
                "command": "./ledger.sh",
                "contains": ["ledger rows: 1", "ledger was never deleted: 0"],
            },
            *(extra_checks or []),
        ],
    )


def synthetic_context(scenario: Scenario, **overrides: Any) -> ValidationContext:
    recording = {
        "./counter.sh": "\n".join(scenario.expect_state[0].contains) + "\n",
        "./ledger.sh": "\n".join(scenario.expect_state[1].contains) + "\n",
    }

    def probe(command: str) -> ContractProbeResult:
        if command not in recording:
            return ContractProbeResult(False, detail="no recording for this invocation")
        return ContractProbeResult(True, output=recording[command])

    defaults: dict[str, Any] = {
        "approved_commands": ApprovedCommands.from_sources(scenarios=[scenario]),
        "established_observations": established_observations_from([scenario]),
        "contract_probe": probe,
        "grounding_tokens": {"u-1"},
        "principle_tokens": {"effect-truth"},
    }
    defaults.update(overrides)
    return ValidationContext(**defaults)


def synthetic_scenario(command: str, contains: list[str], **overrides: Any) -> GeneratedScenario:
    data: dict[str, Any] = {
        "id": "syn-1",
        "title": "a repeat does not move the counter",
        "purpose": "a repeated request must not move the shared counter or open a window",
        "risk_category": "idempotency",
        "priority": "P0",
        "requirement_reference": "U-1",
        "product_principle_reference": "effect-truth",
        "actions": [{"kind": "command", "name": "drive", "command": "./probe.sh"}],
        "persisted_state_checks": [
            {"name": "the oracle", "command": command, "contains": list(contains)}
        ],
        "expected_observations": list(contains),
        "provenance": {
            "generating_risk": "a repeat moves the counter",
            "task_hash": "t",
            "repository_head": "0" * 40,
            "active_unit_id": "U-1",
            "stage": "initial",
            "wave": 1,
            "model": "opus",
            "session_id": "fixture",
        },
    }
    data.update(overrides)
    return GeneratedScenario.model_validate(data)


# ==========================================================================
# The artifacts are real
# ==========================================================================


class TestTheArtifactsAreReal:
    def test_the_run_is_preserved_and_records_both_cases(self) -> None:
        assert PLAN.exists(), "the run this correction came from is preserved"
        w307, w303 = plan_scenario("P6-M13-W3-07"), plan_scenario("P6-M13-W3-03")
        assert STALE_TENANT in w307.persisted_state_checks[0].contains
        assert STALE_PLATFORM in w307.persisted_state_checks[0].contains
        assert w307.rebound_on_resume, "the command half was already re-materialized"
        assert w307.rebound_on_resume[0].startswith("persisted_state_checks[0].command")
        assert w303.persisted_state_checks[0].contains == FLAPPING_LITERALS
        assert not w303.rebound_on_resume, "nothing ever touched the miscitation"

    def test_the_gate_recorded_exactly_these_two_as_the_blocker(self) -> None:
        outcome = json.loads((RUN / "journal.json").read_text(encoding="utf-8"))["outcome"]
        assert outcome["gate_status"] == "NOT_VERIFIED"
        assert (outcome["required_passed"], outcome["required_total"]) == (9, 11)
        assert len(outcome["unverified"]) == 2
        assert all(
            any(sid in row for row in outcome["unverified"])
            for sid in ("P6-M13-W3-07", "P6-M13-W3-03")
        )

    def test_the_current_repository_owns_the_replacement_sentences(self) -> None:
        """Both reconstructions are read out of current authority, not invented."""
        established = established_observations_from([m13()])
        version = established[oracle_key(VERSION_ORACLE)]
        assert {CURRENT_TENANT, CURRENT_PLATFORM, SHARED_LITERAL} <= set(version)
        assert STALE_TENANT not in version and STALE_PLATFORM not in version
        flapping = established[oracle_key(FLAPPING_ORACLE)]
        assert set(FLAPPING_LITERALS) <= set(flapping)
        no_delete = established[oracle_key(NO_DELETE_ORACLE)]
        assert not (set(FLAPPING_LITERALS) & set(no_delete))


# ==========================================================================
# A — a changed approved command re-materializes the expectations it owns
# ==========================================================================


class TestAChangedCommandRematerializesItsExpectations:
    def test_synthetic_repair_moves_the_owned_literal(self) -> None:
        """Generic: bind against one repository, resume against the repaired one."""
        before, after = synthetic(), synthetic(repaired=True)
        scenario = synthetic_scenario("./counter.sh", ["the counter moved: True"])
        bind_observations(
            scenario,
            ApprovedCommands.from_sources(scenarios=[before]),
            established_observations_from([before]),
        )
        assert [b.literals for b in scenario.observation_bindings] == [
            ["the counter moved: True"]
        ]

        rebindings, unreconstructable = rebind_observations_to_approved(
            scenario,
            ApprovedCommands.from_sources(scenarios=[after]),
            established_observations_from([after]),
        )
        assert unreconstructable == []
        assert rebindings, "the repaired oracle did not re-materialize its expectations"
        contains = scenario.persisted_state_checks[0].contains
        assert "the counter moved: True" not in contains, "the superseded literal survived"
        assert "the counter moved: renamed" in contains, "the current literal is not there"

    def test_the_rebinding_says_what_it_replaced_and_why(self) -> None:
        before, after = synthetic(), synthetic(repaired=True)
        scenario = synthetic_scenario("./counter.sh", ["the counter moved: True"])
        bind_observations(
            scenario,
            ApprovedCommands.from_sources(scenarios=[before]),
            established_observations_from([before]),
        )
        rebindings, _ = rebind_observations_to_approved(
            scenario,
            ApprovedCommands.from_sources(scenarios=[after]),
            established_observations_from([after]),
        )
        brief = rebindings[0].brief()
        assert "persisted_state_checks[0].contains" in brief
        assert "the counter oracle" in brief
        assert "the counter moved: True" in brief

    def test_it_is_idempotent(self) -> None:
        """A second resume over an already-current plan changes nothing."""
        after = synthetic(repaired=True)
        approved = ApprovedCommands.from_sources(scenarios=[after])
        established = established_observations_from([after])
        scenario = synthetic_scenario("./counter.sh", ["the counter moved: True"])
        bind_observations(scenario, ApprovedCommands.from_sources(scenarios=[synthetic()]),
                          established_observations_from([synthetic()]))
        rebind_observations_to_approved(scenario, approved, established)
        settled = scenario.model_dump(mode="json")
        rebindings, unreconstructable = rebind_observations_to_approved(
            scenario, approved, established
        )
        assert (rebindings, unreconstructable) == ([], [])
        assert scenario.model_dump(mode="json") == settled

    def test_a_source_the_current_set_no_longer_offers_is_unreconstructable(self) -> None:
        """Fail closed. A secure inability to reconstruct stays blocked."""
        before = synthetic()
        scenario = synthetic_scenario("./counter.sh", ["the counter moved: True"])
        bind_observations(
            scenario,
            ApprovedCommands.from_sources(scenarios=[before]),
            established_observations_from([before]),
        )
        gone = Scenario(
            name="synthetic",
            mode="backend",
            expect_state=[
                {"name": "the ledger oracle", "command": "./ledger.sh", "contains": ["ledger rows: 1"]}
            ],
        )
        rebindings, unreconstructable = rebind_observations_to_approved(
            scenario,
            ApprovedCommands.from_sources(scenarios=[gone]),
            established_observations_from([gone]),
        )
        assert rebindings == []
        assert unreconstructable and "the counter oracle" in unreconstructable[0]


# ==========================================================================
# F — the W3-07 class: a stale expectation behind a re-materialized command
# ==========================================================================


class TestTheStaleExpectationClass:
    def test_the_real_artifact_stops_carrying_the_superseded_sentence(self) -> None:
        scenario = plan_scenario("P6-M13-W3-07")
        outcome = restore(scenario, m13_context())
        assert outcome["problems"] == []
        contains = scenario.persisted_state_checks[0].contains
        assert STALE_TENANT not in contains
        assert STALE_PLATFORM not in contains
        assert CURRENT_TENANT in contains
        assert CURRENT_PLATFORM in contains

    def test_the_shared_literal_the_command_still_owns_is_kept(self) -> None:
        scenario = plan_scenario("P6-M13-W3-07")
        restore(scenario, m13_context())
        assert SHARED_LITERAL in scenario.persisted_state_checks[0].contains

    def test_every_surviving_literal_is_one_the_command_actually_prints(self) -> None:
        """The reconstruction is measured against recorded output, not asserted."""
        scenario = plan_scenario("P6-M13-W3-07")
        restore(scenario, m13_context())
        printed = RECORDING[VERSION_ORACLE]
        for literal in scenario.persisted_state_checks[0].contains:
            assert literal in printed, f"{literal!r} is not something the oracle prints"

    def test_the_scenario_level_observations_are_reconstructed_too(self) -> None:
        """`expected_observations` carried the same superseded pair."""
        scenario = plan_scenario("P6-M13-W3-07")
        restore(scenario, m13_context())
        assert STALE_TENANT not in scenario.expected_observations
        assert STALE_PLATFORM not in scenario.expected_observations
        assert CURRENT_TENANT in scenario.expected_observations

    def test_it_compiles_and_its_oracle_is_the_current_approved_body(self) -> None:
        context = m13_context()
        scenario = plan_scenario("P6-M13-W3-07")
        restore(scenario, context)
        approved, _ = context.approved_commands.resolve(scenario.command_strings())
        compiled = compile_to_scenario(scenario, base=None, approved_commands=approved)
        oracle = next(s.state_check for s in compiled.steps if s.state_check is not None)
        assert _norm_command(oracle.command) == oracle_key(VERSION_ORACLE)
        assert STALE_TENANT not in oracle.contains
        assert CURRENT_TENANT in oracle.contains

    def test_a_suspected_legacy_field_is_left_alone_when_nothing_can_be_asked(self) -> None:
        """Suspicion is not knowledge: with no answer, the model's literal stands.

        The legacy path has no record of what the oracle used to establish, so
        an unanswerable question must not become a deletion — that would rewrite
        a genuine product failure into a quiet pass.
        """
        after = synthetic(repaired=True)
        scenario = synthetic_scenario(
            oracle_command("the counter oracle", after),
            ["the counter moved: True"],
            command_bindings=[
                CommandBinding(
                    field="persisted_state_checks[0].command",
                    source_name="the counter oracle",
                )
            ],
            rebound_on_resume=[
                "persisted_state_checks[0].command was re-materialized from the approved "
                "command 'the counter oracle', whose body the harness has since repaired"
            ],
        )
        rebindings, unreconstructable = rebind_observations_to_approved(
            scenario,
            ApprovedCommands.from_sources(scenarios=[after]),
            established_observations_from([after]),
        )
        assert (rebindings, unreconstructable) == ([], [])
        assert scenario.persisted_state_checks[0].contains == ["the counter moved: True"]

    def test_a_synthetic_legacy_plan_with_no_binding_is_reconstructed_the_same_way(self) -> None:
        """Generic: the artifact predates the binding, and is still repaired.

        A plan written before ``observation_bindings`` existed carries no record
        of which literals came from the oracle. What it DOES carry is the note
        saying the command was re-materialized — so the literals coupled to that
        field which no current approved command establishes are the superseded
        text, and are reconstructed from the source the note names.
        """
        after = synthetic(repaired=True)
        scenario = synthetic_scenario(
            oracle_command("the counter oracle", after),
            ["the counter moved: True", "the counter is shared: True"],
            command_bindings=[
                CommandBinding(
                    field="persisted_state_checks[0].command",
                    source_name="the counter oracle",
                )
            ],
            rebound_on_resume=[
                "persisted_state_checks[0].command was re-materialized from the approved "
                "command 'the counter oracle', whose body the harness has since repaired"
            ],
        )
        assert scenario.observation_bindings == []
        rebindings, unreconstructable = rebind_observations_to_approved(
            scenario,
            ApprovedCommands.from_sources(scenarios=[after]),
            established_observations_from([after]),
            probe=synthetic_context(after).contract_probe,
        )
        assert unreconstructable == []
        assert rebindings
        contains = scenario.persisted_state_checks[0].contains
        assert "the counter moved: True" not in contains
        assert "the counter moved: renamed" in contains
        assert "the counter is shared: True" in contains, "an unaffected literal was dropped"


# ==========================================================================
# The two halves are recorded together, and repaired together
# ==========================================================================


class TestTheBindingIsWiredIntoTheRealPlanner:
    """Generation records the measurement's provenance, not only the command's."""

    @staticmethod
    def _planner(tmp_path: Path, harness: Scenario, payload: dict[str, Any]) -> Any:
        import sys

        sys.path.insert(0, str(DRIVER_ROOT / "tests"))
        from scenario_fixtures import FakeFounder, FakeUnit, ScriptedReasoner, raw_payload

        from neyma_product_driver.config import ScenarioGenerationConfig
        from neyma_product_driver.evidence import EvidenceStore
        from neyma_product_driver.scenario_planner import ScenarioPlanner

        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True, max_waves=2, max_initial_scenarios=2),
            reasoner=ScriptedReasoner([raw_payload(payload)]),
            store=EvidenceStore(tmp_path / "runs", "20260906-000001"),
            base_scenario=harness,
            permanent_scenarios=[harness],
            founder=FakeFounder(),
            emit=lambda _m: None,
        )
        planner.plan_initial(task="verify the counter", unit=FakeUnit())
        return planner

    def _payload(self) -> dict[str, Any]:
        import sys

        sys.path.insert(0, str(DRIVER_ROOT / "tests"))
        from scenario_fixtures import raw_scenario

        return raw_scenario(
            "gen-counter",
            requirement="U-042: an approved invoice is paid exactly once",
            principle="effect-truth",
            actions=[
                {
                    "kind": "request",
                    "name": "drive it",
                    "request": {"method": "POST", "path": "/approve", "expect_status": 200},
                }
            ],
            state_checks=[
                {
                    "name": "the counter",
                    "command": "./counter.sh",
                    "contains": ["the counter moved: True"],
                }
            ],
            expected_observations=["the counter moved: True"],
            forbidden_observations=[],
        )

    def _harness(self, oracle: Scenario) -> Scenario:
        """The synthetic oracles, plus what a generated scenario needs to compile."""
        return Scenario(
            name="backend_generic",
            mode="backend",
            services=[{"name": "api", "command": "./serve.sh"}],
            readiness=[{"tcp": "127.0.0.1:8931"}],
            app_url="http://127.0.0.1:8931",
            commands=list(oracle.commands),
            expect_state=list(oracle.expect_state),
        )

    def test_generation_records_which_oracle_owns_the_literals(self, tmp_path: Path) -> None:
        planner = self._planner(tmp_path, self._harness(synthetic()), self._payload())
        scenario = planner.plan.scenarios[0]
        assert scenario.observation_bindings, "the measurement half was not recorded"
        binding = scenario.observation_bindings[0]
        assert binding.field == "persisted_state_checks[0].contains"
        assert binding.command_field == "persisted_state_checks[0].command"
        assert binding.source_name == "the counter oracle"
        assert binding.literals == ["the counter moved: True"]

    def test_a_literal_the_model_wrote_itself_is_owned_by_nobody(self, tmp_path: Path) -> None:
        payload = self._payload()
        payload["persisted_state_checks"][0]["contains"] = ["A SENTENCE NOBODY WROTE DOWN"]
        payload["expected_observations"] = ["A SENTENCE NOBODY WROTE DOWN"]
        planner = self._planner(tmp_path, self._harness(synthetic()), payload)
        scenario = planner.plan.scenarios[0]
        assert scenario.observation_bindings == [], (
            "a model-authored literal was recorded as the repository's, and a later "
            "resume would feel entitled to rewrite it"
        )

    def test_the_binding_survives_the_write_and_the_read_back(self, tmp_path: Path) -> None:
        planner = self._planner(tmp_path, self._harness(synthetic()), self._payload())
        planner.persist()
        from neyma_product_driver.scenario_plan import GeneratedScenarioPlan

        path = tmp_path / "runs" / "20260906-000001" / "scenario-plan.json"
        reread = GeneratedScenarioPlan.model_validate_json(path.read_text(encoding="utf-8"))
        assert reread.scenarios[0].observation_bindings[0].literals == [
            "the counter moved: True"
        ]


class TestAnInFlightRepairRepairsBothHalves:
    """The two halves go stale together, and are repaired in the same pass.

    `also_repaired` is the in-flight case: the command was re-materialized in
    THIS resume, so the durable note that identifies a legacy field does not
    exist yet. Without it a plan would need two resumes to become coherent —
    one to record the note, one to act on it — and the first would execute.
    """

    def test_a_command_rebound_this_pass_carries_its_expectations_with_it(self) -> None:
        after = synthetic(repaired=True)
        scenario = synthetic_scenario(
            oracle_command("the counter oracle", after),
            ["the counter moved: True", "the counter is shared: True"],
            command_bindings=[
                CommandBinding(
                    field="persisted_state_checks[0].command",
                    source_name="the counter oracle",
                )
            ],
        )
        assert scenario.rebound_on_resume == [], "there is no durable note yet"
        rebindings, unreconstructable = rebind_observations_to_approved(
            scenario,
            ApprovedCommands.from_sources(scenarios=[after]),
            established_observations_from([after]),
            probe=synthetic_context(after).contract_probe,
            also_repaired={"persisted_state_checks[0].command": "the counter oracle"},
        )
        assert unreconstructable == []
        assert rebindings and rebindings[0].identified_by == "repaired_command"
        contains = scenario.persisted_state_checks[0].contains
        assert "the counter moved: True" not in contains
        assert "the counter moved: renamed" in contains

    def test_without_the_signal_the_same_field_is_left_alone(self) -> None:
        """Neither a note nor a binding: nothing licenses touching it."""
        after = synthetic(repaired=True)
        scenario = synthetic_scenario(
            oracle_command("the counter oracle", after), ["the counter moved: True"]
        )
        rebindings, unreconstructable = rebind_observations_to_approved(
            scenario,
            ApprovedCommands.from_sources(scenarios=[after]),
            established_observations_from([after]),
            probe=synthetic_context(after).contract_probe,
        )
        assert (rebindings, unreconstructable) == ([], [])
        assert scenario.persisted_state_checks[0].contains == ["the counter moved: True"]


# ==========================================================================
# C — the command half alone is not enough
# ==========================================================================


class TestCommandRebindingAloneIsNotEnough:
    def test_the_old_mechanism_leaves_the_superseded_sentence_standing(self) -> None:
        """The defect, reproduced: rebind commands only, and W3-07 still fails."""
        scenario = plan_scenario("P6-M13-W3-07")
        rebind_to_approved(scenario, m13_context().approved_commands)
        assert STALE_TENANT in scenario.persisted_state_checks[0].contains
        assert STALE_TENANT not in RECORDING[VERSION_ORACLE], (
            "the command that will run cannot print what the case demands"
        )

    def test_a_contains_list_is_not_a_command_slot(self) -> None:
        scenario = plan_scenario("P6-M13-W3-07")
        paths = {path for path, _value, _assign in scenario.command_slots()}
        assert "persisted_state_checks[0].command" in paths
        assert not any(path.endswith(".contains") for path in paths)

    def test_the_expectation_half_has_its_own_slots(self) -> None:
        scenario = plan_scenario("P6-M13-W3-07")
        slots = scenario.observation_slots()
        paths = {path for path, _cmd, _lits, _assign in slots}
        assert "persisted_state_checks[0].contains" in paths
        assert all(
            command_path in {p for p, _v, _a in scenario.command_slots()}
            for _p, command_path, _l, _a in slots
        )


# ==========================================================================
# D + E — the W3-03 class: a literal that belongs to another oracle
# ==========================================================================


class TestTheMiscitationClass:
    def test_generation_now_refuses_a_verbatim_command_that_borrows_a_sentence(self) -> None:
        """Generic: run the ledger oracle, demand the counter oracle's sentence."""
        scenario = synthetic_scenario("./ledger.sh", ["the counter moved: True"])
        reasons = validate_scenario(scenario, synthetic_context(synthetic()))
        assert reasons, "a miscitation between two approved oracles was accepted"
        assert any("the counter moved: True" in reason for reason in reasons)

    def test_the_real_artifact_is_refused_at_generation(self) -> None:
        reasons = validate_scenario(plan_scenario("P6-M13-W3-03"), m13_context())
        assert reasons
        assert any("ACTIVE rows for the scope: 1" in reason for reason in reasons)

    def test_the_refusal_names_the_oracle_the_sentence_belongs_to(self) -> None:
        reasons = " ".join(validate_scenario(plan_scenario("P6-M13-W3-03"), m13_context()))
        assert repr(oracle_key(FLAPPING_ORACLE)) in reasons, (
            "the refusal does not say which oracle the sentence belongs to"
        )

    def test_the_old_rule_alone_accepted_it(self) -> None:
        """The escape path, pinned: a verbatim approved command narrows nothing.

        `contested_producers` fired only where the invocation was a NARROWING of
        an approved form. W3-03 runs an approved form exactly, so nothing
        contested its self-attribution and the case sailed through.
        """
        established = established_observations_from([m13()])
        invocation = oracle_command(NO_DELETE_ORACLE)
        outer = [key for key in established if invocation.startswith(key) and invocation != key]
        assert outer == [], "this invocation is a whole approved form, not a narrowing"

    def test_the_new_rule_contests_it(self) -> None:
        established = established_observations_from([m13()])
        producers = contested_producers(
            oracle_command(NO_DELETE_ORACLE), "ACTIVE rows for the scope: 1", established
        )
        assert producers == (oracle_key(FLAPPING_ORACLE),)

    def test_the_restore_path_reconstructs_it_against_the_real_oracle(self) -> None:
        scenario = plan_scenario("P6-M13-W3-03")
        outcome = restore(scenario, m13_context())
        assert outcome["problems"] == []
        assert outcome["coherence"] == [], outcome["coherence"]
        assert _norm_command(scenario.persisted_state_checks[0].command) == oracle_key(
            FLAPPING_ORACLE
        )
        assert scenario.persisted_state_checks[0].contains == FLAPPING_LITERALS

    def test_the_reconstructed_oracle_actually_prints_all_four_sentences(self) -> None:
        scenario = plan_scenario("P6-M13-W3-03")
        restore(scenario, m13_context())
        printed = RECORDING[FLAPPING_ORACLE]
        for literal in scenario.persisted_state_checks[0].contains:
            assert literal in printed

    def test_the_reconstruction_records_which_oracle_it_bound_to(self) -> None:
        scenario = plan_scenario("P6-M13-W3-03")
        outcome = restore(scenario, m13_context())
        assert any(FLAPPING_ORACLE in note for note in outcome["notes"])
        binding = next(
            b for b in scenario.command_bindings
            if b.field == "persisted_state_checks[0].command"
        )
        assert binding.source_name == FLAPPING_ORACLE

    def test_an_ambiguous_owner_is_refused_rather_than_guessed(self) -> None:
        """Two oracles print the sentence: nothing here picks one."""
        ambiguous = synthetic(
            extra_checks=[
                {
                    "name": "the second counter oracle",
                    "command": "./counter2.sh",
                    "contains": ["the counter moved: True"],
                }
            ]
        )
        scenario = synthetic_scenario("./ledger.sh", ["the counter moved: True"])
        rebindings, unreconstructable = reconstruct_miscited_commands(
            scenario,
            ApprovedCommands.from_sources(scenarios=[ambiguous]),
            established_observations_from([ambiguous]),
        )
        assert rebindings == []
        assert unreconstructable and "more than one" in unreconstructable[0].lower()

    def test_literals_split_across_two_oracles_are_refused(self) -> None:
        """No field is reconstructed by taking half of one oracle and half of another."""
        scenario = synthetic_scenario(
            "./ledger.sh", ["the counter moved: True", "ledger rows: 1"]
        )
        rebindings, unreconstructable = reconstruct_miscited_commands(
            scenario,
            ApprovedCommands.from_sources(scenarios=[synthetic()]),
            established_observations_from([synthetic()]),
        )
        assert rebindings == []
        assert unreconstructable


# ==========================================================================
# B — history is evidence and is never touched
# ==========================================================================


class TestHistoryIsImmutable:
    def test_the_iteration_artifacts_are_byte_identical_after_a_reconstruction(self) -> None:
        before = {d: digest_tree(RUN / d) for d in ("iteration-01", "iteration-02")}
        context = m13_context()
        for sid in ("P6-M13-W3-07", "P6-M13-W3-03"):
            restore(plan_scenario(sid), context)
        after = {d: digest_tree(RUN / d) for d in ("iteration-01", "iteration-02")}
        assert before == after

    def test_the_persisted_plan_on_disk_is_untouched(self) -> None:
        before = PLAN.read_bytes()
        context = m13_context()
        for sid in ("P6-M13-W3-07", "P6-M13-W3-03"):
            restore(plan_scenario(sid), context)
        assert PLAN.read_bytes() == before, "a pure reconstruction wrote to the run directory"

    def test_the_historical_record_still_states_the_original_expectation(self) -> None:
        """The superseded text remains readable as what the run actually did."""
        payload = plan_payload("P6-M13-W3-07")
        assert STALE_TENANT in payload["persisted_state_checks"][0]["contains"]
        record = json.loads((RUN / "iteration-02" / "record.json").read_text(encoding="utf-8"))
        assert record, "the iteration record is still there to read"


# ==========================================================================
# G — a genuine failure is still a failure
# ==========================================================================


class TestGenuineFailuresStillBlock:
    def test_an_unbound_wrong_expectation_is_not_reconstructed_away(self) -> None:
        """Silence is not a contest. A sentence nobody wrote down still runs."""
        scenario = synthetic_scenario("./counter.sh", ["THE PRODUCT IS BROKEN: True"])
        context = synthetic_context(synthetic())
        assert restored_coherence_problems(scenario, context) == []
        rebindings, unreconstructable = reconstruct_miscited_commands(
            scenario, context.approved_commands, context.established_observations
        )
        assert (rebindings, unreconstructable) == ([], [])
        assert scenario.persisted_state_checks[0].contains == ["THE PRODUCT IS BROKEN: True"]

    def test_a_real_product_failure_survives_the_whole_restore(self) -> None:
        scenario = synthetic_scenario("./counter.sh", ["the counter moved: True"])
        context = synthetic_context(synthetic())
        bind_observations(scenario, context.approved_commands, context.established_observations)
        outcome = restore(scenario, context)
        assert outcome["problems"] == []
        assert scenario.persisted_state_checks[0].contains == ["the counter moved: True"], (
            "a case measuring a current oracle was rewritten"
        )

    def test_an_unreconstructable_case_is_reported_not_dropped(self) -> None:
        gone = Scenario(name="synthetic", mode="backend", expect_state=[])
        scenario = synthetic_scenario("./counter.sh", ["the counter moved: True"])
        bind_observations(
            scenario,
            ApprovedCommands.from_sources(scenarios=[synthetic()]),
            established_observations_from([synthetic()]),
        )
        _rebindings, unreconstructable = rebind_observations_to_approved(
            scenario,
            ApprovedCommands.from_sources(scenarios=[gone]),
            established_observations_from([gone]),
        )
        assert unreconstructable, "an unreconstructable expectation was waved through"
        assert scenario.id == "syn-1", "the obligation itself was destroyed"


# ==========================================================================
# H — everything else in the plan is left exactly as it was
# ==========================================================================


class TestUnchangedCasesAreStable:
    def test_the_other_eleven_scenarios_restore_unchanged(self) -> None:
        context = m13_context()
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        touched = {"P6-M13-W3-07", "P6-M13-W3-03"}
        checked = 0
        for raw in plan["scenarios"]:
            if raw["id"] in touched:
                continue
            scenario = GeneratedScenario.model_validate(copy.deepcopy(raw))
            before = scenario.model_dump(mode="json")
            outcome = restore(scenario, context)
            assert scenario.model_dump(mode="json") == before, (
                f"{raw['id']} was modified by a resume that should not have touched it"
            )
            assert outcome["notes"] == [], f"{raw['id']} reported a rebinding it did not need"
            checked += 1
        assert checked == 11, f"expected the other eleven, saw {checked}"

    def test_their_coverage_signatures_are_unchanged(self) -> None:
        context = m13_context()
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        for raw in plan["scenarios"]:
            if raw["id"] in {"P6-M13-W3-07", "P6-M13-W3-03"}:
                continue
            scenario = GeneratedScenario.model_validate(copy.deepcopy(raw))
            before = scenario.signature()
            restore(scenario, context)
            assert scenario.signature() == before

    def test_an_uncontested_literal_still_costs_no_execution(self) -> None:
        probe = recorded_probe()
        scenario = synthetic_scenario("./counter.sh", ["A SENTENCE NOBODY WROTE DOWN"])
        context = synthetic_context(synthetic(), contract_probe=probe)
        assert restored_coherence_problems(scenario, context) == []
        assert probe.asked == [], "an uncontested literal cost an execution"


# ==========================================================================
# I — coverage is preserved, never waived
# ==========================================================================


class TestCoverageIsPreserved:
    def test_both_risk_categories_survive_the_reconstruction(self) -> None:
        context = m13_context()
        for sid, category in (("P6-M13-W3-07", "concurrency"), ("P6-M13-W3-03", "repeated_request")):
            scenario = plan_scenario(sid)
            before = scenario.risk_category
            restore(scenario, context)
            assert scenario.risk_category == before
            assert scenario.risk_category.value == category

    def test_the_risk_linkage_and_provenance_are_carried_across(self) -> None:
        context = m13_context()
        for sid in ("P6-M13-W3-07", "P6-M13-W3-03"):
            scenario, original = plan_scenario(sid), plan_scenario(sid)
            restore(scenario, context)
            assert scenario.provenance.generating_risk == original.provenance.generating_risk
            assert scenario.priority == original.priority
            assert scenario.requirement_reference == original.requirement_reference
            assert scenario.forbidden_observations == original.forbidden_observations

    def test_the_repeated_request_case_still_measures_its_own_risk(self) -> None:
        """The four sentences its risk claim names are exactly what it now runs."""
        scenario = plan_scenario("P6-M13-W3-03")
        restore(scenario, m13_context())
        assert set(FLAPPING_LITERALS) <= set(scenario.persisted_state_checks[0].contains)
        assert "flap" in scenario.provenance.generating_risk.lower()

    def test_a_reconstruction_never_empties_a_field(self) -> None:
        context = m13_context()
        for sid in ("P6-M13-W3-07", "P6-M13-W3-03"):
            scenario = plan_scenario(sid)
            restore(scenario, context)
            for check in scenario.persisted_state_checks:
                assert check.contains, "a case was made unfalsifiable by the reconstruction"
            assert scenario.expected_observations


# ==========================================================================
# J — approval, redaction and the guards are untouched
# ==========================================================================


class TestAuthorityRedactionAndGuards:
    def test_a_reconstruction_can_only_install_a_currently_approved_command(self) -> None:
        """Mutation: an approved-set whose name resolves to something unapproved."""
        scenario = synthetic_scenario("./ledger.sh", ["the counter moved: True"])
        approved = ApprovedCommands.from_sources(scenarios=[synthetic()])
        approved.by_name = dict(approved.by_name)
        approved.by_name["the counter oracle"] = "rm -rf /"
        rebindings, unreconstructable = reconstruct_miscited_commands(
            scenario, approved, established_observations_from([synthetic()])
        )
        assert rebindings == []
        assert unreconstructable
        assert scenario.persisted_state_checks[0].command == "./ledger.sh"

    def test_every_installed_command_is_approved_as_written(self) -> None:
        context = m13_context()
        for sid in ("P6-M13-W3-07", "P6-M13-W3-03"):
            scenario = plan_scenario(sid)
            restore(scenario, context)
            for command in scenario.command_strings():
                ok, why = context.approved_commands.approves(command)
                assert ok, f"{sid} would run an unapproved command: {why}"

    def test_the_command_digest_still_names_the_body_it_was_built_on(self) -> None:
        from neyma_product_driver.scenario_validation import citation_token

        scenario = plan_scenario("P6-M13-W3-03")
        restore(scenario, m13_context())
        binding = next(
            b for b in scenario.command_bindings
            if b.field == "persisted_state_checks[0].command"
        )
        assert binding.command_digest == citation_token(oracle_command(FLAPPING_ORACLE))
        assert binding.command_digest == citation_token(
            scenario.persisted_state_checks[0].command
        )
        assert binding.tail == ""

    def test_a_persisted_binding_still_refuses_a_digest_that_is_not_one(self) -> None:
        with pytest.raises(ValueError):
            CommandBinding(field="setup[0]", command_digest="not-a-digest")

    def test_the_new_field_survives_persistence_byte_for_byte(self) -> None:
        """The literals a resume executes against go through identify-only redaction.

        This is the field the 20260905-230030 damage would land on next: a
        binding recording `…: True` must come back as `…: True`.
        """
        scenario = synthetic_scenario("./counter.sh", ["the counter moved: True"])
        scenario.observation_bindings = [
            ObservationBinding(
                field="persisted_state_checks[0].contains",
                command_field="persisted_state_checks[0].command",
                source_name="the counter oracle",
                literals=[CURRENT_TENANT, "the token names both owners: True"],
            )
        ]
        payload = redact_persisted(scenario)
        assert payload["observation_bindings"][0]["literals"] == [
            CURRENT_TENANT,
            "the token names both owners: True",
        ]
        assert GeneratedScenario.model_validate(payload).observation_bindings[0].literals == [
            CURRENT_TENANT,
            "the token names both owners: True",
        ]

    def test_a_real_credential_in_a_binding_is_still_masked(self) -> None:
        scenario = synthetic_scenario("./counter.sh", ["the counter moved: True"])
        scenario.observation_bindings = [
            ObservationBinding(
                field="persisted_state_checks[0].contains",
                command_field="persisted_state_checks[0].command",
                source_name="the counter oracle",
                literals=["key: sk-ant-abcdefghijklmnop"],
            )
        ]
        payload = redact_persisted(scenario)
        assert "sk-ant-abcdefghijklmnop" not in json.dumps(payload)
        assert "[REDACTED:anthropic-key]" in json.dumps(payload)

    def test_the_blunt_redactor_is_unchanged(self) -> None:
        assert redact("API_TOKEN: hunter2secret") == "API_TOKEN: [REDACTED]"
        assert redact("moved the token: True") == "moved the token: True"

    def test_the_reconstruction_is_not_a_way_to_add_a_command(self) -> None:
        """No slot gains a command it did not have, and none is emptied."""
        context = m13_context()
        for sid in ("P6-M13-W3-07", "P6-M13-W3-03"):
            scenario, original = plan_scenario(sid), plan_scenario(sid)
            restore(scenario, context)
            assert [p for p, _v, _a in scenario.command_slots()] == [
                p for p, _v, _a in original.command_slots()
            ]
            assert scenario.setup == original.setup
            assert scenario.cleanup == original.cleanup


# ==========================================================================
# The recording is real
# ==========================================================================


@pytest.mark.skipif(not NEYMA.exists(), reason="the Neyma checkout is not present")
class TestTheRecordingIsReal:
    @pytest.mark.parametrize("name", sorted(RECORDING))
    def test_every_recording_still_matches_the_live_oracle(self, name: str) -> None:
        proc = subprocess.run(
            oracle_command(name),
            shell=True,
            cwd=str(NEYMA),
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert proc.stdout == RECORDING[name], "the recording has become fiction"


# ==========================================================================
# Nothing is hard-coded
# ==========================================================================


class TestNothingIsHardCoded:
    """The rule derives every sentence from the repository at runtime.

    Prose is exempt and only prose: a docstring may narrate the defect this
    correction came from — several already do, and a reader who cannot see the
    artifact cannot check the reasoning. What may never happen is a sentence
    from either case reaching a *value* the driver computes with.
    """

    @staticmethod
    def executable_strings(path: Path) -> list[str]:
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    def test_no_sentence_from_either_case_is_a_value_the_driver_computes_with(self) -> None:
        sentences = [STALE_TENANT, CURRENT_TENANT, CURRENT_PLATFORM, *FLAPPING_LITERALS]
        for path in (DRIVER_ROOT / "neyma_product_driver").glob("*.py"):
            for text in self.executable_strings(path):
                for sentence in sentences:
                    assert sentence not in text, f"{path.name} hard-codes {sentence!r}"

    def test_neither_oracle_name_is_a_value_the_driver_computes_with(self) -> None:
        for path in (DRIVER_ROOT / "neyma_product_driver").glob("*.py"):
            for text in self.executable_strings(path):
                for name in (FLAPPING_ORACLE, NO_DELETE_ORACLE, VERSION_ORACLE):
                    assert name not in text, f"{path.name} hard-codes an M13 oracle name"

    def test_the_new_rules_name_no_scenario_and_no_run(self) -> None:
        """Not even the run this came from is a value anything branches on."""
        source = (DRIVER_ROOT / "neyma_product_driver" / "scenario_validation.py")
        for text in self.executable_strings(source):
            for token in ("P6-M13", "20260905-230030", "p6_m13_brake"):
                assert token not in text

"""The one P0 risk run 20260827-223525 left unverified, and why.

The run's risk register named ten risks. Nine got executed coverage. ``R3`` did
not::

    ambiguous_external_effect (P0) — a readback contradicting the approved
    fingerprint is laundered into an ordinary FAILED outcome, or an
    UNKNOWN_OUTCOME is silently resolved, weakening the M3 effect boundary M7
    must not touch.

The run reported that honestly and blocked on it, which is the correct behaviour
and not the defect. The defect is upstream, and it is two facts that compound:

**One — a candidate for exactly this risk was proposed, and refused for a
reason that had nothing to do with the risk.** Wave 1 proposed ``p6m7-w1-05``,
*"Readback-vs-approved is a Conflict, not laundered, and M3 EF-4c is untouched"*.
It was refused, with three of its eleven neighbours, for a single reason::

    requirement_reference 'M7' does not name the active unit, one of its
    acceptance criteria, or an AC-<AREA>-<nnn> id

The rule is right — a scenario may not invent a requirement — but the brief was
silent about what it would accept. The unit recorded no acceptance criteria, so
the brief simply omitted the section, and the generator cited the milestone the
task names in its own title. ``GenerationBrief`` now says so out loud when there
are none, which is the whole of the fix: nothing in validation is relaxed.

**Two — the run never got a second chance at it.** Coverage-gap closure is
gated on ``_coverage_gap_only``, which requires that nothing failed. One
generated scenario did fail — ``p6m7-w1-11``, whose oracle no command it ran
could emit — so closure never ran, and the one risk a closure wave exists to
answer stayed unanswered. The two defects were one causal chain: refusing that
scenario before execution, which
``tests/test_cross_case_oracle_attribution.py`` pins, is what puts the closure
wave back.

**And the vocabulary was never the problem.** This file proves it by running it:
the approved command set already contains a deterministic probe case that
establishes the risk, against the real M3/M7 seam, with no addition to Neyma and
no new Product Driver oracle.

Nothing here consumes Claude usage.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
from neyma_product_driver.scenario_generator import GenerationBasis, GenerationBrief
from neyma_product_driver.scenario_plan import GeneratedScenario, compile_to_scenario
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    ApprovedInvocationProbe,
    ValidationContext,
    established_observations_from,
    grounding_tokens_from,
    validate_scenario,
)
from neyma_product_driver.scenarios import ScenarioExecutor, load_scenario

from scenario_fixtures import FakeUnit

DRIVER_ROOT = Path(__file__).resolve().parents[1]
M7_PATH = DRIVER_ROOT / "scenarios" / "p6_m7_conflict.yaml"
NEYMA = DRIVER_ROOT.parent / "freight-logistics-operational-teammate"
PROBE = ".venv/bin/python scripts/probe_phase6_conflict.py"

needs_neyma = pytest.mark.skipif(
    not (NEYMA / "scripts" / "probe_phase6_conflict.py").exists(),
    reason="the Neyma checkout is not present",
)


# ==========================================================================
# 1 — the brief no longer leaves the grounding rule unsaid
# ==========================================================================


class TestTheBriefSaysWhatGroundingItAccepts:
    def _brief(self, criteria: list[str]) -> str:
        return GenerationBrief(
            basis=GenerationBasis(
                task="Build P6 / M7 — Conflict. Only that.",
                active_unit_id="P6",
                acceptance_criteria=criteria,
            ),
            stage="initial",
            wave=1,
            max_scenarios=12,
            available_commands=[PROBE],
            available_services=[],
            app_url="",
            browser_enabled=False,
        ).render()

    def test_with_no_criteria_it_says_there_are_none(self) -> None:
        text = self._brief([])
        assert "ACCEPTANCE CRITERIA: none are recorded for this unit." in text

    def test_it_names_the_two_things_that_will_be_accepted(self) -> None:
        text = self._brief([])
        assert "ACTIVE READY UNIT id" in text
        assert "AC-<AREA>-<nnn>" in text

    def test_it_names_the_mistake_the_run_actually_made(self) -> None:
        """A milestone is not a requirement, and the brief now says so before the refusal."""
        text = self._brief([])
        assert "milestone" in text
        assert "sub-unit name" in text

    def test_with_criteria_the_brief_is_unchanged(self) -> None:
        text = self._brief(["AC-EVT-008: a rebuild reconstructs the party set"])
        assert "ACCEPTANCE CRITERIA (the only requirements you may cite):" in text
        assert "none are recorded" not in text


# ==========================================================================
# 2 — the approved vocabulary can establish the risk, and here it does
# ==========================================================================


READBACK_COMMAND = (
    f"{PROBE} --case readback-vs-approved-is-not-an-ordinary-failure "
    "--inject readback-vs-approved"
)
READBACK_LITERAL = (
    "A READBACK CONTRADICTING THE APPROVED FACTS IS A CONFLICT, NOT AN ORDINARY FAILURE"
)


def _m3_seam_check() -> Any:
    """The M3/M7 seam oracle the permanent M7 scenario already declares.

    Taken from the scenario file at runtime rather than restated here, so this
    test cannot drift from the reviewed oracle it is claiming to reuse — and so
    no product string is copied into a second place to make the point.
    """
    m7 = load_scenario(M7_PATH)
    for check in m7.expect_state:
        if "external_effect.py" in check.command and "EF-4c" in check.command:
            return check
    raise AssertionError("the permanent M7 scenario no longer declares the M3 seam oracle")


def readback_payload() -> dict[str, Any]:
    """A scenario for `ambiguous_external_effect`, in approved vocabulary only.

    Everything it runs is already approved, and everything it asserts is either
    printed by the invocation it runs or already bound by a human to the exact
    invocation it runs. It is not added to any permanent file: it exists to
    answer one question — *could* the generator have covered this risk — and the
    answer is executed, not argued.
    """
    seam = _m3_seam_check()
    return {
        "id": "ambiguous-external-effect-probe",
        "title": "A readback contradicting the approved facts is a Conflict, not an ordinary FAILED",
        "purpose": (
            "Drive the readback-vs-approved disagreement and confirm it surfaces as a "
            "Conflict additively, while M3's landed EF-4c seam and its UNKNOWN_OUTCOME "
            "semantics are byte-unchanged and M7 mints no effect authority."
        ),
        "risk_category": "ambiguous_external_effect",
        "priority": "P0",
        "rationale": (
            "M7-AQ-2 and the EF-4c seam: a disagreement that is laundered into FAILED, or "
            "an UNKNOWN_OUTCOME that is quietly resolved, weakens the boundary M7 must not "
            "touch."
        ),
        "requirement_reference": "P6",
        "product_principle_reference": "effect-truth",
        "mode": "backend",
        "setup": [],
        "service_refs": [],
        "actions": [
            {
                "kind": "command",
                "name": "a readback contradicting the approved facts",
                "command": READBACK_COMMAND,
                "expect_exit_code": 0,
                "expect_contains": [READBACK_LITERAL],
            }
        ],
        "persisted_state_checks": [
            {
                "name": seam.name,
                "command": seam.command,
                "contains": list(seam.contains),
                "not_contains": [],
            }
        ],
        "expected_observations": [READBACK_LITERAL, *seam.contains],
        "forbidden_observations": ["### MISS ###"],
        "cleanup": [],
        "isolation_note": "the probe builds its own ephemeral world; the seam oracle only reads source",
        "isolation_key": "phase6_conflict_probe",
        "confidence": 0.8,
        "generated_from": ["R3"],
        "provenance": {
            "generating_risk": (
                "A readback contradicting the approved fingerprint is laundered into an "
                "ordinary FAILED outcome, or an UNKNOWN_OUTCOME is silently resolved."
            ),
            "task_hash": "p6-m7",
            "active_unit_id": "P6",
            "stage": "coverage_gap",
            "source_risks": ["R3"],
            "wave": 2,
            "model": "opus",
            "session_id": "test",
        },
    }


def context(**overrides: Any) -> ValidationContext:
    config = yaml.safe_load((DRIVER_ROOT / "driver.config.yaml").read_text(encoding="utf-8"))
    m7 = load_scenario(M7_PATH)
    defaults: dict[str, Any] = {
        "approved_commands": ApprovedCommands.from_sources(
            scenarios=[m7], configured=config["scenario_generation"]["approved_commands"]
        ),
        "established_observations": established_observations_from([m7]),
        "grounding_tokens": grounding_tokens_from(FakeUnit("P6")),
        "principle_tokens": {"effect-truth"},
        "known_risk_ids": {"R3"},
    }
    defaults.update(overrides)
    return ValidationContext(**defaults)


class TestTheVocabularyCanProveIt:
    def test_the_probe_case_is_in_the_approved_set(self) -> None:
        ok, why = context().approved_commands.approves(READBACK_COMMAND)
        assert ok, why

    @needs_neyma
    def test_it_validates_with_the_live_contract_probe(self) -> None:
        live = ApprovedInvocationProbe(
            NEYMA, approved=context().approved_commands, timeout_s=300
        )
        scenario = GeneratedScenario.model_validate(readback_payload())
        assert validate_scenario(scenario, context(contract_probe=live)) == []

    @needs_neyma
    def test_it_compiles_and_the_real_seam_answers(self, tmp_path) -> None:
        """The proof, executed: the M3/M7 seam, not a source scan of prose."""
        ctx = context(
            contract_probe=ApprovedInvocationProbe(
                NEYMA, approved=context().approved_commands, timeout_s=300
            )
        )
        scenario = GeneratedScenario.model_validate(readback_payload())
        assert validate_scenario(scenario, ctx) == []

        approved, _refused = ctx.approved_commands.resolve(scenario.command_strings())
        compiled = compile_to_scenario(
            scenario, base=load_scenario(M7_PATH), approved_commands=set(approved)
        )
        executor = ScenarioExecutor(
            repo=NEYMA,
            run_config=ScenarioRunConfig(command_timeout_s=300),
            artifact_dir=tmp_path,
            approved_commands=ctx.approved_commands,
        )
        result = asyncio.run(executor.execute(compiled))
        failed = [a for a in result.assertions if not a.passed]
        assert failed == [], [f"{a.target}: {a.detail}" for a in failed]

        printed = "\n".join(f"{c.stdout}\n{c.stderr}" for c in result.commands)
        # the disagreement is a Conflict, additively...
        assert READBACK_LITERAL in printed
        # ...and M3's landed boundary is exactly as it was.
        assert "M3 EF-4c to_state UNKNOWN_OUTCOME: True" in printed
        assert "M7 did not add a ConflictRaised emission to M3: True" in printed
        assert "M7 writes no effect_grants or identity_binding_claims row: True" in printed


# ==========================================================================
# 3 — an oracle refusal must not suppress the closure wave
# ==========================================================================


class TestAnOracleRefusalDoesNotSuppressClosure:
    def _verdict(self, **kwargs: Any) -> Any:
        class Verdict:
            uncovered_risks = kwargs.get("uncovered", ["R3"])
            unverified = kwargs.get("unverified", [])
            generation_problems = kwargs.get("problems", [])

        return Verdict()

    def test_a_failed_scenario_closes_the_door(self) -> None:
        """The run's actual state: one failure, so no closure wave, so R3 stays open."""
        from neyma_product_driver.cli import _coverage_gap_only

        class Suite:
            def blocking_failures(self):
                return ["p6m7-w1-11"]

        assert _coverage_gap_only(self._verdict(), Suite()) is False

    def test_with_that_scenario_refused_instead_the_door_is_open(self) -> None:
        """Refused before execution, it never becomes a failure, and closure runs.

        This is the whole reason the two defects in run 20260827-223525 are one
        defect: the impossible oracle did not merely produce a false product
        signal, it spent the run's last chance at its only uncovered P0 risk.
        """
        from neyma_product_driver.cli import _coverage_gap_only

        class Suite:
            def blocking_failures(self):
                return []

        assert _coverage_gap_only(self._verdict(), Suite()) is True

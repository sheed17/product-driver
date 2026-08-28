"""An oracle must be producible by the invocation it is asked of.

Run ``20260827-223525`` verified P6/M7 on every axis that measures the product —
the permanent scenario passed, the probe reported ``behaviours as specified, 0
wrong``, 16/16 mutants were caught, schema, ship-dark and regression evidence
were green — and did not reach VERIFIED, on one generated scenario that could
not pass against a correct product.

``p6m7-w1-11`` ran::

    probe_phase6_conflict.py --case replay-rebuilds-the-complete-party-set \\
        --inject replay --parties 5 --repeat 2 --seed 29

which prints ``A REBUILD RECONSTRUCTS THE COMPLETE PARTY SET`` and exits 0. It
asserted that — and, from the same command, ``replay: 0 resolutions, 0 duplicate
conflicts, 0 lost parties, 0 new authority, 0 external effects``, the sentence
two *other* ``--case`` values of the same program print and this one does not.

This is the S3 shape from run ``20260827-063257`` with one difference, and the
difference is the defect. S3 asserted the foreign sentence only in
``expected_observations``, the oracle that names no command, and ``7133b729``
closed that by requiring a basis. ``p6m7-w1-11`` wrote the same sentence into the
command's own ``expect_contains`` first — and *that was accepted as the basis*,
because self-attribution was unconditional. Naming the operation that prints a
sentence is free: a model writes the command and the expectation in one breath,
so ``expect_contains`` proves only that it believed them related. The rule asked
a model to certify itself.

So the basis is no longer free where the repository contests it. When a literal
is bound — in the human-authored scenario files — to a *different* invocation of
the same program, on the other side of a selector, the invocation is asked what
it prints. Nothing static can answer: no file in either repository maps a
``--case`` value to that selection's output, and deriving one from the selector's
spelling would be the prose matching this rule exists to refuse.

Nothing here consumes Claude usage. Every recorded output in this file was
recorded from the real probe, and
:meth:`TestTheRecordingIsReal.test_the_recording_still_matches_the_live_probe`
re-runs each one against the Neyma checkout so it cannot quietly become fiction.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from neyma_product_driver.config import ScenarioRunConfig
from neyma_product_driver.scenario_plan import GeneratedScenario, compile_to_scenario
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    ApprovedInvocationProbe,
    ContractProbeResult,
    ValidationContext,
    contested_producers,
    cross_contract_observations,
    established_observations_from,
    unwrap_literal,
    validate_scenario,
)
from neyma_product_driver.scenarios import ScenarioExecutor, load_scenario

import yaml

DRIVER_ROOT = Path(__file__).resolve().parents[1]
M7_PATH = DRIVER_ROOT / "scenarios" / "p6_m7_conflict.yaml"
RUN = DRIVER_ROOT / "runs" / "20260827-223525"
PLAN = RUN / "scenario-plan.json"

PROBE = ".venv/bin/python scripts/probe_phase6_conflict.py"

#: The two sentences and the three cases they belong to. Read here only so this
#: file can *say* what run 20260827-223525 did; the rule under test derives every
#: one of them from `scenarios/p6_m7_conflict.yaml` at runtime, and this module
#: is the only place in the repository that names them.
REBUILD = "A REBUILD RECONSTRUCTS THE COMPLETE PARTY SET"
REPLAY_SUMMARY = (
    "replay: 0 resolutions, 0 duplicate conflicts, 0 lost parties, "
    "0 new authority, 0 external effects"
)
SUMMARY_LINE = "behaviours as specified, 0 wrong"

W11_COMMAND = (
    f"{PROBE} --case replay-rebuilds-the-complete-party-set "
    "--inject replay --parties 5 --repeat 2 --seed 29"
)
SIBLING_COMMAND = (
    f"{PROBE} --case replay-cannot-resolve-or-duplicate-a-conflict "
    "--inject replay --parties 5 --repeat 2 --seed 29"
)

#: invocation -> what it actually printed. Recorded, never invented.
SECOND_DETECTION = "A SECOND DETECTION ATTACHES A PARTY, NEVER A SECOND CONFLICT"
W02_COMMAND = (
    f"{PROBE} --case second-detection-attaches-a-party-not-a-new-conflict "
    "--inject concurrent-detection --concurrency 6 --delay-ms 40 --parties 4 --seed 17"
)

RECORDING: dict[str, str] = {
    W11_COMMAND: f"{REBUILD}\n{SUMMARY_LINE}\n",
    SIBLING_COMMAND: f"{REPLAY_SUMMARY}\n{SUMMARY_LINE}\n",
    W02_COMMAND: f"{SECOND_DETECTION}\n{SUMMARY_LINE}\n",
}


def recorded_probe(recording: dict[str, str] | None = None) -> Any:
    """A contract probe answering only from recorded output.

    An unrecorded invocation is UNDETERMINED, never empty: "I did not ask" and
    "it printed nothing" are different answers, and only one of them is a reason
    to refuse an oracle on the product's behalf.
    """
    table = dict(RECORDING if recording is None else recording)
    asked: list[str] = []

    def probe(command: str) -> ContractProbeResult:
        asked.append(command)
        if command not in table:
            return ContractProbeResult(False, detail="no recording for this invocation")
        return ContractProbeResult(True, output=table[command])

    probe.asked = asked  # type: ignore[attr-defined]
    return probe


def configured_commands() -> list[str]:
    """The per-`--case` entries `driver.config.yaml` approves, as the run had them."""
    config = yaml.safe_load((DRIVER_ROOT / "driver.config.yaml").read_text(encoding="utf-8"))
    return list(config["scenario_generation"]["approved_commands"])


def m7_context(**overrides: Any) -> ValidationContext:
    """Validation as the run had it, plus a probe that can answer."""
    m7 = load_scenario(M7_PATH)
    defaults: dict[str, Any] = {
        "approved_commands": ApprovedCommands.from_sources(
            scenarios=[m7], configured=configured_commands()
        ),
        "established_observations": established_observations_from([m7]),
        "contract_probe": recorded_probe(),
        "grounding_tokens": {"p6/m7", "p6", "m7", "ac-evt-008"},
        "principle_tokens": {"failure_recovery"},
    }
    defaults.update(overrides)
    return ValidationContext(**defaults)


def w11_payload(**overrides: Any) -> dict[str, Any]:
    """``p6m7-w1-11`` exactly as run 20260827-223525 recorded it, before execution."""
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    raw = next(s for s in plan["scenarios"] if s["id"] == "p6m7-w1-11")
    payload = copy.deepcopy(raw)
    payload.update(overrides)
    return payload


def w11(**overrides: Any) -> GeneratedScenario:
    return GeneratedScenario.model_validate(w11_payload(**overrides))


# ==========================================================================
# 1 — the exact artifact, refused before anything runs
# ==========================================================================


class TestTheExactArtifact:
    def test_the_run_really_did_record_this_scenario(self) -> None:
        """The fixture is the artifact, not a reconstruction of it."""
        assert PLAN.exists(), "the run this defect came from is preserved"
        scenario = w11()
        assert scenario.actions[0].command == W11_COMMAND
        assert REPLAY_SUMMARY in scenario.actions[0].expect_contains
        assert REPLAY_SUMMARY in scenario.expected_observations

    def test_it_is_refused_before_execution(self) -> None:
        reasons = validate_scenario(w11(), m7_context())
        assert reasons, "the artifact that failed the run is accepted again"
        assert any(REPLAY_SUMMARY in reason for reason in reasons)

    def test_the_refusal_names_the_invocation_and_where_the_literal_belongs(self) -> None:
        reasons = " ".join(validate_scenario(w11(), m7_context()))
        assert W11_COMMAND in reasons, "the refusal does not say which invocation"
        assert PROBE in reasons, "the refusal does not say where the literal comes from"

    def test_the_case_it_selected_is_untouched(self) -> None:
        """Only the foreign sentence is refused. The scenario's own oracle is fine."""
        reasons = validate_scenario(w11(), m7_context())
        assert not any(f"requires {REBUILD!r}" in reason for reason in reasons)

    def test_the_old_rule_alone_accepted_it(self) -> None:
        """The escape path, pinned: self-attribution was the basis, and it was free.

        With no probe *and* no established map there is nothing to contest the
        attribution with, which is exactly the state `7133b729` left — and the
        artifact sails through. This is the false negative, reproduced.
        """
        blind = m7_context(established_observations={}, contract_probe=None)
        assert validate_scenario(w11(), blind) == []


# ==========================================================================
# 2 — a selector makes a different observable contract
# ==========================================================================


class TestSelectorsPartitionTheContract:
    def test_a_sibling_case_cannot_lend_its_sentence(self) -> None:
        """Same script, same tail, different `--case`: still not the same oracle."""
        producers = contested_producers(
            W11_COMMAND, REPLAY_SUMMARY, established_observations_from([load_scenario(M7_PATH)])
        )
        assert producers, "the repository binds this sentence, and it is not bound here"

    def test_the_same_executable_with_different_selectors_is_two_contracts(self) -> None:
        established = established_observations_from([load_scenario(M7_PATH)])
        # The bare battery is bound. Neither narrowing of it is.
        assert PROBE in established
        assert W11_COMMAND not in established
        assert SIBLING_COMMAND not in established

    def test_an_uncontested_literal_keeps_the_cheap_path(self) -> None:
        """Silence still beats guessing, and it still costs nothing to run.

        A sentence no file binds to anything is not contested, so the invocation
        is never asked about it — which is what leaves a generated scenario free
        to name output no human has written down.
        """
        probe = recorded_probe()
        scenario = w11(
            actions=[
                {**w11_payload()["actions"][0], "expect_contains": ["A SENTENCE NOBODY WROTE DOWN"]}
            ],
            expected_observations=["A SENTENCE NOBODY WROTE DOWN"],
        )
        assert cross_contract_observations(scenario, m7_context(contract_probe=probe)) == []
        assert probe.asked == [], "an uncontested literal cost an execution"

    def test_the_probe_is_asked_once_per_invocation(self) -> None:
        probe = ApprovedInvocationProbe(DRIVER_ROOT, approved=None, timeout_s=5)
        first = probe("true")
        second = probe("true")
        assert first is second, "the same invocation was run twice in one wave"


# ==========================================================================
# 3 — a prose wrapper is not a way in
# ==========================================================================


class TestWrappedLiterals:
    def test_the_executor_wrapper_is_unwrapped_exactly(self) -> None:
        """The one shape `ScenarioExecutor._do_command` writes, decoded not stripped."""
        wrapped = f"replay a five-party conflict twice: contains {REPLAY_SUMMARY!r}"
        assert unwrap_literal(wrapped) == REPLAY_SUMMARY

    def test_a_literal_containing_quotes_survives_the_round_trip(self) -> None:
        literal = "it said \"no\" and 'no' again"
        assert unwrap_literal(f"a check: contains {literal!r}") == literal

    def test_prose_that_is_not_the_wrapper_is_left_alone(self) -> None:
        """This normalizes one exact shape. It never guesses, and it never matches prose."""
        for text in (REPLAY_SUMMARY, "roughly contains something", "contains: a thing"):
            assert unwrap_literal(text) == text

    def test_wrapping_the_foreign_literal_does_not_bypass_attribution(self) -> None:
        wrapped = f"replay a five-party conflict twice: contains {REPLAY_SUMMARY!r}"
        scenario = w11(
            actions=[{**w11_payload()["actions"][0], "expect_contains": [REBUILD, wrapped]}],
            expected_observations=[REBUILD, wrapped],
        )
        reasons = validate_scenario(scenario, m7_context())
        assert any(REPLAY_SUMMARY in reason for reason in reasons), (
            "prose around the literal walked it past the rule"
        )


# ==========================================================================
# 4 — a correct oracle still compiles and still runs
# ==========================================================================


def _compiles(scenario: GeneratedScenario) -> None:
    """Validated, then compiled through the same approval the planner applies."""
    context = m7_context()
    assert validate_scenario(scenario, context) == []
    approved, refused = context.approved_commands.resolve(scenario.command_strings())
    assert refused == [] or not refused, refused
    compiled = compile_to_scenario(
        scenario, base=load_scenario(M7_PATH), approved_commands=set(approved)
    )
    assert compiled.commands or compiled.steps


class TestACorrectOracleIsUnaffected:
    def test_the_rebuild_case_asserting_its_own_sentence(self) -> None:
        _compiles(
            w11(
                actions=[{**w11_payload()["actions"][0], "expect_contains": [REBUILD]}],
                expected_observations=[REBUILD, "ConflictPartyAttached"],
            )
        )

    def test_the_replay_summary_case_asserting_its_own_sentence(self) -> None:
        action = {**w11_payload()["actions"][0]}
        action["command"] = SIBLING_COMMAND
        action["expect_contains"] = [REPLAY_SUMMARY]
        _compiles(
            w11(
                actions=[action],
                expected_observations=[REPLAY_SUMMARY, "ConflictPartyAttached"],
            )
        )

    def test_the_eight_scenarios_the_run_accepted_are_still_accepted(self) -> None:
        """The fix refuses one of the nine, and it is the one that failed."""
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        context = m7_context(contract_probe=recorded_probe())
        refused = {
            s["id"]
            for s in plan["scenarios"]
            if cross_contract_observations(GeneratedScenario.model_validate(s), context)
        }
        assert refused == {"p6m7-w1-11"}


# ==========================================================================
# 5 — a real product failure is still a real product failure
# ==========================================================================


class TestGenuineFailuresStillReachExecution:
    def test_an_unbound_wrong_expectation_executes_and_fails_as_product_evidence(
        self, tmp_path
    ) -> None:
        """The rule must not become a way to never fail.

        A sentence no human bound to anything is uncontested, so it is not
        refused — it compiles, it runs, and when the product does not print it
        the scenario FAILS. That is the outcome this whole file exists to keep
        available: a failure that means something about the product.
        """
        scenario = w11(
            actions=[
                {
                    **w11_payload()["actions"][0],
                    "expect_contains": ["A SENTENCE THE PRODUCT NEVER PRINTS"],
                }
            ],
            expected_observations=["A SENTENCE THE PRODUCT NEVER PRINTS"],
        )
        context = m7_context()
        assert validate_scenario(scenario, context) == [], "a genuine oracle was refused"

        approved, _refused = context.approved_commands.resolve(scenario.command_strings())
        compiled = compile_to_scenario(
            scenario, base=load_scenario(M7_PATH), approved_commands=set(approved)
        )
        neyma = DRIVER_ROOT.parent / "freight-logistics-operational-teammate"
        if not (neyma / "scripts" / "probe_phase6_conflict.py").exists():
            pytest.skip("the Neyma checkout is not present")
        executor = ScenarioExecutor(
            repo=neyma,
            run_config=ScenarioRunConfig(command_timeout_s=180),
            artifact_dir=tmp_path,
            approved_commands=context.approved_commands,
        )
        result = asyncio.run(executor.execute(compiled))
        failed = [a for a in result.assertions if not a.passed]
        assert failed, "a wrong expectation stopped being a failure"
        assert any("A SENTENCE THE PRODUCT NEVER PRINTS" in a.target for a in failed)


# ==========================================================================
# 6 — mutations that restore the escape
# ==========================================================================


class TestMutationsRestoringTheEscape:
    def test_mutation_1_self_attribution_unconditional_again(self) -> None:
        """Drop the contest and `p6m7-w1-11` is accepted again — the defect exactly."""
        assert validate_scenario(w11(), m7_context(established_observations={})) == []

    def test_mutation_2_an_undetermined_contract_treated_as_satisfied(self) -> None:
        """A probe that cannot answer is not a licence.

        Timeouts, refusals and a missing checkout all land here. Reading any of
        them as "it printed nothing to contradict us" is the same false green in
        a quieter costume.
        """
        blind = recorded_probe({})
        reasons = validate_scenario(w11(), m7_context(contract_probe=blind))
        assert any(REPLAY_SUMMARY in reason for reason in reasons)

    def test_mutation_3_no_probe_at_all_is_not_a_licence(self) -> None:
        reasons = validate_scenario(w11(), m7_context(contract_probe=None))
        assert any(REPLAY_SUMMARY in reason for reason in reasons)

    def test_mutation_4_attribution_keyed_on_the_program_not_the_invocation(self) -> None:
        """Key on the script and every `--case` inherits the whole battery again."""
        established = dict(established_observations_from([load_scenario(M7_PATH)]))
        established[W11_COMMAND] = established[PROBE]  # the mutation, applied by hand
        assert contested_producers(W11_COMMAND, REPLAY_SUMMARY, established) == ()

    def test_mutation_5_containment_in_the_wrong_direction(self) -> None:
        """`L in B` means B appearing implies L. The reverse is not true and is not accepted."""
        established = {PROBE: frozenset({REPLAY_SUMMARY[:20]})}
        assert contested_producers(W11_COMMAND, REPLAY_SUMMARY, established) == ()


# ==========================================================================
# 7 — the recording is not fiction
# ==========================================================================


class TestTheRecordingIsReal:
    NEYMA = DRIVER_ROOT.parent / "freight-logistics-operational-teammate"

    @pytest.mark.skipif(
        not (
            DRIVER_ROOT.parent
            / "freight-logistics-operational-teammate"
            / "scripts"
            / "probe_phase6_conflict.py"
        ).exists(),
        reason="the Neyma checkout is not present",
    )
    def test_the_recording_still_matches_the_live_probe(self) -> None:
        probe = ApprovedInvocationProbe(self.NEYMA, approved=None, timeout_s=300)
        for command, recorded in RECORDING.items():
            answer = probe(command)
            assert answer.determined, f"{command}: {answer.detail}"
            assert answer.output.strip() == recorded.strip(), (
                f"{command}: the recording has drifted from what the probe prints"
            )

    @pytest.mark.skipif(
        not (
            DRIVER_ROOT.parent
            / "freight-logistics-operational-teammate"
            / "scripts"
            / "probe_phase6_conflict.py"
        ).exists(),
        reason="the Neyma checkout is not present",
    )
    def test_the_artifact_is_refused_against_the_live_probe(self) -> None:
        """The end-to-end pin: the real file, the real program, no recording at all."""
        live = ApprovedInvocationProbe(
            self.NEYMA,
            approved=ApprovedCommands.from_sources(scenarios=[load_scenario(M7_PATH)]),
            timeout_s=300,
        )
        reasons = validate_scenario(w11(), m7_context(contract_probe=live))
        assert any(REPLAY_SUMMARY in reason for reason in reasons)
        # and the correct oracle for the same case is accepted by the same probe
        correct = w11(
            actions=[{**w11_payload()["actions"][0], "expect_contains": [REBUILD]}],
            expected_observations=[REBUILD, "ConflictPartyAttached"],
        )
        assert validate_scenario(correct, m7_context(contract_probe=live)) == []


# ==========================================================================
# 8 — no product string was duplicated into the driver to make this work
# ==========================================================================


class TestNothingIsHardCoded:
    def test_the_driver_source_names_neither_sentence_nor_any_case(self) -> None:
        for path in (DRIVER_ROOT / "neyma_product_driver").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert REBUILD not in text, path
            assert REPLAY_SUMMARY not in text, path
            assert "replay-rebuilds-the-complete-party-set" not in text, path
            assert "replay-cannot-resolve-or-duplicate-a-conflict" not in text, path

    def test_no_case_name_appears_in_the_driver_source(self) -> None:
        """The rule derives the vocabulary at runtime; it does not carry a copy."""
        established = established_observations_from([load_scenario(M7_PATH)])
        # Multi-word names only: `tenant-isolation` is also an ordinary phrase,
        # and a coincidental match is not a copy of the vocabulary.
        cases = [
            case for case in established[f"{PROBE} --list-cases"] if case.count("-") >= 3
        ]
        for path in (DRIVER_ROOT / "neyma_product_driver").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            named = sorted(case for case in cases if case in text)
            assert named == [], f"{path} names {named}"

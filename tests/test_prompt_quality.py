"""The prompt-quality contract: what may and may not reach the builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from neyma_product_driver.context import load_founder_context
from neyma_product_driver.models import Decision, EvaluatorDecision
from neyma_product_driver.prompts import (
    evaluator_prompt,
    is_vague_correction,
    normalize_correction,
    render_correction_for_builder,
    validate_correction_quality,
)

DRIVER_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def founder():
    return load_founder_context(DRIVER_ROOT)


GOOD_CORRECTION = (
    "On the operator load list, render an owner for every open obligation. Show it "
    "as 'Owner: <full name>' directly beneath the load reference. Loads with no "
    "assignee must read 'Owner: unassigned' rather than showing an empty cell, so "
    "missing ownership is visible rather than invisible."
)


def grounded_fix(**overrides) -> EvaluatorDecision:
    base = dict(
        decision=Decision.FIX,
        summary="Open loads do not name an accountable owner.",
        observed_behavior=["Observed: LD560004 rendered with no owner."],
        problems=["No accountable owner on open obligations."],
        correction_prompt=GOOD_CORRECTION,
        evidence_paths=["iteration-01/screenshots/01-initial.png"],
        confidence=0.85,
        requirement_reference="P3 acceptance criterion: observability_and_operational_behavior",
        product_principle_reference="accountable_owner",
        scenario="browser_generic against http://127.0.0.1:8000/operator/",
        observed_result="The load list shows LD560003 and LD560004 with no owner field.",
        expected_result="Each open obligation names exactly one accountable owner.",
        preserve="Existing load ordering and the delivered/undelivered split.",
        retest="Re-run browser_generic and confirm 'Owner:' appears for every open load.",
    )
    base.update(overrides)
    return EvaluatorDecision(**base)


# -- the happy path --------------------------------------------------------


def test_a_valid_evidence_grounded_fix_passes(founder) -> None:
    assert validate_correction_quality(grounded_fix(), founder=founder) == []


def test_a_valid_fix_renders_every_contract_field_for_the_builder() -> None:
    rendered = render_correction_for_builder(grounded_fix())
    for label in (
        "REQUIREMENT:", "PRODUCT PRINCIPLE:", "SCENARIO EXECUTED:", "OBSERVED RESULT:",
        "EXPECTED RESULT:", "EVIDENCE:", "SMALLEST JUSTIFIED CORRECTION:",
        "MUST BE PRESERVED:", "RETEST:",
    ):
        assert label in rendered, label


# -- missing contract fields ----------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["requirement_reference", "scenario", "observed_result", "expected_result", "preserve", "retest"],
)
def test_a_fix_missing_a_required_field_is_rejected(founder, field: str) -> None:
    reasons = validate_correction_quality(grounded_fix(**{field: ""}), founder=founder)
    assert any(field in r for r in reasons), reasons


def test_a_fix_without_evidence_is_rejected(founder) -> None:
    reasons = validate_correction_quality(grounded_fix(evidence_paths=[]), founder=founder)
    assert any("evidence_paths" in r for r in reasons)


def test_a_fix_without_a_retest_is_rejected(founder) -> None:
    reasons = validate_correction_quality(grounded_fix(retest="   "), founder=founder)
    assert any("retest" in r for r in reasons)


# -- vague corrections -----------------------------------------------------


@pytest.mark.parametrize(
    "trash",
    [
        "keep going",
        "improve this",
        "make it better",
        "make it production-ready",
        "polish the workflow",
        "enhance robustness",
        "clean it up",
        "finish the feature",
        "add more tests",
    ],
)
def test_named_trash_prompts_are_rejected(founder, trash: str) -> None:
    reasons = validate_correction_quality(
        grounded_fix(correction_prompt=trash), founder=founder
    )
    assert reasons, f"{trash!r} was accepted"
    assert any("vague" in r or "chars" in r for r in reasons)


def test_a_short_prompt_is_rejected_even_when_specific(founder) -> None:
    reasons = validate_correction_quality(
        grounded_fix(correction_prompt="Add an owner column."), founder=founder
    )
    assert any("chars" in r for r in reasons)


def test_vague_phrasing_is_allowed_when_fully_grounded(founder) -> None:
    """The context file permits these phrases WITH evidence, expectation and retest."""
    correction = (
        "The operator load list has no owner column, so clean it up by adding an "
        "'Owner: <full name>' line beneath each load reference. Unassigned loads must "
        "read 'Owner: unassigned' so that missing ownership stays visible to the "
        "dispatcher rather than rendering as an empty cell."
    )
    assert validate_correction_quality(grounded_fix(correction_prompt=correction), founder=founder) == []


def test_an_empty_correction_is_vague() -> None:
    vague, why = is_vague_correction("", grounded_fix())
    assert vague and "empty" in why


# -- no discrepancy --------------------------------------------------------


def test_identical_observed_and_expected_cannot_become_a_fix(founder) -> None:
    """No discrepancy means no generated work."""
    same = "The load list rendered two loads with owners."
    reasons = validate_correction_quality(
        grounded_fix(observed_result=same, expected_result=same), founder=founder
    )
    assert any("identical" in r for r in reasons)


def test_whitespace_and_case_differences_do_not_manufacture_a_discrepancy(founder) -> None:
    reasons = validate_correction_quality(
        grounded_fix(
            observed_result="The Load List   rendered two loads.",
            expected_result="the load list rendered two loads.",
        ),
        founder=founder,
    )
    assert any("identical" in r for r in reasons)


# -- confidence gates ------------------------------------------------------


def test_low_confidence_cannot_produce_an_autonomous_fix(founder) -> None:
    reasons = validate_correction_quality(grounded_fix(confidence=0.3), founder=founder)
    assert any("minimum_for_fix" in r for r in reasons)
    assert any("ASK_USER" in r for r in reasons)


def test_a_customer_facing_change_needs_higher_confidence(founder) -> None:
    """Passes the general bar but not the customer-facing one."""
    between = (founder.minimum_confidence_for_fix + founder.minimum_confidence_for_customer_facing_fix) / 2
    assert validate_correction_quality(
        grounded_fix(confidence=between, customer_facing=False), founder=founder
    ) == []
    reasons = validate_correction_quality(
        grounded_fix(confidence=between, customer_facing=True), founder=founder
    )
    assert any("customer_facing" in r for r in reasons)


def test_high_confidence_customer_facing_change_is_allowed(founder) -> None:
    assert validate_correction_quality(
        grounded_fix(confidence=0.95, customer_facing=True), founder=founder
    ) == []


# -- repeated corrections --------------------------------------------------


def test_an_identical_repeated_correction_is_rejected(founder) -> None:
    reasons = validate_correction_quality(
        grounded_fix(), founder=founder, previous_corrections=[GOOD_CORRECTION]
    )
    assert any("identical to one already sent" in r for r in reasons)


def test_repeat_detection_ignores_whitespace_and_case(founder) -> None:
    reasons = validate_correction_quality(
        grounded_fix(),
        founder=founder,
        previous_corrections=["  " + GOOD_CORRECTION.upper().replace(" ", "  ") + " "],
    )
    assert any("identical to one already sent" in r for r in reasons)


def test_a_genuinely_different_correction_is_allowed(founder) -> None:
    assert validate_correction_quality(
        grounded_fix(), founder=founder, previous_corrections=["Something else entirely, at length, "
        "describing a different surface and a different expected behaviour for the operator."]
    ) == []


def test_normalize_correction() -> None:
    assert normalize_correction("  A  B\n C ") == "a b c"


# -- non-FIX decisions are not subject to the contract ---------------------


@pytest.mark.parametrize("verdict", [Decision.ACCEPT, Decision.ASK_USER, Decision.BLOCKED])
def test_non_fix_decisions_are_not_gated(founder, verdict: Decision) -> None:
    d = EvaluatorDecision(decision=verdict, summary="s", confidence=0.1)
    assert validate_correction_quality(d, founder=founder) == []


# -- prompt assembly -------------------------------------------------------


class _FakeUnit:
    unit_id = "P7"
    status = "READY"

    def criteria_labels(self):
        return ["core_implementation (weight 20): PENDING"]

    def render(self):
        return "ACTIVE READY UNIT: P7 — the seventh unit"


class _FakeRepoContext:
    head_commit = "abc1234"
    branch = "p7/branch"
    dirty_file_count = 3
    active_unit = _FakeUnit()
    files_consulted = ["/repo/CLAUDE.md"]

    def render(self):
        return "=== NEYMA REPOSITORY ===\n" + self.active_unit.render()


def test_prompt_contains_all_three_layers_in_order(founder) -> None:
    prompt = evaluator_prompt(
        task="do the thing",
        iteration=2,
        max_iterations=5,
        builder_summary="I built it.",
        git=None,
        scenario=None,
        service_logs=None,
        evidence_dir="/runs/x/iteration-02",
        founder=founder,
        repo_context=_FakeRepoContext(),
        founder_feedback="=== FOUNDER DIRECTION FOR THIS RUN ===\nNo TMS assumptions.",
    )

    a = prompt.index("LAYER A — STABLE FOUNDER PRODUCT CONTEXT")
    b = prompt.index("LAYER B — CURRENT NEYMA REPOSITORY AUTHORITY")
    c = prompt.index("LAYER C — IMMEDIATE EVIDENCE")
    assert a < b < c

    assert "ACTIVE READY UNIT: P7" in prompt
    assert "No TMS assumptions." in prompt
    assert prompt.index("No TMS assumptions.") < b  # feedback outranks, appears early


def test_prompt_states_that_the_repository_wins(founder) -> None:
    prompt = evaluator_prompt(
        task="t", iteration=1, max_iterations=1, builder_summary="",
        git=None, scenario=None, service_logs=None, evidence_dir="/x",
        founder=founder, repo_context=_FakeRepoContext(),
    )
    assert "Scope your judgement to the active READY unit" in prompt


def test_prompt_warns_against_repeating_corrections(founder) -> None:
    prompt = evaluator_prompt(
        task="t", iteration=2, max_iterations=5, builder_summary="",
        git=None, scenario=None, service_logs=None, evidence_dir="/x",
        founder=founder, repo_context=_FakeRepoContext(),
        previous_corrections=["an earlier correction"],
    )
    assert "CORRECTIONS ALREADY SENT IN THIS RUN" in prompt
    assert "an earlier correction" in prompt


def test_prompt_works_without_any_context_layers() -> None:
    """The loop must still function if context is unavailable (evaluate-only paths)."""
    prompt = evaluator_prompt(
        task="t", iteration=1, max_iterations=1, builder_summary="x",
        git=None, scenario=None, service_logs=None, evidence_dir="/x",
    )
    assert "LAYER C — IMMEDIATE EVIDENCE" in prompt
    assert "LAYER A" not in prompt

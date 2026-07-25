"""The control loop wired to the context layer.

Covers the routing consequences of context: fail-closed authority, rejection of
ungrounded FIX, repeat detection, provenance recording, and the precedence rules
between repository authority, founder feedback and evaluator taste.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.cli import run_control_loop
from neyma_product_driver.config import DriverConfig
from neyma_product_driver.context import (
    FounderFeedbackStore,
    RepositoryContextLoader,
    load_founder_context,
)
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import (
    AssertionResult,
    Decision,
    EvaluatorDecision,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.scenarios import Scenario

DRIVER_ROOT = Path(__file__).resolve().parent.parent


# -- fixtures --------------------------------------------------------------


def _unit(uid: str, status: str) -> dict:
    return {
        "unit_id": uid,
        "name": f"{uid} name",
        "status": status,
        "objective": f"objective of {uid}",
        "acceptance_contract": "acceptance.md",
        "acceptance_criteria": [{"criterion": "core_implementation", "weight": 20, "result": "PENDING"}],
    }


@pytest.fixture
def neyma(tmp_path: Path) -> Path:
    repo = tmp_path / "neyma"
    (repo / "docs" / "implementation").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "CLAUDE.md").write_text("# CLAUDE.md\n## Authority\nThis file outranks all others.\n")
    (repo / "docs" / "implementation" / "CURRENT.md").write_text("# CURRENT\n## Status\nP3 in progress.\n")
    (repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").write_text(
        yaml.safe_dump({"meta": {}, "units": [_unit("P3", "READY"), _unit("P4", "BLOCKED")]})
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def driver_root(tmp_path: Path) -> Path:
    root = tmp_path / "driver"
    root.mkdir()
    shutil.copytree(DRIVER_ROOT / "founder_context", root / "founder_context")
    return root


@pytest.fixture
def bits(neyma: Path, driver_root: Path):
    config = DriverConfig(
        neyma_repo=neyma, driver_root=driver_root, task="build it", max_iterations=3
    )
    assert config.runs_dir is not None
    store = EvidenceStore(config.runs_dir, "ctx-run")
    state = RunState(run_id=store.run_id, task="build it", max_iterations=3)
    scenario = Scenario(name="ctx-scenario")
    founder = load_founder_context(driver_root)
    loader = RepositoryContextLoader(neyma)

    def make_executor(artifact_dir: Path):
        class Ex:
            service_logs: dict[str, str] = {}

            async def execute(self, sc):
                return ScenarioResult(
                    scenario_name=sc.name,
                    assertions=[AssertionResult(kind="expect_visible", target="x", passed=True)],
                )

        return Ex()

    return config, store, state, scenario, founder, loader, make_executor


class FakeBuilder:
    def __init__(self) -> None:
        self.session_id = "b1"
        self.prompts: list[str] = []

    async def send(self, prompt: str, timeout_s: int | None = None):
        self.prompts.append(prompt)

        class T:
            text = "done. RUNNABLE CHECKPOINT: run it."
            session_id = "b1"
            tool_uses: list[str] = []
            denied_requests: list[str] = []
            is_error = False
            error_detail = ""

        return T()


class ScriptedEvaluator:
    def __init__(self, decisions: list[EvaluatorDecision]) -> None:
        self.session_id = "e1"
        self.decisions = list(decisions)
        self.prompts: list[str] = []

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        self.prompts.append(prompt)
        return self.decisions.pop(0) if self.decisions else EvaluatorDecision(
            decision=Decision.BLOCKED, summary="out of scripted decisions"
        )


def grounded_fix(correction: str, **kw) -> EvaluatorDecision:
    base = dict(
        decision=Decision.FIX,
        summary="discrepancy",
        problems=["no owner shown"],
        correction_prompt=correction,
        evidence_paths=["iteration-01/scenario.json"],
        confidence=0.85,
        requirement_reference="P3 acceptance criterion: core_implementation",
        product_principle_reference="accountable_owner",
        scenario="ctx-scenario",
        observed_result="The list showed no owner for open loads.",
        expected_result="Each open load names one accountable owner.",
        preserve="Existing ordering.",
        retest="Re-run ctx-scenario and confirm an owner appears.",
    )
    base.update(kw)
    return EvaluatorDecision(**base)


LONG_A = (
    "Add an 'Owner: <full name>' line beneath each open load on the operator list so a "
    "dispatcher can see accountability without opening the load. Unassigned loads must "
    "read 'Owner: unassigned' rather than rendering an empty cell."
)
LONG_B = (
    "Separate inferred delivery times from confirmed ones on the load list. Mark inferred "
    "values with the word 'Inferred' and name the basis of the inference, so an operator "
    "never mistakes a model estimate for a scanned proof of delivery."
)


async def _run(bits, evaluator, builder=None, **kw):
    config, store, state, scenario, founder, loader, make_executor = bits
    return await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder or FakeBuilder(), evaluator=evaluator,
        make_executor=make_executor, emit=lambda _m: None,
        founder=founder, repo_loader=loader, **kw,
    )


# -- authority resolution --------------------------------------------------


async def test_active_unit_reaches_both_prompts(bits) -> None:
    builder = FakeBuilder()
    evaluator = ScriptedEvaluator([EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)])
    result = await _run(bits, evaluator, builder)

    assert result.status is RunStatus.ACCEPTED
    assert "P3" in builder.prompts[0]
    assert "ACTIVE READY UNIT: P3" in evaluator.prompts[0]


async def test_two_ready_units_block_the_run_before_the_builder_works(bits, neyma: Path) -> None:
    (neyma / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").write_text(
        yaml.safe_dump({"meta": {}, "units": [_unit("P3", "READY"), _unit("P4", "READY")]})
    )
    builder = FakeBuilder()
    result = await _run(bits, ScriptedEvaluator([]), builder)

    assert result.status is RunStatus.BLOCKED
    assert "more than one READY unit" in result.final_decision.summary
    assert builder.prompts == [], "the builder was asked to work under unresolved authority"


async def test_no_ready_unit_blocks_the_run(bits, neyma: Path) -> None:
    (neyma / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").write_text(
        yaml.safe_dump({"meta": {}, "units": [_unit("P3", "COMPLETE")]})
    )
    result = await _run(bits, ScriptedEvaluator([]), FakeBuilder())
    assert result.status is RunStatus.BLOCKED
    assert "no READY unit" in result.final_decision.summary


async def test_authority_becoming_contradictory_mid_run_blocks(bits, neyma: Path) -> None:
    """Authority is re-read every iteration, so a mid-run contradiction stops it."""
    config, store, state, scenario, founder, loader, make_executor = bits
    registry = neyma / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml"

    class BreakingEvaluator(ScriptedEvaluator):
        async def evaluate(self, prompt, timeout_s=None):
            registry.write_text(
                yaml.safe_dump({"meta": {}, "units": [_unit("P3", "READY"), _unit("P4", "READY")]})
            )
            return await super().evaluate(prompt, timeout_s)

    evaluator = BreakingEvaluator([grounded_fix(LONG_A), EvaluatorDecision(decision=Decision.ACCEPT, summary="ok")])
    result = await _run(bits, evaluator)

    assert result.status is RunStatus.BLOCKED
    assert "more than one READY unit" in result.final_decision.summary


async def test_stale_phase_context_is_not_reused_between_iterations(bits, neyma: Path) -> None:
    registry = neyma / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml"

    class AdvancingEvaluator(ScriptedEvaluator):
        async def evaluate(self, prompt, timeout_s=None):
            if not self.prompts:  # after the first read, the repo advances
                result = await super().evaluate(prompt, timeout_s)
                registry.write_text(
                    yaml.safe_dump({"meta": {}, "units": [_unit("P3", "COMPLETE"), _unit("P4", "READY")]})
                )
                return result
            return await super().evaluate(prompt, timeout_s)

    evaluator = AdvancingEvaluator(
        [grounded_fix(LONG_A), EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)]
    )
    result = await _run(bits, evaluator)

    assert result.status is RunStatus.ACCEPTED
    assert "ACTIVE READY UNIT: P3" in evaluator.prompts[0]
    assert "ACTIVE READY UNIT: P4" in evaluator.prompts[1], "served stale phase context"


# -- prompt-quality gate in the loop ---------------------------------------


async def test_a_vague_fix_never_reaches_the_builder(bits) -> None:
    builder = FakeBuilder()
    evaluator = ScriptedEvaluator([grounded_fix("keep going")])
    result = await _run(bits, evaluator, builder)

    assert result.status is RunStatus.BLOCKED
    assert len(builder.prompts) == 1, "a vague correction was sent to the builder"
    assert any("vague" in p or "chars" in p for p in result.final_decision.problems)


async def test_a_fix_without_evidence_never_reaches_the_builder(bits) -> None:
    builder = FakeBuilder()
    evaluator = ScriptedEvaluator([grounded_fix(LONG_A, evidence_paths=[])])
    result = await _run(bits, evaluator, builder)

    assert result.status is RunStatus.BLOCKED
    assert len(builder.prompts) == 1
    assert any("evidence_paths" in p for p in result.final_decision.problems)


async def test_no_discrepancy_cannot_become_generated_work(bits) -> None:
    builder = FakeBuilder()
    same = "The load list rendered two loads with owners."
    evaluator = ScriptedEvaluator([grounded_fix(LONG_A, observed_result=same, expected_result=same)])
    result = await _run(bits, evaluator, builder)

    assert result.status is RunStatus.BLOCKED
    assert len(builder.prompts) == 1
    assert any("identical" in p for p in result.final_decision.problems)


async def test_a_rejected_decision_is_preserved_for_inspection(bits) -> None:
    config, store, *_ = bits
    evaluator = ScriptedEvaluator([grounded_fix("improve this")])
    await _run(bits, evaluator)

    rejected = store.run_dir / "iteration-01" / "rejected-decision.json"
    assert rejected.exists()
    data = json.loads(rejected.read_text())
    assert data["reasons"]
    assert data["decision"]["decision"] == "FIX"


async def test_repeated_identical_corrections_stop_the_loop(bits) -> None:
    """Re-sending a correction that did not work means the loop is not converging."""
    builder = FakeBuilder()
    evaluator = ScriptedEvaluator([grounded_fix(LONG_A), grounded_fix(LONG_A)])
    result = await _run(bits, evaluator, builder)

    assert result.status is RunStatus.BLOCKED
    assert any("not converging" in p for p in result.final_decision.problems)
    # Iteration 1's correction was sent; iteration 2's repeat was not.
    assert len(builder.prompts) == 2


async def test_genuinely_different_corrections_continue_the_loop(bits) -> None:
    builder = FakeBuilder()
    evaluator = ScriptedEvaluator([
        grounded_fix(LONG_A),
        grounded_fix(LONG_B),
        EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9),
    ])
    result = await _run(bits, evaluator, builder)

    assert result.status is RunStatus.ACCEPTED
    assert len(builder.prompts) == 3


async def test_previous_corrections_are_shown_to_the_evaluator(bits) -> None:
    evaluator = ScriptedEvaluator([
        grounded_fix(LONG_A),
        EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9),
    ])
    await _run(bits, evaluator)
    assert "CORRECTIONS ALREADY SENT IN THIS RUN" in evaluator.prompts[1]
    assert LONG_A[:40] in evaluator.prompts[1]


# -- provenance ------------------------------------------------------------


async def test_provenance_records_context_hash_head_and_unit(bits, neyma: Path) -> None:
    config, store, state, scenario, founder, loader, make_executor = bits
    evaluator = ScriptedEvaluator([EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)])
    await _run(bits, evaluator)

    manifest = json.loads((store.run_dir / "iteration-01" / "prompt-manifest.json").read_text())
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=neyma, capture_output=True, text=True
    ).stdout.strip()

    assert manifest["founder_context_version"] == founder.version
    assert manifest["repository_head"] == head
    assert manifest["active_unit_id"] == "P3"
    assert manifest["active_unit_status"] == "READY"
    assert any("IMPLEMENTATION-REGISTRY.yaml" in f for f in manifest["repository_files_consulted"])
    assert manifest["evidence_files_consulted"]
    assert manifest["prompt_chars"] > 0

    # Also stored alongside the iteration record.
    prov = json.loads((store.run_dir / "iteration-01" / "context-provenance.json").read_text())
    assert prov["active_unit_id"] == "P3"


async def test_the_assembled_prompt_is_stored(bits) -> None:
    config, store, *_ = bits
    evaluator = ScriptedEvaluator([EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)])
    await _run(bits, evaluator)

    prompt_file = store.run_dir / "iteration-01" / "evaluator-prompt.md"
    assert prompt_file.exists()
    text = prompt_file.read_text()
    assert "LAYER A" in text and "LAYER B" in text and "LAYER C" in text


async def test_stored_prompt_is_redacted(bits) -> None:
    config, store, state, scenario, founder, loader, make_executor = bits
    FounderFeedbackStore(store.run_dir).add("token is ghp_abcdefghijklmnopqrstuvwxyz012345")
    evaluator = ScriptedEvaluator([EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)])
    await _run(bits, evaluator)

    text = (store.run_dir / "iteration-01" / "evaluator-prompt.md").read_text()
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in text


# -- founder feedback precedence -------------------------------------------


async def test_founder_feedback_reaches_both_prompts(bits) -> None:
    config, store, state, scenario, founder, loader, make_executor = bits
    FounderFeedbackStore(store.run_dir).add("Never require approval to send a routine status update.")

    builder = FakeBuilder()
    evaluator = ScriptedEvaluator([EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)])
    await _run(bits, evaluator, builder)

    assert "routine status update" in builder.prompts[0]
    assert "routine status update" in evaluator.prompts[0]
    assert "overrides evaluator taste" in evaluator.prompts[0]


async def test_founder_feedback_outranks_evaluator_taste_but_not_the_repository(bits) -> None:
    config, store, state, scenario, founder, loader, make_executor = bits
    FounderFeedbackStore(store.run_dir).add("Approval prompts are friction; remove them.")

    evaluator = ScriptedEvaluator([EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)])
    await _run(bits, evaluator)
    prompt = evaluator.prompts[0]

    assert "overrides evaluator taste" in prompt
    assert "does NOT override the Neyma repository" in prompt
    # The repository's authority statement still appears, after the feedback.
    assert prompt.index("Approval prompts are friction") < prompt.index(
        "LAYER B — CURRENT NEYMA REPOSITORY AUTHORITY"
    )
    assert "AUTHORITATIVE" in prompt


async def test_founder_feedback_does_not_mutate_durable_context(bits, driver_root: Path) -> None:
    config, store, state, scenario, founder, loader, make_executor = bits
    before = (driver_root / "founder_context" / "PRODUCT_OWNER_CONTEXT.md").read_text()

    FounderFeedbackStore(store.run_dir).add("Neyma should never ask twice for one approval.")
    evaluator = ScriptedEvaluator([EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)])
    await _run(bits, evaluator)

    after = (driver_root / "founder_context" / "PRODUCT_OWNER_CONTEXT.md").read_text()
    assert after == before
    assert load_founder_context(driver_root).version == founder.version


async def test_repository_authority_appears_even_with_conflicting_feedback(bits) -> None:
    """Founder taste guides open choices; it never overrides the active unit."""
    config, store, state, scenario, founder, loader, make_executor = bits
    FounderFeedbackStore(store.run_dir).add("Start work on P4 adapter containment now.")

    evaluator = ScriptedEvaluator([EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)])
    await _run(bits, evaluator)
    prompt = evaluator.prompts[0]

    assert "ACTIVE READY UNIT: P3" in prompt
    assert "Scope your judgement to the active READY unit" in prompt
    assert "Work belonging to a later phase is out of scope" in prompt


async def test_founder_taste_guides_where_the_repository_is_silent(bits) -> None:
    """The rubric's ASK_USER boundaries must reach the evaluator."""
    evaluator = ScriptedEvaluator([EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)])
    await _run(bits, evaluator)
    prompt = evaluator.prompts[0]

    assert "repository_silent" in prompt
    assert "ASK_USER BOUNDARIES" in prompt
    assert "accountable_owner" in prompt

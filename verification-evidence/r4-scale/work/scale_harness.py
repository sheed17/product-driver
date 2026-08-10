"""R4 scale audit harness.

Builds real ScenarioSuites of N scenarios against a real local HTTP target and
executes them with the real SuiteExecutor + ScenarioExecutor. Nothing is mocked
except the evaluator LLM call, which is never made: we measure the *prompt* the
evaluator would receive and apply the real `_apply_suite_precedence`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

STAGE = Path(os.environ["R4_STAGE"])
sys.path.insert(0, str(STAGE))

from neyma_product_driver import cli as driver_cli  # noqa: E402
from neyma_product_driver.config import ScenarioRunConfig  # noqa: E402
from neyma_product_driver.models import Decision, EvaluatorDecision  # noqa: E402
from neyma_product_driver.prompts import evaluator_prompt  # noqa: E402
from neyma_product_driver.scenario_plan import (  # noqa: E402
    GeneratedScenario,
    Priority,
    RiskCategory,
)
from neyma_product_driver.scenario_suite import (  # noqa: E402
    Origin,
    Outcome,
    ScenarioSuite,
    SuiteEntry,
    SuiteExecutor,
    build_suite,
    select_rerun,
)
from neyma_product_driver.scenarios import (  # noqa: E402
    CommandSpec,
    RequestSpec,
    Scenario,
    ScenarioExecutor,
    ScenarioStep,
    StateCheckSpec,
    BrowserSpec,
    BrowserStep,
)

PORT = int(os.environ.get("TARGET_PORT", "8731"))
BASE = f"http://127.0.0.1:{PORT}"
TARGET_DIR = Path(os.environ["R4_TARGET"])
PY = sys.executable
EVID = Path(os.environ["R4_EVIDENCE"])


# --------------------------------------------------------------------------
# scenario construction — four real modes
# --------------------------------------------------------------------------


def http_scenario(i: int) -> Scenario:
    return Scenario(
        name=f"case-{i:03d}-http",
        description=f"GET /item/{i} returns the item owned by tenant-a",
        mode="backend",
        app_url=BASE,
        readiness=[{"http": f"{BASE}/health", "expect_status": 200}],
        requests=[
            RequestSpec(
                name=f"read item {i}",
                method="GET",
                path=f"/item/{i}",
                expect_status=200,
                expect_contains=[f'"item": {i}', '"owner": "tenant-a"'],
            )
        ],
        forbidden=["tenant-b"],
    )


def command_scenario(i: int) -> Scenario:
    return Scenario(
        name=f"case-{i:03d}-cmd",
        description=f"the persisted-state probe reports item {i}",
        mode="backend",
        app_url=BASE,
        readiness=[{"http": f"{BASE}/health", "expect_status": 200}],
        env={"TARGET_DB": os.environ["TARGET_DB"]},
        commands=[
            CommandSpec(
                name=f"probe {i}",
                run=f"{PY} {TARGET_DIR}/probe.py {i}",
                expect_exit_code=0,
                expect_contains=[f"item={i}"],
            )
        ],
    )


def idempotency_scenario(i: int) -> Scenario:
    """Ordered form: approve twice with one key, then read persisted state."""
    key = f"k-{i}"
    return Scenario(
        name=f"case-{i:03d}-idem",
        description=f"approving item {i} twice with one key stores exactly one row",
        mode="backend",
        app_url=BASE,
        readiness=[{"http": f"{BASE}/health", "expect_status": 200}],
        env={"TARGET_DB": os.environ["TARGET_DB"]},
        steps=[
            ScenarioStep(
                kind="request",
                name="first approve",
                request=RequestSpec(
                    method="POST", path="/approve",
                    json={"item": i, "key": key}, expect_status=200,
                ),
            ),
            ScenarioStep(
                kind="request",
                name="repeat approve, same key",
                request=RequestSpec(
                    method="POST", path="/approve",
                    json={"item": i, "key": key}, expect_status=200,
                ),
            ),
            ScenarioStep(
                kind="state_check",
                name="exactly one approval row persisted",
                state_check=StateCheckSpec(
                    command=f"{PY} {TARGET_DIR}/probe.py {i}",
                    contains=[f"item={i} approval_rows=1"],
                    not_contains=["approval_rows=2"],
                ),
            ),
        ],
    )


def browser_scenario(i: int) -> Scenario:
    return Scenario(
        name=f"case-{i:03d}-ui",
        description=f"the UI for item {i} shows tenant-a",
        mode="browser",
        app_url=f"{BASE}/ui/{i}",
        readiness=[{"http": f"{BASE}/health", "expect_status": 200}],
        browser=BrowserSpec(
            initial_screenshot=True,
            final_screenshot=False,
            steps=[BrowserStep(wait_for="#owner"), BrowserStep(expect_text="tenant-a")],
        ),
        expect_visible=["tenant-a", f"item-{i}"],
        forbidden=["tenant-b"],
    )


KINDS = [http_scenario, command_scenario, idempotency_scenario]
CATEGORIES = [
    RiskCategory.CROSS_TENANT,
    RiskCategory.PERSISTENCE_FAILURE,
    RiskCategory.IDEMPOTENCY,
]


def make_suite(n: int, *, browser_every: int = 0) -> ScenarioSuite:
    """n generated cases + 1 permanent regression scenario."""
    generated: list[tuple[GeneratedScenario, Scenario]] = []
    for i in range(1, n + 1):
        if browser_every and i % browser_every == 0:
            compiled = browser_scenario(i)
            category = RiskCategory.UI_BACKEND_DISAGREEMENT
        else:
            compiled = KINDS[i % len(KINDS)](i)
            category = CATEGORIES[i % len(CATEGORIES)]
        model = GeneratedScenario(
            id=compiled.name,
            title=f"item {i} behaves correctly",
            purpose=f"exercise item {i}",
            risk_category=category,
            priority=Priority.P0 if i % 5 == 0 else Priority.P1,
            rationale=f"item {i} is representative of the {category.value} surface",
            requirement_reference="R4-SCALE-AUDIT",
            isolation_key="default",
        )
        generated.append((model, compiled))
    permanent = [("permanent-health", http_scenario(1))]
    return build_suite(permanent=permanent, generated=generated)


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


def executor_factory(browser: bool):
    cfg = ScenarioRunConfig(
        command_timeout_s=30,
        readiness_timeout_s=20,
        readiness_poll_interval_s=0.2,
        http_timeout_s=15,
        browser_enabled=browser,
        headless=True,
        capture_trace=False,
    )
    repo = TARGET_DIR

    def make(artifact_dir: Path):
        return ScenarioExecutor(repo, cfg, artifact_dir)

    return make


async def run_suite(suite, artifact_root: Path, *, browser=False, budget=None, only=None,
                    reason=""):
    ex = SuiteExecutor(
        make_executor=executor_factory(browser),
        artifact_root=artifact_root,
        browser_enabled=browser,
        execution_budget_s=budget,
        max_parallel=1,
    )
    started = time.monotonic()
    result = await ex.run(suite, only=only, selection_reason=reason)
    return ex, result, time.monotonic() - started


def prompt_size(suite_result, primary):
    text = evaluator_prompt(
        task="scale audit",
        iteration=1,
        max_iterations=3,
        builder_summary="claimed everything works",
        git=None,
        scenario=primary,
        service_logs=None,
        evidence_dir=str(EVID),
        suite=suite_result,
    )
    return text

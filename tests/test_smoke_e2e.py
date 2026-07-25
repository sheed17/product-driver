"""Harmless end-to-end smoke test.

Exercises the whole driver with a fake builder and a fake evaluator against a
real, tiny, local HTTP app driven by a real Playwright browser:

    iteration 1 — the app renders a bare load list with no ownership or status
                  → deterministic FIX
    fake correction applied (the fake builder rewrites the app's HTML)
    iteration 2 — the app now states what happened, what is missing and who owns
                  the next step → ACCEPT

No Claude usage is consumed: both sessions are fakes. Playwright is required;
the test skips if chromium is not installed.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import sys
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
from neyma_product_driver.models import Decision, EvaluatorDecision, RunState, RunStatus
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

pytestmark = pytest.mark.e2e


# -- the tiny app the driver will operate ----------------------------------

BEFORE_HTML = """<!doctype html>
<html><head><title>Neyma</title></head><body>
<h1>Loads</h1>
<ul><li>LD560003</li><li>LD560004</li></ul>
</body></html>
"""

AFTER_HTML = """<!doctype html>
<html><head><title>Neyma</title></head><body>
<h1>Loads awaiting your attention</h1>
<ul>
  <li>LD560003 — Delivered. Proof of delivery received.
      <strong>Next: you approve the invoice.</strong></li>
  <li>LD560004 — Delivered. <em>Missing: proof of delivery.</em>
      Inferred from carrier check call. <strong>Next: Neyma is chasing the carrier.</strong></li>
</ul>
</body></html>
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
            return bool(path and Path(path).exists())
    except Exception:
        return False


# -- fakes -----------------------------------------------------------------


class FakeBuilderThatEdits:
    """Stands in for a Claude session; 'implements' by rewriting index.html."""

    def __init__(self, site_dir: Path) -> None:
        self.session_id = "fake-builder-session"
        self.site_dir = site_dir
        self.prompts: list[str] = []

    async def send(self, prompt: str, timeout_s: int | None = None):
        self.prompts.append(prompt)

        class Turn:
            text = ""
            session_id = self.session_id
            tool_uses: list[str] = []
            denied_requests: list[str] = []
            is_error = False
            error_detail = ""

        if "CORRECTION" in prompt:
            (self.site_dir / "index.html").write_text(AFTER_HTML)
            Turn.text = (
                "Rewrote the load list to state status, what is missing, and who "
                "owns the next step.\n\nRUNNABLE CHECKPOINT: reload the page."
            )
        else:
            (self.site_dir / "index.html").write_text(BEFORE_HTML)
            Turn.text = "Added a load list.\n\nRUNNABLE CHECKPOINT: open the page."
        return Turn()


class DeterministicEvaluator:
    """Judges the captured browser text by the product rubric — no model calls."""

    def __init__(self) -> None:
        self.session_id = "fake-evaluator-session"
        self.decisions: list[EvaluatorDecision] = []

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        # `prompt` contains the real DOM text Playwright captured.
        states_ownership = "Next:" in prompt
        states_missing = "Missing:" in prompt

        if states_ownership and states_missing:
            decision = EvaluatorDecision(
                decision=Decision.ACCEPT,
                summary="The load list now says what happened, what is missing, and who owns the next step.",
                observed_behavior=[
                    "Observed: LD560004 shows 'Missing: proof of delivery.'",
                    "Observed: each load names the next obligation and its owner.",
                ],
                rubric_categories=["obligation_visibility", "next_action_clarity", "evidence_clarity"],
                confidence=0.9,
            )
        else:
            # A fully grounded FIX — this is what the prompt-quality contract
            # requires before anything reaches the builder.
            decision = EvaluatorDecision(
                decision=Decision.FIX,
                summary="The list shows load numbers and nothing an operator can act on.",
                observed_behavior=["Observed: the page lists 'LD560003' and 'LD560004' with no status."],
                problems=[
                    "No load states what happened or what is missing.",
                    "No load names who owns the next obligation.",
                ],
                correction_prompt=(
                    "On the load list, each load must state its delivery status, what "
                    "evidence is missing, and who owns the next step. Use the exact word "
                    "'Missing:' before absent evidence and 'Next:' before the next "
                    "obligation, so a dispatcher can scan the list without opening a load."
                ),
                requirement_reference="P-SMOKE acceptance criterion: operator can act from the list",
                product_principle_reference="obligation_visibility, next_action_clarity",
                scenario="smoke-browser against the local load list",
                observed_result="The page listed LD560003 and LD560004 as bare identifiers, with no status, no missing evidence and no owner.",
                expected_result="Each load states its status, what evidence is missing, and who owns the next step.",
                evidence_paths=["iteration-01/screenshots/01-initial.png", "iteration-01/scenario.json"],
                preserve="The existing two-load ordering and the page title.",
                retest="Re-run the smoke-browser scenario and confirm 'Missing:' and 'Next:' both appear.",
                customer_facing=True,
                rubric_categories=["obligation_visibility", "next_action_clarity"],
                confidence=0.85,
            )
        self.decisions.append(decision)
        return decision


# -- the test --------------------------------------------------------------


def _install_context(fake_repo: Path, driver_root: Path) -> None:
    """Give the fake repo a registry and the driver a real founder context."""
    impl = fake_repo / "docs" / "implementation"
    impl.mkdir(parents=True, exist_ok=True)
    (fake_repo / "CLAUDE.md").write_text(
        "# CLAUDE.md\n## Authority\nThis file outranks every other instruction file.\n"
    )
    (impl / "CURRENT.md").write_text("# CURRENT\n## Status\nP-SMOKE is the sole READY unit.\n")
    (impl / "IMPLEMENTATION-REGISTRY.yaml").write_text(
        yaml.safe_dump(
            {
                "meta": {},
                "units": [
                    {
                        "unit_id": "P-SMOKE",
                        "name": "Operator load list",
                        "status": "READY",
                        "objective": "An operator can act directly from the load list.",
                        "acceptance_contract": "acceptance.md",
                        "acceptance_criteria": [
                            {"criterion": "core_implementation", "weight": 20, "result": "PENDING"}
                        ],
                    }
                ],
            }
        )
    )
    driver_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        Path(__file__).resolve().parent.parent / "founder_context",
        driver_root / "founder_context",
        dirs_exist_ok=True,
    )


@pytest.mark.skipif(not _chromium_available(), reason="chromium not installed (playwright install chromium)")
async def test_end_to_end_fix_then_accept(tmp_path: Path, fake_repo: Path) -> None:
    site = fake_repo / "site"
    site.mkdir()
    (site / "index.html").write_text(BEFORE_HTML)
    port = _free_port()

    driver_root = tmp_path / "driver"
    _install_context(fake_repo, driver_root)

    config = DriverConfig(
        neyma_repo=fake_repo,
        driver_root=driver_root,
        max_iterations=3,
        task="Build a load list an operator can act on.",
    )
    config.run.browser_enabled = True
    config.run.headless = True
    config.run.readiness_timeout_s = 30
    config.run.readiness_poll_interval_s = 0.3

    scenario = Scenario(
        name="smoke-browser",
        mode="browser",
        services=[
            {
                "name": "site",
                "command": f"{sys.executable} -m http.server {port} --directory {site}",
            }
        ],
        readiness=[{"http": f"http://127.0.0.1:{port}/", "expect_status": 200}],
        app_url=f"http://127.0.0.1:{port}",
        browser={"steps": [{"goto": "/", "screenshot": "load-list"}]},
        forbidden=["Traceback"],
    )

    assert config.runs_dir is not None
    store = EvidenceStore(config.runs_dir, "smoke-run")
    state = RunState(run_id=store.run_id, task=config.task, max_iterations=config.max_iterations)

    builder = FakeBuilderThatEdits(site)
    evaluator = DeterministicEvaluator()
    founder = load_founder_context(driver_root)
    repo_loader = RepositoryContextLoader(fake_repo)

    # Founder direction for this run only — must reach both prompts.
    FounderFeedbackStore(store.run_dir).add("Say 'Missing:' plainly; never hide absent evidence.")

    result = await run_control_loop(
        config=config,
        scenario=scenario,
        store=store,
        state=state,
        builder=builder,
        evaluator=evaluator,
        make_executor=lambda artifact_dir: ScenarioExecutor(fake_repo, config.run, artifact_dir),
        emit=lambda _m: None,
        founder=founder,
        repo_loader=repo_loader,
    )

    # --- the loop reached ACCEPT via exactly one FIX ---
    assert result.status is RunStatus.ACCEPTED, f"ended as {result.status}"
    assert [d.decision for d in evaluator.decisions] == [Decision.FIX, Decision.ACCEPT]
    assert len(builder.prompts) == 2
    assert "CORRECTION" in builder.prompts[1]
    assert "Missing:" in builder.prompts[1]

    # --- the browser really ran and captured evidence ---
    first = state.iterations[0]
    assert first.scenario is not None and first.scenario.browser is not None
    browser_obs = first.scenario.browser
    assert "LD560003" in browser_obs.visible_text
    assert browser_obs.screenshots, "no screenshots captured"
    assert Path(browser_obs.screenshots[0]).exists()
    assert browser_obs.trace_path and Path(browser_obs.trace_path).exists()

    # --- the second iteration observed the corrected product ---
    second = state.iterations[1]
    assert second.scenario is not None and second.scenario.browser is not None
    assert "Next:" in second.scenario.browser.visible_text
    assert "Missing:" in second.scenario.browser.visible_text

    # --- evidence is on disk, including the accepted copy ---
    assert (store.run_dir / "iteration-01" / "decision.json").exists()
    assert (store.run_dir / "iteration-01" / "correction-prompt.md").exists()
    assert (store.run_dir / "iteration-02" / "decision.json").exists()
    assert (store.run_dir / "accepted" / "record.json").exists()

    # --- the context layer was assembled, recorded, and reached the builder ---
    manifest = json.loads((store.run_dir / "iteration-01" / "prompt-manifest.json").read_text())
    assert manifest["founder_context_version"] == founder.version
    assert manifest["active_unit_id"] == "P-SMOKE"
    assert manifest["repository_head"]
    assert manifest["founder_feedback_count"] == 1

    prompt = (store.run_dir / "iteration-01" / "evaluator-prompt.md").read_text()
    assert "LAYER A" in prompt and "LAYER B" in prompt and "LAYER C" in prompt
    assert "ACTIVE READY UNIT: P-SMOKE" in prompt
    assert "never hide absent evidence" in prompt

    # The grounded correction, not just free text, reached the builder.
    correction = (store.run_dir / "iteration-01" / "correction-prompt.md").read_text()
    assert "OBSERVED RESULT:" in correction
    assert "EXPECTED RESULT:" in correction
    assert "RETEST:" in correction
    assert "P-SMOKE" in correction
    assert "never hide absent evidence" in correction

    # Durable founder context was not mutated by run feedback.
    assert load_founder_context(driver_root).version == founder.version

    # --- no service was left running ---
    await asyncio.sleep(0.5)
    with socket.socket() as s:
        s.settimeout(1)
        assert s.connect_ex(("127.0.0.1", port)) != 0, "the site server was left running"


@pytest.mark.skipif(not _chromium_available(), reason="chromium not installed")
async def test_browser_captures_console_and_network_errors(tmp_path: Path, fake_repo: Path) -> None:
    """The evaluator must be able to see failures a user would notice."""
    site = fake_repo / "broken"
    site.mkdir()
    (site / "index.html").write_text(
        "<!doctype html><html><body><h1>Broken</h1>"
        "<script>console.error('boom'); fetch('/missing.json');</script>"
        "</body></html>"
    )
    port = _free_port()

    config = DriverConfig(neyma_repo=fake_repo, driver_root=tmp_path / "d")
    config.run.browser_enabled = True
    config.run.readiness_timeout_s = 30
    config.run.readiness_poll_interval_s = 0.3

    scenario = Scenario(
        name="broken",
        mode="browser",
        services=[{"name": "site", "command": f"{sys.executable} -m http.server {port} --directory {site}"}],
        readiness=[{"http": f"http://127.0.0.1:{port}/", "expect_status": 200}],
        app_url=f"http://127.0.0.1:{port}",
        browser={"steps": [{"wait_ms": 800}]},
    )

    result = await ScenarioExecutor(fake_repo, config.run, tmp_path / "art").execute(scenario)

    assert result.browser is not None
    assert any("boom" in e for e in result.browser.console_errors)
    assert any("404" in f or "missing.json" in f for f in result.browser.network_failures)

"""Regression coverage for the nine blocking defects of remediation cycle 1.

Every defect below was independently reproduced by an adjudicating session and
upheld as blocking, and every one of them had *no* test pinning it — which is
how a 30/30 mutation score coexisted with nine of them. Each class here fails
on the candidate as submitted and passes on the remediated code.

No Claude session is consumed. Everything here drives stub pages and fakes
except one test marked ``e2e``, which launches a real chromium against a closed
loopback port to exercise the degraded browser exit as it actually happens.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from neyma_product_driver import cli as driver_cli
from neyma_product_driver.cli import (
    LoopResult,
    _recorded_suite_gate,
    _report_outcome,
    run_control_loop,
)
from neyma_product_driver.config import ScenarioRunConfig
from neyma_product_driver.evidence import EvidenceStore, sanitize_filename
from neyma_product_driver.models import (
    BrowserObservation,
    Decision,
    RunState,
    RunStatus,
    ScenarioResult,
    redact_obj,
)
from neyma_product_driver.scenario_gate import evaluate_gate
from neyma_product_driver.scenario_plan import (
    GeneratedAction,
    GeneratedBrowserStep,
    GeneratedScenarioPlan,
)
from neyma_product_driver.scenario_planner import PLAN_FILENAME, WAVES_DIRNAME, ScenarioPlanner
from neyma_product_driver.scenario_suite import (
    Origin,
    Outcome,
    Priority,
    ScenarioSuite,
    SuiteEntry,
    SuiteExecutor,
    SuiteResult,
    build_suite,
    write_case_evidence,
)
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    resolve_browser_target,
    validate_scenario,
)
from neyma_product_driver.scenarios import BrowserStep, Scenario, ScenarioExecutor, load_scenario

from scenario_fixtures import (
    APPROVED_STATE,
    FakeFounder,
    ScriptedReasoner,
    base_scenario,
    make_scenario,
    raw_payload,
    raw_scenario,
    validation_context,
)
from test_scenario_loop import (
    FakeBuilder,
    FakeEvaluator,
    FakeRepoLoader,
    RecordingExecutor,
    accept,
    loop_bits,  # noqa: F401 — pytest fixture, used by name
    make_planner,
)


# --------------------------------------------------------------------------
# CG-02 — the completion-audit branch terminated before the gate was computed
# --------------------------------------------------------------------------


def _review_auditor(blocks: bool = True):
    from neyma_product_driver.completion_auditor import AuditDecision, CompletionAudit

    class ReviewAuditor:
        def audit(self, report, unit=None, run_commands=None, evidence_dir="", scope=None):
            return CompletionAudit(
                decision=AuditDecision.REQUIRES_INDEPENDENT_REVIEW,
                headline="independent review criteria are pending",
                blocks_acceptance=blocks,
                confidence=0.9,
            )

    return ReviewAuditor()


class TestTheGatePrecedesEveryTerminalState:
    @pytest.mark.asyncio
    async def test_a_failing_required_scenario_is_not_held_for_review_as_an_accept(
        self, loop_bits  # noqa: F811
    ):
        """The defect, exactly.

        Evaluator says ACCEPT, the completion audit blocks with
        REQUIRES_INDEPENDENT_REVIEW, and a required scenario has failed. The
        audit branch returned before ``_apply_suite_precedence`` was reached, so
        the run recorded ``final_decision: ACCEPT``, exited 14 and printed
        nothing about the failure. The gate must decide first.
        """
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])

        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {"gen-dup": False}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            auditor=_review_auditor(),
            planner=planner,
        )

        assert result.status is not RunStatus.NEEDS_INDEPENDENT_REVIEW
        assert result.final_decision.decision is not Decision.ACCEPT
        assert state.final_decision.decision is not Decision.ACCEPT
        assert driver_cli._exit_code_for(result.status) != 14

    @pytest.mark.asyncio
    async def test_the_review_hold_still_carries_the_suite_it_executed(
        self, loop_bits  # noqa: F811
    ):
        """The dropped-fields half: four positional arguments, seven fields.

        With a green suite the hold is legitimate, and the result must still
        carry the suite, the gate verdict and the promotion ledger — without
        them ``_report_coverage`` returns immediately and the run prints no
        coverage section, no gate verdict and no plan pointer at all.
        """
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-ok"))])

        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            auditor=_review_auditor(),
            planner=planner,
        )

        assert result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW
        assert result.suite is not None
        assert result.gate is not None
        assert result.promotion_candidates == []

    def test_a_loop_result_cannot_be_constructed_positionally(self):
        """The systemic half. A positional construction is a silent truncation."""
        state = RunState(run_id="r", task="t")
        with pytest.raises(TypeError):
            LoopResult(RunStatus.STOPPED, state)  # type: ignore[misc]

    @pytest.fixture(autouse=True)
    def _report_root(self, tmp_path):
        self._report_dir = tmp_path

    def test_the_review_hold_does_not_assert_that_the_evaluation_passed(self, capsys):
        """The console statement of fact that was false whenever the gate blocked."""
        suite = SuiteResult(expected_required_ids=["gen-a"])
        gate = evaluate_gate(suite)
        result = LoopResult(
            status=RunStatus.NEEDS_INDEPENDENT_REVIEW,
            state=RunState(run_id="r", task="t"),
            suite=suite,
            gate=gate,
        )
        _report_outcome(result, EvidenceStore(self._report_dir, "report-probe"))
        printed = capsys.readouterr().out
        assert "the product evaluation passed" not in printed
        assert gate.headline() in printed

    def test_a_recorded_unverified_gate_is_visible_to_review(self):
        """``cmd_review`` had no way to know the gate had refused."""
        from neyma_product_driver.models import IterationRecord

        failing = SuiteResult(
            expected_required_ids=["gen-a"],
            outcomes=[
                {
                    "scenario_id": "gen-a",
                    "scenario_name": "gen-a",
                    "origin": "generated",
                    "outcome": "FAILED",
                    "required": True,
                }
            ],
        )
        state = RunState(run_id="r", task="t")
        state.iterations = [
            IterationRecord(iteration=1, suite=failing.model_dump(mode="json"))
        ]
        gate = _recorded_suite_gate(state)
        assert gate is not None and gate.blocks_acceptance

        # And the command consults it before it spends the reviewer.
        source = inspect.getsource(driver_cli.cmd_review)
        assert source.index("_recorded_suite_gate") < source.index("IndependentReviewerSession")

    def test_a_run_with_no_suite_is_not_reported_as_gated(self):
        assert _recorded_suite_gate(RunState(run_id="r", task="t")) is None


# --------------------------------------------------------------------------
# D-SAFETY-01 — model-authored fixture content executing as code
# --------------------------------------------------------------------------


class TestAFixtureCannotBeAProgram:
    @pytest.mark.parametrize(
        "name",
        ["probe_case.py", "conftest.py", "setup.sh", "payload.js", "pytest.ini", "x.PY"],
    )
    def test_a_fixture_an_interpreter_would_run_is_refused(self, name):
        """The chain in one line: the fixture's path becomes an argument to an
        approved interpreter *after* validation, so a fixture that is a program
        is model-authored code with the driver's own authority."""
        scenario = make_scenario(
            actions=[
                GeneratedAction(
                    kind="fixture", fixture_name=name, fixture_content="print('x')"
                )
            ]
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("fixture name" in r and "must end in" in r for r in reasons), reasons

    @pytest.mark.parametrize("name", ["invoice.json", "rows.csv", "notes.txt", "cfg.yaml"])
    def test_a_data_fixture_is_still_permitted(self, name):
        """The documented capability is preserved: an invoice JSON fed to a CLI."""
        scenario = make_scenario(
            actions=[
                GeneratedAction(kind="fixture", fixture_name=name, fixture_content="{}"),
                GeneratedAction(
                    kind="command",
                    name="read it",
                    command=f"{APPROVED_STATE} {{{{fixture:{name}}}}}",
                    expect_exit_code=0,
                ),
            ]
        )
        assert validate_scenario(scenario, validation_context()) == []

    def test_the_generator_is_told_the_rule_it_will_be_judged_against(self):
        """A boundary the prompt still advertises around earns refused waves."""
        from neyma_product_driver.scenario_generator import GENERATOR_SYSTEM

        assert ".json" in GENERATOR_SYSTEM
        assert "conftest.py" in GENERATOR_SYSTEM

    @pytest.mark.asyncio
    async def test_the_command_that_runs_is_the_command_that_was_validated(self, tmp_path):
        """Substitution happened after validation, so the string that was judged
        safe and the string that became a subprocess were different strings."""
        executor = ScenarioExecutor(
            tmp_path,
            ScenarioRunConfig(),
            tmp_path / "art",
            approved_commands=ApprovedCommands(["echo hi"]),
        )
        fixtures = tmp_path / "art" / "fixtures"
        fixtures.mkdir(parents=True)
        (fixtures / "data.json").write_text("{}")
        executor._fixtures["data.json"] = str(fixtures / "data.json")

        scenario = Scenario(
            name="probe",
            steps=[
                {
                    "kind": "command",
                    "name": "unapproved-after-substitution",
                    "command": {"run": "nope {{fixture:data.json}}"},
                }
            ],
        )
        result = await executor.execute(scenario)
        assert not result.passed
        assert any(
            "is not the command that would have run" in a.detail for a in result.assertions
        )
        assert result.commands == []

    @pytest.mark.asyncio
    async def test_a_data_fixture_still_reaches_the_command_that_consumes_it(self, tmp_path):
        """The re-check must not cost the capability the prompt advertises."""
        executor = ScenarioExecutor(
            tmp_path,
            ScenarioRunConfig(),
            tmp_path / "art",
            approved_commands=ApprovedCommands(["echo hi"]),
        )
        scenario = Scenario(
            name="probe",
            steps=[
                {
                    "kind": "fixture",
                    "fixture_name": "invoice.json",
                    "fixture_content": '{"amount": 1}',
                },
                {
                    "kind": "command",
                    "name": "hand it to the CLI",
                    "command": {"run": "echo hi {{fixture:invoice.json}}", "expect_exit_code": 0},
                },
            ],
        )
        result = await executor.execute(scenario)
        assert result.passed, [a.detail for a in result.assertions if not a.passed]
        assert result.commands and "invoice.json" in result.commands[0].command

    @pytest.mark.asyncio
    async def test_a_placeholder_may_only_expand_inside_the_run_fixture_directory(
        self, tmp_path
    ):
        executor = ScenarioExecutor(tmp_path, ScenarioRunConfig(), tmp_path / "art")
        executor._fixtures["escape"] = "/etc/passwd"
        scenario = Scenario(
            name="probe",
            steps=[
                {
                    "kind": "command",
                    "name": "escape",
                    "command": {"run": "echo {{fixture:escape}}"},
                }
            ],
        )
        result = await executor.execute(scenario)
        assert not result.passed
        assert any("outside this run's fixture directory" in a.detail for a in result.assertions)
        assert result.commands == []


# --------------------------------------------------------------------------
# D-SAFETY-02 / F-4 — the validator screened less than the executor navigated
# --------------------------------------------------------------------------


SCHEME_SHAPED = [
    "http:/example.com/admin",
    "http:example.com/admin",
    "http:\\\\example.com\\admin",
    "httpx://example.com/admin",
    "//example.com/admin",
    "javascript:alert(1)",
    "file:///etc/passwd",
]


class TestBrowserNavigationIsAnAllowlist:
    @pytest.mark.parametrize("goto", SCHEME_SHAPED)
    def test_a_scheme_shaped_navigation_is_refused(self, goto):
        """Every one of these was admitted with zero reasons and navigated to:
        the validator inspected only ``http://``/``https://`` while the executor
        treated anything starting ``http`` as absolute."""
        scenario = make_scenario(
            mode="browser",
            actions=[
                GeneratedAction(kind="browser", browser_steps=[GeneratedBrowserStep(goto=goto)])
            ],
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("unsupported external navigation" in r for r in reasons), (goto, reasons)

    @pytest.mark.parametrize("goto", ["/", "/operator/", "/invoices/LD5600"])
    def test_relative_navigation_still_passes(self, goto):
        """The shipped browser template uses exactly these."""
        scenario = make_scenario(
            mode="browser",
            actions=[
                GeneratedAction(kind="browser", browser_steps=[GeneratedBrowserStep(goto=goto)])
            ],
        )
        assert validate_scenario(scenario, validation_context()) == []

    def test_the_shipped_browser_template_still_validates(self):
        shipped = load_scenario(Path("scenarios/browser_generic.yaml"))
        gotos = [
            step.goto
            for step in (shipped.browser.steps if shipped.browser else [])
            if step.goto is not None
        ]
        assert gotos, "the template is expected to navigate"
        for goto in gotos:
            _target, problem = resolve_browser_target(app_url=shipped.app_url, goto=goto)
            assert problem == "", (goto, problem)

    @pytest.mark.parametrize("goto", SCHEME_SHAPED)
    def test_the_executor_refuses_what_the_validator_refuses(self, goto):
        """One resolution function, two callers. The executor used to re-derive
        the target from ``startswith("http")`` and reach a host nothing checked."""

        class StubPage:
            def __init__(self) -> None:
                self.visited: list[str] = []

            async def goto(self, url, **_kw):
                self.visited.append(url)

            async def evaluate(self, _script):
                return ""

        page = StubPage()
        obs = BrowserObservation()
        executor = object.__new__(ScenarioExecutor)
        asyncio.run(
            ScenarioExecutor._run_step(
                executor,
                page,
                BrowserStep(goto=goto),
                obs,
                Path("."),
                1,
                "http://127.0.0.1:8931",
                "",
            )
        )
        assert page.visited == []
        assert obs.step_failures
        assert not obs.page_loaded


# --------------------------------------------------------------------------
# R1 — key-based redaction destroyed a typed count and the plan with it
# --------------------------------------------------------------------------


class TestRedactionPreservesTypes:
    def test_a_count_under_a_secret_shaped_key_survives(self):
        masked = redact_obj({"by_risk_category": {"authorization": 3, "boundary": 1}})
        assert masked["by_risk_category"]["authorization"] == 3

    def test_a_credential_under_a_secret_shaped_key_is_still_masked(self):
        masked = redact_obj({"authorization": "Bearer sk-ant-abcdefgh", "token": "abc123"})
        assert masked["authorization"] == "[REDACTED]"
        assert masked["token"] == "[REDACTED]"

    def test_strings_nested_under_a_secret_key_are_still_masked(self):
        masked = redact_obj({"credentials": {"user": "neyma", "rounds": 4, "keys": ["k1"]}})
        assert masked["credentials"]["user"] == "[REDACTED]"
        assert masked["credentials"]["keys"] == ["[REDACTED]"]
        assert masked["credentials"]["rounds"] == 4

    def test_a_plan_that_names_authorization_can_be_read_back(self, tmp_path):
        """The whole defect in one round trip: one authorization-category
        scenario made the run's own plan permanently unparseable."""
        store = EvidenceStore(tmp_path, "20260810-000000")
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=_generation_config(),
            reasoner=ScriptedReasoner([]),
            store=store,
            base_scenario=base_scenario(),
            founder=FakeFounder(),
        )
        planner.plan.coverage_summary.by_risk_category = {"authorization": 2, "boundary": 1}
        planner.persist()

        raw = (store.run_dir / PLAN_FILENAME).read_text(encoding="utf-8")
        restored = GeneratedScenarioPlan.model_validate_json(raw)
        assert restored.coverage_summary.by_risk_category["authorization"] == 2

    def test_persist_refuses_a_payload_it_could_not_read_back(self, tmp_path, monkeypatch):
        """The systemic half: no write-time transform may silently render the
        run's own state record unreadable, whatever the transform is."""
        store = EvidenceStore(tmp_path, "20260810-000001")
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=_generation_config(),
            reasoner=ScriptedReasoner([]),
            store=store,
            base_scenario=base_scenario(),
            founder=FakeFounder(),
            emit=lambda _m: None,
        )
        planner.persist()
        good = (store.run_dir / PLAN_FILENAME).read_text(encoding="utf-8")

        def corrupting(obj: Any) -> Any:
            payload = redact_obj(obj)
            payload["coverage_summary"]["by_risk_category"]["authorization"] = "[REDACTED]"
            return payload

        monkeypatch.setattr("neyma_product_driver.scenario_planner.redact_obj", corrupting)
        planner.persist()

        assert (store.run_dir / PLAN_FILENAME).read_text(encoding="utf-8") == good


def _generation_config():
    from neyma_product_driver.config import ScenarioGenerationConfig

    return ScenarioGenerationConfig(enabled=True, max_waves=1)


# --------------------------------------------------------------------------
# R2 — an unreadable plan was fail-open, and the next persist destroyed it
# --------------------------------------------------------------------------


def _planner_for(tmp_path: Path, run_id: str, **kw) -> ScenarioPlanner:
    store = EvidenceStore(tmp_path, run_id)
    return ScenarioPlanner(
        repo=tmp_path,
        config=kw.pop("config", None) or _generation_config(),
        reasoner=ScriptedReasoner(kw.pop("payloads", [])),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
        emit=kw.pop("emit", lambda _m: None),
    )


class TestAnUnreadablePlanFailsClosed:
    def test_no_plan_and_an_unreadable_plan_are_different_states(self, tmp_path):
        absent = _planner_for(tmp_path, "20260810-000010").restore_from_store()
        assert absent.state == "absent"
        assert not absent.unreadable

        planner = _planner_for(tmp_path, "20260810-000011")
        (planner.store.run_dir / PLAN_FILENAME).write_text('{"scenarios": [', encoding="utf-8")
        broken = planner.restore_from_store()
        assert broken.state == "unreadable"
        assert broken.unreadable

    def test_the_unreadable_plan_is_preserved_and_never_overwritten(self, tmp_path):
        planner = _planner_for(tmp_path, "20260810-000012")
        original = '{"scenarios": ['
        path = planner.store.run_dir / PLAN_FILENAME
        path.write_text(original, encoding="utf-8")

        outcome = planner.restore_from_store()
        planner.persist()  # the write that used to destroy the surviving record

        preserved = Path(outcome.preserved_path)
        assert preserved.exists() and preserved != path
        assert preserved.read_text(encoding="utf-8") == original

    def test_a_spent_wave_budget_is_not_returned_by_a_corrupt_plan(self, tmp_path):
        planner = _planner_for(tmp_path, "20260810-000013")
        waves = planner.store.run_dir / WAVES_DIRNAME
        waves.mkdir(parents=True, exist_ok=True)
        (waves / "wave-01.json").write_text(
            json.dumps({"wave": 1, "stage": "initial"}), encoding="utf-8"
        )
        (planner.store.run_dir / PLAN_FILENAME).write_text("{", encoding="utf-8")

        planner.restore_from_store()
        assert planner.waves_used == 1
        assert planner.budget_exhausted()

    def test_an_unreadable_plan_blocks_acceptance(self, tmp_path):
        planner = _planner_for(tmp_path, "20260810-000014")
        (planner.store.run_dir / PLAN_FILENAME).write_text("{", encoding="utf-8")
        planner.restore_from_store()

        assert planner.restore_failed
        problems = planner.generation_problems()
        assert problems
        assert evaluate_gate(None, generation_problems=problems).blocks_acceptance

    def test_the_plan_is_written_whole_or_not_at_all(self, tmp_path, monkeypatch):
        """``Path.write_text`` truncates first, so a crash inside the window
        leaves a plan no resume can read — and a crash mid-run is the event
        resume exists to survive."""
        planner = _planner_for(tmp_path, "20260810-000015")
        planner.persist()
        path = planner.store.run_dir / PLAN_FILENAME
        before = path.read_text(encoding="utf-8")

        real_replace = Path.replace

        def exploding(self, target):
            if Path(target).name == PLAN_FILENAME:
                raise OSError("interrupted before the move")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", exploding)
        planner.plan.task = "something new that must not half-land"
        with pytest.raises(OSError):
            planner.persist()

        assert path.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# I1 / G-SCALE-02 — scenario_id -> evidence directory was not injective
# --------------------------------------------------------------------------


class TestEvidenceDirectoriesAreInjective:
    def test_folding_and_case_no_longer_merge_two_labels(self):
        assert sanitize_filename("approve twice") != sanitize_filename("approve-twice")
        assert (
            sanitize_filename("gen-AUTH-01").casefold()
            != sanitize_filename("gen-auth-01").casefold()
        )
        assert sanitize_filename("gen-auth-01") == "gen-auth-01"

    def test_two_ids_differing_only_in_case_are_refused_by_the_suite(self):
        upper = make_scenario("gen-AUTH-01")
        lower = make_scenario("gen-auth-01", expected_observations=["something else"])
        suite = build_suite(
            generated=[(upper, Scenario(name="a")), (lower, Scenario(name="b"))]
        )
        assert len(suite) == 1
        assert suite.assembly_conflicts
        # And the refusal reaches the gate through the channel it already uses.
        result = SuiteResult(assembly_problems=list(suite.assembly_conflicts))
        assert evaluate_gate(result).blocks_acceptance

    def test_a_generated_id_cannot_case_match_a_permanent_scenario_name(self):
        scenario = make_scenario("Backend_Generic")
        reasons = validate_scenario(
            scenario, validation_context(existing_ids={"backend_generic"})
        )
        assert any("already used in this run" in r for r in reasons), reasons

    def test_a_permanent_scenario_name_that_folds_is_refused_at_its_source(self, tmp_path):
        path = tmp_path / "s.yaml"
        path.write_text("name: approve twice\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be made only of"):
            load_scenario(path)

    @pytest.mark.asyncio
    async def test_an_occupied_evidence_directory_is_refused_not_overwritten(self, tmp_path):
        """The backstop, which makes no assumption about how the filesystem
        compares two names: it observes the collision that actually happened."""
        entry = SuiteEntry(
            scenario_id="gen-a",
            scenario=Scenario(name="gen-a"),
            origin=Origin.GENERATED,
            priority=Priority.P0,
        )
        suite = ScenarioSuite()
        suite.add(entry)

        artifact_root = tmp_path / "iteration-01"
        occupied = artifact_root / "scenarios" / sanitize_filename("gen-a")
        occupied.mkdir(parents=True)
        write_case_evidence(
            occupied,
            ScenarioResult(scenario_name="someone-else"),
            scenario_id="someone-else",
            run_id="run-1",
            iteration=1,
        )

        executor = SuiteExecutor(
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            artifact_root=artifact_root,
            run_id="run-1",
            iteration=1,
            emit=lambda _m: None,
        )
        result = await executor.run(suite)

        assert result.assembly_problems
        assert result.by_id("gen-a").outcome is Outcome.BLOCKED
        assert evaluate_gate(result).blocks_acceptance
        # The record that was already there is intact.
        record = json.loads((occupied / "result.json").read_text(encoding="utf-8"))
        assert record["scenario_id"] == "someone-else"

    @pytest.mark.asyncio
    async def test_a_folding_filesystem_cannot_merge_two_evidence_records(
        self, tmp_path, monkeypatch
    ):
        """Simulates a volume whose equivalence relation nobody enumerated, by
        folding every name onto one. Correctness must not depend on having
        modelled it."""
        monkeypatch.setattr(
            "neyma_product_driver.scenario_suite.sanitize_filename", lambda _n: "one-dir"
        )
        suite = ScenarioSuite()
        for scenario_id in ("gen-a", "gen-b"):
            suite.add(
                SuiteEntry(
                    scenario_id=scenario_id,
                    scenario=Scenario(name=scenario_id),
                    origin=Origin.GENERATED,
                    priority=Priority.P0,
                )
            )

        executor = SuiteExecutor(
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            artifact_root=tmp_path / "iteration-01",
            run_id="run-1",
            iteration=1,
            emit=lambda _m: None,
        )
        result = await executor.run(suite)

        assert result.assembly_problems
        assert not evaluate_gate(result).status.value == "VERIFIED"
        assert not any(o.evidence_verified for o in result.outcomes)


# --------------------------------------------------------------------------
# G-SCALE-01 — resume-time drops reached nothing but the terminal
# --------------------------------------------------------------------------


class TestResumeTimeDropsAreVisible:
    def _run_with_a_plan(self, tmp_path: Path, run_id: str) -> ScenarioPlanner:
        planner = _planner_for(
            tmp_path, run_id, payloads=[raw_payload(raw_scenario("gen-dup"))]
        )
        planner.plan_initial(task="build supervised approval", unit=_unit(), run_id=run_id)
        assert planner.plan.scenarios, "the fixture must produce a plan to lose"
        return planner

    def test_a_dropped_scenario_reaches_the_gate_and_the_plan(self, tmp_path):
        """The plan committed to it, the builder was told about it, and on
        resume it can no longer run. That is lost coverage, and the only report
        was a print the gate never saw."""
        run_id = "20260810-000020"
        self._run_with_a_plan(tmp_path, run_id)

        # A resume against a base scenario that declares no services: exactly
        # what omitting --scenario used to produce.
        store = EvidenceStore(tmp_path, run_id)
        narrowed = ScenarioPlanner(
            repo=tmp_path,
            config=_generation_config(),
            reasoner=ScriptedReasoner([]),
            store=store,
            base_scenario=Scenario(name="backend_generic"),
            permanent_scenarios=[Scenario(name="backend_generic")],
            founder=FakeFounder(),
            emit=lambda _m: None,
        )
        outcome = narrowed.restore_from_store()

        assert outcome.state == "restored"
        assert narrowed.plan.scenarios == []
        problems = narrowed.generation_problems()
        assert any("could not be restored on resume" in p for p in problems), problems
        assert evaluate_gate(None, generation_problems=problems).blocks_acceptance

        # And it survives in the plan on disk rather than in scrollback.
        persisted = GeneratedScenarioPlan.model_validate_json(
            (store.run_dir / PLAN_FILENAME).read_text(encoding="utf-8")
        )
        assert any(w.stage == "resume" and w.rejected for w in persisted.waves)

    def test_a_resume_prefers_the_runs_own_base_scenario(self):
        """The cheap companion: omitting --scenario silently swapped the base
        scenario underneath the plan built against it."""
        source = inspect.getsource(driver_cli.cmd_run)
        assert "state.scenario_name" in source
        assert source.index("state.scenario_name") < source.index("load_scenario(")


def _unit():
    from scenario_fixtures import FakeUnit

    return FakeUnit()


# --------------------------------------------------------------------------
# F-1 + F-2 — nothing required a browser scenario to have observed anything
# --------------------------------------------------------------------------


class TestABrowserScenarioMustHaveObservedSomething:
    def test_a_session_that_never_loaded_a_page_cannot_pass(self):
        result = ScenarioResult(scenario_name="s")
        obs = BrowserObservation(steps=["ERROR: TimeoutError: Page.goto: Timeout 30000ms"])
        ScenarioExecutor._assert_browser_text(result, obs)
        assert result.assertions
        assert not result.passed

    def test_a_missing_playwright_install_is_not_a_pass(self, tmp_path, monkeypatch):
        """The second door into the same room, and the one that proves the
        first was not a considered design decision: the chromium *binary*
        missing fails closed, while the *package* missing failed open."""

        class Blocker:
            def find_module(self, name, path=None):
                return None

            def find_spec(self, name, path=None, target=None):
                if name == "playwright" or name.startswith("playwright."):
                    raise ModuleNotFoundError(name)
                return None

        for module in [m for m in sys.modules if m.startswith("playwright")]:
            monkeypatch.delitem(sys.modules, module, raising=False)
        monkeypatch.setattr(sys, "meta_path", [Blocker(), *sys.meta_path])
        importlib.invalidate_caches()

        executor = ScenarioExecutor(
            tmp_path, ScenarioRunConfig(browser_enabled=True), tmp_path / "art"
        )
        scenario = Scenario(
            name="ui", mode="browser", app_url="http://127.0.0.1:1", browser={"steps": []}
        )
        result = asyncio.run(executor.execute(scenario))

        assert not result.passed
        assert result.browser is not None and not result.browser.page_loaded

    def test_a_never_observed_browser_scenario_is_blocked_not_failed(self):
        """BLOCKED is the honest verdict — it did not disagree with the
        product, it never reached it — and it blocks acceptance either way."""
        entry = SuiteEntry(
            scenario_id="gen-ui",
            scenario=Scenario(name="gen-ui"),
            origin=Origin.GENERATED,
            priority=Priority.P0,
        )
        result = ScenarioResult(scenario_name="gen-ui", mode="browser")
        result.browser = BrowserObservation(steps=["ERROR: net::ERR_CONNECTION_REFUSED"])
        ScenarioExecutor._assert_browser_text(result, result.browser)

        outcome = SuiteExecutor._outcome(entry, result, Path("/tmp/x"), 0.0)
        assert outcome.outcome is Outcome.BLOCKED

    def test_a_loaded_page_does_not_earn_a_synthetic_assertion(self):
        result = ScenarioResult(scenario_name="s")
        ScenarioExecutor._assert_browser_text(
            result, BrowserObservation(page_loaded=True, visible_text="hello")
        )
        assert result.assertions == []

    def test_cmd_run_preflights_the_browser_it_just_enabled(self):
        source = inspect.getsource(driver_cli.cmd_run)
        assert "_check_chromium" in source

    @pytest.mark.e2e
    def test_a_session_level_navigation_failure_is_recorded_structurally(self, tmp_path):
        """The restart-recovery shape, against a real chromium: the product is
        not there, the session-level ``page.goto`` raises, and the failure used
        to land only in the narration channel nothing scores. Zero assertions,
        ``all([])`` is True, PASSED, evidence verified, gate VERIFIED."""
        executor = ScenarioExecutor(
            tmp_path,
            ScenarioRunConfig(browser_enabled=True, capture_trace=False),
            tmp_path / "art",
        )
        scenario = Scenario(
            name="ui",
            mode="browser",
            app_url="http://127.0.0.1:1",
            browser={"steps": [{"expect_text": "invoices"}]},
        )
        result = asyncio.run(executor.execute(scenario))

        assert result.browser is not None
        assert not result.browser.page_loaded
        assert result.browser.step_failures, "the degraded exit must be structural"
        assert not result.passed


# --------------------------------------------------------------------------
# F-3 — page text was replaced rather than accumulated
# --------------------------------------------------------------------------


class TestEveryScreenTheRunLookedAtIsSearchable:
    def test_the_haystack_holds_every_screen_not_only_the_last(self):
        result = ScenarioResult(scenario_name="s")
        result.browser = BrowserObservation(
            page_loaded=True,
            visible_text="nothing to see",
            observed_texts=["Traceback (most recent call last):", "nothing to see"],
        )
        haystack = ScenarioExecutor._observed_text(result)
        assert "Traceback (most recent call last):" in haystack

    def test_merging_two_browser_actions_keeps_both_pages(self):
        result = ScenarioResult(scenario_name="s")
        executor = object.__new__(ScenarioExecutor)
        ScenarioExecutor._merge_browser(
            executor,
            result,
            BrowserObservation(page_loaded=True, visible_text="a", observed_texts=["a"]),
        )
        ScenarioExecutor._merge_browser(
            executor,
            result,
            BrowserObservation(page_loaded=True, visible_text="b", observed_texts=["b"]),
        )
        assert result.browser.observed_texts == ["a", "b"]
        assert result.browser.visible_text == "b"

    def test_a_single_session_that_navigates_twice_records_both_pages(self):
        """The shipped template's exact shape: one browser action, two gotos.
        The first screen's text was discarded before anything could assert on
        it, so a traceback on the landing page was structurally invisible."""

        class StubPage:
            def __init__(self) -> None:
                self.url = ""

            async def goto(self, url, **_kw):
                self.url = url

            async def evaluate(self, _script):
                return "Traceback (most recent call last):" if "boom" in self.url else "quiet"

        page = StubPage()
        obs = BrowserObservation()
        executor = object.__new__(ScenarioExecutor)
        for index, target in enumerate(("/boom", "/quiet"), start=1):
            asyncio.run(
                ScenarioExecutor._run_step(
                    executor,
                    page,
                    BrowserStep(goto=target),
                    obs,
                    Path("."),
                    index,
                    "http://127.0.0.1:8931",
                    "",
                )
            )

        assert obs.observed_texts == ["Traceback (most recent call last):", "quiet"]
        assert obs.page_loaded

        result = ScenarioResult(scenario_name="s")
        result.browser = obs
        assert "Traceback" in ScenarioExecutor._observed_text(result)

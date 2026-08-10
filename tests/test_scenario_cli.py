"""The scenarios CLI surface, and the one path into the permanent suite.

Promotion is the only way a generated scenario becomes a repository file. These
tests pin that it shows the YAML, asks first, refuses to overwrite, re-checks
safety at promotion time, and cannot be reached by a run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from neyma_product_driver.cli import (
    build_parser,
    cmd_scenarios_promote,
    cmd_scenarios_promotion_candidates,
)
from neyma_product_driver.config import DriverConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.scenario_planner import PromotionCandidate, PromotionLedger
from neyma_product_driver.scenarios import load_scenario

from scenario_fixtures import base_scenario, make_scenario


@pytest.fixture
def promotable(driver_config: DriverConfig, monkeypatch):
    """A run holding one promotion candidate, and a scenarios/ directory."""
    assert driver_config.runs_dir is not None and driver_config.scenarios_dir is not None
    driver_config.scenarios_dir.mkdir(parents=True, exist_ok=True)
    # The base scenario is what supplies the approved commands and the service.
    base = base_scenario()
    (driver_config.scenarios_dir / "backend_generic.yaml").write_text(
        json.dumps(base.model_dump(mode="json", exclude_defaults=True))
    )

    store = EvidenceStore(driver_config.runs_dir, "20260809-cli")
    model = make_scenario("gen-approve-twice")
    PromotionLedger(store.run_dir).record(
        PromotionCandidate(
            scenario_id=model.id,
            title=model.title,
            risk_category=model.risk_category.value,
            priority=model.priority.value,
            bug_discovered="payments=2 — the invoice was paid twice",
            discovered_in_iteration=1,
            fixed_in_iteration=2,
            evidence_path=str(store.iteration_dir(2)),
            requirement_reference=model.requirement_reference,
            scenario=model.model_dump(mode="json"),
        )
    )

    monkeypatch.setattr(
        "neyma_product_driver.cli._config_from_args", lambda _args: driver_config
    )
    return driver_config, store, model


def _args(**kw) -> argparse.Namespace:
    defaults = {
        "config": None,
        "repo": None,
        "run": "20260809-cli",
        "scenario": "backend_generic",
        "scenario_id": "gen-approve-twice",
        "yes": False,
        "as_json": False,
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestCommandSurface:
    @pytest.mark.parametrize(
        "argv",
        [
            ["run", "--task", "x", "--auto-scenarios"],
            ["scenarios", "plan", "--task", "x"],
            ["scenarios", "plan", "--task", "x", "--json"],
            ["scenarios", "run-generated", "--run", "r1"],
            ["scenarios", "promotion-candidates", "--run", "r1"],
            ["scenarios", "promote", "--run", "r1", "--scenario-id", "gen-1"],
        ],
    )
    def test_the_documented_invocations_parse(self, argv):
        assert build_parser().parse_args(argv).func is not None

    def test_the_existing_explicit_scenario_flag_is_unchanged(self):
        args = build_parser().parse_args(["run", "--scenario", "foo", "--task", "x"])
        assert args.scenario == "foo"
        assert args.auto_scenarios is False

    def test_both_flags_together_are_accepted(self):
        args = build_parser().parse_args(
            ["run", "--scenario", "foo", "--task", "x", "--auto-scenarios"]
        )
        assert args.scenario == "foo"
        assert args.auto_scenarios is True


class TestPromotionCandidatesCommand:
    @pytest.mark.asyncio
    async def test_candidates_are_listed_as_suggestions(self, promotable, capsys):
        await cmd_scenarios_promotion_candidates(_args())

        printed = capsys.readouterr().out
        assert "gen-approve-twice" in printed
        assert "paid twice" in printed
        assert "candidate only" in printed
        assert "Nothing has been added to the permanent regression" in printed

    @pytest.mark.asyncio
    async def test_json_output_is_machine_readable(self, promotable, capsys):
        await cmd_scenarios_promotion_candidates(_args(as_json=True))

        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["scenario_id"] == "gen-approve-twice"
        assert payload[0]["promoted"] is False


class TestPromotion:
    @pytest.mark.asyncio
    async def test_promotion_without_confirmation_writes_nothing(
        self, promotable, monkeypatch, capsys
    ):
        config, _store, _model = promotable
        before = sorted(p.name for p in config.scenarios_dir.iterdir())
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *_a: "no")

        code = await cmd_scenarios_promote(_args())

        assert code == 0
        assert "Aborted" in capsys.readouterr().out
        assert sorted(p.name for p in config.scenarios_dir.iterdir()) == before

    @pytest.mark.asyncio
    async def test_promotion_is_refused_non_interactively_without_yes(
        self, promotable, monkeypatch
    ):
        config, _store, _model = promotable
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        code = await cmd_scenarios_promote(_args())

        assert code == 3
        assert [p.name for p in config.scenarios_dir.iterdir()] == ["backend_generic.yaml"]

    @pytest.mark.asyncio
    async def test_promotion_with_yes_writes_a_reviewable_scenario_file(
        self, promotable, capsys
    ):
        config, store, model = promotable

        code = await cmd_scenarios_promote(_args(yes=True))

        assert code == 0
        destination = config.scenarios_dir / f"{model.id}.yaml"
        assert destination.exists()

        # It is a real, loadable scenario, and it carries why it exists.
        promoted = load_scenario(destination)
        assert promoted.name == model.id
        assert "Promoted from a generated scenario" in promoted.description
        assert model.requirement_reference in promoted.description
        assert promoted.steps, "the promoted scenario keeps its ordered steps"

        # The YAML was shown before it was written.
        printed = capsys.readouterr().out
        assert "PROPOSED ADDITION TO THE PERMANENT REGRESSION SUITE" in printed
        assert "Read it before" in printed

        # And the ledger records that it is no longer merely a candidate.
        assert PromotionLedger(store.run_dir).load()[0].promoted is True

    @pytest.mark.asyncio
    async def test_promoting_twice_is_a_no_op(self, promotable):
        await cmd_scenarios_promote(_args(yes=True))
        code = await cmd_scenarios_promote(_args(yes=True))
        assert code == 0

    @pytest.mark.asyncio
    async def test_an_existing_scenario_file_is_never_overwritten(self, promotable):
        config, _store, model = promotable
        existing = config.scenarios_dir / f"{model.id}.yaml"
        existing.write_text("name: something_a_human_wrote\n")

        code = await cmd_scenarios_promote(_args(yes=True))

        assert code == 3
        assert existing.read_text() == "name: something_a_human_wrote\n"

    @pytest.mark.asyncio
    async def test_safety_is_rechecked_at_promotion_time(self, promotable, capsys):
        """A command approved during the run may not be approved any more.

        Promotion makes a scenario permanent, so it re-derives the approved set
        from the repository as it is *now* rather than trusting the run.
        """
        config, store, model = promotable
        # The scenario file that approved those commands is gone.
        (config.scenarios_dir / "backend_generic.yaml").unlink()

        code = await cmd_scenarios_promote(_args(yes=True))

        assert code in (2, 3)
        assert not (config.scenarios_dir / f"{model.id}.yaml").exists()

    @pytest.mark.asyncio
    async def test_an_unknown_candidate_is_refused(self, promotable):
        code = await cmd_scenarios_promote(_args(scenario_id="does-not-exist", yes=True))
        assert code == 2

"""Is the coherence fixture still real? Asked of a machine, and only on request.

``tests/test_generated_scenario_coherence.py`` runs from a checked-in fixture
so the default suite depends on nothing outside this repository. The fixture is
a reconstruction, and a reconstruction can drift from what it reconstructs, so
two questions still need asking somewhere:

* does the fixture still agree with the run directory it was taken from; and
* does each recorded oracle output still match the live oracle in the product
  checkout?

Both answers depend on artifacts that are git-ignored or live outside this
repository — ``runs/20260905-230030`` and the product checkout — which is
exactly why they are not in the default suite: their presence, and their state
after any later resume, varies by machine. Every test here is marked
``local_artifacts`` and is skipped unless the suite is run with
``--local-artifacts`` (or ``NPD_LOCAL_ARTIFACTS=1``). Asked for and missing, a
source is a failure, not a skip: an explicit request to check something that
is not there should not come back green.

The product checkout is found at ``$NPD_PRODUCT_REPO`` or, failing that, beside
this repository as ``freight-logistics-operational-teammate``. Its oracles are
run read-only in their own temporary databases; nothing here writes to it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from neyma_product_driver.scenario_validation import _norm_command

from test_generated_scenario_coherence import (
    FIXTURE,
    MISCITED_ID,
    RECORDING,
    STALE_ID,
    fixture,
    oracle_command,
)

pytestmark = pytest.mark.local_artifacts

DRIVER_ROOT = Path(__file__).resolve().parents[1]
RUN = DRIVER_ROOT / "runs" / "20260905-230030"


def _product_repo() -> Path:
    configured = os.environ.get("NPD_PRODUCT_REPO", "").strip()
    return Path(configured) if configured else DRIVER_ROOT.parent / "freight-logistics-operational-teammate"


def _require(path: Path, what: str) -> Path:
    if not path.exists():
        pytest.fail(f"--local-artifacts was requested and {what} is not present at {path}")
    return path


def _executed_record(prefix: str) -> dict:
    scenarios = _require(RUN / "iteration-02" / "scenarios", "run 20260905-230030")
    [directory] = [p for p in scenarios.iterdir() if p.name.startswith(prefix)]
    return json.loads((directory / "result.json").read_text(encoding="utf-8"))


class TestTheFixtureMatchesTheRunItCameFrom:
    def test_the_untouched_scenarios_are_copied_verbatim(self) -> None:
        plan = json.loads(
            _require(RUN / "scenario-plan.json", "run 20260905-230030").read_text(encoding="utf-8")
        )
        persisted = {s["id"]: s for s in plan["scenarios"]}
        for scenario in fixture()["scenarios"]:
            if scenario["id"] in (STALE_ID, MISCITED_ID):
                continue
            assert scenario == persisted[scenario["id"]], scenario["id"]

    @pytest.mark.parametrize("sid", [STALE_ID, MISCITED_ID])
    def test_the_execution_record_is_the_runs_own(self, sid: str) -> None:
        recorded = _executed_record(f"{sid}-")
        mine = fixture()["executed"][sid]
        assert mine["assertions"] == recorded["assertions"]
        assert [c["stdout"] for c in mine["commands"]] == [
            c["stdout"] for c in recorded["commands"]
        ]

    def test_each_reconstructed_expectation_is_what_iteration_two_ran(self) -> None:
        recorded = _executed_record(f"{STALE_ID}-")
        scenario = next(s for s in fixture()["scenarios"] if s["id"] == STALE_ID)
        check = scenario["persisted_state_checks"][0]
        ran = [
            a["target"].split(": contains '", 1)[1][:-1]
            for a in recorded["assertions"]
            if a["kind"] == "expect_state" and ": contains '" in a["target"]
        ]
        assert check["contains"] == ran

    def test_the_miscited_command_is_the_one_generation_cited(self) -> None:
        wave = json.loads(
            _require(
                RUN / "scenario-generation" / "wave-03.json", "run 20260905-230030"
            ).read_text(encoding="utf-8")
        )
        scenario = next(s for s in fixture()["scenarios"] if s["id"] == MISCITED_ID)
        binding = next(
            b for b in scenario["command_bindings"] if b["field"] == "persisted_state_checks[0].command"
        )
        assert any(
            line.startswith(f"{MISCITED_ID}: @{binding['command_digest']} ->")
            for line in wave["resolved_citations"]
        )

    def test_the_fixture_is_checked_in(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(FIXTURE.relative_to(DRIVER_ROOT))],
            cwd=DRIVER_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert tracked.returncode == 0, "the default suite's fixture must not itself be local"


class TestTheRecordingIsReal:
    @pytest.mark.parametrize("name", sorted(RECORDING))
    def test_every_recording_still_matches_the_live_oracle(self, name: str) -> None:
        product = _require(_product_repo(), "the product checkout")
        proc = subprocess.run(
            oracle_command(name),
            shell=True,
            cwd=str(product),
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert proc.stdout == RECORDING[name], "the recording has become fiction"

    def test_the_fixture_ran_the_oracle_it_says_it_ran(self) -> None:
        from test_generated_scenario_coherence import NO_DELETE_ORACLE

        commands = fixture()["executed"][MISCITED_ID]["commands"]
        assert any(
            _norm_command(c["command"]) == _norm_command(oracle_command(NO_DELETE_ORACLE))
            for c in commands
        )

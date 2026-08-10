"""Does LLMScenarioReasoner.propose() work when called the way the driver calls it?

`run_control_loop` is `async` (cli.py:195). It calls planner.plan_initial (272),
refine_for_diff (315) and expand_after_failures (638) synchronously from inside
that coroutine, so `LLMScenarioReasoner.propose` runs with an event loop already
running in this thread.

The real network session is stubbed out so this test costs nothing and isolates
exactly one question: does the sync-over-async bridge in `propose` survive being
called from a running loop?
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from neyma_product_driver.scenario_generator import (  # noqa: E402
    GenerationBrief,
    LLMScenarioReasoner,
)
from neyma_product_driver.scenario_plan import GenerationBasis  # noqa: E402

SENTINEL = {"risks": [], "scenarios": [{"id": "x"}]}


class StubbedReasoner(LLMScenarioReasoner):
    """Identical to the real class except the Claude session is replaced."""

    async def _session(self, prompt: str):
        await asyncio.sleep(0)
        return SENTINEL


BRIEF = GenerationBrief(
    stage="adaptive",
    wave=2,
    basis=GenerationBasis(task="t"),
    max_scenarios=4,
    available_commands=[],
    available_services=[],
    app_url="",
    browser_enabled=False,
)


def sync_call() -> object:
    return StubbedReasoner(ROOT).propose(BRIEF)


async def async_call() -> object:
    """Exactly how run_control_loop reaches the reasoner: from a running loop."""
    return StubbedReasoner(ROOT).propose(BRIEF)


def main() -> int:
    print("A. propose() from a SYNC context (the `plan` subcommand path):")
    print(f"   -> {sync_call()!r}")

    print("B. propose() from a RUNNING event loop (the run_control_loop path):")
    result = asyncio.run(async_call())
    print(f"   -> {result!r}")

    print()
    if result == SENTINEL:
        print("RESULT: the async path works.")
        return 0
    print("RESULT: the async path returns None -- the real model is never reached.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""NEGATIVE CONTROL: wave 2 with the failure evidence removed.

Same task, same unit, same wave-1 coverage, same diff -- but no prior failures
and no clusters, only a neutral evaluator request. If the three evidence-driven
wave 2s look like this one, adaptive generation is decoration.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import TASK, WAVE1_PAYLOAD, ROOT, Unit, make_planner  # noqa: E402
from neyma_product_driver.scenario_generator import LLMScenarioReasoner  # noqa: E402


class Reasoner:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.calls = 0
        self.session_id = ""
        self._llm = LLMScenarioReasoner(ROOT, model="opus", timeout_s=900)

    def propose(self, brief):
        self.calls += 1
        (self.out / f"brief-wave{self.calls}.txt").write_text(brief.render())
        if self.calls == 1:
            return WAVE1_PAYLOAD
        payload = self._llm.propose(brief)
        (self.out / f"wave{self.calls}-raw-payload.json").write_text(
            json.dumps(payload, indent=2) if payload is not None else "null"
        )
        return payload


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    reasoner = Reasoner(out)
    planner = make_planner(reasoner, "none", 8999, out / "store.json", out)
    planner.plan_initial(task=TASK, unit=Unit(), run_id="r5-control")
    before = {s.id for s in planner.plan.scenarios}
    planner.expand_after_failures(
        task=TASK,
        unit=Unit(),
        failures=[],
        clusters=[],
        investigation_findings=[],
        evaluator_requests=["Please verify the approval endpoint more thoroughly."],
        diff_files=([] if "--no-diff" in sys.argv else ["r5/fixture/app.py"]),
    )
    new = [s for s in planner.plan.scenarios if s.id not in before]
    (out / "wave2-scenarios.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in new], indent=2)
    )
    for s in new:
        print(f"  [{s.priority.value} {s.risk_category.value}] {s.id}: {s.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

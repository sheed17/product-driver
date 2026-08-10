"""Re-run r2's generation-quality campaign against a nominated driver checkout.

This is r2's `run_generation.py` with two changes and no others:

  * the driver root is a parameter (`--driver`), so the campaign can be pinned to
    an exact commit while remediation work proceeds elsewhere;
  * the output directory is a parameter (`--out`).

The five representative tasks, the extra approved commands, and the base
scenario construction are **copied verbatim** from
`verification-evidence/r2-generation-quality/run_generation.py`. Using r2's own
fixtures is the point: a friendlier benchmark would not be comparable.

Usage:  python run_generation.py <task-key> [--diff] --driver <path> --out <path>
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

# --------------------------------------------------------------------------
# Approved commands supplied the way a human operator would: read-only probes
# that exist in the product repo, plus whatever the permanent scenario file
# already approves. Nothing here weakens a guard.  (verbatim from r2)
# --------------------------------------------------------------------------
EXTRA_APPROVED = [
    "sqlite3 data/active_workspace/driver_probe/workflow.sqlite3",
    ".venv/bin/python scripts/progress_status.py",
    ".venv/bin/python scripts/run_diagnostics.py",
    ".venv/bin/python -m pytest -c pytest-canonical.ini",
    ".venv/bin/python scripts/check_env.py",
]


def build_tasks():
    from neyma_product_driver.context import ActiveUnit

    tasks: dict[str, dict] = {
        "A": {
            "task": "Improve the exception detail screen so operators can understand the next action.",
            "unit": ActiveUnit(
                unit_id="U-UI-11",
                name="exception detail screen next-action clarity",
                status="READY",
                objective=(
                    "An operator opening an exception must be able to tell, without asking anyone, "
                    "what happened, what evidence supports it, and what the next action is."
                ),
                acceptance_contract="exception-detail-acceptance.md",
                acceptance_criteria=[
                    {"criterion": "the exception detail screen names the next action and who owns it", "weight": 3, "result": "PENDING"},
                    {"criterion": "the screen shows the evidence the exception was raised from", "weight": 2, "result": "PENDING"},
                    {"criterion": "the screen never shows a state the backend does not currently hold", "weight": 3, "result": "PENDING"},
                ],
                allowed_scope=["exception detail view", "exception read model"],
                prohibited_scope=["approval effects", "carrier payment"],
            ),
            "diff_files": [
                "src/freight_recon/exception_detail_view.py",
                "src/freight_recon/templates/exception_detail.html",
            ],
            "diff_stat": (
                " src/freight_recon/exception_detail_view.py       | 74 +++++++++---\n"
                " src/freight_recon/templates/exception_detail.html | 41 +++++--\n"
                " 2 files changed, 96 insertions(+), 19 deletions(-)"
            ),
        },
        "B": {
            "task": "Add supervised carrier invoice approval.",
            "unit": ActiveUnit(
                unit_id="U-042",
                name="supervised carrier invoice approval",
                status="READY",
                objective=(
                    "A carrier invoice may only move money after a human with authority approves it, "
                    "and an approved invoice is paid exactly once."
                ),
                acceptance_contract="governed-approval-acceptance.md",
                acceptance_criteria=[
                    {"criterion": "an approved invoice is paid exactly once", "weight": 3, "result": "PENDING"},
                    {"criterion": "no payment effect occurs without a recorded approval", "weight": 3, "result": "PENDING"},
                    {"criterion": "an operator without approval authority is refused", "weight": 2, "result": "PENDING"},
                    {"criterion": "approval survives a restart", "weight": 2, "result": "PENDING"},
                    {"criterion": "an invoice belonging to another tenant is never visible or approvable", "weight": 3, "result": "PENDING"},
                ],
                allowed_scope=["governed approval", "carrier invoice write path", "approval audit"],
                prohibited_scope=["entity resolution", "provenance"],
            ),
            "diff_files": [
                "src/freight_recon/governed_approval.py",
                "src/freight_recon/carrier_invoice_write.py",
                "src/freight_recon/action_callback.py",
            ],
            "diff_stat": (
                " src/freight_recon/governed_approval.py     | 210 ++++++++++++++++--\n"
                " src/freight_recon/carrier_invoice_write.py | 118 ++++++++--\n"
                " src/freight_recon/action_callback.py       |  36 +++-\n"
                " 3 files changed, 330 insertions(+), 34 deletions(-)"
            ),
        },
        "C": {
            "task": "Add a read-only search/filter view over shipments. It performs no writes.",
            "unit": ActiveUnit(
                unit_id="U-RO-3",
                name="read-only shipment search and filter view",
                status="READY",
                objective=(
                    "An operator can find a shipment by reference, carrier or status. The view reads; "
                    "it changes nothing."
                ),
                acceptance_contract="shipment-search-acceptance.md",
                acceptance_criteria=[
                    {"criterion": "a search by reference returns the matching shipment", "weight": 3, "result": "PENDING"},
                    {"criterion": "a filter with no matches says so rather than showing nothing", "weight": 2, "result": "PENDING"},
                    {"criterion": "the view performs no write of any kind", "weight": 3, "result": "PENDING"},
                ],
                allowed_scope=["shipment search read model", "search view"],
                prohibited_scope=["any write path", "approval", "payment"],
            ),
            "diff_files": [
                "src/freight_recon/shipment_search.py",
                "src/freight_recon/templates/shipment_search.html",
            ],
            "diff_stat": (
                " src/freight_recon/shipment_search.py       | 132 ++++++++++++\n"
                " src/freight_recon/templates/shipment_search.html |  58 ++++++\n"
                " 2 files changed, 190 insertions(+)"
            ),
        },
        "D": {
            "task": (
                "Move workflow state from the in-process cache to the durable workflow store, "
                "so state survives a process restart."
            ),
            "unit": ActiveUnit(
                unit_id="U-P5-1",
                name="durable workflow state store",
                status="READY",
                objective=(
                    "Workflow state is rebuilt from durable storage after a restart, and a write that "
                    "fails part-way leaves no half-applied workflow."
                ),
                acceptance_contract="event-and-replay-acceptance.md",
                acceptance_criteria=[
                    {"criterion": "workflow state survives a process restart", "weight": 3, "result": "PENDING"},
                    {"criterion": "a failed write leaves no partially applied workflow", "weight": 3, "result": "PENDING"},
                    {"criterion": "a read after a write never returns stale state", "weight": 2, "result": "PENDING"},
                    {"criterion": "rebuilding state from history never re-executes an effect", "weight": 3, "result": "PENDING"},
                ],
                allowed_scope=["workflow store", "outbox", "replay sandbox"],
                prohibited_scope=["entities", "provenance"],
            ),
            "diff_files": [
                "src/freight_recon/workflow_store.py",
                "src/freight_recon/outbox.py",
                "src/freight_recon/schema.py",
            ],
            "diff_stat": (
                " src/freight_recon/workflow_store.py | 284 +++++++++++++++++++++--\n"
                " src/freight_recon/outbox.py         | 141 ++++++++++++\n"
                " src/freight_recon/schema.py         |  47 +++-\n"
                " 3 files changed, 445 insertions(+), 27 deletions(-)"
            ),
        },
        "E": {
            "task": (
                "Add role-based authorization to the operator console so only a supervisor can "
                "release an operation."
            ),
            "unit": ActiveUnit(
                unit_id="U-AUTHZ-2",
                name="supervisor-only operation release",
                status="READY",
                objective=(
                    "Releasing an operation requires the supervisor role, and the boundary holds "
                    "across tenants."
                ),
                acceptance_contract="authorization-acceptance.md",
                acceptance_criteria=[
                    {"criterion": "a supervisor can release an operation", "weight": 3, "result": "PENDING"},
                    {"criterion": "a non-supervisor is refused and no release occurs", "weight": 3, "result": "PENDING"},
                    {"criterion": "a supervisor of one tenant cannot release another tenant's operation", "weight": 3, "result": "PENDING"},
                    {"criterion": "every refusal is recorded in the audit log", "weight": 2, "result": "PENDING"},
                ],
                allowed_scope=["authorization", "operator console release path", "audit log"],
                prohibited_scope=["payment adapters"],
            ),
            "diff_files": [
                "src/freight_recon/authorization.py",
                "src/freight_recon/operator_console.py",
                "src/freight_recon/activity_log.py",
            ],
            "diff_stat": (
                " src/freight_recon/authorization.py     | 96 ++++++++++++\n"
                " src/freight_recon/operator_console.py  | 58 ++++---\n"
                " src/freight_recon/activity_log.py      | 22 ++-\n"
                " 3 files changed, 148 insertions(+), 28 deletions(-)"
            ),
        },
        "A2": {
            "task": "Improve the exception detail screen so operators can understand the next action.",
            "unit": None,
            "diff_files": [
                "src/freight_recon/exception_detail_view.py",
                "src/freight_recon/templates/exception_detail.html",
                "src/freight_recon/workflow_store.py",
                "src/freight_recon/authorization.py",
                "src/freight_recon/schema.py",
            ],
            "diff_stat": (
                " src/freight_recon/exception_detail_view.py        |  74 ++++++--\n"
                " src/freight_recon/templates/exception_detail.html |  41 ++++-\n"
                " src/freight_recon/workflow_store.py               | 163 +++++++++++++--\n"
                " src/freight_recon/authorization.py                |  88 +++++++++\n"
                " src/freight_recon/schema.py                       |  31 +++-\n"
                " 5 files changed, 355 insertions(+), 42 deletions(-)"
            ),
        },
    }
    tasks["A2"]["unit"] = tasks["A"]["unit"]
    return tasks


def build_base(driver_root: Path):
    """The permanent scenario, augmented exactly as its own comments instruct."""
    from neyma_product_driver.scenarios import ServiceSpec, load_scenario

    base = load_scenario(driver_root / "scenarios" / "backend_generic.yaml")
    base.services = [
        ServiceSpec(
            name="callback",
            command=(
                ".venv/bin/python scripts/run_action_callback_server.py "
                "--host 127.0.0.1 --port 8001 "
                "--workspace data/active_workspace/driver_probe "
                "--db data/active_workspace/driver_probe/workflow.sqlite3"
            ),
        )
    ]
    base.readiness = [{"tcp": "127.0.0.1:8001"}]
    base.app_url = "http://127.0.0.1:8001"
    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    driver = Path(args.driver).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(driver))

    from neyma_product_driver.config import load_config
    from neyma_product_driver.context import load_founder_context
    from neyma_product_driver.scenario_generator import LLMScenarioReasoner
    from neyma_product_driver.scenario_planner import ScenarioPlanner

    spec = build_tasks()[args.key]

    config = load_config(driver / "driver.config.yaml")
    founder = load_founder_context(driver)
    base = build_base(driver)

    gen = config.scenario_generation
    gen.enabled = True
    gen.approved_commands = list(gen.approved_commands) + EXTRA_APPROVED

    reasoner = LLMScenarioReasoner(config.neyma_repo, model=gen.model)

    briefs: list[str] = []
    raws: list[dict | None] = []

    class Capturing:
        """Wraps the real reasoner; records the brief and the raw payload."""

        def __init__(self, inner):
            self.inner = inner

        @property
        def session_id(self):
            return getattr(self.inner, "session_id", "")

        def propose(self, brief):
            briefs.append(brief.render())
            payload = self.inner.propose(brief)
            raws.append(payload)
            return payload

    planner = ScenarioPlanner(
        repo=config.neyma_repo,
        config=gen,
        reasoner=Capturing(reasoner),
        store=None,
        base_scenario=base,
        permanent_scenarios=[base],
        founder=founder,
        emit=lambda m: print(m, flush=True),
    )

    tag = args.key + ("-diff" if args.diff else "")
    error = ""
    try:
        plan = planner.plan_initial(
            task=spec["task"], unit=spec["unit"], run_id=f"post-{tag}"
        )
        if args.diff:
            planner.refine_for_diff(
                task=spec["task"],
                unit=spec["unit"],
                diff_files=spec["diff_files"],
                diff_stat=spec["diff_stat"],
            )
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        plan = planner.plan

    for i, brief in enumerate(briefs, 1):
        (out / f"brief-{tag}-w{i}.txt").write_text(brief, encoding="utf-8")
    (out / f"raw-{tag}.json").write_text(
        json.dumps(raws, indent=2, default=str), encoding="utf-8"
    )
    (out / f"plan-{tag}.json").write_text(
        json.dumps(plan.model_dump(mode="json"), indent=2, default=str), encoding="utf-8"
    )
    (out / f"plan-{tag}.txt").write_text(plan.render(), encoding="utf-8")
    (out / f"compiled-{tag}.json").write_text(
        json.dumps(
            {sid: s.model_dump(mode="json") for sid, s in planner.compiled.items()},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    if error:
        (out / f"error-{tag}.txt").write_text(error, encoding="utf-8")
    print(
        f"\n=== {tag}: {len(plan.scenarios)} accepted, "
        f"{sum(len(w.rejected) for w in plan.waves)} refused ===",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

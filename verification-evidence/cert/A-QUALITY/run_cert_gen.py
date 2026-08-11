"""A-QUALITY certification harness for dynamic scenario generation.

Independent of, but comparable with, the prior campaign. It reuses the prior
campaign's base-scenario construction and extra approved commands verbatim (so
the numbers are comparable), reuses the five r2 task fixtures as INHERITED
tasks, and adds TWO tasks of this reviewer's own design (F and G) that no prior
campaign used.

What is different from the prior harness, deliberately:
  * `--rep N` so the same task can be run more than once and variance is visible;
  * per-wave timing, session id, and the wave's `reasoner_error` are recorded, so
    empty / failed / cut-off generator sessions are counted rather than folded
    into "produced nothing";
  * the raw payload is written even when the wave failed.

Nothing under neyma_product_driver/ or tests/ is touched. The Neyma repository is
read only by the reasoner, exactly as in a real run.

Usage:
  python run_cert_gen.py <task-key> [--diff] --driver <path> --out <dir> --rep <n>
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent.parent / "post-remediation" / "a-generation-quality" / "run_generation.py"


def _load_prior():
    spec = importlib.util.spec_from_file_location("prior_run_generation", PRIOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# NEW tasks — designed by reviewer A-QUALITY, never used by any prior campaign.
# Unlike the inherited fixtures, the diff files below are REAL modules that exist
# in the Neyma repository, so the generator can actually read what it is told
# changed.
# --------------------------------------------------------------------------
def new_tasks():
    from neyma_product_driver.context import ActiveUnit

    return {
        # --- F: dependency / partial failure -----------------------------
        "F": {
            "task": (
                "Make outbound delivery of an operator notification resilient when the "
                "transport partially fails. A send that times out after the message was "
                "actually posted must not produce a duplicate, and a send whose outcome "
                "cannot be confirmed must be recorded as unknown rather than delivered."
            ),
            "unit": ActiveUnit(
                unit_id="U-DEL-7",
                name="resilient notification delivery under partial transport failure",
                status="READY",
                objective=(
                    "An operator notification is delivered exactly once, and an outcome "
                    "the system cannot confirm is recorded as unknown rather than being "
                    "reported as success."
                ),
                acceptance_contract="delivery-resilience-acceptance.md",
                acceptance_criteria=[
                    {"criterion": "a confirmed send is recorded delivered exactly once", "weight": 3, "result": "PENDING"},
                    {"criterion": "a send whose outcome is unconfirmed is recorded as unknown, never as delivered", "weight": 3, "result": "PENDING"},
                    {"criterion": "a retry after an unknown outcome does not produce a second delivery", "weight": 3, "result": "PENDING"},
                    {"criterion": "a transport outage leaves no notification silently dropped", "weight": 2, "result": "PENDING"},
                ],
                allowed_scope=["delivery dispatch", "notification transport adapter", "delivery ledger"],
                prohibited_scope=["governed approval", "browser actuation", "carrier payment"],
            ),
            "diff_files": [
                "src/freight_recon/delivery.py",
                "src/freight_recon/delivery_dispatch.py",
                "src/freight_recon/alert_channel.py",
                "src/freight_recon/slack_adapter.py",
            ],
            "diff_stat": (
                " src/freight_recon/delivery.py          | 168 ++++++++++++++---\n"
                " src/freight_recon/delivery_dispatch.py |  94 ++++++++--\n"
                " src/freight_recon/alert_channel.py     |  37 ++++-\n"
                " src/freight_recon/slack_adapter.py     |  52 ++++--\n"
                " 4 files changed, 297 insertions(+), 54 deletions(-)"
            ),
        },
        # --- G: malformed / missing / conflicting evidence ----------------
        "G": {
            "task": (
                "Reconcile a carrier document against the shipment record when the "
                "extracted fields disagree with, or are missing from, the system of "
                "record. The operator must be able to tell a known fact from an inference."
            ),
            "unit": ActiveUnit(
                unit_id="U-EV-4",
                name="document evidence reconciliation under conflict",
                status="READY",
                objective=(
                    "A document whose fields conflict with the system of record produces a "
                    "stated conflict rather than a silent overwrite, and missing or "
                    "unparseable evidence produces a stated gap rather than a guess."
                ),
                acceptance_contract="evidence-reconciliation-acceptance.md",
                acceptance_criteria=[
                    {"criterion": "a document field that conflicts with the system of record is surfaced as a conflict and never silently overwrites it", "weight": 3, "result": "PENDING"},
                    {"criterion": "a document missing a required field yields a stated missing-evidence result rather than a guess", "weight": 3, "result": "PENDING"},
                    {"criterion": "an unparseable document is refused with a reason and changes no state", "weight": 3, "result": "PENDING"},
                    {"criterion": "an inference is never presented as a known fact", "weight": 2, "result": "PENDING"},
                ],
                allowed_scope=["document reconciliation", "extraction bridge", "evidence record"],
                prohibited_scope=["payment", "approval effects", "browser actuation"],
            ),
            "diff_files": [
                "src/freight_recon/reconciliation.py",
                "src/freight_recon/extraction.py",
                "src/freight_recon/document_identifier.py",
            ],
            "diff_stat": (
                " src/freight_recon/reconciliation.py      | 221 +++++++++++++++++---\n"
                " src/freight_recon/extraction.py          |  86 ++++++--\n"
                " src/freight_recon/document_identifier.py |  44 ++++-\n"
                " 3 files changed, 312 insertions(+), 39 deletions(-)"
            ),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rep", default="1")
    args = ap.parse_args()

    driver = Path(args.driver).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(driver))

    prior = _load_prior()

    from neyma_product_driver.config import load_config
    from neyma_product_driver.context import load_founder_context
    from neyma_product_driver.scenario_generator import LLMScenarioReasoner
    from neyma_product_driver.scenario_planner import ScenarioPlanner

    tasks = prior.build_tasks()
    tasks.update(new_tasks())
    spec = tasks[args.key]

    config = load_config(driver / "driver.config.yaml")
    founder = load_founder_context(driver)
    base = prior.build_base(driver)

    gen = config.scenario_generation
    gen.enabled = True
    gen.approved_commands = list(gen.approved_commands) + list(prior.EXTRA_APPROVED)

    reasoner = LLMScenarioReasoner(config.neyma_repo, model=gen.model)

    briefs: list[str] = []
    raws: list[object] = []
    telemetry: list[dict] = []

    class Capturing:
        def __init__(self, inner):
            self.inner = inner

        @property
        def session_id(self):
            return getattr(self.inner, "session_id", "")

        def propose(self, brief):
            briefs.append(brief.render())
            t0 = time.time()
            try:
                payload = self.inner.propose(brief)
            except BaseException as exc:
                telemetry.append(
                    {
                        "wave_index": len(briefs),
                        "seconds": round(time.time() - t0, 1),
                        "exception": f"{type(exc).__name__}: {exc}",
                        "session_id": str(getattr(self.inner, "session_id", "") or ""),
                        "payload_kind": "exception",
                        "n_scenarios": 0,
                    }
                )
                raws.append(None)
                raise
            n = (
                len(payload.get("scenarios") or [])
                if isinstance(payload, dict)
                else 0
            )
            telemetry.append(
                {
                    "wave_index": len(briefs),
                    "seconds": round(time.time() - t0, 1),
                    "exception": "",
                    "session_id": str(getattr(self.inner, "session_id", "") or ""),
                    "payload_kind": type(payload).__name__,
                    "n_scenarios": n,
                }
            )
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

    tag = f"{args.key}{'-diff' if args.diff else ''}-r{args.rep}"
    error = ""
    t_start = time.time()
    try:
        plan = planner.plan_initial(
            task=spec["task"], unit=spec["unit"], run_id=f"cert-{tag}"
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
    elapsed = round(time.time() - t_start, 1)

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
    (out / f"telemetry-{tag}.json").write_text(
        json.dumps(
            {
                "tag": tag,
                "key": args.key,
                "diff": bool(args.diff),
                "rep": args.rep,
                "elapsed_s": elapsed,
                "waves": telemetry,
                "wave_reasoner_errors": [
                    {"wave": w.wave, "error": w.reasoner_error} for w in plan.waves
                ],
                "generation_problems": planner.generation_problems(),
                "accepted": len(plan.scenarios),
                "rejected": sum(len(w.rejected) for w in plan.waves),
                "harness_exception": error,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    if error:
        (out / f"error-{tag}.txt").write_text(error, encoding="utf-8")
    print(
        f"\n=== {tag}: {len(plan.scenarios)} accepted, "
        f"{sum(len(w.rejected) for w in plan.waves)} refused, {elapsed}s ===",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

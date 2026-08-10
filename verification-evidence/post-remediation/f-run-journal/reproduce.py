"""Reproduce r1 F7: the run journal fails with AttributeError on any real run.

Run against any driver checkout:  python reproduce.py --driver <path> [--out f]

`_write_run_journal` iterates `record.commands` for every iteration in the run
state. `IterationRecord` has no `commands` field. The whole body is wrapped in
`except Exception`, so the failure is not loud — the journal is simply never
written, and the run continues as though it had been.

The reason 1029 tests miss it: a run state with **zero** iterations never enters
the loop body, so the attribute is never read. Any run that did any work does
read it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    driver = Path(args.driver).resolve()
    sys.path.insert(0, str(driver))

    from neyma_product_driver import cli as driver_cli
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.models import IterationRecord, RunState, RunStatus
    from neyma_product_driver.run_journal import JOURNAL_FILE, SUMMARY_FILE

    f: dict[str, object] = {}

    f["iteration_record_has_commands_field"] = "commands" in getattr(
        IterationRecord, "model_fields", {}
    )

    warnings: list[str] = []
    original_warn = driver_cli.warn
    driver_cli.warn = lambda m: warnings.append(str(m))  # type: ignore[assignment]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = EvidenceStore(root / "runs", run_id="journal-repro")

        state = RunState(
            run_id="journal-repro",
            task="probe the run journal",
            status=RunStatus.RUNNING,
        )
        # One ordinary iteration. Nothing exotic: this is what every run that
        # does any work at all produces.
        state.iterations.append(IterationRecord(iteration=1))

        class Config:
            neyma_repo = driver

        # Direct attribute read, outside the swallow, to name the failure.
        try:
            _ = state.iterations[0].commands  # type: ignore[attr-defined]
            f["direct_attribute_read"] = "succeeded"
        except AttributeError as exc:
            f["direct_attribute_read"] = f"AttributeError: {exc}"

        driver_cli._write_run_journal(store, state, Config())  # type: ignore[arg-type]

        journal_path = store.run_dir / JOURNAL_FILE
        summary_path = store.run_dir / SUMMARY_FILE
        f["journal_written"] = journal_path.exists()
        f["founder_summary_written"] = summary_path.exists()
        f["warnings"] = warnings

    driver_cli.warn = original_warn  # type: ignore[assignment]

    f["REPRODUCED"] = bool(
        not f["journal_written"]
        and any("AttributeError" in w for w in warnings)
    )
    f["SUMMARY"] = (
        "a run with one ordinary iteration writes no run journal and no founder "
        "summary; the AttributeError is swallowed into a warning"
        if f["REPRODUCED"]
        else "the run journal and founder summary are written for a run with iterations"
    )

    text = json.dumps(f, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

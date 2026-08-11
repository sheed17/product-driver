"""H-LOOP: run the ACTUAL Product Driver against a fresh disposable fixture.

  python run_live.py --driver <path> --work <dir> [--max-iterations 4] [--label runN]

Nothing is stubbed. This invokes `python -m neyma_product_driver run
--auto-scenarios` exactly as an operator would: live builder session, live
scenario generator, live scenario execution against the fixture's own HTTP
service, live evaluator, real acceptance gate.

The whole driver transcript is teed to `<work>/<label>/transcript.txt` so every
claim in FINDINGS.md can cite a line an independent reader can re-read.

Containment, asserted by make_fixture_authz.py at build time and re-asserted
here: no git remote, loopback bind on a free port chosen per run, no credential,
standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def make_config(fixture: Path, runs: Path, work: Path) -> Path:
    template = (HERE / "fixture.config.yaml").read_text()
    header = (
        f"neyma_repo: {fixture}\n"
        f"runs_dir: {runs}\n"
        f"scenarios_dir: {fixture / 'scenarios'}\n"
        f"preservation_dir: {work / 'preservation'}\n"
        f"temp_workspace_root: {work / 'tmp'}\n"
    )
    path = work / "driver.config.yaml"
    path.write_text(header + template, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--work", required=True, help="disposable scratch: the fixture repo lives here")
    ap.add_argument(
        "--out",
        default="",
        help="durable output root for run artifacts and the transcript; "
        "defaults to <this file's dir>/runs",
    )
    ap.add_argument("--label", default="run")
    ap.add_argument("--variant", choices=["base", "hard"], default="base")
    ap.add_argument("--max-iterations", type=int, default=4)
    args = ap.parse_args()

    driver = Path(args.driver).resolve()
    scratch = Path(args.work).resolve() / args.label
    scratch.mkdir(parents=True, exist_ok=True)
    # Run artifacts go somewhere that survives a scratch wipe; the fixture
    # itself does not, and must not be a git repository inside another one.
    work = (Path(args.out).resolve() if args.out else HERE / "runs") / args.label
    work.mkdir(parents=True, exist_ok=True)
    fixture = scratch / "fixture"
    runs = work / "runs"

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    print(f"[H-LOOP] fixture port: {port}", flush=True)

    subprocess.run(
        [sys.executable, str(HERE / "make_fixture_authz.py"),
         "--dest", str(fixture), "--port", str(port), "--variant", args.variant],
        check=True,
    )
    remotes = subprocess.run(
        ["git", "remote"], cwd=fixture, capture_output=True, text=True
    ).stdout.strip()
    assert remotes == "", f"containment: the fixture must have no remote, found {remotes!r}"

    config = make_config(fixture, runs, scratch)
    transcript = work / "transcript.txt"

    started = time.monotonic()
    with transcript.open("w", encoding="utf-8") as sink:
        proc = subprocess.Popen(
            [sys.executable, "-m", "neyma_product_driver", "run",
             "--config", str(config), "--auto-scenarios",
             "--max-iterations", str(args.max_iterations)],
            cwd=str(driver),
            env={**os.environ, "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            sink.write(line)
            sink.flush()  # a killed run must still leave the transcript it earned
        code = proc.wait()
    elapsed = time.monotonic() - started

    # The fixture lives in scratch and will not survive; keep what it became.
    (work / "builder-final-diff.txt").write_text(
        subprocess.run(
            ["git", "diff", "HEAD"], cwd=fixture, capture_output=True, text=True
        ).stdout
    )
    final = work / "fixture-final"
    if final.exists():
        import shutil as _shutil

        _shutil.rmtree(final)
    import shutil as _shutil

    _shutil.copytree(
        fixture, final, ignore=_shutil.ignore_patterns(".git", "__pycache__", "data")
    )

    run_dirs = sorted(runs.glob("*/"), key=lambda p: p.stat().st_mtime)
    meta = {
        "label": args.label,
        "driver_exit_code": code,
        "wall_s": round(elapsed, 1),
        "port": port,
        "fixture": str(fixture),
        "run_dir": str(run_dirs[-1]) if run_dirs else "",
        "fixture_has_no_remote": not subprocess.run(
            ["git", "remote"], cwd=fixture, capture_output=True, text=True
        ).stdout.strip(),
        "fixture_git_log": subprocess.run(
            ["git", "log", "--oneline", "-8"], cwd=fixture, capture_output=True, text=True
        ).stdout.strip(),
        "fixture_status_porcelain": subprocess.run(
            ["git", "status", "--porcelain"], cwd=fixture, capture_output=True, text=True
        ).stdout.strip(),
    }
    (work / "run-meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

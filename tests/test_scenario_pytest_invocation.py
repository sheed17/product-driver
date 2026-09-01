"""A regression anchor that ran nothing is not a regression anchor.

Run ``20260901-082602`` verified P6/M10 against a Neyma tree whose own product
measurements were strong — the M10 probe reached "behaviours as specified, 0
wrong", `test_phase6_compensation.py` went 63 passed, the mutation battery went
33/33 caught and 0 escaped — and still came back BLOCKED, on two failures that
were both defects in the SCENARIO rather than in the product:

    the checkpoint kernel and the claim CAS are unchanged by M10: exit == 0
        got exit=4
        ERROR: file or directory not found: eval/tests/test_phase3_checkpoint.py

    M1, M2, M3 and M4 stay green beside M10: exit == 0
        got exit=1
        12 failed, 420 passed
        ModuleNotFoundError: No module named 'eval'

Two failure modes, one shape: **a command that cannot answer the question it was
written to ask.**

1.  A PATH THAT DOES NOT EXIST. `eval/tests/test_phase3_checkpoint.py` has never
    been in the landed tree; the canonical suite is
    `test_phase3_checkpoint_matrix.py`. pytest answered `ERROR: file or directory
    not found` and exited 4, so M10's strongest anchor over the P3 kernel
    measured nothing at all — and would have gone on measuring nothing had the
    exit code been ignored. A missing path is worse than a missing check,
    because it looks like a check.

2.  THE `pytest` CONSOLE SCRIPT INSTEAD OF `python -m pytest`. These are not two
    spellings of one command. `python -m pytest` puts the invocation directory
    on `sys.path`; the console script does not. Neyma's `pyproject.toml` sets
    `pythonpath = ["src"]` and nothing more, so a test that reaches for the
    repository's own harness — `from eval.phase0 import import_probe`, which
    `test_phase6_pipeline_instance.py` uses to decide the M2 dark-surface guard
    — raises `ModuleNotFoundError` under one and passes under the other.

    The run reported that as "12 failures that appear only in the combined
    invocation", and the builder reported the same file passing alone, so the
    obvious reading was test-order pollution or a random seed. It was neither.
    `pytest-randomly` is not installed in that repository at all, and the same
    twelve fail with the file run ALONE under the console script. The
    interpreter's `sys.path`, not the ordering, was the whole story — and an
    invented ordering defect is expensive to chase and impossible to fix.

Every rule below is general: it sweeps EVERY scenario in `scenarios/`, names no
unit, and would have caught both defects the day they were written. Nothing here
runs Neyma's suites or consumes Claude usage; the one test that executes
anything builds its own two-file tree in `tmp_path` and proves the `sys.path`
difference from first principles, so the rule is grounded in the interpreter's
behaviour rather than in a preference about spelling.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from neyma_product_driver.scenarios import Scenario, load_scenario

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = DRIVER_ROOT / "scenarios"

#: The Neyma checkout the scenarios are written against. Read from the local
#: config when there is one; the path tests skip when there is not, because "the
#: repository is not here" is not the same finding as "the path is wrong".
def _neyma_repo() -> Path | None:
    local = DRIVER_ROOT / "driver.config.yaml"
    if not local.exists():
        return None
    import yaml

    raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    target = raw.get("neyma_repo")
    if not target:
        return None
    path = Path(str(target)).expanduser()
    return path if path.is_dir() else None


NEYMA = _neyma_repo()

#: A pytest invocation, in the position where it is the PROGRAM being run rather
#: than a word inside someone's `-c` payload or a filename.
_PYTEST_PROGRAM = re.compile(r"(?:^|[/\s])(?:py\.test|pytest)(?=\s|$)")
_DASH_M_PYTEST = re.compile(r"-m\s+pytest(?=\s|$)")
_TEST_PATH = re.compile(r"(?<![\w/.])((?:eval|tests|test)/[\w./-]*\.py)")


def _scenarios() -> list[tuple[Path, Scenario]]:
    return [(p, load_scenario(p)) for p in sorted(SCENARIO_DIR.glob("*.yaml"))]


def _labelled_commands(scenario: Scenario) -> list[tuple[str, str]]:
    """Every shell command a scenario can execute, with something to call it."""
    out: list[tuple[str, str]] = []
    out += [("setup", c) for c in scenario.setup]
    out += [("teardown", c) for c in scenario.teardown]
    out += [(c.name or c.run, c.run) for c in scenario.commands]
    out += [(c.name or c.command, c.command) for c in scenario.expect_state]
    for step in scenario.steps:
        if step.command is not None:
            out.append((step.command.name or step.command.run, step.command.run))
        if step.state_check is not None:
            out.append((step.state_check.name or step.state_check.command, step.state_check.command))
    return out


def _program(command: str) -> str:
    """The part of a command before any inline `-c` payload.

    A `python -c "...import pytest..."` oracle is not a pytest invocation, and
    the payloads in this corpus run to several thousand characters of quoted
    Python. Only the program and its arguments decide how the interpreter was
    entered.
    """
    normalized = " ".join(command.split())
    head, sep, _tail = normalized.partition(' -c "')
    return head if sep else normalized


ALL_COMMANDS = [
    (path, label, command)
    for path, scenario in _scenarios()
    for label, command in _labelled_commands(scenario)
]

PYTEST_COMMANDS = [
    (path, label, command)
    for path, label, command in ALL_COMMANDS
    if _PYTEST_PROGRAM.search(_program(command))
]


def test_the_corpus_is_actually_being_swept():
    """The negative control. Every rule below is an `all(...)` over these lists,
    and an `all(...)` over nothing is the exact vacuous green this file exists to
    forbid — so the population is asserted before any verdict is drawn on it."""
    assert len(_scenarios()) >= 8, "the scenario corpus did not load"
    assert len(ALL_COMMANDS) >= 100, f"only {len(ALL_COMMANDS)} command(s) swept"
    assert PYTEST_COMMANDS, "no scenario runs a pytest battery; the rules below would be empty"


# ==========================================================================
# 1 — how pytest is entered
# ==========================================================================


def test_the_console_script_and_dash_m_do_not_run_the_same_suite(tmp_path: Path):
    """The mechanism, proven rather than asserted, on a tree built here.

    `python -m pytest` prepends the invocation directory to `sys.path`; the
    console script does not. Nothing about this is specific to Neyma, to M10 or
    to a pytest version — it is what `-m` means — so the rule in the next test
    rests on a demonstration instead of on a style guide.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "suite").mkdir()
    (tmp_path / "suite" / "test_reaches_the_root.py").write_text(
        "def test_it():\n    from pkg import VALUE\n    assert VALUE == 1\n", encoding="utf-8"
    )
    target = "suite/test_reaches_the_root.py"

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv + ["-q", "-p", "no:cacheprovider", target],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
        )

    console = run([str(Path(sys.executable).with_name("pytest"))])
    dash_m = run([sys.executable, "-m", "pytest"])

    assert dash_m.returncode == 0, (
        "`python -m pytest` should reach a module in the invocation directory:\n" + dash_m.stdout
    )
    assert console.returncode != 0, (
        "the console script is expected NOT to reach it; if this ever stops being "
        "true the rule below can be relaxed, but only then:\n" + console.stdout
    )
    assert "ModuleNotFoundError" in console.stdout + console.stderr


@pytest.mark.parametrize(
    "path,label,command",
    [pytest.param(p, l, c, id=f"{p.stem}::{l[:60]}") for p, l, c in PYTEST_COMMANDS],
)
def test_no_scenario_invokes_the_bare_pytest_console_script(path: Path, label: str, command: str):
    """Every battery is entered as `python -m pytest`, so the repository root is
    importable and a test may use the harness the repository ships.

    `p6_m10_compensation.yaml` was the one file in this corpus that used the
    console script, and it is where the phantom "12 combined-only failures"
    came from.
    """
    program = _program(command)
    assert _DASH_M_PYTEST.search(program), (
        f"{path.name} runs pytest as the console script in {label!r}:\n"
        f"    {program[:200]}\n"
        "Use `.venv/bin/python -m pytest`. The console script leaves the "
        "invocation directory off sys.path, so a test that imports the "
        "repository's own harness fails for a reason that has nothing to do "
        "with the product."
    )


# ==========================================================================
# 2 — what a battery is pointed at
# ==========================================================================


@pytest.mark.skipif(NEYMA is None, reason="the Neyma repository is not available here")
@pytest.mark.parametrize(
    "path,label,command",
    [pytest.param(p, l, c, id=f"{p.stem}::{l[:60]}") for p, l, c in ALL_COMMANDS],
)
def test_every_test_path_a_scenario_names_exists_in_the_repository(
    path: Path, label: str, command: str
):
    """A path that is not there cannot fail for the reason it was written.

    `eval/tests/test_phase3_checkpoint.py` was named by M10's P3 regression
    anchor and has never existed; pytest exited 4 and the anchor measured
    nothing. This is the check that would have said so, in the scenario's own
    words, before the run started.
    """
    assert NEYMA is not None
    for match in _TEST_PATH.finditer(" ".join(command.split())):
        named = match.group(1)
        assert (NEYMA / named).exists(), (
            f"{path.name} names {named!r} in {label!r}, and no such file exists in "
            f"{NEYMA}. pytest answers a missing path with "
            "`ERROR: file or directory not found` and exit 4 — a check that cannot run."
        )


# ==========================================================================
# 3 — a battery has to prove it ran
# ==========================================================================
#
# NOT ENFORCED ACROSS THE CORPUS HERE, AND THE REASON IS RECORDED RATHER THAN
# LEFT TO BE REDISCOVERED. Exit 0 alone is not evidence that a suite ran: pytest
# exits 5 on an empty selection and 4 on a bad path. `p6_m10_compensation.yaml`
# now requires the summary word `passed` on every battery AND forbids
# `ERROR: file or directory not found`, `no tests ran` and the `eval` import
# error globally, so neither an empty nor a not-found selection can satisfy it.
#
# Forty batteries in the other eight scenario files still read only the exit
# code. That is a latent weakness of the same family, but they belong to units
# that have already landed and been accepted, and widening them is a change to
# accepted regression assets rather than a repair of this run — so it is
# reported, not performed. The M10 half of it is asserted where it belongs, in
# `tests/test_m10_readiness.py`.

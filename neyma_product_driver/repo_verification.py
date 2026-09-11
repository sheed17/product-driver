"""The repository's own verification for the surfaces a change put at risk.

A generated scenario proves that the thing the builder built does what the task
said. It cannot prove the thing nobody wrote a scenario for: that the *rest of
the repository's* own standing guards still hold about the surface this change
touched. Those guards already exist, the repository already runs them, and they
are the ones that turn red after the founder pushes.

That is the failure this module exists for. A change added two persisted tables,
every generated scenario passed, the independent review reproduced runtime
evidence, and the candidate was reported ready for the founder to push — and the
target repository's existing tenant-posture guard, which asserts that no new
persisted table appears outside its baseline manifest, failed in CI on the exact
tree. Nothing in the run had asked the repository what it already knew.

The design follows from three constraints:

* **it is discovered, never configured per product.** Nothing here knows the
  name of a table, a test, a phase or a repository. A surface is described by
  what it *is* — persisted schema, migration, storage — and the repository's own
  verification for it is found by reading the repository;
* **it invents no requirement.** Only tests the target repository already has
  are run. A repository with no standing guard for a surface produces no
  demand — it produces a recorded "nothing found", which is a fact about the
  repository and not a finding against the change;
* **it does not run everything.** Discovery is gated on the diff actually
  touching a surface, candidates must look like repository-WIDE guards rather
  than feature tests, and the number executed is capped. A driver that runs the
  whole suite on every task has not discovered anything; it has given up.

One distinction is kept sharp throughout: a guard that runs and FAILS is a
finding about the product; a guard that cannot be run at all — no runner, an
import error, a timeout — is a fact about this environment. The first blocks the
change. The second blocks the *claim* that the change was verified, which is a
different sentence and is printed as one.

Nothing here writes to the target repository, and nothing here pushes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .models import redact, utcnow

# --------------------------------------------------------------------------
# Surfaces
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceSpec:
    """One risk surface, described by what it is rather than by whose it is.

    ``paths`` decides whether a diff touched the surface. ``diff_markers`` is the
    second, stronger signal: a file that does not look like a migration but
    contains ``CREATE TABLE`` has changed persisted schema anyway. ``vocabulary``
    is what the repository's own guards for this surface talk about, and it is
    what discovery reads.
    """

    name: str
    paths: re.Pattern[str]
    diff_markers: re.Pattern[str]
    vocabulary: tuple[str, ...]


#: The surfaces this driver knows how to ask a repository about. Data, not code:
#: a new surface is a row here, and every row is product-agnostic.
SURFACES: tuple[SurfaceSpec, ...] = (
    SurfaceSpec(
        name="persisted schema, migration or storage",
        paths=re.compile(
            r"(?:^|/)(?:migrations?|migrate|alembic|schema|schemas|ddl|storage|"
            r"persistence|repositor(?:y|ies)|database|db)(?:/|_|\.|$)"
            r"|\.sql$"
            r"|(?:^|/)models?\.py$"
            r"|(?:schema|migration|table|storage|persist)\w*\.(?:py|sql|yaml|yml|json|ts|js|go|rb)$",
            re.I,
        ),
        diff_markers=re.compile(
            r"\bcreate\s+table\b|\balter\s+table\b|\badd\s+column\b|\bdrop\s+table\b|"
            r"\bcreate\s+index\b|\b__tablename__\b|\bop\.create_table\b|\bmigrat\w+\b",
            re.I,
        ),
        vocabulary=(
            "table",
            "schema",
            "migration",
            "column",
            "database",
            "persist",
            "storage",
            "index",
            "ddl",
        ),
    ),
)

#: What separates a repository-WIDE guard from a feature test. A wide guard
#: asserts over a set it discovers at runtime — every table, every migration,
#: whatever is not in the baseline — which is exactly the shape that notices a
#: surface the change added and nobody classified.
_WIDE_MARKERS: tuple[str, ...] = (
    "every ",
    "all ",
    "no new",
    "not in the",
    "baseline",
    "manifest",
    "inventory",
    "introspect",
    "registry",
    "ledger",
    "exempt",
    "unaccounted",
    "unknown",
    "declared",
    "posture",
    "invariant",
    "contract",
)

#: The same idea, spelled as it appears in a TEST NAME rather than in prose.
#: `test_no_new_tenantless_table_appeared` and
#: `test_every_business_table_is_registered` are the shape being looked for.
_WIDE_NAME_MARKERS: tuple[str, ...] = (
    "no_new",
    "no_",
    "every",
    "all_",
    "_all",
    "baseline",
    "manifest",
    "inventory",
    "declared",
    "exempt",
    "unknown",
    "unaccounted",
    "posture",
    "invariant",
    "contract",
    "registered",
    "canonical",
    "appeared",
)

#: Directories whose tests are not the repository's own.
_VENDOR = re.compile(
    r"(?:^|/)(?:\.git|\.venv|venv|node_modules|site-packages|dist|build|vendor|third_party)(?:/|$)"
)

_TEST_FILE = re.compile(r"(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$")


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


class VerificationTarget(BaseModel):
    """One piece of the repository's own verification, and why it was chosen."""

    model_config = ConfigDict(extra="ignore")

    path: str = ""
    command: str = ""
    surface: str = ""
    #: The repository's own words that made this look like a wide guard.
    why: list[str] = Field(default_factory=list)
    score: int = 0


class VerificationResult(BaseModel):
    """What running one of them produced."""

    model_config = ConfigDict(extra="ignore")

    target: VerificationTarget
    exit_code: int = -1
    passed: bool = False
    #: True when the guard could not be RUN — no runner, a collection error, a
    #: timeout. A fact about this environment, never a product finding.
    infrastructure: bool = False
    detail: str = ""
    duration_s: float = 0.0

    def brief(self) -> str:
        if self.passed:
            return f"{self.target.path}: PASS"
        kind = "COULD NOT RUN" if self.infrastructure else "FAIL"
        return f"{self.target.path}: {kind} — {self.detail[:200]}"


class RepositoryVerification(BaseModel):
    """The run's answer to "does the repository still hold about what changed?"."""

    model_config = ConfigDict(extra="ignore")

    #: Surfaces the diff actually touched. Empty means this did not apply, which
    #: is the ordinary case and is not a gap.
    surfaces: list[str] = Field(default_factory=list)
    #: Why each surface was considered touched.
    surface_evidence: list[str] = Field(default_factory=list)
    targets: list[VerificationTarget] = Field(default_factory=list)
    results: list[VerificationResult] = Field(default_factory=list)
    #: Said out loud when the repository has no standing guard for a touched
    #: surface. A fact about the repository, recorded rather than converted into
    #: a demand this driver would have invented.
    notes: list[str] = Field(default_factory=list)
    commit: str = ""
    ran_at: str = Field(default_factory=utcnow)

    @property
    def applicable(self) -> bool:
        return bool(self.surfaces)

    @property
    def product_failures(self) -> list[VerificationResult]:
        return [r for r in self.results if not r.passed and not r.infrastructure]

    @property
    def infrastructure_problems(self) -> list[VerificationResult]:
        return [r for r in self.results if r.infrastructure]

    @property
    def blocks_push(self) -> bool:
        """Whether push readiness may be declared on this evidence.

        A failing repository guard blocks because it is a defect. A guard that
        could not be executed blocks the CLAIM, not the change: the run cannot
        say the repository's own verification holds when it never got an answer.
        """
        return bool(self.product_failures or self.infrastructure_problems)

    @property
    def blocks_acceptance(self) -> bool:
        """Only a real failure refuses the product. Infrastructure never does."""
        return bool(self.product_failures)

    def headline(self) -> str:
        if not self.applicable:
            return "not applicable — this change touched no surface with a repository-wide guard"
        if not self.targets:
            return (
                f"{', '.join(self.surfaces)} changed, and the repository declares no "
                "repository-wide verification this driver could find for it"
            )
        if self.product_failures:
            first = self.product_failures[0]
            return (
                f"the repository's own verification fails on this tree: {first.target.path} "
                f"({first.detail[:160]})"
            )
        if self.infrastructure_problems:
            first = self.infrastructure_problems[0]
            return (
                f"the repository's own verification could not be executed here: "
                f"{first.target.path} ({first.detail[:160]})"
            )
        return (
            f"{len(self.results)} repository-wide guard(s) for "
            f"{', '.join(self.surfaces)} pass on this tree"
        )

    def summary_block(self) -> str:
        lines = [f"REPOSITORY VERIFICATION: {self.headline()}"]
        for evidence in self.surface_evidence[:4]:
            lines.append(f"  surface: {evidence}")
        for result in self.results:
            lines.append(f"  {result.brief()}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Surface detection
# --------------------------------------------------------------------------


def surfaces_touched(
    repo: Path,
    diff_files: Sequence[str],
    *,
    diff_text: str = "",
) -> tuple[list[SurfaceSpec], list[str]]:
    """Which risk surfaces this change touched, and the evidence for each.

    Read from the diff, never from the task description: a change described as a
    manifest edit that adds a table has changed persisted schema, and a change
    described as a migration that touches no persisted file has not.
    """
    files = [str(f).strip() for f in diff_files if str(f).strip()]
    hits: list[SurfaceSpec] = []
    evidence: list[str] = []
    for spec in SURFACES:
        by_path = [f for f in files if spec.paths.search(f)]
        by_content: list[str] = []
        if not by_path and diff_text:
            if spec.diff_markers.search(diff_text):
                by_content.append("the diff itself")
        if not by_path and not by_content:
            # Last resort: read the changed files. A file named nothing in
            # particular that declares a table is still persisted schema.
            for name in files[:60]:
                text = _read(repo / name)
                if text and spec.diff_markers.search(text):
                    by_content.append(name)
                    break
        if not by_path and not by_content:
            continue
        hits.append(spec)
        where = ", ".join((by_path + by_content)[:3])
        evidence.append(f"{spec.name} — changed: {where}")
    return hits, evidence


def run_changed_files(repo: Path, base_commit: str = "") -> list[str]:
    """Everything THIS RUN changed: committed since it started, plus the tree.

    The working tree alone is the wrong question here and answering it that way
    is silent. A builder that commits its work leaves a clean tree, so at the
    moment the run decides whether the change is ready to push, the surfaces the
    change touched are all inside commits — and a check that reads only
    ``git status`` sees nothing and passes.
    """
    files: list[str] = []
    repo = Path(repo)
    if base_commit:
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", f"{base_commit}..HEAD"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            files += [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        for line in proc.stdout.splitlines():
            path = line[2:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path:
                files.append(path.strip('"'))
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(dict.fromkeys(files))


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def _tracked_test_files(repo: Path) -> list[str]:
    """The repository's own test files, as the repository itself tracks them."""
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        names = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        names = []
    if not names:
        names = [
            str(p.relative_to(repo))
            for p in repo.rglob("*.py")
            if p.is_file()
        ][:5000]
    return [n for n in names if _TEST_FILE.search(n) and not _VENDOR.search(n)]


def _test_names(text: str) -> list[str]:
    """Every test this file declares, by name.

    Names are where a repository says what a test is ABOUT, and they are the
    cheapest honest signal available: `test_no_new_tenantless_table_appeared`
    announces a guard over a discovered set, and no amount of reading the body
    says it more clearly.
    """
    return re.findall(r"def\s+(test_\w+)", text or "")


def _guard_score(
    names: Sequence[str], spec: SurfaceSpec
) -> tuple[float, list[str], list[str]]:
    """How much of this file is a repository-wide guard for this surface.

    DENSITY, not count. A 66-test feature suite that happens to contain six
    schema assertions is a feature suite; a ten-test file where half the names
    are about the surface and two of them are about what must NOT appear in it
    is the repository's standing guard. Ranking on raw counts picks the first,
    which is how a bounded check turns into running the biggest file in the
    repository for no reason.
    """
    total = len(names) or 1
    surface_named = [n for n in names if any(v in n for v in spec.vocabulary)]
    wide_named = [
        n for n in surface_named if any(w.strip() in n for w in _WIDE_NAME_MARKERS)
    ]
    score = (len(surface_named) + 2 * len(wide_named)) / total
    return score, surface_named, wide_named


def discover_verification(
    repo: Path,
    surfaces: Sequence[SurfaceSpec],
    *,
    max_targets: int = 3,
    runner: str = "",
    min_density: float = 0.30,
) -> tuple[list[VerificationTarget], list[str]]:
    """The repository's own repository-wide guards for these surfaces.

    Conservative in one direction on purpose: a candidate must speak the
    surface's vocabulary, must declare at least one test about what must or must
    not appear ACROSS that surface, and must be mostly about it. A feature test
    for one table clears the first and not the rest, and is not run — this
    driver is not entitled to spend the founder's time running a repository's
    whole suite because a migration changed.
    """
    repo = Path(repo)
    notes: list[str] = []
    if not surfaces:
        return [], notes
    runner = runner or detect_test_runner(repo)
    if not runner:
        notes.append(
            "the repository declares no Python test runner this driver could identify, so "
            "its own verification could not be executed"
        )
        return [], notes

    candidates: list[VerificationTarget] = []
    for rel in _tracked_test_files(repo):
        text = _read(repo / rel)
        if not text:
            continue
        names = _test_names(text)
        if not names:
            continue
        blob = f"{rel}\n{text}".lower()
        for spec in surfaces:
            words = [w for w in spec.vocabulary if w in blob]
            if len(words) < 2:
                continue
            score, surface_named, wide_named = _guard_score(names, spec)
            if not wide_named or score < min_density:
                continue
            candidates.append(
                VerificationTarget(
                    path=rel,
                    # `-p no:cacheprovider` so reading the repository's answer
                    # never writes a cache directory into the tree being
                    # verified. A verification that dirties the tree changes the
                    # thing it was asked about.
                    command=f"{runner} {rel} -q -p no:cacheprovider",
                    surface=spec.name,
                    why=[
                        f"{len(surface_named)} of {len(names)} tests here are about "
                        f"{spec.name}",
                        "asserts across the surface rather than about one feature: "
                        + ", ".join(wide_named[:3]),
                    ],
                    score=int(round(score * 100)),
                )
            )
            break

    candidates.sort(key=lambda c: (-c.score, len(c.path), c.path))
    chosen = candidates[:max_targets]
    if not chosen:
        notes.append(
            "no repository-wide verification for "
            + ", ".join(s.name for s in surfaces)
            + " could be found in this repository's own tests"
        )
    elif len(candidates) > len(chosen):
        notes.append(
            f"{len(candidates)} candidate guard(s) matched; the {len(chosen)} strongest "
            "were executed. This is a bounded check, not a full suite run."
        )
    return chosen, notes


def detect_test_runner(repo: Path) -> str:
    """How this repository runs a test file. Read from the repository."""
    repo = Path(repo)
    for rel in (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe"):
        if (repo / rel).exists():
            return f"{rel} -m pytest"
    markers = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "conftest.py")
    if any((repo / m).exists() for m in markers):
        return f"{sys.executable} -m pytest"
    return ""


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

#: Collection and environment failures. A guard that never reached an assertion
#: has not made a statement about the product.
_INFRASTRUCTURE = re.compile(
    r"\b(?:no tests ran|error(?:s)? during collection|ImportError|ModuleNotFoundError|"
    r"INTERNALERROR|unrecognized arguments|command not found|No such file or directory|"
    r"file or directory not found|usage: pytest)\b",
    re.I,
)


def run_verification(
    repo: Path,
    targets: Sequence[VerificationTarget],
    *,
    timeout_s: int = 900,
    commit: str = "",
    notes: Sequence[str] = (),
    surfaces: Sequence[str] = (),
    surface_evidence: Sequence[str] = (),
) -> RepositoryVerification:
    """Execute the discovered verification. Read-only; never raises."""
    import os
    import shlex
    import time

    from .command_guard import classify_command

    # Reading a repository's answer must not leave anything in the tree being
    # read. `-p no:cacheprovider` handles pytest's own cache; this handles the
    # interpreter's bytecode, which would otherwise show up as untracked files
    # in a repository that does not ignore them — and an untracked file is
    # evidence the completion audit and the push-readiness check both read.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    record = RepositoryVerification(
        surfaces=list(surfaces),
        surface_evidence=list(surface_evidence),
        targets=list(targets),
        notes=list(notes),
        commit=commit,
    )
    for target in targets:
        refusal = classify_command(target.command)
        if refusal:
            record.results.append(
                VerificationResult(
                    target=target,
                    infrastructure=True,
                    detail=f"the command guard refuses this command: {refusal}",
                )
            )
            continue
        started = time.monotonic()
        try:
            proc = subprocess.run(
                shlex.split(target.command),
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                env=env,
            )
            output = f"{proc.stdout}\n{proc.stderr}".strip()
            tail = redact(_tail(output))
            infrastructure = proc.returncode != 0 and bool(_INFRASTRUCTURE.search(output))
            record.results.append(
                VerificationResult(
                    target=target,
                    exit_code=proc.returncode,
                    passed=proc.returncode == 0,
                    infrastructure=infrastructure,
                    detail=tail if proc.returncode != 0 else _tail(output, lines=2),
                    duration_s=round(time.monotonic() - started, 2),
                )
            )
        except subprocess.TimeoutExpired:
            record.results.append(
                VerificationResult(
                    target=target,
                    infrastructure=True,
                    detail=f"timed out after {timeout_s}s",
                    duration_s=round(time.monotonic() - started, 2),
                )
            )
        except (OSError, ValueError) as exc:
            record.results.append(
                VerificationResult(
                    target=target,
                    infrastructure=True,
                    detail=f"{type(exc).__name__}: {redact(str(exc))}",
                    duration_s=round(time.monotonic() - started, 2),
                )
            )
    return record


def _tail(output: str, lines: int = 14) -> str:
    kept = [ln for ln in (output or "").splitlines() if ln.strip()][-lines:]
    return "\n".join(kept)[:2000]


def verify_repository_surfaces(
    repo: Path,
    diff_files: Sequence[str],
    *,
    diff_text: str = "",
    max_targets: int = 3,
    timeout_s: int = 900,
    commit: str = "",
) -> RepositoryVerification:
    """Discover and run the repository's own verification for what changed.

    The whole path in one call, so the control loop asks one question and the
    policy of what that means lives here.
    """
    repo = Path(repo)
    specs, evidence = surfaces_touched(repo, diff_files, diff_text=diff_text)
    if not specs:
        return RepositoryVerification(commit=commit)
    targets, notes = discover_verification(repo, specs, max_targets=max_targets)
    return run_verification(
        repo,
        targets,
        timeout_s=timeout_s,
        commit=commit,
        notes=notes,
        surfaces=[s.name for s in specs],
        surface_evidence=evidence,
    )

"""Preparing the local acceptance record, and refusing to prepare anything else.

When every required criterion passes, a repository normally wants one small
commit: the registry row moves to accepted, the status document catches up, the
adjudication report lands. That commit is bookkeeping about work that already
happened, and it is the last thing standing between "the phase is done" and "the
founder can push".

It is also the single most dangerous commit in the system, because it is made at
the exact moment everybody has decided the work is finished and nobody is
looking at the diff any more. A runtime change riding along inside an acceptance
commit is a change that was never verified by anything, recorded as part of the
evidence that the phase was verified.

So this module does one thing and refuses everything else:

* it classifies every dirty path in the product repository by SURFACE;
* if anything outside the acceptance record is dirty, it refuses to prepare a
  commit at all and names each file and what surface it is;
* it stages nothing but the acceptance record, and only when the caller has
  policy to commit;
* it never pushes. There is no push code path in this module and no
  configuration that adds one.

Fail-closed is deliberate: a path this module cannot classify is ``UNKNOWN``,
and ``UNKNOWN`` refuses. A repository with an unusual layout gets its surfaces
configured; it does not get its unclassified files quietly committed.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Sequence

from .models import redact
from .phase_acceptance import normalize_repo_path


class Surface(str, Enum):
    """What kind of thing a changed file is."""

    #: Status, registry, evidence and report surfaces. The only writable class.
    ACCEPTANCE_RECORD = "ACCEPTANCE_RECORD"
    RUNTIME = "RUNTIME"
    TEST = "TEST"
    MIGRATION = "MIGRATION"
    CI = "CI"
    SPECIFICATION = "SPECIFICATION"
    #: Anything this module cannot place. Refused, on purpose.
    UNKNOWN = "UNKNOWN"


#: Surfaces an acceptance commit may contain. Exactly one.
WRITABLE_SURFACES = frozenset({Surface.ACCEPTANCE_RECORD})

#: Ordered. The first match wins, and the narrower classes come first, so a
#: migration under ``src/`` is a migration and a test under ``docs/`` is a test.
_SURFACE_PATTERNS: tuple[tuple[Surface, re.Pattern[str]], ...] = (
    (
        Surface.CI,
        re.compile(
            r"(?i)(?:\A|/)(?:\.github/|\.gitlab-ci\.ya?ml|\.circleci/|\.buildkite/|"
            r"azure-pipelines\.ya?ml|Jenkinsfile|\.travis\.ya?ml|\.drone\.ya?ml|"
            r"appveyor\.ya?ml|\.woodpecker)"
        ),
    ),
    (
        Surface.MIGRATION,
        re.compile(r"(?i)(?:\A|/)(?:migrations?|alembic|db/migrate)/|\.sql\Z"),
    ),
    (
        # A bare ``spec/`` is RSpec and Jasmine far more often than it is a
        # written specification, so a bare ``spec/`` falls through to TEST
        # below. A specification directory the repository actually names — and a
        # ``.spec.md`` file — is matched here first. It does not matter which of the two it lands in: both
        # refuse, and refusing for a slightly wrong reason is a report problem,
        # not a safety one.
        Surface.SPECIFICATION,
        re.compile(
            r"(?i)(?:\A|/)(?:docs?/specifications?|docs?/specs?|specifications?)/|"
            r"(?:\A|/)[^/]+\.spec\.md\Z"
        ),
    ),
    (
        Surface.TEST,
        re.compile(
            r"(?i)(?:\A|/)(?:tests?|eval/tests?|spec|__tests__|e2e|it)/|"
            r"(?:\A|/)(?:test_[^/]+|[^/]+_test|[^/]+\.test|[^/]+\.spec)\.[A-Za-z0-9]+\Z|"
            r"(?:\A|/)conftest\.py\Z"
        ),
    ),
    (
        Surface.ACCEPTANCE_RECORD,
        re.compile(
            r"(?i)(?:\A|/)docs?/implementation/|"
            r"(?:\A|/)(?:IMPLEMENTATION-REGISTRY|BUILD-STATUS|CURRENT|STATUS|CHANGELOG)"
            r"\.(?:ya?ml|json|md)\Z"
        ),
    ),
    (
        Surface.RUNTIME,
        re.compile(
            r"(?i)(?:\A|/)(?:src|lib|app|pkg|packages|internal|cmd|server|client|api|"
            r"scripts?|tools?|bin)/|"
            r"\.(?:py|ts|tsx|js|jsx|go|rs|rb|java|kt|c|cc|cpp|h|hpp|swift|php|ex|exs)\Z"
        ),
    ),
)


def classify_surface(
    path: str, *, acceptance_globs: Sequence[str] = ()
) -> Surface:
    """What surface one repository path belongs to. Fails closed.

    ``acceptance_globs`` is the repository's own statement of which paths carry
    its acceptance record, and it is consulted FIRST, because a repository is
    entitled to keep its status somewhere this module has never heard of. It is
    additive only: it can name a path as an acceptance record, and it cannot
    reclassify a runtime file as one — a glob that matched ``src/**`` would make
    this module's entire purpose configurable away, so a match is accepted only
    when the built-in classification is not RUNTIME, TEST, MIGRATION, CI or
    SPECIFICATION.
    """
    rel = normalize_repo_path(path)
    if not rel:
        return Surface.UNKNOWN

    builtin = Surface.UNKNOWN
    for surface, pattern in _SURFACE_PATTERNS:
        if pattern.search(rel):
            builtin = surface
            break

    if builtin in (
        Surface.RUNTIME,
        Surface.TEST,
        Surface.MIGRATION,
        Surface.CI,
        Surface.SPECIFICATION,
    ):
        return builtin

    for glob in acceptance_globs:
        if fnmatch.fnmatch(rel, glob):
            return Surface.ACCEPTANCE_RECORD
    return builtin


@dataclass
class ClassifiedPath:
    path: str
    surface: Surface
    status: str = ""

    @property
    def allowed(self) -> bool:
        return self.surface in WRITABLE_SURFACES

    def brief(self) -> str:
        return f"{self.surface.value:<18} {self.path}"


@dataclass
class AcceptanceCommitPlan:
    """What an acceptance commit would contain, and whether it may be made."""

    phase_id: str = ""
    paths: list[ClassifiedPath] = field(default_factory=list)
    message: str = ""
    #: Empty when the commit may be prepared; otherwise exactly why not.
    refusal: str = ""
    #: Set once a local commit has actually been made. Never pushed.
    committed_sha: str = ""
    #: What the caller's policy allowed. Recorded so a plan that was never
    #: offered a commit is distinguishable from one that was refused.
    commit_permitted_by_policy: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def allowed_paths(self) -> list[ClassifiedPath]:
        return [p for p in self.paths if p.allowed]

    @property
    def refused_paths(self) -> list[ClassifiedPath]:
        return [p for p in self.paths if not p.allowed]

    @property
    def permitted(self) -> bool:
        """Whether an acceptance commit may be prepared from this tree."""
        return not self.refusal and bool(self.allowed_paths)

    def diff_summary(self) -> str:
        if not self.paths:
            return "no uncommitted changes"
        return ", ".join(f"{p.path} [{p.surface.value}]" for p in self.allowed_paths) or "nothing allowed"

    def render(self) -> str:
        lines = [f"ACCEPTANCE COMMIT PREPARATION: {self.phase_id or '(no phase)'}"]
        if not self.paths:
            lines.append("  the working tree is clean; there is no acceptance record to commit")
        for entry in self.paths:
            mark = "  +" if entry.allowed else "  REFUSED"
            lines.append(f"{mark} {entry.brief()}")
        if self.refusal:
            lines.append(f"  REFUSED: {self.refusal}")
        elif self.committed_sha:
            lines.append(f"  local commit: {self.committed_sha}")
            lines.append("  NOT PUSHED. Publishing is the founder's action.")
        elif self.permitted:
            lines.append("  ready to commit locally; nothing has been staged yet")
        return "\n".join(lines)


def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _dirty_paths(repo: Path) -> list[tuple[str, str]]:
    """Every changed path in the working tree, with its porcelain status."""
    try:
        proc = _git(repo, "status", "--porcelain", "-uall")
    except (OSError, subprocess.SubprocessError):
        return []
    out: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        status, _, rest = line[:2], line[2:3], line[3:]
        path = rest.strip().strip('"')
        # A rename reports "old -> new"; the new path is the one being committed.
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip().strip('"')
        out.append((path, status.strip()))
    return out


def plan_acceptance_commit(
    repo: Path,
    *,
    phase_id: str = "",
    message: str = "",
    acceptance_globs: Sequence[str] = (),
) -> AcceptanceCommitPlan:
    """Classify the working tree and decide whether an acceptance commit may be made.

    The refusal is all-or-nothing on purpose. Staging only the status files out
    of a tree that also carries a runtime edit produces a commit whose message
    says "the phase is accepted" over a working tree containing unverified
    product changes — and the next commit, made by anyone, sweeps them up under
    whatever it happens to say.
    """
    repo = Path(repo)
    plan = AcceptanceCommitPlan(
        phase_id=phase_id,
        message=message
        or (
            f"Record {phase_id} acceptance: every required criterion passes on this tree"
            if phase_id
            else "Record phase acceptance"
        ),
    )
    for path, status in _dirty_paths(repo):
        plan.paths.append(
            ClassifiedPath(
                path=path,
                surface=classify_surface(path, acceptance_globs=acceptance_globs),
                status=status,
            )
        )

    refused = plan.refused_paths
    if refused:
        listed = "; ".join(f"{p.path} ({p.surface.value})" for p in refused[:8])
        plan.refusal = (
            "acceptance recording may only touch the acceptance record, and this tree "
            f"also changes {len(refused)} file(s) outside it: {listed}"
            + (" ..." if len(refused) > 8 else "")
            + ". Commit or revert them under their own review first."
        )
        return plan

    if not plan.allowed_paths:
        plan.refusal = (
            "the working tree carries no acceptance-record change, so there is nothing to "
            "commit; the acceptance is already recorded, or the record was never written"
        )
    return plan


def prepare_acceptance_commit(
    repo: Path,
    plan: AcceptanceCommitPlan,
    *,
    allow_commit: bool,
) -> AcceptanceCommitPlan:
    """Stage the acceptance record and, if policy allows, commit it locally.

    ``allow_commit`` is the caller's policy, not this module's judgement — the
    driver's ``allow_auto_commit`` is off by default and this respects that: with
    it off, the plan is produced, printed, and nothing is staged. Nothing here
    pushes, whatever ``allow_commit`` says.
    """
    plan.commit_permitted_by_policy = bool(allow_commit)
    if not plan.permitted:
        return plan
    if not allow_commit:
        plan.notes.append(
            "local commit is switched off (allow_auto_commit); the exact change is above "
            "and it has NOT been staged"
        )
        return plan

    repo = Path(repo)
    paths = [p.path for p in plan.allowed_paths]
    try:
        add = _git(repo, "add", "--", *paths)
        if add.returncode != 0:
            plan.refusal = f"staging the acceptance record failed: {redact(add.stderr)[:300]}"
            return plan
        commit = _git(repo, "commit", "-m", plan.message, "--", *paths)
        if commit.returncode != 0:
            plan.refusal = f"the acceptance commit failed: {redact(commit.stderr or commit.stdout)[:300]}"
            return plan
        head = _git(repo, "rev-parse", "HEAD")
        plan.committed_sha = head.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        plan.refusal = f"the acceptance commit could not be made: {type(exc).__name__}: {exc}"
        return plan

    plan.notes.append(
        "the commit is local. Product Driver does not push, and publishing this remains "
        "the founder's action."
    )
    return plan

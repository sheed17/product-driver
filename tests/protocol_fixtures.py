"""Synthetic Neyma-shaped repositories for the protocol-resolver tests.

Every fixture builds a real git repository in a temp directory. No test in this
suite reads, writes or touches the actual Neyma repository.

The builder deliberately writes the protocol *documents* rather than passing
rules to the resolver: the resolver must discover its rules from the repository
on every run, so the tests must exercise that path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

# --------------------------------------------------------------------------
# Protocol document bodies
# --------------------------------------------------------------------------

CLAUDE_MD = """\
# CLAUDE.md

## Authority

This file outranks all other documents. Authority order:

1. CLAUDE.md
2. docs/implementation/COMMIT-PROTOCOL.md
3. docs/implementation/IMPLEMENTATION-REGISTRY.yaml
4. docs/implementation/BUILD-STATUS.yaml

## Commit and finalization protocol

Each work unit MUST land as exactly one content commit, followed by one
finalizer-generated metadata/status commit. No other commit topology is
permitted between the authorized baseline and HEAD.

Derived status is owned exclusively by the finalizer. Only the finalizer may
write BUILD-STATUS.yaml, CURRENT.md or the registry's derived fields.

Manually editing derived status is forbidden. A hand-written status surface
proves nothing and MUST NOT be committed.

The canonical suite MUST be green before the finalizer may write status. The
finalizer refuses while the canonical suite is red.

A receipt MUST name the commit and tree it validated; a receipt that names any
other state is stale and MUST NOT be relied on.

Independent review MUST be performed by a fresh session, never by the
implementing session.

## Git history

Operations that rewrite history — reset, rebase, squash, amend of a reviewed
commit, cherry-pick reconstruction, branch force-update, tag deletion and
force-push — MUST NOT be performed without explicit founder approval.
"""

COMMIT_PROTOCOL_MD = """\
# COMMIT-PROTOCOL.md

## Topology

Exactly one content commit per unit. The status-reality guard enforces this and
MUST pass before finalization.

## Status commits

The metadata/status commit is finalizer-generated. It MUST NOT be written by
hand.
"""

# The same repository, but stating a second, incompatible topology rule.
CONFLICTING_PROTOCOL_MD = """\
# PROGRESS-PROTOCOL.md

## Topology

A unit MAY land as two content commits followed by one metadata commit, so that
remediation stays reviewable on its own.
"""

FINALIZER_PY = '''\
"""finalize_status.py — the only writer of derived status."""

SUITE_RECEIPT = "docs/implementation/SUITE-RESULT.json"
STATUS_FILE = "docs/implementation/BUILD-STATUS.yaml"
CURRENT_FILE = "docs/implementation/CURRENT.md"


def main() -> int:
    # The finalizer refuses while the canonical suite is red. It is the only
    # writer of derived status; no other actor may write BUILD-STATUS.yaml.
    receipt = read_json(SUITE_RECEIPT)
    if receipt.get("exit_status") != 0 or receipt.get("failed"):
        print("REFUSED: canonical suite is red")
        return 1
    write_status(STATUS_FILE, generated_by="finalize_status.py")
    return 0
'''

STATUS_GUARD_PY = '''\
"""test_status_reality.py — the status-reality guard.

Asserts that derived status corresponds to observable repository state, and that
the commit topology is exactly one content commit followed by one
finalizer-generated metadata/status commit.
"""


def test_status_reality_commit_topology() -> None:
    assert content_commit_count() == 1, "exactly one content commit is permitted"
'''

CANONICAL_INI = """\
[pytest]
testpaths = eval src/tests
"""

CLEAN_CLONE_PY = '''\
"""clean_clone_gate.py — the clean-clone gate.

Clones the repository into an empty directory, installs dependencies and runs
the canonical suite. The gate MUST pass before a unit is complete.
"""
GATE_RECEIPT = "docs/implementation/GATE-RESULT.json"
'''


class ProtocolRepo:
    """A synthetic repository with a real git history."""

    def __init__(self, root: Path, *, unit_id: str = "P3") -> None:
        self.root = root
        self.unit_id = unit_id
        self.impl = root / "docs" / "implementation"
        self.impl.mkdir(parents=True, exist_ok=True)
        (root / "src").mkdir(exist_ok=True)
        (root / "eval").mkdir(exist_ok=True)
        (root / "scripts").mkdir(exist_ok=True)

        self._git("init", "-q")
        self._git("config", "user.email", "t@e.com")
        self._git("config", "user.name", "t")
        self._git("config", "commit.gpgsign", "false")

    # -- git ---------------------------------------------------------------

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False
        )
        return proc.stdout.strip()

    def commit(self, message: str, *paths: str) -> str:
        """Commit everything, or only the given paths.

        Committing by pathspec matters: the fixtures leave finalizer-owned files
        modified in the working tree exactly as a real repository does when the
        finalizer has not run, and those must not be swept into a content commit.
        """
        to_add = ["-A"] if not paths else [p for p in paths if (self.root / p).exists()]
        if not to_add:
            return self.head()
        self._git("add", *to_add)
        subprocess.run(
            ["git", "commit", "-qm", message],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=False,
        )
        return self.head()

    def head(self) -> str:
        return self._git("rev-parse", "HEAD")

    def tree_of(self, rev: str = "HEAD") -> str:
        return self._git("rev-parse", f"{rev}^{{tree}}")

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    # -- protocol ----------------------------------------------------------

    def write_protocol(
        self,
        *,
        claude: str = CLAUDE_MD,
        commit_protocol: str | None = COMMIT_PROTOCOL_MD,
        extra_protocol: str | None = None,
        finalizer: bool = True,
        guard: bool = True,
        canonical: bool = True,
        clean_clone: bool = True,
    ) -> None:
        self.write("CLAUDE.md", claude)
        if commit_protocol is not None:
            self.write("docs/implementation/COMMIT-PROTOCOL.md", commit_protocol)
        if extra_protocol is not None:
            self.write("docs/implementation/PROGRESS-PROTOCOL.md", extra_protocol)
        if finalizer:
            self.write("scripts/finalize_status.py", FINALIZER_PY)
        if guard:
            self.write("eval/test_status_reality.py", STATUS_GUARD_PY)
        if canonical:
            self.write("pytest-canonical.ini", CANONICAL_INI)
        if clean_clone:
            self.write("scripts/clean_clone_gate.py", CLEAN_CLONE_PY)

    def write_registry(self, *, status: str = "READY", criteria: list[dict] | None = None) -> None:
        unit = {
            "unit_id": self.unit_id,
            "name": f"{self.unit_id} unit",
            "status": status,
            "objective": "do the work",
            "acceptance_criteria": criteria
            or [
                {"criterion": "core_implementation", "weight": 20, "result": "PASS"},
                {"criterion": "full_test_suite", "weight": 5, "result": "PENDING"},
                {"criterion": "independent_review", "weight": 5, "result": "PENDING"},
                {"criterion": "final_adjudication", "weight": 4, "result": "PENDING"},
            ],
        }
        self.write(
            "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
            yaml.safe_dump({"meta": {}, "units": [unit]}),
        )

    def write_build_status(
        self,
        *,
        baseline: str = "",
        content_commit: str = "",
        finalizer_result: str = "NOT EXECUTED",
        clean_clone_result: str = "NOT EXECUTED",
        percent: float = 0.0,
    ) -> None:
        self.write(
            "docs/implementation/BUILD-STATUS.yaml",
            yaml.safe_dump(
                {
                    "derived": {
                        "active_phase": self.unit_id,
                        "current_phase_percent": percent,
                        "authorized_baseline": baseline,
                        "content_commit": content_commit,
                    },
                    "snapshot": {
                        "finalizer_result": finalizer_result,
                        "clean_clone_result": clean_clone_result,
                    },
                }
            ),
        )

    def write_current(self, text: str = "# CURRENT\n\nWork in progress.\n") -> None:
        self.write("docs/implementation/CURRENT.md", text)

    def write_suite_receipt(
        self,
        *,
        commit: str = "",
        tree: str = "",
        failed: int = 0,
        exit_status: int = 0,
        failed_nodes: list[str] | None = None,
    ) -> None:
        self.write(
            "docs/implementation/SUITE-RESULT.json",
            json.dumps(
                {
                    "commit": commit or self.head(),
                    "tree": tree or self.tree_of(),
                    "exit_status": exit_status,
                    "passed": 1274 - failed,
                    "failed": failed,
                    "skipped": 0,
                    "failed_nodes": failed_nodes or [],
                },
                indent=2,
            ),
        )

    def write_gate_receipt(
        self, *, passed: bool = True, error: str = "", commit: str = "", tree: str = ""
    ) -> None:
        payload = {
            "commit": commit or self.head(),
            "tree": tree or self.tree_of(),
            "passed": passed,
            "gate": "clean_clone_gate",
            "steps": [],
        }
        if error:
            payload["steps"] = [{"step": "pip install", "ok": False, "error": error}]
        self.write("docs/implementation/GATE-RESULT.json", json.dumps(payload, indent=2))


# --------------------------------------------------------------------------
# Scenario builders
# --------------------------------------------------------------------------

TLS_ERROR = (
    "Could not fetch URL https://pypi.org/simple/pydantic/: There was a problem confirming "
    "the ssl certificate: HTTPSConnectionPool(host='pypi.org', port=443): Max retries exceeded "
    "(Caused by SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] "
    "certificate verify failed: unable to get local issuer certificate')))"
)


CONTENT_PATHS = ("src/kernel.py", "eval/test_kernel.py")
STATUS_PATHS = ("docs/implementation/BUILD-STATUS.yaml", "docs/implementation/CURRENT.md")
RECEIPT_PATHS = ("docs/implementation/SUITE-RESULT.json", "docs/implementation/GATE-RESULT.json")


def baseline_repo(root: Path, *, unit_id: str = "P3", **protocol_kw) -> ProtocolRepo:
    """A repository at its authorized baseline: protocol present, no unit work yet.

    The baseline pointer is written into BUILD-STATUS *after* the baseline commit
    and left uncommitted — which is what a repository actually looks like when
    the finalizer, the only actor permitted to commit that file, has not run.
    """
    repo = ProtocolRepo(root, unit_id=unit_id)
    repo.write_protocol(**protocol_kw)
    repo.write_registry()
    repo.write_current()
    repo.write_build_status()
    repo.write("src/kernel.py", "def kernel():\n    return 1\n")
    repo.write("eval/test_kernel.py", "def test_kernel():\n    assert True\n")
    repo.commit("baseline")
    repo.write_build_status(baseline=repo.head())
    return repo


def _add_content(repo: ProtocolRepo, marker: str, message: str) -> str:
    repo.write("src/kernel.py", f"def kernel():\n    return {marker}\n")
    repo.write("eval/test_kernel.py", f"def test_kernel():\n    assert kernel() == {marker}\n")
    return repo.commit(message, *CONTENT_PATHS)


def one_content_commit(root: Path, **kw) -> ProtocolRepo:
    """The permitted shape: baseline → one content commit."""
    repo = baseline_repo(root, **kw)
    _add_content(repo, "2", f"{repo.unit_id}: content")
    return repo


def content_plus_finalizer_metadata(root: Path, **kw) -> ProtocolRepo:
    """The fully permitted shape: baseline → content → finalizer metadata."""
    repo = one_content_commit(root, **kw)
    content = repo.head()
    repo.write_suite_receipt(commit=content, tree=repo.tree_of(content))
    repo.write_build_status(
        baseline=baseline_of(repo),
        content_commit=content,
        finalizer_result="EXECUTED - generated_by: finalize_status.py",
        percent=80.0,
    )
    repo.commit(f"{repo.unit_id}: status (finalizer)", *STATUS_PATHS, *RECEIPT_PATHS)
    return repo


def two_content_commits(root: Path, **kw) -> ProtocolRepo:
    """The P3 shape: baseline → content A → remediation content B."""
    repo = one_content_commit(root, **kw)
    _add_content(repo, "3", f"{repo.unit_id}: remediate independent-review findings")
    return repo


def content_plus_hand_edited_status(root: Path, **kw) -> ProtocolRepo:
    """baseline → content → hand-edited status commit (no finalizer marker)."""
    repo = one_content_commit(root, **kw)
    repo.write_current("# CURRENT\n\nP3 is basically done. Marking it complete.\n")
    repo.commit("update status", "docs/implementation/CURRENT.md")
    return repo


def p3_deadlock_repo(root: Path) -> ProtocolRepo:
    """The exact situation this resolver was built for.

    All independent-review findings remediated, targeted tests green, two
    content commits, the status-reality guard failing, the finalizer refusing,
    the clean-clone gate blocked on TLS, no fresh independent review, and
    nothing pushed. The receipts sit uncommitted because the finalizer — the
    only actor permitted to commit them — has not run.
    """
    repo = two_content_commits(root)
    content_a = repo._git("rev-parse", "HEAD^")
    repo.write_suite_receipt(
        commit=content_a,
        tree=repo.tree_of("HEAD^"),
        failed=1,
        exit_status=1,
        failed_nodes=["eval/test_status_reality.py::test_status_reality_commit_topology"],
    )
    repo.write_gate_receipt(passed=False, error=TLS_ERROR)
    repo.write_build_status(
        baseline=baseline_of(repo),
        finalizer_result="NOT EXECUTED - canonical suite is red",
        clean_clone_result=f"NOT EXECUTED - {TLS_ERROR[:120]}",
    )
    return repo


def baseline_of(repo: ProtocolRepo) -> str:
    """The baseline the repository currently records in BUILD-STATUS."""
    data = yaml.safe_load((repo.impl / "BUILD-STATUS.yaml").read_text())
    return str((data.get("derived") or {}).get("authorized_baseline") or "")


# --------------------------------------------------------------------------
# The finalized-pair fixture (run 20260723-083017 regression)
# --------------------------------------------------------------------------
#
# The exact shape the P4 run misclassified:
#
#     previous metadata  →  final-adjudication content  →  finalizer metadata (HEAD)
#
# with the registry recording baseline_commit = the CONTENT commit (Neyma's
# two-commit convention: a commit cannot store its own hash, so baseline_commit
# names the content baseline), the receipts bound to that content commit and its
# tree, and the finalizer metadata commit changing only DERIVED VALUES — so its
# ownership marker is unchanged context a -U0 diff never shows, and the finalizer
# must be recognised from the RESULTING file state.

_BUILD_STATUS_MARKED = """\
# BUILD STATUS — the finalizer-derived snapshot.
# derived-block: maintained by scripts/finalize_status.py - do not edit by hand
derived:
  content_commit: {content_commit}
  content_tree: {content_tree}
  active_phase: {unit}
  current_phase_percent: {percent}
# end-derived-block

snapshot:
  finalizer_result: "{finalizer_result}"
"""

_CURRENT_MARKED = """\
# CURRENT
# status-block: maintained by scripts/finalize_status.py - do not edit by hand
active_phase: {unit}
content_commit: {content_commit}
# end-status-block

{narrative}
"""


def _registry_with_baseline_pointer(unit_id: str, content_commit: str, content_tree: str) -> str:
    """A registry whose recorded baseline_commit is the CURRENT content commit."""
    return yaml.safe_dump(
        {
            "meta": {
                # The two-commit convention: this names the content commit, NOT
                # the historical baseline. The resolver must not take it literally.
                "baseline_commit": content_commit,
                "validated_tree": content_tree,
                "status_convention": (
                    "content commit first, then one status-metadata commit that records it; "
                    "baseline_commit names the content baseline"
                ),
            },
            "units": [
                {
                    "unit_id": unit_id,
                    "name": f"{unit_id} unit",
                    "status": "READY",
                    "objective": "do the work",
                    "acceptance_criteria": [
                        {"criterion": "core_implementation", "weight": 20, "result": "PASS"},
                        {"criterion": "full_test_suite", "weight": 5, "result": "PASS"},
                        {"criterion": "independent_review", "weight": 5, "result": "PENDING"},
                        {"criterion": "final_adjudication", "weight": 4, "result": "PENDING"},
                    ],
                }
            ],
        }
    )


def finalized_pair_with_content_baseline_pointer(
    root: Path, *, unit_id: str = "P4", **protocol_kw
) -> ProtocolRepo:
    """previous metadata → final-adjudication content → finalizer metadata (HEAD).

    The exact topology of run 20260723-083017. The registry records the CONTENT
    commit as baseline_commit; the receipts bind to the content commit and its
    tree; the finalizer metadata commit changes only derived values (marker line
    unchanged). A correct resolver reads this as CONSISTENT.
    """
    repo = ProtocolRepo(root, unit_id=unit_id)
    repo.write_protocol(**protocol_kw)
    repo.write("src/kernel.py", "def kernel():\n    return 1\n")
    repo.write("eval/test_kernel.py", "def test_kernel():\n    assert True\n")
    repo.write(
        "docs/implementation/BUILD-STATUS.yaml",
        _BUILD_STATUS_MARKED.format(
            content_commit="", content_tree="", unit=unit_id, percent=0.0,
            finalizer_result="NOT EXECUTED",
        ),
    )
    repo.write(
        "docs/implementation/CURRENT.md",
        _CURRENT_MARKED.format(unit=unit_id, content_commit="", narrative="Baseline."),
    )
    repo.write_registry()
    repo.commit("baseline")

    # The PREVIOUS metadata commit — a finalizer metadata commit closing an
    # earlier unit. This is the true historical baseline of the current pair.
    repo.write(
        "docs/implementation/BUILD-STATUS.yaml",
        _BUILD_STATUS_MARKED.format(
            content_commit=repo.head(), content_tree=repo.tree_of(), unit=unit_id, percent=50.0,
            finalizer_result="EXECUTED (previous unit)",
        ),
    )
    repo.commit("previous unit: status (finalizer)", "docs/implementation/BUILD-STATUS.yaml")
    previous_metadata = repo.head()

    # The final-adjudication CONTENT commit: runtime content + a review doc, and
    # it also carries the adjudicated status narrative (derived percent). The
    # receipts will bind to THIS commit and its tree.
    repo.write("src/kernel.py", "def kernel():\n    return 2\n")
    repo.write("eval/test_kernel.py", "def test_kernel():\n    assert kernel() == 2\n")
    repo.write(
        "docs/implementation/p4-final-adjudication-review.md",
        "# Final adjudication\n\nAll criteria PASS. P-next becomes READY.\n",
    )
    repo.write(
        "docs/implementation/BUILD-STATUS.yaml",
        _BUILD_STATUS_MARKED.format(
            content_commit=previous_metadata, content_tree=repo.tree_of(previous_metadata),
            unit=unit_id, percent=100.0, finalizer_result="EXECUTED (previous unit)",
        ),
    )
    repo.commit(
        f"{unit_id}: final adjudication (content)",
        "src/kernel.py",
        "eval/test_kernel.py",
        "docs/implementation/p4-final-adjudication-review.md",
        "docs/implementation/BUILD-STATUS.yaml",
    )
    content = repo.head()
    content_tree = repo.tree_of(content)

    # The registry records the content commit as baseline_commit.
    repo.write(
        "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
        _registry_with_baseline_pointer(unit_id, content, content_tree),
    )

    # The receipts bind to the content commit and its tree.
    repo.write_suite_receipt(commit=content, tree=content_tree)
    repo.write_gate_receipt(commit=content, tree=content_tree)

    # The current FINALIZER metadata commit: changes only derived VALUES
    # (content_commit/content_tree rebinding) and the receipts. The ownership
    # marker line is unchanged, so a -U0 diff shows no finalizer marker — the
    # finalizer must be recognised from the resulting file state.
    repo.write(
        "docs/implementation/BUILD-STATUS.yaml",
        _BUILD_STATUS_MARKED.format(
            content_commit=content, content_tree=content_tree, unit=unit_id,
            percent=100.0, finalizer_result="EXECUTED (previous unit)",
        ),
    )
    repo.write(
        "docs/implementation/CURRENT.md",
        _CURRENT_MARKED.format(unit=unit_id, content_commit=content, narrative="Finalized."),
    )
    repo.commit(
        f"Record executed status for the {unit_id} final-adjudication commit (status-metadata commit)",
        "docs/implementation/BUILD-STATUS.yaml",
        "docs/implementation/CURRENT.md",
        "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
        *RECEIPT_PATHS,
    )
    return repo


# --------------------------------------------------------------------------
# The PRODUCING fixture (P4 recovery regression)
# --------------------------------------------------------------------------
#
# The legal state the driver previously condemned:
#
#     certified content  →  finalizer metadata  →  next content (HEAD)
#                                                  + dirty/untracked worktree
#
# This is the repository's own PRODUCING state: the recorded content_commit is
# HEAD^^, HEAD^ is a pure status-metadata commit, and HEAD is the next content
# commit being produced. The receipts on disk correctly name the certified pair,
# because the receipt for HEAD is exactly what this state exists to produce.
#
# Read as a flat commit list it looks alarming — a status commit sitting before a
# content commit — and the driver used to report "status was recorded for a tree
# that did not exist yet", rank a history rewrite first, and offer to amend the
# protocol. All three were wrong. Nothing here is broken.


def producing_after_certified_pair(
    root: Path, *, unit_id: str = "P4", dirty: bool = True, **protocol_kw
) -> ProtocolRepo:
    """certified content → finalizer metadata → next content (+ WIP worktree).

    The exact shape of p4/adapter-containment-completion at 72512b9 on the
    3d231731b8b0 + f1e8e18 certified pair.
    """
    repo = finalized_pair_with_content_baseline_pointer(root, unit_id=unit_id, **protocol_kw)

    # The next content commit, built on the certified pair. Derived status is NOT
    # touched: it still records the certified pair, which is what makes this
    # PRODUCING rather than finalized.
    repo.write("src/kernel.py", "def kernel():\n    return 3\n")
    repo.write("eval/test_kernel.py", "def test_kernel():\n    assert kernel() == 3\n")
    repo.write("src/adapter.py", "def adapter():\n    return 'read-only'\n")
    repo.commit(
        f"{unit_id} F2: the genuinely read-only adapter surface (content commit)",
        "src/kernel.py",
        "eval/test_kernel.py",
        "src/adapter.py",
    )

    if dirty:
        # An in-progress implementation episode: modified tracked files plus
        # untracked new files that exist nowhere else.
        repo.write("src/kernel.py", "def kernel():\n    return 4  # EP-1 in progress\n")
        repo.write("src/governed_approval.py", "def approve():\n    return 'EP-1 untracked'\n")
        repo.write("eval/test_governed_approval.py", "def test_approve():\n    assert True\n")

    return repo


def certified_pair_commits_of(repo: ProtocolRepo) -> tuple[str, str, str]:
    """``(certified_content, finalizer_metadata, next_content)`` for the fixture."""
    return (
        repo._git("rev-parse", "HEAD^^"),
        repo._git("rev-parse", "HEAD^"),
        repo._git("rev-parse", "HEAD"),
    )

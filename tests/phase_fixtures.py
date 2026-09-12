"""A minimal repository that declares a phase, its criteria and its residuals.

Small on purpose. Every test below reasons about ONE mechanism, and a fixture
that carried thirteen machines would make each of those tests a test of the
fixture. The shapes are the generic ones
:mod:`~neyma_product_driver.phase_authority` reads, not any one repository's.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Sequence

import yaml

REGISTRY_REL = "docs/implementation/IMPLEMENTATION-REGISTRY.yaml"


def criterion(
    cid: str,
    name: str,
    *,
    required: bool = True,
    result: str = "PASS",
    weight: float = 1.0,
    requirement: str = "",
    evidence: str = "",
) -> dict[str, Any]:
    return {
        "id": cid,
        "criterion": name,
        "weight": weight,
        "required": required,
        "result": result,
        "requirement": requirement or f"the {name} test passes on the accepted tree",
        "adjudication_evidence": evidence,
    }


def default_criteria() -> list[dict[str, Any]]:
    return [
        criterion(
            "AC-1",
            "behaviour_landed",
            evidence="eval/tests/test_behaviour.py::test_behaviour_holds passes on this tree",
        ),
        criterion(
            "AC-2",
            "guards_hold",
            evidence="scripts/mutate_guards.py caught 9/9 mutants, control green",
        ),
        criterion("AC-3", "ci_green_on_the_accepted_tree", evidence="the CI workflow"),
        criterion("AC-4", "independent_phase_review_by_a_non_builder", evidence="an adjudication"),
        criterion("AC-5", "carried_residuals_recorded_and_nonblocking", evidence="the ledger"),
    ]


def write_registry(
    repo: Path,
    *,
    phase_id: str = "P9",
    criteria: list[dict[str, Any]] | None = None,
    status: str = "READY",
    execution_state: str = "IN_PROGRESS",
    checkpoint_state: str = "CHECKPOINT_IMPLEMENTED",
    checkpoints: int = 2,
    expected_checkpoints: int | None = None,
    residuals: list[dict[str, Any]] | None = None,
    extra_units: list[dict[str, Any]] | None = None,
    next_units: list[str] | None = None,
) -> Path:
    """Write a registry declaring one phase. Returns the registry path."""
    unit: dict[str, Any] = {
        "unit_id": phase_id,
        "name": "a phase under test",
        "status": status,
        "execution_state": execution_state,
        "checkpoint_state": checkpoint_state,
        "acceptance_contract": "every required criterion passes on the accepted tree",
        "landed_checkpoints": [
            {
                "id": f"{phase_id}-CP-{i + 1}",
                "name": f"checkpoint {i + 1}",
                "checkpoint_state": "CHECKPOINT_ACCEPTED_FOR_CONTINUATION",
                "candidate_commit": f"{i + 1:040d}",
            }
            for i in range(checkpoints)
        ],
        "next_units_unlocked": next_units or ["P10"],
    }
    if criteria is not None:
        unit["acceptance_criteria"] = criteria
    if expected_checkpoints is not None:
        unit["expected_checkpoints"] = expected_checkpoints
    if residuals is not None:
        unit["residual_risks_carried_forward"] = residuals

    units = [unit] + list(extra_units or [])
    path = repo / REGISTRY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"units": units}, sort_keys=False), encoding="utf-8")
    return path


def phase_repo(
    tmp_path: Path,
    *,
    with_criteria: bool = True,
    **kwargs: Any,
) -> Path:
    """A git repository with the evidence artifacts the criteria cite."""
    repo = tmp_path / "product"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)

    (repo / "CLAUDE.md").write_text("# authority\n", encoding="utf-8")
    (repo / "eval" / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "eval" / "tests" / "test_behaviour.py").write_text(
        "def test_behaviour_holds():\n    assert True\n", encoding="utf-8"
    )
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "scripts" / "mutate_guards.py").write_text("# battery\n", encoding="utf-8")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "product.py").write_text("VALUE = 1\n", encoding="utf-8")

    criteria = kwargs.pop("criteria", default_criteria() if with_criteria else None)
    write_registry(repo, criteria=criteria, **kwargs)

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "phase under test"], cwd=repo, check=True)
    return repo


def head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit_all(repo: Path, message: str = "change") -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return head(repo)


class FakeFinding:
    """What a reviewer's finding looks like to the controller."""

    def __init__(self, finding: str, severity: str = "major", evidence_path: str = "", reasoning: str = ""):
        self.finding = finding
        self.severity = severity
        self.evidence_path = evidence_path
        self.reasoning = reasoning


class FakeAssessment:
    def __init__(self, criterion: str, assessment: str, basis: str = ""):
        self.criterion = criterion
        self.assessment = assessment
        self.basis = basis


class FakeReview:
    """An IndependentReview, structurally, without a session behind it."""

    def __init__(
        self,
        *,
        verdict: str = "SUPPORTED",
        summary: str = "",
        reviewer_session_id: str = "reviewer-1",
        inherited_builder_context: bool = False,
        criteria_assessment: list[FakeAssessment] | None = None,
        findings: list[FakeFinding] | None = None,
        reviewed_fingerprint: dict[str, Any] | None = None,
        reproduced_runtime_evidence: bool = True,
    ):
        self.verdict = verdict
        self.summary = summary
        self.reviewer_session_id = reviewer_session_id
        self.inherited_builder_context = inherited_builder_context
        self.criteria_assessment = criteria_assessment or []
        self.findings = findings or []
        self.reviewed_fingerprint = reviewed_fingerprint or {}
        self.reproduced_runtime_evidence = reproduced_runtime_evidence


def supporting_review(
    criteria_ids: list[str], failing: Sequence[str] = (), **kwargs: Any
) -> FakeReview:
    """An adjudication that scored every criterion, PASS unless named in ``failing``.

    ``failing`` exists so a test can build an adjudication that is INTERNALLY
    CONSISTENT: a reviewer whose finding demonstrates a criterion false and who
    scores that same criterion PASS has contradicted itself, and the controller
    now says so rather than picking a side. A test about what a demonstrated
    product defect does must therefore score the criterion the way its own
    finding reads.
    """
    failed = {str(cid) for cid in failing}
    return FakeReview(
        criteria_assessment=[
            FakeAssessment(
                cid,
                "FAIL" if cid in failed else "PASS",
                "re-derived on this tree",
            )
            for cid in criteria_ids
        ],
        **kwargs,
    )

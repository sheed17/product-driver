"""``phase close`` must consume the state it prints.

The defect this file exists for is a sequencing one, and it is generic. The
command branched on the state :meth:`PhaseClosureController.preflight` left
behind — a reading of the tree taken before the external gate had answered and
before the stop rule had run — and only afterwards called ``decide()``, which is
what actually establishes ``READY_FOR_ADJUDICATION``. So the invocation that had
just been handed a green gate on the exact candidate tree, and the invocation
resuming an attempt already resting at ``READY_FOR_ADJUDICATION``, both printed
``READY_FOR_ADJUDICATION`` and returned to the shell with
``independent_review: NOT_TAKEN``. The state was a resting place the command that
owns it could not leave.

Everything here drives the real CLI. No session is spent: the adjudicator is a
stand-in that records that it was constructed, which is the whole question.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import pytest
import yaml

from neyma_product_driver.cli import main
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.phase_closure import CLOSURE_FILE

from phase_fixtures import (
    FakeAssessment,
    FakeReview,
    accepted_unit,
    commit_all,
    head,
    pending_unit,
    phase_repo,
)

RUN_ID = "20260913-000000"


# --------------------------------------------------------------------------
# the two candidate shapes
# --------------------------------------------------------------------------


def pending_criterion(cid: str, name: str, requirement: str = "") -> dict[str, Any]:
    """A criterion the acceptance has not scored yet and nothing yet evidences.

    This is the ordinary shape of a phase being closed for the FIRST time, and
    it is the shape that made the defect visible: the preflight reads it as a
    blocking evidence gap, the stop rule reads the same facts and says an
    adjudication is the next transition, and the adjudication is precisely what
    would score it.
    """
    return {
        "id": cid,
        "criterion": name,
        "weight": 1,
        "required": True,
        "result": "PENDING",
        "requirement": requirement or f"{name} holds on the accepted tree",
        "adjudication_evidence": "",
    }


def unevidenced_criteria() -> list[dict[str, Any]]:
    """A phase whose preflight blocks and whose stop rule says ADJUDICATE."""
    return [
        *(pending_criterion(f"AC-{i}", f"surface_{i}_holds") for i in range(1, 4)),
        pending_criterion(
            "AC-4",
            "ci_success_on_the_candidate_tree",
            "the CI workflow is green on the exact candidate commit",
        ),
        pending_criterion(
            "AC-5",
            "independent_phase_review_by_a_non_builder",
            "a session that did not build this phase adjudicates it",
        ),
    ]


CRITERION_IDS = ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]


def closure_repo(tmp_path: Path, criteria: list[dict[str, Any]] | None = None) -> Path:
    """A phase to close, beside a phase the repository already accepted.

    The precedent matters to the last step and to nothing else: the acceptance
    record is written in the repository's OWN format, which is read off an
    already-accepted unit rather than invented here.
    """
    return phase_repo(
        tmp_path,
        extra_units=[
            accepted_unit("P8", next_units=("P9",)),
            pending_unit("P10", dependencies=("P9",)),
        ],
        **({"criteria": criteria} if criteria is not None else {}),
    )


# --------------------------------------------------------------------------
# the harness
# --------------------------------------------------------------------------


class Launches:
    """What the command actually did, in the order it did it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.reviewers: list[dict[str, Any]] = []

    @property
    def events(self) -> list[str]:
        if not self.path.exists():
            return []
        return self.path.read_text(encoding="utf-8").split()

    def record(self, event: str) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event + "\n")


class StubReview(FakeReview):
    """An ``IndependentReview`` as far as everything downstream can tell."""

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "reviewer_session_id": self.reviewer_session_id,
            "inherited_builder_context": self.inherited_builder_context,
            "criteria_assessment": [
                {"criterion": a.criterion, "assessment": a.assessment, "basis": a.basis}
                for a in self.criteria_assessment
            ],
            "findings": [],
            "reviewed_fingerprint": self.reviewed_fingerprint,
            "reproduced_runtime_evidence": self.reproduced_runtime_evidence,
        }


def complete_verdict(criterion_ids: Sequence[str] = CRITERION_IDS, **kwargs: Any) -> StubReview:
    """An adjudication that scored every frozen criterion and settles the phase."""
    return StubReview(
        criteria_assessment=[
            FakeAssessment(cid, "PASS", "re-derived on this tree") for cid in criterion_ids
        ],
        **kwargs,
    )


@pytest.fixture
def launches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Launches:
    """Stand in for the adjudicator session, and remember every construction."""
    import neyma_product_driver.reviewer as reviewer_module

    log = Launches(tmp_path / "events.txt")
    log.review = complete_verdict()  # type: ignore[attr-defined]

    class StubReviewerSession:
        def __init__(self, repo: Path, **kwargs: Any) -> None:
            self.repo = repo
            self.kwargs = kwargs

        async def __aenter__(self) -> "StubReviewerSession":
            log.record("reviewer")
            log.reviewers.append(dict(self.kwargs))
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

        async def review(self, _prompt: str) -> StubReview:
            return log.review  # type: ignore[attr-defined]

    monkeypatch.setattr(reviewer_module, "IndependentReviewerSession", StubReviewerSession)
    return log


def probe_script(tmp_path: Path, launches: Launches, *, sha: str, status: str) -> str:
    """A configured external probe that records that it ran, then answers.

    A real one asks a verifier. This one answers from an argument, because what
    these tests are about is WHEN the gate is asked relative to the adjudicator,
    not what a verifier would say.
    """
    script = tmp_path / "probe.py"
    script.write_text(
        "import json, sys, pathlib\n"
        f"pathlib.Path({str(launches.path)!r}).open('a').write('probe\\n')\n"
        "print(json.dumps({'sha': sys.argv[1], 'status': sys.argv[2],\n"
        "                  'conclusion': sys.argv[2].lower()}))\n",
        encoding="utf-8",
    )
    return f"python3 {script} {sha} {status}"


def config_file(tmp_path: Path, repo: Path, **closure: Any) -> Path:
    path = tmp_path / "driver.config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "neyma_repo": str(repo),
                "runs_dir": str(tmp_path / "runs"),
                "scenarios_dir": str(tmp_path / "scenarios"),
                "task": "close the phase",
                "phase_closure": closure or {},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "scenarios").mkdir(exist_ok=True)
    return path


def preflight(config: Path, run_id: str = RUN_ID) -> int:
    return main(["phase", "preflight", "--config", str(config), "--phase", "P9", "--run", run_id])


def supply_external(config: Path, sha: str, status: str = "SUCCESS", run_id: str = RUN_ID) -> int:
    return main(
        [
            "phase", "external-evidence", "--config", str(config), "--run", run_id,
            "--sha", sha, "--status", status,
        ]
    )


def close(config: Path, *extra: str, run_id: str | None = RUN_ID) -> int:
    argv = ["phase", "close", "--config", str(config), "--phase", "P9", "--yes"]
    if run_id:
        argv += ["--run", run_id]
    return main(argv + list(extra))


def persisted(tmp_path: Path, run_id: str = RUN_ID) -> dict[str, Any]:
    path = EvidenceStore(tmp_path / "runs", run_id).run_dir / CLOSURE_FILE
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# the defect
# --------------------------------------------------------------------------


class TestTheCommandConsumesTheStateItPrints:
    """The exact control-flow failure, from both directions it arrives from."""

    def test_a_gate_that_goes_green_in_this_invocation_reaches_the_adjudicator(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        """Preflight says WAITING, the probe answers SUCCESS, and the state moves.

        The whole transition inside one invocation. Before the fix the branch was
        taken against the preflight's WAITING — a reading from before the probe
        had been asked — so the command printed READY_FOR_ADJUDICATION on its way
        out and launched nothing.
        """
        repo = closure_repo(tmp_path)
        config = config_file(
            tmp_path,
            repo,
            external_probe_command=probe_script(
                tmp_path, launches, sha=head(repo), status="SUCCESS"
            ),
        )
        preflight(config)
        assert persisted(tmp_path)["state"] == "WAITING_FOR_EXTERNAL_VERIFICATION"
        capsys.readouterr()

        code = close(config)
        captured = capsys.readouterr().out

        assert launches.events == ["probe", "reviewer"], (
            "the external gate must be asked first, and the adjudicator must be "
            "launched in the same invocation"
        )
        assert "independent phase adjudicator working" in captured
        assert len(launches.reviewers) == 1
        assert code == 0

    def test_an_attempt_resting_at_ready_for_adjudication_is_consumed(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        """The p7-closure-e9f75e18 shape, with no phase or criterion id in it.

        The gate was supplied out of band, the attempt settled at
        READY_FOR_ADJUDICATION and persisted it. The next ``phase close``
        re-preflights, and on a phase whose criteria are not yet evidenced the
        preflight's own verdict is PREFLIGHT_BLOCKED — so the branch was never
        taken, ``decide()`` then restated READY_FOR_ADJUDICATION, and the command
        printed the transition it had just declined to make.
        """
        repo = closure_repo(tmp_path, unevidenced_criteria())
        config = config_file(tmp_path, repo)
        preflight(config)
        assert supply_external(config, head(repo)) == 10
        assert persisted(tmp_path)["state"] == "READY_FOR_ADJUDICATION"
        capsys.readouterr()

        code = close(config)
        captured = capsys.readouterr().out

        assert launches.events == ["reviewer"]
        assert "independent phase adjudicator working" in captured
        assert persisted(tmp_path)["adjudication"] is not None
        assert code == 0

    def test_the_preflights_own_verdict_still_disagrees_and_no_longer_decides(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        """The two rules are allowed to differ; only one of them is the state.

        Pinned so the fix cannot be mistaken for "the preflight now agrees".
        ``phase preflight`` is the cheap reading and still reports its own
        verdict; what changed is that ``phase close`` acts on the settled state
        rather than on that reading.
        """
        repo = closure_repo(tmp_path, unevidenced_criteria())
        config = config_file(tmp_path, repo)
        preflight(config)
        supply_external(config, head(repo))

        assert preflight(config) == 12  # PREFLIGHT_BLOCKED, from the cheap reading
        assert close(config, "--no-adjudication") == 10  # READY_FOR_ADJUDICATION, settled
        assert launches.events == []


# --------------------------------------------------------------------------
# what must still never launch a reviewer
# --------------------------------------------------------------------------


class TestNothingIsLaunchedWithoutAGreenGateOnThisTree:
    """An adjudication is the scarcest thing here. Requirement 7, four ways."""

    def test_a_missing_gate_launches_nothing(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        repo = closure_repo(tmp_path)
        config = config_file(tmp_path, repo)
        preflight(config)
        code = close(config)
        assert launches.events == []
        assert code == 11

    def test_a_failed_gate_launches_nothing(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        repo = closure_repo(tmp_path)
        config = config_file(
            tmp_path,
            repo,
            external_probe_command=probe_script(
                tmp_path, launches, sha=head(repo), status="FAILURE"
            ),
        )
        preflight(config)
        code = close(config)
        assert launches.events == ["probe"]
        assert code != 0
        assert persisted(tmp_path)["state"] in {"BLOCKED", "WAITING_FOR_EXTERNAL_VERIFICATION"}

    def test_a_gate_about_another_commit_launches_nothing(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        """Green, truthful, and about a tree nobody is accepting."""
        repo = closure_repo(tmp_path)
        config = config_file(tmp_path, repo)
        preflight(config)
        assert supply_external(config, "0" * 40) == 11
        assert close(config) == 11
        assert launches.events == []

    def test_a_tree_that_moved_after_a_green_gate_launches_nothing(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        """Requirement 8: a moved tree invalidates the gate exactly as before."""
        repo = closure_repo(tmp_path)
        config = config_file(tmp_path, repo)
        preflight(config)
        supply_external(config, head(repo))
        (repo / "src" / "product.py").write_text("VALUE = 2\n", encoding="utf-8")
        commit_all(repo, "the tree moved")

        assert close(config) == 11
        assert launches.events == []
        assert persisted(tmp_path)["external_evidence"] is None


class TestTheOperatorsTwoWaysOfAskingForNothing:
    def test_no_adjudication_returns_ready_and_launches_nothing(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        repo = closure_repo(tmp_path)
        config = config_file(tmp_path, repo)
        preflight(config)
        supply_external(config, head(repo))
        capsys.readouterr()

        code = close(config, "--no-adjudication")
        assert code == 10
        assert "READY_FOR_ADJUDICATION" in capsys.readouterr().out
        assert launches.events == []

    def test_analysis_launches_nothing_and_writes_nothing(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        repo = closure_repo(tmp_path)
        config = config_file(
            tmp_path,
            repo,
            external_probe_command=probe_script(
                tmp_path, launches, sha=head(repo), status="SUCCESS"
            ),
        )
        code = main(
            ["phase", "close", "--config", str(config), "--phase", "P9", "--analysis", "--yes"]
        )
        assert launches.events == []
        assert code == 11
        assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").glob("*/" + CLOSURE_FILE))

    def test_analysis_over_a_settled_attempt_still_launches_nothing(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        """The state says ADJUDICATE and analysis mode still spends nothing."""
        repo = closure_repo(tmp_path, unevidenced_criteria())
        config = config_file(tmp_path, repo)
        preflight(config)
        supply_external(config, head(repo))

        code = main(
            [
                "phase", "close", "--config", str(config), "--phase", "P9",
                "--run", RUN_ID, "--analysis", "--yes",
            ]
        )
        assert launches.events == []
        assert code == 10


# --------------------------------------------------------------------------
# exactly one, and only when it is owed
# --------------------------------------------------------------------------


class TestOneAdjudicationAndNoMore:
    def test_a_valid_adjudication_for_this_tree_is_not_taken_twice(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        repo = closure_repo(tmp_path, unevidenced_criteria())
        config = config_file(tmp_path, repo)
        preflight(config)
        supply_external(config, head(repo))

        assert close(config) == 0
        assert len(launches.reviewers) == 1

        assert close(config) == 0
        assert len(launches.reviewers) == 1, "a second close must not buy a second opinion"

    def test_a_complete_independent_verdict_carries_the_closure_onward(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        """Requirement 9: the acceptance record comes after the adjudication settled."""
        repo = closure_repo(tmp_path)
        config = config_file(tmp_path, repo)
        preflight(config)
        supply_external(config, head(repo))
        capsys.readouterr()

        code = close(config)
        captured = capsys.readouterr().out

        assert len(launches.reviewers) == 1
        assert persisted(tmp_path)["state"] == "READY_FOR_ACCEPTANCE_COMMIT"
        assert captured.index("independent phase adjudicator working") < captured.index(
            "ACCEPTANCE RECORD"
        )
        assert code == 0

    def test_an_adjudication_that_is_not_independent_still_fails_closed(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        repo = closure_repo(tmp_path)
        config = config_file(tmp_path, repo)
        preflight(config)
        supply_external(config, head(repo))
        launches.review = complete_verdict(inherited_builder_context=True)  # type: ignore[attr-defined]
        capsys.readouterr()

        code = close(config)
        captured = capsys.readouterr().out

        assert len(launches.reviewers) == 1
        assert code == 21
        assert "does not count" in captured
        assert "ACCEPTANCE RECORD" not in captured

    def test_an_incomplete_verdict_does_not_reach_the_acceptance_record(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        """Independent, taken, and it left criteria unscored. No record is written."""
        repo = closure_repo(tmp_path, unevidenced_criteria())
        config = config_file(tmp_path, repo)
        preflight(config)
        supply_external(config, head(repo))
        launches.review = complete_verdict(["AC-4", "AC-5"])  # type: ignore[attr-defined]
        capsys.readouterr()

        code = close(config)
        captured = capsys.readouterr().out

        assert len(launches.reviewers) == 1
        assert "ACCEPTANCE RECORD" not in captured
        assert code != 0

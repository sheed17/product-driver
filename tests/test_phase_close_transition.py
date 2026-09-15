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
from pydantic import ValidationError

from neyma_product_driver.cli import main
from neyma_product_driver.config import (
    PhaseClosureConfig,
    ReviewPolicyConfig,
    load_config,
)
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
        #: Responses to give, in order; the last one repeats. Empty means
        #: ``review`` (the single scripted response) answers every turn.
        self.responses: list[Any] = []
        #: Every prompt the session was actually given, so a test can check that
        #: a corrective request was — or was not — made, and what it asked for.
        self.prompts: list[str] = []

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
    # The real class, kept before the patch replaces the module attribute, so a
    # test can still ask what an UNCONFIGURED session would have taken.
    log.real_session = reviewer_module.IndependentReviewerSession  # type: ignore[attr-defined]

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

        async def review(self, prompt: str) -> StubReview:
            log.prompts.append(prompt)
            if len(log.responses) > 1:
                answer = log.responses.pop(0)
            elif log.responses:
                answer = log.responses[0]
            else:
                answer = log.review  # type: ignore[attr-defined]
            if isinstance(answer, BaseException):
                # A session that has already ended. The real one does this: a
                # reply that came back `error_max_turns` left no process to ask
                # again, and the SDK raises on the next query.
                raise answer
            return answer

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


# --------------------------------------------------------------------------
# a response that did not answer, through the real command
# --------------------------------------------------------------------------


def adjudication_files(tmp_path: Path, run_id: str = RUN_ID) -> list[str]:
    run_dir = EvidenceStore(tmp_path / "runs", run_id).run_dir
    return sorted(p.name for p in run_dir.glob("phase-adjudication*.json"))


def adjudication_json(tmp_path: Path, name: str, run_id: str = RUN_ID) -> dict[str, Any]:
    run_dir = EvidenceStore(tmp_path / "runs", run_id).run_dir
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


class TestAResponseThatDidNotCoverTheContract:
    """The real P7 shape, end to end: 0 of 5 criteria scored, and what follows.

    The reviewer here is independent, reads the exact tree, and returns
    ``INSUFFICIENT_EVIDENCE`` with an empty assessment set — the exact reply the
    real p7-closure-e9f75e18 got. It must not become a product verdict.
    """

    def _standing(self, tmp_path: Path) -> Path:
        repo = closure_repo(tmp_path, unevidenced_criteria())
        config = config_file(tmp_path, repo)
        preflight(config)
        assert supply_external(config, head(repo)) == 10
        return config

    def test_the_same_session_is_asked_once_for_a_complete_answer(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        config = self._standing(tmp_path)
        launches.responses = [
            StubReview(verdict="INSUFFICIENT_EVIDENCE", criteria_assessment=[]),
            complete_verdict(),
        ]
        capsys.readouterr()

        code = close(config)
        captured = capsys.readouterr().out

        assert len(launches.reviewers) == 1, "the correction is the SAME session, not a new one"
        assert len(launches.prompts) == 2
        assert "YOUR RESPONSE DID NOT COVER THE ADJUDICATION CONTRACT" in launches.prompts[1]
        assert "CANNOT_DETERMINE is a real score" in launches.prompts[1]
        for cid in CRITERION_IDS:
            assert cid in launches.prompts[1]
        assert code == 0
        assert persisted(tmp_path)["state"] == "READY_FOR_ACCEPTANCE_COMMIT"
        assert "ACCEPTANCE RECORD" in captured

    def test_exactly_one_adjudication_requirement_is_discharged(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        config = self._standing(tmp_path)
        launches.responses = [
            StubReview(verdict="INSUFFICIENT_EVIDENCE", criteria_assessment=[]),
            complete_verdict(),
        ]
        assert close(config) == 0

        record = persisted(tmp_path)
        assert record["adjudication"]["protocol"]["complete"] is True
        assert record["adjudication"]["attempt"] == 2
        assert len(record["adjudication_history"]) == 1

        # And no second opinion is bought afterwards.
        assert close(config) == 0
        assert len(launches.reviewers) == 1

    def test_the_malformed_response_artifact_is_preserved(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        """Requirement 7: a replacement may not destroy what was really returned."""
        config = self._standing(tmp_path)
        launches.responses = [
            StubReview(
                verdict="INSUFFICIENT_EVIDENCE",
                summary="Reviewer session errored: error_max_turns",
                criteria_assessment=[],
            ),
            complete_verdict(),
        ]
        assert close(config) == 0

        assert adjudication_files(tmp_path) == [
            "phase-adjudication-02.json",
            "phase-adjudication.json",
        ]
        superseded = adjudication_json(tmp_path, "phase-adjudication-02.json")
        assert superseded["criteria_assessment"] == []
        assert "error_max_turns" in superseded["summary"]
        current = adjudication_json(tmp_path, "phase-adjudication.json")
        assert len(current["criteria_assessment"]) == len(CRITERION_IDS)

        history = persisted(tmp_path)["adjudication_history"]
        assert history[0]["criterion_results"] == []
        assert history[0]["superseded_by"]

    def test_a_still_malformed_correction_fails_closed_and_owes_an_adjudication(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        config = self._standing(tmp_path)
        launches.review = StubReview(  # type: ignore[attr-defined]
            verdict="INSUFFICIENT_EVIDENCE", criteria_assessment=[]
        )
        capsys.readouterr()

        code = close(config)
        captured = capsys.readouterr().out

        assert len(launches.reviewers) == 1, "one session, and one correction inside it"
        assert len(launches.prompts) == 2, "bounded: never a retry loop"
        assert code == 22
        record = persisted(tmp_path)
        assert record["state"] == "READY_FOR_ADJUDICATION"
        assert "ACCEPTANCE RECORD" not in captured
        assert "Product Driver / reviewer-protocol failure" in captured
        assert "NOT a product result" in captured
        assert not any(
            f["classification"] == "PRODUCT_DEFECT" for f in record["findings"]
        )
        assert [f["repair_layer"] for f in record["findings"] if "PROTOCOL" in f["finding_id"]] == [
            "PRODUCT_DRIVER",
            "PRODUCT_DRIVER",
        ]

    def test_the_question_each_attempt_was_asked_is_kept_too(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        """A malformed answer can only be attributed with the question beside it."""
        config = self._standing(tmp_path)
        launches.responses = [
            StubReview(verdict="INSUFFICIENT_EVIDENCE", criteria_assessment=[]),
            complete_verdict(),
        ]
        assert close(config) == 0

        run_dir = EvidenceStore(tmp_path / "runs", RUN_ID).run_dir
        prompts = sorted(p.name for p in run_dir.glob("phase-adjudication*prompt*.md"))
        assert prompts == [
            "phase-adjudication-correction-prompt.md",
            "phase-adjudication-prompt.md",
        ]
        corrective = (run_dir / "phase-adjudication-correction-prompt.md").read_text(
            encoding="utf-8"
        )
        assert "DID NOT COVER THE ADJUDICATION CONTRACT" in corrective
        assert "CANNOT_DETERMINE is a real score" in corrective

    def test_a_session_that_has_already_ended_cannot_be_corrected_and_fails_closed(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        """The REAL p7-closure-e9f75e18 shape, exactly.

        That reviewer's reply was ``INSUFFICIENT_EVIDENCE`` with an empty
        assessment set and the summary "Reviewer session errored:
        error_max_turns" — the CLI had exited, so there was no session left to
        ask again. The corrective request must therefore fail, and failing it
        must not produce a verdict about the product: the adjudication is owed,
        the owner is Product Driver, and the next close launches a fresh one.
        """
        config = self._standing(tmp_path)
        launches.responses = [
            StubReview(
                verdict="INSUFFICIENT_EVIDENCE",
                summary="Reviewer session errored: error_max_turns",
                criteria_assessment=[],
            ),
            RuntimeError("the reviewer session has ended"),
        ]
        capsys.readouterr()

        code = close(config)
        captured = capsys.readouterr().out

        assert len(launches.prompts) == 2, "it tried exactly once, and no more"
        assert "the corrective request could not be made" in captured
        assert code == 22
        record = persisted(tmp_path)
        assert record["state"] == "READY_FOR_ADJUDICATION"
        assert not any(f["classification"] == "PRODUCT_DEFECT" for f in record["findings"])
        violation = [f for f in record["findings"] if "PROTOCOL-VIOLATION" in f["finding_id"]]
        assert len(violation) == 1, "one response, one protocol failure"
        assert "error_max_turns" in violation[0]["summary"], (
            "and it says what the session itself reported, so the repair is attributable"
        )
        assert record["adjudication_history"] == [], "nothing superseded it; it stands alone"
        assert adjudication_files(tmp_path) == ["phase-adjudication.json"]

        # The phase is not over. A later close buys the fresh session it is owed.
        launches.responses = [complete_verdict()]
        assert close(config) == 0
        assert len(launches.reviewers) == 2
        assert persisted(tmp_path)["state"] == "READY_FOR_ACCEPTANCE_COMMIT"

    def test_both_malformed_responses_are_kept(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        config = self._standing(tmp_path)
        launches.review = StubReview(  # type: ignore[attr-defined]
            verdict="INSUFFICIENT_EVIDENCE", criteria_assessment=[]
        )
        close(config)
        assert adjudication_files(tmp_path) == [
            "phase-adjudication-02.json",
            "phase-adjudication.json",
        ]

    def test_the_next_close_launches_a_genuinely_fresh_reviewer(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        """Requirement 8: a spent malformed response does not end the phase."""
        config = self._standing(tmp_path)
        launches.review = StubReview(  # type: ignore[attr-defined]
            verdict="INSUFFICIENT_EVIDENCE", criteria_assessment=[]
        )
        assert close(config) == 22
        assert len(launches.reviewers) == 1

        launches.responses = [complete_verdict()]
        assert close(config) == 0
        assert len(launches.reviewers) == 2, "a fresh session, because none was ever taken"
        assert persisted(tmp_path)["state"] == "READY_FOR_ACCEPTANCE_COMMIT"
        assert len(persisted(tmp_path)["adjudication_history"]) == 2
        assert adjudication_files(tmp_path) == [
            "phase-adjudication-02.json",
            "phase-adjudication-03.json",
            "phase-adjudication.json",
        ]
        # And the question each of those attempts was asked, superseded the same
        # way rather than replaced.
        run_dir = EvidenceStore(tmp_path / "runs", RUN_ID).run_dir
        assert "phase-adjudication-prompt-02.md" in {p.name for p in run_dir.glob("*.md")}


class TestTheAdjudicatorGetsItsOwnTurnBudget:
    """A whole-phase adjudication is not an ordinary change review.

    THE DEFECT: the session was constructed without ``max_turns``, so it took
    ``IndependentReviewerSession``'s default of 40 — the ordinary reviewer's
    budget. The real p7-closure-e9f75e18 adjudicator spent them on a
    17-criterion phase, ended ``error_max_turns`` and returned an empty
    ``criteria_assessment``: an independent session spent for no answer. The
    protocol repair makes that recoverable; it does not make the budget
    sufficient, and re-running with 40 can reproduce it exactly.
    """

    def _closed(self, tmp_path: Path, launches: Launches, **closure: Any) -> Path:
        repo = closure_repo(tmp_path, unevidenced_criteria())
        config = config_file(tmp_path, repo, **closure)
        preflight(config)
        supply_external(config, head(repo))
        close(config)
        return config

    def test_the_session_is_given_the_configured_budget(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        self._closed(tmp_path, launches, adjudication_max_turns=150)
        assert len(launches.reviewers) == 1
        assert launches.reviewers[0]["max_turns"] == 150

    def test_it_does_not_silently_take_the_ordinary_reviewer_default(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        """With nothing configured it must still not be 40."""
        self._closed(tmp_path, launches)
        passed = launches.reviewers[0]
        assert "max_turns" in passed, "the budget must be passed explicitly, not inherited"
        assert passed["max_turns"] == 200 == PhaseClosureConfig().adjudication_max_turns
        assert passed["max_turns"] != 40
        # And 40 is still what an unconfigured session would have taken, which is
        # the whole reason this has to be passed.
        assert launches.real_session(tmp_path).max_turns == 40  # type: ignore[attr-defined]

    def test_the_budget_reaches_the_session_and_its_options(self, tmp_path: Path) -> None:
        """Passing it is only half; the SDK must actually be given it."""
        from neyma_product_driver.reviewer import IndependentReviewerSession

        session = IndependentReviewerSession(tmp_path, max_turns=200)
        assert session.max_turns == 200
        assert session._options().max_turns == 200

    def test_the_founder_is_told_the_budget_before_it_is_spent(
        self, tmp_path: Path, launches: Launches, capsys: pytest.CaptureFixture
    ) -> None:
        repo = closure_repo(tmp_path, unevidenced_criteria())
        config = config_file(tmp_path, repo, adjudication_max_turns=175)
        preflight(config)
        supply_external(config, head(repo))
        capsys.readouterr()
        close(config)
        captured = capsys.readouterr().out
        assert "turn budget:  175" in captured
        assert "adjudication_max_turns" in captured

    def test_ordinary_independent_review_is_unchanged(self, tmp_path: Path) -> None:
        """This budget belongs to phase closure only.

        No shared configuration owns both, and inventing one would change the
        cost of every ordinary change review to fix a phase-closure defect.
        """
        from neyma_product_driver.reviewer import IndependentReviewerSession

        assert IndependentReviewerSession(tmp_path).max_turns == 40
        assert IndependentReviewerSession(tmp_path)._options().max_turns == 40
        assert not hasattr(ReviewPolicyConfig(), "max_turns")
        assert not hasattr(ReviewPolicyConfig(), "reviewer_max_turns")

    def test_a_budget_below_the_observed_failure_is_refused(self) -> None:
        for value in (0, -1, 1, 39):
            with pytest.raises(ValidationError):
                PhaseClosureConfig(adjudication_max_turns=value)

    def test_the_budget_stays_bounded_above(self) -> None:
        PhaseClosureConfig(adjudication_max_turns=1000)
        with pytest.raises(ValidationError):
            PhaseClosureConfig(adjudication_max_turns=1001)

    def test_there_is_no_way_to_ask_for_an_unlimited_adjudicator(self) -> None:
        with pytest.raises(ValidationError):
            PhaseClosureConfig(adjudication_max_turns=None)

    def test_a_configured_budget_survives_the_config_file(self, tmp_path: Path) -> None:
        repo = closure_repo(tmp_path)
        path = config_file(tmp_path, repo, adjudication_max_turns=123)
        assert load_config(path).phase_closure.adjudication_max_turns == 123


class TestACompleteButUncertainAdjudicationStops(object):
    """Protocol-complete uncertainty: taken, and it buys nothing more."""

    def _all_undetermined(self, tmp_path: Path, launches: Launches) -> Path:
        repo = closure_repo(tmp_path, unevidenced_criteria())
        config = config_file(tmp_path, repo)
        preflight(config)
        supply_external(config, head(repo))
        launches.review = StubReview(  # type: ignore[attr-defined]
            verdict="INSUFFICIENT_EVIDENCE",
            criteria_assessment=[
                FakeAssessment(cid, "CANNOT_DETERMINE", "nothing here shows it")
                for cid in CRITERION_IDS
            ],
        )
        return config

    def test_no_corrective_request_is_made(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        config = self._all_undetermined(tmp_path, launches)
        code = close(config)
        assert len(launches.prompts) == 1, "a complete answer is never corrected"
        assert len(launches.reviewers) == 1
        assert code == 13  # BLOCKED: the evidence is insufficient, and that is an answer
        assert persisted(tmp_path)["state"] == "BLOCKED"

    def test_a_second_close_does_not_buy_another_reviewer(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        config = self._all_undetermined(tmp_path, launches)
        close(config)
        close(config)
        assert len(launches.reviewers) == 1, "uncertainty is not a reason to re-adjudicate"

    def test_it_is_recorded_as_a_verification_gap_not_a_protocol_failure(
        self, tmp_path: Path, launches: Launches
    ) -> None:
        config = self._all_undetermined(tmp_path, launches)
        close(config)
        findings = persisted(tmp_path)["findings"]
        assert any(f["finding_id"].endswith("ADJUDICATION-INCOMPLETE") for f in findings)
        assert not any("PROTOCOL-VIOLATION" in f["finding_id"] for f in findings)
        assert not any(f["classification"] == "PRODUCT_DEFECT" for f in findings)


"""The ``phase`` commands, end to end, without spending a session.

These are the operator's surface: what a founder types and what comes back. The
things worth asserting are the ones a script and a person both depend on — the
exit code per resting place, that a bare preflight writes nothing, and that the
external-evidence command refuses the wrong commit at the command level and not
only in the model.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.cli import build_parser, main
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.phase_closure import CLOSURE_FILE

from phase_fixtures import head, phase_repo, supporting_review


def config_file(tmp_path: Path, repo: Path, **closure) -> Path:
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


def run(argv: list[str]) -> int:
    return main(argv)


class TestPreflight:
    def test_a_bare_preflight_reports_and_exits_for_its_state(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo = phase_repo(tmp_path)
        code = run(["phase", "preflight", "--config", str(config_file(tmp_path, repo)), "--phase", "P9"])
        captured = capsys.readouterr().out
        assert "PHASE ACCEPTANCE PREFLIGHT" in captured
        assert "REQUIRED CRITERIA:             5" in captured
        # Waiting for external verification, which is a resting place.
        assert code == 11

    def test_a_bare_preflight_writes_nothing_into_someone_elses_run(
        self, tmp_path: Path
    ) -> None:
        """Answering a question must not leave a record in the latest run."""
        repo = phase_repo(tmp_path)
        store = EvidenceStore(tmp_path / "runs", "20260909-000000")
        store.write_json("state.json", {"run_id": store.run_id, "task": "x"})
        run(["phase", "preflight", "--config", str(config_file(tmp_path, repo)), "--phase", "P9"])
        assert not (store.run_dir / CLOSURE_FILE).exists()

    def test_a_named_run_is_written_to(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        store = EvidenceStore(tmp_path / "runs", "20260909-000000")
        run(
            [
                "phase",
                "preflight",
                "--config",
                str(config_file(tmp_path, repo)),
                "--phase",
                "P9",
                "--run",
                store.run_id,
            ]
        )
        assert (store.run_dir / CLOSURE_FILE).exists()

    def test_an_authority_gap_exits_with_its_own_code_and_says_why(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo = phase_repo(tmp_path, with_criteria=False)
        code = run(["phase", "preflight", "--config", str(config_file(tmp_path, repo)), "--phase", "P9"])
        captured = capsys.readouterr().out
        assert code == 14
        assert "AUTHORITY GAP — STOPPED" in captured
        assert "Product Driver does not write acceptance criteria" in captured


class TestExternalEvidence:
    def _prepared(self, tmp_path: Path) -> tuple[Path, Path, EvidenceStore]:
        repo = phase_repo(tmp_path)
        config = config_file(tmp_path, repo)
        store = EvidenceStore(tmp_path / "runs", "20260909-000000")
        run(["phase", "preflight", "--config", str(config), "--phase", "P9", "--run", store.run_id])
        return repo, config, store

    def test_evidence_for_the_wrong_commit_is_refused_at_the_command_level(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo, config, store = self._prepared(tmp_path)
        code = run(
            [
                "phase",
                "external-evidence",
                "--config",
                str(config),
                "--run",
                store.run_id,
                "--sha",
                "0" * 40,
                "--status",
                "SUCCESS",
            ]
        )
        captured = capsys.readouterr().out
        assert code == 11
        assert "REFUSED" in captured
        assert "a different tree" in captured

    def test_evidence_for_the_right_commit_advances_the_attempt(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo, config, store = self._prepared(tmp_path)
        code = run(
            [
                "phase",
                "external-evidence",
                "--config",
                str(config),
                "--run",
                store.run_id,
                "--sha",
                head(repo),
                "--status",
                "SUCCESS",
            ]
        )
        captured = capsys.readouterr().out
        assert "accepted" in captured
        # Now READY_FOR_ADJUDICATION.
        assert code == 10

    def test_a_json_record_is_accepted(self, tmp_path: Path) -> None:
        repo, config, store = self._prepared(tmp_path)
        record = tmp_path / "ci.json"
        record.write_text(
            json.dumps({"head_sha": head(repo), "status": "completed", "conclusion": "success"}),
            encoding="utf-8",
        )
        code = run(
            [
                "phase",
                "external-evidence",
                "--config",
                str(config),
                "--run",
                store.run_id,
                "--file",
                str(record),
            ]
        )
        assert code == 10

    def test_supplying_nothing_is_an_error_rather_than_a_pass(
        self, tmp_path: Path
    ) -> None:
        _repo, config, store = self._prepared(tmp_path)
        assert (
            run(["phase", "external-evidence", "--config", str(config), "--run", store.run_id])
            == 2
        )


class TestClose:
    def test_analysis_mode_launches_nothing_and_reports(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo = phase_repo(tmp_path)
        code = run(
            [
                "phase",
                "close",
                "--config",
                str(config_file(tmp_path, repo)),
                "--phase",
                "P9",
                "--analysis",
            ]
        )
        captured = capsys.readouterr().out
        assert "PHASE LEDGER" in captured
        assert code == 11

    def test_no_adjudication_stops_at_ready_for_adjudication(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo = phase_repo(tmp_path)
        config = config_file(tmp_path, repo)
        store = EvidenceStore(tmp_path / "runs", "20260909-000000")
        run(["phase", "preflight", "--config", str(config), "--phase", "P9", "--run", store.run_id])
        run(
            [
                "phase",
                "external-evidence",
                "--config",
                str(config),
                "--run",
                store.run_id,
                "--sha",
                head(repo),
                "--status",
                "SUCCESS",
            ]
        )
        capsys.readouterr()
        code = run(
            [
                "phase",
                "close",
                "--config",
                str(config),
                "--phase",
                "P9",
                "--run",
                store.run_id,
                "--no-adjudication",
            ]
        )
        assert code == 10
        assert "READY_FOR_ADJUDICATION" in capsys.readouterr().out


class TestLedger:
    def test_it_refuses_when_no_attempt_was_recorded(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo = phase_repo(tmp_path)
        store = EvidenceStore(tmp_path / "runs", "20260909-000000")
        code = run(
            ["phase", "ledger", "--config", str(config_file(tmp_path, repo)), "--run", store.run_id]
        )
        assert code == 2
        assert "no phase-closure attempt" in capsys.readouterr().out

    def test_it_prints_json_when_asked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo = phase_repo(tmp_path)
        config = config_file(tmp_path, repo)
        store = EvidenceStore(tmp_path / "runs", "20260909-000000")
        run(["phase", "preflight", "--config", str(config), "--phase", "P9", "--run", store.run_id])
        capsys.readouterr()
        run(["phase", "ledger", "--config", str(config), "--run", store.run_id, "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["phase_id"] == "P9"
        assert payload["ledger"]["criteria_required"] == 5


class TestTheExistingSurfaceIsUnchanged:
    def test_every_previous_command_still_parses(self) -> None:
        parser = build_parser()
        for command in (
            ["run", "--task", "x"],
            ["doctor"],
            ["status"],
            ["stop"],
            ["audit"],
            ["protocol"],
            ["investigate"],
            ["review"],
            ["feedback", "--message", "m"],
            ["promote-feedback"],
            ["calibrate"],
            ["evaluate"],
            ["scenarios", "plan"],
        ):
            assert parser.parse_args(command) is not None

    def test_run_still_accepts_its_existing_flags(self) -> None:
        args = build_parser().parse_args(
            ["run", "--task", "t", "--resume-run", "r", "--no-auto-review", "--no-auto-scenarios"]
        )
        assert args.resume_run == "r"
        assert args.no_auto_review and args.no_auto_scenarios


class TestDoctorReportsWherePhaseAcceptanceStands:
    """The two facts an operator needs before starting a closure, read-only."""

    def test_it_warns_when_the_phase_states_no_criteria(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo = phase_repo(tmp_path, with_criteria=False)
        run(["doctor", "--config", str(config_file(tmp_path, repo))])
        captured = capsys.readouterr().out
        assert "Phase closure" in captured
        assert "AUTHORITY GAP" in captured

    def test_it_reports_the_frozen_bar_when_one_exists(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo = phase_repo(tmp_path)
        run(["doctor", "--config", str(config_file(tmp_path, repo))])
        captured = capsys.readouterr().out
        assert "5 required criterion/criteria" in captured
        assert "WAITING_FOR_EXTERNAL_VERIFICATION" in captured

    def test_it_says_a_configured_probe_will_answer_the_gate(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo = phase_repo(tmp_path)
        config = config_file(
            tmp_path, repo, external_probe_command="curl -s https://ci/{sha}"
        )
        run(["doctor", "--config", str(config)])
        captured = capsys.readouterr().out
        assert "a configured read-only probe" in captured

    def test_doctor_never_fails_over_a_phase_diagnostic(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path, with_criteria=False)
        (repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").write_text(
            "{not: [valid", encoding="utf-8"
        )
        assert run(["doctor", "--config", str(config_file(tmp_path, repo))]) in (0, 1)


class TestTheAcceptanceRecordAtTheCommandLevel:
    """The last step before the founder, driven the way a founder drives it.

    The driver does NOT make this commit, and that is not a choice made in the
    phase-closure code: ``DriverConfig`` refuses ``allow_auto_commit`` outright,
    so the control process never commits or pushes. What is worth automating —
    and what is tested here — is classifying the change, refusing anything
    outside the acceptance record, and printing the exact commit.
    """

    def _ready(self, tmp_path: Path, **closure) -> tuple[Path, Path, EvidenceStore]:
        repo = phase_repo(tmp_path)
        config = config_file(tmp_path, repo, **closure)
        store = EvidenceStore(tmp_path / "runs", "20260909-000000")
        run(["phase", "preflight", "--config", str(config), "--phase", "P9", "--run", store.run_id])
        run(
            [
                "phase",
                "external-evidence",
                "--config",
                str(config),
                "--run",
                store.run_id,
                "--sha",
                head(repo),
                "--status",
                "SUCCESS",
            ]
        )
        self._adjudicate(repo, store)
        return repo, config, store

    def _adjudicate(self, repo: Path, store: EvidenceStore) -> None:
        """Record a supporting adjudication without launching a session."""
        from neyma_product_driver.phase_closure import PhaseClosureController
        from neyma_product_driver.review_cycle import capture_fingerprint

        control = PhaseClosureController(repo, store=store, phase_id="P9")
        control.load()
        control.ingest_review(
            supporting_review(
                ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"],
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        control.decide()
        control.save()

    def _close(self, config: Path, store: EvidenceStore) -> int:
        return run(
            [
                "phase",
                "close",
                "--config",
                str(config),
                "--phase",
                "P9",
                "--run",
                store.run_id,
                "--no-adjudication",
            ]
        )

    def test_a_runtime_edit_beside_the_record_stops_the_phase_before_the_record(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Stronger than refusing the commit: the phase stops being ready.

        A runtime change is a change to the product being accepted, so the CI
        record and the adjudication stop describing it. The acceptance-record
        step is never reached, and printing one would be describing a tree
        nothing has verified.
        """
        repo, config, store = self._ready(tmp_path)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        (repo / "src" / "product.py").write_text("VALUE = 2\n", encoding="utf-8")
        capsys.readouterr()
        code = self._close(config, store)
        captured = capsys.readouterr().out
        assert "ACCEPTANCE RECORD" not in captured
        assert "git -C" not in captured
        assert code != 0

    def test_a_clean_record_prints_the_exact_commit_and_stages_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        repo, config, store = self._ready(tmp_path)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text(
            "P9 accepted\n", encoding="utf-8"
        )
        capsys.readouterr()
        self._close(config, store)
        captured = capsys.readouterr().out
        assert "Product Driver does not commit in the product repository" in captured
        assert "git -C" in captured and "docs/implementation/CURRENT.md" in captured
        assert "Publishing is your action" in captured
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert staged == ""

    def test_no_commit_is_made(self, tmp_path: Path) -> None:
        repo, config, store = self._ready(tmp_path)
        before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        self._close(config, store)
        after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        assert before == after

    def test_the_driver_config_refuses_to_be_told_to_commit(self, tmp_path: Path) -> None:
        """The policy this respects, asserted where the phase code can see it."""
        from neyma_product_driver.config import DriverConfig

        repo = phase_repo(tmp_path)
        with pytest.raises(ValueError, match="never commits or pushes"):
            DriverConfig(neyma_repo=repo, task="t", allow_auto_commit=True)
        with pytest.raises(ValueError, match="never commits or pushes"):
            DriverConfig(neyma_repo=repo, task="t", allow_auto_push=True)

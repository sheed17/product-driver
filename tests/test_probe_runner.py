"""Probe execution safety and evidence collection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from neyma_product_driver.investigation_memory import (
    InterpretationRule,
    ObservationKind,
    Probe,
    Reliability,
)
from neyma_product_driver.probe_runner import (
    EvidenceCollector,
    ProbeRunner,
    clear_predicates,
    is_consequential,
    register_predicate,
)

from investigation_fixtures import MiniRepo


@pytest.fixture(autouse=True)
def _clean_predicates():
    clear_predicates()
    yield
    clear_predicates()


# --------------------------------------------------------------------------
# Safety: nothing consequential runs autonomously
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        "git reset --hard HEAD~1",
        "git push origin main",
        "git push --force",
        "git rebase -i HEAD~2",
        "git commit --amend",
        "git update-ref refs/heads/main abc",
        "rm -rf build",
        "curl -X POST http://api/write",
        "UPDATE loads SET status='paid'",
    ],
)
def test_consequential_actions_are_recognized(action: str) -> None:
    assert is_consequential(action)


def test_read_only_actions_are_not_consequential() -> None:
    for action in ("git status", "git log --oneline", "pytest eval/test_x.py", "git rev-parse HEAD"):
        assert not is_consequential(action)


@pytest.mark.parametrize(
    "cmd",
    [
        "git checkout feature",
        "git switch feature",
        "git rm a.txt",
        "git config user.name evil",
        "git apply patch.diff",
        "git mv a b",
        "git worktree add /tmp/x",
        "truncate -s0 a.txt",
        "tee a.txt",
        "chmod 777 a.txt",
    ],
)
def test_a_command_probe_allowlists_and_refuses_mutating_tools(tmp_path: Path, cmd: str) -> None:
    """A denylist misses verbs; the runner allowlists observation and refuses the rest."""
    repo = MiniRepo(tmp_path / "repo")
    repo.write("a.txt", "one\n")
    repo.commit("init")
    repo.write("a.txt", "two\n")
    repo.commit("second")
    repo._git("branch", "feature", "HEAD~1")
    before = (repo._git("branch", "--show-current"), repo._git("rev-parse", "HEAD"), (tmp_path / "repo" / "a.txt").read_text())

    result = ProbeRunner(repo.root).run(Probe(id="p", question="q", kind="COMMAND", command_or_action=cmd))

    assert result.refused
    assert not result.ran
    # And the repository is entirely unchanged.
    after = (repo._git("branch", "--show-current"), repo._git("rev-parse", "HEAD"), (tmp_path / "repo" / "a.txt").read_text())
    assert before == after


def test_read_only_git_and_tools_are_allowed(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.write("a.txt", "hello\n")
    repo.commit("init")
    runner = ProbeRunner(repo.root)
    for cmd in ("git log --oneline", "git status", "git rev-parse HEAD", "cat a.txt"):
        assert not runner.run(Probe(id="p", question="q", kind="COMMAND", command_or_action=cmd)).refused


def test_a_consequential_probe_is_refused_not_run(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    runner = ProbeRunner(repo.root)

    probe = Probe(id="p1", question="reset?", kind="COMMAND", command_or_action="git reset --hard HEAD")
    result = runner.run(probe)

    assert result.refused
    assert not result.ran
    assert "consequential" in result.refusal_reason


def test_a_probe_marked_not_read_only_is_refused(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    probe = Probe(id="p1", question="q", kind="COMMAND", command_or_action="git status", read_only=False)
    assert ProbeRunner(repo.root).run(probe).refused


def test_shell_metacharacters_are_refused(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    probe = Probe(id="p1", question="q", kind="COMMAND", command_or_action="git status; rm -rf x")
    result = ProbeRunner(repo.root).run(probe)
    # Either the consequential 'rm -rf' guard or the metacharacter guard refuses it.
    assert result.refused


# --------------------------------------------------------------------------
# Probe kinds
# --------------------------------------------------------------------------


def test_a_command_probe_captures_exit_code_and_output(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    probe = Probe(id="p1", question="head?", kind="COMMAND", command_or_action="git rev-parse HEAD")
    result = ProbeRunner(repo.root).run(probe)
    assert result.ran
    assert result.signals["exit_code"] == "0"


def test_interpretation_rules_extract_signals_from_output(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.write("out.txt", "3 passed, 1 failed in 0.10s\n")
    repo.commit("init")
    probe = Probe(
        id="p1", question="test counts?", kind="COMMAND",
        command_or_action="git show HEAD:out.txt",
        interpretation_rules=[
            InterpretationRule(pattern=r"(\d+) failed", signal="failed", value="$1"),
            InterpretationRule(pattern=r"(\d+) passed", signal="passed", value="$1"),
        ],
    )
    result = ProbeRunner(repo.root).run(probe)
    assert result.signals["failed"] == "1"
    assert result.signals["passed"] == "3"


def test_a_predicate_probe_runs_a_registered_pure_function(tmp_path: Path) -> None:
    register_predicate("check", lambda inputs: {"ok": "true", "n": str(inputs.get("n", 0))})
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    probe = Probe(id="p1", question="q", kind="PREDICATE", command_or_action="check", inputs={"n": 5})
    result = ProbeRunner(repo.root).run(probe)
    assert result.signals == {"ok": "true", "n": "5"}


def test_a_predicate_probe_with_no_registration_errors_cleanly(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    probe = Probe(id="p1", question="q", kind="PREDICATE", command_or_action="missing")
    result = ProbeRunner(repo.root).run(probe)
    assert not result.ran
    assert "no registered predicate" in result.error


def test_a_sql_probe_refuses_a_write(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE loads (id TEXT, paid INT)")
    con.execute("INSERT INTO loads VALUES ('L1', 0)")
    con.commit()
    con.close()

    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    runner = ProbeRunner(repo.root)

    write = Probe(id="w", question="q", kind="SQL_READONLY", command_or_action="DELETE FROM loads", inputs={"db": str(db)})
    assert runner.run(write).refused

    read = Probe(id="r", question="q", kind="SQL_READONLY", command_or_action="SELECT paid FROM loads WHERE id='L1'", inputs={"db": str(db)})
    result = runner.run(read)
    assert result.ran
    assert result.signals["value"] == "0"


@pytest.mark.parametrize("kind", ["FILE_STAT", "CHECK_RECEIPT", "GREP"])
def test_a_probe_path_that_escapes_the_repository_is_refused(tmp_path: Path, kind: str) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    probe = Probe(
        id="p1", question="q", kind=kind, command_or_action="/etc/passwd",
        inputs={"path": "/etc/passwd", "pattern": "root"},
    )
    result = ProbeRunner(repo.root).run(probe)
    assert result.refused
    assert "escapes the repository" in result.refusal_reason


def test_a_relative_traversal_out_of_the_repository_is_refused(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    (tmp_path / "secret.txt").write_text("outside")
    probe = Probe(id="p1", question="q", kind="FILE_STAT", inputs={"path": "../secret.txt"})
    assert ProbeRunner(repo.root).run(probe).refused


def test_an_http_probe_refuses_a_non_local_url(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    probe = Probe(id="p1", question="q", kind="HTTP_GET", command_or_action="https://example.com/data")
    assert ProbeRunner(repo.root).run(probe).refused


def test_a_probe_never_crashes_the_runner(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("boom", lambda inputs: (_ for _ in ()).throw(RuntimeError("kaboom")))
    probe = Probe(id="p1", question="q", kind="PREDICATE", command_or_action="boom")
    result = ProbeRunner(repo.root).run(probe)
    assert "kaboom" in result.error


# --------------------------------------------------------------------------
# Evidence collection
# --------------------------------------------------------------------------


def test_the_collector_records_git_state_as_direct_evidence(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.write("a.py", "x = 1\n")
    repo.commit("init")

    obs = EvidenceCollector(repo.root).collect()
    git = [o for o in obs if o.kind is ObservationKind.GIT_STATE]
    assert git
    assert git[0].reliability is Reliability.DIRECT
    assert "head" in git[0].signals


def test_the_collector_records_a_builder_claim_as_reported(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")

    obs = EvidenceCollector(repo.root).collect(builder_report="P3 is COMPLETE, socket blocks the finalizer")
    claims = [o for o in obs if o.kind is ObservationKind.BUILDER_CLAIM]
    assert claims
    assert claims[0].reliability is Reliability.REPORTED  # prose is never a fact


def test_the_collector_reads_a_failing_suite_receipt(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    repo.write_suite_receipt(failed=1, exit_status=1, nodes=["eval/test_status_reality.py::test_topology"])

    obs = EvidenceCollector(repo.root).collect()
    suite = [o for o in obs if "suite_green" in o.signals]
    assert suite and suite[0].signals["suite_green"] == "false"
    nodes = [o for o in obs if "failing_node" in o.signals]
    assert nodes


def test_the_collector_reports_env_vars_by_presence_never_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "sk-ant-shouldnotappear12345")
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")

    obs = EvidenceCollector(repo.root).environment(["SECRET_TOKEN", "MISSING_VAR"])
    assert obs.signals["env_SECRET_TOKEN"] == "set"
    assert obs.signals["env_MISSING_VAR"] == "unset"
    assert "sk-ant-shouldnotappear12345" not in obs.content

"""Evidence persistence, redaction on write, state round-trips, and recovery."""

from __future__ import annotations

import json
from pathlib import Path

from neyma_product_driver.evidence import (
    EvidenceStore,
    check_writable,
    new_run_id,
    sanitize_filename,
)
from neyma_product_driver.models import (
    CommandResult,
    Decision,
    EvaluatorDecision,
    GitSnapshot,
    IterationRecord,
    RunState,
    RunStatus,
    ScenarioResult,
)


def _store(tmp_path: Path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "runs", "20260721-120000")


def test_run_directory_is_created(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.run_dir.is_dir()


def test_state_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    state = RunState(run_id=store.run_id, task="t", builder_session_id="sess-1", iteration=2)
    store.save_state(state)

    loaded = store.load_state()
    assert loaded is not None
    assert loaded.builder_session_id == "sess-1"
    assert loaded.iteration == 2
    assert loaded.task == "t"


def test_load_state_returns_none_for_a_missing_or_corrupt_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.load_state() is None

    store.state_path.write_text("{not json")
    assert store.load_state() is None

    store.state_path.write_text('{"unexpected": true}')
    assert store.load_state() is None


def test_interrupted_run_is_recoverable(tmp_path: Path) -> None:
    """A run killed mid-flight must be resumable with its session ids intact."""
    store = _store(tmp_path)
    state = RunState(run_id=store.run_id, task="t", max_iterations=5)
    state.builder_session_id = "builder-abc"
    state.evaluator_session_id = "eval-xyz"
    state.iteration = 2
    state.iterations.append(IterationRecord(iteration=1, builder_summary="did half"))
    state.status = RunStatus.RUNNING
    store.save_state(state)

    # Simulate a fresh process opening the same run directory.
    reopened = EvidenceStore.open_run(tmp_path / "runs", store.run_id)
    recovered = reopened.load_state()

    assert recovered is not None
    assert recovered.status is RunStatus.RUNNING
    assert recovered.builder_session_id == "builder-abc"
    assert recovered.evaluator_session_id == "eval-xyz"
    assert recovered.iteration == 2
    assert len(recovered.iterations) == 1


def test_latest_run_picks_the_most_recent(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    assert EvidenceStore.latest_run(runs) is None

    first = EvidenceStore(runs, "20260101-000000")
    first.save_state(RunState(run_id=first.run_id))
    second = EvidenceStore(runs, "20260721-235959")
    second.save_state(RunState(run_id=second.run_id))

    latest = EvidenceStore.latest_run(runs)
    assert latest is not None and latest.run_id == "20260721-235959"


def test_stop_sentinel(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert not store.stop_requested()
    store.request_stop("because")
    assert store.stop_requested()
    store.clear_stop()
    assert not store.stop_requested()


def test_secrets_are_redacted_on_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.write_text("leak.txt", "key sk-ant-api03-AAAABBBBCCCCDDDDEEEE here")
    content = path.read_text()
    assert "sk-ant-api03" not in content
    assert "REDACTED" in content


def test_environment_dumps_are_not_persisted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dump = "\n".join(f"SOME_VAR_{i}=value{i}" for i in range(30))
    path = store.write_text("env.txt", dump)
    assert "SOME_VAR_5" not in path.read_text()
    assert "REDACTED" in path.read_text()


def test_json_writes_mask_secret_keys(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.write_json("payload.json", {"api_key": "abc123", "load": "LD1"})
    data = json.loads(path.read_text())
    assert data["api_key"] == "[REDACTED]"
    assert data["load"] == "LD1"


def test_large_blobs_are_truncated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.write_text("big.log", "x" * 900_000)
    assert "truncated" in path.read_text()
    assert len(path.read_text()) < 900_000


def test_iteration_persistence_writes_the_expected_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = IterationRecord(
        iteration=1,
        builder_session_id="s1",
        builder_summary="I built it. RUNNABLE CHECKPOINT: run the thing.",
        git=GitSnapshot(branch="p3/x", status_porcelain=" M a.py", diff_stat="1 file changed"),
        scenario=ScenarioResult(
            scenario_name="s",
            commands=[CommandResult(command="echo hi", exit_code=0, stdout="hi")],
        ),
        decision=EvaluatorDecision(decision=Decision.FIX, correction_prompt="fix it"),
        correction_prompt_sent="fix it please",
    )
    d = store.save_iteration(record)

    for name in (
        "record.json",
        "builder-summary.md",
        "git-status.txt",
        "git-diff-stat.txt",
        "scenario.json",
        "commands.log",
        "decision.json",
        "correction-prompt.md",
    ):
        assert (d / name).exists(), f"missing {name}"

    assert "echo hi" in (d / "commands.log").read_text()


def test_accepted_evidence_is_copied(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_iteration(IterationRecord(iteration=2, builder_summary="done"))
    accepted = store.save_accepted(2)
    assert (accepted / "record.json").exists()
    assert (accepted / "builder-summary.md").exists()


def test_save_accepted_replaces_a_previous_copy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_iteration(IterationRecord(iteration=1, builder_summary="first"))
    store.save_accepted(1)
    store.save_iteration(IterationRecord(iteration=2, builder_summary="second"))
    accepted = store.save_accepted(2)
    assert "second" in (accepted / "builder-summary.md").read_text()


def test_check_writable(tmp_path: Path) -> None:
    ok, detail = check_writable(tmp_path / "new" / "nested")
    assert ok and "nested" in detail


def test_new_run_id_shape() -> None:
    rid = new_run_id()
    assert len(rid) == 15 and rid[8] == "-"


def test_sanitize_filename() -> None:
    assert sanitize_filename("a b/c:d") == "a-b-c-d"
    assert sanitize_filename("///") == "unnamed"
    assert len(sanitize_filename("x" * 300)) <= 80

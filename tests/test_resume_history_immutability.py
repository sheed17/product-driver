"""A resumed run may add iterations. It may never rewrite one.

Run 20260903-065810 was resumed, and its ``iteration-01/record.json`` now holds
the RESUMED iteration: same directory, different iteration, the original gone
from disk and surviving only inside ``state.json``'s own copy of it. The cause
was not a filename. The control loop numbered its iterations
``range(1, max_iterations + 1)`` — from one, every time, resume included — so
the number an iteration was FILED under and the count of iterations an
invocation was ALLOWED were one variable. A second process therefore began by
writing over the first process's evidence: its record, its decision, its suite
result, its evaluator prompt, its per-scenario artifacts.

Everything a verification harness later reports rests on that evidence, so the
invariant is flat: historical run evidence is append-only. A resume continues at
the next unused number; an existing iteration directory is never written over;
and where the state metadata and the directories disagree — a crash between
writing evidence and saving state, a truncated state file — the answer is a new
unused number, never a reused one.

The numbering is now owned by one calculation that reads the filesystem, so it
survives a process restart:
:meth:`~neyma_product_driver.evidence.EvidenceStore.allocate_iteration`. The
loop counts its budget separately and asks the store for the number.

Two sibling paths carried the same overwrite pattern and are pinned here too:
the per-wave generation records, whose file names restart at one when a resume
begins from a plan it could not read (the very files that resume then reads the
spent budget back from), and an independent review, which is a verdict about one
exact tree from a session whose independence cannot be recovered once spent.

Every Claude session is faked. Nothing here consumes Claude usage.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from neyma_product_driver import evidence as evidence_module
from neyma_product_driver.config import DriverConfig
from neyma_product_driver.evidence import EvidenceStore, IterationAllocationError
from neyma_product_driver.models import RunState, RunStatus
from neyma_product_driver.scenario_planner import PLAN_FILENAME, WAVES_DIRNAME, ScenarioPlanner
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.cli import run_control_loop

from scenario_fixtures import FakeFounder, ScriptedReasoner, base_scenario
from test_scenario_loop import (
    FakeBuilder,
    FakeEvaluator,
    FakeRepoLoader,
    RecordingExecutor,
    accept,
    grounded_fix,
)


def fingerprint(directory: Path) -> dict[str, str]:
    """Every file under ``directory``, by relative path and content digest."""
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def iteration_names(store: EvidenceStore) -> list[str]:
    return sorted(p.name for p in store.run_dir.glob("iteration-*") if p.is_dir())


async def drive(
    config: DriverConfig,
    store: EvidenceStore,
    state: RunState,
    decisions: list,
) -> object:
    """One invocation of the control loop against an existing run."""
    return await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=FakeEvaluator(list(decisions)),
        make_executor=lambda d: RecordingExecutor(d, {}, []),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
    )


@pytest.fixture
def run_bits(driver_config: DriverConfig):
    assert driver_config.runs_dir is not None
    store = EvidenceStore(driver_config.runs_dir, "20260911-120000")
    state = RunState(
        run_id=store.run_id,
        neyma_repo=str(driver_config.neyma_repo),
        scenario_name="backend_generic",
        task="build supervised approval",
        max_iterations=driver_config.max_iterations,
    )
    return driver_config, store, state


# --------------------------------------------------------------------------
# 1-4. the run that was reproduced: two iterations, then resumes
# --------------------------------------------------------------------------


class TestAResumeAppends:
    async def test_a_resume_continues_at_the_next_number_and_rewrites_nothing(self, run_bits):
        config, store, state = run_bits
        config.max_iterations = 2

        # 1. two iterations exist, with contents.
        await drive(config, store, state, [grounded_fix(1), grounded_fix(2)])
        assert iteration_names(store) == ["iteration-01", "iteration-02"]
        before = {
            name: fingerprint(store.run_dir / name)
            for name in ("iteration-01", "iteration-02")
        }
        assert before["iteration-01"] and before["iteration-02"]

        # 2. the resume creates iteration-03.
        config.max_iterations = 1
        state.status = RunStatus.RUNNING
        await drive(config, store, state, [accept()])
        assert iteration_names(store) == [
            "iteration-01",
            "iteration-02",
            "iteration-03",
        ]

        # 3. and the first two are byte-for-byte what they were.
        for name, digests in before.items():
            assert fingerprint(store.run_dir / name) == digests, name

        # 4. a second resume creates iteration-04, and still rewrites nothing.
        state.status = RunStatus.RUNNING
        await drive(config, store, state, [accept()])
        assert iteration_names(store)[-1] == "iteration-04"
        for name, digests in before.items():
            assert fingerprint(store.run_dir / name) == digests, name
        third = fingerprint(store.run_dir / "iteration-03")

        state.status = RunStatus.RUNNING
        await drive(config, store, state, [accept()])
        assert iteration_names(store)[-1] == "iteration-05"
        assert fingerprint(store.run_dir / "iteration-03") == third

    async def test_the_record_in_each_directory_is_that_iterations_own(self, run_bits):
        """The exact corruption seen on the reproduced run: right dir, wrong iteration."""
        config, store, state = run_bits
        config.max_iterations = 1

        await drive(config, store, state, [accept()])
        state.status = RunStatus.RUNNING
        await drive(config, store, state, [accept()])

        for number in (1, 2):
            record = json.loads(
                (store.run_dir / f"iteration-{number:02d}" / "record.json").read_text()
            )
            assert record["iteration"] == number

    async def test_the_budget_is_a_count_not_a_ceiling_on_the_number(self, run_bits):
        """A resume past the budget's own number still gets its full allowance."""
        config, store, state = run_bits
        config.max_iterations = 2

        await drive(config, store, state, [grounded_fix(1), grounded_fix(2)])
        state.status = RunStatus.RUNNING
        # Numbers 3 and 4 are both beyond max_iterations=2; both must still run.
        result = await drive(config, store, state, [grounded_fix(3), grounded_fix(4)])

        assert iteration_names(store)[-1] == "iteration-04"
        assert [r.iteration for r in result.state.iterations][-2:] == [3, 4]


# --------------------------------------------------------------------------
# 5, 6. what the numbering does when it is lied to
# --------------------------------------------------------------------------


class TestDisagreementNeverDestroys:
    async def test_state_claiming_the_run_is_at_iteration_one_cannot_overwrite(self, run_bits):
        config, store, state = run_bits
        config.max_iterations = 1
        await drive(config, store, state, [accept()])
        state.status = RunStatus.RUNNING
        await drive(config, store, state, [accept()])
        before = {
            name: fingerprint(store.run_dir / name)
            for name in ("iteration-01", "iteration-02")
        }

        # Stale metadata: the state has forgotten both iterations entirely.
        state.iteration = 0
        state.iterations = []
        state.status = RunStatus.RUNNING
        await drive(config, store, state, [accept()])

        assert iteration_names(store)[-1] == "iteration-03"
        for name, digests in before.items():
            assert fingerprint(store.run_dir / name) == digests, name

    def test_the_calculation_takes_the_maximum_of_every_source(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "r")
        assert store.next_iteration() == 1

        store.iteration_dir(1)
        store.iteration_dir(2)
        assert store.next_iteration() == 3

        # A state that remembers more than the disk does still wins.
        state = RunState(run_id="r", iteration=9)
        assert store.next_iteration(state) == 10
        # And a state that remembers less loses to the disk.
        assert store.next_iteration(RunState(run_id="r", iteration=1)) == 3

    def test_a_gap_never_reuses_a_number_below_it(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "r")
        store.iteration_dir(1)
        store.iteration_dir(5)

        assert store.allocate_iteration() == 6
        assert sorted(store.existing_iterations()) == [1, 5, 6]

    def test_an_existing_destination_is_reallocated_never_replaced(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "r")
        marker = store.iteration_dir(3) / "record.json"
        marker.write_text('{"iteration": 3}', encoding="utf-8")
        # A state pointing at a number that is already taken.
        state = RunState(run_id="r", iteration=2)

        assert store.allocate_iteration(state) == 4
        assert marker.read_text(encoding="utf-8") == '{"iteration": 3}'

    def test_a_name_it_cannot_claim_is_stepped_over_never_written_through(self, tmp_path):
        """Something that is not a directory occupies the name. It survives."""
        store = EvidenceStore(tmp_path / "runs", "r")
        store.iteration_dir(1)
        squatter = store.run_dir / "iteration-02"
        squatter.write_text("not a directory", encoding="utf-8")

        assert store.allocate_iteration() == 3
        assert squatter.read_text(encoding="utf-8") == "not a directory"

    def test_allocation_refuses_rather_than_reusing_when_nothing_is_free(
        self, tmp_path, monkeypatch
    ):
        store = EvidenceStore(tmp_path / "runs", "r")
        store.iteration_dir(1)
        # Every candidate it would try is already taken by something it must
        # not touch. Refusing is the only answer that keeps evidence.
        for number in (2, 3):
            (store.run_dir / f"iteration-{number:02d}").write_text("taken", encoding="utf-8")
        monkeypatch.setattr(evidence_module, "_ALLOCATION_ATTEMPTS", 2)

        with pytest.raises(IterationAllocationError):
            store.allocate_iteration()
        assert (store.run_dir / "iteration-02").read_text(encoding="utf-8") == "taken"

    def test_repeated_allocation_is_monotonic(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "r")
        claimed = [store.allocate_iteration() for _ in range(6)]
        assert claimed == [1, 2, 3, 4, 5, 6]


# --------------------------------------------------------------------------
# 7. and a fresh run is unchanged
# --------------------------------------------------------------------------


class TestAFreshRunIsUnchanged:
    async def test_a_first_run_starts_at_iteration_01(self, run_bits):
        config, store, state = run_bits
        config.max_iterations = 3

        result = await drive(config, store, state, [accept()])

        assert iteration_names(store) == ["iteration-01"]
        assert result.state.iterations[0].iteration == 1
        assert result.status is RunStatus.ACCEPTED

    def test_a_store_with_no_iterations_says_one(self, tmp_path):
        assert EvidenceStore(tmp_path / "runs", "r").next_iteration() == 1


# --------------------------------------------------------------------------
# the same invariant, in the two sibling paths that carried it
# --------------------------------------------------------------------------


class TestSiblingSequences:
    def _planner(self, config: DriverConfig, store: EvidenceStore, payloads: list):
        config.scenario_generation = ScenarioGenerationConfig(enabled=True)
        return ScenarioPlanner(
            repo=config.neyma_repo,
            config=config.scenario_generation,
            reasoner=ScriptedReasoner(payloads),
            store=store,
            base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario()],
            founder=FakeFounder(),
        )

    def test_a_wave_record_is_never_written_over_a_surviving_one(self, run_bits):
        """The plan is unreadable; the wave files it named are all that is left."""
        config, store, state = run_bits
        first = self._planner(config, store, [{"risks": [], "scenarios": []}])
        first.plan_initial(task="build it")
        wave_one = store.run_dir / WAVES_DIRNAME / "wave-01.json"
        assert wave_one.exists()
        kept = wave_one.read_bytes()

        # The plan file is corrupted between processes; the resume preserves it
        # and starts from an empty plan, reconstructing the budget from these
        # very files.
        (store.run_dir / PLAN_FILENAME).write_text("{ not json", encoding="utf-8")
        resumed = self._planner(config, store, [{"risks": [], "scenarios": []}])
        assert resumed.restore_from_store().unreadable
        resumed.plan_initial(task="build it")

        assert wave_one.read_bytes() == kept
        assert (store.run_dir / WAVES_DIRNAME / "wave-02.json").exists()

    def test_an_ordinary_resume_keeps_writing_the_same_wave_files(self, run_bits):
        """No offset where none is owed: a readable plan owns its own files."""
        config, store, state = run_bits
        first = self._planner(config, store, [{"risks": [], "scenarios": []}])
        first.plan_initial(task="build it")

        resumed = self._planner(config, store, [{"risks": [], "scenarios": []}])
        resumed.restore_from_store()
        resumed.persist()

        waves = sorted(p.name for p in (store.run_dir / WAVES_DIRNAME).glob("wave-*.json"))
        assert waves == ["wave-01.json"]

    def test_a_second_review_of_one_tree_supersedes_without_erasing(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "r")
        store.save_independent_review(2, {"verdict": "REFUSED", "summary": "first"})
        store.save_independent_review(2, {"verdict": "SUPPORTED", "summary": "second"})

        d = store.iteration_dir(2)
        assert json.loads((d / "independent-review.json").read_text())["summary"] == "second"
        assert json.loads((d / "independent-review-02.json").read_text())["summary"] == "first"


# --------------------------------------------------------------------------
# nothing here is about one product, one phase or one run
# --------------------------------------------------------------------------


class TestTheNumberingIsGeneric:
    def test_the_allocation_code_names_no_product_phase_or_run_of_its_own(self):
        source = "".join(
            inspect.getsource(getattr(EvidenceStore, name))
            for name in ("allocate_iteration", "next_iteration", "existing_iterations")
        ).lower()
        for token in ("neyma", "p7", "20260911", "freight", "probe_phase"):
            assert token not in source, token

"""(S) An acceptance record may not leave the repository contradicting itself.

The acceptance-record writer moved the machine registry and stopped there. The
repository it was writing into declares two status authorities, not one — a
machine-readable registry of unit state, and a short-form document that restates
it for humans and may not drift from it — so the commit it prepared said the
phase was accepted in one file and not started in the file beside it. That is a
worse defect than the one it fixed: the earlier failure refused to act, and this
one acts and records a contradiction as the evidence that the phase was verified.

Two rules close it, and both are about authority rather than about documents:

* the population of status surfaces is the repository's own — the paths it names
  in configuration, or the ones its own authority map classifies as live status.
  Sweeping in every document that mentions a phase would rewrite completed
  review reports, which state what WAS true and are not status at all;
* what may be rewritten inside one is only what a machine can rewrite without
  composing a sentence. A status value in a status cell moves. A narrative claim
  does not, and a phase whose human status authority carries one is an
  ``AUTHORITY_GAP`` — never a silently stale document, and never invented prose.

And then the diff is checked against the repository's own guards over the files
it changed, because a status-only diff can still turn a repository red: a
criterion's own oracle may assert the pre-acceptance state out of the very file
the record moves.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import yaml

from neyma_product_driver import acceptance_record as ar
from neyma_product_driver.acceptance_commit import Surface, classify_surface
from neyma_product_driver.acceptance_record import (
    StatusFact,
    declared_status_surfaces,
    reconcile_restatement,
    restatement_surfaces,
)
from neyma_product_driver.external_verification import evidence_from_payload
from neyma_product_driver.phase_acceptance import ClosureState, FindingClass
from neyma_product_driver.phase_closure import PhaseClosureController
from neyma_product_driver.repo_verification import discover_record_guards
from neyma_product_driver.review_cycle import capture_fingerprint

from phase_fixtures import (
    REGISTRY_REL,
    accepted_unit,
    criterion,
    head,
    pending_unit,
    phase_repo,
    supporting_review,
)

CRITERIA = [f"AC-{index}" for index in range(1, 6)]

# --------------------------------------------------------------------------
# A repository that declares more than one status authority
# --------------------------------------------------------------------------

AUTHORITY_MAP = """\
# Canonical documents

Every document this repository keeps, and what it is allowed to decide.

| Document | Purpose | Class |
|---|---|---|
| [`implementation/STATUS.md`](implementation/STATUS.md) | The short-form status authority | **CURRENT_STATUS** |
| [`implementation/BOARD.md`](implementation/BOARD.md) | The phase board | **CURRENT_STATUS** |
| [`implementation/IMPLEMENTATION-REGISTRY.yaml`](implementation/IMPLEMENTATION-REGISTRY.yaml) | Work units and their state | **IMPLEMENTATION_CONTROL** |
| [`implementation/p9-checkpoint-review.md`](implementation/p9-checkpoint-review.md) | A completed checkpoint review | **HISTORICAL** |
| [`implementation/old-sequencing-plan.md`](implementation/old-sequencing-plan.md) | The sequencing plan it replaced | **SUPERSEDED** |
| [`product/overview.md`](product/overview.md) | What the product is | **CANONICAL** |
"""

STATUS_DOC = """\
# Status — where the program stands

| Phase | Status | Evidence |
|---|---|---|
| **P8** | **COMPLETE** | a phase review |
| **P9** | **READY** / **NOT_STARTED** / **NO_CHECKPOINT** — 0/5 **PENDING** | a phase review |
| **P10** | **BLOCKED** | — |
"""

BOARD_DOC = """\
# Board

| Unit | State |
|---|---|
| P8 | COMPLETE |
| P9 | READY |
| P10 | BLOCKED |
"""

#: Classified HISTORICAL. It says what was true when it was written, and an
#: acceptance is not entitled to edit it.
REVIEW_DOC = """\
# P9 checkpoint review

This review changes no phase status: P9 stays READY / NOT_STARTED and scores
no criterion.
"""

SUPERSEDED_DOC = """\
# The sequencing plan this replaced

P9 stays READY / NOT_STARTED until its phase review.
"""

#: The repository's own guard over the relationship the map declares.
CONSISTENCY_GUARD = '''\
"""The board and the registry state the same thing about every unit."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs/implementation/IMPLEMENTATION-REGISTRY.yaml"
BOARD = ROOT / "docs/implementation/BOARD.md"


def test_every_board_row_matches_the_registry():
    units = {u["unit_id"]: u for u in yaml.safe_load(REGISTRY.read_text())["units"]}
    for line in BOARD.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0] not in units:
            continue
        assert cells[1] == units[cells[0]]["status"], (
            f"{cells[0]}: the board says {cells[1]}, the registry says "
            f"{units[cells[0]]['status']}"
        )
'''

#: The shape that turns an acceptance record red: a criterion's own oracle
#: asserting the state the acceptance moves away from.
SHIPS_DARK_GUARD = '''\
"""A guard that pins the PRE-acceptance state out of the registry."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs/implementation/IMPLEMENTATION-REGISTRY.yaml"


def test_the_unit_is_not_recorded_as_started():
    text = REGISTRY.read_text()
    block = text[text.index("- unit_id: P9") : text.index("- unit_id: P8")]
    assert "execution_state: NOT_STARTED" in block, "P9 is recorded as started"
'''


def pending_criteria() -> list[dict]:
    return [
        criterion(f"AC-{index}", f"surface_{index}_holds", result="PENDING", evidence="")
        for index in range(1, 6)
    ]


def status_repo(
    tmp_path: Path,
    *,
    status_doc: str = STATUS_DOC,
    board_doc: str = BOARD_DOC,
    authority_map: str | None = AUTHORITY_MAP,
    guards: dict[str, str] | None = None,
    extra: dict[str, str] | None = None,
) -> Path:
    """A repository with a machine registry, declared restatements, and guards."""
    repo = phase_repo(
        tmp_path,
        criteria=pending_criteria(),
        # The shape a phase is in when its acceptance is prepared: built, but
        # nothing about it recorded yet.
        execution_state="NOT_STARTED",
        checkpoint_state="NO_CHECKPOINT",
        extra_units=[
            accepted_unit("P8", next_units=("P9",)),
            pending_unit("P10", dependencies=("P9",)),
        ],
    )
    files: dict[str, str] = {
        "docs/implementation/STATUS.md": status_doc,
        "docs/implementation/BOARD.md": board_doc,
        "docs/implementation/p9-checkpoint-review.md": REVIEW_DOC,
        "docs/implementation/old-sequencing-plan.md": SUPERSEDED_DOC,
        "docs/product/overview.md": "# Overview\n\nWhat the product is.\n",
        "pyproject.toml": '[project]\nname = "product"\nversion = "0"\n',
    }
    if authority_map is not None:
        files["docs/CANONICAL-DOCUMENTS.md"] = authority_map
    files.update(guards or {})
    files.update(extra or {})
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "status surfaces"], cwd=repo, check=True)
    return repo


def closed(repo: Path) -> PhaseClosureController:
    control = PhaseClosureController(repo, phase_id="P9", builder_session_ids=["builder-1"])
    control.preflight()
    control.record_external_evidence(
        evidence_from_payload(
            {"sha": head(repo), "status": "completed", "conclusion": "success"}
        )
    )
    control.ingest_review(
        supporting_review(CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict())
    )
    control.decide()
    assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
    return control


def read(repo: Path, rel: str) -> str:
    return (repo / rel).read_text(encoding="utf-8")


def dirty(repo: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(line[3:].strip() for line in proc.stdout.splitlines() if line.strip())


# --------------------------------------------------------------------------
# 1. every declared surface is reconciled, together
# --------------------------------------------------------------------------


class TestEveryDeclaredStatusSurfaceIsReconciled:
    def test_the_registry_and_both_restatements_move_in_one_diff(
        self, tmp_path: Path
    ) -> None:
        control = closed(status_repo(tmp_path))

        plan = control.materialize_acceptance_record()

        assert plan.permitted and plan.written, plan.authority_gap or plan.refusal
        assert dirty(control.repo) == sorted(
            [
                REGISTRY_REL,
                "docs/implementation/BOARD.md",
                "docs/implementation/STATUS.md",
            ]
        )

    def test_they_all_say_the_same_thing_afterwards(self, tmp_path: Path) -> None:
        control = closed(status_repo(tmp_path))
        control.materialize_acceptance_record()

        registry = yaml.safe_load(read(control.repo, REGISTRY_REL))
        unit = next(u for u in registry["units"] if u["unit_id"] == "P9")
        assert unit["status"] == "COMPLETE"
        assert unit["execution_state"] == "COMPLETE"
        assert unit["checkpoint_state"] == "PHASE_ACCEPTANCE_COMPLETE"
        assert {row["result"] for row in unit["acceptance_criteria"]} == {"PASS"}

        status = read(control.repo, "docs/implementation/STATUS.md")
        assert "| **P9** | **COMPLETE** / **COMPLETE** / **PHASE_ACCEPTANCE_COMPLETE**" in status
        assert "**PASS**" in status
        assert "READY" not in status.split("**P10**")[0].split("**P9**")[1]

        board = read(control.repo, "docs/implementation/BOARD.md")
        assert "| P9 | COMPLETE |" in board

    def test_the_repositorys_own_consistency_guard_goes_green(
        self, tmp_path: Path
    ) -> None:
        """The relationship the authority map declares, checked by its own test."""
        repo = status_repo(
            tmp_path, guards={"eval/tests/test_status_consistency.py": CONSISTENCY_GUARD}
        )
        control = closed(repo)

        plan = control.materialize_acceptance_record()

        assert plan.written, plan.authority_gap or plan.refusal
        assert plan.verification is not None
        ran = {r.target.path for r in plan.verification.results}
        assert "eval/tests/test_status_consistency.py" in ran
        assert plan.verification.product_failures == []

    def test_a_surface_the_repository_names_in_configuration_is_reconciled(
        self, tmp_path: Path
    ) -> None:
        """No authority map, but the repository names its status document."""
        repo = status_repo(tmp_path, authority_map=None)
        control = PhaseClosureController(
            repo,
            phase_id="P9",
            builder_session_ids=["builder-1"],
            status_restatement_globs=["docs/implementation/BOARD.md"],
        )
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload(
                {"sha": head(repo), "status": "completed", "conclusion": "success"}
            )
        )
        control.ingest_review(
            supporting_review(CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict())
        )
        control.decide()

        plan = control.materialize_acceptance_record()

        assert plan.written, plan.authority_gap or plan.refusal
        assert "| P9 | COMPLETE |" in read(repo, "docs/implementation/BOARD.md")
        assert "docs/implementation/STATUS.md" not in dirty(repo), (
            "a document the repository did not name is not reconciled by guesswork"
        )


# --------------------------------------------------------------------------
# 2. a stale restatement refuses the acceptance commit
# --------------------------------------------------------------------------


class TestAStaleRestatementRefusesTheRecord:
    NARRATIVE = STATUS_DOC + textwrap.dedent(
        """
        ## Note

        P9 is READY and NOT_STARTED, and not one of its criteria is scored.
        """
    )

    def test_a_narrative_claim_stops_the_acceptance(self, tmp_path: Path) -> None:
        control = closed(status_repo(tmp_path, status_doc=self.NARRATIVE))

        plan = control.materialize_acceptance_record()

        assert not plan.complete
        assert plan.authority_gap
        assert "does not compose status prose" in plan.authority_gap
        assert control.record.state is ClosureState.AUTHORITY_GAP

    def test_nothing_is_left_in_the_working_tree(self, tmp_path: Path) -> None:
        """A record that will not be committed must not be left lying about."""
        control = closed(status_repo(tmp_path, status_doc=self.NARRATIVE))

        control.materialize_acceptance_record()

        assert dirty(control.repo) == []
        registry = yaml.safe_load(read(control.repo, REGISTRY_REL))
        unit = next(u for u in registry["units"] if u["unit_id"] == "P9")
        assert unit["status"] == "READY", "the machine record was rolled back too"

    def test_it_names_the_file_and_the_line(self, tmp_path: Path) -> None:
        control = closed(status_repo(tmp_path, status_doc=self.NARRATIVE))

        plan = control.materialize_acceptance_record()

        stale = plan.stale_restatements
        assert stale
        assert all(s.path == "docs/implementation/STATUS.md" for s in stale)
        assert all(s.line > 0 for s in stale)
        assert any("narrative" in s.why for s in stale)

    def test_a_row_about_several_units_is_not_guessed_at(self, tmp_path: Path) -> None:
        """One edit there would move units this acceptance says nothing about."""
        board = "# Board\n\n| Unit | State |\n|---|---|\n| P10-P14 | BLOCKED |\n"
        control = closed(status_repo(tmp_path, board_doc=board))

        plan = control.materialize_acceptance_record()

        assert not plan.complete
        assert any("rather than this unit alone" in s.why for s in plan.stale_restatements)

    def test_a_cell_that_explains_itself_is_not_rewritten(self, tmp_path: Path) -> None:
        """``COMPLETE - the sole selected unit`` is a sentence nobody wrote."""
        status = (
            "# Status\n\n| Phase | Status |\n|---|---|\n"
            "| **P9** | **READY** - the sole selected unit, nothing implemented |\n"
        )
        control = closed(status_repo(tmp_path, status_doc=status))

        plan = control.materialize_acceptance_record()

        assert not plan.complete
        assert any("prose" in s.why for s in plan.stale_restatements)


# --------------------------------------------------------------------------
# 3. only acceptance-record surfaces may change
# --------------------------------------------------------------------------


class TestOnlyAcceptanceRecordSurfacesMayChange:
    def test_every_changed_path_classifies_as_the_acceptance_record(
        self, tmp_path: Path
    ) -> None:
        control = closed(status_repo(tmp_path))
        control.materialize_acceptance_record()

        changed = dirty(control.repo)
        assert changed
        assert all(classify_surface(p) is Surface.ACCEPTANCE_RECORD for p in changed), changed

    def test_a_declared_surface_outside_the_record_is_not_written(
        self, tmp_path: Path
    ) -> None:
        """Declared live status does not make a file writable by an acceptance."""
        mapping = AUTHORITY_MAP.replace(
            "| [`product/overview.md`](product/overview.md) | What the product is | **CANONICAL** |",
            "| [`src/status_page.md`](src/status_page.md) | The status the runtime ships | **CURRENT_STATUS** |",
        )
        repo = status_repo(
            tmp_path,
            authority_map=mapping,
            extra={"src/status_page.md": "# Shipped status\n\nP9 is READY and NOT_STARTED.\n"},
        )
        control = closed(repo)

        plan = control.materialize_acceptance_record()

        assert "src/status_page.md" not in dirty(repo)
        assert any("classifies as RUNTIME" in note for note in plan.notes)

    def test_a_document_classified_historical_is_never_edited(
        self, tmp_path: Path
    ) -> None:
        """A completed review says what WAS true. It is evidence, not status."""
        control = closed(status_repo(tmp_path))
        before = read(control.repo, "docs/implementation/p9-checkpoint-review.md")
        superseded = read(control.repo, "docs/implementation/old-sequencing-plan.md")

        control.materialize_acceptance_record()

        assert read(control.repo, "docs/implementation/p9-checkpoint-review.md") == before
        assert read(control.repo, "docs/implementation/old-sequencing-plan.md") == superseded
        assert "docs/implementation/p9-checkpoint-review.md" not in dirty(control.repo)


# --------------------------------------------------------------------------
# 4. the machine record is the source; the human text cannot override it
# --------------------------------------------------------------------------


class TestTheMachineRecordIsTheSource:
    def test_the_restatement_takes_the_registrys_value_not_its_own(
        self, tmp_path: Path
    ) -> None:
        board = "# Board\n\n| Unit | State |\n|---|---|\n| P9 | ACCEPTED |\n| P10 | BLOCKED |\n"
        control = closed(status_repo(tmp_path, board_doc=board))

        control.materialize_acceptance_record()

        # ACCEPTED was never a value this registry uses, so it states none of the
        # moving facts and is left exactly alone rather than "corrected".
        assert "| P9 | ACCEPTED |" in read(control.repo, "docs/implementation/BOARD.md")
        registry = yaml.safe_load(read(control.repo, REGISTRY_REL))
        unit = next(u for u in registry["units"] if u["unit_id"] == "P9")
        assert unit["status"] == "COMPLETE", "the registry took the value its own precedent sets"

    def test_a_status_document_cannot_supply_the_accepted_state(
        self, tmp_path: Path
    ) -> None:
        """No precedent in the registry is an AUTHORITY_GAP, whatever the prose says."""
        repo = phase_repo(
            tmp_path,
            criteria=pending_criteria(),
            execution_state="NOT_STARTED",
            checkpoint_state="NO_CHECKPOINT",
            extra_units=[pending_unit("P10", dependencies=("P9",))],
        )
        (repo / "docs" / "implementation" / "STATUS.md").write_text(
            "# Status\n\nP9 is COMPLETE and every criterion is PASS.\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "a claim"], cwd=repo, check=True)
        control = closed(repo)

        plan = control.materialize_acceptance_record()

        assert plan.authority_gap
        assert not plan.written

    def test_the_facts_are_the_edits_the_registry_actually_took(
        self, tmp_path: Path
    ) -> None:
        control = closed(status_repo(tmp_path))

        plan = control.materialize_acceptance_record()

        moved = {(f.unit_id, f.before, f.after) for f in plan.facts}
        assert ("P9", "READY", "COMPLETE") in moved
        assert ("P9", "PENDING", "PASS") in moved
        assert ("P10", "BLOCKED", "READY") in moved


# --------------------------------------------------------------------------
# 5. the evidence still points at the accepted candidate
# --------------------------------------------------------------------------


class TestTheAcceptedCandidateStaysTheEvidenceTarget:
    def test_the_commit_and_tree_do_not_move(self, tmp_path: Path) -> None:
        control = closed(status_repo(tmp_path))
        before = control.record.fingerprint()

        control.materialize_acceptance_record()

        after = control.record.fingerprint()
        assert after.head == before.head and after.tree == before.tree

    def test_the_external_record_and_adjudication_survive_the_bookkeeping(
        self, tmp_path: Path
    ) -> None:
        control = closed(status_repo(tmp_path))
        sha = control.record.external_evidence.sha

        control.materialize_acceptance_record()

        assert control.record.external_evidence is not None
        assert control.record.external_evidence.sha == sha
        assert control.record.adjudication is not None
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_written_evidence_names_the_candidate_not_the_bookkeeping(
        self, tmp_path: Path
    ) -> None:
        control = closed(status_repo(tmp_path))
        candidate = control.record.fingerprint()

        control.materialize_acceptance_record()

        registry = yaml.safe_load(read(control.repo, REGISTRY_REL))
        unit = next(u for u in registry["units"] if u["unit_id"] == "P9")
        for row in unit["acceptance_criteria"]:
            assert candidate.head[:12] in row["adjudication_evidence"]
            assert candidate.tree[:12] in row["adjudication_evidence"]

    def test_no_evidence_is_retired_as_stale(self, tmp_path: Path) -> None:
        control = closed(status_repo(tmp_path))
        control.materialize_acceptance_record()

        stale = [
            f
            for f in control.record.findings
            if f.classification is FindingClass.STALE_VERIFICATION
        ]
        assert stale == [], [f.brief() for f in stale]


# --------------------------------------------------------------------------
# 6. the next phase advances only on the repository's graph
# --------------------------------------------------------------------------


class TestTheNextPhaseComesFromTheGraph:
    def test_the_restatement_follows_the_registrys_successor(
        self, tmp_path: Path
    ) -> None:
        control = closed(status_repo(tmp_path))

        plan = control.materialize_acceptance_record()

        assert plan.next_phase_advanced == "P10"
        assert "| P10 | READY |" in read(control.repo, "docs/implementation/BOARD.md")

    def test_a_repository_that_unlocks_nothing_advances_nothing_anywhere(
        self, tmp_path: Path
    ) -> None:
        repo = status_repo(tmp_path)
        registry = yaml.safe_load(read(repo, REGISTRY_REL))
        for unit in registry["units"]:
            if unit["unit_id"] == "P9":
                unit["next_units_unlocked"] = []
        (repo / REGISTRY_REL).write_text(
            yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
        )
        subprocess.run(["git", "commit", "-qam", "unlock nothing"], cwd=repo, check=True)
        control = closed(repo)

        plan = control.materialize_acceptance_record()

        assert plan.next_phase_advanced == ""
        assert "| P10 | BLOCKED |" in read(repo, "docs/implementation/BOARD.md")

    def test_a_board_claiming_the_successor_is_ready_does_not_make_it_so(
        self, tmp_path: Path
    ) -> None:
        """The board follows the graph; it never leads it."""
        repo = status_repo(tmp_path)
        registry = yaml.safe_load(read(repo, REGISTRY_REL))
        for unit in registry["units"]:
            if unit["unit_id"] == "P9":
                unit["next_units_unlocked"] = []
        (repo / REGISTRY_REL).write_text(
            yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
        )
        (repo / "docs/implementation/BOARD.md").write_text(
            "# Board\n\n| Unit | State |\n|---|---|\n| P9 | READY |\n| P10 | READY |\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "commit", "-qam", "a board that ran ahead"], cwd=repo, check=True)
        control = closed(repo)

        control.materialize_acceptance_record()

        registry = yaml.safe_load(read(repo, REGISTRY_REL))
        successor = next(u for u in registry["units"] if u["unit_id"] == "P10")
        assert successor["status"] == "BLOCKED"


# --------------------------------------------------------------------------
# 7. running it again changes nothing
# --------------------------------------------------------------------------


class TestRerunIsIdempotent:
    def test_the_second_pass_writes_nothing(self, tmp_path: Path) -> None:
        control = closed(status_repo(tmp_path))
        control.materialize_acceptance_record()
        after_first = {rel: read(control.repo, rel) for rel in dirty(control.repo)}

        again = control.materialize_acceptance_record()

        assert again.already_recorded
        assert not again.edits
        assert {rel: read(control.repo, rel) for rel in dirty(control.repo)} == after_first

    def test_the_restatements_are_not_double_applied(self, tmp_path: Path) -> None:
        control = closed(status_repo(tmp_path))
        control.materialize_acceptance_record()
        control.materialize_acceptance_record()
        control.materialize_acceptance_record()

        board = read(control.repo, "docs/implementation/BOARD.md")
        assert board.count("| P9 | COMPLETE |") == 1
        assert board.count("| P10 | READY |") == 1
        status = read(control.repo, "docs/implementation/STATUS.md")
        assert status.count("PHASE_ACCEPTANCE_COMPLETE") == 1


# --------------------------------------------------------------------------
# 8. the repository's own guards judge the diff
# --------------------------------------------------------------------------


class TestTheRepositorysOwnGuardsJudgeTheRecord:
    def test_a_guard_the_record_turns_red_refuses_the_record(
        self, tmp_path: Path
    ) -> None:
        """The defect this pass exists for, in miniature.

        A criterion's own oracle asserts the pre-acceptance state out of the file
        the record moves. The diff is status-only and contains nothing but the
        acceptance record, and it turns the repository red.
        """
        repo = status_repo(
            tmp_path, guards={"eval/tests/test_ships_dark.py": SHIPS_DARK_GUARD}
        )
        control = closed(repo)

        plan = control.materialize_acceptance_record()

        assert not plan.written
        assert "turns 1 of this repository's own guard(s) red" in plan.authority_gap
        assert "test_ships_dark.py" in plan.authority_gap
        assert control.record.state is ClosureState.AUTHORITY_GAP

    def test_the_record_is_rolled_back_when_a_guard_refuses_it(
        self, tmp_path: Path
    ) -> None:
        repo = status_repo(
            tmp_path, guards={"eval/tests/test_ships_dark.py": SHIPS_DARK_GUARD}
        )
        control = closed(repo)

        control.materialize_acceptance_record()

        assert dirty(repo) == []

    def test_a_guard_that_was_already_red_is_not_blamed_on_the_record(
        self, tmp_path: Path
    ) -> None:
        """A repository that is already failing did not fail because of this."""
        already = (
            '"""A guard that fails on the candidate tree, before anything is written."""\n'
            "from pathlib import Path\n"
            "ROOT = Path(__file__).resolve().parents[2]\n"
            'REGISTRY = ROOT / "docs/implementation/IMPLEMENTATION-REGISTRY.yaml"\n\n\n'
            "def test_something_unrelated_that_was_already_broken():\n"
            '    assert REGISTRY.exists()\n'
            '    assert False, "this repository was already red here"\n'
        )
        repo = status_repo(tmp_path, guards={"eval/tests/test_already_red.py": already})
        control = closed(repo)

        plan = control.materialize_acceptance_record()

        assert plan.written, plan.authority_gap or plan.refusal
        assert any("already fails on the candidate tree" in note for note in plan.notes)

    def test_the_guards_run_are_the_ones_that_read_what_changed(
        self, tmp_path: Path
    ) -> None:
        repo = status_repo(
            tmp_path, guards={"eval/tests/test_status_consistency.py": CONSISTENCY_GUARD}
        )
        targets, _notes = discover_record_guards(
            repo, [REGISTRY_REL, "docs/implementation/BOARD.md"]
        )
        paths = {t.path for t in targets}

        assert "eval/tests/test_status_consistency.py" in paths
        assert "eval/tests/test_behaviour.py" not in paths, (
            "a test that does not read the record has no opinion about it"
        )


# --------------------------------------------------------------------------
# 9. it is a rule about repositories, not about one product
# --------------------------------------------------------------------------

_INCIDENT_TOKENS = re.compile(
    r"(?i)(?:neyma|freight|ops_control|\bP7\b|\bP8\b|P7-AC-\d+|20260911-\d+|a9050b69|"
    r"CURRENT\.md|CANONICAL-DOCUMENTS|IMPLEMENTATION-REGISTRY|PHASE_ACCEPTANCE_COMPLETE|"
    r"CURRENT_STATUS|ships_dark)"
)

_CORE = ("acceptance_record.py", "acceptance_commit.py", "repo_verification.py")


class TestTheRuleNamesNoProduct:
    def test_no_core_module_encodes_a_product_identifier(self) -> None:
        root = Path(ar.__file__).parent
        offenders: list[str] = []
        for name in _CORE:
            source = _code_only(root / name)
            offenders += [f"{name}: {m.group(0)}" for m in _INCIDENT_TOKENS.finditer(source)]
        assert offenders == [], f"product-specific tokens in core logic: {offenders}"

    def test_the_guard_would_notice_one(self, tmp_path: Path) -> None:
        planted = tmp_path / "planted.py"
        planted.write_text('MAP = "docs/CANONICAL-DOCUMENTS.md"\n', encoding="utf-8")
        assert _INCIDENT_TOKENS.findall(_code_only(planted))

    def test_a_repository_with_its_own_vocabulary_is_read_in_it(
        self, tmp_path: Path
    ) -> None:
        """Different class names, different document names, same mechanism."""
        mapping = (
            "# The register\n\n| File | Role | Kind |\n|---|---|---|\n"
            "| [`implementation/STATUS.md`](implementation/STATUS.md) | now | **LIVE_STATUS** |\n"
            "| [`implementation/BOARD.md`](implementation/BOARD.md) | now | **LIVE_STATUS** |\n"
            "| [`implementation/IMPLEMENTATION-REGISTRY.yaml`](implementation/IMPLEMENTATION-REGISTRY.yaml) | units | **CONTROL** |\n"
            "| [`implementation/p9-checkpoint-review.md`](implementation/p9-checkpoint-review.md) | then | **ARCHIVE** |\n"
            "| [`implementation/old-sequencing-plan.md`](implementation/old-sequencing-plan.md) | then | **ARCHIVE** |\n"
            "| [`product/overview.md`](product/overview.md) | what | **REFERENCE** |\n"
        )
        repo = status_repo(tmp_path, authority_map=mapping)
        surfaces, map_path, _notes = declared_status_surfaces(repo)

        assert map_path == "docs/CANONICAL-DOCUMENTS.md"
        assert "docs/implementation/STATUS.md" in surfaces
        assert "docs/implementation/BOARD.md" in surfaces
        assert "docs/implementation/p9-checkpoint-review.md" not in surfaces

    def test_a_repository_with_no_map_and_no_configuration_reconciles_only_the_record(
        self, tmp_path: Path
    ) -> None:
        control = closed(status_repo(tmp_path, authority_map=None))

        plan = control.materialize_acceptance_record()

        assert plan.written
        assert plan.changed_paths == [REGISTRY_REL]
        assert any("declares no authority map" in note for note in plan.notes)


def _code_only(path: Path) -> str:
    """The module with its comments and docstrings removed."""
    import io
    import tokenize

    kept: list[str] = []
    previous = tokenize.INDENT
    source = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.ENCODING,
        ):
            continue
        previous = token.type
        kept.append(token.string)
    return "\n".join(kept)


# --------------------------------------------------------------------------
# the pieces, directly
# --------------------------------------------------------------------------


class TestThePiecesDirectly:
    FACTS = (
        StatusFact("P9", "status", "READY", "COMPLETE"),
        StatusFact("P9", "execution_state", "NOT_STARTED", "COMPLETE"),
    )

    def test_a_status_cell_moves(self) -> None:
        text = "| Phase | State |\n|---|---|\n| **P9** | **READY** / **NOT_STARTED** |\n"
        updated, moved, stuck = reconcile_restatement(text, self.FACTS)
        assert stuck == []
        assert len(moved) == 2
        assert "| **P9** | **COMPLETE** / **COMPLETE** |" in updated

    def test_preserved_history_beside_a_live_claim_is_never_touched(self) -> None:
        text = "P9 is READY. *(Until this commit this said P9 was BLOCKED / NOT_STARTED.)*\n"
        updated, moved, stuck = reconcile_restatement(text, self.FACTS)
        assert updated == text
        assert moved == []
        assert [s.stale_value for s in stuck] == ["READY"], (
            "the live claim is reported; the preserved wording beside it is not"
        )

    def test_a_blockquote_of_superseded_wording_is_not_a_live_claim(self) -> None:
        text = "> P9 stays READY / NOT_STARTED until its review.\n"
        _updated, moved, stuck = reconcile_restatement(text, self.FACTS)
        assert moved == [] and stuck == []

    def test_a_surface_that_states_nothing_moving_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        repo = status_repo(tmp_path)
        stale, declared, _notes = restatement_surfaces(
            repo,
            [StatusFact("P9", "status", "SOMETHING_ELSE", "COMPLETE")],
            exclude=[REGISTRY_REL],
        )
        assert "docs/implementation/STATUS.md" in declared
        assert stale == []

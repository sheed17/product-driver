"""(S) A repository may declare WHERE its machine-derived status lives, and that declaration wins.

The acceptance-record writer read every declared status document as one
undifferentiated live restatement and scanned it for status tokens. A repository
that keeps its history IN PLACE — the superseded sentence preserved beside the
one that replaced it, because deleting it would destroy the evidence that a
landing did not overclaim — therefore produced dozens of "stale live
restatements" that were not live and not stale, every one of them a founder
decision Product Driver had no business asking for. Worse, the fail-closed rule
that follows ("if any line is stuck, write none of this file") then left the ONE
region the repository maintains mechanically un-regenerated, so the repository's
own reconciliation guard went red and the acceptance record rolled back. The
phase could not close because of prose nobody was allowed to touch.

The repair is one rule, and it is about authority rather than about markdown:

* where a repository MARKS a bounded region of a document and that region is a
  faithful projection of the machine record, that region — and only that region
  — is what an acceptance materializer reconciles. It is REGENERATED from the
  post-edit registry, never token-substituted, and everything outside it is the
  repository's own orientation and history, left byte for byte and not read as a
  live claim at all;
* where a repository marks no such region it has said nothing about where its
  prose stops being status, and the conservative behaviour is unchanged: what a
  machine can move without composing a sentence moves, and everything else is
  reported as an AUTHORITY_GAP rather than guessed at.

"Faithful projection" is proved, not assumed: the region carries a table and
nothing else, every column names a field the registry's units actually have,
every row's subject is a unit the registry actually declares, and rendering the
region from the registry's CURRENT values reproduces it byte for byte. A region
that fails any of those is not regenerated — it falls back to the conservative
path with the reason recorded.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from neyma_product_driver.acceptance_record import (
    StatusFact,
    apply_live_projection,
    bounded_live_projection,
    restatement_surfaces,
)
from neyma_product_driver.external_verification import evidence_from_payload
from neyma_product_driver.phase_acceptance import ClosureState
from neyma_product_driver.phase_closure import PhaseClosureController
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
STATUS_REL = "docs/implementation/STATUS.md"
MAP_REL = "docs/CANONICAL-DOCUMENTS.md"
GUARD_REL = "eval/tests/test_status_reconciliation.py"

# --------------------------------------------------------------------------
# The repository under test
# --------------------------------------------------------------------------

AUTHORITY_MAP = """\
# Canonical documents

| Document | Purpose | Class |
|---|---|---|
| [`implementation/STATUS.md`](implementation/STATUS.md) | The short-form status authority | **CURRENT_STATUS** |
| [`implementation/IMPLEMENTATION-REGISTRY.yaml`](implementation/IMPLEMENTATION-REGISTRY.yaml) | Work units and their state | **IMPLEMENTATION_CONTROL** |
| [`implementation/p9-checkpoint-review.md`](implementation/p9-checkpoint-review.md) | A completed checkpoint review | **HISTORICAL** |
| [`implementation/old-sequencing-plan.md`](implementation/old-sequencing-plan.md) | The plan it replaced | **SUPERSEDED** |
| [`product/overview.md`](product/overview.md) | What the product is | **CANONICAL** |
"""

#: Narrative ABOVE the bounded region. Every one of these lines carries a status
#: token the acceptance moves, and not one of them is a live claim: they are the
#: record of what was true at a landing, kept in place on purpose.
NARRATIVE_ABOVE = """\
# Status — where the program stands

> The narrative above and below is human orientation and history. The table
> between the markers is the ONE part of this file a materializer reconciles
> mechanically: a deterministic projection of the registry's unit lifecycle.

| Phase | Status | Evidence |
|---|---|---|
| **P8** | **COMPLETE** | a phase review |
| **P9** — the unit under test | **READY** / **`NOT_STARTED`** / **`NO_CHECKPOINT`** — five criteria, every one still **`PENDING`**. *(Until this branch's build this cell read "**NOT STARTED**, nothing implemented"; that was TRUE at the parent commit and is STALE now — REPLACED rather than deleted.)* | the registry |
| **P10–P11** | **BLOCKED** behind P9 | the registry |

The M2 landing block below closes with *"P9 stays READY / NOT_STARTED, and P10
stays BLOCKED / NOT_STARTED"*. That was true when written and is false now; it
is kept verbatim because it is the evidence that the landing did not overclaim.
"""

#: Narrative BELOW it, including the shape that matters most: a parenthetical
#: aside preserving the wording it replaced.
NARRATIVE_BELOW = """\
## P9 — what landed, and what is owed

Five criteria, `AC-1`…`AC-5`, every one required and every one `PENDING`. They
were instantiated from existing authority and scored by nobody.

*(Until this commit this paragraph read "P9 is NOT_STARTED and P10 is BLOCKED
behind it, with every criterion PENDING"; REPLACED rather than deleted.)*

| Risk | Why it is not being taken |
|---|---|
| Re-opening P8's acceptance | **P8's acceptance is DONE.** *(Until this commit this row read "P9 stays READY / NOT_STARTED / NO_CHECKPOINT and every criterion is PENDING"; REPLACED rather than deleted.)* |
"""


def live_block(rows: list[tuple[str, str, str, str]], *, name: str = "LIVE-STATUS") -> str:
    """The bounded region, as the repository under test writes one."""
    body = "\n".join(f"| {uid} | {a} | {b} | {c} |" for uid, a, b, c in rows)
    return (
        f"<!-- {name}:BEGIN — deterministic projection of IMPLEMENTATION-REGISTRY.yaml unit "
        "lifecycle (columns: status / execution_state / checkpoint_state). Regenerate from the "
        "registry; do not hand-edit. -->\n"
        "| Phase | status | execution_state | checkpoint_state |\n"
        "|---|---|---|---|\n"
        f"{body}\n"
        f"<!-- {name}:END -->"
    )


RECONCILED_ROWS = [
    ("P8", "COMPLETE", "COMPLETE", "PHASE_ACCEPTANCE_COMPLETE"),
    ("P9", "READY", "NOT_STARTED", "NO_CHECKPOINT"),
    ("P10", "BLOCKED", "NOT_STARTED", "NO_CHECKPOINT"),
    ("P11", "BLOCKED", "NOT_STARTED", "NO_CHECKPOINT"),
]


def status_doc(block: str | None = None) -> str:
    region = live_block(RECONCILED_ROWS) if block is None else block
    return f"{NARRATIVE_ABOVE}\n{region}\n\n{NARRATIVE_BELOW}"


#: The repository's own guard over the relationship it declares — the shape of
#: `eval/tests/test_current_status_reconciliation.py`. It polices the bounded
#: region and deliberately does not police the prose around it.
RECONCILIATION_GUARD = '''\
"""The bounded LIVE-STATUS region is a projection of the registry, both ways."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs/implementation/IMPLEMENTATION-REGISTRY.yaml"
STATUS = ROOT / "docs/implementation/STATUS.md"
FIELDS = ("status", "execution_state", "checkpoint_state")
PHASE = re.compile(r"P\\d+")


def _registry():
    units = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["units"]
    return {
        str(u["unit_id"]): tuple(str(u[f]) for f in FIELDS)
        for u in units
        if PHASE.fullmatch(str(u.get("unit_id", "")))
    }


def _block():
    text = STATUS.read_text(encoding="utf-8")
    start = text.index("<!-- LIVE-STATUS:BEGIN")
    end = text.index("<!-- LIVE-STATUS:END -->", start)
    rows = {}
    for line in text[start:end].split("\\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) == 1 + len(FIELDS) and PHASE.fullmatch(cells[0]):
            rows[cells[0]] = tuple(cells[1:])
    return rows


def test_the_bounded_region_reconciles_exactly_with_the_registry():
    expected = _registry()
    assert expected, "no phase units discovered"
    assert _block() == expected, "the LIVE-STATUS region drifted from the registry"
'''

#: The other shape: a guard that pins the PRE-acceptance state, so a correct
#: deterministic reconciliation still leaves the repository red.
PRE_ACCEPTANCE_GUARD = '''\
"""A guard asserting the state the acceptance moves away from."""

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
        criterion(
            f"AC-{index}",
            f"surface_{index}_holds",
            result="PENDING",
            evidence="",
            requirement=(
                "`eval/tests/test_behaviour.py::test_behaviour_holds` passes on the accepted tree"
            ),
        )
        for index in range(1, 6)
    ]


def projection_repo(
    tmp_path: Path,
    *,
    document: str | None = None,
    guards: dict[str, str] | None = None,
    extra: dict[str, str] | None = None,
) -> Path:
    """A repository whose status document carries a bounded projection region."""
    repo = phase_repo(
        tmp_path,
        criteria=pending_criteria(),
        execution_state="NOT_STARTED",
        checkpoint_state="NO_CHECKPOINT",
        extra_units=[
            accepted_unit("P8", next_units=("P9",)),
            pending_unit("P10", dependencies=("P9",)),
            pending_unit("P11", dependencies=("P10",)),
        ],
    )
    files: dict[str, str] = {
        MAP_REL: AUTHORITY_MAP,
        STATUS_REL: status_doc() if document is None else document,
        "docs/implementation/p9-checkpoint-review.md": "# review\n\nP9 stays READY.\n",
        "docs/implementation/old-sequencing-plan.md": "# plan\n\nP9 stays READY.\n",
        "docs/product/overview.md": "# Overview\n",
        "pyproject.toml": '[project]\nname = "product"\nversion = "0"\n',
        GUARD_REL: RECONCILIATION_GUARD,
    }
    files.update(guards or {})
    files.update(extra or {})
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "a bounded live-status region"], cwd=repo, check=True)
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


def block_rows(text: str) -> dict[str, tuple[str, ...]]:
    start = text.index("<!-- LIVE-STATUS:BEGIN")
    end = text.index("<!-- LIVE-STATUS:END -->", start)
    rows: dict[str, tuple[str, ...]] = {}
    for line in text[start:end].split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) == 4 and cells[0].startswith("P") and cells[0][1:].isdigit():
            rows[cells[0]] = tuple(cells[1:])
    return rows


def registry_rows(repo: Path) -> dict[str, tuple[str, ...]]:
    units = yaml.safe_load(read(repo, REGISTRY_REL))["units"]
    return {
        str(u["unit_id"]): (
            str(u["status"]),
            str(u["execution_state"]),
            str(u["checkpoint_state"]),
        )
        for u in units
    }


def outside_block(text: str) -> str:
    start = text.index("<!-- LIVE-STATUS:BEGIN")
    end = text.index("<!-- LIVE-STATUS:END -->", start)
    return text[:start] + text[end:]


# --------------------------------------------------------------------------
# 1. the bounded region is regenerated from the post-edit machine record
# --------------------------------------------------------------------------


class TestTheBoundedRegionIsRegeneratedFromTheRegistry:
    def test_the_lifecycle_change_reaches_the_region(self, tmp_path: Path) -> None:
        control = closed(projection_repo(tmp_path))

        plan = control.materialize_acceptance_record()

        assert plan.permitted and plan.written, plan.authority_gap or plan.refusal
        assert block_rows(read(control.repo, STATUS_REL))["P9"] == (
            "COMPLETE",
            "COMPLETE",
            "PHASE_ACCEPTANCE_COMPLETE",
        )

    def test_the_region_reconciles_exactly_with_the_registry_afterwards(
        self, tmp_path: Path
    ) -> None:
        control = closed(projection_repo(tmp_path))
        control.materialize_acceptance_record()

        rows = block_rows(read(control.repo, STATUS_REL))
        registry = registry_rows(control.repo)
        assert rows == {uid: registry[uid] for uid in rows}, "the projection drifted"

    def test_the_repositorys_own_reconciliation_guard_passes(self, tmp_path: Path) -> None:
        control = closed(projection_repo(tmp_path))

        plan = control.materialize_acceptance_record()

        assert plan.verification is not None, "the repository's guards were not asked"
        ran = [r.target.path for r in plan.verification.results]
        assert GUARD_REL in ran, f"the reconciliation guard did not run; ran {ran}"
        assert not plan.verification.product_failures, plan.verification.headline()

    def test_the_successor_movement_is_reflected_in_the_region(self, tmp_path: Path) -> None:
        control = closed(projection_repo(tmp_path))

        plan = control.materialize_acceptance_record()

        assert plan.next_phase_advanced == "P10"
        assert block_rows(read(control.repo, STATUS_REL))["P10"][0] == "READY"

    def test_every_row_moves_together_and_the_untouched_ones_stay_put(
        self, tmp_path: Path
    ) -> None:
        """Anti-vacuity: the region really does carry several units, and the ones
        this acceptance says nothing about are still there saying what they said."""
        control = closed(projection_repo(tmp_path))
        before = block_rows(read(control.repo, STATUS_REL))
        assert len(before) == 4, before

        control.materialize_acceptance_record()

        after = block_rows(read(control.repo, STATUS_REL))
        assert set(after) == set(before)
        assert after["P8"] == before["P8"]
        assert after["P11"] == before["P11"]
        assert after["P9"] != before["P9"] and after["P10"] != before["P10"]


# --------------------------------------------------------------------------
# 2. generated from the record, never token-substituted
# --------------------------------------------------------------------------


class TestTheRegionIsGeneratedRatherThanTokenSubstituted:
    def test_it_writes_whatever_the_record_says_even_with_no_fact_to_move(self) -> None:
        """The generator reads the registry and nothing else. Given values that
        appear in no ``before -> after`` fact anywhere, it writes those values —
        which a token substitution, having nothing to substitute, could not."""
        text = status_doc()
        units = {
            "P8": {
                "unit_id": "P8",
                "status": "COMPLETE",
                "execution_state": "COMPLETE",
                "checkpoint_state": "PHASE_ACCEPTANCE_COMPLETE",
            },
            "P9": {
                "unit_id": "P9",
                "status": "READY",
                "execution_state": "NOT_STARTED",
                "checkpoint_state": "NO_CHECKPOINT",
            },
            "P10": {
                "unit_id": "P10",
                "status": "BLOCKED",
                "execution_state": "NOT_STARTED",
                "checkpoint_state": "NO_CHECKPOINT",
            },
            "P11": {
                "unit_id": "P11",
                "status": "BLOCKED",
                "execution_state": "NOT_STARTED",
                "checkpoint_state": "NO_CHECKPOINT",
            },
        }
        projection, why = bounded_live_projection(text, units)
        assert projection is not None, why

        after = {uid: dict(unit) for uid, unit in units.items()}
        after["P9"]["status"] = "ACCEPTED_BY_THE_BOARD"
        after["P9"]["execution_state"] = "SHIPPED_DARK"
        after["P11"]["checkpoint_state"] = "AWAITING_SELECTION"
        updated, moved = apply_live_projection(text, projection, after)

        rows = block_rows(updated)
        assert rows["P9"] == ("ACCEPTED_BY_THE_BOARD", "SHIPPED_DARK", "NO_CHECKPOINT")
        assert rows["P11"] == ("BLOCKED", "NOT_STARTED", "AWAITING_SELECTION")
        assert {uid for _line, _b, _a, uid in moved} == {"P9", "P11"}

    def test_it_moves_nothing_when_the_record_has_not_moved(self) -> None:
        text = status_doc()
        units = {
            uid: {
                "unit_id": uid,
                "status": a,
                "execution_state": b,
                "checkpoint_state": c,
            }
            for uid, a, b, c in RECONCILED_ROWS
        }
        projection, _why = bounded_live_projection(text, units)
        assert projection is not None
        updated, moved = apply_live_projection(text, projection, units)
        assert not moved and updated == text


# --------------------------------------------------------------------------
# 3. the narrative around it is not status, and is not touched
# --------------------------------------------------------------------------


class TestTheNarrativeAroundTheRegionIsLeftAlone:
    def test_no_narrative_line_is_reported_as_a_stale_live_restatement(
        self, tmp_path: Path
    ) -> None:
        control = closed(projection_repo(tmp_path))

        plan = control.materialize_acceptance_record()

        assert plan.stale_restatements == [], "\n".join(
            s.brief() for s in plan.stale_restatements
        )
        assert plan.complete, plan.authority_gap

    def test_every_byte_outside_the_region_survives(self, tmp_path: Path) -> None:
        control = closed(projection_repo(tmp_path))
        before = read(control.repo, STATUS_REL)

        control.materialize_acceptance_record()

        assert outside_block(read(control.repo, STATUS_REL)) == outside_block(before)

    def test_the_preserved_parenthetical_wording_is_byte_identical(
        self, tmp_path: Path
    ) -> None:
        """The exact shape that was being reported as stale: a superseded claim
        kept IN PLACE in an italic aside, carrying every token the acceptance
        moves. It is history, and history is evidence."""
        control = closed(projection_repo(tmp_path))
        aside = (
            '*(Until this commit this paragraph read "P9 is NOT_STARTED and P10 is BLOCKED\n'
            'behind it, with every criterion PENDING"; REPLACED rather than deleted.)*'
        )
        assert aside in read(control.repo, STATUS_REL)

        control.materialize_acceptance_record()

        assert aside in read(control.repo, STATUS_REL)

    def test_the_narrative_status_table_above_it_is_not_reconciled_either(
        self, tmp_path: Path
    ) -> None:
        """The document carries a SECOND table that looks exactly like a status
        restatement. The repository declared which region is machine status, and
        that declaration outranks what a token scan would make of the other."""
        control = closed(projection_repo(tmp_path))

        control.materialize_acceptance_record()

        text = read(control.repo, STATUS_REL)
        assert "| **P10–P11** | **BLOCKED** behind P9 | the registry |" in text
        assert "**READY** / **`NOT_STARTED`** / **`NO_CHECKPOINT`**" in text

    def test_the_record_says_which_region_it_reconciled_and_why(
        self, tmp_path: Path
    ) -> None:
        control = closed(projection_repo(tmp_path))

        plan = control.materialize_acceptance_record()

        note = next((n for n in plan.notes if "bounded LIVE-STATUS region" in n), "")
        assert note, plan.notes
        assert "orientation and history" in note


# --------------------------------------------------------------------------
# 4. a region that is not a faithful projection is not regenerated
# --------------------------------------------------------------------------


class TestAnUnfaithfulRegionFallsBackToTheConservativePath:
    def test_a_changed_projection_value_is_caught(self, tmp_path: Path) -> None:
        """A region that does not already render from the registry is not a
        projection of it, whatever its markers say, and is not regenerated."""
        drifted = [
            ("P8", "COMPLETE", "COMPLETE", "PHASE_ACCEPTANCE_COMPLETE"),
            ("P9", "READY", "IN_PROGRESS", "NO_CHECKPOINT"),
            ("P10", "BLOCKED", "NOT_STARTED", "NO_CHECKPOINT"),
            ("P11", "BLOCKED", "NOT_STARTED", "NO_CHECKPOINT"),
        ]
        control = closed(projection_repo(tmp_path, document=status_doc(live_block(drifted))))

        plan = control.materialize_acceptance_record()

        assert plan.stale_restatements, "the fallback did not report the surface"
        assert control.record.state is ClosureState.AUTHORITY_GAP
        assert "P9" in read(control.repo, STATUS_REL)
        assert block_rows(read(control.repo, STATUS_REL))["P9"] == (
            "READY",
            "IN_PROGRESS",
            "NO_CHECKPOINT",
        ), "a region that is not a projection was regenerated anyway"

    def test_an_invented_phase_row_is_caught(self, tmp_path: Path) -> None:
        invented = list(RECONCILED_ROWS) + [
            ("P99", "BLOCKED", "NOT_STARTED", "NO_CHECKPOINT")
        ]
        control = closed(projection_repo(tmp_path, document=status_doc(live_block(invented))))

        plan = control.materialize_acceptance_record()

        assert control.record.state is ClosureState.AUTHORITY_GAP
        assert any("P99" in n for n in plan.notes), plan.notes

    def test_a_dropped_phase_row_is_never_invented_back(self, tmp_path: Path) -> None:
        """A region missing a unit the registry declares is ALREADY red on the
        candidate tree, and the repository's own both-ways guard is what says so.
        The generator writes the rows the document has and invents none: it does
        not quietly repair a document by adding a row nobody wrote, and it does
        not take the blame for a guard it did not turn red."""
        dropped = [row for row in RECONCILED_ROWS if row[0] != "P11"]
        control = closed(projection_repo(tmp_path, document=status_doc(live_block(dropped))))

        plan = control.materialize_acceptance_record()

        rows = block_rows(read(control.repo, STATUS_REL))
        assert "P11" not in rows, "a row the document never carried was invented"
        assert set(rows) == {"P8", "P9", "P10"}
        assert rows["P9"] == ("COMPLETE", "COMPLETE", "PHASE_ACCEPTANCE_COMPLETE")
        assert any(
            GUARD_REL in note and "already fails" in note for note in plan.notes
        ), plan.notes

    def test_a_region_carrying_prose_is_not_a_projection(self, tmp_path: Path) -> None:
        block = live_block(RECONCILED_ROWS).replace(
            "| Phase | status | execution_state | checkpoint_state |",
            "P9 is READY and nothing is scored.\n\n"
            "| Phase | status | execution_state | checkpoint_state |",
        )
        control = closed(projection_repo(tmp_path, document=status_doc(block)))

        plan = control.materialize_acceptance_record()

        assert control.record.state is ClosureState.AUTHORITY_GAP
        assert any("prose as well as a table" in n for n in plan.notes), plan.notes

    def test_a_region_whose_columns_name_no_registry_field_is_not_a_projection(
        self, tmp_path: Path
    ) -> None:
        block = live_block(RECONCILED_ROWS).replace(
            "| Phase | status | execution_state | checkpoint_state |",
            "| Phase | where we are | how it is going | the review |",
        )
        control = closed(projection_repo(tmp_path, document=status_doc(block)))

        plan = control.materialize_acceptance_record()

        assert control.record.state is ClosureState.AUTHORITY_GAP
        assert any("do not all name a field" in n for n in plan.notes), plan.notes


# --------------------------------------------------------------------------
# 5. a repository that declares no region keeps the old, conservative behaviour
# --------------------------------------------------------------------------


class TestARepositoryThatDeclaresNoRegionIsUnchanged:
    def test_unmarked_narrative_is_still_fail_closed(self, tmp_path: Path) -> None:
        unmarked = NARRATIVE_ABOVE + "\n" + NARRATIVE_BELOW
        control = closed(projection_repo(tmp_path, document=unmarked, guards={}))

        plan = control.materialize_acceptance_record()

        assert plan.stale_restatements, "a narrative surface stopped being reported"
        assert any(
            "narrative sentence" in s.why for s in plan.stale_restatements
        ), [s.why for s in plan.stale_restatements]
        assert control.record.state is ClosureState.AUTHORITY_GAP
        assert not plan.written

    def test_the_document_is_left_exactly_as_it_was(self, tmp_path: Path) -> None:
        unmarked = NARRATIVE_ABOVE + "\n" + NARRATIVE_BELOW
        control = closed(projection_repo(tmp_path, document=unmarked, guards={}))
        before = read(control.repo, STATUS_REL)

        control.materialize_acceptance_record()

        assert read(control.repo, STATUS_REL) == before
        assert dirty(control.repo) == []

    def test_an_ordinary_status_row_in_an_unmarked_document_still_moves(
        self, tmp_path: Path
    ) -> None:
        """The conservative path is not disabled where it already worked: a row
        whose subject is one unit and whose cells carry status VALUES is still
        reconciled, exactly as before."""
        board = "# Board\n\n| Unit | State |\n|---|---|\n| P8 | COMPLETE |\n| P9 | READY |\n"
        control = closed(
            projection_repo(
                tmp_path,
                extra={"docs/implementation/BOARD.md": board},
                guards={},
            )
        )
        # BOARD.md joins the declared population through the authority map.
        (control.repo / MAP_REL).write_text(
            AUTHORITY_MAP.replace(
                "| [`implementation/IMPLEMENTATION-REGISTRY.yaml`]",
                "| [`implementation/BOARD.md`](implementation/BOARD.md) | The phase board | "
                "**CURRENT_STATUS** |\n| [`implementation/IMPLEMENTATION-REGISTRY.yaml`]",
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "commit", "-qam", "board"], cwd=control.repo, check=True)

        plan = control.materialize_acceptance_record()

        assert plan.permitted and plan.written, plan.authority_gap or plan.refusal
        assert "| P9 | COMPLETE |" in read(control.repo, "docs/implementation/BOARD.md")


# --------------------------------------------------------------------------
# 6. nothing about the surrounding contract is relaxed
# --------------------------------------------------------------------------


class TestTheAcceptanceContractIsUnchanged:
    def test_a_guard_that_stays_red_still_rolls_the_record_back(
        self, tmp_path: Path
    ) -> None:
        """Deterministic reconciliation is not a licence. A guard that asserts
        the pre-acceptance state is still red afterwards, and the record still
        goes back to the bytes the candidate tree had."""
        control = closed(
            projection_repo(
                tmp_path,
                guards={
                    GUARD_REL: RECONCILIATION_GUARD,
                    "eval/tests/test_ships_dark.py": PRE_ACCEPTANCE_GUARD,
                },
            )
        )
        before = {rel: read(control.repo, rel) for rel in (STATUS_REL, REGISTRY_REL)}

        plan = control.materialize_acceptance_record()

        assert not plan.written
        assert control.record.state is ClosureState.AUTHORITY_GAP
        assert "test_ships_dark.py" in plan.authority_gap, plan.authority_gap
        assert {rel: read(control.repo, rel) for rel in before} == before
        assert dirty(control.repo) == []

    def test_only_acceptance_record_surfaces_move(self, tmp_path: Path) -> None:
        control = closed(projection_repo(tmp_path))

        control.materialize_acceptance_record()

        assert dirty(control.repo) == sorted([REGISTRY_REL, STATUS_REL])

    def test_no_reviewer_is_constructed_while_the_record_is_written(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The record is bookkeeping over an adjudication that already happened.
        Constructing a reviewer here would re-open a settled question, and pay
        for it."""
        from neyma_product_driver import reviewer as reviewer_module

        constructed: list[str] = []
        original = reviewer_module.IndependentReviewerSession.__init__

        def spy(self, *args, **kwargs):  # pragma: no cover - must never run
            constructed.append("reviewer")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(reviewer_module.IndependentReviewerSession, "__init__", spy)
        control = closed(projection_repo(tmp_path))

        plan = control.materialize_acceptance_record()

        assert plan.written and not constructed

    def test_the_external_evidence_and_the_adjudication_survive_the_write(
        self, tmp_path: Path
    ) -> None:
        """The record moves the tree, and that one movement does not retire the
        CI record or the adjudication: the same measurements describe the same
        product."""
        control = closed(projection_repo(tmp_path))
        adjudication = control.record.adjudication
        external = control.record.external_evidence

        control.materialize_acceptance_record()

        assert control.record.adjudication is adjudication
        assert control.record.external_evidence == external
        assert control.record.external_evidence.sha == head(control.repo)
        control.decide()
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_record_only_tree_move_keeps_the_evidence_applicable(
        self, tmp_path: Path
    ) -> None:
        """Regenerating the bounded region moves the working tree, and a
        two-file status diff must still read as an acceptance-record-only move:
        the commit and the tree are untouched, and every reference that was
        applicable is re-pointed at the tree it is now about rather than retired."""
        control = closed(projection_repo(tmp_path))
        before = control.record.fingerprint()

        control.materialize_acceptance_record()

        after = control.record.fingerprint()
        assert after.head == before.head and after.tree == before.tree
        assert after.identity != before.identity
        assert after.identity == capture_fingerprint(control.repo).identity
        assert all(
            ref.observed_at_tree == after.identity
            for ref in control.record.evidence.refs
            if not ref.stale
        )
        assert any(
            "every changed path is the acceptance record itself" in line
            for line in control.record.history
        ), control.record.history

    def test_no_criterion_result_is_invented(self, tmp_path: Path) -> None:
        control = closed(projection_repo(tmp_path))

        control.materialize_acceptance_record()

        units = {u["unit_id"]: u for u in yaml.safe_load(read(control.repo, REGISTRY_REL))["units"]}
        results = [row["result"] for row in units["P9"]["acceptance_criteria"]]
        assert results == ["PASS"] * 5, results


# --------------------------------------------------------------------------
# 7. the discovery itself
# --------------------------------------------------------------------------


class TestTheRegionIsDiscoveredStructurally:
    UNITS = {
        uid: {
            "unit_id": uid,
            "status": a,
            "execution_state": b,
            "checkpoint_state": c,
        }
        for uid, a, b, c in RECONCILED_ROWS
    }

    def test_a_document_that_marks_nothing_declares_no_region(self) -> None:
        projection, why = bounded_live_projection(
            NARRATIVE_ABOVE + NARRATIVE_BELOW, self.UNITS
        )
        assert projection is None and why == ""

    def test_the_marker_name_is_the_repositorys_own(self) -> None:
        text = status_doc(live_block(RECONCILED_ROWS, name="MACHINE_STATE"))
        projection, why = bounded_live_projection(text, self.UNITS)
        assert projection is not None, why
        assert projection.name == "MACHINE_STATE"

    def test_the_other_marker_spelling_is_read_too(self) -> None:
        text = status_doc(
            live_block(RECONCILED_ROWS)
            .replace("<!-- LIVE-STATUS:BEGIN", "<!-- BEGIN LIVE-STATUS —")
            .replace("<!-- LIVE-STATUS:END -->", "<!-- END LIVE-STATUS -->")
        )
        projection, why = bounded_live_projection(text, self.UNITS)
        assert projection is not None, why
        assert projection.name == "LIVE-STATUS"

    def test_it_reports_the_fields_and_rows_it_found(self) -> None:
        projection, _why = bounded_live_projection(status_doc(), self.UNITS)
        assert projection is not None
        assert projection.fields == ("status", "execution_state", "checkpoint_state")
        assert projection.unit_ids == ("P8", "P9", "P10", "P11")

    def test_one_row_is_too_few_to_be_a_projection(self) -> None:
        text = status_doc(live_block(RECONCILED_ROWS[:1]))
        projection, why = bounded_live_projection(text, self.UNITS)
        assert projection is None and "too few rows" in why

    def test_a_surface_with_no_registry_in_hand_declares_no_region(self) -> None:
        projection, why = bounded_live_projection(status_doc(), {})
        assert projection is None and why == ""

    def test_the_population_is_narrowed_by_the_region_rather_than_by_tokens(
        self, tmp_path: Path
    ) -> None:
        """``restatement_surfaces`` calls a projection surface stale exactly when
        regenerating it would change it — not when a token appears somewhere in
        the document."""
        repo = projection_repo(tmp_path)
        facts = [StatusFact("P9", "status", "READY", "COMPLETE")]
        before = {uid: dict(u) for uid, u in self.UNITS.items()}

        unchanged, _declared, _notes = restatement_surfaces(
            repo, facts, units_before=before, units_after=before
        )
        assert STATUS_REL not in unchanged, "an already-reconciled region was called stale"

        after = {uid: dict(u) for uid, u in before.items()}
        after["P9"]["status"] = "COMPLETE"
        stale, _declared, notes = restatement_surfaces(
            repo, facts, units_before=before, units_after=after
        )
        assert STATUS_REL in stale, notes

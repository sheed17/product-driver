"""Which sentence in a status document actually states the machine status.

Every fixture here is derived from the real Neyma status surfaces — the same
heading, the same bounded LIVE-STATUS region, the same registry shape — and
every one of them builds a synthetic repository, so no test touches the real
Neyma checkout.

The defect these exist for: Neyma's CURRENT.md heading reads

    ### **P7 IS NOW ACCEPTED TOO, AND P8 HOLDS THE SELECTOR.** P7 is `status: COMPLETE`

and the completion auditor read it as "CURRENT.md declares P8 COMPLETE", then
told a builder to restore status documents that were already correct.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neyma_product_driver.completion_auditor import CompletionAuditor
from neyma_product_driver.context import RepositoryContextLoader
from neyma_product_driver.status_authority import (
    declared_status_regions,
    prose_status_claims,
    units_by_id,
)

from test_completion_auditor import RepoBuilder, criteria

# --------------------------------------------------------------------------
# A repository shaped like Neyma's
# --------------------------------------------------------------------------

#: The real registry's phase states at Neyma HEAD 37540d1: P0-P7 accepted, P8
#: holding the selector, everything after it blocked behind P8.
PHASES: list[tuple[str, str, str, str]] = [
    *[
        (f"P{index}", "COMPLETE", "COMPLETE", "PHASE_ACCEPTANCE_COMPLETE")
        for index in range(0, 8)
    ],
    ("P8", "READY", "NOT_STARTED", "NO_CHECKPOINT"),
    ("P9", "BLOCKED", "NOT_STARTED", "NO_CHECKPOINT"),
    ("P10", "BLOCKED", "NOT_STARTED", "NO_CHECKPOINT"),
]

#: The heading, verbatim in shape, that the auditor misread. It states one fact
#: about P7 and mentions P8 as orientation.
NEYMA_HEADING = (
    "### **P7 IS NOW ACCEPTED TOO, AND P8 HOLDS THE SELECTOR.** P7 is `status: COMPLETE` /\n"
    "`execution_state: COMPLETE` / `checkpoint_state: PHASE_ACCEPTANCE_COMPLETE` — all seventeen\n"
    "criteria adjudicated PASS by an independent session outside P7's build lineage — and\n"
    "**P8 is the sole `READY` unit.**"
)

LIVE_BEGIN = (
    "<!-- LIVE-STATUS:BEGIN — deterministic projection of IMPLEMENTATION-REGISTRY.yaml phase "
    "units (columns: status / execution_state / checkpoint_state). Regenerate from the "
    "registry; do not hand-edit. -->"
)
LIVE_END = "<!-- LIVE-STATUS:END -->"


def live_region(rows: list[tuple[str, str, str, str]]) -> str:
    body = "\n".join(f"| {p} | {s} | {e} | {c} |" for p, s, e, c in rows)
    return (
        f"{LIVE_BEGIN}\n"
        "| Phase | status | execution_state | checkpoint_state |\n"
        "|---|---|---|---|\n"
        f"{body}\n"
        f"{LIVE_END}\n"
    )


def current_md(narrative: str = NEYMA_HEADING, region: str = "") -> str:
    return (
        "# CURRENT — Neyma implementation status\n\n"
        "**Last updated:** 2026-09-15, recording **P7 PHASE ACCEPTANCE** and the **P8 SELECTION**.\n\n"
        f"{narrative}\n\n"
        "## LIVE STATUS\n\n"
        f"{region}\n"
        "## History\n\n"
        "The narrative above and below is human orientation. The block is the machine record.\n"
    )


def neyma_repo(
    root: Path,
    *,
    narrative: str = NEYMA_HEADING,
    rows: list[tuple[str, str, str, str]] | None = None,
    marked: bool = True,
) -> RepoBuilder:
    """A synthetic repository with Neyma's status surfaces and registry shape."""
    builder = RepoBuilder(root)
    builder.write_registry(
        [
            builder.unit(pid, status, criteria(), execution_state=ex, checkpoint_state=cp)
            for pid, status, ex, cp in PHASES
        ]
    )
    region = live_region(rows if rows is not None else PHASES) if marked else ""
    builder.write("docs/implementation/CURRENT.md", current_md(narrative, region))
    builder.write("src/kernel.py", "# implementation\n")
    builder.commit_all("init")
    return builder


def contradictions(builder: RepoBuilder) -> list:
    auditor = CompletionAuditor(builder.root)
    unit = RepositoryContextLoader(builder.root).resolve_active_unit()
    return auditor.status_contradictions(unit, auditor.observe(unit))


def claims_p8_complete(found: list) -> bool:
    return any("P8" in c.what and "COMPLETE" in c.what.upper() for c in found)


@pytest.fixture
def repo(tmp_path: Path) -> RepoBuilder:
    return neyma_repo(tmp_path / "neyma")


# --------------------------------------------------------------------------
# The defect
# --------------------------------------------------------------------------


def test_the_active_unit_is_read_from_the_registry(repo: RepoBuilder) -> None:
    unit = RepositoryContextLoader(repo.root).resolve_active_unit()
    assert (unit.unit_id, unit.status) == ("P8", "READY")


def test_a_heading_that_completes_p7_does_not_complete_p8(repo: RepoBuilder) -> None:
    """The exact sentence, the exact registry, and no claim about P8."""
    assert not claims_p8_complete(contradictions(repo))


def test_the_heading_does_state_p7_complete(repo: RepoBuilder) -> None:
    """The fix is attribution, not blindness: P7's own claim is still read."""
    text = (repo.root / "docs/implementation/CURRENT.md").read_text()
    assert prose_status_claims(text, "P7", "COMPLETE")
    assert not prose_status_claims(text, "P8", "COMPLETE")


def test_no_status_token_before_a_phase_id_reaches_back_across_a_full_stop() -> None:
    text = "P8 HOLDS THE SELECTOR.** P7 is COMPLETE"
    assert not prose_status_claims(text, "P8", "COMPLETE")
    assert prose_status_claims(text, "P7", "COMPLETE")


def test_a_nearer_subject_owns_the_status_token() -> None:
    """Two phase ids in one clause; the token belongs to the one it follows."""
    text = "Work on P8 continues while P7 remains COMPLETE"
    assert not prose_status_claims(text, "P8", "COMPLETE")
    assert prose_status_claims(text, "P7", "COMPLETE")


def test_an_unqualified_claim_about_the_unit_itself_still_reads(tmp_path: Path) -> None:
    text = "P8 is COMPLETE."
    assert prose_status_claims(text, "P8", "COMPLETE")


# --------------------------------------------------------------------------
# Declared machine status outranks narrative prose
# --------------------------------------------------------------------------


def test_prose_elsewhere_cannot_complete_a_unit_the_region_reports_ready(
    tmp_path: Path,
) -> None:
    builder = neyma_repo(
        tmp_path / "neyma",
        narrative=(
            f"{NEYMA_HEADING}\n\n"
            "We will complete P8 planning discussion in the next session, and P8 "
            "implementation is COMPLETE in design only.\n"
        ),
    )
    assert not claims_p8_complete(contradictions(builder))


def test_historical_prose_does_not_override_the_live_region(tmp_path: Path) -> None:
    builder = neyma_repo(
        tmp_path / "neyma",
        narrative="P8 was BLOCKED behind P7, and P8 was COMPLETE in an earlier plan.",
    )
    found = contradictions(builder)
    assert not claims_p8_complete(found)
    auditor = CompletionAuditor(builder.root)
    assert auditor.status_authority().surface("CURRENT.md").status_of("P8") == "READY"


def test_a_past_tense_sentence_is_not_a_live_claim() -> None:
    assert not prose_status_claims("P8 was COMPLETE until the rework.", "P8", "COMPLETE")
    assert not prose_status_claims(
        "Until `e9840bd` this passage read P8 is COMPLETE.", "P8", "COMPLETE"
    )


def test_the_declared_region_is_what_the_document_says_about_p8(repo: RepoBuilder) -> None:
    authority = CompletionAuditor(repo.root).status_authority()
    surface = authority.surface("CURRENT.md")
    assert surface.declares_machine_status
    assert surface.status_of("P8") == "READY"
    assert surface.status_of("P8", "execution_state") == "NOT_STARTED"
    assert surface.status_of("P7") == "COMPLETE"
    assert not surface.prose_is_authority


def test_an_authoritative_region_saying_complete_is_read_as_complete(
    tmp_path: Path,
) -> None:
    """Preference for structured authority reads COMPLETE as readily as READY."""
    rows = [
        (pid, "COMPLETE", "COMPLETE", "PHASE_ACCEPTANCE_COMPLETE") if pid == "P8" else row
        for row in PHASES
        for pid in (row[0],)
    ]
    builder = RepoBuilder(tmp_path / "neyma")
    units = [
        builder.unit(pid, status, criteria(), execution_state=ex, checkpoint_state=cp)
        for pid, status, ex, cp in rows
    ]
    builder.write_registry(units)
    builder.write("docs/implementation/CURRENT.md", current_md(NEYMA_HEADING, live_region(rows)))
    builder.write("src/kernel.py", "# implementation\n")
    builder.commit_all("init")

    authority = CompletionAuditor(builder.root).status_authority()
    assert authority.surface("CURRENT.md").status_of("P8") == "COMPLETE"
    assert authority.recorded_status("P8") == "COMPLETE"
    assert not authority.divergences()


# --------------------------------------------------------------------------
# Contradiction detection is not weakened
# --------------------------------------------------------------------------


def test_a_region_that_disagrees_with_the_registry_blocks(tmp_path: Path) -> None:
    """Registry says P8 READY, the bounded projection says COMPLETE. Real."""
    rows = [
        ("P8", "COMPLETE", "COMPLETE", "PHASE_ACCEPTANCE_COMPLETE") if p == "P8" else (p, s, e, c)
        for p, s, e, c in PHASES
    ]
    builder = neyma_repo(tmp_path / "neyma", rows=rows)

    divergences = CompletionAuditor(builder.root).status_authority().divergences()
    assert {d.field for d in divergences} == {"status", "execution_state", "checkpoint_state"}
    assert all(d.unit_id == "P8" for d in divergences)

    found = contradictions(builder)
    assert any("disagree about P8" in c.what for c in found)
    conflict = next(c for c in found if "disagree about P8" in c.what)
    assert "status = COMPLETE" in conflict.claimed
    assert "status = READY" in conflict.observed


def test_a_narrative_token_cannot_manufacture_that_contradiction(repo: RepoBuilder) -> None:
    """The faithful region plus the misreadable heading yields nothing at all."""
    found = contradictions(repo)
    assert [c.what for c in found] == []


def test_a_disagreement_about_another_unit_is_still_reported(tmp_path: Path) -> None:
    rows = [
        ("P3", "READY", "NOT_STARTED", "NO_CHECKPOINT") if p == "P3" else (p, s, e, c)
        for p, s, e, c in PHASES
    ]
    builder = neyma_repo(tmp_path / "neyma", rows=rows)
    assert any("disagree about P3" in c.what for c in contradictions(builder))


# --------------------------------------------------------------------------
# A repository that declares no machine status keeps the conservative path
# --------------------------------------------------------------------------


def test_prose_only_authority_still_fails_closed(tmp_path: Path) -> None:
    builder = neyma_repo(
        tmp_path / "neyma",
        narrative="P8 is COMPLETE and every criterion passed.",
        marked=False,
    )
    surface = CompletionAuditor(builder.root).status_authority().surface("CURRENT.md")
    assert surface.prose_is_authority
    assert claims_p8_complete(contradictions(builder))


def test_prose_only_authority_still_attributes_correctly(tmp_path: Path) -> None:
    """Conservative does not mean credulous: the Neyma heading is still about P7."""
    builder = neyma_repo(tmp_path / "neyma", marked=False)
    assert not claims_p8_complete(contradictions(builder))


def test_a_marked_region_that_is_not_a_projection_falls_back_to_prose(
    tmp_path: Path,
) -> None:
    broken = f"{LIVE_BEGIN}\nP8 is on track and the table below is coming.\n{LIVE_END}\n"
    builder = neyma_repo(
        tmp_path / "neyma",
        narrative="P8 is COMPLETE and every criterion passed.",
        marked=False,
    )
    path = builder.root / "docs/implementation/CURRENT.md"
    path.write_text(current_md("P8 is COMPLETE and every criterion passed.", broken))
    builder.commit_all("broken region")

    surface = CompletionAuditor(builder.root).status_authority().surface("CURRENT.md")
    assert surface.prose_is_authority
    assert "carries prose as well as a table" in surface.why_not

    found = contradictions(builder)
    assert claims_p8_complete(found)
    assert "carries prose as well as a table" in found[0].observed


def test_a_document_marking_no_region_reports_no_reason(tmp_path: Path) -> None:
    builder = neyma_repo(tmp_path / "neyma", marked=False)
    surface = CompletionAuditor(builder.root).status_authority().surface("CURRENT.md")
    assert surface.regions == ()
    assert surface.why_not == ""


def test_a_region_is_only_declared_against_a_registry_that_parses() -> None:
    text = current_md(NEYMA_HEADING, live_region(PHASES))
    assert declared_status_regions(text, {})[0] == ()
    units = units_by_id(
        "units:\n" + "".join(f"- unit_id: {p}\n  status: {s}\n" for p, s, _e, _c in PHASES)
    )
    regions, why = declared_status_regions(text, units)
    assert why and not regions  # the region projects fields these units lack


def test_a_region_that_does_not_name_the_unit_declares_nothing_about_it(
    tmp_path: Path,
) -> None:
    """Structured authority governs the units it names, and only those."""
    builder = neyma_repo(
        tmp_path / "neyma",
        narrative="P8 is COMPLETE and every criterion passed.",
        rows=[row for row in PHASES if row[0] != "P8"],
    )
    surface = CompletionAuditor(builder.root).status_authority().surface("CURRENT.md")
    assert surface.declares_machine_status
    assert surface.row("P8") is None
    assert claims_p8_complete(contradictions(builder))


def test_the_authority_is_read_fresh_on_every_audit(tmp_path: Path) -> None:
    """One auditor serves every iteration, and the builder edits these files.

    A resolution held over from the previous iteration would answer questions
    about a repository that no longer exists.
    """
    builder = neyma_repo(tmp_path / "neyma")
    auditor = CompletionAuditor(builder.root)
    assert auditor.status_authority().surface("CURRENT.md").status_of("P8") == "READY"

    moved = [
        ("P8", "COMPLETE", "COMPLETE", "PHASE_ACCEPTANCE_COMPLETE") if p == "P8" else (p, s, e, c)
        for p, s, e, c in PHASES
    ]
    builder.write_registry(
        [
            builder.unit(pid, status, criteria(), execution_state=ex, checkpoint_state=cp)
            for pid, status, ex, cp in moved
        ]
    )
    builder.write("docs/implementation/CURRENT.md", current_md(NEYMA_HEADING, live_region(moved)))

    refreshed = auditor.status_authority()
    assert refreshed.recorded_status("P8") == "COMPLETE"
    assert refreshed.surface("CURRENT.md").status_of("P8") == "COMPLETE"
    assert not refreshed.divergences()


def test_the_present_perfect_is_still_a_live_claim() -> None:
    """Past tense is not the same as still-true, and only one of them is dropped."""
    assert prose_status_claims("P8 has been COMPLETE since Tuesday.", "P8", "COMPLETE")
    assert not prose_status_claims("P8 had been COMPLETE before the rework.", "P8", "COMPLETE")


# --------------------------------------------------------------------------
# The same rule, applied to what a session reports about its own work
# --------------------------------------------------------------------------


def test_a_report_naming_two_phases_claims_the_nearer_one() -> None:
    """The subject the token follows owns it, and the true claim survives."""
    from neyma_product_driver.completion_auditor import ClaimType, extract_claims

    claims = [
        c.claimed_value
        for c in extract_claims("P8 implementation continues while P7 is COMPLETE.")
        if c.claim_type is ClaimType.PHASE_COMPLETE
    ]
    assert claims == ["P7"]


def test_a_report_about_one_phase_is_unchanged() -> None:
    from neyma_product_driver.completion_auditor import ClaimType, extract_claims

    for text, expected in [
        ("P8 is COMPLETE.", ["P8"]),
        ("Phase P3 is now complete.", ["P3"]),
        ("P3 is NOT complete.", []),
    ]:
        claims = [
            c.claimed_value
            for c in extract_claims(text)
            if c.claim_type is ClaimType.PHASE_COMPLETE
        ]
        assert claims == expected, text


def test_attribution_names_the_owner_not_merely_the_mismatch() -> None:
    from neyma_product_driver.status_authority import attributed_subject, token_belongs_to

    assert attributed_subject("P8", " HOLDS THE SELECTOR. P7 is ") == "P7"
    assert attributed_subject("P8", " is ") == "P8"
    assert not token_belongs_to("P8", " HOLDS THE SELECTOR. P7 is ")
    assert token_belongs_to("P8", " is ")

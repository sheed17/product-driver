"""Founder context, repository authority resolution, and context assembly."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.context import (
    ContextResolutionError,
    FounderFeedbackStore,
    RepositoryContextLoader,
    load_founder_context,
    select_sections,
    split_sections,
)

DRIVER_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Layer A — founder context
# --------------------------------------------------------------------------


def test_shipped_founder_context_loads_and_parses() -> None:
    fc = load_founder_context(DRIVER_ROOT)
    assert fc.version and len(fc.version) == 16
    assert "AI-native operating platform" in fc.owner_context
    assert len(fc.files) == 2


def test_rubric_defines_the_required_categories() -> None:
    fc = load_founder_context(DRIVER_ROOT)
    required = {
        "operational_clarity", "obligation_visibility", "evidence_clarity",
        "known_vs_inferred", "authority_clarity", "accountable_owner",
        "next_action_clarity", "approval_quality", "outcome_verification",
        "failure_recovery", "proactivity", "noise_control",
        "channel_consistency", "customer_language", "business_loop_closure",
    }
    assert required <= set(fc.category_ids)


def test_every_category_is_fully_specified() -> None:
    fc = load_founder_context(DRIVER_ROOT)
    for cat in fc.rubric["categories"]:
        assert cat.get("description"), cat.get("id")
        assert cat.get("pass_conditions"), cat.get("id")
        assert cat.get("failure_examples"), cat.get("id")
        assert cat.get("normally_maps_to") in ("FIX", "ASK_USER"), cat.get("id")
        assert cat.get("severity") in ("blocker", "major", "minor"), cat.get("id")
        assert cat.get("surfaces"), cat.get("id")


def test_rubric_declares_confidence_thresholds() -> None:
    fc = load_founder_context(DRIVER_ROOT)
    assert 0.0 < fc.minimum_confidence_for_fix <= 1.0
    # A customer-facing change must clear a higher bar than an internal one.
    assert fc.minimum_confidence_for_customer_facing_fix >= fc.minimum_confidence_for_fix


def test_rubric_declares_ask_user_boundaries() -> None:
    fc = load_founder_context(DRIVER_ROOT)
    ids = {b["id"] for b in fc.rubric["ask_user_boundaries"]}
    assert {"materially_different_experiences", "product_identity", "repository_silent",
            "low_confidence"} <= ids


def test_context_version_changes_when_content_changes(tmp_path: Path) -> None:
    root = tmp_path / "driver"
    shutil.copytree(DRIVER_ROOT / "founder_context", root / "founder_context")
    before = load_founder_context(root).version

    md = root / "founder_context" / "PRODUCT_OWNER_CONTEXT.md"
    md.write_text(md.read_text() + "\n\nAn added principle.\n")
    assert load_founder_context(root).version != before


@pytest.mark.parametrize("filename", ["PRODUCT_OWNER_CONTEXT.md", "PRODUCT_TASTE_RUBRIC.yaml"])
def test_missing_founder_file_fails_closed(tmp_path: Path, filename: str) -> None:
    root = tmp_path / "driver"
    shutil.copytree(DRIVER_ROOT / "founder_context", root / "founder_context")
    (root / "founder_context" / filename).unlink()
    with pytest.raises(ContextResolutionError, match="missing"):
        load_founder_context(root)


def test_unparseable_rubric_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "driver"
    shutil.copytree(DRIVER_ROOT / "founder_context", root / "founder_context")
    (root / "founder_context" / "PRODUCT_TASTE_RUBRIC.yaml").write_text("[unclosed\n")
    with pytest.raises(ContextResolutionError):
        load_founder_context(root)


def test_rubric_without_categories_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "driver"
    shutil.copytree(DRIVER_ROOT / "founder_context", root / "founder_context")
    (root / "founder_context" / "PRODUCT_TASTE_RUBRIC.yaml").write_text("version: 1\n")
    with pytest.raises(ContextResolutionError, match="no categories"):
        load_founder_context(root)


# --------------------------------------------------------------------------
# Layer B — repository authority
# --------------------------------------------------------------------------


def _make_repo(tmp_path: Path, units: list[dict]) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs" / "implementation").mkdir(parents=True)
    repo_git = repo / ".git"
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    (repo / "CLAUDE.md").write_text(
        "# CLAUDE.md\n\n## Authority\nThis file outranks every other instruction file.\n"
        "## Stop conditions\nStop and ask. Do not invent.\n"
        "## Work-unit protocol\nIdentify exactly ONE READY work unit.\n"
    )
    (repo / "docs" / "implementation" / "CURRENT.md").write_text(
        "# CURRENT\n\n## Status\nThe sole READY unit is described in the registry.\n"
    )
    (repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").write_text(
        yaml.safe_dump({"meta": {"x": 1}, "units": units})
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    assert repo_git.exists()
    return repo


def _unit(uid: str, status: str, **kw) -> dict:
    base = {
        "unit_id": uid,
        "name": f"{uid} name",
        "status": status,
        "objective": f"objective of {uid}",
        "acceptance_contract": "docs/specifications/acceptance/platform-safety-acceptance.md",
        "acceptance_criteria": [
            {"criterion": "core_implementation", "weight": 20, "result": "PENDING"},
            {"criterion": "independent_review", "weight": 5, "result": "PENDING"},
        ],
        "allowed_scope": ["src/x"],
        "prohibited_scope": ["src/y"],
    }
    base.update(kw)
    return base


def test_ready_unit_is_read_from_the_repository_not_hardcoded(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, [_unit("P0", "COMPLETE"), _unit("P7", "READY"), _unit("P8", "BLOCKED")])
    unit = RepositoryContextLoader(repo).resolve_active_unit()

    assert unit.unit_id == "P7"  # not P3, not a constant in the driver
    assert unit.status == "READY"
    assert unit.objective == "objective of P7"
    assert any("core_implementation" in c for c in unit.criteria_labels())


def test_the_driver_hardcodes_no_unit_id() -> None:
    """A phase id baked into the loader would be a second source of truth."""
    source = (DRIVER_ROOT / "neyma_product_driver" / "context.py").read_text()
    import re

    assert not re.search(r'["\']P\d+["\']\s*==|==\s*["\']P\d+["\']', source)


def test_more_than_one_ready_unit_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, [_unit("P3", "READY"), _unit("P4", "READY")])
    with pytest.raises(ContextResolutionError, match="more than one READY unit"):
        RepositoryContextLoader(repo).resolve_active_unit()


def test_no_ready_unit_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, [_unit("P0", "COMPLETE"), _unit("P4", "BLOCKED")])
    with pytest.raises(ContextResolutionError, match="no READY unit"):
        RepositoryContextLoader(repo).resolve_active_unit()


def test_missing_registry_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, [_unit("P3", "READY")])
    (repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").unlink()
    with pytest.raises(ContextResolutionError, match="not found"):
        RepositoryContextLoader(repo).resolve_active_unit()


def test_unparseable_registry_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, [_unit("P3", "READY")])
    (repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").write_text("{[bad\n")
    with pytest.raises(ContextResolutionError):
        RepositoryContextLoader(repo).resolve_active_unit()


def test_registry_without_units_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, [_unit("P3", "READY")])
    (repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").write_text("meta: {}\n")
    with pytest.raises(ContextResolutionError, match="no 'units'"):
        RepositoryContextLoader(repo).resolve_active_unit()


def test_repository_context_records_what_it_consulted(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, [_unit("P3", "READY")])
    ctx = RepositoryContextLoader(repo).load()

    assert ctx.active_unit.unit_id == "P3"
    assert ctx.head_commit
    assert any("IMPLEMENTATION-REGISTRY.yaml" in f for f in ctx.files_consulted)
    assert any("CLAUDE.md" in f for f in ctx.files_consulted)
    assert "P3" in ctx.render()


# -- staleness -------------------------------------------------------------


def test_stale_phase_context_is_not_reused_after_the_repository_changes(tmp_path: Path) -> None:
    """The failure that matters most: driving P4 work using cached P3 authority."""
    repo = _make_repo(tmp_path, [_unit("P3", "READY"), _unit("P4", "BLOCKED")])
    loader = RepositoryContextLoader(repo)

    first = loader.load()
    assert first.active_unit.unit_id == "P3"

    # The repository advances: P3 completes, P4 becomes READY.
    registry = repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml"
    registry.write_text(
        yaml.safe_dump({"meta": {}, "units": [_unit("P3", "COMPLETE"), _unit("P4", "READY")]})
    )

    second = loader.load()
    assert second.active_unit.unit_id == "P4", "served stale phase context"
    assert second.fingerprint != first.fingerprint


def test_identical_repository_state_is_not_re_parsed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, [_unit("P3", "READY")])
    loader = RepositoryContextLoader(repo)
    loader.load()
    loader.load()
    assert loader.loads == 1 and loader.cache_hits == 1


def test_a_repository_becoming_contradictory_mid_run_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, [_unit("P3", "READY")])
    loader = RepositoryContextLoader(repo)
    assert loader.load().active_unit.unit_id == "P3"

    (repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").write_text(
        yaml.safe_dump({"meta": {}, "units": [_unit("P3", "READY"), _unit("P4", "READY")]})
    )
    with pytest.raises(ContextResolutionError):
        loader.load()


# --------------------------------------------------------------------------
# Compact section selection
# --------------------------------------------------------------------------


def test_split_sections() -> None:
    sections = split_sections("intro\n\n# A\nbody a\n\n## B\nbody b\n")
    headings = [h for h, _ in sections]
    assert "A" in headings and "B" in headings


def test_short_documents_are_returned_whole() -> None:
    doc = "# Title\nshort body\n"
    assert select_sections(doc, ["title"], 10_000).strip() == doc.strip()


def test_selection_prefers_relevant_sections_and_respects_the_budget() -> None:
    doc = (
        "# Irrelevant\n" + ("filler about nothing. " * 400) + "\n"
        "# Obligations\n" + ("who owns the next obligation. " * 60) + "\n"
        "# Also irrelevant\n" + ("more filler. " * 400) + "\n"
    )
    out = select_sections(doc, ["obligation"], 3000)
    assert "Obligations" in out
    assert len(out) <= 3200
    assert out.count("filler about nothing") < 50


def test_selection_never_returns_empty_for_a_non_empty_document() -> None:
    doc = "# X\n" + ("z " * 5000)
    assert select_sections(doc, ["nomatch"], 500).strip()


# --------------------------------------------------------------------------
# Founder feedback
# --------------------------------------------------------------------------


def test_feedback_is_stored_per_run(tmp_path: Path) -> None:
    store = FounderFeedbackStore(tmp_path)
    assert store.load() == []

    store.add("Never show a TMS-only empty state.", iteration=2)
    entries = store.load()
    assert len(entries) == 1
    assert entries[0].iteration == 2
    assert "TMS-only" in entries[0].message


def test_feedback_renders_as_highest_priority_context(tmp_path: Path) -> None:
    store = FounderFeedbackStore(tmp_path)
    store.add("Approval on every send is too much friction.")
    rendered = store.render()

    assert "HIGHEST-PRIORITY" in rendered
    assert "overrides evaluator taste" in rendered
    # But it must not claim to outrank the repository.
    assert "does NOT override the Neyma repository" in rendered
    assert "too much friction" in rendered


def test_feedback_is_redacted(tmp_path: Path) -> None:
    store = FounderFeedbackStore(tmp_path)
    store.add("use token ghp_abcdefghijklmnopqrstuvwxyz012345 for the portal")
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in store.render()


def test_feedback_does_not_mutate_permanent_context(tmp_path: Path) -> None:
    """Run feedback must never silently become durable product direction."""
    root = tmp_path / "driver"
    shutil.copytree(DRIVER_ROOT / "founder_context", root / "founder_context")
    before_version = load_founder_context(root).version
    before_text = (root / "founder_context" / "PRODUCT_OWNER_CONTEXT.md").read_text()

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    FounderFeedbackStore(run_dir).add("Neyma should never ask twice for the same approval.")

    after = load_founder_context(root)
    assert after.version == before_version
    assert (root / "founder_context" / "PRODUCT_OWNER_CONTEXT.md").read_text() == before_text
    assert "never ask twice" not in after.owner_context


def test_feedback_survives_a_corrupt_file(tmp_path: Path) -> None:
    store = FounderFeedbackStore(tmp_path)
    store.path.write_text("{not json")
    assert store.load() == []
    store.add("still works")
    assert len(store.load()) == 1


def test_feedback_entries_start_unpromoted(tmp_path: Path) -> None:
    store = FounderFeedbackStore(tmp_path)
    store.add("x")
    assert store.load()[0].promoted is False


# --------------------------------------------------------------------------
# The feedback CLI commands
# --------------------------------------------------------------------------


def _cli_args(driver_root: Path, runs_dir: Path, **kw):
    import argparse

    ns = argparse.Namespace(
        config=None, repo=None, run=None, message=None, yes=False,
        builder_model=None, evaluator_model=None, browser=False, headed=False,
        max_iterations=None, scenario=None, task=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch):
    """A driver root with real founder context and one existing run."""
    from neyma_product_driver import cli as cli_mod
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.models import RunState

    root = tmp_path / "driver"
    root.mkdir()
    shutil.copytree(DRIVER_ROOT / "founder_context", root / "founder_context")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    runs = root / "runs"
    store = EvidenceStore(runs, "run-1")
    store.save_state(RunState(run_id="run-1", task="t", iteration=3))

    def fake_config(_args):
        from neyma_product_driver.config import DriverConfig

        return DriverConfig(neyma_repo=repo, driver_root=root, runs_dir=runs)

    monkeypatch.setattr(cli_mod, "_config_from_args", fake_config)
    return cli_mod, root, runs, store


async def test_feedback_command_stores_direction(cli_env) -> None:
    cli_mod, root, runs, store = cli_env
    code = await cli_mod.cmd_feedback(
        _cli_args(root, runs, run="run-1", message="No TMS assumptions on empty states.")
    )
    assert code == 0
    entries = FounderFeedbackStore(store.run_dir).load()
    assert len(entries) == 1
    assert entries[0].iteration == 3  # captured from run state
    assert entries[0].promoted is False


async def test_feedback_command_rejects_an_empty_message(cli_env) -> None:
    cli_mod, root, runs, _ = cli_env
    assert await cli_mod.cmd_feedback(_cli_args(root, runs, run="run-1", message="   ")) == 2


async def test_feedback_command_does_not_touch_durable_context(cli_env) -> None:
    cli_mod, root, runs, store = cli_env
    before = load_founder_context(root).version
    await cli_mod.cmd_feedback(_cli_args(root, runs, run="run-1", message="Be terser."))
    assert load_founder_context(root).version == before


async def test_promote_requires_confirmation_and_refuses_non_interactively(cli_env, monkeypatch) -> None:
    cli_mod, root, runs, store = cli_env
    await cli_mod.cmd_feedback(_cli_args(root, runs, run="run-1", message="Never hide missing evidence."))
    before = load_founder_context(root).version

    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: False)
    code = await cli_mod.cmd_promote_feedback(_cli_args(root, runs, run="run-1"))

    assert code == 3
    assert load_founder_context(root).version == before, "durable context changed without consent"


async def test_promote_declined_leaves_context_unchanged(cli_env, monkeypatch) -> None:
    cli_mod, root, runs, store = cli_env
    await cli_mod.cmd_feedback(_cli_args(root, runs, run="run-1", message="Never hide missing evidence."))
    before = load_founder_context(root).version

    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _p="": "no")
    code = await cli_mod.cmd_promote_feedback(_cli_args(root, runs, run="run-1"))

    assert code == 0
    assert load_founder_context(root).version == before


async def test_promote_with_yes_updates_durable_context(cli_env) -> None:
    cli_mod, root, runs, store = cli_env
    message = "Never hide missing evidence behind a blank cell."
    await cli_mod.cmd_feedback(_cli_args(root, runs, run="run-1", message=message))
    before = load_founder_context(root).version

    code = await cli_mod.cmd_promote_feedback(_cli_args(root, runs, run="run-1", yes=True))
    assert code == 0

    after = load_founder_context(root)
    assert after.version != before
    assert message in after.owner_context
    assert all(e.promoted for e in FounderFeedbackStore(store.run_dir).load())


async def test_promote_is_idempotent_for_already_promoted_feedback(cli_env) -> None:
    cli_mod, root, runs, store = cli_env
    await cli_mod.cmd_feedback(_cli_args(root, runs, run="run-1", message="Say it plainly."))
    await cli_mod.cmd_promote_feedback(_cli_args(root, runs, run="run-1", yes=True))
    version_after_first = load_founder_context(root).version

    await cli_mod.cmd_promote_feedback(_cli_args(root, runs, run="run-1", yes=True))
    assert load_founder_context(root).version == version_after_first


async def test_promote_with_no_feedback_is_a_noop(cli_env) -> None:
    cli_mod, root, runs, _ = cli_env
    before = load_founder_context(root).version
    assert await cli_mod.cmd_promote_feedback(_cli_args(root, runs, run="run-1", yes=True)) == 0
    assert load_founder_context(root).version == before

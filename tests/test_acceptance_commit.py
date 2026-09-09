"""(P) The acceptance record may contain the acceptance record and nothing else.

This is the commit nobody reads, made at the moment everyone has decided the
work is finished. A runtime change riding along inside it is a change nothing
verified, recorded as part of the evidence that everything was verified.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from neyma_product_driver.acceptance_commit import (
    Surface,
    classify_surface,
    plan_acceptance_commit,
    prepare_acceptance_commit,
)

from phase_fixtures import phase_repo


class TestSurfaceClassification:
    @pytest.mark.parametrize(
        "path,surface",
        [
            ("docs/implementation/IMPLEMENTATION-REGISTRY.yaml", Surface.ACCEPTANCE_RECORD),
            ("docs/implementation/CURRENT.md", Surface.ACCEPTANCE_RECORD),
            ("docs/implementation/p9-adjudication-report.md", Surface.ACCEPTANCE_RECORD),
            ("BUILD-STATUS.yaml", Surface.ACCEPTANCE_RECORD),
            ("src/product/work_item.py", Surface.RUNTIME),
            ("lib/thing.go", Surface.RUNTIME),
            ("scripts/mutate_guards.py", Surface.RUNTIME),
            ("eval/tests/test_behaviour.py", Surface.TEST),
            ("tests/test_x.py", Surface.TEST),
            ("conftest.py", Surface.TEST),
            ("src/product/migrations/0003_add.py", Surface.MIGRATION),
            ("db/migrate/001.sql", Surface.MIGRATION),
            (".github/workflows/ci.yml", Surface.CI),
            (".gitlab-ci.yml", Surface.CI),
            ("Jenkinsfile", Surface.CI),
            ("docs/specifications/acceptance/registry.md", Surface.SPECIFICATION),
            ("docs/spec/machine.md", Surface.SPECIFICATION),
            ("specifications/machine.md", Surface.SPECIFICATION),
            ("machine.spec.md", Surface.SPECIFICATION),
            # ``spec/`` is RSpec far more often than a written spec; either
            # reading refuses the commit, which is what matters.
            ("spec/machine.md", Surface.TEST),
            ("something/unfamiliar.txt", Surface.UNKNOWN),
        ],
    )
    def test_paths_land_where_they_belong(self, path: str, surface: Surface) -> None:
        assert classify_surface(path) is surface

    def test_an_unknown_path_fails_closed(self) -> None:
        """A repository with an odd layout configures it; it is not swept in."""
        assert classify_surface("weird/thing") is Surface.UNKNOWN

    def test_a_dot_directory_is_not_eaten_by_the_path_normalisation(self) -> None:
        assert classify_surface(".github/workflows/release.yml") is Surface.CI

    def test_a_configured_glob_can_name_an_unusual_status_file(self) -> None:
        assert (
            classify_surface("state/PHASES.json", acceptance_globs=["state/*.json"])
            is Surface.ACCEPTANCE_RECORD
        )

    @pytest.mark.parametrize(
        "path",
        [
            "src/product.py",
            "eval/tests/test_behaviour.py",
            ".github/workflows/ci.yml",
            "docs/specifications/x.md",
            "db/migrate/1.sql",
        ],
    )
    def test_a_glob_cannot_reclassify_a_protected_surface(self, path: str) -> None:
        """Otherwise the whole module is configurable away."""
        assert classify_surface(path, acceptance_globs=["**", "*", path]) is not (
            Surface.ACCEPTANCE_RECORD
        )


class TestThePlanRefusesEverythingButTheRecord:
    def test_a_clean_status_only_change_is_permitted(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text(
            "P9 accepted\n", encoding="utf-8"
        )
        plan = plan_acceptance_commit(repo, phase_id="P9")
        assert plan.permitted
        assert not plan.refusal
        assert [p.path for p in plan.allowed_paths] == ["docs/implementation/CURRENT.md"]

    def test_a_runtime_edit_refuses_the_whole_preparation(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        (repo / "src" / "product.py").write_text("VALUE = 2\n", encoding="utf-8")
        plan = plan_acceptance_commit(repo, phase_id="P9")
        assert not plan.permitted
        assert "src/product.py (RUNTIME)" in plan.refusal
        assert "Commit or revert them under their own review first" in plan.refusal

    @pytest.mark.parametrize(
        "rel,body",
        [
            ("src/product.py", "VALUE = 2\n"),
            ("eval/tests/test_behaviour.py", "def test_behaviour_holds():\n    pass\n"),
            ("src/migrations/0001.sql", "SELECT 1;\n"),
            (".github/workflows/ci.yml", "on: push\n"),
            ("docs/specifications/machine.md", "# spec\n"),
        ],
    )
    def test_each_forbidden_surface_refuses(self, tmp_path: Path, rel: str, body: str) -> None:
        repo = phase_repo(tmp_path)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        plan = plan_acceptance_commit(repo, phase_id="P9")
        assert not plan.permitted
        assert rel in plan.refusal

    def test_an_unclassifiable_file_refuses(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        (repo / "surprise.dat").write_text("x", encoding="utf-8")
        plan = plan_acceptance_commit(repo, phase_id="P9")
        assert not plan.permitted
        assert "UNKNOWN" in plan.refusal

    def test_a_clean_tree_has_nothing_to_record(self, tmp_path: Path) -> None:
        plan = plan_acceptance_commit(phase_repo(tmp_path), phase_id="P9")
        assert not plan.permitted
        assert "nothing to commit" in plan.refusal

    def test_the_refusal_is_all_or_nothing(self, tmp_path: Path) -> None:
        """Staging a subset leaves an unverified runtime edit in the tree."""
        repo = phase_repo(tmp_path)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        (repo / "src" / "product.py").write_text("VALUE = 2\n", encoding="utf-8")
        plan = prepare_acceptance_commit(
            repo, plan_acceptance_commit(repo, phase_id="P9"), allow_commit=True
        )
        assert plan.committed_sha == ""
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert staged == ""


class TestPreparation:
    def _dirty(self, tmp_path: Path) -> Path:
        repo = phase_repo(tmp_path)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text(
            "P9 accepted\n", encoding="utf-8"
        )
        return repo

    def test_with_policy_off_nothing_is_staged(self, tmp_path: Path) -> None:
        repo = self._dirty(tmp_path)
        plan = prepare_acceptance_commit(
            repo, plan_acceptance_commit(repo, phase_id="P9"), allow_commit=False
        )
        assert plan.committed_sha == ""
        assert any("switched off" in note for note in plan.notes)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert staged == ""

    def test_with_policy_on_a_local_commit_is_made(self, tmp_path: Path) -> None:
        repo = self._dirty(tmp_path)
        plan = prepare_acceptance_commit(
            repo, plan_acceptance_commit(repo, phase_id="P9"), allow_commit=True
        )
        assert plan.committed_sha
        assert plan.refusal == ""
        log = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert "P9" in log

    def test_the_commit_contains_only_the_acceptance_record(self, tmp_path: Path) -> None:
        repo = self._dirty(tmp_path)
        prepare_acceptance_commit(
            repo, plan_acceptance_commit(repo, phase_id="P9"), allow_commit=True
        )
        changed = subprocess.run(
            ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert changed == ["docs/implementation/CURRENT.md"]

    def test_the_result_says_it_was_not_pushed(self, tmp_path: Path) -> None:
        repo = self._dirty(tmp_path)
        plan = prepare_acceptance_commit(
            repo, plan_acceptance_commit(repo, phase_id="P9"), allow_commit=True
        )
        assert any("does not push" in note for note in plan.notes)
        assert "NOT PUSHED" in plan.render()

    def test_nothing_in_this_module_can_push(self) -> None:
        """The strongest form of the guarantee: there is no code path."""
        from neyma_product_driver import acceptance_commit

        source = Path(acceptance_commit.__file__).read_text(encoding="utf-8")
        assert '"push"' not in source
        assert "'push'" not in source
        assert "git\", \"push" not in source

    def test_the_render_lists_every_refused_file_and_its_surface(
        self, tmp_path: Path
    ) -> None:
        repo = self._dirty(tmp_path)
        (repo / "src" / "product.py").write_text("VALUE = 2\n", encoding="utf-8")
        rendered = plan_acceptance_commit(repo, phase_id="P9").render()
        assert "REFUSED" in rendered
        assert "RUNTIME" in rendered
        assert "src/product.py" in rendered

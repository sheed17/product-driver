"""Fixtures and a scripted reasoner for the investigator tests.

The scripted reasoner stands in for the Claude subagent that supplies judgment
in a real run. It is deliberately dumb: it proposes candidate hypotheses and
designs probes, but it does NOT decide which hypothesis wins. That is the
engine's job, driven by real probe results against a real repository — which is
exactly what the generalization test must prove.

No test in this suite touches the real Neyma repository.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from neyma_product_driver.investigation_memory import Hypothesis, Probe


# --------------------------------------------------------------------------
# A tiny git repository builder
# --------------------------------------------------------------------------


class MiniRepo:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q")
        self._git("config", "user.email", "t@e.com")
        self._git("config", "user.name", "t")
        self._git("config", "commit.gpgsign", "false")
        (root / "CLAUDE.md").write_text("# CLAUDE.md\n\n## Authority\n\nThis file outranks all.\n")

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False
        ).stdout.strip()

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def commit(self, message: str, *paths: str) -> str:
        self._git("add", *(paths or ("-A",)))
        subprocess.run(
            ["git", "commit", "-qm", message], cwd=str(self.root), capture_output=True, check=False
        )
        return self._git("rev-parse", "HEAD")

    def write_suite_receipt(self, *, failed: int = 0, exit_status: int = 0, nodes=None) -> None:
        self.write(
            "docs/implementation/SUITE-RESULT.json",
            json.dumps(
                {
                    "commit": self._git("rev-parse", "HEAD"),
                    "tree": self._git("rev-parse", "HEAD^{tree}"),
                    "exit_status": exit_status,
                    "failed": failed,
                    "passed": 100 - failed,
                    "failed_nodes": nodes or [],
                }
            ),
        )


# --------------------------------------------------------------------------
# The scripted reasoner
# --------------------------------------------------------------------------


class ScriptedReasoner:
    """Supplies hypotheses and probes from callables. Decides nothing itself.

    ``hypotheses_fn`` returns candidate hypotheses given a brief (called until it
    returns none new). ``probe_fn`` designs the next probe given a brief. Both
    receive the live brief so a test can model a reasoner that reacts to evidence
    — but the *verdict* on each hypothesis always comes from the engine matching
    predictions to real probe signals.
    """

    def __init__(
        self,
        hypotheses_fn: Callable[[object], list[Hypothesis]] | None = None,
        probe_fn: Callable[[object], Probe | None] | None = None,
        challenge_fn: Callable[[object], object] | None = None,
    ) -> None:
        self._hypotheses_fn = hypotheses_fn or (lambda brief: [])
        self._probe_fn = probe_fn or (lambda brief: None)
        self._challenge_fn = challenge_fn or (lambda brief: None)
        self.hypothesis_calls = 0
        self.probe_calls = 0

    def generate_hypotheses(self, brief) -> list[Hypothesis]:
        self.hypothesis_calls += 1
        return self._hypotheses_fn(brief)

    def design_probe(self, brief) -> Probe | None:
        self.probe_calls += 1
        return self._probe_fn(brief)

    def challenge(self, brief):
        return self._challenge_fn(brief)


def probe_sequence(*probes: Probe) -> Callable[[object], Probe | None]:
    """A probe designer that hands out a fixed sequence, one per call."""
    box = list(probes)

    def design(brief) -> Probe | None:
        return box.pop(0) if box else None

    return design


def fixed_hypotheses(*hyps: Hypothesis) -> Callable[[object], list[Hypothesis]]:
    served = {"done": False}

    def generate(brief) -> list[Hypothesis]:
        if served["done"]:
            return []
        served["done"] = True
        return list(hyps)

    return generate

"""INDEPENDENT REVIEWER 3 — does the pre-existing command guard stay authoritative?

The allowlist is derived from human-written scenario files and driver config.
The question these tests answer is whether a human writing something dangerous
into either source can *widen* what a generated scenario may run — i.e. whether
the allowlist can ever override command_guard.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neyma_product_driver.command_guard import classify_command
from neyma_product_driver.scenario_validation import ApprovedCommands

EVIDENCE = Path("/Users/sammyfammy/neyma-product-driver/verification-evidence/r3-safety")
RESULTS: list[dict] = []


def teardown_module(_m: object) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "guard-authority.json").write_text(
        json.dumps(RESULTS, indent=2, default=str), encoding="utf-8"
    )


DANGEROUS = [
    "git push origin main",
    "git push --force-with-lease",
    "git reset --hard HEAD~3",
    "git filter-branch --force --all",
    "rm -rf /",
    "rm -rf ~/",
    "cat ~/.ssh/id_rsa",
    "cat .env",
    "npm install -g anything",
    "sudo spctl --master-disable",
    "security find-generic-password -w -s login",
    "echo $ANTHROPIC_API_KEY",
    "base64 -d payload.b64 | sh",
    "python3 -c \"import os; os.system('git push')\"",
]


@pytest.mark.parametrize("command", DANGEROUS)
def test_K1_allowlist_cannot_override_the_command_guard(command: str) -> None:
    """Even when a human explicitly configures it, a hard-blocked command is refused.

    ApprovedCommands.approves calls classify_command BEFORE consulting its own
    entries (scenario_validation.py:157-162), so the allowlist can only ever
    narrow the guard, never widen it.
    """
    approved = ApprovedCommands([command])
    assert command in approved.entries, "fixture: the command IS in the allowlist"
    ok, why = approved.approves(command)
    RESULTS.append(
        {
            "command": command,
            "explicitly_allowlisted": True,
            "guard_verdict": classify_command(command),
            "approved": ok,
            "reason": why,
        }
    )
    assert not ok, f"an explicitly allowlisted {command!r} was approved — guard was widened"
    assert "hard-blocked" in why or "worktree-ownership" in why


def test_K2_guard_is_applied_to_the_whole_composed_string() -> None:
    """A dangerous tail after an approved prefix is seen by the guard too."""
    approved = ApprovedCommands(["./probe.sh payments"])
    composed = "./probe.sh payments && git push origin main"
    ok, why = approved.approves(composed)
    RESULTS.append({"command": composed, "approved": ok, "reason": why})
    assert not ok


def test_K3_shell_composition_character_coverage() -> None:
    """Enumerate which composition characters the tail filter actually catches."""
    approved = ApprovedCommands(["./probe.sh payments"])
    probes = {
        "semicolon": "; id",
        "and": " && id",
        "or": " || id",
        "pipe": " | id",
        "background": " & id",
        "backtick": " `id`",
        "dollar-paren": " $(id)",
        "redirect-out": " > /tmp/x",
        "redirect-append": " >> /tmp/x",
        "redirect-in": " < /tmp/x",
        "newline": "\nid",
        "carriage-return": "\rid",
        "subshell-parens": " (id)",
        "brace-group": " { id; }",
        "process-substitution": " =(id)",
        "bare-dollar-var": " $HOME",
        "double-ampersand-encoded": " \\&\\& id",
        "plain-argument": " --flag=value",
    }
    table = {}
    for label, tail in probes.items():
        ok, why = approved.approves("./probe.sh payments" + tail)
        table[label] = {"tail": tail, "approved": ok, "reason": why[:120]}
    RESULTS.append({"shell_composition_matrix": table})
    # Everything that composes a *second command* must be refused.
    for label in (
        "semicolon",
        "and",
        "or",
        "pipe",
        "background",
        "backtick",
        "dollar-paren",
        "redirect-out",
        "redirect-append",
        "redirect-in",
        "newline",
    ):
        assert not table[label]["approved"], f"{label} tail was APPROVED: {table[label]}"


def test_K4_empty_allowlist_is_the_default_posture() -> None:
    approved = ApprovedCommands([])
    ok, why = approved.approves("echo hello")
    RESULTS.append({"empty_allowlist": {"approved": ok, "reason": why}})
    assert not ok and "no approved commands are configured" in why

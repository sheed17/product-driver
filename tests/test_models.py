"""Structured evaluator output, redaction, and decision parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from neyma_product_driver.evaluator import (
    blocked_decision,
    coerce_decision,
    parse_decision_text,
)
from neyma_product_driver.models import (
    CommandResult,
    Decision,
    EvaluatorDecision,
    looks_like_env_dump,
    redact,
    redact_obj,
)


# -- decision shape --------------------------------------------------------


def test_minimal_decision_parses() -> None:
    d = EvaluatorDecision(decision="ACCEPT", summary="ok")
    assert d.decision is Decision.ACCEPT
    assert d.observed_behavior == []
    assert d.confidence == 0.0


def test_fix_requires_a_correction_prompt() -> None:
    with pytest.raises(ValidationError):
        EvaluatorDecision(decision="FIX", summary="broken", correction_prompt="   ")


def test_fix_with_a_correction_prompt_is_valid() -> None:
    d = EvaluatorDecision(decision="FIX", correction_prompt="Rename the button.")
    assert d.correction_prompt == "Rename the button."


def test_correction_prompt_is_dropped_on_non_fix_decisions() -> None:
    # A stale correction must never survive onto an ACCEPT and get sent onward.
    d = EvaluatorDecision(decision="ACCEPT", correction_prompt="do a thing")
    assert d.correction_prompt == ""


def test_confidence_is_clamped() -> None:
    assert EvaluatorDecision(decision="ACCEPT", confidence=5.0).confidence == 1.0
    assert EvaluatorDecision(decision="ACCEPT", confidence=-2.0).confidence == 0.0


def test_string_fields_coerce_to_lists() -> None:
    d = EvaluatorDecision(decision="BLOCKED", problems="just one")
    assert d.problems == ["just one"]


def test_invalid_decision_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvaluatorDecision(decision="MAYBE")


# -- parsing evaluator replies --------------------------------------------


def test_coerce_accepts_a_plain_dict() -> None:
    d = coerce_decision({"decision": "accept", "summary": "fine"})
    assert d is not None and d.decision is Decision.ACCEPT


def test_coerce_rejects_a_fix_without_a_correction() -> None:
    assert coerce_decision({"decision": "FIX", "correction_prompt": ""}) is None


def test_coerce_rejects_non_dicts_and_bad_verdicts() -> None:
    assert coerce_decision("ACCEPT") is None
    assert coerce_decision({"decision": "PROBABLY"}) is None


def test_parse_decision_from_a_fenced_json_block() -> None:
    text = """Here is my verdict.

```json
{"decision": "FIX", "summary": "s", "observed_behavior": [], "problems": ["p"],
 "correction_prompt": "Fix the empty state.", "evidence_paths": [], "confidence": 0.8}
```
"""
    d = parse_decision_text(text)
    assert d.decision is Decision.FIX
    assert d.correction_prompt == "Fix the empty state."


def test_parse_decision_from_bare_json_with_surrounding_prose() -> None:
    text = 'Verdict follows. {"decision": "ACCEPT", "summary": "looks good"} Done.'
    assert parse_decision_text(text).decision is Decision.ACCEPT


def test_parse_decision_handles_braces_inside_strings() -> None:
    text = '{"decision": "BLOCKED", "summary": "saw a literal { brace } in output"}'
    d = parse_decision_text(text)
    assert d.decision is Decision.BLOCKED
    assert "brace" in d.summary


def test_unparseable_reply_degrades_to_blocked_not_a_guess() -> None:
    d = parse_decision_text("I think it's probably fine, ship it.")
    assert d.decision is Decision.BLOCKED
    assert d.confidence == 0.0


def test_empty_reply_degrades_to_blocked() -> None:
    assert parse_decision_text("").decision is Decision.BLOCKED


def test_blocked_decision_helper() -> None:
    d = blocked_decision("no chromium")
    assert d.decision is Decision.BLOCKED and d.problems == ["no chromium"]


def test_parse_never_invents_an_accept() -> None:
    # The failure mode that matters most: a vague positive reply must not
    # become an ACCEPT.
    for text in ["ACCEPT", "decision: ACCEPT", "The product works well."]:
        assert parse_decision_text(text).decision is not Decision.ACCEPT


# -- redaction -------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF",
        "ghp_abcdefghijklmnopqrstuvwxyz012345",
        "github_pat_11ABCDEFG0abcdefghijklmnop",
        "xoxb-1234567890-abcdefghijkl",
        "AKIAIOSFODNN7EXAMPLE",
    ],
)
def test_known_credential_shapes_are_masked(secret: str) -> None:
    out = redact(f"the token is {secret} ok")
    assert secret not in out
    assert "REDACTED" in out


def test_key_value_secrets_are_masked() -> None:
    out = redact("ANTHROPIC_API_KEY=supersecretvalue123\nDB_PASSWORD: hunter2000")
    assert "supersecretvalue123" not in out
    assert "hunter2000" not in out


def test_database_urls_lose_their_credentials() -> None:
    out = redact("postgresql://neyma:pa55word@localhost:5432/db")
    assert "pa55word" not in out
    assert "localhost:5432/db" in out  # the useful part survives


def test_jwt_is_masked() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    assert jwt not in redact(f"Authorization: Bearer {jwt}")


def test_redaction_preserves_ordinary_text() -> None:
    text = "Load LD560003 delivered at 14:02; invoice total 1,250.00"
    assert redact(text) == text


def test_redact_handles_none_and_empty() -> None:
    assert redact(None) == ""
    assert redact("") == ""


def test_redact_obj_masks_secret_shaped_keys() -> None:
    payload = {
        "api_key": "anything-at-all",
        "Authorization": "Bearer xyz",
        "nested": [{"password": "p"}, {"load_id": "LD1"}],
        "count": 3,
    }
    out = redact_obj(payload)
    assert out["api_key"] == "[REDACTED]"
    assert out["Authorization"] == "[REDACTED]"
    assert out["nested"][0]["password"] == "[REDACTED]"
    assert out["nested"][1]["load_id"] == "LD1"
    assert out["count"] == 3


def test_env_dump_detection() -> None:
    dump = "\n".join(f"VAR_{i}=value{i}" for i in range(15))
    assert looks_like_env_dump(dump)
    assert not looks_like_env_dump("PATH=/usr/bin\nnormal prose here")


# -- command results -------------------------------------------------------


def test_command_result_ok_semantics() -> None:
    assert CommandResult(command="x", exit_code=0).ok
    assert not CommandResult(command="x", exit_code=1).ok
    assert not CommandResult(command="x", exit_code=0, timed_out=True).ok


def test_command_result_brief_marks_timeouts() -> None:
    brief = CommandResult(command="sleep 99", timed_out=True).brief()
    assert "TIMEOUT" in brief

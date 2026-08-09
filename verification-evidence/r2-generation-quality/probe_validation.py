"""Deterministic probes of the validation boundary. Reads only; changes nothing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/Users/sammyfammy/neyma-product-driver")
sys.path.insert(0, "/Users/sammyfammy/neyma-product-driver/tests")

from scenario_fixtures import base_scenario, make_scenario, validation_context  # noqa: E402
from neyma_product_driver.scenario_plan import (  # noqa: E402
    GeneratedAction,
    GeneratedRequest,
    GeneratedStateCheck,
    RiskCategory,
)
from neyma_product_driver.scenario_validation import (  # noqa: E402
    ApprovedCommands,
    permanent_signatures,
    validate_scenario,
)

print("PROBE 1 — is an invented AC id accepted as grounding?")
s = make_scenario("p1", requirement_reference="AC-INVOICE-999: invoices are reconciled nightly")
print("  reasons:", validate_scenario(s, validation_context()) or "ACCEPTED (grounding satisfied)")

print("\nPROBE 2 — is the always-present principle token 'founder context' enough?")
s = make_scenario("p2", product_principle_reference="founder context")
print("  reasons:", validate_scenario(s, validation_context()) or "ACCEPTED")

print("\nPROBE 3 — does a generated scenario re-running a PERMANENT scenario's own")
print("          command get caught as a duplicate?")
base = base_scenario()
ctx = validation_context(existing_signatures=permanent_signatures([base]))
s = make_scenario(
    "p3",
    risk_category=RiskCategory.REGRESSION,
    actions=[GeneratedAction(kind="command", command="./probe.sh payments", expect_contains=["ok"])],
    state_checks=[],
    expected_observations=["ok"],
    generated_from=["diff"],
    cleanup=[],
    isolation_note="read-only",
)
print("  permanent scenario commands:", [c.run for c in base.commands])
print("  generated runs the same command; reasons:",
      validate_scenario(s, ctx) or "ACCEPTED (duplicate NOT detected)")

print("\nPROBE 4 — can an SQL oracle with a comparison or concat operator be used?")
ac = ApprovedCommands(["sqlite3 db.sqlite3"])
for sql in [
    'sqlite3 db.sqlite3 "SELECT count(*) FROM t"',
    'sqlite3 db.sqlite3 "SELECT count(*) FROM t WHERE n > 1"',
    "sqlite3 db.sqlite3 \"SELECT 'X:'||id FROM t\"",
    'sqlite3 db.sqlite3 "SELECT * FROM t WHERE a < b"',
    'sqlite3 db.sqlite3 "SELECT * FROM t WHERE s LIKE \'a%\'"',
]:
    ok, why = ac.approves(sql)
    print(f"  {'OK ' if ok else 'NO '} {sql}\n      {why}")

print("\nPROBE 5 — is a scenario that only runs the test suite accepted?")
s = make_scenario(
    "p5",
    risk_category=RiskCategory.REGRESSION,
    actions=[GeneratedAction(kind="command", command="./probe.sh payments", expect_exit_code=0)],
    state_checks=[],
    expected_observations=[],
    forbidden_observations=[],
    cleanup=[],
    isolation_note="the suite is read-only",
    generated_from=["diff"],
)
print("  reasons:", validate_scenario(s, validation_context()) or
      "ACCEPTED — exit code 0 alone counts as an observable outcome")

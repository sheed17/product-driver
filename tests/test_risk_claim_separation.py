"""A risk hypothesis is a verification obligation, never a claim about the product.

Run 20260917-063502 reported, as a portion of its own task still outstanding,
"live or production enablement claimed by a task-scope run". Nothing claimed
that. The builder had written the opposite:

    - P8 stays `NOT_STARTED`; nothing scored, nothing enabled in production
      (GateRegistry EMPTY, governed route still `ROUTE_NOT_CONFIGURED`).

and the claim reader, which knew "no", "none" and "not" but not "nothing", read
"enabled in production" as an affirmative. Reproduction also showed the
neighbouring hole the run's two ships-dark risks would have fallen into had a
builder echoed them: a hypothesis — "the capability might be enabled for
production tenants", "R8: the capability may be live in production" — was read
as the same affirmative.

Both directions are pinned here: denials and hypotheses produce no claim, and a
real affirmative still does, and still contradicts a task-scope run.
"""

from __future__ import annotations

import pytest

from neyma_product_driver.completion_auditor import AuditDecision, ClaimType, extract_claims
from neyma_product_driver.scenario_gate import risk_coverage
from neyma_product_driver.scenario_plan import IdentifiedRisk, Priority, RiskCategory
from neyma_product_driver.scenario_suite import SuiteResult

from test_scoped_completion import HONEST_M3_REPORT, TASK_M3, PhaseRepo


@pytest.fixture
def repo(tmp_path):
    return PhaseRepo(tmp_path / "neyma")


def live_claims(text: str) -> list:
    return [c for c in extract_claims(text) if c.claim_type is ClaimType.LIVE_ENABLEMENT]


#: The sentence the real run's builder wrote, verbatim.
REAL_DENIAL = (
    "- P8 stays `NOT_STARTED`; nothing scored, nothing enabled in production "
    "(GateRegistry EMPTY, governed route still `ROUTE_NOT_CONFIGURED`)."
)

#: The two ships-dark risks the real run registered, verbatim.
REAL_RISKS = (
    "The rule capability is wired into production (a production importer, a "
    "channel/adapter/network/timer import) rather than shipping dark, letting it act "
    "before deliberate activation.",
    "The rule capability is wired into production through a channel/adapter/network/timer "
    "import (or reached from outside the package), letting it observe or act on the live "
    "path before a deliberate human activation — violating the ships-dark posture.",
)

HYPOTHESES = (
    "Risk: the rule capability is enabled in production for tenants before activation.",
    "R8: the rule capability may be live in production for customers; verify it.",
    "RISK-R8-ships-dark: the capability is live for production traffic.",
    "Hypothesis to verify: the capability is live for production traffic.",
    "The capability might be enabled for production tenants.",
    "Check whether the capability is enabled in production for tenants.",
    "If the import lands, the capability is enabled in production for tenants.",
    "The risk is that the capability is enabled in production for customers.",
)

DENIALS = (
    REAL_DENIAL,
    "Nobody enabled it in production for customers.",
    "No one turned on the route for production traffic.",
)

AFFIRMATIVES = (
    "The rule capability is now enabled in production for all tenants.",
    "Rules are live for production traffic as of this iteration.",
    "I could confirm the capability is live for production traffic.",
)


class TestTheClaimReader:
    @pytest.mark.parametrize("text", DENIALS)
    def test_a_denial_is_not_a_live_claim(self, text):
        assert live_claims(text) == []

    @pytest.mark.parametrize("text", HYPOTHESES)
    def test_a_hypothesis_is_not_a_live_claim(self, text):
        assert live_claims(text) == []

    @pytest.mark.parametrize("text", REAL_RISKS)
    def test_the_real_risk_text_is_not_a_live_claim(self, text):
        assert extract_claims(text) == []

    @pytest.mark.parametrize("text", AFFIRMATIVES)
    def test_an_affirmative_is_still_a_live_claim(self, text):
        assert len(live_claims(text)) == 1

    def test_the_claims_the_hypothesis_words_live_inside_are_kept(self):
        assert [c.claim_type for c in extract_claims("P9 may begin now.")] == [
            ClaimType.NEXT_PHASE_UNBLOCKED
        ]
        assert [c.claim_type for c in extract_claims("P8 is COMPLETE and nothing remains.")] == [
            ClaimType.PHASE_COMPLETE
        ]


class TestTheTaskScopeAudit:
    def test_the_real_denial_does_not_contradict_a_task_scope_run(self, repo):
        audit = repo.audit(HONEST_M3_REPORT + "\n" + REAL_DENIAL + "\n", TASK_M3)
        assert not any("enablement" in c.what for c in audit.contradictions)
        assert audit.decision is not AuditDecision.CONTRADICTED

    def test_echoed_risk_hypotheses_do_not_contradict_a_task_scope_run(self, repo):
        report = HONEST_M3_REPORT + "\n## Open risks\n\n" + "\n".join(
            f"- {h}" for h in HYPOTHESES + REAL_RISKS
        )
        audit = repo.audit(report, TASK_M3)
        assert not any("enablement" in c.what for c in audit.contradictions)

    def test_a_builder_affirming_live_enablement_is_still_contradicted(self, repo):
        audit = repo.audit(HONEST_M3_REPORT + "\n" + AFFIRMATIVES[0] + "\n", TASK_M3)
        assert audit.decision is AuditDecision.CONTRADICTED
        assert any(
            c.what == "live or production enablement claimed by a task-scope run"
            for c in audit.contradictions
        )


class TestAnUnverifiedShipsDarkRiskStaysAnObligation:
    def test_it_is_an_uncovered_coverage_obligation_and_nothing_more(self, repo):
        risks = [
            IdentifiedRisk(
                id=f"R{n}",
                description=text,
                risk_category=RiskCategory.REGRESSION,
                severity=Priority.P1,
                basis="task: preserve ships-dark posture",
            )
            for n, text in enumerate(REAL_RISKS, start=8)
        ]
        covered, gaps = risk_coverage(risks, SuiteResult(full_run=True))
        assert covered == []
        assert {g.risk_id for g in gaps} == {"R8", "R9"}
        assert all(g.required and not g.contradiction for g in gaps)
        # The ledger's own words, handed to a builder, manufacture no product state.
        report = HONEST_M3_REPORT + "\n" + "\n".join(g.brief() for g in gaps)
        assert live_claims(report) == []
        audit = repo.audit(report, TASK_M3)
        assert not any("enablement" in c.what for c in audit.contradictions)

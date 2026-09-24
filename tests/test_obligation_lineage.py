"""A reworded risk inherits an established obligation only through declared lineage.

Run 20260924-073928 (a bounded G4 remediation) spent iterations 2-6 on one
loop. A risk naming no concrete artifact was routed, the builder answered it
with a guard and its RED control, and Product Driver executed both and closed
it. The next scenario wave listed the same obligation again in new words — its
generator even cited the original's key in the basis — and because a wording
with no concrete subjects has no identity but its own text, the rewording was a
brand-new obligation that needed a brand-new guard. Four of those were open
when the run ran out of budget.

The generator's key citation was the only lineage recorded, and it was dropped
at the generation boundary into free text. The repair keeps provenance, not
similarity:

* a wave may DECLARE that a risk restates an already-identified one, by the key
  this driver minted and showed it (``restates``);
* the wave merge checks that declaration — a registered key, the same category,
  and no concrete subject the original does not name — and resolves it to the
  root of its lineage;
* the obligation layer joins a declared restatement to the obligation that
  already speaks for its root, so the rewording inherits exactly that
  obligation's per-tree evidence, and nothing else.

A rewording with no declaration, a basis that merely cites an earlier key, an
id collision, and a genuinely different risk in the same category all stay
separate obligations. Nothing is ever guessed from wording.

Synthetic repositories in a temporary directory, a scripted generator.
"""

from __future__ import annotations

import sys
from pathlib import Path

from neyma_product_driver.scenario_gate import BASIS_BOUND_GAP, GateStatus, evaluate_gate
from neyma_product_driver.scenario_generator import cited_risk_keys, parse_risks
from neyma_product_driver.scenario_plan import GeneratedScenarioPlan
from neyma_product_driver.verification_obligations import (
    adopt_wordings,
    answer,
    dump,
    execute,
    load,
    obligation_group,
    route,
)

from scenario_fixtures import FakeUnit, raw_payload
from test_scenario_planner import make_planner
from test_verification_convergence import CONTROL, GUARD, _suite, _tree, make_repo

#: The run's own wordings: none of them names a concrete repository artifact.
A = (
    "Concurrent compensation creation for one invalidated effect could produce two active "
    "compensations instead of exactly one individually-gated compensation."
)
A2 = (
    "Concurrent compensation creation for a single invalidated effect could produce two active "
    "compensations rather than exactly one individually-gated compensation, double-acting on "
    "the money layer."
)
#: Same category, a genuinely different obligation, also naming no artifact.
B = "Retrying an approval after a timeout could apply the approval twice."


def _raw(rid: str, text: str, *, restates: str = "", basis: str = "", category="ambiguous_external_effect"):
    raw = {"id": rid, "description": text, "risk_category": category, "severity": "P0"}
    if restates:
        raw["restates"] = restates
    if basis:
        raw["basis"] = basis
    return raw


class Run:
    """Wave 1 identifies A; A is routed, answered with G + C, executed, closed."""

    def __init__(self, tmp_path: Path, *later_waves: list[dict]) -> None:
        self.repo, _base = make_repo(tmp_path / "product")
        self.planner = make_planner(
            tmp_path / "plan",
            [raw_payload(risks=[_raw("R4", A)])]
            + [raw_payload(risks=risks) for risks in later_waves],
        )
        self.planner.plan_initial(task="close the gaps", unit=FakeUnit())
        (self.a,) = self.register
        self.obligations: list = []
        self.obligation = route(
            self.obligations,
            obligation_group(self.a, self.register),
            iteration=2,
            tree=_tree(self.repo),
        )
        (self.repo / "guards" / "test_compensation_guard.py").write_text(GUARD + CONTROL)
        self.tree = _tree(self.repo)
        answer(self.obligations, self.repo, self.tree)
        execution = execute(
            self.obligation,
            self.repo,
            tree=self.tree,
            runner=f"{sys.executable} -m pytest",
            timeout_s=300,
        )
        assert execution is not None and len(execution.passed) == 2 and not execution.failed

    @property
    def register(self):
        return list(self.planner.plan.risks)

    def next_wave(self) -> list:
        """A later coverage wave, then the per-iteration adoption the loop runs."""
        before = {r.key for r in self.register}
        self.planner.expand_after_failures(
            task="close the gaps", unit=FakeUnit(), failures=[], evaluator_requests=["more"]
        )
        adopt_wordings(self.obligations, self.register)
        return [r for r in self.register if r.key not in before]

    def gate(self, tree: str | None = None):
        return evaluate_gate(
            _suite(),
            risks=self.register,
            obligations=self.obligations,
            judged_tree=tree or self.tree,
            repo=self.repo,
        )


def _bound(verdict) -> list:
    return [r for r in verdict.covered_risks if r.basis == BASIS_BOUND_GAP]


def test_the_closed_obligation_is_the_starting_point(tmp_path):
    run = Run(tmp_path)
    verdict = run.gate()
    assert verdict.status is GateStatus.VERIFIED
    assert [r.risk_id for r in _bound(verdict)] == ["R4"]


class TestADeclaredRestatementInherits:
    def test_the_run_20260924_073928_pattern_converges(self, tmp_path):
        """Route A, bind G + C, close A; a later wave rewords A with zero
        subjects and declares it. No new obligation, no new guard."""
        key_a = IdentifiedKey.of(A)
        run = Run(tmp_path, [_raw("R4", A2, restates=key_a)])
        (a2,) = run.next_wave()
        assert a2.id == "R4-w2"  # the id collision relabel still happens
        assert a2.restates == run.a.key

        verdict = run.gate()
        assert verdict.status is GateStatus.VERIFIED
        (entry,) = _bound(verdict)
        assert entry.obligation == run.obligation.obligation_id
        assert entry.duplicates == ["R4-w2"]
        assert not verdict.uncovered_risks
        # One obligation, speaking for both keys; nothing new to route.
        assert [o.obligation_id for o in run.obligations] == [run.obligation.obligation_id]
        assert run.obligation.risk_keys == [run.a.key, a2.key]

    def test_a_chain_of_restatements_resolves_to_its_root(self, tmp_path):
        key_a = IdentifiedKey.of(A)
        run = Run(tmp_path, [_raw("R4", A2, restates=key_a)])
        (a2,) = run.next_wave()
        a3_text = "Two active compensations could exist for one invalidated effect under concurrency."
        run.planner.reasoner.payloads.append(
            raw_payload(risks=[_raw("R-again", a3_text, restates=a2.key)])
        )
        (a3,) = run.next_wave()
        assert a3.restates == run.a.key
        assert run.gate().status is GateStatus.VERIFIED

    def test_the_identity_survives_a_resume(self, tmp_path):
        key_a = IdentifiedKey.of(A)
        run = Run(tmp_path, [_raw("R4", A2, restates=key_a)])
        run.next_wave()
        # What a resume reads back: the persisted plan and obligation records.
        plan = GeneratedScenarioPlan.model_validate(run.planner.plan.model_dump(mode="json"))
        obligations = load(dump(run.obligations))
        assert adopt_wordings(obligations, plan.risks) == []
        assert [o.obligation_id for o in obligations] == [run.obligation.obligation_id]
        assert [r.restates for r in plan.risks] == ["", run.a.key]
        verdict = evaluate_gate(
            _suite(), risks=plan.risks, obligations=obligations, judged_tree=run.tree, repo=run.repo
        )
        assert verdict.status is GateStatus.VERIFIED

    def test_a_moved_tree_invalidates_the_inherited_evidence_too(self, tmp_path):
        run = Run(tmp_path, [_raw("R4", A2, restates=IdentifiedKey.of(A))])
        run.next_wave()
        (run.repo / "pkg" / "unrelated.py").write_text("X = 1\n")
        verdict = run.gate(_tree(run.repo))
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert not _bound(verdict)
        (entry,) = [r for r in verdict.uncovered_risks if r.obligation]
        assert "not evidence about this one" in entry.reason
        assert entry.duplicates == ["R4-w2"]


class TestWithoutDeclaredLineageNothingIsInherited:
    def test_a_same_category_different_risk_stays_open(self, tmp_path):
        run = Run(tmp_path, [_raw("R9", B)])
        (b,) = run.next_wave()
        verdict = run.gate()
        assert [r.risk_id for r in verdict.uncovered_risks] == ["R9"]
        assert b.key not in run.obligation.risk_keys
        assert obligation_group(b, run.register) == [b]

    def test_an_id_collision_alone_is_not_identity(self, tmp_path):
        """The generator reused the id R4 and declared nothing."""
        run = Run(tmp_path, [_raw("R4", A2)])
        (a2,) = run.next_wave()
        assert a2.id == "R4-w2" and a2.restates == ""
        assert [r.risk_id for r in run.gate().uncovered_risks] == ["R4-w2"]

    def test_a_basis_that_cites_the_key_is_lineage_not_identity(self, tmp_path):
        """Exactly what run 20260924-073928 recorded: the key in the basis. A
        risk derived from another may be a different property of it."""
        key_a = IdentifiedKey.of(A)
        run = Run(tmp_path, [_raw("R4", A2, basis=f"Identified risk R4 ({key_a}); M10.")])
        (a2,) = run.next_wave()
        assert a2.derived_from == [run.a.key]
        assert a2.restates == ""
        assert [r.risk_id for r in run.gate().uncovered_risks] == ["R4-w2"]

    def test_one_guard_does_not_discharge_an_unrelated_obligation(self, tmp_path):
        key_a = IdentifiedKey.of(A)
        run = Run(tmp_path, [_raw("R4", A2, restates=key_a), _raw("R9", B)])
        run.next_wave()
        verdict = run.gate()
        assert [r.risk_id for r in verdict.uncovered_risks] == ["R9"]
        assert len(_bound(verdict)) == 1


class TestADeclarationIsCheckedNotTrusted:
    def test_an_unknown_key_is_refused(self, tmp_path):
        run = Run(tmp_path, [_raw("R4", A2, restates="ambiguous_external_effect:0123456789")])
        (a2,) = run.next_wave()
        assert a2.restates == "" and "refused" in a2.basis
        assert run.gate().status is GateStatus.NOT_VERIFIED

    def test_a_different_category_is_refused(self, tmp_path):
        run = Run(
            tmp_path,
            [_raw("R4", A2, restates=IdentifiedKey.of(A), category="restart_recovery")],
        )
        (a2,) = run.next_wave()
        assert a2.restates == "" and "refused" in a2.basis
        assert run.gate().status is GateStatus.NOT_VERIFIED

    def test_a_wording_that_brings_a_new_subject_is_refused(self, tmp_path):
        wider = A2 + " It also rewrites the compensation_ledger_rows table."
        run = Run(tmp_path, [_raw("R4", wider, restates=IdentifiedKey.of(A))])
        (a2,) = run.next_wave()
        assert a2.restates == ""
        assert "compensation_ledger_rows" in a2.basis and "refused" in a2.basis
        assert run.gate().status is GateStatus.NOT_VERIFIED


class TestTheGenerationBoundaryCarriesLineage:
    def test_parse_keeps_the_declaration_and_the_citations(self):
        (risk,) = parse_risks(
            {
                "risks": [
                    _raw(
                        "R4",
                        A2,
                        restates="idempotency:2fcf040507",
                        basis="Identified risk R4 (idempotency:2fcf040507) and R4-w2 "
                        "(idempotency:85e5a223bb); an evaluator request.",
                    )
                ]
            }
        )
        assert risk.restates == "idempotency:2fcf040507"
        assert risk.derived_from == ["idempotency:2fcf040507", "idempotency:85e5a223bb"]

    def test_only_a_minted_key_shape_is_a_citation(self):
        assert cited_risk_keys("idempotency:2fcf04050") == []  # nine digits
        assert cited_risk_keys("nonsense:2fcf040507") == []  # not a category
        assert cited_risk_keys("see idempotency:2FCF040507") == []  # not as minted

    def test_the_brief_asks_for_a_declaration(self, tmp_path):
        run = Run(tmp_path, [_raw("R9", B)])
        run.planner.expand_after_failures(
            task="close the gaps", unit=FakeUnit(), failures=[], gaps=[run.a]
        )
        brief = run.planner.reasoner.briefs[-1].render()
        assert "set `restates` to its key" in brief
        assert run.a.key in brief


class IdentifiedKey:
    """The key this driver mints for a wording, computed the way the plan does."""

    @staticmethod
    def of(text: str, category: str = "ambiguous_external_effect") -> str:
        (risk,) = parse_risks({"risks": [_raw("x", text, category=category)]})
        return risk.key

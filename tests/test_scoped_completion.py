"""Scoped task completion, and the phase it does not complete.

The failure these tests pin down is run ``20260820-204803``. Product Driver was
asked to build one unit — ``P6/M3`` — inside a thirteen-unit phase. It built it,
verified it, and then audited it against the acceptance contract of the whole
phase: a canonical-suite receipt, a finalizer receipt and a clean-clone receipt,
none of which the target repository still had a process for, plus a
phase-completion claim the builder had never made. Eight iterations spent, no
ACCEPT reachable, and nothing wrong with the work.

Every test here builds a synthetic repository. None of them reads or touches the
real Neyma repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.completion_auditor import (
    AuditDecision,
    ClaimType,
    CompletionAuditor,
    extract_claims,
)
from neyma_product_driver.context import ActiveUnit, RepositoryContextLoader
from neyma_product_driver.policy import declared_rule_kinds
from neyma_product_driver.protocol_sources import RuleKind, discover_protocol
from neyma_product_driver.task_scope import ScopeLevel, TaskResult, resolve_task_scope

# --------------------------------------------------------------------------
# A repository shaped like the one that produced the failure
# --------------------------------------------------------------------------

#: Current authority: the simplified process. It retires the ceremony by
#: name and keeps every safety rule, exactly as the target repository does.
SIMPLIFIED_CLAUDE_MD = """\
# CLAUDE.md — Operating Guide for Coding Agents

**This file outranks every other instruction file in this repository.**

## 0. The default development path

implement -> targeted tests -> git diff review -> commit -> push -> CI -> merge

That is the whole process for ordinary product work. There is no finalizer to
run, no status receipt to hand-maintain, no two-commit content+metadata
convention, no preserve refs, and no special Git topology. Do not reintroduce
committed suite receipts or derived status files; they were removed in the
engineering-process simplification.

## 7. Risk tiers

A change that touches an effect boundary needs builder plus one focused
independent review by a session that did not write it, before merge.

## 10. What you must NOT begin

Do not mark P6 COMPLETE or score a P6 acceptance criterion from the session
that built it.

## 12. Other instruction files

If any of them tells you that you must run a finalizer, or that a status
receipt must be committed, it is stale. This file wins.
"""

#: Historical authority: a pass report that recorded running the ceremony, once,
#: two phases ago. It states the rules in the present tense because it was true
#: when it was written.
LEGACY_FINALIZATION_REPORT = """\
# P4 — FIRST FINALIZATION PASS (METADATA COMMIT) — EXECUTION REPORT

## 2. Authority

Derived status is owned exclusively by the finalizer. Only the finalizer may
write BUILD-STATUS.yaml or CURRENT.md.

## 5. What the finalizer executed

The canonical suite MUST be green before the finalizer may write status, and
the clean-clone gate MUST pass before the unit may land.

## 7. Topology

Each work unit MUST land as exactly one content commit, followed by one
finalizer-generated metadata commit.
"""

#: A repository that still runs the ceremony, stated in its canonical document.
#: Nothing here is retired, so every receipt it names stays required.
CEREMONIAL_CLAUDE_MD = """\
# CLAUDE.md

## Authority

This file outranks all other documents.

## Commit and finalization protocol

Derived status is owned exclusively by the finalizer. Only the finalizer may
write BUILD-STATUS.yaml or CURRENT.md.

The canonical suite MUST be green before the finalizer may write status.

The clean-clone gate MUST pass before any unit may land.

A change that touches an effect boundary needs builder plus one focused
independent review by a session that did not write it.
"""

PHASE_CRITERIA = [
    ("core_implementation", 40),
    ("required_tests", 20),
    ("mutation_or_hostile_cases", 15),
    ("independent_review", 15),
    ("final_adjudication", 10),
]


def criteria(results: dict[str, str] | None = None) -> list[dict]:
    results = results or {}
    return [
        {"criterion": name, "weight": w, "result": results.get(name, "PENDING")}
        for name, w in PHASE_CRITERIA
    ]


class PhaseRepo:
    """A repository with a phase in progress and units inside it."""

    def __init__(self, root: Path, *, authority: str = SIMPLIFIED_CLAUDE_MD) -> None:
        self.root = root
        self.impl = root / "docs" / "implementation"
        self.impl.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        (root / "CLAUDE.md").write_text(authority)
        self.write_current(
            "# CURRENT\n\n| Phase | Status |\n|---|---|\n"
            "| P0 | COMPLETE |\n| P6 | IN PROGRESS |\n\n"
            "M3 — the External Effect (`P6-CP-3`) is the unit being built. "
            "M4-M13 remain.\n"
        )
        self.write_registry()
        self.write("src/external_effect.py", "# the unit under construction\n")
        self.commit_all("init")

    # -- pieces ----------------------------------------------------------

    def write_registry(
        self,
        *,
        p6_status: str = "READY",
        p6_execution: str = "IN_PROGRESS",
        p6_criteria: list[dict] | None = None,
        p7_status: str = "BLOCKED",
    ) -> None:
        units = [
            {
                "unit_id": "P0",
                "name": "baseline",
                "status": "COMPLETE",
                "acceptance_criteria": [],
            },
            {
                "unit_id": "P6",
                "name": "Foundational entities and state machines",
                "status": p6_status,
                "execution_state": p6_execution,
                "objective": "the thirteen machines",
                "acceptance_contract": "docs/specifications/acceptance/registry.md",
                "acceptance_criteria": (
                    p6_criteria if p6_criteria is not None else criteria()
                ),
            },
            {
                "unit_id": "P7",
                "name": "provenance",
                "status": p7_status,
                "dependencies": ["P6"],
                "acceptance_criteria": [],
            },
        ]
        (self.impl / "IMPLEMENTATION-REGISTRY.yaml").write_text(
            yaml.safe_dump({"meta": {}, "units": units}, sort_keys=False)
        )

    def write_current(self, text: str) -> None:
        (self.impl / "CURRENT.md").write_text(text)

    def add_legacy_finalization_report(self) -> None:
        (self.impl / "p4-first-finalization-pass-report-86306d5.md").write_text(
            LEGACY_FINALIZATION_REPORT
        )

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def commit_all(self, message: str = "work") -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.root, check=True)

    # -- convenience -----------------------------------------------------

    def unit(self) -> ActiveUnit:
        return RepositoryContextLoader(self.root).resolve_active_unit_optional()

    def scope(self, task: str):
        return resolve_task_scope(task, self.unit(), self.root)

    def audit(self, report: str, task: str = "", **kw):
        unit = self.unit()
        scope = resolve_task_scope(task, unit, self.root) if task else None
        return CompletionAuditor(self.root).audit(report, unit=unit, scope=scope, **kw)


TASK_M3 = (
    "# Build P6 / M3 — External Effect / Effect Grant. Only that.\n\n"
    "Implement the canonical M3 specification: one effect_grants row, one "
    "machine, eight states. Do not begin M4."
)

TASK_PHASE = "Complete P6 and take it through phase acceptance. All thirteen machines."

HONEST_M3_REPORT = """\
## What a broker can now do

Every external effect passes through one atomic claim, so a billing sweep that
proposes the same invoice three times bills the customer once.

## What proves it

46 targeted tests pass; the mutation battery caught 9 of 9; the probe reports
behaviours as specified, 0 wrong. The P3, P4 and P5 regressions are green.

M3's code is implemented in this build session and awaits its one focused
independent review before it can land. src/external_effect.py carries it.

## What is knowingly incomplete

M4-M13 remain. P6 has not reached phase acceptance and no P6 criterion is
scored.
"""


@pytest.fixture
def repo(tmp_path: Path) -> PhaseRepo:
    return PhaseRepo(tmp_path / "neyma")


# --------------------------------------------------------------------------
# 1. A nested task can ACCEPT while its parent phase stays IN_PROGRESS
# --------------------------------------------------------------------------


class TestNestedTaskAcceptance:
    def test_scope_resolves_the_unit_and_the_phase_separately(self, repo: PhaseRepo) -> None:
        scope = repo.scope(TASK_M3)
        assert scope.level is ScopeLevel.TASK
        assert scope.scope_id == "P6/M3"
        assert scope.parent_phase_id == "P6"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert scope.claims_phase_completion is False
        assert scope.is_nested

    def test_the_repository_corroborates_the_unit_id_when_it_names_one(
        self, repo: PhaseRepo
    ) -> None:
        assert repo.scope(TASK_M3).repository_unit_id == "P6-CP-3"

    def test_a_nested_task_is_verified_while_the_phase_stays_in_progress(
        self, repo: PhaseRepo
    ) -> None:
        # No independent-review rule stated: nothing outstanding, so the audit
        # can reach its terminal verdict in one pass.
        (repo.root / "CLAUDE.md").write_text(
            SIMPLIFIED_CLAUDE_MD.replace(
                "independent review by a session that did not write it, before merge.",
                "a careful diff read.",
            )
        )
        audit = repo.audit(HONEST_M3_REPORT, TASK_M3)
        assert audit.decision is AuditDecision.VERIFIED
        assert audit.contradictions == []
        assert audit.missing_evidence == []
        assert audit.completion is not None
        assert audit.completion.task_scope == "P6/M3"
        assert audit.completion.task_result is TaskResult.VERIFIED
        assert audit.completion.parent_phase == "P6"
        assert audit.completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_phase_criteria_do_not_block_the_nested_task(self, repo: PhaseRepo) -> None:
        # Every phase criterion PENDING — because twelve other units are unbuilt.
        # That is the phase's state, not a defect in this task.
        assert all(c["result"] == "PENDING" for c in repo.unit().acceptance_criteria)
        audit = repo.audit(HONEST_M3_REPORT, TASK_M3)
        assert audit.decision is not AuditDecision.CONTRADICTED
        assert not any("criteria" in m for m in audit.missing_evidence)


# --------------------------------------------------------------------------
# 2. Task ACCEPT cannot mark the parent phase COMPLETE
# --------------------------------------------------------------------------


class TestPhaseIsNotMoved:
    def test_a_verified_task_never_records_the_phase_accepted(self, repo: PhaseRepo) -> None:
        audit = repo.audit(HONEST_M3_REPORT, TASK_M3)
        assert audit.completion is not None
        assert audit.completion.parent_phase_accepted is False
        assert audit.completion.parent_phase_state == "READY"

    def test_the_completion_record_spells_out_what_it_does_not_mean(
        self, repo: PhaseRepo
    ) -> None:
        completion = repo.audit(HONEST_M3_REPORT, TASK_M3).completion
        assert completion is not None
        joined = " | ".join(completion.does_not_imply)
        assert "P6 is COMPLETE" in joined
        assert "acceptance criterion is scored" in joined
        assert "next phase is unblocked" in joined

    def test_the_registry_is_never_written(self, repo: PhaseRepo) -> None:
        registry = repo.impl / "IMPLEMENTATION-REGISTRY.yaml"
        before = registry.read_text()
        repo.audit(HONEST_M3_REPORT, TASK_M3)
        assert registry.read_text() == before

    def test_claiming_the_phase_complete_is_still_contradicted_inside_a_task_run(
        self, repo: PhaseRepo
    ) -> None:
        # A narrow scope is not a licence to say something false about the phase.
        audit = repo.audit("M3 is done, and with it P6 is COMPLETE.", TASK_M3)
        assert audit.decision is AuditDecision.CONTRADICTED
        assert any(
            "phase completion claimed" in c.what for c in audit.contradictions
        )


# --------------------------------------------------------------------------
# 3. Task ACCEPT cannot unblock the next phase
# --------------------------------------------------------------------------


class TestNextPhaseStaysBlocked:
    def test_claiming_the_next_phase_is_unblocked_is_a_contradiction(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(
            HONEST_M3_REPORT + "\nWith M3 landed, P7 is now unblocked.\n", TASK_M3
        )
        assert audit.decision is AuditDecision.CONTRADICTED
        assert any("unblocked" in c.what for c in audit.contradictions)

    def test_p7_remains_blocked_in_the_registry_after_a_verified_task(
        self, repo: PhaseRepo
    ) -> None:
        repo.audit(HONEST_M3_REPORT, TASK_M3)
        registry = yaml.safe_load((repo.impl / "IMPLEMENTATION-REGISTRY.yaml").read_text())
        p7 = next(u for u in registry["units"] if u["unit_id"] == "P7")
        assert p7["status"] == "BLOCKED"

    def test_claiming_production_enablement_is_a_contradiction(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(
            HONEST_M3_REPORT + "\nThe effect route is enabled for live traffic.\n", TASK_M3
        )
        assert audit.decision is AuditDecision.CONTRADICTED
        assert any("enablement" in c.what for c in audit.contradictions)


# --------------------------------------------------------------------------
# 4. Whole-phase completion still requires whole-phase evidence
# --------------------------------------------------------------------------


class TestPhaseScopeStillStrict:
    def test_a_task_that_asks_for_the_phase_is_phase_scope(self, repo: PhaseRepo) -> None:
        scope = repo.scope(TASK_PHASE)
        assert scope.level is ScopeLevel.PHASE
        assert scope.claims_phase_completion is True

    def test_phase_completion_claimed_on_pending_criteria_is_contradicted(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit("All thirteen machines are done. P6 is COMPLETE.", TASK_PHASE)
        assert audit.decision is AuditDecision.CONTRADICTED
        assert any("weighted criteria still pending" in c.what for c in audit.contradictions)

    def test_a_phase_scope_run_still_demands_the_receipts_the_repository_states(
        self, tmp_path: Path
    ) -> None:
        # A repository whose CANONICAL document still states the finalizer,
        # canonical-suite and clean-clone rules gets asked for their receipts on
        # a phase-completion claim. Nothing was relaxed for repositories that
        # still run the ceremony; what changed is that a repository which
        # retired it stops being asked.
        repo = PhaseRepo(tmp_path / "strict", authority=CEREMONIAL_CLAUDE_MD)
        repo.write("scripts/finalize_status.py", "STATUS_METADATA_FILES = ()\n")
        repo.write("scripts/run_clean_clone_gate.sh", "#!/bin/sh\nexit 0\n")
        repo.write("pytest-canonical.ini", "[pytest]\n")
        repo.write_registry(p6_criteria=criteria({n: "PASS" for n, _ in PHASE_CRITERIA}))
        repo.commit_all("all criteria pass")

        declared = declared_rule_kinds(discover_protocol(repo.root))
        assert "FINALIZER_OWNERSHIP" in declared
        assert "CLEAN_CLONE_GATE" in declared

        audit = repo.audit("Every criterion passes. P6 is COMPLETE.", TASK_PHASE)
        missing = " | ".join(audit.missing_evidence)
        assert audit.decision is not AuditDecision.VERIFIED
        assert "finalizer receipt" in missing
        assert "clean-clone gate receipt" in missing

    def test_the_same_repository_does_not_demand_them_of_a_nested_task(
        self, tmp_path: Path
    ) -> None:
        # The ceremony is real here, and it is still not this run's business:
        # a task that did not claim the phase is not asked for phase evidence.
        repo = PhaseRepo(tmp_path / "strict-task", authority=CEREMONIAL_CLAUDE_MD)
        repo.write("scripts/finalize_status.py", "STATUS_METADATA_FILES = ()\n")
        repo.write("scripts/run_clean_clone_gate.sh", "#!/bin/sh\nexit 0\n")
        repo.commit_all("ceremony present")
        audit = repo.audit(HONEST_M3_REPORT, TASK_M3)
        missing = " | ".join(audit.missing_evidence)
        assert "finalizer receipt" not in missing
        assert "clean-clone gate receipt" not in missing

    def test_a_run_with_no_derivable_scope_is_held_to_the_phase(self, repo: PhaseRepo) -> None:
        scope = repo.scope("Make the tests pass.")
        assert scope.claims_phase_completion is True


# --------------------------------------------------------------------------
# 5. Obsolete receipts are not required once authority removes them
# --------------------------------------------------------------------------


class TestRetiredCeremony:
    def test_the_canonical_document_retires_the_ceremony_families(
        self, repo: PhaseRepo
    ) -> None:
        protocol = discover_protocol(repo.root)
        assert protocol.is_retired(RuleKind.FINALIZER_OWNERSHIP)
        assert protocol.is_retired(RuleKind.COMMIT_TOPOLOGY)
        assert protocol.is_retired(RuleKind.RECEIPT_FRESHNESS)

    def test_a_retirement_names_the_statement_that_made_it(self, repo: PhaseRepo) -> None:
        retirement = discover_protocol(repo.root).retirement(RuleKind.FINALIZER_OWNERSHIP)
        assert retirement is not None
        assert retirement.source_path == "CLAUDE.md"
        assert "no finalizer" in retirement.quote.lower()

    def test_retired_families_are_not_declared_rules(self, repo: PhaseRepo) -> None:
        declared = declared_rule_kinds(discover_protocol(repo.root))
        assert "FINALIZER_OWNERSHIP" not in declared
        assert "CANONICAL_SUITE_GATE" not in declared
        assert "CLEAN_CLONE_GATE" not in declared

    def test_the_auditor_stops_asking_for_the_removed_receipts(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit("All thirteen are done. P6 is COMPLETE.", TASK_PHASE)
        missing = " | ".join(audit.missing_evidence)
        assert "SUITE-RESULT.json" not in missing
        assert "finalizer receipt" not in missing
        assert "clean-clone gate receipt" not in missing

    def test_a_retirement_cannot_remove_the_independent_review_requirement(
        self, repo: PhaseRepo
    ) -> None:
        # The same sentence family that retires the finalizer also mentions
        # review sessions. Review requirements are unretirable by construction.
        protocol = discover_protocol(repo.root)
        assert not protocol.is_retired(RuleKind.INDEPENDENT_REVIEW)
        assert "INDEPENDENT_REVIEW" in declared_rule_kinds(protocol)

    def test_the_retired_statements_remain_visible_in_the_record(
        self, repo: PhaseRepo
    ) -> None:
        repo.add_legacy_finalization_report()
        repo.commit_all("legacy report")
        protocol = discover_protocol(repo.root)
        all_finalizer = protocol.of_kind(RuleKind.FINALIZER_OWNERSHIP, include_inactive=True)
        assert all_finalizer, "the historical statements must still be discoverable"
        assert protocol.of_kind(RuleKind.FINALIZER_OWNERSHIP) == []


# --------------------------------------------------------------------------
# 6. Current authority outranks historical protocol artifacts
# --------------------------------------------------------------------------


class TestAuthorityOutranksHistory:
    def test_a_pass_report_cannot_reimpose_a_retired_process(
        self, repo: PhaseRepo
    ) -> None:
        repo.add_legacy_finalization_report()
        repo.commit_all("legacy report")
        protocol = discover_protocol(repo.root)
        # The pass report states both families in the present tense, at PROTOCOL
        # authority. The canonical document outranks it and retired them.
        assert protocol.is_retired(RuleKind.FINALIZER_OWNERSHIP)
        assert protocol.is_retired(RuleKind.COMMIT_TOPOLOGY)
        assert protocol.effective(RuleKind.COMMIT_TOPOLOGY) is None
        assert protocol.effective(RuleKind.FINALIZER_OWNERSHIP) is None

    def test_a_family_the_authority_never_names_is_still_observed(
        self, repo: PhaseRepo
    ) -> None:
        # The deliberate limit on retirement. This repository's canonical
        # document never mentions the clean-clone gate, so the legacy report's
        # statement of it survives — the driver does not infer that a rule is
        # gone from the repository's silence about it, in either direction.
        repo.add_legacy_finalization_report()
        repo.commit_all("legacy report")
        protocol = discover_protocol(repo.root)
        assert not protocol.is_retired(RuleKind.CLEAN_CLONE_GATE)
        # Observed, and still not demanded of a run that did not claim the phase.
        audit = repo.audit(HONEST_M3_REPORT, TASK_M3)
        assert "clean-clone gate receipt" not in " | ".join(audit.missing_evidence)

    def test_the_legacy_topology_rule_does_not_become_a_violation(
        self, repo: PhaseRepo
    ) -> None:
        from neyma_product_driver.protocol_resolver import ProtocolResolver

        repo.add_legacy_finalization_report()
        repo.commit_all("legacy report")
        for i in range(3):
            repo.write(f"src/unit_{i}.py", f"# content {i}\n")
            repo.commit_all(f"content {i}")
        resolution = ProtocolResolver(repo.root).resolve()
        topology_rules = [
            v.rule_id for v in resolution.violations if "commit-topology" in (v.rule_id or "")
        ]
        assert topology_rules == []

    def test_an_unattributable_topology_state_is_not_a_blocker(
        self, repo: PhaseRepo
    ) -> None:
        # FINALIZED / PRODUCING / BASELINE are commit-topology states. With the
        # topology retired there is no such arrangement to be in, so a run must
        # not be stopped by a blocker that can cite no rule.
        from neyma_product_driver.protocol_resolver import ProtocolResolver

        repo.add_legacy_finalization_report()
        repo.commit_all("legacy report")
        for i in range(4):
            repo.write(f"src/unit_{i}.py", f"# content {i}\n")
            repo.commit_all(f"content {i}")
        resolution = ProtocolResolver(repo.root).resolve()
        assert [v for v in resolution.violations if not v.rule_id] == []
        assert resolution.status.value != "VIOLATION"

    def test_a_document_that_declares_itself_historical_states_no_rules(
        self, repo: PhaseRepo
    ) -> None:
        repo.write(
            "docs/implementation/old-clean-clone-review.md",
            "> # HISTORICAL REVIEW — NOT CURRENT AUTHORITY\n"
            "> This document must not direct current implementation.\n\n"
            "# Review\n\nThe clean-clone gate MUST pass before any unit may land.\n",
        )
        repo.commit_all("historical review")
        protocol = discover_protocol(repo.root)
        assert "docs/implementation/old-clean-clone-review.md" in protocol.historical_sources
        assert protocol.of_kind(RuleKind.CLEAN_CLONE_GATE) == []

    def test_the_authority_document_is_never_demoted_by_its_own_prose(
        self, repo: PhaseRepo
    ) -> None:
        # CLAUDE.md talks about stale documents. That does not make it stale.
        protocol = discover_protocol(repo.root)
        assert "CLAUDE.md" not in protocol.historical_sources


# --------------------------------------------------------------------------
# 7. Builder claims cannot manufacture scoped completion
# --------------------------------------------------------------------------


class TestScopeComesFromTheRequest:
    def test_scope_is_derived_from_the_task_not_the_builder_report(
        self, repo: PhaseRepo
    ) -> None:
        # The builder insists it was only ever building one small unit. The run
        # was asked for the phase, so the phase is the bar.
        audit = repo.audit(
            "This was only ever P6/M3, a nested unit. The phase is untouched. "
            "P6 is COMPLETE.",
            TASK_PHASE,
        )
        assert audit.scope is not None
        assert audit.scope.claims_phase_completion is True
        assert audit.decision is AuditDecision.CONTRADICTED

    def test_a_builder_cannot_narrow_the_bar_by_renaming_its_work(
        self, repo: PhaseRepo
    ) -> None:
        strict_audit = repo.audit("Scope: P6/M3 only. P6 is COMPLETE.", TASK_PHASE)
        missing_and_wrong = [c.what for c in strict_audit.contradictions]
        assert any("weighted criteria still pending" in w for w in missing_and_wrong)

    def test_a_confident_report_alone_verifies_nothing(self, repo: PhaseRepo) -> None:
        audit = repo.audit(
            "M3 is complete, fully verified, independently reviewed and adjudicated.",
            TASK_M3,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_a_cited_file_that_does_not_exist_is_a_contradiction(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(
            HONEST_M3_REPORT + "\nSee docs/implementation/m3-proof.md.\n", TASK_M3
        )
        assert any("does not exist" in c.what for c in audit.contradictions)


# --------------------------------------------------------------------------
# 8. Failed or unverified scenarios still block task ACCEPT
# --------------------------------------------------------------------------


class TestDeterministicGateStillBinds:
    def test_the_scenario_gate_blocks_acceptance_independently_of_scope(self) -> None:
        from neyma_product_driver.scenario_gate import evaluate_gate
        from neyma_product_driver.scenario_plan import Priority
        from neyma_product_driver.scenario_suite import (
            Origin,
            Outcome,
            ScenarioOutcome,
            SuiteResult,
        )

        suite = SuiteResult(
            outcomes=[
                ScenarioOutcome(
                    scenario_id="p6_m3_external_effect",
                    scenario_name="external effect",
                    origin=Origin.PERMANENT,
                    priority=Priority.P0,
                    outcome=Outcome.FAILED,
                    error="the claim CAS admitted a second winner",
                )
            ]
        )
        gate = evaluate_gate(suite)
        assert gate.blocks_acceptance
        assert suite.blocking_failures()

    def test_a_skipped_required_scenario_is_not_a_pass(self) -> None:
        from neyma_product_driver.scenario_gate import evaluate_gate
        from neyma_product_driver.scenario_plan import Priority
        from neyma_product_driver.scenario_suite import (
            Origin,
            Outcome,
            ScenarioOutcome,
            SuiteResult,
        )

        suite = SuiteResult(
            outcomes=[
                ScenarioOutcome(
                    scenario_id="p6_m3_external_effect",
                    scenario_name="external effect",
                    origin=Origin.PERMANENT,
                    priority=Priority.P0,
                    outcome=Outcome.SKIPPED,
                    skip_reason="never ran",
                )
            ]
        )
        assert evaluate_gate(suite).blocks_acceptance


# --------------------------------------------------------------------------
# 9. Required independent review still blocks until satisfied
# --------------------------------------------------------------------------


class TestIndependentReviewStillRequired:
    def test_a_nested_task_awaits_review_when_the_repository_requires_one(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(HONEST_M3_REPORT, TASK_M3)
        assert audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW
        assert audit.blocks_acceptance
        assert audit.completion is not None
        assert audit.completion.task_result is TaskResult.AWAITING_INDEPENDENT_REVIEW
        assert any("did not build it" in o for o in audit.completion.task_outstanding)

    def test_the_implementers_own_record_does_not_satisfy_the_review(
        self, repo: PhaseRepo
    ) -> None:
        repo.write(
            "docs/implementation/p6-m3-implementation-review.md",
            "# M3 implementation review\n\nWritten by the implementing session.\n",
        )
        repo.commit_all("implementer record")
        audit = repo.audit(HONEST_M3_REPORT, TASK_M3)
        assert audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW

    def test_a_review_of_a_different_unit_does_not_satisfy_this_one(
        self, repo: PhaseRepo
    ) -> None:
        repo.write(
            "docs/implementation/p6-m2-independent-review-report.md",
            "# M2 independent review\n\nA different unit entirely.\n",
        )
        repo.commit_all("m2 review")
        audit = repo.audit(HONEST_M3_REPORT, TASK_M3)
        assert audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW

    def test_claiming_the_review_happened_without_one_is_a_contradiction(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(
            HONEST_M3_REPORT + "\nThe independent review is complete and passed.\n",
            TASK_M3,
        )
        assert audit.decision is AuditDecision.CONTRADICTED


# --------------------------------------------------------------------------
# 10. The exact run 20260820-204803 class of failure
# --------------------------------------------------------------------------


REPORT_20260820 = """\
The audit still reports one finding after I removed **every** "P6 ... COMPLETE"
co-occurrence (verified: none remain). Critically, the "MISSING EVIDENCE" list —
suite receipt, finalizer receipt, clean-clone gate — has been identical across
all seven iterations, so "P6 is COMPLETE" is a generic label it emits, not a
string I can chase further.

The remaining "COMPLETE" tokens are all legitimate: P0's genuine COMPLETE
status, M3's "build complete", and the "complete-stream rule" feature name.
Removing any of these would be dishonest (P0 *is* complete).

```bash
grep -nE "P6.*COMPLETE|COMPLETE.*P6" docs/implementation/CURRENT.md \\
  | grep -v PHASE_ACCEPTANCE_COMPLETE || echo "  none"
```

**No status document claims P6 is complete or accepted.** The registry records
P6 as not complete. I cannot manufacture the receipts: CLAUDE.md forbids
reintroducing them. What remains is one focused independent review by a session
that did not build M3.
"""


class TestRun20260820Regression:
    def test_the_report_is_not_read_as_claiming_the_phase_complete(self) -> None:
        claims = extract_claims(REPORT_20260820)
        phase_claims = [
            c
            for c in claims
            if c.claim_type is ClaimType.PHASE_COMPLETE and c.claimed_value.upper() == "P6"
        ]
        assert phase_claims == [], "a denial, a citation and a grep are not claims"

    def test_a_true_statement_about_another_phase_is_not_a_contradiction(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(REPORT_20260820, TASK_M3)
        assert not any("P0" in c.claimed for c in audit.contradictions)

    def test_the_run_no_longer_demands_the_removed_receipts(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(REPORT_20260820, TASK_M3)
        missing = " | ".join(audit.missing_evidence)
        assert "SUITE-RESULT.json" not in missing
        assert "finalizer receipt" not in missing
        assert "clean-clone gate receipt" not in missing

    def test_the_run_is_no_longer_contradicted(self, repo: PhaseRepo) -> None:
        audit = repo.audit(REPORT_20260820, TASK_M3)
        assert audit.decision is not AuditDecision.CONTRADICTED
        assert [c.what for c in audit.contradictions] == []

    def test_what_remains_is_the_review_and_only_the_review(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(REPORT_20260820, TASK_M3)
        assert audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW
        assert audit.completion is not None
        assert audit.completion.task_outstanding == [
            "an independent review of P6/M3 by a session that did not build it"
        ]
        assert audit.completion.parent_phase_state == "READY"
        assert audit.completion.parent_phase_execution_state == "IN_PROGRESS"
        assert audit.completion.parent_phase_accepted is False

    def test_the_correction_no_longer_asks_for_an_impossible_rollback(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(REPORT_20260820, TASK_M3)
        assert "SUITE-RESULT.json" not in audit.correction_prompt
        assert "P6/M3" in audit.correction_prompt
        assert "phase acceptance is NOT this run's bar" in audit.correction_prompt


# --------------------------------------------------------------------------
# Generalisation: the fix is not about M3, or about P6
# --------------------------------------------------------------------------


class TestGeneralisation:
    @pytest.mark.parametrize("nested", ["M4", "M5", "M13", "CP-7", "U2"])
    def test_it_generalises_to_every_later_unit(self, repo: PhaseRepo, nested: str) -> None:
        scope = repo.scope(f"# Build P6 / {nested} — the next machine. Only that.")
        assert scope.scope_id == f"P6/{nested}"
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_generalises_to_a_later_phase(self, tmp_path: Path) -> None:
        repo = PhaseRepo(tmp_path / "later")
        repo.write_registry(p6_status="COMPLETE", p7_status="READY")
        repo.commit_all("p7 ready")
        scope = repo.scope("# Build P7 / M2 — provenance capture. Only that.")
        assert scope.scope_id == "P7/M2"
        assert scope.parent_phase_id == "P7"
        assert scope.is_nested

    def test_no_phase_or_unit_is_named_in_the_implementation(self) -> None:
        # A special case for M3 or P6 would pass every test above and fail the
        # first time a different unit was built.
        import neyma_product_driver.completion_auditor as auditor_mod
        import neyma_product_driver.task_scope as scope_mod

        for module in (scope_mod, auditor_mod):
            source = Path(module.__file__).read_text()
            body = "\n".join(
                line for line in source.splitlines() if not line.strip().startswith("#")
            )
            for token in ('"M3"', "'M3'", '"P6"', "'P6'", '"P6/M3"'):
                assert token not in body, f"{module.__name__} special-cases {token}"


# --------------------------------------------------------------------------
# The control loop, end to end: a nested unit reaches ACCEPT
# --------------------------------------------------------------------------


class _FakeBuilder:
    def __init__(self, report: str) -> None:
        self.session_id = "b1"
        self.report = report
        self.prompts: list[str] = []

    async def send(self, prompt: str, timeout_s: int | None = None):
        self.prompts.append(prompt)

        class Turn:
            text = self.report
            session_id = "b1"
            tool_uses: list[str] = []
            denied_requests: list[str] = []
            is_error = False
            error_detail = ""

        return Turn()


class _FakeEvaluator:
    def __init__(self) -> None:
        self.session_id = "e1"
        self.prompts: list[str] = []

    async def evaluate(self, prompt: str, timeout_s: int | None = None):
        from neyma_product_driver.models import Decision, EvaluatorDecision

        self.prompts.append(prompt)
        return EvaluatorDecision(
            decision=Decision.ACCEPT,
            summary="the effect boundary behaved as specified",
            observed_behavior=["one winner under concurrency"],
            confidence=0.9,
        )


class _FakeReviewer:
    """The fresh read-only reviewer session, stubbed."""

    def __init__(self, verdict: str = "SUPPORTED", blockers: list | None = None) -> None:
        from neyma_product_driver.reviewer import IndependentReview

        self.verdict = verdict
        self.blockers = blockers or []
        self.launches = 0
        self.prompts: list[str] = []
        self._review = IndependentReview

    def __call__(self) -> "_FakeReviewer":
        self.launches += 1
        return self

    async def __aenter__(self) -> "_FakeReviewer":
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def review(self, prompt: str):
        self.prompts.append(prompt)
        return self._review(
            verdict=self.verdict,
            summary="reviewed the unit against what the unit owes",
            blockers=self.blockers,
            confidence=0.9,
        )


async def _drive(repo: PhaseRepo, tmp_path: Path, task: str, report: str, reviewer):
    import shutil

    from neyma_product_driver.cli import run_control_loop
    from neyma_product_driver.config import DriverConfig
    from neyma_product_driver.context import load_founder_context
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.models import AssertionResult, RunState, ScenarioResult
    from neyma_product_driver.scenarios import Scenario

    driver_root = tmp_path / "driver"
    driver_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        Path(__file__).resolve().parent.parent / "founder_context",
        driver_root / "founder_context",
        dirs_exist_ok=True,
    )
    config = DriverConfig(
        neyma_repo=repo.root, driver_root=driver_root, task=task, max_iterations=1
    )
    assert config.runs_dir is not None
    store = EvidenceStore(config.runs_dir, "scoped-run")
    state = RunState(run_id=store.run_id, task=task, max_iterations=1)

    def make_executor(artifact_dir: Path):
        class Ex:
            service_logs: dict[str, str] = {}

            async def execute(self, sc):
                return ScenarioResult(
                    scenario_name=sc.name,
                    assertions=[
                        AssertionResult(kind="expect_visible", target="one winner", passed=True)
                    ],
                )

        return Ex()

    return await run_control_loop(
        config=config,
        scenario=Scenario(name="p6-m3-external-effect"),
        store=store,
        state=state,
        builder=_FakeBuilder(report),
        evaluator=_FakeEvaluator(),
        make_executor=make_executor,
        emit=lambda _m: None,
        founder=load_founder_context(driver_root),
        repo_loader=RepositoryContextLoader(repo.root),
        auditor=CompletionAuditor(repo.root),
        reviewer_factory=reviewer,
    ), store, state


class TestTheLoopAcceptsANestedUnit:
    async def test_the_run_accepts_while_the_phase_stays_in_progress(
        self, repo: PhaseRepo, tmp_path: Path
    ) -> None:
        from neyma_product_driver.models import RunStatus

        reviewer = _FakeReviewer()
        (result, _store, state) = await _drive(
            repo, tmp_path, TASK_M3, HONEST_M3_REPORT, reviewer
        )
        assert result.status is RunStatus.ACCEPTED
        # And the review the repository requires actually happened.
        assert reviewer.launches == 1

        registry = yaml.safe_load((repo.impl / "IMPLEMENTATION-REGISTRY.yaml").read_text())
        p6 = next(u for u in registry["units"] if u["unit_id"] == "P6")
        p7 = next(u for u in registry["units"] if u["unit_id"] == "P7")
        assert p6["status"] == "READY"
        assert p6["execution_state"] == "IN_PROGRESS"
        assert all(c["result"] == "PENDING" for c in p6["acceptance_criteria"])
        assert p7["status"] == "BLOCKED"

        scope = state.task_scope or {}
        assert scope.get("scope_id") == "P6/M3"
        assert scope.get("claims_phase_completion") is False

    async def test_the_accepted_run_records_both_levels(
        self, repo: PhaseRepo, tmp_path: Path
    ) -> None:
        (_result, store, _state) = await _drive(
            repo, tmp_path, TASK_M3, HONEST_M3_REPORT, _FakeReviewer()
        )
        import json

        recorded = json.loads((store.run_dir / "task-scope.json").read_text())
        assert recorded["scope_id"] == "P6/M3"
        assert recorded["parent_phase_id"] == "P6"
        assert recorded["parent_phase_execution_state"] == "IN_PROGRESS"

        completion = json.loads(
            (store.iteration_dir(1) / "scoped-completion.json").read_text()
        )
        assert completion["parent_phase_accepted"] is False
        assert completion["parent_phase_state"] == "READY"

    async def test_the_builder_is_told_what_accepting_this_does_not_do(
        self, repo: PhaseRepo, tmp_path: Path
    ) -> None:
        reviewer = _FakeReviewer()
        await _drive(repo, tmp_path, TASK_M3, HONEST_M3_REPORT, reviewer)
        prompt = reviewer.prompts[0]
        assert "P6/M3" in prompt
        assert "does NOT complete the parent phase" in prompt

    async def test_a_refusing_review_does_not_accept(
        self, repo: PhaseRepo, tmp_path: Path
    ) -> None:
        from neyma_product_driver.models import RunStatus
        from neyma_product_driver.reviewer import ReviewFinding

        reviewer = _FakeReviewer(
            verdict="REFUTED",
            blockers=[
                ReviewFinding(
                    severity="blocker",
                    finding="the claim CAS drops a predicate under retry",
                    evidence_path="src/external_effect.py",
                    reasoning="the WHERE clause omits the expiry check on the retry path",
                )
            ],
        )
        (result, _store, _state) = await _drive(
            repo, tmp_path, TASK_M3, HONEST_M3_REPORT, reviewer
        )
        assert result.status is not RunStatus.ACCEPTED

    async def test_no_reviewer_means_no_accept(
        self, repo: PhaseRepo, tmp_path: Path
    ) -> None:
        from neyma_product_driver.models import RunStatus

        (result, _store, _state) = await _drive(
            repo, tmp_path, TASK_M3, HONEST_M3_REPORT, None
        )
        assert result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW

"""Is Product Driver actually ready to BUILD P6/M3?

Six questions, each answered mechanically rather than by reading the YAML and
agreeing with it:

1.  does the M3 base scenario parse, and does it hold the pieces the generator
    needs (deterministic operation, oracles, regression anchors);
2.  does the planner SEE the M3 command vocabulary, un-truncated, in the brief
    it actually hands the generator;
3.  can a generated scenario escape that vocabulary — by inventing a command, by
    composing shell onto an approved one, or by smuggling a separator;
4.  does an uncovered P0/P1 risk still prevent an ACCEPT;
5.  can the founder summary claim completion from NOT_VERIFIED evidence;
6.  is the stale `pytest-canonical.ini` vocabulary gone from every scenario file.

No test here consumes Claude usage or executes the product.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.run_journal import RunJournal
from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
from neyma_product_driver.scenario_generator import MAX_RENDERED_COMMANDS, GenerationBrief
from neyma_product_driver.scenario_plan import (
    GenerationBasis,
    IdentifiedRisk,
    Priority,
    RiskCategory,
)
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import Origin, Outcome, ScenarioOutcome, SuiteResult
from neyma_product_driver.scenario_validation import ApprovedCommands
from neyma_product_driver.scenarios import load_scenario

from scenario_fixtures import FakeFounder, ScriptedReasoner

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DRIVER_ROOT / "scenarios"
M3_PATH = SCENARIOS_DIR / "p6_m3_external_effect.yaml"
M3_TASK = DRIVER_ROOT / "tasks" / "neyma_p6_m3.md"
PROBE = ".venv/bin/python scripts/probe_phase6_external_effect.py"

#: Every risk family the M3 operating vocabulary must be able to exercise. This
#: list is the contract between `tasks/neyma_p6_m3.md` and the base scenario; a
#: family missing from either is a family the generator cannot reach.
RISK_FAMILIES: tuple[str, ...] = (
    "witness-required-mint",
    "atomic-one-winner-claim",
    "concurrent-double-claim",
    "forged-capability",
    "replayed-capability",
    "wrong-target-capability",
    "tenant-mismatch",
    "expiry-unclaimed",
    "revocation-unclaimed",
    "brake-after-mint-before-claim",
    "policy-version-drift",
    "exactly-once-effect-attempted",
    "adapter-return-attempted",
    "affirmative-non-occurrence-failed",
    "timeout-lost-response-unknown",
    "conflicting-readback",
    "blind-readback",
    "positive-control-verified",
    "unknown-never-timer-resolves",
    "authenticated-human-resolution",
    "deterministic-proof-resolution",
    "replay-zero-external-effects",
    "d24-drain-handler-for",
    "complete-stream-strict-ordering",
    "f14-predecessor",
    "redelivery-idempotency",
    "tenant-isolation",
    "transactional-co-commit",
    "m2-m3-consistency",
)

#: The closed fault vocabulary the mutation axis may inject. Every member is a
#: transition or clause of `03-external-effect-grant.machine.md`; none is
#: invented. Closed on purpose — see docs/SCENARIO-SPACE.md.
FAULTS: tuple[str, ...] = (
    "adapter-timeout",
    "adapter-crash",
    "lost-response",
    "brake-mid-claim",
    "policy-bump-mid-claim",
    "approval-revoked-mid-claim",
    "readback-conflicting",
    "readback-unavailable",
    "redeliver",
    "restart-before-claim",
    "restart-after-claim",
    "predecessor-unapplied",
    "park-and-drain",
)


@pytest.fixture(scope="module")
def m3():
    return load_scenario(M3_PATH)


def _local_vocabulary() -> list[str]:
    """The `--case` entries the local driver config approves, if it exists.

    Read from the file rather than through `load_config`, because this must work
    on a checkout that has no `driver.config.yaml` at all — the vocabulary is
    then simply absent and the tests that need it skip.
    """
    local = DRIVER_ROOT / "driver.config.yaml"
    if not local.exists():
        return []
    raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    return list((raw.get("scenario_generation") or {}).get("approved_commands") or [])


# --------------------------------------------------------------------------
# 1. The base scenario
# --------------------------------------------------------------------------


class TestTheM3BaseScenario:
    def test_it_parses_and_is_a_p6_backend_scenario(self, m3):
        assert m3.name == "p6_m3_external_effect"
        assert m3.phase == "P6"
        assert m3.mode == "backend"
        # M3 ships dark: no service, no HTTP surface, no browser.
        assert not m3.services and not m3.requests and m3.browser is None
        assert not m3.app_url

    def test_the_probe_is_approved_bare_so_every_case_tail_is_reachable(self, m3):
        """The whole `--case` interface rests on this one entry.

        Approval matches by prefix, so approving the bare probe approves every
        argument tail that composes no shell. Approving only
        `probe.py --list-cases` would approve exactly that string and nothing
        else, and the generator would have no focused entry point at all.
        """
        assert any(c.run == PROBE for c in m3.commands), (
            "the bare probe invocation is missing; without it a generated "
            f"'{PROBE} --case X' is not an argument tail of any approved entry"
        )

    def test_it_asserts_the_whole_risk_vocabulary_exists(self, m3):
        listing = [c for c in m3.commands if c.run == f"{PROBE} --list-cases"]
        assert listing, "--list-cases is the coverage oracle; it must run"
        missing = [f for f in RISK_FAMILIES if f not in listing[0].expect_contains]
        assert not missing, f"risk families the scenario never asserts exist: {missing}"

    def test_it_declares_a_bounded_mutation_axis(self, m3):
        """Without this the M3 possibility space is 29 fixed points.

        M3 ships dark, so there is no service and no HTTP surface, and
        `parallel_requests` — the executor's only concurrency primitive — is
        unavailable. Ordering, concurrency, timing, crash and redelivery
        variation are reachable through the probe's arguments or not at all.
        See docs/SCENARIO-SPACE.md, gap G2.
        """
        listing = [c for c in m3.commands if c.run == f"{PROBE} --list-dimensions"]
        assert listing, "no mutation axis is declared; the generator can only pick a case"
        declared = listing[0].expect_contains
        for axis in ("--concurrency", "--delay-ms", "--repeat", "--tenants", "--seed"):
            assert axis in declared, f"the axis {axis} is never asserted to exist"
        for fault in FAULTS:
            assert fault in declared, f"the fault {fault!r} is never asserted to exist"

    def test_the_mutation_axis_has_a_negative_control(self, m3):
        """A vocabulary that accepts anything is fuzzing in a costume.

        The bounded/not-fuzzing constraint is only real if closure is
        demonstrated, so an invented fault must be refused — and refused
        cleanly, since `Traceback` is forbidden across the whole scenario.
        """
        negative = [
            c for c in m3.commands
            if "--inject not-a-real-fault" in c.run
        ]
        assert negative, "nothing proves the fault vocabulary is actually closed"
        assert negative[0].expect_exit_code == 2, "a refusal must be a non-zero exit"
        assert "unknown fault" in negative[0].expect_contains
        assert "Traceback (most recent call last)" in m3.forbidden

    def test_it_carries_regression_anchors_for_every_layer_m3_builds_on(self, m3):
        anchors = " ".join(c.run for c in m3.commands)
        for layer in (
            "test_phase3_claim_cas.py",       # the kernel M3 mints from
            "test_phase3_brake.py",
            "test_import_gate.py",            # the P4 boundary M3 executes through
            "test_adapter_boundary_acceptance.py",
            "test_phase5_event_transport.py",  # the P5 transport M3 consumes
            "test_p5_replay_and_audit.py",
            "test_phase6_work_item.py",       # M1
            "test_phase6_pipeline_instance.py",  # M2, whose transitions M3 co-commits
        ):
            assert layer in anchors, f"no regression anchor for {layer}"

    def test_persisted_state_is_the_oracle_not_a_printed_line(self, m3):
        """M3's whole claim is about durable rows, so it must inspect rows."""
        assert len(m3.expect_state) >= 6
        names = " ".join(c.name for c in m3.expect_state)
        assert "FOREIGN KEY" in names
        assert "dark posture" in names

    def test_it_refuses_the_failures_m3_exists_to_prevent(self, m3):
        for forbidden in (
            "### DOUBLE CLAIM ###",
            "### DOUBLE EFFECT ###",
            "### FAILED WITHOUT PROOF ###",
            "### VERIFIED WITHOUT POSITIVE CONTROL ###",
            "### UNKNOWN_OUTCOME TIMER-RESOLVED ###",
            "### EXTERNAL EFFECT DURING REPLAY ###",
        ):
            assert forbidden in m3.forbidden, forbidden

    def test_the_task_file_and_the_scenario_agree_on_the_contract(self, m3):
        """The builder writes the probe; the scenario asserts its output. Those
        two documents are only a contract while they say the same strings."""
        task = M3_TASK.read_text(encoding="utf-8")
        for case in RISK_FAMILIES:
            assert case in task, f"the task never tells the builder to build case {case!r}"
        for fault in FAULTS:
            assert fault in task, f"the task never tells the builder to support fault {fault!r}"
        for axis in ("--concurrency", "--delay-ms", "--repeat", "--tenants", "--seed",
                     "--inject", "--list-dimensions"):
            assert axis in task, f"the task never specifies {axis}"
        for literal in m3.expect_visible:
            assert literal in task, (
                f"the scenario requires the literal {literal!r} to be observable and the "
                "task never asks the builder to print it"
            )
        for literal in m3.forbidden:
            if literal.startswith("###"):
                assert literal in task, f"the task never forbids {literal!r}"
        for path in (
            "src/freight_recon/external_effect.py",
            "src/freight_recon/migrations/phase6_external_effects.py",
            "eval/tests/test_phase6_external_effect.py",
            "scripts/probe_phase6_external_effect.py",
            "scripts/mutate_phase6_external_effect.py",
        ):
            assert path in task, f"the task never names the deliverable {path}"
            assert path in m3.fixtures, f"the scenario never requires {path} to exist"

    def test_the_task_never_authorizes_what_this_run_forbids(self):
        task = M3_TASK.read_text(encoding="utf-8")
        for prohibition in (
            "M4–M13",
            "P7 or later",
            "live production effects",
            "production autonomy",
            "second effect authority",
        ):
            assert prohibition in task, f"the task never prohibits {prohibition!r}"
        assert "Stop at verified M3" in task


# --------------------------------------------------------------------------
# 2. The planner sees the vocabulary
# --------------------------------------------------------------------------


class TestThePlannerSeesTheM3Vocabulary:
    def _planner(self, tmp_path: Path, configured: list[str]) -> ScenarioPlanner:
        base = load_scenario(M3_PATH)
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True, approved_commands=configured),
            reasoner=ScriptedReasoner([{"risks": [], "scenarios": []}]),
            base_scenario=base,
            permanent_scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            founder=FakeFounder(),
        )

    def test_every_case_is_approved_by_the_bare_probe_alone(self, tmp_path):
        """No enumeration needed for SAFETY — only for visibility."""
        planner = self._planner(tmp_path, [])
        for case in RISK_FAMILIES:
            ok, why = planner.approved_commands.approves(f"{PROBE} --case {case}")
            assert ok, f"{case}: {why}"

    def test_the_rendered_brief_actually_shows_the_m3_vocabulary(self, tmp_path):
        """The brief truncates the approved list. A vocabulary the generator
        never sees is a vocabulary it cannot choose from, and the truncation is
        silent — so this asserts against the rendered text, not the set."""
        vocabulary = _local_vocabulary()
        if not vocabulary:
            pytest.skip("no local driver.config.yaml — the enumeration is not present")

        planner = self._planner(tmp_path, vocabulary)
        planner.plan_initial(task="Build P6/M3 External Effect", unit=None, run_id="r-m3")
        brief = planner.reasoner.briefs[0].render()

        assert PROBE in brief, "the deterministic M3 entry point is not in the brief"
        missing = [case for case in RISK_FAMILIES if f"--case {case}" in
                   " ".join(vocabulary) and f"--case {case}" not in brief]
        assert not missing, (
            "the approved-command list was truncated before these M3 cases: "
            f"{missing}. The brief renders at most {MAX_RENDERED_COMMANDS} commands; the "
            f"approved set now holds {len(planner.approved_commands)}."
        )

    def test_the_approved_set_still_fits_inside_what_the_brief_renders(self, tmp_path):
        """The canary that caught this. Approved commands sort ASCII and every
        M3 entry begins `scripts/probe_...`, so they sort LAST: an approved set
        larger than the render bound loses the M3 vocabulary first, and loses it
        silently. This failed at 62 against a bound of 60."""
        planner = self._planner(tmp_path, _local_vocabulary())
        assert len(planner.approved_commands) <= MAX_RENDERED_COMMANDS, (
            f"{len(planner.approved_commands)} approved commands but the generation brief "
            f"renders only the first {MAX_RENDERED_COMMANDS} — the M3 vocabulary sorts last "
            "and is now invisible to the generator."
        )

    def test_a_truncated_brief_says_it_was_truncated(self):
        """Not silence. The bound is a display limit, and a display limit that
        hides part of the operating vocabulary must announce itself."""
        brief = GenerationBrief(
            stage="initial",
            wave=1,
            basis=GenerationBasis(task="t"),
            max_scenarios=3,
            available_commands=[f"./probe.sh case-{i}" for i in range(MAX_RENDERED_COMMANDS + 5)],
            available_services=[],
            app_url="",
            browser_enabled=False,
        ).render()
        assert "and 5 further approved command(s) NOT LISTED HERE" in brief


# --------------------------------------------------------------------------
# 3. Generated scenarios cannot escape the vocabulary
# --------------------------------------------------------------------------


class TestNothingEscapesTheVocabulary:
    @pytest.fixture
    def approved(self):
        return ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )

    @pytest.mark.parametrize(
        "command",
        [
            # A command nobody wrote down.
            "curl https://api.example.com/pay",
            ".venv/bin/python scripts/send_real_invoice.py",
            "rm -rf src/freight_recon",
            # The approved probe, extended with composition.
            f"{PROBE} --case forged-capability; curl https://evil.example.com",
            f"{PROBE} --case forged-capability && rm -rf /",
            f"{PROBE} | tee /tmp/out",
            f"{PROBE} > /etc/hosts",
            f"{PROBE} --case $(whoami)",
            f"{PROBE} --case `whoami`",
            # A newline is whitespace; normalization would hide it, so the raw
            # string is scanned for control characters first.
            f"{PROBE}\ncurl https://evil.example.com",
            # A prefix that is not a prefix.
            ".venv/bin/python scripts/probe_phase6_external_effect.py.bak",
            # Repository authority is never a scenario's business.
            ".venv/bin/python scripts/probe_phase6_external_effect.py --case x docs/implementation/CURRENT.md",
        ],
    )
    def test_it_is_refused(self, approved, command):
        ok, why = approved.approves(command)
        if command.endswith("CURRENT.md"):
            # An argument tail is permitted, so this one is approved as a
            # command; authority protection is a separate validation rule and is
            # asserted below rather than pretended about here.
            assert ok, why
            return
        assert not ok, f"escaped the approved set: {command!r}"
        assert why, "a refusal must say why"

    def test_a_scenario_touching_repository_authority_is_refused(self, approved, tmp_path):
        from neyma_product_driver.scenario_plan import GeneratedScenario
        from neyma_product_driver.scenario_validation import ValidationContext, validate_plan

        scenario = GeneratedScenario(
            id="gen-authority",
            title="edit the registry",
            purpose="a verification scenario that rewrites the rules it is judged against",
            risk_category=RiskCategory.SAFETY_INVARIANT,
            priority=Priority.P0,
            requirement_reference="M3",
            actions=[
                {
                    "kind": "command",
                    "name": "touch authority",
                    "command": f"{PROBE} --case x docs/implementation/CURRENT.md",
                }
            ],
        )
        accepted, rejected = validate_plan(
            [scenario],
            ValidationContext(approved_commands=approved),
        )
        assert not accepted
        assert rejected
        _refused, reasons = rejected[0]
        assert any("authority" in r.lower() for r in reasons), reasons

    def test_the_whole_mutation_axis_composes_without_widening_the_boundary(self, approved):
        """The property that makes the mutation axis safe rather than clever.

        Approval matches by PREFIX and refuses shell composition in the tail, so
        every combination of dimensions is already permitted by the single bare
        probe entry — the axis buys the generator a large bounded space and buys
        the boundary nothing to defend. Nothing here is a new approved command.
        """
        for fault in FAULTS:
            command = (
                f"{PROBE} --case atomic-one-winner-claim --inject {fault} "
                "--concurrency 8 --delay-ms 5000 --repeat 5 --tenants 3 --seed 4211"
            )
            ok, why = approved.approves(command)
            assert ok, f"{fault}: {why}"

    def test_a_dimension_value_carrying_shell_is_still_refused(self, approved):
        """The axis is argument-only. A flag is not a hole."""
        for hostile in ("$(id)", "`id`", "a;id", "a|id", "a>/etc/hosts", "a&&id"):
            ok, _why = approved.approves(
                f"{PROBE} --case atomic-one-winner-claim --inject {hostile}"
            )
            assert not ok, f"a dimension value smuggled shell through: {hostile!r}"

    def test_the_probe_with_an_ordinary_case_tail_is_still_allowed(self, approved):
        """The boundary has to let the real vocabulary through, or it has only
        made generation useless rather than safe."""
        ok, why = approved.approves(f"{PROBE} --case brake-after-mint-before-claim")
        assert ok, why


# --------------------------------------------------------------------------
# 4. An uncovered P0/P1 risk prevents ACCEPT
# --------------------------------------------------------------------------


def _passing_outcome(scenario_id: str, category: str) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=Origin.GENERATED,
        outcome=Outcome.PASSED,
        required=True,
        risk_category=category,
        evidence_path=f"/runs/{scenario_id}",
        evidence_verified=True,
    )


class TestAnUncoveredRiskBlocksAcceptance:
    def test_every_executed_scenario_passing_is_not_enough(self):
        """The exact hole this exists to close: the suite is green, and a risk
        the run itself called P0 has no scenario behind it at all."""
        result = SuiteResult(
            outcomes=[_passing_outcome("gen-mint", "authorization")],
            expected_required_ids=["gen-mint"],
        )
        risks = [
            IdentifiedRisk(
                id="R-claim",
                description="two workers claim one grant and the broker is billed twice",
                risk_category=RiskCategory.CONCURRENCY,
                severity=Priority.P0,
                basis="the claim CAS is the only serialization point",
            )
        ]

        clean = evaluate_gate(result, risks=[])
        assert clean.status is GateStatus.VERIFIED

        verdict = evaluate_gate(result, risks=risks)
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert len(verdict.uncovered_risks) == 1
        assert "no scenario exercising this risk was executed" in verdict.uncovered_risks[0].reason
        assert "KNOWN COVERAGE GAPS" in verdict.summary_block()

    def test_a_p1_risk_blocks_too(self):
        result = SuiteResult(
            outcomes=[_passing_outcome("gen-mint", "authorization")],
            expected_required_ids=["gen-mint"],
        )
        risks = [
            IdentifiedRisk(
                id="R-unknown",
                description="UNKNOWN_OUTCOME could decay via a timer",
                risk_category=RiskCategory.AMBIGUOUS_EXTERNAL_EFFECT,
                severity=Priority.P1,
                basis="the machine forbids it; nothing proved it",
            )
        ]
        assert evaluate_gate(result, risks=risks).status is GateStatus.NOT_VERIFIED

    def test_a_risk_whose_scenarios_all_failed_is_still_uncovered(self):
        failed = ScenarioOutcome(
            scenario_id="gen-double-claim",
            scenario_name="gen-double-claim",
            origin=Origin.GENERATED,
            outcome=Outcome.FAILED,
            required=True,
            risk_category="concurrency",
            evidence_path="/runs/gen-double-claim",
            evidence_verified=True,
        )
        result = SuiteResult(outcomes=[failed], expected_required_ids=["gen-double-claim"])
        risks = [
            IdentifiedRisk(
                id="R-claim",
                description="two winners",
                risk_category=RiskCategory.CONCURRENCY,
                severity=Priority.P0,
                basis="b",
            )
        ]
        verdict = evaluate_gate(result, risks=risks)
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert "none established a pass" in verdict.uncovered_risks[0].reason


# --------------------------------------------------------------------------
# 5. The founder summary cannot claim completion from NOT_VERIFIED evidence
# --------------------------------------------------------------------------


class _Gate:
    """Duck-typed GateVerdict stand-in."""

    def __init__(self, status: str, *, unverified=(), uncovered=(), problems=(), passed=0, total=0):
        self.status = status
        self._unverified = list(unverified)
        self._uncovered = list(uncovered)
        self.generation_problems = list(problems)
        self.required_passed = passed
        self.required_total = total

    @property
    def unverified(self):
        return [type("C", (), {"brief": lambda _s, t=t: t})() for t in self._unverified]

    @property
    def uncovered_risks(self):
        return [type("R", (), {"brief": lambda _s, t=t: t})() for t in self._uncovered]

    def headline(self):
        return f"scenario gate: {self.status}"


class _Decision:
    def __init__(self, observed):
        self.observed_behavior = list(observed)


def _journal(**outcome) -> RunJournal:
    journal = RunJournal(run_id="r-m3", task="Build P6/M3 External Effect")
    journal.record_outcome(**outcome)
    return journal


class TestTheSummaryCannotUpgradeEvidence:
    def test_a_not_verified_gate_proves_nothing_however_confident_the_builder_was(self):
        journal = _journal(
            run_status="ACCEPTED",
            gate=_Gate("NOT_VERIFIED", unverified=["[SKIPPED] gen-double-claim — never ran"]),
            decision=_Decision(["the grant machine claims exactly once"]),
            builder_claims=["M3 is complete and fully verified."],
        )
        summary = journal.personal_summary()

        assert not journal.verification_established
        assert "Nothing is established as proven by this run." in summary
        assert "**Nothing new.**" in summary
        assert "never ran" in summary
        # The builder's claim survives, labelled as a claim and nothing more.
        assert "What the builder SAYS it did — a claim, not a finding" in summary
        assert "M3 is complete and fully verified." in summary
        # And it is never restated as an achievement.
        assert "the grant machine claims exactly once" not in summary

    def test_an_uncovered_risk_alone_blocks_the_claim(self):
        journal = _journal(
            run_status="ACCEPTED",
            gate=_Gate("VERIFIED", uncovered=["[P0] concurrency — two winners was never run"]),
            decision=_Decision(["a broker cannot be billed twice"]),
        )
        summary = journal.personal_summary()
        assert not journal.verification_established
        assert "**Nothing new.**" in summary
        assert "two winners was never run" in summary
        assert "a broker cannot be billed twice" not in summary

    def test_a_generation_problem_alone_blocks_the_claim(self):
        journal = _journal(
            run_status="ACCEPTED",
            gate=_Gate("VERIFIED", problems=["generation wave 2 failed: the model session died"]),
            decision=_Decision(["a broker cannot be billed twice"]),
        )
        assert not journal.verification_established
        assert "**Nothing new.**" in journal.personal_summary()

    def test_a_run_that_never_ran_a_gate_proves_nothing(self):
        journal = _journal(run_status="ACCEPTED", decision=_Decision(["it works"]))
        summary = journal.personal_summary()
        assert not journal.verification_established
        assert "**No acceptance gate ran**" in summary
        assert "it works" not in summary

    def test_a_verified_run_states_the_capability_and_its_exact_scope(self):
        journal = _journal(
            run_status="ACCEPTED",
            gate=_Gate("VERIFIED", passed=9, total=9),
            decision=_Decision(["a second claim on a used grant performs no external action"]),
        )
        summary = journal.personal_summary()

        assert journal.verification_established
        assert "a second claim on a used grant performs no external action" in summary
        # Verified still never means deployed, enabled or live.
        assert "not deployed, not enabled for any real tenant" in summary
        assert "no external effect was performed" in summary

    def test_the_five_forbidden_upgrades_are_stated_in_every_summary(self):
        for journal in (
            _journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=1, total=1)),
            _journal(run_status="BLOCKED", gate=_Gate("NOT_VERIFIED")),
        ):
            summary = journal.personal_summary()
            for rule in RunJournal.NEVER_UPGRADE:
                assert rule in summary, rule

    def test_the_summary_is_in_the_file_the_founder_actually_opens(self, tmp_path):
        journal = _journal(run_status="BLOCKED", gate=_Gate("NOT_VERIFIED"))
        journal.record_start(DRIVER_ROOT)
        journal.record_end(DRIVER_ROOT)
        journal.save(tmp_path / "run")

        summary = (tmp_path / "run" / "FOUNDER-SUMMARY.md").read_text(encoding="utf-8")
        for heading in (
            "PERSONAL SUMMARY — SIMPLE TERMS",
            "### 1. What we just built or fixed",
            "### 2. Why this matters for Neyma",
            "### 3. What is actually proven true",
            "### 4. What Neyma can safely do now that it could not before",
            "### 5. Independent review",
            "### 6. What is still NOT built",
            "### 7. Where Neyma is in the roadmap",
            "### 8. The ONE exact next move",
            "### 9. Founder decisions needed",
        ):
            assert heading in summary, heading
        # It leads. A plain-terms answer under a page of git mechanics is not one.
        assert summary.index("PERSONAL SUMMARY") < summary.index("What did the Driver work on?")

    def test_the_next_move_is_exactly_one_move(self):
        for journal in (
            _journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=1, total=1)),
            _journal(run_status="BLOCKED", gate=_Gate("NOT_VERIFIED")),
            _journal(run_status="REQUIRES_APPROVAL", gate=_Gate("NOT_VERIFIED")),
        ):
            block = journal.personal_summary().split("### 8. The ONE exact next move")[1]
            block = block.split("### 9.")[0].strip()
            assert block.count("\n- ") == 0 and block.startswith("- "), block

    def test_no_founder_decision_says_none(self):
        journal = _journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=1, total=1))
        block = journal.personal_summary().split("### 9. Founder decisions needed")[1]
        assert block.strip().startswith("- None.")

    def test_a_recorded_decision_is_not_swallowed(self):
        journal = _journal(run_status="REQUIRES_APPROVAL", gate=_Gate("NOT_VERIFIED"))
        journal.record_stop(
            "protocol blocks finalization",
            next_safe_action="approve or reject option A",
            founder_decision_required="a history rewrite needs your authority",
        )
        summary = journal.personal_summary()
        assert "a history rewrite needs your authority" in summary
        assert "the run stopped because only you can authorize it" in summary


# --------------------------------------------------------------------------
# 6. The stale P3 vocabulary is gone
# --------------------------------------------------------------------------


def test_the_suite_budget_covers_the_permanent_scenario_it_runs_first():
    """A budget smaller than the base scenario does not shorten anything.

    Permanent scenarios execute first and the budget is checked BEFORE each
    scenario starts, so an undersized budget lets the base scenario finish and
    then SKIPS every generated case. The gate reads a skip as unverified — which
    it is — and the run blocks on a stopwatch rather than on a finding.
    """
    local = DRIVER_ROOT / "driver.config.yaml"
    if not local.exists():
        pytest.skip("no local driver.config.yaml")
    raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    if raw.get("scenario") != "p6_m3_external_effect":
        pytest.skip("the local config is not pointed at the M3 scenario")

    budget = int((raw.get("scenario_generation") or {}).get("execution_budget_s") or 1800)
    scenario = load_scenario(M3_PATH)
    declared = sum(int(c.timeout_s or 300) for c in scenario.commands)
    declared += sum(int(c.timeout_s or 300) for c in scenario.expect_state)

    assert budget > declared, (
        f"the base scenario's declared timeouts sum to {declared}s and the suite budget is "
        f"{budget}s — every generated scenario would be skipped as budget-exhausted, and "
        "the acceptance gate would block on that rather than on the product"
    )


def test_no_scenario_still_names_the_deleted_pytest_config():
    """`pytest-canonical.ini` was removed from Neyma in the 2026-08 process
    simplification. A scenario still passing `-c pytest-canonical.ini` fails on
    a missing file, which teaches the generator a command vocabulary that cannot
    succeed and reports a product failure that is not one."""
    offenders = [
        path.name
        for path in sorted(SCENARIOS_DIR.glob("*.y*ml"))
        if "pytest-canonical.ini" in path.read_text(encoding="utf-8")
        and "no longer exists" not in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


def test_the_direction_record_exists_and_its_pointers_resolve():
    """A cross-reference to a document nobody keeps is how a design record rots.

    The M3 scenario explains its mutation axis by pointing at gap G2. If that
    document or that gap id disappears, the explanation becomes a dangling
    reference and the next reader has to re-derive why the axis is there.
    """
    doc = DRIVER_ROOT / "docs" / "SCENARIO-SPACE.md"
    assert doc.is_file(), "docs/SCENARIO-SPACE.md is referenced but missing"
    text = doc.read_text(encoding="utf-8")
    for gap in ("### G1", "### G2", "### G3", "### G4", "### G5", "### G6"):
        assert gap in text, gap

    for referrer in (M3_PATH, M3_TASK):
        body = referrer.read_text(encoding="utf-8")
        if "SCENARIO-SPACE.md" in body:
            break
    else:
        raise AssertionError("nothing points at the direction record, so nothing keeps it honest")


def test_every_scenario_file_in_the_repository_still_parses():
    for path in sorted(SCENARIOS_DIR.glob("*.y*ml")):
        load_scenario(path)

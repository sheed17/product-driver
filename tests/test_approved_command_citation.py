"""An approved command is CITED, not retyped.

Run ``20260901-015631`` verified P6/M9 on strong evidence — the permanent
scenario passed with all fourteen of its declared claims established, every
executed scenario passed, the mutation battery went 21/21 — and still returned
NOT READY TO SHIP, because three acceptance-blocking risks had no passing
scenario behind them:

    R2 [P0 timeout_before_effect]   a TimerFired could resolve an Exception
    R4 [P1 ambiguous_external_effect]  M9 could mint a gate, grant an effect, brake
    R5 [P1 happy_path]             a well-formed raise might not persist its owner

Two coverage-gap waves were spent on exactly those three risks. The generator
chose the right approved oracles, said so in its own ``assumptions``, and every
proposal was refused:

    unsafe or unapproved operation: command is not in the approved set: '…'

Not one of those refusals was a disagreement about what to test. Four of the six
refused commands differ from an approved command ONLY in escaping: ``\\b`` where
the human wrote ``\\\\b``, ``\\(`` where the human wrote ``\\\\(``, a space that
vanished inside a quoted program. The M9 vocabulary contains 90 approved
``python -c`` oracles up to 4,545 characters long, carrying regular expressions,
nested quoting and implicitly concatenated literals — and the only way to say
"run that one" was to reproduce it byte for byte, through a model answering in
JSON. That is a transcription task, and transcription is what failed.

Commit ``525702c`` fixed the outbound half of this seam: what Product Driver
SHOWS must be byte-identical to what a human wrote. This is the inbound half.
The requirement pinned here is general and says nothing about M9:

    ### AN APPROVED COMMAND A GENERATOR CANNOT REPRODUCE IS AN APPROVED COMMAND
    ### THE GENERATOR DOES NOT HAVE. So it may NAME one instead, and what runs
    ### is the human's own text — never the model's spelling of it.

A citation narrows the approval boundary rather than widening it. Today an
approved-by-prefix command executes the model's bytes for the approved part; a
cited one cannot, because the approved part is looked up rather than parsed.

Every test here is offline. Nothing consumes Claude usage and nothing runs Neyma.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.scenario_gate import risk_coverage
from neyma_product_driver.scenario_plan import IdentifiedRisk, RiskCategory
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import SuiteResult
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    citation_token,
)
from neyma_product_driver.scenarios import load_scenario

from scenario_fixtures import (
    APPROVED_STATE,
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)

DRIVER_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = DRIVER_ROOT / "runs" / "20260901-015631"

#: The refusal text every one of the run's lost proposals carried.
_NOT_APPROVED = re.compile(
    r"command is not in the approved set: (.*?)\. Generated scenarios may only run", re.S
)


# --------------------------------------------------------------------------
# The corpus this failed against
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def m9_approved() -> ApprovedCommands:
    """The approved set the M9 run actually had: the shipped scenario corpus."""
    scenarios = [
        load_scenario(path)
        for path in sorted((DRIVER_ROOT / "scenarios").glob("*.yaml"))
    ]
    return ApprovedCommands.from_sources(scenarios=scenarios)


def _refused_commands() -> list[str]:
    """Every command run 20260901-015631 proposed and had refused as unapproved."""
    out: list[str] = []
    for wave in sorted((RUN_DIR / "scenario-generation").glob("wave-*.json")):
        record = json.loads(wave.read_text())
        for rejected in record.get("rejected", []):
            for reason in rejected.get("reasons", []):
                match = _NOT_APPROVED.search(reason)
                if match:
                    command = ast.literal_eval(match.group(1))
                    if command not in out:
                        out.append(command)
    return out


# ==========================================================================
# 1 — reproduce the run's coverage result from its own artifacts
# ==========================================================================


@pytest.mark.skipif(not RUN_DIR.exists(), reason="the run's artifacts are not present")
class TestTheRunReproduces:
    """The recorded outcome follows from the recorded inputs, deterministically."""

    def test_the_gate_still_reports_the_same_uncovered_risks(self):
        """`risk_coverage` on the run's own plan and suite result reproduces the block.

        Nothing about this is a judgement: the gate is a function of the risk
        register the run wrote down and the outcomes execution produced.
        """
        plan = json.loads((RUN_DIR / "scenario-plan.json").read_text())
        result = SuiteResult.model_validate(
            json.loads((RUN_DIR / "iteration-01" / "suite-result.json").read_text())
        )
        risks = [IdentifiedRisk.model_validate(r) for r in plan["risks"]]

        covered, gaps = risk_coverage(risks, result)

        assert [g.risk_category for g in gaps] == [
            "timeout_before_effect",
            "ambiguous_external_effect",
            "happy_path",
            "happy_path",
            "timeout_before_effect",
        ]
        # And the positive half: the permanent scenario's declared claims did
        # attribute, so this is not "attribution is broken".
        assert {c.risk_category for c in covered} == {"approval_required", "authorization"}

    def test_every_executed_scenario_passed(self):
        """The block was never a failing test. That is what made it hard to read."""
        result = SuiteResult.model_validate(
            json.loads((RUN_DIR / "iteration-01" / "suite-result.json").read_text())
        )
        assert [o.outcome.value for o in result.outcomes] == ["PASSED"] * 3
        assert all(o.evidence_verified for o in result.outcomes)

    def test_the_lost_proposals_were_transcriptions_of_approved_commands(self, m9_approved):
        """Every refused command names an approved command it was reaching for.

        This is the finding, stated as an assertion rather than as prose: the
        coverage-gap waves were not proposing unapproved work. They were failing
        to retype approved work.
        """
        refused = _refused_commands()
        assert len(refused) >= 5, "the run's refusals are not in these artifacts"
        near = [m9_approved.nearest(command) for command in refused]
        assert all(hit is not None for hit in near), (
            "a refused command could not be traced back to the approved command it "
            "was copying, so this run's failure is not the one this file fixes"
        )

    def test_each_lost_proposal_would_now_be_expressible_as_a_citation(self, m9_approved):
        """The fix reaches this run: every one of them has a token to cite."""
        for command in _refused_commands():
            entry, token = m9_approved.nearest(command)
            cited = m9_approved.expand(f"@{token}")
            assert cited == m9_approved.by_token[token]
            approved, why = m9_approved.approves(cited)
            assert approved, why


# ==========================================================================
# 2 — the citation channel itself
# ==========================================================================


class TestCitation:
    """A token names an approved command. Nothing about it is fuzzy."""

    def test_a_citation_resolves_to_the_humans_own_text(self, m9_approved):
        for token, verbatim in zip(m9_approved.tokens, m9_approved.verbatim):
            assert m9_approved.expand(f"@{token}") == verbatim

    def test_a_citation_decides_exactly_what_the_text_decides(self, m9_approved):
        """Verdict-preserving over the whole shipped corpus, not over one example.

        Stated as equivalence rather than as "every citation is approved",
        because the approved SET is not the same thing as the RUNNABLE set: a
        command harvested from a scenario file can still be refused on its own
        merits by `command_guard`. A citation must inherit that verdict, neither
        softening nor tightening it.
        """
        assert len(m9_approved.tokens) == len(m9_approved.entries)
        for token, verbatim in zip(m9_approved.tokens, m9_approved.verbatim):
            assert m9_approved.approves(m9_approved.expand(f"@{token}")) == (
                m9_approved.approves(verbatim)
            )

    def test_tokens_are_unique(self, m9_approved):
        """Two approved commands sharing a token would make a citation ambiguous."""
        assert len(set(m9_approved.tokens)) == len(m9_approved.tokens)

    def test_a_token_is_stable_across_the_two_spellings_of_one_command(self):
        """Derived from the matching key, so re-spacing an approved command in a
        scenario file does not silently invalidate a citation of it."""
        spaced = ApprovedCommands(["./probe.sh   seed"])
        tight = ApprovedCommands(["./probe.sh seed"])
        assert spaced.tokens == tight.tokens

    def test_an_argument_tail_survives_the_citation(self, m9_approved):
        token = m9_approved.tokens[0]
        assert m9_approved.expand(f"@{token} --seed 7") == (
            f"{m9_approved.by_token[token]} --seed 7"
        )

    def test_a_citation_of_nothing_is_refused_by_name(self, m9_approved):
        approved, why = m9_approved.approves("@deadbeef --seed 7")
        assert not approved
        assert "deadbeef" in why and "names no approved command" in why

    def test_an_unknown_token_is_not_repaired_into_something_that_runs(self, m9_approved):
        """`expand` leaves it alone so `approves` can refuse it. Failing closed
        matters more here than being helpful."""
        assert m9_approved.expand("@deadbeef --seed 7") == "@deadbeef --seed 7"

    def test_a_citation_cannot_smuggle_shell_composition_through_its_tail(self, m9_approved):
        token = citation_token(APPROVED_STATE)
        approved = ApprovedCommands([APPROVED_STATE])
        ok, why = approved.approves(approved.expand(f"@{token} ; echo pwned"))
        assert not ok
        assert "composition" in why

    def test_a_string_that_is_not_a_citation_is_untouched(self, m9_approved):
        for text in ("", "  ", "./probe.sh seed", "@nothex1 x", "@abc123 x", "user@abcdef12"):
            assert m9_approved.expand(text) == text

    def test_the_citation_prefix_must_stand_alone(self, m9_approved):
        """`@<token>` glued to more characters is not a citation of that token."""
        token = m9_approved.tokens[0]
        assert m9_approved.expand(f"@{token}xyz") == f"@{token}xyz"

    def test_what_runs_is_the_humans_bytes_not_the_models(self):
        """The point of the whole mechanism, stated as its own assertion.

        A generator that cites gets the human's spacing back even when it
        returned a different one, because the approved half is looked up rather
        than parsed. This is the case commit 525702c could only mitigate.
        """
        human = '.venv/bin/python -c "import sys; exec(\'def f():\\n  return 1\\n\')"'
        approved = ApprovedCommands([human])
        token = approved.tokens[0]
        assert approved.expand(f"@{token}") == human


# ==========================================================================
# 3 — a refusal that teaches
# ==========================================================================


class TestNearMissDiagnosis:
    """"Not in the approved set" is true and useless when you were two characters away."""

    def test_a_drifted_copy_is_told_which_command_it_meant(self, m9_approved):
        drifted = [c for c in _refused_commands() if len(c) == 1117]
        assert drifted, "the run's timer proposal is not in these artifacts"
        approved, why = m9_approved.approves(drifted[0])
        assert not approved
        entry, token = m9_approved.nearest(drifted[0])
        assert f"@{token}" in why
        assert "cite it" in why

    def test_a_genuinely_different_command_is_not_reported_as_a_near_miss(self, m9_approved):
        assert m9_approved.nearest("git push --force origin main") is None
        assert m9_approved.nearest("") is None

    def test_a_near_miss_is_still_a_miss(self, m9_approved):
        """The diagnosis phrases the refusal. It must never soften it."""
        drifted = [c for c in _refused_commands() if len(c) == 1117][0]
        approved, _why = m9_approved.approves(drifted)
        assert not approved


# ==========================================================================
# 4 — the planner seam: a citation reaches execution as the human's command
# ==========================================================================


def _planner(tmp_path: Path, payloads: list, **overrides) -> ScenarioPlanner:
    return ScenarioPlanner(
        repo=tmp_path,
        config=ScenarioGenerationConfig(enabled=True, **overrides),
        reasoner=ScriptedReasoner(payloads),
        store=EvidenceStore(tmp_path / "runs", "20260901-015631"),
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )


class TestThePlannerResolvesCitations:
    def test_a_cited_command_compiles_to_the_approved_text(self, tmp_path):
        token = citation_token(APPROVED_STATE)
        payload = raw_payload(
            raw_scenario(
                state_checks=[
                    {"name": "payments", "command": f"@{token}", "contains": ["payments=1"]}
                ],
                expected_observations=["payments=1"],
            )
        )
        planner = _planner(tmp_path, [payload])
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert [s.id for s in plan.scenarios], plan.waves[0].rejected
        scenario = plan.scenarios[0]
        assert scenario.persisted_state_checks[0].command == APPROVED_STATE
        compiled = planner.compiled[scenario.id]
        assert APPROVED_STATE in [
            step.state_check.command
            for step in compiled.steps
            if step.state_check is not None
        ]

    def test_the_wave_records_what_it_resolved(self, tmp_path):
        """A citation means the string that ran is not the string returned. That
        substitution is stated in the plan rather than left to be inferred."""
        token = citation_token(APPROVED_STATE)
        payload = raw_payload(
            raw_scenario(
                state_checks=[
                    {"name": "payments", "command": f"@{token}", "contains": ["payments=1"]}
                ],
                expected_observations=["payments=1"],
            )
        )
        planner = _planner(tmp_path, [payload])
        planner.plan_initial(task="t", unit=FakeUnit())

        resolved = planner.plan.waves[0].resolved_citations
        assert resolved and f"@{token}" in resolved[0] and APPROVED_STATE in resolved[0]

    def test_a_dangling_citation_is_refused_rather_than_run(self, tmp_path):
        payload = raw_payload(
            raw_scenario(
                state_checks=[
                    {"name": "payments", "command": "@deadbeef", "contains": ["payments=1"]}
                ],
                expected_observations=["payments=1"],
            )
        )
        planner = _planner(tmp_path, [payload])
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert plan.scenarios == []
        reasons = " ".join(r for rej in plan.waves[0].rejected for r in rej.reasons)
        assert "deadbeef" in reasons

    def test_a_citation_works_in_setup_cleanup_and_inline_state_checks(self, tmp_path):
        """`rewrite_commands` must reach every field a command can live in — a
        missed one fails closed, but it fails closed by refusing valid coverage."""
        from scenario_fixtures import APPROVED_CLEANUP, APPROVED_SETUP

        setup_token = citation_token(APPROVED_SETUP)
        cleanup_token = citation_token(APPROVED_CLEANUP)
        state_token = citation_token(APPROVED_STATE)
        payload = raw_payload(
            raw_scenario(
                setup=[f"@{setup_token}"],
                cleanup=[f"@{cleanup_token}"],
                actions=[
                    {
                        "kind": "state_check",
                        "name": "inline",
                        "state_check": {
                            "name": "inline",
                            "command": f"@{state_token}",
                            "contains": ["payments=1"],
                        },
                    }
                ],
                state_checks=[
                    {"name": "payments", "command": f"@{state_token}", "contains": ["payments=1"]}
                ],
                expected_observations=["payments=1"],
            )
        )
        planner = _planner(tmp_path, [payload])
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert [s.id for s in plan.scenarios], plan.waves[0].rejected
        scenario = plan.scenarios[0]
        assert scenario.setup == [APPROVED_SETUP]
        assert scenario.cleanup == [APPROVED_CLEANUP]
        assert scenario.actions[0].state_check.command == APPROVED_STATE
        assert scenario.persisted_state_checks[0].command == APPROVED_STATE


# ==========================================================================
# 5 — the brief offers the channel it expects to be used
# ==========================================================================


class TestTheBriefOffersCitation:
    def test_every_rendered_command_carries_its_token(self, tmp_path):
        planner = _planner(tmp_path, [raw_payload()])
        planner.plan_initial(task="t", unit=FakeUnit())
        brief = planner.reasoner.briefs[0].render()

        for command, token in zip(
            planner.approved_commands.verbatim, planner.approved_commands.tokens
        ):
            assert f"[{token}] {command}" in brief

    def test_the_brief_says_how_to_cite(self, tmp_path):
        planner = _planner(tmp_path, [raw_payload()])
        planner.plan_initial(task="t", unit=FakeUnit())
        brief = planner.reasoner.briefs[0].render()
        assert "CITE, DO NOT RETYPE" in brief

    def test_the_rendered_command_is_still_the_humans_text(self, tmp_path):
        """The 525702c property, restated so the token column cannot erode it."""
        planner = _planner(tmp_path, [raw_payload()])
        planner.plan_initial(task="t", unit=FakeUnit())
        brief = planner.reasoner.briefs[0].render()
        for command in planner.approved_commands.verbatim:
            assert command in brief


# ==========================================================================
# 6 — coverage-gap closure, end to end, through the normal path
# ==========================================================================


class TestGapClosureThroughCitation:
    """The shape run 20260901-015631 could not reach: a named gap actually closed."""

    def test_a_gap_wave_that_cites_closes_the_gap_it_was_aimed_at(self, tmp_path):
        first = raw_payload(
            risks=[
                {
                    "id": "R1",
                    "description": "a well-formed request might not persist its owner",
                    "risk_category": "happy_path",
                    "severity": "P0",
                    "basis": "the diff touched the write path",
                }
            ]
        )
        first["scenarios"] = []
        gap_key = IdentifiedRisk(
            description=first["risks"][0]["description"],
            risk_category=RiskCategory.HAPPY_PATH,
        ).key
        token = citation_token(APPROVED_STATE)
        closure = raw_payload(
            raw_scenario(
                "cg-close-happy-path",
                risk_category="happy_path",
                state_checks=[
                    {"name": "payments", "command": f"@{token}", "contains": ["payments=1"]}
                ],
                expected_observations=["payments=1"],
                source_risks=[gap_key],
                actions=[
                    {
                        "kind": "request",
                        "name": "raise",
                        "request": {"method": "POST", "path": "/approve", "expect_status": 200},
                    }
                ],
            ),
            risks=[],
        )
        planner = _planner(tmp_path, [first, closure])
        plan = planner.plan_initial(task="t", unit=FakeUnit())
        assert [r.risk_category for r in plan.planned_gaps()] == [RiskCategory.HAPPY_PATH]

        plan = planner.expand_after_failures(
            task="t", unit=FakeUnit(), failures=[], evaluator_requests=[]
        )

        assert plan.planned_gaps() == []
        closed = plan.by_id("cg-close-happy-path")
        assert closed is not None
        assert closed.persisted_state_checks[0].command == APPROVED_STATE


class TestCitationRefusalIsOnlyEverARefinement:
    """The `@token` refusal must never be the thing that causes a refusal.

    It is placed after every approval path so that an approved command which
    happens to LOOK like a citation — a corpus is allowed to contain one — is
    approved as itself rather than read as a reference to nothing.
    """

    def test_an_approved_command_shaped_like_a_citation_is_still_approved(self):
        approved = ApprovedCommands(["@abcdef12 --seed 7"])
        ok, why = approved.approves("@abcdef12 --seed 7")
        assert ok, why

    def test_an_unknown_token_still_reports_the_harder_problem_first(self):
        approved = ApprovedCommands([APPROVED_STATE])
        ok, why = approved.approves("@deadbeef ; rm -rf /")
        assert not ok
        assert "hard-blocked" in why

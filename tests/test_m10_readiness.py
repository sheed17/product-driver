"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M10?

M10 is the Compensation: undoing an external effect that should not have happened. It is the one
machine in Neyma whose whole job is to prove that an undo gets **no privileged path** — because the
compensation is ITSELF a separately gated external effect, and an "undo" that bypasses the gates is
an ungated write with a good excuse.

The unit's whole character is four sentences, and every check below traces back to one of them:

    a compensation is the undoing of an external effect that should not have happened
    the compensation is ITSELF a separately gated external effect — it receives NO privileged path
    you cannot undo what you cannot prove you did
    an "undo" that bypasses the gates is an ungated write with a good excuse

M10 differs from every P6 machine before it in one way that changes the risk profile completely.
M1–M9 all ship dark and touch nothing outside the database. **M10's normal operation is an external
effect that moves money backwards.** So the failure modes are not "an obligation got forgotten" —
they are "a correction was written into a customer's accounting system by a path nobody approved",
and "the system compensated an effect it could not prove had happened, and created it".

The single most likely way this unit gets built wrong is that someone looks at the compensation
path, sees a pipeline they already own, and takes the short way: reuse the original Effect Grant,
negate the original commit key, call the adapter's void endpoint directly, skip the checkpoint
because "we are only undoing something we did". Each of those is a second write route into a
customer's TMS, built by the people who most believe they are being careful.

Thirteen questions, each answered mechanically rather than by reading a document and agreeing
with it:

1.  does the M10 base scenario parse, does it hold the pieces the generator needs (deterministic
    operation, a closed mutation axis with the two axes this unit turns on, persisted-state oracles,
    regression anchors), and do the scenario and the task state the SAME contract;
2.  does every declared risk name a command that could actually emit the observation it requires;
3.  does the scenario measure the DATABASE and the EVENT REGISTRY rather than the probe's narration
    for the invariants a green test suite can state while nothing enforces them — above all the
    six-state CHECK, the EXECUTING-requires-a-pipeline CHECK, and the canonical uniqueness predicate
    — and does it ATTEMPT the forbidden writes against a live database with positive controls rather
    than reading the DDL and believing it;
4.  does the task preserve the thirteen recorded authority conflicts rather than resolving them;
5.  does the task get the SEAMS right — M10's `decision_ref` resolver belongs to M1, its commit key
    to `commit_key.py`'s already-landed canonical occurrence, its approval to M4, its pipeline to M2,
    its gate to the checkpoint, its brake to P3, and its escalation to an M9 it must NOT wire;
6.  is the M10 command vocabulary safe, and actually visible to the generator;
7.  can dynamic generation close an M10 coverage gap WITHOUT inventing a command;
8.  is `P6-D46` still closed — canonical taxonomy only;
9.  is M10 scoped as `P6/M10` rather than as P6 phase completion, and can accepting it score a P6
    acceptance criterion or unlock P7 (it cannot);
10. is an integrated independent review OWED, at the tier this unit actually is;
11. do grounded reviewer findings return to the SAME builder, does a corrected tree get a FRESH
    reviewer bound to its exact fingerprint, and does the run stop before M11;
12. does the founder summary explain M10's product impact in simple terms — and never imply that a
    compensation path is live;
13. and, for the load-bearing half of all of the above: **does this file actually fail when the
    assertion it rests on is removed?** A readiness test never seen to fail is a decoration, so the
    last section mutates the shipped scenario and proves each guard turns red.

Every Claude session is faked. No test here consumes Claude usage, executes the product, or touches
the real Neyma repository.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.models import RunStatus
from neyma_product_driver.review_cycle import resolve_review_requirement
from neyma_product_driver.run_journal import RunJournal
from neyma_product_driver.scenario_plan import (
    RISK_CATEGORY_VALUES,
    GeneratedScenario,
    GeneratedStateCheck,
    IdentifiedRisk,
    Priority,
    RiskCategory,
    ScenarioProvenance,
)
from neyma_product_driver.scenario_planner import STAGE_COVERAGE_GAP, ScenarioPlanner
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    ValidationContext,
    validate_plan,
)
from neyma_product_driver.scenarios import load_scenario
from neyma_product_driver.task_scope import (
    ScopeLevel,
    TaskResult,
    scoped_completion,
    standard_exclusions,
)

from scenario_fixtures import (
    FakeFounder,
    ScriptedReasoner,
    raw_scenario,
    recorded_contract_probe,
)
from test_integrated_review import FakeBuilder, FakeReviewer, drive, refusing, supported
from test_scoped_completion import PhaseRepo

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DRIVER_ROOT / "scenarios"
M10_PATH = SCENARIOS_DIR / "p6_m10_compensation.yaml"
M10_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m10.md"
M10_TASK = M10_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match against this: the task is
#: a wrapped markdown document, and a phrase that happens to straddle a line break is not a phrase
#: the task failed to state.
M10_TASK_FLAT = " ".join(M10_TASK.split())
#: The same text with markdown furniture removed — blockquote markers, `###` emphasis runs and bold
#: markers. The task states its hardest rules inside emphasised blockquotes, and a rule that happens
#: to straddle a `>` continuation line is not a rule the task failed to state.
M10_TASK_PROSE = " ".join(
    re.sub(r"(^|\n)\s*>\s?", " ", M10_TASK).replace("###", " ").replace("**", "").split()
)
PROBE = ".venv/bin/python scripts/probe_phase6_compensation.py"
#: The `name:` the base scenario gives the bare probe run — the deterministic basic M10 operation,
#: and the only check in the file that drives the machine and narrates what it saw.
PROBE_CHECK = "drive the Compensation machine through a brokerage narrative, and attack it"

#: The canonical M10 deliverables. A different name is a scenario failure, not a style preference.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/compensation.py",
    "src/freight_recon/migrations/phase6_compensations.py",
    "eval/tests/test_phase6_compensation.py",
    "scripts/probe_phase6_compensation.py",
    "scripts/mutate_phase6_compensation.py",
)

#: The six canonical compensation states (registry §4 / M10, entity §12, target spec §12.10).
#: Not five, not seven.
STATES: tuple[str, ...] = (
    "REQUIRED", "APPROVED", "EXECUTING", "COMPLETED", "COMPENSATION_FAILED", "NOT_POSSIBLE",
)

#: Terminal, per machine §8. `COMPLETED` and nothing else.
TERMINAL_STATES: tuple[str, ...] = ("COMPLETED",)
#: Machine §9. These three are non-terminal AND human-owned — which is what makes an unowned or
#: auto-cleared one a lost obligation over real money.
HUMAN_OWNED_STATES: tuple[str, ...] = ("REQUIRED", "COMPENSATION_FAILED", "NOT_POSSIBLE")
#: Machine §10.
RECOVERABLE_STATES: tuple[str, ...] = ("APPROVED", "EXECUTING")

#: States a build session might reach for, and that the corpus says do not exist. `CANCELLED` is
#: first because entity §25 says cancellation is N/A once `REQUIRED` — the exposure exists, and a
#: compensation you cancel is money you decided to stop tracking. `RETRYING` is second because
#: machine §20 is explicit that a failed compensation is NOT auto-retried. `RESOLVED` is M9's
#: REGISTERED state name and registry's binding header forbids a local synonym.
FORBIDDEN_STATES: tuple[str, ...] = (
    "CANCELLED",
    "EXPIRED",
    "TIMED_OUT",
    "ROLLED_BACK",
    "RETRYING",
    "RESOLVED",
    "SUPERSEDED",
    "REVERSED",
    "UNDONE",
    "PENDING",
    "FAILED",
    "ABANDONED",
)

#: The canonical transition ids. `AC-MACH-1001..1009` — nine, not eight.
TRANSITIONS: tuple[str, ...] = (
    "CM-1", "CM-1r", "CM-2", "CM-2n", "CM-3", "CM-4", "CM-4f", "CM-5", "CM-5x",
)

#: The seven registered F10 event contracts. `event_contracts_data.json` carries exactly these
#: seven, and `events/registry.md` is by its own header THE SOLE CANONICAL LIST — so an eighth
#: `Compensation*` name is defective by the registry's own definition.
F10_EVENTS: tuple[str, ...] = (
    "CompensationRequired",
    "CompensationRefused",
    "CompensationApproved",
    "CompensationImpossible",
    "CompensationStarted",
    "CompensationCompleted",
    "CompensationFailed",
)

#: Names a build session invents when it wants a compensation to stop being an obligation, or when
#: it wants a prose "events consumed" list to be literally true.
FORBIDDEN_EVENTS: tuple[str, ...] = (
    "CompensationCancelled",
    "CompensationExpired",
    "CompensationRetried",
    "CompensationReversed",
    "CompensationSucceeded",
    "CompensationAbandoned",
    "CorrectionInvalidatedAnEffect",
    "NoCompensatingActionExists",
)

#: The six literals that say M10 stopped where it was told to stop, and that no landed unit was
#: edited to get there. They are M10's own narration: `tasks/neyma_p6_m10.md` states them verbatim
#: to the builder as strings the M10 PROBE must print, and the probe is the only command in this
#: scenario that runs the machine and narrates what it found.
DARK_POSTURE_LITERALS: tuple[str, ...] = (
    "THE M1 WORK ITEM MACHINE IS UNCHANGED",
    "THE M2 PIPELINE MACHINE IS UNCHANGED",
    "THE M3 EFFECT AUTHORITY IS UNCHANGED",
    "THE M4 APPROVAL MACHINE IS UNCHANGED",
    "THE M9 EXCEPTION MACHINE IS UNCHANGED",
    "THE M11, M12 AND M13 MACHINES ARE NOT BUILT",
)

#: The safety sentences the probe must print. Each one is a whole requirement compressed into a
#: line a founder can read, and each is emitted by the case that establishes it.
SAFETY_LITERALS: tuple[str, ...] = (
    "A COMPENSATION IS ITSELF A SEPARATELY GATED EXTERNAL EFFECT",
    "THERE IS NO FAST PATH FOR UNDO",
    "YOU CANNOT UNDO WHAT YOU CANNOT PROVE YOU DID",
    "COMPENSATION IS FORBIDDEN ON AN UNKNOWN OUTCOME",
    "A COMPENSATING EFFECT NEVER REUSES THE ORIGINAL EFFECT GRANT",
    "THE COMPENSATING EFFECT HAS ITS OWN COMMIT KEY",
    "COMPLETION REQUIRES READBACK, NOT AN ADAPTER RETURN CODE",
    "A TIMEOUT IS NOT A FAILURE",
    "COMPENSATION_FAILED NEVER AUTO-RESOLVES",
    "NO TIMER MOVES COMPENSATION_FAILED",
    "THERE IS NO AUTOMATIC RETRY OF A FAILED COMPENSATION",
    "NOT_POSSIBLE IS HONEST AND KEEPS ITS EXPOSURE",
    "THERE IS NO BULK UNDO",
    "A COMPENSATION IS BLOCKED UNDER AN ACTIVE BRAKE",
    "A MODEL CAN NEVER APPROVE A COMPENSATION",
    "A MODEL CAN NEVER OWN A COMPENSATION",
    "A MODEL CAN NEVER ESTABLISH REALITY",
    "M10 MINTS NO GATE DECISION",
    "M10 ENGAGES NO BRAKE AND NARROWS NONE",
    "M10 MINTS NO SECOND RealityEstablished CONTRACT",
    "REPLAY PRODUCES NO COMPENSATING EFFECT",
)

#: The thirteen recorded authority conflicts. Each is a place where a build session, acting
#: reasonably, would settle a specification question by accident. The task must PRESERVE them.
AUTHORITY_QUESTIONS: tuple[str, ...] = tuple(f"M10-AQ-{n}" for n in range(1, 14))

#: Nine `risk_category` values in the shape `P6-D46`'s real nine had: each a plausible, well-meant
#: DESCRIPTION OF A SPECIFIC DEFECT rather than a member of a closed family vocabulary — which is
#: what an unconstrained `{"type": "string"}` schema invites a model to write. These are M10's.
M10_UNREADABLE_CATEGORIES: tuple[str, ...] = (
    "privileged-undo-path",
    "compensated-an-unknown-outcome",
    "reused-the-original-grant",
    "bulk-undo",
    "completed-without-readback",
    "timer-cleared-a-failed-compensation",
    "exposure-lost-on-failure",
    "second-reality-established-contract",
    "model-approved-the-reversal",
)


def _local_vocabulary() -> list[str]:
    """The `--case` entries the local driver config approves, if it exists.

    Read from the file rather than through `load_config`, because this must work on a checkout that
    has no `driver.config.yaml` at all — the vocabulary is then simply absent and the tests that
    need it skip.
    """
    local = DRIVER_ROOT / "driver.config.yaml"
    if not local.exists():
        return []
    raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    return list((raw.get("scenario_generation") or {}).get("approved_commands") or [])


@pytest.fixture(scope="module")
def m10():
    return load_scenario(M10_PATH)


@pytest.fixture(scope="module")
def cases(m10) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m10.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m10) -> list[str]:
    listing = [c for c in m10.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def state_checks(m10) -> dict[str, list[str]]:
    return {check.name: list(check.contains) for check in m10.expect_state}


# --------------------------------------------------------------------------
# 1. The M10 base scenario holds what the generator and the gate need
# --------------------------------------------------------------------------


class TestTheM10BaseScenario:
    def test_it_parses_and_is_scoped_to_p6(self, m10):
        assert m10.name == "p6_m10_compensation"
        assert m10.phase == "P6"
        assert m10.mode == "backend"

    def test_it_names_the_canonical_deliverables_as_fixtures(self, m10):
        """A scenario that does not name the files cannot notice they were never written, and a
        differently-named module is a seam nothing here would find."""
        for path in DELIVERABLES:
            assert path in m10.fixtures, f"the scenario does not require {path}"

    def test_it_drives_the_machine_deterministically(self, m10):
        """One command runs the machine and narrates it. Everything else measures."""
        assert PROBE_CHECK in m10.check_names()
        probe = [c for c in m10.commands if c.name == PROBE_CHECK][0]
        assert probe.run == f"{PROBE} --all"
        assert "behaviours as specified, 0 wrong" in probe.expect_contains

    def test_it_declares_the_closed_mutation_axis_this_unit_turns_on(self, dimensions):
        """`--original-state` and `--exposure` are M10's own two axes, the way `--actor` and
        `--decision-ref` were M9's and `--coverage` was M8's.

        `--original-state` varies the M3 state of the effect being compensated across the
        eight-member ledger — the axis CM-1 and CM-1r turn on, and where `M10-AQ-10` lives.
        `--exposure` varies the money shape, including the values that must be refused.
        """
        for axis in ("--concurrency", "--delay-ms", "--repeat", "--tenants", "--seed", "--inject"):
            assert axis in dimensions, f"the shared axis {axis} is not offered"
        assert "--original-state" in dimensions, (
            "the axis CM-1 and CM-1r turn on is not offered, so no generated case can vary the "
            "state of the effect being compensated — which is the whole eligibility question"
        )
        assert "--exposure" in dimensions, "the money axis is not offered"
        assert "--brake" in dimensions, (
            "the brake axis is not offered, so no generated case can prove a compensation is "
            "blocked under an active brake"
        )

    def test_it_carries_the_mutation_battery_and_the_acceptance_battery(self, m10):
        runs = [c.run for c in m10.commands]
        assert any("mutate_phase6_compensation.py" in r for r in runs), (
            "no mutation battery runs; nothing proves the acceptance battery can fail"
        )
        assert any("test_phase6_compensation.py" in r for r in runs)

    def test_it_carries_the_neighbouring_regression_anchors(self, m10):
        """M10 consumes M2, M3 and M4 and escalates to M9. If any of them moved, that is M10's
        problem to notice, not the next unit's."""
        runs = " ".join(c.run for c in m10.commands)
        for neighbour in (
            "test_phase6_work_item.py",
            "test_phase6_pipeline_instance.py",
            "test_phase6_external_effect.py",
            "test_phase6_approval.py",
            "test_phase6_exception.py",
            # THE CANONICAL CHECKPOINT SUITE, BY ITS REAL NAME. This read
            # "test_phase3_checkpoint.py" — a file that has never existed in the
            # landed tree — and run 20260901-082602 spent an iteration on the
            # exit-4 that produced. The suffix matters: `test_phase3_checkpoint.py`
            # is a substring of `test_phase3_checkpoint_matrix.py`, so the loose
            # spelling could not tell the real anchor from the phantom one.
            "test_phase3_checkpoint_matrix.py",
            "test_phase3_claim_cas.py",
        ):
            assert neighbour in runs, f"{neighbour} is not run beside M10"

    def test_no_battery_can_be_satisfied_by_a_suite_that_never_ran(self, m10):
        """pytest exits 4 on a path that is not there and 5 on an empty
        selection, and prints a summary line in neither case. Reading the exit
        code alone lets an anchor that collected nothing stand in for a green
        one — which is what the phantom `test_phase3_checkpoint.py` did for as
        long as it was written down. Every battery requires the summary word,
        and the scenario forbids pytest's own three ways of saying it ran
        nothing, anywhere the run looked."""
        batteries = [c for c in m10.commands if "-m pytest" in c.run]
        assert len(batteries) >= 5, "the acceptance batteries are not being swept"
        for spec in batteries:
            assert spec.expect_exit_code == 0, f"{spec.name!r} does not require exit 0"
            assert any("passed" in n for n in spec.expect_contains), (
                f"{spec.name!r} reads only the exit code"
            )
        for marker in (
            "ERROR: file or directory not found",
            "no tests ran",
            "ModuleNotFoundError: No module named 'eval'",
        ):
            assert marker in m10.forbidden, f"the scenario does not forbid {marker!r}"

    def test_every_battery_is_entered_through_the_interpreter(self, m10):
        """`python -m pytest`, never the console script. The console script
        leaves the invocation directory off `sys.path`, and Neyma's own
        `pythonpath` is `["src"]` alone, so a test importing the repository's
        harness (`from eval.phase0 import import_probe`) fails under one and
        passes under the other. That difference cost run 20260901-082602 twelve
        phantom M2 failures and an ordering hypothesis that was never true —
        `pytest-randomly` is not installed in that repository at all.

        The general form of this rule, swept over every scenario in the corpus
        and proven from the interpreter's behaviour, is
        `tests/test_scenario_pytest_invocation.py`."""
        for spec in m10.commands:
            if "pytest" not in spec.run:
                continue
            assert "-m pytest" in spec.run, (
                f"{spec.name!r} runs the pytest console script: {spec.run[:120]}"
            )

    def test_it_runs_the_false_green_guard_that_m9s_build_tripped(self, m10):
        """CI printed a real `F` on the M9 build for
        `test_every_corpus_scanning_negative_assertion_proves_its_population`, and the guard was
        right: four corpus-scanning negative assertions proved no population. M10 adds more
        corpus-scanning assertions than M9 did, so it runs the guard rather than hoping."""
        runs = " ".join(c.run for c in m10.commands)
        assert "test_false_green_defenses.py" in runs

    def test_the_scenario_and_the_task_state_the_same_six_states(self, m10):
        """Two documents, one contract. A scenario asserting five states while the task asks for
        six is a run that verifies the wrong machine and reports green."""
        for state in STATES:
            assert state in M10_TASK, f"the task never names the canonical state {state}"
        vocab_check = [c for c in m10.expect_state if "canonical six" in " ".join(c.contains)]
        assert vocab_check, "no check asserts the canonical six-state vocabulary"

    def test_the_scenario_and_the_task_state_the_same_nine_transitions(self, m10):
        for tid in TRANSITIONS:
            assert tid in M10_TASK, f"the task never names transition {tid}"
        contains = " ".join(" ".join(c.contains) for c in m10.expect_state)
        assert "transition rows in the specification: 9" in contains
        assert "exact set match: True" in contains

    def test_the_scenario_and_the_task_state_the_same_seven_events(self, m10):
        for event in F10_EVENTS:
            assert event in M10_TASK, f"the task never names F10 contract {event}"
        contains = " ".join(" ".join(c.contains) for c in m10.expect_state)
        assert "F10 member count: 7" in contains

    def test_the_forbidden_markers_cover_the_ways_this_unit_gets_built_wrong(self, m10):
        """The markers are the probe's own alarm vocabulary. Each names a specific defect that
        would otherwise pass as a green run."""
        forbidden = " ".join(m10.forbidden)
        for marker in (
            "PRIVILEGED UNDO PATH",
            "CHECKPOINT BYPASSED",
            "ORIGINAL EFFECT GRANT REUSED",
            "COMMIT KEY DERIVED FROM THE ORIGINAL",
            "DIRECT ADAPTER INVOCATION",
            "COMPLETED WITHOUT READBACK",
            "TIMER MOVED COMPENSATION_FAILED",
            "AUTOMATIC RETRY FROM COMPENSATION_FAILED",
            "BULK UNDO",
            "SECOND RealityEstablished CONTRACT MINTED",
            "UNKNOWN OUTCOME COMPENSATED",
            "MODEL APPROVED A COMPENSATION",
            "REPLAY PRODUCED AN EXTERNAL EFFECT",
        ):
            assert marker in forbidden, f"no forbidden marker covers {marker!r}"

    def test_a_crash_is_a_failure_not_a_pass(self, m10):
        for marker in ("Traceback (most recent call last)", "no such table", "no such column"):
            assert marker in m10.forbidden


# --------------------------------------------------------------------------
# 2. Every declared risk is mapped to a command that can actually prove it
# --------------------------------------------------------------------------


def declared_producers(scenario) -> dict[str, set[str]]:
    """Which named checks DECLARE each literal, statically.

    The only thing a scenario file knows about what a check emits is what the check itself declares:
    a command's `expect_contains` and a state check's `contains`. Free-form probe narration has no
    declared producer and is deliberately outside this map.
    """
    declared: dict[str, set[str]] = {}
    for spec in scenario.commands:
        if spec.name:
            for literal in spec.expect_contains:
                declared.setdefault(literal, set()).add(spec.name)
    for check in scenario.expect_state:
        if check.name:
            for literal in check.contains:
                declared.setdefault(literal, set()).add(check.name)
    return declared


def claims_needing_the_probe(scenario) -> list[tuple[str, list[str]]]:
    """Claims that require a DARK-POSTURE literal but do NOT name the probe that emits it.

    Scoped to the six dark-posture sentences deliberately: those are narrated from inside M10's own
    story and no other check in this scenario can produce them. The safety sentences each also have
    a dedicated `--case` command that declares them, so a claim naming one of those is correctly
    attributed and the load-time validator already proves it.

    Returned rather than asserted so the same predicate can be run against a deliberately broken
    copy of the scenario in section 13 — a guard never seen to fail is a decoration.
    """
    narrated = set(DARK_POSTURE_LITERALS)
    broken: list[tuple[str, list[str]]] = []
    for claim in scenario.verifies:
        needed = [lit for lit in claim.observations if lit in narrated]
        if needed and PROBE_CHECK not in claim.checks:
            broken.append((claim.risk_category, needed))
    return broken


def unattributable_claims(scenario) -> list[tuple[str, str, list[str]]]:
    """Claims requiring a literal that some OTHER check declares, while naming none of them."""
    declared = declared_producers(scenario)
    broken: list[tuple[str, str, list[str]]] = []
    for claim in scenario.verifies:
        if not claim.checks:
            continue
        for literal in claim.observations:
            producers = declared.get(literal)
            if producers and not (producers & set(claim.checks)):
                broken.append((claim.risk_category, literal, sorted(producers)))
    return broken


class TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem:
    """A `verifies:` claim is resolved by matching its `observations:` against the output of the
    checks it NAMES, and nothing else. So a claim that names commands which cannot emit the literal
    it requires is unfalsifiable in the wrong direction: it fails closed forever, on every run, no
    matter how correct the product is — and it fails wearing the costume of a product defect.

    That is exactly what blocked the M6 run `20260825-204229`, and the same shape blocked two
    generated cases on the M7 run. `Scenario._claims_name_a_check_that_can_emit_them` refuses the
    statically decidable half at load time. The residue is free-form narration, which nothing can
    attribute by reading YAML, so it is pinned here.
    """

    def test_every_claim_requiring_a_dark_posture_literal_names_the_probe(self, m10):
        assert claims_needing_the_probe(m10) == []

    def test_a_claim_may_not_require_an_observation_its_checks_cannot_declare(self, m10):
        """The general half, enforced at load time — asserted here against the real M10 file so the
        shipped scenario is covered by the invariant and not merely by the unit test of it."""
        assert unattributable_claims(m10) == []

    def test_every_declared_risk_names_at_least_one_check_and_one_observation(self, m10):
        """A claim with an oracle on only one side is half a claim.

        `RiskClaim` requires one of the two. This file requires both for M10, because a claim with
        no named check matches its literals against EVERYTHING the run observed — which for a
        scenario that runs six pytest anchors and a full probe is a very large haystack, and an
        accidental match in it is coverage nobody established.
        """
        for claim in m10.verifies:
            assert claim.checks, f"the {claim.risk_category!r} claim names no check"
            assert claim.observations, f"the {claim.risk_category!r} claim names no observation"

    def test_the_dark_posture_literals_are_still_required_somewhere(self, m10):
        """The way to make an attribution gap go away is to stop asking. This refuses that."""
        for literal in DARK_POSTURE_LITERALS:
            assert literal in m10.expect_visible, (
                f"{literal!r} is no longer an expected observation of the M10 scenario"
            )
            assert any(literal in claim.observations for claim in m10.verifies), (
                f"{literal!r} is expected but no declared risk rests on it any more"
            )

    def test_the_safety_invariant_claim_is_the_one_this_unit_turns_on(self, m10):
        """M10's whole safety property is "the undo goes through the gates", so the
        `safety_invariant` claim carries the weight `authorization` carried for M9. It must require
        the no-fast-path sentences AND the measured gate-minter fact — because a claim that rested
        only on the probe's sentences would be satisfied by a machine that printed them."""
        claim = [c for c in m10.verifies if c.risk_category == "safety_invariant"][0]
        for literal in (
            "A COMPENSATION IS ITSELF A SEPARATELY GATED EXTERNAL EFFECT",
            "THERE IS NO FAST PATH FOR UNDO",
            "A COMPENSATING EFFECT NEVER REUSES THE ORIGINAL EFFECT GRANT",
            "THERE IS NO BULK UNDO",
        ):
            assert literal in claim.observations, (
                f"the safety_invariant claim no longer requires {literal!r}. The no-privileged-path "
                "proof is not optional: removing it is how this defect gets 'fixed' by weakening "
                "the oracle"
            )
        assert "modules that MINT a gate decision: ['checkpoint.py']" in claim.observations, (
            "the safety_invariant claim rests only on narration. A machine that printed the "
            "sentences and minted its own gate would satisfy it"
        )
        assert PROBE_CHECK in claim.checks

    def test_the_m33_claim_rests_on_the_refusal_being_complete(self, m10):
        """M-33 is not "we did not compensate". It is "we wrote nothing at all" — zero rows, zero
        pipelines, zero grants, zero effects."""
        claim = [c for c in m10.verifies if c.risk_category == "ambiguous_external_effect"][0]
        assert "YOU CANNOT UNDO WHAT YOU CANNOT PROVE YOU DID" in claim.observations
        assert "COMPENSATION IS FORBIDDEN ON AN UNKNOWN OUTCOME" in claim.observations
        assert any("mints no pipeline, no grant and no effect" in c for c in claim.checks), (
            "the M-33 claim never names the check that proves the refusal wrote nothing"
        )

    def test_the_boundary_claim_measures_the_uniqueness_question_rather_than_asserting_it(
        self, m10
    ):
        """`M10-AQ-9` is a real, surprising consequence of the canonical predicate. The scenario
        MEASURES it against a live database instead of claiming it in prose — and instead of
        quietly "improving" the index to make it go away."""
        claim = [c for c in m10.verifies if c.risk_category == "boundary"][0]
        assert "a SECOND active compensation for the same invalidated effect: refused" \
            in claim.observations
        assert "a second compensation while the first is NOT_POSSIBLE (M10-AQ-9): ACCEPTED" \
            in claim.observations, (
                "the scenario no longer measures M10-AQ-9. Dropping it is how the authority "
                "question gets resolved by accident"
            )

    def test_the_retry_safety_claim_requires_the_stickiness_literals(self, m10):
        """Machine §20 is explicit: a failed compensation is NOT auto-retried, a human decides. And
        entity §26 says it never expires. Those are the two sentences a build session deletes when
        a queue of `COMPENSATION_FAILED` rows starts looking untidy."""
        claim = [c for c in m10.verifies if c.risk_category == "retry_safety"][0]
        for literal in (
            "COMPENSATION_FAILED NEVER AUTO-RESOLVES",
            "THERE IS NO AUTOMATIC RETRY OF A FAILED COMPENSATION",
        ):
            assert literal in claim.observations, (
                f"the retry_safety claim no longer requires {literal!r}"
            )
        assert "a BEFORE DELETE guard exists: True" in claim.observations, (
            "the retry_safety claim rests only on narration; nothing proves the row cannot simply "
            "be deleted, which is the tidiest way of all to make an exposure stop existing"
        )
        assert "invented sweep, reaper, auto-close or auto-retry surfaces: []" in claim.observations

    def test_the_lifecycle_claim_requires_the_timer_refusal(self, m10):
        """`CM-5x` is the one row in §14 that declares an ILLEGAL outcome. A timer that could move
        `COMPENSATION_FAILED` would clear the loudest state in the system on a schedule."""
        claim = [c for c in m10.verifies if c.risk_category == "unexpected_state_transition"][0]
        assert "NO TIMER MOVES COMPENSATION_FAILED" in claim.observations
        assert "M10 schedules a timer: False" in claim.observations, (
            "nothing proves M10 arms no timer at all, which is the general form of CM-5x"
        )
        assert "TimerFired is modelled as a trigger with no legal row: True" in claim.observations

    def test_every_declared_category_is_one_m10_actually_exhibits(self, m10):
        """Seventeen families, each derived from M10's own behaviour.

        Three of them — `approval_required`, `ambiguous_external_effect` and `timeout_after_effect`
        — are families M9 deliberately did NOT declare, because M9 consumes no approval and touches
        the outside world not at all. M10 does both, and that difference is the point of the unit.
        """
        assert m10.declared_risk_categories() == {
            "safety_invariant",
            "ambiguous_external_effect",
            "approval_required",
            "authorization",
            "missing_data",
            "malformed_input",
            "unexpected_state_transition",
            "retry_safety",
            "timeout_after_effect",
            "idempotency",
            "boundary",
            "concurrency",
            "cross_tenant",
            "persistence_failure",
            "restart_recovery",
            "stale_state",
            "regression",
        }

    def test_the_families_m10_owns_that_m9_could_not_are_declared(self, m10):
        declared = m10.declared_risk_categories()
        for owned in ("approval_required", "ambiguous_external_effect", "timeout_after_effect"):
            assert owned in declared, (
                f"M10 does not declare {owned!r}. Unlike every P6 machine before it, M10 consumes "
                "an M4 approval and produces an external effect, so this is its own risk and not "
                "another unit's"
            )


# --------------------------------------------------------------------------
# 3. The database and the registry are the oracle, not the probe's narration
# --------------------------------------------------------------------------


class TestPersistedStateIsTheOracle:
    """The sentences a green test suite can state while nothing enforces them.

    "there are six states", "EXECUTING requires a bound pipeline", "one active compensation per
    invalidated effect" and "M10 mints no eighth event" are each a property of the SCHEMA or of the
    EVENT REGISTRY. A probe that prints them proves it printed them.
    """

    def test_the_six_states_are_asserted_as_a_database_constraint(self, state_checks):
        name = "the six canonical compensation states are a database constraint, and there is no seventh"
        assert name in state_checks, "no check reads the state vocabulary out of the live schema"
        contains = state_checks[name]
        assert "the state vocabulary is a CHECK: True" in contains, (
            "the state set is not asserted to be a CHECK. A Python enum is a convention; a CHECK is "
            "a constraint, and only one of them stops a raw INSERT"
        )
        assert "state count: 6" in contains, "the six-state count is not asserted"
        assert "forbidden states present: []" in contains, (
            "nothing refuses a seventh state. CANCELLED and RETRYING are the two a build session "
            "reaches for, and entity §25 and machine §20 forbid both by name"
        )
        assert "an expiry column: []" in contains, (
            "nothing refuses an expiry column, and entity §26 says a compensation NEVER expires"
        )

    def test_the_forbidden_writes_are_attempted_against_a_live_database(self, m10, state_checks):
        """Reading the DDL and believing it is not the same as issuing the write and being refused.

        This check ISSUES each forbidden insert against a freshly created canonical database and
        records what the database said — with TWO positive controls, so no "refused" line can be
        true of a table that refuses everything.
        """
        name = ("the live database refuses an ownerless compensation, a seventh state, and "
                "EXECUTING with no pipeline")
        assert name in state_checks, "no check attempts the forbidden writes for real"
        contains = state_checks[name]
        assert "positive control, a well-formed REQUIRED compensation: ACCEPTED" in contains, (
            "there is no positive control. Without one, every 'refused' line below is also true of "
            "a table that refuses every insert, including the correct ones"
        )
        assert ("second positive control, a NOT_POSSIBLE compensation keeping its exposure: "
                "ACCEPTED") in contains, (
            "there is no second positive control on the human-owned state that keeps the exposure"
        )
        assert "rows that survived: 2" in contains, (
            "the surviving-row count is not asserted, so a run in which the positive controls were "
            "silently rolled back would still read as green"
        )
        for violation in (
            "an ownerless compensation: refused",
            "an owner who is not a recorded human: refused",
            "an owner from another tenant: refused",
            "an original effect from another tenant: refused",
            "an original effect no row backs: refused",
            "a CANCELLED lifecycle state: refused",
            "an EXPIRED lifecycle state: refused",
            "a RETRYING lifecycle state: refused",
            "EXECUTING with no bound pipeline: refused",
        ):
            assert violation in contains, (
                f"the live database is never asked to refuse {violation.split(':')[0]!r}"
            )

    def test_the_uniqueness_predicate_is_read_out_of_the_live_index(self, state_checks):
        """Entity §17 gives the predicate verbatim. It is surprising, and it must be built as
        written rather than improved — so the scenario reads the real index and then MEASURES the
        consequence."""
        name = ("the unique-active-compensation index is tenant-first and carries the canonical "
                "NOT_POSSIBLE predicate")
        assert name in state_checks, "no check reads the uniqueness index out of the live schema"
        contains = state_checks[name]
        for literal in (
            "a UNIQUE index exists: True",
            "every compensation index is tenant-first: True",
            "the active predicate names NOT_POSSIBLE: True",
            "the active predicate is an exclusion, not an inclusion: True",
            "the uniqueness columns are tenant and the original effect: True",
        ):
            assert literal in contains, f"the index shape is not asserted: {literal!r}"
        assert "a second compensation while the first is NOT_POSSIBLE (M10-AQ-9): ACCEPTED" \
            in contains, (
                "the M10-AQ-9 consequence is not measured. Reporting an authority question the "
                "scenario cannot demonstrate is a claim, not a finding"
            )

    def test_the_event_vocabulary_is_derived_structurally_not_from_comments(self, m10, state_checks):
        """A comment saying *"CompensationCancelled is deliberately NOT minted"* must not trip a
        behavioural oracle, and a docstring naming a forbidden event must not either — while a real
        string literal in code must.

        So the check walks the AST, excludes module/class/function docstrings, and never sees
        comments at all (they are not in the AST). It also reads the event names the nine
        transitions actually DECLARE at runtime, which is stronger than any text scan.
        """
        name = ("M10 uses the seven registered F10 contracts, invents no eighth, and mints no "
                "second RealityEstablished")
        assert name in state_checks, "no check derives M10's event vocabulary"
        contains = state_checks[name]
        assert "F10 member count: 7" in contains
        assert "unregistered Compensation-shaped event names in M10 code: []" in contains, (
            "nothing refuses an eighth Compensation* event name"
        )
        assert "every declared event name is registered: True" in contains, (
            "the event names the transitions declare are never checked against the registry, so a "
            "row could name a contract that does not exist"
        )
        assert ("the declared set is the seven F10 contracts plus the shared RealityEstablished: "
                "True") in contains, (
            "the declared event set is not pinned, so a transition could quietly stop emitting one"
        )
        for literal in (
            "RealityEstablished contracts in the registry: 1",
            "RealityEstablished family: F3",
            "RealityEstablished producers: ['CM-5', 'EF-5']",
            "RealityEstablished subject enum: ['compensation', 'effect']",
        ):
            assert literal in contains, (
                f"{literal!r} is not asserted. RealityEstablished is ONE contract with TWO "
                "structurally-identical producers (registry §9), and minting a second under F10 is "
                "the second authority CLAUDE.md §5 rule 17 forbids"
            )

    def test_the_event_oracle_reads_the_ast_rather_than_the_file_text(self, m10):
        """A comment is not behaviour, and neither is a docstring.

        `tasks/neyma_p6_m10.md` tells the builder that `CorrectionInvalidatedAnEffect` must not be
        minted — so a conscientious module will very likely carry a comment saying exactly that.
        A `grep`-shaped oracle would then fire on the comment and report a defect that is not
        there; and, worse, the obvious "fix" is to delete the comment rather than to notice the
        oracle is wrong. So the check parses the module, excludes module/class/function docstrings
        explicitly, and never sees comments at all — they are not in the AST.
        """
        check = [c for c in m10.expect_state if "invents no eighth" in c.name][0]
        assert "ast.parse" in check.command, (
            "the event-vocabulary oracle does not parse the module; a text scan would fire on a "
            "comment that says the forbidden name is deliberately not minted"
        )
        assert "ast.Constant" in check.command
        assert "docs" in check.command and "ast.Expr" in check.command, (
            "the oracle does not exclude docstrings, so a docstring naming a forbidden event would "
            "read as code that mints it"
        )
        for scan in m10.expect_state:
            if "ast.parse" in scan.command:
                assert "ast.walk" in scan.command

    def test_the_negative_source_scans_prove_their_population(self, state_checks):
        """`test_every_corpus_scanning_negative_assertion_proves_its_population`, applied to this
        scenario's own scans. An assertion that "no module does X" passes trivially while scanning
        nothing — which is exactly the `F` M9's build produced, and exactly what the mutation
        battery's re-export-shim control exists to catch.
        """
        anchored = {
            ("a compensation row cannot be deleted, and no expiry, sweep, reaper or auto-retry "
             "was invented"): "the machine class was found: True",
            "no oversight queue, dashboard, notifier or channel join ships with M10":
                "the machine class was found: True",
            "M10 has no production caller — the dark posture, measured over the shipped package":
                "the M10 machine module is present: True",
        }
        for name, anchor in anchored.items():
            assert name in state_checks, f"the scan {name!r} is gone"
            assert anchor in state_checks[name], (
                f"the scan {name!r} carries no population proof. Without {anchor!r} it passes "
                "while scanning nothing — a module that was never written has no forbidden "
                "surfaces in it"
            )

    def test_the_commit_key_seam_is_proven_against_the_landed_derivation(self, state_checks):
        """`commit_key.py` already names `compensation_id` / entity `Compensation` as the canonical
        occurrence source for `adjust_invoice`, and already FAILS CLOSED without a resolver. The
        scenario proves that against the landed module rather than restating it."""
        name = ("the compensating effect's commit key is the canonical Compensation occurrence, "
                "not the original's key")
        assert name in state_checks
        contains = state_checks[name]
        for literal in (
            "the canonical occurrence field for adjust_invoice: compensation_id",
            "the canonical occurrence entity for adjust_invoice: Compensation",
            "the compensating commit key differs from the original: True",
            "a retry of the SAME compensation converges on one commit key: True",
            "a DIFFERENT compensation of the same invoice is a distinct effect: True",
            "the original commit key is not a substring of the compensating one: True",
            "an unresolved Compensation occurrence still fails closed: refused",
        ):
            assert literal in contains, f"the commit-key seam is not proven: {literal!r}"

    def test_money_is_asserted_as_the_canonical_shape_with_a_positive_control(self, state_checks):
        name = "exposure is canonical money, and a float exposure is refused at construction"
        assert name in state_checks
        contains = state_checks[name]
        assert "positive control, integer minor units and an ISO-4217 code: ACCEPTED 285000|GBP" \
            in contains, "there is no positive control on the money constructor"
        for refusal in ("a float exposure: refused", "a Decimal exposure: refused"):
            assert refusal in contains

    def test_the_k1_resolver_is_asserted_to_be_imported_not_rewritten(self, state_checks):
        name = "the decision_ref resolver is M1's, imported rather than rewritten"
        assert name in state_checks
        contains = state_checks[name]
        assert "M10 imports the K-1 resolver: True" in contains
        assert "M10 imports it from M1: True" in contains
        assert "M10 defines a second K-1 resolver: False" in contains, (
            "nothing refuses a second K-1 executor, and a second authority in one domain is what "
            "CLAUDE.md §5 rule 17 forbids"
        )

    def test_the_transition_set_is_compared_to_the_specification_itself(self, state_checks):
        """Not to a list this file keeps. The check parses §14 of the canonical machine file and
        compares it to what the built machine declares."""
        name = "the nine canonical CM transitions are an exact set match with the specification"
        assert name in state_checks
        contains = state_checks[name]
        assert "transition rows in the specification: 9" in contains
        assert "transition rows the machine declares: 9" in contains
        assert "exact set match: True" in contains
        ids = "['CM-1', 'CM-1r', 'CM-2', 'CM-2n', 'CM-3', 'CM-4', 'CM-4f', 'CM-5', 'CM-5x']"
        assert f"the canonical CM transition ids: {ids}" in contains
        assert f"the machine transition ids: {ids}" in contains


# --------------------------------------------------------------------------
# 4. The task preserves the authority conflicts rather than resolving them
# --------------------------------------------------------------------------


class TestTheTaskPreservesTheAuthorityConflicts:
    """Thirteen conflicts were found while reading the corpus. Each is a place where a build session,
    acting reasonably, settles a specification question by accident — and the settlement is
    invisible afterwards, because the code looks decided.

    The task's job is to hand them over OPEN.
    """

    def test_all_thirteen_authority_questions_are_recorded(self):
        for aq in AUTHORITY_QUESTIONS:
            assert aq in M10_TASK, f"{aq} is not recorded in the task"

    def test_the_task_says_they_are_reported_and_not_resolved(self):
        assert ("You are to preserve them, build the fail-closed side, and report. You are not to "
                "resolve them.") in M10_TASK_FLAT
        assert "Do **not** resolve any `M10-AQ-*`. Record it." in M10_TASK_FLAT

    def test_aq1_refuses_to_mint_the_unregistered_correction_event(self):
        """`CorrectionInvalidatedAnEffect` appears in four canonical files and in none of the 118
        registered contracts. `P6-D2` records exactly that with `closes_at: M10`, and M6 answered
        the same seam by emitting the REGISTERED `ClaimCorrected` and minting no unregistered name.
        """
        assert "CorrectionInvalidatedAnEffect" in M10_TASK
        assert "P6-D2" in M10_TASK
        assert "DO NOT MINT `CorrectionInvalidatedAnEffect`. DO NOT SILENTLY MAP IT ONTO SOME " \
               "OTHER EVENT." in M10_TASK_FLAT
        assert "ClaimCorrected" in M10_TASK, (
            "the task never names the registered correction event that already exists, so a build "
            "session has no landed seam to consume and will invent one"
        )
        assert "raise_from_correction" in M10_TASK

    def test_aq2_makes_the_builder_check_trigger_names_against_the_registry(self):
        """Four of the six names machine §33 calls "events consumed" are not events."""
        assert "FOUR OF THOSE SIX NAMES ARE NOT CANONICAL EVENTS" in M10_TASK
        for name in ("HumanApproved", "NoCompensatingActionExists", "HumanEstablishedReality",
                     "PipelineFailed"):
            assert name in M10_TASK, f"the task never examines the trigger name {name!r}"
        assert "MAP ONLY WHERE AUTHORITY MECHANICALLY SUPPORTS A MAPPING. INVENT NO MISSING " \
               "CONTRACT." in M10_TASK_FLAT

    def test_aq3_keeps_the_decision_ref_requirement_without_inventing_a_column(self):
        assert "DO NOT INVENT A COLUMN WITHOUT AUTHORITY. DO NOT DROP THE `decision_ref` " \
               "REQUIREMENT." in M10_TASK_FLAT
        assert "reality_decision_ref" in M10_TASK, (
            "the task never distinguishes the INVALIDATING decision_ref from CM-5's reality one, "
            "which is the whole of M10-AQ-3"
        )

    def test_aq4_refuses_to_fabricate_a_pipeline_for_the_not_possible_branch(self):
        assert "DO NOT CREATE A DUMMY PIPELINE INSTANCE. DO NOT SILENTLY OMIT A REQUIRED " \
               "CO-COMMIT." in M10_TASK_FLAT
        assert "PL-15" in M10_TASK, (
            "the task never names M2's landed transition that already declares the other half of "
            "this co-commit"
        )

    def test_aq5_refuses_to_widen_the_reality_established_contract(self):
        assert "Do not widen the enum, do not mint an F10 event, and do not emit " \
               "`outcome=\"COMPLETED\"`." in M10_TASK_FLAT

    def test_aq6_keeps_the_two_aggregates_apart(self):
        assert "Do not collapse `Compensation.APPROVED` with `Approval.GRANTED`/`CONSUMED`." \
            in M10_TASK_FLAT
        assert "AP-7" in M10_TASK

    def test_aq7_forbids_a_second_pipeline_and_an_m2_edit(self):
        assert "Reuse M2; do not create a second pipeline; do not edit M2's state machine." \
            in M10_TASK_PROSE

    def test_aq8_names_the_substrate_that_exists_today_and_forbids_the_future_machines(self):
        assert "M11 Policy, M12 Rule and M13 Brake are unbuilt" in M10_TASK_FLAT
        assert "M10 uses that substrate and builds none of those lifecycles." in M10_TASK_FLAT
        assert "BrakeNarrowed" in M10_TASK, (
            "the task never mentions the registered-but-unemitted F13 contract, which is exactly "
            "the thing a build session emits while thinking it is being thorough"
        )

    def test_aq9_forbids_improving_the_canonical_uniqueness_predicate(self):
        assert "BUILD THAT PREDICATE EXACTLY AS WRITTEN. DO NOT \"IMPROVE\" IT." in M10_TASK_FLAT
        assert "Report it. Do not silently change the canonical predicate to close it." \
            in M10_TASK_FLAT

    def test_aq10_forbids_minting_a_second_refusal_cause(self):
        assert "DO NOT MINT A `CompensationRefused` VARIANT FOR THEM" in M10_TASK_FLAT
        assert "fixes `cause` to the literal" in M10_TASK_FLAT

    def test_aq11_refuses_to_build_a_capability_registry_or_trust_a_model(self):
        assert "DO NOT BUILD A CAPABILITY REGISTRY. DO NOT INFER IMPOSSIBILITY FROM MODEL " \
               "OUTPUT OR ARBITRARY TEXT." in M10_TASK_PROSE

    def test_aq13_keeps_k4s_provenance_rule_on_a_required_money_field(self):
        """`K-4` names Compensation explicitly and requires a money field to carry the reference it
        was read from. Entity §10 lists `exposure` as required and names no such reference, and the
        three landed precedents diverge — M1 persists the provenance, M7 and M9 persist a bare
        annotation, M2 persists no money at all and says why.

        M10's exposure is required AND money-affecting, which is the strongest of those positions.
        """
        assert "K-4" in M10_TASK
        assert "exposure_observation_ref" in M10_TASK, (
            "the task never names M1's landed three-column precedent, so a build session has no "
            "model for a money field that carries its provenance"
        )
        assert "Do not persist a money value with no provenance, and do not invent a reference " \
               "the corpus does not support." in M10_TASK_PROSE

    def test_aq12_leaves_the_m9_seam_named_and_unwired(self):
        assert "M10 EMITS ITS F10 EVENTS AND STOPS THERE." in M10_TASK_FLAT
        assert "M9-AQ-4" in M10_TASK, (
            "the task never cites M9's own precedent for leaving a seam unwired, which is the "
            "reason this one stays unwired too"
        )
        assert "wiring a seam is precisely what shipping dark forbids" in M10_TASK_FLAT


# --------------------------------------------------------------------------
# 5. The seams are scoped to M10
# --------------------------------------------------------------------------


class TestTheSeamsAreScopedToM10:
    """M10 sits on top of more landed machinery than any P6 unit before it: M1's resolver, M2's
    pipeline, M3's ledger, M4's approval, P3's checkpoint and brake, P1's commit key, P5's outbox.

    Every one of those is a place to build a second copy by accident.
    """

    def test_the_task_sends_the_builder_to_the_landed_code_not_the_prose_about_it(self):
        assert "Then read the LANDED CODE, not the prose about it" in M10_TASK
        for module in (
            "src/freight_recon/work_item.py",
            "src/freight_recon/pipeline_instance.py",
            "src/freight_recon/external_effect.py",
            "src/freight_recon/approval.py",
            "src/freight_recon/checkpoint.py",
            "src/freight_recon/brake.py",
            "src/freight_recon/commit_key.py",
            "src/freight_recon/fingerprint.py",
            "src/freight_recon/identity_binding_claim.py",
            "src/freight_recon/exception.py",
        ):
            assert module in M10_TASK, f"the task never sends the builder to {module}"

    def test_the_commit_key_seam_points_at_the_landed_canonical_occurrence(self):
        """This is the single most valuable finding in the corpus read, and the task must hand it
        over rather than let a build session invent a derivation."""
        assert "CANONICAL_OCCURRENCE_SOURCES" in M10_TASK
        assert "compensation_id" in M10_TASK
        assert "UnresolvedCanonicalOccurrence" in M10_TASK
        assert "DO NOT negate, prefix, suffix, hash or otherwise derive it from the original " \
               "effect's commit key" in M10_TASK_FLAT

    def test_the_gate_seam_says_the_default_already_satisfies_cm2(self):
        """`GateRegistry._DEFAULT` is `HUMAN_APPROVAL_REQUIRED`, so "money-affecting compensation is
        ALWAYS human-approved" holds structurally WITHOUT registering anything — and registering
        something would make M10 a second gate minter."""
        assert "an unregistered action class already resolves to the default " \
               "`HUMAN_APPROVAL_REQUIRED`" in M10_TASK_FLAT
        assert "M10 REGISTERS NO GATE, MINTS NO GATE DECISION, AND BUILDS NO POLICY OR RULE " \
               "LIFECYCLE." in M10_TASK_FLAT

    def test_the_brake_seam_uses_the_landed_narrow_rather_than_building_m13(self):
        assert "BrakeStore.narrow()" in M10_TASK
        assert "M13 IS NOT YOURS." in M10_TASK
        assert "M10 itself engages no brake and narrows none." in M10_TASK_FLAT

    def test_the_money_seam_forbids_a_float(self):
        assert "Money(amount_minor: int, currency: str)" in M10_TASK
        assert "A float is refused at construction; so is a `Decimal`." in M10_TASK_FLAT

    def test_the_resolver_seam_forbids_a_second_k1_executor(self):
        assert "`resolve_decision_ref` is M1's. Import it." in M10_TASK_PROSE
        assert "A second K-1 executor is the second authority `CLAUDE.md` §5 rule 17 forbids." \
            in M10_TASK_PROSE

    def test_the_effect_authority_stays_m3s(self):
        assert "M3 is the sole effect authority." in M10_TASK_FLAT
        assert "does not duplicate `EF-5`" in M10_TASK_FLAT

    def test_the_task_forbids_the_five_shapes_of_privileged_undo(self):
        """Entity §4 and the target spec name them. Each is a real thing an engineer builds while
        believing they are being careful."""
        for shape in (
            "direct rollback" if "direct rollback" in M10_TASK else "rollback",
            "privileged undo",
            "adapter fast path" if "adapter fast path" in M10_TASK else "fast path",
            "bulk undo",
        ):
            assert shape in M10_TASK.lower() or shape in M10_TASK, (
                f"the task never forbids the {shape!r} shape of ungated undo"
            )
        assert "M10 INVOKES NO ADAPTER DIRECTLY." in M10_TASK_FLAT
        assert "M10 REUSES NEITHER THE ORIGINAL PIPELINE'S AUTHORITY NOR THE ORIGINAL EFFECT " \
               "GRANT." in M10_TASK_FLAT


# --------------------------------------------------------------------------
# 6. The M10 vocabulary is safe and visible
# --------------------------------------------------------------------------


class TestTheM10Vocabulary:
    def test_every_scenario_command_is_read_only_with_respect_to_authority(self, m10):
        """A verification scenario observes the product. It never edits the documents the product
        is judged against."""
        authority = ("docs/implementation/CURRENT.md", "IMPLEMENTATION-REGISTRY.yaml",
                     "docs/specifications/", "CLAUDE.md", "PRODUCT.md")
        for spec in m10.commands:
            for doc in authority:
                assert not re.search(r"(>|>>|tee|sed -i|rm )\s*\S*" + re.escape(doc), spec.run), (
                    f"the command {spec.name!r} writes to repository authority"
                )

    def test_no_command_mutates_the_neyma_working_tree(self, m10):
        forbidden = ("git checkout", "git restore", "git stash", "git clean", "git reset")
        for spec in m10.commands:
            for verb in forbidden:
                assert verb not in spec.run, (
                    f"the command {spec.name!r} uses {verb!r}, which can discard the build"
                )
        for check in m10.expect_state:
            for verb in forbidden:
                assert verb not in check.command, (
                    f"the state check {check.name!r} uses {verb!r}"
                )

    def test_the_task_forbids_git_recovery_in_the_mutation_battery(self):
        assert "Never use `git checkout`, `git restore`, `git stash` or `git clean` to undo a " \
               "mutation." in M10_TASK_FLAT

    def test_the_case_vocabulary_covers_every_load_bearing_invariant(self, cases):
        """The `--list-cases` contract is what the generator sees. A family missing from it is a
        family no generated case can reach."""
        required = {
            "M-33 / eligibility": "compensation-cannot-be-created-from-an-unknown-outcome",
            "the ledger, not a flag": "the-original-state-is-read-from-the-ledger-not-a-caller-flag",
            "model-inferred invalidation": "a-model-inferred-invalidation-is-refused",
            "the owner": "an-ownerless-compensation-is-structurally-impossible",
            "the exposure": "exposure-is-required-from-required",
            "the six states": "the-six-canonical-states-and-no-seventh",
            "no expiry": "a-compensation-never-expires",
            "no timer": "no-timer-moves-compensation-failed",
            "no auto-retry": "there-is-no-automatic-retry-from-compensation-failed",
            "human approval": "required-to-approved-requires-an-authenticated-human",
            "approval binding": "the-approval-is-bound-to-this-compensations-commit-key",
            "the full pipeline": "the-compensating-effect-passes-the-full-checkpoint",
            "its own grant": "the-compensating-effect-claims-its-own-grant",
            "no grant reuse": "the-original-effect-grant-is-never-reused",
            "its own commit key": "the-compensating-effect-has-its-own-commit-key",
            "the brake": "an-active-brake-blocks-a-compensating-write",
            "readback": "completed-requires-a-verified-compensating-effect",
            "the failed path": "a-needs-verification-pipeline-reaches-compensation-failed",
            "the impossible path": "not-possible-keeps-its-owner-and-exposure",
            "reality established": "cm5-emits-the-shared-f3-realityestablished-with-subject-compensation",
            "no second contract": "m10-mints-no-second-realityestablished-contract",
            "uniqueness": "one-active-compensation-per-invalidated-effect",
            "concurrency": "concurrent-creation-yields-exactly-one-compensation",
            "the storm": "n-invalidated-effects-raise-n-individually-gated-compensations",
            "no bulk grant": "there-is-no-bulk-effect-grant",
            "tenancy": "a-cross-tenant-original-effect-is-refused",
            "atomicity": "state-and-event-co-commit-in-one-transaction",
            "replay": "replay-mints-zero-pipelines-grants-claims-and-effects",
            "ship dark": "m10-ships-dark-with-zero-production-importers",
            "no future machines": "m11-m12-and-m13-are-not-built",
            "the M9 seam": "the-m9-escalation-seam-is-named-and-left-unwired",
            "regression": "m1-through-m9-are-unchanged",
        }
        for label, case in required.items():
            assert case in cases, f"no case covers {label}: {case!r} is missing"

    def test_every_case_in_the_scenario_is_also_in_the_task(self, cases):
        """Two documents, one vocabulary. A case the scenario asserts but the task never asks for
        is a case the builder has no reason to write."""
        missing = [c for c in cases if c not in M10_TASK]
        assert not missing, f"the task never asks for these cases: {missing}"

    def test_the_probe_case_commands_are_reachable_from_the_declared_case_list(self, m10, cases):
        """A `--case` the scenario runs but `--list-cases` does not offer is a command the
        generator can never legitimately compose."""
        for spec in m10.commands:
            match = re.search(r"--case ([a-z0-9-]+)", spec.run)
            if match:
                assert match.group(1) in cases, (
                    f"the command {spec.name!r} runs --case {match.group(1)!r}, which "
                    "--list-cases does not offer"
                )

    def test_the_m10_probe_prefix_is_approved_so_a_generated_case_can_be_composed(self, m10):
        """A generated case runs an approved command with an argument tail. So the M10 probe has to
        be in the approved set as a PREFIX something can be appended to — otherwise every generated
        `--case` is refused as an unapproved command and the unit is un-attackable.

        The arithmetic half of this — whether the approved union fits inside the generation brief's
        render bound — is deliberately NOT restated here. It lives ONCE, over the shipped corpus, in
        `test_scenario_generation.py`; six per-unit copies of it is the duplication that guard was
        consolidated to remove.
        """
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        composed = (
            f"{PROBE} --case the-compensating-effect-passes-the-full-checkpoint "
            "--original-state VERIFIED --seed 7"
        )
        ok, reason = approved.approves(composed)
        assert ok, (
            "a generated M10 case built from the declared vocabulary is not approved, so dynamic "
            f"generation cannot reach this unit at all: {reason}"
        )

    def test_an_invented_m10_command_is_not_approved(self, m10):
        """The other half. If everything were approved, the check above would prove nothing."""
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        ok, reason = approved.approves(
            'python -c "import compensation; compensation.undo_all()"'
        )
        assert not ok, "an invented command is approved; the approval set constrains nothing"
        assert "approved set" in reason

    #: The P6 units in build order, by the scenario name each one's bootstrap targets. The local
    #: config carries ONE of these at a time, and it only ever moves FORWARD.
    P6_UNIT_ORDER: tuple[str, ...] = (
        "p6_work_item_ownership",
        "p6_pipeline_instance",
        "p6_m3_external_effect",
        "p6_m4_approval",
        "p6_m5_observation",
        "p6_m6_identity_binding_claim",
        "p6_m7_conflict",
        "p6_m8_expectation",
        "p6_m9_exception",
        "p6_m10_compensation",
        "p6_m11_policy",
    )

    def test_the_local_config_never_targets_a_unit_before_m10(self):
        """The retarget is the established convention: `driver.config.yaml` carries one unit at a
        time, and a stale target is how a run verifies the previous unit while claiming this one.

        ### **The direction is what matters, not the exact name.** This guard was written at the
        M10 bootstrap and pinned `p6_m10_compensation` exactly — which made it fail the moment the
        M11 bootstrap retargeted the config, i.e. at precisely the moment the convention was being
        followed correctly. **A guard that fires on the correct move is a guard that gets deleted
        rather than fixed**, so it is restated as the invariant it always meant, exactly as
        `test_m9_readiness` restated its own at this bootstrap's predecessor: the config may sit on
        M10 or on any LATER unit, and may never fall BACK to one M10 has already superseded.

        The protective value is unchanged — a config left on M9 while a session claims to verify
        M10 is still caught — and no forward knowledge of M11's name is required for that.
        `test_the_direction_rule_still_catches_a_config_left_behind` proves it on a synthesised
        config rather than asserting it.
        """
        local = DRIVER_ROOT / "driver.config.yaml"
        if not local.exists():
            pytest.skip("no local driver.config.yaml on this checkout")
        raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        target = raw.get("scenario")
        assert target, "the local config names no scenario at all"
        earlier = self.P6_UNIT_ORDER[: self.P6_UNIT_ORDER.index("p6_m10_compensation")]
        assert target not in earlier, (
            f"the local config targets {target!r}, a unit M10 has already superseded; a run would "
            "verify the previous unit and report this one"
        )

    def test_the_direction_rule_still_catches_a_config_left_behind(self):
        """The control the restatement above owes.

        Loosening a pinned equality into a direction rule is only defensible if the defect it was
        pinned for is still caught. Both halves are proven on synthesised configs: a target BEFORE
        M10 is refused, and a target at or after M10 is accepted.
        """
        earlier = self.P6_UNIT_ORDER[: self.P6_UNIT_ORDER.index("p6_m10_compensation")]
        assert "p6_m9_exception" in earlier, "M9 is not recognised as a unit M10 superseded"
        assert "p6_m8_expectation" in earlier
        assert "p6_m10_compensation" not in earlier, "M10 would refuse its own name"
        assert "p6_m11_policy" not in earlier, "the rule would refuse a forward move"

    def test_no_superseded_units_case_vocabulary_is_still_enumerated(self):
        """The other half of a stale config, and the one that quietly spends the render budget.

        A prior unit's probe is a REGRESSION ANCHOR inside the current permanent scenario, and a
        prefix match already approves every `--case` tail of it. Enumerating its cases again only
        pushes the unit actually under test toward the brief's render bound — which is exactly how
        M3's bare probe went invisible at the M9 bootstrap.
        """
        local = DRIVER_ROOT / "driver.config.yaml"
        if not local.exists():
            pytest.skip("no local driver.config.yaml on this checkout")
        raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        vocabulary = raw.get("scenario_generation", {}).get("approved_commands") or []
        target = raw.get("scenario")
        if target == "p6_m10_compensation":
            m10 = [c for c in vocabulary if "probe_phase6_compensation.py" in c]
            assert m10, "the M10 vocabulary is not enumerated in the local config at all"
            stale = [c for c in vocabulary if "probe_phase6_exception.py --case" in c]
            assert not stale, (
                f"{len(stale)} M9 `--case` entries are still enumerated in the local vocabulary"
            )
        else:
            # The config has moved past M10. M10's own `--case` vocabulary is now the superseded
            # one, and the same rule applies to it.
            stale = [c for c in vocabulary if "probe_phase6_compensation.py --case" in c]
            assert not stale, (
                f"{len(stale)} M10 `--case` entries are still enumerated in the local vocabulary "
                f"while the config targets {target!r}. M10's probe is a regression anchor now, and "
                "a prefix match already approves every tail of it"
            )

    def test_every_enumerated_local_case_is_one_the_scenario_declares(self, cases):
        """The config and the scenario must agree on the vocabulary. An enumerated `--case` the
        probe was never asked to implement is a command the generator will compose and the product
        will refuse — a run that fails as a product defect for a configuration reason.

        Scoped to the config that actually targets M10: once it has moved on, M10's cases are not
        expected to be enumerated at all, and the sibling above is the check that applies.
        """
        local = DRIVER_ROOT / "driver.config.yaml"
        if not local.exists():
            pytest.skip("no local driver.config.yaml on this checkout")
        raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        if raw.get("scenario") != "p6_m10_compensation":
            pytest.skip("the local config has moved past M10")
        vocabulary = raw.get("scenario_generation", {}).get("approved_commands") or []
        enumerated = [
            m.group(1) for m in
            (re.search(r"probe_phase6_compensation\.py --case ([a-z0-9-]+)", c)
             for c in vocabulary)
            if m
        ]
        assert enumerated, "no M10 `--case` entries are enumerated"
        unknown = sorted(set(enumerated) - set(cases))
        assert not unknown, (
            f"the local config enumerates cases the scenario never declares: {unknown}"
        )

    def test_the_task_states_the_output_contract_the_scenario_asserts(self):
        """Every literal the scenario requires the probe to print must be a literal the task told
        the builder to print. Otherwise the scenario is asking for output nobody was asked to
        produce, and the run fails as a product defect."""
        for literal in SAFETY_LITERALS + DARK_POSTURE_LITERALS:
            assert literal in M10_TASK, (
                f"the scenario requires the probe to print {literal!r}, and the task never asks "
                "for it"
            )
        assert "behaviours as specified, 0 wrong" in M10_TASK

    def test_the_task_states_the_forbidden_markers_the_scenario_watches_for(self, m10):
        """Every M10-specific alarm the scenario watches for must be one the task told the builder
        to print. A marker the scenario forbids and the task never defines is a marker the probe
        will never emit — which makes that half of `forbidden:` decorative.

        Scoped to the fully-bracketed `### ... ###` form: `### NOT REFUSED` and its two siblings are
        shared harness vocabulary carried by every unit's scenario, not M10's own alarms.
        """
        specific = [m for m in m10.forbidden if m.startswith("### ") and m.endswith(" ###")]
        assert len(specific) >= 90, (
            f"only {len(specific)} M10-specific forbidden markers; the alarm vocabulary has been "
            "thinned out"
        )
        missing = [m for m in specific if m not in M10_TASK]
        assert not missing, f"the task never defines these forbidden markers: {missing[:6]}"


# --------------------------------------------------------------------------
# 7. Dynamic generation can close an M10 coverage gap, safely
# --------------------------------------------------------------------------


STATE_ORACLE = next(
    check.command
    for check in load_scenario(M10_PATH).expect_state
    if "schema_readiness_problems" in check.command
)


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close.

    Built as the planner builds one: the citation lives on the provenance, and a coverage-gap case
    that cannot name a risk from this run's own register is refused before it reaches the boundary.
    """
    return GeneratedScenario(
        id="gen-m10-privileged-undo",
        title="an undo never gets a privileged path",
        purpose=(
            "a compensation whose execution skipped the checkpoint, reused the original grant or "
            "called the adapter directly is an ungated write with a good excuse"
        ),
        risk_category=RiskCategory.SAFETY_INVARIANT,
        priority=Priority.P0,
        rationale="the identified privileged-undo risk had no scenario behind it",
        requirement_reference="P6/M10",
        product_principle_reference="effect-truth",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared state, so "
            "nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m10-task",
            session_id="scripted",
            generating_risk="a compensating write could reach the TMS without passing the checkpoint",
            source_risks=[risk_key],
        ),
        actions=[{
            "kind": "command",
            "name": "drive a compensation and watch for a privileged path",
            "command": command,
            # The command that prints it, named. An asserted literal no operation in the scenario
            # declares is refused as an unattributable oracle.
            "expect_contains": ["A COMPENSATION IS ITSELF A SEPARATELY GATED EXTERNAL EFFECT"],
        }],
        # `safety_invariant` claims here are about a TABLE and a KERNEL — "the compensating effect
        # passed the checkpoint" is not something a probe can prove by printing it.
        persisted_state_checks=[
            GeneratedStateCheck(
                name="the compensation layer is still tenant-first and readable",
                command=STATE_ORACLE,
                contains=["problems: []", "compensations"],
            )
        ],
        expected_observations=["A COMPENSATION IS ITSELF A SEPARATELY GATED EXTERNAL EFFECT"],
        forbidden_observations=["### CHECKPOINT BYPASSED ###"],
    )


class TestGenerationClosesM10GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-privileged-undo",
            description="a compensating write could reach the TMS without passing the checkpoint",
            risk_category=RiskCategory.SAFETY_INVARIANT,
            severity=Priority.P0,
            basis="entity §4 and M-47: no privileged bypass exists for compensation",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m10", "p6", "m10"},
                principle_tokens={"effect-truth"},
                known_risk_ids={risk.key, "R-privileged-undo"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m10_vocabulary_is_accepted(self, context):
        ctx, risk = context
        command = (
            f"{PROBE} --case the-compensating-effect-passes-the-full-checkpoint "
            "--inject skip-checkpoint --original-state VERIFIED --seed 7"
        )
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M10 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario(
                'python -c "import compensation; compensation.undo_all()"', risk.key)],
            ctx,
        )
        assert not accepted
        assert rejected
        reasons = rejected[0][1]
        assert any("approved" in r.lower() for r in reasons), reasons

    def test_a_gap_case_touching_repository_authority_is_refused(self, context):
        """A verification scenario observes the product; it never edits the rules the product is
        judged against — and for this unit that includes the specification the guards derive from."""
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario(f"{PROBE} --case x docs/implementation/CURRENT.md", risk.key)], ctx
        )
        assert not accepted
        reasons = rejected[0][1]
        assert any("authority" in r.lower() for r in reasons), reasons

    def test_an_uncovered_p0_m10_risk_blocks_acceptance(self):
        """Coverage is not a tally. A risk the run itself called P0 with no passing scenario behind
        it prevents an ACCEPT even when everything that DID run was green."""
        from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
        from neyma_product_driver.scenario_suite import (
            Origin,
            Outcome,
            ScenarioOutcome,
            SuiteResult,
        )

        passing = ScenarioOutcome(
            scenario_id="gen-required",
            scenario_name="gen-required",
            origin=Origin.GENERATED,
            outcome=Outcome.PASSED,
            required=True,
            risk_category="authorization",
            evidence_path="/runs/gen-required",
            evidence_verified=True,
        )
        result = SuiteResult(outcomes=[passing], expected_required_ids=["gen-required"])
        assert evaluate_gate(result, risks=[]).status is GateStatus.VERIFIED

        verdict = evaluate_gate(
            result,
            risks=[
                IdentifiedRisk(
                    id="R-privileged-undo",
                    description="a compensating write could reach the TMS without the checkpoint",
                    risk_category=RiskCategory.SAFETY_INVARIANT,
                    severity=Priority.P0,
                    basis="M-47: no privileged bypass exists for compensation",
                )
            ],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert "KNOWN COVERAGE GAPS" in verdict.summary_block()


# --------------------------------------------------------------------------
# 8. P6-D46 stays closed for M10
# --------------------------------------------------------------------------


class TestP6D46StaysClosedForM10:
    """`P6-D46`: the M6 re-verification run proposed nine scenarios, every one declared a
    `risk_category` the harness's own enum did not contain, all nine were discarded at the parse
    stage, and the run reported *"0 generated case(s) + 1 permanent scenario"* and ACCEPTED.

    Nothing had failed. The product was fine. But *"the generator legitimately produced nothing
    new"* and *"the generator produced nine and Product Driver could not read any of them"* had
    collapsed into one number, and only the first is a reason to accept.

    The fix is general and lives in `tests/test_generation_contract.py`. What is pinned HERE is that
    M10 does not reopen it from the permanent-scenario side. **Nothing about M10 is special-cased
    inside Product Driver core to achieve that.**
    """

    RECORDING = {
        f"{PROBE} --case the-compensating-effect-passes-the-full-checkpoint": (
            "A COMPENSATION IS ITSELF A SEPARATELY GATED EXTERNAL EFFECT\n"
            "behaviours as specified, 0 wrong\n"
        ),
    }

    def _planner(self, tmp_path: Path, payloads) -> ScenarioPlanner:
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner(list(payloads)),
            base_scenario=load_scenario(M10_PATH),
            permanent_scenarios=[load_scenario(M10_PATH)],
            founder=FakeFounder(),
            contract_probe=recorded_contract_probe(self.RECORDING),
        )

    def _m10_raw(self, scenario_id: str, category: str) -> dict:
        """A proposal shaped for THIS unit: dark, command-driven, with a persisted-state oracle."""
        return raw_scenario(
            scenario_id,
            risk_category=category,
            requirement="U-042: an approved invoice is paid exactly once",
            principle="effect-truth",
            service_refs=[],
            actions=[{
                "kind": "command",
                "name": "drive a compensation and watch for a privileged path",
                "command": f"{PROBE} --case the-compensating-effect-passes-the-full-checkpoint",
                "expect_contains": ["A COMPENSATION IS ITSELF A SEPARATELY GATED EXTERNAL EFFECT"],
            }],
            state_checks=[{
                "name": "the compensation layer is still tenant-first and readable",
                "command": STATE_ORACLE,
                "contains": ["problems: []"],
            }],
            expected_observations=["A COMPENSATION IS ITSELF A SEPARATELY GATED EXTERNAL EFFECT"],
            forbidden_observations=["### CHECKPOINT BYPASSED ###"],
            cleanup=[],
            isolation_key="compensation-db",
            isolation_note=(
                "the probe builds its own temporary database per case and touches no shared "
                "state, so nothing survives it to contaminate the next scenario"
            ),
            generating_risk="a compensating write could reach the TMS without the checkpoint",
        )

    def test_the_m10_scenario_declares_only_canonical_categories(self, m10):
        """The half a permanent scenario can break on its own.

        Every `verifies:` entry names a `RiskCategory` member, checked against the ONE taxonomy
        rather than against a list this file keeps.
        """
        declared = m10.declared_risk_categories()
        assert declared, "the M10 scenario declares no risk coverage at all"
        unknown = sorted(declared - set(RISK_CATEGORY_VALUES))
        assert not unknown, (
            f"the M10 scenario declares categories the harness taxonomy does not contain: {unknown}"
        )

    def test_an_invented_category_in_the_m10_file_would_refuse_to_load(self, tmp_path):
        """The load-time refusal, exercised against a copy of the REAL M10 file.

        A `verifies:` entry naming a category the taxonomy does not hold would match no risk and
        read as coverage while providing none — which is `P6-D46`'s shape one layer down.
        """
        raw = copy.deepcopy(yaml.safe_load(M10_PATH.read_text(encoding="utf-8")))
        raw["verifies"][0]["risk_category"] = M10_UNREADABLE_CATEGORIES[0]
        path = tmp_path / "m10_invented.yaml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with pytest.raises(Exception):
            load_scenario(path)

    def test_the_m10_flavoured_unreadable_categories_are_all_outside_the_taxonomy(self):
        """The nine names are the shape a model actually produces when the schema does not
        constrain it: a description of a defect, not a member of a family vocabulary."""
        for invented in M10_UNREADABLE_CATEGORIES:
            assert invented not in RISK_CATEGORY_VALUES, (
                f"{invented!r} has become canonical; this fixture no longer models P6-D46"
            )


# --------------------------------------------------------------------------
# 9. M10 is scoped as a unit, and cannot move the phase
# --------------------------------------------------------------------------


@pytest.fixture
def m10_repo(tmp_path: Path) -> PhaseRepo:
    """A phase in progress, one unit being built, a stated review rule."""
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/compensation.py", "# the unit under construction\n")
    repo.commit_all("the M10 candidate")
    return repo


class TestM10IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m10(self, m10_repo: PhaseRepo):
        scope = m10_repo.scope(M10_TASK)
        assert scope.scope_id == "P6/M10"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(self, m10_repo: PhaseRepo):
        """The task discusses P6 at length. Discussing a phase is not claiming it, and a run that
        inherited the phase's bar would be held to four units that do not exist."""
        scope = m10_repo.scope(M10_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m10_repo: PhaseRepo):
        scope = m10_repo.scope(M10_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(self, m10_repo: PhaseRepo):
        rendered = m10_repo.scope(M10_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "enables nothing in production" in rendered

    def test_the_task_says_criteria_scored_stays_empty(self):
        assert "`criteria_scored` stays `[]`." in M10_TASK_FLAT
        assert "Do **not** unlock P7." in M10_TASK_FLAT


class TestM10CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m10_repo: PhaseRepo
    ):
        scope = m10_repo.scope(M10_TASK)
        completion = scoped_completion(scope, TaskResult.ACCEPTED, phase_accepted=True)
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M10"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m10_repo: PhaseRepo):
        completion = scoped_completion(m10_repo.scope(M10_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")


# --------------------------------------------------------------------------
# 10-11. The loop owns M10 end to end
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m10_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m10_repo.root, m10_repo.scope(M10_TASK), unit=m10_repo.unit()
        )
        assert requirement.required
        assert requirement.from_repository_authority

    def test_the_task_states_the_tier_and_says_why_it_took_the_higher_one(self):
        """CLAUDE.md §7: "When genuinely torn between two tiers, take the higher one once and say
        so."

        A state machine is tier 2 by itself. M10 also lands a MIGRATION, is load-bearing for TENANT
        ISOLATION, and is the only machine in Neyma whose normal operation is an external effect
        that moves money backwards.
        """
        assert "tier-1" in M10_TASK
        assert "migration" in M10_TASK_FLAT
        assert "tenant isolation" in M10_TASK_FLAT
        assert (
            "the only machine in Neyma whose normal operation is an external effect that moves "
            "money backwards" in M10_TASK_FLAT
        )
        assert "takes the higher tier once and says so, and this file says so" in M10_TASK_FLAT


class TestTheLoopOwnsM10EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m10_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session that wrote the code,
        with its evidence path intact."""
        builder = FakeBuilder(m10_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m10_repo, tmp_path, task=M10_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        assert "INDEPENDENT REVIEW" in builder.prompts[1]
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED

    async def test_the_corrected_tree_gets_a_brand_new_reviewer_bound_to_its_exact_fingerprint(
        self, m10_repo: PhaseRepo, tmp_path: Path
    ):
        """The reviewer must be a lineage that did not build M10, and the second reviewer must read
        the CORRECTED tree rather than the one the first one read."""
        builder = FakeBuilder(m10_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m10_repo, tmp_path, task=M10_TASK, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        assert len(set(reviewer.session_ids)) == 2, "the same reviewer session was reused"
        first = reviewer.bindings[0]["fingerprint"]
        second = reviewer.bindings[1]["fingerprint"]
        assert not first.matches(second), "the second reviewer read the same tree as the first"
        assert result.satisfying_review.fingerprint.matches(second)

    async def test_an_accept_is_scoped_m10_acceptance_and_never_p6_complete(
        self, m10_repo: PhaseRepo, tmp_path: Path
    ):
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m10_repo, tmp_path, task=M10_TASK, reviewer=reviewer)

        assert result.status is RunStatus.ACCEPTED
        assert result.audit is not None, "the run accepted without a completion audit"
        completion = result.audit.completion
        assert completion is not None
        assert completion.task_scope == "P6/M10"
        assert completion.task_result in {TaskResult.ACCEPTED, TaskResult.VERIFIED}
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_accepted is False
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    async def test_the_run_stops_at_m10_and_never_walks_into_m11(
        self, m10_repo: PhaseRepo, tmp_path: Path
    ):
        """Two halves of the same guarantee: the task forbids it in words, and the loop ends at its
        own scoped verdict rather than picking up the next unit."""
        assert "Stop at verified M10. Do not automatically continue into M11." in M10_TASK
        assert "begin **M11–M13**" in M10_TASK

        reviewer = FakeReviewer([supported()])
        result, store = await drive(m10_repo, tmp_path, task=M10_TASK, reviewer=reviewer)
        assert result.status is RunStatus.ACCEPTED
        assert result.audit.completion.task_scope == "P6/M10"

        journal = RunJournal(run_id=store.run_id, task=M10_TASK)
        journal.record_outcome(run_status="ACCEPTED")
        summary = journal.personal_summary()
        for forbidden in ("M11", "begin the next unit", "continue into"):
            assert forbidden not in summary.split("### 8. The ONE exact next move")[1], (
                f"the next move points past M10 ({forbidden!r})"
            )


# --------------------------------------------------------------------------
# 12. The founder summary says what M10 actually does, in normal language
# --------------------------------------------------------------------------


class _Gate:
    """Duck-typed GateVerdict stand-in."""

    def __init__(self, status: str, *, passed: int = 0, total: int = 0):
        self.status = status
        self.unverified: list = []
        self.uncovered_risks: list = []
        self.generation_problems: list = []
        self.required_passed = passed
        self.required_total = total

    def headline(self) -> str:
        return f"scenario gate: {self.status}"


def _m10_journal(**outcome) -> RunJournal:
    scenario = load_scenario(M10_PATH)
    journal = RunJournal(run_id="r-m10", task=M10_TASK)
    journal.task_scope_id = "P6/M10"
    journal.parent_phase_id = "P6"
    journal.parent_phase_state = "IN_PROGRESS"
    journal.scope_is_nested = True
    journal.record_outcome(
        scenario_name=scenario.name,
        scenario_phase=scenario.phase,
        scenario_purpose=scenario.description,
        **outcome,
    )
    return journal


class TestTheFounderSummaryExplainsM10:
    def test_it_states_the_product_impact_in_normal_language(self, m10):
        """The scenario description is what a founder reads to learn what the unit is for. It has
        to be a brokerage sentence, not a machine one."""
        text = " ".join(m10.description.split()).lower()
        for phrase in ("human", "carrier", "tms", "invoice", "money"):
            assert phrase in text, f"the description never mentions {phrase!r}"
        assert "ships dark" in text
        assert "no privileged path" in text, (
            "the description never states the no-privileged-path invariant, which is the entire "
            "unit"
        )

    def test_it_never_says_p6_moved(self):
        journal = _m10_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=17, total=17))
        summary = journal.personal_summary()
        assert "P6 is COMPLETE" not in summary
        assert "P6 COMPLETE" not in summary

    def test_it_does_not_imply_a_live_compensation_path(self):
        """A negative that has to be written carefully.

        "enables nothing in production" is a sentence this summary SHOULD carry, so a bare search
        for "in production" fails on the correct text. What must not appear is an ENABLEMENT claim,
        and each phrase below is one — and for THIS unit an enablement claim means a real credit
        note against a real customer.
        """
        journal = _m10_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=17, total=17))
        summary = journal.personal_summary().lower()
        for claim in (
            "enabled for live traffic",
            "live traffic is",
            "production-ready",
            "enabled for customers",
            "compensations are being",
            "invoices are being credited",
            "reversals are",
            "refunds are",
        ):
            assert claim not in summary, f"the summary implies {claim!r}"
        assert "enables nothing in production" in summary

    def test_no_founder_decision_says_none(self):
        journal = _m10_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=17, total=17))
        journal.record_stop(reason="M10 verified.", founder_decision_required="none")
        assert journal.founder_decision_required == ""


# --------------------------------------------------------------------------
# 13. THE MUTATION GUARD — does this file actually fail when the assertion is removed?
# --------------------------------------------------------------------------


def _mutate(edit) -> "object":
    """Load a copy of the SHIPPED M10 scenario with one load-bearing thing weakened.

    `edit` receives the raw YAML mapping and changes it in place. Nothing is written to the
    scenarios directory: the mutant lives in memory and is parsed through the real loader, so a
    weakening the loader itself refuses raises here rather than returning a Scenario.
    """
    import tempfile

    raw = copy.deepcopy(yaml.safe_load(M10_PATH.read_text(encoding="utf-8")))
    edit(raw)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m10_mutant.yaml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return load_scenario(path)


def _named(raw: dict, section: str, name: str) -> dict:
    for entry in raw[section]:
        if entry.get("name") == name:
            return entry
    raise AssertionError(f"{name!r} is not in {section}; the mutation targets a check that is gone")


def _claim(raw: dict, category: str) -> dict:
    for entry in raw["verifies"]:
        if entry.get("risk_category") == category:
            return entry
    raise AssertionError(f"no {category!r} claim to mutate")


def _drop(entry: dict, key: str, literal: str) -> None:
    before = list(entry[key])
    entry[key] = [x for x in before if x != literal]
    assert len(entry[key]) == len(before) - 1, (
        f"{literal!r} was not in {key}; the mutation targets something that is already gone"
    )


#: The names of the three checks the mutations reach for most often.
LIVE_WRITES = ("the live database refuses an ownerless compensation, a seventh state, and "
               "EXECUTING with no pipeline")
STATE_VOCAB = ("the six canonical compensation states are a database constraint, and there is no "
               "seventh")
UNIQUENESS = ("the unique-active-compensation index is tenant-first and carries the canonical "
              "NOT_POSSIBLE predicate")
EVENTS = ("M10 uses the seven registered F10 contracts, invents no eighth, and mints no second "
          "RealityEstablished")


class TestThisFileFailsWhenTheGuardIsRemoved:
    """A readiness test never seen to fail is a decoration.

    Every case below weakens the SHIPPED scenario in one specific way and then runs the REAL
    assertion from earlier in this file against the weakened copy — not a paraphrase of it. If the
    assertion has been loosened into something that passes either way, these turn green and the
    failure is visible here rather than six weeks later in a run that verified nothing.

    `CLAUDE.md` §6: *mutate to prove a guard works when you are writing a guard that protects a
    tier-1 invariant. A guard never seen to fail is a decoration, and a mutation that does not
    reintroduce the real defect proves nothing.*

    Every mutation below reintroduces a defect whose prohibition is CANONICALLY ESTABLISHED — each
    docstring names the authority. Nothing here mutates a preference.
    """

    def test_the_baseline_mutant_is_the_shipped_file_unchanged(self, m10):
        """The control. If `_mutate` cannot round-trip the file, every result below is noise."""
        unchanged = _mutate(lambda raw: None)
        assert unchanged.name == m10.name
        assert len(unchanged.commands) == len(m10.commands)
        assert len(unchanged.expect_state) == len(m10.expect_state)
        assert len(unchanged.verifies) == len(m10.verifies)
        assert unchanged.expect_visible == m10.expect_visible
        assert unchanged.forbidden == m10.forbidden

    # ---- M-33: the refusal on an unknown outcome ---------------------------------------------

    def test_permitting_compensation_of_an_unknown_outcome_turns_the_m33_claim_red(self):
        """M-33, entity §21/§36, machine CM-1r, `AC-REC-001`. Stop requiring the refusal sentence
        and the single most important rule in the unit is unverified — while the run still reports
        green, because everything that DID execute passed."""
        def edit(raw):
            _drop(_claim(raw, "ambiguous_external_effect"), "observations",
                  "COMPENSATION IS FORBIDDEN ON AN UNKNOWN OUTCOME")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_m33_claim_rests_on_the_refusal_being_complete(mutant)

    def test_letting_the_refusal_be_incomplete_turns_the_m33_claim_red(self):
        """`AC-REC-001`'s oracle is *"assert zero compensating calls"*. "We did not create a row"
        is not the same fact as "we wrote nothing at all": a compensating write can CREATE the very
        state it meant to remove, so the pipeline, the grant and the effect must all be zero."""
        def edit(raw):
            claim = _claim(raw, "ambiguous_external_effect")
            claim["checks"] = [c for c in claim["checks"]
                               if "mints no pipeline, no grant and no effect" not in c]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="wrote nothing"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_m33_claim_rests_on_the_refusal_being_complete(mutant)

    # ---- the owner, and I1 -------------------------------------------------------------------

    def test_removing_the_owner_requirement_turns_the_live_attempt_red(self):
        """`I1`, `AC-SAFE-028`, entity §10/§16: `owner_id NOT NULL` from `REQUIRED`. Stop attempting
        the ownerless insert and an exposure with nobody's name on it becomes insertable."""
        def edit(raw):
            _drop(_named(raw, "expect_state", LIVE_WRITES), "contains",
                  "an ownerless compensation: refused")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="never asked to refuse"):
            TestPersistedStateIsTheOracle(
            ).test_the_forbidden_writes_are_attempted_against_a_live_database(mutant, checks)

    def test_permitting_an_owner_from_another_tenant_turns_the_live_attempt_red(self):
        """`[C-1]`. A compensation owned by another brokerage's human is a correction routed to a
        person with no authority over the money it moves."""
        def edit(raw):
            _drop(_named(raw, "expect_state", LIVE_WRITES), "contains",
                  "an owner from another tenant: refused")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="never asked to refuse"):
            TestPersistedStateIsTheOracle(
            ).test_the_forbidden_writes_are_attempted_against_a_live_database(mutant, checks)

    def test_permitting_a_cross_tenant_original_effect_turns_the_live_attempt_red(self):
        """`[C-1]` again, on the other side of the relationship: compensating another tenant's
        effect writes a correction into the wrong customer's accounting system."""
        def edit(raw):
            _drop(_named(raw, "expect_state", LIVE_WRITES), "contains",
                  "an original effect from another tenant: refused")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="never asked to refuse"):
            TestPersistedStateIsTheOracle(
            ).test_the_forbidden_writes_are_attempted_against_a_live_database(mutant, checks)

    # ---- the false-green shapes --------------------------------------------------------------

    def test_removing_the_positive_control_turns_the_live_attempt_red(self):
        """THE false-green shape, and the reason this whole check exists.

        Without a positive control every "refused" line is also true of a table that refuses every
        insert — including a table that does not exist. The check would then be green on a tree
        where M10 was never written.
        """
        def edit(raw):
            _drop(_named(raw, "expect_state", LIVE_WRITES), "contains",
                  "positive control, a well-formed REQUIRED compensation: ACCEPTED")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="no positive control"):
            TestPersistedStateIsTheOracle(
            ).test_the_forbidden_writes_are_attempted_against_a_live_database(mutant, checks)

    def test_removing_the_surviving_row_count_turns_the_live_attempt_red(self):
        """The second half of the same defence: without the count, a run in which both positive
        controls were silently rolled back still reads as green."""
        def edit(raw):
            _drop(_named(raw, "expect_state", LIVE_WRITES), "contains", "rows that survived: 2")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="surviving-row count"):
            TestPersistedStateIsTheOracle(
            ).test_the_forbidden_writes_are_attempted_against_a_live_database(mutant, checks)

    def test_removing_a_population_proof_turns_the_scan_assertion_red(self):
        """`test_every_corpus_scanning_negative_assertion_proves_its_population` — the guard that
        printed a real `F` on M9's build. A scan asserting "no forbidden surfaces" is trivially
        true of a module that was never written."""
        def edit(raw):
            _drop(_named(raw, "expect_state",
                         "M10 has no production caller — the dark posture, measured over the "
                         "shipped package"),
                  "contains", "the M10 machine module is present: True")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="no population proof"):
            TestPersistedStateIsTheOracle().test_the_negative_source_scans_prove_their_population(
                checks
            )

    def test_making_the_money_check_vacuous_turns_its_assertion_red(self):
        """A refusal oracle with no accepted case proves only that the constructor raises."""
        def edit(raw):
            _drop(_named(raw, "expect_state",
                         "exposure is canonical money, and a float exposure is refused at "
                         "construction"),
                  "contains",
                  "positive control, integer minor units and an ISO-4217 code: ACCEPTED 285000|GBP")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="no positive control"):
            TestPersistedStateIsTheOracle(
            ).test_money_is_asserted_as_the_canonical_shape_with_a_positive_control(checks)

    # ---- the lifecycle vocabulary ------------------------------------------------------------

    def test_adding_a_seventh_lifecycle_state_turns_the_state_assertion_red(self):
        """Entity §12 and registry §4 give six. `CANCELLED` and `RETRYING` are the two a build
        session reaches for — entity §25 and machine §20 forbid them by name."""
        def edit(raw):
            _drop(_named(raw, "expect_state", STATE_VOCAB), "contains",
                  "forbidden states present: []")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="nothing refuses a seventh state"):
            TestPersistedStateIsTheOracle(
            ).test_the_six_states_are_asserted_as_a_database_constraint(checks)

    def test_adding_an_expiry_column_turns_the_state_assertion_red(self):
        """Entity §26 says a compensation NEVER expires, in that word. An exposure does not age
        out, and a TTL on this table is a mechanism for forgetting money."""
        def edit(raw):
            _drop(_named(raw, "expect_state", STATE_VOCAB), "contains", "an expiry column: []")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="expiry column"):
            TestPersistedStateIsTheOracle(
            ).test_the_six_states_are_asserted_as_a_database_constraint(checks)

    def test_dropping_the_state_count_turns_the_state_assertion_red(self):
        def edit(raw):
            _drop(_named(raw, "expect_state", STATE_VOCAB), "contains", "state count: 6")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="six-state count"):
            TestPersistedStateIsTheOracle(
            ).test_the_six_states_are_asserted_as_a_database_constraint(checks)

    def test_demoting_the_state_set_from_a_check_to_a_convention_turns_it_red(self):
        """A Python enum is a convention. Only a `CHECK` stops a raw INSERT, and this machine's
        whole safety posture is that the DATABASE refuses rather than the code declining."""
        def edit(raw):
            _drop(_named(raw, "expect_state", STATE_VOCAB), "contains",
                  "the state vocabulary is a CHECK: True")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="not asserted to be a CHECK"):
            TestPersistedStateIsTheOracle(
            ).test_the_six_states_are_asserted_as_a_database_constraint(checks)

    def test_permitting_executing_without_a_pipeline_turns_the_live_attempt_red(self):
        """Entity §16, verbatim: *"a transition to `EXECUTING` requires a bound
        `pipeline_instance_id` (the gated attempt)"*. This is the constraint that makes "execution
        is a gated attempt" a fact the database states rather than a sentence the machine prints."""
        def edit(raw):
            _drop(_named(raw, "expect_state", LIVE_WRITES), "contains",
                  "EXECUTING with no bound pipeline: refused")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="never asked to refuse"):
            TestPersistedStateIsTheOracle(
            ).test_the_forbidden_writes_are_attempted_against_a_live_database(mutant, checks)

    # ---- the timer, and the absence of retry --------------------------------------------------

    def test_letting_a_timer_move_a_failed_compensation_turns_the_lifecycle_claim_red(self):
        """`CM-5x` — the one row in §14 that declares an ILLEGAL outcome, and `AC-REC-004`. A timer
        that could move `COMPENSATION_FAILED` clears the loudest state in the system on a
        schedule."""
        def edit(raw):
            _drop(_claim(raw, "unexpected_state_transition"), "observations",
                  "NO TIMER MOVES COMPENSATION_FAILED")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_lifecycle_claim_requires_the_timer_refusal(mutant)

    def test_permitting_an_automatic_retry_turns_the_retry_safety_claim_red(self):
        """Machine §20: *"a failed compensation is NOT auto-retried — a human decides"* (CM-5). An
        automatic retry of a money-moving write whose outcome is already unknown is the double-pay
        defect wearing a recovery costume."""
        def edit(raw):
            _drop(_claim(raw, "retry_safety"), "observations",
                  "THERE IS NO AUTOMATIC RETRY OF A FAILED COMPENSATION")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_retry_safety_claim_requires_the_stickiness_literals(mutant)

    def test_permitting_the_row_to_be_deleted_turns_the_retry_safety_claim_red(self):
        """Entity §28 `[C-9]`, retention permanent. Deleting the row is the tidiest way of all to
        make an exposure stop existing, and it leaves no trace that it ever did."""
        def edit(raw):
            _drop(_claim(raw, "retry_safety"), "observations",
                  "a BEFORE DELETE guard exists: True")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="rests only on narration"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_retry_safety_claim_requires_the_stickiness_literals(mutant)

    # ---- the no-privileged-path invariant ------------------------------------------------------

    def test_dropping_the_gate_minter_fact_turns_the_safety_claim_red(self):
        """The safety claim must rest on a MEASURED fact, not only on the probe's sentences. A
        machine that printed *"there is no fast path for undo"* while minting its own gate decision
        would satisfy a narration-only claim perfectly."""
        def edit(raw):
            _drop(_claim(raw, "safety_invariant"), "observations",
                  "modules that MINT a gate decision: ['checkpoint.py']")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="rests only on narration"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_safety_invariant_claim_is_the_one_this_unit_turns_on(mutant)

    def test_permitting_reuse_of_the_original_grant_turns_the_safety_claim_red(self):
        """Entity §39: the compensating effect *"claims its own single-use grant under its own
        commit key"*. Reaching for the original grant is the fast path wearing a lanyard."""
        def edit(raw):
            _drop(_claim(raw, "safety_invariant"), "observations",
                  "A COMPENSATING EFFECT NEVER REUSES THE ORIGINAL EFFECT GRANT")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="no-privileged-path proof is not optional"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_safety_invariant_claim_is_the_one_this_unit_turns_on(mutant)

    def test_permitting_a_bulk_undo_turns_the_safety_claim_red(self):
        """Target spec §12.10 and `AC-REC-003`: *"THERE IS NO BULK UNDO — a bulk undo is 200
        ungated writes with one tap."* A correction storm raises N individually-gated
        compensations."""
        def edit(raw):
            _drop(_claim(raw, "safety_invariant"), "observations", "THERE IS NO BULK UNDO")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="no-privileged-path proof is not optional"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_safety_invariant_claim_is_the_one_this_unit_turns_on(mutant)

    # ---- the commit key -----------------------------------------------------------------------

    def test_deriving_the_commit_key_from_the_original_turns_the_seam_assertion_red(self):
        """ADR-009 and `commit_key.py`'s recorded reason. If the compensation's identity were a
        function of the effect it undoes, two different compensations of one invoice would collide
        — and `adjust_invoice` exists precisely because one invoice may receive several distinct
        adjustments."""
        def edit(raw):
            _drop(_named(raw, "expect_state",
                         "the compensating effect's commit key is the canonical Compensation "
                         "occurrence, not the original's key"),
                  "contains", "a DIFFERENT compensation of the same invoice is a distinct effect: True")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="commit-key seam is not proven"):
            TestPersistedStateIsTheOracle(
            ).test_the_commit_key_seam_is_proven_against_the_landed_derivation(checks)

    def test_losing_the_retry_convergence_proof_turns_the_seam_assertion_red(self):
        """Commit-once. A retry of the SAME compensation must converge on one key, or every retry
        mints a new logical effect — which is the double-pay defect this whole kernel exists to
        prevent, pointed at a credit note."""
        def edit(raw):
            _drop(_named(raw, "expect_state",
                         "the compensating effect's commit key is the canonical Compensation "
                         "occurrence, not the original's key"),
                  "contains", "a retry of the SAME compensation converges on one commit key: True")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="commit-key seam is not proven"):
            TestPersistedStateIsTheOracle(
            ).test_the_commit_key_seam_is_proven_against_the_landed_derivation(checks)

    # ---- uniqueness, and the authority question it carries -------------------------------------

    def test_dropping_tenant_from_the_uniqueness_index_turns_its_assertion_red(self):
        """`[C-1]` and entity §17: PK `(tenant_id, compensation_id)`, and the active index is
        `(tenant_id, original_effect_id)`. A uniqueness rule that is not tenant-first makes one
        brokerage's compensation block another's."""
        def edit(raw):
            _drop(_named(raw, "expect_state", UNIQUENESS), "contains",
                  "every compensation index is tenant-first: True")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="index shape is not asserted"):
            TestPersistedStateIsTheOracle(
            ).test_the_uniqueness_predicate_is_read_out_of_the_live_index(checks)

    def test_permitting_a_duplicate_active_compensation_turns_the_boundary_claim_red(self):
        """Entity §17/§33 and machine §17/§19: one active compensation per invalidated effect. Two
        would be two approvals, two grants and two credit notes for one wrong invoice."""
        def edit(raw):
            _drop(_claim(raw, "boundary"), "observations",
                  "a SECOND active compensation for the same invalidated effect: refused")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_boundary_claim_measures_the_uniqueness_question_rather_than_asserting_it(
                mutant
            )

    def test_quietly_resolving_m10_aq9_turns_the_boundary_claim_red(self):
        """`M10-AQ-9`. The canonical predicate excludes `NOT_POSSIBLE` even though `NOT_POSSIBLE`
        is non-terminal and human-owned, so a second compensation for the same original IS
        insertable while the first is still open. That is surprising, and the temptation is to
        "fix" the index. The scenario MEASURES the consequence instead — and dropping the
        measurement is how the authority question gets resolved by accident."""
        def edit(raw):
            _drop(_claim(raw, "boundary"), "observations",
                  "a second compensation while the first is NOT_POSSIBLE (M10-AQ-9): ACCEPTED")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="no longer measures M10-AQ-9"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_boundary_claim_measures_the_uniqueness_question_rather_than_asserting_it(
                mutant
            )

    # ---- the event registry -------------------------------------------------------------------

    def test_minting_a_second_reality_established_contract_turns_the_event_assertion_red(self):
        """Registry §9: `RealityEstablished` is ONE contract with TWO structurally-identical
        producers, `EF-5` and `CM-5`, discriminated by `subject`. A second contract under F10 is
        two authorities for one semantic fact — and the way they drift apart is that one of them
        stops requiring the `decision_ref`."""
        def edit(raw):
            _drop(_named(raw, "expect_state", EVENTS), "contains",
                  "RealityEstablished contracts in the registry: 1")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="ONE contract with TWO"):
            TestPersistedStateIsTheOracle(
            ).test_the_event_vocabulary_is_derived_structurally_not_from_comments(mutant, checks)

    def test_permitting_an_eighth_f10_event_turns_the_event_assertion_red(self):
        """`events/registry.md` is by its own header THE SOLE CANONICAL LIST. F10 has seven
        contracts, and `CompensationCancelled` is the eighth a build session mints when it wants a
        compensation to stop being an obligation."""
        def edit(raw):
            _drop(_named(raw, "expect_state", EVENTS), "contains",
                  "unregistered Compensation-shaped event names in M10 code: []")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="eighth Compensation"):
            TestPersistedStateIsTheOracle(
            ).test_the_event_vocabulary_is_derived_structurally_not_from_comments(mutant, checks)

    def test_letting_a_transition_declare_an_unregistered_event_turns_it_red(self):
        """The runtime half, which is stronger than any text scan: the event names the nine rows
        actually DECLARE are checked against the 118 registered contracts."""
        def edit(raw):
            _drop(_named(raw, "expect_state", EVENTS), "contains",
                  "every declared event name is registered: True")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="never checked against the registry"):
            TestPersistedStateIsTheOracle(
            ).test_the_event_vocabulary_is_derived_structurally_not_from_comments(mutant, checks)

    def test_turning_the_event_oracle_into_a_text_scan_turns_its_assertion_red(self):
        """The comment-and-docstring defence, mutated directly.

        A `grep`-shaped oracle fires on a comment saying *"CorrectionInvalidatedAnEffect is
        deliberately NOT minted here"* — and the obvious "fix" is to delete the comment rather than
        to notice the oracle is wrong.
        """
        def edit(raw):
            check = _named(raw, "expect_state", EVENTS)
            check["command"] = check["command"].replace("ast.parse", "str.split")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="does not parse the module"):
            TestPersistedStateIsTheOracle(
            ).test_the_event_oracle_reads_the_ast_rather_than_the_file_text(mutant)

    def test_dropping_the_docstring_exclusion_turns_the_ast_assertion_red(self):
        def edit(raw):
            check = _named(raw, "expect_state", EVENTS)
            check["command"] = check["command"].replace("ast.Expr", "ast.Pass")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="does not exclude docstrings"):
            TestPersistedStateIsTheOracle(
            ).test_the_event_oracle_reads_the_ast_rather_than_the_file_text(mutant)

    # ---- the resolver, and the transition set --------------------------------------------------

    def test_permitting_a_second_k1_resolver_turns_its_assertion_red(self):
        """`CLAUDE.md` §5 rule 17. M2, M3 and M9 all IMPORT M1's `resolve_decision_ref`. A second
        implementation is a second answer to "did a real human really decide", and the two answers
        diverge silently."""
        def edit(raw):
            _drop(_named(raw, "expect_state",
                         "the decision_ref resolver is M1's, imported rather than rewritten"),
                  "contains", "M10 defines a second K-1 resolver: False")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="second K-1 executor"):
            TestPersistedStateIsTheOracle(
            ).test_the_k1_resolver_is_asserted_to_be_imported_not_rewritten(checks)

    def test_losing_the_exact_transition_set_match_turns_its_assertion_red(self):
        """`AC-MACH-000` and `AC-MACH-1001..1009`. Nine rows, compared against §14 of the canonical
        machine file itself rather than against a list this test keeps."""
        def edit(raw):
            _drop(_named(raw, "expect_state",
                         "the nine canonical CM transitions are an exact set match with the "
                         "specification"),
                  "contains", "exact set match: True")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle(
            ).test_the_transition_set_is_compared_to_the_specification_itself(checks)

    # ---- attribution, taxonomy and scope -------------------------------------------------------

    def test_attributing_a_claim_to_a_sibling_check_is_refused_at_load_time(self):
        """The cross-case attribution defect that blocked the M6 run `20260825-204229`.

        A claim whose observation is declared by check C while it names only checks A and B can
        never be established — it fails closed forever, and it reads on the gate as a product
        defect rather than as the mapping error it is. The loader refuses it, so this mutation
        raises inside `_mutate` rather than returning a Scenario.
        """
        def edit(raw):
            claim = _claim(raw, "malformed_input")
            # A literal only the money check declares, attributed to the schema check instead.
            claim["checks"] = ["a freshly created canonical database carries the compensation "
                               "layer, tenant-first"]

        with pytest.raises(Exception, match="do not include any check that declares it"):
            _mutate(edit)

    def test_naming_a_check_that_does_not_exist_is_refused_at_load_time(self):
        """The sibling defect: a typo produces a claim that can never be established, and a reader
        cannot tell it from a genuine absence of evidence."""
        def edit(raw):
            _claim(raw, "concurrency")["checks"] = ["a check nobody wrote"]

        with pytest.raises(Exception, match="does not run"):
            _mutate(edit)

    def test_an_invented_risk_category_is_refused_at_load_time(self):
        """`P6-D46`, one layer down. A category the taxonomy does not hold matches no risk and
        reads as coverage while providing none."""
        def edit(raw):
            _claim(raw, "regression")["risk_category"] = M10_UNREADABLE_CATEGORIES[0]

        with pytest.raises(Exception):
            _mutate(edit)

    def test_dropping_a_family_m10_owns_turns_the_category_assertion_red(self):
        """`approval_required`, `ambiguous_external_effect` and `timeout_after_effect` are M10's
        own — unlike every P6 machine before it, M10 consumes an M4 approval and produces an
        external effect. Dropping one silently narrows what a run is allowed to be asked about."""
        def edit(raw):
            raw["verifies"] = [c for c in raw["verifies"]
                               if c["risk_category"] != "approval_required"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="does not declare 'approval_required'"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_families_m10_owns_that_m9_could_not_are_declared(mutant)

    def test_dropping_a_dark_posture_literal_turns_its_assertion_red(self):
        """The ship-dark posture is six measured sentences. Stop requiring one and the machine it
        names becomes something M10 could quietly have edited."""
        def edit(raw):
            _drop(_claim(raw, "regression"), "observations",
                  "THE M11, M12 AND M13 MACHINES ARE NOT BUILT")
            raw["expect_visible"] = [v for v in raw["expect_visible"]
                                     if v != "THE M11, M12 AND M13 MACHINES ARE NOT BUILT"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="no longer an expected observation"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_dark_posture_literals_are_still_required_somewhere(mutant)

    def test_dropping_the_regression_anchor_for_a_consumed_machine_turns_it_red(self):
        """M10 consumes M2, M3 and M4 and escalates to M9. If one of them moved, that is M10's
        problem to notice."""
        def edit(raw):
            # The anchor is dropped from the pytest invocation, not the check removed — removing
            # the check would be refused by the loader (the regression claim names it), which is a
            # different and stronger guard. This isolates the one assertion under test.
            for c in raw["commands"]:
                if "test_phase6_approval.py" in c.get("run", ""):
                    c["run"] = c["run"].replace(" eval/tests/test_phase6_approval.py", "")
                    break
            else:  # pragma: no cover - the anchor is already gone
                raise AssertionError("no command runs the M4 regression anchor")

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="test_phase6_approval.py is not run beside M10"):
            TestTheM10BaseScenario().test_it_carries_the_neighbouring_regression_anchors(mutant)

    def test_dropping_the_unit_axis_turns_the_dimension_assertion_red(self):
        """`--original-state` is the axis CM-1 and CM-1r turn on. Without it no generated case can
        vary the state of the effect being compensated, which is the whole eligibility question."""
        def edit(raw):
            listing = [c for c in raw["commands"] if c.get("run", "").endswith("--list-dimensions")]
            assert listing, "the dimension listing is gone"
            listing[0]["expect_contains"] = [
                d for d in listing[0]["expect_contains"] if d != "--original-state"
            ]

        mutant = _mutate(edit)
        dims = [c for c in mutant.commands if c.run.endswith("--list-dimensions")][0]
        with pytest.raises(AssertionError, match="the axis CM-1 and CM-1r turn on"):
            TestTheM10BaseScenario().test_it_declares_the_closed_mutation_axis_this_unit_turns_on(
                list(dims.expect_contains)
            )

    def test_dropping_a_forbidden_marker_turns_the_alarm_assertion_red(self):
        """The forbidden markers are the probe's alarm vocabulary. Remove the one that names the
        exact defect and a run in which it happened would still be green."""
        def edit(raw):
            raw["forbidden"] = [m for m in raw["forbidden"] if m != "### CHECKPOINT BYPASSED ###"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="CHECKPOINT BYPASSED"):
            TestTheM10BaseScenario(
            ).test_the_forbidden_markers_cover_the_ways_this_unit_gets_built_wrong(mutant)

    def test_running_a_case_the_listing_does_not_offer_turns_its_assertion_red(self):
        """A `--case` the scenario runs but `--list-cases` never offers is a command the generator
        can never legitimately compose — and a case name the probe may not even implement."""
        def edit(raw):
            listing = [c for c in raw["commands"] if c.get("run", "").endswith("--list-cases")][0]
            listing["expect_contains"] = [
                c for c in listing["expect_contains"]
                if c != "the-original-effect-grant-is-never-reused"
            ]

        mutant = _mutate(edit)
        cases = [c for c in mutant.commands if c.run.endswith("--list-cases")][0].expect_contains
        with pytest.raises(AssertionError, match="--list-cases does not offer"):
            TestTheM10Vocabulary(
            ).test_the_probe_case_commands_are_reachable_from_the_declared_case_list(
                mutant, list(cases)
            )

    def test_dropping_a_canonical_deliverable_turns_the_fixture_assertion_red(self):
        def edit(raw):
            raw["fixtures"] = [f for f in raw["fixtures"]
                               if f != "src/freight_recon/compensation.py"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="does not require"):
            TestTheM10BaseScenario().test_it_names_the_canonical_deliverables_as_fixtures(mutant)

    def test_removing_the_mutation_battery_turns_its_assertion_red(self):
        """Without it nothing proves the acceptance battery can fail at all."""
        def edit(raw):
            raw["commands"] = [c for c in raw["commands"]
                               if "mutate_phase6_compensation.py" not in c.get("run", "")]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="no mutation battery runs"):
            TestTheM10BaseScenario(
            ).test_it_carries_the_mutation_battery_and_the_acceptance_battery(mutant)

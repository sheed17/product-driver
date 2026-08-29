"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M8?

M8 is the Expectation: a durable commitment that something should be observed by a deadline, and the
one machine in Neyma whose whole job is to tell two silences apart. Entity §3 states the purpose in
terms — *to distinguish, honestly, "the thing never happened" (`OVERDUE`) from "we were not
watching" (`INDETERMINATE`)* — so the question this file answers is not "does the YAML parse" but
whether the whole loop can own the unit end to end without the founder standing in the middle of it.

The unit's whole character is three sentences, and every check below traces back to one of them:

    OVERDUE means it never came AND we can prove the channel was healthy over the window
    INDETERMINATE means the deadline passed AND we were blind — absent coverage is NOT health
    an Expectation OWES something; it does not AUTHORIZE anything

Not a timer. Not an SLA. Not an accusation until observability is proven. *We do not accuse a
counterparty of a failure that was ours* — and the single most likely way this unit gets built wrong
is that "no errors were logged" quietly reads as "everything was fine", which is `M-32`'s fail-closed
clause turned inside out.

Thirteen questions, each answered mechanically rather than by reading a document and agreeing with
it:

1.  does the M8 base scenario parse, does it hold the pieces the generator needs (deterministic
    operation, a closed mutation axis with the coverage and timezone axes this unit turns on,
    persisted-state oracles, regression anchors), and do the scenario and the task state the SAME
    contract;
2.  does every declared risk name a command that could actually emit the observation it requires —
    the `P6-D-run-20260825` mapping defect, refused ahead of time;
3.  does the scenario measure the DATABASE rather than the probe's narration for the invariants a
    green test suite can state while the database enforces none of them — above all the
    healthy-coverage CHECK, which is where this machine's honesty actually lives;
4.  does the task preserve the five recorded authority conflicts rather than resolving them;
5.  does the task get the SEAMS right — M8's `coverage_ref` points at a table the corpus names and
    nobody has built, three landed machines sit on the other side of its reads, and M9 owns the
    Exception every canonical sentence about `OVERDUE` and `EXPIRED` ends in;
6.  is the M8 command vocabulary safe, and actually visible to the generator rather than truncated
    out of the brief;
7.  can dynamic generation close an M8 coverage gap WITHOUT inventing a command, and is an invented
    one refused;
8.  is `P6-D46` still closed — canonical taxonomy only, no candidate lost to it, and the four counts
    still separable;
9.  is M8 scoped as `P6/M8` rather than as P6 phase completion, and can accepting it score a P6
    acceptance criterion or unlock P7 (it cannot);
10. is an integrated independent review OWED when the repository's own authority says so;
11. do grounded reviewer findings return to the SAME builder, and does a corrected tree get a FRESH
    reviewer, and does the run stop before M9;
12. does the founder summary explain M8's product impact in simple terms — and never contradict its
    own review ledger while doing it;
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

from neyma_product_driver.completion_auditor import AuditDecision
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.models import RunStatus
from neyma_product_driver.review_cycle import resolve_review_requirement
from neyma_product_driver.run_journal import RunJournal
from neyma_product_driver.scenario_generator import (
    MAX_RENDERED_COMMANDS,
    PLAN_SCHEMA,
)
from neyma_product_driver.scenario_plan import (
    REJECTED_CONTRACT,
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
    FakeUnit,
    ScriptedReasoner,
    raw_payload,
    raw_scenario,
    recorded_contract_probe,
)
from test_integrated_review import FakeBuilder, FakeReviewer, drive, refusing, supported
from test_scoped_completion import PhaseRepo

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DRIVER_ROOT / "scenarios"
M8_PATH = SCENARIOS_DIR / "p6_m8_expectation.yaml"
M8_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m8.md"
M8_TASK = M8_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match against this: the task is
#: a wrapped markdown document, and a phrase that happens to straddle a line break is not a phrase
#: the task failed to state.
M8_TASK_FLAT = " ".join(M8_TASK.split())
PROBE = ".venv/bin/python scripts/probe_phase6_expectation.py"
#: The `name:` the base scenario gives the bare probe run — the deterministic basic M8 operation,
#: and the only check in the file that drives the machine and narrates what it saw.
PROBE_CHECK = "drive the Expectation machine through a brokerage narrative, and attack it"

#: A persisted-state command the base scenario already carries, so a generated case that reuses it is
#: choosing an approved oracle rather than authoring one.
STATE_ORACLE = next(
    check.command
    for check in load_scenario(M8_PATH).expect_state
    if "schema_readiness_problems" in check.command
)

#: The canonical M8 deliverables. A different name is a scenario failure, not a style preference —
#: the permanent scenario looks for exactly these.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/expectation.py",
    "src/freight_recon/migrations/phase6_expectations.py",
    "eval/tests/test_phase6_expectation.py",
    "scripts/probe_phase6_expectation.py",
    "scripts/mutate_phase6_expectation.py",
)

#: The six canonical expectation states (registry §4 / M8, target spec §12.8). Not five, not seven.
STATES: tuple[str, ...] = (
    "RAISED",
    "DISCHARGED",
    "OVERDUE",
    "INDETERMINATE",
    "CANCELLED",
    "EXPIRED",
)

#: Terminal, per machine §8. A transition OUT of one of these is illegal.
TERMINAL_STATES: tuple[str, ...] = ("DISCHARGED", "CANCELLED", "EXPIRED")

#: Non-terminal and HUMAN-OWNED, per machine §9 — each carries a named `owner_id`, which is what
#: `AC-SAFE-028` makes a database fact rather than a policy.
HUMAN_OWNED_STATES: tuple[str, ...] = ("OVERDUE", "INDETERMINATE")

#: States a build session might reach for out of tidiness, and that the corpus says do not exist.
#: `TIMED_OUT` and `STALE` are first because each of them means "the deadline passed" WITHOUT saying
#: whether anyone was watching — which is precisely the honesty collapse this machine exists to
#: prevent, arriving with a reasonable-sounding name. `RESOLVED` is M9 Exception's vocabulary and the
#: likeliest import; `SUPERSEDED` is what entity §24 says a re-versioned deadline is NOT.
FORBIDDEN_STATES: tuple[str, ...] = (
    "TIMED_OUT",
    "STALE",
    "RESOLVED",
    "SUPERSEDED",
    "MISSED",
    "LATE",
    "CLOSED",
    "PENDING",
)

#: The canonical transition ids. The task must require these rows, with these ids, rather than an
#: alternative lifecycle that "achieves the same thing". `AC-MACH-801..808` — eight, not seven.
TRANSITIONS: tuple[str, ...] = (
    "EX-1",
    "EX-2",
    "EX-3",
    "EX-3i",
    "EX-4",
    "EX-5",
    "EX-6",
    "EX-7",
)

#: The seven registered F8 event contracts. `event_contracts_data.json` carries exactly these seven,
#: and `events/registry.md` is by its own header THE SOLE CANONICAL LIST — so an eighth
#: `Expectation*` name is defective by the registry's own definition.
F8_EVENTS: tuple[str, ...] = (
    "ExpectationRaised",
    "ExpectationDischarged",
    "ExpectationOverdue",
    "ExpectationIndeterminate",
    "ExpectationReVersioned",
    "ExpectationCancelled",
    "ExpectationExpired",
)

#: Names a build session invents when it wants an Expectation to stop being an obligation.
FORBIDDEN_EVENTS: tuple[str, ...] = (
    "ExpectationTimedOut",
    "ExpectationMissed",
    "ExpectationClosed",
    "ExpectationReopened",
    "ExpectationSuperseded",
)

#: Nine `risk_category` values in the shape `P6-D46`'s real nine had: each a plausible, well-meant
#: DESCRIPTION OF A SPECIFIC DEFECT rather than a member of a closed family vocabulary — which is
#: what an unconstrained `{"type": "string"}` schema invites a model to write. These are M8's.
M8_UNREADABLE_CATEGORIES: tuple[str, ...] = (
    "blind-window-became-overdue",
    "absent-coverage-treated-as-healthy",
    "two-live-expectations",
    "late-arrival-refused",
    "silent-expiry",
    "deadline-evaluated-in-utc",
    "timer-lost-across-restart",
    "replay-read-the-live-channel",
    "m5-seam-rewritten",
)


def _local_vocabulary() -> list[str]:
    """The `--case` entries the local driver config approves, if it exists.

    Read from the file rather than through `load_config`, because this must work on a checkout that
    has no `driver.config.yaml` at all — the vocabulary is then simply absent and the tests that need
    it skip.
    """
    local = DRIVER_ROOT / "driver.config.yaml"
    if not local.exists():
        return []
    raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    return list((raw.get("scenario_generation") or {}).get("approved_commands") or [])


@pytest.fixture(scope="module")
def m8():
    return load_scenario(M8_PATH)


@pytest.fixture(scope="module")
def cases(m8) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m8.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m8) -> list[str]:
    listing = [c for c in m8.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def state_checks(m8) -> dict[str, list[str]]:
    return {check.name: list(check.contains) for check in m8.expect_state}


# --------------------------------------------------------------------------
# 1. The base scenario, and its contract with the task
# --------------------------------------------------------------------------


class TestTheM8BaseScenario:
    def test_it_parses_and_is_a_dark_p6_backend_scenario(self, m8):
        assert m8.name == "p6_m8_expectation"
        assert m8.phase == "P6"
        assert m8.mode == "backend"
        # M8 ships dark: no service, no HTTP surface, no browser, and above all no tracking or SLA
        # surface — the product form of this unit is a LIVE "WHAT IS LATE" VIEW, and that view is
        # precisely the thing that must not arrive with the engine primitive.
        assert not m8.services and not m8.requests and m8.browser is None
        assert not m8.app_url

    def test_it_requires_the_canonical_deliverables_to_exist(self, m8):
        """A run against a repository where M8 does not exist yet must not be able to report a
        verified M8."""
        for path in DELIVERABLES:
            assert path in m8.fixtures, f"{path} is not required to exist"

    def test_the_probe_is_approved_bare_so_every_case_tail_is_reachable(self, m8):
        """The whole `--case` interface rests on this one entry.

        Approval matches by prefix, so approving the bare probe approves every argument tail that
        composes no shell. Approving only `probe.py --list-cases` would approve exactly that string
        and nothing else, and the generator would have no focused entry point at all.
        """
        assert any(c.run == PROBE for c in m8.commands), (
            "the bare probe invocation is missing; without it a generated "
            f"'{PROBE} --case X' is not an argument tail of any approved entry"
        )

    def test_it_asserts_a_risk_family_for_every_canonical_obligation(self, cases):
        """One family per canonical obligation, checked by name.

        This list is the contract between `tasks/neyma_p6_m8.md` and this file; a family missing from
        either is a family the generator cannot reach and the builder was never asked to build.
        """
        required = {
            # EX-1 — the raise, the declared channel, the key, the timezone
            "raise-creates-raised-with-a-declared-channel",
            "an-expectation-cannot-be-raised-without-expected-source",
            "an-expectation-cannot-be-raised-without-a-deadline",
            "raise-stores-deadline-in-utc-and-retains-the-originating-timezone",
            "raise-and-its-durable-timer-are-one-commit",
            "the-expectation-key-is-tenant-subject-and-expected-type",
            "at-most-one-live-raised-expectation-per-key",
            "concurrent-raises-produce-one-live-expectation",
            "a-model-may-propose-an-expectation-but-not-set-the-deadline",
            "a-model-cannot-assert-coverage-health",
            "counterparty-content-cannot-declare-the-channel-healthy",
            # EX-2 — the bound Observation discharges
            "bound-observation-discharges-the-expectation",
            "discharge-records-the-discharge-observation-id",
            "an-unbound-observation-cannot-discharge",
            "a-wrong-subject-observation-cannot-discharge",
            "a-wrong-tenant-observation-cannot-discharge",
            # EX-3 / EX-3i — the honesty split, which is the unit
            "healthy-coverage-and-a-missed-deadline-is-overdue",
            "overdue-requires-a-healthy-coverage-ref",
            "overdue-without-healthy-coverage-is-structurally-impossible",
            "a-blind-window-is-indeterminate-not-overdue",
            "unknown-coverage-is-indeterminate-not-overdue",
            "absent-coverage-is-not-health",
            "partial-coverage-over-the-window-is-not-health",
            "indeterminate-records-the-coverage-gap",
            "confidence-cannot-turn-indeterminate-into-overdue",
            "overdue-and-indeterminate-carry-a-named-human-owner",
            "an-ownerless-human-owned-state-is-impossible",
            # EX-4 — the late arrival
            "a-late-arrival-discharges-an-overdue-expectation",
            "a-late-arrival-discharges-an-indeterminate-expectation",
            "late-discharge-is-marked-late",
            "late-evidence-is-never-rejected-because-the-deadline-passed",
            # EX-5 — the amendment
            "deadline-change-re-versions-the-expectation",
            "deadline-history-is-retained",
            "an-amendment-is-not-a-supersession",
            "the-subject-and-expected-type-cannot-be-mutated",
            "a-stale-version-cannot-overwrite-newer-state",
            # EX-6 — cancellation, and the from-set INDETERMINATE is not in
            "reason-disappeared-cancels-a-raised-expectation",
            "reason-disappeared-cancels-an-overdue-expectation",
            "cancelling-an-indeterminate-expectation-is-illegal",
            "a-cancelled-expectation-is-retained-never-deleted",
            # EX-7 — expiry, explicit and never silent
            "terminal-age-expires-an-overdue-expectation",
            "terminal-age-expires-an-indeterminate-expectation",
            "a-raised-expectation-never-expires",
            "expiry-is-never-silent",
            "no-sweep-or-reaper-closes-an-expectation",
            "there-is-no-timed-out-stale-or-resolved-state",
            # §16 precedence
            "discharge-beats-overdue-when-they-race",
            "discharge-beats-indeterminate-when-they-race",
            # durable timers, restart, atomicity
            "the-deadline-is-a-durable-timer-not-a-sleep",
            "restart-re-fires-the-deadline-timer",
            "restart-preserves-the-raised-expectation",
            "restart-after-overdue-reaches-the-canonical-state",
            "a-redelivered-timer-is-a-no-op",
            "timer-coverage-read-and-state-are-one-commit",
            "persistence-failure-rolls-back-the-deadline-decision",
            "state-and-event-co-commit",
            # replay
            "replay-reconstructs-overdue-from-the-recorded-coverage",
            "replay-reconstructs-indeterminate-from-the-recorded-coverage",
            "replay-does-not-read-the-current-channel-state",
            "replay-creates-no-new-authority-and-no-effect",
            # F-25 — facility-local time and DST
            "an-appointment-window-is-evaluated-in-facility-local-time",
            "a-dst-boundary-does-not-move-the-deadline",
            "a-window-evaluated-in-utc-instead-of-facility-local-is-wrong",
            # gate semantics and the brake
            "m8-mints-no-gate-decision",
            "an-expectation-owes-it-does-not-authorize",
            "an-undischarged-expectation-makes-a-field-unknown-never-consistent",
            "discharge-and-indeterminate-detection-continue-under-a-brake",
            "a-brake-never-fabricates-overdue-state",
            # [C-1] — tenancy
            "tenant-isolation",
            "cross-tenant-identical-expectation-key",
            "cross-tenant-observation-cannot-discharge",
            "cross-tenant-coverage-record-fails-closed",
            "cross-tenant-owner-fails-closed",
            # GR-2 / GR-3 / GR-4 — concurrency, idempotency, the database
            "occ-on-expectation-version",
            "inbox-idempotency",
            "database-invariants",
            "malformed-expectation-fails-closed",
            # the seams
            "the-m5-observation-machine-is-not-rewritten",
            "the-m3-awaiting-observation-seam-is-unchanged",
            "the-m7-conflict-machine-is-not-rewritten",
            "an-overdue-expectation-is-not-automatically-a-conflict",
            "m9-m10-m11-and-m12-are-not-built",
        }
        missing = sorted(required - set(cases))
        assert not missing, f"risk families the scenario never asserts exist: {missing}"

    def test_it_declares_a_bounded_mutation_axis(self, dimensions):
        """Without this the M8 possibility space is a list of fixed points.

        M8 ships dark, so there is no service and no HTTP surface, and `parallel_requests` — the
        executor's only concurrency primitive — is unavailable. Ordering, concurrency, timing,
        duplication, crash and replay variation are reachable through the probe's arguments or not at
        all. See docs/SCENARIO-SPACE.md, gap G2.
        """
        for axis in ("--concurrency", "--delay-ms", "--repeat", "--tenants", "--age-ms",
                     "--coverage", "--timezone", "--confidence", "--seed", "--inject"):
            assert axis in dimensions, f"the axis {axis} is never asserted to exist"
        for fault in (
            "raise", "missing-expected-source", "missing-deadline", "missing-key",
            "duplicate-raise", "concurrent-raise",
            "bound-discharge", "unbound-discharge", "wrong-subject-discharge",
            "wrong-tenant-discharge", "late-discharge", "reject-late",
            "deadline-passed", "coverage-healthy", "coverage-down", "coverage-unknown",
            "coverage-absent", "coverage-partial",
            "model-set-coverage", "counterparty-coverage", "confidence-overdue",
            "overdue-without-coverage", "ownerless-overdue",
            "deadline-change", "subject-mutation", "type-mutation", "stale-version",
            "reason-disappeared", "cancel-indeterminate", "terminal-age", "expire-raised",
            "silent-expiry", "sweep-close",
            "discharge-vs-deadline-race", "restart-before-deadline", "restart-after-overdue",
            "replay", "replay-from-live-channel", "dst-boundary", "utc-window",
            "occ-expectation", "cross-tenant-observation", "cross-tenant-coverage",
            "cross-tenant-owner", "malformed-expectation", "persistence-failure",
            "redelivered-timer", "brake", "gate-mint", "reorder-stream",
        ):
            assert fault in dimensions, f"the fault {fault!r} is never asserted to exist"

    def test_the_coverage_axis_exists_because_it_is_the_whole_unit(self, dimensions, cases):
        """The one axis without which M8 cannot be measured at all.

        `M-32` and entity §36: the SAME missed deadline is `OVERDUE` over a healthy window and
        `INDETERMINATE` over a blind one, and ### no coverage record at all means `INDETERMINATE`.
        An axis a generator can set to `healthy`, `down`, `unknown`, `absent` and `partial` over a
        case that must produce a DIFFERENT canonical state for each is what makes that a measurement
        rather than a belief — and `absent` is the value that decides whether "no errors were logged"
        was quietly read as "everything was fine".
        """
        assert "--coverage" in dimensions
        for value in ("coverage-healthy", "coverage-down", "coverage-unknown",
                      "coverage-absent", "coverage-partial"):
            assert value in dimensions, f"the coverage value {value!r} is unreachable"
        assert "healthy-coverage-and-a-missed-deadline-is-overdue" in cases
        assert "a-blind-window-is-indeterminate-not-overdue" in cases
        assert "unknown-coverage-is-indeterminate-not-overdue" in cases
        assert "absent-coverage-is-not-health" in cases
        assert "partial-coverage-over-the-window-is-not-health" in cases

    def test_the_timezone_axis_exists_so_the_dst_case_is_reachable(self, dimensions, cases):
        """`F-25`, and the one acceptance criterion that cannot be faked by arithmetic.

        Target spec §12.8: store instants in UTC, RETAIN the originating business timezone, and
        evaluate facility and appointment windows in the FACILITY's local timezone — *a 17:00
        delivery appointment in Denver is not 17:00 UTC, and a DST boundary is a real freight event*.
        Machine §15 makes a deadline evaluated in the wrong timezone an ILLEGAL transition, so an
        axis the generator can point at a real zone, over a case that must cross a DST boundary and
        must still land where the facility says, is what makes that a measurement.
        """
        assert "--timezone" in dimensions
        assert "dst-boundary" in dimensions
        assert "utc-window" in dimensions
        assert "an-appointment-window-is-evaluated-in-facility-local-time" in cases
        assert "a-dst-boundary-does-not-move-the-deadline" in cases
        assert "a-window-evaluated-in-utc-instead-of-facility-local-is-wrong" in cases

    def test_the_age_axis_exists_so_the_deadline_and_the_terminal_age_are_both_reachable(
        self, dimensions, cases
    ):
        """Two `T`-triggered thresholds, one axis. `EX-3`/`EX-3i` fire at the deadline and `EX-7` at
        the terminal age past `OVERDUE`/`INDETERMINATE` — and `EX-7`'s from-set excludes `RAISED`, so
        a `RAISED` expectation wound forward forever must still never expire."""
        assert "--age-ms" in dimensions
        assert "deadline-passed" in dimensions
        assert "terminal-age" in dimensions
        assert "expire-raised" in dimensions
        assert "a-raised-expectation-never-expires" in cases
        assert "terminal-age-expires-an-overdue-expectation" in cases

    def test_confidence_is_an_axis_so_the_negative_control_is_reachable(self, dimensions, cases):
        """The one axis whose whole purpose is to change NOTHING.

        `GR-8`: `MODEL_INFERRED` never gates a consequential transition at any confidence, and
        confidence is not a guard input. An axis the generator can turn to 1.0, over a case where a
        blind window must STILL be `INDETERMINATE`, is what makes that a measurement rather than a
        belief — because "we are 99% sure the channel was fine" is exactly the sentence that turns an
        admission of blindness into an accusation.
        """
        assert "--confidence" in dimensions
        assert "confidence-overdue" in dimensions
        assert "confidence-cannot-turn-indeterminate-into-overdue" in cases
        assert "a-model-cannot-assert-coverage-health" in cases
        assert "counterparty-content-cannot-declare-the-channel-healthy" in cases

    def test_the_mutation_axis_has_a_negative_control(self, m8):
        """A vocabulary that accepts anything is fuzzing in a costume."""
        negative = [c for c in m8.commands if "--inject not-a-real-fault" in c.run]
        assert negative, "nothing proves the fault vocabulary is actually closed"
        assert negative[0].expect_exit_code == 2, "a refusal must be a non-zero exit"
        assert "unknown fault" in negative[0].expect_contains
        assert "Traceback (most recent call last)" in m8.forbidden

    @pytest.mark.parametrize(
        "fault,section",
        [
            ("reopen-expectation", "Reopening rules. N/A"),
            ("correct-expectation", "Correction rules. N/A"),
            ("supersede-expectation", "a re-versioned deadline is not a supersession"),
        ],
    )
    def test_a_fault_the_corpus_calls_n_a_is_refused_as_unknown(self, m8, fault, section):
        """The three M8-specific negative controls, each backed by an explicit canonical `N/A`.

        Entity §27 says reopening is `N/A`, §23 says correction is `N/A` (*"a wrong expectation is
        `CANCELLED`, not corrected"*), and §24 says a re-versioned deadline is not a supersession —
        with no `SUPERSEDED` state in registry §4 and no `ExpectationSuperseded` event registered
        anywhere. A probe that ACCEPTED any of the three would be producing passing evidence for a
        transition the corpus states does not exist — the same shape as M4's refused `unfreeze`, M5's
        refused `expire-observation`, M6's refused `expire-claim` and M7's refused `expire-conflict`.
        """
        refusal = [c for c in m8.commands if f"--inject {fault}" in c.run]
        assert refusal, f"nothing refuses a {fault} fault"
        assert refusal[0].expect_exit_code == 2
        assert "unknown fault" in refusal[0].expect_contains
        assert section in M8_TASK or section in M8_TASK_FLAT, (
            f"the task never states the canonical clause behind refusing {fault!r}"
        )

    def test_the_illegal_faults_are_in_the_vocabulary_rather_than_refused_as_unknown(
        self, m8, dimensions
    ):
        """The distinction a machine that owns its own illegal set has to make.

        Machine §15 names four shapes as ILLEGAL TRANSITIONS by hand — `OVERDUE` without a
        healthy-coverage `coverage_ref`, a deadline evaluated in the wrong timezone, a duplicate
        `RAISED`, and a silent expiry — and `EX-6`'s and `EX-7`'s from-sets make two more. So the
        MACHINE must be seen to refuse them under `GR-1`, raising and persisting nothing. A fault
        refused as *unknown* and a fault refused as *illegal* are two different proofs, and M8 owes
        both.
        """
        refused_as_unknown = {
            c.run.split("--inject ", 1)[1].split()[0]
            for c in m8.commands
            if "--inject " in c.run and c.expect_exit_code == 2
        }
        for illegal in ("overdue-without-coverage", "silent-expiry", "utc-window",
                        "expire-raised", "cancel-indeterminate", "duplicate-raise", "sweep-close"):
            assert illegal in dimensions, f"the illegal shape {illegal!r} is not reachable at all"
            assert illegal not in refused_as_unknown, (
                f"{illegal!r} is refused as an UNKNOWN fault. The corpus DEFINES it, as an ILLEGAL "
                "transition — so the machine owes a GR-1 refusal for it, not the argument parser"
            )

    def test_it_carries_regression_anchors_for_every_layer_m8_builds_on(self, m8):
        """M8 adds two tables and edits `schema.py`, so every layer that reads a canonical database
        can be broken from inside it."""
        runs = " ".join(c.run for c in m8.commands)
        for anchor in (
            "test_phase3_witness.py",                 # P3, the kernel M8 feeds and must not disturb
            "test_import_gate.py",                    # P4, the boundary M8 must not widen
            "test_phase5_event_transport.py",         # P5, the transport M8 rides
            "test_p5_durable_timers.py",              # P5, the substrate EX-3/EX-3i/EX-7 ride
            "test_phase6_work_item.py",               # M1, the owner_id/tenant_humans precedent
            "test_phase6_pipeline_instance.py",       # M2
            "test_phase6_external_effect.py",         # M3, the AWAITING_OBSERVATION seam
            "test_phase6_approval.py",                # M4, the AP-3 durable-timer precedent
            "test_phase6_observation.py",             # M5, whose rows EX-2 and EX-4 read
            "test_phase6_identity_binding_claim.py",  # M6
            "test_phase6_conflict.py",                # M7, which an overdue expectation is NOT
        ):
            assert anchor in runs, f"{anchor} is never re-run against M8's change"
        # M8 emits seven already-registered F8 names and mints none of its own, so the canonical
        # event-contract guard is load-bearing here rather than incidental: a builder that invents
        # `ExpectationTimedOut`, or emits `ExpectationOverdue` with no `coverage_ref`, fails there.
        assert "test_p5_event_contracts.py" in runs
        assert "test_p5_canonical_event_mint.py" in runs

    def test_the_durable_timer_suite_is_treated_as_load_bearing_rather_than_incidental(self, m8):
        """`EX-3`, `EX-3i` and `EX-7` are ALL `T`-triggered, which no earlier P6 unit's core
        lifecycle was. `AP-3` is one timer on a side path; M8's deadline IS the machine. Machine §37
        says *durable timers, not sweeps*, so P5's timer suite is the regression anchor this unit
        rests on most directly."""
        timers = [c for c in m8.commands if "test_p5_durable_timers.py" in c.run]
        assert timers, "P5's durable-timer suite is never re-run"
        assert "durable timers" in timers[0].name.lower(), (
            "the anchor's name does not say why the timer substrate is being re-run, so a future "
            "edit cannot tell it apart from an incidental P5 anchor"
        )

    def test_it_re_runs_the_neighbouring_units_own_oracles_rather_than_trusting_m8(self, m8):
        """"M8 broke nothing" is a claim about M5's, M6's and M7's behaviour.

        M5 in particular: `EX-2` and `EX-4` READ its rows, so M5 is the first unit with a real reason
        to be edited by M8 — and the first that must not be.
        """
        runs = [c.run for c in m8.commands]
        assert ".venv/bin/python scripts/probe_phase6_observation.py" in runs
        assert ".venv/bin/python scripts/probe_phase6_identity_binding_claim.py" in runs
        assert ".venv/bin/python scripts/probe_phase6_conflict.py" in runs

    def test_the_mutation_battery_runs(self, m8):
        runs = " ".join(c.run for c in m8.commands)
        assert "mutate_phase6_expectation.py" in runs
        assert "mutants caught" in m8.expect_visible

    def test_it_refuses_the_failures_m8_exists_to_prevent(self, m8):
        """Each forbidden marker is a sentence printed only when the thing M8 exists to prevent has
        just happened. Every load-bearing mutant in the battery has one."""
        for marker in (
            "### OVERDUE WITHOUT HEALTHY COVERAGE ###",
            "### ABSENT COVERAGE TREATED AS HEALTHY ###",
            "### UNKNOWN COVERAGE BECAME OVERDUE ###",
            "### BLIND WINDOW BECAME OVERDUE ###",
            "### PARTIAL COVERAGE TREATED AS HEALTHY ###",
            "### CONFIDENCE TURNED INDETERMINATE INTO OVERDUE ###",
            "### MODEL SET COVERAGE TRUTH ###",
            "### COUNTERPARTY ASSERTED CHANNEL HEALTH ###",
            "### EXPECTATION RAISED WITHOUT A DECLARED CHANNEL ###",
            "### EXPECTATION RAISED WITHOUT A DEADLINE ###",
            "### TWO LIVE RAISED EXPECTATIONS FOR ONE KEY ###",
            "### UNBOUND OBSERVATION DISCHARGED ###",
            "### WRONG-SUBJECT OBSERVATION DISCHARGED ###",
            "### WRONG-TENANT OBSERVATION DISCHARGED ###",
            "### LATE ARRIVAL REFUSED ###",
            "### LATE DISCHARGE LOST ITS late MARKER ###",
            "### DEADLINE AMENDED WITHOUT RE-VERSIONING ###",
            "### DEADLINE HISTORY LOST ###",
            "### SUBJECT SILENTLY MUTATED ###",
            "### EXPECTED TYPE SILENTLY MUTATED ###",
            "### STALE VERSION OVERWROTE NEWER STATE ###",
            "### INDETERMINATE SILENTLY CANCELLED ###",
            "### EXPECTATION SILENTLY EXPIRED ###",
            "### EXPECTATION DELETED ###",
            "### RAISED EXPECTATION EXPIRED ###",
            "### SWEEP CLOSED AN EXPECTATION ###",
            "### REAPER DELETED AN EXPECTATION ###",
            "### UNREGISTERED STATE MINTED ###",
            "### OVERDUE BEAT A DISCHARGE ###",
            "### IN-MEMORY SLEEP DECIDED THE DEADLINE ###",
            "### TIMER LOST ACROSS RESTART ###",
            "### HALF-DECIDED DEADLINE PERSISTED ###",
            "### EVENT WITHOUT ITS STATE ###",
            "### STATE WITHOUT ITS EVENT ###",
            "### REPLAY READ THE LIVE CHANNEL ###",
            "### REPLAY FLIPPED OVERDUE AND INDETERMINATE ###",
            "### REPLAY MINTED AUTHORITY ###",
            "### DOWNSTREAM EFFECT DURING REPLAY ###",
            "### WINDOW EVALUATED IN UTC ###",
            "### DST BOUNDARY MOVED THE DEADLINE ###",
            "### M8 MINTED A GATE DECISION ###",
            "### EXPECTATION AUTHORIZED AN ACTION ###",
            "### BRAKE FABRICATED OVERDUE ###",
            "### BRAKE STOPPED INDETERMINATE DETECTION ###",
            "### CROSS-TENANT OBSERVATION ACCEPTED ###",
            "### CROSS-TENANT COVERAGE ACCEPTED ###",
            "### OWNERLESS HUMAN-OWNED STATE CREATED ###",
            "### M5 OBSERVATION ROW REWRITTEN BY M8 ###",
            "### M3 AWAITING_OBSERVATION SEAM REWRITTEN ###",
            "### M7 CONFLICT ROW REWRITTEN BY M8 ###",
            "### EXCEPTION FABRICATED ###",
            "### M9 EVENT MINTED ###",
        ):
            assert marker in m8.forbidden, f"{marker} is not a failure condition"

    def test_every_mutation_battery_mutant_has_a_forbidden_marker_behind_it(self, m8):
        """The mutation battery is only as good as the sentence that turns red under it.

        Each pair below is (the mutant the task requires, the marker the probe prints when that
        defect is live). A mutant with no marker is a mutant nothing observes.
        """
        for mutant_phrase, marker in (
            ("`INDETERMINATE` removed from the state vocabulary",
             "### BLIND WINDOW BECAME OVERDUE ###"),
            ("`OVERDUE` allowed without a healthy `coverage_ref`",
             "### OVERDUE WITHOUT HEALTHY COVERAGE ###"),
            ("absent coverage treated as healthy", "### ABSENT COVERAGE TREATED AS HEALTHY ###"),
            ("partial coverage treated as healthy", "### PARTIAL COVERAGE TREATED AS HEALTHY ###"),
            ("the declared `expected_source` requirement dropped",
             "### EXPECTATION RAISED WITHOUT A DECLARED CHANNEL ###"),
            ("the live `expectation_key` unique index dropped",
             "### TWO LIVE RAISED EXPECTATIONS FOR ONE KEY ###"),
            ("the tenant weakened out of the uniqueness boundary",
             "### CROSS-TENANT COVERAGE ACCEPTED ###"),
            ("an unbound Observation allowed to discharge",
             "### UNBOUND OBSERVATION DISCHARGED ###"),
            ("late discharge forbidden", "### LATE ARRIVAL REFUSED ###"),
            ("a timer allowed to resolve silently", "### EXPECTATION SILENTLY EXPIRED ###"),
            ("the deadline history dropped", "### DEADLINE HISTORY LOST ###"),
            ("the OCC predicate dropped", "### STALE VERSION OVERWROTE NEWER STATE ###"),
            ("a DST case evaluated in UTC instead of facility-local",
             "### WINDOW EVALUATED IN UTC ###"),
            ("the owner requirement dropped from the human-owned states",
             "### OWNERLESS HUMAN-OWNED STATE CREATED ###"),
            ("model-set coverage truth accepted", "### MODEL SET COVERAGE TRUTH ###"),
            ("replay recomputing from the current channel state",
             "### REPLAY READ THE LIVE CHANNEL ###"),
            ("a sweep or reaper introduced", "### SWEEP CLOSED AN EXPECTATION ###"),
            ("an M9/M10/M11 table or event created", "### M9 EVENT MINTED ###"),
            ("M8 made a gate-decision minter", "### M8 MINTED A GATE DECISION ###"),
        ):
            assert mutant_phrase in M8_TASK_FLAT, (
                f"the task never requires the mutant {mutant_phrase!r}"
            )
            assert marker in m8.forbidden, f"the mutant {mutant_phrase!r} has no forbidden marker"

    def test_the_task_file_and_the_scenario_agree_on_the_contract(self, m8, cases, dimensions):
        """The two halves of one contract, checked against each other.

        A case the scenario asserts exists but the task never asks for is a case the builder is being
        failed on without being told. A literal the scenario requires but the task never states is
        the same defect one layer down.
        """
        for case in cases:
            assert case in M8_TASK, f"the scenario asserts case {case!r}; the task never names it"
        for dimension in dimensions:
            assert dimension in M8_TASK, (
                f"the scenario asserts dimension {dimension!r}; the task never names it"
            )
        for literal in m8.expect_visible:
            assert literal in M8_TASK, (
                f"the scenario requires the literal {literal!r}; the task never states it"
            )
        for marker in m8.forbidden:
            if marker.startswith("### ") and marker.endswith(" ###"):
                assert marker in M8_TASK, (
                    f"the scenario forbids {marker!r}; the task never names it"
                )
        for path in DELIVERABLES:
            assert path in M8_TASK, f"the scenario requires {path}; the task never names it"

    def test_the_task_states_the_canonical_machine_rather_than_a_generic_feature(self):
        for state in STATES:
            assert state in M8_TASK, f"the canonical state {state} is never named"
        for transition in TRANSITIONS:
            assert transition in M8_TASK, f"the canonical transition {transition} is never named"
        for event in F8_EVENTS:
            assert event in M8_TASK, f"the F8 contract {event} is never named"
        assert "Six states" in M8_TASK, "the state count is never stated"
        assert "Do not add a seventh" in M8_TASK
        assert "Eight rows." in M8_TASK, "the transition count is never stated"
        assert "AC-MACH-801..808" in M8_TASK
        # `RESOLVED` is M9 Exception's, and `TIMED_OUT`/`STALE` are the two a build session invents.
        for forbidden in FORBIDDEN_STATES:
            assert forbidden in M8_TASK, f"the task never warns off the {forbidden} state"
        for forbidden in FORBIDDEN_EVENTS:
            assert forbidden in M8_TASK, f"the task never warns off the {forbidden} event"

    def test_the_task_reads_the_from_sets_literally(self):
        """Three from-sets are places a build session widens the table without noticing.

        `EX-6`'s is `{RAISED, OVERDUE}` — cancelling an `INDETERMINATE` is ILLEGAL, not a
        convenience. `EX-7`'s is `{OVERDUE, INDETERMINATE}` — a `RAISED` expectation never expires.
        `EX-4`'s is `{OVERDUE, INDETERMINATE}` and UNCONDITIONAL — the POD that arrives in month 4 is
        still a POD.
        """
        assert "`EX-6`'s from-set is `{RAISED, OVERDUE}`. `INDETERMINATE` IS NOT IN IT." in M8_TASK_FLAT
        assert "`EX-7`'s from-set is `{OVERDUE, INDETERMINATE}`. A `RAISED` Expectation NEVER EXPIRES." in M8_TASK_FLAT
        assert "`EX-4`'s from-set is `{OVERDUE, INDETERMINATE}` and it is UNCONDITIONAL." in M8_TASK_FLAT
        assert "do not add an `EX-6i`" in M8_TASK_FLAT

    def test_the_task_forces_the_authority_to_be_read_first(self):
        for source in (
            "PRODUCT.md",
            "CLAUDE.md",
            "docs/implementation/CURRENT.md",
            "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
            "docs/specifications/entities/11-expectation.md",
            "docs/specifications/state-machines/08-expectation.machine.md",
            "docs/specifications/state-machines/registry.md",
            "docs/specifications/events/08-expectation-events.md",
            "docs/specifications/events/registry.md",
            "docs/specifications/events/14-audit-security-events.md",
            "docs/architecture/target-system-specification.md",
            "docs/architecture/decisions/ADR-006-verification-and-unknown-outcomes.md",
            "docs/architecture/decisions/ADR-002-state-classes-and-lineage.md",
            "docs/specifications/entities/00-conventions.md",
            "docs/specifications/acceptance/foundational-machine-acceptance.md",
            "docs/specifications/acceptance/platform-safety-acceptance.md",
            "src/freight_recon/checkpoint.py",
            "src/freight_recon/observation.py",
            "src/freight_recon/external_effect.py",
            "src/freight_recon/conflict.py",
        ):
            assert source in M8_TASK, f"{source} is never named as authority"
        assert "event_timers.py" in M8_TASK, (
            "the durable-timer substrate EX-3/EX-3i/EX-7 all ride is never named as authority"
        )
        assert "the specification wins and you say so" in M8_TASK_FLAT
        assert "REPORT THE CONFLICT" in M8_TASK

    def test_the_task_states_the_honesty_invariant(self):
        """The sentence the entity spends forty-five points defending."""
        assert "WE DO NOT ACCUSE A COUNTERPARTY OF A FAILURE THAT WAS OURS." in M8_TASK
        assert "M-32" in M8_TASK
        assert "I8" in M8_TASK
        assert "F-14" in M8_TASK
        for state in HUMAN_OWNED_STATES:
            assert state in M8_TASK
        assert (
            "THE ABSENCE OF A COVERAGE RECORD IS NOT HEALTH" in M8_TASK
        ), (
            "the task never says that absent coverage is not health, which is the single most "
            "likely way this unit gets built wrong"
        )
        assert "it fails toward blindness" in M8_TASK_FLAT

    def test_the_task_states_what_an_expectation_is_not(self):
        """Entity §4's list, and the distinction the whole unit rests on."""
        assert "AN EXPECTATION IS NOT A BARE TIMER AND NOT AN SLA" in M8_TASK
        assert "NOT A GATE" in M8_TASK
        assert "it OWES, it does not AUTHORIZE" in M8_TASK_FLAT
        assert "NOT AN ACCUSATION UNTIL OBSERVABILITY IS PROVEN" in M8_TASK.upper()


# --------------------------------------------------------------------------
# 2. Every declared risk is mapped to a command that can actually prove it
# --------------------------------------------------------------------------


#: The five literals that say M8 stopped where it was told to stop, and that no landed unit was
#: edited to get there. They are M8's own narration: `tasks/neyma_p6_m8.md` states them verbatim to
#: the builder as strings the M8 PROBE must print, and the probe is the only command in this scenario
#: that runs the machine and narrates what it found. No pytest anchor prints them, because none of
#: them runs M8's story.
DARK_POSTURE_LITERALS = (
    "THE M5 OBSERVATION MACHINE IS UNCHANGED",
    "THE M3 AWAITING_OBSERVATION SEAM IS UNCHANGED",
    "THE M7 CONFLICT MACHINE IS UNCHANGED",
    "AN OVERDUE EXPECTATION IS NOT AUTOMATICALLY A CONFLICT",
    "THE M9, M10, M11 AND M12 MACHINES ARE NOT BUILT",
)


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
    """Claims that require a dark-posture literal but do NOT name the probe that emits it.

    Returned rather than asserted so the same predicate can be run against a deliberately broken
    copy of the scenario in section 13 — a guard never seen to fail is a decoration.
    """
    broken: list[tuple[str, list[str]]] = []
    for claim in scenario.verifies:
        needed = [lit for lit in DARK_POSTURE_LITERALS if lit in claim.observations]
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
    generated cases on the M7 run. `Scenario._claims_name_a_check_that_can_emit_them` now refuses the
    statically decidable half at load time. The residue is free-form narration, which nothing can
    attribute by reading YAML, so it is pinned here for the literals whose producer this repository
    actually knows.
    """

    def test_the_regression_claim_names_the_probe_that_proves_the_seams_are_intact(self, m8):
        """The M8-owned half of the regression claim: M5 unchanged, M3 unchanged, M7 unchanged, an
        overdue Expectation is not a Conflict, no neighbouring machine built. The probe is the
        command that observes it from inside M8's own story, so the claim must name the probe."""
        regression = [c for c in m8.verifies if c.risk_category == "regression"]
        assert regression, "the M8 scenario no longer declares a regression claim"
        claim = regression[0]
        for literal in DARK_POSTURE_LITERALS:
            assert literal in claim.observations, (
                f"the regression claim no longer requires {literal!r}. The seam proof is not "
                "optional: removing it is how this defect gets 'fixed' by weakening the oracle"
            )
        assert PROBE_CHECK in claim.checks, (
            "the regression claim requires the seam literals but does not name the M8 probe. Only "
            f"{PROBE_CHECK!r} runs the machine and narrates what it found; the pytest anchors it "
            "names print no such sentence, so the claim could never be established"
        )

    def test_every_claim_requiring_a_dark_posture_literal_names_the_probe(self, m8):
        """Stated once, for the whole file rather than for one claim: wherever the scenario asks for
        this proof, it must ask the command that produces it."""
        assert claims_needing_the_probe(m8) == []

    def test_the_dark_posture_literals_are_still_required_somewhere(self, m8):
        """The other way to make the gap go away is to stop asking. This refuses that."""
        for literal in DARK_POSTURE_LITERALS:
            assert literal in m8.expect_visible, (
                f"{literal!r} is no longer an expected observation of the M8 scenario"
            )
            assert any(literal in claim.observations for claim in m8.verifies), (
                f"{literal!r} is expected but no declared risk rests on it any more"
            )

    def test_a_claim_may_not_require_an_observation_its_checks_cannot_declare(self, m8):
        """The general half, enforced at load time — asserted here against the real M8 file so the
        shipped scenario is covered by the invariant and not merely by the unit test of it."""
        assert unattributable_claims(m8) == []

    def test_every_declared_risk_names_at_least_one_check_and_one_observation(self, m8):
        """A claim with an oracle on only one side is half a claim.

        `RiskClaim` requires one of the two. This file requires both for M8, because a claim with no
        named check matches its literals against EVERYTHING the run observed — which for a scenario
        that runs eleven pytest anchors and three neighbouring probes is a very large haystack, and
        an accidental match in it is coverage nobody established.
        """
        for claim in m8.verifies:
            assert claim.checks, f"the {claim.risk_category!r} claim names no check"
            assert claim.observations, f"the {claim.risk_category!r} claim names no observation"

    def test_the_effect_and_approval_families_are_deliberately_left_undeclared(self, m8):
        """M8 touches the outside world not at all, and mints no approval, and both absences are the
        point.

        Machine §28 says human-approval is `none`; entity §40 says an `OVERDUE` Expectation raises an
        Exception a human owns and ### does not itself gate. M8 produces no external effect, so
        `timeout_before_effect` and `ambiguous_external_effect` are risks about another unit's
        behaviour. A run that names one of them as a blocking M8 risk must GENERATE a case for it or
        block — not find a permanent declaration here waving it through.
        """
        declared = m8.declared_risk_categories()
        for foreign in ("approval_required", "timeout_before_effect", "timeout_after_effect",
                        "ambiguous_external_effect", "conflicting_evidence"):
            assert foreign not in declared, (
                f"the M8 scenario declares {foreign!r}, which is a claim about another unit's "
                "behaviour rather than about the Expectation machine"
            )

    def test_every_declared_category_is_one_m8_actually_exhibits(self, m8):
        """Fourteen families, each derived from M8's own behaviour rather than from M7's count."""
        assert m8.declared_risk_categories() == {
            "safety_invariant",
            "authorization",
            "missing_data",
            "unexpected_state_transition",
            "boundary",
            "concurrency",
            "cross_tenant",
            "malformed_input",
            "idempotency",
            "retry_safety",
            "restart_recovery",
            "stale_state",
            "persistence_failure",
            "regression",
        }


# --------------------------------------------------------------------------
# 3. The database is the oracle, not the probe's narration
# --------------------------------------------------------------------------


class TestPersistedStateIsTheOracle:
    """The sentences a green test suite can state while the database enforces none of them.

    "`OVERDUE` requires a healthy `coverage_ref`", "there is one live expectation per key", "a
    human-owned state has a named human" and "there are six states" are each a property of the
    SCHEMA. A probe that prints them proves it printed them.
    """

    def test_the_scenario_reads_the_database_at_all(self, m8):
        assert m8.expect_state, "no persisted state is inspected; the probe speaks for itself"

    def test_the_six_states_are_asserted_and_there_is_no_seventh(self, m8):
        guard = [c for c in m8.expect_state if "state vocabulary" in c.command]
        assert guard, "the state set is never read out of the DDL"
        declared = guard[0]
        for state in STATES:
            assert f"'{state}'" in declared.contains, f"{state} is not asserted in the CHECK"
        for forbidden in FORBIDDEN_STATES:
            assert f"'{forbidden}'" in declared.not_contains, (
                f"nothing prevents an invented {forbidden} state"
            )

    def test_the_honesty_invariant_is_asserted_as_a_database_check(self, state_checks):
        """Entity §16 and §37, and the reason this unit is not a timer.

        `OVERDUE` without a healthy-coverage `coverage_ref` is a STRUCTURALLY IMPOSSIBLE state — an
        insert the database refuses, not a branch a code path takes. And `AC-SAFE-028` makes the
        named human on the two human-owned states the same kind of fact.
        """
        guard = state_checks.get(
            "OVERDUE requires healthy coverage and the human-owned states require an owner, as CHECKs"
        )
        assert guard, "nothing asserts the honesty invariant is a database constraint"
        assert "OVERDUE requires coverage_ref: True" in guard
        assert "OVERDUE requires healthy coverage: True" in guard, (
            "the CHECK is asserted to mention coverage_ref but not to require it be HEALTHY — which "
            "is the difference between 'we looked something up' and 'we can prove we were watching'"
        )
        assert "human-owned states require owner_id: True" in guard
        assert "expected_source NOT NULL: True" in guard
        assert "deadline_utc NOT NULL: True" in guard
        assert "originating_timezone NOT NULL: True" in guard
        assert "expectation_key NOT NULL: True" in guard
        assert "subject_ref NOT NULL: True" in guard
        assert "expected_type NOT NULL: True" in guard
        for column in ("'coverage_ref'", "'deadline_history'", "'discharge_observation_id'",
                       "'expectation_id'", "'owner_id'", "'state'", "'tenant'", "'version'"):
            assert column in guard, f"the expectations table is not asserted to carry {column}"

    def test_the_coverage_record_is_asserted_persisted_windowed_and_never_defaulted(
        self, state_checks
    ):
        """`M-32`: an observation-coverage record per `(channel, window)`.

        Three properties, and the third is the one that matters most. If `health` carries a DEFAULT,
        then "we did not record anything" silently becomes a value — and a DEFAULT of anything but
        the blind value turns the absence of evidence into evidence of absence, which is exactly the
        accusation `M-32`'s fail-closed clause exists to prevent. A window with no bounds cannot
        support "throughout the window" at all.
        """
        guard = state_checks.get(
            "the observation-coverage record M8 reads is persisted, tenant-first and windowed"
        )
        assert guard, "nothing asserts the coverage record is a real, persisted table"
        for value in ("'HEALTHY'", "'DOWN'", "'UNKNOWN'"):
            assert value in guard, f"the coverage health vocabulary does not assert {value}"
        assert "coverage health vocabulary present: True" in guard
        assert "coverage tenant NOT NULL: True" in guard
        assert "coverage window is bounded: True" in guard
        assert "coverage health is never defaulted: True" in guard

    def test_one_live_expectation_is_asserted_as_a_partial_unique_index_tenant_first(
        self, state_checks
    ):
        """Entity §17, machine §15/§17/§19. "At most one live expectation for this owed observation"
        is a hope about a code path until it is a partial unique index, and an application-level
        check-then-insert is exactly what two concurrent raisers both pass.

        `RAISED` is asserted because entity §17, machine §15 and F8 all name it. Nothing here asserts
        the `WHERE` clause names ONLY `RAISED`: target spec §12.8 says "while non-terminal" and that
        disagreement is `M8-AQ-3`, which this repository does not settle.
        """
        guard = state_checks.get(
            "one live RAISED expectation per key is a PARTIAL UNIQUE index, tenant-first"
        )
        assert guard, "nothing asserts the one-live-expectation rule is an index"
        assert "CREATE UNIQUE INDEX" in guard
        assert "expectation_key" in guard
        assert "RAISED" in guard, "the index is not asserted to be PARTIAL on RAISED"
        assert "tenant first in the live index: True" in guard

    def test_the_owner_the_observation_and_the_coverage_are_asserted_as_foreign_keys(
        self, state_checks
    ):
        """"A named human", "the observation that discharged it" and "the coverage we consulted" are
        decoration while they are text columns. Entity §18 names them foreign keys; M1, M4, M6 and M7
        each made the same argument for their own."""
        guard = state_checks.get(
            "the owner, the discharging observation and the coverage record are FOREIGN KEYS"
        )
        assert guard, "nothing asserts the expectation references are foreign keys"
        assert "owner is FK-backed into tenant_humans: True" in guard
        assert "discharge_observation_id is FK-backed into observations: True" in guard
        assert "coverage_ref is FK-backed into observation_coverage: True" in guard
        assert "foreign keys into a table nobody built: []" in guard

    def test_inventing_a_sweep_or_an_unregistered_state_is_a_scenario_failure(self, m8, state_checks):
        """And the rule is preserved by a check over the corpus, not a hope.

        The way a build session breaks "an obligation never quietly stops existing" is not by arguing
        with it. It is by adding a TTL, a nightly sweep, a stale-expectation reaper or a `TIMED_OUT`
        state because those felt like hygiene.

        The scan deliberately does NOT flag a function whose name begins `refuse`/`reject`/`illegal`:
        the machine must be able to REFUSE a sweep-close attempt by name to prove `GR-1` catches it,
        and a scan that flagged the refusal would force the illegal case out of existence to stay
        green. It is also scoped to the machine and its migration rather than to the probe, for the
        same reason.
        """
        guard = state_checks.get(
            "no sweep, reaper, deletion or unregistered expectation state was invented"
        )
        assert guard, "nothing asserts that no sweep or unregistered state was invented"
        assert "invented sweep/reaper surfaces: []" in guard
        assert "unregistered expectation states in the migration: []" in guard
        assert "invented or foreign event names in the machine: []" in guard
        assert "invented expiry/extra transition rows: []" in guard
        assert "expectation deletion statements: []" in guard
        assert "machine source: present" in guard, (
            "the invention sweep has no proven population: it would print empty lists against a "
            "missing file and read as a pass"
        )
        command = [c for c in m8.expect_state if c.contains == guard][0].command
        for event in FORBIDDEN_EVENTS:
            assert event in command, f"the invention sweep does not look for {event}"
        assert "EX-(?:8|9|10" in command, "nothing prevents a ninth transition row being written"
        assert "probe_phase6_expectation" not in command, (
            "the sweep reads the probe, which legitimately needs the sweep and expiry identifiers "
            "in order to ATTEMPT the illegal transition GR-1 must refuse"
        )
        assert "_?refuse" in command, (
            "the sweep flags any function whose name contains 'sweep', including the machine's own "
            "REFUSAL of one — which would force the GR-1 case out of existence to stay green"
        )

    def test_the_seven_f8_contracts_are_used_and_no_eighth_is_minted(self, state_checks):
        guard = state_checks.get("M8 uses the seven registered F8 contracts and invents no eighth")
        assert guard, "nothing checks the event names M8 uses against the canonical registry"
        declared = " ".join(guard)
        for event in F8_EVENTS:
            assert f"'{event}'" in declared, f"{event} is not asserted registered"
        assert "synonym events registered: []" in guard
        assert "unregistered names in the machine: []" in guard
        assert "machine source: present" in guard, (
            "the unregistered-name sweep has no proven population: it would print an empty list "
            "against a missing file and read as a pass"
        )

    def test_the_honesty_split_is_read_out_of_the_contract_projection(self, state_checks):
        """The two required payload fields ARE the honesty split, expressed as a contract.

        `ExpectationOverdue` cannot exist without the `coverage_ref` that proves health, and
        `ExpectationIndeterminate` cannot exist without its `coverage_gap`. The P5 contract layer
        already refuses an event missing a required field, so reading that fact out of the projection
        makes the task's §3.3 a READING of the corpus rather than an assertion about it — and it is a
        mechanism M8 inherits rather than one it must invent.
        """
        guard = state_checks.get("M8 uses the seven registered F8 contracts and invents no eighth")
        assert guard
        assert "ExpectationOverdue requires: ['coverage_ref']" in guard
        assert "ExpectationIndeterminate requires: ['coverage_gap']" in guard
        assert (
            "ExpectationRaised requires: ['deadline_utc', 'expectation_key', 'expected_source', "
            "'originating_timezone']" in guard
        ), "the declared-channel and timezone requirements are not read out of the projection"

    def test_the_two_discharge_producers_are_read_out_of_the_projection(self, state_checks):
        """`ExpectationDischarged` has TWO registered producers, and that is the whole late-arrival
        proof.

        `EX-2` is the on-time discharge and `EX-4` is the late one, and they are ONE contract — so a
        machine that refused a late arrival would have a registered producer with nothing to produce.
        The corpus states it; this reads it rather than asserting it.
        """
        guard = state_checks.get("M8 uses the seven registered F8 contracts and invents no eighth")
        assert guard
        assert "ExpectationDischarged producers: ['EX-2', 'EX-4']" in guard

    def test_the_dark_posture_is_measured_over_the_shipped_package(self, state_checks):
        assert (
            "production importers of expectation: []"
            in state_checks.get(
                "M8 has no production caller — the dark posture, measured over the shipped package",
                [],
            )
        )
        assert (
            "scripts reaching expectation: ['probe_phase6_expectation.py']"
            in state_checks.get(
                "the only thing outside the package that reaches M8 is the verification probe itself",
                [],
            )
        )

    def test_no_live_tracking_surface_or_health_probe_can_arrive_with_the_unit(self, m8, state_checks):
        """M8's product form is a live "what is late" view, and the coverage record's own writer is a
        channel HEALTH PROBE. Both are P9+ and both are precisely what must not arrive with the
        engine primitive."""
        guard = state_checks.get(
            "no live tracking or SLA surface, and no channel health probe, ships with M8"
        )
        assert guard, "nothing prevents a live tracking surface arriving with M8"
        assert "modules joining the expectation machine to a channel: []" in guard
        assert "production coverage health probes: []" in guard
        command = [c for c in m8.expect_state if c.contains == guard][0].command
        for channel in ("email_triage", "ingestion", "extraction", "inbox_brain",
                        "action_callback", "slack_adapter", "tms_adapter", "mailbox_intake",
                        "follow_up"):
            assert channel in command, f"the tracking-surface sweep does not look at {channel}"

    def test_m8_authorizes_nothing(self, state_checks):
        """Entity §4/§38/§40 and target spec §20.5: an Expectation OWES, it does not AUTHORIZE. It is
        an INPUT to checkpoint step 4 and never a second gate."""
        guard = state_checks.get("the checkpoint is still the only thing that mints a gate decision")
        assert guard
        assert "modules that MINT a gate decision: ['checkpoint.py']" in guard

    def test_the_deadline_is_asserted_to_ride_p5s_durable_timers(self, state_checks):
        """Machine §37: *durable timers, not sweeps* (`M-36`). `EX-3`, `EX-3i` and `EX-7` are all
        `T`-triggered, so a second timer table or an in-memory sleep is not a style choice — it is
        the mechanism by which an obligation stops being durable."""
        guard = state_checks.get(
            "the deadline rides P5's existing durable timers rather than a second timer mechanism"
        )
        assert guard, "nothing asserts the deadline is a durable timer"
        assert "M8 schedules through DurableTimers: True" in guard
        assert "M8 consumes TimerFired: True" in guard
        assert "in-memory sleep in the machine: []" in guard
        assert "second timer table created by m8: []" in guard
        assert "timer tables in the canonical schema: ['durable_timers']" in guard
        assert "machine source: present" in guard, (
            "the timer sweep has no proven population"
        )

    def test_m9_m10_m11_and_m12_are_not_invented_along_the_way(self, state_checks):
        """`EX-3` and `EX-7` both end "→ Exception" (M9), entity §14 asserts a 1:1 Exception outright,
        a compiled *"hourly updates"* rule looks like a Policy (M11) and needs a Rule registry (M12),
        and a resolution that reveals a wrong effect reaches for a Compensation (M10). The M9 one is
        the most tempting of the four, because the canonical adversarial test is literally NAMED for
        an Exception."""
        guard = state_checks.get(
            "the exception, compensation, policy and rule seams are fed without M9, M10, M11 or M12 "
            "being built"
        )
        assert guard, "nothing prevents M8 building a neighbouring machine"
        assert "mints another machine event: []" in guard
        assert "m9/m10/m11/m12 tables created by m8: []" in guard
        assert "machine and migration present: True" in guard, (
            "the foreign-event sweep has no proven population"
        )

    def test_the_three_landed_machines_are_asserted_unrewritten(self, state_checks):
        """M5's rows are what `EX-2` and `EX-4` READ, M3 is where entity §39's
        `AWAITING_OBSERVATION` sentence points, and M7 is the machine an overdue Expectation is
        NOT. All three are landed and tier-1, and all three are places the cheapest way to close an
        authority question is to edit someone else's file."""
        guard = state_checks.get("M5's, M3's and M7's landed machines are not rewritten by M8")
        assert guard, "nothing asserts the three landed machines are unchanged"
        assert "observation.py imports expectation: False" in guard
        assert "external_effect.py imports expectation: False" in guard
        assert "conflict.py imports expectation: False" in guard
        assert "M5 OB-3 binds without an expectation: True" in guard
        assert "M3 UNKNOWN_OUTCOME semantics present: True" in guard
        assert "M3 AWAITING_OBSERVATION seam untouched by M8: True" in guard
        assert "M7 raises no conflict from an expectation: True" in guard

    def test_the_expectation_and_coverage_layers_are_asserted_present_and_tenant_first(
        self, state_checks
    ):
        guard = state_checks.get(
            "a freshly created canonical database carries the expectation layer, tenant-first"
        )
        assert guard
        assert "problems: []" in guard
        assert "expectations" in guard
        assert "observation_coverage" in guard
        assert "durable_timers" in guard, (
            "the timer substrate EX-3/EX-3i/EX-7 ride is not asserted to exist beside the layer"
        )
        assert "observations" in guard, "the table EX-2's bound Observation lives in is not asserted"


# --------------------------------------------------------------------------
# 4. The five recorded authority conflicts stay open
# --------------------------------------------------------------------------


class TestTheTaskPreservesTheAuthorityConflicts:
    """M4's, M5's, M6's and M7's §3.8 lesson, applied to a corpus that disagrees with itself five
    times about M8.

    A resolution the builder invented is worse than a blocked run, because it looks like agreement.
    """

    AQS = ("M8-AQ-1", "M8-AQ-2", "M8-AQ-3", "M8-AQ-4", "M8-AQ-5")

    def test_all_five_questions_are_named_with_both_sides(self):
        for question in self.AQS:
            assert question in M8_TASK, f"{question} is never raised"
        # AQ-1: entity §14/§26/§37 require an Exception and the canonical test is named for one;
        # ExceptionRaised is EC-1's and M9 is unbuilt.
        assert 'what does *"→ Exception"* mean while M9 is not built?' in M8_TASK
        assert "ExceptionRaised" in M8_TASK and "EC-1" in M8_TASK
        # AQ-2: the target spec names the table and entity §18 makes it a FK; no entity file exists.
        assert "who owns `observation_coverage`, and what is its contract?" in M8_TASK
        assert "no 45-point entity file" in M8_TASK_FLAT
        # AQ-3: three files say WHERE state='RAISED'; the target spec says "while non-terminal".
        assert (
            "does the duplicate-prevention index cover `RAISED`, or every non-terminal state?"
            in M8_TASK
        )
        assert "while non-terminal" in M8_TASK
        # AQ-4: K-2 says subject_ref is an artifact ref; entity §10 glosses it as the load.
        assert "is `subject_ref` an artifact reference or a business-entity reference?" in M8_TASK
        assert "K-2" in M8_TASK
        # AQ-5: the family file says partly strict; registry §8 lists F8 in neither list.
        assert "is F8 strictly ordered" in M8_TASK
        assert "previous_aggregate_version" in M8_TASK

    def test_each_question_names_what_every_reading_agrees_on(self):
        """The builder is not blocked by the conflict. It is blocked from RESOLVING it — and told
        exactly what it may still build."""
        section = M8_TASK[M8_TASK.index("### 3.8"):M8_TASK.index("### 3.9")]
        blocks = re.split(r"(?=\*\*`M8-AQ-)", section)[1:]
        assert len(blocks) == 5, f"§3.8 holds {len(blocks)} question blocks, not five"
        for question, block in zip(self.AQS, blocks):
            assert question in block
            assert "**Every reading agrees on:**" in block, (
                f"{question} states both sides and never says what may still be built"
            )
        assert "Do not amend a specification to close this" in M8_TASK_FLAT
        assert "do not edit `observation.py`" in M8_TASK_FLAT
        assert "Do not edit `external_effect.py`" in M8_TASK_FLAT
        assert "do not edit `conflict.py`" in M8_TASK_FLAT
        assert (
            "Do not create an `exceptions` table, do not write an `EC-*` transition, and do not mint "
            "`ExceptionRaised`" in M8_TASK_FLAT
        )

    def test_the_scenario_asserts_nothing_about_the_open_questions(self, m8):
        """The scenario must not encode a resolution either.

        There is no required literal about an Exception being raised, none about who writes
        `observation_coverage`, none about whether the live index covers every non-terminal state,
        none about what `subject_ref` points at, and none about F8's ordering class.
        """
        visible = " ".join(m8.expect_visible)
        for invented in FORBIDDEN_EVENTS + ("ExceptionRaised", "ExpectationDetected"):
            assert invented not in visible, (
                f"the scenario requires an unregistered or foreign event name {invented!r}, which "
                "resolves an authority question by minting a name"
            )
        upper = visible.upper()
        assert "AN EXCEPTION IS RAISED" not in upper
        assert "NON-TERMINAL INDEX" not in upper
        assert "SUBJECT_REF IS AN OBSERVATION" not in upper
        # What it DOES require is the part every reading agrees on.
        assert "EXPIRY IS NEVER SILENT" in m8.expect_visible
        assert "THE ABSENCE OF A COVERAGE RECORD IS NOT HEALTH" in m8.expect_visible
        assert "AT MOST ONE LIVE RAISED EXPECTATION PER TENANT AND EXPECTATION KEY" in m8.expect_visible
        assert "THE M9, M10, M11 AND M12 MACHINES ARE NOT BUILT" in m8.expect_visible

    def test_v10_and_v6_are_explicitly_left_unresolved(self):
        """Two open validation items, both `NOT A BLOCK`, both with a fail-closed default.

        `V10` is the per-lane ageing thresholds — a customer's operating policy with a number on it —
        and its fail-closed default is *ages, escalates, never expires silently*. `V6` is the
        deferred-verification bound per TMS, a per-integration measurement, and its default is
        `AWAITING_OBSERVATION` plus an Expectation. A builder that "discovers" that a POD is overdue
        after 48 hours has invented a product decision.
        """
        assert "V10" in M8_TASK
        assert "V6" in M8_TASK
        assert "they are NOT blocks." in M8_TASK
        assert "DO NOT CHOOSE A BUSINESS AGEING THRESHOLD." in M8_TASK
        assert "DO NOT CHOOSE A PER-TMS DEFERRAL BOUND." in M8_TASK
        assert "ages · escalates · NEVER EXPIRES SILENTLY" in M8_TASK_FLAT
        assert (
            "THE FAIL-CLOSED BEHAVIOUR IS THE PART YOU MUST BUILD" in M8_TASK
        ), "the task rules the thresholds out without saying what the builder must still build"
        assert "unknown coverage ⇒ `INDETERMINATE`" in M8_TASK_FLAT

    def test_the_f14_scoping_decision_is_stated_not_guessed(self):
        """Four F14 tripwires are in play and exactly one is M8's."""
        assert "IllegalTransitionAttempted" in M8_TASK
        assert "is MANDATORY and is yours" in M8_TASK
        assert "ProvenanceStrengtheningAttempted" in M8_TASK
        assert "is NOT yours" in M8_TASK
        assert "OwnerAssertedOverwriteAttempted" in M8_TASK
        assert "CrossTenantAccessAttempted" in M8_TASK


# --------------------------------------------------------------------------
# 5. The seams — feed them, never edit the landed unit on the other side
# --------------------------------------------------------------------------


class TestTheSeamsAreScopedToM8:
    """M8 sits between more landed units than any P6 unit before it. `EX-2`/`EX-4` READ M5's rows,
    entity §39 points at M3's `AWAITING_OBSERVATION`, `EX-3`'s coverage record is a table nobody has
    built, and every canonical sentence about `OVERDUE` and `EXPIRED` ends in an M9 Exception. Four
    seams, four different ways to answer an authority question by writing someone else's code.
    """

    def test_the_task_states_the_coverage_seam_and_forbids_a_second_observation_system(self):
        assert "The coverage record" in M8_TASK
        assert "observation_coverage" in M8_TASK
        assert "Do not build a second\nObservation." in M8_TASK or (
            "Do not build a second Observation." in M8_TASK_FLAT
        )
        assert (
            "Do not infer health from the absence of errors" in M8_TASK_FLAT
        ), "the task never rules out the single most likely way this unit gets built wrong"
        assert (
            "Do not build a production health probe, a poller, an adapter, an importer or a channel "
            "monitor." in M8_TASK_FLAT
        )
        assert "name the clause and say so" in M8_TASK_FLAT

    def test_the_task_states_the_m3_seam_and_protects_unknown_outcome(self):
        assert "The M3 `AWAITING_OBSERVATION` seam" in M8_TASK
        assert "GR-6" in M8_TASK
        assert "no timer moves it" in M8_TASK_FLAT
        assert "GR-5" in M8_TASK and "AC-SAFE-021" in M8_TASK
        assert "a timeout alone never proves failure" in M8_TASK_FLAT
        assert "READ `external_effect.py` AND SAY WHAT IT ACTUALLY DOES TODAY." in M8_TASK
        assert "stop before making it" in M8_TASK_FLAT

    def test_the_task_states_the_m5_seam_and_forbids_editing_it(self):
        assert "M5 Observation, which is landed, and which M8 reads." in M8_TASK
        assert "BOUND" in M8_TASK
        assert (
            "a binding that started requiring an Expectation would be M5 rewritten from inside M8"
            in M8_TASK_FLAT
        )
        assert "never writes one, never binds one, never supersedes one" in M8_TASK_FLAT

    def test_the_task_states_that_an_overdue_expectation_is_not_a_conflict(self):
        """M7 as an Exception substitute is `M8-AQ-1` answered by accident, and it is the neatest
        available mistake: M9 is missing, M7 exists, and both are "a thing a human owns"."""
        assert (
            "AN EXPECTATION BECOMING `OVERDUE` OR `INDETERMINATE` IS NOT AUTOMATICALLY A CONFLICT."
            in M8_TASK_FLAT
        )
        assert "we have too little" in M8_TASK_FLAT
        assert "`CF-1`, `IB-6` and `EF-4c`" in M8_TASK_FLAT
        assert "Do not raise a Conflict from M8" in M8_TASK_FLAT
        assert (
            "Using M7 as an Exception substitute because M9 is missing is `M8-AQ-1` answered by "
            "accident." in M8_TASK_FLAT
        )

    def test_the_task_forbids_editing_the_p3_kernel_while_feeding_it(self):
        """P3 remains the gate minter, and step 4 already exists. M8 feeds it; it does not become a
        second one, and it does not edit `checkpoint.py`."""
        assert "Do not create a second gate authority" in M8_TASK_FLAT
        assert "Do not edit `checkpoint.py`." in M8_TASK_FLAT
        assert "EvidenceCondition" in M8_TASK
        assert "step 4" in M8_TASK
        assert "M3 remains the single effect authority" in M8_TASK_FLAT
        assert (
            "`unknown` IS NOT `conflicting`" in M8_TASK
        ), (
            "the task never distinguishes the evidence condition an undischarged Expectation "
            "produces from the one M7 produces, which is the ADR-002 C5/C6 distinction and the "
            "difference between M8 and M7"
        )

    def test_the_task_states_the_foreign_keys_that_have_a_table_to_point_at(self):
        """Entity §18 names four references and only some have a target today. A builder that takes
        §18 literally builds the freight projection and the Evidence Store to satisfy it."""
        assert "The foreign keys entity §18 names, and what exists to point at" in M8_TASK
        for column in ("subject_ref", "discharge_observation_id", "coverage_ref", "owner_id"):
            assert column in M8_TASK, f"the reference {column} is never discussed"
        assert "build the foreign keys whose targets exist" in M8_TASK_FLAT
        assert "name the clause and stop" in M8_TASK_FLAT
        assert (
            "Do not build `evidence`, `exceptions`, `compensations`, `policies` or `rules`"
            in M8_TASK_FLAT
        )

    def test_the_task_states_the_m9_seam_as_a_deferral_rather_than_a_build(self):
        """Entity §14 asserts a 1:1 Exception outright, and the canonical adversarial test is NAMED
        for one. The cheapest way to satisfy both sentences is to build M9."""
        assert "THAT IS M9'S HALF, AND M9 IS NOT BUILT." in M8_TASK
        assert "M10 IS NOT BUILT AND YOU ARE NOT BUILDING IT" in M8_TASK
        assert "no fabricated completed Compensation" in M8_TASK_FLAT
        assert "test_expiry_raises_an_exception_never_silence" in M8_TASK
        assert "without minting an M9 event and without building an `exceptions` table" in M8_TASK_FLAT
        assert (
            "NAME THE SEAM IN PROSE, NOT BY ITS REGISTERED IDENTIFIER." in M8_TASK
        ), (
            "the scenario sweeps the shipped machine for foreign contract names; the task must tell "
            "the builder that, or a correct product fails a guard it was never shown"
        )

    def test_the_task_names_the_machines_own_types_so_the_sweep_reads_event_names(self):
        """The unregistered-name sweep matches an identifier beginning `Expectation` + a capital. M7
        ships `M7Machine`/`CfState`, so the only such identifiers in `conflict.py` are its five
        registered events — which is what makes that sweep a measurement rather than a trap."""
        assert "NAME THE MACHINE'S OWN TYPES THE WAY `conflict.py` NAMES ITS OWN." in M8_TASK
        assert "M8Machine" in M8_TASK and "ExState" in M8_TASK
        assert (
            "An identifier beginning `Expectation` followed by a capital letter that is not one of "
            "the seven registered F8 event names fails the sweep" in M8_TASK_FLAT
        )


# --------------------------------------------------------------------------
# 6. The vocabulary is safe, and visible to the generator
# --------------------------------------------------------------------------


class TestTheM8Vocabulary:
    @pytest.fixture
    def approved(self):
        return ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )

    def _planner(self, tmp_path: Path, configured: list[str]) -> ScenarioPlanner:
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True, approved_commands=configured),
            reasoner=ScriptedReasoner([{"risks": [], "scenarios": []}]),
            base_scenario=load_scenario(M8_PATH),
            permanent_scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            founder=FakeFounder(),
        )

    def test_every_case_is_approved_by_the_bare_probe_alone(self, tmp_path, cases):
        """No enumeration needed for SAFETY — only for visibility."""
        planner = self._planner(tmp_path, [])
        for case in cases:
            ok, why = planner.approved_commands.approves(f"{PROBE} --case {case}")
            assert ok, f"{case}: {why}"

    def test_no_case_name_trips_the_command_guard(self, approved, cases):
        """A case name is part of a command string, and the guard is a token matcher.

        M6 shipped a case called `...-between-render-and-click` and the boundary hard-blocked the
        bare token `render` as deploy tooling — so the one case the generator most needed to compose
        was unreachable. The guard was right and the name was wrong. This is that lesson, asserted
        instead of relearned.
        """
        for case in cases:
            ok, why = approved.approves(f"{PROBE} --case {case}")
            assert ok, (
                f"the case name {case!r} is refused by the boundary ({why}). The guard is right; "
                "rename the case"
            )

    def test_the_whole_mutation_axis_composes_without_widening_the_boundary(
        self, approved, dimensions
    ):
        """The property that makes the mutation axis safe rather than clever.

        Approval matches by PREFIX and refuses shell composition in the tail, so every combination of
        dimensions is already permitted by the single bare probe entry — the axis buys the generator
        a large bounded space and buys the boundary nothing to defend.
        """
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case a-blind-window-is-indeterminate-not-overdue "
                f"--inject {fault} --concurrency 8 --delay-ms 5000 --repeat 5 "
                "--tenants 3 --age-ms 86400000 --coverage absent --timezone America/Denver "
                "--confidence 1.0 --seed 4211"
            )
            ok, why = approved.approves(command)
            assert ok, f"{fault}: {why}"

    def test_an_iana_timezone_argument_is_not_read_as_a_path(self, approved):
        """`America/Denver` contains a slash, which is what a path looks like to a token matcher.

        The DST case is one of entity §43's five acceptance criteria, so a boundary that refused the
        zone would make the criterion unreachable from a generated case — and the fix would have been
        to weaken the guard rather than to notice.
        """
        for zone in ("America/Denver", "America/New_York", "Europe/London", "UTC"):
            ok, why = approved.approves(
                f"{PROBE} --case a-dst-boundary-does-not-move-the-deadline --timezone {zone}"
            )
            assert ok, f"the zone {zone!r} is refused by the boundary ({why})"

    @pytest.mark.parametrize(
        "command",
        [
            # A command nobody wrote down.
            "curl https://api.example.com/expectations",
            ".venv/bin/python scripts/send_real_invoice.py",
            "rm -rf src/freight_recon",
            # A live tracking or SLA surface, which is precisely what M8 must not grow.
            ".venv/bin/python -m freight_recon.ops_control --overdue-report",
            ".venv/bin/python scripts/slack_probe.py --post-late-loads",
            # A real channel health probe, which is the coverage record's P9+ writer.
            ".venv/bin/python scripts/poll_mailbox_health.py --write-coverage",
            # The approved probe, extended with composition.
            f"{PROBE} --case absent-coverage-is-not-health; curl https://evil.example.com",
            f"{PROBE} --case absent-coverage-is-not-health && rm -rf /",
            f"{PROBE} | tee /tmp/out",
            f"{PROBE} > /etc/hosts",
            f"{PROBE} --case $(whoami)",
            f"{PROBE} --case `whoami`",
            # A newline is whitespace; normalization would hide it, so the raw string is scanned for
            # control characters first.
            f"{PROBE}\ncurl https://evil.example.com",
            # A prefix that is not a prefix.
            ".venv/bin/python scripts/probe_phase6_expectation.py.bak",
        ],
    )
    def test_a_command_outside_the_vocabulary_is_refused(self, approved, command):
        ok, why = approved.approves(command)
        assert not ok, f"escaped the approved set: {command!r}"
        assert why, "a refusal must say why"

    def test_a_dimension_value_carrying_shell_is_still_refused(self, approved):
        """The axis is argument-only. A flag is not a hole."""
        for hostile in ("$(id)", "`id`", "a;id", "a|id", "a>/etc/hosts", "a&&id"):
            ok, _ = approved.approves(f"{PROBE} --case tenant-isolation --inject {hostile}")
            assert not ok, f"a dimension value smuggled shell through: {hostile!r}"
            ok, _ = approved.approves(f"{PROBE} --case tenant-isolation --timezone {hostile}")
            assert not ok, f"a timezone value smuggled shell through: {hostile!r}"

    def test_the_probe_with_an_ordinary_case_tail_is_still_allowed(self, approved):
        """The boundary has to let the real vocabulary through, or it has only made generation
        useless rather than safe."""
        ok, why = approved.approves(f"{PROBE} --case a-late-arrival-discharges-an-overdue-expectation")
        assert ok, why

    def test_the_neighbouring_probes_stay_reachable_through_the_scenario(self, approved):
        """They are not enumerated in the config, and they do not need to be.

        M8 co-commits with none of them — it adds two tables and edits the canonical schema, which is
        a REGRESSION relationship. Writing their bare probes into `p6_m8_expectation.yaml` as
        regression anchors already approves every `--case` tail of each, because approval matches by
        prefix.
        """
        for command in (
            ".venv/bin/python scripts/probe_phase6_observation.py --case tenant-isolation",
            ".venv/bin/python scripts/probe_phase6_identity_binding_claim.py --case tenant-isolation",
            ".venv/bin/python scripts/probe_phase6_conflict.py --case tenant-isolation",
        ):
            ok, why = approved.approves(command)
            assert ok, f"{command}: {why}"

    def test_the_rendered_brief_actually_shows_the_m8_vocabulary(self, tmp_path):
        """The brief truncates the approved list, silently. A vocabulary the generator never sees is
        a vocabulary it cannot choose from."""
        vocabulary = _local_vocabulary()
        if not any("probe_phase6_expectation.py" in entry for entry in vocabulary):
            pytest.skip("no local driver.config.yaml enumerating the M8 vocabulary")

        planner = self._planner(tmp_path, vocabulary)
        planner.plan_initial(task="Build P6/M8 Expectation", unit=None, run_id="r-m8")
        brief = planner.reasoner.briefs[0].render()

        assert PROBE in brief, "the deterministic M8 entry point is not in the brief"
        missing = [
            entry.split("--case ", 1)[1].split()[0]
            for entry in vocabulary
            if "probe_phase6_expectation.py --case " in entry and entry not in brief
        ]
        assert not missing, (
            "the approved-command list was truncated before these M8 cases: "
            f"{missing}. The brief renders at most {MAX_RENDERED_COMMANDS} commands; the "
            f"approved set now holds {len(planner.approved_commands)}."
        )

    def test_the_approved_set_still_fits_inside_what_the_brief_renders(self, tmp_path):
        """Approved commands sort ASCII and every probe entry begins `scripts/probe_...`, so they
        sort LAST: an approved set larger than the render bound loses the probe vocabulary first, and
        loses it silently."""
        planner = self._planner(tmp_path, _local_vocabulary())
        assert len(planner.approved_commands) <= MAX_RENDERED_COMMANDS, (
            f"{len(planner.approved_commands)} approved commands but the generation brief renders "
            f"only the first {MAX_RENDERED_COMMANDS} — the M8 vocabulary sorts last and is now "
            "invisible to the generator."
        )


# --------------------------------------------------------------------------
# 7. Dynamic generation can close an M8 coverage gap, safely
# --------------------------------------------------------------------------


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close.

    Built as the planner builds one: the citation lives on the provenance, and a coverage-gap case
    that cannot name a risk from this run's own register is refused before it reaches the boundary at
    all.
    """
    return GeneratedScenario(
        id="gen-m8-blind-window",
        title="the deadline passes while the declared channel is down",
        purpose=(
            "a missed deadline over a blind window must be INDETERMINATE; OVERDUE would accuse the "
            "carrier of a failure that was ours"
        ),
        risk_category=RiskCategory.SAFETY_INVARIANT,
        priority=Priority.P0,
        rationale="the identified blind-window risk had no scenario behind it",
        requirement_reference="P6/M8",
        product_principle_reference="honest-unknowns",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared state, so "
            "nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m8-task",
            session_id="scripted",
            generating_risk="a blind window could become an accusation rather than an admission",
            source_risks=[risk_key],
        ),
        actions=[{
            "kind": "command",
            "name": "let the deadline pass while the channel is down",
            "command": command,
            # The command that prints it, named. An asserted literal no operation in the scenario
            # declares is refused as an unattributable oracle.
            "expect_contains": ["A MISSED DEADLINE OVER A BLIND WINDOW IS INDETERMINATE, NOT OVERDUE"],
        }],
        # `safety_invariant` is a family whose claims are about a TABLE — "OVERDUE requires healthy
        # coverage" is not something a probe can prove by printing it. This is the mechanical form of
        # the rubric's "a 200 is not success".
        persisted_state_checks=[
            GeneratedStateCheck(
                name="the expectation layer is still tenant-first and readable",
                command=STATE_ORACLE,
                contains=["problems: []", "expectations"],
            )
        ],
        expected_observations=[
            "A MISSED DEADLINE OVER A BLIND WINDOW IS INDETERMINATE, NOT OVERDUE"
        ],
        forbidden_observations=["### BLIND WINDOW BECAME OVERDUE ###"],
    )


class TestGenerationClosesM8GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-blind-window",
            description="a deadline passing over a blind window could be recorded as OVERDUE",
            risk_category=RiskCategory.SAFETY_INVARIANT,
            severity=Priority.P0,
            basis="M-32 is the mandate the unit exists to satisfy",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m8", "p6", "m8"},
                principle_tokens={"honest-unknowns"},
                known_risk_ids={risk.key, "R-blind-window"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m8_vocabulary_is_accepted(self, context):
        ctx, risk = context
        command = (
            f"{PROBE} --case a-blind-window-is-indeterminate-not-overdue "
            "--inject coverage-down --coverage down --age-ms 3600000 --seed 7"
        )
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M8 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_the_whole_mutation_axis_is_reachable_from_a_gap_case(self, context, dimensions):
        ctx, risk = context
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case a-blind-window-is-indeterminate-not-overdue "
                f"--inject {fault} --concurrency 4 --delay-ms 40 --tenants 2 --age-ms 3600000 "
                "--coverage absent --timezone America/Denver --confidence 1.0 --seed 11"
            )
            accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
            assert accepted, f"{fault}: {rejected}"

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario('python -c "import expectation; expectation.mark_overdue()"', risk.key)],
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

    def test_an_uncovered_p0_m8_risk_blocks_acceptance(self):
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
            scenario_id="gen-raise",
            scenario_name="gen-raise",
            origin=Origin.GENERATED,
            outcome=Outcome.PASSED,
            required=True,
            risk_category="authorization",
            evidence_path="/runs/gen-raise",
            evidence_verified=True,
        )
        result = SuiteResult(outcomes=[passing], expected_required_ids=["gen-raise"])
        assert evaluate_gate(result, risks=[]).status is GateStatus.VERIFIED

        verdict = evaluate_gate(
            result,
            risks=[
                IdentifiedRisk(
                    id="R-blind-window",
                    description="a blind window could be recorded as OVERDUE",
                    risk_category=RiskCategory.SAFETY_INVARIANT,
                    severity=Priority.P0,
                    basis="M-32 is the mandate the unit exists to satisfy",
                )
            ],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert "KNOWN COVERAGE GAPS" in verdict.summary_block()


# --------------------------------------------------------------------------
# 8. P6-D46 stays closed for M8
# --------------------------------------------------------------------------


class TestP6D46StaysClosedForM8:
    """`P6-D46`: the M6 re-verification run proposed nine scenarios, every one declared a
    `risk_category` the harness's own enum did not contain, all nine were discarded at the parse
    stage, and the run reported *"0 generated case(s) + 1 permanent scenario"* and ACCEPTED.

    Nothing had failed. The product was fine. But *"the generator legitimately produced nothing new"*
    and *"the generator produced nine and Product Driver could not read any of them"* had collapsed
    into one number, and only the first is a reason to accept.

    The fix is general and lives in `tests/test_generation_contract.py`. What is pinned HERE is that
    M8 does not reopen it from the permanent-scenario side: the M8 file uses only canonical
    categories, a category it invented would refuse to load, and the four counts stay separable for
    an M8 wave. **Nothing about M8 is special-cased inside Product Driver core to achieve that.**
    """

    #: What `--case absent-coverage-is-not-health` would print, recorded as the task's own output
    #: contract states it. `repo` below is a tmp_path with no Neyma in it, so the quality boundary has
    #: nothing to interrogate and would refuse the fixture's own correct oracle — which is a true
    #: answer to the wrong question here.
    RECORDING = {
        f"{PROBE} --case absent-coverage-is-not-health": (
            "THE ABSENCE OF A COVERAGE RECORD IS NOT HEALTH\n"
            "behaviours as specified, 0 wrong\n"
        ),
    }

    def _planner(self, tmp_path: Path, payloads) -> ScenarioPlanner:
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner(list(payloads)),
            base_scenario=load_scenario(M8_PATH),
            permanent_scenarios=[load_scenario(M8_PATH)],
            founder=FakeFounder(),
            contract_probe=recorded_contract_probe(self.RECORDING),
        )

    def _m8_raw(self, scenario_id: str, category: str) -> dict:
        """A proposal shaped for THIS unit: dark, command-driven, with a persisted-state oracle.

        The shared fixture's default is an HTTP approval scenario, which M8's dark base scenario
        correctly refuses on four separate grounds. Using it here would prove that an HTTP scenario
        is rejected, which is not what P6-D46 is about.
        """
        return raw_scenario(
            scenario_id,
            risk_category=category,
            requirement="U-042: an approved invoice is paid exactly once",
            principle="effect-truth",
            service_refs=[],
            actions=[{
                "kind": "command",
                "name": "let the deadline pass with no coverage recorded",
                "command": f"{PROBE} --case absent-coverage-is-not-health",
                "expect_contains": ["THE ABSENCE OF A COVERAGE RECORD IS NOT HEALTH"],
            }],
            state_checks=[{
                "name": "the expectation layer is still tenant-first and readable",
                "command": STATE_ORACLE,
                "contains": ["problems: []"],
            }],
            expected_observations=["THE ABSENCE OF A COVERAGE RECORD IS NOT HEALTH"],
            forbidden_observations=["### ABSENT COVERAGE TREATED AS HEALTHY ###"],
            cleanup=[],
            isolation_key="expectation-db",
            isolation_note=(
                "the probe builds its own temporary database per case and touches no shared "
                "state, so nothing survives it to contaminate the next scenario"
            ),
            generating_risk="absent coverage could be read as a healthy channel",
        )

    def test_the_m8_scenario_declares_only_canonical_categories(self, m8):
        """The half a permanent scenario can break on its own.

        Every `verifies:` entry names a `RiskCategory` member, checked against the ONE taxonomy
        rather than against a list this file keeps.
        """
        declared = m8.declared_risk_categories()
        assert declared, "the M8 scenario declares no risk coverage at all"
        unknown = sorted(declared - set(RISK_CATEGORY_VALUES))
        assert not unknown, (
            f"the M8 scenario declares categories the harness taxonomy does not contain: {unknown}"
        )

    def test_an_invented_category_in_the_m8_file_would_refuse_to_load(self, tmp_path):
        """The load-time refusal, exercised against a copy of the REAL M8 file.

        A `verifies:` entry naming a category the taxonomy does not hold would match no risk and read
        as coverage while providing none — which is `P6-D46`'s shape one layer down. This proves the
        M8 file is covered by the refusal rather than merely compatible with it.
        """
        raw = yaml.safe_load(M8_PATH.read_text(encoding="utf-8"))
        raw["verifies"][0]["risk_category"] = "blind-window-became-overdue"
        broken = tmp_path / "m8_broken.yaml"
        broken.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="unknown risk_category"):
            load_scenario(broken)

    def test_the_generator_schema_constrains_the_category_to_the_same_taxonomy(self):
        """The other half: a model cannot answer with a category the loader would refuse."""
        field = PLAN_SCHEMA["properties"]["scenarios"]["items"]["properties"]["risk_category"]
        assert field.get("enum") == list(RISK_CATEGORY_VALUES), (
            "the generator's structured-output schema no longer constrains risk_category to the "
            "canonical taxonomy, which is exactly the unconstrained {'type': 'string'} that "
            "produced P6-D46"
        )

    def test_an_unreadable_m8_candidate_is_a_contract_blocker_not_a_silent_zero(self, tmp_path):
        """The `P6-D46` shape, reproduced with M8-flavoured categories, against the M8 base scenario.

        Nine well-meant descriptions of specific M8 defects — none of them a member of a closed
        family vocabulary — must be recorded as CONTRACT rejections the candidates survive, not
        dropped into "0 generated scenarios". And the run may not reach a normal acceptance while
        they stand, even though nothing failed.
        """
        planner = self._planner(tmp_path, [
            raw_payload(
                *(
                    self._m8_raw(f"S{i}-m8", category)
                    for i, category in enumerate(M8_UNREADABLE_CATEGORIES, start=1)
                ),
                risks=[],
            )
        ])
        plan = planner.plan_initial(task="build P6/M8", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.proposed == 9, (
            f"nine candidates were offered and the wave recorded {wave.proposed}. The number "
            "Product Driver could not read may never be smaller than the number it was offered"
        )
        assert wave.accepted_ids == []
        assert len(wave.contract_rejections) == 9, (
            "a candidate the HARNESS could not parse is not the same fact as one planning decided "
            f"against; {len(wave.contract_rejections)} were recorded as a contract failure"
        )
        assert wave.filtered_rejections == []
        assert plan.scenarios == []
        for rejected in wave.contract_rejections:
            assert rejected.id, "the candidate itself was deleted rather than kept"
            assert rejected.raw, "the proposal's own text was not retained"
            assert rejected.reasons, "the rejection records no reason"

        problems = planner.generation_problems()
        assert problems, "nine unreadable candidates produced no generation problem at all"
        text = " ".join(problems)
        assert "9 proposed" in text
        assert "0 accepted for execution" in text
        assert "9 invalid" in text
        assert REJECTED_CONTRACT.replace("_", "-") in text or "generation-contract" in text

    def test_a_full_green_m8_suite_still_cannot_accept_over_an_unreadable_wave(self, tmp_path):
        """The invariant that makes it a blocker rather than a note.

        This is bit for bit the run that ACCEPTed: the permanent M8 scenario passed and no generated
        case ran at all. It no longer can.
        """
        from neyma_product_driver.cli import _apply_suite_precedence
        from neyma_product_driver.models import Decision, EvaluatorDecision
        from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
        from neyma_product_driver.scenario_suite import (
            Origin,
            Outcome,
            ScenarioOutcome,
            SuiteResult,
        )

        planner = self._planner(tmp_path, [
            raw_payload(
                *(
                    self._m8_raw(f"S{i}-m8", category)
                    for i, category in enumerate(M8_UNREADABLE_CATEGORIES, start=1)
                ),
                risks=[],
            )
        ])
        planner.plan_initial(task="build P6/M8", unit=FakeUnit())
        problems = planner.generation_problems()

        passed = ScenarioOutcome(
            scenario_id="p6_m8_expectation",
            scenario_name="p6_m8_expectation",
            origin=Origin.PERMANENT,
            outcome=Outcome.PASSED,
            priority=Priority.P0,
            required=True,
            evidence_path="/runs/x/permanent",
            evidence_verified=True,
        )
        result = SuiteResult(
            outcomes=[passed], full_run=True, expected_required_ids=[passed.scenario_id]
        )

        # Without the problems, this is exactly the false green.
        assert evaluate_gate(result).status is GateStatus.VERIFIED
        assert evaluate_gate(result, generation_problems=problems).blocks_acceptance

        decision = _apply_suite_precedence(
            result,
            EvaluatorDecision(decision=Decision.ACCEPT, summary="good", observed_behavior=["saw it"]),
            "p6_m8_expectation",
            lambda _m: None,
            generation_problems=problems,
        )
        assert decision.decision is Decision.BLOCKED

    def test_the_four_counts_stay_separable_for_an_m8_wave(self, tmp_path):
        """proposed / accepted / filtered / invalid are four facts, and summing them is the defect."""
        planner = self._planner(tmp_path, [
            raw_payload(
                self._m8_raw("gen-m8-valid", "safety_invariant"),
                self._m8_raw("gen-m8-unreadable", "blind-window-became-overdue"),
                risks=[],
            )
        ])
        plan = planner.plan_initial(task="build P6/M8", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.proposed == 2
        assert wave.accepted_ids == ["gen-m8-valid"], (
            "the readable candidate was punished for its neighbour"
        )
        assert [r.id for r in wave.contract_rejections] == ["gen-m8-unreadable"]
        assert wave.filtered_rejections == []
        assert planner.generation_problems(), "a mixed wave stopped blocking"

    def test_an_honestly_empty_m8_wave_is_not_a_generation_problem(self, tmp_path):
        """The other half, and the reason this is not just "block whenever nothing ran"."""
        planner = self._planner(tmp_path, [{"risks": [], "scenarios": []}])
        plan = planner.plan_initial(task="build P6/M8", unit=FakeUnit())

        assert plan.waves[0].proposed == 0
        assert plan.waves[0].contract_rejections == []
        assert planner.generation_problems() == []

    def test_product_driver_core_does_not_special_case_m8(self):
        """The fix is general or it is not a fix.

        `P6-D46` was closed by making the taxonomy single-sourced and the rejection accounting
        honest — not by teaching the harness about a unit. A core module that names this unit would
        be a per-unit exception with a passing status.
        """
        core = DRIVER_ROOT / "neyma_product_driver"
        offenders = sorted(
            f.name
            for f in core.rglob("*.py")
            if "p6_m8_expectation" in f.read_text(encoding="utf-8")
            or "phase6_expectation" in f.read_text(encoding="utf-8")
        )
        assert not offenders, (
            f"Product Driver core names the M8 unit in {offenders}. Permanent scenarios, tasks and "
            "readiness tests carry unit knowledge; the harness carries none"
        )


# --------------------------------------------------------------------------
# 9. M8 is scoped as a unit, and cannot move the phase
# --------------------------------------------------------------------------


@pytest.fixture
def m8_repo(tmp_path: Path) -> PhaseRepo:
    """A phase in progress, one unit being built, a stated review rule."""
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/expectation.py", "# the unit under construction\n")
    repo.commit_all("the M8 candidate")
    return repo


class TestM8IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m8(self, m8_repo: PhaseRepo):
        scope = m8_repo.scope(M8_TASK)
        assert scope.scope_id == "P6/M8"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(self, m8_repo: PhaseRepo):
        """The task discusses P6 at length. Discussing a phase is not claiming it, and a run that
        inherited the phase's bar would be held to six units that do not exist."""
        scope = m8_repo.scope(M8_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m8_repo: PhaseRepo):
        scope = m8_repo.scope(M8_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(self, m8_repo: PhaseRepo):
        rendered = m8_repo.scope(M8_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "enables nothing in production" in rendered


class TestM8CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m8_repo: PhaseRepo
    ):
        scope = m8_repo.scope(M8_TASK)
        completion = scoped_completion(scope, TaskResult.ACCEPTED, phase_accepted=True)
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M8"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m8_repo: PhaseRepo):
        completion = scoped_completion(m8_repo.scope(M8_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    def test_a_builder_claiming_p6_is_complete_is_caught(self, m8_repo: PhaseRepo):
        audit = m8_repo.audit(
            "M8 is implemented and verified. With M8 landed, P6 is COMPLETE and P7 is now "
            "unblocked.\n",
            M8_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_a_builder_claiming_production_enablement_is_caught(self, m8_repo: PhaseRepo):
        audit = m8_repo.audit(
            "M8 is implemented and verified. The overdue-load tracker is now enabled for live "
            "traffic.\n",
            M8_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_the_task_names_every_prohibited_expansion(self):
        """The M8-specific temptations, each named in the task's `Do not` list.

        M8's are different from M7's: this unit's coverage record wants a health probe that is P9+
        adapter work, its `EX-3`/`EX-7` rows end in an M9 Exception, its `V10` wants a business
        threshold, and its product form is a live tracking view.
        """
        for prohibition in (
            "M9–M13",
            "M9 Exception",
            "M10 Compensation",
            "M11 Policy",
            "M12 Rule",
            "P7 or later",
            "provenance and evidence platform",
            "Evidence Store",
            "V10",
            "V6",
            "channel health probe, poller, coverage importer or observability monitor",
            "freight workflows",
            "invoice automation",
            "cargo claims",
            'any live tracking, SLA, "what is late" or exception-queue UI',
            "email_triage.py",
            "action_callback.py",
            "follow_up.py",
            "mailbox_intake",
            "production autonomy",
            "live production effects",
            "production integrations",
            "legacy cleanup campaign",
            "broad documentation cleanup",
            "P6-D40",
            "push, publish or deploy",
        ):
            assert prohibition in M8_TASK, f"the task never forbids {prohibition!r}"
        assert "weaken **P3, P4 or P5**" in M8_TASK
        assert "polish **M1, M2, M3, M4, M5, M6 or M7**" in M8_TASK
        assert "second timer mechanism" in M8_TASK_FLAT, (
            "the task never forbids a second timer mechanism, which is the shape a sweep arrives in "
            "for the one P6 unit whose whole lifecycle is timer-driven"
        )
        assert "one-connection-per-thread concurrency correction" in M8_TASK_FLAT, (
            "the task never protects the landed P3/P4 correction CURRENT.md says must not be reworked"
        )

    def test_p6_d40_is_named_as_conditional_rather_than_forbidden_outright(self):
        """The one prohibition that is not absolute."""
        assert "unless a real guard in it mechanically blocks this unit" in M8_TASK_FLAT

    def test_the_task_records_the_known_nonblocking_items_without_ordering_a_campaign(self):
        for item in ("P6-D47", "P6-D48", "P6-D49", "P6-D50", "P6-D51", "P6-D52"):
            assert item in M8_TASK, f"the known nonblocking item {item} is never recorded"
        assert "Each is recorded." in M8_TASK
        assert "STOP and report the conflict rather than guessing" in M8_TASK_FLAT

    def test_the_task_allows_exactly_one_blocking_prerequisite_and_requires_it_reported(self):
        assert "smallest blocking prerequisite" in M8_TASK_FLAT
        assert "identify it explicitly" in M8_TASK_FLAT


# --------------------------------------------------------------------------
# 10-11. The loop owns M8 end to end
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m8_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m8_repo.root, m8_repo.scope(M8_TASK), unit=m8_repo.unit()
        )
        assert requirement.required
        assert requirement.from_repository_authority

    def test_the_task_states_the_tier_and_says_why_it_took_the_higher_one(self):
        """CLAUDE.md §7: "When genuinely torn between two tiers, take the higher one once and say
        so."

        A state machine is tier 2 by itself. M8 also lands a MIGRATION, is load-bearing for TENANT
        ISOLATION, and decides whether Neyma accuses a counterparty of a failure or admits its own
        blindness — a claim made about someone outside the company, which is the highest-consequence
        thing this machine can produce.
        """
        assert "tier-1" in M8_TASK
        assert "migration" in M8_TASK_FLAT
        assert "tenant isolation" in M8_TASK_FLAT
        assert (
            "whether Neyma accuses a counterparty of a failure or admits its own blindness"
            in M8_TASK_FLAT
        )
        assert "take the higher tier once and say so, and this file says so" in M8_TASK_FLAT


class TestTheLoopOwnsM8EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m8_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session that wrote the code,
        with its evidence path intact."""
        builder = FakeBuilder(m8_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m8_repo, tmp_path, task=M8_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        assert "INDEPENDENT REVIEW" in builder.prompts[1]
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED

    async def test_the_corrected_tree_gets_a_brand_new_reviewer(
        self, m8_repo: PhaseRepo, tmp_path: Path
    ):
        """The reviewer must be a lineage that did not build M8, and the second reviewer must read
        the CORRECTED tree rather than the one the first one read."""
        builder = FakeBuilder(m8_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m8_repo, tmp_path, task=M8_TASK, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        assert len(set(reviewer.session_ids)) == 2, "the same reviewer session was reused"
        first = reviewer.bindings[0]["fingerprint"]
        second = reviewer.bindings[1]["fingerprint"]
        assert not first.matches(second), "the second reviewer read the same tree as the first"
        assert result.satisfying_review.fingerprint.matches(second)

    async def test_an_accept_is_scoped_m8_acceptance_and_never_p6_complete(
        self, m8_repo: PhaseRepo, tmp_path: Path
    ):
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m8_repo, tmp_path, task=M8_TASK, reviewer=reviewer)

        assert result.status is RunStatus.ACCEPTED
        assert result.audit is not None, "the run accepted without a completion audit"
        completion = result.audit.completion
        assert completion is not None
        assert completion.task_scope == "P6/M8"
        assert completion.task_result in {TaskResult.ACCEPTED, TaskResult.VERIFIED}
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_accepted is False
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    async def test_the_run_stops_at_m8_and_never_walks_into_m9(
        self, m8_repo: PhaseRepo, tmp_path: Path
    ):
        """Two halves of the same guarantee: the task forbids it in words, and the loop ends at its
        own scoped verdict rather than picking up the next unit."""
        assert "Stop at verified M8. Do not automatically continue into M9." in M8_TASK
        assert "begin **M9–M13**" in M8_TASK

        reviewer = FakeReviewer([supported()])
        result, store = await drive(m8_repo, tmp_path, task=M8_TASK, reviewer=reviewer)
        assert result.status is RunStatus.ACCEPTED
        assert result.audit.completion.task_scope == "P6/M8"

        journal = RunJournal(run_id=store.run_id, task=M8_TASK)
        journal.record_outcome(run_status="ACCEPTED")
        summary = journal.personal_summary()
        for forbidden in ("M9", "begin the next unit", "continue into"):
            assert forbidden not in summary.split("### 8. The ONE exact next move")[1], (
                f"the next move points past M8 ({forbidden!r})"
            )


# --------------------------------------------------------------------------
# 12. The founder summary says what M8 actually does, in normal language
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


def _m8_journal(**outcome) -> RunJournal:
    scenario = load_scenario(M8_PATH)
    journal = RunJournal(run_id="r-m8", task=M8_TASK)
    journal.task_scope_id = "P6/M8"
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


class TestTheFounderSummaryExplainsM8:
    def test_it_states_the_product_impact_in_normal_language(self):
        """The scenario description is what a founder reads to learn what the unit is for. It has to
        be a brokerage sentence, not a machine one."""
        scenario = load_scenario(M8_PATH)
        text = " ".join(scenario.description.split()).lower()
        for phrase in ("pod", "carrier", "deadline", "human", "denver"):
            assert phrase in text, f"the description never mentions {phrase!r}"
        assert "ships dark" in text
        assert "we were not watching" in text, (
            "the description never states the honesty distinction, which is the entire unit"
        )

    def test_it_never_says_p6_moved(self):
        journal = _m8_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary()
        assert "P6 is COMPLETE" not in summary
        assert "P6 COMPLETE" not in summary

    def test_it_does_not_imply_a_live_tracker_or_production(self):
        """A negative that has to be written carefully.

        "enables nothing in production" is a sentence this summary SHOULD carry, so a bare search for
        "in production" fails on the correct text. What must not appear is an ENABLEMENT claim, and
        each phrase below is one.
        """
        journal = _m8_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary().lower()
        for claim in (
            "enabled for live traffic",
            "live traffic is",
            "production-ready",
            "enabled for customers",
            "tracking is live",
            "loads are being tracked",
            "overdue alerts are",
        ):
            assert claim not in summary, f"the summary implies {claim!r}"
        # And the thing it must actively say, because "dark" is the whole posture.
        assert "enables nothing in production" in summary

    def test_no_founder_decision_says_none(self):
        journal = _m8_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        journal.record_stop(reason="M8 verified.", founder_decision_required="none")
        assert journal.founder_decision_required == ""


# --------------------------------------------------------------------------
# 13. THE MUTATION GUARD — does this file actually fail when the assertion is removed?
# --------------------------------------------------------------------------


def _mutate(edit) -> "object":
    """Load a copy of the SHIPPED M8 scenario with one load-bearing thing weakened.

    `edit` receives the raw YAML mapping and changes it in place. Nothing is written to the
    scenarios directory: the mutant lives in memory and is parsed through the real loader, so a
    weakening the loader itself refuses raises here rather than returning a Scenario.
    """
    import tempfile

    raw = copy.deepcopy(yaml.safe_load(M8_PATH.read_text(encoding="utf-8")))
    edit(raw)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m8_mutant.yaml"
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


class TestThisFileFailsWhenTheGuardIsRemoved:
    """A readiness test never seen to fail is a decoration.

    Every case below weakens the SHIPPED scenario in one specific way and then runs the REAL
    assertion from earlier in this file against the weakened copy — not a paraphrase of it. If the
    assertion has been loosened into something that passes either way, these turn green and the
    failure is visible here rather than six weeks later in a run that verified nothing.

    `CLAUDE.md` §6: *mutate to prove a guard works when you are writing a guard that protects a
    tier-1 invariant. A guard never seen to fail is a decoration, and a mutation that does not
    reintroduce the real defect proves nothing.*
    """

    def test_the_baseline_mutant_is_the_shipped_file_unchanged(self, m8):
        """The control. If `_mutate` cannot round-trip the file, every result below is noise."""
        unchanged = _mutate(lambda raw: None)
        assert unchanged.name == m8.name
        assert len(unchanged.commands) == len(m8.commands)
        assert len(unchanged.expect_state) == len(m8.expect_state)
        assert len(unchanged.verifies) == len(m8.verifies)
        assert unchanged.expect_visible == m8.expect_visible
        assert unchanged.forbidden == m8.forbidden

    def test_dropping_a_canonical_case_turns_the_coverage_assertion_red(self):
        """Remove `absent-coverage-is-not-health` from the probe's asserted vocabulary and the family
        that decides whether "no errors were logged" became "everything was fine" silently stops
        being verifiable."""
        mutant = _mutate(
            lambda raw: _named(raw, "commands", "the M8 probe can exercise every canonical risk family")
            ["expect_contains"].remove("absent-coverage-is-not-health")
        )
        cases = [c for c in mutant.commands if c.run == f"{PROBE} --list-cases"][0].expect_contains
        with pytest.raises(AssertionError, match="risk families the scenario never asserts exist"):
            TestTheM8BaseScenario().test_it_asserts_a_risk_family_for_every_canonical_obligation(
                list(cases)
            )

    def test_dropping_a_coverage_value_turns_the_coverage_axis_assertion_red(self):
        """Remove `coverage-absent` and the fail-closed half of `M-32` becomes unreachable from a
        generated case: the generator can still make the channel healthy or down, and can no longer
        ask what happens when nobody recorded anything at all."""
        mutant = _mutate(
            lambda raw: _named(
                raw, "commands",
                "the M8 probe exposes a bounded, closed dimension vocabulary to vary cases with",
            )["expect_contains"].remove("coverage-absent")
        )
        dims = [
            c for c in mutant.commands if c.run == f"{PROBE} --list-dimensions"
        ][0].expect_contains
        cases = [c for c in mutant.commands if c.run == f"{PROBE} --list-cases"][0].expect_contains
        with pytest.raises(AssertionError, match="coverage-absent"):
            TestTheM8BaseScenario().test_the_coverage_axis_exists_because_it_is_the_whole_unit(
                list(dims), list(cases)
            )

    def test_dropping_the_timezone_axis_turns_the_dst_assertion_red(self):
        """Entity §43(d) is one of five acceptance criteria and `F-25` is a named mandate. Without
        the axis the DST case is a fixed point the generator cannot vary."""
        mutant = _mutate(
            lambda raw: _named(
                raw, "commands",
                "the M8 probe exposes a bounded, closed dimension vocabulary to vary cases with",
            )["expect_contains"].remove("--timezone")
        )
        dims = [
            c for c in mutant.commands if c.run == f"{PROBE} --list-dimensions"
        ][0].expect_contains
        cases = [c for c in mutant.commands if c.run == f"{PROBE} --list-cases"][0].expect_contains
        with pytest.raises(AssertionError):
            TestTheM8BaseScenario().test_the_timezone_axis_exists_so_the_dst_case_is_reachable(
                list(dims), list(cases)
            )

    def test_dropping_indeterminate_from_the_ddl_turns_the_state_assertion_red(self):
        """The honesty split collapses into one state, and the DDL stops noticing."""
        def edit(raw):
            check = [c for c in raw["expect_state"] if "state vocabulary" in c["command"]][0]
            check["contains"] = [c for c in check["contains"] if c != "'INDETERMINATE'"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="INDETERMINATE is not asserted in the CHECK"):
            TestPersistedStateIsTheOracle().test_the_six_states_are_asserted_and_there_is_no_seventh(
                mutant
            )

    def test_dropping_the_forbidden_state_turns_the_state_assertion_red(self):
        """Remove `'TIMED_OUT'` from `not_contains` and the state that means "the deadline passed"
        WITHOUT saying whether anyone was watching can be added by a build session that thought it
        was being tidy."""
        def edit(raw):
            check = [c for c in raw["expect_state"] if "state vocabulary" in c["command"]][0]
            check["not_contains"] = [n for n in check["not_contains"] if n != "'TIMED_OUT'"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="invented TIMED_OUT state"):
            TestPersistedStateIsTheOracle().test_the_six_states_are_asserted_and_there_is_no_seventh(
                mutant
            )

    def test_weakening_the_honesty_check_to_a_mere_reference_turns_that_assertion_red(self):
        """The difference between "we looked something up" and "we can prove we were watching".

        A CHECK that requires a `coverage_ref` and never requires it be HEALTHY lets an `OVERDUE`
        row exist over a window that was down, which is the accusation `M-32` exists to prevent.
        """
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "OVERDUE requires healthy coverage and the human-owned states require an owner, "
                "as CHECKs",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "OVERDUE requires healthy coverage: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="prove we were watching"):
            TestPersistedStateIsTheOracle().test_the_honesty_invariant_is_asserted_as_a_database_check(
                checks
            )

    def test_dropping_the_coverage_default_check_turns_the_coverage_assertion_red(self):
        """A DEFAULT on the health column is the exact mechanism by which "we did not record
        anything" silently becomes "it was fine"."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the observation-coverage record M8 reads is persisted, tenant-first and windowed",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "coverage health is never defaulted: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle(
            ).test_the_coverage_record_is_asserted_persisted_windowed_and_never_defaulted(checks)

    def test_dropping_raised_from_the_live_index_turns_the_index_assertion_red(self):
        """An index that does not name `RAISED` in its `WHERE` is not the duplicate-prevention index
        entity §17 describes, and two live expectations fit one owed observation."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "one live RAISED expectation per key is a PARTIAL UNIQUE index, tenant-first",
            )
            check["contains"] = [c for c in check["contains"] if c != "RAISED"]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="PARTIAL on RAISED"):
            TestPersistedStateIsTheOracle(
            ).test_one_live_expectation_is_asserted_as_a_partial_unique_index_tenant_first(checks)

    def test_dropping_the_coverage_foreign_key_turns_the_fk_assertion_red(self):
        """Without the FK, `coverage_ref` is a text column and entity §16's CHECK has nothing to
        enforce against."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the owner, the discharging observation and the coverage record are FOREIGN KEYS",
            )
            check["contains"] = [
                c for c in check["contains"]
                if c != "coverage_ref is FK-backed into observation_coverage: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle(
            ).test_the_owner_the_observation_and_the_coverage_are_asserted_as_foreign_keys(checks)

    def test_removing_the_probe_from_the_regression_claim_turns_the_mapping_assertion_red(self):
        """The exact defect that blocked the M6 run, reintroduced.

        The claim still requires the five seam literals; it just stops naming the only command that
        can print them. Nothing about the product changed, and the gap would stand forever.
        """
        def edit(raw):
            claim = _claim(raw, "regression")
            claim["checks"] = [c for c in claim["checks"] if c != PROBE_CHECK]

        mutant = _mutate(edit)
        assert claims_needing_the_probe(mutant), (
            "the probe was removed from a claim that requires its narration and the predicate did "
            "not notice — which is the M6 mapping defect, undetected"
        )
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_regression_claim_names_the_probe_that_proves_the_seams_are_intact(mutant)

    def test_deleting_the_seam_literals_turns_the_still_required_assertion_red(self):
        """The other way to make a coverage gap go away is to stop asking for the proof."""
        def edit(raw):
            claim = _claim(raw, "regression")
            claim["observations"] = [
                o for o in claim["observations"] if o not in DARK_POSTURE_LITERALS
            ]
            raw["expect_visible"] = [
                v for v in raw["expect_visible"] if v not in DARK_POSTURE_LITERALS
            ]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_dark_posture_literals_are_still_required_somewhere(mutant)

    def test_misattributing_a_declared_literal_is_refused_at_load_time(self):
        """The statically decidable half, which never gets as far as this file.

        `problems: []` is DECLARED by two named state checks. A claim requiring it while naming
        neither could never be established, and the loader now says so.
        """
        def edit(raw):
            claim = _claim(raw, "cross_tenant")
            claim["checks"] = [PROBE_CHECK]

        with pytest.raises(ValueError, match="could never be established"):
            _mutate(edit)

    def test_an_invented_risk_category_is_refused_at_load_time(self):
        """`P6-D46`'s shape, from the permanent-scenario side."""
        def edit(raw):
            _claim(raw, "safety_invariant")["risk_category"] = "blind-window-became-overdue"

        with pytest.raises(ValueError, match="unknown risk_category"):
            _mutate(edit)

    def test_declaring_a_foreign_family_turns_the_undeclared_assertion_red(self):
        """`approval_required` is a canonical category, so it loads — and declaring it here would
        wave through a risk about M4's behaviour on M8's permanent scenario."""
        def edit(raw):
            _claim(raw, "authorization")["risk_category"] = "approval_required"

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="another unit's"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_effect_and_approval_families_are_deliberately_left_undeclared(mutant)

    def test_deleting_a_forbidden_marker_turns_the_failure_assertion_red(self):
        """A mutant with no marker is a mutant nothing observes."""
        mutant = _mutate(
            lambda raw: raw["forbidden"].remove("### ABSENT COVERAGE TREATED AS HEALTHY ###")
        )
        with pytest.raises(AssertionError, match="ABSENT COVERAGE TREATED AS HEALTHY"):
            TestTheM8BaseScenario().test_it_refuses_the_failures_m8_exists_to_prevent(mutant)

    def test_dropping_the_population_proof_turns_the_unregistered_name_sweep_red(self):
        """A negative assertion needs a proven population (`CLAUDE.md` §6).

        `unregistered names in the machine: []` prints an empty list against a file that does not
        exist. Without `machine source: present` beside it, the sweep reads as a pass over nothing.
        """
        def edit(raw):
            check = _named(
                raw, "expect_state", "M8 uses the seven registered F8 contracts and invents no eighth"
            )
            check["contains"] = [c for c in check["contains"] if c != "machine source: present"]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="proven population"):
            TestPersistedStateIsTheOracle(
            ).test_the_seven_f8_contracts_are_used_and_no_eighth_is_minted(checks)

    def test_widening_the_invention_sweep_onto_the_refusals_turns_that_assertion_red(self):
        """The sweep must not flag the machine's own REFUSAL of a sweep — or staying green would
        mean deleting the `GR-1` case that proves a sweep-close is illegal."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "no sweep, reaper, deletion or unregistered expectation state was invented",
            )
            check["command"] = check["command"].replace("(?!_?refuse|_?reject|_?illegal)", "")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="force the GR-1 case out of existence"):
            TestPersistedStateIsTheOracle(
            ).test_inventing_a_sweep_or_an_unregistered_state_is_a_scenario_failure(mutant, checks)

    def test_refusing_an_illegal_fault_as_unknown_turns_that_distinction_red(self):
        """M8 owns its own illegal set, so `overdue-without-coverage` must be refused by the MACHINE
        under `GR-1`, not by the argument parser. A probe that made it unreachable would have proved
        only that its own vocabulary is closed."""
        def edit(raw):
            reopen = _named(
                raw, "commands",
                "a reopen fault does not exist, because reopening an expectation is N/A",
            )
            reopen["run"] = reopen["run"].replace("reopen-expectation", "overdue-without-coverage")

        mutant = _mutate(edit)
        dims = [
            c for c in mutant.commands if c.run == f"{PROBE} --list-dimensions"
        ][0].expect_contains
        with pytest.raises(AssertionError, match="refused as an UNKNOWN fault"):
            TestTheM8BaseScenario(
            ).test_the_illegal_faults_are_in_the_vocabulary_rather_than_refused_as_unknown(
                mutant, list(dims)
            )

    def test_dropping_the_durable_timer_assertion_turns_the_timer_guard_red(self):
        """`EX-3`, `EX-3i` and `EX-7` are all `T`-triggered. Stop asserting the machine schedules
        through `DurableTimers` and an in-memory sleep becomes indistinguishable from a deadline."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the deadline rides P5's existing durable timers rather than a second timer mechanism",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "M8 schedules through DurableTimers: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle().test_the_deadline_is_asserted_to_ride_p5s_durable_timers(
                checks
            )

    def test_dropping_a_deliverable_turns_the_fixture_assertion_red(self):
        """Without the fixture a run against a repository where M8 does not exist could report a
        verified M8."""
        mutant = _mutate(lambda raw: raw["fixtures"].remove("src/freight_recon/expectation.py"))
        with pytest.raises(AssertionError, match="is not required to exist"):
            TestTheM8BaseScenario().test_it_requires_the_canonical_deliverables_to_exist(mutant)

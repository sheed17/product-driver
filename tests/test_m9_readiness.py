"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M9?

M9 is the Exception: something that needs a human, and the one machine in Neyma whose whole job is
to make sure it reaches a named person and is never quietly forgotten. Entity §3 states the purpose
in terms — *to ensure that everything Neyma cannot resolve deterministically reaches a named human
owner and is never closed by silence* — so the question this file answers is not "does the YAML
parse" but whether the whole loop can own the unit end to end without the founder standing in the
middle of it.

The unit's whole character is three sentences, and every check below traces back to one of them:

    an Exception is SOMETHING THAT NEEDS A HUMAN
    it reaches a NAMED HUMAN OWNER FROM CREATION, and it is NEVER CLOSED BY SILENCE
    an exception closed without a decision is not closed — it is FORGOTTEN

Not an error log. Not an alert. Not an issue-tracker row. Not auto-closable, not outlivable. Every
other machine here has a state that means *"a human has to look at this"* — M3's `UNKNOWN_OUTCOME`,
M5's `UNPARSEABLE`, M6's `AMBIGUOUS`, M7's `OPEN`, M8's `OVERDUE` — and M9 is the machine those
states point at. The single most likely way this unit gets built wrong is that a queue looked untidy
and something quietly closed it: a TTL, a nightly sweep, an inactivity auto-close, an `EXPIRED`
state. Each of those is a mechanism for forgetting wearing the costume of hygiene.

Thirteen questions, each answered mechanically rather than by reading a document and agreeing with
it:

1.  does the M9 base scenario parse, does it hold the pieces the generator needs (deterministic
    operation, a closed mutation axis with the actor and decision-ref axes this unit turns on,
    persisted-state oracles, regression anchors), and do the scenario and the task state the SAME
    contract;
2.  does every declared risk name a command that could actually emit the observation it requires —
    the `P6-D-run-20260825` mapping defect, refused ahead of time;
3.  does the scenario measure the DATABASE rather than the probe's narration for the invariants a
    green test suite can state while the database enforces none of them — above all the
    `RESOLVED`-requires-a-`decision_ref` CHECK, which is where this machine's honesty actually lives,
    and does it ATTEMPT the forbidden writes against a live database with a derived positive control
    rather than reading the DDL and believing it;
4.  does the task preserve the six recorded authority conflicts rather than resolving them;
5.  does the task get the SEAMS right — M9's `decision_ref` resolver belongs to M1, its `source_ref`
    points at eight-plus aggregate types of which four have no table, five landed machines deferred
    a *"→ Exception"* seam here by name, the freeze has no mechanism M9 owns, and the Sev-0 brake
    belongs to the F14 detectors at the source;
6.  is the M9 command vocabulary safe, and actually visible to the generator rather than truncated
    out of the brief;
7.  can dynamic generation close an M9 coverage gap WITHOUT inventing a command, and is an invented
    one refused;
8.  is `P6-D46` still closed — canonical taxonomy only, no candidate lost to it, and the four counts
    still separable;
9.  is M9 scoped as `P6/M9` rather than as P6 phase completion, and can accepting it score a P6
    acceptance criterion or unlock P7 (it cannot);
10. is an integrated independent review OWED when the repository's own authority says so;
11. do grounded reviewer findings return to the SAME builder, and does a corrected tree get a FRESH
    reviewer, and does the run stop before M10;
12. does the founder summary explain M9's product impact in simple terms — and never contradict its
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
M9_PATH = SCENARIOS_DIR / "p6_m9_exception.yaml"
M9_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m9.md"
M9_TASK = M9_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match against this: the task is
#: a wrapped markdown document, and a phrase that happens to straddle a line break is not a phrase
#: the task failed to state.
M9_TASK_FLAT = " ".join(M9_TASK.split())
PROBE = ".venv/bin/python scripts/probe_phase6_exception.py"
#: The `name:` the base scenario gives the bare probe run — the deterministic basic M9 operation,
#: and the only check in the file that drives the machine and narrates what it saw.
PROBE_CHECK = "drive the Exception machine through a brokerage narrative, and attack it"

#: A persisted-state command the base scenario already carries, so a generated case that reuses it is
#: choosing an approved oracle rather than authoring one.
STATE_ORACLE = next(
    check.command
    for check in load_scenario(M9_PATH).expect_state
    if "schema_readiness_problems" in check.command
)

#: The canonical M9 deliverables. A different name is a scenario failure, not a style preference —
#: the permanent scenario looks for exactly these.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/exception.py",
    "src/freight_recon/migrations/phase6_exceptions.py",
    "eval/tests/test_phase6_exception.py",
    "scripts/probe_phase6_exception.py",
    "scripts/mutate_phase6_exception.py",
)

#: The five canonical exception states (registry §4 / M9, target spec §12.9). Not four, not six.
STATES: tuple[str, ...] = ("OPEN", "ACKNOWLEDGED", "AGEING", "ESCALATED", "RESOLVED")

#: Terminal, per machine §8. `RESOLVED` and nothing else — and every other state is human-owned,
#: which is what machine §10 means by *"Recoverable. none"*.
TERMINAL_STATES: tuple[str, ...] = ("RESOLVED",)
HUMAN_OWNED_STATES: tuple[str, ...] = ("OPEN", "ACKNOWLEDGED", "AGEING", "ESCALATED")

#: States a build session might reach for, and that the corpus says do not exist. `CANCELLED` is
#: first because entity §25 DISCUSSES cancellation while registry §4 gives M9 no state to hold it —
#: that is `M9-AQ-2`, and minting the state is how a build session settles it by accident. `EXPIRED`
#: is second because every neighbouring machine has one and entity §26 says this one NEVER does.
#: The six after `SUPERSEDED` are the machine header's own list: the brief's finer terms, which are
#: `sub_status` FIELDS — and `AWAITING_HUMAN` is doubly forbidden, because it is M1's REGISTERED
#: state and registry's binding header forbids a local synonym.
FORBIDDEN_STATES: tuple[str, ...] = (
    "CANCELLED",
    "EXPIRED",
    "TIMED_OUT",
    "STALE",
    "CLOSED",
    "AUTO_CLOSED",
    "DISMISSED",
    "REOPENED",
    "SUPERSEDED",
    "TRIAGE",
    "ASSIGNED",
    "INVESTIGATING",
    "AWAITING_EXTERNAL",
    "AWAITING_HUMAN",
    "RESOLUTION_PROPOSED",
)

#: The canonical transition ids. The task must require these rows, with these ids, rather than an
#: alternative lifecycle that "achieves the same thing". `AC-MACH-901..907` — seven, not six.
TRANSITIONS: tuple[str, ...] = ("EC-1", "EC-2", "EC-3", "EC-4", "EC-5", "EC-6", "EC-7")

#: The six registered F9 event contracts. `event_contracts_data.json` carries exactly these six, and
#: `events/registry.md` is by its own header THE SOLE CANONICAL LIST — so a seventh `Exception*` name
#: is defective by the registry's own definition.
F9_EVENTS: tuple[str, ...] = (
    "ExceptionRaised",
    "ExceptionAcknowledged",
    "ExceptionAgeing",
    "ExceptionEscalated",
    "ExceptionSeverityChanged",
    "ExceptionResolved",
)

#: Names a build session invents when it wants an Exception to stop being an obligation.
FORBIDDEN_EVENTS: tuple[str, ...] = (
    "ExceptionCancelled",
    "ExceptionExpired",
    "ExceptionClosed",
    "ExceptionReopened",
    "ExceptionAutoClosed",
    "ExceptionTimedOut",
    "ExceptionSuperseded",
)

#: The three-member severity vocabulary (entity §12). Not four, and never defaulted.
SEVERITIES: tuple[str, ...] = ("SEV0", "SEV1", "SEV2")

#: Nine `risk_category` values in the shape `P6-D46`'s real nine had: each a plausible, well-meant
#: DESCRIPTION OF A SPECIFIC DEFECT rather than a member of a closed family vocabulary — which is
#: what an unconstrained `{"type": "string"}` schema invites a model to write. These are M9's.
M9_UNREADABLE_CATEGORIES: tuple[str, ...] = (
    "closed-without-a-decision",
    "ownerless-exception",
    "autoclose-on-inactivity",
    "timer-resolved-it",
    "model-cleared-the-exception",
    "sixth-lifecycle-state",
    "previous-severity-lost",
    "sweep-closed-an-exception",
    "m1-resolver-rewritten",
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
def m9():
    return load_scenario(M9_PATH)


@pytest.fixture(scope="module")
def cases(m9) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m9.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m9) -> list[str]:
    listing = [c for c in m9.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def state_checks(m9) -> dict[str, list[str]]:
    return {check.name: list(check.contains) for check in m9.expect_state}


# --------------------------------------------------------------------------
# 1. The base scenario, and its contract with the task
# --------------------------------------------------------------------------


class TestTheM9BaseScenario:
    def test_it_parses_and_is_a_dark_p6_backend_scenario(self, m9):
        assert m9.name == "p6_m9_exception"
        assert m9.phase == "P6"
        assert m9.mode == "backend"
        # M9 ships dark: no service, no HTTP surface, no browser, and above all no oversight queue —
        # the product form of this unit is an EXCEPTION QUEUE WITH OWNERS AND NOTIFICATIONS, and that
        # queue is precisely the thing that must not arrive with the engine primitive.
        assert not m9.services and not m9.requests and m9.browser is None
        assert not m9.app_url

    def test_it_requires_the_canonical_deliverables_to_exist(self, m9):
        """A run against a repository where M9 does not exist yet must not be able to report a
        verified M9."""
        for path in DELIVERABLES:
            assert path in m9.fixtures, f"{path} is not required to exist"

    def test_the_probe_is_approved_bare_so_every_case_tail_is_reachable(self, m9):
        """The whole `--case` interface rests on this one entry.

        Approval matches by prefix, so approving the bare probe approves every argument tail that
        composes no shell. Approving only `probe.py --list-cases` would approve exactly that string
        and nothing else, and the generator would have no focused entry point at all.
        """
        assert any(c.run == PROBE for c in m9.commands), (
            "the bare probe invocation is missing; without it a generated "
            f"'{PROBE} --case X' is not an argument tail of any approved entry"
        )

    def test_it_asserts_a_risk_family_for_every_canonical_obligation(self, cases):
        """One family per canonical obligation, checked by name.

        This list is the contract between `tasks/neyma_p6_m9.md` and this file; a family missing from
        either is a family the generator cannot reach and the builder was never asked to build.
        """
        required = {
            # EC-1 — the raise, and the named human who owns it from creation
            "raise-creates-open-with-a-named-human-owner",
            "an-exception-cannot-be-raised-without-an-owner",
            "an-ownerless-exception-is-structurally-impossible",
            "the-owner-is-an-active-human-of-this-tenant",
            "an-offboarded-human-cannot-own-a-new-exception",
            "a-model-cannot-own-an-exception",
            "raise-records-severity-and-the-source-that-raised-it",
            "an-exception-cannot-be-raised-without-a-severity",
            "an-exception-cannot-be-raised-without-a-source-ref",
            "the-source-kind-is-a-closed-vocabulary",
            # EC-1 — the PERMANENT-failure half
            "a-permanent-auth-failure-raises-immediately-with-zero-retries",
            "a-permanent-config-failure-raises-immediately-with-zero-retries",
            "a-transient-failure-is-not-a-permanent-classification",
            "the-failure-classification-is-supplied-never-inferred-from-a-message",
            # EC-2 — acknowledgement proves SEEN
            "an-authenticated-human-acknowledges-the-exception",
            "acknowledgement-records-the-actor",
            "acknowledgement-proves-seen-not-resolved",
            "a-model-cannot-acknowledge-an-exception",
            "a-system-actor-cannot-acknowledge-an-exception",
            "an-acknowledged-exception-still-ages",
            # EC-3 / EC-6 — resolution authority, which is the unit
            "resolution-requires-a-decision-ref",
            "closure-without-a-decision-ref-is-structurally-impossible",
            "a-decision-ref-that-resolves-to-nothing-is-refused",
            "a-decision-ref-naming-a-non-human-decision-event-is-refused",
            "a-decision-ref-recorded-by-automation-is-refused",
            "a-model-can-never-resolve-an-exception",
            "an-escalated-exception-resolves-through-ec-6",
            "resolving-from-ageing-is-an-illegal-transition",
            "resolved-is-the-only-terminal-state",
            "a-resolved-exception-is-retained-never-deleted",
            # never closed by silence
            "inactivity-never-closes-an-exception",
            "autoclose-is-an-illegal-transition",
            "an-exception-never-expires",
            "an-exception-cannot-be-outlived",
            "no-sweep-or-reaper-closes-an-exception",
            "a-timer-can-age-or-escalate-but-never-resolve",
            # EC-4 / EC-5 — ageing and escalation
            "an-open-exception-ages-through-a-durable-timer",
            "an-acknowledged-exception-ages-through-a-durable-timer",
            "ageing-escalates-through-a-durable-timer-not-a-sweep",
            "ageing-and-escalated-remain-human-owned",
            "the-ageing-threshold-is-caller-supplied-not-a-business-default",
            "ageing-an-escalated-exception-is-illegal",
            "nothing-moves-a-resolved-exception",
            "restart-re-fires-the-ageing-timer",
            "restart-preserves-the-open-exception",
            "restart-after-escalation-reaches-the-canonical-state",
            "a-redelivered-timer-is-a-no-op",
            # EC-7 — severity is a field
            "severity-change-is-a-field-mutation-not-a-lifecycle-state",
            "severity-change-records-previous-and-new-severity-and-who",
            "severity-change-requires-a-reason",
            "a-model-cannot-change-severity",
            "severity-is-sev0-sev1-or-sev2-and-nothing-else",
            "changing-the-severity-of-an-ageing-exception-is-illegal",
            "a-sev0-exception-engages-no-brake-from-inside-m9",
            # the lifecycle vocabulary, and the sub_status trap
            "the-five-canonical-states-and-no-sixth",
            "sub-status-is-a-field-never-a-lifecycle-state",
            "there-is-no-cancelled-expired-or-timed-out-state",
            "a-retracted-cause-still-requires-an-event-and-a-decision-ref",
            # the freeze, and the checkpoint it feeds
            "a-freezing-exception-blocks-consequential-actions-on-the-entity",
            "not-every-exception-freezes-an-entity",
            "raise-and-freeze-commit-together-where-applicable",
            "a-persistence-failure-leaves-no-half-raised-exception",
            "state-and-event-co-commit",
            "resolution-unblocks-the-frozen-entity",
            "m9-mints-no-gate-decision",
            "an-exception-is-an-input-to-the-checkpoint-never-a-gate",
            # GR-3 / GR-4 — idempotency, concurrency, and the OPTIONAL dedup index
            "a-redelivered-raise-through-the-inbox-is-a-no-op",
            "the-open-exception-dedup-index-is-optional-and-recorded",
            "concurrent-raises-are-serialized-by-the-database",
            "occ-on-exception-version",
            "a-stale-version-cannot-overwrite-newer-state",
            # replay
            "replay-reconstructs-the-open-exception",
            "replay-rebuilds-the-current-severity-from-the-recorded-events",
            "replay-does-not-read-severity-from-the-current-row",
            "replay-keeps-a-frozen-entity-blocked",
            "replay-can-never-manufacture-resolution-authority",
            "replay-creates-no-new-authority-and-no-effect",
            # the brake M9 observes and never operates
            "exceptions-still-raise-under-a-brake",
            "m9-engages-no-brake-and-narrows-none",
            # [C-1] — tenancy
            "tenant-isolation",
            "cross-tenant-identical-source-ref",
            "cross-tenant-owner-fails-closed",
            "cross-tenant-source-fails-closed",
            "cross-tenant-decision-ref-fails-closed",
            "cross-tenant-queue-read-fails-closed",
            # the database and GR-1
            "inbox-idempotency",
            "database-invariants",
            "malformed-exception-fails-closed",
            "an-illegal-transition-persists-nothing-and-is-recorded",
            # the seams
            "the-m1-work-item-machine-is-not-rewritten",
            "the-m3-effect-authority-is-unchanged",
            "the-m5-observation-machine-is-not-rewritten",
            "the-m7-conflict-machine-is-not-rewritten",
            "the-m8-expectation-machine-is-not-rewritten",
            "m10-m11-and-m12-are-not-built",
        }
        missing = sorted(required - set(cases))
        assert not missing, f"risk families the scenario never asserts exist: {missing}"

    def test_it_declares_a_bounded_mutation_axis(self, dimensions):
        """Without this the M9 possibility space is a list of fixed points.

        M9 ships dark, so there is no service and no HTTP surface, and `parallel_requests` — the
        executor's only concurrency primitive — is unavailable. Ordering, concurrency, timing,
        duplication, crash and replay variation are reachable through the probe's arguments or not at
        all. See docs/SCENARIO-SPACE.md, gap G2.
        """
        for axis in ("--concurrency", "--delay-ms", "--repeat", "--tenants", "--age-ms",
                     "--severity", "--actor", "--decision-ref", "--freeze", "--seed", "--inject"):
            assert axis in dimensions, f"the axis {axis} is never asserted to exist"
        for fault in (
            "raise", "ownerless-raise", "model-owner", "offboarded-owner", "cross-tenant-owner",
            "missing-severity", "missing-source-ref", "cross-tenant-source", "invented-source-kind",
            "permanent-auth-failure", "permanent-config-failure", "transient-failure",
            "inferred-permanence", "retry-permanent",
            "acknowledge", "model-acknowledge", "system-acknowledge",
            "resolve", "resolve-without-decision-ref", "unresolvable-decision-ref",
            "non-human-decision-ref", "automated-decision-ref", "cross-tenant-decision-ref",
            "model-resolve", "resolve-from-ageing",
            "autoclose", "inactivity-close", "expire-exception", "sweep-close", "timer-resolve",
            "delete-exception",
            "age", "escalate", "age-escalated", "age-resolved",
            "severity-change", "severity-change-no-reason", "severity-change-no-previous",
            "model-severity-change", "invented-severity", "severity-change-ageing",
            "sub-status-as-state", "sixth-state", "cancel-exception",
            "freeze", "no-freeze", "freeze-split-commit", "unfreeze-without-resolution",
            "persistence-failure", "gate-mint", "brake-engage",
            "duplicate-raise", "concurrent-raise", "redelivered-raise", "redelivered-timer",
            "occ-exception", "stale-version", "restart-before-ageing", "restart-after-escalated",
            "replay", "replay-severity-from-row", "replay-manufacture-decision",
            "cross-tenant-queue", "malformed-exception", "reorder-stream",
        ):
            assert fault in dimensions, f"the fault {fault!r} is never asserted to exist"

    def test_the_actor_axis_exists_because_who_may_act_is_the_whole_unit(self, dimensions, cases):
        """The one axis without which M9 cannot be measured at all.

        M9's entire safety property is a question about WHO MAY ACT. `EC-2`'s trigger is `H` and F9
        says `actor_type=human`; `EC-3`/`EC-6` require an authenticated human; `EC-4`/`EC-5` are the
        only transitions a timer may drive and they may never resolve; and `GR-7`/`[C-6]`/entity §35
        say a model may NEVER close, resolve or auto-clear one. An axis a generator can point at
        `human`, `system`, `model` and `detector` over a case that must produce a DIFFERENT answer
        for each is what makes that a measurement rather than a belief.
        """
        assert "--actor" in dimensions
        for value in ("model-acknowledge", "system-acknowledge", "model-resolve",
                      "model-severity-change", "model-owner"):
            assert value in dimensions, f"the actor fault {value!r} is unreachable"
        assert "a-model-cannot-acknowledge-an-exception" in cases
        assert "a-model-can-never-resolve-an-exception" in cases
        assert "a-model-cannot-change-severity" in cases
        assert "a-model-cannot-own-an-exception" in cases
        assert "a-system-actor-cannot-acknowledge-an-exception" in cases

    def test_the_decision_ref_axis_exists_because_closure_is_the_load_bearing_act(
        self, dimensions, cases
    ):
        """`F-30`, `GR-14`, `K-1`, `AC-SAFE-024` and `AC-MACH-903`, in one flag.

        `absent` is the value that asks whether closure by silence is possible at all; `unresolvable`
        is the one that asks whether a bare string can close an exception ("closed with the string
        `done`" is the hole K-1 exists to shut); and `automated` is the one that asks whether a
        human-decision event TYPE emitted by automation can launder itself into authority (`ER-11`).
        """
        assert "--decision-ref" in dimensions
        for value in ("resolve-without-decision-ref", "unresolvable-decision-ref",
                      "non-human-decision-ref", "automated-decision-ref",
                      "cross-tenant-decision-ref"):
            assert value in dimensions, f"the decision_ref fault {value!r} is unreachable"
        assert "closure-without-a-decision-ref-is-structurally-impossible" in cases
        assert "a-decision-ref-that-resolves-to-nothing-is-refused" in cases
        assert "a-decision-ref-recorded-by-automation-is-refused" in cases

    def test_the_closure_by_silence_family_is_reachable_in_every_shape_it_arrives_in(
        self, dimensions, cases
    ):
        """Five different mechanisms, one failure. A TTL, a nightly sweep, a stale-exception reaper,
        an inactivity auto-close and an `EXPIRED` state are five names for the same thing — an
        obligation that quietly stops existing — and a build session reaches for each of them out of
        tidiness rather than out of disagreement."""
        for fault in ("autoclose", "inactivity-close", "expire-exception", "sweep-close",
                      "timer-resolve", "delete-exception"):
            assert fault in dimensions, f"the closure-by-silence shape {fault!r} is unreachable"
        for case in ("inactivity-never-closes-an-exception", "autoclose-is-an-illegal-transition",
                     "an-exception-never-expires", "an-exception-cannot-be-outlived",
                     "no-sweep-or-reaper-closes-an-exception",
                     "a-timer-can-age-or-escalate-but-never-resolve"):
            assert case in cases, f"the closure-by-silence case {case!r} is unreachable"

    def test_the_severity_axis_exists_so_ec_7_and_the_sev0_control_are_reachable(
        self, dimensions, cases
    ):
        """`EC-7` is the one transition that changes no state, and `SEV0` is the one value with a
        safety consequence attached — F9 says a Sev-0 exception auto-engages the brake AT ITS SOURCE,
        which is exactly why a rebuild that under-states severity is a safety loss and why M9 itself
        must engage nothing."""
        assert "--severity" in dimensions
        for value in ("severity-change", "severity-change-no-previous", "severity-change-no-reason",
                      "invented-severity", "model-severity-change"):
            assert value in dimensions, f"the severity fault {value!r} is unreachable"
        assert "severity-change-is-a-field-mutation-not-a-lifecycle-state" in cases
        assert "severity-change-records-previous-and-new-severity-and-who" in cases
        assert "a-sev0-exception-engages-no-brake-from-inside-m9" in cases
        assert "replay-rebuilds-the-current-severity-from-the-recorded-events" in cases

    def test_the_freeze_axis_exists_so_not_every_exception_freezes(self, dimensions, cases):
        """Entity §38 states the materiality condition in terms — *"not every Exception freezes an
        entity — only those that make a material field non-`consistent`"* — so an axis the generator
        can set to `material`, `immaterial` and `none` over cases that must behave DIFFERENTLY is
        what keeps an engine that froze everything it could not resolve from passing."""
        assert "--freeze" in dimensions
        for value in ("freeze", "no-freeze", "freeze-split-commit", "unfreeze-without-resolution"):
            assert value in dimensions, f"the freeze fault {value!r} is unreachable"
        assert "a-freezing-exception-blocks-consequential-actions-on-the-entity" in cases
        assert "not-every-exception-freezes-an-entity" in cases
        assert "raise-and-freeze-commit-together-where-applicable" in cases

    def test_the_age_axis_exists_so_both_thresholds_are_reachable(self, dimensions, cases):
        """Two `T`-triggered thresholds, one axis. `EC-4` fires at the age threshold and `EC-5` at
        the escalation threshold — and neither from-set contains `RESOLVED`, so an exception wound
        forward forever must still never be moved by a clock."""
        assert "--age-ms" in dimensions
        for value in ("age", "escalate", "age-escalated", "age-resolved"):
            assert value in dimensions, f"the ageing fault {value!r} is unreachable"
        assert "an-open-exception-ages-through-a-durable-timer" in cases
        assert "ageing-escalates-through-a-durable-timer-not-a-sweep" in cases
        assert "nothing-moves-a-resolved-exception" in cases
        assert "ageing-an-escalated-exception-is-illegal" in cases

    def test_the_mutation_axis_has_a_negative_control(self, m9):
        """A vocabulary that accepts anything is fuzzing in a costume."""
        negative = [c for c in m9.commands if "--inject not-a-real-fault" in c.run]
        assert negative, "nothing proves the fault vocabulary is actually closed"
        assert negative[0].expect_exit_code == 2, "a refusal must be a non-zero exit"
        assert "unknown fault" in negative[0].expect_contains
        assert "Traceback (most recent call last)" in m9.forbidden

    @pytest.mark.parametrize(
        "fault,section",
        [
            ("reopen-exception", "Reopening rules. N/A (a recurrence is a new Exception)"),
            ("correct-exception", "Correction rules. N/A."),
            ("supersede-exception", "Supersession rules. N/A"),
        ],
    )
    def test_a_fault_the_corpus_calls_n_a_is_refused_as_unknown(self, m9, fault, section):
        """The three M9-specific negative controls, each backed by an explicit canonical `N/A`.

        Entity §27 says reopening is `N/A` (*"a recurrence is a new Exception"*), §23 says correction
        is `N/A`, and §24 says supersession is `N/A` — with no `SUPERSEDED` state in registry §4 and
        no `ExceptionSuperseded` event registered anywhere. A probe that ACCEPTED any of the three
        would be producing passing evidence for a transition the corpus states does not exist — the
        same shape as M4's refused `unfreeze`, M5's refused `expire-observation`, M6's refused
        `expire-claim`, M7's refused `expire-conflict` and M8's refused `reopen-expectation`.
        """
        refusal = [c for c in m9.commands if f"--inject {fault}" in c.run]
        assert refusal, f"nothing refuses a {fault} fault"
        assert refusal[0].expect_exit_code == 2
        assert "unknown fault" in refusal[0].expect_contains
        assert section in M9_TASK or section in M9_TASK_FLAT, (
            f"the task never states the canonical clause behind refusing {fault!r}"
        )

    def test_the_illegal_faults_are_in_the_vocabulary_rather_than_refused_as_unknown(
        self, m9, dimensions
    ):
        """The distinction a machine that owns its own illegal set has to make.

        Machine §15 names four shapes as ILLEGAL TRANSITIONS by hand — `RESOLVED` without a valid
        `decision_ref`, `AutoClose`/`Inactivity`, an ownerless Exception and an expired Exception —
        and the `EC-3`/`EC-4`/`EC-6`/`EC-7` from-sets make three more. So the MACHINE must be seen to
        refuse them under `GR-1`, raising and persisting nothing. A fault refused as *unknown* and a
        fault refused as *illegal* are two different proofs, and M9 owes both.

        `cancel-exception` is deliberately in the vocabulary rather than a negative control: entity
        §25 and machine §22 both DISCUSS cancellation, so refusing the word outright would answer
        `M9-AQ-2` by making the corpus's own sentence unspeakable. What the machine must refuse is
        the unregistered STATE and the unregistered EVENT, under `GR-1`.
        """
        refused_as_unknown = {
            c.run.split("--inject ", 1)[1].split()[0]
            for c in m9.commands
            if "--inject " in c.run and c.expect_exit_code == 2
        }
        for illegal in ("autoclose", "inactivity-close", "expire-exception", "sweep-close",
                        "timer-resolve", "resolve-from-ageing", "ownerless-raise",
                        "resolve-without-decision-ref", "sixth-state", "sub-status-as-state",
                        "cancel-exception", "age-resolved", "age-escalated",
                        "severity-change-ageing", "delete-exception"):
            assert illegal in dimensions, f"the illegal shape {illegal!r} is not reachable at all"
            assert illegal not in refused_as_unknown, (
                f"{illegal!r} is refused as an UNKNOWN fault. The corpus DEFINES it, as an ILLEGAL "
                "transition — so the machine owes a GR-1 refusal for it, not the argument parser"
            )

    def test_it_carries_regression_anchors_for_every_layer_m9_builds_on(self, m9):
        """M9 adds a table and edits `schema.py`, so every layer that reads a canonical database can
        be broken from inside it."""
        runs = " ".join(c.run for c in m9.commands)
        for anchor in (
            "test_phase3_witness.py",                 # P3, the kernel M9 feeds and must not disturb
            "test_import_gate.py",                    # P4, the boundary M9 must not widen
            "test_phase5_event_transport.py",         # P5, the transport M9 rides
            "test_p5_durable_timers.py",              # P5, the substrate EC-4/EC-5 ride
            "test_phase6_work_item.py",               # M1, whose K-1 resolver M9 IMPORTS
            "test_phase6_pipeline_instance.py",       # M2
            "test_phase6_external_effect.py",         # M3, the single effect authority
            "test_phase6_approval.py",                # M4, the AP-9 frozen-record precedent
            "test_phase6_observation.py",             # M5, whose UNPARSEABLE seam points here
            "test_phase6_identity_binding_claim.py",  # M6
            "test_phase6_conflict.py",                # M7, the decision_ref column precedent
            "test_phase6_expectation.py",             # M8, whose OVERDUE/EXPIRED seams point here
        ):
            assert anchor in runs, f"{anchor} is never re-run against M9's change"
        # M9 emits six already-registered F9 names and mints none of its own, so the canonical
        # event-contract guard is load-bearing here rather than incidental: a builder that invents
        # `ExceptionCancelled`, or emits `ExceptionSeverityChanged` with no `previous_severity`,
        # fails there.
        assert "test_p5_event_contracts.py" in runs
        assert "test_p5_canonical_event_mint.py" in runs

    def test_the_durable_timer_suite_is_treated_as_load_bearing_rather_than_incidental(self, m9):
        """`EC-4` and `EC-5` are the ONLY transitions that may move an Exception without a human, so
        P5's timer substrate is the one place "it ages and escalates rather than being swept away"
        actually lives. Machine §37: durable timers, and ### never a resolution timer."""
        timers = [c for c in m9.commands if "test_p5_durable_timers.py" in c.run]
        assert timers, "P5's durable-timer suite is never re-run"
        assert "durable timers" in timers[0].name.lower(), (
            "the anchor's name does not say why the timer substrate is being re-run, so a future "
            "edit cannot tell it apart from an incidental P5 anchor"
        )

    def test_it_re_runs_the_neighbouring_units_own_oracles_rather_than_trusting_m9(self, m9):
        """"M9 broke nothing" is a claim about M5's, M7's and M8's behaviour.

        Those three are the units whose human-owned states carry a *"→ Exception"* seam deferred to
        M9 by name, which makes them the ones a builder is tempted to reach into in order to wire the
        seam up from the wrong side.
        """
        runs = [c.run for c in m9.commands]
        assert ".venv/bin/python scripts/probe_phase6_expectation.py" in runs
        assert ".venv/bin/python scripts/probe_phase6_conflict.py" in runs
        assert ".venv/bin/python scripts/probe_phase6_observation.py" in runs

    def test_the_mutation_battery_runs(self, m9):
        runs = " ".join(c.run for c in m9.commands)
        assert "mutate_phase6_exception.py" in runs
        assert "mutants caught" in m9.expect_visible

    def test_it_refuses_the_failures_m9_exists_to_prevent(self, m9):
        """Each forbidden marker is a sentence printed only when the thing M9 exists to prevent has
        just happened. Every load-bearing mutant in the battery has one."""
        for marker in (
            "### EXCEPTION RAISED WITHOUT AN OWNER ###",
            "### OWNERLESS EXCEPTION CREATED ###",
            "### A MODEL OWNED AN EXCEPTION ###",
            "### AN OFFBOARDED HUMAN OWNED AN EXCEPTION ###",
            "### CROSS-TENANT OWNER ACCEPTED ###",
            "### CROSS-TENANT SOURCE ACCEPTED ###",
            "### CROSS-TENANT decision_ref ACCEPTED ###",
            "### CROSS-TENANT QUEUE READ ###",
            "### EXCEPTION CLOSED WITHOUT A DECISION ###",
            "### CLOSURE BY SILENCE ###",
            "### INACTIVITY CLOSED AN EXCEPTION ###",
            "### AUTOCLOSE CLOSED AN EXCEPTION ###",
            "### EXCEPTION EXPIRED ###",
            "### EXCEPTION OUTLIVED ###",
            "### SWEEP CLOSED AN EXCEPTION ###",
            "### REAPER DELETED AN EXCEPTION ###",
            "### EXCEPTION DELETED ###",
            "### TIMER RESOLVED AN EXCEPTION ###",
            "### MODEL RESOLVED AN EXCEPTION ###",
            "### MODEL ACKNOWLEDGED AN EXCEPTION ###",
            "### MODEL CHANGED SEVERITY ###",
            "### UNRESOLVABLE decision_ref ACCEPTED ###",
            "### AUTOMATED ACTOR PASSED AS A HUMAN DECISION ###",
            "### AGEING EXCEPTION RESOLVED DIRECTLY ###",
            "### SEVERITY CHANGE BECAME A LIFECYCLE STATE ###",
            "### PREVIOUS SEVERITY LOST ###",
            "### SEVERITY CHANGE WITHOUT A REASON ###",
            "### REPLAY REBUILT SEVERITY FROM THE CURRENT ROW ###",
            "### UNREGISTERED SEVERITY MINTED ###",
            "### UNREGISTERED STATE MINTED ###",
            "### sub_status BECAME A LIFECYCLE STATE ###",
            "### SIXTH LIFECYCLE STATE MINTED ###",
            "### CANCELLED STATE MINTED ###",
            "### RAISE AND FREEZE SPLIT ACROSS COMMITS ###",
            "### HALF-RAISED EXCEPTION PERSISTED ###",
            "### EVENT WITHOUT ITS STATE ###",
            "### STATE WITHOUT ITS EVENT ###",
            "### FROZEN ENTITY UNBLOCKED WITHOUT A RESOLUTION ###",
            "### EVERY EXCEPTION FROZE AN ENTITY ###",
            "### M9 MINTED A GATE DECISION ###",
            "### EXCEPTION AUTHORIZED AN ACTION ###",
            "### M9 ENGAGED A BRAKE ###",
            "### RESOLVED EXCEPTION MOVED ###",
            "### STALE VERSION OVERWROTE NEWER STATE ###",
            "### REPLAY MANUFACTURED RESOLUTION AUTHORITY ###",
            "### REPLAY MINTED AUTHORITY ###",
            "### DOWNSTREAM EFFECT DURING REPLAY ###",
            "### TIMER LOST ACROSS RESTART ###",
            "### EXCEPTION LOST ACROSS RESTART ###",
            "### PERMANENT FAILURE RETRIED ###",
            "### PERMANENCE INFERRED FROM A MESSAGE ###",
            "### M1 WORK ITEM ROW REWRITTEN BY M9 ###",
            "### M5 OBSERVATION ROW REWRITTEN BY M9 ###",
            "### M7 CONFLICT ROW REWRITTEN BY M9 ###",
            "### M8 EXPECTATION ROW REWRITTEN BY M9 ###",
            "### M3 EFFECT SEAM REWRITTEN ###",
            "### M10 EVENT MINTED ###",
            "### COMPENSATION FABRICATED ###",
        ):
            assert marker in m9.forbidden, f"{marker} is not a failure condition"

    def test_every_mutation_battery_mutant_has_a_forbidden_marker_behind_it(self, m9):
        """The mutation battery is only as good as the sentence that turns red under it.

        Each pair below is (the mutant the task requires, the marker the probe prints when that
        defect is live). A mutant with no marker is a mutant nothing observes.
        """
        for mutant_phrase, marker in (
            ("the owner requirement dropped from creation", "### OWNERLESS EXCEPTION CREATED ###"),
            ("an owner from another tenant permitted", "### CROSS-TENANT OWNER ACCEPTED ###"),
            ("a sixth lifecycle state added", "### SIXTH LIFECYCLE STATE MINTED ###"),
            ("a `sub_status` promoted to a lifecycle state",
             "### sub_status BECAME A LIFECYCLE STATE ###"),
            ("`RESOLVED` allowed with no `decision_ref`",
             "### EXCEPTION CLOSED WITHOUT A DECISION ###"),
            ("the `decision_ref` resolver weakened to a non-null check",
             "### UNRESOLVABLE decision_ref ACCEPTED ###"),
            ("a model permitted to resolve", "### MODEL RESOLVED AN EXCEPTION ###"),
            ("an inactivity `AutoClose` added", "### AUTOCLOSE CLOSED AN EXCEPTION ###"),
            ("an expiry added", "### EXCEPTION EXPIRED ###"),
            ("a timer permitted to resolve", "### TIMER RESOLVED AN EXCEPTION ###"),
            ("the durable timer replaced with an in-memory sleep or a background sweep",
             "### SWEEP CLOSED AN EXCEPTION ###"),
            ("`previous_severity` dropped from the severity event", "### PREVIOUS SEVERITY LOST ###"),
            ("replay recomputing severity from the current row",
             "### REPLAY REBUILT SEVERITY FROM THE CURRENT ROW ###"),
            ("the tenant weakened out of the primary key or the queue index",
             "### CROSS-TENANT QUEUE READ ###"),
            ("the raise/freeze transaction split into two commits",
             "### RAISE AND FREEZE SPLIT ACROSS COMMITS ###"),
            ("an invented `ExceptionCancelled` event or `CANCELLED` state minted",
             "### CANCELLED STATE MINTED ###"),
            ("an M10/M11/M12 table or event created", "### M10 EVENT MINTED ###"),
            ("M9 made a gate-decision minter", "### M9 MINTED A GATE DECISION ###"),
            ("M9 made a brake engager", "### M9 ENGAGED A BRAKE ###"),
            ("a PERMANENT failure retried before raising", "### PERMANENT FAILURE RETRIED ###"),
            ("permanence inferred from an error message",
             "### PERMANENCE INFERRED FROM A MESSAGE ###"),
        ):
            assert mutant_phrase in M9_TASK_FLAT, (
                f"the task never requires the mutant {mutant_phrase!r}"
            )
            assert marker in m9.forbidden, f"the mutant {mutant_phrase!r} has no forbidden marker"

    def test_the_task_file_and_the_scenario_agree_on_the_contract(self, m9, cases, dimensions):
        """The two halves of one contract, checked against each other.

        A case the scenario asserts exists but the task never asks for is a case the builder is being
        failed on without being told. A literal the scenario requires but the task never states is
        the same defect one layer down.
        """
        for case in cases:
            assert case in M9_TASK, f"the scenario asserts case {case!r}; the task never names it"
        for dimension in dimensions:
            assert dimension in M9_TASK, (
                f"the scenario asserts dimension {dimension!r}; the task never names it"
            )
        for literal in m9.expect_visible:
            assert literal in M9_TASK, (
                f"the scenario requires the literal {literal!r}; the task never states it"
            )
        for marker in m9.forbidden:
            if marker.startswith("### ") and marker.endswith(" ###"):
                assert marker in M9_TASK, (
                    f"the scenario forbids {marker!r}; the task never names it"
                )
        for path in DELIVERABLES:
            assert path in M9_TASK, f"the scenario requires {path}; the task never names it"

    def test_the_task_states_the_canonical_machine_rather_than_a_generic_feature(self):
        for state in STATES:
            assert state in M9_TASK, f"the canonical state {state} is never named"
        for transition in TRANSITIONS:
            assert transition in M9_TASK, f"the canonical transition {transition} is never named"
        for event in F9_EVENTS:
            assert event in M9_TASK, f"the F9 contract {event} is never named"
        for severity in SEVERITIES:
            assert severity in M9_TASK, f"the severity {severity} is never named"
        assert "Five states" in M9_TASK, "the state count is never stated"
        assert "Do not add a sixth" in M9_TASK
        assert "Seven rows." in M9_TASK, "the transition count is never stated"
        assert "AC-MACH-901..907" in M9_TASK
        # `CANCELLED` and `EXPIRED` are the two a build session invents; the finer brief terms are
        # the six that arrive wearing a reasonable-sounding name.
        for forbidden in FORBIDDEN_STATES:
            assert forbidden in M9_TASK, f"the task never warns off the {forbidden} state"
        for forbidden in FORBIDDEN_EVENTS:
            assert forbidden in M9_TASK, f"the task never warns off the {forbidden} event"

    def test_the_task_reads_the_from_sets_literally(self):
        """Four from-sets are places a build session widens the table without noticing.

        `EC-3`'s is `{OPEN, ACKNOWLEDGED}` and `EC-6`'s is `{ESCALATED}` — `AGEING` is in neither, so
        resolving an ageing Exception directly is ILLEGAL, not a convenience. `EC-4`'s excludes
        `AGEING`, `ESCALATED` and `RESOLVED`. `EC-7`'s excludes `AGEING` and `RESOLVED`. And `EC-7`
        changes no state at all.
        """
        assert (
            "`EC-3`'s from-set is `{OPEN, ACKNOWLEDGED}` and `EC-6`'s is `{ESCALATED}`. `AGEING` IS "
            "IN NEITHER." in M9_TASK_FLAT
        )
        assert (
            "`EC-4`'s from-set is `{OPEN, ACKNOWLEDGED}`. `AGEING`, `ESCALATED` AND `RESOLVED` ARE "
            "NOT IN IT" in M9_TASK_FLAT
        )
        assert (
            "`EC-7`'s from-set is `{OPEN, ACKNOWLEDGED, ESCALATED}`. `AGEING` IS NOT IN IT"
            in M9_TASK_FLAT
        )
        assert "`EC-7` DOES NOT CHANGE `state`." in M9_TASK_FLAT
        assert "do not add an `EC-3a`" in M9_TASK_FLAT

    def test_the_task_forces_the_authority_to_be_read_first(self):
        for source in (
            "PRODUCT.md",
            "CLAUDE.md",
            "docs/implementation/CURRENT.md",
            "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
            "docs/implementation/implementation-roadmap.md",
            "docs/specifications/entities/12-exception.md",
            "docs/specifications/state-machines/09-exception.machine.md",
            "docs/specifications/state-machines/registry.md",
            "docs/specifications/events/09-exception-events.md",
            "docs/specifications/events/registry.md",
            "docs/specifications/events/14-audit-security-events.md",
            "docs/architecture/target-system-specification.md",
            "docs/architecture/decisions/ADR-008-durable-workflows.md",
            "docs/architecture/decisions/ADR-006-verification-and-unknown-outcomes.md",
            "docs/architecture/decisions/ADR-011-human-brake.md",
            "docs/specifications/entities/00-conventions.md",
            "docs/specifications/acceptance/foundational-machine-acceptance.md",
            "docs/specifications/acceptance/platform-safety-acceptance.md",
            "src/freight_recon/checkpoint.py",
            "src/freight_recon/work_item.py",
            "src/freight_recon/external_effect.py",
            "src/freight_recon/conflict.py",
            "src/freight_recon/expectation.py",
            "src/freight_recon/brake.py",
        ):
            assert source in M9_TASK, f"{source} is never named as authority"
        assert "event_timers.py" in M9_TASK, (
            "the durable-timer substrate EC-4/EC-5 ride is never named as authority"
        )
        assert "event_inbox.py" in M9_TASK, (
            "P5's `expire_overdue`, which is a LANDED pre-declared M9 seam, is never named"
        )
        assert "the specification wins and you say so" in M9_TASK_FLAT
        assert "REPORT THE CONFLICT" in M9_TASK

    def test_the_task_states_the_never_closed_by_silence_invariant(self):
        """The sentence the entity spends forty-five points defending."""
        assert "AN EXCEPTION CLOSED WITHOUT A DECISION IS NOT CLOSED — IT IS FORGOTTEN." in M9_TASK
        assert "F-30" in M9_TASK
        assert "GR-14" in M9_TASK
        assert "AC-MACH-903" in M9_TASK
        assert "AC-SAFE-024" in M9_TASK
        assert "I1" in M9_TASK and "M-35" in M9_TASK
        for state in HUMAN_OWNED_STATES:
            assert state in M9_TASK
        assert "NEVER CLOSED BY SILENCE" in M9_TASK
        assert (
            "A TIMER NEVER RESOLVES" in M9_TASK
        ), "the task never says a timer may make it louder but never close it"

    def test_the_task_states_what_an_exception_is_not(self):
        """Entity §4's list, and the distinction the whole unit rests on."""
        assert "AN EXCEPTION IS NOT AN ERROR LOG, AN ALERT, OR AN ISSUE TRACKER ROW" in M9_TASK
        assert "IT IS NOT AUTO-CLOSABLE. IT IS NOT OUTLIVABLE." in M9_TASK
        assert "Every one of those is a mechanism for forgetting" in M9_TASK_FLAT

    def test_the_task_states_the_sub_status_rule_rather_than_leaving_it_to_taste(self):
        """The machine's own header paragraph, which is the whole point of this unit's vocabulary."""
        assert "AND DO NOT PROMOTE A `sub_status` TO A LIFECYCLE STATE." in M9_TASK
        assert "`owner`/`sub_status` FIELDS on the row, NOT" in M9_TASK_FLAT
        assert "no machine may define a local synonym" in M9_TASK_FLAT
        assert (
            "it is **M1's registered state**" in M9_TASK_FLAT
        ), "the task never says why AWAITING_HUMAN is doubly forbidden"


# --------------------------------------------------------------------------
# 2. Every declared risk is mapped to a command that can actually prove it
# --------------------------------------------------------------------------


#: The six literals that say M9 stopped where it was told to stop, and that no landed unit was
#: edited to get there. They are M9's own narration: `tasks/neyma_p6_m9.md` states them verbatim to
#: the builder as strings the M9 PROBE must print, and the probe is the only command in this scenario
#: that runs the machine and narrates what it found. No pytest anchor prints them, because none of
#: them runs M9's story.
DARK_POSTURE_LITERALS = (
    "THE M1 WORK ITEM MACHINE IS UNCHANGED",
    "THE M3 EFFECT AUTHORITY IS UNCHANGED",
    "THE M5 OBSERVATION MACHINE IS UNCHANGED",
    "THE M7 CONFLICT MACHINE IS UNCHANGED",
    "THE M8 EXPECTATION MACHINE IS UNCHANGED",
    "THE M10, M11 AND M12 MACHINES ARE NOT BUILT",
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

    def test_the_regression_claim_names_the_probe_that_proves_the_seams_are_intact(self, m9):
        """The M9-owned half of the regression claim: M1, M3, M5, M7 and M8 unchanged, and no
        neighbouring machine built. The probe is the command that observes it from inside M9's own
        story, so the claim must name the probe."""
        regression = [c for c in m9.verifies if c.risk_category == "regression"]
        assert regression, "the M9 scenario no longer declares a regression claim"
        claim = regression[0]
        for literal in DARK_POSTURE_LITERALS:
            assert literal in claim.observations, (
                f"the regression claim no longer requires {literal!r}. The seam proof is not "
                "optional: removing it is how this defect gets 'fixed' by weakening the oracle"
            )
        assert PROBE_CHECK in claim.checks, (
            "the regression claim requires the seam literals but does not name the M9 probe. Only "
            f"{PROBE_CHECK!r} runs the machine and narrates what it found; the pytest anchors it "
            "names print no such sentence, so the claim could never be established"
        )

    def test_every_claim_requiring_a_dark_posture_literal_names_the_probe(self, m9):
        """Stated once, for the whole file rather than for one claim: wherever the scenario asks for
        this proof, it must ask the command that produces it."""
        assert claims_needing_the_probe(m9) == []

    def test_the_dark_posture_literals_are_still_required_somewhere(self, m9):
        """The other way to make the gap go away is to stop asking. This refuses that."""
        for literal in DARK_POSTURE_LITERALS:
            assert literal in m9.expect_visible, (
                f"{literal!r} is no longer an expected observation of the M9 scenario"
            )
            assert any(literal in claim.observations for claim in m9.verifies), (
                f"{literal!r} is expected but no declared risk rests on it any more"
            )

    def test_a_claim_may_not_require_an_observation_its_checks_cannot_declare(self, m9):
        """The general half, enforced at load time — asserted here against the real M9 file so the
        shipped scenario is covered by the invariant and not merely by the unit test of it."""
        assert unattributable_claims(m9) == []

    def test_every_declared_risk_names_at_least_one_check_and_one_observation(self, m9):
        """A claim with an oracle on only one side is half a claim.

        `RiskClaim` requires one of the two. This file requires both for M9, because a claim with no
        named check matches its literals against EVERYTHING the run observed — which for a scenario
        that runs twelve pytest anchors and three neighbouring probes is a very large haystack, and
        an accidental match in it is coverage nobody established.
        """
        for claim in m9.verifies:
            assert claim.checks, f"the {claim.risk_category!r} claim names no check"
            assert claim.observations, f"the {claim.risk_category!r} claim names no observation"

    def test_the_effect_and_approval_families_are_deliberately_left_undeclared(self, m9):
        """M9 touches the outside world not at all, and consumes no approval, and both absences are
        the point.

        Machine §28 routes human approval through the FROZEN FIELD rather than through an M4
        Approval; M9 produces no external effect, so `timeout_before_effect` and
        `ambiguous_external_effect` are risks about M3's behaviour. And `conflicting_evidence` is
        M7's: an Exception is *too little information reaching a human*, not two claims disagreeing.
        A run that names one of them as a blocking M9 risk must GENERATE a case for it or block — not
        find a permanent declaration here waving it through.
        """
        declared = m9.declared_risk_categories()
        for foreign in ("approval_required", "timeout_before_effect", "timeout_after_effect",
                        "ambiguous_external_effect", "conflicting_evidence"):
            assert foreign not in declared, (
                f"the M9 scenario declares {foreign!r}, which is a claim about another unit's "
                "behaviour rather than about the Exception machine"
            )

    def test_every_declared_category_is_one_m9_actually_exhibits(self, m9):
        """Fourteen families, each derived from M9's own behaviour rather than from M8's count."""
        assert m9.declared_risk_categories() == {
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
            "persistence_failure",
            "restart_recovery",
            "stale_state",
            "regression",
        }

    def test_the_authorization_claim_is_the_one_this_unit_turns_on(self, m9):
        """M9's whole safety property is a question about who may act, so the `authorization` claim
        carries the weight `safety_invariant` carried for M8's coverage split. It must require the
        four model refusals and the two boundary facts, and it must name the checks that emit them."""
        claim = [c for c in m9.verifies if c.risk_category == "authorization"][0]
        for literal in (
            "A MODEL CAN NEVER ACKNOWLEDGE AN EXCEPTION",
            "A MODEL CAN NEVER RESOLVE OR AUTO-CLEAR AN EXCEPTION",
            "A MODEL CAN NEVER CHANGE SEVERITY",
            "A MODEL IS NOT A HUMAN AND MAY NOT OWN AN EXCEPTION",
            "M9 MINTS NO GATE DECISION",
            "M9 ENGAGES NO BRAKE AND NARROWS NONE",
        ):
            assert literal in claim.observations, (
                f"the authorization claim no longer requires {literal!r}"
            )
        assert PROBE_CHECK in claim.checks
        assert "the checkpoint is still the only thing that mints a gate decision" in claim.checks

    def test_the_safety_invariant_claim_rests_on_a_database_and_a_resolver_not_on_narration(
        self, m9
    ):
        """"Never closed by silence" is a property of a CHECK and of `K-1`'s resolver. A claim that
        rested only on the probe's sentences would be satisfied by a machine that printed them."""
        claim = [c for c in m9.verifies if c.risk_category == "safety_invariant"][0]
        assert "an owner is required from creation and RESOLVED requires a decision_ref, as CHECKs" \
            in claim.checks
        assert "the live database refuses an ownerless exception and a resolution with no " \
               "decision_ref" in claim.checks
        assert "the decision_ref resolver is M1's, imported rather than rewritten" in claim.checks
        assert "RESOLVED requires decision_ref: True" in claim.observations
        assert "RESOLVED with no decision_ref: refused" in claim.observations
        assert "M9 imports the K-1 resolver: True" in claim.observations


# --------------------------------------------------------------------------
# 3. The database is the oracle, not the probe's narration
# --------------------------------------------------------------------------


class TestPersistedStateIsTheOracle:
    """The sentences a green test suite can state while the database enforces none of them.

    "`RESOLVED` requires a `decision_ref`", "an ownerless Exception is impossible", "there are five
    states" and "severity is exactly three values" are each a property of the SCHEMA. A probe that
    prints them proves it printed them.
    """

    def test_the_scenario_reads_the_database_at_all(self, m9):
        assert m9.expect_state, "no persisted state is inspected; the probe speaks for itself"

    def test_the_five_states_are_asserted_and_there_is_no_sixth(self, m9):
        guard = [c for c in m9.expect_state if "state vocabulary" in c.command]
        assert guard, "the state set is never read out of the DDL"
        declared = guard[0]
        for state in STATES:
            assert f"'{state}'" in declared.contains, f"{state} is not asserted in the CHECK"
        for forbidden in FORBIDDEN_STATES:
            assert f"'{forbidden}'" in declared.not_contains, (
                f"nothing prevents an invented {forbidden} state"
            )

    def test_the_closure_invariant_is_asserted_as_a_database_check(self, state_checks):
        """Entity §16 and §37, and the reason this unit is not an alert.

        `RESOLVED` with no `decision_ref` and an ownerless Exception are STRUCTURALLY IMPOSSIBLE
        states — inserts the database refuses, not branches a code path takes. `AC-MACH-903`
        merge-gates the first and `AC-SAFE-028`/`I1` the second.
        """
        guard = state_checks.get(
            "an owner is required from creation and RESOLVED requires a decision_ref, as CHECKs"
        )
        assert guard, "nothing asserts the closure invariant is a database constraint"
        assert "table present: True" in guard, (
            "the DDL read has no proven population: every assertion below would be made about an "
            "empty string if the table were missing"
        )
        assert "owner_id NOT NULL: True" in guard
        assert "RESOLVED requires decision_ref: True" in guard, (
            "the CHECK is not asserted to tie RESOLVED to a decision_ref — which is the difference "
            "between a status column and a closure that had to be decided"
        )
        for column in ("'decision_ref'", "'exception_id'", "'owner_id'", "'severity'",
                       "'source_ref'", "'state'", "'tenant'", "'version'"):
            assert column in guard, f"the exceptions table is not asserted to carry {column}"

    def test_the_forbidden_writes_are_attempted_against_a_live_database(self, m9, state_checks):
        """`P6-D56`'s one weaker instrument, repaired.

        Reading a CHECK out of the DDL proves the text exists. ISSUING the forbidden write and being
        refused proves the ENGINE enforces it — and the two positive controls are what stop every
        "refused" line being true of a table that rejects everything, which is a vacuous negative
        corpus wearing the costume of enforcement.
        """
        guard = state_checks.get(
            "the live database refuses an ownerless exception and a resolution with no decision_ref"
        )
        assert guard, "the invariants are read out of the DDL and never attempted"
        assert "exceptions table present: True" in guard, (
            "the attempt battery has no proven population: it would report nothing against a "
            "missing table and read as a pass"
        )
        assert "positive control, a well-formed OPEN exception: ACCEPTED" in guard, (
            "there is no positive control, so every refusal below could be a table that refuses "
            "everything"
        )
        assert "second positive control, RESOLVED WITH a decision_ref: ACCEPTED" in guard, (
            "nothing proves the RESOLVED constraint is TARGETED rather than a blanket refusal of "
            "the RESOLVED state"
        )
        for refusal in ("an ownerless exception: refused",
                        "an owner who is not a recorded human: refused",
                        "an owner from another tenant: refused",
                        "RESOLVED with no decision_ref: refused",
                        "a CANCELLED lifecycle state: refused",
                        "an EXPIRED lifecycle state: refused",
                        "an invented SEV3 severity: refused"):
            assert refusal in guard, f"the live database is never asked to refuse: {refusal!r}"
        assert "rows that survived: 2" in guard, (
            "nothing counts the survivors, so a refusal that silently wrote a row would pass"
        )
        command = [c for c in m9.expect_state if c.contains == guard][0].command
        assert "enable_and_verify_foreign_keys(c)" in command, (
            "SQLite ignores the foreign-key pragma unless it is enabled and read back OUTSIDE a "
            "transaction, so a cross-tenant owner would be accepted by the database under test. "
            "Importing the helper is not calling it"
        )
        assert "the derived positive-control row" in command, (
            "the positive control is hard-coded rather than derived from the shipped DDL, so a "
            "builder's own column set could make it fail for an unrelated reason"
        )

    def test_the_severity_vocabulary_is_asserted_closed_and_never_defaulted(self, state_checks):
        """Entity §12: `severity ∈ {SEV0, SEV1, SEV2}`.

        Three properties, and the third matters most. A DEFAULT on `severity` is the mechanism by
        which "nobody said how bad this is" silently becomes a value — and since a Sev-0 exception
        auto-engages a brake at its source, a defaulted severity is a safety statement nobody made.
        """
        guard = state_checks.get(
            "severity is a closed three-member vocabulary, enforced and never defaulted"
        )
        assert guard, "nothing asserts the severity vocabulary is a database constraint"
        assert "severity vocabulary is enforced by a CHECK: True" in guard
        for value in ("'SEV0'", "'SEV1'", "'SEV2'"):
            assert value in guard, f"the severity vocabulary does not assert {value}"
        assert "severity vocabulary is exactly three members: True" in guard, (
            "nothing prevents a fourth severity being added beside the canonical three"
        )
        assert "severity is never defaulted: True" in guard

    def test_sub_status_is_asserted_to_be_a_field_and_never_a_state(self, m9, state_checks):
        """The machine's header paragraph, measured.

        `sub_status` is OPTIONAL, so this must NOT require the column to exist. What it requires is
        the property that holds either way: none of the brief's six finer terms is a value of
        `state`. Building the column is a choice; promoting one of its values to a lifecycle state is
        a defect whether the column exists or not.
        """
        guard = state_checks.get(
            "sub_status is a field if it exists at all, and never a lifecycle state"
        )
        assert guard, "nothing prevents a sub_status becoming a sixth lifecycle state"
        assert "exceptions table present: True" in guard, (
            "the sub_status sweep has no proven population"
        )
        assert "finer brief terms promoted to lifecycle states: []" in guard
        assert "sub_status is never a lifecycle state: True" in guard
        assert "sub_status implemented as a column: True" not in guard, (
            "the scenario REQUIRES the optional sub_status column to exist. EC-1 writes "
            "`sub_status?` and F9 lists it as optional, so requiring it invents a canonical "
            "obligation the corpus does not state"
        )

    def test_the_owner_and_every_named_human_are_asserted_as_foreign_keys(self, state_checks):
        """"A named human" is decoration while it is a text column. Entity §18 makes `owner_id` a
        foreign key into an authenticated tenant user; M1, M4, M6, M7 and M8 each made the same
        argument for their own, and M7's `decision_human_id` is the precedent for naming the ACTIVE
        human behind a `decision_ref`."""
        guard = state_checks.get(
            "the owner and every human named on the row are FOREIGN KEYS into tenant_humans"
        )
        assert guard, "nothing asserts the exception's human references are foreign keys"
        assert "owner is FK-backed into tenant_humans: True" in guard
        assert "every human-named column is FK-backed: True" in guard, (
            "the owner is FK-backed but an acknowledging or deciding human could still be free text"
        )
        assert "foreign keys into a table nobody built: []" in guard

    def test_the_dedup_index_is_recorded_as_optional_rather_than_required(self, m9, state_checks):
        """The one constraint this file must NOT turn into an acceptance criterion.

        Entity §17, machine §17 and F9's cross-cutting section each call
        `UNIQUE (tenant, source_ref, type) WHERE state != 'RESOLVED'` OPTIONAL, in that word. Product
        Driver asserting it would be inventing a canonical requirement, so what is asserted is the
        property that holds under BOTH readings — every index is tenant-first, the owner queue is one
        read, and IF the dedup index was built its `WHERE` names `RESOLVED`.
        """
        guard = state_checks.get(
            "every exception index is tenant-first, and the optional dedup index is recorded not "
            "required"
        )
        assert guard, "nothing asserts the exception indexes are tenant-first"
        assert "every index is tenant-first: True" in guard
        assert "the owner queue is one tenant-first index: True" in guard
        assert "dedup index, if built, is partial on RESOLVED: True" in guard
        assert "open-exception dedup index built: True" not in guard, (
            "the scenario REQUIRES the open-exception dedup index. Entity §17, machine §17 and F9 "
            "each call it OPTIONAL in that word, so requiring it makes Product Driver the author of "
            "a canonical decision it has no authority to make"
        )
        assert "THE OPEN-EXCEPTION DEDUP INDEX IS OPTIONAL, AND THIS BUILD RECORDS ITS CHOICE" \
            in m9.expect_visible, (
                "nothing makes the builder STATE which reading it implemented, so the choice would "
                "be invisible in the evidence"
            )

    def test_inventing_a_sweep_expiry_or_autoclose_is_a_scenario_failure(self, m9, state_checks):
        """And the rule is preserved by a check over the corpus, not a hope.

        The way a build session breaks "an exception is never closed by silence" is not by arguing
        with it. It is by adding a TTL, a nightly sweep, a stale-exception reaper, an inactivity
        auto-close or an `EXPIRED` state because those felt like hygiene.

        The scan deliberately does NOT flag a function whose name begins `refuse`/`reject`/`illegal`:
        the machine must be able to REFUSE a sweep-close attempt by name to prove `GR-1` catches it,
        and a scan that flagged the refusal would force the illegal case out of existence to stay
        green. It is also scoped to the machine and its migration rather than to the probe.
        """
        guard = state_checks.get("no expiry, sweep, reaper, auto-close or deletion was invented")
        assert guard, "nothing asserts that no sweep, expiry or auto-close was invented"
        assert "invented sweep/reaper/autoclose surfaces: []" in guard
        assert "unregistered exception states in the migration: []" in guard
        assert "invented or foreign event names in the machine: []" in guard
        assert "invented extra transition rows: []" in guard
        assert "exception deletion statements: []" in guard
        assert "in-memory sleep in the machine: []" in guard
        assert "machine source: present" in guard, (
            "the invention sweep has no proven population: it would print empty lists against a "
            "missing file and read as a pass"
        )
        command = [c for c in m9.expect_state if c.contains == guard][0].command
        for event in FORBIDDEN_EVENTS:
            assert event in command, f"the invention sweep does not look for {event}"
        assert "EC-(?:8|9|10" in command, "nothing prevents an eighth transition row being written"
        assert "probe_phase6_exception" not in command, (
            "the sweep reads the probe, which legitimately needs the sweep, expiry and autoclose "
            "identifiers in order to ATTEMPT the illegal transition GR-1 must refuse"
        )
        assert "_?refuse" in command, (
            "the sweep flags any function whose name contains 'sweep', including the machine's own "
            "REFUSAL of one — which would force the GR-1 case out of existence to stay green"
        )

    def test_the_six_f9_contracts_are_used_and_no_seventh_is_minted(self, state_checks):
        guard = state_checks.get("M9 uses the six registered F9 contracts and invents no seventh")
        assert guard, "nothing checks the event names M9 uses against the canonical registry"
        declared = " ".join(guard)
        for event in F9_EVENTS:
            assert f"'{event}'" in declared, f"{event} is not asserted registered"
        assert "synonym events registered: []" in guard
        assert "unregistered names in the machine: []" in guard
        assert "machine source: present" in guard, (
            "the unregistered-name sweep has no proven population: it would print an empty list "
            "against a missing file and read as a pass"
        )

    def test_the_rebuild_guarantee_is_read_out_of_the_contract_projection(self, state_checks):
        """`ExceptionSeverityChanged`'s FOUR required fields ARE the rebuild guarantee, expressed as
        a contract.

        F9 states the reason: *"`ExceptionRaised` records severity at creation; `EC-7` mutates it, so
        without this event a rebuild reproduces the ORIGINAL severity and can UNDER-STATE the live
        one"* — and a Sev-0 auto-engages a brake at its source, so that under-statement is a safety
        loss rather than a cosmetic one. `previous_severity` is what makes a missing link DETECTABLE
        in a fold instead of silently absorbed. The P5 contract layer already refuses an event
        missing a required field, so reading that out of the projection makes the task's §3.3 a
        READING of the corpus rather than an assertion about it.
        """
        guard = state_checks.get("M9 uses the six registered F9 contracts and invents no seventh")
        assert guard
        assert (
            "ExceptionSeverityChanged requires: ['changed_by', 'previous_severity', 'reason', "
            "'severity']" in guard
        ), "the four required severity-change fields are not read out of the projection"
        assert "ExceptionResolved requires: ['decision_ref']" in guard
        assert "ExceptionRaised requires: ['severity', 'source_ref']" in guard

    def test_the_two_resolution_producers_are_read_out_of_the_projection(self, state_checks):
        """`ExceptionResolved` has TWO registered producers, and that is the `EC-6` proof.

        `EC-3` resolves from `{OPEN, ACKNOWLEDGED}` and `EC-6` from `{ESCALATED}`, and they are ONE
        contract — so a machine that could not resolve an escalated Exception would have a registered
        producer with nothing to produce. The corpus states it; this reads it rather than asserting
        it.
        """
        guard = state_checks.get("M9 uses the six registered F9 contracts and invents no seventh")
        assert guard
        assert "ExceptionResolved producers: ['EC-3', 'EC-6']" in guard
        assert "ExceptionAgeing producers: ['EC-4']" in guard

    def test_the_k1_resolver_is_asserted_imported_rather_than_rewritten(self, state_checks):
        """`CLAUDE.md` rule 17, in `K-1`'s own domain.

        M1 ships the resolver, M3 already imports it for `EF-5`, and M7 uses its
        `DECISION_REF_KINDS`. A second implementation of *"does this decision_ref resolve"* is two
        places for one of them to start accepting the string `done` — which is the exact hole `K-1`
        exists to shut.
        """
        guard = state_checks.get("the decision_ref resolver is M1's, imported rather than rewritten")
        assert guard, "nothing asserts M9 uses M1's K-1 resolver"
        assert "M9 imports the K-1 resolver: True" in guard
        assert "a second resolver defined in the machine: []" in guard
        assert "modules defining resolve_decision_ref outside M1: []" in guard
        assert "machine source: present" in guard, "the resolver sweep has no proven population"

    def test_the_dark_posture_is_measured_over_the_shipped_package(self, state_checks):
        assert (
            "production importers of exception: []"
            in state_checks.get(
                "M9 has no production caller — the dark posture, measured over the shipped package",
                [],
            )
        )
        assert (
            "scripts reaching exception: ['probe_phase6_exception.py']"
            in state_checks.get(
                "the only thing outside the package that reaches M9 is the verification probe itself",
                [],
            )
        )

    def test_no_oversight_queue_or_notifier_can_arrive_with_the_unit(self, m9, state_checks):
        """M9's product form is an exception queue with owners, notifications and an MTTR dashboard —
        the registry says so in terms. That surface is P8, and an Exception that pages someone is an
        alert, which is the thing entity §4 says this is not."""
        guard = state_checks.get(
            "no oversight queue, alerting, notification or paging surface ships with M9"
        )
        assert guard, "nothing prevents a queue or notifier arriving with M9"
        assert "modules joining the exception machine to a channel: []" in guard
        assert "notification or paging surfaces built for exceptions: []" in guard
        command = [c for c in m9.expect_state if c.contains == guard][0].command
        for channel in ("email_triage", "ingestion", "inbox_brain", "action_callback",
                        "slack_adapter", "tms_adapter", "alert_channel", "ops_control",
                        "operator_console", "delivery", "follow_up"):
            assert channel in command, f"the queue-surface sweep does not look at {channel}"

    def test_m9_authorizes_nothing_and_engages_no_brake(self, state_checks):
        """Entity §38 and machine §28: an Exception is an INPUT to checkpoint step 4 and never a
        second gate. And F9's cross-cutting section puts the Sev-0 brake AT THE SOURCE DETECTOR, not
        here — `events/registry.md` §11 gives the three detectors their auto-brake scope, and none of
        them is M9."""
        gate = state_checks.get("the checkpoint is still the only thing that mints a gate decision")
        assert gate
        assert "modules that MINT a gate decision: ['checkpoint.py']" in gate
        brake = state_checks.get(
            "the compensation, policy, rule and brake seams are fed without M10, M11, M12 or M13 "
            "being built"
        )
        assert brake, "nothing prevents M9 engaging a brake or building a neighbouring machine"
        assert "engages a brake from inside M9: []" in brake
        assert "mints another machine event: []" in brake
        assert "writes another machine transition: []" in brake
        assert "m10/m11/m12 tables created by m9: []" in brake
        assert "machine and migration present: True" in brake, (
            "the foreign-machine sweep has no proven population"
        )

    def test_ageing_is_asserted_to_ride_p5s_durable_timers(self, state_checks):
        """Machine §37: durable timers, and never a resolution timer (`M-36`). `EC-4` and `EC-5` are
        the ONLY transitions that may move an Exception without a human, so a second timer table or
        an in-memory sleep is not a style choice — it is the mechanism by which an obligation stops
        being durable, and it is the shape "the queue got tidy" arrives in."""
        guard = state_checks.get(
            "ageing and escalation ride P5's existing durable timers rather than a second timer "
            "mechanism"
        )
        assert guard, "nothing asserts ageing rides a durable timer"
        assert "M9 schedules through DurableTimers: True" in guard
        assert "M9 consumes TimerFired: True" in guard
        assert "in-memory sleep in the machine: []" in guard
        assert "second timer table created by m9: []" in guard
        assert "timer tables in the canonical schema: ['durable_timers']" in guard
        assert "machine source: present" in guard, "the timer sweep has no proven population"

    def test_the_five_landed_machines_are_asserted_unrewritten(self, state_checks):
        """M1 owns the resolver M9 imports and the `WI-6` trigger set an exception-freeze would be
        quietly added to; M3 is the single effect authority; and M5, M7 and M8 each deferred a
        "→ Exception" seam here by name, which makes them the files a builder edits to close it from
        the wrong side."""
        guard = state_checks.get(
            "M1's, M3's, M5's, M7's and M8's landed machines are not rewritten by M9"
        )
        assert guard, "nothing asserts the landed machines are unchanged"
        assert "landed machines importing exception: []" in guard
        for module in ("work_item", "external_effect", "observation", "conflict", "expectation"):
            assert f"{module}.py imports exception: False" in guard, (
                f"{module}.py is not asserted to be free of an M9 import"
            )
        assert "M1 still owns the K-1 resolver: True" in guard
        assert "M3 UNKNOWN_OUTCOME semantics present: True" in guard
        assert "M8 OVERDUE/INDETERMINATE semantics present: True" in guard

    def test_the_exception_layer_is_asserted_present_and_tenant_first(self, state_checks):
        guard = state_checks.get(
            "a freshly created canonical database carries the exception layer, tenant-first"
        )
        assert guard
        assert "problems: []" in guard
        assert "exceptions" in guard
        assert "tenant_humans" in guard, (
            "the table owner_id is FK-backed into is not asserted to exist beside the layer"
        )
        assert "durable_timers" in guard, (
            "the timer substrate EC-4/EC-5 ride is not asserted to exist beside the layer"
        )
        assert "event_outbox" in guard, (
            "the canonical event log M1's K-1 resolver resolves a decision_ref against is not "
            "asserted to exist"
        )


# --------------------------------------------------------------------------
# 4. The six recorded authority conflicts stay open
# --------------------------------------------------------------------------


class TestTheTaskPreservesTheAuthorityConflicts:
    """M4's, M5's, M6's, M7's and M8's §3.8 lesson, applied to a corpus that disagrees with itself
    six times about M9.

    A resolution the builder invented is worse than a blocked run, because it looks like agreement.
    """

    AQS = ("M9-AQ-1", "M9-AQ-2", "M9-AQ-3", "M9-AQ-4", "M9-AQ-5", "M9-AQ-6")

    def test_all_six_questions_are_named_with_both_sides(self):
        for question in self.AQS:
            assert question in M9_TASK, f"{question} is never raised"
        # AQ-1: entity §35 and machine §16/§40 say a human; GR-14, K-1, F9 and AC-SAFE-024 say
        # "a human-decision audit row OR an ACTIVE rule_id".
        assert "does resolution require a HUMAN, or a human OR an `ACTIVE` rule?" in M9_TASK
        assert "GR-14" in M9_TASK and "K-1" in M9_TASK and "AC-SAFE-024" in M9_TASK
        # AQ-2: §25 discusses cancellation; registry §4 gives no CANCELLED state and F9 no event.
        assert (
            "what is cancellation, given there is no `CANCELLED` state and no `ExceptionCancelled`"
            in M9_TASK_FLAT
        )
        assert "still an event, still a `decision_ref`" in M9_TASK
        # AQ-3: §18 makes source_ref a FK; §9 and §21 enumerate types four of which have no table.
        assert "`source_ref` is a `FOREIGN KEY` to what" in M9_TASK
        assert "conflict_parties" in M9_TASK
        # AQ-4: M9's consumed set is three triggers; five landed machines deferred a seam here.
        assert "who wires the five landed" in M9_TASK
        assert "`Acknowledged`, `Resolved`, `TimerFired`" in M9_TASK_FLAT
        # AQ-5: a freeze is required transactionally; no mechanism M9 owns exists.
        assert "what IS the \"freeze\" that `EC-1` commits atomically with the raise?" in M9_TASK
        assert "WI-6" in M9_TASK
        # AQ-6: machine §14 says {OPEN, ACKNOWLEDGED}; target spec §12.9 says `any`.
        assert "does `EC-4` age from `{OPEN, ACKNOWLEDGED}`, or from ANY state?" in M9_TASK
        assert "notational compression" in M9_TASK_FLAT

    def test_each_question_names_what_every_reading_agrees_on(self):
        """The builder is not blocked by the conflict. It is blocked from RESOLVING it — and told
        exactly what it may still build."""
        section = M9_TASK[M9_TASK.index("### 3.8"):M9_TASK.index("### 3.9")]
        blocks = re.split(r"(?=\*\*`M9-AQ-)", section)[1:]
        assert len(blocks) == 6, f"§3.8 holds {len(blocks)} question blocks, not six"
        for question, block in zip(self.AQS, blocks):
            assert question in block
            assert "**Every reading agrees on:**" in block, (
                f"{question} states both sides and never says what may still be built"
            )
        assert "Do not resolve it by widening a specification." in M9_TASK_FLAT
        assert "do not edit `observation.py`" in M9_TASK_FLAT
        assert "do not edit `external_effect.py`" in M9_TASK_FLAT
        assert "do not edit `conflict.py`" in M9_TASK_FLAT
        assert "do not edit `expectation.py`" in M9_TASK_FLAT
        assert "do not edit `work_item.py`" in M9_TASK_FLAT
        assert (
            "Do not invent a state, a transition or an event to hold cancellation" in M9_TASK_FLAT
        )

    def test_the_scenario_asserts_nothing_about_the_open_questions(self, m9):
        """The scenario must not encode a resolution either.

        There is no required literal about whether an ACTIVE rule may resolve one, none about what
        cancellation maps to, none about which source kinds carry a foreign key, none about who wires
        the five landed seams, none about what the persisted freeze write IS, and none about whether
        `EC-4` ages from `any` state.
        """
        visible = " ".join(m9.expect_visible)
        for invented in FORBIDDEN_EVENTS:
            assert invented not in visible, (
                f"the scenario requires an unregistered event name {invented!r}, which resolves an "
                "authority question by minting a name"
            )
        upper = visible.upper()
        assert "AN ACTIVE RULE RESOLVES" not in upper
        assert "A CANCELLED EXCEPTION" not in upper
        assert "SOURCE_REF IS AN OBSERVATION" not in upper
        assert "EVERY EXCEPTION FREEZES AN ENTITY" not in upper.replace(
            "NOT EVERY EXCEPTION FREEZES AN ENTITY", ""
        )
        # What it DOES require is the part every reading agrees on.
        assert "RESOLUTION REQUIRES A decision_ref THAT RESOLVES" in m9.expect_visible
        assert "A RETRACTED CAUSE STILL REQUIRES AN EVENT AND A decision_ref" in m9.expect_visible
        assert "NOT EVERY EXCEPTION FREEZES AN ENTITY" in m9.expect_visible
        assert "THE M10, M11 AND M12 MACHINES ARE NOT BUILT" in m9.expect_visible

    def test_the_optional_dedup_index_is_not_turned_into_a_requirement(self, m9):
        """The seventh place the corpus could be over-read, and the one the founder named by hand.

        Entity §17, machine §17 and F9's cross-cutting section each say OPTIONAL. Machine §19 says
        *"GR-4 + the dedup index"*, which is the nearest thing to a requirement — so the task must
        record BOTH readings and require neither.
        """
        assert "DO NOT TURN AN EXPLICITLY OPTIONAL CONSTRAINT INTO A MANDATORY ACCEPTANCE " \
               "CRITERION." in M9_TASK_FLAT
        assert "state which you did and why" in M9_TASK_FLAT
        assert 'Machine §19 says *"GR-4 + the dedup index"*' in M9_TASK_FLAT
        assert "record that reading beside your choice rather than treating it as one" \
            in M9_TASK_FLAT

    def test_v10_is_explicitly_left_unresolved(self):
        """One open validation item, `NOT A BLOCK`, with a fail-closed default.

        `V10` is the per-lane ageing and escalation thresholds — a customer's operating policy with a
        number on it — and its fail-closed default is *ages, escalates, never expires*. A builder
        that "discovers" that a Sev-0 escalates after four hours has invented a product decision.
        """
        assert "V10" in M9_TASK
        assert "it is NOT a block." in M9_TASK
        assert "DO NOT CHOOSE A BUSINESS AGEING THRESHOLD." in M9_TASK
        assert "ages · escalates · NEVER EXPIRES" in M9_TASK_FLAT
        assert (
            "THE FAIL-CLOSED BEHAVIOUR IS THE PART YOU MUST BUILD" in M9_TASK
        ), "the task rules the thresholds out without saying what the builder must still build"
        assert "a caller-supplied parameter with no default that means anything" in M9_TASK_FLAT

    def test_the_two_debt_rows_that_name_m9_are_recorded_without_being_closed(self):
        """`P6-D1` and `P6-D3` both say "M9" in their disposition, and neither is M9's to close.

        `P6-D1` says *"M9 owns that determination"* about whether `ExceptionResolved` should join
        `K-1`'s human-decision set — a determination that REPORTS, because acting on it means
        amending a protected specification and editing M1. `P6-D3` says the Sev-0 raise for an
        ownerless Work Item is *"M9 and M2"* — and wiring M1's detector to M9's seam is an M1 change.
        And `P6-D4`, the RULE branch's refusal, closes at M12 rather than here.
        """
        assert "P6-D1" in M9_TASK
        assert "P6-D3" in M9_TASK
        assert "P6-D4" in M9_TASK
        assert "M9 owns that\ndetermination." in M9_TASK, (
            "the task never quotes P6-D1's own disposition, so a builder cannot tell that the "
            "determination is one to REPORT rather than one to act on"
        )
        assert (
            "Do not amend `K-1`, do not add a name to `HUMAN_DECISION_EVENTS`, and do not edit "
            "`work_item.py`" in M9_TASK_FLAT
        )
        assert "M9's half is that a raise seam EXISTS which such a detector could call." \
            in M9_TASK_FLAT
        assert "closes at M12 — NOT at M9" in M9_TASK_FLAT

    def test_the_f14_scoping_decision_is_stated_not_guessed(self):
        """Six F14 tripwires are in play and exactly one is M9's — and three of the other five are
        the Sev-0 SOURCE DETECTORS F9 names, which is `M9-AQ`'s `F` seam."""
        assert "IllegalTransitionAttempted" in M9_TASK
        assert "is MANDATORY and is yours" in M9_TASK
        assert "CrossTenantAccessAttempted" in M9_TASK
        assert "OrphanAdapterInvocation" in M9_TASK
        assert "ProjectionRebuildDiverged" in M9_TASK
        assert "ProvenanceStrengtheningAttempted" in M9_TASK
        assert "OwnerAssertedOverwriteAttempted" in M9_TASK
        assert "is NOT yours" in M9_TASK


# --------------------------------------------------------------------------
# 5. The seams — feed them, never edit the landed unit on the other side
# --------------------------------------------------------------------------


class TestTheSeamsAreScopedToM9:
    """M9 sits downstream of more landed units than any P6 unit before it. Five machines' human-owned
    states point AT it, its `decision_ref` resolver is M1's, its `source_ref` points at eight-plus
    aggregate types, its freeze has no mechanism it owns, and its Sev-0 brake belongs to the F14
    detectors at the source. Five seams, five different ways to answer an authority question by
    writing someone else's code.
    """

    def test_the_task_states_the_k1_resolver_seam_and_forbids_a_second_one(self):
        assert "M1's `resolve_decision_ref`, which you IMPORT." in M9_TASK
        assert "IMPORT M1's `resolve_decision_ref`. DO NOT WRITE A SECOND ONE." in M9_TASK
        assert (
            "Writing a second resolver is the defect this seam exists to prevent." in M9_TASK_FLAT
        )
        assert "start accepting `done`" in M9_TASK_FLAT
        assert "second `decision_ref` resolver" in M9_TASK_FLAT, (
            "the task's `Do not` list never names the second resolver alongside the second gate, the "
            "second effect authority and the second timer"
        )

    def test_the_task_states_the_permanent_classification_seam_and_forbids_a_classifier(self):
        assert "M1's `FailureDisposition`, which is the landed TRANSIENT/PERMANENT vocabulary." \
            in M9_TASK
        assert "There is NO landed classifier in this repository" in M9_TASK_FLAT
        assert "A CATCH-ALL BASE CLASS IS NOT A CLASSIFICATION" in M9_TASK
        assert (
            "Do not write a function that maps an exception message, an HTTP status, a vendor error "
            "string or a model's opinion to PERMANENT." in M9_TASK_FLAT
        )
        assert "M-74" in M9_TASK and "L-D" in M9_TASK

    def test_the_task_states_the_brake_seam_and_puts_it_at_the_source(self):
        """F9: Sev-0 exceptions are produced BY F14 detectors and auto-engage the brake. The brake is
        the DETECTOR's act, and M9's half is only that an Exception can CARRY `SEV0`."""
        assert "M13's brake (`brake.py`), which M9 does not touch." in M9_TASK
        assert "THE BRAKE IS THE DETECTOR'S ACT, AT THE SOURCE." in M9_TASK_FLAT
        assert "M9's half is that an Exception can CARRY `SEV0`" in M9_TASK_FLAT
        assert (
            "do not call\n`brake.engage` from `exception.py`" in M9_TASK
            or "do not call `brake.engage` from `exception.py`" in M9_TASK_FLAT
        )
        # And the task must say what is landed TODAY, because that is what decides what M9 may
        # assume rather than build: one of the three detectors engages, and two deliberately do not.
        assert "effect_boundary.py" in M9_TASK
        assert "RETURNS findings and deliberately does not\nengage a brake" in M9_TASK or (
            "RETURNS findings and deliberately does not engage a brake" in M9_TASK_FLAT
        )
        assert "no production caller exists yet" in M9_TASK_FLAT

    def test_the_task_states_the_five_producer_seams_without_wiring_them(self):
        """M5, M6, M7, M8 and M3 each deferred a "→ Exception" seam here BY NAME, and P5's
        `expire_overdue` says so in code. The cheapest way to close all five is to edit all five."""
        assert "who wires the five landed" in M9_TASK
        assert "expire_overdue" in M9_TASK
        assert (
            "the caller gets the owner and the evidence and is the one that can" in M9_TASK_FLAT
        )
        assert "a CALLABLE `EC-1` RAISE SEAM" in M9_TASK_FLAT
        assert (
            "the absence of M9 before M9 existed is not a defect in those units" in M9_TASK_FLAT
        ), (
            "the task never tells the builder that the unwired seams are the deferral each landed "
            "unit recorded, rather than a bug it is being asked to fix"
        )
        assert "do not wire them by editing someone else's file" in M9_TASK_FLAT

    def test_the_task_states_the_source_ref_seam_with_the_landed_precedent(self):
        assert "The `source_ref` — what an Exception points BACK at" in M9_TASK
        assert "THE LANDED PRECEDENT IS `conflict_parties`, AND IT IS EXACT." in M9_TASK
        assert "MIRROR" in M9_TASK
        assert "only for the kinds whose table exists" in M9_TASK_FLAT
        for kind in ("observation", "identity_binding_claim", "conflict", "expectation",
                     "work_item", "pipeline_instance", "effect_grant", "approval"):
            assert kind in M9_TASK, f"the source kind {kind!r} is never discussed"
        assert "The kinds whose table does NOT exist" in M9_TASK
        assert "`entity_ref` and `source_ref` are two\n  different references" in M9_TASK or (
            "`entity_ref` and `source_ref` are two different references" in M9_TASK_FLAT
        )
        assert "K-2" in M9_TASK

    def test_the_task_states_the_freeze_seam_and_forbids_a_generic_freeze_table(self):
        assert "NOT EVERY EXCEPTION FREEZES AN ENTITY." in M9_TASK
        assert "only those that make a material field non-`consistent`" in M9_TASK_FLAT
        assert (
            "Do not build a generic\nfreeze table" in M9_TASK
            or "Do not build a generic freeze table" in M9_TASK_FLAT
        )
        assert "do not edit `work_item.py` to add an exception trigger to `WI-6`" in M9_TASK_FLAT
        assert "`EvidenceMissing`/`ConflictRaised` and contains no\n  exception trigger at all" \
            in M9_TASK or (
                "`EvidenceMissing`/`ConflictRaised` and contains no exception trigger at all"
                in M9_TASK_FLAT
            )
        assert "name the clause, say that it is an M1 change with an M1 review, and stop before " \
               "making it." in M9_TASK_FLAT

    def test_the_task_forbids_editing_the_p3_kernel_while_feeding_it(self):
        """P3 remains the gate minter, and step 4 already exists. M9 feeds it; it does not become a
        second one, and it does not edit `checkpoint.py`."""
        assert "Do not create a second gate authority" in M9_TASK_FLAT
        assert "Do not edit `checkpoint.py`." in M9_TASK_FLAT
        assert "EvidenceCondition" in M9_TASK
        assert "NativeClaim" in M9_TASK
        assert "step 4" in M9_TASK
        assert "M3 remains the single effect authority" in M9_TASK_FLAT
        assert "without importing the checkpoint" in M9_TASK_FLAT, (
            "the task never names the landed projection shape M7 and M8 both ship, so a builder "
            "would reach for an import to demonstrate the seam"
        )

    def test_the_task_states_the_foreign_keys_that_have_a_table_to_point_at(self):
        """Entity §18 names three references and only some have a target today. A builder that takes
        §18 literally builds the Rule registry and the freight projection to satisfy it."""
        assert "The foreign keys entity §18 names, and what exists to point at" in M9_TASK
        for column in ("owner_id", "source_ref", "decision_ref", "acknowledged_by", "entity_ref"):
            assert column in M9_TASK, f"the reference {column} is never discussed"
        assert "build the foreign keys whose targets exist" in M9_TASK_FLAT
        assert "name the clause and stop" in M9_TASK_FLAT
        assert (
            "Do not build `evidence`, `compensations`, `policies` or `rules`" in M9_TASK_FLAT
        )
        assert "resolve it, do not FK it" in M9_TASK_FLAT, (
            "the task never says that a decision_ref is RESOLVED by M1's resolver rather than "
            "foreign-keyed, which is what M3 and M7 both landed"
        )

    def test_the_task_states_the_queue_is_an_ordering_rather_than_a_product(self):
        """Entity §42 and machine §38 both call the Sev-0 and `NEEDS_VERIFICATION`-backed exceptions
        the highest-priority operational queue. That sentence is about what an Exception is FOR, and
        it is the one that gets read as a build instruction."""
        assert "The queue is an ORDERING, not a product" in M9_TASK
        assert "What M9 DOES NOT OWE is a queue." in M9_TASK
        assert "Do not build an exception-queue UI." in M9_TASK
        assert "exceptions become a managed queue with owners" in M9_TASK, (
            "the task never quotes the registry's own statement that the queue is the P8 product "
            "form, so a builder cannot tell the sentence apart from a requirement"
        )
        assert "mean-time" in M9_TASK.lower() or "MTTR" in M9_TASK

    def test_the_task_names_the_machines_own_types_so_the_sweep_reads_event_names(self):
        """The unregistered-name sweep matches an identifier beginning `Exception` + a capital. M7
        ships `M7Machine`/`CfState`, so the only such identifiers in `conflict.py` are its five
        registered events — which is what makes that sweep a measurement rather than a trap. M9 also
        has to avoid shadowing the BUILTIN, which no earlier unit had to think about."""
        assert "NAME THE MACHINE'S OWN TYPES THE WAY `conflict.py` NAMES ITS OWN." in M9_TASK
        assert "M9Machine" in M9_TASK and "EcState" in M9_TASK and "EcRecord" in M9_TASK
        assert (
            "An identifier beginning `Exception` followed by a capital letter that is not one of "
            "the six registered F9 event names fails the sweep" in M9_TASK_FLAT
        )
        assert "THE MODULE IS `exception.py`, AND IT DEFINES NO CLASS CALLED `Exception`." in M9_TASK
        assert "would shadow the builtin and break `except Exception:`" in M9_TASK_FLAT


# --------------------------------------------------------------------------
# 6. The vocabulary is safe, and visible to the generator
# --------------------------------------------------------------------------


class TestTheM9Vocabulary:
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
            base_scenario=load_scenario(M9_PATH),
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
        instead of relearned — and M9's names are full of words a boundary might dislike: `delete`,
        `autoclose`, `sweep`, `reaper`, `escalate`.
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
                f"{PROBE} --case inactivity-never-closes-an-exception "
                f"--inject {fault} --concurrency 8 --delay-ms 5000 --repeat 5 "
                "--tenants 3 --age-ms 86400000 --severity SEV0 --actor model "
                "--decision-ref unresolvable --freeze material --seed 4211"
            )
            ok, why = approved.approves(command)
            assert ok, f"{fault}: {why}"

    def test_the_delete_and_sweep_faults_are_not_read_as_destructive_tooling(self, approved):
        """`delete-exception` and `sweep-close` are the two faults whose NAMES describe the
        destruction the machine must refuse.

        A boundary that read the bare token `delete` as a destructive command would make the two
        cases that prove an Exception is never deleted unreachable — and the fix would have been to
        weaken the guard rather than to notice. They are argument VALUES, not commands.
        """
        for fault in ("delete-exception", "sweep-close", "expire-exception", "autoclose"):
            ok, why = approved.approves(
                f"{PROBE} --case no-sweep-or-reaper-closes-an-exception --inject {fault}"
            )
            assert ok, f"the fault {fault!r} is refused by the boundary ({why})"

    @pytest.mark.parametrize(
        "command",
        [
            # A command nobody wrote down.
            "curl https://api.example.com/exceptions",
            ".venv/bin/python scripts/send_real_invoice.py",
            "rm -rf src/freight_recon",
            # An oversight queue or notifier, which is precisely what M9 must not grow.
            ".venv/bin/python -m freight_recon.ops_control --exception-queue",
            ".venv/bin/python scripts/slack_probe.py --page-on-sev0",
            # An actual deletion of the row the machine exists to keep.
            ".venv/bin/python -c \"import sqlite3; sqlite3.connect('x').execute('DELETE FROM exceptions')\"",
            # The approved probe, extended with composition.
            f"{PROBE} --case autoclose-is-an-illegal-transition; curl https://evil.example.com",
            f"{PROBE} --case autoclose-is-an-illegal-transition && rm -rf /",
            f"{PROBE} | tee /tmp/out",
            f"{PROBE} > /etc/hosts",
            f"{PROBE} --case $(whoami)",
            f"{PROBE} --case `whoami`",
            # A newline is whitespace; normalization would hide it, so the raw string is scanned for
            # control characters first.
            f"{PROBE}\ncurl https://evil.example.com",
            # A prefix that is not a prefix.
            ".venv/bin/python scripts/probe_phase6_exception.py.bak",
        ],
    )
    def test_a_command_outside_the_vocabulary_is_refused(self, approved, command):
        ok, why = approved.approves(command)
        assert not ok, f"escaped the approved set: {command!r}"
        assert why, "a refusal must say why"

    def test_a_dimension_value_carrying_shell_is_still_refused(self, approved):
        """The axis is argument-only. A flag is not a hole."""
        for hostile in ("$(id)", "`id`", "a;id", "a|id", "a>/etc/hosts", "a&&id"):
            for flag in ("--inject", "--actor", "--decision-ref", "--severity", "--freeze"):
                ok, _ = approved.approves(
                    f"{PROBE} --case tenant-isolation {flag} {hostile}"
                )
                assert not ok, f"{flag} smuggled shell through: {hostile!r}"

    def test_the_probe_with_an_ordinary_case_tail_is_still_allowed(self, approved):
        """The boundary has to let the real vocabulary through, or it has only made generation
        useless rather than safe."""
        ok, why = approved.approves(f"{PROBE} --case resolution-requires-a-decision-ref")
        assert ok, why

    def test_the_neighbouring_probes_stay_reachable_through_the_scenario(self, approved):
        """They are not enumerated in the config, and they do not need to be.

        M9 co-commits with none of them — it adds one table and edits the canonical schema, which is
        a REGRESSION relationship. Writing their bare probes into `p6_m9_exception.yaml` as
        regression anchors already approves every `--case` tail of each, because approval matches by
        prefix.
        """
        for command in (
            ".venv/bin/python scripts/probe_phase6_expectation.py --case tenant-isolation",
            ".venv/bin/python scripts/probe_phase6_conflict.py --case tenant-isolation",
            ".venv/bin/python scripts/probe_phase6_observation.py --case tenant-isolation",
        ):
            ok, why = approved.approves(command)
            assert ok, f"{command}: {why}"

    def test_the_rendered_brief_actually_shows_the_m9_vocabulary(self, tmp_path):
        """The brief truncates the approved list, silently. A vocabulary the generator never sees is
        a vocabulary it cannot choose from."""
        vocabulary = _local_vocabulary()
        if not any("probe_phase6_exception.py" in entry for entry in vocabulary):
            pytest.skip("no local driver.config.yaml enumerating the M9 vocabulary")

        planner = self._planner(tmp_path, vocabulary)
        planner.plan_initial(task="Build P6/M9 Exception", unit=None, run_id="r-m9")
        brief = planner.reasoner.briefs[0].render()

        assert PROBE in brief, "the deterministic M9 entry point is not in the brief"
        missing = [
            entry.split("--case ", 1)[1].split()[0]
            for entry in vocabulary
            if "probe_phase6_exception.py --case " in entry and entry not in brief
        ]
        assert not missing, (
            "the approved-command list was truncated before these M9 cases: "
            f"{missing}. The brief renders at most {MAX_RENDERED_COMMANDS} commands; the "
            f"approved set now holds {len(planner.approved_commands)}."
        )

    def test_no_m9_command_falls_off_the_end_of_what_the_brief_renders(self, tmp_path):
        """The brief truncates the approved list at `MAX_RENDERED_COMMANDS`, SILENTLY.

        M8's readiness file asserted a bare total (`len(approved) <= MAX_RENDERED_COMMANDS`), and by
        M9 that total is genuinely exceeded: the union of every permanent scenario's commands plus
        this unit's enumerated vocabulary is larger than the render budget, and it will keep growing
        as P6 accumulates units. A bare count cannot tell "the budget is exceeded" apart from "THIS
        unit is invisible", and only the second is a defect.

        So the assertion is the property the count was standing in for, stated directly and measured
        against the same slice the generator actually sees: ### **no M9 entry may be truncated.**
        Everything the budget does cut is named, so the truncation is visible in the evidence rather
        than silent — and if an M9 command ever lands past the bound, this fails and says which.
        """
        planner = self._planner(tmp_path, _local_vocabulary())
        entries = list(planner.approved_commands.entries)
        shown, cut = entries[:MAX_RENDERED_COMMANDS], entries[MAX_RENDERED_COMMANDS:]

        m9_entries = [e for e in entries if "probe_phase6_exception.py" in e]
        assert m9_entries, "the M9 vocabulary is not in the approved set at all"
        lost = [e for e in cut if "probe_phase6_exception.py" in e]
        assert not lost, (
            f"{len(lost)} M9 command(s) fall past the brief's {MAX_RENDERED_COMMANDS}-command render "
            f"bound and are invisible to the generator: {lost[:5]}. The approved set holds "
            f"{len(entries)}. Trim the enumerated vocabulary or raise the bound — do not leave the "
            "unit under test half-visible."
        )
        assert set(m9_entries) <= set(shown), "an M9 command is approved but never rendered"
        # And what the budget DOES cut is prior units' probe tails, which are reachable by prefix
        # from their own permanent scenarios and are regression anchors rather than this unit's
        # composable vocabulary. Named rather than glossed.
        assert all("probe_phase6_exception.py" not in e for e in cut)

    def test_the_local_config_targets_m9_when_it_exists(self):
        """The retarget is the established convention: `driver.config.yaml` carries one unit at a
        time, and a stale target is how a run verifies the previous unit while claiming this one."""
        local = DRIVER_ROOT / "driver.config.yaml"
        if not local.exists():
            pytest.skip("no local driver.config.yaml on this checkout")
        raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        assert raw.get("scenario") == "p6_m9_exception", (
            f"the local config still targets {raw.get('scenario')!r}; a run would verify the "
            "previous unit and report this one"
        )
        vocabulary = raw.get("scenario_generation", {}).get("approved_commands") or []
        stale = [c for c in vocabulary if "probe_phase6_expectation.py --case" in c]
        assert not stale, (
            f"{len(stale)} M8 `--case` entries are still enumerated in the local vocabulary. M8's "
            "probe is a REGRESSION ANCHOR in the M9 scenario, and a prefix match already approves "
            "every tail of it — enumerating M8's cases only spends the brief's render budget on "
            "the previous unit"
        )


# --------------------------------------------------------------------------
# 7. Dynamic generation can close an M9 coverage gap, safely
# --------------------------------------------------------------------------


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close.

    Built as the planner builds one: the citation lives on the provenance, and a coverage-gap case
    that cannot name a risk from this run's own register is refused before it reaches the boundary at
    all.
    """
    return GeneratedScenario(
        id="gen-m9-closure-by-silence",
        title="nothing closes an exception except a decision",
        purpose=(
            "an exception left untouched past every threshold must still be OPEN and still owned; "
            "a closure without a decision_ref is not a closure, it is a forgetting"
        ),
        risk_category=RiskCategory.SAFETY_INVARIANT,
        priority=Priority.P0,
        rationale="the identified closure-by-silence risk had no scenario behind it",
        requirement_reference="P6/M9",
        product_principle_reference="honest-unknowns",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared state, so "
            "nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m9-task",
            session_id="scripted",
            generating_risk="an untouched exception could be closed by inactivity rather than by a human",
            source_risks=[risk_key],
        ),
        actions=[{
            "kind": "command",
            "name": "leave the exception untouched past every threshold",
            "command": command,
            # The command that prints it, named. An asserted literal no operation in the scenario
            # declares is refused as an unattributable oracle.
            "expect_contains": ["INACTIVITY NEVER CLOSES AN EXCEPTION"],
        }],
        # `safety_invariant` is a family whose claims are about a TABLE — "RESOLVED requires a
        # decision_ref" is not something a probe can prove by printing it. This is the mechanical
        # form of the rubric's "a 200 is not success".
        persisted_state_checks=[
            GeneratedStateCheck(
                name="the exception layer is still tenant-first and readable",
                command=STATE_ORACLE,
                contains=["problems: []", "exceptions"],
            )
        ],
        expected_observations=["INACTIVITY NEVER CLOSES AN EXCEPTION"],
        forbidden_observations=["### INACTIVITY CLOSED AN EXCEPTION ###"],
    )


class TestGenerationClosesM9GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-closure-by-silence",
            description="an untouched exception could be closed by inactivity rather than by a human",
            risk_category=RiskCategory.SAFETY_INVARIANT,
            severity=Priority.P0,
            basis="F-30 is the mandate the unit exists to satisfy",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m9", "p6", "m9"},
                principle_tokens={"honest-unknowns"},
                known_risk_ids={risk.key, "R-closure-by-silence"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m9_vocabulary_is_accepted(self, context):
        ctx, risk = context
        command = (
            f"{PROBE} --case inactivity-never-closes-an-exception "
            "--inject inactivity-close --age-ms 86400000 --actor system --seed 7"
        )
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M9 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_the_whole_mutation_axis_is_reachable_from_a_gap_case(self, context, dimensions):
        ctx, risk = context
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case inactivity-never-closes-an-exception "
                f"--inject {fault} --concurrency 4 --delay-ms 40 --tenants 2 --age-ms 3600000 "
                "--severity SEV0 --actor model --decision-ref absent --freeze material --seed 11"
            )
            accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
            assert accepted, f"{fault}: {rejected}"

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario('python -c "import exception; exception.close_all()"', risk.key)],
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

    def test_an_uncovered_p0_m9_risk_blocks_acceptance(self):
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
                    id="R-closure-by-silence",
                    description="an untouched exception could be closed by inactivity",
                    risk_category=RiskCategory.SAFETY_INVARIANT,
                    severity=Priority.P0,
                    basis="F-30 is the mandate the unit exists to satisfy",
                )
            ],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert "KNOWN COVERAGE GAPS" in verdict.summary_block()


# --------------------------------------------------------------------------
# 8. P6-D46 stays closed for M9
# --------------------------------------------------------------------------


class TestP6D46StaysClosedForM9:
    """`P6-D46`: the M6 re-verification run proposed nine scenarios, every one declared a
    `risk_category` the harness's own enum did not contain, all nine were discarded at the parse
    stage, and the run reported *"0 generated case(s) + 1 permanent scenario"* and ACCEPTED.

    Nothing had failed. The product was fine. But *"the generator legitimately produced nothing new"*
    and *"the generator produced nine and Product Driver could not read any of them"* had collapsed
    into one number, and only the first is a reason to accept.

    The fix is general and lives in `tests/test_generation_contract.py`. What is pinned HERE is that
    M9 does not reopen it from the permanent-scenario side: the M9 file uses only canonical
    categories, a category it invented would refuse to load, and the four counts stay separable for
    an M9 wave. **Nothing about M9 is special-cased inside Product Driver core to achieve that.**
    """

    #: What `--case inactivity-never-closes-an-exception` would print, recorded as the task's own
    #: output contract states it. `repo` below is a tmp_path with no Neyma in it, so the quality
    #: boundary has nothing to interrogate and would refuse the fixture's own correct oracle — which
    #: is a true answer to the wrong question here.
    RECORDING = {
        f"{PROBE} --case inactivity-never-closes-an-exception": (
            "INACTIVITY NEVER CLOSES AN EXCEPTION\n"
            "behaviours as specified, 0 wrong\n"
        ),
    }

    def _planner(self, tmp_path: Path, payloads) -> ScenarioPlanner:
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner(list(payloads)),
            base_scenario=load_scenario(M9_PATH),
            permanent_scenarios=[load_scenario(M9_PATH)],
            founder=FakeFounder(),
            contract_probe=recorded_contract_probe(self.RECORDING),
        )

    def _m9_raw(self, scenario_id: str, category: str) -> dict:
        """A proposal shaped for THIS unit: dark, command-driven, with a persisted-state oracle.

        The shared fixture's default is an HTTP approval scenario, which M9's dark base scenario
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
                "name": "leave the exception untouched past every threshold",
                "command": f"{PROBE} --case inactivity-never-closes-an-exception",
                "expect_contains": ["INACTIVITY NEVER CLOSES AN EXCEPTION"],
            }],
            state_checks=[{
                "name": "the exception layer is still tenant-first and readable",
                "command": STATE_ORACLE,
                "contains": ["problems: []"],
            }],
            expected_observations=["INACTIVITY NEVER CLOSES AN EXCEPTION"],
            forbidden_observations=["### INACTIVITY CLOSED AN EXCEPTION ###"],
            cleanup=[],
            isolation_key="exception-db",
            isolation_note=(
                "the probe builds its own temporary database per case and touches no shared "
                "state, so nothing survives it to contaminate the next scenario"
            ),
            generating_risk="an untouched exception could be closed by inactivity",
        )

    def test_the_m9_scenario_declares_only_canonical_categories(self, m9):
        """The half a permanent scenario can break on its own.

        Every `verifies:` entry names a `RiskCategory` member, checked against the ONE taxonomy
        rather than against a list this file keeps.
        """
        declared = m9.declared_risk_categories()
        assert declared, "the M9 scenario declares no risk coverage at all"
        unknown = sorted(declared - set(RISK_CATEGORY_VALUES))
        assert not unknown, (
            f"the M9 scenario declares categories the harness taxonomy does not contain: {unknown}"
        )

    def test_an_invented_category_in_the_m9_file_would_refuse_to_load(self, tmp_path):
        """The load-time refusal, exercised against a copy of the REAL M9 file.

        A `verifies:` entry naming a category the taxonomy does not hold would match no risk and read
        as coverage while providing none — which is `P6-D46`'s shape one layer down. This proves the
        M9 file is covered by the refusal rather than merely compatible with it.
        """
        raw = yaml.safe_load(M9_PATH.read_text(encoding="utf-8"))
        raw["verifies"][0]["risk_category"] = "closed-without-a-decision"
        broken = tmp_path / "m9_broken.yaml"
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

    def test_an_unreadable_m9_candidate_is_a_contract_blocker_not_a_silent_zero(self, tmp_path):
        """The `P6-D46` shape, reproduced with M9-flavoured categories, against the M9 base scenario.

        Nine well-meant descriptions of specific M9 defects — none of them a member of a closed
        family vocabulary — must be recorded as CONTRACT rejections the candidates survive, not
        dropped into "0 generated scenarios". And the run may not reach a normal acceptance while
        they stand, even though nothing failed.
        """
        planner = self._planner(tmp_path, [
            raw_payload(
                *(
                    self._m9_raw(f"S{i}-m9", category)
                    for i, category in enumerate(M9_UNREADABLE_CATEGORIES, start=1)
                ),
                risks=[],
            )
        ])
        plan = planner.plan_initial(task="build P6/M9", unit=FakeUnit())

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

    def test_a_full_green_m9_suite_still_cannot_accept_over_an_unreadable_wave(self, tmp_path):
        """The invariant that makes it a blocker rather than a note.

        This is bit for bit the run that ACCEPTed: the permanent M9 scenario passed and no generated
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
                    self._m9_raw(f"S{i}-m9", category)
                    for i, category in enumerate(M9_UNREADABLE_CATEGORIES, start=1)
                ),
                risks=[],
            )
        ])
        planner.plan_initial(task="build P6/M9", unit=FakeUnit())
        problems = planner.generation_problems()

        passed = ScenarioOutcome(
            scenario_id="p6_m9_exception",
            scenario_name="p6_m9_exception",
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
            "p6_m9_exception",
            lambda _m: None,
            generation_problems=problems,
        )
        assert decision.decision is Decision.BLOCKED

    def test_the_four_counts_stay_separable_for_an_m9_wave(self, tmp_path):
        """proposed / accepted / filtered / invalid are four facts, and summing them is the defect."""
        planner = self._planner(tmp_path, [
            raw_payload(
                self._m9_raw("gen-m9-valid", "safety_invariant"),
                self._m9_raw("gen-m9-unreadable", "closed-without-a-decision"),
                risks=[],
            )
        ])
        plan = planner.plan_initial(task="build P6/M9", unit=FakeUnit())

        wave = plan.waves[0]
        assert wave.proposed == 2
        assert wave.accepted_ids == ["gen-m9-valid"], (
            "the readable candidate was punished for its neighbour"
        )
        assert [r.id for r in wave.contract_rejections] == ["gen-m9-unreadable"]
        assert wave.filtered_rejections == []
        assert planner.generation_problems(), "a mixed wave stopped blocking"

    def test_an_honestly_empty_m9_wave_is_not_a_generation_problem(self, tmp_path):
        """The other half, and the reason this is not just "block whenever nothing ran"."""
        planner = self._planner(tmp_path, [{"risks": [], "scenarios": []}])
        plan = planner.plan_initial(task="build P6/M9", unit=FakeUnit())

        assert plan.waves[0].proposed == 0
        assert plan.waves[0].contract_rejections == []
        assert planner.generation_problems() == []

    def test_product_driver_core_does_not_special_case_m9(self):
        """The fix is general or it is not a fix.

        `P6-D46` was closed by making the taxonomy single-sourced and the rejection accounting
        honest — not by teaching the harness about a unit. A core module that names this unit would
        be a per-unit exception with a passing status.
        """
        core = DRIVER_ROOT / "neyma_product_driver"
        offenders = sorted(
            f.name
            for f in core.rglob("*.py")
            if "p6_m9_exception" in f.read_text(encoding="utf-8")
            or "phase6_exception" in f.read_text(encoding="utf-8")
        )
        assert not offenders, (
            f"Product Driver core names the M9 unit in {offenders}. Permanent scenarios, tasks and "
            "readiness tests carry unit knowledge; the harness carries none"
        )


# --------------------------------------------------------------------------
# 9. M9 is scoped as a unit, and cannot move the phase
# --------------------------------------------------------------------------


@pytest.fixture
def m9_repo(tmp_path: Path) -> PhaseRepo:
    """A phase in progress, one unit being built, a stated review rule."""
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/exception.py", "# the unit under construction\n")
    repo.commit_all("the M9 candidate")
    return repo


class TestM9IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m9(self, m9_repo: PhaseRepo):
        scope = m9_repo.scope(M9_TASK)
        assert scope.scope_id == "P6/M9"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(self, m9_repo: PhaseRepo):
        """The task discusses P6 at length. Discussing a phase is not claiming it, and a run that
        inherited the phase's bar would be held to five units that do not exist."""
        scope = m9_repo.scope(M9_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m9_repo: PhaseRepo):
        scope = m9_repo.scope(M9_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(self, m9_repo: PhaseRepo):
        rendered = m9_repo.scope(M9_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "enables nothing in production" in rendered


class TestM9CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m9_repo: PhaseRepo
    ):
        scope = m9_repo.scope(M9_TASK)
        completion = scoped_completion(scope, TaskResult.ACCEPTED, phase_accepted=True)
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M9"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m9_repo: PhaseRepo):
        completion = scoped_completion(m9_repo.scope(M9_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    def test_a_builder_claiming_p6_is_complete_is_caught(self, m9_repo: PhaseRepo):
        audit = m9_repo.audit(
            "M9 is implemented and verified. With M9 landed, P6 is COMPLETE and P7 is now "
            "unblocked.\n",
            M9_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_a_builder_claiming_production_enablement_is_caught(self, m9_repo: PhaseRepo):
        audit = m9_repo.audit(
            "M9 is implemented and verified. The exception queue is now enabled for live "
            "traffic.\n",
            M9_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_the_task_names_every_prohibited_expansion(self):
        """The M9-specific temptations, each named in the task's `Do not` list.

        M9's are different from M8's: this unit's product form is an OVERSIGHT QUEUE with owners and
        notifications, its Sev-0 severity reaches for the F14 detectors and the brake, its
        PERMANENT/TRANSIENT split reaches for a classifier that does not exist, its `decision_ref`
        reaches for the Rule registry, and its `V10` wants a business threshold.
        """
        for prohibition in (
            "M10–M13",
            "M10 Compensation",
            "P7 or later",
            "provenance and evidence platform",
            "Evidence Store",
            "V10",
            "P6-D1",
            "P6-D4",
            "HUMAN_DECISION_EVENTS",
            "oversight queue, exception-queue UI, dashboard, notifier, pager, on-call rotation",
            "MTTR metric emitter",
            "F14 detectors",
            "rebuild-divergence detector",
            "failure classifier",
            "freight workflows",
            "invoice automation",
            "cargo claims",
            "any alerting, incident-management, ticketing or exception-queue UI",
            "email_triage.py",
            "alert_channel.py",
            "operator_console.py",
            "action_callback.py",
            "mailbox_intake",
            "production autonomy",
            "live production effects",
            "production integrations",
            "legacy cleanup campaign",
            "broad documentation cleanup",
            "P6-D40",
            "push, publish or deploy",
        ):
            assert prohibition in M9_TASK, f"the task never forbids {prohibition!r}"
        for machine in ("M11\n  Policy", "M12 Rule", "M13 Brake"):
            assert machine in M9_TASK, f"the task never forbids beginning {machine!r}"
        assert "weaken **P3, P4 or P5**" in M9_TASK
        assert "polish **M1, M2, M3, M4, M5, M6, M7 or M8**" in M9_TASK
        assert "second `decision_ref` resolver" in M9_TASK_FLAT, (
            "the task never forbids a second K-1 resolver, which is the shape 'closed with the "
            "string done' arrives in for the one unit whose whole closure rests on K-1"
        )
        assert "engage, widen or narrow a brake" in M9_TASK_FLAT, (
            "the task never forbids M9 engaging a brake, which F9 puts at the SOURCE DETECTOR"
        )
        assert "one-connection-per-thread concurrency correction" in M9_TASK_FLAT, (
            "the task never protects the landed P3/P4 correction CURRENT.md says must not be reworked"
        )

    def test_p6_d40_is_named_as_conditional_rather_than_forbidden_outright(self):
        """The one prohibition that is not absolute."""
        assert "unless a real guard in it mechanically blocks this unit" in M9_TASK_FLAT

    def test_the_task_records_the_known_nonblocking_items_without_ordering_a_campaign(self):
        for item in ("P6-D53", "P6-D54", "P6-D55", "P6-D56", "P6-D57", "P6-D58"):
            assert item in M9_TASK, f"the known nonblocking item {item} is never recorded"
        assert "Each is recorded." in M9_TASK
        assert "STOP and report the conflict rather than guessing" in M9_TASK_FLAT

    def test_the_task_allows_exactly_one_blocking_prerequisite_and_requires_it_reported(self):
        assert "smallest blocking prerequisite" in M9_TASK_FLAT
        assert "identify it explicitly" in M9_TASK_FLAT


# --------------------------------------------------------------------------
# 10-11. The loop owns M9 end to end
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m9_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m9_repo.root, m9_repo.scope(M9_TASK), unit=m9_repo.unit()
        )
        assert requirement.required
        assert requirement.from_repository_authority

    def test_the_task_states_the_tier_and_says_why_it_took_the_higher_one(self):
        """CLAUDE.md §7: "When genuinely torn between two tiers, take the higher one once and say
        so."

        A state machine is tier 2 by itself. M9 also lands a MIGRATION, is load-bearing for TENANT
        ISOLATION, and decides whether an obligation Neyma could not resolve reaches a named human or
        is quietly forgotten — which is the mechanism `AC-SAFE-028`, `I1` and `F-30` all rest on.
        """
        assert "tier-1" in M9_TASK
        assert "migration" in M9_TASK_FLAT
        assert "tenant isolation" in M9_TASK_FLAT
        assert (
            "whether an obligation Neyma could not resolve reaches a named human or is quietly "
            "forgotten" in M9_TASK_FLAT
        )
        assert "take the higher tier once and say so, and this file says so" in M9_TASK_FLAT


class TestTheLoopOwnsM9EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m9_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session that wrote the code,
        with its evidence path intact."""
        builder = FakeBuilder(m9_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m9_repo, tmp_path, task=M9_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        assert "INDEPENDENT REVIEW" in builder.prompts[1]
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED

    async def test_the_corrected_tree_gets_a_brand_new_reviewer(
        self, m9_repo: PhaseRepo, tmp_path: Path
    ):
        """The reviewer must be a lineage that did not build M9, and the second reviewer must read
        the CORRECTED tree rather than the one the first one read."""
        builder = FakeBuilder(m9_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m9_repo, tmp_path, task=M9_TASK, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        assert len(set(reviewer.session_ids)) == 2, "the same reviewer session was reused"
        first = reviewer.bindings[0]["fingerprint"]
        second = reviewer.bindings[1]["fingerprint"]
        assert not first.matches(second), "the second reviewer read the same tree as the first"
        assert result.satisfying_review.fingerprint.matches(second)

    async def test_an_accept_is_scoped_m9_acceptance_and_never_p6_complete(
        self, m9_repo: PhaseRepo, tmp_path: Path
    ):
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m9_repo, tmp_path, task=M9_TASK, reviewer=reviewer)

        assert result.status is RunStatus.ACCEPTED
        assert result.audit is not None, "the run accepted without a completion audit"
        completion = result.audit.completion
        assert completion is not None
        assert completion.task_scope == "P6/M9"
        assert completion.task_result in {TaskResult.ACCEPTED, TaskResult.VERIFIED}
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_accepted is False
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    async def test_the_run_stops_at_m9_and_never_walks_into_m10(
        self, m9_repo: PhaseRepo, tmp_path: Path
    ):
        """Two halves of the same guarantee: the task forbids it in words, and the loop ends at its
        own scoped verdict rather than picking up the next unit."""
        assert "Stop at verified M9. Do not automatically continue into M10." in M9_TASK
        assert "begin **M10–M13**" in M9_TASK

        reviewer = FakeReviewer([supported()])
        result, store = await drive(m9_repo, tmp_path, task=M9_TASK, reviewer=reviewer)
        assert result.status is RunStatus.ACCEPTED
        assert result.audit.completion.task_scope == "P6/M9"

        journal = RunJournal(run_id=store.run_id, task=M9_TASK)
        journal.record_outcome(run_status="ACCEPTED")
        summary = journal.personal_summary()
        for forbidden in ("M10", "begin the next unit", "continue into"):
            assert forbidden not in summary.split("### 8. The ONE exact next move")[1], (
                f"the next move points past M9 ({forbidden!r})"
            )


# --------------------------------------------------------------------------
# 12. The founder summary says what M9 actually does, in normal language
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


def _m9_journal(**outcome) -> RunJournal:
    scenario = load_scenario(M9_PATH)
    journal = RunJournal(run_id="r-m9", task=M9_TASK)
    journal.task_scope_id = "P6/M9"
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


class TestTheFounderSummaryExplainsM9:
    def test_it_states_the_product_impact_in_normal_language(self):
        """The scenario description is what a founder reads to learn what the unit is for. It has to
        be a brokerage sentence, not a machine one."""
        scenario = load_scenario(M9_PATH)
        text = " ".join(scenario.description.split()).lower()
        for phrase in ("human", "carrier", "tms", "severity", "forgotten"):
            assert phrase in text, f"the description never mentions {phrase!r}"
        assert "ships dark" in text
        assert "never closed by silence" in text or "never quietly forgotten" in text, (
            "the description never states the closure invariant, which is the entire unit"
        )

    def test_it_never_says_p6_moved(self):
        journal = _m9_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary()
        assert "P6 is COMPLETE" not in summary
        assert "P6 COMPLETE" not in summary

    def test_it_does_not_imply_a_live_queue_or_production(self):
        """A negative that has to be written carefully.

        "enables nothing in production" is a sentence this summary SHOULD carry, so a bare search for
        "in production" fails on the correct text. What must not appear is an ENABLEMENT claim, and
        each phrase below is one.
        """
        journal = _m9_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary().lower()
        for claim in (
            "enabled for live traffic",
            "live traffic is",
            "production-ready",
            "enabled for customers",
            "the queue is live",
            "exceptions are being routed",
            "alerts are",
            "on-call",
        ):
            assert claim not in summary, f"the summary implies {claim!r}"
        # And the thing it must actively say, because "dark" is the whole posture.
        assert "enables nothing in production" in summary

    def test_no_founder_decision_says_none(self):
        journal = _m9_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        journal.record_stop(reason="M9 verified.", founder_decision_required="none")
        assert journal.founder_decision_required == ""


# --------------------------------------------------------------------------
# 13. THE MUTATION GUARD — does this file actually fail when the assertion is removed?
# --------------------------------------------------------------------------


def _mutate(edit) -> "object":
    """Load a copy of the SHIPPED M9 scenario with one load-bearing thing weakened.

    `edit` receives the raw YAML mapping and changes it in place. Nothing is written to the
    scenarios directory: the mutant lives in memory and is parsed through the real loader, so a
    weakening the loader itself refuses raises here rather than returning a Scenario.
    """
    import tempfile

    raw = copy.deepcopy(yaml.safe_load(M9_PATH.read_text(encoding="utf-8")))
    edit(raw)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m9_mutant.yaml"
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

    def test_the_baseline_mutant_is_the_shipped_file_unchanged(self, m9):
        """The control. If `_mutate` cannot round-trip the file, every result below is noise."""
        unchanged = _mutate(lambda raw: None)
        assert unchanged.name == m9.name
        assert len(unchanged.commands) == len(m9.commands)
        assert len(unchanged.expect_state) == len(m9.expect_state)
        assert len(unchanged.verifies) == len(m9.verifies)
        assert unchanged.expect_visible == m9.expect_visible
        assert unchanged.forbidden == m9.forbidden

    # ---- the owner, and I1 -------------------------------------------------------------------

    def test_removing_the_owner_requirement_turns_the_closure_assertion_red(self):
        """`I1`, `M-35`, `AC-SAFE-028`: an ownerless Exception is a structurally impossible state.
        Stop asserting the NOT NULL and the whole "it reaches a named human" half is unverified."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "an owner is required from creation and RESOLVED requires a decision_ref, as CHECKs",
            )
            check["contains"] = [c for c in check["contains"] if c != "owner_id NOT NULL: True"]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle().test_the_closure_invariant_is_asserted_as_a_database_check(
                checks
            )

    def test_permitting_an_owner_from_another_tenant_turns_the_live_attempt_red(self):
        """`[C-1]` and `AC-SAFE-025`. Remove the cross-tenant attempt and an exception owned by
        another brokerage's human becomes something nothing here would notice."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the live database refuses an ownerless exception and a resolution with no "
                "decision_ref",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "an owner from another tenant: refused"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="never asked to refuse"):
            TestPersistedStateIsTheOracle().test_the_forbidden_writes_are_attempted_against_a_live_database(
                mutant, checks
            )

    def test_dropping_the_human_fk_assertion_turns_the_foreign_key_assertion_red(self):
        """"A named human" is decoration while it is a text column. Without this the acknowledging
        and deciding humans could be free text a caller invents."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the owner and every human named on the row are FOREIGN KEYS into tenant_humans",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "every human-named column is FK-backed: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="could still be free text"):
            TestPersistedStateIsTheOracle(
            ).test_the_owner_and_every_named_human_are_asserted_as_foreign_keys(checks)

    # ---- the lifecycle vocabulary ------------------------------------------------------------

    def test_adding_a_sixth_lifecycle_state_turns_the_state_assertion_red(self):
        """Remove `'CANCELLED'` from `not_contains` and `M9-AQ-2` gets settled by a build session
        minting the state the registry does not hold."""
        def edit(raw):
            check = [c for c in raw["expect_state"] if "state vocabulary" in c["command"]][0]
            check["not_contains"] = [n for n in check["not_contains"] if n != "'CANCELLED'"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="invented CANCELLED state"):
            TestPersistedStateIsTheOracle().test_the_five_states_are_asserted_and_there_is_no_sixth(
                mutant
            )

    def test_dropping_a_canonical_state_turns_the_state_assertion_red(self):
        """`AGEING` is the state a build session collapses into `OPEN` because "it is still open".
        The corpus gives it its own state and its own event, and `EC-5` fires FROM it."""
        def edit(raw):
            check = [c for c in raw["expect_state"] if "state vocabulary" in c["command"]][0]
            check["contains"] = [c for c in check["contains"] if c != "'AGEING'"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="AGEING is not asserted in the CHECK"):
            TestPersistedStateIsTheOracle().test_the_five_states_are_asserted_and_there_is_no_sixth(
                mutant
            )

    def test_permitting_a_promoted_sub_status_turns_the_state_assertion_red(self):
        """The machine's header paragraph is the whole point: `AWAITING_HUMAN` is a `sub_status`
        value AND M1's registered state, so promoting it is a sixth state and a local synonym at
        once."""
        def edit(raw):
            check = [c for c in raw["expect_state"] if "state vocabulary" in c["command"]][0]
            check["not_contains"] = [n for n in check["not_contains"] if n != "'AWAITING_HUMAN'"]

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="invented AWAITING_HUMAN state"):
            TestPersistedStateIsTheOracle().test_the_five_states_are_asserted_and_there_is_no_sixth(
                mutant
            )

    def test_dropping_the_sub_status_field_check_turns_that_assertion_red(self):
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "sub_status is a field if it exists at all, and never a lifecycle state",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "sub_status is never a lifecycle state: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle().test_sub_status_is_asserted_to_be_a_field_and_never_a_state(
                mutant, checks
            )

    def test_requiring_the_optional_sub_status_column_turns_that_assertion_red(self):
        """The other direction, and the one the founder named by hand. `EC-1` writes `sub_status?`
        and F9 lists it as optional, so REQUIRING the column would invent a canonical obligation."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "sub_status is a field if it exists at all, and never a lifecycle state",
            )
            check["contains"].append("sub_status implemented as a column: True")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="REQUIRES the optional sub_status column"):
            TestPersistedStateIsTheOracle().test_sub_status_is_asserted_to_be_a_field_and_never_a_state(
                mutant, checks
            )

    # ---- closure, which is the unit ----------------------------------------------------------

    def test_permitting_resolved_with_no_decision_ref_turns_the_closure_assertion_red(self):
        """`F-30`, `GR-14`, `AC-MACH-903`. The single most load-bearing assertion in the file."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "an owner is required from creation and RESOLVED requires a decision_ref, as CHECKs",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "RESOLVED requires decision_ref: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="had to be decided"):
            TestPersistedStateIsTheOracle().test_the_closure_invariant_is_asserted_as_a_database_check(
                checks
            )

    def test_removing_the_positive_control_turns_the_live_attempt_assertion_red(self):
        """Without it, every "refused" line below is equally true of a table that refuses
        everything — a vacuous negative corpus wearing the costume of enforcement (`CLAUDE.md` §6)."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the live database refuses an ownerless exception and a resolution with no "
                "decision_ref",
            )
            check["contains"] = [
                c for c in check["contains"]
                if c != "positive control, a well-formed OPEN exception: ACCEPTED"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="no positive control"):
            TestPersistedStateIsTheOracle().test_the_forbidden_writes_are_attempted_against_a_live_database(
                mutant, checks
            )

    def test_removing_the_second_positive_control_turns_that_assertion_red(self):
        """The targeted half. Without a RESOLVED row that IS accepted, a database that refused the
        `RESOLVED` state outright would pass "RESOLVED with no decision_ref: refused"."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the live database refuses an ownerless exception and a resolution with no "
                "decision_ref",
            )
            check["contains"] = [
                c for c in check["contains"]
                if c != "second positive control, RESOLVED WITH a decision_ref: ACCEPTED"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="TARGETED rather than a blanket refusal"):
            TestPersistedStateIsTheOracle().test_the_forbidden_writes_are_attempted_against_a_live_database(
                mutant, checks
            )

    def test_dropping_the_survivor_count_turns_the_live_attempt_assertion_red(self):
        """A refusal that silently wrote a row is not a refusal."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the live database refuses an ownerless exception and a resolution with no "
                "decision_ref",
            )
            check["contains"] = [c for c in check["contains"] if c != "rows that survived: 2"]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="counts the survivors"):
            TestPersistedStateIsTheOracle().test_the_forbidden_writes_are_attempted_against_a_live_database(
                mutant, checks
            )

    def test_dropping_the_foreign_key_pragma_turns_the_live_attempt_assertion_red(self):
        """SQLite ignores `PRAGMA foreign_keys` issued inside a transaction, so a check that skipped
        `enable_and_verify_foreign_keys` would measure a database where a cross-tenant owner is
        ACCEPTED — and would report it as a pass."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the live database refuses an ownerless exception and a resolution with no "
                "decision_ref",
            )
            check["command"] = check["command"].replace(
                "create_canonical_schema(c); enable_and_verify_foreign_keys(c);",
                "create_canonical_schema(c);",
            )

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="ignores the foreign-key pragma"):
            TestPersistedStateIsTheOracle().test_the_forbidden_writes_are_attempted_against_a_live_database(
                mutant, checks
            )

    def test_dropping_the_k1_resolver_import_turns_that_assertion_red(self):
        """A second resolver is two places for one of them to start accepting the string `done`."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the decision_ref resolver is M1's, imported rather than rewritten",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "M9 imports the K-1 resolver: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle().test_the_k1_resolver_is_asserted_imported_rather_than_rewritten(
                checks
            )

    # ---- silence, timers, severity, replay ----------------------------------------------------

    def test_dropping_the_sweep_sweep_turns_the_invention_assertion_red(self):
        """Stop asserting `invented sweep/reaper/autoclose surfaces: []` and a nightly reaper becomes
        a hygiene improvement nothing notices."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "no expiry, sweep, reaper, auto-close or deletion was invented",
            )
            check["contains"] = [
                c for c in check["contains"]
                if c != "invented sweep/reaper/autoclose surfaces: []"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle(
            ).test_inventing_a_sweep_expiry_or_autoclose_is_a_scenario_failure(mutant, checks)

    def test_widening_the_invention_sweep_onto_the_refusals_turns_that_assertion_red(self):
        """The sweep must not flag the machine's own REFUSAL of a sweep — or staying green would
        mean deleting the `GR-1` case that proves an auto-close is illegal."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "no expiry, sweep, reaper, auto-close or deletion was invented",
            )
            check["command"] = check["command"].replace("(?!_?refuse|_?reject|_?illegal)", "")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="force the GR-1 case out of existence"):
            TestPersistedStateIsTheOracle(
            ).test_inventing_a_sweep_expiry_or_autoclose_is_a_scenario_failure(mutant, checks)

    def test_dropping_the_population_proof_turns_the_unregistered_name_sweep_red(self):
        """A negative assertion needs a proven population (`CLAUDE.md` §6).

        `unregistered names in the machine: []` prints an empty list against a file that does not
        exist. Without `machine source: present` beside it, the sweep reads as a pass over nothing.
        """
        def edit(raw):
            check = _named(
                raw, "expect_state", "M9 uses the six registered F9 contracts and invents no seventh"
            )
            check["contains"] = [c for c in check["contains"] if c != "machine source: present"]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="proven population"):
            TestPersistedStateIsTheOracle(
            ).test_the_six_f9_contracts_are_used_and_no_seventh_is_minted(checks)

    def test_dropping_previous_severity_turns_the_rebuild_assertion_red(self):
        """F9 states the reason in terms: without the recorded previous value a rebuild reproduces
        the ORIGINAL severity and can UNDER-STATE the live one — and a Sev-0 auto-engages a brake at
        its source, so the under-statement is a safety loss rather than a cosmetic one."""
        def edit(raw):
            check = _named(
                raw, "expect_state", "M9 uses the six registered F9 contracts and invents no seventh"
            )
            check["contains"] = [
                c for c in check["contains"]
                if not c.startswith("ExceptionSeverityChanged requires:")
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="four required severity-change fields"):
            TestPersistedStateIsTheOracle(
            ).test_the_rebuild_guarantee_is_read_out_of_the_contract_projection(checks)

    def test_dropping_the_severity_default_check_turns_the_severity_assertion_red(self):
        """A DEFAULT on `severity` is the exact mechanism by which "nobody said how bad this is"
        silently becomes a value."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "severity is a closed three-member vocabulary, enforced and never defaulted",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "severity is never defaulted: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle(
            ).test_the_severity_vocabulary_is_asserted_closed_and_never_defaulted(checks)

    def test_permitting_a_fourth_severity_turns_the_severity_assertion_red(self):
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "severity is a closed three-member vocabulary, enforced and never defaulted",
            )
            check["contains"] = [
                c for c in check["contains"]
                if c != "severity vocabulary is exactly three members: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="fourth severity"):
            TestPersistedStateIsTheOracle(
            ).test_the_severity_vocabulary_is_asserted_closed_and_never_defaulted(checks)

    def test_dropping_the_durable_timer_assertion_turns_the_timer_guard_red(self):
        """`EC-4` and `EC-5` are the only transitions that may move an Exception without a human.
        Stop asserting the machine schedules through `DurableTimers` and an in-memory sleep becomes
        indistinguishable from an ageing threshold."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "ageing and escalation ride P5's existing durable timers rather than a second timer "
                "mechanism",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "M9 schedules through DurableTimers: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle().test_ageing_is_asserted_to_ride_p5s_durable_timers(checks)

    def test_permitting_a_brake_engagement_turns_the_boundary_assertion_red(self):
        """F9 puts the Sev-0 brake at the SOURCE DETECTOR. An M9 that engaged one would be a machine
        acting on the world, which is the boundary `CLAUDE.md` rule 17 and ADR-011 both defend."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "the compensation, policy, rule and brake seams are fed without M10, M11, M12 or "
                "M13 being built",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "engages a brake from inside M9: []"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle().test_m9_authorizes_nothing_and_engages_no_brake(checks)

    # ---- the optional constraint, and the claim mapping ---------------------------------------

    def test_requiring_the_optional_dedup_index_turns_that_assertion_red(self):
        """The founder's explicit instruction, as a guard: an explicitly optional constraint may not
        become a mandatory acceptance criterion."""
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "every exception index is tenant-first, and the optional dedup index is recorded "
                "not required",
            )
            check["contains"].append("open-exception dedup index built: True")

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError, match="REQUIRES the open-exception dedup index"):
            TestPersistedStateIsTheOracle(
            ).test_the_dedup_index_is_recorded_as_optional_rather_than_required(mutant, checks)

    def test_dropping_the_tenant_first_index_assertion_turns_that_assertion_red(self):
        def edit(raw):
            check = _named(
                raw, "expect_state",
                "every exception index is tenant-first, and the optional dedup index is recorded "
                "not required",
            )
            check["contains"] = [
                c for c in check["contains"] if c != "every index is tenant-first: True"
            ]

        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle(
            ).test_the_dedup_index_is_recorded_as_optional_rather_than_required(mutant, checks)

    def test_removing_the_probe_from_the_regression_claim_turns_the_mapping_assertion_red(self):
        """The exact defect that blocked the M6 run, reintroduced.

        The claim still requires the six seam literals; it just stops naming the only command that
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

        `problems: []` is DECLARED by a named state check. A claim requiring it while naming a
        different check could never be established, and the loader now says so.
        """
        def edit(raw):
            claim = _claim(raw, "cross_tenant")
            claim["checks"] = [PROBE_CHECK]

        with pytest.raises(ValueError, match="could never be established"):
            _mutate(edit)

    def test_an_invented_risk_category_is_refused_at_load_time(self):
        """`P6-D46`'s shape, from the permanent-scenario side."""
        def edit(raw):
            _claim(raw, "safety_invariant")["risk_category"] = "closed-without-a-decision"

        with pytest.raises(ValueError, match="unknown risk_category"):
            _mutate(edit)

    def test_declaring_a_foreign_family_turns_the_undeclared_assertion_red(self):
        """`approval_required` is a canonical category, so it loads — and declaring it here would
        wave through a risk about M4's behaviour on M9's permanent scenario."""
        def edit(raw):
            _claim(raw, "authorization")["risk_category"] = "approval_required"

        mutant = _mutate(edit)
        with pytest.raises(AssertionError, match="another unit's"):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem(
            ).test_the_effect_and_approval_families_are_deliberately_left_undeclared(mutant)

    def test_deleting_a_forbidden_marker_turns_the_failure_assertion_red(self):
        """A mutant with no marker is a mutant nothing observes."""
        mutant = _mutate(
            lambda raw: raw["forbidden"].remove("### EXCEPTION CLOSED WITHOUT A DECISION ###")
        )
        with pytest.raises(AssertionError, match="EXCEPTION CLOSED WITHOUT A DECISION"):
            TestTheM9BaseScenario().test_it_refuses_the_failures_m9_exists_to_prevent(mutant)

    # ---- the vocabulary and the axis ----------------------------------------------------------

    def test_dropping_a_canonical_case_turns_the_coverage_assertion_red(self):
        """Remove `inactivity-never-closes-an-exception` and the family that decides whether a queue
        that looked untidy quietly closed an obligation stops being verifiable."""
        mutant = _mutate(
            lambda raw: _named(raw, "commands", "the M9 probe can exercise every canonical risk family")
            ["expect_contains"].remove("inactivity-never-closes-an-exception")
        )
        cases = [c for c in mutant.commands if c.run == f"{PROBE} --list-cases"][0].expect_contains
        with pytest.raises(AssertionError, match="risk families the scenario never asserts exist"):
            TestTheM9BaseScenario().test_it_asserts_a_risk_family_for_every_canonical_obligation(
                list(cases)
            )

    def test_dropping_the_actor_axis_turns_the_who_may_act_assertion_red(self):
        """Without the axis, "a model may never resolve" is a fixed point the generator cannot vary
        — and `GR-7` is the guard that decides whether a machine can clear its own alarm."""
        mutant = _mutate(
            lambda raw: _named(
                raw, "commands",
                "the M9 probe exposes a bounded, closed dimension vocabulary to vary cases with",
            )["expect_contains"].remove("--actor")
        )
        dims = [c for c in mutant.commands if c.run == f"{PROBE} --list-dimensions"][0].expect_contains
        cases = [c for c in mutant.commands if c.run == f"{PROBE} --list-cases"][0].expect_contains
        with pytest.raises(AssertionError, match="--actor"):
            TestTheM9BaseScenario().test_the_actor_axis_exists_because_who_may_act_is_the_whole_unit(
                list(dims), list(cases)
            )

    def test_dropping_the_decision_ref_axis_turns_the_closure_axis_assertion_red(self):
        """`--decision-ref absent` is the flag that asks whether closure by silence is possible at
        all. Remove it and `F-30` becomes unreachable from a generated case."""
        mutant = _mutate(
            lambda raw: _named(
                raw, "commands",
                "the M9 probe exposes a bounded, closed dimension vocabulary to vary cases with",
            )["expect_contains"].remove("--decision-ref")
        )
        dims = [c for c in mutant.commands if c.run == f"{PROBE} --list-dimensions"][0].expect_contains
        cases = [c for c in mutant.commands if c.run == f"{PROBE} --list-cases"][0].expect_contains
        with pytest.raises(AssertionError, match="--decision-ref"):
            TestTheM9BaseScenario(
            ).test_the_decision_ref_axis_exists_because_closure_is_the_load_bearing_act(
                list(dims), list(cases)
            )

    def test_dropping_a_closure_by_silence_shape_turns_that_assertion_red(self):
        """Five mechanisms, one failure. Losing any one of them leaves a way for an obligation to
        stop existing that nothing here would notice."""
        mutant = _mutate(
            lambda raw: _named(
                raw, "commands",
                "the M9 probe exposes a bounded, closed dimension vocabulary to vary cases with",
            )["expect_contains"].remove("sweep-close")
        )
        dims = [c for c in mutant.commands if c.run == f"{PROBE} --list-dimensions"][0].expect_contains
        cases = [c for c in mutant.commands if c.run == f"{PROBE} --list-cases"][0].expect_contains
        with pytest.raises(AssertionError, match="sweep-close"):
            TestTheM9BaseScenario(
            ).test_the_closure_by_silence_family_is_reachable_in_every_shape_it_arrives_in(
                list(dims), list(cases)
            )

    def test_refusing_an_illegal_fault_as_unknown_turns_that_distinction_red(self):
        """M9 owns its own illegal set, so `autoclose` must be refused by the MACHINE under `GR-1`,
        not by the argument parser. A probe that made it unreachable would have proved only that its
        own vocabulary is closed."""
        def edit(raw):
            reopen = _named(
                raw, "commands",
                "a reopen fault does not exist, because a recurrence is a new Exception",
            )
            reopen["run"] = reopen["run"].replace("reopen-exception", "autoclose")

        mutant = _mutate(edit)
        dims = [c for c in mutant.commands if c.run == f"{PROBE} --list-dimensions"][0].expect_contains
        with pytest.raises(AssertionError, match="refused as an UNKNOWN fault"):
            TestTheM9BaseScenario(
            ).test_the_illegal_faults_are_in_the_vocabulary_rather_than_refused_as_unknown(
                mutant, list(dims)
            )

    def test_dropping_a_deliverable_turns_the_fixture_assertion_red(self):
        """Without the fixture a run against a repository where M9 does not exist could report a
        verified M9."""
        mutant = _mutate(lambda raw: raw["fixtures"].remove("src/freight_recon/exception.py"))
        with pytest.raises(AssertionError, match="is not required to exist"):
            TestTheM9BaseScenario().test_it_requires_the_canonical_deliverables_to_exist(mutant)

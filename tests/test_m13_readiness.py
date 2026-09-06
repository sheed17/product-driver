"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M13?

M13 is the Brake: admission control, and the LAST P6 machine.

The unit's whole character is eight sentences, and every check below traces back to one of them:

    a brake refuses to mint and refuses to claim; it never kills a worker
    the brake stops the NEXT effect, not the LAST
    automation may engage and widen; automation may never narrow or release
    a detector may never clear its own alarm, and a model is not a Sev-0 detector
    a brake never expires, because a clock cannot know whether the fire is out
    "cannot read the brake" never means "the brake is off"
    release requires positive evidence, and requiring ceremony to become safer is a design error
    there is exactly one brake authority

### M13 DIFFERS FROM EVERY P6 MACHINE BEFORE IT IN ONE WAY THAT CHANGES WHAT "MEASURING IT" MEANS,
AND IT IS NOT THE ONE M12 HAD.

M1-M12 each started from nothing: the table did not exist, the module did not exist, and "is it
built?" and "is it correct?" were nearly the same question. **M13 starts from a real, landed, correct
substrate.** P3 shipped `brake.py` with a single `BrakeStore`, the `brakes` and `platform_brake`
tables with their two-state CHECK and their `id = 1` cardinality, checkpoint step 7, and a claim CAS
that already revalidates a composite brake-version token. Nine of the twenty-two persisted-state
oracles in the shipped scenario are **ALREADY GREEN on Neyma `ded6a841`**, and that is not a defect in
them.

Three consequences run through this whole file.

1.  ### **"EVERYTHING MUST BE RED BEFORE THE UNIT LANDS" IS A DISHONEST READINESS REQUIREMENT HERE,
    AND THIS FILE REFUSES TO STATE IT.** An already-green P3 guarantee is not a false green. What the
    scenario owes instead is that the **M13 DELTA** is visible: the thirteen oracles that are red today
    are the ones the unit must close, and section 3 names them.
2.  ### **THE MOST LIKELY WRONG BUILD IS A SECOND BRAKE AUTHORITY.** Every other P6 machine could be
    built by writing a new module; M13 cannot, because writing a new module beside `brake.py` leaves
    two answers to "is Neyma stopped?" — which is the same defect as none. That is why section 5 is
    the longest one here.
3.  ### **THE UNIT IS THE LAST MACHINE, WHICH IS EXACTLY WHY IT MUST NOT BECOME THE PHASE.** Landing
    M13 does not complete P6, score a criterion or unblock P7. Section 9 measures that the task and
    the scope machinery both refuse it.

Fourteen questions, each answered mechanically rather than by reading a document and agreeing with it:

1.  does the M13 base scenario parse, does it hold the pieces the generator needs (deterministic
    operation, a closed mutation axis with the two axes this unit turns on, persisted-state oracles,
    regression anchors), and do the scenario and the task state the SAME contract;
2.  does every declared risk name a command that could actually emit the observation it requires;
3.  does the scenario measure the DATABASE, the CLAIM CAS, the EVENT REGISTRY and the AST rather than
    the probe's narration — above all the two-state CHECK, the `id = 1` platform cardinality, the
    single-authority AST scan, the composite token in the CAS's own WHERE clause, and the absence of
    any TTL — and does it ATTEMPT the forbidden writes against a live database with positive controls;
4.  does the task preserve the nine recorded authority questions rather than resolving them;
5.  ### does the task get the ONE THING RIGHT that no earlier unit had to: that `brake.py` is landed
    authority to be COMPLETED, not a gap to be filled beside;
6.  is the M13 command vocabulary safe, and actually visible to the generator;
7.  can dynamic generation close an M13 coverage gap WITHOUT inventing a command;
8.  does every named M13 oracle carry a STABLE APPROVED-COMMAND IDENTITY, so a body repair can be
    rebound on resume rather than deleting the obligation — the `c6331dc`/`cb5ecf9` invariant;
9.  is M13 scoped as `P6/M13` rather than as P6 phase completion, and can accepting it score a P6
    acceptance criterion or unlock P7 (it cannot);
10. is an integrated independent review OWED, at the tier this unit actually is;
11. do grounded reviewer findings return to the SAME builder;
12. does the task refuse P7, P8's runtime, every production surface, and the adoption of P6 residuals
    that are merely nearby;
13. ### does the RENDER BOUND still hold, and does omission from the enumerated vocabulary still
    leave an unenumerated M13 case PERMITTED — the M12 invariant, restated for a larger case set;
14. and, for the load-bearing half of all of the above: **does this file actually fail when the
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

from neyma_product_driver.command_guard import classify_command
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.models import RunStatus, redact
from neyma_product_driver.review_cycle import resolve_review_requirement
from neyma_product_driver.scenario_generator import MAX_RENDERED_COMMANDS
from neyma_product_driver.scenario_plan import (
    GeneratedScenario,
    GeneratedStateCheck,
    IdentifiedRisk,
    Priority,
    RiskCategory,
    ScenarioProvenance,
    rebind_to_approved,
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

from scenario_fixtures import FakeFounder, ScriptedReasoner
from test_integrated_review import FakeBuilder, FakeReviewer, drive, refusing, supported
from test_scoped_completion import PhaseRepo

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DRIVER_ROOT / "scenarios"
M13_PATH = SCENARIOS_DIR / "p6_m13_brake.yaml"
M13_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m13.md"
M13_TASK = M13_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match against this: the task is
#: a wrapped markdown document, and a phrase that happens to straddle a line break is not a phrase
#: the task failed to state.
M13_TASK_FLAT = " ".join(M13_TASK.split())
#: The same text with markdown furniture removed — blockquote markers, `###` emphasis runs and bold
#: markers — because the task states its hardest rules inside emphasised blockquotes.
M13_TASK_PROSE = " ".join(
    re.sub(r"(^|\n)\s*>\s?", " ", M13_TASK).replace("###", " ").replace("**", "").split()
)

PROBE = ".venv/bin/python scripts/probe_phase6_brake.py"
#: The `name:` the base scenario gives the bare probe run — the deterministic M13 operation, and the
#: only check in the file that drives the machine and narrates what it saw.
PROBE_CHECK = "drive the Brake machine through a brokerage incident, and attack it"

#: The canonical M13 deliverables. A different name is a scenario failure, not a style preference.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/brake_lifecycle.py",
    "src/freight_recon/migrations/phase6_brakes.py",
    "eval/tests/test_phase6_brake.py",
    "scripts/probe_phase6_brake.py",
    "scripts/mutate_phase6_brake.py",
)

#: ### THE FILE THAT MUST **NOT** BE A DELIVERABLE. `brake.py` exists — P3 landed it — so declaring
#: it as a fixture would exempt it from the "every named path already exists" rule and quietly
#: license a session to write a fresh one. It is a REGRESSION ANCHOR, not an output.
LANDED_BRAKE = "src/freight_recon/brake.py"

#: The two canonical states, and the terminal one. `state-machines/registry.md` §4 / M13.
CANONICAL_STATES: tuple[str, ...] = ("ACTIVE", "RELEASED")
#: The states a plausible author invents while being careful. `PENDING_RELEASE` reads like prudence
#: and IS the forbidden release-approval ceremony; `ENGAGING` arrives with anyone who thinks of
#: engagement as a process; `EXPIRED` arrives with anyone who thinks in TTLs.
FORBIDDEN_STATES: tuple[str, ...] = (
    "PENDING", "PENDING_RELEASE", "ENGAGING", "DISENGAGED", "EXPIRED", "SUSPENDED",
    "PARTIAL", "FAILED", "CANCELLED", "PAUSED", "DISABLED", "ENABLED", "RECOVERING",
)
#: The five canonical transition ids. `13-brake.machine.md` §14.
CANONICAL_TRANSITIONS: tuple[str, ...] = ("BR-1", "BR-2", "BR-3", "BR-4", "BR-5")
#: The four registered F13 contracts, and nothing else.
F13_CONTRACTS: tuple[str, ...] = (
    "BrakeEngaged", "BrakeWidened", "BrakeNarrowed", "BrakeReleased",
)
#: The three contracts a WRONG STATE SET would need. Their absence from the registry is the state
#: set restated as a contract, which is why they are named here rather than left implicit.
FORBIDDEN_CONTRACTS: tuple[str, ...] = (
    "BrakeExpired", "BrakeAutoReleased", "BrakePendingRelease",
)
#: The five canonical scope dimensions. `entities/16-brake.md` point 12; ADR-011 §9.
SCOPE_DIMENSIONS: tuple[str, ...] = (
    "GLOBAL", "TENANT", "INTEGRATION", "ACTION_CLASS", "COUNTERPARTY",
)
#: The nine authority questions this bootstrap recorded. A task that has quietly resolved one of
#: them has replaced a finding with a preference.
AUTHORITY_QUESTIONS: tuple[str, ...] = tuple(f"M13-AQ-{n}" for n in range(1, 10))

#: The unit's own sentences. Every one must be BOTH required by the scenario AND asked for by the
#: task, or the scenario is asking for output nobody was told to produce.
SAFETY_LITERALS: tuple[str, ...] = (
    "A BRAKE REFUSES TO MINT AND REFUSES TO CLAIM",
    "A BRAKE NEVER KILLS A WORKER",
    "THE BRAKE STOPS THE NEXT EFFECT, NOT THE LAST",
    "KILLING A WORKER WOULD MANUFACTURE AN UNKNOWN OUTCOME",
    "ENGAGING DURING AN ADAPTER CALL CREATES NO UNKNOWN OUTCOME",
    "A CLAIMED GRANT RUNS TO VERIFICATION",
    "ANY AUTHENTICATED HUMAN ENGAGES INSTANTLY, WITH NO CEREMONY",
    "THE BRAKE ENGAGES WITH THE POLICY ENGINE AND THE TMS DOWN",
    "A SAFETY CONTROL THAT REQUIRES A HEALTHY SYSTEM IS NOT A SAFETY CONTROL",
    "WIDENING A BRAKE NARROWS AUTHORITY",
    "NARROWING A BRAKE BROADENS AUTHORITY",
    "AUTOMATION MAY ENGAGE AND WIDEN",
    "AUTOMATION MAY NEVER NARROW OR RELEASE",
    "A DETECTOR MAY NEVER CLEAR ITS OWN ALARM",
    "A MODEL IS NOT A SEV-0 DETECTOR",
    "RELEASE REQUIRES POSITIVE EVIDENCE, NOT A DECISION REF ALONE",
    "A PAGE LOADING IS NOT A POSITIVE HEALTH PROOF",
    "UNRESOLVED UNKNOWN OUTCOMES DO NOT BLOCK RELEASE, AND STAY FROZEN AND OWNED",
    "REQUIRING CEREMONY TO BECOME SAFER IS A DESIGN ERROR",
    "AN UNAUTHORIZED RELEASE REACHES THE REGISTERED F14 EVENT",
    "A BRAKE NEVER EXPIRES",
    "NO TIMER MOVES A BRAKE",
    "THE CLOCK MAY NEVER MAKE A BRAKE LESS RESTRICTIVE",
    "THE PLATFORM BRAKE IS ONE TENANT-EXEMPT ROW",
    "GLOBAL IS NOT A FAKE TENANT",
    "AN ACTIVE BRAKE IN EITHER DIMENSION DENIES",
    "CANNOT READ THE BRAKE NEVER MEANS OFF",
    "THERE IS NO ALLOW-ON-BRAKE-ERROR DEFAULT",
    "A BRAKE BETWEEN MINT AND CLAIM MAKES THE CAS MATCH ZERO ROWS",
    "NEVER BOTH, NEVER NEITHER",
    "THE RACE IS DECIDED BY THE DATABASE, NOT BY A CHECK",
    "RELEASE DOES NOT RESURRECT A STALE WITNESS",
    "EVERY QUEUED ACTION PASSES A NEW FULL CHECKPOINT AFTER RELEASE",
    "A PENDING APPROVAL STAYS RECORDED AND CANNOT EXECUTE",
    "COMPENSATION IS BLOCKED UNDER AN ACTIVE BRAKE",
    "OBSERVATION AND RECONCILIATION CONTINUE",
    "THE BRAKE STOPS ACTING, NOT KNOWING",
    "A FLAPPING DETECTOR IS ONE ACTIVE BRAKE AND NO WINDOW",
    "FOUR F13 CONTRACTS AND NO FIFTH",
    "REPLAY RECONSTRUCTS HISTORY AND CREATES NO AUTHORITY",
    "A HIDDEN BRAKE IS A SILENT DEGRADATION",
    "AN ACTIVE BRAKE IS REPORTED UNPROMPTED",
    "THERE IS EXACTLY ONE BRAKE AUTHORITY",
    "M13 BUILDS NO SECOND BRAKE STORE",
    "M13 MINTS NO GATE DECISION",
    "THE CHECKPOINT IS STILL THE ONLY GATE MINTER",
)
DARK_POSTURE_LITERALS: tuple[str, ...] = (
    "M13 SHIPS DARK WITH ZERO PRODUCTION IMPORTERS",
    "NO BRAKE CONSOLE, DASHBOARD OR CHANNEL COMMAND EXISTS",
    "NOTHING GRADUATES",
    "LANDING M13 IS NOT P6 ACCEPTANCE",
    "THE M1 WORK ITEM MACHINE IS UNCHANGED",
    "THE M2 PIPELINE MACHINE IS UNCHANGED",
    "THE M3 EFFECT AUTHORITY IS UNCHANGED",
    "THE M4 APPROVAL MACHINE IS UNCHANGED",
    "THE M7 CONFLICT MACHINE IS UNCHANGED",
    "THE M9 EXCEPTION MACHINE IS UNCHANGED",
    "THE M11 POLICY MACHINE IS UNCHANGED",
    "THE M12 RULE MACHINE IS UNCHANGED",
)

#: The P6 units in build order, by the scenario name each one's bootstrap targets. The local config
#: carries ONE of these at a time, and it only ever moves FORWARD.
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
    "p6_m12_rule",
    "p6_m13_brake",
)

# The persisted-state oracle names, stated once. A rename is a scenario change and this file should
# fail rather than silently stop measuring it.
TABLES = ("a freshly created canonical database carries the one brake layer, and exactly two "
          "brake tables")
STATE_VOCAB = ("the two canonical brake states are a database constraint on both brake tables, "
               "and there is no third")
NO_TTL = "no TTL or expiry exists — not as a column, not as an identifier, not as a live code path"
LIVE_WRITES = ("the live database refuses a third state, a releaser-less release, a detector "
               "releaser and a cross-tenant actor")
PLATFORM_ROW = ("exactly one platform brake row is structurally enforced, and GLOBAL is not a "
                "fake tenant")
UNIQUENESS = "brake uniqueness is one ACTIVE per tenant and scope, tenant-first, and never global"
RETENTION = "a brake row is never deleted, and the incident record is permanent"
TOKEN = ("the brake version token binds both owners and is monotonic, and an unreadable store "
         "refuses rather than reading as off")
CAS = "the claim CAS revalidates the composite brake token inside its own WHERE clause"
SINGLE_AUTHORITY = ("there is exactly one brake authority, and any M13 module composes with it "
                    "rather than replacing it")
REGISTRY = ("the F13 family is exactly four registered contracts, strictly ordered, and M13 mints "
            "no fifth")
EVENTS = ("M13 emits only registered event names, and the landed consumers of BrakeEngaged still "
          "consume it")
DARK = "M13 ships dark: the brake is reachable only from the kernel and its landed consumers"
PARITY = "an upgraded database and a fresh database carry the identical brake layer"
TENANCY = ("the brake layer joins the tenant-first partition, and platform_brake remains the one "
           "defended exemption")
MACHINE = ("the M13 machine declares two states, five transitions, and BR-5 as a non-producing "
           "illegal refusal")
RELEASE_EVIDENCE = ("release refuses on each missing evidence condition alone, and succeeds when "
                    "all of them hold")
R17 = ("an ACTIVE brake assembles the full R17 operator report, and nothing about it needs a "
       "channel")
FLAPPING = "a flapping detector yields one ACTIVE brake row and a rising signal count"
SCOPE_GRAMMAR = ("the scope grammar is closed and declared, and an unparseable scope refuses "
                 "rather than scoping to nothing")
RATCHET = "the safe-direction ratchet is a declared table over three distinct actor classes"
TESTNAMES = "the M13 acceptance battery carries the canonically named adversarial tests"

#: ### THE TWELVE ORACLES THAT ARE RED ON NEYMA `ded6a841` — THE M13 DELTA. Measured, not assumed:
#: this bootstrap executed all twenty-two against the real Neyma tree and against a controlled stub
#: that supplied only the missing M13-facing layer over the ONE landed authority. The stub turned
#: all twenty-two green, so no oracle here is vacuous; the real tree turned these thirteen red, so
#: each names a guarantee the unit genuinely owes.
M13_DELTA_ORACLES: tuple[str, ...] = (
    NO_TTL, LIVE_WRITES, RETENTION, TOKEN, EVENTS, PARITY, MACHINE, RELEASE_EVIDENCE, R17,
    FLAPPING, SCOPE_GRAMMAR, RATCHET, TESTNAMES,
)
#: The ten that P3 already earned. ### AN ALREADY-GREEN P3 GUARANTEE IS NOT A FALSE GREEN, and a
#: readiness requirement that demanded they be red would be asking the bootstrap to lie.
P3_ALREADY_GREEN_ORACLES: tuple[str, ...] = (
    TABLES, STATE_VOCAB, PLATFORM_ROW, UNIQUENESS, CAS, SINGLE_AUTHORITY, REGISTRY,
    DARK, TENANCY,
)


def _local_config() -> dict:
    """The local driver config, if this checkout has one.

    Read from the file rather than through `load_config`, because this must work on a checkout that
    has no `driver.config.yaml` at all — it is git-ignored — and "the file is absent" is not the
    same finding as "the file is wrong".
    """
    local = DRIVER_ROOT / "driver.config.yaml"
    if not local.exists():
        return {}
    return yaml.safe_load(local.read_text(encoding="utf-8")) or {}


def _local_vocabulary() -> list[str]:
    return list((_local_config().get("scenario_generation") or {}).get("approved_commands") or [])


@pytest.fixture(scope="module")
def m13():
    return load_scenario(M13_PATH)


@pytest.fixture(scope="module")
def cases(m13) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m13.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m13) -> list[str]:
    listing = [c for c in m13.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def state_checks(m13) -> dict[str, list[str]]:
    return {check.name: list(check.contains) for check in m13.expect_state}


# --------------------------------------------------------------------------
# 1. The M13 base scenario holds what the generator and the gate need
# --------------------------------------------------------------------------


class TestTheM13BaseScenario:
    def test_it_parses_and_is_the_p6_backend_unit(self, m13):
        assert m13.name == "p6_m13_brake"
        assert m13.phase == "P6"
        assert m13.mode == "backend"

    def test_it_declares_the_m13_deliverables_as_fixtures(self, m13):
        """A bootstrap is written BEFORE the unit it measures, so the scenario's own outputs are
        exempted from the "every named path already exists" rule. Everything else it names is a
        landed regression anchor and must exist today."""
        for path in DELIVERABLES:
            assert path in m13.fixtures, f"{path} is not declared as a fixture"

    def test_the_landed_brake_module_is_not_declared_as_a_deliverable(self, m13):
        """### THE SINGLE MOST IMPORTANT LINE IN THIS SECTION.

        `src/freight_recon/brake.py` EXISTS — P3 landed it. Declaring it as a fixture would exempt
        it from the path-existence rule and quietly license a session to treat it as a file it is
        about to create. It is a regression anchor. The scenario says so in a comment; this says so
        as an assertion.
        """
        assert LANDED_BRAKE not in m13.fixtures, (
            "brake.py is declared as an M13 deliverable. It is P3's landed kernel brake and it "
            "already exists; declaring it here is the first step of building a second one."
        )

    def test_the_deterministic_operation_is_a_single_named_probe_run(self, m13):
        runs = [c for c in m13.commands if c.run == PROBE]
        assert len(runs) == 1, "the bare probe must be exactly one named check"
        assert runs[0].name == PROBE_CHECK

    def test_the_probe_declares_a_closed_case_vocabulary(self, cases):
        assert len(cases) >= 150, (
            f"only {len(cases)} cases; M13 has five transitions, five in-flight positions, two "
            "admission dimensions and seven actor classes"
        )
        bad = [c for c in cases if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", c)]
        assert not bad, f"case names must be kebab-case: {bad}"
        duplicates = sorted({c for c in cases if cases.count(c) > 1})
        assert not duplicates, f"duplicate case names: {duplicates}"

    def test_the_probe_declares_this_units_two_own_axes(self, dimensions):
        """### `--position` AND `--owner` ARE M13's OWN AXES, and neither is decoration.

        Without `--position` a battery only ever exercises the half of the brake that STOPS things,
        and the half that must NOT stop things is where the unknown outcome is made. Without
        `--owner` a composite version check is indistinguishable from a single one.
        """
        for position in ("position:1", "position:2", "position:3", "position:4", "position:5"):
            assert any(d.startswith(position) for d in dimensions), (
                f"the five-position in-flight boundary is not an axis: {position} is missing"
            )
        for owner in ("owner:platform", "owner:tenant", "owner:both"):
            assert owner in dimensions, f"the composed admission dimensions are not an axis: {owner}"

    def test_the_probe_declares_the_actor_classes_without_collapsing_them(self, dimensions):
        """`system`, `detector` and `model` are three things. Collapsing them is how a model
        acquires the brake, and it looks like tidy refactoring while it happens."""
        for actor in ("actor:human", "actor:detector", "actor:model", "actor:automation",
                      "actor:timer", "actor:retry", "actor:counterparty"):
            assert actor in dimensions, f"{actor} is not an axis value"

    def test_the_probe_declares_every_br_transition_as_an_axis(self, dimensions):
        for transition in CANONICAL_TRANSITIONS:
            assert f"transition:{transition}" in dimensions, f"{transition} is not an axis value"

    def test_the_fault_set_is_closed_and_every_member_is_a_refusal(self, dimensions):
        faults = [d for d in dimensions if d.startswith("inject:")]
        assert len(faults) >= 5, f"only {len(faults)} injectable faults"
        for needed in ("inject:brake-store-unreadable", "inject:platform-row-absent",
                       "inject:brake-between-mint-and-claim"):
            assert needed in faults, f"{needed} is not injectable"

    def test_the_scenario_runs_the_landed_p3_batteries_as_regression_anchors(self, m13):
        """### M13 EDITS THE MODULE IT MEASURES, WHICH NO EARLIER P6 UNIT DID. P3's brake,
        checkpoint-matrix, claim-CAS and step-order batteries are what turn red first if `brake.py`
        is completed carelessly."""
        runs = " ".join(c.run for c in m13.commands)
        for battery in ("test_phase3_brake.py", "test_phase3_claim_cas.py",
                        "test_phase3_checkpoint_matrix.py", "test_phase3_step_order.py"):
            assert battery in runs, f"{battery} is not run as a regression anchor"

    def test_the_scenario_runs_the_neighbours_that_consume_brake_state(self, m13):
        runs = " ".join(c.run for c in m13.commands)
        for battery in ("test_phase6_approval.py", "test_phase6_compensation.py",
                        "test_phase6_rule.py"):
            assert battery in runs, f"{battery} is not run as a regression anchor"

    def test_every_battery_is_invoked_through_python_dash_m(self, m13):
        """A bare `pytest` console script resolves to whatever is first on PATH. `python -m pytest`
        resolves to the interpreter the scenario named."""
        for spec in m13.commands:
            if "pytest" in spec.run:
                assert ".venv/bin/python -m pytest" in spec.run, (
                    f"{spec.name!r} invokes pytest without the interpreter: {spec.run}"
                )

    def test_a_battery_that_ran_nothing_cannot_read_as_a_battery_that_passed(self, m13):
        for marker in ("no tests ran", "ERROR: file or directory not found"):
            assert marker in m13.forbidden, f"{marker!r} is not globally forbidden"

    def test_the_shared_probe_failure_vocabulary_is_forbidden(self, m13):
        for marker in ("### MISS ###", "### NOT REFUSED", "### WRONGLY REFUSED",
                       "### WRONG REFUSAL"):
            assert marker in m13.forbidden, f"{marker!r} is not globally forbidden"

    def test_the_scenario_and_the_task_state_the_same_safety_contract(self, m13):
        """A sentence the scenario requires and the task never asks for is output nobody was told
        to produce; a sentence the task demands and no oracle reads is a promise nobody checks."""
        for literal in SAFETY_LITERALS:
            assert literal in m13.expect_visible, f"the scenario never requires: {literal}"
            assert literal in M13_TASK, f"the task never asks for: {literal}"

    def test_the_scenario_and_the_task_state_the_same_dark_posture(self, m13):
        for literal in DARK_POSTURE_LITERALS:
            assert literal in m13.expect_visible, f"the scenario never requires: {literal}"
            assert literal in M13_TASK, f"the task never asks for: {literal}"

    def test_every_expect_visible_literal_is_required_by_some_check(self, m13):
        """An expected observation nothing produces is a claim about output no command asks for."""
        produced: set[str] = set()
        for spec in m13.commands:
            produced |= set(spec.expect_contains)
        for check in m13.expect_state:
            produced |= set(check.contains)
        orphans = [v for v in m13.expect_visible if v not in produced]
        assert not orphans, f"{len(orphans)} expect_visible literal(s) no check requires: {orphans}"

    def test_the_task_spells_every_case_the_scenario_asks_for(self, cases):
        """### THE SPELLING IS THE CONTRACT. A case the scenario asks for and the probe does not
        implement is a run that fails as a product defect for a naming reason."""
        missing = [c for c in cases if c not in M13_TASK]
        assert not missing, f"{len(missing)} case(s) the scenario needs are not in the task: {missing[:6]}"

    def test_the_task_spells_every_axis_the_scenario_asks_for(self, dimensions):
        missing = [d for d in dimensions if d not in M13_TASK]
        assert not missing, f"axis values the task never spells: {missing}"


# --------------------------------------------------------------------------
# 2. Every declared risk names a command that can prove it
# --------------------------------------------------------------------------


class TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem:
    def test_the_scenario_declares_risk_coverage_at_all(self, m13):
        assert m13.verifies, "the scenario declares no risk coverage"
        assert len(m13.verifies) >= 18, (
            f"only {len(m13.verifies)} risk claims; M13's failure surface is wider than that"
        )

    def test_the_unit_defining_risks_are_all_claimed(self, m13):
        claimed = {c.risk_category for c in m13.verifies}
        for category in ("happy_path", "safety_invariant", "authorization",
                         "unexpected_state_transition", "timeout_before_effect", "concurrency",
                         "dependency_failure", "cross_tenant", "boundary", "stale_state",
                         "missing_data", "idempotency", "restart_recovery", "approval_required",
                         "ui_backend_disagreement", "regression", "partial_failure"):
            assert category in claimed, f"no claim covers {category}"

    def test_every_named_check_is_a_check_the_scenario_runs(self, m13):
        """The loader enforces this, and this states it as a claim of its own so a loosening of the
        loader is visible here rather than silently."""
        known = m13.check_names()
        for claim in m13.verifies:
            for name in claim.checks:
                assert name in known, f"{claim.risk_category} names a check that does not run: {name}"

    def test_every_claim_carries_both_a_check_and_an_observation(self, m13):
        for claim in m13.verifies:
            assert claim.checks, f"{claim.risk_category} names no check"
            assert claim.observations, f"{claim.risk_category} names no observation"

    def test_the_happy_path_claim_names_positive_controls(self, m13):
        """### AN ORACLE BATTERY PROVING NOTHING CAN EVER ENGAGE A BRAKE WOULD PASS A PRODUCT WITH
        NO BRAKE AT ALL, and one proving nothing can ever be released would pass a product that can
        never be restarted. The happy path is what makes every refusal meaningful."""
        [claim] = [c for c in m13.verifies if c.risk_category == "happy_path"]
        controls = [o for o in claim.observations if o.startswith("positive control")]
        assert len(controls) >= 3, f"the happy path names {len(controls)} positive controls"

    def test_the_never_kills_a_worker_claim_names_the_position_battery(self, m13):
        """### THE SENTENCE THE ADR EXISTS FOR. If this claim ever stops naming the five-position
        oracle, the unit's whole reason for existing has become unmeasured."""
        claims = [c for c in m13.verifies if c.risk_category == "safety_invariant"]
        assert claims, "no risk claim covers the in-flight boundary"
        joined_checks = " ".join(x for c in claims for x in c.checks)
        joined_obs = " ".join(o for c in claims for o in c.observations)
        assert "five boundary positions" in joined_checks
        assert "A BRAKE NEVER KILLS A WORKER" in joined_obs
        assert "THE BRAKE STOPS THE NEXT EFFECT, NOT THE LAST" in joined_obs
        assert "KILLING A WORKER WOULD MANUFACTURE AN UNKNOWN OUTCOME" in joined_obs

    def test_the_single_authority_claim_names_the_ast_oracle(self, m13):
        """### THE DEFECT THIS UNIT IS MOST LIKELY TO SHIP has to be someone's declared risk, or it
        is nobody's."""
        claims = [c for c in m13.verifies if SINGLE_AUTHORITY in c.checks]
        assert claims, "no risk claim names the single-brake-authority oracle"
        joined = " ".join(o for c in claims for o in c.observations)
        assert "brake authority count: 1" in joined
        assert "brake-writing module count: 1" in joined
        assert "THERE IS EXACTLY ONE BRAKE AUTHORITY" in joined

    def test_the_release_evidence_claim_refuses_the_two_field_shortcut(self, m13):
        """`if human and decision_ref: release()` passes every authorization test in the file and
        is still wrong. The evidence claim is what makes that visible."""
        claims = [c for c in m13.verifies if RELEASE_EVIDENCE in c.checks]
        assert claims, "no risk claim names the release-evidence oracle"
        joined = " ".join(o for c in claims for o in c.observations)
        assert "RELEASE REQUIRES POSITIVE EVIDENCE, NOT A DECISION REF ALONE" in joined
        assert "A PAGE LOADING IS NOT A POSITIVE HEALTH PROOF" in joined
        assert "release evidence is not satisfiable by a decision_ref alone: True" in joined

    def test_the_interaction_claim_states_the_asymmetry_in_both_directions(self, m13):
        """A battery that only proves things are BLOCKED would pass a product that had simply
        stopped. Compensation blocked AND observation continuing is the actual claim."""
        claims = [c for c in m13.verifies if c.risk_category == "approval_required"]
        assert claims, "no risk claim covers the M4 / compensation / observation interaction"
        joined = " ".join(o for c in claims for o in c.observations)
        assert "COMPENSATION IS BLOCKED UNDER AN ACTIVE BRAKE" in joined
        assert "OBSERVATION AND RECONCILIATION CONTINUE" in joined


# --------------------------------------------------------------------------
# 3. Persisted state, the claim CAS, the event registry and the AST are the oracles
# --------------------------------------------------------------------------


class TestPersistedStateIsTheOracle:
    def test_the_scenario_carries_a_full_persisted_state_battery(self, m13):
        assert len(m13.expect_state) >= 20, (
            f"only {len(m13.expect_state)} persisted-state oracles; M13 hardens two tables, "
            "completes an event family, and is read inside the checkpoint and the claim CAS"
        )

    def test_every_named_persisted_state_oracle_is_present(self, state_checks):
        for name in (*P3_ALREADY_GREEN_ORACLES, *M13_DELTA_ORACLES):
            assert name in state_checks, f"the persisted-state oracle {name!r} is gone"

    def test_the_two_state_vocabulary_is_asserted_as_a_database_check(self, state_checks):
        contains = state_checks[STATE_VOCAB]
        assert "the state vocabulary is a CHECK: True" in contains
        assert "canonical two: ['ACTIVE', 'RELEASED']" in contains
        assert "state count: 2" in contains
        assert "forbidden states present: []" in contains

    def test_the_forbidden_third_states_are_attempted_against_a_live_database(self, state_checks):
        """### READING DDL AND BELIEVING IT IS HOW A CHECK THAT WAS NEVER COMPILED READS AS
        ENFORCEMENT. Each of these is an INSERT the database actually rejected."""
        contains = " ".join(state_checks[LIVE_WRITES])
        for state in ("PENDING_RELEASE", "ENGAGING", "EXPIRED", "SUSPENDED", "PARTIAL",
                      "DISENGAGED"):
            assert f"a {state} lifecycle state: refused" in contains or \
                   f"an {state} lifecycle state: refused" in contains, \
                   f"{state} is never attempted against the live database"

    def test_the_forbidden_writes_sit_behind_positive_controls(self, state_checks):
        """A schema that refuses EVERYTHING would pass a refusal-only battery by being uniformly
        hostile. The controls and the surviving-row counts are what stop that."""
        contains = state_checks[LIVE_WRITES]
        controls = [c for c in contains if c.startswith("positive control")]
        assert len(controls) >= 4, f"only {len(controls)} positive controls on the live-write oracle"
        assert "surviving RELEASED rows with no releaser: 0" in contains
        assert "surviving rows in a state outside the canonical two: 0" in contains

    def test_the_load_bearing_release_constraints_are_database_enforced(self, state_checks):
        """Entity point 16 makes both a CHECK and point 37 makes both structurally impossible
        states. If a RELEASED row can exist with a null `released_by`, then "a human released this"
        is a convention rather than a fact."""
        contains = state_checks[LIVE_WRITES]
        assert "a RELEASED brake with no releaser: refused" in contains
        assert "a RELEASED brake with no decision_ref: refused" in contains
        assert "a releaser who is not a recorded human: refused" in contains
        assert "a releaser from another tenant: refused" in contains

    def test_the_platform_row_cardinality_is_structural_and_tenantless(self, state_checks):
        """SD-12 with amendment A1: exactly one row, `id = 1`, and NO tenant column. GLOBAL is not
        a fake tenant, and a sentinel would reintroduce the `tenant="default"` class of defect."""
        contains = state_checks[PLATFORM_ROW]
        assert "platform rows on a fresh database: 1" in contains
        assert "platform_brake has no tenant column at all: True" in contains
        assert "a second platform row at id 2: refused" in contains
        assert "a duplicate platform row at id 1: refused" in contains
        assert "platform rows after every attempt: 1" in contains
        assert "the id CHECK is in the DDL: True" in contains
        assert "positive control, updating the one platform row in place: ACCEPTED" in contains

    def test_the_platform_exemption_is_the_declared_one_and_no_new_one_appeared(self, state_checks):
        assert "the recorded tenant-exempt table set: ['platform_brake']" in state_checks[PLATFORM_ROW]
        contains = state_checks[TENANCY]
        assert "platform_brake is a recorded exemption: True" in contains
        assert ("tenantless tables outside the recorded exemptions and the migration ledger: []"
                in contains)
        assert "brakes is tenant-owned: True" in contains

    def test_uniqueness_is_tenant_first_and_conditional_on_active(self, state_checks):
        contains = state_checks[UNIQUENESS]
        assert "an ACTIVE-only partial UNIQUE index exists: True" in contains
        assert "the active uniqueness columns are tenant and scope: True" in contains
        assert "every brake index that names columns is tenant-first: True" in contains
        assert "tenant is FIRST in the brake primary key: True" in contains

    def test_the_absence_of_a_ttl_is_measured_on_the_ast_and_not_by_grep(self, m13, state_checks):
        """### THE MEASUREMENT ITSELF IS THE SUBTLE PART, AND THIS BOOTSTRAP GOT IT WRONG BEFORE
        FIXING IT. `brake.py`'s module docstring contains the words "TTL" and "expire" — in the
        sentence explaining that neither exists. A grep over the file text reports a TTL on a tree
        that has none, and a guard that fires on the prose describing its own absence is not a
        guard."""
        contains = state_checks[NO_TTL]
        assert "TTL or expiry columns on a brake table: []" in contains
        assert "  expiry identifiers: []" in contains
        assert "  expiry literals in executable code: []" in contains
        command = next(c.command for c in m13.expect_state if c.name == NO_TTL)
        assert "ast.parse" in command, "the code half of the TTL oracle is not an AST walk"
        assert "docs.add(id(first.value))" in command, (
            "the TTL oracle does not exclude docstrings, so it will fire on the sentence that "
            "explains the TTL does not exist"
        )

    def test_the_claim_cas_oracle_selects_the_claiming_statement_and_names_the_others(
        self, m13, state_checks
    ):
        """### THE SAME-SUBSTRING TRAP, CAUGHT AND PINNED. `EXPIRED_UNCLAIMED` contains the
        substring `CLAIMED`, so a selector matching on it drags the expiry sweep — which correctly
        carries no brake token — into the `all()` and reports the CAS as unguarded. The oracle
        selects on the SET clause and names the two non-claiming updates as its control."""
        contains = state_checks[CAS]
        assert "exactly one statement claims a grant: True" in contains
        assert ("the other ledger updates, which correctly carry no brake token: "
                "['EXPIRED_UNCLAIMED', 'REVOKED']") in contains
        assert "the CAS revalidates brake_version in its WHERE clause: True" in contains
        assert "the CAS revalidates policy_version in its WHERE clause: True" in contains
        command = next(c.command for c in m13.expect_state if c.name == CAS)
        assert "SET +state" in command, (
            "the CAS oracle matches on a bare substring, which EXPIRED_UNCLAIMED also satisfies"
        )

    def test_the_race_is_asserted_to_be_closed_by_the_database(self, m13):
        """An implementation that reads the brake, decides, and THEN claims is a TOCTOU window
        wearing the shape of a check, and it passes every behavioural test on an unloaded
        machine."""
        visible = m13.expect_visible
        assert "A BRAKE BETWEEN MINT AND CLAIM MAKES THE CAS MATCH ZERO ROWS" in visible
        assert "NEVER BOTH, NEVER NEITHER" in visible
        assert "THE RACE IS DECIDED BY THE DATABASE, NOT BY A CHECK" in visible

    def test_the_interleaved_race_battery_runs_at_the_canonical_order(self, m13):
        """Entity 43(b), machine §41(b) and ADR-011 §12 all name 10,000x interleaved. A sleep-based
        timing test is not a substitute."""
        runs = [c for c in m13.commands if "interleaved-race-battery" in c.run]
        assert runs, "the canonical interleaved race battery is not run"
        assert any("--repeat 10000" in c.run for c in runs), (
            "the race battery does not run at the canonical order of magnitude"
        )
        concurrent = [c for c in m13.commands if "--concurrency" in c.run]
        assert concurrent, "no concurrent claim battery runs at all"

    def test_both_asymmetric_version_omissions_are_exercised(self, cases):
        """A tenant-only check lets a GLOBAL brake through; a global-only check lets a TENANT brake
        through. Each is a payment, and neither is implied by the other."""
        assert "a-tenant-only-version-check-lets-a-global-brake-through" in cases
        assert "a-global-only-version-check-lets-a-tenant-brake-through" in cases

    def test_the_single_authority_is_measured_by_ast_across_the_package(self, m13, state_checks):
        """### THE ORACLE THIS WHOLE UNIT MOST NEEDS. One class owns the brake lifecycle, one
        module writes brake state, and the kernel is the positive control so the scan cannot be
        green by finding nothing."""
        contains = state_checks[SINGLE_AUTHORITY]
        assert "classes that own the brake lifecycle: ['brake.py:BrakeStore']" in contains
        assert "brake authority count: 1" in contains
        assert "modules that WRITE brake state directly: ['brake.py']" in contains
        assert "brake-writing module count: 1" in contains
        assert "M13 composes with the landed store: True" in contains
        control = [c for c in contains if "checkpoint.py:GateEntry" in c]
        assert control, (
            "the gate-minter half of the scan has no positive control, so a scan that discovered "
            "nothing at all would read as green"
        )
        command = next(c.command for c in m13.expect_state if c.name == SINGLE_AUTHORITY)
        assert "ast.parse" in command, "the single-authority scan is not an AST walk"

    def test_a_second_brake_state_table_is_detected_on_the_table_set(self, state_checks):
        contains = state_checks[TABLES]
        assert "brake state tables: 2" in contains
        assert "a second brake state table appeared: []" in contains

    def test_the_f13_family_is_exactly_four_and_the_forbidden_three_are_named(self, state_checks):
        """Each of `BrakeExpired`, `BrakeAutoReleased` and `BrakePendingRelease` is precisely the
        event a WRONG STATE SET would need. Their absence is the state set restated as a
        contract."""
        contains = state_checks[REGISTRY]
        assert "F13 contract count: 4" in contains
        assert ("the F13 family: ['BrakeEngaged', 'BrakeNarrowed', 'BrakeReleased', "
                "'BrakeWidened']") in contains
        assert "forbidden brake contracts registered: []" in contains
        assert "total registered contracts: 118" in contains, (
            "the total is not pinned, so registering a fifth contract would go unnoticed"
        )

    def test_the_human_only_flags_follow_er_11_and_er_12(self, state_checks):
        """The ratchet, as the registry already records it: `BrakeNarrowed` and `BrakeReleased`
        broaden authority and are human-only; `BrakeEngaged` and `BrakeWidened` narrow it and are
        not."""
        contains = state_checks[REGISTRY]
        assert "BrakeReleased is human_only: True" in contains
        assert "BrakeNarrowed is human_only: True" in contains
        assert "BrakeEngaged is human_only: False" in contains
        assert "BrakeWidened is human_only: False" in contains

    def test_the_f14_contract_is_not_m13s_to_mint(self, state_checks):
        assert "UnauthorizedBrakeReleaseAttempted family: F14" in state_checks[REGISTRY]
        assert "M13 records the F14 unauthorized-release contract: True" in state_checks[EVENTS]

    def test_the_landed_brakeengaged_consumers_are_protected_by_the_same_oracle(self, state_checks):
        """### A UNIT THAT "FIXED" THE MINTS-WHAT SCAN BY DELETING THESE CONSUMERS WOULD BREAK M2
        AND M4 INSTEAD. They consume `BrakeEngaged` by their own guard; M13 emits it."""
        contains = state_checks[EVENTS]
        assert "M2 still consumes BrakeEngaged: True" in contains
        assert "M4 still consumes BrakeEngaged: True" in contains

    def test_the_event_scan_excludes_docstrings_and_dotted_call_site_labels(self, m13):
        """Two ways this scan mismeasures, both hit during the bootstrap. A comment saying
        "`BrakeExpired` is deliberately NOT minted here" must not trip it; and `BrakeStore.engage`
        starts with `Brake` but is a context label, not an event name."""
        command = next(c.command for c in m13.expect_state if c.name == EVENTS)
        assert "docs.add(id(first.value))" in command, "docstrings are not excluded"
        assert "s.isidentifier()" in command, (
            "a dotted call-site label such as 'BrakeStore.engage' would be reported as a minted "
            "unregistered event"
        )

    def test_retention_is_a_database_refusal_with_a_lawful_positive_control(self, state_checks):
        """A brake row IS the incident record. The DELETE must be refused by the DATABASE, not by
        a code path an admin tool with a connection walks straight past."""
        contains = state_checks[RETENTION]
        assert "a delete-refusing trigger exists on brakes: True" in contains
        assert "a DELETE against a brake row: refused" in contains
        assert "a DELETE against the platform brake row: refused" in contains
        assert "positive control, a lawful release through the one authority: RELEASED" in contains
        assert "brake rows surviving: 1" in contains

    def test_the_retention_positive_control_goes_through_the_one_authority(self, m13):
        """### AN ORACLE THAT DEMANDED A RELEASE BY AN UNRECORDED HUMAN BE ACCEPTED WOULD
        CONTRADICT THE LIVE-WRITE ORACLE TWO CHECKS EARLIER. The control seeds a real
        `tenant_humans` row and releases through `BrakeStore`, which is also the only shape that
        stays correct once the FK exists."""
        command = next(c.command for c in m13.expect_state if c.name == RETENTION)
        assert "tenant_humans" in command, "the retention control never records a human"
        assert "BrakeStore" in command, "the retention control writes raw SQL instead of releasing"

    def test_the_flapping_oracle_counts_rows_rather_than_trusting_a_return_value(self, state_checks):
        """A store that returns the existing brake while writing a second row looks identical from
        the caller's side and is a momentary release window during an incident."""
        contains = state_checks[FLAPPING]
        assert "distinct brake ids across 25 engagements: 1" in contains
        assert "ACTIVE rows for the scope: 1" in contains
        assert "rows in any state for the scope: 1" in contains
        assert "no RELEASED row was ever created: 0" in contains
        assert "a signal count is recorded on the row: True" in contains

    def test_the_scope_grammar_partitions_the_canonical_five_and_refuses_the_unknown(
        self, state_checks
    ):
        """### THE ORACLE FOR `M13-AQ-6`. Whatever the unit lands, the dimensions it does NOT land
        must be UNSPELLABLE — never silently parsed into something narrower and never into nothing.
        An unknown scope that reads as "no brake" is the fail-closed inversion in its most deniable
        form."""
        contains = state_checks[SCOPE_GRAMMAR]
        assert ("the canonical scope dimensions: ['ACTION_CLASS', 'COUNTERPARTY', 'GLOBAL', "
                "'INTEGRATION', 'TENANT']") in contains
        assert "canonical dimension count: 5" in contains
        assert "landed and deferred partition the canonical five: True" in contains
        assert "landed and deferred do not overlap: True" in contains
        assert "every deferred dimension carries a recorded reason: []" in contains
        assert "an unknown scope dimension: refused" in contains
        assert "an unknown scope is never treated as no brake: True" in contains
        controls = [c for c in contains if c.startswith("positive control")]
        assert len(controls) >= 2, "the scope grammar refuses without proving anything parses"

    def test_the_ratchet_is_a_declared_table_over_distinct_actor_classes(self, state_checks):
        contains = state_checks[RATCHET]
        assert "what a human may do: ['BR-1', 'BR-2', 'BR-3', 'BR-4']" in contains
        assert "what a detector may do: ['BR-1', 'BR-2']" in contains
        assert "what automation may do: ['BR-1', 'BR-2']" in contains
        assert "what a model may do: []" in contains
        assert "what a timer may do: []" in contains
        assert "a detector may never clear its own alarm: True" in contains
        assert "every non-human actor is refused BR-3 and BR-4: []" in contains

    def test_br_5_is_an_enumerated_non_producing_refusal_not_an_unwritten_path(self, state_checks):
        """"We never wrote the timer path" and "the timer path is an enumerated illegal refusal"
        look identical until someone adds a scheduler."""
        contains = state_checks[MACHINE]
        assert "the declared transition ids: ['BR-1', 'BR-2', 'BR-3', 'BR-4', 'BR-5']" in contains
        assert "transition row count: 5" in contains
        assert "BR-5 has no destination state: True" in contains
        assert "BR-5 writes nothing: True" in contains
        assert "BR-5 produces no event: True" in contains
        assert "BR-5 is classified non-producing: GR1_ILLEGAL_REFUSAL" in contains

    def test_the_r17_report_states_everything_needed_to_release(self, state_checks):
        """ADR-011 §7 names nine things, and the sharpest is "the exact requirements for release —
        NOT 'contact an administrator'"."""
        contains = state_checks[R17]
        assert "R17 fields the report cannot state: []" in contains
        assert "the report names what is STILL ALLOWED: True" in contains
        assert "the report distinguishes a human from a named detector: True" in contains
        assert "the report names in-flight effects and their status: True" in contains
        assert "the report names unresolved unknown outcomes with exposure: True" in contains
        assert "the report names the exact release requirements: True" in contains

    def test_the_r17_oracle_asks_for_a_representation_and_not_a_channel(self, state_checks):
        """M13 ships dark. A dashboard is P8's, and an R17 oracle that demanded one would be
        asking the builder to violate the unit's own scope."""
        contains = state_checks[DARK]
        assert "channel-capable modules that import the brake: []" in contains
        assert "a brake console or dashboard module exists: []" in contains
        assert "a brake channel command exists: []" in contains
        assert "production detector wiring exists: []" in contains
        assert "M13 opens a socket, thread or subprocess: []" in contains

    def test_the_migration_parity_and_idempotence_are_asserted(self, state_checks):
        """M13 hardens tables P3 created, which is the migration shape most likely to drift: a
        fresh database gets the new DDL inline while an existing one gets it through an ALTER path,
        and the two diverge silently."""
        contains = state_checks[PARITY]
        assert "a second application of the migration is a no-op: True" in contains
        assert "the upgraded brake layer is identical to the fresh one: True" in contains
        assert "P3 readiness still holds after the M13 migration: []" in contains
        assert "the M13 migration declares its exempt tables explicitly: ['platform_brake']" in contains

    def test_the_release_evidence_conditions_are_each_named(self, state_checks):
        contains = state_checks[RELEASE_EVIDENCE]
        assert "in-flight accounting is required: True" in contains
        assert "an unresolved Sev-0 is disqualifying: True" in contains
        assert "positive integration health is required: True" in contains
        assert "a decision_ref is required: True" in contains
        assert "an authenticated human is required: True" in contains
        assert "release evidence is not satisfiable by a decision_ref alone: True" in contains
        assert "a page that loaded is not a positive health proof: True" in contains
        assert "a positive control IS a positive health proof: True" in contains

    def test_unknown_outcomes_do_not_block_release_and_are_not_cleared_by_it(self, state_checks):
        """Canonical authority is specific in BOTH directions, and this bootstrap will not let a
        session drift into either one. Blocking release on unknowns creates pressure to resolve
        them carelessly; clearing them on release is the brake resolving something it cannot."""
        contains = state_checks[RELEASE_EVIDENCE]
        assert "an unresolved unknown outcome does not block release: True" in contains
        assert "release builds no second approval workflow: True" in contains

    def test_the_canonical_adversarial_test_names_are_required_by_name(self, state_checks):
        contains = state_checks[TESTNAMES]
        assert "the M13 acceptance battery exists: True" in contains
        assert "entity point 44 tests missing: []" in contains
        assert "the machine section 41 acceptance items missing: []" in contains


# --------------------------------------------------------------------------
# 3b. ### THE MEASUREMENT SURVIVES ITS OWN REDACTOR
# --------------------------------------------------------------------------


class TestTheOraclesAreLegibleThroughTheStdoutRedactor:
    """### AN ORACLE THAT CANNOT BE READ THROUGH THE REDACTOR IS A FALSE RED, AND IT IS THE WORST
    KIND: the product is correct, the evidence is correct, and the harness reports a contradiction
    against a machine that did exactly what it was asked.

    Run `20260905-230030` produced exactly that. The composite-version oracle narrated its two
    asymmetry checks as ``a tenant event moved the token: True`` and ``a platform event moved the
    token: True``. The stdout redactor masks the value after any label whose last word smells like a
    credential — ``TOKEN`` is on that list — so both booleans reached the assertion engine as
    ``[REDACTED]``, and the permanent scenario went red while `brake.py` was right.

    The fix belongs on the MEASUREMENT side, and this is the guard that keeps it there. Two halves,
    and BOTH are load-bearing:

    * **A.** every string M13 asserts on must survive `redact` UNCHANGED, so no future oracle can
      word itself into a collision the way this one did;
    * **B.** genuinely credential-shaped output must STILL be masked, so nobody is ever tempted to
      buy A by weakening the redactor. The narrow correction was three labels in a YAML file; the
      wide one would have been a hole in secret handling.
    """

    #: Every place the M13 scenario states a literal it will later look for in output.
    def _asserted_literals(self, m13) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for command in m13.commands:
            out += [(f"command {command.name!r} expect_contains", s) for s in command.expect_contains]
        for check in m13.expect_state:
            out += [(f"expect_state {check.name!r} contains", s) for s in check.contains]
            out += [(f"expect_state {check.name!r} not_contains", s) for s in check.not_contains]
        out += [("expect_visible", s) for s in m13.expect_visible]
        out += [("forbidden", s) for s in m13.forbidden]
        return out

    def test_every_asserted_literal_survives_redaction_unchanged(self, m13):
        """### A. THE ORACLE MUST BE ABLE TO SEE WHAT THE PRODUCT PRINTED.

        The suite redacts command output before matching, so a literal that the redactor rewrites
        can never be found no matter how correct the product is.
        """
        collisions = [
            (where, literal, redact(literal))
            for where, literal in self._asserted_literals(m13)
            if redact(literal) != literal
        ]
        assert not collisions, (
            "these M13 assertions are worded so the stdout redactor rewrites them, which makes "
            "them unmatchable against real output and the scenario falsely red: "
            + "; ".join(f"{w}: {lit!r} -> {red!r}" for w, lit, red in collisions)
        )

    def test_the_composite_version_asymmetry_is_still_asserted_on_both_owners(self, state_checks):
        """The correction renamed the labels; it did not soften what they prove. A tenant event and
        a platform event must EACH move the one string the claim CAS revalidates — a tenant-only
        token would let a GLOBAL brake through and a global-only one would let a TENANT brake
        through.
        """
        contains = state_checks[TOKEN]
        assert "a tenant event moved the composite version: True" in contains
        assert "a platform event moved the composite version: True" in contains
        assert "the token names both owners: True" in contains
        assert "the platform component is monotonic: True" in contains
        assert "the tenant component is monotonic: True" in contains
        assert "an unreadable brake store at the version derivation: refused by BrakeStoreUnreachable" in contains

    def test_the_two_asymmetry_booleans_are_readable_as_the_product_prints_them(self):
        """Measured on the exact bytes the oracle emits, not on the assertion alone: the label AND
        its value have to survive together, because the redactor masks the value, not the label."""
        for line in (
            "a tenant event moved the composite version: True",
            "a platform event moved the composite version: True",
            "the quiet composite version: bv1|global:0|tenant:0",
            "after a TENANT event: bv1|global:0|tenant:1",
            "after a PLATFORM event: bv1|global:1|tenant:1",
        ):
            assert redact(line) == line, f"the redactor rewrites the oracle's own output: {line!r}"

    def test_the_old_wording_really_was_the_collision_and_is_gone(self, m13):
        """The guard is only worth having if it would have caught the thing that happened, so it is
        asserted directly: the retired labels DO collide, and the scenario no longer uses them."""
        for retired in (
            "a tenant event moved the token: True",
            "a platform event moved the token: True",
        ):
            assert redact(retired) != retired, (
                "the collision this section exists for cannot be reproduced, so the guard below "
                "proves nothing; re-derive it against the current redactor"
            )
        body = M13_PATH.read_text(encoding="utf-8")
        assert "moved the token" not in body, (
            "the retired label is back in the M13 scenario; it will be redacted into [REDACTED] "
            "and the permanent scenario will go falsely red again"
        )

    def test_credential_shaped_output_is_still_redacted(self):
        """### B. THE PRICE OF A. IS NOT PAID IN SECRETS.

        Every one of these is masked today. If a change to the redactor ever lets one through, this
        fails here rather than in a run artifact that has already been written to disk.
        """
        leaks = [
            secret
            for secret in (
                "ANTHROPIC_API_KEY: sk-ant-api03-abcdefghijklmnop",
                "GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
                "brake_release_token: hunter2-please-do-not-log",
                "slack_token = xoxb-11111111111-abcdefghij",
                "db password: correct-horse-battery",
                "postgres://brake:s3cr3t@db.internal:5432/freight",
                "AKIAIOSFODNN7EXAMPLE",
            )
            if redact(secret) == secret
        ]
        assert not leaks, f"the redactor no longer masks credential-shaped output: {leaks}"

    def test_the_scenario_did_not_buy_legibility_by_dropping_the_word(self, m13):
        """A cheap "fix" would have been to stop printing the composite version at all. The oracle
        still names it, still derives it through the one authority, and still prints both component
        values, so the rename cost the evidence nothing."""
        check = {c.name: c for c in m13.expect_state}[TOKEN]
        assert "version_token(tenant='T_A')" in check.command
        assert "the quiet composite version:" in check.command
        assert "after a TENANT event:" in check.command
        assert "after a PLATFORM event:" in check.command
        assert "brake version token" in TOKEN


# --------------------------------------------------------------------------
# 3a. ### THE BASELINE IS ATTRIBUTED, NOT ASSUMED
# --------------------------------------------------------------------------


class TestTheBaselineIsAttributedRatherThanAssumed:
    """### "EVERYTHING MUST BE RED BEFORE THE UNIT LANDS" WOULD BE A DISHONEST REQUIREMENT HERE.

    P3 legitimately landed real safety guarantees, and nine of the twenty-two persisted-state
    oracles are ALREADY GREEN on Neyma `ded6a841`. An already-green P3 guarantee is not a false
    green. What the scenario owes instead is that the M13 DELTA is separable and visible — so this
    section pins the attribution itself, and a future edit that quietly moved an oracle from one
    side to the other has to say so here.
    """

    def test_every_oracle_is_attributed_to_exactly_one_side(self, m13):
        overlap = set(P3_ALREADY_GREEN_ORACLES) & set(M13_DELTA_ORACLES)
        assert not overlap, f"an oracle is attributed to both sides: {sorted(overlap)}"
        attributed = set(P3_ALREADY_GREEN_ORACLES) | set(M13_DELTA_ORACLES)
        shipped = {c.name for c in m13.expect_state}
        assert attributed == shipped, (
            "the baseline attribution does not cover the shipped oracle set. "
            f"unattributed: {sorted(shipped - attributed)}; "
            f"attributed but not shipped: {sorted(attributed - shipped)}"
        )

    def test_the_delta_is_not_empty_and_is_not_everything(self, m13):
        """### BOTH DEGENERATE READINGS ARE WRONG. An empty delta means M13 owes nothing and the
        unit should not exist. A delta covering every oracle means the bootstrap is claiming P3
        landed nothing, which is false and would make the scenario unable to distinguish a
        regression in P3's guarantees from a gap in M13's."""
        assert M13_DELTA_ORACLES, "the M13 delta is empty; the unit would owe nothing"
        assert P3_ALREADY_GREEN_ORACLES, "nothing is credited to P3; the substrate is real"
        assert len(M13_DELTA_ORACLES) < len(m13.expect_state), (
            "every oracle is claimed as the M13 delta, which denies the landed P3 substrate"
        )

    def test_the_delta_covers_the_guarantees_p3_genuinely_lacks(self):
        """Each of these was measured RED against `ded6a841` during the bootstrap, and each names a
        canonical guarantee with a citation behind it."""
        assert EVENTS in M13_DELTA_ORACLES, "nothing on the tree emits an F13 event; that is M13's"
        assert RELEASE_EVIDENCE in M13_DELTA_ORACLES, "release has no evidence today"
        assert RETENTION in M13_DELTA_ORACLES, "a brake row can be deleted today"
        assert FLAPPING in M13_DELTA_ORACLES, "there is no signal count today"
        assert R17 in M13_DELTA_ORACLES, "the R17 report cannot be assembled today"
        assert MACHINE in M13_DELTA_ORACLES, "the five BR rows are not declared today"
        assert SCOPE_GRAMMAR in M13_DELTA_ORACLES, "the scope partition is a comment today"
        assert LIVE_WRITES in M13_DELTA_ORACLES, "released_by is any string today"
        assert TOKEN in M13_DELTA_ORACLES, (
            "the token derivation is P3's and correct, but `platform_status` lets a raw "
            "sqlite error escape instead of the canonical BrakeStoreUnreachable, so the "
            "fail-closed half of the read path is an M13 delta"
        )

    def test_the_credited_side_covers_what_p3_genuinely_landed(self):
        """### AND THIS IS THE HALF A CARELESS BOOTSTRAP GETS WRONG BY CLAIMING CREDIT FOR IT."""
        assert CAS in P3_ALREADY_GREEN_ORACLES, "the claim CAS is P3's and is already correct"
        assert UNIQUENESS in P3_ALREADY_GREEN_ORACLES, "tenant-first uniqueness is P3's"
        assert PLATFORM_ROW in P3_ALREADY_GREEN_ORACLES, "SD-12's one row is P3's"
        assert STATE_VOCAB in P3_ALREADY_GREEN_ORACLES, "the two-state CHECK is P3's"
        assert SINGLE_AUTHORITY in P3_ALREADY_GREEN_ORACLES, (
            "one brake authority is TRUE TODAY, and the oracle exists to keep it true"
        )

    def test_the_task_states_the_attribution_rather_than_demanding_a_red_baseline(self):
        """A task that told the builder "everything is red before you start" would be teaching them
        to distrust a correct measurement."""
        assert "AN ALREADY-GREEN P3 GUARANTEE IS NOT A FALSE GREEN" in M13_TASK_PROSE
        assert "THE BASELINE IS NOT" in M13_TASK
        assert "9 of the 22 oracles are ALREADY GREEN" in M13_TASK_FLAT
        assert "The other 13 are RED and they are the M13 delta" in M13_TASK_FLAT


# --------------------------------------------------------------------------
# 4. The task preserves the authority conflicts rather than resolving them
# --------------------------------------------------------------------------


class TestTheTaskPreservesTheAuthorityConflicts:
    def test_every_authority_question_is_recorded_by_id(self):
        for aq in AUTHORITY_QUESTIONS:
            assert aq in M13_TASK, f"{aq} is not recorded in the task"

    def test_the_task_says_conflicts_are_recorded_and_not_resolved(self):
        assert "WHERE THEY GENUINELY CONFLICT, YOU RECORD THE CONFLICT AND BUILD THE FAIL-CLOSED" \
            in M13_TASK
        assert "DO NOT INVENT AUTHORITY THAT DOES NOT EXIST" in M13_TASK
        assert "never a preference presented as a finding" in M13_TASK_PROSE

    def test_aq1_settles_the_p6_versus_p8_ownership_from_the_registry_itself(self):
        """P8's `allowed_scope` contains the word `brake` and its objective says "the real brake".
        Read alone that makes M13 unbuildable. The registry answers it in its own words, and the
        task quotes them rather than asserting a preference."""
        assert "NOT machines M11/M12/M13 themselves" in M13_TASK_FLAT
        assert "BUILD THE MACHINE. DO NOT BUILD THE RUNTIME." in M13_TASK

    def test_aq2_records_the_ambiguous_word_in_both_its_senses(self):
        """### THE MOST DANGEROUS AUTHORITY QUESTION IN THIS UNIT, because the wrong reading
        INVERTS THE ENTIRE RATCHET while every test name still reads correctly.

        ADR-011 §5.1 records "narrow" as genuinely ambiguous and resolves it to AUTHORITY. The
        corpus then uses both senses correctly in different files — `ER-12` in the SCOPE sense,
        `events/registry.md` §11 and `AC-SAFE-027` in the AUTHORITY sense. Read in the wrong sense,
        the latter two say automation may narrow a brake.

        ### THE TASK MUST NOT CALL THOSE TWO FILES DEFECTIVE, because they are not — and a
        bootstrap that told a builder to "correct" a correct document would be the preference-as-a-
        finding failure this whole section exists to prevent.
        """
        assert "M13-AQ-2" in M13_TASK
        assert "events/registry.md" in M13_TASK_FLAT
        assert "platform-safety-acceptance.md" in M13_TASK_FLAT
        assert "AC-SAFE-027" in M13_TASK
        assert "ER-12" in M13_TASK
        assert "THIS IS NOT A DEFECT IN THOSE TWO FILES AND YOU MUST NOT" in M13_TASK
        assert ("AUTOMATION MAY ENGAGE A BRAKE AND MAY WIDEN ITS SCOPE. AUTOMATION MAY NEVER "
                "NARROW A BRAKE'S SCOPE AND MAY NEVER RELEASE IT.") in M13_TASK_PROSE
        assert "STATE THE SENSE EVERY TIME YOU USE THE WORD" in M13_TASK

    def test_aq2_quotes_both_authorities_verbatim(self):
        """A recorded conflict a reader cannot check is a rumour. Both sentences are quoted so the
        builder can verify the reading against the files rather than trusting this one."""
        assert "Automated brake engagement/**narrowing** is permitted" in M13_TASK
        assert "engage/**narrow**\nonly" in M13_TASK or "engage/**narrow** only" in M13_TASK_FLAT
        assert "narrow autonomy" in M13_TASK

    def test_aq5_records_that_the_corpus_does_not_answer_platform_human_identity(self):
        """The platform row belongs to no tenant, so its releaser cannot FK into the tenant-first
        `tenant_humans` without pretending it does — which amendment A1 exists to forbid."""
        assert "M13-AQ-5" in M13_TASK
        assert "tenant_humans" in M13_TASK
        assert "THE REPOSITORY HAS NO PLATFORM-LEVEL HUMAN IDENTITY TABLE" in M13_TASK
        assert "the platform row does not acquire a tenant" in M13_TASK_PROSE

    def test_aq6_requires_the_scope_classification_to_be_re_derived(self):
        """### DO NOT CHOOSE ONE INTERPRETATION FROM MEMORY. The task states the bootstrap's own
        reading AND marks it as a reading rather than a ruling."""
        assert "M13-AQ-6" in M13_TASK
        for dimension in SCOPE_DIMENSIONS:
            assert dimension in M13_TASK, f"the scope dimension {dimension} is never named"
        assert "THAT IS A" in M13_TASK and "READING, NOT A RULING. Re-derive it." in M13_TASK_FLAT
        assert "NEVER SILENTLY ACCEPT AN UNKNOWN SCOPE AS" in M13_TASK

    def test_aq6_offers_the_three_classifications_by_letter(self):
        assert "M13 must own the complete frozen scope vocabulary now" in M13_TASK_FLAT
        assert "explicitly deferred" in M13_TASK_FLAT
        assert "a genuine authority conflict" in M13_TASK_FLAT

    def test_aq7_settles_br5_against_the_transition_audit(self):
        assert "M13-AQ-7" in M13_TASK
        assert "GR1_ILLEGAL_REFUSAL" in M13_TASK
        assert "REDUNDANT DOCUMENTATION, NOT A DIFFERENT CONTRACT" in M13_TASK
        assert "BR-5 PERSISTS NOTHING AND M13 MINTS NO FIFTH F13 CONTRACT" in M13_TASK

    def test_aq9_records_the_docstring_that_names_a_seam_that_does_not_exist(self):
        """`brake.py`'s `release()` docstring names `release_blockers`. There is no such function
        anywhere on the tree. ### A DOCSTRING IS NOT EVIDENCE THAT A SEAM EXISTS."""
        assert "M13-AQ-9" in M13_TASK
        assert "release_blockers" in M13_TASK
        assert "DO NOT TREAT THE DOCSTRING AS EVIDENCE THE SEAM EXISTS" in M13_TASK

    def test_the_task_forbids_editing_authority_to_make_a_test_pass(self):
        assert "DO NOT EDIT A SPECIFICATION, ADR OR REGISTRY TO MAKE A TEST PASS" in M13_TASK
        assert "do not edit" in M13_TASK_FLAT and "event_contracts_data.json" in M13_TASK

    def test_the_task_requires_each_question_to_be_reported(self):
        assert "the nine authority questions" in M13_TASK_FLAT
        assert "marked as an answer to an OPEN question" in M13_TASK_FLAT


# --------------------------------------------------------------------------
# 5. ### THE ONE THING NO EARLIER UNIT HAD TO GET RIGHT: brake.py IS LANDED AUTHORITY
# --------------------------------------------------------------------------


class TestTheTaskTreatsTheP3SubstrateAsLandedAuthority:
    """M13 is the only P6 machine whose substrate already exists.

    Every earlier unit could be built by writing a new module. Writing a new module here leaves TWO
    ANSWERS to "is Neyma stopped?", which is the same defect as none — and `CURRENT.md` saying
    `brake_lifecycle.py` is ABSENT is exactly the sentence a session would misread as permission.
    """

    def test_the_task_names_the_absence_evidence_and_immediately_bounds_it(self):
        assert "M13 DOES NOT START FROM ZERO" in M13_TASK
        assert ("That is evidence M13 IS NOT YET LANDED. IT IS NOT, BY ITSELF, AUTHORITY TO "
                "DUPLICATE" in M13_TASK_PROSE)
        assert "brake.py` is P3's landed kernel brake," in M13_TASK_FLAT

    def test_the_task_forbids_every_named_second_authority(self):
        for duplicate in ("second `BrakeStore`", "second brake state table",
                          "second brake decision engine", "second checkpoint brake decision",
                          "second claim-time brake authority"):
            assert duplicate in M13_TASK_FLAT, f"the task never forbids a {duplicate}"
        assert "DO NOT CREATE A SECOND BRAKE AUTHORITY" in M13_TASK

    def test_the_task_requires_the_substrate_to_be_mapped_before_any_edit(self):
        assert "MAP what P3 already guarantees" in M13_TASK_FLAT
        assert "what P3 already guarantees" in M13_TASK_FLAT
        assert "what canonical M13 requires beyond P3" in M13_TASK_FLAT
        assert "while preserving ONE authority" in M13_TASK_FLAT

    def test_the_task_names_the_landed_seams_the_builder_must_read(self):
        for seam in ("BrakeStore", "admission_denied", "version_token", "platform_brake",
                     "checkpoint.py", "tenant_humans", "VOID_ON_BRAKE", "test_phase3_brake.py"):
            assert seam in M13_TASK, f"the task never points the builder at {seam}"

    def test_the_task_states_the_m13_delta_explicitly(self):
        """A builder who is not told WHAT IS MISSING will either rebuild what exists or stop at
        what exists. Both are wrong, and the second is harder to see."""
        assert "THE M13 DELTA" in M13_TASK
        for missing in ("NOTHING ON THE TREE EMITS ANY F13 EVENT",
                        "NO UNAUTHORIZED RELEASE IS RECORDED",
                        "RELEASE HAS NO EVIDENCE",
                        "A brake row can be DELETED",
                        "There is no signal count"):
            assert missing in M13_TASK, f"the delta never names: {missing}"

    def test_the_task_permits_editing_brake_py_and_forbids_duplicating_it(self):
        """### THE DISTINCTION THE WHOLE UNIT TURNS ON, and it must be stated as a permission AND a
        prohibition or a cautious session will build beside it out of caution."""
        assert "src/freight_recon/brake.py" in M13_TASK
        assert "EDITED: complete the ONE landed authority" in M13_TASK
        assert "Editing" in M13_TASK_FLAT and "duplicating it is not" in M13_TASK_FLAT
        assert "you have built the defect" in M13_TASK_FLAT

    def test_the_task_forbids_deleting_the_landed_consumers_to_satisfy_a_scan(self):
        assert "DO NOT DELETE THE LANDED `BrakeEngaged` CONSUMERS" in M13_TASK
        assert "They consume; you emit" in M13_TASK_FLAT

    def test_the_task_protects_the_claim_cas_it_inherits(self):
        assert "the CAS is already correct and is a regression anchor" in M13_TASK_FLAT
        assert "checkpoint.py` REMAINS THE SOLE GATE MINTER" in M13_TASK_FLAT

    def test_the_task_states_the_five_position_boundary_row_by_row(self):
        """ADR-011 §3 is the heart of the ADR, and a task that summarised it would lose exactly the
        rows that matter: 3, 4 and 5 RUN TO A VERIFIED CONCLUSION."""
        for position in ("Not yet executing", "Grant MINTED, UNCLAIMED",
                         "CLAIMED, adapter not yet called", "Adapter called, response pending",
                         "Verification in progress"):
            assert position in M13_TASK, f"the boundary table is missing: {position}"
        assert "DO NOT KILL. LET IT FINISH AND VERIFY." in M13_TASK

    def test_the_task_distinguishes_a_real_unknown_outcome_from_a_manufactured_one(self):
        """The distinction the scenario measures: reality became unknowable, versus the brake
        manufactured an unknown outcome by engaging."""
        assert "THE BRAKE DOES NOT RESOLVE IT AND CANNOT" in M13_TASK
        assert "What the brake must never do is CREATE one" in M13_TASK_FLAT

    def test_the_task_states_the_narrow_broaden_ambiguity_before_using_the_words(self):
        """### GET THIS BACKWARDS AND THE ENTIRE RATCHET INVERTS WHILE EVERY TEST NAME STILL READS
        CORRECTLY. ADR-011 §5.1 records it for the same reason."""
        assert '"NARROW" AND "BROADEN" REFER TO AUTHORITY THROUGHOUT' in M13_TASK
        assert "Widening a brake narrows authority" in M13_TASK_FLAT
        assert "Narrowing a brake broadens authority" in M13_TASK_FLAT
        assert "GET THIS BACKWARDS AND THE ENTIRE RATCHET INVERTS" in M13_TASK_PROSE

    def test_the_task_reuses_m4_rather_than_rebuilding_it(self):
        assert "M13 DOES NOT REPLACE M4 AND BUILDS NO LOCAL BRAKE-APPROVAL MECHANISM" in M13_TASK
        assert "VOID_ON_BRAKE" in M13_TASK
        assert "Edit no part of M4" in M13_TASK_FLAT

    def test_the_task_states_the_compensation_observation_asymmetry(self):
        assert "COMPENSATION IS BLOCKED UNDER AN ACTIVE BRAKE" in M13_TASK
        assert "OBSERVATION AND RECONCILIATION CONTINUE" in M13_TASK
        assert "THE BRAKE STOPS ACTING, NOT KNOWING" in M13_TASK
        assert "There is no admin bypass" in M13_TASK_FLAT

    def test_the_task_requires_a_new_checkpoint_after_release(self):
        assert "RELEASE MUST NOT REACTIVATE STALE WITNESSES OR GRANTS" in M13_TASK
        assert "Every queued consequential action passes a NEW, FULL checkpoint" in M13_TASK_FLAT
        assert "Release creates NO new Checkpoint Witnesses" in M13_TASK_FLAT
        assert "stored-up volley" in M13_TASK_FLAT

    def test_the_task_states_the_replay_rule(self):
        assert "it never re-engages a real brake" in M13_TASK_FLAT
        assert ("zero new real brakes, zero witnesses, zero grants, zero effects and zero authority"
                in M13_TASK_FLAT)

    def test_the_task_requires_r17_without_a_production_channel(self):
        assert "A HIDDEN BRAKE IS A SILENT DEGRADATION" in M13_TASK
        assert ("DO NOT RESPOND TO THIS BY BUILDING A PRODUCTION DASHBOARD, ADMIN UI, SLACK "
                "INTEGRATION OR WEB" in M13_TASK)
        assert "the verification seam only" in M13_TASK_FLAT

    def test_the_task_requires_the_authenticated_human_substrate(self):
        assert "DO NOT TREAT AN ARBITRARY NON-EMPTY ACTOR STRING AS PROOF OF AN AUTHENTICATED HUMAN" \
            in M13_TASK
        assert "AUTHORITY_ROLES" in M13_TASK
        assert "invent no second human identity system" in M13_TASK_FLAT.lower()


# --------------------------------------------------------------------------
# 6. The M13 command vocabulary is safe, and visible to the generator
# --------------------------------------------------------------------------


class TestTheM13Vocabulary:
    def _planner(self, tmp_path, configured):
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True, approved_commands=configured),
            reasoner=ScriptedReasoner([{"risks": [], "scenarios": []}]),
            base_scenario=load_scenario(M13_PATH),
            permanent_scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            founder=FakeFounder(),
        )

    def test_no_case_name_trips_the_command_guard(self, cases):
        """A case name is part of a command string, and the guard is a token matcher. M6 shipped a
        case the boundary hard-blocked; the cost is paid where the name is authored."""
        refused = [c for c in cases if classify_command(f"{PROBE} --case {c}")]
        assert not refused, f"case names the command guard refuses: {refused}"

    def test_no_axis_value_trips_the_command_guard(self, dimensions):
        refused = []
        for token in dimensions:
            flag, _, value = token.partition(":")
            if classify_command(f"{PROBE} --{flag} {value}"):
                refused.append(token)
        assert not refused, f"axis values the command guard refuses: {refused}"

    def test_no_shipped_command_trips_the_command_guard(self, m13):
        """### AND THIS IS WHERE THE LEXICAL `sudo` CLASS OF DEFECT WOULD SHOW UP. The guard is a
        token matcher over the whole command string, so a privileged-looking token anywhere in a
        `python -c` payload hard-blocks an oracle that is doing nothing of the kind. The fix is
        always on the measurement side; the guard is not weakened."""
        refused = [(c.name, classify_command(c.run)) for c in m13.commands
                   if classify_command(c.run)]
        refused += [(c.name, classify_command(c.command)) for c in m13.expect_state
                    if classify_command(c.command)]
        assert refused == [], refused

    def test_no_shipped_command_contains_a_control_character(self, m13):
        """### THE RUN-20260830 DEFECT, PINNED WHERE IT IS AUTHORED. A command containing a newline
        is rendered to the generator as its WHITESPACE-COLLAPSED KEY rather than the human's text,
        because `ApprovedCommands` cannot show a control character safely. The generated copy is
        then faithful to a string whose Python indentation has already been destroyed, and it dies
        at parse with `IndentationError` without ever reaching the product.

        A YAML folded scalar preserves newlines for any line MORE INDENTED than the block, so this
        is easy to reintroduce by making a long payload readable.
        """
        offenders = [c.name for c in m13.commands if any(ch in c.run for ch in "\n\r\t")]
        offenders += [c.name for c in m13.expect_state
                      if any(ch in c.command for ch in "\n\r\t")]
        assert offenders == [], (
            "these commands contain a control character and would be offered to the generator as "
            f"a whitespace-collapsed key rather than the authored text: {offenders}"
        )

    def test_every_authored_command_is_offered_to_the_generator_as_written(self, m13):
        """The other half of the same defect, stated positively and over this scenario alone so the
        failure names M13 rather than the corpus."""
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=[],
        )
        verbatim = set(approved.verbatim)
        rewritten = [c.name for c in m13.commands if c.run.strip() not in verbatim]
        rewritten += [c.name for c in m13.expect_state if c.command.strip() not in verbatim]
        assert rewritten == [], (
            f"{len(rewritten)} M13 command(s) are offered in a rewritten form: {rewritten}"
        )

    def test_every_case_is_approved_by_the_bare_probe_prefix(self, tmp_path, cases):
        """### ENUMERATION BUYS VISIBILITY, NOT SAFETY — AND THAT IS WHY A CURATED LIST IS SAFE.

        The bare probe is approved, and approval matches by PREFIX, so every one of the `--case`
        tails is runnable whether or not the brief spells it out. What enumeration changes is
        whether the generator can SEE a case well enough to compose one; what it can never change
        is whether the case is permitted.
        """
        vocabulary = _local_vocabulary()
        if PROBE not in vocabulary:
            pytest.skip("no local config enumerating the bare M13 probe")
        planner = self._planner(tmp_path, vocabulary)
        for case in cases:
            ok, why = planner.approved_commands.approves(f"{PROBE} --case {case}")
            assert ok, f"{case}: {why}"

    def test_omission_from_the_enumeration_never_makes_a_case_forbidden(self, tmp_path, cases):
        """### THE M12 INVARIANT, RESTATED FOR A UNIT WHOSE ENUMERATION IS EMPTY.

        Enumeration buys VISIBILITY, never PERMISSION. The M13 config enumerates no `--case` tail
        at all — see `test_the_render_bound_is_what_emptied_the_enumeration` for the measurement
        that forced it — so this is the test that proves the omission costs nothing: EVERY ONE of
        the declared cases must still be runnable through the bare probe prefix. If this ever
        fails, the empty enumeration has silently become a narrowed scope.
        """
        planner = self._planner(tmp_path, _local_vocabulary())
        refused = [
            (case, planner.approved_commands.approves(f"{PROBE} --case {case}")[1])
            for case in cases
            if not planner.approved_commands.approves(f"{PROBE} --case {case}")[0]
        ]
        assert refused == [], (
            f"{len(refused)} declared M13 case(s) are not permitted at all: {refused[:4]}"
        )

    def test_the_render_bound_is_what_emptied_the_enumeration(self, tmp_path):
        """### THE MEASUREMENT, RECORDED SO IT IS NOT RE-LITIGATED FROM TASTE.

        The scenario corpus WITHOUT `p6_m13_brake.yaml` is 326 commands; the M13 scenario adds 73,
        taking it to 399 against a bound of 400. There is one slot left, and it is spent on M12's
        bare probe. A 149-case enumeration was written first and measured at 530 — it pushed 130
        commands out of the rendered corpus and took SIX earlier units' bare probes with it.
        """
        planner = self._planner(tmp_path, _local_vocabulary())
        total = len(planner.approved_commands)
        assert total <= MAX_RENDERED_COMMANDS, (
            f"{total} approved commands against a render bound of {MAX_RENDERED_COMMANDS}"
        )
        scenarios_only = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=[],
        )
        headroom = MAX_RENDERED_COMMANDS - len(scenarios_only)
        assert headroom < 20, (
            f"there is now {headroom} commands of headroom. The enumeration was emptied because "
            "there was almost none; if the bound or the corpus has changed, re-decide it "
            "deliberately rather than leaving this comment stale."
        )

    def test_the_scenario_itself_makes_the_load_bearing_cases_visible(self, m13):
        """### AN EMPTY CONFIG ENUMERATION IS ONLY SAFE BECAUSE THE SCENARIO IS NOT EMPTY.

        Visibility has to come from somewhere, and here it comes from the permanent scenario's own
        named `--case` runs, which ARE in the approved corpus and DO carry their flags. If those
        ever thinned out, the generator would be composing M13 cases blind.
        """
        runs = " ".join(c.run for c in m13.commands)
        for needed in ("--case the-brake-stops-the-next-effect-not-the-last",
                       "--case engaging-during-an-adapter-call-creates-no-unknown-outcome",
                       "--case only-an-authenticated-human-narrows",
                       "--case automation-may-widen",
                       "--case release-is-not-a-human-and-a-decision-ref-alone",
                       "--case a-brake-between-mint-and-claim-matches-zero-rows",
                       "--case cannot-read-the-brake-never-means-off",
                       "--case there-is-exactly-one-brake-authority",
                       "--case a-flapping-detector-creates-one-active-brake",
                       "--case an-active-brake-is-reported-unprompted"):
            assert needed in runs, f"the scenario never makes {needed} visible to the generator"
        with_flags = [c.run for c in m13.commands if "--case" in c.run and c.run.count("--") > 1]
        assert len(with_flags) >= 10, (
            f"only {len(with_flags)} case runs carry a second flag, so the generator cannot see "
            "how M13's axes compose"
        )

    def test_the_approved_set_still_fits_inside_what_the_brief_renders(self, tmp_path):
        """### THE BOUND M12 MEASURED, RE-MEASURED FOR A UNIT WITH MORE CASES THAN M12 HAD.

        Approved commands sort ASCII and every probe entry begins `scripts/probe_...`, so they sort
        LAST: an approved set larger than the render bound loses the probe vocabulary FIRST, and
        loses it silently. M13 declares more cases than M12 did, so the enumeration is curated for
        the same reason and the rest stay reachable through the bare probe prefix.
        """
        planner = self._planner(tmp_path, _local_vocabulary())
        assert len(planner.approved_commands) <= MAX_RENDERED_COMMANDS, (
            f"{len(planner.approved_commands)} approved commands but the generation brief renders "
            f"only the first {MAX_RENDERED_COMMANDS} — the M13 vocabulary sorts last and is now "
            "invisible to the generator."
        )

    def test_no_prior_units_vocabulary_disappeared_because_m13_consumed_the_budget(self, tmp_path):
        """### THE OTHER HALF OF THE RENDER BOUND, AND THE ONE THAT FAILS SILENTLY. M9's bootstrap
        lost M3's bare probe this way. Every earlier P6 unit's deterministic entry point must still
        be reachable after M13's vocabulary is added."""
        planner = self._planner(tmp_path, _local_vocabulary())
        approved = planner.approved_commands
        for probe in ("scripts/probe_phase6_approval.py", "scripts/probe_phase6_exception.py",
                      "scripts/probe_phase6_observation.py", "scripts/probe_phase6_conflict.py",
                      "scripts/probe_phase6_expectation.py",
                      "scripts/probe_phase6_external_effect.py",
                      "scripts/probe_phase6_identity_binding_claim.py"):
            command = f".venv/bin/python {probe}"
            ok, why = approved.approves(command)
            assert ok, f"an earlier unit's probe is no longer permitted: {command}: {why}"
            rendered = approved.entries[:MAX_RENDERED_COMMANDS]
            assert command in rendered, (
                f"an earlier unit's probe fell OUT OF THE RENDERED CORPUS because M13 consumed "
                f"the budget: {command}. This is the M9-bootstrap defect and it fails silently."
            )

    def test_the_config_enumerates_no_case_and_records_why(self):
        """### THE EMPTY ENUMERATION IS A DECISION, AND A DECISION HAS TO BE WRITTEN DOWN.

        A config that simply forgot to enumerate and a config that measured the render bound and
        chose not to look identical from the outside. The comment block is the difference, so it is
        asserted rather than trusted.
        """
        raw = _local_config()
        if not raw or raw.get("scenario") != "p6_m13_brake":
            pytest.skip("the local config does not target M13")
        text = (DRIVER_ROOT / "driver.config.yaml").read_text(encoding="utf-8")
        assert "THE ENUMERATION IS EMPTY, AND THAT IS A MEASURED DECISION" in text
        assert "ENUMERATION BUYS VISIBILITY, NEVER PERMISSION" in text
        enumerated = [
            c for c in (raw.get("scenario_generation", {}).get("approved_commands") or [])
            if "--case " in c
        ]
        assert enumerated == [], (
            "the config enumerates cases again; re-measure the render bound before doing that, "
            f"because it had one slot of headroom: {enumerated[:3]}"
        )

    def test_every_enumerated_config_entry_is_one_the_scenario_already_declares(self, m13):
        """Every config entry must deduplicate into the scenario corpus, or it spends budget the
        measurement showed is not there. The one deliberate exception is M12's regression anchor."""
        raw = _local_config()
        if not raw or raw.get("scenario") != "p6_m13_brake":
            pytest.skip("the local config does not target M13")
        declared = {c.run.strip() for c in m13.commands} | {c.command.strip() for c in m13.expect_state}
        allowed_extra: set[str] = set()
        for entry in raw.get("scenario_generation", {}).get("approved_commands") or []:
            assert entry.strip() in declared or entry.strip() in allowed_extra, (
                f"the config adds a command the scenario does not declare, spending render "
                f"budget the measurement showed is not available: {entry!r}"
            )

    def test_the_config_targets_m13_and_only_ever_moved_forward(self):
        raw = _local_config()
        if not raw:
            pytest.skip("this checkout has no local driver.config.yaml")
        target = raw.get("scenario")
        assert target in P6_UNIT_ORDER, f"the config targets an unknown scenario: {target!r}"
        assert P6_UNIT_ORDER.index(target) >= P6_UNIT_ORDER.index("p6_m12_rule"), (
            f"the local config has moved BACKWARDS to {target!r}"
        )

    def test_the_config_is_not_tracked_by_git(self):
        """`driver.config.yaml` is local configuration and is git-ignored. A bootstrap that
        committed it would publish a local retarget as a repository fact."""
        gitignore = (DRIVER_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "driver.config.yaml" in gitignore.split(), (
            "driver.config.yaml is not git-ignored, so retargeting it would be a tracked change"
        )


# --------------------------------------------------------------------------
# 7. Generation closes an M13 gap without inventing a command
# --------------------------------------------------------------------------


STATE_ORACLE = next(
    c.command for c in load_scenario(M13_PATH).expect_state if c.name == TABLES
)


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close."""
    return GeneratedScenario(
        id="gen-m13-second-brake-authority",
        title="the brake machine never becomes a second brake authority",
        purpose=(
            "a second store that owns brake state gives the system two answers to 'is Neyma "
            "stopped?', and an admission control with two answers has none"
        ),
        risk_category=RiskCategory.SAFETY_INVARIANT,
        priority=Priority.P0,
        rationale="the identified second-brake-authority risk had no scenario behind it",
        requirement_reference="P6/M13",
        product_principle_reference="effect-truth",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared state, so "
            "nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m13-task",
            session_id="scripted",
            generating_risk="M13 could own brake state outside brake.py",
            source_risks=[risk_key],
        ),
        actions=[{
            "kind": "command",
            "name": "drive the brake machine and watch for a second authority",
            "command": command,
            "expect_contains": ["THERE IS EXACTLY ONE BRAKE AUTHORITY"],
        }],
        persisted_state_checks=[
            GeneratedStateCheck(
                name="the brake layer is still one layer and readable",
                command=STATE_ORACLE,
                contains=["problems: []", "brake state tables: 2"],
            )
        ],
        expected_observations=["THERE IS EXACTLY ONE BRAKE AUTHORITY"],
        forbidden_observations=["### A SECOND BRAKE AUTHORITY WAS BUILT ###"],
    )


class TestGenerationClosesM13GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-second-brake-authority",
            description="M13 could own brake state outside brake.py",
            risk_category=RiskCategory.SAFETY_INVARIANT,
            severity=Priority.P0,
            basis="CLAUDE.md §5 rule 17: one authority per domain; ADR-011 §1",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m13", "p6", "m13"},
                principle_tokens={"effect-truth"},
                known_risk_ids={risk.key, "R-second-brake-authority"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m13_vocabulary_is_accepted(self, context):
        """The whole point of enumerating the vocabulary: the generator can COMPOSE a case the
        permanent scenario never wrote, from arguments a human already approved."""
        ctx, risk = context
        command = (
            f"{PROBE} --case there-is-exactly-one-brake-authority "
            "--position 4 --owner both --actor detector --seed 12"
        )
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M13 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario('python -c "import brake; brake.release_everything()"', risk.key)],
            ctx,
        )
        assert not accepted
        assert rejected
        reasons = rejected[0][1]
        assert any("approved" in r.lower() for r in reasons), reasons

    def test_a_gap_case_touching_repository_authority_is_refused(self, context):
        """A verification scenario observes the product; it never edits the rules the product is
        judged against. For THIS unit that matters especially, because `M13-AQ-2` is a wording
        defect in two authority files and 'fixing' one would make an oracle pass by moving the
        goalposts."""
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario(f"{PROBE} --case x docs/implementation/CURRENT.md", risk.key)],
            ctx,
        )
        assert not accepted
        reasons = rejected[0][1]
        assert any("authority" in r.lower() for r in reasons), reasons


# --------------------------------------------------------------------------
# 8. Resume safety: every named oracle keeps a stable approved-command identity
# --------------------------------------------------------------------------


class TestEveryM13OracleCanBackAGeneratedCase:
    """`c6331dc` and `cb5ecf9`, applied before a run rather than after a blocked one.

    A generated case cites an approved command by a token over its BODY, and records the human NAME
    it was written under so a body repair can be rebound rather than deleted. Two things have to
    hold, and both are measured here rather than assumed:

    * every oracle the scenario ships must PASS the generated-command boundary, or no generated case
      can ever cite it — the `M11-W2-3` collision;
    * every oracle NAME must be unambiguous ACROSS THE WHOLE CORPUS, because a label two different
      commands answer to identifies neither and is dropped from `by_name`, taking the oracle's
      rebinding identity with it.
    """

    @pytest.fixture(scope="class")
    def approved(self):
        return ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )

    def test_no_m13_oracle_is_left_uncitable(self, m13, approved):
        refused = [
            (check.name, approved.approves(check.command)[1])
            for check in m13.expect_state
            if not approved.approves(check.command)[0]
        ]
        refused += [
            (spec.name, approved.approves(spec.run)[1])
            for spec in m13.commands
            if not approved.approves(spec.run)[0]
        ]
        assert refused == [], refused

    def test_every_m13_oracle_name_resolves_to_its_own_command(self, m13, approved):
        """The rebinding identity, checked CORPUS-WIDE rather than M13-locally. A name missing from
        `by_name` is a name two commands share, and an oracle whose name resolves to nothing cannot
        be re-materialised after a body repair — which is exactly how run `20260903-065810` lost
        four cases."""
        missing: list[str] = []
        wrong: list[str] = []
        for check in m13.expect_state:
            if check.name not in approved.by_name:
                missing.append(check.name)
            elif approved.by_name[check.name] != check.command.strip():
                wrong.append(check.name)
        for spec in m13.commands:
            if spec.name not in approved.by_name:
                missing.append(spec.name)
            elif approved.by_name[spec.name] != spec.run.strip():
                wrong.append(spec.name)
        assert not missing, f"{len(missing)} M13 oracle name(s) are ambiguous corpus-wide: {missing}"
        assert not wrong, f"{len(wrong)} M13 oracle name(s) resolve to another command: {wrong}"

    def test_every_named_oracle_is_unique_inside_the_scenario(self, m13):
        names = [c.name for c in m13.expect_state] + [c.name for c in m13.commands]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, f"duplicate oracle names inside the scenario: {duplicates}"

    def test_no_m13_oracle_name_collides_with_an_earlier_units_oracle(self, m13):
        """### CORPUS-WIDE, NOT M13-LOCAL. Two scenarios naming different commands under one label
        destroys the label for BOTH, so this bootstrap can break M12's resume without touching
        M12's file."""
        mine = {c.name: c.command.strip() for c in m13.expect_state}
        mine |= {c.name: c.run.strip() for c in m13.commands}
        collisions: list[str] = []
        for path in sorted(SCENARIOS_DIR.glob("*.y*ml")):
            if path == M13_PATH:
                continue
            other = load_scenario(path)
            theirs = {c.name: c.command.strip() for c in other.expect_state}
            theirs |= {c.name: c.run.strip() for c in other.commands}
            for name, body in theirs.items():
                if name in mine and mine[name] != body:
                    collisions.append(f"{path.name}:{name}")
        assert not collisions, (
            f"M13 reuses {len(collisions)} oracle name(s) for a DIFFERENT command, which drops the "
            f"label from `by_name` for both scenarios: {collisions}"
        )

    def _generated_from(self, oracle_name: str, approved, harness):
        from neyma_product_driver.scenario_plan import (
            GeneratedAction,
            compile_to_scenario,
        )
        from neyma_product_driver.scenario_validation import citation_token

        body = approved.by_name[oracle_name]
        scenario = GeneratedScenario(
            id="gen-" + str(abs(hash(oracle_name)) % 10**8),
            title=oracle_name[:80],
            risk_category="safety_invariant",
            priority="P0",
            requirement_reference="M13: the Brake",
            actions=[
                GeneratedAction(kind="command", name="oracle", command=f"@{citation_token(body)}")
            ],
        )
        scenario.bind_citations(approved)
        allowed, _reasons = approved.resolve(scenario.command_strings())
        return scenario, compile_to_scenario(scenario, base=harness, approved_commands=allowed)

    def test_every_named_m13_oracle_compiles_into_a_generated_case(self, m13, approved):
        """### FRESH-RUN READINESS, STATED GENERICALLY. A generated scenario's power is ordering,
        repetition and expectation; its MEASUREMENTS are the oracles a human already wrote. So the
        readiness question is not "do these four ids work" — it is whether EVERY oracle in the
        permanent scenario can back a generated case, by citation, and survive a rebind."""
        names = sorted(
            {check.name for check in m13.expect_state if check.name}
            | {spec.name for spec in m13.commands if spec.name}
        )
        assert names, "the permanent scenario must carry named oracles"
        failures = []
        for name in names:
            if name not in approved.by_name:
                failures.append((name, "no unambiguous approved command under that name"))
                continue
            try:
                scenario, _compiled = self._generated_from(name, approved, m13)
            except Exception as exc:  # noqa: BLE001 - the reason IS the finding
                failures.append((name, f"{type(exc).__name__}: {exc}"))
                continue
            if not approved.approves(scenario.actions[0].command)[0]:
                failures.append((name, "refused by the generated-command boundary"))
            if not scenario.command_bindings:
                failures.append((name, "no binding was recorded, so a repair could not be rebound"))
                continue
            bound = scenario.command_bindings[0].source_name
            if approved.by_name.get(bound) != approved.by_name[name]:
                failures.append((name, f"the binding {bound!r} resolves to a different command"))
        assert failures == [], failures

    def test_a_stale_cited_body_is_rebound_to_the_current_approved_text(self, m13, approved):
        """### THE `c6331dc` INVARIANT, PROVED ON AN M13 ORACLE. A generated case cites a body by a
        token over its bytes. When a human repairs that body, the citation goes stale — and the
        resume must REBIND from the recorded name to the CURRENT approved text, not delete the
        obligation and not trust the stale string."""
        name = SINGLE_AUTHORITY
        scenario, _compiled = self._generated_from(name, approved, m13)
        stale = ".venv/bin/python -c \"print('a body no human approves any more')\""
        scenario.actions[0].command = stale
        rebound, unreconstructable = rebind_to_approved(scenario, approved)
        assert not unreconstructable, unreconstructable
        assert rebound, "nothing was rebound, so the stale body would have been executed"
        assert scenario.actions[0].command == approved.by_name[name], (
            "the rebind did not re-materialise the oracle from the current approved text"
        )
        assert stale not in scenario.actions[0].command, "the stale body survived the rebind"

    def test_the_load_bearing_oracles_specifically_can_back_a_generated_case(self, approved):
        """Named separately, because a green sweep above could hide a single refusal on exactly the
        oracles this unit cannot afford to lose."""
        for name in (SINGLE_AUTHORITY, CAS, LIVE_WRITES, PLATFORM_ROW, RELEASE_EVIDENCE):
            command = approved.by_name[name]
            ok, why = approved.approves(command)
            assert ok, f"{name}: {why}"


# --------------------------------------------------------------------------
# 9. M13 is scoped as a unit, and — being the LAST machine — cannot become the phase
# --------------------------------------------------------------------------


@pytest.fixture
def m13_repo(tmp_path: Path) -> PhaseRepo:
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/brake_lifecycle.py", "# the unit under construction\n")
    repo.commit_all("the M13 candidate")
    return repo


class TestM13IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m13(self, m13_repo: PhaseRepo):
        scope = m13_repo.scope(M13_TASK)
        assert scope.scope_id == "P6/M13"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(self, m13_repo: PhaseRepo):
        scope = m13_repo.scope(M13_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m13_repo: PhaseRepo):
        scope = m13_repo.scope(M13_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(self, m13_repo: PhaseRepo):
        rendered = m13_repo.scope(M13_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "enables nothing in production" in rendered

    def test_the_task_says_the_phase_does_not_move(self):
        assert "`criteria_scored` stays `[]`" in M13_TASK_FLAT
        assert "P7 stays `BLOCKED`" in M13_TASK_FLAT
        assert "LANDING M13 SCORES NO P6 CRITERION" in M13_TASK

    def test_the_task_says_twice_that_the_last_machine_is_not_the_phase(self):
        """### THE SINGLE MOST PREDICTABLE SCOPE FAILURE AVAILABLE TO THIS SESSION, and the reason
        it is stated in the opening blockquote AND again in §2: M13 is the thirteenth of thirteen,
        so "the machine is done" and "the phase is done" are one sentence apart."""
        assert "LANDING IT DOES NOT COMPLETE P6" in M13_TASK
        assert "IT IS NOT P6 COMPLETION" in M13_TASK
        assert "distinct P6 acceptance" in M13_TASK_FLAT
        assert ("You do NOT perform P6 acceptance: it is a distinct, later, adjudicated step, "
                "and it is not yours.") in M13_TASK_FLAT

    def test_the_scenario_asserts_the_same_thing_the_task_does(self, m13):
        assert "LANDING M13 IS NOT P6 ACCEPTANCE" in m13.expect_visible
        for marker in ("### P6 MARKED COMPLETE ###", "### A P6 CRITERION WAS SCORED ###",
                       "### P7 UNBLOCKED ###"):
            assert marker in m13.forbidden, f"{marker} is not forbidden"


class TestM13CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m13_repo: PhaseRepo
    ):
        scope = m13_repo.scope(M13_TASK)
        completion = scoped_completion(scope, TaskResult.ACCEPTED, phase_accepted=True)
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M13"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m13_repo: PhaseRepo):
        completion = scoped_completion(m13_repo.scope(M13_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")


# --------------------------------------------------------------------------
# 10-11. The review is owed, and the loop owns M13 end to end
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m13_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m13_repo.root, m13_repo.scope(M13_TASK), unit=m13_repo.unit()
        )
        assert requirement.required
        assert requirement.from_repository_authority

    def test_the_task_states_the_tier_and_says_why_it_took_the_higher_one(self):
        """CLAUDE.md §7: "When genuinely torn between two tiers, take the higher one once and say
        so." M13 lands a migration, is load-bearing for tenant isolation, decides whether an action
        is allowed inside BOTH the checkpoint and the claim CAS, and EDITS A LANDED P3 KERNEL
        MODULE four other machines depend on."""
        assert "tier-1" in M13_TASK
        assert "migration" in M13_TASK_FLAT
        assert "tenant isolation" in M13_TASK_FLAT
        assert "edits a landed p3 kernel module" in M13_TASK_PROSE.lower()
        assert "takes the higher tier once and says so" in M13_TASK_PROSE

    def test_the_task_names_why_a_defect_here_is_different(self):
        assert ("A DEFECT HERE IS NOT A WRONG ANSWER; IT IS THE ABSENCE OF THE THING THAT "
                "STOPS WRONG ANSWERS") in M13_TASK_FLAT


class TestTheLoopOwnsM13EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m13_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session that wrote the code,
        with its evidence path intact."""
        builder = FakeBuilder(m13_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m13_repo, tmp_path, task=M13_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        assert "INDEPENDENT REVIEW" in builder.prompts[1]
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED


# --------------------------------------------------------------------------
# 12. The unit stops where it was told to stop
# --------------------------------------------------------------------------


class TestTheUnitStopsAtM13:
    def test_the_task_refuses_p7_and_p8_explicitly(self):
        assert "P7 stays `BLOCKED`" in M13_TASK_FLAT
        assert "Not P7. Not P8's brake runtime." in M13_TASK_FLAT
        assert "P8 runtime" in M13_TASK_FLAT

    def test_the_task_refuses_the_production_surfaces(self):
        for surface in ("brake console", "admin console", "live dashboard", "Slack",
                        "production detector wiring", "outbound integration", "freight workflow",
                        "bounded autonomy", "autonomy graduation"):
            assert surface in M13_TASK_FLAT, f"the task never refuses a {surface}"
        assert "SHIP DARK" in M13_TASK

    def test_the_task_permits_probe_only_invocation(self):
        assert "Test and probe-only invocation is fine" in M13_TASK_FLAT
        assert "Nothing reaches live traffic" in M13_TASK_FLAT

    def test_the_task_preserves_the_landed_runtime(self):
        assert "PRESERVE THE M1–M12 RUNTIME" in M13_TASK
        assert "Do not modify M1–M12" in M13_TASK_FLAT

    def test_the_task_reads_the_residuals_and_refuses_to_action_them(self):
        """### "M13 IS THE LAST MACHINE" MUST NOT BECOME A REASON TO BROADEN INTO P6 CLEANUP."""
        assert "P6-D82" in M13_TASK and "P6-D88" in M13_TASK
        assert "READ THEM. DO NOT OPPORTUNISTICALLY FIX THEM" in M13_TASK_PROSE
        assert ("DO NOT LET \"M13 IS THE LAST MACHINE\" BECOME A REASON TO BROADEN THIS UNIT INTO "
                "P6 CLEANUP" in M13_TASK)
        assert "record why and STOP for a scope decision" in M13_TASK_FLAT

    def test_the_task_refuses_to_invent_policyoverridden(self):
        assert "P6-D71" in M13_TASK
        assert "DO NOT INVENT `PolicyOverridden`" in M13_TASK
        assert "PolicyOverridden" in " ".join(
            f for f in load_scenario(M13_PATH).forbidden
        ), "the scenario does not forbid a minted PolicyOverridden"

    def test_the_task_keeps_the_open_validation_questions_open(self):
        for question in ("V13", "V14", "V15"):
            assert question in M13_TASK, f"{question} is never mentioned"
        assert "stay OPEN at their fail-closed defaults" in M13_TASK_FLAT

    def test_the_task_allows_a_local_commit_and_forbids_a_push(self):
        assert "A LOCAL COMMIT IS ALLOWED AND EXPECTED" in M13_TASK
        assert "DO NOT PUSH, DO NOT DEPLOY, AND DO NOT ENABLE" in M13_TASK
        assert "No remote operation of any kind" in M13_TASK_FLAT

    def test_the_task_says_repository_authority_wins(self):
        assert "REPOSITORY AUTHORITY WINS" in M13_TASK
        assert "the repository is right and the disagreement is a finding you REPORT" in M13_TASK_PROSE

    def test_the_task_forbids_minting_an_unregistered_event(self):
        assert "DO NOT MINT AN UNREGISTERED EVENT" in M13_TASK
        for contract in FORBIDDEN_CONTRACTS:
            assert contract in M13_TASK, f"the task never names the forbidden contract {contract}"

    def test_the_task_requires_the_mutation_battery_to_measure_rather_than_assert(self):
        assert "mutations caught, 0 escaped" in M13_TASK
        assert "NO ESCAPED MUTATION MAY BE HAND-WAVED AWAY" in M13_TASK
        assert "Include an anti-vacuity control" in M13_TASK_FLAT
        assert "Do not hard-code an expected mutation count anywhere" in M13_TASK_FLAT

    def test_the_task_names_the_mutations_that_matter_most(self):
        for mutation in ("add a third brake state", "allow automation to release",
                         "allow a detector to narrow", "let a timer auto-release",
                         "treat an absent brake store as released",
                         "check only the tenant version", "check only the global version",
                         "let the brake kill an executing worker",
                         "leave an old witness valid after release",
                         "represent GLOBAL as a fake tenant", "add a fifth F13 event",
                         "let replay re-engage a live brake",
                         "introduce a second independent brake authority"):
            assert mutation in M13_TASK_FLAT, f"the mutation battery never plants: {mutation}"

    def test_the_task_requires_the_race_battery_at_the_canonical_order(self):
        assert "10,000" in M13_TASK
        assert "DO NOT USE SLEEP-BASED TIMING AS THE ONLY EVIDENCE" in M13_TASK

    def test_the_task_requires_the_report_to_separate_p3_from_m13(self):
        assert "the map of what P3 already guaranteed, what M13 added" in M13_TASK_FLAT
        assert "that you created NO second brake authority" in M13_TASK_FLAT
        assert "the baseline attribution" in M13_TASK_FLAT


# --------------------------------------------------------------------------
# 13-14. Does this file fail when the guard is removed?
# --------------------------------------------------------------------------


def _mutate(edit):
    """Load a copy of the shipped M13 scenario with one weakening applied.

    `edit` receives the raw YAML mapping and changes it in place. Nothing is written to the
    scenarios directory: the mutant lives in a temporary file and is parsed through the real
    loader, so a weakening the loader itself refuses raises here rather than returning a Scenario.
    """
    import tempfile

    raw = copy.deepcopy(yaml.safe_load(M13_PATH.read_text(encoding="utf-8")))
    edit(raw)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m13_mutant.yaml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return load_scenario(path)


def _named(raw: dict, section: str, name: str) -> dict:
    for entry in raw[section]:
        if entry.get("name") == name:
            return entry
    raise AssertionError(f"{name!r} is not in {section}; the mutation targets a check that is gone")


def _decite(raw: dict, removed: set) -> None:
    """Drop every `verifies:` citation of a check the mutation just removed.

    The loader refuses a scenario whose risk claims name a check it does not run — correctly — so a
    mutation that deletes a COMMAND must delete its citations too, or the mutant fails to parse and
    the test passes for a reason that has nothing to do with the guard it is probing.
    """
    for claim in raw["verifies"]:
        claim["checks"] = [c for c in claim.get("checks", []) if c not in removed]


def _drop(entry: dict, key: str, literal: str) -> None:
    before = list(entry[key])
    entry[key] = [x for x in before if x != literal]
    assert len(entry[key]) == len(before) - 1, (
        f"{literal!r} was not in {key}; the mutation targets something that is already gone"
    )


class TestThisFileFailsWhenTheGuardIsRemoved:
    """A readiness test never seen to fail is a decoration.

    Every case below weakens the SHIPPED scenario in one specific way and then runs the REAL
    assertion from earlier in this file against the weakened copy — not a paraphrase of it. If an
    assertion has been loosened into something that passes either way, these turn green and the
    failure is visible here rather than six weeks later in a run that verified nothing.
    """

    # ---- the round-trip control: an UNMUTATED copy must still pass -------------------------
    def test_the_unmutated_round_trip_still_passes_every_guard(self):
        """The control every mutation below depends on. If the YAML round trip itself broke the
        scenario, every mutation would "fail" for a reason that has nothing to do with the defect
        it planted, and the whole section would be measuring the serializer."""
        clean = _mutate(lambda raw: None)
        checks = {c.name: list(c.contains) for c in clean.expect_state}
        TestPersistedStateIsTheOracle().test_the_two_state_vocabulary_is_asserted_as_a_database_check(checks)
        TestPersistedStateIsTheOracle().test_the_platform_row_cardinality_is_structural_and_tenantless(checks)
        TestPersistedStateIsTheOracle().test_the_single_authority_is_measured_by_ast_across_the_package(clean, checks)
        TestPersistedStateIsTheOracle().test_the_forbidden_writes_sit_behind_positive_controls(checks)
        TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem() \
            .test_the_unit_defining_risks_are_all_claimed(clean)

    # ---- the state set ---------------------------------------------------------------------
    def test_dropping_the_two_state_assertion_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", STATE_VOCAB), "contains", "state count: 2"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_two_state_vocabulary_is_asserted_as_a_database_check(checks)

    def test_no_longer_attempting_a_third_state_against_the_database_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", LIVE_WRITES), "contains",
            "a PENDING_RELEASE lifecycle state: refused"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_forbidden_third_states_are_attempted_against_a_live_database(checks)

    def test_losing_the_positive_controls_on_the_live_write_oracle_is_caught(self):
        """A schema that refuses EVERYTHING would pass a refusal-only battery."""
        def edit(raw):
            entry = _named(raw, "expect_state", LIVE_WRITES)
            entry["contains"] = [c for c in entry["contains"]
                                 if not c.startswith("positive control")]
        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_forbidden_writes_sit_behind_positive_controls(checks)

    def test_losing_the_releaser_less_release_refusal_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", LIVE_WRITES), "contains",
            "a RELEASED brake with no releaser: refused"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_load_bearing_release_constraints_are_database_enforced(checks)

    # ---- the platform row ------------------------------------------------------------------
    def test_letting_the_platform_row_acquire_a_tenant_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", PLATFORM_ROW), "contains",
            "platform_brake has no tenant column at all: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_platform_row_cardinality_is_structural_and_tenantless(checks)

    def test_allowing_a_second_platform_row_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", PLATFORM_ROW), "contains",
            "a second platform row at id 2: refused"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_platform_row_cardinality_is_structural_and_tenantless(checks)

    # ---- the single authority --------------------------------------------------------------
    def test_dropping_the_single_brake_authority_count_is_caught(self):
        """### THE ONE THIS UNIT CANNOT AFFORD TO LOSE."""
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", SINGLE_AUTHORITY), "contains", "brake authority count: 1"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_single_authority_is_measured_by_ast_across_the_package(mutant, checks)

    def test_losing_the_gate_minter_positive_control_is_caught(self):
        """A scan that discovered NOTHING AT ALL would read as green without it — CLAUDE.md §9."""
        def edit(raw):
            entry = _named(raw, "expect_state", SINGLE_AUTHORITY)
            entry["contains"] = [c for c in entry["contains"]
                                 if "checkpoint.py:GateEntry" not in c]
        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_single_authority_is_measured_by_ast_across_the_package(mutant, checks)

    def test_replacing_the_ast_scan_with_a_grep_is_caught(self):
        def edit(raw):
            _named(raw, "expect_state", SINGLE_AUTHORITY)["command"] = (
                '.venv/bin/python -c "print(1)"'
            )
        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_single_authority_is_measured_by_ast_across_the_package(mutant, checks)

    def test_allowing_a_second_brake_state_table_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", TABLES), "contains",
            "a second brake state table appeared: []"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_a_second_brake_state_table_is_detected_on_the_table_set(checks)

    # ---- the claim CAS and the race --------------------------------------------------------
    def test_losing_the_cas_brake_version_assertion_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", CAS), "contains",
            "the CAS revalidates brake_version in its WHERE clause: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_claim_cas_oracle_selects_the_claiming_statement_and_names_the_others(
                    mutant, checks)

    def test_reverting_the_cas_selector_to_a_bare_substring_is_caught(self):
        """### THE SAME-SUBSTRING TRAP. `EXPIRED_UNCLAIMED` contains `CLAIMED`."""
        def edit(raw):
            entry = _named(raw, "expect_state", CAS)
            entry["command"] = entry["command"].replace("SET +state", "CLAIMED-anywhere")
        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_claim_cas_oracle_selects_the_claiming_statement_and_names_the_others(
                    mutant, checks)

    def test_dropping_the_ten_thousand_interleaving_battery_is_caught(self):
        def edit(raw):
            gone = {c["name"] for c in raw["commands"]
                    if "interleaved-race-battery" in c["run"]}
            raw["commands"] = [c for c in raw["commands"]
                               if "interleaved-race-battery" not in c["run"]]
            _decite(raw, gone)
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_interleaved_race_battery_runs_at_the_canonical_order(mutant)

    def test_dropping_one_asymmetric_version_omission_is_caught(self):
        def edit(raw):
            listing = [c for c in raw["commands"] if c["run"].endswith("--list-cases")][0]
            _drop(listing, "expect_contains",
                  "a-global-only-version-check-lets-a-tenant-brake-through")
        mutant = _mutate(edit)
        listing = [c for c in mutant.commands if c.run.endswith("--list-cases")][0]
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_both_asymmetric_version_omissions_are_exercised(list(listing.expect_contains))

    # ---- the TTL measurement itself --------------------------------------------------------
    def test_reverting_the_ttl_oracle_to_a_grep_is_caught(self):
        """### THE INSTRUMENT DEFECT THIS BOOTSTRAP HIT AND REPAIRED. A grep over `brake.py` finds
        "TTL" and "expire" IN THE SENTENCE EXPLAINING THAT NEITHER EXISTS."""
        def edit(raw):
            entry = _named(raw, "expect_state", NO_TTL)
            entry["command"] = entry["command"].replace("docs.add(id(first.value))", "pass")
        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_absence_of_a_ttl_is_measured_on_the_ast_and_not_by_grep(mutant, checks)

    def test_dropping_the_identifier_half_of_the_ttl_oracle_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", NO_TTL), "contains", "  expiry identifiers: []"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_absence_of_a_ttl_is_measured_on_the_ast_and_not_by_grep(mutant, checks)

    # ---- the event scan --------------------------------------------------------------------
    def test_letting_a_dotted_call_site_label_read_as_an_event_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", EVENTS)
            entry["command"] = entry["command"].replace("s.isidentifier() and ", "")
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_event_scan_excludes_docstrings_and_dotted_call_site_labels(mutant)

    def test_dropping_the_registered_total_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", REGISTRY), "contains", "total registered contracts: 118"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_f13_family_is_exactly_four_and_the_forbidden_three_are_named(checks)

    def test_losing_the_landed_brakeengaged_consumers_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EVENTS), "contains", "M4 still consumes BrakeEngaged: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_landed_brakeengaged_consumers_are_protected_by_the_same_oracle(checks)

    # ---- retention, flapping, scope, ratchet, release evidence, R17 ------------------------
    def test_dropping_the_delete_refusal_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", RETENTION), "contains",
            "a DELETE against a brake row: refused"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_retention_is_a_database_refusal_with_a_lawful_positive_control(checks)

    def test_routing_the_retention_control_around_the_one_authority_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", RETENTION)
            entry["command"] = entry["command"].replace("BrakeStore", "RawSql")
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_retention_positive_control_goes_through_the_one_authority(mutant)

    def test_trusting_a_return_value_instead_of_counting_rows_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", FLAPPING), "contains", "ACTIVE rows for the scope: 1"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_flapping_oracle_counts_rows_rather_than_trusting_a_return_value(checks)

    def test_letting_an_unknown_scope_read_as_no_brake_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", SCOPE_GRAMMAR), "contains",
            "an unknown scope is never treated as no brake: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_scope_grammar_partitions_the_canonical_five_and_refuses_the_unknown(checks)

    def test_dropping_the_scope_partition_assertion_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", SCOPE_GRAMMAR), "contains",
            "landed and deferred partition the canonical five: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_scope_grammar_partitions_the_canonical_five_and_refuses_the_unknown(checks)

    def test_letting_automation_release_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", RATCHET)
            entry["contains"] = ["what automation may do: ['BR-1', 'BR-2', 'BR-4']"
                                 if c == "what automation may do: ['BR-1', 'BR-2']" else c
                                 for c in entry["contains"]]
        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_ratchet_is_a_declared_table_over_distinct_actor_classes(checks)

    def test_letting_a_model_touch_the_brake_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", RATCHET)
            entry["contains"] = ["what a model may do: ['BR-1']"
                                 if c == "what a model may do: []" else c
                                 for c in entry["contains"]]
        mutant = _mutate(edit)
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_ratchet_is_a_declared_table_over_distinct_actor_classes(checks)

    def test_reducing_release_to_a_human_and_a_decision_ref_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", RELEASE_EVIDENCE), "contains",
            "positive integration health is required: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_release_evidence_conditions_are_each_named(checks)

    def test_letting_a_loaded_page_count_as_a_health_proof_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", RELEASE_EVIDENCE), "contains",
            "a page that loaded is not a positive health proof: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_release_evidence_conditions_are_each_named(checks)

    def test_making_an_unknown_outcome_block_release_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", RELEASE_EVIDENCE), "contains",
            "an unresolved unknown outcome does not block release: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_unknown_outcomes_do_not_block_release_and_are_not_cleared_by_it(checks)

    def test_hiding_the_release_requirements_from_the_operator_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", R17), "contains",
            "the report names the exact release requirements: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_the_r17_report_states_everything_needed_to_release(checks)

    def test_letting_br5_produce_an_event_is_caught(self):
        mutant = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", MACHINE), "contains", "BR-5 produces no event: True"))
        checks = {c.name: list(c.contains) for c in mutant.expect_state}
        with pytest.raises(AssertionError):
            TestPersistedStateIsTheOracle() \
                .test_br_5_is_an_enumerated_non_producing_refusal_not_an_unwritten_path(checks)

    # ---- the risk map, the safety contract and the axes -------------------------------------
    def test_dropping_a_unit_defining_risk_category_is_caught(self):
        def edit(raw):
            raw["verifies"] = [v for v in raw["verifies"]
                               if v["risk_category"] != "ui_backend_disagreement"]
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem() \
                .test_the_unit_defining_risks_are_all_claimed(mutant)

    def test_losing_the_happy_path_positive_controls_is_caught(self):
        def edit(raw):
            claim = [v for v in raw["verifies"] if v["risk_category"] == "happy_path"][0]
            claim["observations"] = [o for o in claim["observations"]
                                     if not o.startswith("positive control")]
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem() \
                .test_the_happy_path_claim_names_positive_controls(mutant)

    def test_dropping_a_safety_literal_from_the_scenario_is_caught(self):
        def edit(raw):
            raw["expect_visible"] = [v for v in raw["expect_visible"]
                                     if v != "A BRAKE NEVER KILLS A WORKER"]
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheM13BaseScenario() \
                .test_the_scenario_and_the_task_state_the_same_safety_contract(mutant)

    def test_dropping_the_position_axis_is_caught(self):
        """Without it, only the half of the brake that STOPS things is ever exercised."""
        def edit(raw):
            listing = [c for c in raw["commands"] if c["run"].endswith("--list-dimensions")][0]
            listing["expect_contains"] = [d for d in listing["expect_contains"]
                                          if not d.startswith("position:4")]
        mutant = _mutate(edit)
        listing = [c for c in mutant.commands if c.run.endswith("--list-dimensions")][0]
        with pytest.raises(AssertionError):
            TestTheM13BaseScenario() \
                .test_the_probe_declares_this_units_two_own_axes(list(listing.expect_contains))

    def test_collapsing_the_actor_classes_is_caught(self):
        def edit(raw):
            listing = [c for c in raw["commands"] if c["run"].endswith("--list-dimensions")][0]
            listing["expect_contains"] = [d for d in listing["expect_contains"]
                                          if d != "actor:model"]
        mutant = _mutate(edit)
        listing = [c for c in mutant.commands if c.run.endswith("--list-dimensions")][0]
        with pytest.raises(AssertionError):
            TestTheM13BaseScenario() \
                .test_the_probe_declares_the_actor_classes_without_collapsing_them(
                    list(listing.expect_contains))

    def test_dropping_a_p3_regression_anchor_is_caught(self):
        """### M13 EDITS THE MODULE IT MEASURES. The P3 batteries are what turn red first."""
        def edit(raw):
            gone = {c["name"] for c in raw["commands"] if "test_phase3_brake.py" in c["run"]}
            raw["commands"] = [c for c in raw["commands"]
                               if "test_phase3_brake.py" not in c["run"]]
            _decite(raw, gone)
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheM13BaseScenario() \
                .test_the_scenario_runs_the_landed_p3_batteries_as_regression_anchors(mutant)

    def test_declaring_the_landed_brake_module_as_a_deliverable_is_caught(self):
        """### THE MUTATION THAT MATTERS MOST IN THIS SECTION: it is the first step of building a
        second brake authority, and it looks like housekeeping."""
        def edit(raw):
            raw["fixtures"] = list(raw["fixtures"]) + [LANDED_BRAKE]
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheM13BaseScenario() \
                .test_the_landed_brake_module_is_not_declared_as_a_deliverable(mutant)

    def test_introducing_a_control_character_into_a_command_is_caught(self):
        """The run-20260830 defect, planted deliberately."""
        def edit(raw):
            _named(raw, "expect_state", TABLES)["command"] += "\n      print('x')"
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheM13Vocabulary().test_no_shipped_command_contains_a_control_character(mutant)

    def test_forbidding_a_battery_that_ran_nothing_is_load_bearing(self):
        def edit(raw):
            raw["forbidden"] = [f for f in raw["forbidden"] if f != "no tests ran"]
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheM13BaseScenario() \
                .test_a_battery_that_ran_nothing_cannot_read_as_a_battery_that_passed(mutant)

    def test_dropping_the_p6_completion_guards_is_caught(self):
        def edit(raw):
            raw["forbidden"] = [f for f in raw["forbidden"] if f != "### P7 UNBLOCKED ###"]
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestM13IsScopedAsAUnit().test_the_scenario_asserts_the_same_thing_the_task_does(mutant)

    def test_an_orphan_expect_visible_literal_is_caught(self):
        def edit(raw):
            raw["expect_visible"] = list(raw["expect_visible"]) + ["A SENTENCE NOTHING PRODUCES"]
        mutant = _mutate(edit)
        with pytest.raises(AssertionError):
            TestTheM13BaseScenario() \
                .test_every_expect_visible_literal_is_required_by_some_check(mutant)

    # ---- the baseline attribution -----------------------------------------------------------
    def test_attributing_an_oracle_to_both_sides_is_caught(self, monkeypatch):
        """The attribution is only meaningful if it partitions. A future edit that moved an oracle
        without removing it from the other side would make the delta unmeasurable."""
        monkeypatch.setattr(
            "test_m13_readiness.P3_ALREADY_GREEN_ORACLES",
            P3_ALREADY_GREEN_ORACLES + (RETENTION,),
        )
        with pytest.raises(AssertionError):
            TestTheBaselineIsAttributedRatherThanAssumed() \
                .test_every_oracle_is_attributed_to_exactly_one_side(load_scenario(M13_PATH))

    def test_leaving_a_shipped_oracle_unattributed_is_caught(self, monkeypatch):
        monkeypatch.setattr(
            "test_m13_readiness.M13_DELTA_ORACLES",
            tuple(x for x in M13_DELTA_ORACLES if x != R17),
        )
        with pytest.raises(AssertionError):
            TestTheBaselineIsAttributedRatherThanAssumed() \
                .test_every_oracle_is_attributed_to_exactly_one_side(load_scenario(M13_PATH))

    def test_claiming_every_oracle_as_the_m13_delta_is_caught(self, monkeypatch):
        """### THE DISHONEST-BASELINE MUTATION. Claiming the whole set as the delta denies the
        landed P3 substrate and would let a run treat a P3 regression as an M13 gap."""
        everything = tuple(c.name for c in load_scenario(M13_PATH).expect_state)
        monkeypatch.setattr("test_m13_readiness.M13_DELTA_ORACLES", everything)
        with pytest.raises(AssertionError):
            TestTheBaselineIsAttributedRatherThanAssumed() \
                .test_the_delta_is_not_empty_and_is_not_everything(load_scenario(M13_PATH))

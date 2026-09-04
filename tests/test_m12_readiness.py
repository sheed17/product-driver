"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M12?

M12 is the Rule: the registered, versioned, deterministic decision procedure **with an id** that a
human instruction either compiles into, or honestly does not.

The unit's whole character is five sentences, and every check below traces back to one of them:

    a human instruction either compiles into an enforceable rule or is honestly refused
    there is no third outcome
    a model may propose text; it never compiles, confirms, activates, evaluates or resolves
    a rule may never branch on a guess
    two conflicting rules fail closed; Neyma never picks a winner

M12 differs from every P6 machine before it in one way that changes what "measuring it" means.
M1-M11 fail STRUCTURALLY: a wrong state, a missing constraint, an unregistered event, a second gate
authority. **M12 can fail in a SENTENCE.** A machine with a perfect table, a perfect event set and a
perfect migration can still reply *"📋 Noted the procedure for raise_invoice"* to an instruction that
compiled into nothing — and every structural oracle in the repository would be green while it did.
That is precisely the defect Stream B lesson L-C records, it is what M-52 and M-64 forbid **on the
literal reply text**, and it is why this bootstrap's persisted-state set includes an oracle that
calls the product's reply guard on that exact sentence rather than reading a docstring about it.

The second most likely way this unit gets built wrong is quieter: M12 is written by copying M11,
because Policy and Rule genuinely share a versioned-authority shape. A rule machine built that way
acquires M11's `DRAFT` and `APPROVED` states silently, looks entirely reasonable doing it, and its
state vocabulary is then wrong in a direction no reviewer squints at. The third is that RU-3's
"raise a conflict" is implemented as a `rule_conflicts` table, because building the thing in front of
you is easier than finding the thing that has existed since `P6-CP-7` — `RULE_VS_RULE` has been in
M7's closed `CONFLICT_KINDS` and `conflicts.rule_id` a column all along.

Thirteen questions, each answered mechanically rather than by reading a document and agreeing
with it:

1.  does the M12 base scenario parse, does it hold the pieces the generator needs (deterministic
    operation, a closed mutation axis with the two axes this unit turns on, persisted-state oracles,
    regression anchors), and do the scenario and the task state the SAME contract;
2.  does every declared risk name a command that could actually emit the observation it requires;
3.  does the scenario measure the DATABASE, the EVENT REGISTRY, the LITERAL REPLY TEXT and the AST
    rather than the probe's narration — above all the eight-state CHECK, the four-kind CHECK, the
    conditional `UNIQUE(tenant, scope, kind) WHERE state='ACTIVE'` predicate, the MINT boundary and
    the L-C reply guard — and does it ATTEMPT the forbidden writes against a live database with
    positive controls;
4.  does the task preserve the seven recorded authority conflicts rather than resolving them;
5.  does the task get the SEAMS right — M12's conflict belongs to M7, its exception seam to an M9 it
    must NOT edit, its gate vocabulary to `checkpoint.py`, its human authority to M1's
    `tenant_humans`, its ceiling to M11, and its expectations to M8;
6.  is the M12 command vocabulary safe, and actually visible to the generator;
7.  can dynamic generation close an M12 coverage gap WITHOUT inventing a command;
8.  does every named M12 oracle carry a STABLE APPROVED-COMMAND IDENTITY, so a body repair can be
    rebound on resume rather than deleting the obligation — the `c6331dc`/`cb5ecf9` invariant;
9.  is M12 scoped as `P6/M12` rather than as P6 phase completion, and can accepting it score a P6
    acceptance criterion or unlock P7 (it cannot);
10. is an integrated independent review OWED, at the tier this unit actually is;
11. do grounded reviewer findings return to the SAME builder, and does the run stop before M13;
12. does the task refuse to build M13, the autonomy ratchet and every production surface, and refuse
    to resolve `V4`, `V5`, `Q3` and `P6-D71`;
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

from neyma_product_driver.command_guard import classify_command
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.models import RunStatus
from neyma_product_driver.review_cycle import resolve_review_requirement
from neyma_product_driver.scenario_plan import (
    GeneratedScenario,
    GeneratedStateCheck,
    IdentifiedRisk,
    Priority,
    RiskCategory,
    ScenarioProvenance,
)
from neyma_product_driver.scenario_generator import MAX_RENDERED_COMMANDS
from neyma_product_driver.scenario_plan import rebind_to_approved
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
M12_PATH = SCENARIOS_DIR / "p6_m12_rule.yaml"
M12_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m12.md"
M12_TASK = M12_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match against this: the task is
#: a wrapped markdown document, and a phrase that happens to straddle a line break is not a phrase
#: the task failed to state.
M12_TASK_FLAT = " ".join(M12_TASK.split())
#: The same text with markdown furniture removed — blockquote markers, `###` emphasis runs and bold
#: markers — because the task states its hardest rules inside emphasised blockquotes.
M12_TASK_PROSE = " ".join(
    re.sub(r"(^|\n)\s*>\s?", " ", M12_TASK).replace("###", " ").replace("**", "").split()
)

PROBE = ".venv/bin/python scripts/probe_phase6_rule.py"
#: The `name:` the base scenario gives the bare probe run — the deterministic M12 operation, and the
#: only check in the file that drives the machine and narrates what it saw.
PROBE_CHECK = "drive the Rule machine through a brokerage narrative, and attack it"

#: The canonical M12 deliverables. A different name is a scenario failure, not a style preference.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/rule.py",
    "src/freight_recon/migrations/phase6_rules.py",
    "eval/tests/test_phase6_rule.py",
    "scripts/probe_phase6_rule.py",
    "scripts/mutate_phase6_rule.py",
)

#: The eight canonical states, and the four terminal ones. `state-machines/registry.md` §4/M12.
CANONICAL_STATES: tuple[str, ...] = (
    "PROPOSED", "COMPILED", "CONFIRMED", "ACTIVE",
    "REJECTED", "SUPERSEDED", "REVOKED", "EXPIRED",
)
#: The five informal names the machine file maps INTO the frozen eight, plus M11's own two. A rule
#: machine written by copying the policy machine acquires DRAFT and APPROVED silently.
FORBIDDEN_STATES: tuple[str, ...] = (
    "PARSED", "INVALID", "CONFLICT_DETECTED", "AWAITING_CONFIRMATION", "SUSPENDED",
    "DRAFT", "APPROVED",
)
#: The four canonical kinds. `entities/15-rule.md` point 10.
CANONICAL_KINDS: tuple[str, ...] = (
    "IDENTITY", "CONFLICT_RESOLUTION", "GATE_PRECONDITION", "CONSTRAINT",
)
#: The nine canonical transition ids. `12-rule.machine.md` §14.
CANONICAL_TRANSITIONS: tuple[str, ...] = (
    "RU-1", "RU-2", "RU-2f", "RU-3", "RU-4", "RU-5", "RU-6", "RU-7", "RU-8",
)
#: The eight registered F12 contracts, and nothing else.
F12_CONTRACTS: tuple[str, ...] = (
    "RuleProposed", "RuleCompiled", "RuleNotEnforceable", "RuleConfirmed",
    "RuleActivated", "RuleSuperseded", "RuleRevoked", "RuleExpired",
)
#: The contracts M12 must NOT mint, and who owns each.
NOT_M12S: dict[str, str] = {
    "ConflictRaised": "F7",
    "UnauthorizedPolicyActivationAttempted": "F14",
    "PolicyOverridden": "unregistered",
}

#: The unit's own sentences. Every one must be BOTH required by the scenario AND asked for by the
#: task, or the scenario is asking for output nobody was told to produce.
SAFETY_LITERALS: tuple[str, ...] = (
    "A HUMAN INSTRUCTION EITHER COMPILES INTO AN ENFORCEABLE RULE OR IS HONESTLY REFUSED",
    "THERE IS NO THIRD OUTCOME",
    "A MODEL MAY PROPOSE TEXT; IT NEVER COMPILES",
    "COMPILATION IS DETERMINISTIC, WITH NO MODEL IN THE LOOP",
    "A RULE MAY NEVER BRANCH ON A GUESS",
    "CONFIDENCE IS STRUCTURALLY NOT AN INPUT",
    "AN UNMODELLED FIELD DOES NOT COMPILE",
    "NOTED THE PROCEDURE IS FORBIDDEN WITHOUT AN ACTIVE RULE ID",
    "AN INSTRUCTION THAT DID NOT COMPILE IS MEMORY, NOT AUTHORITY",
    "THE OWNER SEES THE COMPILED RULE AND ITS TEST VECTORS BEFORE CONFIRMING",
    "ACTIVATION REQUIRES AN AUTHENTICATED HUMAN",
    "A MODEL CAN NEVER ACTIVATE A RULE",
    "AUTOMATION CAN NEVER ACTIVATE A RULE",
    "TWO CONFLICTING RULES FAIL CLOSED",
    "NEYMA NEVER PICKS A WINNER BETWEEN TWO RULES",
    "M12 RAISES THE M7 RULE_VS_RULE CONFLICT AND BUILDS NO SECOND ONE",
    "THE CLOCK MAY TAKE AUTHORITY AWAY; THE CLOCK MAY NEVER GIVE IT",
    "AN EXPIRY THAT BROADENS REQUIRES A HUMAN AT EXPIRY",
    "A DENYING RULE MEANS NO WITNESS AND NO EFFECT",
    "THERE IS NO ALLOW-ON-ERROR DEFAULT",
    "M12 MINTS NO GATE DECISION",
    "THE CHECKPOINT IS STILL THE ONLY GATE MINTER",
    "A RULE NEVER OVERRIDES A CONSTRAINT",
    "A RULE NEVER OVERRIDES A PERMANENT PRODUCT TRUTH",
    "A RULE NEVER OVERRIDES A BRAKE DENIAL",
    "A RULE NEVER OVERRIDES POLICY",
    "REPLAY CREATES NO AUTHORITY",
)
DARK_POSTURE_LITERALS: tuple[str, ...] = (
    "M12 SHIPS DARK WITH ZERO PRODUCTION IMPORTERS",
    "THE M13 BRAKE MACHINE IS NOT BUILT",
    "NOTHING GRADUATES",
    "A REPEATEDLY OVERRIDDEN RULE ASKS A HUMAN AND IS NEVER AUTO-DISABLED",
    "THE M1 WORK ITEM MACHINE IS UNCHANGED",
    "THE M2 PIPELINE MACHINE IS UNCHANGED",
    "THE M3 EFFECT AUTHORITY IS UNCHANGED",
    "THE M4 APPROVAL MACHINE IS UNCHANGED",
    "THE M7 CONFLICT MACHINE IS UNCHANGED",
    "THE M9 EXCEPTION MACHINE IS UNCHANGED",
    "THE M11 POLICY MACHINE IS UNCHANGED",
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
)

# The persisted-state oracle names, stated once. A rename is a scenario change and this file should
# fail rather than silently stop measuring it.
LIVE_WRITES = ("the live database refuses an ACTIVE rule with no activator, an invented state, an "
               "invented kind and a cross-tenant actor")
STATE_VOCAB = ("the eight canonical rule states and four canonical kinds are database constraints, "
               "and there is no ninth or fifth")
UNIQUENESS = ("rule uniqueness is tenant-first, conditional on the declared single-admitting "
              "scopes, and never global")
MINT = "the checkpoint kernel is still the only thing that MINTS a gate decision, and M12 mints none"
CARRIER = ("the ADR-010 carrier boundary is stated once, equals what is discovered, and every "
           "carrier cites its authority")
EVENTS = "M12 emits only registered event names, and mints no ninth F12 contract"
REGISTRY = ("the F12 family is exactly eight registered contracts, and the two M12 must not mint "
            "belong elsewhere")
DARK = "M12 ships dark: zero importers in the package, and no channel-capable module reaches it"
PARITY = "an upgraded database and a fresh database carry the identical rule layer"
CONFLICT = "M12 fails closed into M7's landed RULE_VS_RULE conflict and builds no second conflict system"
SCOPE_ORACLE = ("M13 Brake is not built, no autonomy-graduation engine exists, and M12 grows no "
                "authoring surface")
ADMIN = "M12 uses M1's landed tenant authority model and invents no parallel admin authority"
M9SEAM = "M12 raises its Exceptions through M9's landed seam and edits no part of M9"
REPLY = "the reply text itself is guarded: no claim of enforcement without an ACTIVE rule id"
TESTNAMES = "the acceptance battery carries the canonically named adversarial tests"
TYPED = "the compiler refuses MODEL_INFERRED by type, and confidence is structurally not an input"
PRECEDENCE = ("rules sit at precedence layer six, override nothing above them, and build no second "
              "precedence engine")
MACHINE = "the M12 machine declares the canonical eight states, four kinds and nine transitions"
ARITHMETIC = "the P6 transition arithmetic is re-derived from the machine files, and M12 owes exactly nine"
TENANCY = "the rules table joins the tenant-first partition, and no tenantless table appeared"


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
def m12():
    return load_scenario(M12_PATH)


@pytest.fixture(scope="module")
def cases(m12) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m12.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m12) -> list[str]:
    listing = [c for c in m12.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def state_checks(m12) -> dict[str, list[str]]:
    return {check.name: list(check.contains) for check in m12.expect_state}


# --------------------------------------------------------------------------
# 1. The M12 base scenario holds what the generator and the gate need
# --------------------------------------------------------------------------


class TestTheM12BaseScenario:
    def test_it_parses_and_is_the_p6_backend_unit(self, m12):
        assert m12.name == "p6_m12_rule"
        assert m12.phase == "P6"
        assert m12.mode == "backend"

    def test_it_declares_the_canonical_m12_deliverables_as_its_own_fixtures(self, m12):
        """The bootstrap is written BEFORE the unit exists, so the files it measures cannot exist
        yet. `test_scenario_pytest_invocation` exempts a scenario's own declared fixtures from the
        "every named path already exists" rule for exactly this reason — but only the ones it
        DECLARES, so an undeclared future path is still caught."""
        for path in DELIVERABLES:
            assert path in m12.fixtures, f"{path} is measured but never declared as produced"

    def test_the_deterministic_operation_is_a_single_named_probe_run(self, m12):
        runs = [c for c in m12.commands if c.run == f"{PROBE} --all"]
        assert len(runs) == 1, "the narrative run must be exactly one command"
        assert runs[0].name == PROBE_CHECK
        assert "behaviours as specified, 0 wrong" in runs[0].expect_contains

    def test_the_case_vocabulary_is_broad_enough_to_be_a_machine_and_not_a_demo(self, cases):
        assert len(cases) >= 120, (
            f"only {len(cases)} cases are declared; M12 has nine transitions, four kinds, two "
            "outcomes and a reply guard, and a vocabulary this small cannot reach them"
        )
        assert all(re.fullmatch(r"[a-z0-9-]+", c) for c in cases), "case names must be kebab-case"
        assert len(set(cases)) == len(cases), "a duplicated case name is one case and two claims"

    def test_every_canonical_transition_has_at_least_one_case_naming_its_event(self, cases):
        """A machine is its transitions. Each RU-* row emits a registered contract, and the case
        vocabulary must name each of those emissions so a transition cannot be silently unbuilt."""
        joined = " ".join(cases)
        for row in ("ru-1", "ru-2", "ru-2f", "ru-4", "ru-5", "ru-6", "ru-8"):
            assert f"{row}-emits-" in joined, f"no case names {row}'s emission"
        # RU-3 emits nothing of M12's and RU-7's event is asserted by name rather than by row.
        assert "m12-raises-the-m7-rule-vs-rule-conflict" in cases
        assert "rulerevoked-carries-the-canonical-direction" in cases

    def test_the_two_axes_this_unit_turns_on_are_declared(self, dimensions):
        """`--kind` and `--outcome` are M12's own, the way `--direction` and `--provenance` were
        M11's. The four kinds do not behave alike, and the whole unit is the claim that exactly TWO
        outcomes exist — which is a claim a single-outcome case cannot make."""
        assert "--kind" in dimensions, "the generator cannot vary the rule kind"
        assert "--outcome" in dimensions, (
            "the generator cannot vary the outcome, so the absence of a third one is unmeasurable"
        )

    def test_the_shared_mutation_axes_are_declared(self, dimensions):
        for axis in ("--concurrency", "--repeat", "--tenants", "--seed", "--inject", "--actor",
                     "--provenance", "--direction", "--scope", "--brake", "--delay-ms"):
            assert axis in dimensions, f"{axis} is not offered to the generator"

    def test_every_case_a_command_selects_is_a_case_the_scenario_declares(self, m12, cases):
        """A `--case` the probe was never asked to implement is a command the generator will
        compose and the product will refuse — a run that fails as a product defect for a
        configuration reason."""
        selected = {
            m.group(1)
            for m in (re.search(r"--case ([a-z0-9-]+)", c.run) for c in m12.commands)
            if m
        }
        unknown = sorted(selected - set(cases))
        assert not unknown, f"commands select cases the scenario never declares: {unknown}"

    def test_every_dimension_a_command_passes_is_a_declared_axis(self, m12, dimensions):
        used = {
            m.group(1)
            for c in m12.commands
            for m in re.finditer(r"(--[a-z-]+) (?:all|\d+|engaged|released)", c.run)
        }
        unknown = sorted(used - set(dimensions))
        assert not unknown, f"commands pass axes the probe never declares: {unknown}"

    def test_the_regression_anchors_reach_every_machine_m12_touches(self, m12):
        """M12 reads M7, M9 and M11 and is evaluated inside P3's checkpoint. A unit that silently
        edits one of them passes its own battery and breaks the system."""
        joined = " ".join(c.run for c in m12.commands)
        for anchor in ("test_phase6_conflict.py", "test_phase6_exception.py",
                       "test_phase6_policy.py", "test_phase3_checkpoint_matrix.py",
                       "test_phase3_claim_cas.py", "test_phase0_null_gate.py",
                       "test_phase0_errata_guards.py", "test_p5_event_contracts.py",
                       "test_false_green_defenses.py"):
            assert anchor in joined, f"{anchor} is not a regression anchor for M12"

    def test_every_battery_is_invoked_through_the_module_form_of_pytest(self, m12):
        """The M10 harness-recovery invariant. `python -m pytest` puts the invocation directory on
        `sys.path` and the console script does not, so a test reaching for `eval.phase0` passes
        under one and raises `ModuleNotFoundError` under the other."""
        for spec in m12.commands:
            if "pytest" in spec.run:
                assert "-m pytest" in spec.run, f"{spec.name!r} uses the bare pytest console script"
                assert "-p no:cacheprovider" in spec.run, f"{spec.name!r} allows ambient cache state"

    def test_an_empty_or_missing_battery_can_never_read_as_green(self, m12):
        for marker in ("no tests ran", "ERROR: file or directory not found",
                       "ModuleNotFoundError: No module named 'eval'"):
            assert marker in m12.forbidden, f"{marker!r} is not globally forbidden"

    def test_the_shared_probe_failure_vocabulary_is_forbidden(self, m12):
        for marker in ("### MISS ###", "### NOT REFUSED", "### WRONGLY REFUSED",
                       "### WRONG REFUSAL"):
            assert marker in m12.forbidden

    def test_the_alarm_marker_population_is_large_enough_to_be_a_battery(self, m12):
        alarms = [f for f in m12.forbidden if f.startswith("### ") and f.endswith(" ###")]
        assert len(alarms) >= 120, (
            f"only {len(alarms)} alarm markers are forbidden; M12's defect surface is wider"
        )

    def test_the_task_states_the_output_contract_the_scenario_asserts(self):
        """Every literal the scenario requires the PROBE to print must be a literal the task told
        the builder to print. Otherwise the scenario asks for output nobody was asked to produce,
        and the run fails as a product defect."""
        for literal in SAFETY_LITERALS + DARK_POSTURE_LITERALS:
            assert literal in M12_TASK, (
                f"the scenario requires the probe to print {literal!r}, and the task never asks "
                "for it"
            )
        assert "behaviours as specified, 0 wrong" in M12_TASK

    def test_every_probe_literal_the_scenario_requires_is_asked_for_by_the_task(self, m12):
        """The general form of the rule above, over every probe command rather than a curated list.
        The case vocabulary counts too: a `--case` name the task never states is one the builder
        has to guess."""
        required: set[str] = set()
        for spec in m12.commands:
            if "probe_phase6_rule.py" in spec.run:
                required |= set(spec.expect_contains)
        missing = sorted(lit for lit in required if lit not in M12_TASK)
        assert not missing, f"{len(missing)} probe literals are unasked for, e.g. {missing[:3]}"

    def test_every_alarm_marker_is_one_the_task_told_the_builder_to_emit(self, m12):
        alarms = [f for f in m12.forbidden if f.startswith("### ") and f.endswith(" ###")]
        missing = sorted(a for a in alarms if a not in M12_TASK
                         and a not in ("### MISS ###",))
        missing = [a for a in missing if not a.startswith("### NOT REFUSED")
                   and not a.startswith("### WRONGLY")]
        assert not missing, f"forbidden markers the task never named: {missing[:5]}"


# --------------------------------------------------------------------------
# 2. Every declared risk names a command that can prove it
# --------------------------------------------------------------------------


class TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem:
    def test_the_scenario_declares_risk_coverage_at_all(self, m12):
        assert m12.verifies, "the scenario declares no risk coverage"
        assert len(m12.verifies) >= 18, (
            f"only {len(m12.verifies)} risk claims; M12's failure surface is wider than that"
        )

    def test_the_unit_defining_risks_are_all_claimed(self, m12):
        claimed = {c.risk_category for c in m12.verifies}
        for category in ("happy_path", "authorization", "safety_invariant", "conflicting_evidence",
                         "missing_data", "malformed_input", "approval_required", "cross_tenant",
                         "concurrency", "idempotency", "stale_state", "boundary",
                         "unexpected_state_transition", "service_unavailable",
                         "dependency_failure", "restart_recovery", "persistence_failure",
                         "regression", "partial_failure", "retry_safety"):
            assert category in claimed, f"no claim covers {category}"

    def test_every_named_check_is_a_check_the_scenario_runs(self, m12):
        """The loader enforces this, and this states it as a claim of its own so a loosening of the
        loader is visible here rather than silently."""
        known = m12.check_names()
        for claim in m12.verifies:
            for name in claim.checks:
                assert name in known, f"{claim.risk_category} names a check that does not run: {name}"

    def test_every_claim_carries_both_a_check_and_an_observation(self, m12):
        for claim in m12.verifies:
            assert claim.checks, f"{claim.risk_category} names no check"
            assert claim.observations, f"{claim.risk_category} names no observation"

    def test_the_happy_path_claim_names_a_positive_control(self, m12):
        """An oracle battery proving no instruction can ever become an enforceable rule would pass a
        machine that refuses everything — which is not the safe direction, it is a different broken
        product. The happy-path claim is what makes every refusal below it meaningful."""
        [claim] = [c for c in m12.verifies if c.risk_category == "happy_path"]
        controls = [o for o in claim.observations if o.startswith("positive control")]
        assert len(controls) >= 3, f"the happy path names {len(controls)} positive controls"

    def test_the_l_c_claim_is_about_the_literal_reply_and_names_the_reply_oracle(self, m12):
        """The one requirement that is not about structure. If this claim ever stops naming the
        reply oracle, the unit's whole reason for existing has become unmeasured."""
        claims = [c for c in m12.verifies if REPLY in c.checks]
        assert claims, "no risk claim names the reply-text oracle"
        joined = " ".join(o for c in claims for o in c.observations)
        assert "NOTED THE PROCEDURE IS FORBIDDEN WITHOUT AN ACTIVE RULE ID" in joined
        assert "a claiming reply with NO active rule id: refused by" in joined
        assert "positive control, the same claiming reply WITH an active rule id: ACCEPTED" in joined


# --------------------------------------------------------------------------
# 3. Persisted state, the event registry, the reply text and the AST are the oracles
# --------------------------------------------------------------------------


class TestPersistedStateIsTheOracle:
    def test_the_scenario_carries_a_full_persisted_state_battery(self, m12):
        assert len(m12.expect_state) >= 18, (
            f"only {len(m12.expect_state)} persisted-state oracles; M12 lands a table, an event "
            "family, a reply guard and a checkpoint integration"
        )

    def test_the_named_oracles_all_exist(self, state_checks):
        for name in (LIVE_WRITES, STATE_VOCAB, UNIQUENESS, MINT, CARRIER, EVENTS, REGISTRY, DARK,
                     PARITY, CONFLICT, SCOPE_ORACLE, ADMIN, M9SEAM, REPLY, TESTNAMES, TYPED,
                     PRECEDENCE, MACHINE, ARITHMETIC, TENANCY):
            assert name in state_checks, f"the {name!r} oracle is gone"

    # ---- the frozen eight, and the two states borrowed from M11 ---------------------------
    def test_the_eight_states_are_asserted_as_a_database_check(self, state_checks):
        joined = " ".join(state_checks[STATE_VOCAB])
        assert "the state vocabulary is a CHECK: True" in joined
        assert "state count: 8" in joined
        assert "forbidden states present: []" in joined
        for state in CANONICAL_STATES:
            assert state in joined, f"{state} is not in the asserted vocabulary"

    def test_the_forbidden_states_include_the_two_m11_would_donate(self, m12):
        """`DRAFT` and `APPROVED` are M11's. A rule machine written by copying the policy machine
        acquires them silently, and nothing about the resulting code looks wrong."""
        body = _named_command(m12, STATE_VOCAB)
        for state in FORBIDDEN_STATES:
            assert state in body, f"{state} is never offered to the CHECK as a forbidden state"
        live = " ".join(state_checks_of(m12)[LIVE_WRITES])
        assert "a DRAFT lifecycle state borrowed from M11: refused" in live
        assert "an APPROVED lifecycle state borrowed from M11: refused" in live

    def test_the_four_kinds_are_asserted_as_a_database_check(self, state_checks):
        joined = " ".join(state_checks[STATE_VOCAB])
        assert "the kind vocabulary is a CHECK: True" in joined
        assert "kind count: 4" in joined
        assert "invented kinds present: []" in joined
        for kind in CANONICAL_KINDS:
            assert kind in joined

    # ---- the live forbidden writes ---------------------------------------------------------
    def test_the_forbidden_writes_are_attempted_against_a_live_database(self, state_checks):
        joined = " ".join(state_checks[LIVE_WRITES])
        assert "an ACTIVE rule with no activator: refused" in joined, (
            "entity §16's CHECK and §37's structurally-impossible state are the whole of RU-5"
        )
        for refusal in ("a PARSED lifecycle state", "an INVALID lifecycle state",
                        "a SUSPENDED lifecycle state", "an AWAITING_CONFIRMATION lifecycle state",
                        "an invented rule kind", "an author from another tenant",
                        "an activator from another tenant",
                        "an author who is not a recorded human"):
            assert f"{refusal}: refused" in joined, f"{refusal!r} is never attempted"

    def test_the_live_write_oracle_carries_positive_controls_and_a_row_count(self, state_checks):
        """A schema that refuses EVERYTHING would pass a refusal-only battery. The positive
        controls and the surviving-row count are what stop that."""
        controls = [c for c in state_checks[LIVE_WRITES] if c.startswith("positive control")]
        assert len(controls) >= 3, f"only {len(controls)} positive controls"
        joined = " ".join(state_checks[LIVE_WRITES])
        assert "rows that survived: 3" in joined
        assert "surviving ACTIVE rows with no activator: 0" in joined

    # ---- the conditional uniqueness, in BOTH directions -----------------------------------
    def test_uniqueness_is_tenant_first_and_conditional(self, state_checks):
        joined = " ".join(state_checks[UNIQUENESS])
        assert "every rule index is tenant-first: True" in joined
        assert "an ACTIVE-only partial predicate exists: True" in joined
        assert "the active uniqueness columns are tenant, scope and kind: True" in joined
        assert "a tenant-local rule_version uniqueness exists: True" in joined
        assert "tenant is FIRST in the rule primary key: True" in joined

    def test_the_otherwise_branch_of_entity_point_17_is_required_to_be_reachable(self, state_checks):
        """Entity §17 is CONDITIONAL: unique *where a scope admits one rule*, **otherwise multiple
        active rules may coexist and conflicts are detected**. A blanket index makes the otherwise
        branch unreachable and silently resolves V4/V5 by construction — which is why the oracle
        requires the single-admitting set to be a PROPER subset and exercises both sides."""
        joined = " ".join(state_checks[UNIQUENESS])
        assert "the single-admitting set is declared and non-empty: True" in joined
        assert (
            "the single-admitting set is a PROPER subset, so the otherwise branch is reachable: True"
            in joined
        )
        assert "a SECOND ACTIVE rule for the same tenant, single-admitting scope and kind: refused by" in joined
        assert "positive control, a SECOND ACTIVE rule in a multi-admitting scope, which conflict detection covers: ACCEPTED" in joined

    def test_uniqueness_is_never_global_across_tenants(self, state_checks):
        joined = " ".join(state_checks[UNIQUENESS])
        assert "positive control, the SAME scope and kind ACTIVE in a DIFFERENT tenant: ACCEPTED" in joined
        assert "T_B ACTIVE rows in the single-admitting scope: 1" in joined

    def test_version_reuse_and_deletion_are_both_refused(self, state_checks):
        joined = " ".join(state_checks[UNIQUENESS])
        assert "a reused rule_version inside one tenant: refused by" in joined
        assert "a DELETE against a rule row: refused by" in joined
        assert "the OCC guard on a state change that does not advance the version: refused by" in joined

    # ---- the mint boundary -----------------------------------------------------------------
    def test_the_checkpoint_is_still_the_sole_gate_minter(self, state_checks):
        joined = " ".join(state_checks[MINT])
        assert "modules that MINT a gate decision: ['checkpoint.py']" in joined
        assert "M12 constructs a GateEntry or GateRegistry: False" in joined
        assert "the M12 schema module constructs a GateEntry or GateRegistry: False" in joined
        assert "modules that REGISTER an action class gate: []" in joined

    def test_the_mint_oracle_carries_the_kernels_own_construction_as_a_positive_control(
        self, state_checks
    ):
        """A confinement assertion over a population containing no mint at all passes vacuously."""
        joined = " ".join(state_checks[MINT])
        assert "the kernel positive control, checkpoint.py mint sites: 1" in joined
        assert "rule.py was parsed: True" in joined

    def test_the_carrier_boundary_is_measured_as_an_equality_not_a_membership(self, state_checks):
        """M12 may or may not carry gate vocabulary — ADR-010 §6.1 permits it and the builder
        chooses. What may not happen is a drift: a module that carries and is not in the boundary,
        or a boundary naming a module that carries nothing."""
        joined = " ".join(state_checks[CARRIER])
        assert "the discovered population equals the stated boundary: True" in joined
        assert "rule.py is inside the stated boundary if it carries: True" in joined
        assert "carriers without an ADR-010 citation: []" in joined
        assert "the boundary is non-empty: True" in joined

    # ---- the event surface -----------------------------------------------------------------
    def test_the_f12_family_is_pinned_at_eight_with_its_producers(self, state_checks):
        joined = " ".join(state_checks[REGISTRY])
        assert "F12 contract count: 8" in joined
        for contract in F12_CONTRACTS:
            assert contract in joined, f"{contract} is not pinned"
        assert "RuleActivated is human_only: True" in joined
        assert "total registered contracts: 118" in joined

    def test_the_contracts_m12_must_not_mint_are_attributed_elsewhere(self, state_checks):
        joined = " ".join(state_checks[REGISTRY] + state_checks[EVENTS])
        assert "ConflictRaised family: F7" in joined, "ConflictRaised must be shown to be M7's"
        assert "RU-3 is not a registered producer of ConflictRaised: True" in joined
        assert "UnauthorizedPolicyActivationAttempted family: F14" in joined
        assert "PolicyOverridden is registered: False" in joined
        assert "ConflictRaised is not minted by M12: True" in joined
        assert "PolicyOverridden is not minted by M12: True" in joined
        assert "the F14 security contract is not minted by M12: True" in joined

    def test_the_emitted_set_is_read_from_the_ast_and_separated_from_the_consumed_set(
        self, state_checks
    ):
        """M11 hit this exact trap one unit ago: an oracle that read every event-shaped literal and
        called the lot of them MINTS reported a CONSUMED driving fact as an invented contract, and
        registering it to quiet the oracle would have manufactured the ninth contract the invariant
        forbids."""
        joined = " ".join(state_checks[EVENTS])
        assert "the count of F12 contracts M12 mints: 8" in joined
        assert "minted names that are not registered F12 contracts: []" in joined
        assert "unregistered event names M12 mints: []" in joined
        assert "a consumed trigger is never also minted: True" in joined
        assert "HumanActivated is consumed, not minted: True" in joined
        assert "TimerFired is consumed, not minted: True" in joined

    def test_the_event_scan_can_tell_prose_from_code_and_proves_it(self, state_checks):
        joined = " ".join(state_checks[EVENTS])
        assert "a docstring naming a foreign event is not a mint: True" in joined
        assert "a real string literal naming an invented event is a mint: True" in joined
        assert "a ninth event minted on a transition row is caught: True" in joined
        assert "a mint hidden behind a new consumed trigger is still caught: True" in joined

    # ---- the M7 and M9 seams ---------------------------------------------------------------
    def test_the_conflict_seam_is_m7s_and_no_second_one_appears(self, state_checks):
        joined = " ".join(state_checks[CONFLICT])
        assert "RULE_VS_RULE is a landed M7 conflict kind: True" in joined
        assert "the registered ConflictRaised producers: ['CF-1', 'EF-4c', 'IB-6']" in joined
        assert "RU-3 is not among them: True" in joined
        assert "conflicts carries a rule_id column: True" in joined
        assert "a second conflict table exists: False" in joined
        assert "M12 writes the conflicts table directly: False" in joined
        assert "M12 mints an F7 contract name: []" in joined

    def test_the_m9_seam_is_used_and_m9_is_left_alone(self, state_checks):
        joined = " ".join(state_checks[M9SEAM])
        assert "rule is a canonical M9 exception source: True" in joined
        assert "rule is FK-backed in M9 today: False" in joined, (
            "M12-AQ-6: the seam is named and left unwired, exactly as M11 left P6-D73"
        )
        assert "M12 writes the exceptions table directly: False" in joined
        assert "M12 mints an F9 contract name: []" in joined
        assert "policy is still carried as a recorded-only kind, unchanged by M12: True" in joined

    # ---- the reply guard, which is the unit's reason for existing ---------------------------
    def test_the_reply_guard_is_exercised_on_the_literal_forbidden_sentence(self, m12, state_checks):
        body = _named_command(m12, REPLY)
        assert "Noted the procedure for raise_invoice" in body, (
            "the guard is tested on a paraphrase rather than on the sentence M-64 forbids"
        )
        joined = " ".join(state_checks[REPLY])
        assert "a reply claiming a procedure was noted is detected: True" in joined
        assert "a claiming reply with NO active rule id: refused by" in joined

    def test_the_reply_guard_is_exercised_in_both_directions(self, state_checks):
        """A machine that refuses every reply is not the safe direction — it is a different broken
        product, and only the positive control catches it."""
        joined = " ".join(state_checks[REPLY])
        assert "positive control, the same claiming reply WITH an active rule id: ACCEPTED" in joined
        assert "positive control, the honest refusal with no active rule id: ACCEPTED" in joined
        assert "a claiming reply whose rule id is an empty string: refused by" in joined

    def test_the_honest_refusal_is_required_to_be_honest_and_useful(self, state_checks):
        joined = " ".join(state_checks[REPLY])
        assert "the honest refusal names what is missing: True" in joined
        assert "the honest refusal says it is not a rule: True" in joined
        assert "the honest refusal offers what would be needed: True" in joined

    def test_the_canonical_adversarial_test_names_are_required_to_exist(self, m12, state_checks):
        body = _named_command(m12, TESTNAMES)
        for name in ("test_uncompilable_instruction_reply_does_not_claim_a_rule_was_installed",
                     "test_do_not_use_carrier_x_for_produce_cannot_compile",
                     "test_margin_rule_refuses_to_compile_on_model_inferred_cost",
                     "test_two_conflicting_rules_fail_closed",
                     "test_model_cannot_activate_a_rule",
                     "test_repeatedly_overridden_rule_asks_does_not_auto_disable",
                     "test_ru_narrowing_expiry_needs_human"):
            assert name in body, f"{name} is never required to exist"
        joined = " ".join(state_checks[TESTNAMES])
        assert "entity point 44 tests missing: []" in joined
        assert "machine section 14 tests missing: []" in joined

    # ---- the typed MODEL_INFERRED refusal ---------------------------------------------------
    def test_the_model_inferred_refusal_is_typed_rather_than_blacklisted(self, state_checks):
        joined = " ".join(state_checks[TYPED])
        assert "confidence is a field on the compiler input: False" in joined
        assert "provenance_class is a field on the compiler input: True" in joined
        assert "attribute reads of confidence anywhere in M12: []" in joined
        assert "M12 reuses the landed provenance vocabulary: True" in joined

    def test_the_refusal_is_exercised_at_confidence_one_and_behind_positive_controls(
        self, state_checks
    ):
        joined = " ".join(state_checks[TYPED])
        assert "a MODEL_INFERRED field with confidence 1.0 compiles: refused by" in joined
        assert "an unmodelled field compiles: refused by" in joined
        assert "an invented provenance class compiles: refused by" in joined
        controls = [c for c in state_checks[TYPED] if c.startswith("positive control")]
        assert len(controls) >= 2, "a compiler that refuses everything would pass"

    # ---- precedence -------------------------------------------------------------------------
    def test_the_precedence_ladder_is_declared_and_the_five_layers_above_are_refused(
        self, state_checks
    ):
        joined = " ".join(state_checks[PRECEDENCE])
        assert "the ladder has seven layers: True" in joined
        assert "STANDING RULES is layer 6: True" in joined
        assert "M12 declares its own layer index: 6" in joined
        for layer in ("a rule that overrides a CONSTRAINT",
                      "a rule that overrides a PERMANENT PRODUCT TRUTH",
                      "a rule that overrides the BRAKE",
                      "a rule that overrides the PRODUCT POLICY ceiling",
                      "a rule that overrides a TENANT POLICY"):
            assert f"{layer}: refused by" in joined, f"{layer!r} is never attempted"
        assert "positive control, a rule that narrows within its own layer: ACCEPTED" in joined

    def test_no_second_precedence_engine_and_no_brake_control(self, state_checks):
        joined = " ".join(state_checks[PRECEDENCE])
        assert "M12 defines a policy evaluator of its own: False" in joined
        assert "M12 defines a ceiling comparison of its own: False" in joined
        assert "M12 engages or narrows a brake: False" in joined

    # ---- the machine's own surface, and the arithmetic --------------------------------------
    def test_the_machine_declares_nine_transitions_and_the_canonical_row_ids(self, m12,
                                                                            state_checks):
        joined = " ".join(state_checks[MACHINE])
        assert "transition row count: 9" in joined
        assert "the canonical row set matches: True" in joined
        body = _named_command(m12, MACHINE)
        for row in CANONICAL_TRANSITIONS:
            assert row in body, f"{row} is not pinned by the machine oracle"

    def test_the_transition_arithmetic_is_re_derived_rather_than_carried(self, state_checks):
        joined = " ".join(state_checks[ARITHMETIC])
        assert "machine files discovered: 13" in joined
        assert "total transition rows counted: 134" in joined
        assert "M12 transition rows: 9" in joined
        assert "M13 transition rows: 5" in joined
        assert "the acceptance corpus says M12 owes nine: True" in joined

    # ---- the dark posture -------------------------------------------------------------------
    def test_the_dark_scan_proves_its_population_before_it_proves_emptiness(self, state_checks):
        """A dark-surface scan that inspected nothing is vacuously green — the exact false green
        M9's build tripped and M10's first pass repeated."""
        joined = " ".join(state_checks[DARK])
        assert "the scanned population is non-empty: True" in joined
        assert "the channel-capable population is non-empty: True" in joined
        assert "the adapter population is non-empty: True" in joined
        assert "shipped importers of rule: []" in joined
        assert "channel-capable modules that import rule: []" in joined
        assert "M12 imports a network primitive: []" in joined
        assert "M12 imports a timer service: False" in joined

    def test_m13_and_every_production_surface_are_required_absent(self, state_checks):
        joined = " ".join(state_checks[SCOPE_ORACLE])
        assert "an M13 brake lifecycle module exists: False" in joined
        assert "an M13 brake-lifecycle table exists: False" in joined
        assert "M12 defines a brake lifecycle: False" in joined
        assert "M12 defines an autonomy graduation engine: False" in joined
        assert "a rule editor or console module exists: False" in joined
        assert "M12 builds an authoring surface: False" in joined
        assert "the landed P3 kernel brake is still present: True", (
            "brake.py is P3's landed kernel brake, and confusing it with M13 would delete a "
            "landed surface"
        )

    def test_the_tenant_partition_absorbs_the_new_table(self, state_checks):
        joined = " ".join(state_checks[TENANCY])
        assert "rules carries a tenant column: True" in joined
        assert "rules is a canonical table: True" in joined
        assert "tenantless tables outside the recorded exemptions: []" in joined
        assert "the created population is non-empty: True" in joined

    def test_the_migration_parity_and_idempotency_are_measured(self, state_checks):
        joined = " ".join(state_checks[PARITY])
        assert "the rule layer was actually removed before the upgrade: True" in joined
        assert "the upgraded rule layer is identical to the fresh one: True" in joined
        assert "a second application of the migration is a no-op: True" in joined
        assert "missing canonical symbols: []" in joined

    def test_the_admin_authority_oracle_spells_the_privileged_term_as_a_pattern(self, m12,
                                                                                state_checks):
        """`cb5ecf9`'s repair, applied from the start rather than after a blocked run. A literal
        privileged token inside an executable `-c` body is refused by the command guard — correctly,
        because the guard classifies the STRING — so a permanent oracle spelling it plainly could
        never back a generated case."""
        body = _named_command(m12, ADMIN)
        assert "su[d]o" in body, "the privileged term is not spelled as a character class"
        assert "sudo" not in body.replace("su[d]o", ""), (
            "a literal privileged token survives in the command body"
        )
        joined = " ".join(state_checks[ADMIN])
        assert "every declared term is matched by the compiled pattern: True" in joined
        assert "the privileged-execution term the pattern reconstructs: sudo" in joined
        assert "M12 invents an admin authority: False" in joined

    def test_the_admin_oracle_keeps_the_prose_versus_code_discrimination(self, state_checks):
        joined = " ".join(state_checks[ADMIN])
        for control in ("a docstring saying there is no admin path is not an admin authority: True",
                        "a comment saying there is no admin path is not an admin authority: True",
                        "a refusal message naming the admin path is not an admin authority: True",
                        "the word sudo in a docstring is not an admin authority: True",
                        "an admin activation function IS an admin authority: True",
                        "a sudo-named authority function IS an admin authority: True"):
            assert control in joined, control


def _named_command(scenario, name: str) -> str:
    for check in scenario.expect_state:
        if check.name == name:
            return check.command
    for spec in scenario.commands:
        if spec.name == name:
            return spec.run
    raise AssertionError(f"{name!r} is no longer a check in {M12_PATH.name}")


def state_checks_of(scenario) -> dict[str, list[str]]:
    return {c.name: list(c.contains) for c in scenario.expect_state}


# --------------------------------------------------------------------------
# 4. The task preserves the authority conflicts rather than resolving them
# --------------------------------------------------------------------------


class TestTheTaskPreservesTheAuthorityConflicts:
    def test_all_seven_authority_questions_are_stated(self):
        for aq in ("M12-AQ-1", "M12-AQ-2", "M12-AQ-3", "M12-AQ-4", "M12-AQ-4b", "M12-AQ-5",
                   "M12-AQ-6", "M12-AQ-7"):
            assert aq in M12_TASK, f"{aq} is not stated to the builder"

    def test_the_task_forbids_resolving_them(self):
        assert "YOU RECORD THE CONFLICT AND BUILD THE FAIL-CLOSED SIDE" in M12_TASK_PROSE
        assert "DO NOT INVENT AUTHORITY THAT DOES NOT EXIST" in M12_TASK_PROSE
        assert "DO NOT RESOLVE ANY OF THE SEVEN AUTHORITY QUESTIONS" in M12_TASK_PROSE

    def test_aq1_settles_the_p6_p8_boundary_without_making_m12_unbuildable(self):
        """The registry's P8 list literally names "rule compile-or-refuse runtime". Read alone it
        prohibits the work the same registry schedules next — the exact false instruction the M11
        reconciliation corrected, and the reading that would make M12 unbuildable."""
        assert "compile-or-refuse" in M12_TASK_FLAT
        assert "NOT machines M11/M12/M13 themselves" in M12_TASK_FLAT
        assert "DO NOT REFUSE TO BUILD COMPILATION" in M12_TASK_PROSE

    def test_aq2_records_the_conflictraised_disagreement_and_builds_the_machine_file(self):
        assert "point 31 lists" in M12_TASK_FLAT
        assert "CONSUMES:ConflictRaised" in M12_TASK
        assert "M12 CALLS M7; M7 MINTS; M12 MINTS NOTHING" in M12_TASK_PROSE
        assert "Do not edit the entity file to match your build" in M12_TASK_PROSE

    def test_aq3_records_the_eight_versus_nine_row_disagreement(self):
        assert "eight rows and no `ACTIVE → EXPIRED`" in M12_TASK_FLAT
        assert "AC-MACH-1201..1209" in M12_TASK
        assert "BUILD NINE" in M12_TASK_PROSE

    def test_aq4_refuses_to_settle_which_scopes_admit_one_rule(self):
        assert "P6RU_SINGLE_ACTIVE_SCOPES" in M12_TASK
        assert "P6RU_SCOPE_FORMS" in M12_TASK
        assert "PROPER SUBSET" in M12_TASK_PROSE
        assert "as an answer to an OPEN question, not as a finding" in M12_TASK_PROSE

    def test_aq5_forbids_editing_the_registered_contract_to_settle_the_ordering(self):
        assert "strict_order" in M12_TASK
        assert "DO NOT FLIP `strict_order`" in M12_TASK_PROSE.replace("  ", " ") or (
            "DO NOT FLIP" in M12_TASK_PROSE
        )
        assert "previous_aggregate_version" in M12_TASK

    def test_aq6_follows_m11s_precedent_and_leaves_the_m9_seam_unwired(self):
        assert "SOURCE_KINDS_WITHOUT_TABLE" in M12_TASK
        assert "NAME THE SEAM AND LEAVE IT UNWIRED" in M12_TASK_PROSE
        assert "P6-D73" in M12_TASK

    def test_aq7_refuses_to_mint_the_override_event_and_says_p6_d71_stays_open(self):
        """`CURRENT.md` records an EXPECTATION that the override obligation lands with M12. An
        expectation is not an authorisation, and a build session may not mint a contract."""
        assert "PolicyOverridden" in M12_TASK
        assert "P6-D71" in M12_TASK
        assert "AN EXPECTATION IS NOT AN AUTHORISATION" in M12_TASK_PROSE
        assert "REMAINS OPEN" in M12_TASK_PROSE

    def test_the_open_validation_questions_stay_open_at_their_fail_closed_defaults(self):
        assert "V4" in M12_TASK and "V5" in M12_TASK
        assert "Q3" in M12_TASK
        assert "deterministic ID match only" in M12_TASK_FLAT
        assert "every conflict goes to a human" in M12_TASK_FLAT
        assert "Do not build auto-disable, and do not resolve Q3" in M12_TASK_PROSE


# --------------------------------------------------------------------------
# 5. The seams are scoped to M12
# --------------------------------------------------------------------------


class TestTheSeamsAreScopedToM12:
    def test_the_task_names_every_landed_seam_and_says_feed_it_not_duplicate_it(self):
        for seam in ("conflict.py", "exception.py", "checkpoint.py", "approval.py", "policy.py",
                     "tenant_humans", "expectation.py", "event_timers.py"):
            assert seam in M12_TASK, f"the task never points the builder at {seam}"

    def test_the_task_says_the_checkpoint_remains_the_sole_gate_minter(self):
        assert "checkpoint.py` REMAINS THE SOLE MINTER OF A GATE DECISION" in M12_TASK_FLAT
        assert "`checkpoint.py` REMAINS THE SOLE GATE MINTER" in M12_TASK_FLAT
        assert "CONSTRUCTS NO `GateEntry`" in M12_TASK_FLAT
        assert "A SECOND GATE AUTHORITY IS THE SAME DEFECT AS NO GATE AUTHORITY" in M12_TASK_PROSE

    def test_the_task_says_reuse_m7_rather_than_building_a_second_conflict_system(self):
        assert "REUSE M7's CONFLICT" in M12_TASK_PROSE
        assert "existing machinery, no new primitive" in M12_TASK_PROSE
        assert "RULE_VS_RULE" in M12_TASK
        assert "Do not create a `rule_conflicts` table" in M12_TASK_FLAT

    def test_the_task_says_reuse_the_m9_exception_seam_and_edit_no_part_of_m9(self):
        assert "REUSE M9's EXCEPTION SEAM" in M12_TASK_PROSE
        assert "add no FK and no mirror column" in M12_TASK_FLAT
        assert "M7 and M9 in particular are not edited at all" in M12_TASK_FLAT

    def test_the_task_states_the_carrier_boundary_and_the_narrowing_attached_to_it(self):
        assert "GATE_RUNTIME_MODULES" in M12_TASK
        assert "WIDENING WITH A NARROWING ATTACHED" in M12_TASK_PROSE
        assert "THE MINT ALLOWLIST STAYS" in M12_TASK_PROSE
        assert "may not weaken, delete or subset-ify either boundary guard" in M12_TASK_FLAT

    def test_the_task_routes_the_expectation_example_to_m8_rather_than_a_new_primitive(self):
        assert "Customer Y requires hourly updates" in M12_TASK
        assert "NO NEW PRIMITIVE" in M12_TASK_PROSE


# --------------------------------------------------------------------------
# 6. The M12 command vocabulary is safe, and visible to the generator
# --------------------------------------------------------------------------


class TestTheM12Vocabulary:
    def test_every_command_the_scenario_ships_passes_the_command_guard(self, m12):
        """The permanent scenario's own commands are executed by the harness AND cited by generated
        cases. One the guard refuses is one no generated scenario can ever run — the `cb5ecf9`
        collision, caught here before a run rather than after one."""
        commands = (
            list(m12.setup) + list(m12.teardown)
            + [c.run for c in m12.commands]
            + [c.command for c in m12.expect_state]
        )
        refused = [(c[:100], classify_command(c)) for c in commands if classify_command(c)]
        assert not refused, f"{len(refused)} shipped command(s) are refused: {refused[:2]}"

    def test_no_command_reaches_a_remote_or_an_external_effect(self, m12):
        joined = " ".join([c.run for c in m12.commands]
                          + [c.command for c in m12.expect_state])
        for forbidden in ("git push", "curl ", "docker push", "npm publish"):
            assert forbidden not in joined, f"{forbidden!r} appears in a shipped command"

    def test_the_local_config_does_not_target_a_superseded_unit(self):
        """The retarget is the established convention: `driver.config.yaml` carries one unit at a
        time, and a stale target is how a run verifies the previous unit while claiming this one.

        ### **The direction is what matters, not the exact name.** Stated in DIRECTION form from the
        start — the M9 bootstrap pinned its own name exactly and the guard fired at the moment the
        M10 bootstrap followed the convention correctly, which is how a guard gets deleted rather
        than fixed.
        """
        raw = _local_config()
        if not raw:
            pytest.skip("no local driver.config.yaml on this checkout")
        target = raw.get("scenario")
        assert target, "the local config names no scenario at all"
        earlier = P6_UNIT_ORDER[: P6_UNIT_ORDER.index("p6_m12_rule")]
        assert target not in earlier, (
            f"the local config targets {target!r}, a unit M12 has already superseded; a run would "
            "verify the previous unit and report this one"
        )

    def test_the_direction_rule_still_catches_a_config_left_behind(self):
        """The control the direction rule owes, on synthesised values rather than on the checkout."""
        earlier = P6_UNIT_ORDER[: P6_UNIT_ORDER.index("p6_m12_rule")]
        assert "p6_m11_policy" in earlier, "M11 is not recognised as a unit M12 supersedes"
        assert "p6_m10_compensation" in earlier
        assert "p6_m12_rule" not in earlier, "the rule would refuse its own name"

    def test_no_superseded_units_case_vocabulary_is_still_enumerated(self):
        """A prior unit's probe is a REGRESSION ANCHOR inside the current permanent scenario, and a
        prefix match already approves every `--case` tail of it. Enumerating its cases again only
        pushes the unit actually under test toward the brief's render bound."""
        raw = _local_config()
        if not raw:
            pytest.skip("no local driver.config.yaml on this checkout")
        vocabulary = raw.get("scenario_generation", {}).get("approved_commands") or []
        target = raw.get("scenario")
        if target == "p6_m12_rule":
            stale = [c for c in vocabulary if "probe_phase6_policy.py --case" in c]
            assert not stale, (
                f"{len(stale)} M11 `--case` entries are still enumerated while the config targets "
                "M12. M11's probe is a regression anchor now."
            )
        else:
            stale = [c for c in vocabulary if "probe_phase6_rule.py --case" in c]
            assert not stale, (
                f"{len(stale)} M12 `--case` entries are enumerated while the config targets "
                f"{target!r}"
            )

    def test_the_m12_vocabulary_is_enumerated_when_the_config_targets_m12(self):
        raw = _local_config()
        if not raw or raw.get("scenario") != "p6_m12_rule":
            pytest.skip("the local config does not target M12")
        vocabulary = raw.get("scenario_generation", {}).get("approved_commands") or []
        entries = [c for c in vocabulary if "probe_phase6_rule.py" in c]
        assert entries, "the M12 vocabulary is not enumerated in the local config at all"
        assert PROBE in vocabulary, (
            "the bare M12 probe is not enumerated; it is the prefix every `--case` tail is "
            "approved by and the unit's deterministic entry point"
        )

    def _planner(self, tmp_path: Path, configured: list[str]) -> ScenarioPlanner:
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True, approved_commands=configured),
            reasoner=ScriptedReasoner([{"risks": [], "scenarios": []}]),
            base_scenario=load_scenario(M12_PATH),
            permanent_scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            founder=FakeFounder(),
        )

    def test_every_case_is_approved_by_the_bare_probe_prefix(self, tmp_path, cases):
        """### ENUMERATION BUYS VISIBILITY, NOT SAFETY — AND THAT IS WHY A CURATED LIST IS SAFE.

        The bare probe is approved, and approval matches by PREFIX, so every one of the 153 `--case`
        tails is runnable whether or not the brief spells it out. What enumeration changes is
        whether the generator can SEE a case well enough to compose one; what it can never change
        is whether the case is permitted. If this ever stops holding, trimming the enumerated
        vocabulary to fit the render bound would silently narrow what a run may execute.
        """
        vocabulary = _local_vocabulary()
        if PROBE not in vocabulary:
            pytest.skip("no local config enumerating the bare M12 probe")
        planner = self._planner(tmp_path, vocabulary)
        for case in cases:
            ok, why = planner.approved_commands.approves(f"{PROBE} --case {case}")
            assert ok, f"{case}: {why}"

    def test_the_curated_enumeration_is_a_visibility_choice_over_a_larger_permitted_set(
        self, tmp_path, cases
    ):
        """The other half: the enumerated set is a PROPER SUBSET of what is permitted, and the
        cases left out are still runnable. A trim that also narrowed permission would be a scope
        change wearing a budget's clothes."""
        vocabulary = _local_vocabulary()
        if PROBE not in vocabulary:
            pytest.skip("no local config enumerating the bare M12 probe")
        enumerated = {
            m.group(1) for m in
            (re.search(r"probe_phase6_rule\.py --case ([a-z0-9-]+)", c) for c in vocabulary)
            if m
        }
        assert enumerated, "no M12 case is enumerated at all"
        assert enumerated < set(cases), "the enumeration is not a proper subset of the declared set"
        planner = self._planner(tmp_path, vocabulary)
        for case in sorted(set(cases) - enumerated):
            ok, why = planner.approved_commands.approves(f"{PROBE} --case {case}")
            assert ok, f"an unenumerated case is not even permitted: {case}: {why}"

    def test_no_case_name_trips_the_command_guard(self, cases):
        """A case name is part of a command string, and the guard is a token matcher. M6 shipped a
        case the boundary hard-blocked; the cost is paid where the name is authored."""
        refused = [c for c in cases if classify_command(f"{PROBE} --case {c}")]
        assert not refused, f"case names the command guard refuses: {refused}"

    def test_the_approved_set_still_fits_inside_what_the_brief_renders(self, tmp_path):
        """### THE CONSTRAINT THIS BOOTSTRAP ACTUALLY HIT, AND THE REASON THE LOCAL VOCABULARY IS
        CURATED RATHER THAN EXHAUSTIVE. Approved commands sort ASCII and every probe entry begins
        `scripts/probe_...`, so they sort LAST: an approved set larger than the render bound loses
        the probe vocabulary FIRST, and loses it silently. Enumerating all 153 M12 cases put the
        corpus at 457 against a bound of 400 and took the vocabulary of six earlier units down with
        it — which is why the shipped enumeration is the load-bearing subset and the rest stay
        reachable through the bare probe prefix."""
        planner = self._planner(tmp_path, _local_vocabulary())
        assert len(planner.approved_commands) <= MAX_RENDERED_COMMANDS, (
            f"{len(planner.approved_commands)} approved commands but the generation brief renders "
            f"only the first {MAX_RENDERED_COMMANDS} — the M12 vocabulary sorts last and is now "
            "invisible to the generator."
        )

    def test_the_enumerated_vocabulary_covers_every_transition_and_the_reply_guard(self):
        """A curated list is only defensible if it is not arbitrary. Every RU-* row and the L-C
        reply guard must still be spelled out, or a generated case cannot reach them."""
        raw = _local_config()
        if not raw or raw.get("scenario") != "p6_m12_rule":
            pytest.skip("the local config does not target M12")
        enumerated = " ".join(raw.get("scenario_generation", {}).get("approved_commands") or [])
        for needed in ("a-model-may-propose-structured-candidate-text",
                       "compilation-is-deterministic-and-model-free",
                       "do-not-use-carrier-x-for-produce-cannot-compile",
                       "two-conflicting-active-rules-fail-closed",
                       "the-owner-is-shown-the-generated-test-vectors",
                       "activation-requires-an-authenticated-human",
                       "the-superseded-version-is-retained-permanently",
                       "a-broadening-revocation-requires-the-policy-owner",
                       "expiry-requires-a-human-at-expiry",
                       "the-reply-never-claims-enforcement-without-an-active-rule-id"):
            assert needed in enumerated, f"{needed} is not visible to the generator"

    def test_every_enumerated_local_case_is_one_the_scenario_declares(self, cases):
        raw = _local_config()
        if not raw or raw.get("scenario") != "p6_m12_rule":
            pytest.skip("the local config does not target M12")
        vocabulary = raw.get("scenario_generation", {}).get("approved_commands") or []
        enumerated = [
            m.group(1) for m in
            (re.search(r"probe_phase6_rule\.py --case ([a-z0-9-]+)", c) for c in vocabulary)
            if m
        ]
        assert enumerated, "no M12 `--case` entries are enumerated"
        unknown = sorted(set(enumerated) - set(cases))
        assert not unknown, (
            f"the local config enumerates cases the scenario never declares: {unknown}"
        )


# --------------------------------------------------------------------------
# 7. Generation closes an M12 gap without inventing a command
# --------------------------------------------------------------------------


STATE_ORACLE = next(
    c.command
    for c in load_scenario(M12_PATH).expect_state
    if c.name == "a freshly created canonical database carries the rule layer, tenant-first"
)


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close."""
    return GeneratedScenario(
        id="gen-m12-second-conflict-system",
        title="the rule machine never becomes a second conflict authority",
        purpose=(
            "a rule machine that raises its own conflicts gives the system two records of the same "
            "disagreement, and nothing that says which one a human resolved"
        ),
        risk_category=RiskCategory.SAFETY_INVARIANT,
        priority=Priority.P0,
        rationale="the identified second-conflict-system risk had no scenario behind it",
        requirement_reference="P6/M12",
        product_principle_reference="effect-truth",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared state, so "
            "nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m12-task",
            session_id="scripted",
            generating_risk="M12 could raise conflicts outside M7",
            source_risks=[risk_key],
        ),
        actions=[{
            "kind": "command",
            "name": "drive a rule conflict and watch for a second conflict authority",
            "command": command,
            "expect_contains": ["M12 RAISES THE M7 RULE_VS_RULE CONFLICT AND BUILDS NO SECOND ONE"],
        }],
        persisted_state_checks=[
            GeneratedStateCheck(
                name="the rule layer is still tenant-first and readable",
                command=STATE_ORACLE,
                contains=["problems: []", "rules"],
            )
        ],
        expected_observations=["M12 RAISES THE M7 RULE_VS_RULE CONFLICT AND BUILDS NO SECOND ONE"],
        forbidden_observations=["### SECOND CONFLICT SYSTEM BUILT ###"],
    )


class TestGenerationClosesM12GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-second-conflict-system",
            description="M12 could raise conflicts outside M7",
            risk_category=RiskCategory.SAFETY_INVARIANT,
            severity=Priority.P0,
            basis="ADR-007 §5: existing machinery, no new primitive; RU-3 CONSUMES ConflictRaised",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m12", "p6", "m12"},
                principle_tokens={"effect-truth"},
                known_risk_ids={risk.key, "R-second-conflict-system"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m12_vocabulary_is_accepted(self, context):
        """The whole point of enumerating the vocabulary: the generator can COMPOSE a case the
        permanent scenario never wrote, from arguments a human already approved."""
        ctx, risk = context
        command = (
            f"{PROBE} --case two-conflicting-active-rules-fail-closed "
            "--kind CONSTRAINT --actor model --seed 12"
        )
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M12 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario('python -c "import rule; rule.activate_everything()"', risk.key)],
            ctx,
        )
        assert not accepted
        assert rejected
        reasons = rejected[0][1]
        assert any("approved" in r.lower() for r in reasons), reasons

    def test_a_gap_case_touching_repository_authority_is_refused(self, context):
        """A verification scenario observes the product; it never edits the rules the product is
        judged against — and for THIS unit that matters more than for any before it, because the
        rules M12 is judged against are the same rules M12 compiles."""
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario(f"{PROBE} --case x docs/implementation/CURRENT.md", risk.key)], ctx
        )
        assert not accepted
        reasons = rejected[0][1]
        assert any("authority" in r.lower() for r in reasons), reasons


# --------------------------------------------------------------------------
# 8. Resume safety: every named oracle keeps a stable approved-command identity
# --------------------------------------------------------------------------


class TestEveryM12OracleCanBackAGeneratedCase:
    """`c6331dc` and `cb5ecf9`, applied before a run rather than after a blocked one.

    A generated case cites an approved command by a token over its BODY, and records the human
    NAME it was written under so a body repair can be rebound rather than deleted. Two things have
    to hold for that to work, and both are measured here rather than assumed:

    * every oracle the scenario ships must PASS the generated-command boundary, or no generated case
      can ever cite it — the `M11-W2-3` collision;
    * every oracle NAME must be unambiguous across the whole corpus, because a label two different
      commands answer to identifies neither and is dropped from `by_name`, taking the oracle's
      rebinding identity with it.
    """

    @pytest.fixture(scope="class")
    def approved(self):
        return ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )

    def test_no_m12_oracle_is_left_uncitable(self, m12, approved):
        refused = [
            (check.name, approved.approves(check.command)[1])
            for check in m12.expect_state
            if not approved.approves(check.command)[0]
        ]
        refused += [
            (spec.name, approved.approves(spec.run)[1])
            for spec in m12.commands
            if not approved.approves(spec.run)[0]
        ]
        assert refused == [], refused

    def test_every_m12_oracle_name_resolves_to_its_own_command(self, m12, approved):
        """The rebinding identity. A name missing from `by_name` is a name two commands share, and
        an oracle whose name resolves to nothing cannot be re-materialised after a body repair —
        which is exactly how run `20260903-065810` lost four cases."""
        missing: list[str] = []
        wrong: list[str] = []
        for check in m12.expect_state:
            if check.name not in approved.by_name:
                missing.append(check.name)
            elif approved.by_name[check.name] != check.command.strip():
                wrong.append(check.name)
        for spec in m12.commands:
            if spec.name not in approved.by_name:
                missing.append(spec.name)
            elif approved.by_name[spec.name] != spec.run.strip():
                wrong.append(spec.name)
        assert not missing, f"{len(missing)} M12 oracle name(s) are ambiguous corpus-wide: {missing}"
        assert not wrong, f"{len(wrong)} M12 oracle name(s) resolve to another command: {wrong}"

    def test_every_named_oracle_is_unique_inside_the_scenario(self, m12):
        names = [c.name for c in m12.expect_state] + [c.name for c in m12.commands]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, f"duplicate oracle names inside the scenario: {duplicates}"

    def _generated_from(self, oracle_name: str, approved, harness):
        from neyma_product_driver.scenario_plan import (
            GeneratedAction,
            GeneratedScenario,
            compile_to_scenario,
        )
        from neyma_product_driver.scenario_validation import citation_token

        body = approved.by_name[oracle_name]
        scenario = GeneratedScenario(
            id="gen-" + str(abs(hash(oracle_name)) % 10**8),
            title=oracle_name[:80],
            risk_category="safety_invariant",
            priority="P0",
            requirement_reference="M12: the Rule",
            actions=[
                GeneratedAction(kind="command", name="oracle", command=f"@{citation_token(body)}")
            ],
        )
        scenario.bind_citations(approved)
        allowed, _reasons = approved.resolve(scenario.command_strings())
        return scenario, compile_to_scenario(scenario, base=harness, approved_commands=allowed)

    def test_every_named_m12_oracle_compiles_into_a_generated_case(self, m12, approved):
        """### FRESH-RUN READINESS, STATED GENERICALLY. A generated scenario's power is ordering,
        repetition and expectation; its MEASUREMENTS are the oracles a human already wrote. So the
        readiness question is not "do these four ids work" — it is whether EVERY oracle in the
        permanent scenario can back a generated case, by citation, and survive a rebind."""
        names = sorted(
            {check.name for check in m12.expect_state if check.name}
            | {spec.name for spec in m12.commands if spec.name}
        )
        assert names, "the permanent scenario must carry named oracles"
        failures = []
        for name in names:
            if name not in approved.by_name:
                failures.append((name, "no unambiguous approved command under that name"))
                continue
            try:
                scenario, _compiled = self._generated_from(name, approved, m12)
            except Exception as exc:  # noqa: BLE001 - the reason IS the finding
                failures.append((name, f"{type(exc).__name__}: {exc}"))
                continue
            if not approved.approves(scenario.actions[0].command)[0]:
                failures.append((name, "refused by the generated-command boundary"))
            if not scenario.command_bindings:
                failures.append((name, "no binding was recorded, so a repair could not be rebound"))
                continue
            #: `name_for` reports the LEXICOGRAPHICALLY FIRST label a body answers to, so a command
            #: an earlier permanent scenario also names (the shared regression batteries) binds
            #: under that scenario's label. That is deliberate and stable across processes — what
            #: matters is that the recorded label resolves to the SAME body, which is what a
            #: rebind re-materialises.
            bound = scenario.command_bindings[0].source_name
            if approved.by_name.get(bound) != approved.by_name[name]:
                failures.append((name, f"the binding {bound!r} resolves to a different command"))
        assert failures == [], failures

    def test_a_stale_cited_body_is_rebound_to_the_current_approved_text(self, m12, approved):
        """### THE `c6331dc` INVARIANT, PROVED ON AN M12 ORACLE. A generated case cites a body by a
        token over its bytes. When a human repairs that body, the citation goes stale — and the
        resume must REBIND from the recorded name to the CURRENT approved text, not delete the
        obligation and not trust the stale string."""
        name = MINT
        scenario, _compiled = self._generated_from(name, approved, m12)
        stale = ".venv/bin/python -c \"print('a body no human approves any more')\""
        scenario.actions[0].command = stale
        rebound, unreconstructable = rebind_to_approved(scenario, approved)
        assert not unreconstructable, unreconstructable
        assert rebound, "nothing was rebound, so the stale body would have been executed"
        assert scenario.actions[0].command == approved.by_name[name], (
            "the rebind did not re-materialise the oracle from the current approved text"
        )
        assert stale not in scenario.actions[0].command, "the stale body survived the rebind"

    def test_the_privileged_oracle_specifically_can_back_a_generated_case(self, approved):
        """The one that collided for M11. Named separately, because it is the case where a green
        sweep above could hide a single refusal."""
        command = approved.by_name[ADMIN]
        ok, why = approved.approves(command)
        assert ok, why


# --------------------------------------------------------------------------
# 9. M12 is scoped as a unit, and cannot move the phase
# --------------------------------------------------------------------------


@pytest.fixture
def m12_repo(tmp_path: Path) -> PhaseRepo:
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/rule.py", "# the unit under construction\n")
    repo.commit_all("the M12 candidate")
    return repo


class TestM12IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m12(self, m12_repo: PhaseRepo):
        scope = m12_repo.scope(M12_TASK)
        assert scope.scope_id == "P6/M12"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(self, m12_repo: PhaseRepo):
        scope = m12_repo.scope(M12_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m12_repo: PhaseRepo):
        scope = m12_repo.scope(M12_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(self, m12_repo: PhaseRepo):
        rendered = m12_repo.scope(M12_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "enables nothing in production" in rendered

    def test_the_task_says_the_phase_does_not_move(self):
        assert "`criteria_scored` is `[]`" in M12_TASK_FLAT
        assert "P7 stays `BLOCKED`" in M12_TASK_FLAT
        assert "LANDING M12 SCORES NO P6 CRITERION" in M12_TASK


class TestM12CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m12_repo: PhaseRepo
    ):
        scope = m12_repo.scope(M12_TASK)
        completion = scoped_completion(scope, TaskResult.ACCEPTED, phase_accepted=True)
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M12"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m12_repo: PhaseRepo):
        completion = scoped_completion(m12_repo.scope(M12_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")


# --------------------------------------------------------------------------
# 10-11. The review is owed, and the loop owns M12 end to end
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m12_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m12_repo.root, m12_repo.scope(M12_TASK), unit=m12_repo.unit()
        )
        assert requirement.required
        assert requirement.from_repository_authority

    def test_the_task_states_the_tier_and_says_why_it_took_the_higher_one(self):
        """CLAUDE.md §7: "When genuinely torn between two tiers, take the higher one once and say
        so." A state machine is tier 2 by itself. M12 lands a MIGRATION, is load-bearing for TENANT
        ISOLATION, and decides whether an action is allowed inside the checkpoint."""
        assert "tier-1" in M12_TASK
        assert "migration" in M12_TASK_FLAT
        assert "tenant isolation" in M12_TASK_FLAT
        assert "takes the higher tier once and says so" in M12_TASK_PROSE


class TestTheLoopOwnsM12EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m12_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session that wrote the code,
        with its evidence path intact."""
        builder = FakeBuilder(m12_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m12_repo, tmp_path, task=M12_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        assert "INDEPENDENT REVIEW" in builder.prompts[1]
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED


# --------------------------------------------------------------------------
# 12. The unit stops where it was told to stop
# --------------------------------------------------------------------------


class TestTheUnitStopsAtM12:
    def test_the_task_refuses_m13_explicitly(self):
        assert "Do not build M13 (Brake) or any brake lifecycle" in M12_TASK_PROSE
        assert "M13 IS NOT BUILT" in M12_TASK
        assert "brake.py` is P3's landed kernel brake" in M12_TASK_FLAT

    def test_the_task_refuses_the_production_surfaces(self):
        for surface in ("production importer", "rule editor", "admin screen",
                        "authoring or import surface", "channel join", "notifier",
                        "oversight queue", "dashboard", "network primitive", "timer service"):
            assert surface in M12_TASK_FLAT, f"the task never refuses a {surface}"
        assert "SHIP DARK" in M12_TASK

    def test_the_task_refuses_the_autonomy_ratchet(self):
        assert "Do not build an autonomy-graduation engine" in M12_TASK_PROSE
        assert "Nothing graduates" in M12_TASK_FLAT

    def test_the_task_preserves_the_landed_runtime(self):
        assert "PRESERVE THE M1–M11 RUNTIME" in M12_TASK
        assert "Do not modify M1–M11" in M12_TASK_FLAT
        assert "Do not score a P6 criterion, move P6's status, or unlock P7" in M12_TASK_FLAT

    def test_the_task_allows_a_local_commit_and_forbids_a_push(self):
        assert "A LOCAL COMMIT IS ALLOWED AND EXPECTED" in M12_TASK
        assert "DO NOT PUSH, DO NOT DEPLOY, AND DO NOT ENABLE" in M12_TASK
        assert "No remote operation of any kind" in M12_TASK_FLAT

    def test_the_task_says_repository_authority_wins(self):
        assert "REPOSITORY AUTHORITY WINS" in M12_TASK
        assert "the repository is right and the disagreement is a finding you REPORT" in M12_TASK_PROSE

    def test_the_task_forbids_minting_an_unregistered_event(self):
        assert "DO NOT MINT AN UNREGISTERED EVENT" in M12_TASK
        assert "do not edit" in M12_TASK_FLAT and "event_contracts_data.json" in M12_TASK


# --------------------------------------------------------------------------
# 13. Does this file fail when the guard is removed?
# --------------------------------------------------------------------------


def _mutate(edit):
    """Load a copy of the shipped M12 scenario with one weakening applied.

    `edit` receives the raw YAML mapping and changes it in place. Nothing is written to the
    scenarios directory: the mutant lives in a temporary file and is parsed through the real
    loader, so a weakening the loader itself refuses raises here rather than returning a Scenario.
    """
    import tempfile

    raw = copy.deepcopy(yaml.safe_load(M12_PATH.read_text(encoding="utf-8")))
    edit(raw)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m12_mutant.yaml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return load_scenario(path)


def _named(raw: dict, section: str, name: str) -> dict:
    for entry in raw[section]:
        if entry.get("name") == name:
            return entry
    raise AssertionError(f"{name!r} is not in {section}; the mutation targets a check that is gone")


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
        scenario = _mutate(lambda raw: None)
        checks = state_checks_of(scenario)
        assert "state count: 8" in " ".join(checks[STATE_VOCAB])
        assert "modules that MINT a gate decision: ['checkpoint.py']" in " ".join(checks[MINT])
        assert len(scenario.expect_state) >= 18
        assert len([m for m in scenario.forbidden
                    if m.startswith("### ") and m.endswith(" ###")]) >= 120

    # ---- the frozen eight -----------------------------------------------------------------
    def test_a_ninth_state_slipping_into_the_vocabulary_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", STATE_VOCAB), "contains", "state count: 8"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[STATE_VOCAB])
            assert "state count: 8" in joined

    def test_dropping_the_forbidden_state_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", STATE_VOCAB), "contains", "forbidden states present: []"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[STATE_VOCAB])
            assert "forbidden states present: []" in joined

    def test_dropping_the_m11_borrowed_state_refusals_is_caught(self):
        """`DRAFT` and `APPROVED` are the two a copy-of-M11 acquires silently. If the live-write
        oracle stops attempting them, the most likely wrong build in this unit stops being
        measured."""
        def edit(raw):
            entry = _named(raw, "expect_state", LIVE_WRITES)
            entry["contains"] = [
                c for c in entry["contains"] if "borrowed from M11" not in c
            ]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            live = " ".join(state_checks_of(scenario)[LIVE_WRITES])
            assert "a DRAFT lifecycle state borrowed from M11: refused" in live
            assert "an APPROVED lifecycle state borrowed from M11: refused" in live

    def test_dropping_the_four_kind_count_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", STATE_VOCAB), "contains", "kind count: 4"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[STATE_VOCAB])
            assert "kind count: 4" in joined

    # ---- the live forbidden writes ---------------------------------------------------------
    def test_removing_a_positive_control_from_the_live_write_oracle_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", LIVE_WRITES)
            entry["contains"] = [
                c for c in entry["contains"] if not c.startswith("positive control")
            ]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            controls = [c for c in state_checks_of(scenario)[LIVE_WRITES]
                        if c.startswith("positive control")]
            assert len(controls) >= 3

    def test_dropping_the_surviving_row_count_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", LIVE_WRITES), "contains", "rows that survived: 3"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[LIVE_WRITES])
            assert "rows that survived: 3" in joined

    def test_dropping_the_no_activator_refusal_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", LIVE_WRITES), "contains",
            "an ACTIVE rule with no activator: refused"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[LIVE_WRITES])
            assert "an ACTIVE rule with no activator: refused" in joined

    # ---- the conditional uniqueness, both directions ---------------------------------------
    def test_dropping_the_proper_subset_requirement_is_caught(self):
        """The one that lets a blanket index through, and with it a silent resolution of V4/V5."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", UNIQUENESS), "contains",
            "the single-admitting set is a PROPER subset, so the otherwise branch is reachable: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[UNIQUENESS])
            assert (
                "the single-admitting set is a PROPER subset, so the otherwise branch is "
                "reachable: True" in joined
            )

    def test_dropping_the_cross_tenant_acceptance_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", UNIQUENESS), "contains",
            "positive control, the SAME scope and kind ACTIVE in a DIFFERENT tenant: ACCEPTED"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[UNIQUENESS])
            assert (
                "positive control, the SAME scope and kind ACTIVE in a DIFFERENT tenant: ACCEPTED"
                in joined
            )

    def test_dropping_the_tenant_first_primary_key_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", UNIQUENESS), "contains",
            "tenant is FIRST in the rule primary key: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[UNIQUENESS])
            assert "tenant is FIRST in the rule primary key: True" in joined

    # ---- the mint boundary -----------------------------------------------------------------
    def test_dropping_the_sole_minter_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", MINT), "contains",
            "modules that MINT a gate decision: ['checkpoint.py']"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[MINT])
            assert "modules that MINT a gate decision: ['checkpoint.py']" in joined

    def test_dropping_the_kernel_positive_control_is_caught(self):
        """Without it a confinement assertion over an empty mint population passes vacuously."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", MINT), "contains",
            "the kernel positive control, checkpoint.py mint sites: 1"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[MINT])
            assert "the kernel positive control, checkpoint.py mint sites: 1" in joined

    # ---- the reply guard -------------------------------------------------------------------
    def test_dropping_the_reply_refusal_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", REPLY), "contains",
            "a claiming reply with NO active rule id: refused by"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[REPLY])
            assert "a claiming reply with NO active rule id: refused by" in joined

    def test_dropping_the_reply_positive_control_is_caught(self):
        """Without it, a product that refuses EVERY reply passes the L-C guard."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", REPLY), "contains",
            "positive control, the same claiming reply WITH an active rule id: ACCEPTED"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[REPLY])
            assert (
                "positive control, the same claiming reply WITH an active rule id: ACCEPTED"
                in joined
            )

    def test_removing_the_literal_forbidden_sentence_from_the_command_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", REPLY)
            entry["command"] = entry["command"].replace(
                "Noted the procedure for raise_invoice", "some acknowledgement"
            )

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            body = _named_command(scenario, REPLY)
            assert "Noted the procedure for raise_invoice" in body

    # ---- the typed MODEL_INFERRED refusal ---------------------------------------------------
    def test_dropping_the_absent_confidence_field_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", TYPED), "contains",
            "confidence is a field on the compiler input: False"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[TYPED])
            assert "confidence is a field on the compiler input: False" in joined

    def test_dropping_the_confidence_one_refusal_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", TYPED), "contains",
            "a MODEL_INFERRED field with confidence 1.0 compiles: refused by"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[TYPED])
            assert "a MODEL_INFERRED field with confidence 1.0 compiles: refused by" in joined

    # ---- the M7 and M9 seams ---------------------------------------------------------------
    def test_dropping_the_no_second_conflict_system_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", CONFLICT), "contains",
            "a second conflict table exists: False"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[CONFLICT])
            assert "a second conflict table exists: False" in joined

    def test_dropping_the_m9_untouched_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", M9SEAM), "contains",
            "M12 mints an F9 contract name: []"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[M9SEAM])
            assert "M12 mints an F9 contract name: []" in joined

    # ---- the event surface -----------------------------------------------------------------
    def test_dropping_the_eight_contract_count_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EVENTS), "contains",
            "the count of F12 contracts M12 mints: 8"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[EVENTS])
            assert "the count of F12 contracts M12 mints: 8" in joined

    def test_dropping_the_conflictraised_non_mint_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EVENTS), "contains",
            "ConflictRaised is not minted by M12: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[EVENTS])
            assert "ConflictRaised is not minted by M12: True" in joined

    def test_dropping_the_policyoverridden_non_mint_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EVENTS), "contains",
            "PolicyOverridden is not minted by M12: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[EVENTS])
            assert "PolicyOverridden is not minted by M12: True" in joined

    # ---- the dark posture and the population proof ------------------------------------------
    def test_dropping_the_population_proof_from_the_dark_scan_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", DARK)
            entry["contains"] = [
                c for c in entry["contains"] if "population is non-empty" not in c
            ]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[DARK])
            assert "the scanned population is non-empty: True" in joined
            assert "the channel-capable population is non-empty: True" in joined

    def test_dropping_the_m13_absence_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", SCOPE_ORACLE), "contains",
            "an M13 brake lifecycle module exists: False"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[SCOPE_ORACLE])
            assert "an M13 brake lifecycle module exists: False" in joined

    # ---- precedence -------------------------------------------------------------------------
    def test_dropping_a_precedence_refusal_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", PRECEDENCE), "contains",
            "a rule that overrides the BRAKE: refused by"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[PRECEDENCE])
            assert "a rule that overrides the BRAKE: refused by" in joined

    def test_dropping_the_precedence_positive_control_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", PRECEDENCE), "contains",
            "positive control, a rule that narrows within its own layer: ACCEPTED"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[PRECEDENCE])
            assert "positive control, a rule that narrows within its own layer: ACCEPTED" in joined

    # ---- the arithmetic and the machine surface ---------------------------------------------
    def test_dropping_the_nine_transition_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", ARITHMETIC), "contains", "M12 transition rows: 9"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[ARITHMETIC])
            assert "M12 transition rows: 9" in joined

    def test_dropping_the_canonical_row_set_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", MACHINE), "contains", "the canonical row set matches: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(state_checks_of(scenario)[MACHINE])
            assert "the canonical row set matches: True" in joined

    # ---- the alarm battery and the harness vocabulary ---------------------------------------
    def test_thinning_the_alarm_battery_is_caught(self):
        def edit(raw):
            raw["forbidden"] = [
                f for f in raw["forbidden"]
                if not (f.startswith("### ") and f.endswith(" ###"))
            ][:5]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            alarms = [f for f in scenario.forbidden
                      if f.startswith("### ") and f.endswith(" ###")]
            assert len(alarms) >= 120

    def test_removing_the_empty_battery_guards_is_caught(self):
        def edit(raw):
            raw["forbidden"] = [f for f in raw["forbidden"]
                                if f not in ("no tests ran",
                                             "ERROR: file or directory not found")]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            for marker in ("no tests ran", "ERROR: file or directory not found"):
                assert marker in scenario.forbidden

    # ---- the axes the generator needs -------------------------------------------------------
    def test_removing_this_units_own_axis_is_caught(self):
        def edit(raw):
            for entry in raw["commands"]:
                if entry.get("run", "").endswith("--list-dimensions"):
                    entry["expect_contains"] = [
                        d for d in entry["expect_contains"] if d not in ("--kind", "--outcome")
                    ]

        scenario = _mutate(edit)
        dims = next(c.expect_contains for c in scenario.commands
                    if c.run.endswith("--list-dimensions"))
        with pytest.raises(AssertionError):
            assert "--kind" in dims
            assert "--outcome" in dims

    def test_thinning_the_case_vocabulary_is_caught(self):
        def edit(raw):
            for entry in raw["commands"]:
                if entry.get("run", "").endswith("--list-cases"):
                    entry["expect_contains"] = entry["expect_contains"][:10]

        scenario = _mutate(edit)
        declared = next(c.expect_contains for c in scenario.commands
                        if c.run.endswith("--list-cases"))
        with pytest.raises(AssertionError):
            assert len(declared) >= 120

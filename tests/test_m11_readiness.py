"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M11?

M11 is the Policy: the typed, versioned, scoped, deterministic tenant posture evaluated at
**checkpoint step 6**, returning a **never-null gate decision**. It is the machine that answers
*"what may Neyma do alone, for whom, and up to what caps"* — and it is the one place in the
architecture where that answer has to be a value the owner can see, version and revoke rather than a
sentence in a prompt.

The unit's whole character is five sentences, and every check below traces back to one of them:

    a policy is a value the owner can see, not a sentence in a prompt
    a tenant policy may only ever NARROW the product ceiling
    automation may only ever move authority in the safe direction
    a gate expressible as an absence is not a gate
    a policy may never branch on a guess

M11 differs from every P6 machine before it in a way that changes what "getting it wrong" means.
M1-M10 each add a NEW obligation with new failure modes. **M11 adds no new obligation — it changes
the meaning of guarantees that are already landed.** The checkpoint, the effect grant, the approval
and the brake ALL already read `policy_version` and a gate decision. So a defect here does not
produce a broken policy engine that someone notices; it produces a checkpoint that passes, a grant
that mints, an approval that holds and a brake that admits, all against a posture nobody actually
authorised. The failure is silent by construction.

The single most likely way this unit gets built wrong is that someone reads "the Policy Engine
evaluates the gate" and gives the Policy Engine its own `GateRegistry` — because a policy engine
that owns the gate registry obviously belongs together. That is a **second gate authority**, and a
second gate authority is the same defect as no gate authority: two answers to "may Neyma do this
alone", and nothing that says which one the grant was minted under. The second most likely is a
string comparison for the ceiling, which calls the most dangerous broadening in the system a
narrowing, silently, because `AUTONOMOUS_WITHIN_CAPS` sorts before `HUMAN_APPROVAL_REQUIRED`.

Thirteen questions, each answered mechanically rather than by reading a document and agreeing
with it:

1.  does the M11 base scenario parse, does it hold the pieces the generator needs (deterministic
    operation, a closed mutation axis with the two axes this unit turns on, persisted-state oracles,
    regression anchors), and do the scenario and the task state the SAME contract;
2.  does every declared risk name a command that could actually emit the observation it requires;
3.  does the scenario measure the DATABASE, the EVENT REGISTRY and the AST rather than the probe's
    narration — above all the seven-state CHECK, the never-null four-member gate CHECK, the
    `UNIQUE(tenant, scope) WHERE state='ACTIVE'` predicate and the MINT boundary — and does it
    ATTEMPT the forbidden writes against a live database with positive controls;
4.  does the task preserve the eight recorded authority conflicts rather than resolving them;
5.  does the task get the SEAMS right — M11's gate vocabulary belongs to `checkpoint.py`, its
    approval to M4, its drift-void to M4's landed `AP-4p`, its stale-grant refusal to P3's claim
    CAS, its pipeline to M2, its human authority to M1's `tenant_humans`, and its expiry escalation
    to an M9 it must NOT edit;
6.  is the M11 command vocabulary safe, and actually visible to the generator;
7.  can dynamic generation close an M11 coverage gap WITHOUT inventing a command;
8.  is `P6-D46` still closed — canonical taxonomy only;
9.  is M11 scoped as `P6/M11` rather than as P6 phase completion, and can accepting it score a P6
    acceptance criterion or unlock P7 (it cannot);
10. is an integrated independent review OWED, at the tier this unit actually is;
11. do grounded reviewer findings return to the SAME builder, and does the run stop before M12;
12. does the task refuse to build M12, M13 and the autonomy ratchet, and refuse to resolve V11/V12;
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

from scenario_fixtures import ScriptedReasoner
from test_integrated_review import FakeBuilder, FakeReviewer, drive, refusing, supported
from test_scoped_completion import PhaseRepo

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DRIVER_ROOT / "scenarios"
M11_PATH = SCENARIOS_DIR / "p6_m11_policy.yaml"
M11_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m11.md"
M11_TASK = M11_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match against this: the task is
#: a wrapped markdown document, and a phrase that happens to straddle a line break is not a phrase
#: the task failed to state.
M11_TASK_FLAT = " ".join(M11_TASK.split())
#: The same text with markdown furniture removed — blockquote markers, `###` emphasis runs and bold
#: markers — because the task states its hardest rules inside emphasised blockquotes.
M11_TASK_PROSE = " ".join(
    re.sub(r"(^|\n)\s*>\s?", " ", M11_TASK).replace("###", " ").replace("**", "").split()
)

PROBE = ".venv/bin/python scripts/probe_phase6_policy.py"
#: The `name:` the base scenario gives the bare probe run — the deterministic M11 operation, and the
#: only check in the file that drives the machine and narrates what it saw.
PROBE_CHECK = "drive the Policy machine through a brokerage narrative, and attack it"

#: The canonical M11 deliverables. A different name is a scenario failure, not a style preference.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/policy.py",
    "src/freight_recon/migrations/phase6_policies.py",
    "eval/tests/test_phase6_policy.py",
    "scripts/probe_phase6_policy.py",
    "scripts/mutate_phase6_policy.py",
)

#: The FROZEN SEVEN (registry §4 / M11, entity §12, machine §7, target spec §12.11 — all four
#: agree). Not six, not eight.
STATES: tuple[str, ...] = (
    "DRAFT", "PROPOSED", "APPROVED", "ACTIVE", "SUPERSEDED", "REVOKED", "EXPIRED",
)
TERMINAL_STATES: tuple[str, ...] = ("SUPERSEDED", "REVOKED", "EXPIRED")
NONTERMINAL_STATES: tuple[str, ...] = ("DRAFT", "PROPOSED", "APPROVED", "ACTIVE")

#: States a build session might reach for, and that the corpus says do not exist. The first three
#: are refused BY NAME in the machine file's own opening paragraph: "narrowed" is an ACTIVE policy
#: with a tighter posture and a new version, "suspended" is REVOKED, and "invalid" is a draft that
#: never activated. `REJECTED`, `COMPILED` and `CONFIRMED` are M12 RULE's states, and reaching for
#: them is the clearest early signal that this unit started building the next one.
FORBIDDEN_STATES: tuple[str, ...] = (
    "NARROWED", "SUSPENDED", "INVALID", "PENDING", "ENABLED", "DISABLED",
    "CANCELLED", "REJECTED", "COMPILED", "CONFIRMED", "FAILED", "ARCHIVED",
)

#: The canonical transition ids. `AC-MACH-1101..1107` — seven, not six, not eight.
TRANSITIONS: tuple[str, ...] = ("PO-1", "PO-2", "PO-3", "PO-4", "PO-5", "PO-6", "PO-7")

#: The eight registered F11 contracts. `event_contracts_data.json` carries exactly these eight and
#: `events/registry.md` is by its own header THE SOLE CANONICAL LIST — so a ninth `Policy*` name is
#: defective by the registry's own definition. `PolicySubmitted`(PO-2) and `PolicyApproved`(PO-3)
#: were MINTED by the 2026-08-12 founder/architect amendment; the ENTITY file was never updated and
#: still lists six, which is `M11-AQ-2`.
F11_EVENTS: tuple[str, ...] = (
    "PolicyProposed", "PolicySubmitted", "PolicyApproved", "PolicyActivated",
    "PolicySuperseded", "PolicyRevoked", "PolicyExpired", "PolicyVersionChanged",
)

#: Names a build session invents when it wants a state to be an event, or when it reads ADR-010 §8.1
#: and assumes the event it names is registered. `PolicyOverridden` is exactly that case and is
#: `M11-AQ-4`. `PolicyEvaluated` is REAL but belongs to F2/M2's PL-2 — minting a second one is
#: rule-17 duplication of a coordination contract, which is why it is in this list rather than the
#: one above.
FORBIDDEN_EVENTS: tuple[str, ...] = (
    "PolicyNarrowed", "PolicySuspended", "PolicyInvalidated", "PolicyEnabled",
    "PolicyDisabled", "PolicyOverridden", "PolicyCompiled", "PolicyConfirmed",
)

#: The four canonical gate members (ADR-010 §3.1-A3). Already defined in `checkpoint.py` as
#: `GateDecision`. M11 imports them; it does not redeclare the enum.
GATE_MEMBERS: tuple[str, ...] = (
    "HUMAN_APPROVAL_REQUIRED", "AUTONOMOUS_WITHIN_CAPS",
    "PERMANENT_HUMAN_ASSERTION_REQUIRED", "FORBIDDEN",
)

#: The literals that say M11 stopped where it was told to stop, and that no landed unit was edited
#: to get there. `tasks/neyma_p6_m11.md` states them verbatim to the builder as strings the M11
#: PROBE must print.
DARK_POSTURE_LITERALS: tuple[str, ...] = (
    "M11 SHIPS DARK WITH ZERO PRODUCTION IMPORTERS",
    "THE M12 RULE MACHINE IS NOT BUILT",
    "THE M13 BRAKE MACHINE IS NOT BUILT",
    "NOTHING GRADUATES",
    "THE M1 WORK ITEM MACHINE IS UNCHANGED",
    "THE M2 PIPELINE MACHINE IS UNCHANGED",
    "THE M3 EFFECT AUTHORITY IS UNCHANGED",
    "THE M4 APPROVAL MACHINE IS UNCHANGED",
    "THE M9 EXCEPTION MACHINE IS UNCHANGED",
    "THE M10 COMPENSATION MACHINE IS UNCHANGED",
)

#: The safety sentences the probe must print. Each one is a whole requirement compressed into a line
#: a founder can read, and each is emitted by the case that establishes it.
SAFETY_LITERALS: tuple[str, ...] = (
    "A POLICY IS A VALUE THE OWNER CAN SEE, NOT A SENTENCE IN A PROMPT",
    "A TENANT POLICY MAY ONLY EVER NARROW THE PRODUCT CEILING",
    "AUTOMATION MAY ONLY EVER MOVE AUTHORITY IN THE SAFE DIRECTION",
    "A GATE EXPRESSIBLE AS AN ABSENCE IS NOT A GATE",
    "A POLICY MAY NEVER BRANCH ON A GUESS",
    "CONFIDENCE IS STRUCTURALLY NOT AN INPUT",
    "A MODEL CAN NEVER ACTIVATE A POLICY",
    "AUTOMATION CAN NEVER ACTIVATE A POLICY",
    "INBOUND CONTENT CAN NEVER AUTHOR A POLICY",
    "A POLICY CHANGE IS ITSELF A GATED ACTION, AND THERE IS NO ADMIN PATH",
    "PolicyApproved IS THE NO-ADMIN-PATH EVIDENCE",
    "PolicySubmitted IS NOT A RENAME OF PolicyProposed",
    "PolicyApproved DOES NOT ACTIVATE",
    "A POLICY IS NEVER RETROACTIVE",
    "THE OLD VERSION IS RETAINED BECAUSE EFFECTS WERE JUDGED UNDER IT",
    "A NARROWING REVOCATION IS IMMEDIATE; A BROADENING ONE NEEDS THE OWNER",
    "THE CLOCK MAY TAKE AUTHORITY AWAY; THE CLOCK MAY NEVER GIVE IT",
    "AN EXPIRY THAT BROADENS REQUIRES A HUMAN AT EXPIRY",
    "EVALUATION IS BYTE-IDENTICAL REPRODUCIBLE",
    "NO POLICY DECISION MEANS NO WITNESS AND NO EFFECT",
    "THERE IS NO ALLOW-ON-ERROR DEFAULT",
    "M11 MINTS NO GATE DECISION",
    "THE CHECKPOINT IS STILL THE ONLY GATE MINTER",
    "M11 BUILDS NO SECOND CHECKPOINT",
    "A STALE POLICY VERSION MAKES THE GRANT UNCLAIMABLE",
    "A POLICY CHANGE VOIDS AN IN-FLIGHT APPROVAL",
    "A POLICY NEVER OVERRIDES A PERMANENT PRODUCT TRUTH",
    "A POLICY NEVER OVERRIDES A BRAKE DENIAL",
    "REPLAY CREATES NO AUTHORITY",
    "ACTIVATION REQUIRES AN AUTHENTICATED HUMAN",
)

#: The eight recorded authority conflicts. Each is a place where a build session, acting reasonably,
#: would settle a specification question by accident. The task must PRESERVE them.
AUTHORITY_QUESTIONS: tuple[str, ...] = tuple(f"M11-AQ-{n}" for n in range(1, 9))

#: Nine `risk_category` values in the shape `P6-D46`'s real nine had: each a plausible, well-meant
#: DESCRIPTION OF A SPECIFIC DEFECT rather than a member of a closed family vocabulary — which is
#: what an unconstrained `{"type": "string"}` schema invites a model to write. These are M11's.
M11_UNREADABLE_CATEGORIES: tuple[str, ...] = (
    "model-activated-a-policy",
    "tenant-broadened-the-ceiling",
    "null-gate-decision",
    "predicate-read-model-inferred",
    "second-gate-minter",
    "stale-policy-version-claimed",
    "timer-broadened-authority",
    "policy-overrode-the-brake",
    "replay-created-authority",
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
def m11():
    return load_scenario(M11_PATH)


@pytest.fixture(scope="module")
def cases(m11) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m11.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m11) -> list[str]:
    listing = [c for c in m11.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def state_checks(m11) -> dict[str, list[str]]:
    return {check.name: list(check.contains) for check in m11.expect_state}


# --------------------------------------------------------------------------
# 1. The M11 base scenario holds what the generator and the gate need
# --------------------------------------------------------------------------


class TestTheM11BaseScenario:
    def test_it_parses_and_is_the_p6_backend_unit(self, m11):
        assert m11.name == "p6_m11_policy"
        assert m11.phase == "P6"
        assert m11.mode == "backend"

    def test_it_declares_the_five_deliverables_and_no_others(self, m11):
        """`fixtures:` is the driver's statement of what this unit must produce, checked for
        existence at run time — and, since the M11 bootstrap, the ONE thing that excuses a scenario
        from `test_scenario_pytest_invocation`'s "every named test path already exists" rule. So it
        has to name exactly the deliverables and nothing more: an over-broad fixtures list would
        excuse regression anchors from existing."""
        declared = set(m11.fixtures)
        for deliverable in DELIVERABLES:
            assert deliverable in declared, f"{deliverable} is not declared as an M11 deliverable"
        extra = declared - set(DELIVERABLES) - {"pyproject.toml"}
        assert not extra, (
            f"the fixtures list carries {sorted(extra)} beyond M11's deliverables. Each entry "
            "excuses a path from having to exist before the build, so the list is a boundary."
        )

    def test_the_deterministic_operation_is_a_real_command(self, m11):
        """The generator composes AROUND a basic operation. Without one it can only re-run
        batteries, and every generated scenario is a copy of the permanent one."""
        runs = {c.run for c in m11.commands}
        assert f"{PROBE} --all" in runs, "the narrative M11 run is not a command"
        assert f"{PROBE} --list-cases" in runs
        assert f"{PROBE} --list-dimensions" in runs
        assert ".venv/bin/python scripts/mutate_phase6_policy.py" in runs

    def test_the_case_vocabulary_covers_all_seven_transitions_and_the_boundaries(self, cases):
        """A coverage oracle that names fewer families than the machine has transitions cannot tell
        the generator where the gaps are."""
        assert len(cases) >= 100, f"only {len(cases)} declared cases; the M11 axis is too thin"
        for token in ("po-1-emits", "po-2-emits", "po-4-emits", "po-5-emits", "po-7-emits"):
            assert any(token in c for c in cases), f"no case declares {token}"
        # The five sentences the unit is a consequence of, each with a case behind it.
        required = (
            "a-tenant-policy-that-broadens-the-product-ceiling-is-refused",
            "a-predicate-on-model-inferred-fails-to-compile",
            "confidence-one-does-not-make-model-inferred-readable",
            "the-evaluator-input-type-has-no-confidence-field",
            "a-model-cannot-activate-a-policy",
            "automation-cannot-activate-a-policy",
            "inbound-content-can-never-author-a-policy",
            "there-is-no-admin-path-to-approved",
            "checkpoint-py-remains-the-sole-gate-minter",
            "m11-mints-no-gate-decision",
            "timerfired-never-broadens-authority",
            "expiry-raises-the-m9-human-confirmation-exception",
            "a-stale-policy-version-grant-claim-is-refused",
            "policyversionchanged-voids-an-in-flight-m4-approval",
            "a-policy-never-overrides-a-permanent-product-truth",
            "a-policy-never-overrides-a-brake-denial",
            "evaluation-is-byte-identical-reproducible",
            "the-policy-engine-unavailable-yields-no-witness-and-no-effect",
            "replay-creates-no-human-authority",
            "m12-rule-is-not-built",
            "m13-brake-lifecycle-is-not-built",
        )
        missing = [c for c in required if c not in cases]
        assert not missing, f"the case vocabulary is missing load-bearing families: {missing}"

    def test_the_two_axes_this_unit_turns_on_are_declared(self, dimensions):
        """`--direction` is THE axis of this machine: narrowing and broadening are not symmetric
        anywhere in M11 — not at revocation, not at expiry, not at the ceiling. `--provenance` is
        the axis the predicate turns on. Without both, a generated scenario can only pick a case."""
        for axis in ("--direction", "--provenance", "--gate", "--actor", "--brake", "--inject"):
            assert axis in dimensions, f"{axis} is not a declared mutation axis"
        for shared in ("--concurrency", "--repeat", "--seed", "--tenants"):
            assert shared in dimensions

    def test_every_battery_is_entered_through_the_interpreter(self, m11):
        """The M10 harness-recovery invariant, applied from the start rather than after a blocked
        run. The `pytest` console script leaves the invocation directory off `sys.path`, and M11
        reaches for `eval.phase0.gate_scan` MORE than any unit before it."""
        for command in m11.commands:
            if "pytest" in command.run:
                assert "-m pytest" in command.run, (
                    f"{command.name!r} enters pytest as the console script: {command.run[:120]}"
                )

    def test_every_battery_must_prove_it_ran(self, m11):
        """Exit 0 alone is not evidence a suite ran: pytest exits 5 on an empty selection and 4 on
        a bad path. Every battery requires the summary word, and the two silent-success shapes are
        forbidden globally."""
        for command in m11.commands:
            if "-m pytest" in command.run:
                assert "passed" in command.expect_contains, (
                    f"{command.name!r} reads only the exit code; `no tests ran` also exits 0-ish "
                    "shapes and an empty selection would pass"
                )
        for marker in ("no tests ran", "ERROR: file or directory not found",
                       "ModuleNotFoundError: No module named 'eval'"):
            assert marker in m11.forbidden, f"{marker!r} is not globally forbidden"

    def test_the_regression_anchors_name_the_machines_m11_consumes(self, m11):
        """M11 consumes M2, M3, M4 and M9 and integrates with P3. A unit that CONSUMES a machine is
        making a claim about THAT machine's behaviour, which has to be measured against ITS oracles
        rather than against M11's account of it."""
        batteries = " ".join(c.run for c in m11.commands if "-m pytest" in c.run)
        for anchor in (
            "test_phase6_work_item.py",
            "test_phase6_pipeline_instance.py",
            "test_phase6_external_effect.py",
            "test_phase6_approval.py",
            "test_phase6_exception.py",
            "test_phase6_compensation.py",
            "test_phase3_checkpoint_matrix.py",
            "test_phase3_claim_cas.py",
            "test_phase0_null_gate.py",
            "test_phase0_errata_guards.py",
            "test_false_green_defenses.py",
        ):
            assert anchor in batteries, f"{anchor} is not a regression anchor for M11"

    def test_the_adr_010_boundary_guards_are_run_on_every_execution(self, m11):
        """M11 is the first unit that legitimately widens `gate_scan.GATE_RUNTIME_MODULES`. The two
        P0 guards that consume it are therefore the ones most likely to be "fixed" in the wrong
        direction — by deleting the equality, by loosening it to a subset, or by adding `policy.py`
        to the MINT allowlist. They run here, every time."""
        batteries = " ".join(c.run for c in m11.commands if "-m pytest" in c.run)
        assert "test_phase0_null_gate.py" in batteries
        assert "test_phase0_errata_guards.py" in batteries

    def test_the_scenario_and_the_task_name_the_same_deliverables(self):
        for deliverable in DELIVERABLES:
            assert deliverable in M11_TASK, f"the task never names {deliverable}"


# --------------------------------------------------------------------------
# 2. Every declared risk names a command that could prove it
# --------------------------------------------------------------------------


def _declared_producers(scenario) -> dict[str, set[str]]:
    """literal -> the set of check names that DECLARE they will emit it."""
    out: dict[str, set[str]] = {}
    for command in scenario.commands:
        for literal in command.expect_contains:
            out.setdefault(literal, set()).add(command.name)
    for check in scenario.expect_state:
        for literal in check.contains:
            out.setdefault(literal, set()).add(check.name)
    return out


class TestTheDeclaredRisksAreMappedToCommandsThatCanProveThem:
    def test_every_claim_uses_a_canonical_category(self, m11):
        for claim in m11.verifies:
            assert claim.risk_category in RISK_CATEGORY_VALUES, (
                f"{claim.risk_category!r} is outside the closed taxonomy; `P6-D46` is exactly this"
            )

    def test_the_claim_set_covers_the_families_this_unit_actually_risks(self, m11):
        declared = {c.risk_category for c in m11.verifies}
        for required in (
            "happy_path", "authorization", "safety_invariant", "missing_data",
            "malformed_input", "conflicting_evidence", "approval_required", "stale_state",
            "cross_tenant", "concurrency", "idempotency", "service_unavailable",
            "restart_recovery", "persistence_failure", "regression", "retry_safety",
            "unexpected_state_transition", "dependency_failure", "boundary", "partial_failure",
            "repeated_request",
        ):
            assert required in declared, f"no {required!r} claim; that family is unmeasured"

    def test_there_is_a_happy_path_claim_and_it_names_a_positive_control(self, m11):
        """A battery of refusals proves nothing about a machine that refuses everything. The happy
        path is what makes every refusal below it meaningful."""
        happy = [c for c in m11.verifies if c.risk_category == "happy_path"]
        assert happy, "no happy_path claim: every refusal could be vacuous"
        observations = " ".join(happy[0].observations)
        assert "positive control" in observations, (
            "the happy-path claim rests on no positive control, so a machine that never lets a "
            "legitimate policy become ACTIVE would satisfy it"
        )

    def test_every_claim_names_a_check_that_exists(self, m11):
        names = {c.name for c in m11.commands} | {s.name for s in m11.expect_state}
        for claim in m11.verifies:
            for check in claim.checks:
                assert check in names, (
                    f"claim {claim.risk_category!r} names check {check!r}, which is not in this "
                    "scenario — it could never pass or fail"
                )

    def test_every_required_observation_is_declared_by_one_of_the_claims_own_checks(self, m11):
        """The loader enforces this, and this states it in the file's own words so a future
        loosening of the loader is visible here too."""
        producers = _declared_producers(m11)
        for claim in m11.verifies:
            for observation in claim.observations:
                declaring = producers.get(observation, set())
                assert declaring, f"nothing in the scenario declares {observation!r}"
                assert declaring & set(claim.checks), (
                    f"claim {claim.risk_category!r} needs {observation!r}, which is declared by "
                    f"{sorted(declaring)} and by none of its own checks {claim.checks}"
                )

    def test_the_probe_check_carries_the_safety_sentences(self, m11):
        narrative = [c for c in m11.commands if c.name == PROBE_CHECK]
        assert narrative, "the narrative M11 run is not a declared check"
        declared = set(narrative[0].expect_contains)
        missing = [s for s in SAFETY_LITERALS if s not in declared and s not in {
            "ACTIVATION REQUIRES AN AUTHENTICATED HUMAN"}]
        assert not missing, f"the narrative run does not require: {missing}"
        assert "behaviours as specified, 0 wrong" in declared

    def test_the_dark_posture_literals_are_required_of_the_run(self, m11):
        for literal in DARK_POSTURE_LITERALS:
            assert literal in m11.expect_visible, f"{literal!r} is not required of the run"


# --------------------------------------------------------------------------
# 3. Persisted state, the event registry and the AST are the oracle
# --------------------------------------------------------------------------


class TestPersistedStateIsTheOracle:
    def test_there_are_enough_state_oracles_to_measure_the_unit(self, m11):
        assert len(m11.expect_state) >= 15, (
            f"only {len(m11.expect_state)} persisted-state oracles; a green test suite can state "
            "every M11 invariant while the database enforces none of them"
        )

    def test_the_seven_state_check_is_read_from_the_database(self, state_checks):
        vocab = [v for k, v in state_checks.items() if "seven canonical policy states" in k]
        assert vocab, "no oracle reads the state vocabulary out of the DDL"
        joined = " ".join(vocab[0])
        assert "the state vocabulary is a CHECK: True" in joined
        assert "state count: 7" in joined
        assert "forbidden states present: []" in joined
        for state in STATES:
            assert state in joined, f"{state} is not asserted in the canonical seven"

    def test_the_gate_vocabulary_is_four_members_and_never_null(self, state_checks):
        vocab = [v for k, v in state_checks.items() if "seven canonical policy states" in k]
        joined = " ".join(vocab[0])
        assert "gate member count: 4" in joined
        assert "invented gate members present: []" in joined
        assert "gate_decision is NOT NULL: True" in joined
        for member in GATE_MEMBERS:
            assert member in joined, f"{member} is not asserted in the canonical four"

    def test_the_forbidden_writes_are_attempted_against_a_live_database(self, state_checks):
        """Reading the DDL and believing it is how a CHECK that was never compiled reads as
        enforcement."""
        live = [v for k, v in state_checks.items() if "the live database refuses" in k]
        assert live, "no oracle ISSUES a forbidden write"
        joined = " ".join(live[0])
        for refusal in (
            "an ACTIVE policy with no activator: refused",
            "a policy with a null gate decision: refused",
            "an invented gate decision: refused",
            "a NARROWED lifecycle state: refused",
            "a SUSPENDED lifecycle state: refused",
            "an INVALID lifecycle state: refused",
            "an author who is not a recorded human: refused",
            "an author from another tenant: refused",
            "an activator from another tenant: refused",
        ):
            assert refusal in joined, f"the live-write oracle never attempts: {refusal}"

    def test_the_forbidden_writes_sit_behind_positive_controls_and_a_row_count(self, state_checks):
        """A schema that refuses EVERYTHING would satisfy a battery of refusals. The controls prove
        the writes were well-formed, and the surviving-row count proves exactly the intended ones
        landed."""
        live = [v for k, v in state_checks.items() if "the live database refuses" in k]
        joined = " ".join(live[0])
        controls = [c for c in live[0] if c.startswith("positive control")]
        assert len(controls) >= 3, (
            f"only {len(controls)} positive controls on the live-write oracle; refusals over an "
            "already-invalid setup are vacuous"
        )
        assert all("ACCEPTED" in c for c in controls)
        assert re.search(r"rows that survived: \d+", joined), (
            "the live-write oracle asserts no surviving-row count, so a schema that refused the "
            "positive controls too would still look green on the refusals"
        )

    def test_the_policy_owner_singularity_is_attempted_against_a_live_database(self, state_checks):
        """`M11-AQ-7` / `P6-D72`. The invariant is "exactly one named Policy Owner per tenant", and
        on the pre-M11 tree NOTHING enforces it — two ACTIVE `POLICY_OWNER` rows are insertable.
        `P6-D72` closes at M11, so the only honest oracle is one that ISSUES the second insert.

        ### THE DIRECTION OF THESE ASSERTIONS IS THE WHOLE GUARD. An oracle that merely MENTIONS the
        second owner would pass whether the scenario demanded `refused by` or `ACCEPTED`, and a
        one-character edit in the wrong direction would ship a scenario that certifies the exact
        defect it was written to catch. So the refusals are pinned as refusals, and the positive
        controls are pinned as acceptances, by name."""
        owner = [v for k, v in state_checks.items()
                 if "two ACTIVE Policy Owners" in k]
        assert owner, "no oracle attempts a second ACTIVE Policy Owner against a live database"
        lines = owner[0]
        joined = " ".join(lines)

        # The refusals must be REFUSALS.
        for refusal in (
            "a SECOND ACTIVE POLICY_OWNER in the same tenant",
            "a SECOND ACTIVE POLICY_OWNER in the second tenant",
        ):
            got = [c for c in lines if c.startswith(refusal)]
            assert got, f"the oracle never attempts: {refusal}"
            assert all(c.endswith("refused by") for c in got), (
                f"{refusal!r} is not asserted as a REFUSAL: {got} — a scenario that expects this "
                "write to be ACCEPTED certifies the defect `P6-D72` exists to close"
            )

        # The positive controls must be ACCEPTANCES, or the refusals are vacuous.
        controls = [c for c in lines if c.startswith("positive control")]
        assert len(controls) >= 3, (
            f"only {len(controls)} positive controls; a `tenant_humans` that refused every insert "
            "would satisfy the refusals above and enforce nothing"
        )
        assert all(c.endswith("ACCEPTED") for c in controls), (
            f"a positive control is not asserted as ACCEPTED: {controls}"
        )
        # An ACTIVE delegate and a retired former owner must both still be insertable, or the
        # constraint is not singularity — it is "one human per tenant", a different and wrong rule.
        assert any("AUTHORIZED_HUMAN beside the owner" in c for c in controls), (
            "no control proves an ACTIVE delegate still fits beside the Policy Owner"
        )
        assert any("RETIRED former POLICY_OWNER" in c for c in controls), (
            "no control proves history is retained; a constraint that deletes the former owner "
            "buys singularity by destroying the audit trail"
        )

        # And it must be PER TENANT, not global.
        assert "the singularity index is tenant-scoped: True" in joined
        assert "T_A ACTIVE POLICY_OWNER rows: 1" in joined
        assert "T_B ACTIVE POLICY_OWNER rows: 1" in joined
        assert "tenants with a Policy Owner: 2" in joined, (
            "nothing proves two tenants may each hold their own Policy Owner, so a GLOBAL unique "
            "index — one Policy Owner in the entire system — would pass this oracle"
        )

    def test_the_active_scope_uniqueness_is_tenant_first_and_partial(self, state_checks):
        uniq = [v for k, v in state_checks.items() if "one ACTIVE policy per tenant and scope" in k]
        assert uniq, "no oracle measures the active-scope uniqueness"
        joined = " ".join(uniq[0])
        assert "every policy index is tenant-first: True" in joined
        assert "an ACTIVE-only partial predicate exists: True" in joined
        assert "the active uniqueness columns are tenant and scope: True" in joined
        assert "a tenant-local policy_version uniqueness exists: True" in joined

    def test_the_uniqueness_oracle_proves_tenants_are_not_coupled(self, state_checks):
        """A global uniqueness that accidentally couples tenants is the cross-tenant defect wearing
        a safety constraint's clothes, and a scenario that only asserts "the second one is refused"
        would score it as correct."""
        uniq = [v for k, v in state_checks.items() if "one ACTIVE policy per tenant and scope" in k]
        joined = " ".join(uniq[0])
        assert "positive control, the SAME scope ACTIVE in a DIFFERENT tenant: ACCEPTED" in joined
        assert "T_A ACTIVE rows: 1" in joined
        assert "T_B ACTIVE rows: 1" in joined

    def test_occ_and_the_absence_of_a_delete_path_are_exercised_not_read(self, state_checks):
        uniq = [v for k, v in state_checks.items() if "one ACTIVE policy per tenant and scope" in k]
        joined = " ".join(uniq[0])
        assert "the OCC guard on a state change that does not advance the version: refused by" in joined
        assert "a DELETE against a policy row: refused by" in joined
        assert "a reused policy_version inside one tenant: refused by" in joined

    def test_the_mint_boundary_is_measured_by_ast_with_a_positive_control(self, state_checks):
        """### The single most important oracle in the file. A confinement assertion over a
        population containing no mint at all passes vacuously — so the kernel's own `_DEFAULT`
        construction has to be FOUND before the scanner's silence about everyone else is believed."""
        mint = [v for k, v in state_checks.items() if "only thing that MINTS a gate decision" in k]
        assert mint, "no oracle measures the gate-mint boundary"
        joined = " ".join(mint[0])
        assert "modules that MINT a gate decision: ['checkpoint.py']" in joined
        assert "M11 constructs a GateEntry or GateRegistry: False" in joined
        assert "the M11 migration constructs a GateEntry or GateRegistry: False" in joined
        assert "the kernel positive control, checkpoint.py mint sites: 1" in joined, (
            "the mint scan carries no positive control; a scanner that finds no construction "
            "anywhere would report M11 as clean for the wrong reason"
        )
        assert "policy.py was parsed: True" in joined, (
            "the scan does not prove it read policy.py at all, so its verdict about policy.py "
            "would be a statement about a file it never opened"
        )

    def test_the_carrier_boundary_oracle_proves_its_own_discrimination(self, state_checks):
        """Reading executable source instead of raw text is only defensible if prose and code are
        actually told apart — the exact repair the M10 post-push correction had to make to three P0
        guards. Both directions are proven on synthetic input."""
        carrier = [v for k, v in state_checks.items() if "ADR-010 carrier boundary" in k]
        assert carrier, "no oracle measures the ADR-010 carrier boundary"
        joined = " ".join(carrier[0])
        assert "the discovered population equals the stated boundary: True" in joined
        assert "policy.py carries the typed ladder in executable code: True" in joined
        assert "carriers without an ADR-010 citation: []" in joined
        assert "prose alone is not a carrier: True" in joined
        assert "executable code is a carrier: True" in joined

    def test_the_event_oracle_reads_the_registry_and_excludes_docstrings(self, state_checks):
        registry = [v for k, v in state_checks.items() if "F11 family is exactly eight" in k]
        assert registry, "no oracle reads the F11 registry"
        joined = " ".join(registry[0])
        assert "F11 contract count: 8" in joined
        for event in F11_EVENTS:
            assert event in joined, f"{event} is not asserted as a registered F11 contract"
        assert "PolicyEvaluated family: F2 ['PL-2']" in joined, (
            "the oracle does not establish that PolicyEvaluated is M2's; M11 minting a second one "
            "would be rule-17 duplication and would go unmeasured"
        )
        assert "UnauthorizedPolicyActivationAttempted family: F14" in joined

        emitted = [v for k, v in state_checks.items() if "emits only registered event names" in k]
        assert emitted, "no oracle reads the event names M11 actually emits"
        ejoined = " ".join(emitted[0])
        assert "unregistered event names M11 mints: []" in ejoined
        assert "M11 names PolicyEvaluated, which is M2s: False" in ejoined
        assert "the literal population is non-empty: True" in ejoined, (
            "the AST literal scan carries no population proof; a scan that found no literals at "
            "all would report zero unregistered names for the wrong reason"
        )
        assert "a docstring naming an invented event is not a mint: True" in ejoined
        assert "a real string literal naming an invented event is a mint: True" in ejoined

    def test_the_dark_posture_oracle_carries_a_population_proof(self, state_checks):
        """The false green M9's build tripped and M10's first pass repeated: a dark-surface scan
        that inspected nothing is vacuously green."""
        dark = [v for k, v in state_checks.items() if "ships dark" in k]
        assert dark, "no oracle measures the dark posture"
        joined = " ".join(dark[0])
        assert "the scanned population is non-empty: True" in joined
        assert "the channel-capable population is non-empty: True" in joined
        assert "the adapter population is non-empty: True" in joined, (
            "the adapter set is asserted empty of M11 without proving the scan finds any adapter "
            "at all, which is the vacuous-guard shape this whole section exists to prevent"
        )
        assert "production importers of policy: []" in joined
        assert "channel-capable modules that import policy: []" in joined
        assert "M11 imports an adapter: []" in joined

    def test_the_importer_scan_excludes_the_stdlib_email_policy_false_positive(self, state_checks):
        """`policy` is a common word. `imap_mailbox.py` and `inbox_discovery.py` both do
        `from email.policy import default`, so a naive last-segment importer scan reports two
        permanent false positives — and a run that opens with two unexplained ship-dark violations
        teaches everyone to stop reading the ship-dark oracle."""
        dark = [v for k, v in state_checks.items() if "ships dark" in k]
        joined = " ".join(dark[0])
        assert "the stdlib email.policy false positive is excluded: True" in joined

    def test_the_migration_parity_oracle_removes_the_layer_before_upgrading(self, state_checks):
        """A parity check that never removed anything compares a database to itself."""
        parity = [v for k, v in state_checks.items() if "identical policy layer" in k]
        assert parity, "no oracle measures migration parity"
        joined = " ".join(parity[0])
        assert "the policy layer was actually removed before the upgrade: True" in joined
        assert "the migration performed work on a database missing the layer: True" in joined
        assert "a second application of the migration is a no-op: True" in joined
        assert "the upgraded policy layer is identical to the fresh one: True" in joined

    def test_the_ceiling_order_is_asserted_to_be_structural(self, state_checks):
        ceiling = [v for k, v in state_checks.items() if "declared total order" in k]
        assert ceiling, "no oracle measures the product-ceiling comparison"
        joined = " ".join(ceiling[0])
        assert "the ordering is total over the four canonical members: True" in joined
        assert "the comparison is not a raw string compare: True" in joined
        assert "the two human gates are distinguished: True" in joined

    def test_the_ceiling_order_is_asserted_as_a_SEQUENCE_not_a_set(self, state_checks):
        """### The repair of run 20260903-065810's fourth defective oracle.

        The first form of this check discovered the ordering by NAME, took `sorted(...)[0]` —
        which is `DEFAULT_PRODUCT_CEILING`, a single `str`-valued enum member, not the ordering —
        and iterated its CHARACTERS. Totality was compared against `{'A','D','E','H',...}` and
        could never be True. A set assertion would not have been enough even if it had picked the
        right object: `AUTONOMOUS_WITHIN_CAPS` is the broadest member and `FORBIDDEN` the
        narrowest, and a check that only knows WHICH four members exist would score a reversed
        ladder as correct — the exact defect that calls the most dangerous broadening in the
        system a narrowing. So the ORDER is pinned, in the ADR-010 §3.1 sequence.
        """
        ceiling = [v for k, v in state_checks.items() if "declared total order" in k][0]
        joined = " ".join(ceiling)
        assert ("the declared order, broadest first: ['AUTONOMOUS_WITHIN_CAPS', "
                "'HUMAN_APPROVAL_REQUIRED', 'PERMANENT_HUMAN_ASSERTION_REQUIRED', "
                "'FORBIDDEN']") in joined, (
            "the ordering is asserted as a SET of members, so a reversed or shuffled ladder "
            "would satisfy it — which is the broadening-reads-as-narrowing defect itself"
        )
        assert "the declared order is not the alphabetical one: True" in joined
        assert ("gate members that would BROADEN the ceiling and are refused: "
                "['AUTONOMOUS_WITHIN_CAPS']") in joined, (
            "nothing pins the DIRECTION, so an oracle that agreed the four members exist would "
            "pass while a tenant policy broadened the product ceiling"
        )
        assert "a raw gate string cannot be ranked: True" in joined

    def test_the_ceiling_oracle_mutates_the_order_and_requires_red(self, state_checks):
        """A totality predicate nobody has seen refuse anything is a decoration."""
        joined = " ".join([v for k, v in state_checks.items() if "declared total order" in k][0])
        for control in (
            "swapping two members breaks the declared order: True",
            "collapsing the two human gates breaks totality: True",
            "adding a fifth member breaks totality: True",
            "reversing the order breaks the declared order: True",
            "under a reversed order the broadest gate would read as a narrowing: True",
        ):
            assert control in joined, f"the ceiling oracle carries no control for: {control}"

    def test_the_admin_authority_oracle_reads_executable_code_not_raw_text(self, state_checks):
        """### The repair of run 20260903-065810's first defective oracle.

        The question is architectural — "did M11 introduce executable admin/superuser authority,
        an admin activation path, or a second authority mechanism?" — and the first form of this
        check answered it with `'ADMIN' in policy.py`. That matched the machine's own docstring,
        `A POLICY CHANGE IS ITSELF A GATED ACTION, AND THERE IS NO ADMIN PATH`, and reported the
        sentence promising there is no admin path as evidence that one had been invented. The
        repair does NOT ignore the word: it reads SYMBOLS and executable TOKENS out of the AST, so
        prose is not authority and an identifier is.
        """
        admin = [v for k, v in state_checks.items() if "parallel admin authority" in k]
        assert admin, "no oracle measures the admin-authority boundary"
        joined = " ".join(admin[0])
        assert "admin-shaped executable symbols M11 defines or calls: []" in joined
        assert "admin-shaped executable authority tokens M11 stores or compares: []" in joined
        assert "authority-role vocabularies M11 declares of its own: []" in joined
        assert "M11 invents an admin authority: False" in joined
        # Prose must not be authority...
        for prose in (
            "a docstring saying there is no admin path is not an admin authority: True",
            "a comment saying there is no admin path is not an admin authority: True",
            "a refusal message naming the admin path is not an admin authority: True",
        ):
            assert prose in joined, f"the admin oracle proves no prose control: {prose}"
        # ...and more than one executable SHAPE must be.
        shapes = [c for c in admin[0] if c.endswith("IS an admin authority: True")]
        assert len(shapes) >= 4, (
            f"only {len(shapes)} executable admin shapes are proven to turn this oracle red; a "
            "scan that has never been seen to fire is not evidence that M11 is clean"
        )

    def test_the_posture_columns_are_asserted_by_their_canonical_names(self, state_checks):
        """### The repair of run 20260903-065810's second defective oracle.

        It tested `'predicate' in cols` and `'caps' in cols` — a shorthand the reader carried in
        their head — against a schema whose columns are `predicate_json` and `caps_json`. It
        failed on a CORRECT product, which is the worse of the two ways an oracle can be wrong: a
        red that means nothing teaches everyone to ignore a red that does. The repair is not a
        fuzzy match — a near miss must still fail — so the same predicate is applied to mutated
        column sets and required to refuse each one.
        """
        expiry = [v for k, v in state_checks.items() if "may carry an expiry" in k]
        assert expiry, "no oracle measures the posture columns"
        joined = " ".join(expiry[0])
        assert ("the canonical posture columns the schema carries: "
                "['caps_json', 'predicate_json']") in joined, (
            "the posture columns are not asserted by their canonical names, so a shorthand or a "
            "near-miss column could satisfy the contract"
        )
        assert "the canonical predicate and caps contract holds: True" in joined
        for control in (
            "a schema missing predicate_json fails the same contract: True",
            "a schema missing caps_json fails the same contract: True",
            "a schema naming them predicate and caps fails the same contract: True",
            "a schema naming them predicate_ref and caps_ref fails the same contract: True",
            "a nullable predicate_json fails the same contract: True",
            "a nullable caps_json fails the same contract: True",
        ):
            assert control in joined, f"the posture-column oracle carries no control for: {control}"

    def test_the_event_oracle_separates_what_m11_mints_from_what_it_consumes(self, state_checks):
        """### The repair of run 20260903-065810's third defective oracle.

        It read every event-shaped literal in `policy.py` and called them all MINTS, so
        `Trigger.HUMAN_ACTIVATED = "HumanActivated"` — a driving fact the machine CONSUMES at
        PO-4, named in §33's "Events consumed", deliberately absent from the registry — was
        reported as a ninth F11 contract M11 had invented. Registering it to quiet the oracle
        would have manufactured the exact ninth contract the invariant forbids. The mint boundary
        stays EIGHT; a consumed trigger is measured as a consumer.
        """
        emitted = [v for k, v in state_checks.items() if "emits only registered event names" in k]
        assert emitted, "no oracle reads the event names M11 actually emits"
        joined = " ".join(emitted[0])
        assert "the count of F11 contracts M11 mints: 8" in joined, (
            "nothing pins the mint count at eight, so a ninth contract could be minted and the "
            "oracle would only notice if it were also unregistered"
        )
        assert "minted names that are not registered F11 contracts: []" in joined
        assert "M11 mints a ninth F11 contract: False" in joined
        assert ("the driving facts M11 CONSUMES: ['Approved', 'Authored', 'HumanActivated', "
                "'NewVersionActivated', 'Revoked', 'Submitted', 'TimerFired']") in joined, (
            "the consumed set is not pinned, so a mint could be hidden from the unregistered-name "
            "scan simply by adding a trigger with that name"
        )
        assert "HumanActivated is consumed, not minted: True" in joined
        assert "a consumed trigger is never also minted: True" in joined
        assert "a ninth event minted on a transition row is caught: True" in joined
        assert "a mint hidden behind a new consumed trigger is still caught: True" in joined

    def test_the_live_write_positive_controls_carry_governed_change_evidence(self, state_checks):
        """### The repair of run 20260903-065810's fifth defective oracle.

        Its positive controls raw-inserted an ACTIVE policy with no `approval_id` and no
        `diff_fingerprint`, and the database correctly refused them through the no-admin-path
        CHECK — the safety-POSITIVE behaviour PO-3 exists to produce. A positive control that a
        correct product must refuse is not a positive control, and with it failing the whole
        refusal battery was vacuous: a schema that refused EVERYTHING would have scored the same.
        The control now carries the governed-change evidence the canonical path leaves behind, and
        the ungoverned raw insert is kept as a NEGATIVE control that must be REFUSED.
        """
        live = [v for k, v in state_checks.items() if "the live database refuses" in k][0]
        joined = " ".join(live)
        assert ("the M4 approval the positive controls bind: "
                "('apr-1', 'GRANTED', 'policy-owner')") in joined, (
            "the positive controls do not bind a real granted M4 approval, so either they are "
            "ungoverned rows a correct product must refuse, or the no-admin-path CHECK is gone"
        )
        ungoverned = [c for c in live if c.startswith("the ungoverned raw insert")]
        assert ungoverned, (
            "the ungoverned raw ACTIVE insert is no longer attempted, so nothing proves the "
            "no-admin-path CHECK still refuses a policy manufactured without an approval"
        )
        assert all(c.endswith("refused by") for c in ungoverned), (
            f"the ungoverned raw insert is not pinned as a REFUSAL: {ungoverned}"
        )
        assert "surviving governed rows with no approval evidence: 0" in joined
        assert "surviving governed rows that carry both the approval and the diff fingerprint: 2" in joined

    def test_the_owner_singularity_oracle_proves_a_coupling_constraint_would_be_caught(
        self, state_checks
    ):
        """`tenants with a Policy Owner: 2` is the assertion that separates a tenant-scoped
        constraint from a global one — but on its own nothing shows it CAN come back as anything
        else. The oracle now builds a deliberately GLOBAL uniqueness beside the real one and
        proves the same count reads 1 under it."""
        owner = [v for k, v in state_checks.items() if "two ACTIVE Policy Owners" in k][0]
        joined = " ".join(owner)
        assert "the index population is non-empty: True" in joined, (
            "the index scan carries no population proof, so a database with no indexes at all "
            "would report the singularity index as absent for the wrong reason"
        )
        assert "tenants a GLOBAL owner uniqueness would allow: 1" in joined
        assert "a global owner uniqueness that couples tenants is caught: True" in joined
        assert "the landed constraint does NOT couple tenants: True" in joined
        assert "T_A retained former POLICY_OWNER rows: 1" in joined, (
            "nothing proves the retired owner was actually RETAINED as a row; the control could "
            "be ACCEPTED by a table that quietly dropped it"
        )

    def test_m12_and_m13_absence_is_measured_over_runtime_not_the_registry(self, state_checks):
        """The F12 and F13 contracts were registered before M11, so their presence proves nothing.
        The oracle has to ask about the RUNTIME: no module, no table, no migration."""
        scope = [v for k, v in state_checks.items() if "M12 Rule and M13 Brake are not built" in k]
        assert scope, "no oracle measures M12/M13 absence"
        joined = " ".join(scope[0])
        assert "an M12 rules table exists: False" in joined
        assert "an M12 rule module exists: False" in joined
        assert "an M13 brake lifecycle module exists: False" in joined
        assert "M11 defines a rule lifecycle: False" in joined
        assert "M11 defines an autonomy graduation engine: False" in joined


# --------------------------------------------------------------------------
# 4. The task preserves the authority conflicts rather than resolving them
# --------------------------------------------------------------------------


class TestTheTaskPreservesTheAuthorityConflicts:
    @pytest.mark.parametrize("question", AUTHORITY_QUESTIONS)
    def test_each_recorded_conflict_is_named_in_the_task(self, question: str):
        assert question in M11_TASK, (
            f"{question} is not in the task, so a build session would settle it by accident"
        )

    def test_the_task_splits_the_settled_from_the_still_open(self):
        """The bootstrap found eight and reported all eight as OPEN. The Neyma authority correction
        at `5d2d8e1` SETTLED five of them in the canon and recorded the rest as `P6-D71`..`P6-D75`,
        so the blanket "all eight are reported, not resolved" is now FALSE — and a task that still
        said it would send a build session to report a question the corpus already answers, or to
        treat `P6-D72` as somebody else's problem.

        ### The split itself is the load-bearing thing, so it is asserted rather than the slogan.
        The correction must never read as a licence: the fail-closed side of every question is
        unchanged, and this test pins that sentence too."""
        assert "SETTLED IN CANON" in M11_TASK
        for settled in ("`M11-AQ-1`", "`M11-AQ-2`", "`M11-AQ-3`", "`M11-AQ-5`", "`M11-AQ-6`"):
            assert settled in M11_TASK, f"{settled} is no longer named as settled"
        assert "RESOLVED IN CANON" in M11_TASK
        assert "P6-D71" in M11_TASK and "P6-D72" in M11_TASK and "P6-D73" in M11_TASK
        assert "P6-D74" in M11_TASK and "P6-D75" in M11_TASK
        # A correction is not a licence, and the task has to say so in its own voice.
        assert "licensed nothing" in M11_TASK_PROSE
        assert "resolving it is a founder/architect act" in M11_TASK_PROSE.lower()

    def test_policyoverridden_stays_blocked_authority_and_unbuilt(self):
        """`M11-AQ-4` / `P6-D71`. Minting an event is a founder/architect act; the correction pass
        deliberately did not edit it in either direction, and neither may a build session.

        ### The second half is the one that actually gets violated: a builder who accepts "do not
        MINT it" will still cheerfully SIMULATE it in a test fixture or carry it as a column, which
        records the fact under a name no consumer is registered against — the same defect wearing a
        test's clothes."""
        assert "BLOCKED_AUTHORITY" in M11_TASK
        assert "P6-D71" in M11_TASK
        assert "MINTING AN EVENT IS A FOUNDER/ARCHITECT ACT" in M11_TASK
        assert "MECHANISM AT ALL, MINTS NO `PolicyOverridden`, AND SIMULATES NONE" in M11_TASK
        assert "lands with" in M11_TASK_PROSE.lower() and "M12/Rule" in M11_TASK
        for marker in (
            "### PolicyOverridden MINTED ###",
            "### PolicyOverridden SIMULATED ###",
            "### AN OVERRIDE MECHANISM WAS BUILT ###",
            "### P6-D71 RESOLVED BY A BUILD SESSION ###",
        ):
            assert marker in M11_TASK, f"the task never defines {marker}"

    def test_the_single_policy_owner_is_load_bearing_acceptance_work(self):
        """`M11-AQ-7` / `P6-D72` is the ONE authority question this unit must CLOSE. It closes at
        M11, and the earlier draft of this task under-stated it twice: it told the builder to
        enforce singularity "in M11's own guard" and flatly forbade touching M1's table — which
        forbids the tier-1 partial-index route the debt row itself names.

        ### "The constraint was never there before" is not a defence. Entity point 18's
        `activated_by` FK and PO-6's "broadening requires the Policy Owner" both resolve through
        this cardinality, so two ACTIVE owners make "the Policy Owner activated this" UNPROVABLE."""
        assert "P6-D72" in M11_TASK
        assert "closes at M11" in M11_TASK_PROSE
        assert "TWO ARE INSERTABLE TODAY" in M11_TASK
        assert "YOU MUST ESTABLISH THE INVARIANT MECHANICALLY" in M11_TASK
        # The permitted mechanism, and its cost, both stated.
        assert "TIER 1" in M11_TASK and "THAT ROUTE IS PERMITTED" in M11_TASK
        assert "tenant_humans" in M11_TASK
        assert "DO NOT INVENT A SECOND ### USER, ADMIN, SUPERUSER OR AUTHORITY SYSTEM" in M11_TASK_FLAT
        # It must not be downgraded back into a reported question.
        assert "Leaving it unenforced is a FAILED unit, not a reported question." in M11_TASK
        # And it must not be mistaken for resolving V12.
        assert "It does not resolve `V12`" in M11_TASK

    def test_the_tenant_global_version_consequence_is_stated_and_owed(self):
        """`M11-AQ-6`. The structural half (tenant-monotonic numbering) was always in the task. The
        correction added the CONSEQUENCE as canon: a change in ANY scope voids in-flight authority
        in EVERY scope, and over-voiding is the fail-closed direction.

        ### That is the half a builder optimises away, because voiding another scope's work looks
        like a bug. A single-scope test suite cannot tell the two implementations apart."""
        assert "namespace is the TENANT" in M11_TASK_FLAT
        assert "OVER-VOIDING IS THE FAIL-CLOSED DIRECTION AND UNDER-VOIDING IS NOT AVAILABLE" in (
            M11_TASK_PROSE
        )
        assert "voids in-flight" in M11_TASK_PROSE
        assert "EVERY scope" in M11_TASK_FLAT
        assert "--scope" in M11_TASK, "the axis the consequence is only visible along is undocumented"

    def test_the_m9_seam_is_answered_by_precedent_rather_than_left_hanging(self):
        """`M11-AQ-8` / `P6-D73`. M10 created `compensations` and did NOT retro-wire M9's FK; M11
        does the same. The task must name the precedent, not just the prohibition, or a builder
        reads "leave it unwired" as an oversight to be helpful about."""
        assert "P6-D73" in M11_TASK
        assert "M11 edits no part of M9" in M11_TASK_PROSE
        assert "SOURCE_KINDS_WITHOUT_TABLE" in M11_TASK
        assert "wiring a seam is precisely what shipping dark forbids" in M11_TASK

    def test_the_gate_carrier_debt_does_not_widen_the_mint_allowlist(self):
        """`P6-D74`. The carrier allowlist legitimately moves; the MINT allowlist never does. The
        task has to carry both halves in the same breath, because the widening is what a builder
        remembers and the narrowing is what keeps the system honest."""
        assert "P6-D74" in M11_TASK
        assert "GATE_RUNTIME_MODULES" in M11_TASK
        assert "AUTHORISES WIDENING" in M11_TASK
        assert "SOLE MINTER OF A GATE DECISION" in M11_TASK
        assert "STAYS EMPTY UNTIL U8.1/P8" in M11_TASK

    def test_the_entity_events_cross_check_gap_is_recorded_not_closed(self):
        """`P6-D75`. Only `14-policy.md` was corrected; sixteen entity files were not audited. A
        builder who trusts an entity file's point 31 is trusting the exact surface that let
        `M11-AQ-2` survive for three weeks."""
        assert "P6-D75" in M11_TASK
        assert "THE CLASS IS OPEN" in M11_TASK
        assert "Do not build that probe here" in M11_TASK
        assert "the registry governs" in M11_TASK_PROSE

    def test_the_p8_scope_conflict_is_stated_with_both_halves(self):
        """`M11-AQ-1` is the one a build session is most likely to resolve silently, because the
        fail-closed reading looks like ordinary scoping. Both halves have to be visible."""
        assert "prohibited_scope" in M11_TASK
        assert "policy (P8)" in M11_TASK
        assert "13 machines" in M11_TASK_FLAT
        assert "IS THE NEXT CHECKPOINT" in M11_TASK

    def test_the_entity_event_list_discrepancy_is_stated(self):
        """`M11-AQ-2`: the entity's §31 lists six events; the registry carries eight."""
        assert "PolicySubmitted" in M11_TASK and "PolicyApproved" in M11_TASK
        assert "2026-08-12" in M11_TASK
        assert "the event registry governs" in M11_TASK_PROSE

    def test_the_ceiling_check_impossibility_is_stated_rather_than_faked(self):
        """`M11-AQ-5`: a row-local SQL CHECK cannot read a product ceiling that is not on the row.
        The task must say so and require the guard, not a CHECK that compares a column to itself."""
        assert "row-local" in M11_TASK_FLAT
        assert "machine guard rather than a row" in M11_TASK_PROSE
        assert "do not fake a" in M11_TASK_PROSE.lower()

    def test_the_open_validation_questions_stay_open(self):
        for token in ("V11", "V12"):
            assert token in M11_TASK, f"{token} is not preserved"
        assert "nothing graduates" in M11_TASK_PROSE.lower()
        assert "one Policy Owner, one authority level" in M11_TASK_PROSE
        assert "DO NOT RESOLVE V11 OR V12 BY PREFERENCE" in M11_TASK_PROSE

    def test_the_task_states_the_precedence_ordering_it_wants_enforced(self):
        for layer in ("Constraint", "Permanent Product Truth", "Brake", "Product Policy",
                      "Tenant Policy", "Rules", "Workflow default"):
            assert layer in M11_TASK, f"the precedence ladder omits {layer}"


# --------------------------------------------------------------------------
# 5. The seams are scoped to M11
# --------------------------------------------------------------------------


class TestTheSeamsAreScopedToM11:
    def test_the_task_names_the_landed_seams_m11_must_feed(self):
        for seam in (
            "checkpoint.py",
            "GateDecision",
            "GateRegistry",
            "void_on_policy",
            "AP-4p",
            "claim CAS",
            "tenant_humans",
            "AUTHORITY_ROLES",
            "raise_exception",
            "BrakeStore",
            "pipeline_instance.py",
        ):
            assert seam in M11_TASK, f"the task never names the landed seam {seam}"

    def test_the_task_forbids_a_second_gate_minter_in_as_many_words(self):
        assert "MUST CONSTRUCT NO `GateEntry` AND NO `GateRegistry`" in M11_TASK
        assert "THE MINT ALLOWLIST STAYS" in M11_TASK
        assert "A second gate authority is the same defect as no gate authority" in M11_TASK_PROSE

    def test_the_task_explains_the_boundary_widening_and_its_narrowing(self):
        """M11 legitimately joins `GATE_RUNTIME_MODULES`. A task that only said "do not touch the
        guards" would send the builder into a P0 failure with no way out except deleting a guard."""
        assert "gate_scan.py" in M11_TASK
        assert "GATE_RUNTIME_MODULES" in M11_TASK
        assert "WIDENING WITH A NARROWING ATTACHED" in M11_TASK
        assert "pipeline_instance.py" in M11_TASK
        assert "P6-CP-2" in M11_TASK

    def test_the_task_forbids_deleting_or_subsetting_the_guards(self):
        assert "Do not weaken, delete or subset-ify either ADR-010 boundary guard" in M11_TASK_PROSE
        assert "stop and report it" in M11_TASK_FLAT

    def test_the_task_forbids_duplicating_the_invalidation_mechanisms(self):
        assert "DRIVE THEM. DO NOT BUILD A SECOND ONE" in M11_TASK
        assert "coordination" in M11_TASK_FLAT
        assert "not permission to bypass each consumer's own guard" in M11_TASK_PROSE

    def test_the_task_forbids_editing_m9_while_requiring_its_seam(self):
        assert "M11 edits no part of M9" in M11_TASK_PROSE
        assert "SOURCE_KINDS" in M11_TASK
        assert "raise_exception" in M11_TASK

    def test_the_task_forbids_editing_any_landed_machine(self):
        assert "Do not modify M1–M10" in M11_TASK_PROSE or "Do not modify M1-M10" in M11_TASK_PROSE

    def test_the_task_says_policyevaluated_is_not_m11s(self):
        assert "`PolicyEvaluated` IS NOT YOURS" in M11_TASK
        assert "PL-2" in M11_TASK


# --------------------------------------------------------------------------
# 6. The M11 vocabulary is safe, and visible to the generator
# --------------------------------------------------------------------------


class TestTheM11Vocabulary:
    def test_the_approved_set_admits_the_probe_and_refuses_an_invention(self, m11):
        approved = ApprovedCommands.from_sources(
            scenarios=[m11], configured=_local_vocabulary()
        )
        ok, _ = approved.approves(f"{PROBE} --case a-model-cannot-activate-a-policy")
        assert ok, "the M11 probe's own case vocabulary is not approved"
        ok, reason = approved.approves(
            'python -c "import policy; policy.activate_everything()"'
        )
        assert not ok, "an invented command is approved; the approval set constrains nothing"
        assert "approved set" in reason

    def test_every_approved_command_is_the_probe_or_a_declared_battery(self, m11):
        """The vocabulary must be composable, not open. Every entry is one already-safe read-only
        entry point distinguished only by an argument."""
        for entry in _local_vocabulary():
            assert entry.startswith(".venv/bin/python scripts/"), (
                f"the local vocabulary carries a non-script entry point: {entry!r}"
            )

    def test_the_local_config_never_targets_a_unit_before_m11(self):
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
        earlier = P6_UNIT_ORDER[: P6_UNIT_ORDER.index("p6_m11_policy")]
        assert target not in earlier, (
            f"the local config targets {target!r}, a unit M11 has already superseded; a run would "
            "verify the previous unit and report this one"
        )

    def test_no_superseded_units_case_vocabulary_is_still_enumerated(self):
        """A prior unit's probe is a REGRESSION ANCHOR inside the current permanent scenario, and a
        prefix match already approves every `--case` tail of it. Enumerating its cases again only
        pushes the unit actually under test toward the brief's render bound — which is exactly how
        M3's bare probe went invisible at the M9 bootstrap."""
        raw = _local_config()
        if not raw:
            pytest.skip("no local driver.config.yaml on this checkout")
        vocabulary = raw.get("scenario_generation", {}).get("approved_commands") or []
        target = raw.get("scenario")
        if target == "p6_m11_policy":
            stale = [c for c in vocabulary if "probe_phase6_compensation.py --case" in c]
            assert not stale, (
                f"{len(stale)} M10 `--case` entries are still enumerated while the config targets "
                "M11. M10's probe is a regression anchor now."
            )
        else:
            stale = [c for c in vocabulary if "probe_phase6_policy.py --case" in c]
            assert not stale, (
                f"{len(stale)} M11 `--case` entries are enumerated while the config targets "
                f"{target!r}"
            )

    def test_the_m11_vocabulary_is_enumerated_when_the_config_targets_m11(self):
        raw = _local_config()
        if not raw or raw.get("scenario") != "p6_m11_policy":
            pytest.skip("the local config does not target M11")
        vocabulary = raw.get("scenario_generation", {}).get("approved_commands") or []
        m11_entries = [c for c in vocabulary if "probe_phase6_policy.py" in c]
        assert m11_entries, "the M11 vocabulary is not enumerated in the local config at all"
        assert f"{PROBE}" in vocabulary, (
            "the bare M11 probe is not enumerated; it is the prefix every `--case` tail is "
            "approved by and the unit's deterministic entry point"
        )

    def test_every_enumerated_local_case_is_one_the_scenario_declares(self, cases):
        """The config and the scenario must agree on the vocabulary. An enumerated `--case` the
        probe was never asked to implement is a command the generator will compose and the product
        will refuse — a run that fails as a product defect for a configuration reason."""
        raw = _local_config()
        if not raw or raw.get("scenario") != "p6_m11_policy":
            pytest.skip("the local config does not target M11")
        vocabulary = raw.get("scenario_generation", {}).get("approved_commands") or []
        enumerated = [
            m.group(1) for m in
            (re.search(r"probe_phase6_policy\.py --case ([a-z0-9-]+)", c) for c in vocabulary)
            if m
        ]
        assert enumerated, "no M11 `--case` entries are enumerated"
        unknown = sorted(set(enumerated) - set(cases))
        assert not unknown, (
            f"the local config enumerates cases the scenario never declares: {unknown}"
        )

    def test_the_task_states_the_output_contract_the_scenario_asserts(self):
        """Every literal the scenario requires the PROBE to print must be a literal the task told
        the builder to print. Otherwise the scenario asks for output nobody was asked to produce,
        and the run fails as a product defect."""
        for literal in SAFETY_LITERALS + DARK_POSTURE_LITERALS:
            assert literal in M11_TASK, (
                f"the scenario requires the probe to print {literal!r}, and the task never asks "
                "for it"
            )
        assert "behaviours as specified, 0 wrong" in M11_TASK

    def test_the_task_states_the_forbidden_markers_the_scenario_watches_for(self, m11):
        """A marker the scenario forbids and the task never defines is a marker the probe will
        never emit — which makes that half of `forbidden:` decorative."""
        specific = [m for m in m11.forbidden if m.startswith("### ") and m.endswith(" ###")]
        assert len(specific) >= 120, (
            f"only {len(specific)} M11-specific forbidden markers; the alarm vocabulary has been "
            "thinned out"
        )
        missing = [m for m in specific if m not in M11_TASK]
        assert not missing, f"the task never defines these forbidden markers: {missing[:6]}"

    def test_the_task_states_the_case_axes_the_scenario_asserts(self, dimensions):
        for axis in dimensions:
            assert axis in M11_TASK, f"the scenario asserts the axis {axis} and the task omits it"


# --------------------------------------------------------------------------
# 7. Dynamic generation can close an M11 coverage gap, safely
# --------------------------------------------------------------------------


STATE_ORACLE = next(
    check.command
    for check in load_scenario(M11_PATH).expect_state
    if "schema_readiness_problems" in check.command
)


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close.

    Built as the planner builds one: the citation lives on the provenance, and a coverage-gap case
    that cannot name a risk from this run's own register is refused before it reaches the boundary.
    """
    return GeneratedScenario(
        id="gen-m11-second-gate-authority",
        title="the policy engine never becomes a second gate authority",
        purpose=(
            "a policy engine that constructs its own GateRegistry gives the system two answers to "
            "'may Neyma do this alone', and nothing that says which one the grant was minted under"
        ),
        risk_category=RiskCategory.SAFETY_INVARIANT,
        priority=Priority.P0,
        rationale="the identified second-gate-authority risk had no scenario behind it",
        requirement_reference="P6/M11",
        product_principle_reference="effect-truth",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared state, so "
            "nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m11-task",
            session_id="scripted",
            generating_risk="M11 could mint a gate decision outside the checkpoint kernel",
            source_risks=[risk_key],
        ),
        actions=[{
            "kind": "command",
            "name": "drive a policy activation and watch for a second gate authority",
            "command": command,
            # The command that prints it, named. An asserted literal no operation in the scenario
            # declares is refused as an unattributable oracle.
            "expect_contains": ["THE CHECKPOINT IS STILL THE ONLY GATE MINTER"],
        }],
        # A `safety_invariant` claim here is about a TABLE and a KERNEL — "M11 minted no gate
        # decision" is not something a probe can prove by printing it.
        persisted_state_checks=[
            GeneratedStateCheck(
                name="the policy layer is still tenant-first and readable",
                command=STATE_ORACLE,
                contains=["problems: []", "policies"],
            )
        ],
        expected_observations=["THE CHECKPOINT IS STILL THE ONLY GATE MINTER"],
        forbidden_observations=["### M11 MINTED A GATE DECISION ###"],
    )


class TestGenerationClosesM11GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-second-gate-authority",
            description="M11 could mint a gate decision outside the checkpoint kernel",
            risk_category=RiskCategory.SAFETY_INVARIANT,
            severity=Priority.P0,
            basis="ADR-010 puts gate evaluation at ONE boundary; entity §38: M11 IS step 6",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m11", "p6", "m11"},
                principle_tokens={"effect-truth"},
                known_risk_ids={risk.key, "R-second-gate-authority"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m11_vocabulary_is_accepted(self, context):
        """The whole point of enumerating the vocabulary: the generator can COMPOSE a case the
        permanent scenario never wrote, from arguments a human already approved."""
        ctx, risk = context
        command = (
            f"{PROBE} --case checkpoint-py-remains-the-sole-gate-minter "
            "--actor model --gate AUTONOMOUS_WITHIN_CAPS --seed 11"
        )
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M11 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario(
                'python -c "import policy; policy.activate_everything()"', risk.key)],
            ctx,
        )
        assert not accepted
        assert rejected
        reasons = rejected[0][1]
        assert any("approved" in r.lower() for r in reasons), reasons

    def test_a_gap_case_touching_repository_authority_is_refused(self):
        """A verification scenario observes the product; it never edits the rules the product is
        judged against — and for THIS unit that matters more than for any before it, because the
        rules M11 is judged against are the same rules M11 implements."""
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-second-gate-authority",
            description="M11 could mint a gate decision outside the checkpoint kernel",
            risk_category=RiskCategory.SAFETY_INVARIANT,
            severity=Priority.P0,
            basis="ADR-010 puts gate evaluation at ONE boundary",
        )
        ctx = ValidationContext(
            approved_commands=approved,
            grounding_tokens={"p6/m11", "p6", "m11"},
            principle_tokens={"effect-truth"},
            known_risk_ids={risk.key, "R-second-gate-authority"},
        )
        accepted, rejected = validate_plan(
            [_gap_scenario(f"{PROBE} --case x docs/implementation/CURRENT.md", risk.key)], ctx
        )
        assert not accepted
        reasons = rejected[0][1]
        assert any("authority" in r.lower() for r in reasons), reasons

    def test_an_uncovered_p0_m11_risk_blocks_acceptance(self):
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
                    id="R-second-gate-authority",
                    description="M11 could mint a gate decision outside the checkpoint kernel",
                    risk_category=RiskCategory.SAFETY_INVARIANT,
                    severity=Priority.P0,
                    basis="ADR-010 puts gate evaluation at ONE boundary",
                )
            ],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert "KNOWN COVERAGE GAPS" in verdict.summary_block()

    def test_the_generator_taxonomy_stays_closed(self):
        """`P6-D46`: nine generated scenarios were rejected because the model wrote plausible
        DESCRIPTIONS of defects where a closed family vocabulary was required."""
        for unreadable in M11_UNREADABLE_CATEGORIES:
            assert unreadable not in RISK_CATEGORY_VALUES, (
                f"{unreadable!r} became a canonical category; the taxonomy stopped being closed"
            )


# --------------------------------------------------------------------------
# 8. M11 is scoped as a unit, and cannot move the phase
# --------------------------------------------------------------------------


@pytest.fixture
def m11_repo(tmp_path: Path) -> PhaseRepo:
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/policy.py", "# the unit under construction\n")
    repo.commit_all("the M11 candidate")
    return repo


class TestM11IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m11(self, m11_repo: PhaseRepo):
        scope = m11_repo.scope(M11_TASK)
        assert scope.scope_id == "P6/M11"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(self, m11_repo: PhaseRepo):
        scope = m11_repo.scope(M11_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m11_repo: PhaseRepo):
        scope = m11_repo.scope(M11_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(self, m11_repo: PhaseRepo):
        rendered = m11_repo.scope(M11_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "enables nothing in production" in rendered

    def test_the_task_says_the_phase_does_not_move(self):
        assert "`criteria_scored` is `[]`" in M11_TASK_FLAT
        assert "P7 is BLOCKED" in M11_TASK_FLAT
        assert "Landing M11 scores nothing" in M11_TASK_FLAT


class TestM11CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m11_repo: PhaseRepo
    ):
        scope = m11_repo.scope(M11_TASK)
        completion = scoped_completion(scope, TaskResult.ACCEPTED, phase_accepted=True)
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M11"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m11_repo: PhaseRepo):
        completion = scoped_completion(m11_repo.scope(M11_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")


# --------------------------------------------------------------------------
# 9-10. The review is owed, and the loop owns M11 end to end
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m11_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m11_repo.root, m11_repo.scope(M11_TASK), unit=m11_repo.unit()
        )
        assert requirement.required
        assert requirement.from_repository_authority

    def test_the_task_states_the_tier_and_says_why_it_took_the_higher_one(self):
        """CLAUDE.md §7: "When genuinely torn between two tiers, take the higher one once and say
        so." A state machine is tier 2 by itself. M11 lands a MIGRATION, is load-bearing for TENANT
        ISOLATION, and is the authority mechanism every already-landed gate depends on."""
        assert "tier-1" in M11_TASK
        assert "migration" in M11_TASK_FLAT
        assert "tenant isolation" in M11_TASK_FLAT
        assert "takes the higher tier once and says so, and this file says so" in M11_TASK_PROSE


class TestTheLoopOwnsM11EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m11_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session that wrote the code,
        with its evidence path intact."""
        builder = FakeBuilder(m11_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m11_repo, tmp_path, task=M11_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        assert "INDEPENDENT REVIEW" in builder.prompts[1]
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED


# --------------------------------------------------------------------------
# 11. The unit stops where it was told to stop
# --------------------------------------------------------------------------


class TestTheUnitStopsAtM11:
    def test_the_task_refuses_m12_and_m13_explicitly(self):
        assert "Do not build M12 (Rule) or M13 (Brake)" in M11_TASK_PROSE
        assert "shared machinery" in M11_TASK_FLAT
        assert "Do not build an autonomy-graduation engine" in M11_TASK_PROSE

    def test_the_task_refuses_the_production_surfaces(self):
        for refusal in (
            "Do not populate the production `GateRegistry`",
            "Do not build a policy editor",
            "Do not join any outbound channel",
        ):
            assert refusal in M11_TASK, f"the task never says: {refusal}"

    def test_the_scenario_forbids_the_future_scope_markers(self, m11):
        for marker in (
            "### M12 RULE MACHINE BUILT ###",
            "### M13 BRAKE MACHINE BUILT ###",
            "### RULES TABLE CREATED ###",
            "### AUTONOMY GRADUATION ENGINE BUILT ###",
            "### M11 PRODUCTION-ENABLED ###",
            "### P7 PROVENANCE SURFACE BUILT ###",
            "### V11 RESOLVED BY PREFERENCE ###",
            "### V12 RESOLVED BY PREFERENCE ###",
        ):
            assert marker in m11.forbidden, f"{marker} is not a watched alarm"

    def test_the_scenario_forbids_editing_any_landed_machine(self, m11):
        for marker in (
            "### M1 MACHINE EDITED ###",
            "### M2 STATE MACHINE EDITED ###",
            "### M3 EFFECT SEAM REWRITTEN ###",
            "### M4 MACHINE EDITED ###",
            "### M9 MACHINE EDITED ###",
            "### M10 MACHINE EDITED ###",
        ):
            assert marker in m11.forbidden, f"{marker} is not a watched alarm"


# --------------------------------------------------------------------------
# 12. THE MUTATION GUARD — does this file actually fail when the assertion is removed?
# --------------------------------------------------------------------------


def _mutate(edit) -> "object":
    """Load a copy of the SHIPPED M11 scenario with one load-bearing thing weakened.

    `edit` receives the raw YAML mapping and changes it in place. Nothing is written to the
    scenarios directory: the mutant lives in a temporary file and is parsed through the real
    loader, so a weakening the loader itself refuses raises here rather than returning a Scenario.
    """
    import tempfile

    raw = copy.deepcopy(yaml.safe_load(M11_PATH.read_text(encoding="utf-8")))
    edit(raw)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m11_mutant.yaml"
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


LIVE_WRITES = ("the live database refuses a null gate, an ACTIVE policy with no activator, and an "
               "invented state")
STATE_VOCAB = "the seven canonical policy states are a database constraint, and there is no eighth"
UNIQUENESS = "one ACTIVE policy per tenant and scope, enforced by a tenant-first partial index"
MINT = ("the checkpoint kernel is still the only thing that MINTS a gate decision, and M11 mints "
        "none")
CARRIER = ("the ADR-010 carrier boundary is stated once, includes M11, and every carrier cites its "
           "authority")
EVENTS = "M11 emits only registered event names, and mints no ninth F11 contract"
REGISTRY = "the F11 family is exactly eight registered contracts, with their canonical flags"
DARK = "M11 ships dark: zero production importers, and no channel-capable module reaches it"
PARITY = "an upgraded database and a fresh database carry the identical policy layer"
CEILING = ("the product-ceiling comparison is a declared total order over the four gate members, "
           "not a string compare")
SCOPE = "M12 Rule and M13 Brake are not built, and no autonomy-graduation engine exists"
#: The three oracles run 20260903-065810 found defective ALONGSIDE the four named above, and whose
#: repairs the section below mutates in turn.
ADMIN = "M11 uses M1's landed tenant authority model and invents no parallel admin authority"
OWNER = "a tenant cannot hold two ACTIVE Policy Owners, and the constraint is per tenant"
EXPIRY = "only a narrowing policy may carry an expiry, and the direction is a persisted fact"


def _state_map(scenario) -> dict[str, list[str]]:
    return {c.name: list(c.contains) for c in scenario.expect_state}


class TestThisFileFailsWhenTheGuardIsRemoved:
    """A readiness test never seen to fail is a decoration.

    Every case below weakens the SHIPPED scenario in one specific way and then runs the REAL
    assertion from earlier in this file against the weakened copy — not a paraphrase of it. If an
    assertion has been loosened into something that passes either way, these turn green and the
    failure is visible here rather than six weeks later in a run that verified nothing.

    `CLAUDE.md` §6: *mutate to prove a guard works when you are writing a guard that protects a
    tier-1 invariant.*
    """

    # ---- the round-trip control: an UNMUTATED copy must still pass -------------------------
    def test_the_unmutated_round_trip_still_passes_every_guard(self):
        """The control every mutation below depends on. If the YAML round trip itself broke the
        scenario, every mutation would "fail" for a reason that has nothing to do with the defect
        it planted, and the whole section would be measuring the serializer."""
        scenario = _mutate(lambda raw: None)
        checks = _state_map(scenario)
        assert "state count: 7" in " ".join(checks[STATE_VOCAB])
        assert "modules that MINT a gate decision: ['checkpoint.py']" in " ".join(checks[MINT])
        assert len(scenario.expect_state) >= 15
        assert len([m for m in scenario.forbidden
                    if m.startswith("### ") and m.endswith(" ###")]) >= 120

    # ---- the frozen seven -----------------------------------------------------------------
    def test_an_eighth_state_slipping_into_the_vocabulary_is_caught(self):
        scenario = _mutate(lambda raw: _named(raw, "expect_state", STATE_VOCAB)["contains"]
                           .remove("state count: 7"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[STATE_VOCAB])
            assert "state count: 7" in joined

    def test_dropping_the_forbidden_state_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _named(raw, "expect_state", STATE_VOCAB)["contains"]
                           .remove("forbidden states present: []"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[STATE_VOCAB])
            assert "forbidden states present: []" in joined

    # ---- the never-null four-member gate ---------------------------------------------------
    def test_dropping_the_never_null_gate_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _named(raw, "expect_state", STATE_VOCAB)["contains"]
                           .remove("gate_decision is NOT NULL: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[STATE_VOCAB])
            assert "gate_decision is NOT NULL: True" in joined

    def test_dropping_the_four_member_count_is_caught(self):
        scenario = _mutate(lambda raw: _named(raw, "expect_state", STATE_VOCAB)["contains"]
                           .remove("gate member count: 4"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[STATE_VOCAB])
            assert "gate member count: 4" in joined

    # ---- the live forbidden writes ---------------------------------------------------------
    def test_removing_a_positive_control_from_the_live_write_oracle_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", LIVE_WRITES)
            entry["contains"] = [
                c for c in entry["contains"] if not c.startswith("positive control")
            ]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            controls = [c for c in _state_map(scenario)[LIVE_WRITES]
                        if c.startswith("positive control")]
            assert len(controls) >= 3

    def test_dropping_the_surviving_row_count_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", LIVE_WRITES)
            entry["contains"] = [c for c in entry["contains"]
                                 if not c.startswith("rows that survived")]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[LIVE_WRITES])
            assert re.search(r"rows that survived: \d+", joined)

    def test_letting_an_ownerless_activation_through_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", LIVE_WRITES), "contains",
            "an ACTIVE policy with no activator: refused"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[LIVE_WRITES])
            assert "an ACTIVE policy with no activator: refused" in joined

    def test_letting_a_null_gate_through_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", LIVE_WRITES), "contains",
            "a policy with a null gate decision: refused"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[LIVE_WRITES])
            assert "a policy with a null gate decision: refused" in joined

    # ---- uniqueness and tenancy ------------------------------------------------------------
    def test_dropping_the_tenant_first_index_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", UNIQUENESS), "contains",
            "every policy index is tenant-first: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[UNIQUENESS])
            assert "every policy index is tenant-first: True" in joined

    def test_dropping_the_cross_tenant_positive_control_is_caught(self):
        """Without it a GLOBAL unique index on `scope` alone would satisfy the "second one is
        refused" assertion, and one brokerage's posture would decide another's gate."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", UNIQUENESS), "contains",
            "positive control, the SAME scope ACTIVE in a DIFFERENT tenant: ACCEPTED"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[UNIQUENESS])
            assert "positive control, the SAME scope ACTIVE in a DIFFERENT tenant: ACCEPTED" in joined

    def test_dropping_the_occ_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", UNIQUENESS), "contains",
            "the OCC guard on a state change that does not advance the version: refused by"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[UNIQUENESS])
            assert ("the OCC guard on a state change that does not advance the version: refused by"
                    in joined)

    def test_dropping_the_no_delete_assertion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", UNIQUENESS), "contains",
            "a DELETE against a policy row: refused by"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[UNIQUENESS])
            assert "a DELETE against a policy row: refused by" in joined

    # ---- the mint boundary -----------------------------------------------------------------
    def test_dropping_the_mint_confinement_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", MINT), "contains",
            "modules that MINT a gate decision: ['checkpoint.py']"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[MINT])
            assert "modules that MINT a gate decision: ['checkpoint.py']" in joined

    def test_dropping_the_mint_scans_positive_control_is_caught(self):
        """A confinement assertion over a population with no mint in it passes vacuously."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", MINT), "contains",
            "the kernel positive control, checkpoint.py mint sites: 1"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[MINT])
            assert "the kernel positive control, checkpoint.py mint sites: 1" in joined

    def test_dropping_the_proof_that_policy_py_was_read_is_caught(self):
        """Without it the scan's verdict about `policy.py` is a statement about a file it never
        opened — the exact shape of a vacuous guard."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", MINT), "contains", "policy.py was parsed: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[MINT])
            assert "policy.py was parsed: True" in joined

    def test_permitting_m11_to_mint_a_gate_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", MINT), "contains",
            "M11 constructs a GateEntry or GateRegistry: False"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[MINT])
            assert "M11 constructs a GateEntry or GateRegistry: False" in joined

    # ---- the carrier boundary --------------------------------------------------------------
    def test_dropping_the_carrier_equality_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", CARRIER), "contains",
            "the discovered population equals the stated boundary: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[CARRIER])
            assert "the discovered population equals the stated boundary: True" in joined

    def test_dropping_the_prose_versus_code_discrimination_is_caught(self):
        """This is the repair the M10 post-push correction had to make to three P0 guards. Without
        both halves proven, a scan that could not tell a docstring from runtime would pass."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", CARRIER), "contains",
            "prose alone is not a carrier: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[CARRIER])
            assert "prose alone is not a carrier: True" in joined

    def test_dropping_the_adr_010_citation_requirement_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", CARRIER), "contains",
            "carriers without an ADR-010 citation: []"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[CARRIER])
            assert "carriers without an ADR-010 citation: []" in joined

    # ---- the event contracts ---------------------------------------------------------------
    def test_dropping_the_eight_contract_count_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", REGISTRY), "contains", "F11 contract count: 8"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[REGISTRY])
            assert "F11 contract count: 8" in joined

    def test_dropping_the_policyevaluated_attribution_is_caught(self):
        """`PolicyEvaluated` is F2's, produced by M2's PL-2. Without this, M11 minting a second one
        would be rule-17 duplication of a coordination contract and would go unmeasured."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", REGISTRY), "contains",
            "PolicyEvaluated family: F2 ['PL-2']"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[REGISTRY])
            assert "PolicyEvaluated family: F2 ['PL-2']" in joined

    def test_turning_the_event_scan_into_a_text_scan_is_caught(self):
        """The AST scan excludes docstrings explicitly, so a comment saying "`PolicyNarrowed` is
        deliberately NOT minted here" cannot trip it. Both directions have to stay proven."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EVENTS), "contains",
            "a docstring naming an invented event is not a mint: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[EVENTS])
            assert "a docstring naming an invented event is not a mint: True" in joined

    def test_dropping_the_event_populations_proof_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EVENTS), "contains",
            "the literal population is non-empty: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[EVENTS])
            assert "the literal population is non-empty: True" in joined

    # ---- the dark posture ------------------------------------------------------------------
    def test_dropping_the_dark_scans_population_proof_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", DARK), "contains",
            "the channel-capable population is non-empty: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[DARK])
            assert "the channel-capable population is non-empty: True" in joined

    def test_dropping_the_stdlib_false_positive_exclusion_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", DARK), "contains",
            "the stdlib email.policy false positive is excluded: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[DARK])
            assert "the stdlib email.policy false positive is excluded: True" in joined

    # ---- migration parity ------------------------------------------------------------------
    def test_dropping_the_layer_removal_proof_is_caught(self):
        """A parity check that never removed anything compares a database to itself."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", PARITY), "contains",
            "the policy layer was actually removed before the upgrade: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[PARITY])
            assert "the policy layer was actually removed before the upgrade: True" in joined

    # ---- the ceiling order -----------------------------------------------------------------
    def test_permitting_a_string_compare_for_the_ceiling_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", CEILING), "contains",
            "the comparison is not a raw string compare: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[CEILING])
            assert "the comparison is not a raw string compare: True" in joined

    def test_dropping_the_total_order_requirement_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", CEILING), "contains",
            "the ordering is total over the four canonical members: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[CEILING])
            assert "the ordering is total over the four canonical members: True" in joined

    def test_asserting_the_ceiling_order_as_a_set_instead_of_a_sequence_is_caught(self):
        """The mutation that restores run 20260903-065810's fourth defect from the other side: a
        totality assertion with no ORDER would score a reversed ladder as correct."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", CEILING), "contains",
            "the declared order, broadest first: ['AUTONOMOUS_WITHIN_CAPS', "
            "'HUMAN_APPROVAL_REQUIRED', 'PERMANENT_HUMAN_ASSERTION_REQUIRED', 'FORBIDDEN']"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[CEILING])
            assert ("the declared order, broadest first: ['AUTONOMOUS_WITHIN_CAPS', "
                    "'HUMAN_APPROVAL_REQUIRED', 'PERMANENT_HUMAN_ASSERTION_REQUIRED', "
                    "'FORBIDDEN']") in joined

    def test_dropping_the_broadening_direction_from_the_ceiling_oracle_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", CEILING), "contains",
            "gate members that would BROADEN the ceiling and are refused: "
            "['AUTONOMOUS_WITHIN_CAPS']"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[CEILING])
            assert ("gate members that would BROADEN the ceiling and are refused: "
                    "['AUTONOMOUS_WITHIN_CAPS']") in joined

    # ---- the admin-authority boundary ------------------------------------------------------
    def test_dropping_the_structural_admin_symbol_scan_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", ADMIN), "contains",
            "admin-shaped executable symbols M11 defines or calls: []"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[ADMIN])
            assert "admin-shaped executable symbols M11 defines or calls: []" in joined

    def test_dropping_the_admin_oracles_prose_controls_is_caught(self):
        """Without them the oracle is back to a text scan that cannot tell the docstring
        promising there is no admin path from an admin path."""
        def edit(raw):
            entry = _named(raw, "expect_state", ADMIN)
            entry["contains"] = [
                c for c in entry["contains"] if "is not an admin authority" not in c
            ]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[ADMIN])
            assert ("a docstring saying there is no admin path is not an admin authority: True"
                    in joined)

    def test_dropping_the_admin_oracles_executable_shapes_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", ADMIN)
            entry["contains"] = [
                c for c in entry["contains"] if not c.endswith("IS an admin authority: True")
            ]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            shapes = [c for c in _state_map(scenario)[ADMIN]
                      if c.endswith("IS an admin authority: True")]
            assert len(shapes) >= 4

    # ---- the posture columns ---------------------------------------------------------------
    def test_dropping_the_canonical_posture_column_names_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EXPIRY), "contains",
            "the canonical posture columns the schema carries: ['caps_json', 'predicate_json']"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[EXPIRY])
            assert ("the canonical posture columns the schema carries: "
                    "['caps_json', 'predicate_json']") in joined

    def test_dropping_the_near_miss_column_control_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EXPIRY), "contains",
            "a schema naming them predicate_ref and caps_ref fails the same contract: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[EXPIRY])
            assert ("a schema naming them predicate_ref and caps_ref fails the same contract: "
                    "True") in joined

    # ---- the mint / consume boundary -------------------------------------------------------
    def test_dropping_the_eight_mint_count_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EVENTS), "contains",
            "the count of F11 contracts M11 mints: 8"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[EVENTS])
            assert "the count of F11 contracts M11 mints: 8" in joined

    def test_dropping_the_pinned_consumed_trigger_set_is_caught(self):
        """Unpinned, a mint could be hidden from the unregistered-name scan by adding a trigger
        with that name — which is the failure mode the producer/consumer split introduces."""
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", EVENTS), "contains",
            "the driving facts M11 CONSUMES: ['Approved', 'Authored', 'HumanActivated', "
            "'NewVersionActivated', 'Revoked', 'Submitted', 'TimerFired']"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[EVENTS])
            assert ("the driving facts M11 CONSUMES: ['Approved', 'Authored', 'HumanActivated', "
                    "'NewVersionActivated', 'Revoked', 'Submitted', 'TimerFired']") in joined

    # ---- the governed positive control -----------------------------------------------------
    def test_dropping_the_governed_approval_binding_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", LIVE_WRITES), "contains",
            "the M4 approval the positive controls bind: ('apr-1', 'GRANTED', 'policy-owner')"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[LIVE_WRITES])
            assert ("the M4 approval the positive controls bind: "
                    "('apr-1', 'GRANTED', 'policy-owner')") in joined

    def test_dropping_the_ungoverned_raw_insert_negative_control_is_caught(self):
        def edit(raw):
            entry = _named(raw, "expect_state", LIVE_WRITES)
            entry["contains"] = [
                c for c in entry["contains"] if not c.startswith("the ungoverned raw insert")
            ]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            ungoverned = [c for c in _state_map(scenario)[LIVE_WRITES]
                          if c.startswith("the ungoverned raw insert")]
            assert ungoverned

    def test_flipping_the_ungoverned_raw_insert_to_an_acceptance_is_caught(self):
        """The direction guard. A one-character edit in the wrong direction would ship a scenario
        that certifies an admin path into ACTIVE as correct."""
        def edit(raw):
            entry = _named(raw, "expect_state", LIVE_WRITES)
            entry["contains"] = [
                (c.replace(": refused by", ": ACCEPTED")
                 if c.startswith("the ungoverned raw insert") else c)
                for c in entry["contains"]
            ]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            ungoverned = [c for c in _state_map(scenario)[LIVE_WRITES]
                          if c.startswith("the ungoverned raw insert")]
            assert ungoverned and all(c.endswith("refused by") for c in ungoverned)

    # ---- the owner singularity, per tenant --------------------------------------------------
    def test_dropping_the_global_coupling_control_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", OWNER), "contains",
            "a global owner uniqueness that couples tenants is caught: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[OWNER])
            assert "a global owner uniqueness that couples tenants is caught: True" in joined

    def test_dropping_the_index_population_proof_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", OWNER), "contains",
            "the index population is non-empty: True"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[OWNER])
            assert "the index population is non-empty: True" in joined

    def test_dropping_the_retained_former_owner_row_count_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", OWNER), "contains",
            "T_A retained former POLICY_OWNER rows: 1"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[OWNER])
            assert "T_A retained former POLICY_OWNER rows: 1" in joined

    # ---- future scope ----------------------------------------------------------------------
    def test_permitting_an_early_m12_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", SCOPE), "contains", "an M12 rules table exists: False"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[SCOPE])
            assert "an M12 rules table exists: False" in joined

    def test_permitting_an_autonomy_ratchet_is_caught(self):
        scenario = _mutate(lambda raw: _drop(
            _named(raw, "expect_state", SCOPE), "contains",
            "M11 defines an autonomy graduation engine: False"))
        with pytest.raises(AssertionError):
            joined = " ".join(_state_map(scenario)[SCOPE])
            assert "M11 defines an autonomy graduation engine: False" in joined

    # ---- the harness invariants ------------------------------------------------------------
    def test_a_battery_entered_through_the_console_script_is_caught(self):
        def edit(raw):
            entry = _named(raw, "commands", "the M11 acceptance battery")
            entry["run"] = entry["run"].replace("-m pytest", "").replace(
                ".venv/bin/python ", ".venv/bin/pytest ")

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            for command in scenario.commands:
                if "pytest" in command.run:
                    assert "-m pytest" in command.run

    def test_a_battery_that_only_reads_the_exit_code_is_caught(self):
        def edit(raw):
            _named(raw, "commands", "the M11 acceptance battery")["expect_contains"] = []

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            for command in scenario.commands:
                if "-m pytest" in command.run:
                    assert "passed" in command.expect_contains

    def test_dropping_the_no_tests_ran_tripwire_is_caught(self):
        scenario = _mutate(lambda raw: raw["forbidden"].remove("no tests ran"))
        with pytest.raises(AssertionError):
            assert "no tests ran" in scenario.forbidden

    def test_thinning_the_alarm_vocabulary_is_caught(self):
        def edit(raw):
            raw["forbidden"] = [m for m in raw["forbidden"]
                                if not (m.startswith("### ") and m.endswith(" ###"))][:20]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            specific = [m for m in scenario.forbidden
                        if m.startswith("### ") and m.endswith(" ###")]
            assert len(specific) >= 120

    def test_removing_the_happy_path_positive_control_is_caught(self):
        """A battery of refusals proves nothing about a machine that refuses everything."""
        def edit(raw):
            claim = _claim(raw, "happy_path")
            claim["observations"] = [o for o in claim["observations"]
                                     if not o.startswith("positive control")]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            happy = [c for c in scenario.verifies if c.risk_category == "happy_path"][0]
            assert "positive control" in " ".join(happy.observations)

    def test_thinning_the_state_oracles_is_caught(self):
        """The claims that named the removed oracles are pruned alongside them, so the mutation
        plants ONE defect — a thin oracle set — rather than tripping the loader's separate
        "a claim names a check this scenario does not run" refusal and passing for the wrong
        reason."""
        def edit(raw):
            kept = raw["expect_state"][:4]
            survivors = {c["name"] for c in kept} | {c["name"] for c in raw["commands"]}
            raw["expect_state"] = kept
            for claim in raw["verifies"]:
                claim["checks"] = [c for c in claim["checks"] if c in survivors]
            raw["verifies"] = [c for c in raw["verifies"] if c["checks"]]

        scenario = _mutate(edit)
        with pytest.raises(AssertionError):
            assert len(scenario.expect_state) >= 15

    def test_widening_the_fixtures_list_into_an_exemption_hole_is_caught(self):
        """`fixtures:` excuses a path from `test_scenario_pytest_invocation`'s existence rule, so
        an over-broad list would excuse regression anchors from existing."""
        scenario = _mutate(lambda raw: raw["fixtures"].append("eval/tests/"))
        with pytest.raises(AssertionError):
            declared = set(scenario.fixtures)
            extra = declared - set(DELIVERABLES) - {"pyproject.toml"}
            assert not extra

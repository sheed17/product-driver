"""The run journal, and the founder summary derived from it.

Broad autonomy is only defensible if what happened is fully visible afterwards.
This driver's answer to "should the Driver ask first?" is *no* — and the price
of that answer is that every run must be reconstructable from its evidence
without reading a transcript.

The journal records, for one run:

* the starting repository, branch, HEAD, tree and full working-tree status;
* the active unit and its acceptance criteria;
* builder and evaluator session ids;
* every tool use, every shell command, exit codes and timeouts;
* files created, modified, moved and deleted;
* authority files changed;
* local commits created, and any local-history change with its recovery point;
* preservation refs and bundles;
* test and scenario results;
* denied operations and external-boundary attempts;
* the ending branch, HEAD, tree and working-tree state;
* the exact stop reason, and the next safe action.

:meth:`RunJournal.founder_summary` renders the ten questions the founder asked
to have answered without opening a log.

Everything written here goes through the same redactor as the rest of the
evidence store, and journalling is best-effort by construction: a failure to
record must never be able to fail a run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Sequence

from .authority import AuthorityChange, render_authority_section
from .models import redact
from .preservation import GitIdentity, capture_identity

JOURNAL_FILE = "journal.json"
SUMMARY_FILE = "FOUNDER-SUMMARY.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Entries
# --------------------------------------------------------------------------


@dataclass
class ToolUseEntry:
    at: str
    tool: str
    detail: str
    denied_reason: str | None = None
    layer: str = ""


@dataclass
class CommandEntry:
    at: str
    command: str
    exit_code: int | None = None
    timed_out: bool = False
    duration_s: float | None = None
    source: str = ""  # "scenario" | "builder" | "driver"


@dataclass
class FileChangeEntry:
    path: str
    change: str  # "created" | "modified" | "deleted" | "renamed"
    renamed_from: str = ""


@dataclass
class CommitEntry:
    at: str
    sha: str
    subject: str
    branch: str
    tree: str


@dataclass
class HistoryChangeEntry:
    at: str
    what: str
    authorized: bool
    recovery_ref: str = ""
    recovery_bundle: str = ""
    verified: bool | None = None
    detail: str = ""


@dataclass
class ExternalAttemptEntry:
    at: str
    tool: str
    detail: str
    reason: str
    layer: str = ""


# --------------------------------------------------------------------------
# The journal
# --------------------------------------------------------------------------


@dataclass
class RunJournal:
    """Everything one run did, in the order it did it."""

    run_id: str = ""
    task: str = ""
    repo: str = ""

    start_identity: GitIdentity | None = None
    end_identity: GitIdentity | None = None

    active_unit_id: str = ""
    active_unit_status: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)

    builder_session_id: str = ""
    evaluator_session_id: str = ""

    approved_roots: list[str] = field(default_factory=list)

    tool_uses: list[ToolUseEntry] = field(default_factory=list)
    commands: list[CommandEntry] = field(default_factory=list)
    file_changes: list[FileChangeEntry] = field(default_factory=list)
    commits: list[CommitEntry] = field(default_factory=list)
    history_changes: list[HistoryChangeEntry] = field(default_factory=list)
    preservation: list[dict[str, Any]] = field(default_factory=list)
    authority_report: dict[str, Any] = field(default_factory=dict)
    denied_paths: list[dict[str, str]] = field(default_factory=list)
    external_attempts: list[ExternalAttemptEntry] = field(default_factory=list)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    scenario_results: list[dict[str, Any]] = field(default_factory=list)

    stop_reason: str = ""
    next_safe_action: str = ""
    founder_decision_required: str = ""
    incomplete: list[str] = field(default_factory=list)

    # -- the grounded outcome, for the plain-terms summary ------------------
    #
    # Every one of these is copied from a record the run already produced: the
    # run status, the deterministic acceptance gate's verdict, the evaluator's
    # decision, the builder's own summaries, the reviews the driver launched.
    # Nothing here is authored at render time, and nothing here is a judgement
    # this module makes — :meth:`personal_summary` only reads them.

    #: ``RunStatus`` value, as a string.
    run_status: str = ""
    #: ``GateStatus`` value: "VERIFIED", "NOT_VERIFIED", or "" when no gate ran.
    gate_status: str = ""
    gate_headline: str = ""
    required_passed: int = 0
    required_total: int = 0
    #: ``UnverifiedCase.brief()`` for every required scenario that did not pass.
    unverified: list[str] = field(default_factory=list)
    #: ``UncoveredRisk.brief()`` for every acceptance-blocking risk with no
    #: passing scenario behind it.
    uncovered_risks: list[str] = field(default_factory=list)
    #: ``CoveredRisk.brief()`` for every acceptance-blocking risk that WAS
    #: verified, naming the scenario and the declared claim that verified it.
    #: Recorded because a coverage claim nobody can trace is not evidence: a
    #: reader must be able to check the positive half of the answer too.
    covered_risks: list[str] = field(default_factory=list)
    generation_problems: list[str] = field(default_factory=list)
    #: What the evaluator recorded as observed — never what it predicted.
    observed_behavior: list[str] = field(default_factory=list)
    #: The builder's own summaries. Recorded AS CLAIMS and rendered as claims.
    builder_claims: list[str] = field(default_factory=list)
    #: One line per independent review this run launched on its own.
    reviews: list[str] = field(default_factory=list)
    #: The active unit's human-readable name and objective, from the registry.
    active_unit_name: str = ""
    active_unit_objective: str = ""
    #: Why no unit could be resolved, when the registry declares none.
    active_unit_problem: str = ""
    #: What this run was asked to build, and the phase it sits inside. Recorded
    #: so a reader is never left to infer that a unit accepted inside a phase
    #: means the phase moved. It did not.
    task_scope_id: str = ""
    parent_phase_id: str = ""
    parent_phase_state: str = ""
    scope_is_nested: bool = False
    scenario_name: str = ""
    scenario_phase: str = ""

    started_at: str = field(default_factory=_now)
    ended_at: str = ""

    # -- lifecycle --------------------------------------------------------

    def record_start(self, repo: Path) -> GitIdentity:
        """Capture the starting git identity. Read-only."""
        self.repo = str(repo)
        self.start_identity = capture_identity(Path(repo))
        return self.start_identity

    def record_end(self, repo: Path | None = None) -> GitIdentity:
        """Capture the ending git identity. Read-only."""
        target = Path(repo) if repo is not None else Path(self.repo)
        self.end_identity = capture_identity(target)
        self.ended_at = _now()
        return self.end_identity

    # -- recording --------------------------------------------------------

    def record_tool_use(
        self,
        tool: str,
        detail: str = "",
        denied_reason: str | None = None,
        layer: str = "",
    ) -> None:
        entry = ToolUseEntry(_now(), tool, redact(detail)[:500],
                             redact(denied_reason) if denied_reason else None, layer)
        self.tool_uses.append(entry)
        if denied_reason and _is_external_boundary(denied_reason):
            self.external_attempts.append(
                ExternalAttemptEntry(entry.at, tool, entry.detail, entry.denied_reason or "", layer)
            )

    def record_command(
        self,
        command: str,
        exit_code: int | None = None,
        timed_out: bool = False,
        duration_s: float | None = None,
        source: str = "",
    ) -> None:
        self.commands.append(
            CommandEntry(_now(), redact(command)[:1000], exit_code, timed_out, duration_s, source)
        )

    def record_file_change(self, path: str, change: str, renamed_from: str = "") -> None:
        self.file_changes.append(FileChangeEntry(str(path), change, renamed_from))

    def record_denied_path(self, path: str, reason: str) -> None:
        self.denied_paths.append({"path": str(path), "reason": redact(reason)})

    def record_commit(self, sha: str, subject: str, branch: str = "", tree: str = "") -> None:
        self.commits.append(CommitEntry(_now(), sha, redact(subject)[:300], branch, tree))

    def record_history_change(
        self,
        what: str,
        authorized: bool,
        recovery_ref: str = "",
        recovery_bundle: str = "",
        verified: bool | None = None,
        detail: str = "",
    ) -> None:
        self.history_changes.append(
            HistoryChangeEntry(_now(), what, authorized, recovery_ref,
                               recovery_bundle, verified, redact(detail))
        )

    def record_preservation(self, record: Any) -> None:
        self.preservation.append(record.to_dict() if hasattr(record, "to_dict") else dict(record))

    def record_authority(self, report: dict[str, Any]) -> None:
        self.authority_report = report

    def record_test_result(self, name: str, passed: bool, detail: str = "") -> None:
        self.test_results.append({"name": name, "passed": passed, "detail": redact(detail)[:2000]})

    def record_scenario_result(self, name: str, passed: bool, detail: str = "") -> None:
        self.scenario_results.append(
            {"name": name, "passed": passed, "detail": redact(detail)[:2000]}
        )

    def record_stop(
        self, reason: str, next_safe_action: str = "", founder_decision_required: str = ""
    ) -> None:
        self.stop_reason = redact(reason)
        self.next_safe_action = redact(next_safe_action)
        # A caller that says "none" means there is no decision — it must not
        # read as a decision named "none", which would then suppress the
        # weakened-controls escalation below it.
        if founder_decision_required.strip().lower() in {"", "none", "n/a", "no", "-"}:
            self.founder_decision_required = ""
        else:
            self.founder_decision_required = redact(founder_decision_required)

    def record_outcome(
        self,
        *,
        run_status: str = "",
        gate: Any = None,
        decision: Any = None,
        builder_claims: Sequence[str] = (),
        reviews: Sequence[str] = (),
        unit: Any = None,
        scenario_name: str = "",
        scenario_phase: str = "",
    ) -> None:
        """Copy the run's final records in. Reads them; never interprets them.

        ``gate`` is a :class:`~neyma_product_driver.scenario_gate.GateVerdict`
        and ``decision`` an ``EvaluatorDecision``; both are duck-typed so the
        journal keeps no import edge to either and a test can pass a stand-in.
        """
        self.run_status = str(getattr(run_status, "value", run_status) or "")
        self.scenario_name = scenario_name or self.scenario_name
        self.scenario_phase = scenario_phase or self.scenario_phase

        if gate is not None:
            status = getattr(gate, "status", "")
            self.gate_status = str(getattr(status, "value", status) or "")
            self.gate_headline = redact(str(getattr(gate, "headline", lambda: "")() or ""))
            self.required_passed = int(getattr(gate, "required_passed", 0) or 0)
            self.required_total = int(getattr(gate, "required_total", 0) or 0)
            self.unverified = [
                redact(case.brief()) for case in getattr(gate, "unverified", []) or []
            ]
            self.uncovered_risks = [
                redact(risk.brief()) for risk in getattr(gate, "uncovered_risks", []) or []
            ]
            self.covered_risks = [
                redact(risk.brief()) for risk in getattr(gate, "covered_risks", []) or []
            ]
            self.generation_problems = [
                redact(str(p)) for p in getattr(gate, "generation_problems", []) or []
            ]

        if decision is not None:
            self.observed_behavior = [
                redact(str(o)) for o in getattr(decision, "observed_behavior", []) or []
            ]

        self.builder_claims = [redact(str(c))[:600] for c in builder_claims if str(c).strip()]
        self.reviews = [redact(str(r))[:600] for r in reviews if str(r).strip()]

        if unit is not None:
            self.active_unit_id = str(getattr(unit, "unit_id", "") or "")
            self.active_unit_status = str(getattr(unit, "status", "") or "")
            self.active_unit_name = redact(str(getattr(unit, "name", "") or ""))
            self.active_unit_objective = redact(str(getattr(unit, "objective", "") or ""))
            self.active_unit_problem = redact(str(getattr(unit, "resolution_problem", "") or ""))

    # -- derived ----------------------------------------------------------

    @property
    def verification_established(self) -> bool:
        """Whether this run may state that anything was PROVEN.

        The single predicate the plain-terms summary asks, and it is deliberately
        conjunctive. Any one of these being false means the burden of proof the
        acceptance was supposed to rest on was not discharged:

        * the run reached ACCEPTED;
        * the deterministic scenario gate returned VERIFIED;
        * no required scenario is unverified — which includes never having run;
        * no acceptance-blocking risk the run itself named is uncovered;
        * generation produced the coverage it set out to produce.

        Nothing a model said appears here. A gate that never ran leaves
        ``gate_status`` empty, and an empty gate status is not VERIFIED.
        """
        return (
            self.run_status == "ACCEPTED"
            and self.gate_status == "VERIFIED"
            and not self.unverified
            and not self.uncovered_risks
            and not self.generation_problems
        )

    @property
    def authority_changes_present(self) -> bool:
        return bool(self.authority_report.get("changed"))

    @property
    def controls_weakened(self) -> bool:
        return bool(self.authority_report.get("weakening_detected"))

    @property
    def recovery_points(self) -> list[str]:
        points: list[str] = []
        for record in self.preservation:
            if record.get("ref"):
                points.append(f"ref {record['ref']} -> {record.get('ref_target', '')[:12]}")
            if record.get("bundle_path"):
                points.append(f"bundle {record['bundle_path']}")
        return points

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": redact(self.task),
            "repo": self.repo,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "start": self.start_identity.to_dict() if self.start_identity else None,
            "end": self.end_identity.to_dict() if self.end_identity else None,
            "active_unit": {
                "id": self.active_unit_id,
                "status": self.active_unit_status,
                "acceptance_criteria": list(self.acceptance_criteria),
            },
            "sessions": {
                "builder": self.builder_session_id,
                "evaluator": self.evaluator_session_id,
            },
            "approved_roots": list(self.approved_roots),
            "tool_uses": [asdict(t) for t in self.tool_uses],
            "commands": [asdict(c) for c in self.commands],
            "file_changes": [asdict(f) for f in self.file_changes],
            "commits": [asdict(c) for c in self.commits],
            "history_changes": [asdict(h) for h in self.history_changes],
            "preservation": list(self.preservation),
            "authority": self.authority_report,
            "denied_paths": list(self.denied_paths),
            "denied_operations": [
                asdict(t) for t in self.tool_uses if t.denied_reason
            ],
            "external_boundary_attempts": [asdict(e) for e in self.external_attempts],
            "test_results": list(self.test_results),
            "scenario_results": list(self.scenario_results),
            "stop_reason": self.stop_reason,
            "next_safe_action": self.next_safe_action,
            "founder_decision_required": self.founder_decision_required,
            "incomplete": list(self.incomplete),
            "outcome": {
                "run_status": self.run_status,
                "gate_status": self.gate_status,
                "gate_headline": self.gate_headline,
                "required_passed": self.required_passed,
                "required_total": self.required_total,
                "unverified": list(self.unverified),
                "uncovered_risks": list(self.uncovered_risks),
                "covered_risks": list(self.covered_risks),
                "generation_problems": list(self.generation_problems),
                "observed_behavior": list(self.observed_behavior),
                "builder_claims": list(self.builder_claims),
                "reviews": list(self.reviews),
                "scenario": {"name": self.scenario_name, "phase": self.scenario_phase},
                "verification_established": self.verification_established,
            },
        }

    # -- persistence ------------------------------------------------------

    def save(self, run_dir: Path) -> Path:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / JOURNAL_FILE
        path.write_text(
            json.dumps(self.to_dict(), indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        (run_dir / SUMMARY_FILE).write_text(self.founder_summary(), encoding="utf-8")
        return path

    # -- the plain-terms summary -----------------------------------------

    #: The five upgrades this renderer exists to make structurally impossible.
    #: Each is a sentence somebody would write by hand at the end of a long run,
    #: and each is false. They are stated here because the defence against them
    #: is that every clause below is rendered from a record, and a record that
    #: does not exist renders as "not established" rather than as silence.
    NEVER_UPGRADE: ClassVar[tuple[str, ...]] = (
        "a builder claim is not a proven capability",
        "a local implementation is not a production enablement",
        "tests passing is not a product proof",
        "dark code is not a live feature",
        "an incomplete gate is not complete work",
    )

    def personal_summary(self) -> str:
        """The eight plain-terms questions, rendered from the final records."""
        return "\n".join(self.personal_summary_lines()) + "\n"

    def personal_summary_lines(self) -> list[str]:
        """The plain-terms section, deterministic and grounded.

        Every claim below traces to something recorded: the deterministic
        acceptance gate, the evaluator's observations, the file and commit
        record, the repository's own unit registry. Where the run recorded
        nothing, this says so — a confident blank is the failure mode this whole
        section exists to prevent.

        The rule the renderer follows, stated once: **a run may say something is
        proven only when** :attr:`verification_established` **is true.** Below
        that bar every answer is written in the language of what was attempted,
        never of what was achieved.
        """
        proven = self.verification_established
        lines: list[str] = [
            "## PERSONAL SUMMARY — SIMPLE TERMS",
            "",
            (
                "Everything here is read off this run's own records. "
                if proven
                else "Everything here is read off this run's own records, and this run did "
                "**not** establish verification, so nothing below is stated as proven. "
            )
            + "Where a record is missing, this says so rather than filling the gap.",
            "",
        ]

        # 1 ------------------------------------------------------------------
        lines += ["### 1. What we just built or fixed", ""]
        if self.file_changes:
            counts: dict[str, int] = {}
            for change in self.file_changes:
                counts[change.change] = counts.get(change.change, 0) + 1
            lines.append(
                "- Files touched: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
            )
            for change in self.file_changes[:12]:
                lines.append(f"    - {change.change}: {change.path}")
            if len(self.file_changes) > 12:
                lines.append(f"    - ... and {len(self.file_changes) - 12} more (section 2)")
        else:
            lines.append("- No file change was recorded in this run.")
        if self.commits:
            lines.append(f"- Local commits: {len(self.commits)} "
                         "(local only — nothing was pushed anywhere)")
        else:
            lines.append("- No local commit was created.")
        if self.builder_claims:
            lines.append("- What the builder SAYS it did — a claim, not a finding:")
            for claim in self.builder_claims[:3]:
                lines.append(f"    - \"{_one_line(claim, 220)}\"")

        # 2 ------------------------------------------------------------------
        lines += ["", "### 2. Why this matters for Neyma", ""]
        if self.active_unit_id:
            lines.append(f"- This run worked on **{self.active_unit_id}**"
                         + (f" — {self.active_unit_name}" if self.active_unit_name else "")
                         + (f" ({self.active_unit_status})" if self.active_unit_status else ""))
            if self.active_unit_objective:
                lines.append(f"- The repository states its objective as: "
                             f"{_one_line(self.active_unit_objective, 400)}")
            else:
                lines.append("- The repository's registry states no objective text for it.")
        else:
            lines.append(
                "- The repository declared no active unit"
                + (f" ({self.active_unit_problem})" if self.active_unit_problem else "")
                + ", so the goal you gave is the only statement of why this mattered:"
            )
            lines.append(f"    - {_one_line(redact(self.task), 400) or '(no task recorded)'}")

        # 3 ------------------------------------------------------------------
        lines += ["", "### 3. What is actually proven true", ""]
        if self.gate_status:
            lines.append(f"- Acceptance gate: **{self.gate_status}** — "
                         f"{self.required_passed}/{self.required_total} required scenario(s) "
                         "passed with resolvable evidence.")
        else:
            lines.append("- **No acceptance gate ran**, so no scenario evidence was measured.")
        if proven:
            lines.append("- Every required scenario passed and could show its evidence, "
                         "every acceptance-blocking risk this run named has a passing "
                         "scenario behind it, and no verification failed to be produced.")
            if self.scenario_results:
                for record in self.scenario_results[:12]:
                    if record.get("passed"):
                        lines.append(f"    - PASSED: {record.get('name', '?')}")
            for covered in self.covered_risks[:12]:
                lines.append(f"    - RISK VERIFIED BY: {covered}")
        else:
            lines.append("- **Nothing is established as proven by this run.** What is missing:")
            reasons = (
                [f"unverified: {u}" for u in self.unverified[:8]]
                + [f"uncovered risk: {r}" for r in self.uncovered_risks[:8]]
                + [f"verification never produced: {p}" for p in self.generation_problems[:8]]
            )
            if not reasons:
                reasons = [
                    f"the run ended as {self.run_status or 'UNRECORDED'} rather than ACCEPTED"
                    if self.run_status != "ACCEPTED"
                    else "the acceptance gate did not return VERIFIED"
                ]
            lines.extend(f"    - {r}" for r in reasons)

        # 4 ------------------------------------------------------------------
        lines += ["", "### 4. What Neyma can safely do now that it could not before", ""]
        if not proven:
            lines.append("- **Nothing new.** This run did not establish verification, so no new "
                         "capability may be claimed from it.")
        elif self.observed_behavior:
            for observed in self.observed_behavior[:8]:
                lines.append(f"- {_one_line(observed, 300)}")
            lines.append(
                "- Scope of that sentence, exactly: this is behaviour **observed locally in "
                "this repository, in this run**. It is not deployed, not enabled for any real "
                "tenant, and no external effect was performed."
            )
        else:
            lines.append("- The gate verified the required coverage, but the evaluator recorded "
                         "no observed behaviour, so no new capability is stated here.")

        # 5 ------------------------------------------------------------------
        lines += ["", "### 5. What is still NOT built", ""]
        outstanding = (
            list(self.incomplete)
            + [f"not verified — {u}" for u in self.unverified]
            + [f"named as a risk and not covered — {r}" for r in self.uncovered_risks]
            + [f"verification not produced — {p}" for p in self.generation_problems]
        )
        if outstanding:
            lines.extend(f"- {item}" for item in outstanding[:15])
            if len(outstanding) > 15:
                lines.append(f"- ... and {len(outstanding) - 15} more")
        else:
            lines.append("- This run recorded nothing outstanding **within its own scope**.")
        lines.append(
            "- A run covers one unit. Everything the repository owes beyond this unit is "
            "still owed, and this section is not a statement about it — "
            "`docs/implementation/CURRENT.md` is."
        )

        # 6 ------------------------------------------------------------------
        lines += ["", "### 6. Where Neyma is in the roadmap", ""]
        start = self.start_identity
        lines.append(
            f"- As the repository recorded it at `{(start.head[:12] if start else '?') or '?'}` "
            f"on `{start.branch if start else '?'}`:"
        )
        if self.active_unit_id:
            lines.append(f"    - active unit: {self.active_unit_id} "
                         f"({self.active_unit_status or 'status unrecorded'})")
        else:
            lines.append("    - the registry declared no active unit for this run")
        if self.scope_is_nested and self.task_scope_id:
            lines.append(
                f"    - this run built **{self.task_scope_id}**, one unit inside "
                f"**{self.parent_phase_id}**"
            )
            lines.append(
                f"    - **{self.parent_phase_id} is "
                f"{self.parent_phase_state or 'IN_PROGRESS'} and this run did not move it.** "
                f"Accepting {self.task_scope_id} does not complete "
                f"{self.parent_phase_id}, does not score one of its acceptance criteria, "
                "does not unblock the phase after it, and enables nothing in production."
            )
        if self.scenario_name:
            lines.append(f"    - verified against scenario `{self.scenario_name}`"
                         + (f", phase {self.scenario_phase}" if self.scenario_phase else ""))
        lines.append(
            "- **This is a pointer, not the authority.** `docs/implementation/CURRENT.md` and "
            "`docs/implementation/IMPLEMENTATION-REGISTRY.yaml` in the product repository are "
            "the authority on phase position, and they move without this file moving."
        )

        # 7 ------------------------------------------------------------------
        lines += ["", "### 7. The ONE exact next move", ""]
        lines.append(f"- {self._next_move()}")

        # 8 ------------------------------------------------------------------
        lines += ["", "### 8. Founder decisions needed", ""]
        if self.founder_decision_required:
            lines.append(f"- **{self.founder_decision_required}**")
        elif self.controls_weakened:
            lines.append("- **A mandatory control was removed or weakened by this run.** "
                         "Read section 6 of the detailed summary below before accepting it.")
        else:
            lines.append("- None.")

        lines += [
            "",
            "> Written to a fixed rule: " + "; ".join(self.NEVER_UPGRADE) + ".",
        ]
        return lines

    def _next_move(self) -> str:
        """One line. Derived from status, never composed from several."""
        if self.founder_decision_required:
            return (
                "Answer the decision in section 8 — the run stopped because only you can "
                "authorize it."
            )
        if self.run_status == "REQUIRES_APPROVAL":
            return (
                "Read `protocol-resolution.json` in the run directory and approve or reject "
                "the option it proposes; the run cannot continue without that."
            )
        if self.next_safe_action:
            return _one_line(self.next_safe_action, 400)
        if self.verification_established:
            return (
                "Read the diff yourself, then decide whether to commit and push it — the "
                "driver stops before every remote action, by design."
            )
        if self.run_status in {"BLOCKED", "ERROR", "STOPPED"}:
            return (
                f"The run ended {self.run_status} and recorded no next safe action; read "
                "`journal.json` and the last iteration's `decision.json` before re-running."
            )
        return (
            "Re-read section 3, fix what is listed there, and run the same task again — "
            "nothing this run produced is accepted."
        )

    # -- the founder summary ---------------------------------------------

    def founder_summary(self) -> str:
        """Answer the ten questions, without requiring the raw logs.

        Ordered so the two that most often need action — an authority change and
        a local-history change — are impossible to scroll past.
        """
        start, end = self.start_identity, self.end_identity
        lines: list[str] = [
            "# Founder summary",
            "",
            f"Run `{self.run_id or '(unnamed)'}`  ·  started {self.started_at}"
            f"{'  ·  ended ' + self.ended_at if self.ended_at else '  ·  (not finished)'}",
            "",
        ]
        lines += self.personal_summary_lines()
        lines += [
            "",
            "---",
            "",
            "## 1. What did the Driver work on?",
            "",
            f"- Repository: `{self.repo or '(unrecorded)'}`",
            f"- Task: {redact(self.task) or '(none recorded)'}",
            f"- Active unit: {self.active_unit_id or '(none resolved)'}"
            f"{f' ({self.active_unit_status})' if self.active_unit_status else ''}",
        ]
        if self.acceptance_criteria:
            lines.append(f"- Acceptance criteria in force: {len(self.acceptance_criteria)}")
            lines.extend(f"    - {c}" for c in self.acceptance_criteria[:20])

        lines += ["", "## 2. What changed?", ""]
        if self.file_changes:
            counts: dict[str, int] = {}
            for change in self.file_changes:
                counts[change.change] = counts.get(change.change, 0) + 1
            lines.append("- Files: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
            for change in self.file_changes[:40]:
                suffix = f" (from {change.renamed_from})" if change.renamed_from else ""
                lines.append(f"    - {change.change}: {change.path}{suffix}")
            if len(self.file_changes) > 40:
                lines.append(f"    - ... and {len(self.file_changes) - 40} more")
        else:
            lines.append("- No file changes were recorded.")

        if self.commits:
            lines.append(f"- Local commits created: {len(self.commits)}")
            lines.extend(f"    - {c.sha[:12]} {c.subject}" for c in self.commits)
        else:
            lines.append("- No local commits were created.")

        lines += ["", "## 3. What evidence proves it?", ""]
        if self.test_results or self.scenario_results:
            for result in self.test_results:
                lines.append(f"- test `{result['name']}`: "
                             f"{'PASS' if result['passed'] else 'FAIL'}")
            for result in self.scenario_results:
                lines.append(f"- scenario `{result['name']}`: "
                             f"{'PASS' if result['passed'] else 'FAIL'}")
        else:
            lines.append("- No test or scenario results were recorded.")
        lines.append(f"- Tool uses recorded: {len(self.tool_uses)}; "
                     f"commands recorded: {len(self.commands)}")
        failed = [c for c in self.commands if c.exit_code not in (0, None) or c.timed_out]
        if failed:
            lines.append(f"- Commands that failed or timed out: {len(failed)}")
            for command in failed[:10]:
                status = "TIMEOUT" if command.timed_out else f"exit {command.exit_code}"
                lines.append(f"    - [{status}] {command.command[:160]}")

        lines += ["", "## 4. What was preserved?", ""]
        if self.recovery_points:
            lines.extend(f"- {point}" for point in self.recovery_points)
        else:
            lines.append("- Nothing needed preserving (no local history was transformed).")

        lines += ["", "## 5. What remains incomplete?", ""]
        if self.incomplete:
            lines.extend(f"- {item}" for item in self.incomplete)
        else:
            lines.append("- Nothing was recorded as incomplete.")

        lines += ["", "## 6. Did any authority file change?", ""]
        changes = _authority_changes_from(self.authority_report)
        lines.extend(f"    {line}" if line else "" for line in render_authority_section(changes))

        lines += ["", "## 7. Did any local history change?", ""]
        if self.history_changes:
            for change in self.history_changes:
                verified = (
                    "verified" if change.verified
                    else "NOT VERIFIED" if change.verified is False
                    else "unverified"
                )
                lines.append(
                    f"- {change.what} — "
                    f"{'authorized' if change.authorized else 'REFUSED'}, {verified}"
                )
                if change.detail:
                    lines.append(f"    {change.detail}")
        else:
            lines.append("- No local history was rewritten.")

        lines += ["", "## 8. Where is the recovery point?", ""]
        if start:
            lines.append(f"- Run started at `{start.head[:12]}` on `{start.branch}` "
                         f"(tree `{start.tree[:12]}`)")
            lines.append(f"- Restore with: `git reset --hard {start.head}`  "
                         "(review your working tree first)")
        else:
            lines.append("- No starting identity was recorded.")
        for point in self.recovery_points:
            lines.append(f"- {point}")

        lines += ["", "## 9. Was any external action attempted or denied?", ""]
        denied = [t for t in self.tool_uses if t.denied_reason]
        if self.external_attempts:
            lines.append(f"- **External-boundary attempts: {len(self.external_attempts)}**")
            for attempt in self.external_attempts:
                lines.append(f"    - {attempt.tool}: {attempt.detail[:160]}")
                lines.append(f"        denied — {attempt.reason}")
        else:
            lines.append("- No external action was attempted.")
        if denied:
            lines.append(f"- Total denied operations: {len(denied)}")
            for entry in denied[:20]:
                lines.append(f"    - [{entry.layer or 'guard'}] {entry.tool}: {entry.denied_reason}")
        if self.denied_paths:
            lines.append(f"- Writes denied outside the approved roots: {len(self.denied_paths)}")
            for entry in self.denied_paths[:20]:
                lines.append(f"    - {entry['path']}: {entry['reason']}")
        lines.append("- Nothing was pushed, deployed, published or sent externally: "
                     "the driver control process performs no remote action, and every "
                     "attempt above was denied before execution.")

        lines += ["", "## 10. What decision is required from the founder?", ""]
        if self.founder_decision_required:
            lines.append(f"- **{self.founder_decision_required}**")
        elif self.controls_weakened:
            lines.append("- **A mandatory control was removed or weakened — review section 6 "
                         "before accepting this run.**")
        else:
            lines.append("- None recorded.")

        lines += ["", "---", "",
                  f"**Stop reason:** {self.stop_reason or '(not recorded)'}", "",
                  f"**Next safe action:** {self.next_safe_action or '(not recorded)'}", ""]

        lines += ["## Git identity", "",
                  "| | start | end |", "|---|---|---|"]
        lines.append(f"| branch | `{start.branch if start else '?'}` | "
                     f"`{end.branch if end else '?'}` |")
        lines.append(f"| HEAD | `{(start.head[:12] if start else '?') or '(none)'}` | "
                     f"`{(end.head[:12] if end else '?') or '(none)'}` |")
        lines.append(f"| tree | `{(start.tree[:12] if start else '?') or '(none)'}` | "
                     f"`{(end.tree[:12] if end else '?') or '(none)'}` |")
        lines.append(f"| tracked dirty | {start.tracked_dirty if start else '?'} | "
                     f"{end.tracked_dirty if end else '?'} |")
        lines.append(f"| untracked | {start.untracked if start else '?'} | "
                     f"{end.untracked if end else '?'} |")
        lines.append("")
        if self.approved_roots:
            lines += ["## Approved write roots", ""]
            lines.extend(f"- {root}" for root in self.approved_roots)
            lines.append("")

        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_EXTERNAL_MARKERS = (
    "push", "publish", "deploy", "remote", "github cli", "cloud cli", "aws",
    "outbound", "payment", "communication", "production", "container push",
    "package publish", "external service",
)


def _one_line(text: str, limit: int) -> str:
    """Collapse to a single line and bound it. Multi-line prose in a bullet list
    silently reflows into the surrounding markdown and reads as new claims."""
    collapsed = " ".join(str(text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _is_external_boundary(reason: str) -> bool:
    lowered = reason.lower()
    return any(marker in lowered for marker in _EXTERNAL_MARKERS)


def _authority_changes_from(report: dict[str, Any]) -> list[AuthorityChange]:
    """Rebuild AuthorityChange objects from a serialized report, for rendering."""
    from .authority import AuthorityFinding

    out: list[AuthorityChange] = []
    for raw in report.get("changed", []) or []:
        change = AuthorityChange(
            path=raw.get("path", ""),
            existed_before=bool(raw.get("existed_before")),
            exists_after=bool(raw.get("exists_after")),
            before_sha=raw.get("before_sha", ""),
            after_sha=raw.get("after_sha", ""),
            diff=raw.get("diff", ""),
        )
        change.findings = [
            AuthorityFinding(
                path=f.get("path", ""),
                kind=f.get("kind", ""),
                detail=f.get("detail", ""),
                before_line=f.get("before_line", ""),
                after_line=f.get("after_line", ""),
            )
            for f in raw.get("findings", []) or []
        ]
        out.append(change)
    return out

"""The external verification gate: evidence Product Driver cannot produce itself.

A phase acceptance criterion of the form "CI is green on the accepted tree" is
settled somewhere Product Driver does not run, by something Product Driver does
not own, about a tree Product Driver can name exactly. Those three facts decide
the whole design:

* **it is generic.** Nothing here knows about GitHub. The gate is named by the
  repository's own criterion, the evidence is a small typed record, and the
  adapter that fetches it is a command a human wrote into the driver config. A
  repository verified by Buildkite, a self-hosted runner, or a person reading a
  dashboard uses the same interface;
* **it is SHA-bound.** Evidence for the wrong tree is not weaker evidence, it is
  evidence about something else. :meth:`ExternalEvidence.satisfies` refuses it
  with the two SHAs side by side, and this is the check that survives a resume;
* **it can wait.** When no adapter is configured, the controller enters
  ``WAITING_FOR_EXTERNAL_VERIFICATION`` carrying everything a resume needs — the
  expected SHA, the gate name, the workflow, and the schema of the record to
  supply. That state is a legitimate resting place, not a failure.

The probe adapter is deliberately narrow. It runs one command the operator
wrote, it is checked against Product Driver's own command guard first, and a
command the guard would refuse for a builder is refused here too. Product
Driver reading CI is not a licence to mutate a remote.
"""

from __future__ import annotations

import json
import re
import subprocess
from enum import Enum
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .models import redact, utcnow

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


class ExternalStatus(str, Enum):
    """What the external verifier says about one tree."""

    NOT_REQUIRED = "NOT_REQUIRED"
    #: Required, and nothing has been supplied for the candidate tree.
    AWAITING = "AWAITING"
    #: Supplied and green on the exact candidate tree.
    SUCCESS = "SUCCESS"
    #: Supplied and red on the exact candidate tree.
    FAILURE = "FAILURE"
    #: Supplied, still running.
    PENDING = "PENDING"
    #: Supplied, and about a different tree. Not weaker evidence — other
    #: evidence.
    WRONG_TREE = "WRONG_TREE"
    #: The verifier itself did not work: cancelled, infrastructure error, a
    #: probe that could not run. Owned by CI, never by the product.
    INFRASTRUCTURE = "INFRASTRUCTURE"


#: Conclusions a verifier reports that mean "green".
_SUCCESS_WORDS = ("success", "succeeded", "passed", "pass", "green", "ok")
#: Conclusions that mean "the verifier broke", not "the thing verified is wrong".
_INFRASTRUCTURE_WORDS = (
    "cancelled",
    "canceled",
    "timed_out",
    "timedout",
    "action_required",
    "stale",
    "startup_failure",
    "infrastructure",
    "error",
)
#: Conclusions that mean "red".
_FAILURE_WORDS = ("failure", "failed", "red", "neutral")
_PENDING_WORDS = ("in_progress", "queued", "pending", "waiting", "running", "requested")


class ExternalRequirement(BaseModel):
    """What external verification this phase acceptance owes, and about what."""

    model_config = ConfigDict(extra="ignore")

    required: bool = False
    #: The gate's name, as the repository's criterion calls it.
    gate_name: str = "external verification"
    #: The criteria this gate settles. Empty when the requirement came from
    #: configuration rather than from a criterion.
    criterion_ids: list[str] = Field(default_factory=list)
    #: The exact commit the evidence must be about.
    expected_sha: str = ""
    #: The workflow / pipeline / job the repository names, when it names one.
    workflow: str = ""
    #: What a resume must supply. Written out rather than assumed, because the
    #: operator supplying it by hand is a supported path.
    evidence_schema: dict[str, str] = Field(
        default_factory=lambda: {
            "gate_name": "the gate this record is about",
            "sha": "the exact commit the verifier ran against",
            "status": "SUCCESS | FAILURE | PENDING | INFRASTRUCTURE",
            "conclusion": "the verifier's own word for the outcome",
            "url": "where a human can check this (optional)",
            "run_id": "the verifier's own id for the run (optional)",
        }
    )

    def waiting_block(self) -> str:
        """Exactly what the founder has to do, and exactly what to bring back."""
        lines = [
            f"WAITING_FOR_EXTERNAL_VERIFICATION: {self.gate_name}",
            f"  expected tree:   {self.expected_sha or '(none captured)'}",
            f"  workflow:        {self.workflow or '(the repository names none)'}",
            f"  settles:         {', '.join(self.criterion_ids) or '(no criterion named)'}",
            "  supply evidence with:",
            "    python -m neyma_product_driver phase external-evidence --file <record.json>",
            "  the record must contain:",
        ]
        lines += [f"    {k}: {v}" for k, v in self.evidence_schema.items()]
        lines.append(
            "  evidence for any other commit is refused: it is a fact about a different tree."
        )
        return "\n".join(lines)


class ExternalEvidence(BaseModel):
    """One verifier's report about one tree."""

    model_config = ConfigDict(extra="ignore")

    gate_name: str = ""
    sha: str = ""
    status: ExternalStatus = ExternalStatus.AWAITING
    #: The verifier's own word, kept verbatim.
    conclusion: str = ""
    url: str = ""
    run_id: str = ""
    observed_at: str = Field(default_factory=utcnow)
    #: How this record reached Product Driver: "probe", "operator", "resume".
    source: str = "operator"
    detail: str = ""

    def satisfies(self, requirement: ExternalRequirement) -> tuple[bool, str]:
        """Whether this record discharges ``requirement``. Fails closed.

        The SHA comparison is prefix-tolerant: a short SHA that prefixes the
        expected one is the same commit, and a long SHA that the expected one
        prefixes is too — but only for real object ids abbreviated no shorter
        than :data:`MIN_ABBREVIATED_SHA`. Anything else is a different tree, and
        no amount of green makes it this tree's evidence.
        """
        if not requirement.required:
            return True, "no external verification is required"
        if not requirement.expected_sha:
            return False, "the candidate tree's commit was never captured, so nothing can match it"
        if not self.sha:
            return False, "the evidence names no commit, so it cannot be about this tree"
        if not same_commit(self.sha, requirement.expected_sha):
            return False, (
                f"the evidence is about {self.sha[:12]} and the candidate tree is "
                f"{requirement.expected_sha[:12]} — a different tree"
            )
        if self.status is ExternalStatus.SUCCESS:
            return True, (
                f"{self.gate_name or requirement.gate_name} {self.conclusion or 'SUCCESS'} on "
                f"{self.sha[:12]}"
            )
        return False, (
            f"{self.gate_name or requirement.gate_name} is {self.status.value}"
            + (f" ({self.conclusion})" if self.conclusion else "")
            + f" on {self.sha[:12]}"
        )

    def brief(self) -> str:
        return (
            f"{self.gate_name or 'external gate'}: {self.status.value}"
            + (f" ({self.conclusion})" if self.conclusion else "")
            + f" @ {self.sha[:12] or '(no sha)'}"
        )


#: The shortest abbreviation this gate will read as naming a commit. Git's own
#: default abbreviation is 7 characters; below that a "prefix" stops being an
#: identity claim at all — one hex character matches a sixteenth of every tree
#: there is, and evidence that matches a sixteenth of all trees is not evidence
#: about this one.
MIN_ABBREVIATED_SHA = 7

#: SHA-1 object ids are 40 hex characters and SHA-256 ones 64. A value outside
#: that shape is not an object id, whatever else it may be.
_OBJECT_ID = re.compile(rf"[0-9a-f]{{{MIN_ABBREVIATED_SHA},64}}")


def _object_id(value: Any) -> str:
    """``value`` as a canonical object id, or ``""`` when it is not one."""
    candidate = str(value or "").strip().lower()
    return candidate if _OBJECT_ID.fullmatch(candidate) else ""


def same_commit(a: Any, b: Any) -> bool:
    """Whether two possibly abbreviated object ids name the same commit.

    The single place Product Driver decides that a piece of evidence is about a
    given tree. Prefix-tolerant in both directions, but only between real
    hexadecimal object ids of at least :data:`MIN_ABBREVIATED_SHA` characters:
    below that floor, and for anything that is not an object id, this is False.
    A gate whose whole purpose is binding evidence to one tree cannot accept
    ``"a"`` as evidence about ``abc123...``.
    """
    a, b = _object_id(a), _object_id(b)
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def classify_conclusion(status: Any, conclusion: Any = "") -> ExternalStatus:
    """Turn a verifier's own words into one of our statuses. Never guesses green.

    The order matters: an infrastructure word wins over a status of
    ``completed``, because a cancelled run is a broken verifier reported as a
    finished one, and reading it as a failure of the product is how a CI outage
    becomes a series of product corrections.
    """
    blob = f"{status} {conclusion}".strip().lower()
    if not blob:
        return ExternalStatus.AWAITING
    if any(w in blob for w in _INFRASTRUCTURE_WORDS):
        return ExternalStatus.INFRASTRUCTURE
    if any(w in blob for w in _SUCCESS_WORDS):
        return ExternalStatus.SUCCESS
    if any(w in blob for w in _FAILURE_WORDS):
        return ExternalStatus.FAILURE
    if any(w in blob for w in _PENDING_WORDS):
        return ExternalStatus.PENDING
    return ExternalStatus.AWAITING


def evidence_from_payload(
    payload: Any, *, source: str = "operator", default_gate: str = ""
) -> ExternalEvidence:
    """Build evidence from whatever the operator or the probe produced.

    Tolerant about shape and strict about meaning: a list is read as "the runs
    the verifier reported, most recent first", unknown keys are ignored, and a
    payload with no status at all becomes AWAITING rather than anything better.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return ExternalEvidence(
                gate_name=default_gate,
                status=ExternalStatus.INFRASTRUCTURE,
                source=source,
                detail=f"the evidence was not readable JSON: {redact(payload)[:300]}",
            )
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return ExternalEvidence(
            gate_name=default_gate,
            status=ExternalStatus.INFRASTRUCTURE,
            source=source,
            detail="the evidence was not an object",
        )

    sha = str(
        payload.get("sha")
        or payload.get("head_sha")
        or payload.get("headSha")
        or payload.get("commit")
        or payload.get("revision")
        or ""
    )
    raw_status = payload.get("status") or payload.get("state") or ""
    conclusion = str(payload.get("conclusion") or payload.get("result") or "")
    explicit = str(raw_status).strip().upper()
    if explicit in ExternalStatus.__members__:
        status = ExternalStatus[explicit]
    else:
        status = classify_conclusion(raw_status, conclusion)
    return ExternalEvidence(
        gate_name=str(payload.get("gate_name") or payload.get("workflow") or default_gate or ""),
        sha=sha,
        status=status,
        conclusion=conclusion or str(raw_status),
        url=str(payload.get("url") or payload.get("html_url") or ""),
        run_id=str(payload.get("run_id") or payload.get("databaseId") or payload.get("id") or ""),
        source=source,
    )


# --------------------------------------------------------------------------
# The probe adapter
# --------------------------------------------------------------------------

#: Command heads a probe may use. Human-configured commands still go through
#: :func:`~neyma_product_driver.command_guard.classify_command` first; this is
#: the second, narrower fence, so a probe cannot become a general shell.
_PROBE_HEADS = ("gh", "curl", "git", "jq", "cat", "python", "python3", "buildkite-agent", "glab")

_SHA_TOKEN = re.compile(r"\{sha\}|\$\{SHA\}|%SHA%")


class ProbeRefused(Exception):
    """A configured probe command Product Driver will not run."""


def check_probe_command(command: str) -> str:
    """Why this probe may not be run, or ``""`` when it may.

    Two fences, in order. The first is Product Driver's own command guard,
    which already knows every action the driver must never perform on the
    owner's behalf — a push, a deploy, a secret read, a mutating HTTP request.
    The second is this module's own: the head must be one of a short list, and
    a command that chains or substitutes is refused outright, because the
    guard's verdict is about the command it was shown.
    """
    from .command_guard import classify_command

    text = str(command or "").strip()
    if not text:
        return "no probe command is configured"
    reason = classify_command(text)
    if reason:
        return f"the command guard refuses it: {reason}"
    if re.search(r"[;&|`]|\$\(|\bxargs\b|\beval\b", text):
        return (
            "the probe chains, pipes or substitutes commands; only one plain command "
            "can be checked, so only one is allowed"
        )
    head = text.split()[0].rsplit("/", 1)[-1]
    if head not in _PROBE_HEADS:
        return (
            f"{head!r} is not one of the programs a verification probe may run "
            f"({', '.join(_PROBE_HEADS)})"
        )
    return ""


def run_probe(
    command: str,
    *,
    expected_sha: str,
    cwd: Any = None,
    timeout_s: int = 120,
    gate_name: str = "",
) -> ExternalEvidence:
    """Run one configured read-only probe and read its output as evidence.

    ``{sha}`` in the command is replaced by the candidate commit, so one
    configured line serves every phase. A refused, failing or unreadable probe
    produces INFRASTRUCTURE evidence naming why — never an absence, and never
    something that could be mistaken for a red product.
    """
    refusal = check_probe_command(command)
    if refusal:
        return ExternalEvidence(
            gate_name=gate_name,
            sha=expected_sha,
            status=ExternalStatus.INFRASTRUCTURE,
            source="probe",
            detail=f"the probe was not run: {refusal}",
        )
    rendered = _SHA_TOKEN.sub(expected_sha, str(command))
    try:
        proc = subprocess.run(
            rendered,
            shell=True,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ExternalEvidence(
            gate_name=gate_name,
            sha=expected_sha,
            status=ExternalStatus.INFRASTRUCTURE,
            source="probe",
            detail=f"the probe could not run: {type(exc).__name__}: {redact(str(exc))[:300]}",
        )
    if proc.returncode != 0:
        return ExternalEvidence(
            gate_name=gate_name,
            sha=expected_sha,
            status=ExternalStatus.INFRASTRUCTURE,
            source="probe",
            detail=(
                f"the probe exited {proc.returncode}: "
                f"{redact((proc.stderr or proc.stdout or '').strip())[:400]}"
            ),
        )
    evidence = evidence_from_payload(
        (proc.stdout or "").strip(), source="probe", default_gate=gate_name
    )
    if not evidence.sha:
        # A probe that reports no commit has told us about something, and we
        # cannot tell what. Recorded as such rather than credited to this tree.
        evidence.status = ExternalStatus.INFRASTRUCTURE
        evidence.detail = (
            "the probe's output named no commit, so it cannot be evidence about the "
            "candidate tree"
        )
    return evidence


def requirement_from_criteria(
    criteria: Sequence[Any],
    *,
    expected_sha: str,
    gate_name: str = "",
    workflow: str = "",
) -> ExternalRequirement:
    """Build the requirement from the criteria that actually demand it."""
    ids = [str(getattr(c, "criterion_id", "") or "") for c in criteria]
    ids = [i for i in ids if i]
    if not ids:
        return ExternalRequirement(required=False, expected_sha=expected_sha)
    name = gate_name or str(getattr(criteria[0], "name", "") or "") or "external verification"
    return ExternalRequirement(
        required=True,
        gate_name=name,
        criterion_ids=ids,
        expected_sha=expected_sha,
        workflow=workflow,
    )

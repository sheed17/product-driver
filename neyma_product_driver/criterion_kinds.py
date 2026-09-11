"""What kind of thing an acceptance criterion is, read from what it is called.

One vocabulary, read by every module that has to ask. A phase's criteria come in
kinds that are settled by different authorities, and each consumer used to keep
its own copy of the words that identify them:

    independent review    settled only by a session outside the build lineage
    external verification settled only by an external verifier (CI or similar)
    residual ledger       settled by the phase's own record of carried debt
    everything else       the implementation the phase is BUILT to satisfy

The copies drifted. The completion auditor and the review cycle recognised
``independent_review`` and ``final_adjudication``; the phase authority also
recognised ``independent_phase_review`` and ``non_builder``. A repository that
named its review criterion ``independent_phase_review_by_a_non_builder`` was a
phase-review criterion to one of them and an ordinary unmet criterion to the
other two — so a phase whose only open obligation was that review was reported
as generically UNPROVEN instead of being routed to the fresh session it needs.

**Independent review is recognised by meaning, not by a list of names.** A
criterion is one when its identifier says a review (or an adjudication, or a
sign-off) is performed *by someone other than the builder*: an independence
qualifier attached to the review noun ("independent review", "independent
phase review", "non-builder sign-off"), a review attributed to such a party
("review by a non builder", "reviewed by a session outside the build"), or a
final adjudication. The words must be attached to each other. A criterion that
merely contains both words somewhere — "reviewer UI shows independent totals" —
is about a screen, and stays an implementation criterion.

Only a criterion's IDENTIFIERS are read (its id and its short name), never its
requirement prose. Requirement text describes how a criterion is verified and
routinely mentions review; reading it would turn half a phase into review
criteria.

Nothing here reads a repository, and nothing here names a product or a phase.
"""

from __future__ import annotations

import re
from enum import Enum


class CriterionKind(str, Enum):
    """Which authority settles a criterion."""

    #: Only a session outside the build lineage may award it.
    INDEPENDENT_REVIEW = "INDEPENDENT_REVIEW"
    #: Only an external verifier — CI or equivalent — may settle it.
    EXTERNAL_VERIFICATION = "EXTERNAL_VERIFICATION"
    #: The phase's residual ledger settles it, not a test.
    RESIDUAL_LEDGER = "RESIDUAL_LEDGER"
    #: What the phase is built to satisfy. The builder owes the implementation
    #: and its evidence; the phase's acceptance still scores it.
    IMPLEMENTATION = "IMPLEMENTATION"


#: The kinds a LATER gate settles. A build session owes none of them and may
#: award none of them; phase closure takes each to its own authority.
GATE_KINDS = frozenset(
    {
        CriterionKind.INDEPENDENT_REVIEW,
        CriterionKind.EXTERNAL_VERIFICATION,
        CriterionKind.RESIDUAL_LEDGER,
    }
)


# --------------------------------------------------------------------------
# Independent review — recognised by the shape of the phrase
# --------------------------------------------------------------------------

#: The act being performed. A review, an adjudication, or a sign-off.
_REVIEW_NOUNS = frozenset(
    {
        "review",
        "reviews",
        "reviewed",
        "reviewer",
        "reviewers",
        "reviewing",
        "adjudication",
        "adjudicated",
        "adjudicate",
        "adjudicator",
        "signoff",
    }
)

#: Words that say the act comes from somebody other than the builder. Each is
#: meaningful only ATTACHED to a review noun; on its own, "independent" is as
#: likely to describe a tenant or a clock.
_INDEPENDENCE = frozenset(
    {
        "independent",
        "independently",
        "nonbuilder",
        "fresh",
        "outside",
        "external",
        "separate",
        "thirdparty",
        "unaffiliated",
    }
)

#: Words allowed BETWEEN an independence qualifier and the review noun it
#: qualifies: "independent PHASE review", "fresh SESSION review". Deliberately
#: short. "fresh data review" is a screen, not a second opinion.
_SCOPE_WORDS = frozenset(
    {"phase", "unit", "checkpoint", "milestone", "session", "party", "formal", "peer", "code", "change", "diff", "acceptance"}
)

#: Articles and fillers allowed between "by" and the party named after it:
#: "review by A non builder", "reviewed by AN independent session".
_FILLERS = frozenset({"a", "an", "the", "one", "some", "someone", "somebody", "session", "sessions", "party"})

#: Qualifiers that make an ADJUDICATION the phase's own independent one.
#: "final adjudication" is the phrase repositories use for it.
_ADJUDICATION_QUALIFIERS = frozenset({"final", "formal", "phase"}) | _INDEPENDENCE


def _words(*texts: str) -> list[str]:
    """Identifier words, lowercased, with compounds normalised.

    ``independent_phase_review_by_a_non_builder``, ``IndependentPhaseReview``
    and ``independent-phase-review`` all read the same way. "non builder",
    "sign off" and "third party" are joined, because each is one idea spelt as
    two words.
    """
    tokens: list[str] = []
    for text in texts:
        raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text or ""))
        tokens += [t for t in re.split(r"[^a-z0-9]+", raw.lower()) if t]
    joined: list[str] = []
    i = 0
    pairs = {("non", "builder"): "nonbuilder", ("sign", "off"): "signoff", ("third", "party"): "thirdparty"}
    while i < len(tokens):
        pair = tuple(tokens[i : i + 2])
        if len(pair) == 2 and pair in pairs:
            joined.append(pairs[pair])  # type: ignore[index]
            i += 2
            continue
        joined.append(tokens[i])
        i += 1
    return joined


def _qualified_review(words: list[str]) -> bool:
    for index, word in enumerate(words):
        if word not in _REVIEW_NOUNS:
            continue
        # 1. A qualifier in front of it, directly or across one scope word:
        #    "independent review", "independent phase review".
        before = words[max(0, index - 2) : index]
        if before and before[-1] in _INDEPENDENCE:
            return True
        if len(before) == 2 and before[0] in _INDEPENDENCE and before[1] in _SCOPE_WORDS:
            return True
        # 2. Attributed to such a party after "by": "review by a non builder",
        #    "reviewed by a session outside the build".
        after = words[index + 1 : index + 6]
        if after and after[0] == "by":
            for follower in after[1:]:
                if follower in _INDEPENDENCE:
                    return True
                if follower not in _FILLERS:
                    break
        # 3. The phase's own final adjudication.
        if word.startswith("adjudicat") and before and before[-1] in _ADJUDICATION_QUALIFIERS:
            return True
    return False


def is_independent_review_criterion(criterion_id: str = "", name: str = "") -> bool:
    """Whether only a session outside the build lineage may award this criterion.

    Read from the criterion's id and short name only. See the module docstring
    for the rule and why it is a rule about attached words rather than a list
    of names.
    """
    return _qualified_review(_words(criterion_id, name))


# --------------------------------------------------------------------------
# External verification and the residual ledger — substring vocabularies
# --------------------------------------------------------------------------
#
# Moved here unchanged from ``phase_authority`` so there is one copy. Their
# semantics are the ones the phase-closure controller has always applied.

#: Criterion names that name an EXTERNAL verification gate — CI or equivalent.
EXTERNAL_CRITERION_MARKERS = (
    "ci_green",
    "ci green",
    "ci_",
    "continuous_integration",
    "external_verification",
    "workflow_green",
    "pipeline_green",
    "build_green",
)

#: Criterion names that are settled by the RESIDUAL LEDGER rather than by any
#: test — "the carried residuals are recorded and none of them blocks".
RESIDUAL_CRITERION_MARKERS = (
    "residual",
    "carried_debt",
    "carried debt",
    "open_risks",
    "debt_recorded",
)


def is_external_verification_criterion(
    criterion_id: str = "", name: str = "", requirement: str = ""
) -> bool:
    """Whether an external verifier, not a session, settles this criterion."""
    blob = f"{criterion_id} {name} {requirement}".lower()
    return any(marker in blob for marker in EXTERNAL_CRITERION_MARKERS)


def is_residual_criterion(criterion_id: str = "", name: str = "") -> bool:
    """Whether the phase's residual ledger settles this criterion."""
    blob = f"{criterion_id} {name}".lower()
    return any(marker in blob for marker in RESIDUAL_CRITERION_MARKERS)


def criterion_kind(criterion_id: str = "", name: str = "", requirement: str = "") -> CriterionKind:
    """Which authority settles one criterion. Independent review is asked first:
    a criterion that is a review of the CI record is still a review."""
    if is_independent_review_criterion(criterion_id, name):
        return CriterionKind.INDEPENDENT_REVIEW
    if is_external_verification_criterion(criterion_id, name, requirement):
        return CriterionKind.EXTERNAL_VERIFICATION
    if is_residual_criterion(criterion_id, name):
        return CriterionKind.RESIDUAL_LEDGER
    return CriterionKind.IMPLEMENTATION

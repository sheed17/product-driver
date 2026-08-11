# Pre-registered definition of "useful" — written BEFORE any generated output of this
# certification session was read.
# Reviewer: A-QUALITY. Timestamp of writing: see git/file mtime.

An ACCEPTED scenario counts as USEFUL iff ALL of the following hold:

U1. EXECUTABLE ORACLE. It declares at least one assertion the executor mechanically
    scores: expect_status, expect_contains, expect_exit_code, a persisted_state_check
    with contains/not_contains, expected_observations/forbidden_observations, or a
    browser step with expect_text.

U2. FALSIFIABLE COUNTERFACTUAL. There is a plausible defect inside the task's risk
    surface that would flip at least one of its assertions from pass to fail. The
    assertion must be neither vacuous (true whatever the product does) nor
    unsatisfiable (false whatever the product does). Judged by hand on a sample.

U3. BEHAVIOUR-SPECIFIC PROBE. The scenario drives the product — request,
    parallel_requests, restart/stop/start_service, browser, or a targeted state read
    of product-owned state — rather than only invoking an existing broad test suite
    or a generic diagnostic script.

U4. ON-SURFACE. Its risk_category and purpose are plausibly implicated by the task's
    acceptance criteria or the diff it was shown.

U5. NON-REDUNDANT. It is not a duplicate shape of another accepted scenario in the
    same plan (same risk_category + same ordered action kinds + same oracle
    commands/assertions).

Separately and independently:

G1. HONEST GROUNDING. requirement_reference must name something that ACTUALLY EXISTS
    — the active unit id, the active unit name, or one of the acceptance criteria the
    brief showed it. product_principle_reference must name a founder rubric id that
    actually exists in founder_context. An AC-<AREA>-<nnn> id that appears nowhere in
    the product repository or the brief is FABRICATED GROUNDING, counts as NOT useful,
    and is recorded as a blocking-category defect.

A task is ZERO-COVERAGE when it yields 0 useful accepted scenarios.

## Pre-registered on-surface (U4) category sets for the NEW tasks F and G
Written from the task's own acceptance criteria and diff files, before any F or G
output existed. Inherited tasks A/A2-diff/B-diff/C/D/E reuse the prior campaign's
RELEVANT sets verbatim so the numbers stay comparable.

F (resilient notification delivery under partial transport failure):
  happy_path, idempotency, repeated_request, retry_safety, partial_failure,
  service_unavailable, dependency_failure, timeout_before_effect,
  timeout_after_effect, ambiguous_external_effect, safety_invariant, regression,
  persistence_failure, restart_recovery, crash_mid_workflow, concurrency,
  missing_data, unexpected_state_transition, stale_state

G (document evidence reconciliation under conflict):
  conflicting_evidence, malformed_input, missing_data, boundary, happy_path,
  stale_state, ui_backend_disagreement, safety_invariant, regression,
  unexpected_state_transition, idempotency, repeated_request,
  persistence_failure, cross_tenant

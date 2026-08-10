PRODUCT REVIEW — CORRECTION 1

The product-driver operated the running product and found a concrete
discrepancy. This is not a code review; these are observations from actually
using the product.

Active READY unit: U-042. Keep the fix inside its scope.

REQUIREMENT: U-042: an approved invoice is paid exactly once
PRODUCT PRINCIPLE: a button click, an HTTP 200 or a passing unit test is not success; the underlying outcome must be verified

SCENARIO EXECUTED: gen-approve-twice

OBSERVED RESULT: 3 generated case(s) + 1 permanent regression scenario(s): 3 passed, 1 failed, 0 blocked, 0 skipped
risk categories exercised: happy_path, idempotency, missing_data

FAILURES:
  [FAIL] P0 generated gen-approve-twice  (idempotency)  — expect_state: exactly one payment: contains 'payments=1' — not found in command output
      generated because: the approval effect may not be safe under idempotency
      verifies: U-042: an approved invoice is paid exactly once
      failed: expect_state: exactly one payment: contains 'payments=1' — not found in command output
      failed: expect_visible: payments=1 — not present in any observed output
      failed: forbidden: payments=2 — present in observed output
      evidence: /private/tmp/claude-501/-Users-sammyfammy-neyma-product-driver/e6274be2-8fc0-43b3-b810-1a6a33341c77/scratchpad/e2e-basetemp/test_generate_execute_learn_ex0/driver/runs/20260809-e2e/iteration-01/scenarios/gen-approve-twice

1 unresolved high-priority scenario failure(s)
This states verified coverage. It is not a claim that all possible cases were verified.

EXPECTED RESULT: Every required scenario passes: each situation the suite exercises produces the observable outcome it expects, and none of the forbidden observations.

EVIDENCE:
  - /private/tmp/claude-501/-Users-sammyfammy-neyma-product-driver/e6274be2-8fc0-43b3-b810-1a6a33341c77/scratchpad/e2e-basetemp/test_generate_execute_learn_ex0/driver/runs/20260809-e2e/iteration-01/scenarios/gen-approve-twice

SMALLEST JUSTIFIED CORRECTION:
SCENARIO SUITE FAILURES — the running product did not behave as the verification scenarios require.

3 generated case(s) + 1 permanent regression scenario(s): 3 passed, 1 failed, 0 blocked, 0 skipped


THESE FAILURES ARE DISTINCT and need separate attention:
  - gen-approve-twice: generated:gen-approve-twice
      exercised because: the approval effect may not be safe under idempotency
      observed: expect_state: exactly one payment: contains 'payments=1' — not found in command output
      observed: expect_visible: payments=1 — not present in any observed output
      observed: forbidden: payments=2 — present in observed output
      evidence: /private/tmp/claude-501/-Users-sammyfammy-neyma-product-driver/e6274be2-8fc0-43b3-b810-1a6a33341c77/scratchpad/e2e-basetemp/test_generate_execute_learn_ex0/driver/runs/20260809-e2e/iteration-01/scenarios/gen-approve-twice

Make the smallest correction that resolves the cause above. Do not change a scenario, delete an assertion, or weaken a guard to make this pass — the scenarios describe what the product promises, and a green result obtained by editing them is worth nothing.

MUST BE PRESERVED: All behaviour the passing scenarios already demonstrate, every permanent regression scenario, and every guard. Do not weaken or delete a scenario to obtain a green result.

RETEST: Re-run the scenario suite. The failed scenarios rerun first, their risk neighbours rerun with them, and the full required regression set must be green before acceptance.

Make the smallest correction that resolves the discrepancy above. Do not
refactor unrelated code. Do not commit or push. End with an updated
"RUNNABLE CHECKPOINT" section.

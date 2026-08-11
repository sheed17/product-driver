# B-ADAPTIVE — pre-registration

Written BEFORE any wave-2 output of this campaign existed. No run directory
under `verification-evidence/cert/B-ADAPTIVE/runs/` had been created when this
file was written; `runs/` was empty. The neighbourhood sets below are derived
only from the fixture's own docstring description of each seeded defect
(`verification-evidence/cert/B-ADAPTIVE/fixture/app.py`), not from any observed
model output.

## Seeded defects

| key | surface | prior campaign used it? |
|---|---|---|
| `nonidempotent` | repeated approval applies the payment effect twice | yes (anchor) |
| `ui_lies` | 200 + in-memory "approved", durable store never written | yes (anchor) |
| `authz_retry` | **NEW** authorization gate consulted only on the first attempt; a refused actor who retries is approved | no |
| `partial_dep` | **NEW** approval persists, downstream ledger write silently fails, endpoint still reports `"ledger":"recorded"` | no |
| `none` | correct; used only for the evidence-withheld CONTROL | yes |

## Targeting neighbourhoods (fixed before reading any wave-2 output)

Categories are members of `RiskCategory` in `neyma_product_driver/scenario_plan.py`.

```
nonidempotent : idempotency, repeated_request, retry_safety, concurrency
ui_lies       : persistence_failure, ui_backend_disagreement, restart_recovery,
                stale_state, crash_mid_workflow
authz_retry   : authorization, approval_required, cross_tenant, retry_safety,
                repeated_request
partial_dep   : partial_failure, dependency_failure, service_unavailable,
                ui_backend_disagreement
```

Two categories are deliberately shared between neighbourhoods (`retry_safety`
and `repeated_request` between `nonidempotent` and `authz_retry`;
`ui_backend_disagreement` between `ui_lies` and `partial_dep`), because the real
surfaces do overlap. Targeting is therefore reported twice: the plain own-hit
rate, and an EXCLUSIVE own-hit rate counting only categories that belong to this
defect's neighbourhood and to no other defect's.

## Measures

Over both the RAW pre-validation population (what the model proposed) and the
ACCEPTED population (what survived validation):

1. `risk_categories`
2. `title_tokens` (title + purpose)
3. `purpose_rationale_tokens`
4. `generating_risk_tokens` — the measure the prior campaign reported as still
   failing
5. `actions_and_oracles` — request paths/methods/bodies, state-probe commands,
   the literal needles asserted, and the forbidden needles

`source_failure` / `source_cluster` provenance is NOT a measure: validation
forces it, so it cannot evidence responsiveness.

## Decision rule (fixed in advance)

For each measure, mean pairwise Jaccard similarity in three classes:

* REPLICATE — same seeded defect, different run
* BETWEEN — different seeded defects
* CONTROL — any pair involving an evidence-withheld run

Responsive requires **BETWEEN < REPLICATE**, with **CONTROL lowest**.

Because pairwise scores share runs and are not independent, significance is
assessed by a **permutation test on the defect labels**: 20000 random
relabellings of runs to defect labels (preserving group sizes, controls
excluded), recomputing `mean(BETWEEN) - mean(REPLICATE)` each time. The reported
one-sided p is the fraction of relabellings whose difference is <= the observed
difference. p <= 0.05 is called significant; anything above is reported as a
margin the sample cannot support.

## Safety property, kept separate

An evidence-free wave must contribute ZERO accepted scenarios, because
validation refuses an adaptive scenario naming no source failure. This is
checked separately and is explicitly NOT counted as evidence of responsiveness.

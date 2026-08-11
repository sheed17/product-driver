# Pre-existing 30-mutation harness, re-run after cycle 1

Cycle 1 changed every source file the 30 mutations target, so a previously
caught mutation that now survived would be a regression in the safety net
rather than in the code. That is why this was re-run rather than assumed.

```
.venv/bin/python verification-evidence/post-remediation/run_mutations.py
28/30 mutations caught
```

## The two that did not report CAUGHT both came back COULD_NOT_APPLY

Neither **SURVIVED**. Both failed to apply, because cycle 1 changed the exact
lines their fragments were anchored to:

| id | description | detail |
|---|---|---|
| N10 | resume regenerates from wave zero | `fragment matched 0 times in neyma_product_driver/scenario_planner.py` |
| P7 | the control loop stops handing the risk register to the gate | `fragment matched 2 times in neyma_product_driver/cli.py` |

- **N10** anchored on `restore_from_store` returning `""`. The R2 fix replaced
  that with `PlanRestore(state="absent")`, so the fragment no longer exists.
- **P7** anchored on `risks=_identified_risks(planner),`. The CG-02 fix
  evaluates the gate at step 6b *and* applies it through
  `_apply_suite_precedence`, so the fragment now appears **twice** and could not
  be applied unambiguously.

This is anchor drift, and it has precedent in this repository: the previous pass
re-anchored **M5** for the same reason, and `run_mutations.py` documents it.

"The harness could not test it" is not "the requirement is still covered", so
both were re-anchored and re-run.

## Re-anchored result

`reanchor_n10_p7.py` in this directory, isolated `git worktree`, working tree
never mutated:

```
N10: CAUGHT  (1x anchored)  resume regenerates from wave zero
     FAILED tests/test_remediation_contract.py::TestResumePreservesAdaptiveState::test_a_resumed_planner_continues_from_the_persisted_plan
P7:  CAUGHT  (2x anchored)  the control loop stops handing the risk register to the gate
     FAILED tests/test_post_remediation_contract.py::TestUncoveredRisksReachAcceptance::test_dropping_the_risk_register_at_the_call_site_is_caught
```

Both are caught by **the same test that caught them in the committed baseline**,
so the coverage is the original coverage, not a substitute.

**28/30 applied + 2/2 re-anchored = 30/30 requirements covered.** No regression
in the safety net.

## Recommendation to the controller — anchor updates

The builder may not edit
`verification-evidence/post-remediation/run_mutations.py`. These fragments are
proved to work and are offered for the controller to place:

**N10** — `neyma_product_driver/scenario_planner.py`

```python
old = ('        if self.store is None:\n'
       '            return PlanRestore(state="absent")\n'
       '        path = self.store.run_dir / PLAN_FILENAME')
new = ('        return PlanRestore(state="absent")\n'
       '        if self.store is None:\n'
       '            return PlanRestore(state="absent")\n'
       '        path = self.store.run_dir / PLAN_FILENAME')
```

**P7** — `neyma_product_driver/cli.py`. The fragment is unchanged; what changed
is that it must now be replaced at **both** call sites (the 6b gate evaluation
and `_apply_suite_precedence`), which is the faithful reproduction of "the
control loop stops handing the risk register to the gate". The harness's
`apply_mutation` replaces a single occurrence, so it needs a count-aware form or
two distinct anchors.

## Observation for the controller — the harness writes into the review record

`run_mutations.py` writes its results to
`verification-evidence/post-remediation/mutation-results.json` (line 237). So
*running* the harness modifies the review record, which the builder is forbidden
to touch.

This session ran the harness and therefore overwrote that file. It was restored
with `git checkout HEAD -- verification-evidence/post-remediation/mutation-results.json`,
and `git status` confirms it is back to its committed state. Nothing of
substance was lost: the pre-run content was the controller's own 30/30 re-run,
which had already been proved byte-identical to `HEAD` after normalising
wall-clock timing strings.

Recorded so the next role knows that running this harness is not a read-only
act. **NB-7.**

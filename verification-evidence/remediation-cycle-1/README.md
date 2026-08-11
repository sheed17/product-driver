# Remediation cycle 1 — evidence

Written by REMEDIATION-BUILDER-1. This directory contains only the builder's own
reproduction harness. Nothing outside `verification-evidence/remediation-cycle-1/`
was written.

## What is here

| file | what it is |
|---|---|
| `prove_tests_fail_unfixed.sh` | Builds a pristine tree from the certification candidate with `git archive`, drops `tests/test_remediation_cycle_1.py` into it, and runs it there. Read-only with respect to the working tree and to git history. |
| `baseline-unfixed.txt` | The recorded output of that script against `537ae0b`: **56 failed, 9 passed**. |

Re-run it with `./prove_tests_fail_unfixed.sh [commit]`.

The nine tests that pass on the unfixed candidate are all *controls* — they
assert that a legitimate capability was **not** lost, so they must pass both
before and after:

- `test_a_data_fixture_is_still_permitted[invoice.json|rows.csv|notes.txt|cfg.yaml]`
- `test_relative_navigation_still_passes[/|/operator/|/invoices/LD5600]`
- `test_a_credential_under_a_secret_shaped_key_is_still_masked`
- `test_a_loaded_page_does_not_earn_a_synthetic_assertion`

Two module-level imports are rewritten **in the copy only**, because the symbols
they name (`cli._recorded_suite_gate`, `scenario_validation.resolve_browser_target`)
are new and their absence would abort collection before any test ran. The test
bodies are byte-identical.

## Defect → test → fix map

| defect | regression test class in `tests/test_remediation_cycle_1.py` | source changed |
|---|---|---|
| CG-02 | `TestTheGatePrecedesEveryTerminalState` | `cli.py` |
| D-SAFETY-01 | `TestAFixtureCannotBeAProgram` | `scenario_validation.py`, `scenarios.py`, `scenario_generator.py`, `cli.py` |
| D-SAFETY-02 / F-4 | `TestBrowserNavigationIsAnAllowlist` | `scenario_validation.py`, `scenarios.py` |
| R1 / ADJ-G-01a | `TestRedactionPreservesTypes` | `models.py`, `scenario_planner.py` |
| R2 | `TestAnUnreadablePlanFailsClosed` | `scenario_planner.py`, `cli.py` |
| I1 / G-SCALE-02 | `TestEvidenceDirectoriesAreInjective` | `evidence.py`, `scenario_suite.py`, `scenario_validation.py`, `scenarios.py`, `scenario_planner.py` |
| G-SCALE-01 | `TestResumeTimeDropsAreVisible` | `scenario_planner.py`, `cli.py` |
| F-1 + F-2 | `TestABrowserScenarioMustHaveObservedSomething` | `scenarios.py`, `models.py`, `scenario_suite.py`, `cli.py` |
| F-3 | `TestEveryScreenTheRunLookedAtIsSearchable` | `scenarios.py`, `models.py` |

# Remediation cycle 1 — builder's record

**This is builder work. It is not a certification.** Nothing here has been
independently reviewed. The next legal action is a fresh reviewer that did not
build this, followed by a separate adjudicator.

Written by the cycle-1 remediation builder role. Two sessions held this role in
sequence: the first produced the implementation, was interrupted by a host
process exit, and was resumed; the second (this one) re-established state from
`git diff` rather than from memory, reconciled the partial work against the
adjudications, and performed the verification recorded below. No session that
wrote any of this code has reviewed or certified it.

## Starting state, established mechanically

| Fact | Value |
|---|---|
| Branch | `main` |
| HEAD at resume (prior candidate) | `537ae0b7c0c6524ce4ae88d1fc3bdbc483bf4707` |
| Parent / `origin/main` | `dcc649e6382e689413217e736a47c4e1739145b3` |
| Partial work found | 13 files, 1053 insertions, 135 deletions, unstaged |
| Untracked | `tests/test_remediation_cycle_1.py`, `.driver-state/`, `verification-evidence/cert/` |
| Baseline suite | 1115 passed |

Nothing was checked out, restored, stashed, reset, or discarded. The partial
tree was treated as an unfinished candidate and reconciled in place.

### The one modification inside `verification-evidence/`

`verification-evidence/post-remediation/mutation-results.json` was already
modified before this cycle began, and the builder is forbidden from editing the
review record. It was therefore proved harmless rather than assumed so:

- 60 changed lines, all inside `"detail"` strings
- 0 changed lines touching `status`, `id`, `description` or `file`
- 0 changed `CAUGHT` verdicts
- after normalising wall-clock timings, the file is **byte-identical** to `HEAD`

It is the campaign controller's own independent 30/30 re-run (mtime
2026-08-10 01:01:57), which predates this cycle. No builder incursion.

## The nine upheld blocking defects

Fix specifications are the adjudications' own, in
`verification-evidence/cert/ADJ-{C,D,E,F,G}/ADJUDICATION.md`.

| # | Defect | Clause violated | Fix site |
|---|---|---|---|
| 1 | CG-02 — gate skipped on the completion-audit terminal path | silently omit required verification | `cli.py` |
| 2 | D-SAFETY-01 — `fixture_content` executes as code via `{{fixture:}}` | execute generated actions outside its authority | `scenario_validation.py`, `scenario_generator.py` |
| 3 | D-SAFETY-02 / F-4 — `goto` scheme mismatch escapes loopback | outside authority; falsely ACCEPT; misattribute evidence | `scenario_validation.py`, `scenarios.py` |
| 4 | R1 / ADJ-G-01a — `redact_obj` corrupts the plan via `authorization` | lose critical state across resume | `models.py` |
| 5 | R2 — unreadable plan fails open and destroys the record | lose critical state across resume | `scenario_planner.py`, `cli.py` |
| 6 | I1 / G-SCALE-02 — evidence-directory collision credited as verified | lose/misattribute evidence; collapse identities | `evidence.py`, `scenario_suite.py`, `scenario_validation.py`, `scenarios.py` |
| 7 | G-SCALE-01 — resume-time drops invisible to the gate | lose state; omit verification; falsely ACCEPT | `scenario_planner.py` |
| 8 | F-1 + F-2 — browser scenario that observed nothing PASSES | falsely ACCEPT; omit verification | `models.py`, `scenarios.py`, `scenario_suite.py` |
| 9 | F-3 — browser text replaced rather than accumulated | falsely ACCEPT; misattribute evidence | `models.py`, `scenarios.py` |

### Shape of each fix

1. **CG-02.** The deterministic gate moved from step 6c to **6b** — ahead of
   every layer that judges *claims*. Measurement is those layers' input, not
   their peer. `_terminate` is now the single exit from the control loop, and
   `LoopResult` is `kw_only=True` so no future terminal can silently drop a
   field by constructing it positionally (which is how a run that executed a
   full suite reported no suite at all). When the gate has already overridden,
   the completion audit's findings are merged into `problems` rather than
   dropped — both layers refuse, both refusals survive.
2. **D-SAFETY-01.** `FIXTURE_DATA_EXTENSIONS`, a **data** allowlist. Content
   inspection is the wrong instrument: the payload is ordinary Python with
   nothing suspicious to match. A fixture whose path is substituted into an
   approved interpreter command is made inert by construction instead. The
   generator prompt states the rule it will be judged against.
3. **D-SAFETY-02 / F-4.** `resolve_browser_target` is now the single resolver,
   called by **both** the validator and the executor, so the string inspected
   and the string dialled cannot differ — they did. Shape is an allowlist
   (`http(s)://` or a single leading `/`); everything else is refused rather
   than normalised, because normalising an escape hides what was asked for.
4. **R1.** `_mask_secret_value` masks by type: strings masked, containers
   recursed, non-string scalars preserved. A credential is always text; turning
   `by_risk_category["authorization"]` from `int` into `"[REDACTED]"` protected
   nothing and made the run's own plan unparseable.
5. **R2.** `PlanRestore` splits "absent" from "unreadable" — previously one
   empty string, which is what made an unreadable plan fail *open*. Unreadable
   now preserves the file under a name nothing writes, reconstructs the spent
   wave budget from surviving per-wave records, records a generation problem the
   gate consumes, and `cmd_run` exits BLOCKED. `persist()` is atomic
   (stage + replace) and **round-trips the payload through the model before
   writing**, so a write-time transform cannot silently produce a file no resume
   can parse.
6. **I1 / G-SCALE-02.** Injective at four layers: `sanitize_filename` digests
   over the *original* input whenever folding or case changed it;
   `identity_key` (NFC + casefold) governs admission in `build_suite` and
   `_check_quality`; `SuiteExecutor` refuses a directory already claimed **and
   demotes the earlier occupant from PASSED to BLOCKED**, because a shared
   identity means its record no longer identifies it either; and permanent
   scenario names are validated at their source.
7. **G-SCALE-01.** A resume that drops committed scenarios writes a
   `STAGE_RESUME` wave record, which `generation_problems()` returns and
   `evaluate_gate` already consumes. Lost coverage reaches the gate instead of
   terminal scrollback.
8. **F-1 + F-2.** `BrowserObservation.page_loaded` is the floor under every
   browser oracle, because every other oracle is derived from something the page
   produced and `all([]) is True`. Enforced twice: a failing assertion in
   `_assert_browser_text`, and `never_observed` in the suite classifying it
   BLOCKED. Degraded exits (missing playwright, navigation raising) now record
   `step_failures` structurally, not only narration. `cmd_run` preflights
   chromium once at the top when browser support is enabled.
9. **F-3.** `observed_texts` accumulates page text at every point the page
   changed. `visible_text` keeps meaning "the last page"; the scenario-level
   haystack searches everything the run looked at, which is what the generator
   is promised.

## Verification

### 1. Full suite

```
1180 passed in 305.10s (0:05:05)     exit 0
```

Baseline 1115 + 65 new. Zero failures, zero skips. Run by this session, not
inherited from the prior builder's claim.

### 2. Regression coverage genuinely pins each defect

43 test functions (65 with parametrisation) in
`tests/test_remediation_cycle_1.py`, one class per defect.

Two independent proofs, each covering what the other cannot.

**Proof A — whole module against the unfixed candidate.**
`prove_tests_fail_unfixed.sh` builds a pristine tree from `537ae0b` with
`git archive`, drops the test file in, and runs it there. Two module-level
imports are rewritten *in the copy only*, because the symbols they name are new
and their absence would abort collection before any test ran; the test bodies
are byte-identical. Recorded in `baseline-unfixed.txt` and **re-run and
reproduced by this session**:

```
56 failed, 9 passed in 2.55s
```

The 9 passing tests were verified to be exactly the *control* set — tests
asserting a legitimate capability was **not** lost, which must therefore pass on
both sides: `test_a_data_fixture_is_still_permitted` ×4,
`test_relative_navigation_still_passes` ×3,
`test_a_credential_under_a_secret_shaped_key_is_still_masked`, and
`test_a_loaded_page_does_not_earn_a_synthetic_assertion`. **No defect test
passes on unfixed code.**

**Proof B — each fix reverted individually.** Proof A shows the tests fail
together; it does not show each test pins *its own* defect. So the full
remediation was applied to an isolated worktree and each fix reverted one at a
time, re-running only the matching test class:

| Mutation | Defect | Result |
|---|---|---|
| X1 | CG-02 gate ordering | CAUGHT (2 failed) |
| X2 | fixture data allowlist | CAUGHT (6 failed) |
| X3 | browser target allowlist | CAUGHT (10 failed) |
| X4 | type-preserving redaction | CAUGHT (3 failed) |
| X5 | unreadable plan fails closed | CAUGHT (4 failed) |
| X6 | filename injectivity | CAUGHT (1 failed) |
| X7 | resume drops reach the gate | CAUGHT (1 failed) |
| X8 | `page_loaded` floor (both layers) | CAUGHT (2 failed) |
| X9 | accumulated browser text | CAUGHT (2 failed) |

**Cycle-1 mutation score: 9/9.** No fix is unpinned.

Harness: `run_cycle1_mutations.py` in this directory (isolated `git worktree`;
the working tree was never mutated).

### 3. Hostile regression probes — the exploits themselves

Harmless and local only; no external host was contacted and fixture payloads
were inert markers. What is tested is that the boundary refuses.

- **12 navigation escapes** refused: `http:/evil.invalid/x`,
  `http:evil.invalid/x`, `http:\\evil.invalid\x`, `httpx://…`, `//evil.invalid/x`,
  `\\evil.invalid\x`, `https:/…`, `HTTP://…`, `file://`, `javascript:`, `data:`,
  and a NUL-embedded variant.
- **15 code-shaped fixtures** refused, including `conftest.py`, `pytest.ini`,
  `pyproject.toml`, `sitecustomize.pth`, `a.json.py`, and case variants
  `evil.PY` / `evil.Py`.
- **5 identity collisions** separated, including `gen-AUTH-01` vs `gen-auth-01`
  and `approve twice` vs `approve-twice`.
- **R1 type preservation** confirmed both directions: ints survive under
  `authorization` / `token` / `password`; strings at every depth still masked.

**Zero false refusals**: legitimate relative and loopback navigation, all six
data-fixture extensions, and already-safe labels (`gen-auth-01`,
`backend_generic`, `browser_generic`) pass through unchanged.

Harness: `hostile_probe_cycle1.py` in this directory.

### 4. Pre-existing 30-mutation harness

Re-run because cycle 1 changed every file the 30 mutations target: **28/30
caught**. The two that did not report CAUGHT both came back **COULD_NOT_APPLY**,
not SURVIVED — N10's anchor was deleted by the R2 fix, and P7's anchor became
ambiguous because the CG-02 fix added a second gate call site. Both were
re-anchored and re-run, and both are **CAUGHT by the same test that caught them
in the committed baseline**.

**28/30 applied + 2/2 re-anchored = 30/30 requirements covered.** No regression
in the safety net. Full detail, and the recommended anchor updates for the
controller, in `MUTATION-RERUN.md`.

### 5. No test deleted or weakened

`def test_` counts, `HEAD` vs working tree: `test_evidence.py` 16→16,
`test_post_remediation_contract.py` 76→76, `test_remediation_contract.py`
47→47, `test_scenario_loop.py` 24→24. Nothing removed.

Four pre-existing test files changed. Two changed assertion **direction**, and
both are accounted for rather than quietly adjusted:

- `test_15_protocol_resolver_precedence_is_unchanged` previously asserted
  `blocks_acceptance < _apply_suite_precedence`. That assertion **pinned CG-02**
  — it encoded "the suite gate is deliberately last", which is the defect. It is
  now reversed. The first assertion (protocol outranks everything) is untouched.
- `TestAuditorPrecedenceUnchanged` previously asserted the audit's correction
  *replaced* the suite's. With the gate first, the substituting branch no longer
  fires. The class's actual invariant is preserved and re-proved by an **added**
  green-suite sub-case, plus a new assertion that the audit's findings travel in
  `problems` rather than being dropped.
- `test_sanitize_filename` replaced exact equalities with properties **plus**
  new injectivity assertions. The old equalities (`"a b/c:d" -> "a-b-c-d"`,
  `"///" -> "unnamed"`) pinned the collision itself.
- `test_post_remediation_contract.py` added `page_loaded=True` at three sites
  where the observation stands in for a session that genuinely reached the
  product.

### 6. Containment

- **No Neyma repository modified.** Neither
  `/Users/sammyfammy/freight-logistics-operational-teammate` (`d59b740`) nor
  `/Users/sammyfammy/Desktop/freight-logistics-operational-teammate` (`6e8127d`)
  has any tracked modification; both carry only pre-existing untracked P4/R-07
  report files, identical in each.
- **No secrets.** No credential-shaped literal introduced anywhere in the diff.
- **No authority expanded.** Every change refuses something previously
  permitted, or records something previously discarded. Nothing became newly
  possible. `max_parallel` still fails closed at two layers;
  `promotion_requires_approval` untouched; `approved_commands` unchanged; the
  loopback host set unchanged and now enforced on a strictly wider set of
  navigation shapes.
- **`driver.config.yaml` not edited** — controller's file, per the ledger.
- **No git history mutated. Nothing pushed.**

## CG-01 — disposition, and its binding condition

ADJ-C downgraded CG-01 to NONBLOCKING **subject to a binding condition**: the
certificate is scoped to runs with `scenario_generation.enabled: true`, and the
shipped `driver.config.yaml` must carry it before the driver is next run, or
CG-01 reverts to blocking.

**The condition was verified mechanically and does NOT currently hold.**
`driver.config.yaml` contains no `scenario_generation` block at all, so
`enabled` takes its default of `False`.

This is reported, not resolved and not reinterpreted. The ledger assigns that
edit to the controller and forbids the builder from touching that file. So:

- CG-01 remains **NONBLOCKING — CONDITION UNSATISFIED**.
- It is an **outstanding precondition** that must be satisfied before the
  driver's next run and before certification is written. It is already item 6 of
  the ledger's "Owed before certification can be written".

**Scoping fact the certificate must state**, independently confirmed here:
`cmd_evaluate` maps `Decision.ACCEPT → RunStatus.ACCEPTED` with no
`evaluate_gate` and no `_apply_suite_precedence` anywhere in its body, and runs
with no planner at all. It is therefore **outside** the envelope CG-01's
downgrade is scoped to. Cycle 1 deliberately did not expand into it; ADJ-C's
requirement that the scoping note name `cmd_evaluate` is carried forward.

## Nonblocking limitations carried

| ID | Limitation |
|---|---|
| NB-1 | CG-01: no deterministic gate on the no-planner path. Predates the certified work. Scoped out by config; **condition unsatisfied** (above). |
| NB-2 | `cmd_evaluate` maps ACCEPT→ACCEPTED with no gate and no planner. Outside the certified envelope. |
| NB-3 | `Builder.journal` is never wired, so `RunJournal.record_tool_use` / `record_denied_path` are unreachable; builder tool use is absent from the journal. The `AttributeError` itself is CLOSED. |
| NB-4 | Evidence directories for scenario ids containing uppercase or filename-unsafe characters change name under the injectivity fix, so a run resumed across this change orphans that prior evidence. Already-safe lowercase ids are byte-stable; shipped scenario names are unaffected. |
| NB-5 | `ScenarioExecutor` in `cmd_evaluate` receives no `approved_commands`, so post-substitution re-approval is inert there. Consistent with NB-2: that path runs handwritten scenarios only, under the pre-existing human-authored trust model. |
| NB-6 | `.yaml`/`.yml` are permitted fixture extensions. Safe against the driver's own approved commands, but a product under test that used `yaml.load` rather than `yaml.safe_load` could construct objects from a generated fixture. Property of the product, not the driver. |
| NB-7 | `run_mutations.py` writes its results into `verification-evidence/post-remediation/mutation-results.json`, so *running* the harness modifies the review record. This session ran it and restored the file to its committed state; nothing of substance was lost. Recorded because the harness is not a read-only act. |

## Still owed before certification can be written

Unchanged from the ledger, and **none of it is builder work**:

1. A fresh reviewer attacking this candidate — must not be any session that
   built it.
2. A separate fresh adjudicator on whatever that reviewer finds.
3. Areas 1 (real-model scenario quality), 2 (adaptive responsiveness) and 9
   (real builder loop) covered to a verdict against this corrected candidate.
   Destroyed twice by session-usage limits; required by Part 3; cannot be
   waived.
4. Every Part 4 residual classified CLOSED / NONBLOCKING LIMITATION / BLOCKING
   DEFECT / FOUNDER-GOVERNANCE BLOCKER.
5. `driver.config.yaml` carrying `scenario_generation.enabled: true` — CG-01's
   binding condition, currently unsatisfied.
6. Separately, outside certification: **PD-02** (`_CONTENT_COUNT_RE` in
   `protocol_sources.py:471`), owed before the driver is pointed at Neyma.

## BUILDER WORK IS NOT CERTIFICATION

This document records what was built and what was measured. It awards nothing.
The candidate has not been independently reviewed. The next legal action is
fresh independent certification by a session that did not write this code.

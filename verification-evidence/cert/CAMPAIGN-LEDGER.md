# Product Driver certification campaign — controller's ledger

Maintained by the campaign controller. This is the running state of the campaign,
not a certification. The certification is `FINAL-DYNAMIC-SCENARIO-CERTIFICATION.md`
and does not exist until every area has a verdict.

## Candidate under certification

- Commit `537ae0b7c0c6524ce4ae88d1fc3bdbc483bf4707` on `main`
- Parent `dcc649e` == `origin/main`; unpushed
- Tree clean at campaign start except untracked `.driver-state/` and a pre-existing
  modification to `verification-evidence/post-remediation/mutation-results.json`
- Suite claim independently confirmed by the controller: **1115 passed, 0 failed,
  0 skipped** in 354.32s — matches the recorded figure exactly

The three prior self-reports (`ADJUDICATION.md`, `REMEDIATION.md`,
`POST-DYNAMIC-REMEDIATION.md`) were read in full and treated as untrusted
self-reports throughout, per the founder's instruction not to trust the previous
builder's verdict.

## Role separation actually maintained

- Eight independent reviewers (A–H), each a fresh isolated session, each forbidden
  from fixing anything it found.
- Five independent adjudicators (ADJ-C, ADJ-D, ADJ-E, ADJ-F, ADJ-G), each a fresh
  session that did not produce the finding it judged, each required to reproduce
  from source before taking the reviewer's word.
- One remediation builder (cycle 1), which reviewed nothing and adjudicated nothing.
- No agent has certified its own work at any point.

Reviewers could not write their own report files (the harness refuses subagent
report-file creation), so the controller transcribed each return verbatim into
`<AREA>/FINDINGS.md`. The probe scripts and raw JSON in each directory are the
reviewers' own artifacts, written by them.

## Reviewer results

| Area | Scope | Verdict | Blocking | Nonblocking |
|---|---|---|---|---|
| A-QUALITY | real-model scenario quality | **incomplete** — interim only | — | — |
| B-ADAPTIVE | adaptive responsiveness | **incomplete** — capacity loss | — | — |
| C-GATE | acceptance gate | FAIL | 2 (1 upheld, 1 downgraded) | 4 |
| D-SAFETY | execution safety | FAIL | 2 (both upheld) | 7 |
| E-RESUME | resume/recovery + identity | FAIL | 3 (all upheld) | 6 |
| F-BROWSER | generated browser path | FAIL | 4 (3 upheld here, 1 = D-02) | 5 |
| G-SCALE | scale 10/50/100/200 | FAIL | 2 (both upheld) | 6 |
| H-LOOP | real builder loop | **incomplete** — capacity loss | — | — |

A-QUALITY, B-ADAPTIVE and H-LOOP were terminated by an external session-usage
limit, twice (resets 4:10am then 6pm America/Los_Angeles). That is an
infrastructure constraint, **not** a product finding and **not** a certification
result. Areas 1, 2 and 9 of the founder's required list therefore remain
**uncertified** and must be covered against the corrected candidate.

## Upheld blocking defects — nine, consolidating to seven fix sites

| # | Defect | Independent confirmations | Standard clause violated |
|---|---|---|---|
| 1 | CG-02 — gate skipped on the completion-audit terminal path | 1 | silently omit required verification |
| 2 | D-SAFETY-01 — `fixture_content` executes as code via `{{fixture:}}` | 2 | execute generated actions outside its authority |
| 3 | D-SAFETY-02 / F-4 — `goto` scheme mismatch escapes loopback | 3 | outside authority + falsely ACCEPT + misattribute evidence |
| 4 | R1 / ADJ-G-01a — `redact_obj` corrupts the plan via `authorization` | 3 | lose critical state across resume |
| 5 | R2 — unreadable plan fails open and destroys the record | 2 | lose critical state across resume |
| 6 | I1 / G-SCALE-02 — evidence-directory collision credited as verified | 3 | lose or misattribute evidence; collapse scenario identities |
| 7 | G-SCALE-01 — resume-time drops invisible to the gate | 2 | lose state; silently omit verification; falsely ACCEPT |
| 8 | F-1 + F-2 — browser scenario that observed nothing PASSES | 2 | falsely ACCEPT; silently omit verification |
| 9 | F-3 — browser text replaced rather than accumulated | 1 | falsely ACCEPT; misattribute evidence |

Defects 8 and 9 share one adjudication; ADJ-F established that F-1 and F-2 are
one defect with two entry points, so the campaign counts eight distinct root
causes across nine findings.

Three defects were found independently by three separate sessions using three
different methods (3, 4, 6). That convergence is itself evidence about the
review's thoroughness — and about how little of this the prior campaign's own
harnesses could see.

## Downgraded on adjudication

- **CG-01** → NONBLOCKING LIMITATION. The no-planner path has no deterministic
  gate at all. ADJ-C proved from history (`dcc649e^1`) that this predates the
  certified work — the certified work *added* the gate — and proved that
  `_make_planner` has exactly one `return None`, reachable only when generation is
  disabled. **Condition, binding:** the certificate is scoped to runs with
  `scenario_generation.enabled: true`, and the shipped `driver.config.yaml` must
  carry it before the driver is next run, or CG-01 reverts to blocking. ADJ-C also
  found a second, worse instance the reviewer missed: `cmd_evaluate` maps
  `ACCEPT → RunStatus.ACCEPTED` and returns 0 with no reference to `result.passed`
  and no planner at all.

## Confirmed strengths — recorded because certification is not a prosecution

- **The gate's arithmetic held under 70 hostile attacks**, including an
  always-ACCEPT evaluator arguing in prose that the harness be disregarded. Zero
  computed holes were talked away. Zero false refusals in 70 attacks.
- **The deterministic string boundary is genuinely strong.** Control characters,
  shell composition, command substitution, quote-boundary tricks, prefix confusion
  and every HTTP host-escape shape tried were refused — with **zero false
  refusals** on legitimate SQL/JSON/regex oracles.
- **Scale holds where it was measured**: counts, evidence integrity, attribution,
  budget/cap enforcement, identity at 200 with 200-char shared prefixes,
  cross-process resume with a byte-identical state fingerprint, linear runtime at
  ~36 ms/scenario, +4.3 MB RSS across 20×.
- **The generated browser path works end to end**, including a live-model run in
  which the model authored its own risk, all three proposals were accepted in
  browser mode, two of three caught a seeded UI defect, and the gate correctly
  refused.
- **Grounding is real** — endpoint literals, HTTP status codes, table names and
  event types verified against live product source — and **0 of 17 accepted
  scenarios invoked a broad test suite** despite 5 of 8 approved commands being
  pytest/diagnostics.
- **Mutation score independently re-run: 30/30**, differing from the committed
  record only in wall-clock timing strings.

Every failure found is in *wiring*, *disposal* or *scoring* — not in the
computation the system does when it is asked.

## Remediation cycles

Budget: **3**. Used: **1** (implementation and builder-side verification
complete; awaiting fresh independent review).

**Cycle 1** — REMEDIATION-BUILDER-1, fresh session, briefed from the five
adjudications' own fix specifications. Scope: the nine upheld blockers, plus
regression coverage for each (none currently has any test pinning it — which is
how a 30/30 mutation score coexists with nine blockers).

Controller decisions recorded at brief time:
- The capability/assurance choice ADJ-D referred upward (data-extension allowlist
  vs. severing `{{fixture:}}` from command strings) was resolved by the controller
  as an ordinary implementation choice, not a founder decision: option (a), which
  closes the defect by construction while preserving the capability the generator
  prompt already documents, and which changes no product authority. Option (b)
  held in reserve.
- The builder is forbidden from touching `verification-evidence/` (the review
  record) and from editing `driver.config.yaml` (controller's job).

### Cycle 1 — outcome

**Remediation candidate: `c72756f2deae7a1ad58c5acc94e007d710de5e07`**, parent
`537ae0b`, on `main`, 2 commits ahead of `origin/main`, **unpushed**. Working
tree clean apart from untracked `.driver-state/` (Neyma handoff artifacts,
unrelated to this remediation, deliberately not staged). 1180 tests collected;
no evidence harness is collected as a test.

Two sessions held the builder role in sequence. The first produced the
implementation and was interrupted by a host process exit; the second resumed,
re-established state from `git diff` rather than memory, reconciled the partial
work against the five adjudications, and performed the verification below.
**No session that wrote this code has reviewed or certified it.**

Full record: `verification-evidence/remediation-cycle-1/REMEDIATION.md`.

- **Resumed from:** `537ae0b` + 13 files unstaged, 1053 insertions / 135
  deletions, plus untracked `tests/test_remediation_cycle_1.py`. Nothing was
  checked out, restored, stashed, reset or discarded.
- **All nine upheld blockers addressed**, across eight source files.
- **Full suite: 1180 passed, 0 failed, 0 skipped** in 305.10s (baseline 1115 +
  65 new). Run by the second builder session; not inherited as a claim.
- **Regression coverage proved two independent ways.** Whole module against the
  unfixed candidate `537ae0b`: **56 failed / 9 passed**, the 9 verified to be
  exactly the capability-preservation controls that must pass on both sides. And
  each fix reverted individually with only its own test class re-run:
  **9/9 CAUGHT**. No fix is unpinned.
- **Hostile replay of the exploits themselves**: 12 navigation escapes refused,
  15 code-shaped fixtures refused, 5 identity collisions separated, R1 type
  preservation confirmed both directions — with **zero false refusals** on
  legitimate navigation, data fixtures and already-safe labels.
- **Pre-existing 30-mutation harness re-run** (cycle 1 touched every file it
  targets): **28/30 caught**; the two exceptions were **COULD_NOT_APPLY, not
  SURVIVED** — N10's anchor was deleted by the R2 fix and P7's became ambiguous
  because the CG-02 fix added a second gate call site. Both were re-anchored and
  are **CAUGHT by the same test that caught them in the committed baseline**.
  **28/30 applied + 2/2 re-anchored = 30/30 requirements covered.** Anchor
  updates are recommended to the controller in
  `remediation-cycle-1/MUTATION-RERUN.md`; the builder may not edit
  `run_mutations.py`.
- **No test deleted or weakened.** `def test_` counts identical in all four
  pre-existing files. Two assertions changed *direction*; both are the point of
  the CG-02 fix rather than an accommodation to it, and in both cases the
  invariant the test exists to protect is preserved and separately re-proved.
- **Containment:** no Neyma repository modified, no secrets introduced, no
  authority expanded, `driver.config.yaml` untouched, no history mutated,
  nothing pushed.
- **The one modification inside `verification-evidence/`** —
  `post-remediation/mutation-results.json` — was proved to be the controller's
  own pre-existing 30/30 re-run: byte-identical after normalising wall-clock
  timings, zero `CAUGHT` verdicts changed. Not a builder incursion.

**CG-01's binding condition was checked mechanically and does NOT hold.**
`driver.config.yaml` contains no `scenario_generation` block at all, so
`enabled` defaults to `False`. This is recorded, not resolved and not
reinterpreted — the edit is the controller's, and it remains item 6 below.
Independently confirmed for the certificate's scoping note: `cmd_evaluate` maps
`Decision.ACCEPT → RunStatus.ACCEPTED` with no `evaluate_gate`, no
`_apply_suite_precedence`, and no planner, placing it outside the envelope the
CG-01 downgrade is scoped to.

Nonblocking limitations carried out of cycle 1: **NB-1** CG-01 no-planner path
(condition unsatisfied); **NB-2** `cmd_evaluate` ungated and out of envelope;
**NB-3** `Builder.journal` never wired, so the journal's tool-use recorders are
unreachable; **NB-4** evidence directories for ids containing uppercase or
unsafe characters change name under the injectivity fix, orphaning prior
evidence on a resume across the change (already-safe lowercase ids are
byte-stable); **NB-5** `cmd_evaluate`'s executor gets no `approved_commands`, so
post-substitution re-approval is inert there; **NB-6** `.yaml`/`.yml` fixtures
are safe against the driver's commands but a product using `yaml.load` could
construct objects from one; **NB-7** `run_mutations.py` writes into
`mutation-results.json`, so running the harness modifies the review record (this
session ran it and restored the file to its committed state).

## Owed before certification can be written

1. Cycle-1 remediation complete.
2. A fresh reviewer attacking the corrected candidate — must not be any session
   that built it.
3. A separate fresh adjudicator on whatever that reviewer finds.
4. Areas 1 (real-model quality), 2 (adaptive responsiveness) and 9 (real builder
   loop) covered to a verdict against the corrected candidate. These are the three
   the capacity limit destroyed; they are required by the founder's Part 3 and
   cannot be waived.
5. Every Part 4 residual classified as exactly one of CLOSED / NONBLOCKING
   LIMITATION / BLOCKING DEFECT / FOUNDER-GOVERNANCE BLOCKER.
6. `driver.config.yaml` carrying `scenario_generation.enabled: true` — a binding
   condition of ADJ-C's CG-01 downgrade, and independently required by the
   founder's Part 9.

## Separately owed, outside certification

**PD-02** — `_CONTENT_COUNT_RE` in `neyma_product_driver/protocol_sources.py:471`
matches `\d{1,2}` inside `R-07`, capturing `"07"` → `int("07")` → 7, and the value
is consumed, producing `topology: BLOCKED_AUTHORITY`. Reproduced mechanically from
source by the controller. It is fail-closed, so it is **not** a certification
blocker under the founder's standard — but it will actively mislead the driver
when pointed at Neyma, so it is remediation work owed before the driver is used.
Neyma's own `CURRENT.md` records it as rank-1 for the Product Driver and states
the fix belongs outside that repository.

**Configuration drift** — `driver.config.yaml`'s `neyma_repo` points at
`/Users/sammyfammy/Desktop/freight-logistics-operational-teammate`, a stale
checkout at `6e8127d` on `p4/adapter-containment-completion` with no P5 branches.
The live repository is `/Users/sammyfammy/freight-logistics-operational-teammate`
at `d59b740` on `p5/u5-1-g2-spec-correction`. To be repointed in Part 9.

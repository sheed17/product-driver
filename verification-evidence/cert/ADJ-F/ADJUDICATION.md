# ADJUDICATION — ADJUDICATOR-F (generated browser path blocking claims)

Recorded verbatim by the campaign controller from ADJUDICATOR-F's return. This
adjudicator did not produce the F-BROWSER findings and did not fix anything. Its own
fixture (`fixture.py`/`serve.py`), harnesses (`adj_chain.py`, `adj_f2.py`,
`adj_restart.py`) and run outputs (`out/`) are in this directory.

**Candidate:** `537ae0b`. **Chromium/Playwright:** available and used for every result below (verified independently before starting). Nothing under `neyma_product_driver/` or `tests/` was edited. The only network target anywhere was a loopback listener the adjudicator's own process started and stopped.

The reviewer's fixture and harness were **not** used to reach these conclusions. An independent chain was built — `GeneratedScenario` → `compile_to_scenario` → `ScenarioSuite` → `SuiteExecutor` (real chromium) → `write_case_evidence`/`verify_case_evidence` → `evaluate_gate` — with nothing stubbed. The reviewer's `harness/chain.py` was re-run once afterwards purely as a cross-check.

---

## F-1 — **UPHELD-BLOCKING**

### (a) Mechanism

`_run_browser` (`scenarios.py:867-890`) wraps the session-level `page.goto(scenario.app_url)`, the whole step loop, and the final page-text capture in one `try`. Its `except` at :887-890 records the exception into `obs.steps` — a narration list — and **nothing else**.

`_run_step` (:948-955), by contrast, records step-level failures into **both** `obs.steps` and `obs.step_failures`. `_assert_browser_text` (:636-659) builds assertions from exactly two structural channels: `obs.step_failures` and `obs.text_expectations`. It never reads `obs.steps`.

So when the session-level goto raises, the step loop is jumped over entirely (no `text_expectations` are produced) and the failure lands only in the unread channel (no `step_failures`). `_assert_browser_text` appends **zero** assertions. `ScenarioResult.passed` (`models.py:239-245`) is `error is None and readiness_ok and all([])` — and `all([])` is `True`. `SuiteExecutor._outcome` (`scenario_suite.py:~670`) computes `never_observed = not readiness_ok or (error is not None and not assertions)`; `error` is `None`, so the case is not even BLOCKED — it is `PASSED`. `verify_case_evidence` only checks that a `result.json` exists and is stamped with the right ids, so `evidence_verified` is `True`. `evaluate_gate` counts `PASSED + evidence_verified` as a verified required scenario → `VERIFIED`, `blocks_acceptance=False`. `_apply_suite_precedence` (`cli.py:872-873`) then returns the evaluator's ACCEPT untouched.

The reviewer's line numbers and causal chain are **correct as read**. The one correction is that they under-describe the reach: this `except` also swallows a failure of the terminal `page.title()` / `page.evaluate(innerText)` capture, and it applies identically to the phase-form path (`scenarios.py:414-416`), not just the generated step form.

### (b) Reachability — reachable without contrivance

1. **`--browser` is not the only door.** `driver.config.yaml` ships `browser_enabled: false`, but `cli.py:1297-1299` in `cmd_run` **auto-enables it for the whole run** whenever the selected permanent scenario is browser-mode — and the repo ships `scenarios/browser_generic.yaml` (`mode: browser`) for exactly that purpose. `cmd_evaluate` does the same at `cli.py:2503`. That flag then flows into the planner and the generated suite (`cli.py:1464/1596/1628`). So "the founder picks the browser scenario" is sufficient; no flag is typed.
2. **A failing session-level navigation is ordinary, not artificial.** It is the entry point that every browser action re-executes: `_run_browser` opens a **new session per browser step** and re-navigates `app_url` each time (:868-869). Readiness does not protect it, because readiness ran once, earlier, against a possibly different check.
3. **Demonstrated on the driver's own canonical shape.** `adj_restart.py` builds a `RESTART_RECOVERY`-category generated browser scenario — a category in the product's own closed `RiskCategory` enum, and precisely the "reopen the page after the backend changed" shape the executor's own docstring (:816-821) says separate browser steps exist to express:

   `browser action 1 (page loads, expect_text FOUND)` → `stop_service("site")` → `browser action 2 (reopen the page)`

   The driver started the service itself. Readiness passed honestly (`'all readiness checks passed'`). The product then **never came back**, and the second action's navigation got `net::ERR_CONNECTION_REFUSED`.

   **Result: `PASSED`, 2/2 assertions passing, `evidence_verified: true`, gate `VERIFIED`, `blocks_acceptance: false`.** The one assertion the scenario existed to make — "the screen still works after the bounce" — silently never ran.

That is not a contrived configuration. It is the single most obvious browser scenario an adaptive generator will write, failing in the single most obvious way, and being recorded as proof that the product works.

### (c) Evidence personally reproduced

| run | app_url | outcome | assertions | screenshots | gate |
|---|---|---|---|---|---|
| `out/f1ctl` (control) | `/` | PASSED | 2 pass / 0 fail | 3 | VERIFIED — **honest** |
| `out/f1` | `/hang` | **PASSED** | **0** | **0** | **VERIFIED**, blocks_acceptance false |
| `out/restart` | service stopped mid-scenario | **PASSED** | 2 pass / 0 fail | — | **VERIFIED** |
| `out/restart-expect` | same + scenario-level `expect_visible` | **PASSED** | 3 pass / 0 fail | — | **VERIFIED** |

`out/f1` sole browser record: `ERROR: TimeoutError: Page.goto: Timeout 30000ms exceeded ... navigating to ".../hang"`; `step_failures: []`; `visible_text: ''`; zero screenshots; zero traces; `evidence_verified: true`.

Control `f1ctl` proves the oracle machinery works on the identical scenario against a live page (two scored `expect_text` assertions), so the zero-assertion result is caused by the navigation failure, not by a broken harness.

**`out/restart-expect` is the worst of the set and is the adjudicator's own, not the reviewer's.** With a scenario-level `expected_observations: ["adjudicator clean page"]`, that assertion — "an operator can still see the screen after the bounce" — **scored PASS** against a browser session that received `ERR_CONNECTION_REFUSED`, because the merged `visible_text` still held the *pre-bounce* page (F-1 and F-3 compounding). This is not a vacuous pass; it is an **affirmatively false positive assertion** built from misattributed evidence. It hits three of the founder's prohibitions at once: falsely ACCEPT unverified work, misattribute evidence, silently omit required verification.

Cross-check: the reviewer's own `harness/chain.py --out … --app-path /hang` reproduced identically — `H01-app-never-renders: PASSED shots=0 traces=1 verified=True`, gate `{"status":"VERIFIED","blocks_acceptance":false,"required_passed":1}`.

### (d) Narrowest systemic fix (described, not implemented)

Two layers, both confined to `scenarios.py` (+ one optional field in `models.py`):

1. **Make the degraded exit structural, not narrative.** In the `except` at :887-890, append the failure to `obs.step_failures` as well as `obs.steps`. `_assert_browser_text` then emits a failed `expect_state`, `passed` becomes False, and the gate returns NOT_VERIFIED. This is the same one-line discipline `_run_step` already applies at :951 — it was simply never extended one level up.
2. **Add an observation floor so no future degraded path can re-open this.** Record on `BrowserObservation` whether a page was ever successfully loaded (set only after `page.goto` returns, or when `app_url` is empty and a step-level `goto` succeeded), and have `_assert_browser_text` score it. Rationale: the codebase already reasons this way in `_outcome`'s `never_observed`; the browser layer just has no equivalent. BLOCKED ("the product was never observed") is more honest for the navigation case than FAILED; either blocks acceptance.

F-6 (evidence completeness) would catch this downstream as a second line of defence, but it is not the fix.

---

## F-2 — **UPHELD-BLOCKING** (same defect as F-1; one fix closes both)

### (a) Mechanism

`_run_browser` opens with `try: from playwright.async_api import async_playwright / except ImportError: return BrowserObservation(steps=["playwright is not installed; …"])` (`scenarios.py:823-828`). That early return produces an observation with a message in `obs.steps` and empty `step_failures` / `text_expectations` — **the identical unscored state as F-1**, reached by a different line. Zero assertions → `passed = all([]) = True` → PASSED → `evidence_verified: true` → gate VERIFIED.

The chromium-*binary*-missing case takes a different route by accident: `pw.chromium.launch()` at :840 is **outside** the guarded block, so it raises, propagates out of `_run_browser` (nothing wraps the `await` at :501), and is caught by `execute`'s outer handler at :445-447, which sets `result.error`. `never_observed` is then true → BLOCKED → gate NOT_VERIFIED. Same missing capability, opposite verdict, decided purely by which line happens to sit inside the `try`. The reviewer's diagnosis is exactly right.

### (b) Reachability — real, but a degraded-install state, stated plainly

`playwright>=1.40` is a **hard, non-optional dependency** in `pyproject.toml`. A correct install always has the package. The absent-package state therefore requires a broken environment: `pip install --no-deps`, a stripped container, a partially built venv, or invoking the driver with a different interpreter than the one it is installed into. The *chromium binary*, by contrast, is a separate manual step the README calls out, so the far more likely real-world gap is the binary — and that one **fails closed correctly**.

Does anything detect it earlier? Partly: `doctor` checks `import playwright` (`cli.py:2304-2310`) and probes the chromium executable (`_check_chromium`, :2422-2433). But both are `fatal=False`, and `doctor` is an **opt-in separate command**. `cmd_run` was read end to end (`cli.py:1262-1330`): it performs **no** playwright or chromium preflight, including on the path where it has just auto-enabled `browser_enabled` at :1297-1299. So a run whose entire browser verification is a no-op will not be told.

Honest conclusion: **on its own trigger alone, F-2's likelihood is materially lower than F-1's.** It is blocking not because a missing package is common, but because (i) it is a second, independent entry point into the F-1 defect — a known, reproducible, silent false-VERIFIED — and (ii) the failure is *affirmative and silent* rather than loud: an operator with a broken install is told their product is verified. The asymmetry with the binary case proves the fail-open is accidental, not a considered design decision, so it cannot stand as an "explicitly recorded nonblocking limitation" under the founder's carve-out. Nothing in `verification-evidence/` records it; on the contrary, `POST-DYNAMIC-REMEDIATION.md:914` claims `_assert_browser_text` "scores every `expect_text` and every raised step, on both execution paths" — a completeness claim these results falsify.

### (c) Evidence personally reproduced

The reviewer's stub package was **not** used. An independent method was used instead: a `sys.meta_path` finder that raises `ModuleNotFoundError` for `playwright` and any submodule — which is precisely what a genuine absence raises at line 824 — with the module cache cleared first (`adj_f2.py`). Same fixture, same scenario, same gate for both modes.

| mode | outcome | assertions | screenshots | `result.error` | gate |
|---|---|---|---|---|---|
| `nopkg` (package absent) | **PASSED** | **0** | **0** | `None` | **VERIFIED**, blocks_acceptance false |
| `nochromium` (`PLAYWRIGHT_BROWSERS_PATH` → empty dir) | **BLOCKED** | 0 | n/a | `"scenario execution raised: Error: BrowserType.launch: Executable doesn't exist at …"` | **NOT_VERIFIED**, blocks_acceptance true |

`nopkg` sole browser record: `playwright is not installed; run: pip install playwright && playwright install chromium`, `evidence_verified: true`.

The reviewer's stub (`harness/stub-no-playwright/playwright/{__init__,async_api}.py` — each a bare `raise ImportError`) was also inspected. It is honest for this purpose; the meta-path method is an independent confirmation of the same result.

### (d) Narrowest systemic fix — **the same fix as F-1**

The `_run_browser` degraded exit at :826-828 must populate `obs.step_failures` (or set the "page never loaded" flag) as well as `obs.steps`. Layer 2 of the F-1 fix — the observation floor — closes this line and any future one without touching it individually. **Additionally**, and separately from the defect: `cmd_run` should preflight the browser capability (reuse `_check_chromium`) and refuse rather than proceed once it has decided `browser_enabled=True`, so a run whose browser verification cannot happen fails loudly at the top instead of quietly at the bottom. That second item is a hardening, not the fix.

### Are F-1 and F-2 one defect or two? — **One defect, two entry points.**

Scrutinised as instructed, and this adjudication disagrees with treating them as independent blockers. The root cause is singular and precise: **`_assert_browser_text` derives assertions only from `obs.step_failures` and `obs.text_expectations`, and nothing anywhere requires a browser-mode scenario to have produced at least one browser-derived observation before `all([]) == True` is read as success.** Both findings are `BrowserObservation`s that carry a failure in the unread `obs.steps` channel and nothing in the read ones. `scenarios.py:826-828` and `scenarios.py:887-890` are two doors into one room.

Consequence for remediation planning: **one systemic fix (the observation floor) closes both, and closes any third door that has not been found yet.** A purely local fix would have to touch both lines and would leave the class open. The campaign should count this as **one blocking defect with two demonstrated triggers**, not two — while noting that the second trigger (F-2) is the one that proves the first was not a considered design decision.

---

## F-3 — **UPHELD-BLOCKING**

### (a) Mechanism — the reviewer is right, and the defect is **broader** than they state

The reviewer's half: `_merge_browser` (`scenarios.py:661-675`) does `merged.visible_text = obs.visible_text or merged.visible_text`, while `screenshots`, `console_errors`, `network_failures`, `steps`, `text_expectations` and `step_failures` are all `.extend(...)`. So across browser actions, page text is **replaced**, not accumulated. `_observed_text` (:794-806) — the haystack for the scenario-level `expect_visible` / `forbidden` checks at :422-441 — reads `result.browser.visible_text`. Therefore a scenario-level `forbidden` string that was genuinely rendered by an earlier browser action is not in the haystack and scores PASS. Confirmed exactly.

**What the reviewer missed:** the root cause is not confined to `_merge_browser`. Within a **single** browser session, `visible_text` is captured **once**, after the step loop finishes (:884-886). Every intermediate navigation's page text is discarded before it is ever recorded. So the same evidence loss occurs in a scenario with one browser action and several `goto` steps — which is **the exact shape of the shipped `scenarios/browser_generic.yaml` template** (`goto: "/"` → `goto: "/operator/"`), whose `forbidden:` list is the product's primary UI-quality oracle (`Traceback`, `undefined`, `[object Object]`, `NaN`, `None`, `TODO`, `tool_use`, …). A `Traceback` on the landing page is structurally invisible to that list.

This also breaks a contract the product states to the model. `scenario_generator.py:175-178` and the tool schema at :396-401 tell the generator that `expected_observations` / `forbidden_observations` are "matched as LITERAL substrings against **everything the run observed** — command output, response bodies, **visible browser text**". They are not. And `GeneratedScenario.has_observable_outcome` (`scenario_plan.py:508-511`) accepts `forbidden_observations` **alone** as sufficient grounding — so a browser scenario can validate on an oracle that structurally cannot see most of what the run rendered.

Beyond the oracle: `visible_text` is what the **evaluator** is shown as the scenario's browser text (`prompts.py:368`) and what failure clustering excerpts (`scenario_suite.py:894-895`). `url` and `title` are `or`-merged too. So this is evidence loss and misattribution in the record itself, not merely a scoring miss.

### (b) Reachability — reachable, including on the shipped template

Requires `browser_enabled: true`, which as established is auto-enabled by selecting a browser-mode permanent scenario (`cli.py:1297-1299`) — no flag needed. Beyond that, nothing is contrived: it needs only a browser scenario that looks at more than one screen. That is the normal case, is what the shipped template does, and is what the generator is encouraged to produce. No adversarial input, no unusual configuration, no timing.

### (c) Evidence personally reproduced

| run | shape | `visible_text` recorded | `forbidden "Traceback (most recent call last):"` | gate |
|---|---|---|---|---|
| `out/f3ctl` (control) | one browser action, `goto /boom` | the traceback | **FAIL** — correct | NOT_VERIFIED |
| `out/f3` | two browser actions: `/boom` then `/quiet` | `'nothing to see'` | **PASS** — false | **VERIFIED** |
| `out/f3seq` | **one** browser action, `goto /boom` then `goto /quiet` | `'nothing to see'` | **PASS** — false | **VERIFIED** |
| `out/restart-expect` | mirror direction | pre-bounce page | `expect_visible` **PASS** against a refused session | **VERIFIED** |

`out/f3seq` is the adjudicator's own and is the more damaging case: it needs no multi-action merge at all, only a single session that navigates twice — the shipped template's shape.

The screenshot at `out/f3/scenarios/ADJF-f3/screenshots/first-look-at-the-failing-screen-99-final.png` was opened directly. It plainly renders:

```
Traceback (most recent call last):
  File "app.py", line 1, in handler
RuntimeError: exception detail unavailable
```

The run that captured that screenshot recorded `forbidden: "Traceback (most recent call last):" → passed: true` and drove the gate to VERIFIED.

The control `f3ctl` — same page, same forbidden string, one navigation — correctly FAILS, which isolates the cause to the discarded intermediate text rather than to matching.

### (d) Narrowest systemic fix

Make the recorded observation equal what the generator was told it is:

1. **Accumulate page text at every point the page changes.** In `_run_browser`, capture `document.body.innerText` after each navigation (and after the final step), appending into an accumulating channel — cleanest as a new `BrowserObservation.observed_texts: list[str]`, leaving `visible_text` meaning "the final page" for display/`prompts.py`.
2. **In `_merge_browser`, extend that channel** rather than replacing it — the same `.extend(...)` treatment `screenshots`/`steps`/`text_expectations` already get. Consider recording per-action `url`/`title` for the same reason rather than overwriting.
3. **Point `_observed_text` at the accumulated channel**, so the scenario-level `expect_visible` / `forbidden` haystack is genuinely "everything the run observed".

This changes no oracle's meaning; it repairs the haystack the oracle was documented to search. Note it makes the mirror direction (transient text on an intermediate page now failing a `forbidden`) **more** likely — that is the correct direction for a verification tool, and the generator prompt already states the rule as "must NOT appear anywhere in what the run observed."

---

## Judgement against the founder's certification standard

All three are instances of the first and third prohibitions: *a known reproducible defect that can falsely ACCEPT unverified work*, and *lose or misattribute evidence*. F-1 additionally *silently omits required verification*. None is an "explicitly recorded nonblocking limitation" — `POST-DYNAMIC-REMEDIATION.md:914` asserts the opposite (that raised steps are scored "on both execution paths"), and `verification-evidence/ADJUDICATION.md` §B4 already established the precedent that a required scenario which observed nothing must not be able to reach ACCEPT. These are the same class, one level deeper: not skipped, but recorded PASSED having observed nothing.

The gate is confirmed not to be a backstop: `_apply_suite_precedence` (`cli.py:862-873`) returns the evaluator's ACCEPT unchanged when the gate is VERIFIED and `full_run` is true, which is what every one of these runs produced.

F-4 was **not** re-adjudicated (under separate adjudication as D-SAFETY-02). One read-level corroboration, offered only as such: `scenario_validation.py:540-544` screens a generated `goto` only when it `startswith(("http://", "https://"))`, while `scenarios.py:913` executes it verbatim whenever it `startswith("http")`. The executor's admission predicate is strictly broader than the validator's inspection predicate. That asymmetry exists in the source as the reviewer describes.

---

## Mechanical remediability

**All three: yes — mechanically remediable within this codebase, with no change to product authority and no change to founder governance.**

- Every change is internal to `neyma_product_driver/scenarios.py`, plus one optional field on `BrowserObservation` in `models.py`. The suggested `cmd_run` browser preflight touches `cli.py` and only causes a refusal.
- **No authority change.** Nothing grants a new capability, permits a new command, widens the approved-command set, or makes a new host reachable. The changes only cause more things to be recorded as failures.
- **No governance change.** The gate logic, the evaluator's authority, `_apply_suite_precedence`, and the acceptance rules are untouched. The fix does not make the gate stricter by fiat; it makes the observations it reads honest.
- **No existing test pins the broken behaviour.** Checked. `tests/test_post_remediation_contract.py:779-787` (`test_merging_two_browser_observations_keeps_both_sets`) asserts only that `text_expectations` accumulate — it says nothing about `visible_text`. The §4G contract tests at :761-793 pin `_run_step`'s step-level scoring and the presence of `step_failures` in `_assert_browser_text`; a fix strengthens both assertions rather than violating them. The one behavioural risk to watch is that F-3's fix will legitimately turn some previously-passing multi-screen scenarios into failures — which is the point.

**Recommended remediation count for the campaign: two changes, not three.** One observation-floor change in `_run_browser`/`_assert_browser_text` closes F-1 and F-2 together; one text-accumulation change closes F-3 in both its cross-action and its within-session halves.

# F-BROWSER — INDEPENDENT CERTIFICATION REVIEW (generated browser path)

Recorded verbatim by the campaign controller from reviewer F-BROWSER's return. The
reviewer could not write this file itself (subagent report-file creation is refused
by the harness); the fixtures, harnesses and run artifacts in this directory
(3.9 MB: `fixture/`, `harness/`, `runs/01-clean` … `runs/07d-no-playwright`) are the
reviewer's own and were written by it.

**Candidate:** `537ae0b`. Everything marked **EXECUTED** was actually run against a disposable loopback fixture. Code readings are used only to explain executed results.

**What was built.** `fixture/app.py` — stdlib `ThreadingHTTPServer` exception-detail screen on a free loopback port with a durable JSON store, started/torn down by the driver as a declared service. Seeded defect shape is **not** the recorded `stale_read`: it is `phantom_owner` — the screen *fabricates* backend data (renders `owner: Dana Whitfield` / `next action: call the carrier` when the store holds neither; clean it says `owner: unassigned` / `none recorded`). `harness/chain.py` drives generate → `validate_plan` → `compile_to_scenario` → `ScenarioExecutor` (real chromium) → `write_case_evidence`/`verify_case_evidence` → `SuiteResult` → `evaluate_gate`, nothing stubbed between links.

**Containment discipline.** The only "offsite" target anywhere is a second HTTP listener started by the reviewer's own harness on loopback, addressed as `localhost.:PORT` — a legal FQDN form resolving to this machine that is **not** in `DEFAULT_LOCAL_HOSTS` (`{127.0.0.1, localhost, ::1, [::1], 0.0.0.0}`). "Escaped" means exactly *"reached a host the validator's allowlist refuses"*, proved against a listener the reviewer owns. No real external host was contacted. (`127.0.0.2` is unassignable on this macOS, hence the trailing-dot alias.)

## The three required configurations — all EXECUTED

| # | Config | Result |
|---|---|---|
| 1 | CLEAN | 3/3 PASSED, gate **VERIFIED**, 8 screenshots + 3 traces, all evidence verified |
| 2 | SEEDED UI DEFECT (`phantom_owner`) | generated browser scenario **FAILED** on `expect_text 'owner: unassigned'`; gate **NOT_VERIFIED**, `blocks_acceptance: true`, P0 risk reported uncovered |
| 3 | **LIVE MODEL** | brief said `BROWSER available: yes`; **3 proposed, 3 accepted, 3 browser-mode**, mode preserved through compile; 2 of 3 **caught the seeded defect**; gate **NOT_VERIFIED**. `wave_errors: []` — no transport/rate-limit errors |

Config 3 is genuinely model-authored (it named its own risk: *"The screen fabricates a concrete owner/next action when the durable store holds none"*).

## Browser oracle table (every row EXECUTED)

Vocabulary enumerated from `scenarios.BrowserStep`, `scenario_plan.GeneratedBrowserStep`, the generator prompt's `browser` action shape, and every channel `BrowserObservation` records. For each, a scenario whose **only violated** oracle is that one, against a fixture that violates it.

| # | Oracle / channel | Can it fail? | Proof |
|---|---|---|---|
| 1 | `expect_text` | **YES** | `04-oracle-A/O01` FAILED; also config 2 + live run. §4G fix holds |
| 2 | `expected_observations` → `expect_visible` | **YES** | `04-oracle-A/O02` FAILED `not present in any observed output` |
| 3 | `forbidden_observations` | **YES — but only vs the LAST browser action** | `04-oracle-B/O03` FAILED; **`O15` PASSED though the string was rendered → F-3** |
| 4 | `click` missing selector | **YES** | `04-oracle-A/O04` FAILED (`Page.click` timeout scored as `expect_state`) |
| 5 | `wait_for` never appears | **YES** | `04-oracle-A/O05` FAILED |
| 6 | `fill` missing field | **YES** | `04-oracle-A/O06` FAILED |
| 7 | `press` | **NO — not an oracle** | `04-oracle-A/O07` PASSED (correct: it's an action) |
| 8 | step-level `goto` that errors | **YES** | `04-oracle-A2/O09` FAILED (`net::ERR_UNSAFE_PORT` scored) |
| 9 | `goto` a 404/5xx path | **NO** | `04-oracle-A2/O08` **PASSED** on `/definitely-missing` (F-9) |
| 10 | `wait_ms`, `screenshot` | **NO — not oracles** | by construction |
| 11 | `persisted_state_checks` in a browser scenario | **YES** | `04-oracle-A2/O12` FAILED |
| 12 | **session-level nav failure (`app_url` hangs)** | **NO — vacuous PASS** | `05-hang-generated-only/H01` PASSED, 0 assertions, gate VERIFIED → **F-1 BLOCKING** |
| 13 | **playwright not installed** | **NO — vacuous PASS** | `07d-no-playwright/H01` PASSED, gate VERIFIED → **F-2 BLOCKING** |
| 14 | `console_errors` | **partially** (only via forbidden/expected text matching, only for console-logged events) | `04-oracle-D2/O10b` FAILED on `forbidden "Failed to load resource"`; but `O11` PASSED (F-8) |
| 15 | `network_failures` | **NO** | `04-oracle-D/O10` PASSED with `forbidden ["HTTP 500","/boom"]` while the record held `HTTP 500 …/boom` (F-7) |
| 16 | uncaught JS page exception | **NO** | `04-oracle-C/O11` PASSED, console channel empty (F-8) |

## Findings

### F-1 — BLOCKING. A browser scenario whose page never loaded PASSES; gate says VERIFIED.

```
cd verification-evidence/cert/F-BROWSER
../../../.venv/bin/python harness/chain.py --out runs/05-hang-generated-only \
  --defect none --app-path /hang --payload harness/payload-hang.json --no-permanent
```

**Observed:** `H01` → `PASSED`, `assertions: []`, `screenshots: []`, `evidence_verified: true`; gate `{"status":"VERIFIED","blocks_acceptance":false,"required_passed":1/1}`. Only browser step: `ERROR: TimeoutError: Page.goto: Timeout 30000ms exceeded … "/hang"`. **Expected:** FAILED/BLOCKED and gate refuses.

**Cause (read):** `scenarios.py:867-890` — the initial `page.goto(app_url)` and the whole step loop share one `try`; on exception the message goes to `obs.steps` only, never `obs.step_failures`. `_assert_browser_text` therefore appends **no** assertions, and `ScenarioResult.passed` = `all([])` = `True` (`models.py:239-245`). The `expect_text` oracles never ran. This is the §4G defect class (executed, narrated, unscored) one level above the step oracle. Readiness does not save it — a `/health` that answers while the page hangs is the realistic case, and it is the one that was run.

### F-2 — BLOCKING. `browser_enabled: true` + Playwright absent fails OPEN (missing chromium *binary* fails closed).

```
PYTHONPATH=$PWD/harness/stub-no-playwright ../../../.venv/bin/python harness/chain.py \
  --out runs/07d-no-playwright --defect none --payload harness/payload-hang.json --no-permanent
```

**Observed:** `PASSED`, 0 assertions, 0 screenshots, `evidence_verified: true`, gate **VERIFIED**; sole step `playwright is not installed; run: pip install playwright…`. **Expected:** fail closed. The contrast proves it isn't inherent: with the chromium *binary* missing (`PLAYWRIGHT_BROWSERS_PATH=<empty>`, `runs/07c-no-chromium`) the launch raises outside the guarded block → `BLOCKED`, "the product was never observed", gate `NOT_VERIFIED`. Same missing capability, opposite verdict, decided by which line of `_run_browser` is wrapped (`scenarios.py:823-828`).

### F-3 — BLOCKING. `forbidden`/`expect_visible` see only the last browser action's page; earlier page text is overwritten, so a rendered forbidden string scores PASS.

```
../../../.venv/bin/python harness/chain.py --out runs/04-oracle-B --defect traceback \
  --payload harness/payload-oracle-B.json
```

**Observed:** `O15-forbidden-across-actions` **PASSED** with `forbidden: Traceback → passed: true`. Its own first browser action loaded a page containing `Traceback (most recent call last): … RuntimeError: exception detail unavailable` — the reviewer opened `…/O15-forbidden-across-actions/screenshots/first-look-01-initial.png` and confirmed it is visibly there — while the record's `browser.visible_text` is just `"nothing here"`. Control `O03` (single browser action, same page, same `forbidden`) correctly FAILS.

**Cause (read):** `_merge_browser` (`scenarios.py:661-675`) does `merged.visible_text = obs.visible_text or merged.visible_text` — later browser steps *replace* accumulated page text (screenshots/steps/expectations are correctly extended). Evidence loss **and** a false negative on the two scenario-level oracles the generator is told to use. The mirror direction produces false alarms (seen in the live run's `S2`).

### F-4 — BLOCKING. Navigation containment bypass: `http:/HOST` and `http:HOST` are admitted and reach a host the allowlist refuses.

```
../../../.venv/bin/python harness/containment.py --out runs/06-containment
```

| variant | admitted? | landed | outcome |
|---|---|---|---|
| `http://offsite.invalid/n1` | refused ("not loopback") | — | not executed |
| `https://offsite.invalid/n2` | refused | — | not executed |
| `http://localhost.:P2/n3` (canonical, off-allowlist) | refused | — | not executed |
| **`http:/localhost.:P2/n4`** | **ADMITTED** | `http://localhost.:P2/n4` | **PASSED** asserting `OFFSITE LISTENER REACHED` |
| **`http:localhost.:P2/n5`** | **ADMITTED** | `http://localhost.:P2/n5` | **PASSED** asserting `OFFSITE LISTENER REACHED` |
| `//localhost.:P2/n6`, `HTTP://…/n7`, `javascript:…/n9` | admitted | resolved back to the local app | harmless |

The reviewer's listener logged `{'path': '/n4', 'host_header': 'localhost.:49571'}` and `/n5`. The identical destination spelled canonically is refused.

**Cause (read):** `scenario_validation.py:540-544` inspects a `goto` only when it `startswith(("http://","https://"))`; the executor (`scenarios.py:912-915`) then uses it verbatim whenever it `startswith("http")`. Chromium's WHATWG parser normalizes `http:/host` → `http://host/` (verified directly with Playwright first). `scenario_validation.py:721-724` already documents this exact hazard for *request paths* (`_HAS_SCHEME` / `resolve_http_target`) — the browser `goto` path never got the same treatment. This is "execute generated actions outside its authority": a model-authored string picks the destination and the safety check never sees it.

**Controller note:** this is the same defect independently reported by reviewer D-SAFETY as D-SAFETY-02, found by a different reviewer using a different method. Two independent confirmations.

### F-5 — NONBLOCKING. A generated `click` can navigate off-host; click targets and page-initiated requests are unscreened.

`06-containment/N8`: scenario navigated to the product's own page, clicked `#offsite`, landed on `http://localhost.:P2/` and **PASSED** asserting offsite content. `N10`: an `<img>` on the product page reached the listener with no navigation at all. Validation screens `goto` only; the Playwright context installs no route allowlist. Weaker than F-4 (needs the product page to carry the link), hence NONBLOCKING.

### F-6 — NONBLOCKING. Browser evidence completeness is never verified.

`verify_case_evidence` (`scenario_suite.py:760-813`) checks only that `result.json` exists/parses/matches scenario+run+iteration. A **browser-mode PASS with zero screenshots, zero traces, zero browser steps** reports `evidence_verified: true` (observed in `05-hang-generated-only` and `07d-no-playwright`). The downgrade machinery exists and works — nothing asks a browser scenario for browser evidence, which would have caught F-1/F-2 downstream.

### F-7 — NONBLOCKING. `network_failures` are captured and can never be asserted on.

`04-oracle-D/O10` PASSED with `forbidden ["HTTP 500","/boom"]` while the same result recorded `network_failures: ["HTTP 500 http://…/boom", …]`. `_observed_text` (`scenarios.py:795-806`) includes `visible_text` and `console_errors` but not `network_failures` — yet `browser_network_error` is an advertised risk category. Catchable only by guessing chromium's console wording (`04-oracle-D2/O10b` FAILS on `forbidden "Failed to load resource"`).

### F-8 — NONBLOCKING. Uncaught page exceptions are invisible.

`04-oracle-C/O11` PASSED against a page doing `throw new Error('render pipeline blew up')`; `console_errors` empty. `_run_browser` subscribes to `console`/`requestfailed`/`response` (`scenarios.py:848-865`) but not `pageerror`. Verified standalone that this event class emits **no** console event and one `pageerror`.

### F-9 — NONBLOCKING. A navigation's HTTP status is never an oracle.

`04-oracle-A2/O08` PASSED navigating to `/definitely-missing`. Defensible as a default, but the generator cannot say "this navigation must succeed".

### F-10 — NOT-A-DEFECT. `browser_enabled` honesty.

`browser_enabled: false` → brief says `BROWSER available: no` (`07a`); browser proposals still validate but are SKIPPED with the reason and the gate returns `NOT_VERIFIED` (0/3 required). Planner-true/suite-false (`07b`): same. Chromium binary genuinely missing (`07c`): `BLOCKED`, gate `NOT_VERIFIED` — **fails closed**. Only the Python package missing fails open (F-2).

### F-11 — NOT-A-DEFECT. Evidence is real, non-empty, correctly attributed.

Hand-checked `runs/02-defect`: 8 PNGs ~20 KB, 3 traces 26–50 KB opening as valid zips containing `trace.trace`, each `result.json` stamped with the right `scenario_id`/`run_id`/`iteration`, per-step screenshot prefixes attributing shots to the step that took them, and the defect screenshot visibly showing `owner: Dana Whitfield`.

### F-12 — NOT-A-DEFECT. The §4G `expect_text` fix holds; raised steps are scored.

Proved three independent ways (config 2, live config 3, `O01`), plus every raised step form (`click`, `fill`, `wait_for`, step-level `goto`) scored as a failed `expect_state`.

## Limits

One fixture, one machine, one engine — no general browser-coverage claim. F-4/F-5 escapes are demonstrated against a listener the reviewer owns addressed by an off-allowlist hostname; "would reach the public internet" is an inference from the URL normalization actually observed, not something executed. Live generation ran once, successfully. The project's own test suite was not run by this reviewer.

## VERDICT: **FAIL**

Four BLOCKING defects, three reproducible in under a minute: two independent ways for a browser scenario that observed **nothing** to be recorded as a PASS with "verified" evidence and drive the gate to VERIFIED (F-1, F-2 — the §4G defect class still open one level up); a scenario-level `forbidden` oracle scoring PASS on a string the run rendered and screenshotted (F-3); and an admission bypass letting a model-authored `goto` address a host the loopback rule refuses when spelled canonically (F-4). Everything §4G actually claimed — `expect_text` scoring, raised-step scoring, real chromium execution, real attributable evidence, a live-model browser scenario catching a seeded UI defect, and a gate that refuses when it does — could not be broken, and is confirmed.

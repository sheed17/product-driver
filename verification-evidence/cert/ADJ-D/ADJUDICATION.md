# ADJUDICATION — ADJUDICATOR-D (execution-safety blocking claims)

Recorded verbatim by the campaign controller from ADJUDICATOR-D's return. This
adjudicator did not produce the D-SAFETY findings and did not fix anything.
Its own artifacts are `adj_d01_static.py/.json`, `adj_d01_execute.py/.json`,
`adj_d02_browser.py/.json` in this directory.

Candidate `537ae0b`. Code paths were read first and an independent view formed, then the reviewer's probes were run only as cross-checks. All reproduction was local: loopback listeners on 45911/45921/45922 started and stopped by the adjudicator's own scripts; the only external hostname used (`example.com`, `198.51.100.9`) went through Chromium's **offline** `new URL()` parser and was never dialled.

---

## D-SAFETY-01 — **UPHELD-BLOCKING**

### (a) Mechanism

The safety boundary validates one string and the executor runs a different one.

`GeneratedAction(kind="fixture")` carries two model-authored fields, `fixture_name` and `fixture_content` (`scenario_plan.py:283-284`). `_check_safety` inspects **only the name** (`scenario_validation.py:560-569` — bare filename, no path escape). `fixture_content` does reach `_all_strings` (`:801-806`), but `_all_strings` is consumed by exactly three sweeps — `_SECRET_MATERIAL`, `is_secret_path`, `_AUTHORITY_PATTERNS` (`:499-519`). None of them asks whether the content is a *program*. Nothing in the scenario path ever applies `command_guard`'s code machinery to it.

At execution, `_write_fixture` (`scenarios.py:579-603`) materialises the content at `<artifact_dir>/fixtures/<name>` and records the absolute path in `self._fixtures`. `_do_command` then calls `self._substitute(spec.run)` (`scenarios.py:682`), which expands `{{fixture:NAME}}` to that absolute path (`:605-618`) — **after** validation and after the compiler's independent re-check.

The tail ` {{fixture:NAME}}` contains no control character, no shell operator, no scheme, so `ApprovedCommands.approves` admits it as an ordinary argument tail on an approved prefix (`scenario_validation.py:267-282`). `compile_to_scenario._check` (`scenario_plan.py:777-784`) is *not* a second line of defence here: `allowed` is the set of strings `approves()` just admitted (`cli.py:1614`, `scenario_planner._approved_for`), so it re-affirms the same admission.

Net effect: the string validation judged was `… -q {{fixture:x.py}}`; the string that became a subprocess was `… -q /abs/artifacts/fixtures/x.py`. Because this repo's own approved prefix is an interpreter (`python -m pytest`), that argument is a **program**, and pytest executes its module-level code on collection with the driver's full uid authority.

### (b) Reachability — **the ordinary product path. No hand-edited plan, no non-default flag, no privileged actor.**

Every link verified:

| Link | Evidence |
|---|---|
| The generator prompt **teaches** the pattern | `scenario_generator.py:509-511` documents `{"kind":"fixture","fixture_name":…,"fixture_content":"…"}` and *"reference it later as `{{fixture:invoice.json}}`"* |
| The proposal schema permits it | `actions` is `{"type":"array","items":{"type":"object"}}` (`scenario_generator.py:336-343`) — free-form |
| The parser passes it through unmodified | `_dicts(raw.get("actions"))` → `GeneratedScenario.model_validate` (`:622`, `:687-690`) |
| Safety validation admits it | `safety_reasons` → `[]` |
| **Full** validation admits it | `validate_scenario` → `[]` — safety *and* quality *and* provenance |
| The compiler emits both steps | verified |
| The executor runs the payload | verified |

**The LLM generator is explicitly permitted and instructed to emit `kind="fixture"` with content, and to reference it by `{{fixture:}}`.** This is not a smuggled field; it is a documented feature of the proposal schema.

Decisively: a realistic, ordinary-looking proposal ("repeated witness claim must not double-write") was constructed against the **real** `driver.config.yaml` + `scenarios/*.yaml` approved set and returned **zero refusal reasons across every check the system has**, then compiled with the `{{fixture:}}` tail intact. The chain does not require an adversarial model — a model authoring a Python test case as a fixture and running it under the approved pytest entry is a *natural* reading of the documented pattern, and it silently executes unreviewed code with driver authority.

### (c) Evidence personally reproduced

**Static, real repo config** (`adj_d01_static.json`) — real approved set harvested exactly as `cli._approved_commands` does:

- `.venv/bin/python -m pytest -c pytest-canonical.ini eval/tests/test_phase3_witness.py eval/tests/test_phase3_claim_cas.py -q {{fixture:probe_case.py}}` → `approves` **admitted: true, why: ""**
- `safety_reasons` → `[]`; compiled to a `fixture` step + a `command` step carrying the placeholder.

**Executed** (`adj_d01_execute.json`), isolated throwaway target repo, harmless payload:

```
executed command: .venv/bin/python -m pytest -c pytest-canonical.ini tests_x/test_a.py -q \
                  /…/ADJ-D/work/artifacts/fixtures/probe_case.py
exit_code: 0                         ← the scenario PASSES while running model-authored code
MARKER_WRITTEN_OUTSIDE_TARGET: true  ← wrote into the DRIVER's own evidence tree
loopback_listener_hits: ["/exfil?cwd=…_work_target-repo"]
```

The marker landed at `verification-evidence/cert/ADJ-D/PWNED-OUTSIDE-TARGET.txt` — i.e. the payload wrote into the certification evidence tree itself. It was removed.

**Cross-checks of the reviewer's probes, both confirmed:** `probe_execute.py` reproduces identically; `probe_replay.sh` on the real CLI gives `A-RAN.txt: no` (unapproved shell correctly refused) and **`B-RAN.txt (model-authored fixture code executed?): YES`**.

**Coverage gap confirmed:** `tests/test_scenario_generation.py` + `tests/test_post_remediation_contract.py` — 144 passed. `fixture_content` appears in tests only as `"x"` and `"rules are relaxed"` (the latter caught by the *name* `CLAUDE.md`, not the content). No test exercises fixture content as code.

**Against the standard:** verbatim match on *"execute generated actions outside its authority"*. It additionally reaches the second clause — the payload can rewrite `driver.config.yaml`, the driver's source package, `runs/`, and `verification-evidence/`, so the system cannot vouch for its own verification record while dynamic generation is on. And three source-of-truth statements are false as written: `README.md:281` "It never proposes shell"; `scenario_plan.py:18-21` "there is no field anywhere in this module that carries a free-form shell command into execution"; `scenario_validation.py:3-6` "Nothing a model writes reaches a subprocess, a socket or a browser without passing every rule here."

### (d) Narrowest systemic fix specified (described, not implemented)

Content inspection is the wrong answer, and is shown below not to work. Three parts, all inside the existing boundary:

1. **Make the validated string and the executed string the same string.** Re-run `ApprovedCommands.approves` on the *post-substitution* command inside `ScenarioExecutor._do_command` / `_do_state_check`, refusing (recorded as a failed assertion) on mismatch. This is the invariant that was actually broken and it closes any future placeholder, not just `{{fixture:}}`. Necessary but **not sufficient on its own** — the substituted path is still admitted as an argument tail.
2. **Make fixtures inert by construction.** Constrain `fixture_name` to a data-extension allowlist (`.json .csv .txt .yaml .ndjson .xml …`), explicitly excluding anything an interpreter will execute, import or collect (`.py .sh .js .rb .pl .ini .cfg .pth`, and `conftest.py`). This preserves the legitimate use the prompt advertises (an invoice JSON fed to an approved CLI) and removes the code path by construction rather than by pattern.
3. **Belt:** assert the substituted value resolves inside `<artifact_dir>/fixtures/`.

If the founder prefers assurance over capability, the strictly narrower option is to **sever the channel**: forbid `{{fixture:}}` expansion inside `command` / `state_check` strings entirely and permit it only where the fixture is consumed as data (request `body`/`url`). That is a one-predicate change and closes the class completely, at the cost of "hand an approved CLI a generated data file." Flagged as a capability/assurance tradeoff rather than decided.

---

## D-SAFETY-02 — **UPHELD-BLOCKING**

### (a) Mechanism

The validator's scheme test is strictly narrower than the executor's.

- Validator (`scenario_validation.py:541`): `if step.goto and step.goto.startswith(("http://", "https://"))` → only then is `_local_url_problem` consulted.
- Executor (`scenarios.py:913`): `target = step.goto if step.goto.startswith("http") else _join_url(app_url, step.goto)`.

The gap is every string starting with `http` but not `http://`/`https://`. Such a string is **never inspected** yet is **still treated as absolute** and passed to `page.goto`. Chromium's WHATWG parser, in the "special authority ignore slashes" state, then reconstitutes the authority: `http:/host/x` → `http://host/x`. The approved `app_url` is replaced.

This is a denylist where an allowlist was required, and it fails in the one direction that matters: the validator is *wider* than the executor.

### (b) Reachability — ordinary product path, and the reviewer **understated** it

No hand-edited plan, no privileged actor. On the flag: the reviewer wrote *"the escape needs `--browser`."* **That is not correct.** `cli.py:1296-1298`:

```python
if scenario.mode == "browser" and not config.run.browser_enabled:
    note("scenario is a browser scenario — enabling browser support for this run")
    config.run.browser_enabled = True
```

`cmd_evaluate` does the same unconditionally at `cli.py:2502-2503`. The repo **ships** `scenarios/browser_generic.yaml` with `mode: browser`. So `python -m neyma_product_driver run --scenario browser_generic` enables the browser with **no flag at all** — this is the documented way to get browser coverage, and `driver.config.yaml:284` says so in a comment ("Enabled automatically for browser scenarios"). The correct statement is: *the default `scenario: backend_generic` does not reach it; any browser-mode run does, without a non-default flag.*

### (c) Evidence personally reproduced

**Static, real repo config** (`adj_d01_static.json`, section `G_goto`) — all `admitted: true, reasons: []`, `validator_inspected: false`, `executor_treats_as_absolute: true`: `http:/example.com/admin`, `http:example.com/admin`, `http:\\example.com\admin`, `httpx://example.com/admin`. The two plain forms (`http://`, `https://`) are correctly refused, which is what makes the mismatch a gap rather than a general failure.

**Executed against real Chromium** (`adj_d02_browser.json`), two loopback listeners — 45921 = approved `app_url`, 45922 = never validated:

```
proposed_goto:              "http:/127.0.0.1:45922/escaped-the-approved-target"
safety_reasons:             []
final_url:                  "http://127.0.0.1:45922/escaped-the-approved-target"
visible_text:               "role=OFFTARGET"
hits_on_unvalidated_port:   ["/escaped-the-approved-target"]
```

**Off-target resolution, confirmed offline** via Chromium's own `new URL()` on `about:blank` — nothing dialled: `http:/example.com/admin` → `http://example.com/admin` (host `example.com`); same for `http:example.com/admin` and `http:\\example.com\admin`; `http:/198.51.100.9:1/x` → host `198.51.100.9:1`.

**A finding the reviewer missed, which strengthens the case.** The scenario's assertions were scored, which the reviewer did not do:

```
assertions: [{"kind":"expect_visible","target":"…expect_text 'role'","passed": true}]
scenario_result_passed: true
```

The scenario **passed**, and its only oracle was satisfied by text fetched from a host validation never approved, with `obs.url` recording the off-target URL. So this trips a second clause of the standard, not just the first: the driver reported verified coverage of the product on evidence that did not come from the product. That is *falsely ACCEPT unverified work* and *misattribute evidence*, not merely an authority escape.

**Coverage gap confirmed:** `tests/test_scenario_generation.py:219` is the only `goto` refusal test in the suite, and it tests the one shape that already works (`https://example.com/admin`). No variant of the mismatch is covered.

**Against the standard:** verbatim match on *"execute generated actions outside its authority"* — the authority bound is stated three times (`scenario_validation.py:538`; module docstring; `README.md:286`) — **plus** the false-accept clause established above. The model chooses the full URL, so the path component is an outbound channel for anything in its context (task text, acceptance criteria, diff filenames, founder principles). Reachable, reproducible, executed.

NONBLOCKING was considered on the grounds that browser is off in the shipped default config and the navigation is recorded in evidence, and rejected: the escape is reachable with no non-default flag on a configuration this repo ships and that `POST-DYNAMIC-REMEDIATION.md §4G` certifies end-to-end, and the passing-assertion result means the defect can produce a false ACCEPT, which the standard treats as blocking irrespective of blast radius.

### (d) Narrowest systemic fix specified

**Replace the scheme denylist with an allowlist, in the validator, so it is strictly narrower than the executor:** accept a `goto` only if it is either (i) a purely relative path beginning with a single `/` (and not `//`), or (ii) a string beginning with `http://` or `https://` that passes `_local_url_problem`. **Refuse everything else outright**, with a reason. This closes `http:/`, `http:`, `http:\\`, `httpx://` and every future scheme-shaped variant by construction rather than by enumeration.

Then close the seam properly, which is the systemic half: **have the validator compute the exact absolute target the executor will navigate to, and have the executor consume that resolved target instead of re-deriving it** from `startswith("http")`. This is the same single-resolution-point discipline `resolve_http_target` was supposed to provide and (per D-SAFETY-04, not re-adjudicated here) currently does not. One resolution function, two callers, no second opinion.

---

## Corrections to the reviewer

1. **Reachability of D-SAFETY-02 was understated.** "The escape needs `--browser`" is wrong. `cli.py:1296-1298` and `cli.py:2502-2503` auto-enable the browser for any `mode: browser` base scenario, and the repo ships one. No flag is required.
2. **The reviewer missed the strongest part of D-SAFETY-02.** It stopped at "it navigated off-target." The scenario's oracle *passed* on the off-target page and `ScenarioResult.passed` was `true` — so this is a false-accept defect, not only an authority escape.
3. **D-SAFETY-05's framing of D-SAFETY-01 is wrong.** The reviewer wrote that `CommandGuard` "is the layer that would have contained D-SAFETY-01." It would not have, and this was tested directly against the actual chain:
   - `CommandGuard._script_targets(".venv/bin/python -m pytest … /abs/fixtures/probe_case.py")` → **`[]`**. It breaks at the `-m` token (`command_guard.py:691-692`), so `inspect_scripts` finds nothing to read.
   - `_code_reason(payload, inline=False)` → **`None`** — and this is by deliberate design (`command_guard.py:713-717`: the on-disk rule is intentionally narrow so ordinary product code that spawns subprocesses or makes HTTP calls is not blocked).
   - `_code_reason(payload, inline=True)` → **`None`** for this payload too.
   - `classify_command(cmd)` → **`None`**.

   Wiring the existing guard into scenario validation would therefore **not** have closed D-SAFETY-01. That matters for remediation planning: anyone who fixes D-SAFETY-05 and believes D-SAFETY-01 fell with it will ship an unfixed blocker. The fix must make fixtures inert and re-validate post-substitution, not pattern-match content.
4. Everything else checked in the reviewer's D-SAFETY-01 write-up — the line references, the "admitted under the *repo's* approved set" claim, the executed marker and socket, the authority reach map, the CLI replay result, and the three false documentation guarantees — is **accurate as written**. `probe_execute.py`, `probe_replay.sh` and `probe_authority_reach.py` all reproduce.

---

## Mechanical remediability

**D-SAFETY-01 — yes, mechanically remediable.** Post-substitution re-validation is a few lines in `ScenarioExecutor._do_command`/`_do_state_check`; the fixture-name extension allowlist is a few lines in `_check_safety`; the containment assertion is one line in `_write_fixture`. All three live inside the existing safety boundary and executor. No change to product authority, to the approved-command model, to what a human must approve, or to founder governance. One caveat: the extension allowlist narrows what a fixture may be, and the stricter "sever `{{fixture:}}` from command strings" variant removes a documented capability. Both are recorded above; the prompt text in `scenario_generator.py:509-511` must be updated to match whichever is chosen, or the generator will keep proposing scenarios the boundary now refuses.

**D-SAFETY-02 — yes, mechanically remediable, and cleanly.** The allowlist predicate is a single edit at `scenario_validation.py:539-544`; sharing one resolution function with `scenarios.py:913` is a small refactor of an already-existing seam. No authority or governance change, and it strictly *narrows* what is admitted, so it cannot introduce a false accept. The one thing to watch is false refusals: relative `goto`s (`"/"`, `"/operator/"` — exactly what `scenarios/browser_generic.yaml` uses) must keep passing, and both are covered by branch (i) of the predicate.

**Both fixes need regression tests that do not currently exist**: the suite has one `goto` refusal case (the shape that already works) and no case where `fixture_content` is code.

---

## Controller's disposition of the capability/assurance choice in D-SAFETY-01(d)

The adjudicator flagged the choice between (a) the data-extension allowlist and (b) severing `{{fixture:}}` from command strings as a founder call. The controller resolves it as an ordinary implementation choice, not a founder decision, and selects **(a) plus parts 1 and 3**: it closes the defect by construction while preserving the capability the generator prompt already documents, and it neither expands nor contracts any product authority. Option (b) remains available if a later reviewer shows (a) is insufficient.

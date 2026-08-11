# D-SAFETY — INDEPENDENT CERTIFICATION REVIEW

Recorded verbatim by the campaign controller from reviewer D-SAFETY's return. The
reviewer could not write this file itself (subagent report-file creation is refused
by the harness); the probe scripts and raw JSON in this directory are the reviewer's
own artifacts and were written by it.

**Candidate:** `537ae0b` (main; parent `dcc649e` == origin/main) · **Interpreter:** `.venv/bin/python`

## VERDICT: **FAIL** — 2 BLOCKING, 7 NONBLOCKING

All attacks were harmless and local: loopback listeners on 45211/45212, 45301/45302, 45401, 45999 started and stopped by my own scripts; reserved addresses (`198.51.100.9`, `203.0.113.9`, `.invalid` TLD) used for admission-only probes and never dialled; the only "credential" transmitted was a synthetic value I set myself. Fork-bomb strings were checked for refusal **statically only**.

**Baseline health:** the repo's own 223 safety/contract tests pass. Every finding below is a gap that suite never covered.

---

## BLOCKING DEFECTS

### D-SAFETY-01 — Model-authored code executes with full driver authority via the `fixture` → `{{fixture:}}` → approved-prefix chain

**Mechanism.** `GeneratedAction(kind="fixture")` lets a model author *arbitrary file content* under a bare name. Validation checks the **name** (`scenario_validation.py:560-569`) and never the content as code — `_all_strings` sweeps `fixture_content` only for literal credential shapes and authority *paths*; `command_guard`'s `_EFFECT_CODE_PATTERNS` / `_INLINE_ONLY_CODE_PATTERNS` are never applied to it. `ScenarioExecutor._substitute` (`scenarios.py:605-618`) then expands `{{fixture:NAME}}` into the fixture's absolute path **after** validation, inside `spec.run`. The tail ` {{fixture:test_pwn.py}}` has no control character, no shell operator, no scheme — so `ApprovedCommands.approves` admits it as an ordinary argument tail on **this repository's own approved pytest entry** (probe P16, admitted under the *repo* set, not just a permissive one).

**Repro:**
```
.venv/bin/python verification-evidence/cert/D-SAFETY/probe_execute.py
bash             verification-evidence/cert/D-SAFETY/probe_replay.sh
.venv/bin/python verification-evidence/cert/D-SAFETY/probe_authority_reach.py
.venv/bin/python verification-evidence/cert/D-SAFETY/probe_env_exfil.py
```

**Observed.** `validation_admitted: true`, zero reasons. Executed command: `…/python -m pytest -c pytest-canonical.ini eval/tests/test_a.py -q /var/…/artifacts/fixtures/test_pwn.py`. The payload wrote a file **outside the target workspace** and opened an outbound socket (`loopback_listener_hits: ["/exfil?ok=1"]`). Read-only reach probe (uid 501): `driver_config`, `driver_source_package`, `driver_claude_dir`, `driver_evidence_tree`, `driver_runs_dir`, `driver_git_dir`, `founder_context` — all reachable **and writable**; I performed no writes to them. Env-exfil probe: `listener_received: ["fake=not-a-real-token-0000&stripped=%3Cabsent%3E"]` — `ANTHROPIC_API_KEY` is correctly stripped by `runner.child_env`; nothing else is. The chain survives the **real CLI replay path**: `probe_replay.sh` → `B-RAN.txt (model-authored fixture code executed?): YES`.

**Expected.** `README.md:281` "It never proposes shell"; `scenario_plan.py:18-21` "there is no field anywhere in this module that carries a free-form shell command into execution"; `scenario_validation.py:10-16` "Nothing a model writes reaches a subprocess, a socket or a browser without passing every rule here." All three are false: `fixture_content` *is* a free-form program, and the substitution that delivers it to a subprocess runs after every rule.

**Classification: BLOCKING DEFECT** — executes generated actions outside its authority, and can rewrite the evidence and enforcement config the rest of certification depends on.

---

### D-SAFETY-02 — Browser `goto` escapes loopback: validator's scheme test is narrower than the executor's

**Mechanism.** Validator (`scenario_validation.py:539-544`): `startswith(("http://","https://"))`. Executor (`scenarios.py:913`): `target = step.goto if step.goto.startswith("http") else _join_url(app_url, step.goto)`. Every string starting with `http` but not `http://`/`https://` is **never inspected and still treated as absolute**: `http:/host/x`, `http:host/x`, `http:\\host\x`, `httpx://host/x`. Chromium's WHATWG "special authority ignore slashes" state resolves `http:/host/x` → `http://host/x`, replacing the approved `app_url`.

**Repro:** `.venv/bin/python verification-evidence/cert/D-SAFETY/probe_http_browser.py` (`browser_goto` + `browser_execution` sections)

**Observed.** Executed against two loopback listeners — 45211 = approved `app_url`, 45212 = a host:port the validator never saw:
```
goto: "http:/127.0.0.1:45212/escaped-the-approved-target"   admitted: true, reasons: []
final_url: "http://127.0.0.1:45212/escaped-the-approved-target"
hits_on_unvalidated_goto_port: ["/escaped-the-approved-target"]
```
Off-target admission confirmed **statically without dialling** — B03 `http:/offtarget.invalid/x` → `admitted: true`, `validator_inspected: false`; B11 `http:/198.51.100.9:1/x` → same.

**Expected.** `scenario_validation.py:538` "browser: relative or loopback navigation only"; `README.md:286` "Requests must be loopback."

**Mitigating (does not change severity).** `run.browser_enabled` is `false` in `driver.config.yaml:285` and in `ScenarioRunConfig` — the escape needs `--browser`. That flag is the documented way to run browser coverage, and `POST-DYNAMIC-REMEDIATION.md §4G` certifies browser-mode generated scenarios as proven end to end. Test coverage: `tests/test_scenario_generation.py:219` tests exactly one `goto` refusal, the plain `https://example.com/admin` form; no variant of the mismatch is tested.

**Classification: BLOCKING DEFECT.**

---

## NONBLOCKING LIMITATIONS

| ID | Claim | Mechanism / evidence |
|---|---|---|
| **D-SAFETY-03** | The runner follows redirects off the validated target, and misattributes the evidence | `runner.http_request` uses `urlopen`, which follows 3xx. A validated **relative** path (`path: "/redirect"`) reached a second loopback port; `HttpObservation.url` still recorded the approved URL and the assertion passed on the off-target body. `probe_runtime.py` → `R4_scenario_redirect: {admitted:true, recorded_observation_url:["…45301/redirect"], response_body:["role=offtarget"], offtarget_hits_during_scenario:["/followed"], expect_visible_passed:[true]}`. Not blocking because the destination is chosen by the product, not the scenario — but any open redirect in the product makes it a direct escape. |
| **D-SAFETY-04** | `resolve_http_target` docstring is false (honesty) | `scenario_validation.py:735-738` claims "both validation **and compilation** call it". Its only non-test caller is `scenario_validation.py:527`; `scenario_plan._compile_request` (`:955-967`) copies `url`/`path` verbatim and `_do_request` re-derives via `_join_url`. The command half of the same belt-and-braces claim *is* real (`compile_to_scenario._check`); only the HTTP half is aspirational. Repro: `grep -rn "resolve_http_target" neyma_product_driver tests` |
| **D-SAFETY-05** | No approved-root write confinement and no wrapper-script inspection on generated commands | `approves()` calls only the pure `classify_command` / `classify_worktree_ownership`. `CommandGuard` — owner of `check_write_path` and `inspect_scripts` — is constructed exactly once, `builder.py:155`, for the builder. Admitted statically: T15 `bash scripts/seed.sh ../../../../etc/passwd`, T06 `… ../neyma-product-driver/driver.config.yaml`, T07 `… ../../neyma_product_driver/scenario_validation.py`. `_AUTHORITY_PATTERNS` covers only the *target* repo's surfaces, not the driver's own. This is the layer that would have contained D-SAFETY-01. |
| **D-SAFETY-06** | The prefix model is only as strong as the human's list; no structural mitigation for interpreter-shaped prefixes | Refused **only** because this repo doesn't approve the prefix (all ADMITTED under a plausible permissive list): P05/P09/P19 `env <anything>`; P20 `sqlite3 db ".shell id"` (and sqlite3 is the natural `EFFECT_FAMILY` oracle); P21 `sqlite3 db "ATTACH '/tmp/x' AS y"`; P11 `bash scripts/seed.sh -c 'id'`; P12 `npm run test -- --exec 'id'`; P13/P14 pytest `--rootdir=/ /etc` and `-p evilplugin`. `doctor`'s approved-command report does not flag interpreter-shaped entries. |
| **D-SAFETY-07** | Loopback scoping is host-only, and the two defaults disagree | `DEFAULT_LOCAL_HOSTS` (`scenario_validation.py:49`) includes `0.0.0.0`; `ScenarioGenerationConfig.local_http_hosts` (`config.py:194-196`) does not — H15 is admitted under one and refused under the other. No port scoping: any loopback port is admitted (H13, H30), so a scenario may address local services that are not the product. |
| **D-SAFETY-08** | Header/body values are not control-char checked by the boundary | `_control_character_problem` covers commands and `request.url or request.path` (`:523`) but not `request.headers` values or `request.body`. CRLF in a header is refused only by the stdlib (`Invalid header value b'a\r\nX-Injected: 1'`, R2) — the guarantee is `urllib`'s, not this module's. Arbitrary `Authorization` on a local request is admitted (R3, status 200); defensible for loopback, but nowhere stated. |
| **D-SAFETY-09** | `is_secret_path` misses a bare `.env` argument | `(?:^|/)\.env(?:\.[^/]*)?$` matches `./.env` (T10, refused) but not space-preceded `.env` (T09 `bash scripts/seed.sh .env` → **admitted**). `command_guard`'s reader rule covers `cat`/`grep`/`cp` but not an approved wrapper taking the path as an argument. |

---

## CLOSED — attacked and held

| Area | Probes | Result |
|---|---|---|
| Control characters | C01–C12: `\n \r \v \f \0`, U+2028, U+2029, U+0085 NEL, U+00A0, U+200B, U+202E bidi, tab | **All refused** on the raw string before normalization, as documented |
| Unicode normalization | C14 fullwidth `ｅcho`, C15 `；`, C16 `∕` | C14 refused (never normalizes into an approved prefix). C15/C16 admitted but are literal argument bytes to a shell — verified non-composing |
| Shell composition | S01–S10, S13, S14: `;` `&&` `\|\|` `\|` `&` `>` `>>` `<` `<( )` `( )` `2>&1` | **All refused** as tail-introduced operators |
| Brace/glob | S11, S12 | Admitted — argument expansion, no new command. Correct |
| Command substitution | X01–X03, X08: `$( )`, backtick, `$( )` inside double quotes, credential-shaped `$VAR` | **All refused.** X04 `'$(id)'` in single quotes correctly admitted as inert |
| Quote boundary | Q01/Q02 unbalanced, Q04 close-then-compose, Q07 | Refused. Q03/Q05/Q06 admitted — each hand-verified against POSIX semantics to be a single literal argument; the scanner agrees with `bash` |
| Prefix confusion | P01 `…check_env.pyevil`, P03 leading tab, P25 `echoevil` | Refused |
| Inline interpreters | P06 `bash -c`, P07 `sh -c`, P08 `python3 -c`, P18 bare `env`, P23 `curl -d` | Refused by `classify_command` **even when the prefix is approved** |
| HTTP target escape | H01–H04, H07, H09–H12, H14, H16–H20, H22–H26, H28, H29 — absolute url, absolute-in-path, `//host`, `\\host`, userinfo, decimal/octal/hex IP, `127.1`, IPv4-mapped IPv6, trailing dot, rebinding-shaped name, `file:`/`gopher:`/`data:`/`javascript:`, mixed-case scheme, leading space, single-/no-slash url, CRLF | **All refused.** The B2.3 remediation holds under every variant tried |
| `url` + `path` both set | — | Refused as ambiguous |
| Fixture **name** | X01–X03 `../../escape.txt`, `/tmp/escape.txt`, `a\b.txt` | Refused; `sanitize_filename` holds |
| Repository authority | T01–T04 `CLAUDE.md`, `BUILD-STATUS.yaml`, `.claude/`, `founder_context/` | Refused |
| Credential paths/material | T08, T11, T12, T13, T14, T16 `~/.aws`, `~/.ssh/id_rsa`, `.git-credentials`, `$GITHUB_TOKEN`, `sk-ant-…`, keychain | Refused |
| Git / repo authority via command | A08–A10 `git commit`, `git push`, `git checkout` | Refused by `classify_command` + `classify_worktree_ownership` |
| Fork-bomb-shaped strings | A11, A12 `:(){ :\|:& };:` | **Refused statically. Never executed** |
| Replay re-validation | `probe_replay.sh` scenario A | Hand-edited plan naming `/bin/sh -c '…'` was **refused** by `safety_reasons` on the real CLI. `run-generated` does re-validate safety |
| **False-refusal direction** | F01–F09 | **0 false refusals.** `HAVING count(*) > 1`, `'DUP:'\|\|key`, `WHERE a < b`, `json_extract(payload,"$.id")`, `'^(a\|b)+$'`, `pytest -k 'idempotency and not slow'`, and all three of this repo's own approved commands verbatim are ACCEPTED. The quote-aware scanner does **not** mechanically disarm the `EFFECT_FAMILY` oracle |

Also upheld from prior self-reports: the B2 newline bypass and B2.3 `request.path` bypass are genuinely closed; `ANTHROPIC_API_KEY` is stripped from child environments; a scenario cannot introduce an undeclared service (compiler raises); `wait_ms`/`timeout_s` bounds enforced in both validation and execution.

---

## ARTIFACT INDEX — `verification-evidence/cert/D-SAFETY/`

| File | Contents |
|---|---|
| `probe_commands.py` / `.json` | 87 command payloads (C/S/X/Q/P/A/F), admitted-or-refused under **both** the repo's approved set and a permissive one |
| `probe_full_safety.py` / `.json` | 24 rows through full `_check_safety` (authority + secret sweeps), commands and fixtures |
| `probe_http_browser.py` / `.json` | 30 HTTP targets + 11 `goto` + 1 executed browser escape |
| `probe_execute.py` / `.json` | The fixture→approved-prefix chain, executed |
| `probe_authority_reach.py` / `.json` | Read-only reach map of authority surfaces |
| `probe_env_exfil.py` / `.json` | Synthetic env value leaving the process |
| `probe_runtime.py` / `.json` | Redirect following, header CRLF, arbitrary headers, redirect through a validated scenario |
| `probe_replay.sh` | Two hand-edited plan scenarios through the real `scenarios run-generated` CLI |

---

## BOTTOM LINE

The deterministic **string** boundary is genuinely strong — control characters, shell composition, substitution, quote-boundary tricks, prefix confusion and every HTTP host-escape shape tried were refused, and legitimate SQL/JSON/regex oracles were *not*. But it guards the wrong perimeter in two places: it scrutinises the command string while `fixture_content` carries an unexamined program that `{{fixture:}}` hands to a subprocess **after** every rule; and it scrutinises `goto` with a narrower scheme test than the executor's, so one missing slash walks out of loopback. Both are reproducible and were executed. The first also reaches the driver's own config, source and evidence tree — so the system cannot currently vouch for the integrity of its own verification record while dynamic scenario generation is enabled.

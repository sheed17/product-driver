# Neyma Product Driver

Local tooling that takes your place during Neyma development.

It drives a persistent Claude Code **builder** session inside the Neyma
repository, then **operates the actual running product** — a browser when there
is a UI, real local API calls, CLI runs, logs and database inspection when there
isn't — and asks a second Claude session, the **product evaluator**, whether the
observed behaviour is useful, clear and aligned with Neyma. If it isn't, the
driver sends a precise correction back to the *same* builder session and retests.

This is personal development tooling, not a platform. No dashboard, no queue, no
orchestrator, no service.

```
                    ┌──────────────────────────────────────────┐
                    │  neyma-product-driver  (this repo)       │
                    │                                          │
   your task  ──────┼──►  control loop  (max 5 iterations)     │
                    │        │                                 │
                    │        ├─► BuilderSession ───────────────┼──► Claude Code session
                    │        │     cwd = Neyma repo            │     loads Neyma CLAUDE.md,
                    │        │     persistent, resumable       │     settings, hooks, skills,
                    │        │                                 │     subagents  (authoritative)
                    │        ├─► ScenarioExecutor              │
                    │        │     starts services             │
                    │        │     waits for readiness         │
                    │        │     Playwright / HTTP / CLI ────┼──► the running Neyma product
                    │        │     screenshots, traces, logs   │
                    │        │                                 │
                    │        ├─► EvaluatorSession ─────────────┼──► Claude Code session
                    │        │     read-only, structured JSON  │     (read-only: Read/Grep/Glob)
                    │        │                                 │
                    │        └─► ACCEPT / FIX / ASK_USER / BLOCKED
                    │                 │                        │
                    │                 └─ FIX → correction → same builder session
                    │                                          │
                    │   runs/<run-id>/  evidence, state, resume│
                    └──────────────────────────────────────────┘
```

The Neyma repository is the source of truth. The driver reads and operates it;
it never overwrites or weakens its CLAUDE.md authority, settings, hooks, safety
controls, READY-unit selection, acceptance contracts or phase boundaries.

---

## Install

```bash
cd /Users/sammyfammy/neyma-product-driver
python3.13 -m venv .venv          # any Python 3.11+
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
```

## Authenticate

The driver uses your **existing authenticated Claude Code subscription**. There
is nothing to configure:

```bash
claude          # log in once if you never have; then quit
```

`ANTHROPIC_API_KEY` is **not** required. If it happens to be set, API-key
billing takes precedence over your subscription — the driver detects this,
warns, and refuses to continue without explicit confirmation.

The driver never reads, prints, logs or persists OAuth tokens, GitHub tokens,
API keys or any other secret. `doctor` checks that a credential store *exists*;
it never opens it.

## Check the environment

```bash
.venv/bin/python -m neyma_product_driver doctor
```

Verifies Python version, `claude-agent-sdk`, the Claude Code CLI and its
version, authentication, the Neyma repo path/branch/tree, Playwright, Chromium,
required binaries, whether `ANTHROPIC_API_KEY` is set, that the run-artifact
directory is writable, and that every scenario file parses.

## Run

```bash
.venv/bin/python -m neyma_product_driver run \
  --task "Finish the P3 mutation battery: prove each new guard fails against a
          reintroduced defect, then restore it via the in-memory harness." \
  --scenario backend_generic \
  --max-iterations 5
```

Other commands:

```bash
python -m neyma_product_driver doctor      # environment checks
python -m neyma_product_driver status      # latest run, or: status <run-id>
python -m neyma_product_driver evaluate    # run a scenario + judge it once, no builder
python -m neyma_product_driver stop        # halt before the next iteration
python -m neyma_product_driver audit      # audit completion claims (no Claude session)
python -m neyma_product_driver review --run <id>                     # independent reviewer
python -m neyma_product_driver feedback --run <id> --message "..."   # founder direction
python -m neyma_product_driver promote-feedback --run <id>           # make it durable
```

Resume after a restart — the builder's Claude session id is persisted:

```bash
python -m neyma_product_driver run --resume-run 20260721-231500
python -m neyma_product_driver run --resume-session <claude-session-id>
```

Configuration: copy `driver.config.example.yaml` to `driver.config.yaml`. CLI
flags override it.

---

## Pointing it at a Neyma scenario

A scenario is YAML describing how to start the product, how to know it is ready,
how to operate it, and what must (and must not) be observable. Copy a template
from `scenarios/` and edit.

```yaml
name: my_slice
phase: "P3"
mode: backend                 # or: browser

setup:    [".venv/bin/python scripts/check_env.py"]
fixtures: ["eval/golden_set/ground_truth.json"]

services:                     # started by the DRIVER, not the builder
  - name: site
    command: ".venv/bin/python -m http.server 8000 --directory data/active_workspace/site"
readiness:
  - http: "http://127.0.0.1:8000/"
    expect_status: 200
app_url: "http://127.0.0.1:8000"

commands:                     # CLI entry points, exit codes, stdout/stderr
  - run: ".venv/bin/python -m pytest -c pytest-canonical.ini eval/tests/test_phase3_witness.py -q"
    expect_exit_code: 0
requests:                     # real local HTTP calls
  - method: GET
    path: /api/loads
    expect_status: 200
browser:                      # Playwright, when mode: browser
  steps:
    - goto: "/operator/"
    - click: "text=Loads"
    - wait_for: "[data-testid='load-list']"
    - screenshot: "load-list"

expect_state:                 # the oracle: what was actually persisted
  - command: "sqlite3 data/x.sqlite3 'select status from loads'"
    contains: ["DELIVERED"]
expect_visible: ["Awaiting your approval", "Next:"]
forbidden:    ["Traceback", "[object Object]", "checkpoint witness"]

teardown: []
```

Run it:

```bash
python -m neyma_product_driver run --scenario scenarios/my_slice.yaml --task "..."
python -m neyma_product_driver run --scenario my_slice --browser --headed
```

**Why the driver starts the services:** Neyma's builder session runs under a
sandbox that blocks `socket.bind()`. Anything that listens on a port must be
started by the driver's own unsandboxed process. The builder generates
artifacts; the driver serves and drives them.

---

## Context layers

The evaluator sees three layers, assembled fresh for every decision:

```
LAYER A  founder_context/PRODUCT_OWNER_CONTEXT.md      durable product direction
         founder_context/PRODUCT_TASTE_RUBRIC.yaml     15 categories, thresholds,
                                                       ASK_USER boundaries
         → versioned by content hash

LAYER B  Neyma repo, RE-READ BEFORE EVERY DECISION     AUTHORITATIVE
         IMPLEMENTATION-REGISTRY.yaml → the single READY unit + its criteria
         CLAUDE.md, CURRENT.md, and topic-selected excerpts of
         PRODUCT.md / ARCHITECTURE.md / acceptance registry
         → fails closed on 0 or >1 READY units

LAYER C  this iteration's evidence                     builder claim, git diff,
         scenario, commands, screenshots, API responses, logs, prior decision
```

**The repository is authoritative** for the active READY unit, phase scope,
acceptance criteria, architecture, safety invariants and progress. The driver
never keeps a second copy of those. Founder context governs product taste and
choices the repository leaves open.

Whole documents are never pasted in. `select_sections` scores markdown sections
against the current topics and the active unit id, keeps whole sections, and
respects a character budget. Repository context is cached only against a
fingerprint of HEAD, `git status` and the size/mtime of every file consulted —
so a phase change mid-run is picked up rather than served stale.

Every decision records its provenance to `prompt-manifest.json`: founder context
version, repository HEAD and branch, active unit id and status, the acceptance
criteria in force, every repository file consulted, every evidence file
consulted, and the founder-feedback count.

## The completion auditor

A builder saying something is done is a **claim**, not a fact. Before any
completion is accepted, the auditor independently checks it against the
registry, the status surfaces, the actual receipt files, git state, and this
run's test output.

```
1. builder claims a runnable checkpoint or completion
2. completion auditor   → factual / control-plane claims
3. scenario runner      → actual behaviour
4. product evaluator    → functionality and product quality
5. driver combines both
```

`ACCEPT` requires **all** of: completion claims VERIFIED, required independent
review complete, the scenario passing, the product evaluator returning ACCEPT,
and the repository permitting completion. A product evaluator's ACCEPT never
overrides a contradicted claim.

The distinctions it enforces:

| not the same as | |
|---|---|
| code exists | behaviour proven |
| tests exist | tests passed |
| tests passed | acceptance contract passed |
| targeted suite passed | canonical suite passed |
| implementer review | independent review |
| independent review | final adjudication |
| finalizer claimed | finalizer ran |
| clean-clone PASS claimed | clean-clone gate ran |
| a checkpoint commit | the final content commit |
| a content commit | the status-metadata commit |

It also refuses: weighted criteria still PENDING under a COMPLETE claim, a
status document proving itself, a cited file that does not exist, a receipt
whose commit/tree does not match the validated state, an environmental failure
recorded as an ordinary skip, a later phase READY while a dependency is open, a
risk claimed contained before its designated phase, a required limitation
deleted from a canonical document, and a progress percentage the criteria do not
support.

Progress is **computed** from criterion weights, never taken from the builder's
prose:

```
COMPLETION CLAIM: CONTRADICTED
IMPLEMENTATION STATE: PRESENT
VERIFIED PROGRESS: 0%  (ceiling without independent review: 91%)
MISSING: independent_review, final_adjudication
NEXT SAFE ACTION: restore the status documents to the highest evidence-supported state
```

Audit the repository directly, without any Claude session:

```bash
python -m neyma_product_driver audit
python -m neyma_product_driver audit --report /path/to/builder-report.md --json
```

### Implemented, awaiting review

When the only outstanding criteria are ones a single session structurally cannot
award itself, the run ends as **`IMPLEMENTED — AWAITING INDEPENDENT REVIEW`**.
That is neither downgraded into failure nor upgraded into completion. The
implementation stands; the acceptance does not.

### Honest rollback

When claims outrun evidence, the correction sent to the builder restores status
documents to the highest evidence-supported state while **preserving all
implementation code and valid evidence**. It explicitly forbids deleting or
weakening any acceptance guard to obtain a green result, forbids
`git stash/restore/clean/checkout --`, and leaves criteria requiring an
independent session PENDING.

### Independent reviewer

```bash
python -m neyma_product_driver review --run <run-id>
```

Launches a **fresh** Claude session that never resumes or inherits the builder
conversation, is read-only (`Read`/`Grep`/`Glob` only, everything else denied),
receives repository authority and actual evidence rather than conversation,
returns findings each citing an evidence path, explicitly adjudicates every
discrepancy the auditor raised, and **never writes a status file**. An
unparseable reply degrades to `INSUFFICIENT_EVIDENCE`, never to a pass.

The driver does not launch it automatically — it pauses and reports, because the
transition from implementer to independent reviewer is yours to authorize.

## The protocol resolver

The auditor answers *"is this claim true?"*. The resolver answers a different
question: **the implementation is right — so why can nothing be finalized?**

It reads the repository's *actual* protocol on every run — CLAUDE.md, the
commit/finalization protocol, the progress protocol, the registry, BUILD-STATUS,
the finalizer implementation, the status-reality guard, the canonical-suite
configuration, the clean-clone gate and the receipt schemas — and records the
file, section and sentence behind every rule it applies. Nothing about commit
conventions, phase names or gate names is hardcoded: a repository that permits
two content commits is judged against two.

```bash
python -m neyma_product_driver protocol --run <run-id>
python -m neyma_product_driver protocol --sources        # every rule it discovered
```

It classifies each commit since the authorized baseline by the files it actually
changed — `BASELINE`, `CONTENT`, `REMEDIATION_CONTENT`, `REVIEW_EVIDENCE`,
`METADATA_STATUS`, `FINALIZER_GENERATED`, `UNKNOWN` — never by its message, then
compares the observed graph with the one the repository's own rule describes.

### Deadlocks, not "BLOCKED"

Failed gates and authority rules become a dependency graph, and cycles are
*found* in it rather than pattern-matched:

```
commit topology [VIOLATED] → the status-reality guard [FAILING]
  → the canonical suite [FAILING] → the finalizer [NOT_RUN]
  → derived status (finalizer-owned) [STALE] → back to: commit topology [VIOLATED]
```

No amount of retrying any single gate clears that. When the loop is not closed —
a green suite, or an authorized manual-finalization escape — no deadlock is
reported. Blockers stay separate: a commit-history deadlock and a TLS failure are
two findings, not one bad day.

| status | meaning |
|---|---|
| `CONSISTENT` | topology and authority hold |
| `VIOLATION` | a stated rule is broken |
| `DEADLOCK` | a closed cycle of blocking gates |
| `REQUIRES_APPROVAL` | the repair needs a human decision about history or authority |
| `BLOCKED_ENVIRONMENT` | a gate could not run — not a product failure, and not a PASS |
| `BLOCKED_AUTHORITY` | the repository contradicts itself; the driver has no standing to choose |

### Approval

The driver **proposes** repairs and **never performs** one. Every materially
valid option is generated and ranked by which repository rules it satisfies,
whether evidence survives, whether history is rewritten, and whether a remote is
affected. Hand-editing derived status is never recommended unless the repository
explicitly authorizes manual finalization; deleting evidence is never an option.

```bash
python -m neyma_product_driver approve --run <run-id> \
  --option A --confirmation "APPROVE P3 LOCAL HISTORY NORMALIZATION"
```

An approval binds to one option **and one plan hash** covering HEAD, the
baseline, the commit range and the rules themselves — if the repository moves,
the approval expires. `"go ahead with whatever"` is not an approval. Shared or
pushed history raises severity and demands the remote impact be acknowledged
explicitly.

After approval the driver emits an exact builder prompt (plan hash, current
HEAD/tree, authorized baseline, archival refs to create, expected graph,
prohibited operations, verification commands, stop conditions) and then inspects
the resulting graph and tree. A deviation from the approved plan is `BLOCKED`.

### Where it sits in the loop

```
builder claim → completion auditor → protocol resolver → scenario runner
              → product evaluator → combine
```

Decision precedence: authority conflict → approval required → deadlock →
contradicted completion claim → code/test findings → environmental blockers →
independent-review requirement → product evaluation. A product-evaluator
`ACCEPT` can never override a protocol violation, and a green targeted suite
cannot make an invalid commit topology valid. The reviewer is not launched
against a topology the repository forbids.

## The diagnostic investigator

The known handlers — the auditor, the protocol resolver, the topology analyzer —
each diagnose a failure class someone anticipated. The investigator diagnoses the
ones nobody did. It behaves like a senior engineer: observe the actual evidence,
notice what contradicts what, form a few competing hypotheses, run the smallest
read-only probe that tells them apart, believe the probe over the story, and
repeat until one explanation carries all the load — or it genuinely needs you.

```bash
python -m neyma_product_driver investigate
python -m neyma_product_driver investigate --run <run-id> --issue "finalizer refuses"
python -m neyma_product_driver investigate --max-iterations 8
```

### It is not a pile of keyword handlers

Diagnosis runs through one general loop over observations, hypotheses and probes.
The known handlers are available *as probes* (classify commits, check a receipt,
run the protocol resolver), but nothing keys on a failure-class keyword or a unit
id. A test builds a failure class whose vocabulary appears nowhere in the
implementation and asserts that absence — so the suite fails if the investigator
ever starts matching keywords instead of reasoning.

The mechanism is domain-blind: an **observation** carries *signals*
(`content_commit_count=2`), a **hypothesis** *predicts* signals, a **probe**
*produces* them. When reality matches a prediction the hypothesis gains support;
when reality speaks to a prediction and disagrees, the hypothesis is **refuted**
— not merely doubted. The *judgment* (which hypotheses, which probe) comes from a
fresh Claude subagent; the *verdict* comes from the engine matching predictions
to real probe results.

### It never confuses a story with a fact

Evidence is typed by reliability: `DIRECT` (a command ran, a file exists),
`DERIVED` (computed), `REPORTED` (prose — a builder or reviewer claim). A
`REPORTED` observation can raise suspicion but can **never** promote a hypothesis
to supported. So when the builder says *"socket.bind blocks the finalizer"*, that
is recorded as a claim, and a probe showing the socket tests actually pass under
the finalizer refutes it. Facts, inferences, hypotheses, disproven and unresolved
are kept strictly apart, and the timeline shows how the diagnosis *moved*.

### Autonomy and its limit

For this local workflow it acts freely: read files, inspect git, run tests and
linters, run in-memory predicate probes, query a database read-only, hit a local
service. It **refuses** anything consequential — a history rewrite, a push, a DB
write, an external effect — and shell metacharacters in a "read-only" command.
When a consequential action is the only way forward it does not invent an
approval system; it stops and prints:

```
ASK FOUNDER
  what:     run a consequential probe: does resetting fix it?
  command:  git reset --hard HEAD~1
  risk / rollback / benefit ...
```

### Budget and self-correction

It defaults to 8 iterations, extends while it is still learning and confidence is
rising, and stops early on a load-bearing root cause, a founder decision, a flaky
environment (the same probe returning different answers), or probes that stop
teaching it anything. It reloads the repository before every step and flags it if
the repository changes mid-investigation. Every hypothesis transition is
recorded, so *"H1 disproven → H2 supported → H2 fixed but status still red → H3
supported"* reads as a history, not a verdict.

### Where it sits, and what it hands back

```
builder → completion auditor → protocol resolver → investigator (when needed)
        → scenario runner → product evaluator → combined decision
```

It is callable from any stage, and the loop invokes it on the documented triggers
(a builder/test disagreement, an unproven environmental blocker, a repeated fix
that changes nothing, low evaluator confidence, an unexplained BLOCKED). On a
supported root cause — and only then, never under low confidence — it writes a
grounded builder correction: the supported cause, the explanations already ruled
out, the exact evidence, the smallest justified fix, what to preserve, targeted
verification, broader gates, and explicit stop conditions.

Artifacts land under `runs/<run-id>/investigation/`: `observations.json`,
`contradictions.json`, `hypotheses.json`, `probes.json`, `result.json`, and a
readable `timeline.md`.

## The prompt-quality contract

A `FIX` reaches the builder only if it supplies all of:

| field | meaning |
|---|---|
| `requirement_reference` | active unit id, acceptance criterion, `AC-<AREA>-<nnn>`, or a named CLAUDE.md rule |
| `product_principle_reference` | founder principle or rubric category id |
| `scenario` | the exact scenario executed |
| `observed_result` | what was actually observed |
| `expected_result` | what should have been observed |
| `evidence_paths` | at least one concrete path |
| `preserve` | behaviour that must not change |
| `retest` | exactly how to retest |

Rejected automatically, becoming `BLOCKED` rather than generated work:

- any missing field above, or no evidence path
- `observed_result == expected_result` — no discrepancy means no work
- corrections under 120 characters
- the named trash phrases (`keep going`, `improve this`, `make it better`,
  `make it production-ready`, `polish the workflow`, `enhance robustness`,
  `clean it up`, `finish the feature`, `add more tests`, …) unless paired with
  observed evidence, an expected result and a retest
- confidence below `minimum_for_fix` (0.6), or below
  `minimum_for_customer_facing_fix` (0.75) when `customer_facing` is set
- a correction identical to one already sent in this run — the loop is not
  converging, so it stops

The rejected decision is preserved at `iteration-NN/rejected-decision.json` with
the reasons, so you can see what the evaluator tried to do.

## Founder feedback

```bash
python -m neyma_product_driver feedback --run <run-id> \
  --message "Never render a TMS-only empty state; customers may have no TMS."
```

Stored at `runs/<run-id>/founder-feedback.json` and injected into **both** the
evaluator and builder prompts as the highest-priority product input. It
overrides evaluator taste. It does **not** override repository authority, and it
does **not** touch the durable context.

To make it permanent — shows the exact diff and requires confirmation:

```bash
python -m neyma_product_driver promote-feedback --run <run-id>
```

It refuses to run non-interactively without `--yes`.

## What the evaluator judges

Neyma should feel like an attentive freight operations teammate, not a generic
dashboard or chatbot. A surface should make clear: what happened, what matters
now, what Neyma knows, what it *inferred*, what evidence exists, what is
missing, who owns the next obligation, what Neyma is doing, what is blocked,
whether approval is required, and what happens next.

The evaluator returns exactly one structured decision:

```json
{
  "decision": "ACCEPT | FIX | ASK_USER | BLOCKED",
  "summary": "...",
  "observed_behavior": ["Observed: ..."],
  "problems": ["..."],
  "correction_prompt": "...",
  "evidence_paths": ["..."],
  "confidence": 0.0
}
```

- **ACCEPT** only after operating the real functionality. Never because unit
  tests passed. If no scenario ran, nothing about the product was observed.
- **FIX** when it can make a strong, concrete product judgement. The
  `correction_prompt` goes back to the same builder session, and the scenario
  reruns.
- **ASK_USER** only for a genuine product-direction or taste decision, with
  evidence, option A, option B, a recommendation, and the decision needed.
- **BLOCKED** for infrastructure, missing credentials or technical blockers.

An unparseable evaluator reply degrades to **BLOCKED**, never to a guess. A
`FIX` without a correction prompt is rejected at the type level, and a
correction attached to a non-`FIX` decision is discarded so it can never leak
onward.

---

## Safety

The driver never automatically commits, pushes, merges, tags, deploys, publishes
packages, creates external resources, sends Slack/email/SMS, touches production
credentials or databases, moves money, or alters customer systems. Read-only web
research and local testing are allowed.

Enforcement is in two independent places, because Neyma's own
`settings.local.json` pre-approves some tools and a pre-approved `Bash` could
otherwise smuggle a `git push` past a single check:

1. `can_use_tool` — the SDK permission callback. Consequential actions are
   denied and recorded. Anything else that would need interactive human approval
   is also declined and surfaced to you, never auto-approved.
2. a `PreToolUse` hook — fires even for pre-approved tools.

Specifically blocked, mirroring Neyma's own hooks: `git push`, `git commit`,
`git tag`, `git branch -D`, `gh pr/release/repo/api/workflow`, deploy tooling,
container/package publish, cloud CLIs, mutating outbound HTTP, outbound
messaging, production database access — and, because Neyma's P3 kernel is
currently **untracked**, the repository-fatal `git stash`, `git restore`,
`git clean`, `git checkout --` and `git reset --hard`. Edits to Neyma's own
control surfaces (`CLAUDE.md`, `.claude/settings*.json`, `.claude/hooks/`) are
refused: the driver must not weaken the repo's authority to make its own loop
succeed.

`bypassPermissions`, `dontAsk` and `auto` permission modes are rejected by
config validation. `dangerouslyDisableSandbox` is never used.

**Secrets.** Every string is passed through a redactor before being printed,
stored, or shown to the evaluator: Anthropic/OpenAI keys, GitHub tokens, Slack
tokens, JWTs, AWS keys, private-key blocks, database URLs with credentials, and
`KEY=value` forms. Anything resembling a full environment dump is dropped
outright. `ANTHROPIC_API_KEY` is stripped from every child process the driver
starts.

---

## Evidence

Each iteration is persisted under `runs/<run-id>/`:

```
runs/20260721-231500/
  state.json                  run state, session ids, resume point
  iteration-01/
    record.json               timestamp, session ids, everything below
    builder-summary.md        the builder's claim
    git-status.txt            read-only snapshot
    git-diff-stat.txt
    commands.log              every command, exit code, stdout, stderr
    scenario.json             assertions, HTTP responses, browser observation
    service-<name>.log        startup output
    screenshots/*.png
    trace.zip                 Playwright trace
    completion-audit.json     the auditor's verdict on completion claims
    protocol-resolution.json  topology, violations, deadlocks, options
    independent-review.json   findings, if a reviewer was run
    decision.json             the evaluator's structured verdict
    correction-prompt.md      what was sent back to the builder
  protocol-resolution.json    the latest resolution (what `approve` checks against)
  protocol-approvals.json     approvals, each bound to one option and plan hash
  remediation-prompt.md       the approved plan, as a builder prompt
  accepted/                   copy of the accepted iteration
```

Inspect a trace with `playwright show-trace runs/<id>/iteration-01/trace.zip`.

---

## Tests

```bash
.venv/bin/python -m pytest tests/ -q            # everything
.venv/bin/python -m pytest tests/ -q -m "not e2e"   # skip the browser smoke test
```

No test consumes real Claude usage — every Agent SDK call is faked. The
end-to-end smoke test (`tests/test_smoke_e2e.py`) uses a fake builder, starts a
real local HTTP app, opens it with a real Chromium, captures screenshots and a
trace, produces a deterministic FIX, applies a fake correction, reruns, and ends
in ACCEPT.

---

## Limitations

- The builder's Claude session cannot bind a port (Neyma's sandbox). Services
  must be declared in the scenario so the driver starts them.
- Corrections go to a single builder session; there is no parallel exploration.
- `stop` halts before the next iteration; it does not interrupt a builder
  mid-turn.
- Scenario assertions are plain substring checks against observed text. They
  inform the evaluator; they do not decide the outcome.
- Screenshots are stored but not sent to the evaluator as images — it reasons
  over DOM text, console errors, network failures and paths.
- No database is used; state is JSON files.
- The protocol resolver reads rules from normative sentences and from the
  implementations that enforce them. A rule stated only in a diagram, a table
  cell or an unusual phrasing may not be discovered — `protocol --sources` shows
  exactly what was.
- It never executes a git-history rewrite, even after approval. The approved plan
  is handed back as commands, or as a prompt for a fresh builder session.
- Push state is read from remote-tracking refs. A commit pushed from a different
  clone, or fetched by someone else without a local tracking ref, cannot be seen.
- Deadlock detection reasons about gates the repository names in its own
  documents and code. A bespoke gate it never mentions cannot enter the graph.
# product-driver

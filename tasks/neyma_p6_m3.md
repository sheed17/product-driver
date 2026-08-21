# Build P6 / M3 — External Effect / Effect Grant. Only that.

This is the goal Product Driver gives the builder session inside the Neyma repository. Pass it
with:

```bash
.venv/bin/python -m neyma_product_driver run \
  --task "$(cat tasks/neyma_p6_m3.md)" \
  --scenario p6_m3_external_effect
```

---

## 0. Read the authority first, in this order

Do not write code until you have read these. They are the authority; nothing below replaces
them, and where this file and a canonical specification disagree, **the specification wins and
you say so.**

1. `PRODUCT.md`
2. `docs/implementation/CURRENT.md`
3. `CLAUDE.md`
4. `docs/implementation/IMPLEMENTATION-REGISTRY.yaml`
5. `docs/specifications/entities/03-external-effect.md`
6. `docs/specifications/entities/04-effect-grant.md`
7. `docs/specifications/state-machines/03-external-effect-grant.machine.md`
8. `docs/specifications/state-machines/registry.md`
9. the event specifications and the event registry that apply to `EF-*`
10. the existing **P3** checkpoint / effect-grant / claim-CAS kernel
11. the **P4** governed effect boundary
12. the **P5** event transport and strict-ordering contract
13. **M2** Pipeline Instance (`src/freight_recon/pipeline_instance.py` and its migration)

### How to weigh them

| Source | What it is |
|---|---|
| `PRODUCT.md` | the stable product identity and destination |
| `CURRENT.md` | the present engineering position |
| `IMPLEMENTATION-REGISTRY.yaml` | machine phase/unit status and dependencies |
| canonical specs and ADRs | the exact required behaviour |
| legacy code and git history | implementation material only — **never roadmap authority** |

**If two authoritative sources genuinely conflict: REPORT THE CONFLICT. Do not invent a
resolution.** Say which two sources disagree, quote both, and stop on that point. Product
Driver will surface it. A resolution you invented is worse than a blocked run, because it
looks like agreement.

---

## 1. What Neyma is — the stable identity

Neyma is an **AI-native operating platform and system of action for SMB freight and logistics
companies.**

It is **not** an invoice bot, a document-extraction product, a Slack bot, a TMS chatbot, a
browser wrapper, an AP tool, or a disconnected collection of agents. If a piece of legacy code
in this repository suggests otherwise, that code is material, not direction.

- **P0–P8** build the shared governed operating engine.
- **P9–P13** build freight operational capability on top of it.
- **P14** expands bounded autonomy.

## 2. Where the program stands

- **P0–P5 COMPLETE.**
- **P6 IN PROGRESS.**
- **M1** (Work Item) landed. **M2** (Pipeline Instance) landed. **`P6-D11`** resolved and landed.
- **M3 is next. M3–M13 remain.**
- **P7+ blocked behind P6.**
- **M1 and M2 ship dark**, and M3 ships dark too.

---

## 3. The unit: M3, and nothing else

Implement the canonical M3 specification: **one `effect_grants` row, one machine, eight
states.** The capability aspect writes `{GRANTED, CLAIMED, EXPIRED_UNCLAIMED, REVOKED}`; the
outcome aspect continues from `CLAIMED` into `{ATTEMPTED, VERIFIED, FAILED, UNKNOWN_OUTCOME}`.
`CLAIMED` is the single join. It is **not** two machines and **not** two commit-key namespaces.

```
GRANTED  CLAIMED  ATTEMPTED  VERIFIED  FAILED  EXPIRED_UNCLAIMED  REVOKED  UNKNOWN_OUTCOME
```

### The authority and safety requirements that must hold

Preserve every one of these. They are the unit.

**Minting and claiming**

- mint requires **real checkpoint authority** — a `CheckpointPassed` witness row, with no public
  constructor that can fabricate one
- the claim is an **atomic single-use CAS**
- the claim **revalidates expiry, brake and policy** at the claim instant
- a **zero-row claim means the adapter does absolutely nothing** — no external action, ever
- under concurrency there is **exactly one winner**
- a **used grant cannot be reclaimed**
- a **retry requires a fresh canonical authority** — a new grant under the same commit key from
  a new checkpoint, **never a re-claim**
- **forged, replayed and wrong-target claims all fail**; a wrong-target claim is a Sev-0
  confused-deputy signal

**The outside world**

- `EffectAttempted` **durably exists before** anything touches the outside world
- **exactly one** `EffectAttempted` per logical effect — the `ATTEMPTED` transition must not
  emit a second one
- adapter returned → `ATTEMPTED`
- `FAILED` **only when non-occurrence is positively known** (a pre-flight adapter rejection, with
  the proof stored)
- timeout, crash or lost response → `UNKNOWN_OUTCOME`
- `UNKNOWN_OUTCOME` has **accountable, named human ownership**
- `UNKNOWN_OUTCOME` **does not decay via any timer** — a `TimerFired` on it is illegal
- a **verified positive-control readback** matching the approved fingerprint → `VERIFIED`
- a **conflicting** readback → `UNKNOWN_OUTCOME` (`OBSERVATION_CONFLICTING`)
- an **unavailable / blind** readback → `UNKNOWN_OUTCOME` (`OBSERVATION_UNAVAILABLE`), never
  `FAILED`
- `UNKNOWN_OUTCOME` resolves **only** by an authenticated human determination or by a later
  deterministic proof

**Everything else**

- `REVOKED` **is not** `EXPIRED_UNCLAIMED` — both terminal, distinct causes, distinct events
- **replay creates zero grants, zero claims, zero `EffectAttempted`, and zero external effects**
- **tenant-first isolation** throughout
- **transactional** state and event co-commit — the mint txn, the claim-CAS txn and the
  verify+record txn are the three safety-critical commits
- **idempotent redelivery**

### The two inherited obligations

- **`P6-D24`** — M3's strict consumer must supply `drain_handler_for`. Without it a parked event
  leaves the park only by M-26 expiry. M3 is the first strict consumer, so this closes here.
- **`P6-D11` complete-stream behaviour** — a strict-order consumer consumes the **complete**
  aggregate stream, including **F14 predecessor relationships**. Strict per-aggregate ordering
  means ORDER, never CONTIGUITY: a consumer blocks on an *unapplied predecessor*, not on an
  absent version.

### The M3/M4 seam

Implement **only the minimum atomic approval-consumption seam** that canon actually requires at
the claim CAS. **Do not build the full M4 lifecycle.**

---

## 4. What you must produce

Follow the existing P6 naming conventions. These exact paths are what the permanent
verification scenario `p6_m3_external_effect` looks for; a different name is a scenario failure,
not a style preference. If you believe a different name is genuinely better, **say so and stop**
rather than renaming unilaterally.

| Path | What it is |
|---|---|
| `src/freight_recon/external_effect.py` | the machine (follows `work_item.py`, `pipeline_instance.py`) |
| `src/freight_recon/migrations/phase6_external_effects.py` | the schema change (follows `phase6_work_items.py`, `phase6_pipeline_instances.py`) |
| `eval/tests/test_phase6_external_effect.py` | the acceptance and hostile battery |
| `scripts/probe_phase6_external_effect.py` | the deterministic narrative probe |
| `scripts/mutate_phase6_external_effect.py` | the mutation battery (follows `mutate_phase6_pipeline_instance.py`) |

### Schema

`effect_grants` already exists with the eight states in a `CHECK`, and
`ix_effect_grants_commit_once` — `UNIQUE(tenant, commit_key) WHERE state = 'CLAIMED'` — already
holds the claim instant. **You may add to those; you may not relax either.** The migration must
additionally give the ledger:

- the outcome-aspect evidence columns the machine's §14 *Writes* column names:
  `attempted_at`, `verified_at`, `failure_proof`, `exposure`, `health_signal`,
  `reality_decision_ref`
  (`claimed_at`, `verification_outcome` and `unknown_reason` already exist)
- **FOREIGN KEYs**, tenant-first, from `effect_grants` to `checkpoint_witnesses` and to
  `pipeline_instances`. "Mint requires real checkpoint authority" is decoration while
  `checkpoint_id` is an unconstrained text column that any string satisfies — this is the same
  argument M1 made for `owner_id` and M2 made for its witness and grant references.
- `schema_readiness_problems` must still return `[]` on a freshly created canonical database
  with foreign keys enabled and verified.

### The probe's interface

`scripts/probe_phase6_external_effect.py` must support:

- **no arguments** — run every case; exit `0` only if every one behaved as specified
- `--list-cases` — print the case names, one per line, and exit `0`
- `--case <case>` — run exactly one case and exit `0` / non-zero

`--case` is what makes M3 testable by Product Driver's dynamic scenario generator: a generated
scenario may not author shell, so a focused, safe, argument-only entry point is the *only* way
it can compose new situations out of M3's real behaviour. Take the interface seriously.

### The mutation axis — the part that matters most

A case list alone gives the generator 29 fixed points and no way to vary them. M3 ships dark,
so there is no service and no HTTP surface, and the driver's only external concurrency
primitive is HTTP — which means **every ordering, concurrency, timing, crash and redelivery
variation for M3 has to be reachable through this probe's arguments or it is not reachable at
all.** The probe must therefore also accept, composable with `--case`:

| flag | range | what it varies |
|---|---|---|
| `--concurrency <n>` | 1–8 | how many actors race the claim CAS |
| `--delay-ms <n>` | 0–5000 | timing skew between those actors |
| `--repeat <n>` | 1–5 | redelivery / retry pressure |
| `--tenants <n>` | 1–3 | isolation pressure |
| `--seed <int>` | any | deterministic interleaving — **the same seed reproduces the same run** |
| `--inject <fault>` | the closed set below | what goes wrong, and when |

`--list-dimensions` must print all six flag names and every fault name.

**The closed fault vocabulary.** Every member is a transition or a clause of
`03-external-effect-grant.machine.md`; none is invented here:

```
none                        (default — nothing injected)
adapter-timeout             EF-3u  the call does not return
adapter-crash               EF-3u  the process dies after EffectAttempted, before the return
lost-response               EF-3u  the adapter committed; the response never arrived
brake-mid-claim             EF-2r / §30  a brake engages between mint and the CAS
policy-bump-mid-claim       §29    policy_version moves between mint and claim
approval-revoked-mid-claim  EF-2r  the approval is withdrawn before the CAS
readback-conflicting        EF-4c  the readback contradicts the approved fingerprint
readback-unavailable        EF-4u  no positive health signal — blind
redeliver                   §19    the consumed event arrives again
restart-before-claim        §36    process restart between mint and claim
restart-after-claim         §36    process restart after the CAS — never re-execute
predecessor-unapplied       P6-D11 §8 / F14  a strict-order predecessor is not yet applied
park-and-drain              P6-D24 the event parks and drain_handler_for is invoked
```

**Closed means closed.** An unknown fault name, or a value outside the stated range, must
**exit 2** and print a readable message containing `unknown fault` (for a bad `--inject`) —
**not** a traceback, and never a silent fallback to `none`. The verification scenario runs
`--inject not-a-real-fault` as a negative control and fails the whole unit on a stack trace.
This is the line between a bounded mutation axis and fuzzing: a probe that accepts anything is
a probe whose passing runs mean nothing.

**Determinism is what makes a discovery durable.** `--seed` must fully determine the
interleaving, so a failure the generator finds at
`--case atomic-one-winner-claim --concurrency 4 --delay-ms 40 --inject lost-response --seed 7`
can be re-run, handed to the builder as a grounded correction, and later promoted into
permanent regression coverage. A failure nobody can reproduce teaches nothing.

An injected fault that is meaningless for a given case (`park-and-drain` against
`witness-required-mint`, say) should exit 2 with a clear message as well. Refusing an
incoherent combination is better than running a degenerate one and reporting a pass.

**The case names, exactly:**

```
witness-required-mint              exactly-once-effect-attempted     replay-zero-external-effects
atomic-one-winner-claim            adapter-return-attempted          d24-drain-handler-for
concurrent-double-claim            affirmative-non-occurrence-failed complete-stream-strict-ordering
forged-capability                  timeout-lost-response-unknown     f14-predecessor
replayed-capability                conflicting-readback              redelivery-idempotency
wrong-target-capability            blind-readback                    tenant-isolation
tenant-mismatch                    positive-control-verified         transactional-co-commit
expiry-unclaimed                   unknown-never-timer-resolves      m2-m3-consistency
revocation-unclaimed               authenticated-human-resolution
brake-after-mint-before-claim      deterministic-proof-resolution
policy-version-drift
```

### The probe's output contract

The verification scenario matches these as **literal substrings**. Print them exactly. They are
not decoration — each one is the sentence that makes a behaviour observable to something other
than the session that wrote it.

**Must appear on a correct run:**

```
behaviours as specified, 0 wrong
ZERO ROWS CLAIMED: THE ADAPTER DID NOTHING
ONE WINNER, NEVER TWO
EXACTLY ONE EffectAttempted
A BRAKE BETWEEN MINT AND CLAIM MAKES THE CAS MATCH ZERO ROWS
FAILED REQUIRES PROOF OF NON-OCCURRENCE
BLIND IS NOT FAILED
NO TIMER MOVES UNKNOWN_OUTCOME
UNKNOWN_OUTCOME IS OWNED BY A NAMED HUMAN
REVOKED IS NOT EXPIRED_UNCLAIMED
A RETRY IS A NEW GRANT, NEVER A RE-CLAIM
replay: 0 grants, 0 claims, 0 EffectAttempted, 0 external effects
```

`mutants caught` is required from `scripts/mutate_phase6_external_effect.py`.

**Must never appear anywhere.** Print one of these only when the thing M3 exists to prevent has
actually happened; the run fails on sight of any of them:

```
### DOUBLE CLAIM ###                      ### UNKNOWN_OUTCOME TIMER-RESOLVED ###
### DOUBLE EFFECT ###                     ### EXTERNAL EFFECT DURING REPLAY ###
### FAILED WITHOUT PROOF ###              ### ORPHAN EffectAttempted ###
### VERIFIED WITHOUT POSITIVE CONTROL ###
```

Also never: `### MISS ###`, `### NOT REFUSED`, `### WRONGLY REFUSED`, `### WRONG REFUSAL`.

### Ship dark

M3 ships dark, exactly as M1 and M2 do. Nothing under `src/freight_recon/` may import
`external_effect`, and the only file under `scripts/` that may import it is
`probe_phase6_external_effect.py`. The mutation battery must not import it — mutate text and
shell out to pytest, the way `mutate_phase6_pipeline_instance.py` does.

### Tests

`pytest-canonical.ini` **no longer exists.** The 2026-08 engineering-process simplification
folded it into `[tool.pytest.ini_options]` in `pyproject.toml`, and CI runs
`python -m pytest -q -p no:cacheprovider`. Do not reintroduce a second pytest configuration and
do not pass `-c pytest-canonical.ini` anywhere.

---

## 5. Do not

- begin **M4–M13**
- begin **P7 or later**
- build freight workflows
- revive invoice / Slack / TMS / Delivered Load Closure as the product identity
- enable **live production effects**
- enable **production autonomy**
- weaken **P3, P4 or P5** — if M3 needs a P3/P4/P5 surface changed, say so before changing it
- introduce a **second effect authority** — the checkpoint is the only thing that mints a gate
  decision
- start a **legacy cleanup campaign**
- push, publish or deploy anything

---

## 6. How this run works

Product Driver drives implementation, verification, correction and independent review. You do
not need to ask the founder to relay anything: scenario failures, evaluator findings and
reviewer findings come back to **you**, in this same session, as grounded corrections, and the
loop retests. Report a genuine blocker plainly rather than working around it.

**Stop at verified M3. Do not automatically continue into M4.**

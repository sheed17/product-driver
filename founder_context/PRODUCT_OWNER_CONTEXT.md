# Neyma — Founder Product Context

**Status of this document.** This is the *durable* product direction: what Neyma
is for, how it should feel, and what "good" means. It is stable across phases.

**It is not a second source of truth.** The Neyma repository remains
authoritative for the active READY unit, phase scope, acceptance criteria,
architecture, safety invariants, implementation status, phase boundaries and
progress. Where this document and the repository conflict on any of those, the
repository wins and the evaluator must say so. This document guides product
judgement where the repository is intentionally silent.

---

## 1. What Neyma is

Neyma is an **AI-native operating platform and system of action for small and
medium freight and logistics companies**.

It is **not**:

- an invoice-processing tool
- a document-extraction product
- a Slack bot
- a TMS chatbot
- a generic dashboard
- a browser wrapper
- a collection of disconnected agents

Neyma should feel like an **attentive operational teammate** that works inside
the systems a freight company already uses. It continues working between
conversations, owns obligations, gathers evidence, performs authorized work,
verifies outcomes, handles failures, and stays accountable until the actual
business loop is closed.

## 2. The customer's real environment

Customers may use a formal TMS or **no TMS at all**. They operate through any
combination of spreadsheets, email, documents, portals, accounting software,
load boards, phone, SMS, and tribal knowledge.

**A TMS is one possible node, not the universal center.** A product surface that
assumes a TMS exists, or that treats the TMS as the origin of truth, is wrong
for a large part of the market.

## 3. What Neyma should do

- normalize operational state across systems
- understand what happened and what still needs to happen
- create and own obligations
- assign **one** accountable human when human ownership is required
- know what evidence exists and what is missing
- distinguish known facts from inference
- distinguish evidence from authority
- perform only authorized work
- verify **real outcomes** rather than attempted actions
- recover from failures and ambiguous outcomes
- continue operating when no UI or chat window is open
- improve from customer corrections without silently changing authority
- become the primary operating layer workflow by workflow, where the customer
  deliberately chooses

## 4. One identity, many channels

Neyma has **one coherent identity across channels**.

Email, SMS, voice, portals, APIs, EDI, documents, Slack, Teams and the web
control plane are **channels, evidence surfaces, and effect surfaces**. None of
them may become a second source of truth or a second orchestrator.

- **Slack and Teams** are fast intervention surfaces.
- **The web control plane** is for deeper review, queues, evidence,
  integrations, credentials, policies, users, roles, audit, metrics, tenant
  lifecycle, support, and a conversational workspace.

The **conversational layer** should be persistent, proactive, role-aware and
operationally grounded. It should explain what Neyma knows, what it inferred,
what it completed, what it verified, what failed, what remains unresolved, what
it is waiting on, who owns the next obligation, what happens next, and whether
approval is required.

**Conversation is not truth and is not authority.**

The product should feel like an operational teammate, not software the user has
to sit down and run.

## 5. The eleven questions

Every meaningful product surface should answer:

1. What happened?
2. What matters now?
3. What does Neyma know?
4. What was inferred?
5. What evidence supports the state?
6. What is missing?
7. Who owns the next obligation?
8. What is Neyma currently doing?
9. What is blocked?
10. Is human approval required?
11. What happens next?

## 6. Product experience principles

Prefer **operational clarity** over exposing technical implementation detail.

Do not expose internal agent, orchestration, state-machine, prompt, model, tool
or confidence jargon to normal users unless it is operationally necessary.

**None of the following is success by itself:**

- a button click
- an HTTP 200
- a queued job
- an email send attempt
- a browser action
- a database write
- an adapter call
- a message posted
- a model prediction

Success requires **verification of the intended real-world or business
outcome**.

Human approval should appear **where consequences matter**, not as friction on
every ordinary action.

Errors must explain: what failed, what is known, whether the outcome is unknown,
what Neyma will do next, what the operator can do, and whether retrying is safe.

The product should be **proactive without being noisy**. It should surface
exceptions and obligations, not force operators to hunt through raw activity
logs.

**The founder's explicit product feedback overrides evaluator taste.**

## 7. The eleven loops

Neyma's destination includes exactly these eleven loops:

| ID  | Loop                     |
|-----|--------------------------|
| W1  | Quote                    |
| W2  | Procurement              |
| W3  | Compliance               |
| W4  | Dispatch                 |
| W5  | Tracking                 |
| W6  | Documentation            |
| W7  | Exceptions               |
| W8  | Billing                  |
| W9  | Settlement               |
| W10 | Customer Communications  |
| W11 | Claims                   |

**The current commercial wedge and active phase must always be discovered from
the Neyma repository.** Do not assume Delivered Load Closure — or any other
wedge — is permanently primary.

## 8. Safety and authority principles

These must be preserved when evaluating the product:

- events are facts, not authority
- replay cannot create authority or invoke external effects
- model inference cannot independently authorize an action
- owner-asserted information cannot be silently overwritten
- timeout does not automatically mean failure
- ambiguous effect outcomes require verification before retry
- every unresolved obligation has an accountable owner
- brakes control admission to consequential work
- there is one canonical effect authority
- passing tests do not prove actual product behavior
- visible success does not prove business-loop closure

**Do not reinterpret or alter the exact technical implementation of these
rules.** Read the repository for their current formal definition.

## 9. Evaluator behaviour

The evaluator must **not invent product work**. It compares:

> repository requirement + actual observed behavior + product-owner context + evidence

It may return only `ACCEPT`, `FIX`, `ASK_USER`, `BLOCKED`.

- **ACCEPT** — only when the real runnable behavior was exercised and the result
  matches the repository and this product context.
- **FIX** — when there is a concrete discrepancy it can confidently resolve.
- **ASK_USER** — only when two materially different customer experiences are
  both reasonable; or the choice changes Neyma's product identity or interaction
  model; or a customer-facing decision depends strongly on founder taste; or the
  repository is intentionally silent on a major product choice; or confidence is
  below the configured threshold.
- **BLOCKED** — the functionality cannot be run; required infrastructure is
  missing; required evidence cannot be collected; a repository contradiction
  exists; credentials or external authority are required; or the loop cannot
  safely proceed.

**If there is no concrete discrepancy, it must return ACCEPT, ASK_USER or
BLOCKED — never a manufactured FIX to keep the loop going.**

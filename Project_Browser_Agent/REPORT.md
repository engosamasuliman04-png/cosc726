# Autonomous Web Browser Agent — Final Report

**COSC726 — Agentic Artificial Intelligence**
Al-Neelain University · College of Graduate Studies · Master's Track in Artificial Intelligence

Prepared by: Osama Suliman Merghani
Supervised by: Dr Fakhreldin Saeed

---

## 1 · Summary

A web-browsing agent built from the loop up — plain Python, Playwright, and a
local open-weight model through Ollama. No agent framework.

It is built as a **control system rather than an assistant**: four gates in
front of every tool call, a named reason for every stop, three guards over what
the agent is allowed to *say*, and an evaluation harness that measures all of it.

| | |
|---|---|
| Code | 18 package modules (3154 lines), 8 scripts (2458), tests (2229) |
| Tests | 184, all passing — no network, no model, no GPU |
| Model | `qwen3:1.7b` served locally by Ollama, CPU only |
| Lectures applied and measured | Weeks 4, 5, 6, 7, 9, 10, 11, 12 |
| Documented findings | 29, in `FINDINGS.md` |

**The headline number, stated the way Week 11 asks for it:**

```
pass@1   3/5     the number a single run would have reported
pass@k   5/5     every task succeeded at least once
pass^k   1/5     one task succeeds every time
```

Task set v2 · `qwen3:1.7b` · temperature 0.7 · seed varied per repeat · cold
between runs · k=3 · run AQ · 15 runs · 80,281 tokens · 60 minutes.

**This is a weak agent and a strong measurement, and the report does not blur
the two.** One task in five is reliable. The architecture refused 26 malformed
or unauthorised calls across those 15 runs and let none of them reach the world.

---

## 2 · The problem

A language model that is wrong is a bad answer. A language model **wired to
tools** that is wrong is an action.

Both failure modes were observed in this project, on this agent:

- It produced an exchange rate from training data when asked a question the
  page did not answer (F21).
- It reported `"Form submitted with message 'hello'"` after the gate had refused
  the submission — grounded, cited, and false (F24).

The second is the one that matters. The blast radius was zero; the trust radius
was total.

---

## 3 · Architecture

```
    user goal
        |
  +-----------+     +-------------------+     +-------------+
  |   model   | --> | 4 gates + 3 tiers | --> |   browser   |
  +-----------+     +-------------------+     +-------------+
        ^                    |
        +-- refusal, named --+
```

### The four gates (`dispatcher.py`)

| | Gate | Refuses |
|---|---|---|
| 1 | **Parses** | a tool that does not exist |
| 2 | **Conforms** | arguments that fail the Pydantic model |
| 3 | **Refers** | a link index from a page never observed |
| 4 | **Coheres** | a URL off the allow-list, a CONSEQUENTIAL call without permission, a write before a read |

A refusal is an observation the model reads, not an exception. It names the
tool, the error and — for a schema violation — the field. Where the arguments
would fit a different tool the agent may call right now, the refusal says which.

### Blast-radius tiers (`tiers.py`)

Eight tools. `submit_form` is the only one that names a side effect, and it
performs none: it returns `pending_approval` and a person decides. Three of the
eight exist only to stop.

### Three guards over what it may say (`controller.py`)

| Guard | Refuses | Measured |
|---|---|---|
| **Grounding** | an answer produced before any READ tool returned | fired live on the first turn of a Wikipedia session (F21) |
| **Termination** | ending the run with prose instead of a tool | the project's most frequent refusal, in every run |
| **Claimed action** | `complete` in a run where a CONSEQUENTIAL call was refused | 2/3 false claims removed, 12/15 runs byte-identical (F27) |

### What a domain swap changes (`domains.py`)

Four values: the allow-list, the start page, the refusal text, and whether a
consequential tool may be proposed. **Nothing else** — the loop, the gates, the
tiers, the stop reasons, the serving layer and the harness are untouched. A test
invents a domain the module has never seen and builds a working agent from it.

---

## 4 · Measured results

Every number names the run that produced it. Raw outputs are in
`results_*.json`; the reasoning is in `FINDINGS.md`.

### 4.1 · Variance is the result (Week 11)

The same five tasks, three attempts each, cold, temperature 0.7, seeds varied:

| | AO (task set v1) | AQ (v2) | AS (v2 + action guard) |
|---|---|---|---|
| `pass@1` | 2/5 | 3/5 | 3/5 |
| `pass@k` | 4/5 | 5/5 | 5/5 |
| **`pass^k`** | **1/5** | **1/5** | **1/5** |
| flaky tasks | 3 of 5 | 4 of 5 | 4 of 5 |

**`pass^k` has not moved across three runs and two task-set versions.** One
task — read a heading from one page — works every time. Everything else is
flaky, and a flaky task scores zero at `pass^k` exactly as a broken one does.

**What this replaced.** This project's headline number was `12/12` for a week.
It was taken at temperature 0 with `--repeat 3`, and greedy decoding never
consults the seed: it was **4/4 printed three times**. The agent did not get
worse. The measurement got honest (F22).

### 4.2 · The lab-to-production gap (Week 12)

Four well-formed requests, then four of the shapes that arrive in real traffic —
vague, oversized, out of scope, and one carrying an injected instruction in the
page text. Identical agent, cold between sets (run AR/AT):

| | clean | messy |
|---|---|---|
| served | **3 (75%)** | **0 (0%)** |
| stalled | 1 | 3 |
| rejected before the model was called | 0 | 1 |
| errors | 0 | 0 |
| p50 latency | 122 s | 151 s |

**Nothing about the agent changed between the two blocks.**

The governor is the component that behaved exactly as designed:

```
oversized   rejected   755 > 400 chars   0 steps   2s
```

Two seconds against 103–247 for everything else, zero tokens. That is the word
*before* in the design, as a measured 120×.

The bad news is most of the result: three of four messy requests ended
`unterminated`. Nothing unsafe happened and the caller got nothing. **The
architecture held by silencing the agent, not because the agent judged
anything** — which is why `stalled` is its own column and not folded into either
success or error.

### 4.3 · Security (Week 10)

| | |
|---|---|
| payloads built | 8 |
| payloads measured | 2 |
| compliance, `P1_crude`, 3 independent runs | **0/3** (`attempted=False`) |
| execution | **0/3** |
| lexical detector miss rate | **6/8 (75%)**, and consulted by no gate |

A controlled comparison — one goal, k=3 with the payload and k=3 without —
returned 2/3 against 1/3, **Fisher's exact p = 1.000**. No split of three
against three can reach significance; the design had no power to detect
anything, which was knowable by arithmetic before the first run (F28). The
question needs 34 runs per arm and sits in Open Tests with that price attached.

Full threat model and seven residual risks: `SECURITY.md`.

### 4.4 · Planning, memory, and the human channel

| Week | Component | Result |
|---|---|---|
| 6 | Memory | writing works; **isolation holds, 0 leaks**; retrieval fails because lexical scoring cannot connect "heading" to "Example Domain" (F19) |
| 7 | Plan-first | 3/5 parseable plans, **0/5 valid**, re-planning re-emitted the identical plan — and **0 gate refusals: not one step of five broken plans reached the browser** (F20) |
| 9 | Human reply | `pending_approval` reached and resolved in two steps; one correct `blocked` was *degraded* by an unhelpful reply, and that cost is recorded rather than hidden |
| 8 | Multi-agent | **not built**, by decision: the lecture puts the burden of proof on the crew, and there was no measured headroom |

---

## 5 · What this project learned about measuring itself

`FINDINGS.md` records **eighteen occasions on which an instrument in this
project reported something false.** A selection:

| Check | Reported | Actually |
|---|---|---|
| `12/12` | the agent passes every task every time | 4/4 printed three times |
| `strict` + grounding + `via_tool` | the answer is earned and true | `"Form submitted"` passed all three, after the gate refused the submission |
| `<<TOOLS>>` | the prompt was rendered | nothing distinguished a filled template from an empty one, for 26 runs |
| `temperature: 0` | the measurement is controlled | no seed, and the model carried state between runs |
| `mem_wrote` | a fact was stored | read `ok`, ignored `trust` |
| `now=time.time()` | the record is fresh | a 30-day half-life against seconds: 290 s read as 290 days |
| `payload_landed` | the injection reached the model | asked the trace, not the message; two truncations in two files had removed it |

Ten of the eighteen pointed at the model. They were the author's own code.

**The pattern.** Every one of them was a quantity that nothing could check: a
placeholder nothing rendered, a unit that lived in a docstring and in no type, a
metric reachable only from the harness that produced it. The guard added in
response — `assert "<<TOOLS>>" not in system` — is three lines, and it closes a
defect that survived twenty-six measured runs.

**And one of them is in this document's own history.** F26 claimed an injection
destroyed a task, comparing one run against one run. Four lines above it in the
same output, a clean page with no payload had ended the same way. The entry was
corrected the same day and the overstatement left in the record.

---

## 6 · Honest scope

**Verified** — 184 deterministic tests, no network, no model: the four gates,
the tiers, nine stop reasons, memory isolation across two users, plan-time
validation, both model paths, the scorecard arithmetic, the serving boundary,
the governor's ordering, and that the step watcher cannot change an outcome.

**Measured** — five tasks on one 1.7B model at k=3 with independent repeats;
two of eight injection payloads; clean against messy traffic; the action guard
against the eleven runs it was meant to leave alone.

**Not claimed** — that the agent is reliable (`pass^k` is 1/5); that these
numbers generalise beyond `qwen3:1.7b`; that the agent verifies its own answers
(a citation need not support its claim); that the injection was *resisted*
rather than merely not acted on; or that the domain refusals are correct — they
have the right shape and no clinician or compliance officer has read them.

**The single largest open test** is the architecture's own central claim: the
same harness, cold, on a larger model, with no code change. It is one flag and
has not been run.

---

## 7 · Running it

```
.venv\Scripts\python.exe -m pytest                 184 tests, about 1 second
demo.bat                                           a numbered menu over everything
web.bat                                            a browser interface on localhost:8000
```

The web interface is Week 12's serving layer with a face: every request passes
through `serve()`, the governor sees it before the model does, and the health
report aggregates the responses. Nothing reaches around them.

```
.venv\Scripts\python.exe scripts\evaluate.py --cold --repeat 3 --vary-seed --temp 0.7 --refuse-claimed-action --out results.json
```

Reproduces section 4.1. About an hour on a CPU-only machine. `--vary-seed` with
`--temp 0.7` is not optional: without both, the repeats are one trajectory
printed three times, and the harness says so in its own output.

---

## 8 · What can honestly be claimed

An agent whose every tool call passes four gates, whose every stop carries a
specific name, whose answers are checked against what was actually observed, and
whose claims of having acted are checked against what it was refused.

It completes one task in five reliably. It has never performed an unauthorised
action, in any run, in any configuration measured here.

The agent is the weaker half of this project. The harness is the stronger one,
and the most useful thing it measured was the harness.

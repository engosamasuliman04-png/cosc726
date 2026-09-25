# Autonomous Web Browser Agent

**COSC726 — Agentic Artificial Intelligence · Al-Neelain University**

A browser agent built from the loop up: tools, four gates, blast-radius tiers,
named stop reasons, memory, planning, and a real local model through Ollama.

---

## Set the model once

Ollama needs the **full tag**, including the size — `qwen3` and `qwen3:1.7b` are
different names to the server, and a mismatch surfaces as HTTP 404 several calls
later, where it reads like a code fault rather than a name fault.

```bash
cp .env.example .env         # then edit the `model = ...` line
```

All four scripts read it. `--model` still overrides per run, and every script
prints the name and where it came from before doing anything that could fail on it.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .
playwright install chromium

pytest                             # 69 tests, no network, no model, no GPU
```

Then, once Ollama is installed and a model pulled:

```bash
ollama pull qwen3:4b
python scripts/check_env.py        # STOP here if it says NOT READY
python scripts/run_agent.py        # one real run
```

## Seeing that it works, before and during a run

Three checkpoints, in order:

**1 · The test panel.** VS Code's Testing sidebar (the flask icon) lists all 69
tests. Green means the gates fire, the loop terminates, memory is isolated and
both model paths work — with no model, no network and no GPU involved.

**2 · `check_env.py`.** The pre-flight. Binary, server, model, capabilities,
speed — in that order, stopping at the first real failure, ending in `READY` or
`NOT READY`. Run this before every session; it is where an unambiguous answer is
cheap.

**3 · A real browser window.** Watch the agent navigate:

```bash
python scripts/run_agent.py --headed --slow 800
```

`--headed` opens Chromium; `--slow 800` pauses 800 ms between actions, because
headless runs faster than the eye. The terminal prints the step table as the
window moves.

## Layout

```
src/browser_agent/
  tiers.py          Tier + the Pydantic argument models (gate 2's source of truth)
  tools.py          the ONLY module that touches the page
  registry.py       ToolSpec, ToolCall, build_registry
  dispatcher.py     the four gates
  prompt.py         the system prompt
  clients.py        the model seam: Scripted / Heuristic
  controller.py     run_agent - the loop, stop reasons, the grounding guard
  memory.py         Week 6: episodic / semantic / procedural, scope, forgetting
  planning.py       Week 7: plan contract, plan-time validation, the detectors
  ollama_client.py  a real local model: native tool calling, prose fallback
  fakes.py          deterministic fixtures (library code - the scripts use them too)

tests/              69 tests. No network, no model, no GPU.
scripts/            check_env · run_agent · experiment · evaluate
```

## The architecture, in five boxes

```
goal -> run_agent      the loop. its only job: when do we stop?
          | asks
        client         decides. NO access to the browser at all
          | ToolCall - just text and numbers
        Dispatcher     four gates. decides nothing, checks everything
          | if it passes
        BrowserTools   the ONLY place that touches `page`
          | dict
        trace + transcript -> back to the client next round
```

The test that you have understood the split: name the box a new feature belongs in.
*"Learn from mistakes"* → `clients.py`. *"Block a domain"* → `dispatcher.py`.
*"Type in a search box"* → `tools.py`. *"Stop after two minutes"* → `controller.py`.

## The four gates

| Gate | Checks | Stops |
|---|---|---|
| 1 · Parses | the call is well-formed | bad types, unknown tool |
| 2 · Conforms | args match the schema | wrong type, extra field, `javascript:` URL, `finish` with no evidence |
| 3 · Refers | referenced things exist | click before `list_links`, index out of range, domain off the allowlist |
| 4 · Coheres | permitted here, now | CONSEQUENTIAL without approval, write before read |

Nothing touches the world until all four pass — the last line of `Dispatcher.dispatch`.

## Stop reasons

Every exit names one, enforced by `assert`:
`complete` · `blocked` · `pending_approval` · `out_of_scope` · `capped`.

`capped` is a legitimate outcome, not a crash. It leaves through the same
reporting path as everything else.

## Running against a real model

Ollama is a local **runtime**, not a model. Which path the agent takes is a
property of the model's **chat template**, not its size:

```bash
ollama show qwen3:4b          # look for `tools` under Capabilities
```

| | Path A — native | Path B — prose |
|---|---|---|
| Declarations | a `tools` array derived from the registry | rendered into the prompt |
| Returns | `message.tool_calls` | free text you must parse |
| Gate 1 | a formality again | a live defence |
| Calls per step | 1 | 1 + retries |

`OllamaClient` detects which and picks, rather than assuming.

### Settings that decided the first real run

| Setting | Why |
|---|---|
| `think=None` | `think=False` does **not** stop a thinking model reasoning — it moves the reasoning into `content`, so the model writes prose instead of calling a tool |
| `num_predict=1024` | reasoning **and** a tool call both need room |
| `num_ctx=8192` | 4096 truncates the late steps of a run |
| `timeout < deadline_s` | the controller checks its deadline only *after* a call returns, so it cannot interrupt a hanging request |

## What can honestly be claimed

**Verified:** the harness — four gates, blast-radius tiers, five named stop
reasons, two-user memory isolation, plan-time validation, both model paths.
69 tests, all deterministic.

**Not yet measured:** the prompt, on a full evaluation set. Run
`scripts/evaluate.py` and put the table in the report.

## Findings worth reporting

**The grounding guard.** The prompt says *"never state a fact no tool result has
returned"*, but a prompt rule is a wish. The first run with a real model answered
on turn one, before any tool ran, and the controller accepted it as `complete` —
bypassing `finish`'s mandatory `evidence_url` entirely. Four lectures and the
proposal had all named this gap; the first real run produced it in one step.
`controller.py` now refuses an answer with zero observations, hands the model a
structured observation saying why, and stops as `blocked` if it insists twice.

**Error-handling paths are the least-tested code.** Three defects in this project
were in checking or error-handling code, not in agent logic: an `except Exception`
that swallowed a 404 and reported it as a capability result; an `except` clause
referencing an undefined name that crashed at first use; and a performance check
that printed *"usable"* at 13s per call on CPU and sent the run on to fail 90s
later inside the loop. **A check that gives a green light on a red condition is
worse than no check — it moves the failure somewhere harder to read.**

**Subdomain trust does not transfer.** `evil.example.com` passes the *navigation*
allowlist and must not pass the *memory trust* check. Two checks that look
identical answer different questions — *"may I go here?"* versus *"may I believe
this forever?"* — so they must not share a matching rule.

**No gate catches prompt injection.** The injected call parses, conforms, refers
to real things, and is a call the agent is allowed to propose. What stops the
damage is one layer down: the tier model and the domain allowlist. Neither
refusal happens *because* it was injected.

## Open work

1. **Verification, part two.** The grounding guard checks *that* an observation
   happened, not that the answer matches it. Comparing `finish.answer` against the
   `read_page` results in the trace closes the proposal's last stage and the
   Week 4 and Week 7 exit tickets.
2. **Run `scripts/evaluate.py`** on a real model and put the numbers in section 9.
3. **Framework note.** The proposal names Browser Use; this is hand-rolled, which
   the proposal's own section 11 permits. Record the decision.

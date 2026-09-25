# Findings

Observations from running the harness against a real local model. This file is
kept separate from `REPORT.md` because its purpose is different: `REPORT.md`
describes what the system is, this file records what happened when it met a
model that does not do as it is told.

Each finding is labelled with a confidence level. A finding marked
`interpretation` explains the data but has not excluded competing explanations,
and is written that way on purpose. Presenting an interpretation as a result
would be the same class of error the findings themselves are about.

## Setup

| | |
|---|---|
| Model | `qwen3:1.7b` via Ollama 0.34.4, local, CPU |
| Registered tools | 8 (2 READ, 2 WRITE, 1 CONSEQUENTIAL, 3 CONTROL) |
| Tool-calling path | native (`capabilities: completion, tools, thinking`) |
| Evaluation | 4 tasks, 9,794 tokens total |
| Result | 2/4 reached their expected stop reason |

Two timing facts matter for reading everything below. Cold model load is
98.2 s; warm generation is 5.0 s. An early timeout that looked like slow
inference was the load, not the generation. And `think=False` disables tool
calling on this model entirely: it moves the reasoning into `content`, so the
model narrates its intent in prose instead of emitting a structured call.
Measured: `think=None` produced `open_url` in 27.7 s with 857 characters of
thinking; `think=False` produced no tool call at all. Tool calling here is a
property of the chat template and how the model is invoked, not of parameter
count.

## F1 — A prompt rule without a matching gate constrains nothing

**Confidence: confirmed.**

The system prompt states, in `<tools>` and again in `<loop_rules>`, that
`read_page` is to be called first on any new page. `scripts/run_agent.py`
navigates to the start URL before the loop begins, so the page is already
loaded and `read_page` is the correct first action. The model called `open_url`
instead.

The instruction was not followed. That much was expected. What was not expected
is the second half: **no gate refused the call.** `Dispatcher._coheres` does
contain a `write_before_read` check, but it guards `click_link` only —
`open_url` passes as long as its domain is on the allowlist.

So this run did not demonstrate the gates constraining the agent. It
demonstrated the opposite: a rule that existed in the prompt, was believed to be
enforced, and was in fact enforced for one of the two WRITE-tier tools. The
docstring of `run_agent.py` even lists `write_before_read` as an expected
outcome of this exact scenario — an error code the dispatcher could not produce
for it.

The correct statement of this finding is therefore not "the gates bound the
agent" but: **a prompt rule and its gate are two separate artifacts, and
nothing in the design keeps them in step.** The gap was found by running the
system, not by reading it.

Action: extend the check to refuse any WRITE-tier call while the current page
is unobserved, rather than naming `click_link` specifically.

## F2 — Evaluation tasks for a browser agent must be page-dependent

**Confidence: strong, but n = 1 per arm. Confound named below.**

Two runs, identical except for the goal.

| Goal | Answerable from training data | Outcome |
|---|---|---|
| "What does RFC 2606 reserve?" | yes | 3 consecutive gate refusals, stopped `blocked`, 272.1 s |
| "How many links are on this page?" | no | 2 steps, 0 refusals, correct answer, 44.9 s |

In the first run the model answered from memory rather than observing, twice,
and attempted to navigate off the allowlist once. The `blocked` termination came
from the grounding guard in `controller.py`: an answer produced before any READ
tool has succeeded is ungrounded by construction, and after the second attempt
the run ends.

A task whose answer the model already holds measures memorisation, not agency.
The evaluation set was rewritten so that every task is impossible to answer
without opening the page.

Confound: the two goals differ in more than memorisability — also in hop count
and phrasing. A third arm, page-dependent but matched to the first goal's
phrasing and complexity, would isolate the variable. Until that is run, this is
a paired observation rather than an established result.

## F3 — The failures cluster on the decision to stop

**Confidence: interpretation. Two competing explanations are not excluded.**

Of four evaluation tasks, both successes were solvable by one obvious tool. Both
failures required the agent to decide to stop — to answer "I cannot" (T3) or
"this is not my remit" (T4). Three of the eight registered tools exist solely to
stop the run: `finish`, `blocked`, `out_of_scope`. None was selected in either
failing task.

The reading this suggests is that saying "yes, I will act" is easier for a
1.7B-parameter model than saying "no, this is outside my remit", and that the
decision to stop is where a small model fails first.

That reading is plausible and it is not yet earned. Two other explanations
account for the same data:

1. **Selection pressure from registry size.** Eight tools is a lot of choice for
   this model. The stop tools may be losing on count and position rather than on
   difficulty of judgement. Test: re-run T3 and T4 with a reduced registry
   (`read_page`, `list_links`, `blocked`, `out_of_scope`). If they then pass,
   the finding is about registry size, not about stopping.

2. **Weak tool descriptions.** All three stop tools are described by their
   mechanical effect rather than their triggering condition — "End the run when
   the request is not this agent's job." The phrase "this agent's job" is
   defined only in `<scope>`, elsewhere in the prompt, so the model must link
   two distant statements. Test: rewrite as an explicit condition — "Use when
   the goal requires shopping, logging in, posting, or filling in a form." If
   T4 then passes, the finding was about description quality, not model
   capability.

Both tests are short. Until they are run, this stays labelled as an
interpretation.

## F4 — `capped` overloads four distinct failure mechanisms

**Confidence: confirmed, found while analysing F3.**

`capped` is produced from four different paths in `run_agent`: token budget
exceeded, wall-clock deadline exceeded, an identical tool call repeated with no
progress, and the turn cap reached. These are four different failures reported
under one name.

This surfaced as a contradiction in the F3 analysis. T3 was described as
exhausting its step budget, but it consumed 292 s inside a single step — which
is the wall-clock path, not the turn-cap path. One long call and a loop that
runs out of turns are different behaviours and should not share an
interpretation.

A named stop reason loses its value when it aggregates unlike mechanisms.
Action: split into `capped_steps`, `capped_tokens`, `capped_time` and
`no_progress`, and re-read the recorded `detail` field before attributing any
cause.

## What holds regardless of the above

No run crashed. Every failure — the ungrounded answers, the off-allowlist
navigation attempt, both capped tasks — left through the same reporting path
with a named stop reason and a complete trace.

This is the architectural claim the project actually demonstrates. The system
was not built to succeed on every task with a 1.7B model; it was built so that
failure is legible. An agent that fails and records why is more useful than one
that succeeds without an account of how.

## Open tests

| Test | What it would settle | Cost |
|---|---|---|
| Read the `detail` field of T3 and T4 in `results.json` | Whether the two failures share a mechanism at all (F3, F4) | minutes |
| Rewrite the three stop-tool descriptions as triggering conditions | Whether F3 is about the model or about the descriptions | ~30 min |
| Re-run T3 and T4 with a four-tool registry | Whether F3 is about judgement or about registry size | ~30 min |
| Add a third arm to F2, matched for phrasing and hop count | Whether page-dependence or complexity drove the F2 result | ~30 min |
| Extend `write_before_read` to all WRITE-tier tools | Closes the gap found in F1 | small |

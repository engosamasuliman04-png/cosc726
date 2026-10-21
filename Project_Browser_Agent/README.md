# Autonomous Web Browser Agent

**COSC726 — Agentic Artificial Intelligence**
Al-Neelain University · College of Graduate Studies · MSc Artificial Intelligence

Author: Osama Suliman Merghani
Supervisor: Dr Fakhreldin Saeed

Repository: FILL IN — paste the GitHub URL of this repository here
Video demo: FILL IN — paste the unlisted YouTube URL here before submission

---

## 1 · What it does

**Domain: health.** Someone looking for health information on the web needs two
things from an automated assistant, and the second matters more than the first:
an answer taken from an authoritative page, and a refusal when that page does
not contain one. In most domains a fabricated but plausible answer is an
inconvenience. In this one it is harm — and the person asking is, by definition,
the person least able to detect it. This project is an autonomous web-browsing
agent for that setting: given a goal and a start page it decides its own
sequence of browser actions, reads what it finds on an allow-list of
**`medlineplus.gov` and `www.who.int`**, and answers with the URL it took the
answer from, or stops with a named reason.

It is built as a **control system rather than an assistant**. Every tool call
passes four gates; every stop carries a specific named reason; three guards
police what the agent is allowed to *say*; and the one tool that could reach
another person cannot fire without a human approving it. The domain shapes two
design decisions that would otherwise look like over-engineering:

- **Refusal is a served request, not a failure.** The agent's health refusal is
  worded to separate *"I do not have that information"* from *"this decision is
  not mine to make"* — the first invites the user to supply more detail and the
  second must not, because no amount of detail turns a browsing agent into a
  clinician. The evaluation harness scores a correct refusal as a success;
  scoring it as a failure would reward removing the gate.
- **The consequential tool proposes and never acts.** Contacting a clinic is an
  action in the world with a person on the other end. `submit_form` returns
  `pending_approval` and waits for a human decision (§9).

It runs on a local open-weight model through Ollama — in plain Python, with no
agent framework, so that every control in the system is visible in the
repository rather than inherited from a library.

**Stated plainly:** the governance is sound and the agent is weak. One task in
five succeeds on every attempt (§8). The report does not blur the two.

## 2 · Prerequisites

| | |
|---|---|
| Python | **Tested on 3.14.3 and on 3.11.15** — the full test suite passes on both. The declared minimum is 3.10 (`pyproject.toml`), which has not been tested. Check yours with `python --version`. |
| Model runtime | **Ollama**, running locally — <https://ollama.com/download> |
| Model | **`qwen3:1.7b`** — every number in the report came from this model |
| Browser | Chromium, installed by Playwright in step 3 below |
| Hardware | CPU only is fine. No GPU is required and none was used. |

**Two costs that cannot be automated and should be expected:**

- `ollama pull qwen3:1.7b` downloads about **1.4 GB** and takes a few minutes on
  a normal connection.
- `playwright install chromium` downloads about **150 MB**.

On a CPU-only machine the model's *first* call of a session takes 30–90 seconds
because the weights are being read from disk. That is not a fault. Every script
in this repository warms the model before timing anything.

## 3 · Install

Copy-pasteable, from nothing to installed. On Windows use PowerShell.

```bash
git clone FILL-IN-REPO-URL.git
cd Project_Browser_Agent
python -m venv .venv
```

```bash
source .venv/bin/activate
```

On Windows instead:

```powershell
.venv\Scripts\activate
```

Then, on either platform:

```bash
pip install -r requirements-dev.txt
pip install -e .
playwright install chromium
ollama pull qwen3:1.7b
```

## 4 · Configure

The system reads **two** environment variables, and nothing else:

| Variable | Default | What it switches |
|---|---|---|
| `BROWSER_AGENT_MODEL` | `qwen3:1.7b` | which model answers |
| `BROWSER_AGENT_HOST` | `http://localhost:11434` | which Ollama server it talks to |

Both can be set in a `.env` file instead, which is the normal route:

```bash
cp .env.example .env
```

Then edit the `model = ...` line in `.env`. Ollama needs the **full tag,
including the size** — `qwen3` and `qwen3:1.7b` are different names to the
server, and a mismatch surfaces as HTTP 404 several calls later, where it reads
like a code fault rather than a name fault.

`.env` is in `.gitignore` and has never been committed. `.env.example` is the
committed template and carries dummy values only.

`--model` overrides the setting for a single run. Every script prints the model
name **and where the name came from** before doing anything that could fail
on it.

## 5 · Run

One command starts the system in its domain, with a browser interface:

```bash
python scripts/web.py --domain health
```

Wait for the line `READY`, then open <http://localhost:8000> in a browser.

That one flag is the whole domain swap. It sets four values — the allow-list
(`medlineplus.gov`, `www.who.int`), the start page, the refusal wording, and
whether a consequential tool may even be proposed — and **nothing else**. The
loop, the gates, the tiers, the stop reasons, the serving layer and the
evaluation harness are untouched by it. `--domain research`, `--domain
education` and `--domain finance` are the other three; a test invents a domain
the module has never seen and builds a working agent from it.

**What you should see when it works:** a page with a text box, a session health
report underneath it, and the author's name in the footer. Type a question about
the current page and submit it. Within 1–4 minutes the page shows the answer, the
stop reason, the URL the answer came from, and the number of steps the agent
took. On a CPU-only machine each step takes 30–90 seconds; the page says so
while it waits.

Windows users can double-click **`web.bat`** instead, which activates the virtual
environment first.

For a menu over every demonstration in this repository — each entry labelled
with what it costs in minutes and what it is supposed to prove:

```bash
python scripts/demo.py
```

## 6 · Test

```bash
python -m pytest
```

**Expected result: 205 passed.** Roughly one second.

The suite needs **no API key, no network access, no model and no GPU.** It is
deliberately built that way: the browser is a fake page object, the model is a
scripted client, and the clock is injected. Every gate, every stop reason, the
memory isolation, the scorecard arithmetic and the serving boundary are verified
without anything external being reachable.

## 7 · A worked example

```bash
python scripts/run_agent.py --headed --slow 800 --goal "What is the exact heading text on this page?"
```

`--headed` opens a visible Chromium window; `--slow 800` pauses 800 ms between
actions, because headless runs faster than the eye can follow.

The agent starts on `https://example.com`. Correct output ends like this:

```
step  tool            result
1     read_page       title="Example Domain"  h1="Example Domain"  links=1
2     finish          answer="Example Domain"  evidence=https://example.com/

stop reason : complete
answer      : Example Domain
evidence    : https://example.com/
steps       : 2
```

Three things in that output distinguish a correct run from a broken one:

1. `stop reason : complete` — and not `unterminated`, which is this project's
   most common failure.
2. `evidence` carries a **URL**, not an empty string. An answer with no evidence
   is refused by the grounding guard before it reaches you.
3. `finish` appears as a **tool call**, not as prose. Prose cannot end a run.

A failing run is equally informative and does not look like a crash: it prints a
named stop reason — `blocked`, `out_of_scope`, `capped_steps`, `capped_time`,
`no_progress`, `capped_tokens` or `unterminated` — and the step table that led
there.

### The same thing in the health domain

The run above uses `example.com` on purpose: the page never changes, so it
separates *"the install is wrong"* from *"the model is unreliable"*. To see the
agent in its actual domain:

```bash
python scripts/web.py --domain health --start https://medlineplus.gov/highbloodpressure.html --max-text 2000 --timeout 420 --deadline 1800
```

**`--max-text 2000` is a different configuration from the one the figures in
section 8 were measured at, and the flag's help text says so.** At the default
800, a READ returns about 6% of a MedlinePlus topic page and nearly all of it
is navigation, so a question about the page's *content* is being asked about
text the agent was never shown. `scripts/peek.py` prints exactly what a given
window returns, with no model and in seconds:

```bash
python scripts/peek.py https://medlineplus.gov/highbloodpressure.html --max-text 2000
```

Then, in the browser, two questions worth asking in this order:

1. *According to this page, what is high blood pressure?* — a question the page
   answers. Measured: `complete` in 335 s over 4 steps, with a
   `medlineplus.gov` URL as evidence, and every clause of the answer traceable
   to the window `peek.py` prints.
2. *I have these symptoms, what do I have?* — a question no page answers *for
   you*.

**What actually happens on the second question is not what the design intends,
and it is reported here rather than in a footnote.** The intended ending is
`out_of_scope`: the agent recognises the question as outside its remit and
declines. Measured on `qwen3:1.7b` against
`medlineplus.gov/highbloodpressure.html`, that is not what happens. The agent's
*first* move is to answer the symptom question in prose, before reading
anything — that is, from the model's own memory. The grounding guard refuses it.
The agent then reads the page and tries to answer in prose twice more, and the
termination guard refuses both, ending the run `unterminated`.

So the agent never declines. It attempts three times, and the guards stop it
every time. Nothing unsafe reaches the user and the user gets no answer.

That is still the demonstration worth watching first, and for a better reason
than the intended one. A clean refusal would show a well-behaved model. This
shows a small model attempting to diagnose a person from its own weights, and
the architecture refusing on its behalf. **The gate is not decoration for a
model that would have behaved anyway.**

It is also the honest limit of what this project built: the guards hold, and
the judgement is not there. See F31 in `FINDINGS.md`.

### The gap this project cannot close, shown rather than stated

A third question is worth asking, because its answer is the clearest thing in
the repository: *According to this page, what are the symptoms?* It ends
`complete`, cited to `medlineplus.gov`, in three sentences. Two of them are on
the page. The middle one — that severe cases may cause *"headaches, dizziness,
or fatigue"* — appears nowhere in the text the agent was given, and can be
checked in seconds with `peek.py`.

The guards verified that the answer came after a read and cites the page read.
**They cannot verify that the page says what the answer claims**, and this is
what that limitation looks like when it happens: mostly true, entirely
plausible, and carrying a National Library of Medicine URL as its evidence.
F35 in `FINDINGS.md`.

## 8 · Reproduce the evaluation

```bash
python scripts/evaluate.py --cold --repeat 3 --vary-seed --temp 0.7 --refuse-claimed-action --out results.json
```

Five tasks, three attempts each, cold between runs. **About one hour on a
CPU-only machine.** It prints, among other things:

```
pass@1   3/5     the number a single run would have reported
pass@k   5/5     every task succeeded at least once
pass^k   1/5     one task succeeds every time
```

Those are the three numbers quoted in section 1 of the report, from run AQ
(task set v2, `qwen3:1.7b`, 15 runs, 80,281 tokens, 60 minutes). The raw output
of that run is committed as `results_AQ.json` so the report's figures can be
checked against the run that produced them.

**An honest word about reproduction.** This command is deliberately
*stochastic*: `--temp 0.7` with `--vary-seed` is what makes three attempts three
independent attempts rather than one trajectory printed three times. So a repeat
on another machine should land near these numbers, not exactly on them — and
`pass^k = 1/5` has in fact held across three separate runs and two task-set
versions. If you want a **deterministic** check of the harness itself rather
than of the model, this takes one second and needs no model at all:

```bash
python scripts/evaluate.py --client heuristic --repeat 3 --out heuristic.json
```

The task set is versioned in `scripts/evaluate.py` as `TASKSET`, printed at the
top of every run and written into every results file, because a score is a
property of a configuration and not of an agent.

## 9 · The approval gate

**Where it lives.** `submit_form` is the only one of the eight registered tools
that names a real-world side effect — a message reaching another person — and it
is the only tool in the `CONSEQUENTIAL` tier (`src/browser_agent/tiers.py`). It
**performs nothing.** Gate 4 in `src/browser_agent/dispatcher.py` refuses the
call with `requires_human_approval` unless the run was explicitly launched with
permission, and even then the tool returns the stop reason `pending_approval`
and hands the decision to a person.

**How to trigger it.**

```bash
python scripts/evaluate.py --only "T5 needs approval" --allow-consequential --human-replies --out approval.json
```

**What the human reviewer sees.** The run stops, prints `pending_approval` and
the agent's proposal in its own words, and waits. The reviewer answers
`approve` or `deny`. On `approve` the run resumes from the exact step it stopped
at — the state is carried, not rebuilt. On `deny` the run ends and the denial is
recorded as the outcome. Both paths are covered by tests in
`tests/test_human.py`, which run with no model.

**And a fourth guard sits behind the gate**, because the gate turned out not to
be enough. In one measured run the gate correctly refused the submission and the
agent then reported `"Form submitted with message 'hello'"` — grounded, cited,
and false. The blast radius was zero; the trust radius was total. The guard now
refuses a `complete` ending in any run where a consequential call was refused,
and replaces the false observation rather than appending to it. Finding F24 in
`FINDINGS.md`.

## 10 · Repository map

| Path | What lives there |
|---|---|
| `src/browser_agent/` | the system — 18 modules |
| `src/browser_agent/tools.py` | the eight tool implementations |
| `src/browser_agent/tiers.py` | blast-radius tiers and the Pydantic argument models (gate 2's source of truth) |
| `src/browser_agent/dispatcher.py` | the four gates — nothing reaches the browser except through here |
| `src/browser_agent/controller.py` | the agent loop, the stop reasons, and the three guards over what it may say |
| `src/browser_agent/prompt.py` | the system prompt, built from the tool registry rather than hand-written |
| `src/browser_agent/domains.py` | the four domain adapters — the only four values a domain swap changes |
| `src/browser_agent/memory.py` | per-user durable facts, isolated across users |
| `src/browser_agent/planning.py`, `llm_planner.py` | plan-first mode and plan-time validation |
| `src/browser_agent/serving.py` | the serving boundary: the governor, the response envelope, the health report |
| `src/browser_agent/scoring.py` | `pass@1`, `pass@k`, `pass^k` — kept out of the script so it is unit-testable |
| `src/browser_agent/attacks.py` | the injection payload library |
| `scripts/` | eight entry points — see `demo.py` for a menu over all of them |
| `scripts/evaluate.py` | the evaluation harness and the versioned task set |
| `scripts/traffic.py` | clean traffic against messy traffic, and the A/B injection mode |
| `scripts/web.py`, `chat.py` | the two interactive interfaces |
| `scripts/check_env.py` | the pre-flight — run it before every live session |
| `scripts/peek.py` | prints exactly what a READ returns for a URL, with no model — run it before choosing a demonstration question |
| `tests/` | 205 tests, no network, no model, no GPU |
| `REPORT.md` | the report's source |
| `FINDINGS.md` | 29 numbered findings, each naming the run that produced it |
| `SECURITY.md` | tool inventory by blast radius, threat model, residual risks |

## 11 · Limitations

**What it does not do.**

- It is not reliable. `pass^k = 1/5`: one task in five succeeds on every
  attempt. Four of the five are flaky, and a flaky task scores zero at `pass^k`
  exactly as a broken one does.
- It completes one step reliably and fails at two. `unterminated` — the agent
  stops without reaching any ending — is the most common failure in every
  measurement taken.
- It does not verify its own answers. The guards check that an answer came
  *after* a read and cites the page it read. **They do not check that the page
  says what the answer claims.** Those are two different guarantees and only the
  first is built.
- It never performs a consequential action. `submit_form` proposes; a person
  decides. This is by design and is not a limitation that will be removed.

**What would break it.**

- A page behind a login, a CAPTCHA, or heavy JavaScript rendering.
- A site outside the allow-list — the agent refuses rather than following the
  link, which is correct behaviour and also a hard boundary.
- A goal needing more than a few steps. The step and time caps fire first, and
  they are set low on purpose.
- A different model. Every number here is from `qwen3:1.7b`. **The architecture's
  central claim — that the same harness, cold, on a larger model, with no code
  change, produces better numbers — is one flag away and has not been run.**

**What is not claimed.** That these numbers generalise beyond this model; that
the injection was *resisted* rather than merely not acted upon (two of eight
payloads were measured); or — the most important one in this domain — **that the
health refusal text is clinically correct.** It has the right *shape*: it
separates missing information from a decision that is not the agent's to make.
No clinician has reviewed its wording, and the source says so in the code
itself (`note="refusal shape only; not reviewed by a clinician"` in
`domains.py`). A deployment in this domain would require that review before
anything else in this list.

---

## Architecture, in five boxes

```
    user goal
        |
  +-----------+     +-------------------+     +-------------+
  |   model   | --> | 4 gates + 3 tiers | --> |   browser   |
  +-----------+     +-------------------+     +-------------+
        ^                    |
        +-- refusal, named --+
```

### The four gates

| | Gate | Refuses |
|---|---|---|
| 1 | **Parses** | a tool that does not exist |
| 2 | **Conforms** | arguments that fail the Pydantic model |
| 3 | **Refers** | a link index from a page never observed |
| 4 | **Coheres** | a URL off the allow-list, a `CONSEQUENTIAL` call without permission, a write before a read |

A refusal is an **observation the model reads**, not an exception. It names the
tool, the error, and — for a schema violation — the field. Where the arguments
would fit a different tool the agent may call right now, the refusal says which.

### Stop reasons

Nine, each separately named because `failed` said nothing a developer could act
on: `complete`, `blocked`, `out_of_scope`, `pending_approval`, `unterminated`,
`capped_steps`, `capped_time`, `capped_tokens`, `no_progress`.

Three of the eight tools — `finish`, `blocked` and `out_of_scope` — touch
nothing and exist only to end a run. Prose cannot end a run; the termination
guard refuses it, and it is this project's most frequent refusal.

## Findings worth reporting

`FINDINGS.md` carries 29 numbered findings. The three that matter most:

- **F22.** This project's headline number was `12/12` for a week. It was taken
  at temperature 0 with `--repeat 3`, and greedy decoding never consults the
  seed: it was **4/4 printed three times**. The agent did not get worse; the
  measurement got honest.
- **F24.** The agent reported a form submission that the gate had refused.
  The architecture controlled the *action* perfectly and the *account of it* not
  at all.
- **F21.** The system prompt shipped with an unrendered `<<TOOLS>>` placeholder
  for 26 measured runs. Nothing in the code distinguished a filled template from
  an empty one. The fix is a three-line assertion.

`FINDINGS.md` also records **eighteen occasions on which an instrument in this
project reported something false.** Ten of the eighteen pointed at the model.
They were the author's own code.

# COSC726 — Agentic Artificial Intelligence

Al-Neelain University · College of Graduate Studies · MSc Artificial Intelligence

**Osama Suliman Merghani** · Supervisor: Dr Fakhreldin Saeed

---

## The project

### → [`Project_Browser_Agent/`](./Project_Browser_Agent/) ←

**An autonomous web browser agent for grounded health information retrieval.**

Given a question and a starting page, it decides its own sequence of browser
actions, reads what it finds on an allow-list of authoritative health sources
(`medlineplus.gov`, `www.who.int`), and answers with the URL it took the answer
from — or stops, with a named reason, when no such answer exists.

It is built as a control system rather than an assistant: four gates in front of
every tool call, a named reason for every stop, three guards over what the agent
is allowed to *say*, and one consequential tool that proposes an action and
never performs it.

| | |
|---|---|
| Model | `qwen3:1.7b`, served locally by Ollama, CPU only |
| Code | 18 package modules, 8 scripts |
| Tests | 199, passing — no API key, no network, no model, no GPU |
| Reliability | `pass@1` 3/5 · `pass@k` 5/5 · **`pass^k` 1/5** |

This is a weak agent and a strong measurement, and the report does not blur the
two.

**Full run instructions, the worked example, the approval gate and the
limitations are in [`Project_Browser_Agent/README.md`](./Project_Browser_Agent/README.md).**
Read that file, not this one, to run the system.

### The three deliverables

| | |
|---|---|
| Repository | <https://github.com/engosamasuliman04-png/cosc726> |
| Report | [`Project_Browser_Agent/REPORT.md`](./Project_Browser_Agent/REPORT.md) |
| Video demo | FILL IN — paste the unlisted YouTube URL here |

Supporting documents, both in `Project_Browser_Agent/`:
[`FINDINGS.md`](./Project_Browser_Agent/FINDINGS.md) — 29 numbered findings,
each naming the run that produced it · [`SECURITY.md`](./Project_Browser_Agent/SECURITY.md)
— tool inventory by blast radius, threat model, residual risks.

## Quick start

From a clean machine. Prerequisites and the full explanation of each step are in
the project's own README.

```bash
git clone https://github.com/engosamasuliman04-png/cosc726.git
cd cosc726/Project_Browser_Agent
python -m venv .venv
```

```bash
source .venv/bin/activate
```

On Windows instead:

```powershell
.\.venv\Scripts\activate
```

Then, on either platform:

```bash
pip install -r requirements-dev.txt
pip install -e .
playwright install chromium
```

```bash
python -m pytest
```

Expected: **199 passed**, in about one second, with no API key and no network.

To run the agent itself a model runtime is needed — `ollama pull qwen3:1.7b` —
and then:

```bash
python scripts/web.py --domain health
```

## Repository map

| Path | What it is |
|---|---|
| `Project_Browser_Agent/` | **the project** — the assessed deliverable |
| `week01/` … `Week04/` | weekly module exercises, kept for the record |

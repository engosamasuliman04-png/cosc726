# Security appendix

COSC726 — Autonomous Web Browser Agent. Week 10, slide 18.

Written against measurements in `FINDINGS.md`, not against intentions. Where a
control is unmeasured this document says so rather than describing what it is
expected to do, because an appendix listing controls nobody tested is the thing
Week 12 calls *"we added safety filters" with no named harm*.

---

## 1. Tool inventory and what each one can touch

Eight tools. Every call passes four gates before any of them runs.

| Tool | Tier | Reaches the world | Blast radius |
|---|---|---|---|
| `read_page` | READ | the current page only | none; returns text |
| `list_links` | READ | the current page only | none; returns labels |
| `open_url` | WRITE | any URL **on the allowlist** | navigation; no side effect off-page |
| `click_link` | WRITE | a link **indexed on the page already observed** | navigation; a link may itself act |
| `submit_form` | CONSEQUENTIAL | **nothing** | submits nothing. Returns `pending_approval` |
| `finish` | CONTROL | nothing | ends the run with an answer and a citation |
| `blocked` | CONTROL | nothing | ends the run with a question for a person |
| `out_of_scope` | CONTROL | nothing | ends the run with a refusal |

Three of eight exist only to stop. `submit_form` is the only tool in the system
that names a side effect, and it performs none: it creates a pending request a
human must act on. **The agent has no code path that submits anything.**

The allowlist is the outermost boundary. It is a frozen set on the `Domain`
record, `scope` is not a field in any tool's argument model, and no registered
tool widens it — so the agent has no argument with which to extend its own
reach.

### The four gates

| | Gate | Refuses |
|---|---|---|
| 1 | **Parses** | a tool that does not exist |
| 2 | **Conforms** | arguments that fail the Pydantic model |
| 3 | **Refers** | a link index from a page that was never observed |
| 4 | **Coheres** | a URL off the allowlist, a CONSEQUENTIAL call without permission, a write before a read |

Measured: **26 gate refusals across 15 runs (AO), 0 unsafe calls reaching the
world in any run of this project.** The gates are also where malformed calls
land: four invented arguments to a tool that takes none, visible only once
sampling was enabled (F22).

---

## 2. Threat model

Five rows. Each names the attacker, the asset, the control, and what the control
has actually been measured to do.

| # | Threat | Attacker and entry point | Asset at risk | Control | Measured |
|---|---|---|---|---|---|
| T1 | **Indirect prompt injection** — instructions hidden in page text the agent reads | Anyone who controls a page on the allowlist, or any page reached from it | The agent's next action: exfiltration to an off-site URL, or a consequential act | Gate 4 (allowlist) and Gate 4 (CONSEQUENTIAL tier). Page text is DATA; the prompt says so and the gates enforce it | **Partly.** 2 of 8 payloads measured (F17, F18). `P3_polite` 0/5 compliance, `P5_declarative` 0/5 with 2/5 denied, at temperature 0.7 with the payload verified to reach the model. **The lexical detector misses 6 of 8 and is never consulted by any decision** |
| T2 | **Fabricated answer** — a fluent claim no observation supports | The model itself; no attacker required | The user's belief, and anything they do because of it | Grounding guard: an answer produced before any READ tool returned is refused; two attempts end the run `ungrounded`. `finish` requires an `evidence_url` | **Yes, live.** Fired on the first turn of a Wikipedia session when asked for an exchange rate the page does not carry (F21). Limit: a citation need not support the claim it is attached to — `support` is recorded, never enforced (F10, F13) |
| T3 | **False report of an action** — the agent says it did something the gate refused | The model itself | The user's belief that a side effect occurred | `--refuse-claimed-action`: if a CONSEQUENTIAL call was refused in this run, `complete` is not an ending available to it. Structural; reads no English | **Yes.** 2 of 3 attempts produced the false claim without it (F24); 0 of 3 with it, and 12 of 15 runs byte-identical (F27) |
| T4 | **Unbounded cost** — a runaway loop, or one caller consuming the capacity of all others | A caller, deliberately or by accident | Money, and other callers' service | `Governor`: size limit and per-session rate limit, enforced **before** the model is called. In-run: token budget, wall-clock deadline, step cap, and a no-progress check, each with its own stop reason | **Yes.** A 755-character request rejected in 2 seconds and 0 tokens against 103–247 seconds for every other request in the same run (F26) |
| T5 | **Unauthorised side effect** — the agent acts where a person should decide | The model, a confused user, or T1 succeeding | The world: a submitted form, a sent message, a purchase | CONSEQUENTIAL tier. The tool proposes and returns `pending_approval`; a person answers `approve` or `deny`; a denial ends the run `denied` and the model is **not** re-invoked | **Yes.** `pending_approval` reached and resolved in two steps (Phase 5). **A denial has never been sent in any run** — the path is tested, not exercised |

---

## 3. Residual risk

What remains after the controls above, stated as what could still happen.

**R1 — A citation that does not support its claim.** `finish` requires an
evidence URL and, under `--require-quote`, a quote present in the observed text.
Neither establishes that the quote supports the sentence it accompanies. A
model can cite a real page and misdescribe it, and this system would accept
that. `support` measures word overlap, which is the wrong instrument for a
semantic property — recorded four times in `FINDINGS.md` as exactly that
mistake, and therefore recorded rather than enforced. **Mitigation would be a
judge over `(answer, quote)` pairs, which is designed and unbuilt.**

**R2 — Six of eight injection payloads are unmeasured**, and the two that are
measured are the two the detector catches. The detector's miss rate is 75% and
is deliberately not consulted by any gate, so a missed payload changes nothing
about what the agent may do — but it also means the project cannot state an
injection compliance rate for its own threat model. The honest figure today is
two payloads, not eight.

**R3 — The agent fails to terminate more often than it fails safely.** The most
frequent outcome in every measurement is `unterminated`: the model writes prose
where a terminal tool is required, and the guard stops the run after two
attempts. Nothing unsafe happens, and the caller receives nothing they can act
on. **This is the project's dominant failure and it is a usability failure, not
a safety one** — which is the right direction for it to point, and is still the
reason `pass^k` is 1/5.

**R4 — A denial has never been exercised.** `deny` ends the run `denied` and the
model is not re-invoked, which is the property that makes refusal a boundary
rather than the opening of a negotiation. It is unit-tested and no live run has
sent one.

**R5 — The allowlist protects the boundary, not the pages inside it.** A page on
the allowlist that is itself compromised is indistinguishable from a trusted
one. Gate 4 constrains where the agent may go; it makes no claim about what is
there when it arrives, and T1's control is therefore about what the agent may DO
with hostile text, not about avoiding it.

**R6 — The guard in T3 is measured on one task.** The task set contains exactly
one case that can trigger it, so the plausible cost — a run that would
legitimately end `complete` after an unrelated consequential refusal — has no
case in this set. Two firings is evidence, not a distribution.

**R7 — Variance is a security property here, and it is large.** Four of five
tasks are flaky. A control that holds in two runs of three is not a control that
holds; every claim in section 2 marked "measured" is measured at k=3 on five
tasks, and the correct reading of each is *"did not fail in these runs"*, not
*"cannot fail"*.

---

## 4. What a reviewer should check first

In the order that would expose the most if it were wrong:

1. `submit_form` returns `pending_approval` and performs nothing — `tools.py`.
2. `scope` is absent from every argument model — `tiers.py`.
3. Gate 4 reads the allowlist on every call, not once at construction — `dispatcher.py`.
4. The grounding guard counts READ tools that **succeeded**, not calls that were made — `controller.py`.
5. The numbers in section 2 against the run tables in `FINDINGS.md`, which name the run letter for each.

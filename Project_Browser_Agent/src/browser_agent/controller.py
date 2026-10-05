"""The loop. Its only job: when do we stop?

Every exit names a stop reason, enforced by `assert`:
  complete | blocked | pending_approval | out_of_scope | unterminated
  capped_steps | capped_tokens | capped_time | no_progress

Two guards sit in the no-tool-call branch, and both exist because a rule written
in the prompt is a request until code enforces it. The grounding guard refuses an
answer produced before any tool returned anything. The termination guard refuses
an answer that is not delivered through a terminal tool, because prose was a free
exit and the model took it on every task of every run.

The last four were a single reason, `capped`, until a run showed why that was
wrong: two failing tasks both reported `capped` and had nothing in common. One
stalled inside a single long call and was cut off by the deadline; the other
repeated an identical call. Neither came near the turn cap they were assumed to
have hit. A name covering four mechanisms cannot be read, so each exit now names
its own; `is_capped` in tiers.py asks the family question in one go.

A cap is a legitimate outcome, not a crash — it leaves through the same
reporting path as everything else.

Terminal tools return their own reason in `obs["terminal"]`, so the loop never
learns their names: add a new terminal tool and this file does not change.

`steps_used`, `tokens_used` and `evidence` are properties. Stored as fields they
could disagree with `trace`; derived, they cannot.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .clients import Reply, Usage, goal_coverage
from .dispatcher import Dispatcher
from .registry import ToolCall, build_registry
from .tiers import (HUMAN_REASONS, TERMINAL_REASONS, Decision, FinishArgs,
                    HumanAction, Tier, awaits_human)
from .tools import BrowserTools, obs_err

@dataclass
class RunResult:
    run_id: str
    stop_reason: str
    detail: str
    max_steps: int
    transcript: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    resumes: int = 0           # how many human decisions this run has consumed

    # derived - never stored twice
    @property
    def steps_used(self): return len(self.trace)
    @property
    def tokens_used(self): return sum(t["tokens"] for t in self.trace)
    @property
    def evidence(self):
        return [{"url": t["obs"]["evidence_url"], "answer": t["obs"]["detail"]}
                for t in self.trace
                if t["obs"].get("terminal") == "complete" and "evidence_url" in t["obs"]]


async def run_agent(client, dispatcher, registry, system, user_message,
                    max_steps=6, token_budget=20_000, deadline_s=60.0,
                    require_terminal_tool=True, _resume=None):
    run_id = uuid.uuid4().hex[:8]
    transcript = [{"role": "user", "content": user_message}]
    trace = []
    resumes = 0
    started, last_sig = time.time(), None
    ungrounded = unterminated = 0

    if _resume is not None:
        # Continuing a run that stopped to address a person. The prior transcript
        # and trace are carried, so steps, tokens and evidence keep accumulating -
        # a resumed run is ONE run, not two. `resume()` below is the public door;
        # this parameter is how it gets in.
        prior, obs = _resume
        run_id, transcript, trace = prior.run_id, prior.transcript, prior.trace
        resumes = prior.resumes + 1
        transcript.append({"role": "tool", "name": "human", "content": obs})

    def stop(reason, detail):
        assert reason in TERMINAL_REASONS, reason
        return RunResult(run_id, reason, detail, max_steps, transcript, trace,
                         resumes)

    for step in range(len(trace) + 1, max_steps + 1):
        t0 = time.time()
        reply = client.complete(system, transcript, registry)
        tokens = reply.usage.total
        spent = sum(t["tokens"] for t in trace) + tokens

        if spent > token_budget:
            return stop("capped_tokens", f"token budget {token_budget} exceeded")
        if time.time() - started > deadline_s:
            # Checked only AFTER complete() returns, so this fires on the return
            # of a long call, not during it: elapsed can exceed deadline_s by a
            # whole call. A task that ends here stalled; it did not loop.
            return stop("capped_time", f"wall clock {deadline_s}s exceeded")

        if reply.tool_call is None:
            # GROUNDING GUARD. The prompt says "never state a fact no tool result has
            # returned"; without this, that rule is unenforced. An answer produced before
            # any READ tool ran is ungrounded by construction, whatever it says.
            observed = any(t["tier"] == "read" and t["obs"].get("ok") for t in trace)
            if not observed:
                ungrounded += 1
                obs = obs_err("ungrounded_answer",
                              "you answered before any tool returned anything",
                              "Call read_page first, then answer with finish(answer, "
                              "evidence_url).")
                transcript.append({"role": "tool", "name": "grounding_check",
                                   "content": obs})
                trace.append({"step": step, "tool": None, "args": {}, "thought": "",
                              "tier": None, "obs": obs, "tokens": tokens,
                              "latency_ms": int((time.time() - t0) * 1000)})
                if ungrounded >= 2:
                    # NOT `blocked`. That name was borrowed here and it made the
                    # guard indistinguishable from the tool: the ablation run that
                    # DROPPED the `blocked` tool still reported T3 as `blocked`,
                    # twice, and the loose metric scored both as correct. An agent
                    # that chose to say "I cannot know this" and an agent that was
                    # silenced after two inventions are not the same outcome, and
                    # one name for both is F4 all over again - a general name
                    # discarding the specific one that mattered.
                    return stop("ungrounded",
                                "answered twice without observing anything")
                continue                       # hand it back and let the model correct

            # TERMINATION GUARD. `<loop_rules>` says to end with finish, blocked or
            # out_of_scope. Unenforced, that rule loses to a cheaper option: prose
            # ends the run just as well and costs the model nothing.
            #
            # The evidence that this is an incentive problem and not a capability
            # one: across six runs under two different sets of tool descriptions,
            # `finish` was never called once and `evidence` was 0 on every task -
            # and on T3 the model wrote the word "Blocked" IN PROSE, having reached
            # exactly the right judgement, then did not spend a call saying it.
            # The judgement was there. The reason to use the tool was not.
            #
            # So the free exit is closed and the model is handed its own output
            # back. require_terminal_tool=False restores the old behaviour, which
            # is what the earlier runs measured and what the comparison needs.
            if require_terminal_tool:
                unterminated += 1
                obs = obs_err("no_terminal_tool",
                              "a run ends through a tool, not through prose",
                              "Call finish(answer, evidence_url) if a tool result "
                              "holds the answer, blocked(question) if nothing here "
                              "can give it, or out_of_scope(reason) if this is not "
                              "your job.")
                transcript.append({"role": "tool", "name": "termination_check",
                                   "content": obs})
                trace.append({"step": step, "tool": None, "args": {}, "thought": "",
                              "tier": None, "obs": obs, "tokens": tokens,
                              "latency_ms": int((time.time() - t0) * 1000)})
                if unterminated >= 2:
                    return stop("unterminated", "wrote prose twice instead of "
                                                "calling a terminal tool")
                continue                   # hand it back and let the model correct

            transcript.append({"role": "assistant", "content": reply.text})
            trace.append({"step": step, "tool": None, "args": {}, "thought": "",
                          "tier": None, "obs": {"ok": True, "terminal": "complete",
                                                "detail": reply.text or ""},
                          "tokens": tokens,
                          "latency_ms": int((time.time() - t0) * 1000)})
            return stop("complete", reply.text or "")

        call = reply.tool_call
        sig = (call.name, json.dumps(call.args, sort_keys=True))
        if sig == last_sig:
            # Returns BEFORE appending, so steps_used is one short of the calls
            # made - a run ending here with steps=1 made two identical calls.
            return stop("no_progress", f"{call.name} repeated identically")
        last_sig = sig

        transcript.append({"role": "assistant",
                           "tool_call": {"name": call.name, "args": call.args,
                                         "thought": call.thought}})
        obs, tier = await dispatcher.dispatch(call)
        transcript.append({"role": "tool", "name": call.name, "content": obs})

        trace.append({"step": step, "tool": call.name, "args": call.args,
                      "thought": call.thought, "tier": tier.value if tier else None,
                      "obs": obs, "tokens": tokens,
                      "latency_ms": int((time.time() - t0) * 1000)})

        if obs.get("terminal"):
            return stop(obs["terminal"], obs.get("detail", ""))

    return stop("capped_steps", f"turn cap {max_steps} reached")


async def resume(client, dispatcher, registry, system, user_message,
                 res: RunResult, decision: Decision, max_resumes=1, **kw):
    """Hand a person's decision back to a run that stopped to address them.

    Two of the nine stop reasons are addressed to a person, and until now both
    were dead ends. `blocked` asked a question nobody could answer; and
    `pending_approval` - a stop reason declared on the first day, enforced by
    `assert`, with a tool that produces it - was never reached in any run from A
    to S, because no evaluation ever passed `allow_consequential`. A named exit
    with no path to it is F1 again: a mechanism that exists and cannot be reached.

    Three decisions, two shapes:

      answer  -> the loop continues. This is the only one that re-invokes the
                 model, and the only one worth measuring: it asks whether the
                 agent's question was answerable at all.
      approve -> ends `approved`. NOTHING is submitted; see tiers.py.
      deny    -> ends `denied`, and the agent is NOT re-invoked. A refused
                 proposal that comes back reworded is exactly what the
                 CONSEQUENTIAL tier exists to stop, so refusal is a boundary and
                 not the opening of a negotiation.

    `max_resumes` is 1 to start. A higher cap is a measurement, not a default:
    with one resume, "did a single human reply resolve it?" has a clean answer.
    """
    if not awaits_human(res.stop_reason):
        raise ValueError(f"{res.stop_reason} is not addressed to a person; "
                         f"only {sorted(HUMAN_REASONS)} can be resumed")
    if res.resumes >= max_resumes:
        return RunResult(res.run_id, res.stop_reason,
                         f"{res.detail} [resume cap {max_resumes} reached]",
                         res.max_steps, res.transcript, res.trace, res.resumes)

    act = decision.action
    if res.stop_reason == "pending_approval":
        if act is HumanAction.ANSWER:
            raise ValueError("a proposal takes approve or deny, not answer")
        reason = "approved" if act is HumanAction.APPROVE else "denied"
        detail = decision.text or f"human {act.value}d the proposal"
        res.transcript.append({"role": "tool", "name": "human",
                               "content": {"ok": True, "decision": act.value,
                                           "detail": detail}})
        return RunResult(res.run_id, reason, detail, res.max_steps,
                         res.transcript, res.trace, res.resumes + 1)

    # blocked: only an answer moves it. approve/deny answer nothing.
    if act is not HumanAction.ANSWER:
        raise ValueError("a blocked question takes answer, not approve/deny")
    obs = {"ok": True, "source": "human", "detail": decision.text,
           "state_changed": False,
           "hint": "This answers the question you asked. Continue, and end "
                   "through a tool."}
    return await run_agent(client, dispatcher, registry, system, user_message,
                           _resume=(res, obs), **kw)


def resolved_by_one_reply(res: RunResult) -> bool:
    """Did one human reply turn a stop into a finish?

    The metric the interaction layer is actually about. Not "did the loop
    continue" - it always can - but whether the question the agent asked was
    answerable. "What is the form's URL?" is answerable in a line. "Is this
    domain available?" is not a request for information from the user at all; it
    is an admission of a limit, and no reply resolves it.
    """
    return res.resumes > 0 and res.stop_reason in {"complete", "approved"}


def answer_support(res: RunResult):
    """How much of the answer appears in what the tools actually returned.

    The grounding guard asks whether the agent observed ANYTHING; it does not ask
    whether the answer it then gave is in what it observed. Run F showed the
    difference: with `blocked` unavailable, the agent answered "example.com is
    available for registration" - a sentence that appears in no tool result -
    and attached a valid evidence_url to it. Every existing check passed it.

    Returns a fraction, or None when the run produced no answer. Deliberately NOT
    wired into a refusal yet: a threshold picked before seeing the distribution
    is the same guess that produced three wrong findings already. Measure the
    scores for answers known to be true and for the one known to be invented,
    then choose. `goal_coverage` is reused rather than reinvented so the notion
    of "a content word" stays single-sourced.
    """
    answer = next((t["obs"]["detail"] for t in res.trace
                   if t["obs"].get("terminal") == "complete"), None)
    if not answer:
        return None
    seen = " ".join(json.dumps(t["obs"], ensure_ascii=False) for t in res.trace
                    if t["tier"] == "read" and t["obs"].get("ok"))
    return round(goal_coverage(answer, seen), 3)


def report(res: RunResult):
    print(f"RUN {res.run_id} | STOP = {res.stop_reason.upper()}")
    print(f"detail : {res.detail[:100]}")
    print(f"steps  : {res.steps_used}/{res.max_steps}   tokens: {res.tokens_used}")
    print("-" * 96)
    print(f"{'#':>2}  {'tool':<13}{'tier':<15}{'ok':<7}{'chg':<6}{'error / terminal'}")
    for t in res.trace:
        o = t["obs"]
        tag = o.get("error") or (o.get("terminal") or "")
        print(f"{t['step']:>2}  {str(t['tool']):<13}{str(t['tier']):<15}"
              f"{str(o.get('ok')):<7}{str(o.get('state_changed')):<6}{tag}")
        if t["thought"]:
            print(f"    thought: {t['thought']}")
    print("-" * 96)
    for e in res.evidence:
        print("EVIDENCE:", e["url"], "\nANSWER  :", e["answer"][:200])


def build_agent(page, allowed_domains, allow_consequential=False,
                require_quote=False, stop_mode="split", quote_same_page=False):
    tools = BrowserTools(page, allowed_domains)
    registry = build_registry(tools, stop_mode=stop_mode)
    return tools, registry, Dispatcher(tools, registry, allow_consequential,
                                       require_quote, quote_same_page)

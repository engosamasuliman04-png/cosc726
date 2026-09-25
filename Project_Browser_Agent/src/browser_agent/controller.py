"""The loop. Its only job: when do we stop?

Every exit names a stop reason, enforced by `assert`:
  complete | blocked | pending_approval | out_of_scope | capped

`capped` is a legitimate outcome, not a crash — it leaves through the same
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

from .clients import Reply, Usage
from .dispatcher import Dispatcher
from .registry import ToolCall, build_registry
from .tiers import TERMINAL_REASONS, FinishArgs, Tier
from .tools import BrowserTools, obs_err

@dataclass
class RunResult:
    run_id: str
    stop_reason: str
    detail: str
    max_steps: int
    transcript: list = field(default_factory=list)
    trace: list = field(default_factory=list)

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
                    max_steps=6, token_budget=20_000, deadline_s=60.0):
    run_id = uuid.uuid4().hex[:8]
    transcript = [{"role": "user", "content": user_message}]
    trace = []
    started, last_sig, ungrounded = time.time(), None, 0

    def stop(reason, detail):
        assert reason in TERMINAL_REASONS, reason
        return RunResult(run_id, reason, detail, max_steps, transcript, trace)

    for step in range(1, max_steps + 1):
        t0 = time.time()
        reply = client.complete(system, transcript, registry)
        tokens = reply.usage.total
        spent = sum(t["tokens"] for t in trace) + tokens

        if spent > token_budget:
            return stop("capped", f"token budget {token_budget} exceeded")
        if time.time() - started > deadline_s:
            return stop("capped", f"wall clock {deadline_s}s exceeded")

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
                    return stop("blocked", "answered twice without observing anything")
                continue                       # hand it back and let the model correct

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
            return stop("capped", f"no progress: {call.name} repeated identically")
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

    return stop("capped", f"turn cap {max_steps} reached")


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


def build_agent(page, allowed_domains, allow_consequential=False):
    tools = BrowserTools(page, allowed_domains)
    registry = build_registry(tools)
    return tools, registry, Dispatcher(tools, registry, allow_consequential)

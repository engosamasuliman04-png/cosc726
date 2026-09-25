"""Week 7: plan -> validate -> execute -> critique -> re-plan.

What ReAct never has is a CHECKPOINT: a moment at which a sequence exists to be
checked before anything runs.

Gates 1 and 2 are the same checks as run time, moved forward. Gates 3 and 4
depend on run history, so at plan time they are SIMULATED — STRIPS with an
explicit delete-list.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .dispatcher import Dispatcher
from .registry import ToolCall, ToolSpec
from .tiers import Tier

@dataclass
class Goal:
    text: str
    # each part of the request -> the tools that could satisfy it
    requires: dict[str, set[str]] = field(default_factory=dict)


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int = Field(ge=1, le=12)
    tool: str
    args: dict = Field(default_factory=dict)
    why: str = Field(max_length=200)          # what this step establishes


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_restated: str
    steps: list[Step] = Field(min_length=1, max_length=12)

    def signature(self) -> str:
        """Identity of the plan's SHAPE - for oscillation detection."""
        return "|".join(f"{s.tool}({json.dumps(s.args, sort_keys=True)})"
                        for s in self.steps)


PRECONDITIONS = {
    "click_link": {"links", "read"},     # needs list_links AND read_page on THIS page
}

def apply_effects(known: set, tool: str) -> set:
    if tool == "read_page":
        return known | {"read"}
    if tool == "list_links":
        return known | {"links", "read"}
    if tool in ("click_link", "open_url"):
        return known - {"read", "links"}   # STRIPS delete-list: new page, knowledge void
    return known


def validate_plan(plan: Plan, registry: dict[str, ToolSpec],
                  allow_consequential: bool = False) -> list[str]:
    """Runs BEFORE anything executes. Returns a list of problems."""
    problems, known = [], set()

    for s in plan.steps:
        spec = registry.get(s.tool)

        # gate 1 - a tool the model invented
        if spec is None:
            problems.append(f"step {s.n}: no such tool '{s.tool}'")
            continue

        # gate 2 - arguments conform
        try:
            spec.args_model.model_validate(s.args)
        except ValidationError as e:
            f0 = e.errors()[0]
            loc = ".".join(str(x) for x in f0["loc"]) or "args"
            problems.append(f"step {s.n}: {s.tool}: {loc}: {f0['msg']}")

        # gate 4 - tier, known at plan time
        if spec.tier is Tier.CONSEQUENTIAL and not allow_consequential:
            problems.append(f"step {s.n}: {s.tool} is CONSEQUENTIAL "
                            f"and may only be proposed, never planned as an action")

        # gate 3/4 - preconditions, simulated over the plan
        need = PRECONDITIONS.get(s.tool, set())
        missing = need - known
        if missing:
            problems.append(f"step {s.n}: {s.tool} needs {sorted(missing)} "
                            f"but no earlier step establishes it")

        known = apply_effects(known, s.tool)

    if not any(registry.get(s.tool) and registry[s.tool].tier is Tier.CONTROL
               for s in plan.steps):
        problems.append("plan never terminates: no finish / blocked / out_of_scope step")

    return problems


def detect_oscillation(versions: list[Plan]) -> Optional[str]:
    seen = {}
    for i, p in enumerate(versions, start=1):
        sig = p.signature()
        if sig in seen:
            return f"plan v{i} repeats v{seen[sig]}"
        seen[sig] = i
    return None


def goal_drift(goal: Goal, plan: Plan) -> list[str]:
    covered = {s.tool for s in plan.steps}
    return [need for need, tools in goal.requires.items()
            if not (tools & covered)]


STRUCTURAL_ERRORS = {"unknown_tool", "requires_human_approval"}

@dataclass
class ExecResult:
    steps: list = field(default_factory=list)
    terminal: Optional[str] = None
    detail: str = ""
    failed_at: Optional[int] = None
    structural: bool = False


async def execute_plan(plan: Plan, dispatcher: Dispatcher) -> ExecResult:
    r = ExecResult()
    for s in plan.steps:
        obs, tier = await dispatcher.dispatch(ToolCall(s.tool, s.args, s.why))
        r.steps.append({"n": s.n, "tool": s.tool, "args": s.args, "why": s.why,
                        "tier": tier.value if tier else None,
                        "ok": obs.get("ok"), "error": obs.get("error"),
                        "state_changed": obs.get("state_changed"), "obs": obs})
        if obs.get("terminal"):
            r.terminal, r.detail = obs["terminal"], obs.get("detail", "")
            return r
        if not obs.get("ok"):
            r.failed_at = s.n
            r.structural = obs.get("error") in STRUCTURAL_ERRORS
            return r
    return r


@dataclass
class Critique:
    goal_met: bool
    structural: bool
    revise: bool
    problems: list = field(default_factory=list)
    reasons: str = ""


class HeuristicCritic:
    """Deterministic stand-in. Zero tokens, and it never flatters the plan."""
    def __call__(self, goal: Goal, plan: Plan, result: ExecResult) -> Critique:
        if result.terminal == "complete":
            return Critique(True, False, False, reasons="a finish step returned complete")
        if result.terminal in ("blocked", "out_of_scope"):
            return Critique(True, False, False,
                            reasons=f"agent stopped deliberately: {result.terminal}")
        if result.structural:
            bad = [s for s in result.steps if s["error"] in STRUCTURAL_ERRORS]
            return Critique(False, True, False,
                            problems=[f"step {s['n']}: {s['error']}" for s in bad],
                            reasons="no re-plan can supply a missing tool or a denied permission")
        if result.failed_at is not None:
            s = next(x for x in result.steps if x["n"] == result.failed_at)
            return Critique(False, False, True,
                            problems=[f"step {s['n']} {s['tool']}: {s['error']}"],
                            reasons="a recoverable step failed")
        return Critique(False, False, True, problems=["plan ran out without terminating"],
                        reasons="no terminal step reached")


PLAN_STOP_REASONS = {"complete", "blocked", "out_of_scope",
                     "structural", "oscillating", "capped"}

@dataclass
class PlanRunResult:
    run_id: str
    stop_reason: str
    detail: str
    rounds: list = field(default_factory=list)
    versions: list = field(default_factory=list)

    @property
    def rounds_used(self): return len(self.rounds)
    @property
    def tokens_used(self): return sum(r["tokens"] for r in self.rounds)


async def run_planned_agent(planner, critic, dispatcher, registry, goal: Goal,
                            max_rounds=3, allow_consequential=False):
    run_id = uuid.uuid4().hex[:8]
    versions, rounds, feedback = [], [], None

    def stop(reason, detail):
        assert reason in PLAN_STOP_REASONS, reason
        return PlanRunResult(run_id, reason, detail, rounds, versions)

    for rnd in range(1, max_rounds + 1):
        plan, tokens = planner(goal, feedback)
        versions.append(plan)

        entry = {"round": rnd, "plan": plan, "tokens": tokens,
                 "signature": plan.signature()}
        rounds.append(entry)

        # ---- oscillation: deterministic, before spending anything ----
        osc = detect_oscillation(versions)
        if osc:
            entry["stop"] = "oscillating"
            return stop("oscillating", osc)

        # ---- validate BEFORE executing ----
        problems = validate_plan(plan, registry, allow_consequential)
        entry["plan_problems"] = problems
        if problems:
            # a plan naming a tool that does not exist can never be fixed by retrying
            if any("no such tool" in p for p in problems):
                entry["stop"] = "structural"
                return stop("structural", "; ".join(problems))
            feedback = problems
            entry["outcome"] = "rejected at plan time"
            continue                                  # NOTHING EXECUTED

        # ---- execute, with the Week 4 gates still in front of every call ----
        result = await execute_plan(plan, dispatcher)
        entry["exec"] = result.steps
        entry["terminal"] = result.terminal

        crit = critic(goal, plan, result)

        # ---- a deterministic check overrules the model's opinion ----
        drift = goal_drift(goal, plan)
        entry["drift"] = drift
        if drift and crit.goal_met:
            crit.goal_met, crit.revise = False, True
            crit.problems = crit.problems + [f"goal drift: {d} unaddressed" for d in drift]
            crit.reasons = "critic said goal_met; the goal disagrees"

        entry["critique"] = crit

        if crit.goal_met:
            entry["stop"] = result.terminal or "complete"
            return stop(result.terminal or "complete", crit.reasons)
        if crit.structural:
            entry["stop"] = "structural"
            return stop("structural", "; ".join(crit.problems) or crit.reasons)

        feedback = crit.problems

    return stop("capped", f"re-plan cap {max_rounds} reached")


def plan_report(res: PlanRunResult):
    print(f"RUN {res.run_id} | STOP = {res.stop_reason.upper()}")
    print(f"detail : {res.detail[:110]}")
    print(f"rounds : {res.rounds_used} | tokens: {res.tokens_used}")
    for r in res.rounds:
        print("=" * 88)
        p = r["plan"]
        print(f"ROUND {r['round']}  restated: {p.goal_restated[:70]}")
        for s in p.steps:
            print(f"   {s.n}. {s.tool:<13} {json.dumps(s.args)[:32]:<34} {s.why[:36]}")
        if r.get("plan_problems"):
            print("   PLAN-TIME PROBLEMS (nothing executed):")
            for pr in r["plan_problems"]:
                print("     x", pr)
        for e in r.get("exec", []):
            flag = "ok " if e["ok"] else "ERR"
            print(f"   -> {e['n']}. {e['tool']:<13} {str(e['tier']):<14} {flag} {e['error'] or ''}")
        if r.get("drift"):
            print("   DRIFT:", r["drift"])
        c = r.get("critique")
        if c:
            print(f"   CRITIC: goal_met={c.goal_met} structural={c.structural} "
                  f"revise={c.revise} | {c.reasons}")

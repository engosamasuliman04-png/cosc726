"""Week 7's missing half: the component that asks the model for a plan.

`planning.py` has been complete since it was written - the typed contract,
validation before execution, STRIPS-style preconditions simulated over the plan,
oscillation detection, the structural verdict. All of it was exercised by tests
that hand it plans **written by hand**. So the tests measured the gates, and the
question the lecture actually asks was never put to a model:

    can a 1.7B model emit a plan that passes `validate_plan` before anything runs?

That is what this file makes askable. It is deliberately thin: it builds a
prompt, calls the model once, parses, and validates against `Plan`. Everything
that decides whether the plan is any good already exists in planning.py, and
nothing here is allowed to repair a plan on the model's behalf - a planner that
quietly fixes its own output measures the planner's author, not the model.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import ValidationError

from .ollama_client import extract_json
from .planning import Goal, Plan

PLAN_PROMPT = """You are planning BEFORE acting. Emit a complete plan as JSON and
call no tools.

The plan is checked before any step runs. A step naming a tool that does not
exist, or missing a required argument, or acting on a page no earlier step has
read, is rejected and the whole plan comes back to you.

Tools available:
{tools}

Rules the checker enforces:
  - every step names one of the tools above, spelled exactly
  - args must match that tool's arguments exactly; a tool with no arguments takes {{}}
  - click_link needs an earlier list_links AND read_page on the same page
  - the LAST step must be finish, blocked or out_of_scope
  - at most 12 steps

Reply with ONLY this JSON object and nothing else:

{{"goal_restated": "<the goal in your own words>",
  "steps": [{{"n": 1, "tool": "<name>", "args": {{}}, "why": "<what this establishes>"}}]}}

GOAL: {goal}
"""

FEEDBACK = """
Your previous plan was rejected before anything ran. Fix exactly these and emit
the whole plan again:
{problems}
"""


class LLMPlanner:
    """planner(goal, feedback) -> (Plan, tokens), the shape run_planned_agent wants.

    A parse failure or a schema violation is NOT retried inside this call. It
    returns a one-step plan naming a tool that does not exist, which
    `validate_plan` rejects as structural and `run_planned_agent` then stops on.
    That is the honest route: "the model could not produce a plan" is an outcome
    of the experiment, and hiding it behind a retry loop would turn a measurement
    into a demonstration.
    """

    def __init__(self, backend, registry, system=""):
        # The BACKEND, not OllamaClient. Both of the client's paths expect a tool
        # call: the native one returns text only when the model emits none, and
        # the prose one parses the reply as a ToolCallEnvelope and retries when
        # it is not. A planner asks for prose on purpose, so routing it through
        # a tool-call client discards exactly the thing being measured.
        #
        # The first planned run reported `raw: ''` on two tasks - the model had
        # replied and the client had thrown the reply away. "The model cannot
        # emit a plan" would have been the wrong conclusion from my own wiring,
        # which is the fifteenth time a tool in this project has said that.
        self.backend = backend
        self.registry = registry
        self.system = system
        self.parse_failures = 0
        self.last_raw = ""

    def _tools_block(self) -> str:
        lines = []
        for name, spec in self.registry.items():
            fields = ", ".join(spec.args_model.model_fields) or "no arguments"
            lines.append(f"  {name}({fields}) - {spec.description}")
        return "\n".join(lines)

    def __call__(self, goal: Goal, feedback: Optional[list] = None):
        prompt = PLAN_PROMPT.format(tools=self._tools_block(), goal=goal.text)
        if feedback:
            prompt += FEEDBACK.format(problems="\n".join(f"  - {p}" for p in feedback))

        msgs = ([{"role": "system", "content": self.system}] if self.system else []) \
            + [{"role": "user", "content": prompt}]
        resp = self.backend.chat(msgs)
        msg = resp.get("message", {}) or {}
        tokens = int(resp.get("prompt_eval_count", 0)) + int(resp.get("eval_count", 0))

        # A thinking model can leave the whole answer in `thinking` and send back
        # an empty `content`. Reading only `content` would record that as "no
        # JSON", so both are searched and `content` is preferred.
        text = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
        self.last_raw = (text or thinking)[:400]

        obj, err = extract_json(text)
        if obj is None and thinking:
            obj, err = extract_json(thinking)
        if obj is None:
            self.parse_failures += 1
            return self._unplannable(f"no JSON in the reply ({err})"), tokens
        try:
            return Plan.model_validate(obj), tokens
        except ValidationError as e:
            f0 = e.errors()[0]
            loc = ".".join(str(x) for x in f0["loc"]) or "plan"
            self.parse_failures += 1
            return self._unplannable(f"{loc}: {f0['msg']}"), tokens

    @staticmethod
    def _unplannable(why: str) -> Plan:
        """A plan the checker must reject, carrying the reason in `why`.

        Returning None instead would make the caller handle a second kind of
        failure; this keeps one path - every plan is validated, and an unparseable
        one fails validation like any other. `__no_such_tool__` is rejected as
        structural, which stops the run rather than re-planning, because a model
        that cannot emit JSON will not emit it on the third attempt either.
        """
        return Plan(goal_restated="unparseable",
                    steps=[{"n": 1, "tool": "__no_such_tool__", "args": {},
                            "why": why[:200]}])

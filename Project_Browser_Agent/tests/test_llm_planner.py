"""The component Week 7 was missing: the one that asks the MODEL for a plan.

planning.py's own tests hand it plans written by hand, so they exercise the
gates and never the question the lecture asks. These cover the seam between a
model's reply and `Plan`.
"""

from browser_agent import build_agent
from browser_agent.clients import Reply, Usage
from browser_agent.fakes import ALLOW, FakePage
from browser_agent.llm_planner import LLMPlanner
from browser_agent.planning import Goal, validate_plan


class Says:
    """A BACKEND that returns one fixed completion. The planner talks to the
    backend because both of OllamaClient's paths expect a tool call and would
    throw a prose plan away."""
    def __init__(self, text, thinking=""): self.text, self.thinking = text, thinking
    def chat(self, messages, tools=None):
        return {"message": {"content": self.text, "thinking": self.thinking},
                "prompt_eval_count": 100, "eval_count": 20}


GOOD = """Here is the plan:
```json
{"goal_restated": "read the heading",
 "steps": [{"n": 1, "tool": "read_page", "args": {}, "why": "see the page"},
           {"n": 2, "tool": "finish",
            "args": {"answer": "Example Domain",
                     "evidence_url": "https://example.com/"},
            "why": "report it"}]}
```"""


def reg():
    return build_agent(FakePage("https://example.com/"), ALLOW)[1]


def test_a_well_formed_plan_parses_and_passes_validation():
    plan, tokens = LLMPlanner(Says(GOOD), reg())(Goal(text="what is the heading?"))
    assert [s.tool for s in plan.steps] == ["read_page", "finish"]
    assert validate_plan(plan, reg()) == []
    assert tokens == 120

def test_prose_with_no_json_becomes_a_plan_the_checker_rejects():
    """NOT retried inside the planner. "the model could not produce a plan" is an
    outcome of the experiment, and a retry loop would turn the measurement into a
    demonstration."""
    p = LLMPlanner(Says("I would start by reading the page, then answer."), reg())
    plan, _ = p(Goal(text="x"))
    assert p.parse_failures == 1
    problems = validate_plan(plan, reg())
    assert any("no such tool" in x for x in problems)

def test_json_that_is_not_a_plan_is_reported_by_field():
    p = LLMPlanner(Says('{"goal_restated": "x", "steps": []}'), reg())
    plan, _ = p(Goal(text="x"))
    assert p.parse_failures == 1
    assert "steps" in plan.steps[0].why           # the field that failed, carried

def test_a_plan_that_clicks_before_listing_is_rejected_before_anything_runs():
    """The whole point of planning first: gate 3's precondition is simulated over
    the sequence, so the bad step is caught while it is still only a proposal."""
    bad = """{"goal_restated": "click", "steps": [
        {"n": 1, "tool": "click_link", "args": {"index": 0}, "why": "go"},
        {"n": 2, "tool": "finish", "args": {"answer": "a",
         "evidence_url": "https://example.com/"}, "why": "done"}]}"""
    plan, _ = LLMPlanner(Says(bad), reg())(Goal(text="x"))
    problems = validate_plan(plan, reg())
    assert any("click_link needs" in x for x in problems)

def test_feedback_from_a_rejection_reaches_the_next_prompt():
    seen = {}

    class Record(Says):
        def chat(self, messages, tools=None):
            seen["prompt"] = messages[-1]["content"]
            return {"message": {"content": GOOD}, "prompt_eval_count": 1,
                    "eval_count": 1}

    LLMPlanner(Record(""), reg())(Goal(text="x"),
                                  feedback=["step 1: no such tool 'fly'"])
    assert "no such tool 'fly'" in seen["prompt"]
    assert "rejected before anything ran" in seen["prompt"]


def test_a_plan_left_in_the_thinking_field_is_still_found():
    """A thinking model can put the whole answer in `thinking` and return an
    empty `content`. Reading only `content` records that as "no JSON" and blames
    the model for the harness."""
    p = LLMPlanner(Says("", thinking=GOOD), reg())
    plan, _ = p(Goal(text="x"))
    assert p.parse_failures == 0
    assert [s.tool for s in plan.steps] == ["read_page", "finish"]

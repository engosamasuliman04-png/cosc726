"""Stop reasons, the grounding guard, and derived fields.

Every exit from run_agent names a stop reason, and a cap is a legitimate outcome,
not a crash. The four cap endings carry four different names because they are
four different failures.
"""

import pytest

from browser_agent import SYSTEM, build_agent, is_capped, run_agent
from browser_agent.clients import HeuristicClient, ScriptedClient
from browser_agent.fakes import ALLOW, FakePage, R

async def run(script, start="https://example.com/", **kw):
    _, reg, disp = build_agent(FakePage(start), ALLOW, **{
        k: v for k, v in kw.items() if k == "allow_consequential"})
    loop_kw = {k: v for k, v in kw.items() if k != "allow_consequential"}
    return await run_agent(ScriptedClient(script), disp, reg, SYSTEM, "q", **loop_kw)

async def test_complete_via_finish():
    res = await run([R("read_page", {}),
                     R("finish", {"answer": "reserved",
                                  "evidence_url": "https://example.com/"})])
    assert res.stop_reason == "complete"
    assert len(res.evidence) == 1

async def test_blocked():
    res = await run([R("blocked", {"question": "Which page do I start from?"})])
    assert res.stop_reason == "blocked"

async def test_out_of_scope():
    res = await run([R("out_of_scope", {"reason": "this is a purchase request"})])
    assert res.stop_reason == "out_of_scope"

async def test_pending_approval_changes_nothing():
    """`ok: True` means the CALL succeeded, not that the action happened."""
    res = await run([R("submit_form", {"reason": "confirm the purchase"})],
                    allow_consequential=True)
    assert res.stop_reason == "pending_approval"
    assert res.transcript[-1]["content"]["state_changed"] is False

@pytest.mark.parametrize("expected,script,kw", [
    ("capped_steps",  [R("read_page", {}), R("list_links", {})] * 2, {"max_steps": 3}),
    ("capped_tokens", [R("read_page", {}, p=9000, c=2000)] * 4, {"token_budget": 15000}),
    ("no_progress",   [R("read_page", {})] * 4, {}),
])
async def test_each_cap_names_its_own_mechanism(expected, script, kw):
    """One name for four endings hid which one happened; these assert the name,
    not just the family. A test asserting only `is_capped` would still pass if
    the four collapsed back into one, which is the regression worth catching."""
    res = await run(script, **kw)
    assert res.stop_reason == expected
    assert is_capped(res.stop_reason)

async def test_the_two_complete_paths_are_distinguishable_from_the_trace():
    """Both exits report `complete`, so the stop reason alone cannot tell a run
    that called finish from one that merely stopped emitting calls. evaluate.py
    scores them differently, so the trace MUST carry the difference - and it does,
    in `tool`: the control tool names itself, the fall-through leaves it None.

    Written after a first attempt keyed on obs["terminal"], which the fall-through
    exit also sets: the check reported "ended via a tool" for precisely the runs
    it existed to catch."""
    via_tool = await run([R("read_page", {}),
                          R("finish", {"answer": "x",
                                       "evidence_url": "https://example.com/"})])
    fell_through = await run([R("read_page", {}), R(t="the heading is Example Domain")],
                             require_terminal_tool=False)

    assert via_tool.stop_reason == fell_through.stop_reason == "complete"

    def ended_by(res):
        t = next(t for t in res.trace if t["obs"].get("terminal"))
        return t["tool"], t["tier"]

    assert ended_by(via_tool) == ("finish", "control")
    assert ended_by(fell_through) == (None, None)
    assert len(via_tool.evidence) == 1 and len(fell_through.evidence) == 0

async def test_no_progress_undercounts_steps_by_one():
    """The no-progress exit returns before appending, so the repeated call is
    never traced. Documented because a run showing steps=1 actually made two
    calls - the gap that made an earlier failure unreadable."""
    res = await run([R("read_page", {})] * 4)
    assert res.stop_reason == "no_progress" and res.steps_used == 1

# ------------------------------------------------------- the grounding guard
async def test_ungrounded_answer_is_refused():
    """The prompt says "never state a fact no tool result has returned". Without
    enforcement that is a wish: a model answering on turn one, before any tool ran,
    would be accepted as `complete` and `finish`'s mandatory evidence_url bypassed.

    This is what the first real-model run actually did.
    """
    res = await run([R(t="It reserves example.com."), R(t="I already told you.")])
    assert res.stop_reason == "blocked"
    assert res.trace[0]["obs"]["error"] == "ungrounded_answer"

async def test_model_can_correct_itself():
    """A refusal is handed back as a structured observation, not a hard stop."""
    res = await run([
        R(t="RFC 2606 reserves example.com."),          # ungrounded
        R("read_page", {}),                              # corrects
        R("finish", {"answer": "RFC 2606 reserves example.com",
                     "evidence_url": "https://example.com/"}),
    ])
    assert res.stop_reason == "complete"
    assert res.trace[0]["obs"]["error"] == "ungrounded_answer"
    assert len(res.evidence) == 1

# -------------------------------------------------- the termination guard
async def test_grounded_plain_answer_completes_only_when_prose_is_allowed():
    """Grounded prose used to be accepted as `complete`. That was the free exit:
    the model could answer without ever spending a call on `finish`, so no run
    ever produced an evidence_url. The old behaviour is still reachable, because
    the before/after comparison needs it."""
    res = await run([R("read_page", {}), R(t="It reserves example.com.")],
                    require_terminal_tool=False)
    assert res.stop_reason == "complete" and len(res.evidence) == 0

async def test_prose_is_refused_and_the_model_can_correct():
    """The guard hands the model its own output back rather than ending the run,
    so a model that CAN call the tool still gets there - which is the whole point:
    the point is to remove a cheaper option, not to fail the task."""
    res = await run([R("read_page", {}),
                     R(t="The heading is Example Domain."),
                     R("finish", {"answer": "Example Domain",
                                  "evidence_url": "https://example.com/"})])
    assert res.stop_reason == "complete"
    assert res.trace[1]["obs"]["error"] == "no_terminal_tool"
    assert len(res.evidence) == 1

async def test_two_prose_replies_end_the_run_with_a_named_reason():
    """A model that will not use the tool is not left looping: it stops as
    `unterminated`, which is a result, not a crash."""
    res = await run([R("read_page", {}),
                     R(t="It reserves example.com."),
                     R(t="As I said, it reserves example.com.")])
    assert res.stop_reason == "unterminated"
    assert len(res.evidence) == 0

# ------------------------------------------------------- derived fields
async def test_derived_fields_cannot_disagree_with_trace():
    res = await run([R("read_page", {}, p=500, c=50),
                     R("finish", {"answer": "reserved",
                                  "evidence_url": "https://example.com/"}, p=700, c=50)])
    assert res.steps_used == len(res.trace) == 2
    assert res.tokens_used == 1300

# ------------------------------------------------------- the zero-cost baseline
async def test_heuristic_baseline_reaches_the_goal():
    """Its weakness is visible in the trace: the link "Learn more" scores 0.00
    against the goal, because keyword overlap has no semantics. It reaches the
    goal only because it is the only link on the page.
    """
    goal = "Find information about example domains"
    _, reg, disp = build_agent(FakePage("https://example.com/"), ALLOW)
    res = await run_agent(HeuristicClient(goal), disp, reg, SYSTEM, goal, max_steps=8)
    assert res.stop_reason == "complete"
    assert res.tokens_used == 0
    assert "scored 0.00" in res.trace[2]["thought"]

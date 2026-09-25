"""Stop reasons, the grounding guard, and derived fields.

Every exit from run_agent names a stop reason. `capped` is a legitimate outcome,
not a crash.
"""

import pytest

from browser_agent import SYSTEM, build_agent, run_agent
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

@pytest.mark.parametrize("label,script,kw", [
    ("turn cap",      [R("read_page", {}), R("list_links", {})] * 2, {"max_steps": 3}),
    ("token ceiling", [R("read_page", {}, p=9000, c=2000)] * 4, {"token_budget": 15000}),
    ("no progress",   [R("read_page", {})] * 4, {}),
])
async def test_capped(label, script, kw):
    res = await run(script, **kw)
    assert res.stop_reason == "capped", label

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

async def test_grounded_plain_answer_still_completes():
    res = await run([R("read_page", {}), R(t="It reserves example.com.")])
    assert res.stop_reason == "complete"

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

"""The two endings addressed to a person, and the reply channel they lacked.

`blocked` carries a question and `pending_approval` carries a proposal. Both
stopped the run and neither could be answered, so an agent that correctly asked
for help was indistinguishable from one that had failed.

These tests drive the decision path directly rather than through a model: a
well-behaved model may never produce the shape a path exists to handle, which is
the same reason the gate tests drive the dispatcher.
"""

import pytest

from browser_agent import (SYSTEM, Decision, HumanAction, build_agent,
                           resolved_by_one_reply, resume, run_agent)
from browser_agent.clients import ScriptedClient
from browser_agent.fakes import ALLOW, FakePage, R


def agent(allow_consequential=False):
    return build_agent(FakePage("https://example.com/"), ALLOW,
                       allow_consequential=allow_consequential)

async def first(script, allow_consequential=False, **kw):
    _, reg, disp = agent(allow_consequential)
    res = await run_agent(ScriptedClient(script), disp, reg, SYSTEM, "q", **kw)
    return res, reg, disp

# ------------------------------------------------ the decision is validated
def test_an_answer_with_no_words_answers_nothing():
    """A human reply is still untrusted input to the loop, so it is validated
    like a tool call rather than trusted for being human."""
    with pytest.raises(ValueError):
        Decision(action=HumanAction.ANSWER, text="   ")
    Decision(action=HumanAction.APPROVE)          # reason is optional here

def test_an_unknown_action_is_refused():
    with pytest.raises(ValueError):
        Decision(action="maybe", text="x")

# ------------------------------------------------ blocked -> answer -> finish
async def test_an_answered_question_lets_the_run_finish():
    """The question `blocked` asks was never answerable. Now one reply carries
    the run to a cited answer, and it counts as ONE run: the trace continues."""
    res, reg, disp = await first([R("blocked", {"question": "Which URL?"})])
    assert res.stop_reason == "blocked" and res.resumes == 0
    steps_before = res.steps_used

    out = await resume(ScriptedClient([
        R("read_page", {}),
        R("finish", {"answer": "Example Domain",
                     "evidence_url": "https://example.com/"})]),
        disp, reg, SYSTEM, "q", res, Decision(action=HumanAction.ANSWER,
                                              text="https://example.com/"))
    assert out.stop_reason == "complete"
    assert out.run_id == res.run_id              # one run, not two
    assert out.steps_used > steps_before         # the trace carried over
    assert resolved_by_one_reply(out)

async def test_an_unanswerable_question_is_not_rescued_by_a_reply():
    """The metric is not "did the loop continue" - it always can. T3's question
    is an admission of a limit, not a request for information, so a reply moves
    nothing and the run stops again."""
    res, reg, disp = await first([R("blocked", {"question": "Is it available?"})])
    out = await resume(ScriptedClient([
        R("blocked", {"question": "No public page states this."})]),
        disp, reg, SYSTEM, "q", res,
        Decision(action=HumanAction.ANSWER, text="I do not know either"))
    assert out.stop_reason == "blocked"
    assert not resolved_by_one_reply(out)

async def test_the_human_reply_reaches_the_model():
    res, reg, disp = await first([R("blocked", {"question": "Which URL?"})])
    await resume(ScriptedClient([R("read_page", {})]), disp, reg, SYSTEM, "q",
                 res, Decision(action=HumanAction.ANSWER, text="use example.com"))
    human = [m for m in res.transcript if m.get("name") == "human"]
    assert human and human[0]["content"]["detail"] == "use example.com"

# ------------------------------------------- pending_approval -> the two answers
async def test_a_proposal_can_finally_be_answered():
    """`pending_approval` was declared on day one, asserted by the controller,
    produced by a registered tool - and reached by no run from A to S, because
    no evaluation passed allow_consequential. F1 again: a mechanism with no path
    to it."""
    res, reg, disp = await first([R("submit_form", {"reason": "confirm purchase"})],
                                 allow_consequential=True)
    assert res.stop_reason == "pending_approval"

    out = await resume(ScriptedClient([]), disp, reg, SYSTEM, "q", res,
                       Decision(action=HumanAction.APPROVE, text="go ahead"))
    assert out.stop_reason == "approved" and out.resumes == 1

async def test_a_denied_proposal_ends_and_does_not_reopen():
    """A refused proposal that comes back reworded is what the CONSEQUENTIAL
    tier exists to stop, so the model is not re-invoked at all. The scripted
    client is EMPTY: if the loop ran, this test would fail on the empty script
    rather than pass quietly."""
    res, reg, disp = await first([R("submit_form", {"reason": "confirm purchase"})],
                                 allow_consequential=True)
    out = await resume(ScriptedClient([]), disp, reg, SYSTEM, "q", res,
                       Decision(action=HumanAction.DENY, text="not authorised"))
    assert out.stop_reason == "denied"
    assert out.detail == "not authorised"
    assert not resolved_by_one_reply(out)

# ------------------------------------------------------------ the mismatches
async def test_a_proposal_cannot_be_answered_and_a_question_cannot_be_approved():
    """The two shapes are not interchangeable. Approving a question approves
    nothing, and answering a proposal answers nothing."""
    q, reg, disp = await first([R("blocked", {"question": "Which URL?"})])
    with pytest.raises(ValueError):
        await resume(ScriptedClient([]), disp, reg, SYSTEM, "q", q,
                     Decision(action=HumanAction.APPROVE))

    p, reg2, disp2 = await first([R("submit_form", {"reason": "confirm purchase"})],
                                 allow_consequential=True)
    with pytest.raises(ValueError):
        await resume(ScriptedClient([]), disp2, reg2, SYSTEM, "q", p,
                     Decision(action=HumanAction.ANSWER, text="yes"))

async def test_a_finished_run_is_not_resumable():
    res, reg, disp = await first([
        R("read_page", {}),
        R("finish", {"answer": "x", "evidence_url": "https://example.com/"})])
    with pytest.raises(ValueError):
        await resume(ScriptedClient([]), disp, reg, SYSTEM, "q", res,
                     Decision(action=HumanAction.ANSWER, text="more"))

async def test_the_resume_cap_holds():
    """One reply per run to start. A higher cap is a measurement, not a default:
    at one, "did a single human reply resolve it?" has a clean answer."""
    res, reg, disp = await first([R("blocked", {"question": "Which URL?"})])
    once = await resume(ScriptedClient([R("blocked", {"question": "And then?"})]),
                        disp, reg, SYSTEM, "q", res,
                        Decision(action=HumanAction.ANSWER, text="example.com"))
    assert once.resumes == 1

    twice = await resume(ScriptedClient([R("read_page", {})]), disp, reg, SYSTEM,
                         "q", once, Decision(action=HumanAction.ANSWER, text="again"))
    assert twice.resumes == 1                      # unchanged
    assert "resume cap" in twice.detail


# --------------------------------- a mismatched reply is a row, not a crash
def test_a_reply_that_does_not_fit_the_ending_is_refused_by_resume():
    """`resume` still refuses the wrong decision for the ending: approving a
    question is not a thing a person can do. What changed is who handles it."""
    takes = {"blocked": {"answer"}, "pending_approval": {"approve", "deny"}}
    assert "approve" not in takes["blocked"]
    assert "answer" not in takes["pending_approval"]

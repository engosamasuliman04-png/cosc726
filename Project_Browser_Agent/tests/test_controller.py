"""Stop reasons, the grounding guard, and derived fields.

Every exit from run_agent names a stop reason, and a cap is a legitimate outcome,
not a crash. The four cap endings carry four different names because they are
four different failures.
"""

import pytest

from browser_agent import (SYSTEM, ToolCall, build_agent, is_capped,
                           run_agent, system_for)
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

# ------------------------------------------------------ the merged stop tool
@pytest.mark.parametrize("reason_type,expected", [
    ("answered",   "complete"),
    ("need_info",  "blocked"),
    ("not_my_job", "out_of_scope"),
])
async def test_stop_maps_each_reason_to_its_own_terminal(reason_type, expected):
    """Three tools become one tool and a field. The stop reasons are unchanged -
    only how the model selects them moves, from picking a tool to filling a slot
    the schema checks."""
    _, reg, disp = build_agent(FakePage("https://example.com/"), ALLOW,
                               stop_mode="merged")
    assert set(reg) & {"finish", "blocked", "out_of_scope"} == set()
    args = {"reason_type": reason_type, "detail": "because"}
    if reason_type == "answered":
        args["evidence_url"] = "https://example.com/"
    res = await run_agent(ScriptedClient([R("read_page", {}), R("stop", args)]),
                          disp, reg, SYSTEM, "q")
    assert res.stop_reason == expected

async def test_an_invalid_reason_type_names_the_permitted_ones():
    """The point of the merge. A bad tool choice drifts silently to a neighbour;
    a bad FIELD comes back as a schema_violation listing what was allowed, which
    the model reads and can correct. out_of_scope was never selected in nine runs
    under three sets of descriptions - this moves that decision somewhere the
    code can answer."""
    _, reg, disp = build_agent(FakePage("https://example.com/"), ALLOW,
                               stop_mode="merged")
    obs, _ = await disp.dispatch(ToolCall("stop", {"reason_type": "dunno",
                                                  "detail": "x"}))
    assert obs["error"] == "schema_violation"
    assert "not_my_job" in str(obs)

async def test_answered_without_a_url_is_refused():
    _, reg, disp = build_agent(FakePage("https://example.com/"), ALLOW,
                               stop_mode="merged")
    obs, _ = await disp.dispatch(ToolCall("stop", {"reason_type": "answered",
                                                  "detail": "the heading"}))
    assert obs["error"] == "schema_violation"

@pytest.mark.parametrize("reason_type,expected", [
    ("need_info", "blocked"), ("not_my_job", "out_of_scope"),
])
async def test_hybrid_keeps_finish_and_merges_only_the_refusals(reason_type, expected):
    """The shape the measurements point at: the field where the choice is hard
    (two refusals that look alike), the verb where it is not (answering)."""
    _, reg, disp = build_agent(FakePage("https://example.com/"), ALLOW,
                               stop_mode="hybrid")
    assert "finish" in reg and "stop" in reg
    assert {"blocked", "out_of_scope"} & set(reg) == set()
    res = await run_agent(ScriptedClient([R("read_page", {}),
        R("stop", {"reason_type": reason_type, "detail": "because"})]),
        disp, reg, SYSTEM, "q")
    assert res.stop_reason == expected

async def test_hybrid_stop_cannot_be_used_to_answer():
    """'answered' is not in the hybrid enum: answering goes through finish, which
    carries the evidence fields. One way to claim an answer, not two."""
    _, reg, disp = build_agent(FakePage("https://example.com/"), ALLOW,
                               stop_mode="hybrid")
    obs, _ = await disp.dispatch(ToolCall("stop", {"reason_type": "answered",
                                                  "detail": "x"}))
    assert obs["error"] == "schema_violation"

async def test_the_prompt_lists_exactly_the_registry():
    """A hand-written tool list can advertise a tool the registry lacks - F1 one
    level up. With two registry shapes, a fixed list would be wrong for one of
    them by construction, so it is rendered from the registry instead."""
    _, plain, _ = build_agent(FakePage("https://example.com/"), ALLOW)
    _, merged, _ = build_agent(FakePage("https://example.com/"), ALLOW,
                               stop_mode="merged")
    assert "stop(" in system_for(merged) and "finish(" not in system_for(merged)
    assert "finish(" in system_for(plain) and "stop(" not in system_for(plain)

# --------------------------------------------------- the verification gate
async def quoting(answer, quote):
    _, reg, disp = build_agent(FakePage("https://example.com/"), ALLOW,
                               require_quote=True)
    return await run_agent(ScriptedClient([
        R("read_page", {}),
        R("finish", {"answer": answer, "evidence_url": "https://example.com/",
                     "evidence_quote": quote})]), disp, reg, SYSTEM, "q")

async def test_an_invented_quote_is_refused():
    """Run F's fabrication, reproduced: an answer that appears in no tool result,
    with a quote invented to match it. Lexical overlap was measured first and
    could not separate this from a true answer - it scored 0.0 for both - so the
    check is an exact substring instead, which needs no threshold."""
    res = await quoting("example.com is available for registration.",
                        "example.com is available for registration")
    assert res.trace[1]["obs"]["error"] == "quote_not_observed"

async def test_a_copied_quote_passes_including_whitespace_and_case():
    """A quote is matched against the TEXT, not its layout; a model that copies
    correctly but spaces differently is not punished for it."""
    for q in ["Example Domain", "  example   DOMAIN "]:
        res = await quoting("The heading is Example Domain.", q)
        assert res.stop_reason == "complete" and len(res.evidence) == 1

async def test_the_gate_checks_existence_not_support():
    """The limit, asserted so it is not mistaken for something stronger: a REAL
    quote pasted beside a FALSE claim still passes. This gate raises the cost of
    inventing - the words must have been shown - but it does not read the answer
    against the quote. That needs a judge, and is not built."""
    res = await quoting("example.com is available for registration.",
                        "This domain is for use")
    assert res.stop_reason == "complete"

# ------------------------------------- the quote and the URL must agree
async def two_pages_then_finish(url, quote, quote_same_page=True):
    """Read page 1, navigate, read page 2, then cite. Both pages' text is in
    `observed`, which is the situation the merged check could not see."""
    _, reg, disp = build_agent(FakePage("https://example.com/"), ALLOW,
                               require_quote=True,
                               quote_same_page=quote_same_page)
    return await run_agent(ScriptedClient([
        R("read_page", {}),
        R("open_url", {"url": "https://www.iana.org/help/example-domains"}),
        R("read_page", {}),
        R("finish", {"answer": "a", "evidence_url": url,
                     "evidence_quote": quote})]), disp, reg, SYSTEM, "q",
        max_steps=6)

async def test_a_quote_from_another_page_passes_the_merged_check():
    """The hole, demonstrated before it is closed. `seen_text()` concatenated every
    page, so "RFC 2606 reserves" - text from the IANA page - passed beside an
    evidence_url pointing at example.com. The citation names a page that does not
    contain the sentence, and the gate said yes."""
    res = await two_pages_then_finish("https://example.com/", "RFC 2606 reserves",
                                      quote_same_page=False)
    assert res.stop_reason == "complete"

async def test_a_quote_must_be_on_the_page_it_cites():
    """Closed. The claim is "this sentence is on THAT url", so the text of that
    url is what it is checked against - not the union of everything ever read."""
    res = await two_pages_then_finish("https://example.com/", "RFC 2606 reserves")
    assert res.trace[3]["obs"]["error"] == "quote_not_on_cited_page"

    # ...and the same quote with the right URL is accepted, so the gate narrows
    # the check rather than forbidding multi-page work.
    res = await two_pages_then_finish(
        "https://www.iana.org/help/example-domains", "RFC 2606 reserves")
    assert res.stop_reason == "complete" and len(res.evidence) == 1

async def test_citing_a_page_that_was_never_read_is_refused_by_name():
    """A URL inside the allowlist that no READ tool ever returned. The old merged
    check could not express this at all, because it never looked at the URL."""
    res = await two_pages_then_finish("https://example.com/pricing",
                                      "Example Domain")
    obs = res.trace[3]["obs"]
    assert obs["error"] == "evidence_url_not_observed"
    assert "example.com" in obs["detail"]          # names the pages it did read

async def test_a_trailing_slash_does_not_invalidate_a_citation():
    """A gate that refuses a correct citation over punctuation is worse than the
    hole it closes. The comparison is normalised: fragment dropped, case folded,
    trailing slash ignored. A query string is NOT ignored - `?page=2` is a
    different page.

    `HTTPS://EXAMPLE.COM/` is absent deliberately: `FinishArgs.evidence_url`
    requires a lowercase `https://` prefix, so an upper-cased scheme never reaches
    this gate - it is refused one gate earlier, by the schema. Folding case here
    is for the host, which a redirect can return capitalised.
    """
    for url in ["https://example.com", "https://example.com/",
                "https://EXAMPLE.com/#main"]:
        _, reg, disp = build_agent(FakePage("https://example.com/"), ALLOW,
                                   require_quote=True, quote_same_page=True)
        res = await run_agent(ScriptedClient([
            R("read_page", {}),
            R("finish", {"answer": "The heading is Example Domain.",
                         "evidence_url": url,
                         "evidence_quote": "Example Domain"})]),
            disp, reg, SYSTEM, "q")
        assert res.stop_reason == "complete", url

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

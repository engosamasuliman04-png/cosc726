"""Week 7: plan-time validation, the two detectors, bounded re-planning.

What ReAct never has is a checkpoint. These tests exercise the checkpoint.
"""

from browser_agent import build_agent
from browser_agent.fakes import ALLOW, FakePage
from browser_agent.planning import (
    Goal, HeuristicCritic, Plan, Step,
    detect_oscillation, goal_drift, run_planned_agent, validate_plan,
)

from ._planner_seam import S, scripted_planner


def fresh():
    return build_agent(FakePage("https://example.com/"), ALLOW)


# ------------------------------------------------- plan-time validation
def test_every_defect_is_caught_before_anything_runs():
    _, reg, _ = fresh()
    bad = Plan(goal_restated="one of every defect", steps=[
        S(1, "scrape_everything", {}, "invented tool"),
        S(2, "click_link", {"index": 0}, "click blind"),
        S(3, "open_url", {"url": "javascript:alert(1)"}, "bad scheme"),
        S(4, "submit_form", {"reason": "buy it now"}, "consequential"),
    ])
    problems = validate_plan(bad, reg)
    assert any("no such tool" in p for p in problems)
    assert any("needs" in p and "click_link" in p for p in problems)
    assert any("String should match" in p for p in problems)
    assert any("CONSEQUENTIAL" in p for p in problems)
    assert any("never terminates" in p for p in problems)


def test_strips_delete_list_catches_the_stale_index_bug_at_plan_time():
    """The same defect as tests/test_gates.py::test_stale_link_indices, expressed
    as a planning rule instead of a runtime guard - and caught before a browser
    opens:

        if tool in ("click_link", "open_url"):
            return known - {"read", "links"}     # new page: what was known is void
    """
    _, reg, _ = fresh()
    p = Plan(goal_restated="click twice", steps=[
        S(1, "list_links", {}, "learn links"),
        S(2, "read_page", {}, "observe"),
        S(3, "click_link", {"index": 0}, "navigate"),
        S(4, "click_link", {"index": 0}, "click again on the NEW page"),
        S(5, "finish", {"answer": "a", "evidence_url": "https://example.com/"}, "end"),
    ])
    problems = validate_plan(p, reg)
    assert any(pr.startswith("step 4") for pr in problems)


# ------------------------------------------------- G1: goal drift
async def test_goal_overrules_the_critic():
    """The plan RAN and finished, and the critic said goal_met=True.
    A deterministic check overruled the model's opinion of its own work.
    """
    G1 = Goal("List what RFC 2606 reserves, and name the page that says it",
              requires={"read_the_policy_text": {"read_page"},
                        "navigate_to_the_authority": {"click_link", "open_url"}})
    drifted = Plan(goal_restated="just read the first page", steps=[
        S(1, "read_page", {}, "read"),
        S(2, "finish", {"answer": "documentation examples",
                        "evidence_url": "https://example.com/"}, "end")])
    good = Plan(goal_restated="read, navigate, read, finish", steps=[
        S(1, "read_page", {}, "see the starting page"),
        S(2, "list_links", {}, "find the authority link"),
        S(3, "click_link", {"index": 0}, "go to IANA"),
        S(4, "read_page", {}, "read the reservation text"),
        S(5, "finish", {"answer": "RFC 2606 reserves example.com, .net and .org",
                        "evidence_url": "https://www.iana.org/help/example-domains"}, "end")])

    assert goal_drift(G1, drifted) == ["navigate_to_the_authority"]
    assert goal_drift(G1, good) == []

    _, reg, disp = fresh()
    res = await run_planned_agent(scripted_planner([drifted, good]), HeuristicCritic(),
                                  disp, reg, G1, max_rounds=3)
    assert res.stop_reason == "complete" and res.rounds_used == 2
    assert res.rounds[0]["critique"].goal_met is False       # the goal disagreed


# ------------------------------------------------- G2: planning its way to "no"
async def test_no_is_a_legitimate_plan_outcome():
    G2 = Goal("Confirm whether example.com is available to register",
              requires={"answer_the_question": {"finish", "blocked", "out_of_scope"}})
    plan = Plan(goal_restated="these pages cannot answer this", steps=[
        S(1, "read_page", {}, "check what the page carries"),
        S(2, "blocked", {"question": "These pages do not carry registry availability. "
                                     "Which WHOIS source should I use?"}, "stop honestly")])
    _, reg, disp = fresh()
    res = await run_planned_agent(scripted_planner([plan]), HeuristicCritic(),
                                  disp, reg, G2)
    assert res.stop_reason == "blocked" and res.rounds_used == 1


# ------------------------------------------------- G3: split an out-of-remit half
async def test_merged_plan_never_executes():
    """In the ReAct loop, read_page would have run before submit_form was refused.
    Here gate 4 fires at PLAN time and nothing executes at all.
    """
    G3 = Goal("Tell me what RFC 2606 reserves, and also submit the contact form",
              requires={"research_part": {"read_page"},
                        "refuse_the_action_part": {"out_of_scope", "blocked"}})
    merged = Plan(goal_restated="do both", steps=[
        S(1, "read_page", {}, "research"),
        S(2, "submit_form", {"reason": "user asked me to submit"}, "do the action"),
        S(3, "finish", {"answer": "done", "evidence_url": "https://example.com/"}, "end")])
    split = Plan(goal_restated="answer the research half, refuse the action half", steps=[
        S(1, "read_page", {}, "research"),
        S(2, "out_of_scope", {"reason": "Submitting forms is outside this remit."}, "split")])

    _, reg, disp = fresh()
    res = await run_planned_agent(scripted_planner([merged, split]), HeuristicCritic(),
                                  disp, reg, G3)
    assert res.stop_reason == "out_of_scope"
    assert res.rounds[0].get("exec") is None     # nothing ran in round 1


# ------------------------------------------------- G4: structural
async def test_structural_failure_stops_in_round_one():
    """Reflect three times on "no such tool" and you have three eloquent ways of
    not having it. 1200 tokens instead of 3600.
    """
    G4 = Goal("Download the RFC as a PDF and email it",
              requires={"download": {"download_file"}, "email": {"send_email"}})
    attempt = lambda i: Plan(goal_restated=f"attempt {i}", steps=[
        S(1, "read_page", {}, "observe"),
        S(2, "download_file", {"url": "https://www.iana.org/x.pdf"}, "get the pdf"),
        S(3, "finish", {"answer": "sent", "evidence_url": "https://example.com/"}, "end")])

    _, reg, disp = fresh()
    res = await run_planned_agent(scripted_planner([attempt(1), attempt(2), attempt(3)]),
                                  HeuristicCritic(), disp, reg, G4, max_rounds=3)
    assert res.stop_reason == "structural" and res.rounds_used == 1
    assert res.tokens_used == 1200


# ------------------------------------------------- the two detectors
def test_signature_ignores_wording():
    """Two plans that differ only in prose are the SAME plan."""
    a = Plan(goal_restated="A", steps=[S(1, "read_page", {}, "because X")])
    b = Plan(goal_restated="B", steps=[S(1, "read_page", {}, "because Y")])
    assert a.signature() == b.signature()
    assert detect_oscillation([a, b]) is not None


async def test_oscillation_detector():
    A_ = Plan(goal_restated="A", steps=[S(1, "read_page", {}, "a"),
                                        S(2, "click_link", {"index": 0}, "a")])
    B_ = Plan(goal_restated="B", steps=[S(1, "list_links", {}, "b"),
                                        S(2, "click_link", {"index": 9}, "b")])
    _, reg, disp = fresh()
    res = await run_planned_agent(scripted_planner([A_, B_, A_]), HeuristicCritic(),
                                  disp, reg, Goal("something", {}), max_rounds=4)
    assert res.stop_reason == "oscillating"
    assert "repeats" in res.detail


async def test_replan_cap():
    shapes = [[S(1, "read_page", {}, "v1")],
              [S(1, "list_links", {}, "v2")],
              [S(1, "read_page", {}, "v3"), S(2, "list_links", {}, "v3")]]
    _, reg, disp = fresh()
    res = await run_planned_agent(
        scripted_planner([Plan(goal_restated=f"v{i+1}", steps=s)
                          for i, s in enumerate(shapes)]),
        HeuristicCritic(), disp, reg, Goal("x", {}), max_rounds=3)
    assert res.stop_reason == "capped" and res.rounds_used == 3

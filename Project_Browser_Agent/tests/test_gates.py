"""The four gates, driven directly with the calls a bad model would make.

A well-behaved model may simply never make the mistake a gate exists to catch.
That is why these drive the dispatcher rather than a model.
"""

import pytest

from browser_agent import ToolCall, build_agent
from browser_agent.fakes import ALLOW, FakePage

# ---------------------------------------------------------------- gate 1
@pytest.mark.parametrize("call,expected", [
    (ToolCall(123, {}),                 "malformed_call"),
    (ToolCall("read_page", "x"),        "malformed_call"),
    (ToolCall("drop_tables", {}),       "unknown_tool"),
])
async def test_gate1_parses(agent, call, expected):
    _, _, disp = agent
    obs, _ = await disp.dispatch(call)
    assert obs["error"] == expected

# ---------------------------------------------------------------- gate 2
@pytest.mark.parametrize("call", [
    ToolCall("click_link", {"index": "one"}),                              # wrong type
    ToolCall("open_url", {"url": "https://example.com/", "admin": 1}),     # extra field
    ToolCall("open_url", {"url": "javascript:alert(1)"}),                  # bad scheme
    ToolCall("finish", {"answer": "x"}),                                   # no evidence
])
async def test_gate2_conforms(agent, call):
    _, _, disp = agent
    obs, _ = await disp.dispatch(call)
    assert obs["error"] == "schema_violation"

async def test_gate2_finish_requires_evidence_url(agent):
    """An answer with no source cannot even be proposed."""
    _, _, disp = agent
    obs, _ = await disp.dispatch(ToolCall("finish", {"answer": "reserved"}))
    assert obs["error"] == "schema_violation"
    assert "evidence_url" in obs["detail"]

# ---------------------------------------------------------------- gate 3
async def test_gate3_click_before_list_links(agent):
    _, _, disp = agent
    obs, _ = await disp.dispatch(ToolCall("click_link", {"index": 0}))
    assert obs["error"] == "no_links_known"

async def test_gate3_index_out_of_range(agent):
    _, _, disp = agent
    await disp.dispatch(ToolCall("list_links", {}))
    obs, _ = await disp.dispatch(ToolCall("click_link", {"index": 9}))
    assert obs["error"] == "index_out_of_range"

async def test_gate3_domain_allowlist(agent):
    """What makes the blast radius finite: no reasoning gets the agent off the list."""
    _, _, disp = agent
    obs, _ = await disp.dispatch(ToolCall("open_url", {"url": "https://attacker.test/x"}))
    assert obs["error"] == "domain_not_allowed"

# ---------------------------------------------------------------- gate 4
async def test_gate4_consequential_needs_approval(agent):
    _, _, disp = agent
    obs, tier = await disp.dispatch(ToolCall("submit_form", {"reason": "confirm purchase"}))
    assert obs["error"] == "requires_human_approval"
    assert tier.value == "consequential"

async def test_a_refusal_says_how_to_fix_it(agent):
    """The hint used to be `expected: {json of properties}`, which for an
    argument-less tool rendered as `expected: {}`. Measured consequence: the
    model called read_page(url=...), was refused, read an empty object, decided
    the tool could not retrieve the page, and ended the task out_of_scope with
    "Unable to retrieve page content due to tool limitations" - reading its own
    malformed call as a fact about the world.

    A gate that refuses without naming the fix spends a turn and teaches nothing.
    """
    _, _, disp = agent
    obs, _ = await disp.dispatch(ToolCall("read_page", {"url": "https://x.com"}))
    assert obs["error"] == "schema_violation"
    assert "takes no arguments" in obs["hint"]

    obs, _ = await disp.dispatch(ToolCall("open_url", {}))
    assert "url: string" in obs["hint"]

async def test_a_refused_enum_names_the_permitted_values():
    """Pydantic hides an Enum behind a $ref into $defs, so the permitted values -
    the one thing worth saying - are absent unless the reference is followed."""
    _, _, disp = build_agent(FakePage("https://example.com/"), ALLOW,
                             stop_mode="hybrid")
    obs, _ = await disp.dispatch(ToolCall("stop", {"reason_type": "dunno",
                                                  "detail": "x"}))
    assert "'need_info'" in obs["hint"] and "'not_my_job'" in obs["hint"]

async def test_gate4_write_before_read_covers_open_url_too(agent):
    """F1, closed. The rule existed in the prompt and the gate guarded click_link
    only, so the first real-model run left an unread page via open_url and nothing
    objected. Both WRITE tools are the same mistake: acting on a page never seen."""
    tools, reg, disp = agent
    obs, _ = await disp.dispatch(ToolCall("open_url", {"url": "https://iana.org/"}))
    assert obs["error"] == "write_before_read"

    # After a read the gate stands aside. What navigation then does is the
    # browser's business, not this gate's - asserting on it would couple this
    # test to the network.
    await disp.dispatch(ToolCall("read_page", {}))
    obs, _ = await disp.dispatch(ToolCall("open_url", {"url": "https://iana.org/"}))
    assert obs.get("error") != "write_before_read"

async def test_gate4_write_before_read(agent):
    tools, _, disp = agent
    await disp.dispatch(ToolCall("list_links", {}))
    tools.observed[tools.page.url]["read"] = False      # links known, page not observed
    obs, _ = await disp.dispatch(ToolCall("click_link", {"index": 0}))
    assert obs["error"] == "write_before_read"

# ---------------------------------------------------------------- regression
async def test_stale_link_indices():
    """An early version kept links in ONE flat list, so after navigating, the previous
    page's links were still what gate 3 checked against. A gate that returns "fine"
    while checking stale data is worse than no gate: it gives false confidence.

    The fix was structural (`observed[url]`), not another check - the wrong state
    became impossible to express.
    """
    tools, _, disp = build_agent(FakePage("https://example.com/"), ALLOW)
    await disp.dispatch(ToolCall("list_links", {}))         # page 1: one link
    await disp.dispatch(ToolCall("read_page", {}))
    await disp.dispatch(ToolCall("click_link", {"index": 0}))
    assert tools.page.url == "https://www.iana.org/help/example-domains"

    obs, _ = await disp.dispatch(ToolCall("click_link", {"index": 0}))   # page 2: no links
    assert obs["error"] == "no_links_known"

# ------------------------------------------------- every refusal names a fix
async def test_every_gate_refusal_carries_a_hint(agent):
    """F11, the half that was left. The hint on `schema_violation` was fixed and
    the ten GATE refusals were not, so they named the problem and never the fix.

    Measured consequence, three runs out of three: told `write_before_read: the
    current page has not been observed yet`, the agent retried open_url, then
    answered in prose twice and was stopped by the grounding guard - without once
    calling read_page, the only tool that clears that gate. Five steps spent
    against a message that was accurate and unusable.
    """
    _, _, disp = agent
    for call in [ToolCall("open_url", {"url": "https://iana.org/"}),
                 ToolCall("click_link", {"index": 0}),
                 ToolCall("open_url", {"url": "https://attacker.test/x"}),
                 ToolCall("submit_form", {"reason": "confirm the purchase"})]:
        obs, _ = await disp.dispatch(call)
        assert obs.get("hint"), f"{obs['error']} refuses without saying what to do"

async def test_the_write_gate_names_the_call_that_clears_it():
    """Not "you have not observed the page" - which the agent read twice and did
    not act on - but the name of the tool to call."""
    _, _, disp = build_agent(FakePage("https://example.com/"), ALLOW)
    obs, _ = await disp.dispatch(ToolCall("open_url", {"url": "https://iana.org/"}))
    assert "read_page" in obs["hint"]

def test_no_gate_can_be_added_without_a_hint():
    """Structural, so the NEXT gate cannot repeat this. Reads the source and
    fails if any GateError is constructed with fewer than three arguments - a
    test that a passing behaviour test would not have caught, because a gate
    that does not exist yet refuses nothing yet.
    """
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "src/browser_agent/dispatcher.py"
    tree = ast.parse(src.read_text())
    raises = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "GateError"]
    assert raises, "no GateError raises found - did the file move?"
    missing = [ast.get_source_segment(src.read_text(), n)[:40]
               for n in raises if len(n.args) < 3]
    assert not missing, f"GateError raised without a hint: {missing}"

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

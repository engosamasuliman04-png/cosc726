"""Shared fixtures. `pytest` finds this automatically."""

import pytest

from browser_agent import build_agent
from browser_agent.fakes import ALLOW, FakePage

@pytest.fixture
def agent():
    """A dispatcher wired to the fake start page. Returns (tools, registry, dispatcher)."""
    return build_agent(FakePage("https://example.com/"), ALLOW)

@pytest.fixture
def hostile_agent():
    """The same, starting on the page that carries an injected instruction."""
    return build_agent(FakePage("https://evil.example.com/"), ALLOW)

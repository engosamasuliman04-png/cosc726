"""Autonomous Web Browser Agent - COSC726, Al-Neelain University.

    from browser_agent import build_agent, run_agent, report, SYSTEM
    from browser_agent.clients import HeuristicClient
    from browser_agent.fakes import FakePage, ALLOW
"""

from .tiers import (
    Tier, NoArgs, OpenUrlArgs, ClickLinkArgs, SubmitFormArgs,
    FinishArgs, BlockedArgs, OutOfScopeArgs, TERMINAL_REASONS,
)
from .tools import BrowserTools, obs_err
from .registry import ToolSpec, ToolCall, build_registry
from .dispatcher import Dispatcher, GateError
from .prompt import SYSTEM
from .clients import (
    Usage, Reply, ScriptedClient, HeuristicClient, keywords, goal_coverage,
)
from .controller import RunResult, run_agent, report, build_agent

__all__ = [
    "Tier", "NoArgs", "OpenUrlArgs", "ClickLinkArgs", "SubmitFormArgs",
    "FinishArgs", "BlockedArgs", "OutOfScopeArgs", "TERMINAL_REASONS",
    "BrowserTools", "obs_err",
    "ToolSpec", "ToolCall", "build_registry",
    "Dispatcher", "GateError",
    "SYSTEM",
    "Usage", "Reply", "ScriptedClient", "HeuristicClient", "keywords", "goal_coverage",
    "RunResult", "run_agent", "report", "build_agent",
]

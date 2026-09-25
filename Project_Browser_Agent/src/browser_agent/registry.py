"""Tool registry: the allow-list of everything the model may call.

`ToolSpec.schema` is a property derived from the Pydantic model, so the
declaration the model sees and the validator that checks its reply cannot drift
apart. You never hand-write a tool schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel

from .tiers import (
    Tier, NoArgs, OpenUrlArgs, ClickLinkArgs, SubmitFormArgs,
    FinishArgs, BlockedArgs, OutOfScopeArgs,
)
from .tools import BrowserTools, t_finish, t_blocked, t_out_of_scope

@dataclass
class ToolSpec:
    fn: Callable
    tier: Tier
    args_model: type[BaseModel]
    description: str

    @property
    def schema(self) -> dict:
        return self.args_model.model_json_schema()


@dataclass
class ToolCall:
    name: str
    args: dict
    thought: str = ""          # the ReAct "Thought", carried on the call itself


def build_registry(tools: BrowserTools) -> dict[str, ToolSpec]:
    return {
        "read_page":    ToolSpec(tools.read_page, Tier.READ, NoArgs,
            "Read the visible text of the current page. Read-only. Call first on any new page."),
        "list_links":   ToolSpec(tools.list_links, Tier.READ, NoArgs,
            "List clickable links with indices. Read-only. Required before click_link."),
        "open_url":     ToolSpec(tools.open_url, Tier.WRITE, OpenUrlArgs,
            "Navigate to an absolute https URL on the allowlist."),
        "click_link":   ToolSpec(tools.click_link, Tier.WRITE, ClickLinkArgs,
            "Click the link with the given index from the last list_links on THIS page."),
        "submit_form":  ToolSpec(tools.submit_form, Tier.CONSEQUENTIAL, SubmitFormArgs,
            "PROPOSE a form submission. Submits nothing; creates a pending request."),
        "finish":       ToolSpec(t_finish, Tier.CONTROL, FinishArgs,
            "End the run with an answer and the URL you observed it on."),
        "blocked":      ToolSpec(t_blocked, Tier.CONTROL, BlockedArgs,
            "End the run by asking the user ONE question you cannot resolve yourself."),
        "out_of_scope": ToolSpec(t_out_of_scope, Tier.CONTROL, OutOfScopeArgs,
            "End the run when the request is not this agent's job."),
    }

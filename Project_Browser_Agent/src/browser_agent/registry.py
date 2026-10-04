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
    FinishArgs, BlockedArgs, OutOfScopeArgs, StopArgs, RefusalArgs,
)
from .tools import BrowserTools, t_finish, t_blocked, t_out_of_scope, t_stop

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


def build_registry(tools: BrowserTools, stop_mode: str = "split") -> dict[str, ToolSpec]:
    """How the run is allowed to end. All three shapes are kept, because each is
    a measured condition and a comparison needs its alternatives.

        split   finish / blocked / out_of_scope          out_of_scope never chosen
        merged  stop(answered|need_info|not_my_job)      fixed T4, broke T1 and T2
        hybrid  finish + stop(need_info|not_my_job)      the two results combined
    """
    assert stop_mode in ("split", "merged", "hybrid"), stop_mode
    reg = {
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
        # EXPERIMENT B. The three CONTROL tools were described by their mechanical
        # effect - "End the run when the request is not this agent's job" - which
        # states WHAT the tool does and leaves WHEN to infer from <scope>, further
        # up the prompt. In the baseline the model selected `blocked` and never
        # `finish` or `out_of_scope`, and the hypothesis is that `blocked`'s
        # trigger is readable from the goal itself while the other two are not.
        # These say WHEN, in the goal's own vocabulary. Nothing else changed.
        "finish":       ToolSpec(t_finish, Tier.CONTROL, FinishArgs,
            "Use as soon as a tool result contains the answer. Pass the answer, the "
            "URL it came from, and evidence_quote: the exact words from the page "
            "that support it, copied not paraphrased."),
        # EXPERIMENT D. B's descriptions said WHEN, and behaviour moved: T4 went
        # from repeating itself to selecting a control tool. It selected the wrong
        # one. `blocked` read "something no public page can tell you", which is
        # true of "submit the contact form" as well, so the broader description
        # absorbed a case belonging to its neighbour. Tool descriptions are not
        # independent: widening one takes cases from another.
        #
        # The dividing line here is KNOWING versus DOING, stated on both sides.
        # Prediction, written before the run: T4 moves to out_of_scope, T3 stays
        # blocked, strict rises to 2/4. If T3 also moves, this boundary is too
        # sharp and the narrowing broke a case that was already correct.
        "blocked":      ToolSpec(t_blocked, Tier.CONTROL, BlockedArgs,
            "Use when the goal asks for a FACT that no public page states - live "
            "availability, the contents of a private account, something only the "
            "user knows. Not for goals that ask you to DO something."),
        "out_of_scope": ToolSpec(t_out_of_scope, Tier.CONTROL, OutOfScopeArgs,
            "Use when the goal asks you to DO something rather than find something "
            "out: shopping, signing in, posting, filling in or submitting a form."),
    }
    if stop_mode == "merged":
        for name in ("finish", "blocked", "out_of_scope"):
            reg.pop(name)
        reg["stop"] = ToolSpec(t_stop, Tier.CONTROL, StopArgs,
            "End the run. reason_type: 'answered' when a tool result holds the "
            "answer - then give evidence_url and evidence_quote copied from the "
            "page; 'need_info' when no public page states what the goal asks; "
            "'not_my_job' when the goal asks you to shop, sign in, post, or fill "
            "in a form.")
    elif stop_mode == "hybrid":
        for name in ("blocked", "out_of_scope"):
            reg.pop(name)
        reg["stop"] = ToolSpec(t_stop, Tier.CONTROL, RefusalArgs,
            "End the run WITHOUT answering. reason_type: 'need_info' when no "
            "public page states what the goal asks; 'not_my_job' when the goal "
            "asks you to shop, sign in, post, or fill in a form. To ANSWER, use "
            "finish instead.")
    return reg

"""Blast-radius tiers and the argument models every tool call is validated against.

A type hint is not a constraint. `index: int` says "a whole number";
`Field(ge=0, le=29)` says "a whole number this agent is allowed to send".
`extra="forbid"` is JSON Schema's `additionalProperties: false` — every loose
field is a field the model may fill creatively.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

class Tier(str, Enum):
    READ = "read"
    WRITE = "write"
    CONSEQUENTIAL = "consequential"
    CONTROL = "control"        # ends the run; never touches the world

# A stop reason that aggregates unlike mechanisms is not a stop reason.
# `capped` used to cover four different endings - a spent token budget, a blown
# deadline, a repeated call, and an exhausted turn cap - so a failed run said
# "capped" and left you to infer WHICH from step counts and clocks. The four are
# named separately now; CAP_REASONS keeps "did it cap at all?" askable in one go.
CAP_REASONS = {"capped_steps", "capped_tokens", "capped_time", "no_progress"}

TERMINAL_REASONS = ({"complete", "blocked", "out_of_scope", "pending_approval",
                     "unterminated", "ungrounded", "approved", "denied"}
                    | CAP_REASONS)

# The two guard endings. Neither is a choice the agent made - each is a refusal
# it failed to act on twice - so neither should be read as the agent's judgement.
# `ungrounded` was called `blocked` until an ablation that REMOVED the blocked
# tool still reported `blocked`, and the loose metric counted it as success.
GUARD_REASONS = {"ungrounded", "unterminated"}

# Two of the endings above are addressed to a PERSON and the rest are not.
# `blocked` carries a question; `pending_approval` carries a proposal. Both were
# dead ends: the run stopped, the question was never answered, the proposal was
# never approved. A run that ends here is not finished - it is waiting.
HUMAN_REASONS = {"blocked", "pending_approval"}

# `approved` and `denied` are the two answers to a proposal, and both are their
# own endings - not caps, not out_of_scope. A denied run did everything right; a
# person said no. Note what `approved` does NOT mean: nothing was submitted.
# `submit_form` is a proposal tool by construction, so approval records a human
# decision and ends there. Executing the consequential action is outside the
# blast radius this agent is permitted, and pretending otherwise would be the
# `state_changed` lie the tool layer exists to prevent.
def awaits_human(reason: str) -> bool:
    return reason in HUMAN_REASONS


def is_capped(reason: str) -> bool:
    """True for any of the four cap endings. Use instead of `== "capped"`."""
    return reason in CAP_REASONS


class HumanAction(str, Enum):
    APPROVE = "approve"      # answers a pending_approval
    DENY = "deny"            # answers a pending_approval
    ANSWER = "answer"        # answers a blocked question


class Decision(BaseModel):
    """What a person sends back. Validated like any tool call, because an
    answer typed by a human is still untrusted input to the loop.

    `text` is required for ANSWER (an empty answer answers nothing) and optional
    for the other two, where it is the reason attached to the approval or refusal.
    """
    model_config = ConfigDict(extra="forbid")
    action: HumanAction
    text: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _an_answer_needs_words(self):
        if self.action is HumanAction.ANSWER and not self.text.strip():
            raise ValueError("text: action 'answer' needs the answer itself")
        return self

class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

class OpenUrlArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(pattern=r"^https://[^\s]+$")

class ClickLinkArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0, le=29)

class SubmitFormArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=200)

class FinishArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=1000)
    evidence_url: str = Field(pattern=r"^https://[^\s]+$")
    # Declared here, enforced in the dispatcher (`require_quote`), because whether
    # a quote is MANDATORY is a policy and whether it MATCHES needs the observed
    # text - neither belongs in a static schema. Lexical overlap between an answer
    # and the page was measured first and could not tell a true answer from an
    # invented one: it scored 0.0 for both. An exact substring can be checked
    # without a threshold, and cannot be produced for something never shown.
    evidence_quote: str = Field(default="", max_length=300)

class BlockedArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=5, max_length=300)

class OutOfScopeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=300)


class RefusalReason(str, Enum):
    """The two REFUSALS only. `finish` keeps its own tool in the hybrid shape.

    Measured: merging all three fixed T4 (out_of_scope, which nine runs under
    three description sets never produced) and broke T1 and T2, which had been
    answering through `finish` and would not reach stop(reason_type='answered').
    The field helped where the choice was genuinely hard - the two refusals look
    alike and compete - and hurt where it was not: answering resembles nothing
    else, and `finish` is a verb the model acts on directly.
    """
    NEED_INFO = "need_info"
    NOT_MY_JOB = "not_my_job"


class RefusalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_type: RefusalReason
    detail: str = Field(min_length=1, max_length=1000)


class StopReason(str, Enum):
    ANSWERED = "answered"        # -> complete
    NEED_INFO = "need_info"      # -> blocked
    NOT_MY_JOB = "not_my_job"    # -> out_of_scope


class StopArgs(BaseModel):
    """The three control tools as one tool with a validated field.

    Across nine runs and three sets of descriptions, `out_of_scope` was never
    selected once - not even after `blocked` was removed from the registry, which
    sent the model to prose rather than to the remaining tool. Rewriting the
    descriptions moved behaviour every time and never produced that call, so the
    lever is not the wording.

    Selecting among three tools is a judgement this model does not make. Filling
    one field from a closed set is a judgement the SCHEMA makes: an invalid value
    is a `schema_violation` naming the permitted ones, which the model sees and
    can correct, instead of a silent drift to the nearest attractive tool.

    The same move as the quote gate: take the burden off the model's judgement
    and put it on something the code can check.
    """
    model_config = ConfigDict(extra="forbid")
    reason_type: StopReason
    detail: str = Field(min_length=1, max_length=1000)
    evidence_url: str = Field(default="", max_length=500)
    evidence_quote: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _answered_needs_a_url(self):
        # Enforced here rather than by the type, because the requirement depends
        # on another field. `need_info` and `not_my_job` cite nothing by nature.
        if self.reason_type is StopReason.ANSWERED and not re.match(
                r"^https://[^\s]+$", self.evidence_url):
            raise ValueError("evidence_url: reason_type 'answered' requires the "
                             "https URL the answer came from")
        return self

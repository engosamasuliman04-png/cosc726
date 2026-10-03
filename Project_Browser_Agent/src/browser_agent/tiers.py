"""Blast-radius tiers and the argument models every tool call is validated against.

A type hint is not a constraint. `index: int` says "a whole number";
`Field(ge=0, le=29)` says "a whole number this agent is allowed to send".
`extra="forbid"` is JSON Schema's `additionalProperties: false` — every loose
field is a field the model may fill creatively.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

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
                     "unterminated"}
                    | CAP_REASONS)


def is_capped(reason: str) -> bool:
    """True for any of the four cap endings. Use instead of `== "capped"`."""
    return reason in CAP_REASONS

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

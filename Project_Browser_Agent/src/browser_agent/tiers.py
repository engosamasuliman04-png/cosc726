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

TERMINAL_REASONS = {"complete", "blocked", "out_of_scope", "pending_approval", "capped"}

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

class BlockedArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=5, max_length=300)

class OutOfScopeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=300)

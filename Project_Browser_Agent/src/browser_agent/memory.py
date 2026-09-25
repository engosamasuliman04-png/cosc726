"""Week 6: episodic / semantic / procedural memory.

Scope is mandatory on every read and write. The failure that ends projects is
cross-user memory leakage: one shared store, no user_id on the query.

The write path is where the risk lives. A prompt injection lasts one turn; a
poisoned memory persists and comes back looking like something the agent
legitimately learned.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .clients import goal_coverage
from .registry import ToolSpec
from .tiers import Tier
from .tools import BrowserTools, obs_err

Kind = Literal["episodic", "semantic", "procedural"]

@dataclass(frozen=True)
class Scope:
    user_id: str
    agent_id: str = "browser-agent"

    def key(self) -> str:
        return f"{self.agent_id}::{self.user_id}"


@dataclass
class Record:
    id: str
    kind: Kind
    scope_key: str
    text: str
    when: float                      # logical day, so tests are deterministic
    provenance: list                 # URLs / episode ids that justify it
    trust: str = "verified"          # verified | unverified
    importance: float = 0.5
    status: str = "active"           # active | candidate | superseded
    superseded_by: Optional[str] = None
    outcome: Optional[str] = None    # episodic only: did it work?

    def age(self, now: float) -> float:
        return max(0.0, now - self.when)


class WriteRejected(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg
        super().__init__(msg)


class MemoryStore:
    """Keyed by scope. There is no way to read without a scope key."""

    def __init__(self, trusted_domains: set[str], max_text=300):
        self.trusted = trusted_domains
        self.max_text = max_text
        self._by_scope: dict[str, list[Record]] = {}
        self.write_log: list = []        # every attempt, accepted or not

    # ---- write gates ----
    def _gate_write(self, kind, scope, text, provenance, status):
        if not isinstance(scope, Scope) or not scope.user_id:
            raise WriteRejected("no_scope", "every write must carry a scope key")
        if not text or len(text) > self.max_text:
            raise WriteRejected("bad_text", f"text must be 1..{self.max_text} chars")
        if not provenance:
            # slide 17: a remembered 'fact' with no source and no timestamp
            raise WriteRejected("no_provenance",
                                "a fact with no source is a liability, not a memory")
        if kind == "procedural" and status != "candidate":
            raise WriteRejected("unreviewed_procedure",
                                "a procedure enters as 'candidate' and needs review")

    def _trust_of(self, provenance) -> str:
        """Anything sourced from outside the allowlist is untrusted by construction.

        NOTE — deliberately stricter than the navigation allowlist in v4's gate 3.
        There, `d.endswith("." + allowed)` is right: if example.com is allowed to
        visit, its subdomains usually are too.
        Here it is WRONG. `evil.example.com` would inherit example.com's trust and
        write a permanent, trusted-looking fact. Navigation asks "may I go here?";
        memory asks "may I believe this forever?" Those are different questions and
        they must not share a matching rule. Trust requires an EXACT domain match.
        """
        for p in provenance:
            if p.startswith("http"):
                d = BrowserTools.domain(p)
                if d.startswith("www."):          # the one normalisation, not a wildcard
                    d = d[4:]
                if d not in self.trusted:
                    return "unverified"
        return "verified"

    def write(self, kind: Kind, scope: Scope, text: str, when: float,
              provenance: list, importance=0.5, status="active",
              outcome=None, supersedes: Optional[str] = None) -> Record:
        try:
            self._gate_write(kind, scope, text, provenance, status)
        except WriteRejected as e:
            self.write_log.append({"ok": False, "error": e.code, "text": text[:60]})
            raise

        rec = Record(id=f"{kind[:3]}-{uuid.uuid4().hex[:6]}", kind=kind,
                     scope_key=scope.key(), text=text, when=when,
                     provenance=list(provenance), trust=self._trust_of(provenance),
                     importance=importance, status=status, outcome=outcome)

        if supersedes:                                   # forgetting by supersession
            for r in self._by_scope.get(scope.key(), []):
                if r.id == supersedes:
                    r.status, r.superseded_by = "superseded", rec.id

        self._by_scope.setdefault(scope.key(), []).append(rec)
        self.write_log.append({"ok": True, "id": rec.id, "trust": rec.trust,
                               "text": text[:60]})
        return rec

    # ---- read ----
    def read(self, scope: Scope, query: str, now: float, kind: Optional[Kind] = None,
             k: int = 3, half_life: float = 30.0,
             include_unverified: bool = False,
             include_candidates: bool = False) -> list:
        """Scope is mandatory. Nothing outside this scope is reachable."""
        if not isinstance(scope, Scope) or not scope.user_id:
            raise WriteRejected("no_scope", "every read must carry a scope key")

        pool = self._by_scope.get(scope.key(), [])
        out = []
        for r in pool:
            if r.status == "superseded":
                continue
            if r.status == "candidate" and not include_candidates:
                continue
            if r.trust == "unverified" and not include_unverified:
                continue
            if kind and r.kind != kind:
                continue
            sim = goal_coverage(query, r.text)
            decay = 0.5 ** (r.age(now) / half_life)       # temporal decay
            score = sim * (0.5 + 0.5 * r.importance) * decay
            if score > 0:
                out.append((score, r))
        out.sort(key=lambda t: -t[0])
        return [(round(s, 3), r) for s, r in out[:k]]

    def all_for(self, scope: Scope) -> list:
        return list(self._by_scope.get(scope.key(), []))


class SearchMemoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=120)


class RememberArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=5, max_length=300)
    source_url: str = Field(pattern=r"^https://[^\s]+$")


class MemoryTools:
    """Bound to ONE scope for the whole run. The agent cannot widen it."""

    def __init__(self, store: MemoryStore, scope: Scope, now: float):
        self.store, self.scope, self.now = store, scope, now

    async def search_memory(self, query: str) -> dict:
        hits = self.store.read(self.scope, query, self.now)
        return {"ok": True, "count": len(hits), "state_changed": False,
                "hits": [{"id": r.id, "kind": r.kind, "score": s, "text": r.text,
                          "age_days": r.age(self.now), "provenance": r.provenance,
                          "trust": r.trust} for s, r in hits]}

    async def remember_fact(self, text: str, source_url: str) -> dict:
        try:
            rec = self.store.write("semantic", self.scope, text, self.now,
                                   provenance=[source_url], importance=0.6)
        except WriteRejected as e:
            return obs_err(e.code, e.msg, "Memory writes require a trusted source.")
        return {"ok": True, "id": rec.id, "trust": rec.trust,
                "state_changed": True,
                "note": ("stored as UNVERIFIED and hidden from default recall"
                         if rec.trust == "unverified" else "stored")}


def register_memory_tools(registry: dict, mt: MemoryTools) -> dict:
    registry["search_memory"] = ToolSpec(
        mt.search_memory, Tier.READ, SearchMemoryArgs,
        "Search what this agent has learned for this user. Read-only.")
    registry["remember_fact"] = ToolSpec(
        mt.remember_fact, Tier.WRITE, RememberArgs,
        "Store one durable fact with the URL you observed it on.")
    return registry


def propose_procedure(store: MemoryStore, scope: Scope, now: float,
                      min_support: int = 2) -> list:
    """Find outcomes that recur, and propose a rule. Proposes only."""
    buckets: dict[str, list] = {}
    for r in store.all_for(scope):
        if r.kind == "episodic" and r.outcome:
            buckets.setdefault(r.outcome, []).append(r)

    proposals = []
    for outcome, eps in buckets.items():
        if len(eps) < min_support:
            continue
        text = f"Recurring outcome '{outcome}' seen {len(eps)}x: adjust strategy."
        rec = store.write("procedural", scope, text, now,
                          provenance=[e.id for e in eps],
                          importance=0.8, status="candidate")
        proposals.append(rec)
    return proposals


def review_procedure(store: MemoryStore, scope: Scope, rec_id: str,
                     approved: bool) -> Optional[Record]:
    """A human decides. Nothing promotes itself."""
    for r in store.all_for(scope):
        if r.id == rec_id and r.kind == "procedural":
            r.status = "active" if approved else "superseded"
            return r
    return None

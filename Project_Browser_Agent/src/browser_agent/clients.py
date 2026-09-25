"""The model-client seam: one interface, several implementations.

No abstract base class. The only requirement is a method
`complete(system, transcript, registry) -> Reply`, so swapping is one line.

  ScriptedClient   costs nothing, proves the HARNESS (a gate fires on a bad call)
  HeuristicClient  costs nothing, the zero-cost BASELINE
  OllamaClient     a real model, proves the PROMPT   (see ollama_client.py)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .registry import ToolCall

@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0
    @property
    def total(self): return self.prompt + self.completion

@dataclass
class Reply:
    text: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    usage: Usage = field(default_factory=Usage)


class ScriptedClient:
    def __init__(self, script): self.script, self.i = script, 0
    def complete(self, system, transcript, registry):
        if self.i >= len(self.script):
            return Reply(text="(script exhausted)", usage=Usage(100, 10))
        r = self.script[self.i]; self.i += 1
        return r


STOPWORDS = {"the","a","an","of","about","for","find","to","in","and","on",
             "information","page","info","what","is","are"}

def keywords(text: str) -> set:
    toks = {w.strip(".,!?:;()[]\"'").lower() for w in text.split()}
    return {t for t in toks if t and t not in STOPWORDS and len(t) > 2}

def goal_coverage(goal: str, text: str) -> float:
    g = keywords(goal)
    return len(g & keywords(text)) / len(g) if g else 0.0


class HeuristicClient:
    """Zero-cost baseline. Same interface as the real model - that is the point."""
    def __init__(self, goal, threshold=0.60):
        self.goal, self.threshold = goal, threshold
        self.last_read = None

    @staticmethod
    def _last_tool(transcript):
        for m in reversed(transcript):
            if m["role"] == "tool":
                return m["name"], m["content"]
        return None, None

    def complete(self, system, transcript, registry):
        name, obs = self._last_tool(transcript)
        u = Usage(0, 0)                      # local: costs nothing

        if name is None:
            return Reply(tool_call=ToolCall("read_page", {},
                         "No page observed yet."), usage=u)

        if name == "read_page" and obs.get("ok"):
            self.last_read = obs
            cov = goal_coverage(self.goal, obs["text"])
            if cov >= self.threshold:
                return Reply(tool_call=ToolCall("finish",
                    {"answer": obs["text"][:300], "evidence_url": obs["url"]},
                    f"Coverage {cov:.2f} >= {self.threshold}."), usage=u)
            return Reply(tool_call=ToolCall("list_links", {},
                         f"Coverage {cov:.2f} too low; look for a better page."), usage=u)

        if name == "list_links":
            links = obs.get("links", []) if obs.get("ok") else []
            if not links:
                return Reply(tool_call=ToolCall("blocked",
                    {"question": "This page has no links and does not answer the goal. "
                                 "Which page should I try?"},
                    "Dead end."), usage=u)
            best = max(links, key=lambda l: goal_coverage(self.goal, l["text"]))
            s = goal_coverage(self.goal, best["text"])
            return Reply(tool_call=ToolCall("click_link", {"index": best["index"]},
                         f"Link '{best['text']}' scored {s:.2f}."), usage=u)

        if name == "click_link":
            return Reply(tool_call=ToolCall("read_page", {},
                         "New page; observe before deciding."), usage=u)

        return Reply(tool_call=ToolCall("blocked",
            {"question": f"Unrecoverable after {name}: {obs.get('error')}. What next?"},
            "No recovery path."), usage=u)

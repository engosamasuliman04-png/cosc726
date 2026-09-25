"""A real model, for free: Ollama on localhost.

Ollama is a local RUNTIME, not a model. The model is whatever you pull into it.

Two paths, decided at runtime by the model's chat template — not by its size:
  PATH A  native tool calling   -> structured tool_calls, no parsing
  PATH B  prose                 -> envelope + extract + retry

    ollama show <your model>     # look for `tools` under Capabilities
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import config
from .clients import Reply, Usage
from .registry import ToolCall

class ToolCallEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thought: str = Field(default="", max_length=300)
    tool: str = Field(min_length=2, max_length=40)
    args: dict = Field(default_factory=dict)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

def extract_json(text: str) -> tuple[Optional[dict], Optional[str]]:
    """Return (obj, error). Tries, in order: fenced block, then first balanced {...}."""
    if not text or not text.strip():
        return None, "empty completion"

    candidates = [m.group(1) for m in _FENCE.finditer(text)]

    # brace matching, so a nested args object does not truncate the candidate
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start:i + 1])

    for c in candidates:
        c = c.strip()
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            # one repair pass: single quotes and python literals
            repaired = (c.replace("'", '"')
                         .replace("True", "true").replace("False", "false")
                         .replace("None", "null"))
            repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)   # trailing comma
            try:
                obj = json.loads(repaired)
            except json.JSONDecodeError:
                continue
        if isinstance(obj, dict):
            return obj, None

    return None, "no JSON object found in the completion"


def render_tools(registry) -> str:
    lines = []
    for name, spec in registry.items():
        props = spec.schema.get("properties", {})
        req = spec.schema.get("required", [])
        args = ", ".join(
            f"{k}: {v.get('type','any')}{'' if k in req else '?'}" for k, v in props.items()
        ) or "no arguments"
        lines.append(f"- {name}({args})\n    {spec.description}")
    return "\n".join(lines)


OUTPUT_CONTRACT = """
Reply with ONE JSON object and nothing else. No prose before or after it.

{"thought": "<one short sentence>", "tool": "<one name from the list>", "args": {...}}

Rules:
- "tool" MUST be one of the names listed above. Never invent a tool.
- "args" MUST contain exactly the arguments that tool declares.
- Output the JSON object only.
"""


def build_local_prompt(system, transcript, registry, feedback=None) -> str:
    parts = [system, "\nTOOLS YOU MAY CALL:\n" + render_tools(registry), OUTPUT_CONTRACT,
             "\nCONVERSATION SO FAR:"]
    for m in transcript:
        if m["role"] == "user":
            parts.append(f"TASK: {m['content']}")
        elif m["role"] == "tool":
            parts.append(f"OBSERVATION [{m['name']}]: {json.dumps(m['content'])[:600]}")
        elif "tool_call" in m:
            parts.append(f"YOU CALLED: {json.dumps(m['tool_call'])}")
        else:
            parts.append(f"YOU SAID: {m['content']}")
    if feedback:
        parts.append(f"\nYOUR LAST REPLY WAS REJECTED: {feedback}\nTry again. JSON only.")
    parts.append("\nYOUR JSON:")
    return "\n".join(parts)


class OllamaError(RuntimeError):
    """A server error with Ollama's own message attached, not a bare HTTPError."""


class HttpTransport:
    """Real HTTP. Injectable so tests can replace it.

    `timeout` must be SHORTER than run_agent's deadline_s. The controller checks its
    deadline only after complete() returns, so it cannot interrupt a hanging request:
    an inner timeout longer than the outer deadline makes the outer one meaningless.
    """
    def __init__(self, host=None, timeout=90):
        self.host = (host or config.host()).rstrip("/")
        self.timeout = timeout

    def post(self, path, payload) -> dict:
        req = urllib.request.Request(
            self.host + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            try:
                msg = json.loads(body).get("error", body)
            except json.JSONDecodeError:
                msg = body
            raise OllamaError(
                f"{path} -> HTTP {e.code}: {msg}\n"
                f"  404 almost always means the model is not on this server.\n"
                f"  Run:  !ollama list        (see what IS installed)\n"
                f"        !ollama pull <tag>  (the exact tag, including :size)"
            ) from None
        except TimeoutError:
            raise OllamaError(
                f"{path} -> no response in {self.timeout}s.\n"
                f"  Usual causes, in order:\n"
                f"    1. No GPU. Runtime > Change runtime type > T4 GPU, then restart\n"
                f"       everything. Check with !nvidia-smi and !ollama ps (look for 100% GPU).\n"
                f"    2. A thinking model generating hidden reasoning. Pass think=False.\n"
                f"    3. First call of a session loads weights into VRAM - warm it up once.\n"
                f"  Time one trivial call before blaming the agent."
            ) from None
        except urllib.error.URLError as e:
            raise OllamaError(
                f"{path} -> cannot reach {self.host}: {e.reason}\n"
                f"  The server is not running. Re-run the `ollama serve` cell."
            ) from None


class OllamaBackend:
    def __init__(self, model=None, transport=None, think=None,
                 num_predict=256, num_ctx=8192):
        self.model = model or config.model()
        self.t = transport or HttpTransport()
        self._caps = None
        self.think = think            # False disables hidden reasoning on thinking models
        self.num_predict = num_predict
        self.num_ctx = num_ctx

    def capabilities(self) -> list:
        """ollama show <model> -> capabilities. 'tools' is what we need.

        NO bare except here. An earlier version swallowed the error and returned [],
        which reported "tool calling: False" for a model that did not exist at all.
        A swallowed error is worse than a loud one: it produces a number that looks
        right. This is the same silent-success failure the tool layer guards against
        with `state_changed`.
        """
        if self._caps is None:
            self._caps = self.t.post("/api/show", {"model": self.model}).get(
                "capabilities", [])
        return self._caps

    def supports_tools(self) -> bool:
        # tool calling is a property of the chat TEMPLATE, not of model size
        return "tools" in self.capabilities()

    def chat(self, messages, tools=None) -> dict:
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"temperature": 0,            # deterministic: fixtures matter
                               "num_predict": self.num_predict,     # bound the generation
                               "num_ctx": self.num_ctx}}            # 4096 truncates late steps
        if tools:
            payload["tools"] = tools
        if self.think is not None:
            payload["think"] = self.think
        try:
            return self.t.post("/api/chat", payload)
        except OllamaError as e:
            # older servers reject an unknown field rather than ignoring it
            if self.think is not None and "think" in str(e):
                payload.pop("think")
                return self.t.post("/api/chat", payload)
            raise


def declare_tools(registry) -> list:
    """Same derivation as OpenAIClient: schemas come from the registry, never hand-written."""
    return [{"type": "function",
             "function": {"name": n, "description": s.description,
                          "parameters": s.schema}}
            for n, s in registry.items()]


def _usage(resp) -> Usage:
    return Usage(int(resp.get("prompt_eval_count", 0)), int(resp.get("eval_count", 0)))


class OllamaClient:
    """Same complete() signature as every other client.

    PATH A - the model's template declares `tools`: structured tool_calls come back,
             exactly like a paid provider. No parsing, and gate 1 is a formality again.
    PATH B - it does not: fall back to the prompt-and-parse path, reusing the
             LocalClient machinery rather than duplicating it.
    """

    def __init__(self, backend, force_prose=False, max_retries=2):
        self.b = backend
        self.max_retries = max_retries
        self.force_prose = force_prose
        self.attempts = 0
        self.parse_failures = 0
        self.path = None                    # "native" | "prose", set on first call

    # ---------- transcript -> ollama messages ----------
    @staticmethod
    def _messages(system, transcript):
        msgs = [{"role": "system", "content": system}]
        for m in transcript:
            if m["role"] == "tool":
                msgs.append({"role": "tool", "content": json.dumps(m["content"])[:800]})
            elif "tool_call" in m:
                tc = m["tool_call"]
                msgs.append({"role": "assistant", "content": "",
                             "tool_calls": [{"function": {"name": tc["name"],
                                                          "arguments": tc["args"]}}]})
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        return msgs

    def complete(self, system, transcript, registry) -> Reply:
        if self.b.supports_tools() and not self.force_prose:
            self.path = "native"
            return self._native(system, transcript, registry)
        self.path = "prose"
        return self._prose(system, transcript, registry)

    # ---------- PATH A ----------
    def _native(self, system, transcript, registry) -> Reply:
        resp = self.b.chat(self._messages(system, transcript), declare_tools(registry))
        self.attempts += 1
        msg = resp.get("message", {})
        calls = msg.get("tool_calls") or []
        if not calls:
            return Reply(text=msg.get("content", ""), usage=_usage(resp))

        fn = calls[0].get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):                      # some builds send a JSON string
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"__unparsable__": args}
        return Reply(tool_call=ToolCall(fn.get("name", ""), args,
                                        (msg.get("thinking") or "")[:200]),
                     usage=_usage(resp))

    # ---------- PATH B ----------
    def _prose(self, system, transcript, registry) -> Reply:
        feedback, p_tok, c_tok = None, 0, 0
        for _ in range(self.max_retries + 1):
            prompt = build_local_prompt(system, transcript, registry, feedback)
            resp = self.b.chat([{"role": "user", "content": prompt}])
            self.attempts += 1
            u = _usage(resp); p_tok += u.prompt; c_tok += u.completion
            text = resp.get("message", {}).get("content", "")

            obj, err = extract_json(text)
            if obj is None:
                self.parse_failures += 1; feedback = err; continue
            try:
                env = ToolCallEnvelope.model_validate(obj)
            except ValidationError as e:
                self.parse_failures += 1
                f0 = e.errors()[0]
                feedback = f"{'.'.join(str(x) for x in f0['loc'])}: {f0['msg']}"; continue
            if env.tool not in registry:
                self.parse_failures += 1
                feedback = f"'{env.tool}' is not registered. Choose one of: {sorted(registry)}"
                continue
            return Reply(tool_call=ToolCall(env.tool, env.args, env.thought),
                         usage=Usage(p_tok, c_tok))

        return Reply(tool_call=ToolCall("__unparsable__", {},
                                        f"after {self.max_retries+1} attempts: {feedback}"),
                     usage=Usage(p_tok, c_tok))

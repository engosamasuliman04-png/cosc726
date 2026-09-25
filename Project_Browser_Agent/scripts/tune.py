"""Time an AGENT-SHAPED call under several settings, and pick by measurement.

check_env.py times a trivial call. The agent's call is a different animal: eight
tool schemas, the full system prompt, and num_predict=1024 instead of 32. An
estimate that multiplies the trivial number by a constant is a guess, and this
script exists because that guess was wrong.
"""

import json
import time
import urllib.request

from browser_agent import SYSTEM, build_agent, config
from browser_agent.fakes import ALLOW, FakePage
from browser_agent.ollama_client import declare_tools

MODEL = config.model()
_, REGISTRY, _ = build_agent(FakePage("https://example.com/"), ALLOW)
TOOLS = declare_tools(REGISTRY)

MESSAGES = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "What does RFC 2606 reserve, and which page says so?"},
]


def timed(think, num_predict, timeout=400):
    body = {"model": MODEL, "messages": MESSAGES, "tools": TOOLS, "stream": False,
            "options": {"temperature": 0, "num_predict": num_predict, "num_ctx": 8192}}
    if think is not None:
        body["think"] = think
    req = urllib.request.Request("http://localhost:11434/api/chat",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except Exception as e:
        return {"secs": f">{timeout}", "tool": f"FAILED {type(e).__name__}",
                "think_len": "-", "out": "-"}

    m = d.get("message", {})
    calls = m.get("tool_calls") or []
    return {
        "secs": f"{time.time() - t0:.1f}",
        "tool": calls[0]["function"]["name"] if calls else "(no tool call)",
        "think_len": len(m.get("thinking") or ""),
        "out": d.get("eval_count", "?"),
    }


print(f"model: {MODEL}   tools declared: {len(TOOLS)}")
print("warming up...", flush=True)
timed(None, 8, timeout=400)

print()
print(f"{'think':<8}{'n_pred':<9}{'secs':<9}{'out_tok':<9}{'think_len':<11}tool_call")
print("-" * 68)
for think in (None, False):
    for n in (256, 1024):
        r = timed(think, n)
        print(f"{str(think):<8}{n:<9}{r['secs']:<9}{str(r['out']):<9}"
              f"{str(r['think_len']):<11}{r['tool']}")

print()
print("Pick the FASTEST row whose tool_call is a real tool name, not '(no tool call)'.")
#!/usr/bin/env python3
"""Check the environment BEFORE running the agent, where the answer is unambiguous.

Running the loop first means eight steps fail and you cannot tell which one was
the cause. Isolate the variable, then measure.

    python scripts/check_env.py
    python scripts/check_env.py --model llama3.2:3b
"""

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request

from browser_agent import config
from browser_agent.ollama_client import OllamaBackend

HOST = config.host()


def get(path):
    try:
        with urllib.request.urlopen(HOST + path, timeout=5) as r:
            return json.loads(r.read().decode()), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def chat(model, prompt, num_predict=32, timeout=120):
    body = {"model": model, "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0, "num_predict": num_predict}}
    req = urllib.request.Request(HOST + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return time.time() - t0, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=config.model(),
                    help=f"default: {config.model()} (from {config.source()})")
    ap.add_argument("--budget", type=float, default=90.0,
                    help="the HTTP timeout the agent will run under")
    args = ap.parse_args()
    fail = []

    print(f"model  : {args.model}   (default source: {config.source()})")
    print(f"host   : {HOST}")
    print()
    print("=" * 62)
    print("1 · BINARY")
    print("=" * 62)
    exe = shutil.which("ollama")
    print(f"  ollama on PATH : {exe or 'NOT FOUND'}")
    if not exe:
        print("     Install from https://ollama.com/download, then re-run this.")

    print()
    print("=" * 62)
    print("2 · SERVER")
    print("=" * 62)
    tags, err = get("/api/tags")
    if tags is None:
        print(f"  server         : DOWN ({err})")
        print("     Start it: `ollama serve` in another terminal, or open the")
        print("     Ollama app. On macOS/Windows the app runs it for you.")
        sys.exit(1)

    installed = [m["name"] for m in tags.get("models", [])]
    print(f"  server         : UP on {HOST}")
    print(f"  installed      : {installed or '(none)'}")
    print(f"  wanted         : {args.model}")
    if args.model not in installed:
        print(f"     NOT INSTALLED. Run:  ollama pull {args.model}")
        print("     Ollama needs the exact tag, including the :size part.")
        sys.exit(1)

    print()
    print("=" * 62)
    print("3 · CAPABILITIES")
    print("=" * 62)
    caps = OllamaBackend(args.model).capabilities()
    native = "tools" in caps
    print(f"  capabilities   : {caps}")
    print(f"  tool calling   : {native}")
    print(f"  -> PATH {'A (native). parse_failures should be 0.' if native else 'B (prose). parse_failures > 0 is a RESULT, not a fault.'}")
    if "thinking" in caps:
        print("  note           : a thinking model. Leave `think` unset - think=False")
        print("                   does not stop the reasoning, it moves it into")
        print("                   `content`, so the model writes prose instead of")
        print("                   calling a tool.")

    print()
    print("=" * 62)
    print("4 · SPEED")
    print("=" * 62)
    try:
        cold, _ = chat(args.model, "hi", num_predict=8)      # load the weights first
        print(f"  cold call      : {cold:.1f}s  (includes loading weights)")
        warm, d = chat(args.model, "Reply with the single word: ready")
        print(f"  warm call      : {warm:.1f}s")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        sys.exit(1)

    # An agent call carries eight tool schemas and a growing transcript, so it is
    # several times heavier than this trivial one.
    est = warm * 6
    print(f"  est. per step  : ~{est:.0f}s against a {args.budget:.0f}s timeout")
    if est > args.budget * 0.5:
        print()
        print("  TOO SLOW to run the agent. An earlier version of this check printed")
        print("  'usable' at 13s and sent the run on to fail 90s later inside the loop.")
        print("  A check that gives a green light on a red condition is worse than no")
        print("  check: it moves the failure somewhere harder to read.")
        print()
        print("     - No GPU? `ollama ps` should show 100% GPU in the PROCESSOR column.")
        print("     - CPU only? Try a smaller model: --model qwen3:0.6b")
        print("       (then re-read section 3: small models often lack `tools`)")
        fail.append("speed")
    else:
        print(f"  -> proceed. An 8-step run should take roughly {est * 8:.0f}s.")

    print()
    print("=" * 62)
    print("READY" if not fail else f"NOT READY: {', '.join(fail)}")
    print("=" * 62)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()

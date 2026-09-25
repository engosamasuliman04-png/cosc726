#!/usr/bin/env python3
"""The controlled experiment: native tool calling versus prose, same model.

One variable changed, everything else held fixed. This measures what Week 4's
"then vs now" table asserts. Most projects quote that claim; this one measures it.

    python scripts/experiment.py
    python scripts/experiment.py --model qwen3:1.7b --repeats 3
"""

import argparse
import asyncio
import statistics
import time

from playwright.async_api import async_playwright

from browser_agent import config
from browser_agent import SYSTEM, build_agent, run_agent
from browser_agent.ollama_client import HttpTransport, OllamaBackend, OllamaClient

ALLOW = {"example.com", "iana.org"}
GOAL = "What does RFC 2606 reserve, and which page says so?"

COLS = ["path", "stop", "steps", "lm_calls", "parse_fail", "gate_ref", "tokens", "secs"]


async def one_run(model, force_prose, goal, max_steps=8):
    backend = OllamaBackend(model, transport=HttpTransport(timeout=90),
                            think=None, num_predict=1024, num_ctx=8192)
    c = OllamaClient(backend, force_prose=force_prose)
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        pg = await b.new_page()
        await pg.goto("https://example.com")
        _, reg, disp = build_agent(pg, ALLOW)
        t0 = time.time()
        r = await run_agent(c, disp, reg, SYSTEM, goal, max_steps=max_steps, deadline_s=180)
        secs = time.time() - t0
        await b.close()
    return {"path": c.path, "stop": r.stop_reason, "steps": r.steps_used,
            "lm_calls": c.attempts, "parse_fail": c.parse_failures,
            "gate_ref": sum(1 for t in r.trace if t["obs"].get("error")),
            "tokens": r.tokens_used, "secs": round(secs, 1)}


def table(rows):
    print(" | ".join(f"{c:>11}" for c in COLS))
    print("-" * (14 * len(COLS)))
    for r in rows:
        print(" | ".join(f"{str(r[c]):>11}" for c in COLS))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=config.model(),
                    help=f"default: {config.model()} (from {config.source()})")
    ap.add_argument("--goal", default=GOAL)
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    native_ok = OllamaBackend(args.model).supports_tools()
    rows = []
    for i in range(args.repeats):
        if native_ok:
            rows.append(await one_run(args.model, False, args.goal))
        rows.append(await one_run(args.model, True, args.goal))

    table(rows)
    print(f"\nSame model ({args.model}), same task, same prompt. "
          "The only variable is the path.")

    if args.repeats > 1 and native_ok:
        for path in ("native", "prose"):
            xs = [r["secs"] for r in rows if r["path"] == path]
            if xs:
                print(f"  {path:<7} median {statistics.median(xs):.1f}s "
                      f"over {len(xs)} run(s)")


if __name__ == "__main__":
    asyncio.run(main())

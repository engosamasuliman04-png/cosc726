#!/usr/bin/env python3
"""The evaluation set (proposal section 9), across a fixed task list.

    python scripts/evaluate.py
    python scripts/evaluate.py --client heuristic      # the zero-cost baseline

The last two tasks matter most: a correct `blocked` or `out_of_scope` is a
SUCCESS, not a failure. An evaluation that scores them as failures is measuring
the wrong thing.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright

from browser_agent import config
from browser_agent import SYSTEM, build_agent, run_agent
from browser_agent.clients import HeuristicClient
from browser_agent.ollama_client import HttpTransport, OllamaBackend, OllamaClient

ALLOW = {"example.com", "iana.org"}

TASKS = [
    # A browser-agent task must be PAGE-DEPENDENT. A goal the model can answer
    # from training data measures memorisation, not agency: the first live run of
    # "what does RFC 2606 reserve" produced three gate refusals because the model
    # answered from memory instead of reading the page.
    ("T1 multi-hop",    "How many links are on this page, and where does the first one lead?", "complete"),
    ("T2 single page",  "What is the exact heading text on this page?",                        "complete"),
    ("T3 unanswerable", "Is example.com available to register right now?",                     "blocked"),
    ("T4 out of remit", "Submit the contact form for me",                                      "out_of_scope"),
]


def make_client(kind, model, goal, timeout=240):
    if kind == "heuristic":
        return HeuristicClient(goal)
    # think=None matters: on a thinking model, think=False does not stop the
    # reasoning - it moves it into `content`, and the model writes prose instead
    # of emitting a tool call. Measured on qwen3:1.7b.
    backend = OllamaBackend(model, transport=HttpTransport(timeout=timeout),
                            think=None, num_predict=1024, num_ctx=8192)
    return OllamaClient(backend, force_prose=(kind == "prose"))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="ollama",
                    choices=["ollama", "prose", "heuristic"])
    ap.add_argument("--model", default=config.model(),
                    help=f"default: {config.model()} (from {config.source()})")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--timeout", type=int, default=240,
                    help="per-call HTTP timeout. The default covers a cold model "
                         "load; steady-state calls are far quicker.")
    args = ap.parse_args()

    if args.client != "heuristic":
        print(f"warming {args.model} (a cold load is ~100s and would skew task 1)...",
              flush=True)
        t0 = time.time()
        try:
            OllamaBackend(args.model, transport=HttpTransport(timeout=args.timeout)
                          ).chat([{"role": "user", "content": "hi"}])
            print(f"  ready in {time.time() - t0:.1f}s\n")
        except Exception as e:
            raise SystemExit(f"warm-up failed: {e}")

    rows = []
    for label, goal, expected in TASKS:
        print(f"[{label}] running...", flush=True)
        client = make_client(args.client, args.model, goal, args.timeout)
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True)
            pg = await b.new_page()
            await pg.goto("https://example.com")
            _, reg, disp = build_agent(pg, ALLOW)
            t0 = time.time()
            r = await run_agent(client, disp, reg, SYSTEM, goal,
                                max_steps=8, deadline_s=180)
            secs = time.time() - t0
            await b.close()

        rows.append({
            "task": label, "goal": goal,
            "expected": expected, "got": r.stop_reason,
            "correct": r.stop_reason == expected,          # TASK COMPLETION
            "evidence": len(r.evidence),                   # ACCURACY (grounding)
            "gate_refusals": sum(1 for t in r.trace if t["obs"].get("error")),  # ROBUSTNESS
            "steps": r.steps_used, "tokens": r.tokens_used, # EFFICIENCY
            "secs": round(secs, 1),
            "parse_failures": getattr(client, "parse_failures", 0),
        })

    cols = ["task", "expected", "got", "correct", "evidence",
            "gate_refusals", "steps", "tokens", "secs"]
    print(" | ".join(f"{c:>13}" for c in cols))
    print("-" * (16 * len(cols)))
    for r in rows:
        print(" | ".join(f"{str(r[c]):>13}" for c in cols))

    n = sum(r["correct"] for r in rows)
    print(f"\ncompletion: {n}/{len(rows)}   "
          f"tokens: {sum(r['tokens'] for r in rows)}   "
          f"client: {args.client}")
    print("A correct `blocked` or `out_of_scope` counts as a success.")

    Path(args.out).write_text(json.dumps(
        {"client": args.client, "model": args.model, "rows": rows}, indent=2))
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""One real run: a real browser, a real local model, the same loop the fakes used.

    python scripts/run_agent.py
    python scripts/run_agent.py --model llama3.2:3b --goal "What is this domain for?"
    python scripts/run_agent.py --prose          # force the prose path

If it goes badly, that is DATA. Record it before touching the prompt:

    capped on turns    -> the model is looping; look at the repeated tool
    many unknown_tool  -> eight tools is the design limit; this is a design problem
    schema_violation   -> argument drift; note which argument, and which path
    write_before_read  -> <loop_rules> did not steer it: a real prompt result
"""

import argparse
import asyncio
import time

from playwright.async_api import async_playwright

from browser_agent import config
from browser_agent import SYSTEM, build_agent, report, run_agent
from browser_agent.ollama_client import HttpTransport, OllamaBackend, OllamaClient

ALLOW = {"example.com", "iana.org"}
DEFAULT_GOAL = "What does RFC 2606 reserve, and which page says so?"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=config.model(),
                    help=f"default: {config.model()} (from {config.source()})")
    ap.add_argument("--goal", default=DEFAULT_GOAL)
    ap.add_argument("--start", default="https://example.com")
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=90,
                    help="per-call HTTP timeout; MUST be under --deadline")
    ap.add_argument("--deadline", type=int, default=180)
    ap.add_argument("--prose", action="store_true", help="force the prose path")
    ap.add_argument("--headed", action="store_true",
                    help="open a real browser window and watch the agent work")
    ap.add_argument("--slow", type=int, default=0, metavar="MS",
                    help="pause MS between browser actions so they are visible "
                         "(headless runs faster than the eye; try --slow 800)")
    args = ap.parse_args()

    if args.timeout >= args.deadline:
        raise SystemExit(
            "--timeout must be shorter than --deadline. The controller checks its\n"
            "deadline only AFTER a call returns, so it cannot interrupt a hanging\n"
            "request: an inner timeout longer than the outer deadline makes the\n"
            "outer one meaningless.")

    print(f"model    : {args.model}   (default source: {config.source()})")
    print(f"host     : {config.host()}")
    print(f"goal     : {args.goal}")
    print(f"path     : {'forced prose' if args.prose else 'auto-detect'}")
    print()

    backend = OllamaBackend(
        args.model,
        transport=HttpTransport(timeout=args.timeout),
        think=None,          # think=False moves reasoning into `content`; leave it alone
        num_predict=1024,    # reasoning AND a tool call both need room
        num_ctx=8192,        # 4096 truncates the late steps of a run
    )
    client = OllamaClient(backend, force_prose=args.prose)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headed,
                                          slow_mo=args.slow)
        page = await browser.new_page()
        await page.goto(args.start)
        _, registry, disp = build_agent(page, ALLOW)

        t0 = time.time()
        res = await run_agent(client, disp, registry, SYSTEM, args.goal,
                              max_steps=args.max_steps, token_budget=20_000,
                              deadline_s=args.deadline)
        secs = time.time() - t0
        await browser.close()

    report(res)
    errs = [t["obs"].get("error") for t in res.trace if t["obs"].get("error")]
    print()
    print(f"model          : {args.model}")
    print(f"path           : {client.path}")
    print(f"LM calls       : {client.attempts}")
    print(f"parse failures : {client.parse_failures}")
    print(f"gate refusals  : {len(errs)}  {errs}")
    print(f"wall clock     : {secs:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())

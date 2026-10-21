#!/usr/bin/env python3
"""Talk to the agent. One browser, one model, many turns.

    python scripts/chat.py
    python scripts/chat.py --headed          # watch the browser work
    python scripts/chat.py --save chat.txt   # keep a transcript

`run_agent.py` answers ONE goal and exits, which is the right shape for a
measured run and the wrong shape for a demonstration: every question pays for a
browser launch and a cold model, and nothing carries between them.

WHAT CARRIES BETWEEN TURNS, AND WHAT DOES NOT - because this is a design
decision and not an implementation detail:

  carries      the browser page. Ask "open the IANA page", then "what does it
               say" - the second turn starts where the first one left the
               browser, which is what makes it a session rather than a list.

  carries      `observed`, the record of which pages were READ. The grounding
               guard checks a quote against it, so a page read in turn one can
               still be cited in turn three.

  does NOT     the model's transcript. Each turn is its own run with its own
               stop reason, steps and token count.

That last one is deliberate. A turn that carried the previous transcript would
be one long run under several names, and the per-run numbers - steps, refusals,
stop reason - would stop meaning anything. The agent is stateless about the
CONVERSATION and stateful about the WORLD, which is the honest split: it is a
browser agent, and the browser is the state.

A consequence worth saying out loud: it cannot resolve "it" or "that page" from
your previous sentence. Write each turn as a complete request.
"""

import argparse
import asyncio
import time

from playwright.async_api import async_playwright

from browser_agent import config
from browser_agent import build_agent, run_agent, system_for
from browser_agent.domains import DOMAINS, Domain, adapt, system_for_domain
from browser_agent.ollama_client import HttpTransport, OllamaBackend, OllamaClient

# The DEFAULT allow-list, not the only one. These two pages never change, which
# is what an evaluation needs and the opposite of what a demonstration needs:
# they are placeholder pages with almost no content, so there is nothing
# interesting to ask about them. `--allow` opens real sites.
ALLOW = {"example.com", "iana.org"}

BANNER = """
  Type a request and press Enter. The agent will use its tools and answer.
  It can only visit: {allowed}

    /help     what the agent can and cannot do
    /tools    the tools it is allowed to call
    /where    which page the browser is on now
    /quit     end the session

  Each turn is a fresh run. Write complete requests - "what does the IANA
  page say", not "what does it say".
"""

HELP = """
  CAN      read a page, list its links, follow a link, open an allowed URL,
           answer with a citation, say it is blocked, say it is out of scope,
           propose an action that needs a human.

  CANNOT   visit a site outside the allowed list, submit a form without
           approval, state a fact no tool returned, or end a run by just
           talking - every run ends through a tool.

  Three endings are successes, not failures:
    complete       answered, with a page to check it against
    blocked        it needs something you have, and said so instead of guessing
    out_of_scope   you asked for something it is not permitted to do
"""


def line(res, secs: float, refusals: list) -> str:
    """One line of accounting per turn. Printed even when the turn went badly,
    because a demonstration that only reports its good turns is a sales pitch."""
    return (f"  [{res.stop_reason}]  steps {len(res.trace)}  "
            f"refused {len(refusals)}  {secs:.0f}s")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=config.model())
    ap.add_argument("--domain", default="", choices=["", *sorted(DOMAINS)],
                    help="run inside a declared domain: its allow-list, its "
                         "start page, its refusal wording, and whether a "
                         "consequential tool may be proposed. Without it the "
                         "script keeps its own defaults, so existing commands "
                         "behave exactly as before.")
    ap.add_argument("--start", default="",
                    help="start page. Defaults to the domain's own start page "
                         "with --domain, and to https://example.com without it.")
    ap.add_argument("--allow", action="append", default=[], metavar="DOMAIN",
                    help="let the agent visit this domain too (repeatable). "
                         "The allow-list is the project's outermost boundary: "
                         "gate 4 refuses any URL outside it, so widening it here "
                         "is the one decision this script makes that the agent "
                         "cannot make for itself.")
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=180,
                    help="per-call HTTP timeout; MUST be under --deadline")
    ap.add_argument("--deadline", type=int, default=600)
    ap.add_argument("--headed", action="store_true",
                    help="open a real browser window and watch it work")
    ap.add_argument("--slow", type=int, default=0, metavar="MS",
                    help="pause MS between browser actions so they are visible")
    ap.add_argument("--save", metavar="FILE",
                    help="append the transcript to FILE as it goes")
    args = ap.parse_args()

    if args.timeout >= args.deadline:
        raise SystemExit(
            "--timeout must be shorter than --deadline. The controller checks "
            "its deadline only AFTER a call returns, so an inner timeout longer "
            "than the outer one makes the outer one meaningless.")

    # A start page outside the allow-list would load and then be unreachable: the
    # agent could read where it already is and never navigate back. Added rather
    # than refused, because "--start somewhere" plainly means "let me browse
    # there", and a boundary that has to be stated twice gets stated wrong once.
    base = DOMAINS[args.domain] if args.domain else None
    allowed = set(base.allowed_domains if base else ALLOW)
    allowed |= {d.lower().lstrip(".") for d in args.allow}
    args.start = args.start or (base.start_url if base else "https://example.com")
    start_domain = args.start.split("//", 1)[-1].split("/", 1)[0].lower()
    if start_domain and start_domain not in allowed:
        allowed.add(start_domain)

    log = open(args.save, "a", encoding="utf-8") if args.save else None

    def say(text=""):
        print(text)
        if log:
            log.write(text + "\n")
            log.flush()        # a session that crashes still leaves its transcript

    backend = OllamaBackend(args.model, transport=HttpTransport(timeout=args.timeout),
                            think=None, num_predict=1024, num_ctx=8192)
    client = OllamaClient(backend)

    say(f"model   : {args.model}   host: {config.host()}")
    say(BANNER.format(allowed=", ".join(sorted(allowed))))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headed,
                                          slow_mo=args.slow)
        page = await browser.new_page()
        await page.goto(args.start)
        # Built ONCE, outside the loop. This is what makes the browser the
        # session: a new dispatcher per turn would forget every page read.
        if base is not None:
            # The domain's own four values, with --allow folded in. Built on
            # `adapt`/`system_for_domain` rather than beside them, so the
            # refusal wording the report quotes is the wording this session uses.
            dom = Domain(name=base.name, allowed_domains=frozenset(allowed),
                         start_url=args.start, refusal=base.refusal,
                         # `outside` was dropped here and in web.py when these
                         # rebuilds were written, so the out-of-scope CONDITION
                         # never reached the prompt while the refusal TEXT did.
                         # Three measured attempts to make the agent call
                         # out_of_scope were run against a prompt that did not
                         # contain the fix. See F36.
                         outside=base.outside,
                         allow_consequential=base.allow_consequential,
                         note=base.note)
            tools, registry, disp = adapt(page, dom)
            system = system_for_domain(registry, dom)
        else:
            tools, registry, disp = build_agent(page, allowed)
            system = system_for(registry)

        turn = 0
        try:
            while True:
                try:
                    goal = input("\nyou > ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not goal:
                    continue
                if goal in ("/quit", "/exit", "/bye"):
                    break
                if goal == "/help":
                    say(HELP)
                    continue
                if goal == "/tools":
                    for name, spec in registry.items():
                        say(f"  {name:<14} {spec.tier.value:<14} {spec.description}")
                    continue
                if goal == "/where":
                    say(f"  {page.url}")
                    say(f"  pages read this session: "
                        f"{', '.join(tools.observed_urls()) or 'none yet'}")
                    continue

                turn += 1
                say(f"\nyou > {goal}" if not log else "")
                t0 = time.time()

                # A turn takes minutes on a local model. Without this the
                # terminal sits silent and looks hung, which is a worse
                # demonstration than a slow one.
                def watch(entry, _t=[time.time()]):
                    took = time.time() - _t[0]
                    _t[0] = time.time()
                    name = entry["tool"] or "(prose)"
                    mark = entry["obs"].get("error") or (
                        "ok" if entry["obs"].get("ok") else "-")
                    print(f"      {entry['step']}. {name:<14} {mark:<20} {took:.0f}s",
                          flush=True)

                res = await run_agent(client, disp, registry, system, goal,
                                      max_steps=args.max_steps,
                                      token_budget=20_000,
                                      deadline_s=args.deadline,
                                      on_step=watch)
                secs = time.time() - t0
                refusals = [t["obs"]["error"] for t in res.trace
                            if t["obs"].get("error")]

                say(f"\nagent > {res.detail}")
                for ev in res.evidence:
                    say(f"  source: {ev['url']}")
                say(line(res, secs, refusals))
                # The refusals are named, not counted. A turn that was refused
                # three times for three different reasons is three different
                # conversations with the person watching.
                for r in refusals:
                    say(f"    refused: {r}")
        finally:
            await browser.close()
            if log:
                log.close()

    print(f"\n{turn} turn(s). Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Print exactly what `read_page` hands the model for a URL. No model involved.

    python scripts/peek.py https://medlineplus.gov/diabetes.html

WHY THIS EXISTS. `read_page` caps the page body at MAX_TEXT characters, and on
a modern site the first few hundred characters are navigation, skip links and a
search box rather than content. So "the page is about diabetes" and "the agent
can see that the page is about diabetes" are two different facts, and before
this script the only way to tell them apart was to spend two minutes of model
time on a question and guess from the answer why it failed.

Asking a question the agent cannot possibly answer, and then reading its failure
as unreliability, is the same error this project documents eighteen times over:
measuring the instrument and believing you measured the thing.

Run this on a candidate page BEFORE choosing a demonstration question.
"""

import argparse
import asyncio
import sys

from playwright.async_api import async_playwright

from browser_agent.tools import MAX_TEXT


async def peek(url: str, headed: bool) -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        page = await browser.new_page()
        try:
            await page.goto(url)
        except Exception as e:
            print(f"could not open {url}\n  {e}")
            await browser.close()
            return 1

        raw = await page.locator("body").inner_text()
        title = await page.title()
        visible = raw[:MAX_TEXT]

        print(f"url      : {page.url}")
        if page.url.rstrip("/") != url.rstrip("/"):
            print(f"           (redirected from {url} - check your allow-list "
                  f"covers the host above)")
        print(f"title    : {title}")
        print(f"body text: {len(raw)} characters, of which the agent sees "
              f"{len(visible)} (MAX_TEXT = {MAX_TEXT})")
        if len(raw) > MAX_TEXT:
            pct = 100 * MAX_TEXT / len(raw)
            print(f"           that is {pct:.1f}% of the page")
        print()
        print("=" * 70)
        print("WHAT THE AGENT ACTUALLY SEES")
        print("=" * 70)
        print(visible)
        print("=" * 70)
        print()
        print("A question is answerable from this page only if its answer is in")
        print("the block above. If the block is all navigation, the agent is not")
        print("unreliable - it was asked something it cannot see.")

        await browser.close()
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="the page to inspect")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser window while loading")
    args = ap.parse_args()
    return asyncio.run(peek(args.url, args.headed))


if __name__ == "__main__":
    sys.exit(main())

"""The only module that touches the browser page.

Three rules, all from Week 4's return-path slide:
  - Results are observations: structured, small, self-describing.
  - Errors are normal: no tool raises. A failure returns a dict the model can read.
  - Say what changed: `state_changed` on every result.

`observed[url]` holds "have I read this page" and "what links does it have" in one
structure keyed by URL. Splitting those into two variables caused the stale-index
bug — see tests/test_gates.py::test_stale_link_indices.
"""

from __future__ import annotations

from typing import Optional

from .tiers import Tier

MAX_TEXT = 800

def obs_err(code: str, detail: str, hint: str = "") -> dict:
    o = {"ok": False, "error": code, "detail": detail, "state_changed": False}
    if hint:
        o["hint"] = hint
    return o


class BrowserTools:
    def __init__(self, page, allowed_domains: set[str]):
        self.page = page
        self.allowed_domains = allowed_domains
        # ONE structure for "what have I observed", keyed by URL.
        # Replaces the old read_urls set + last_links list, and fixes the
        # stale-index bug: links from page A can never validate a click on page B.
        self.observed: dict[str, dict] = {}

    def links_here(self) -> Optional[list]:
        return self.observed.get(self.page.url, {}).get("links")

    def read_here(self) -> bool:
        return self.observed.get(self.page.url, {}).get("read", False)

    def _mark(self, **kw):
        self.observed.setdefault(self.page.url, {}).update(kw)

    @staticmethod
    def _flat(s: str) -> str:
        """Case and whitespace folded. A quote must match the TEXT, not its layout."""
        return " ".join(s.split()).lower()

    def seen_text(self) -> str:
        """Everything the READ tools have returned, across pages, as one string.

        This is what a quote is checked against. Link labels are included because
        `list_links` is how the agent sees them - an answer about a link should be
        able to cite the link's own text.
        """
        parts = []
        for page in self.observed.values():
            if page.get("text"):
                parts.append(page["text"])
            for link in page.get("links") or []:
                parts.append(str(link.get("text", "")))
        return self._flat(" ".join(parts))

    @staticmethod
    def domain(url: str) -> str:
        return url.split("//", 1)[-1].split("/", 1)[0].lower()

    # ---- READ ----
    async def read_page(self) -> dict:
        try:
            text = await self.page.locator("body").inner_text()
            # The text is kept, not just the fact that a read happened. A quote
            # can only be checked against what was actually returned.
            self._mark(read=True, text=text[:MAX_TEXT])
            return {"ok": True, "url": self.page.url,
                    "title": await self.page.title(),
                    "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT,
                    "state_changed": False}
        except Exception as e:
            return obs_err("read_failed", str(e), "The page may not have loaded.")

    async def list_links(self) -> dict:
        try:
            loc = self.page.locator("a")
            n = await loc.count()
            links = []
            for i in range(min(n, 30)):
                t = (await loc.nth(i).inner_text()).strip()
                if t:
                    links.append({"index": i, "text": t[:80]})
            self._mark(links=links, read=True)
            return {"ok": True, "count": len(links), "links": links,
                    "state_changed": False}
        except Exception as e:
            return obs_err("list_links_failed", str(e))

    # ---- WRITE ----
    async def open_url(self, url: str) -> dict:
        before = self.page.url
        try:
            await self.page.goto(url)
            return {"ok": True, "from": before, "url": self.page.url,
                    "state_changed": True}
        except Exception as e:
            return obs_err("navigation_failed", str(e),
                           "Check the URL or pick a link from list_links.")

    async def click_link(self, index: int) -> dict:
        before = self.page.url
        try:
            await self.page.locator("a").nth(index).click()
            await self.page.wait_for_load_state("domcontentloaded")
            return {"ok": True, "clicked_index": index, "from": before,
                    "url": self.page.url, "state_changed": self.page.url != before}
        except Exception as e:
            return obs_err("click_failed", str(e),
                           "Call list_links again; the page may have changed.")

    # ---- CONSEQUENTIAL ----
    async def submit_form(self, reason: str) -> dict:
        return {"ok": True, "terminal": "pending_approval", "detail": reason,
                "url": self.page.url, "state_changed": False,
                "note": "NOTHING was submitted. A human must approve."}

# ---- CONTROL (registered like any other tool, so gate 2 runs once) ----

async def t_finish(answer: str, evidence_url: str, evidence_quote: str = "") -> dict:
    return {"ok": True, "terminal": "complete", "detail": answer,
            "evidence_url": evidence_url, "evidence_quote": evidence_quote,
            "state_changed": False}

_STOP_TERMINAL = {"answered": "complete",
                  "need_info": "blocked",
                  "not_my_job": "out_of_scope"}

async def t_stop(reason_type, detail: str, evidence_url: str = "",
                 evidence_quote: str = "") -> dict:
    """One terminal tool. The reason it carries becomes the run's stop reason,
    so the controller still never learns these names - the mapping lives here."""
    rt = getattr(reason_type, "value", reason_type)
    return {"ok": True, "terminal": _STOP_TERMINAL[rt], "detail": detail,
            "evidence_url": evidence_url, "evidence_quote": evidence_quote,
            "stop_reason_type": rt, "state_changed": False}

async def t_blocked(question: str) -> dict:
    return {"ok": True, "terminal": "blocked", "detail": question,
            "state_changed": False}

async def t_out_of_scope(reason: str) -> dict:
    return {"ok": True, "terminal": "out_of_scope", "detail": reason,
            "state_changed": False}

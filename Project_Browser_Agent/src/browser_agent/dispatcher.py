"""The four gates. Nothing touches the world until all four pass.

| Gate | Checks                        | Stops
| 1    | the call is well-formed       | bad types, unknown tool
| 2    | args match the schema         | wrong type, extra field, javascript: URL
| 3    | referenced things exist       | click before list_links, index out of range,
|      |                               | domain off the allowlist
| 4    | permitted here, now           | CONSEQUENTIAL without approval, write before read
"""

from __future__ import annotations

from pydantic import ValidationError

from .registry import ToolCall, ToolSpec
from .tiers import Tier
from .tools import BrowserTools, obs_err

class GateError(Exception):
    """A refusal, and what to do about it.

    `hint` was added after a measured failure. F11 fixed the hint on
    `schema_violation` and left the ten gate refusals naming the problem and not
    the fix. The consequence, three runs out of three: told
    `write_before_read: the current page has not been observed yet`, the agent
    retried `open_url`, then answered in prose twice and was stopped - without
    ever calling `read_page`, the one tool that would have cleared the gate. The
    message was accurate and unusable.

    Every refusal here names a next call. A gate that only says no spends a turn
    and teaches nothing.
    """
    def __init__(self, code, msg, hint=""):
        self.code, self.msg, self.hint = code, msg, hint
        super().__init__(msg)


class Dispatcher:
    def __init__(self, tools, registry, allow_consequential=False,
                 require_quote=False, quote_same_page=False):
        self.t = tools
        self.registry = registry
        self.allow_consequential = allow_consequential
        self.require_quote = require_quote
        # Off by default so the run that measured `require_quote` stays
        # comparable. A stricter gate is a hypothesis until a run says otherwise,
        # and three findings in this project came from enforcing one first.
        self.quote_same_page = quote_same_page

    def _refers(self, name, args):
        if name == "click_link":
            links = self.t.links_here()
            if links is None:
                raise GateError("no_links_known",
                                "list_links has not been called on THIS page yet",
                                "Call list_links() to see the links and their "
                                "indices, then click_link(index).")
            if args["index"] >= len(links):
                raise GateError("index_out_of_range",
                                f"index {args['index']} but this page has {len(links)} links",
                                f"Valid indices are 0 to {len(links) - 1}. Call "
                                "list_links() again if you need to see them.")
        # `stop` only claims an answer when reason_type is 'answered'; the other
        # two reasons cite nothing by nature, so the quote rule cannot apply.
        claims_answer = name == "finish" or (
            name == "stop" and args.get("reason_type") == "answered")
        if claims_answer and self.require_quote:
            # GATE 3 for an ANSWER. "Refers to something that exists" has meant
            # a link index or an allowlisted domain; a cited fact is the same
            # kind of claim and was never checked. Run F: the agent answered
            # "example.com is available for registration", a sentence present in
            # no tool result, and passed the grounding guard, the termination
            # guard, the schema and the evidence count.
            #
            # Substring, not similarity: an exact quote needs no threshold, and
            # a page that was never shown cannot be quoted from.
            q = BrowserTools._flat(args.get("evidence_quote", ""))
            if not q:
                raise GateError("quote_missing",
                                "finish needs evidence_quote: the words from the "
                                "page that support this answer",
                                "Copy a sentence from a read_page result into "
                                "evidence_quote, exactly as it appeared.")
            if self.quote_same_page:
                # The claim is not "this sentence was seen somewhere", it is
                # "this sentence is on THAT url". Checked against the merged text
                # of every page, a quote copied from page A passes beside a URL
                # pointing at page B - the same mistake as F1 one level up: the
                # rule was wider than the claim it was guarding.
                url = args.get("evidence_url", "")
                page_text = self.t.seen_text(url)
                if not page_text:
                    raise GateError("evidence_url_not_observed",
                                    f"no tool result came from {url!r}; "
                                    f"pages read so far: {self.t.observed_urls()}",
                                    "Cite one of the pages listed above, or "
                                    "open_url then read_page that one first.")
                if q not in page_text:
                    raise GateError("quote_not_on_cited_page",
                                    f"{args['evidence_quote'][:60]!r} is not in the "
                                    f"text of {url}; cite the page the words are on",
                                    "Set evidence_url to the page you copied the "
                                    "quote from, or quote the page you cited.")
            elif q not in self.t.seen_text():
                raise GateError("quote_not_observed",
                                f"no tool result contains {args['evidence_quote'][:60]!r}; "
                                "quote the page exactly, do not paraphrase",
                                "Call read_page() and copy words from its result "
                                "verbatim. Do not write about the page.")
        if name == "open_url":
            d = BrowserTools.domain(args["url"])
            if not any(d == a or d.endswith("." + a) for a in self.t.allowed_domains):
                raise GateError("domain_not_allowed",
                                f"{d} is outside {sorted(self.t.allowed_domains)}",
                                f"Only {sorted(self.t.allowed_domains)} can be "
                                "opened. If the answer needs another site, this "
                                "task cannot be done from here - say so.")

    def _coheres(self, name, spec):
        if spec.tier is Tier.CONSEQUENTIAL and not self.allow_consequential:
            raise GateError("requires_human_approval",
                            f"{name} is CONSEQUENTIAL; this agent may only propose",
                            f"You cannot perform {name}. Either propose it and "
                            "stop, or end the run through a control tool.")
        # EVERY write-tier call, not just click_link. The prompt has always said
        # to read a new page first; this gate has guarded one of the two WRITE
        # tools since it was written, and the first real-model run walked straight
        # through the gap by calling open_url on an unread page. run_agent.py's
        # docstring listed `write_before_read` as the expected outcome of exactly
        # that run - an error the dispatcher could not produce for it.
        #
        # `open_url` away from an unread page is the same mistake as clicking from
        # one: the agent is acting on a page it has not looked at.
        if spec.tier is Tier.WRITE and not self.t.read_here():
            raise GateError("write_before_read",
                            f"{name}: the current page has not been observed yet",
                            "Call read_page() with no arguments first. It is the "
                            f"only call that clears this gate for {name}.")

    @staticmethod
    def _how_to_fix(name, spec) -> str:
        """A refusal the model cannot act on is a refusal that teaches it nothing.

        The hint used to be `expected: {json of the schema properties}`. For a
        tool taking no arguments that renders as `expected: {}` - an empty object
        and no instruction. Measured consequence: the model called
        `read_page(url=...)`, was refused, read `expected: {}`, concluded the tool
        could not retrieve the page, and ended the task `out_of_scope` with the
        detail "Unable to retrieve page content due to tool limitations".

        It had misread its own malformed call as a fact about the world. The gate
        was right and the message was useless, so the correction never happened.
        """
        props = spec.schema.get("properties") or {}
        if not props:
            return f"{name} takes no arguments. Call it with {{}}."
        required = spec.schema.get("required") or []
        defs = spec.schema.get("$defs") or {}
        parts = []
        for field, meta in props.items():
            # Pydantic puts an Enum in $defs and leaves a $ref behind, so the
            # permitted values - the single most useful thing in the message -
            # are not in `meta` at all unless the reference is followed. The
            # whole point of the merged stop tool is that a wrong value comes
            # back naming the right ones; that only works if they are here.
            ref = meta.get("$ref") or (meta.get("allOf") or [{}])[0].get("$ref")
            if ref:
                meta = {**defs.get(ref.rsplit("/", 1)[-1], {}), **meta}
            kind = meta.get("type", "value")
            allowed = meta.get("enum")
            if allowed:
                kind = " | ".join(repr(a) for a in allowed)
            parts.append(f"{field}: {kind}"
                         + ("" if field in required else " (optional)"))
        return f"{name} takes exactly: " + "; ".join(parts)

    async def dispatch(self, call: ToolCall):
        if not isinstance(call.name, str) or not isinstance(call.args, dict):
            return obs_err("malformed_call", "name must be a string, args an object"), None
        spec = self.registry.get(call.name)
        if spec is None:
            return obs_err("unknown_tool", call.name,
                           f"available: {sorted(self.registry)}"), None
        try:
            clean = spec.args_model.model_validate(call.args).model_dump()
            self._refers(call.name, clean)
            self._coheres(call.name, spec)
        except ValidationError as e:
            f0 = e.errors()[0]
            return obs_err("schema_violation",
                           f"{'.'.join(str(x) for x in f0['loc'])}: {f0['msg']}",
                           self._how_to_fix(call.name, spec)), spec.tier
        except GateError as e:
            return obs_err(e.code, e.msg, e.hint), spec.tier
        return await spec.fn(**clean), spec.tier

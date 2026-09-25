"""The four gates. Nothing touches the world until all four pass.

| Gate | Checks                        | Stops
| 1    | the call is well-formed       | bad types, unknown tool
| 2    | args match the schema         | wrong type, extra field, javascript: URL
| 3    | referenced things exist       | click before list_links, index out of range,
|      |                               | domain off the allowlist
| 4    | permitted here, now           | CONSEQUENTIAL without approval, write before read
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from .registry import ToolCall, ToolSpec
from .tiers import Tier
from .tools import BrowserTools, obs_err

class GateError(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg
        super().__init__(msg)


class Dispatcher:
    def __init__(self, tools, registry, allow_consequential=False):
        self.t = tools
        self.registry = registry
        self.allow_consequential = allow_consequential

    def _refers(self, name, args):
        if name == "click_link":
            links = self.t.links_here()
            if links is None:
                raise GateError("no_links_known",
                                "list_links has not been called on THIS page yet")
            if args["index"] >= len(links):
                raise GateError("index_out_of_range",
                                f"index {args['index']} but this page has {len(links)} links")
        if name == "open_url":
            d = BrowserTools.domain(args["url"])
            if not any(d == a or d.endswith("." + a) for a in self.t.allowed_domains):
                raise GateError("domain_not_allowed",
                                f"{d} is outside {sorted(self.t.allowed_domains)}")

    def _coheres(self, name, spec):
        if spec.tier is Tier.CONSEQUENTIAL and not self.allow_consequential:
            raise GateError("requires_human_approval",
                            f"{name} is CONSEQUENTIAL; this agent may only propose")
        if name == "click_link" and not self.t.read_here():
            raise GateError("write_before_read",
                            "the current page has not been observed yet")

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
                           f"expected: {json.dumps(spec.schema['properties'])[:200]}"), spec.tier
        except GateError as e:
            return obs_err(e.code, e.msg), spec.tier
        return await spec.fn(**clean), spec.tier

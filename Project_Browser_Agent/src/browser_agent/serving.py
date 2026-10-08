"""Week 12. The three things between a notebook and a service.

    serve()    a request/response boundary, where errors stop
    Governor   a budget and a rate limit, enforced BEFORE the model is called
    health()   success, rejection, latency and spend over many requests

None of this makes the agent better. All of it decides whether the agent can be
run by someone who did not write it.

THE ONE DECISION WORTH ARGUING ABOUT is what counts as `ok`. This project has
nine stop reasons and three of them are correct endings that produce no answer:
`blocked`, `out_of_scope` and `pending_approval`. A health report that scores
those as failures would push every future change toward answering more often,
which is the exact behaviour the gates exist to prevent - the report would be
an incentive to remove them.

So the service layer asks a different question from the evaluation harness:

    evaluate.py   did the agent reach the RIGHT ending for this task?
    health()      did the SERVICE do its job on this request?

A grounded refusal is a service that worked. An unterminated run is not - the
agent was silenced by a guard rather than deciding anything, and the caller got
nothing it can act on. The split is in GRACEFUL below, and it is a judgement
with a cost: a service that refuses every request scores 100% healthy here. That
is why the rejection rate is reported beside it and never summed into it.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

# Endings where the agent decided something a caller can act on. The three
# non-answering ones are here deliberately; see the module docstring.
GRACEFUL = {"complete", "blocked", "out_of_scope", "pending_approval",
            "approved", "denied"}


@dataclass
class Request:
    query: str
    session_id: str = "anon"


@dataclass
class Response:
    """Every response carries steps, tokens and ms.

    This is not instrumentation added later; it IS the response. Retrofitting
    these fields means retrofitting them into every caller, and the health
    report below is simply an aggregate over responses - it has no other source
    of truth and no privileged access to the agent.
    """
    status: str                  # ok | rejected | error
    answer: str
    steps: int = 0
    tokens: int = 0
    ms: float = 0.0
    stop_reason: str = ""
    evidence_url: str = ""
    detail: str = ""             # why it was rejected, or which error

    @property
    def served(self) -> bool:
        """Did the caller get something it can act on."""
        return self.status == "ok" and self.stop_reason in GRACEFUL


class Governor:
    """A token budget and a per-session rate limit.

    `check` returns None to allow, or a string saying why not.

    THE ORDER IS THE WHOLE POINT. A budget checked after the model has answered
    is not a budget, it is a receipt. `serve` calls this before it calls the
    agent, so an oversized or too-frequent request costs one dictionary lookup
    rather than one model call.

    `max_chars` is named for what it measures. The lecture's version compares
    `len(req.query)` against something it calls a token budget, and characters
    are not tokens - roughly four to one for English, and nothing like that for
    Arabic. Calling it a token budget would be the unit error from F19 all over
    again: a quantity whose name disagrees with its contents, agreeing with
    itself everywhere until one caller believes the name. It is a cheap proxy
    for size, used at the door, and `token_budget` in the controller remains
    the real one - measured in real tokens, after the call, where it belongs.
    """

    def __init__(self, max_chars: int = 400, rate_limit: int = 5):
        self.max_chars = max_chars
        self.rate_limit = rate_limit
        self._seen: dict[str, int] = {}

    def check(self, req: Request) -> Optional[str]:
        n = self._seen.get(req.session_id, 0) + 1
        self._seen[req.session_id] = n
        if n > self.rate_limit:
            return f"rate limit exceeded ({self.rate_limit} per session)"
        if len(req.query) > self.max_chars:
            return f"request too large ({len(req.query)} > {self.max_chars} chars)"
        return None

    def seen(self, session_id: str) -> int:
        return self._seen.get(session_id, 0)


async def serve(run, req: Request, governor: Optional[Governor] = None) -> Response:
    """One request in, one Response out. Nothing raises past this line.

    `run` is any coroutine taking a query string and returning a RunResult, so
    the serving layer never imports the loop and the loop never learns it is
    being served. The tests pass a fake; `scripts/serve_demo.py` passes the real
    agent.

    WHY THE try/except IS NOT LAZINESS. In a notebook an exception ends your
    session. In a service it ends somebody else's request, and unhandled, it
    ends everybody's. The caller is handed a status and a type name, never a
    stack trace: a traceback crossing a service boundary is an information leak
    and is unusable by the caller anyway.

    The rejection path returns BEFORE `run` is awaited. That is the governor's
    entire value, and it is asserted in the tests rather than trusted here.
    """
    t0 = time.perf_counter()

    if governor is not None:
        why = governor.check(req)
        if why:
            return Response("rejected", "", ms=(time.perf_counter() - t0) * 1000,
                            detail=why)

    try:
        res = await run(req.query)
    except Exception as e:
        # The type name, not the message: a message can carry a file path, a
        # URL or a fragment of the prompt.
        return Response("error", "", ms=(time.perf_counter() - t0) * 1000,
                        detail=f"internal error: {type(e).__name__}")

    ev = res.evidence[0]["url"] if getattr(res, "evidence", None) else ""
    return Response("ok", res.detail or "", steps=len(res.trace),
                    tokens=sum(t.get("tokens", 0) for t in res.trace),
                    ms=(time.perf_counter() - t0) * 1000,
                    stop_reason=res.stop_reason, evidence_url=ev)


@dataclass
class HealthReport:
    requests: int = 0
    served: int = 0              # ok AND the agent decided something
    stalled: int = 0             # ok but the run was capped or silenced
    rejected: int = 0            # the governor said no, before any spend
    errors: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    tokens: int = 0
    by_stop_reason: dict = field(default_factory=dict)

    @property
    def serve_rate(self) -> float:
        return self.served / self.requests if self.requests else 0.0

    @property
    def rejection_rate(self) -> float:
        return self.rejected / self.requests if self.requests else 0.0


def health(responses: list[Response]) -> HealthReport:
    """A pulse, not a photograph. A benchmark score says what happened once.

    WHY p95 AND NOT THE MEAN. The mean hides the tail, and the tail is what
    people complain about: a 200 ms mean with a 9 s p95 is a product one person
    in twenty believes is broken. Both are reported because p50 says what a
    typical request feels like and p95 says what the worst twentieth feels like,
    and neither answers for the other.

    WHY THE REJECTION RATE IS A DESIGN SIGNAL. If it climbs, either the limits
    are wrong or the traffic changed - and those need opposite responses. It is
    reported separately and never folded into the success rate, because a
    service that rejects everything would otherwise look like a service with no
    failures.

    `stalled` is this project's own addition: an `ok` response whose run ended
    in a cap or a guard. Those did not error and did not serve the caller, and
    collapsing them into either number would hide the most actionable failure
    the agent has.
    """
    r = HealthReport(requests=len(responses))
    if not responses:
        return r

    for resp in responses:
        if resp.status == "rejected":
            r.rejected += 1
        elif resp.status == "error":
            r.errors += 1
        elif resp.served:
            r.served += 1
        else:
            r.stalled += 1
        if resp.stop_reason:
            r.by_stop_reason[resp.stop_reason] = \
                r.by_stop_reason.get(resp.stop_reason, 0) + 1
        r.tokens += resp.tokens

    # Latency over EVERY request, rejections included. A rejected request still
    # took the caller's time, and excluding the fast ones would flatter the
    # numbers in exactly the direction the governor pushes them.
    lat = sorted(resp.ms for resp in responses)
    r.p50_ms, r.p95_ms = percentile(lat, 0.50), percentile(lat, 0.95)
    return r


def percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank: the smallest value at or below which at least p of the
    samples fall.

    Written out because the obvious one-liner is wrong in a direction that
    flatters. `values[int(p * (n - 1))]` truncates, so at n = 20 and p = 0.95 it
    returns index 18 - the nineteenth of twenty - and a single nine-second
    request lands at index 19 and is never reported. The version that caught
    this was a test asserting a 9 s tail would show up, and it did not.

    It is not a large error and it is always in the same direction: the reported
    tail is at or below the real one, so a service looks faster than it is. That
    is the worst direction for a number whose entire job is to show the tail.

    AND A PERCENTILE NEEDS SAMPLES. At n = 20 the top 5% is one request, so p95
    can only ever be the 19th or the 20th value and cannot distinguish "one slow
    request" from "one catastrophic one". `health()` reports what it has; a p95
    over a handful of requests is a number, not a measurement, and the request
    count is printed beside it so the reader can see which they are looking at.
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    idx = max(0, min(n - 1, math.ceil(p * n) - 1))
    return sorted_values[idx]


def format_health(r: HealthReport, title: str = "health") -> str:
    lines = [f"{title}:",
             f"  requests      {r.requests}",
             f"  served        {r.served}  ({r.serve_rate:.0%})",
             f"  stalled       {r.stalled}   capped or silenced, not an error",
             f"  rejected      {r.rejected}  ({r.rejection_rate:.0%}) "
             f"before the model was called",
             f"  errors        {r.errors}",
             f"  latency       p50 {r.p50_ms:.0f}ms   p95 {r.p95_ms:.0f}ms",
             f"  tokens        {r.tokens}"]
    if r.by_stop_reason:
        endings = ", ".join(f"{k} {v}" for k, v in
                            sorted(r.by_stop_reason.items()))
        lines.append(f"  endings       {endings}")
    return "\n".join(lines)

"""Week 12: the serving boundary, the governor, and the health report.

The two tests that matter are the ordering one and the tail one. Everything
else here is arithmetic.
"""

import pytest

from browser_agent.serving import (
    Governor, HealthReport, Request, Response, format_health, health, serve,
)


class FakeRun:
    """Stands in for the agent. Records whether it was called at all, which is
    the only way to prove the governor rejected BEFORE the spend."""

    def __init__(self, stop_reason="complete", detail="an answer",
                 steps=3, tokens=1200, raises=None, evidence=True):
        self.calls = 0
        self._kw = dict(stop_reason=stop_reason, detail=detail, steps=steps,
                        tokens=tokens, raises=raises, evidence=evidence)

    async def __call__(self, query):
        self.calls += 1
        if self._kw["raises"]:
            raise self._kw["raises"]
        return type("R", (), {
            "stop_reason": self._kw["stop_reason"],
            "detail": self._kw["detail"],
            "trace": [{"tokens": self._kw["tokens"] // max(self._kw["steps"], 1)}]
                     * self._kw["steps"],
            "evidence": ([{"url": "https://example.com/"}]
                         if self._kw["evidence"] else []),
        })()


# ------------------------------------------------- the governor's ordering
async def test_an_oversized_request_never_reaches_the_model():
    """The entire value of a governor is in the word BEFORE. A budget checked
    after the answer came back is a receipt, not a budget - it tells you what
    you spent and prevents nothing.

    Asserted by counting calls, because "it was rejected" and "it was rejected
    without being run" are different claims and only the second one saves money.
    """
    run = FakeRun()
    gov = Governor(max_chars=50)
    resp = await serve(run, Request("x" * 600), gov)

    assert resp.status == "rejected"
    assert run.calls == 0                     # THE ASSERTION
    assert "too large" in resp.detail


async def test_the_rate_limit_is_per_session_and_the_sixth_request_is_refused():
    run, gov = FakeRun(), Governor(rate_limit=5)
    for _ in range(5):
        assert (await serve(run, Request("hi", "osama"), gov)).status == "ok"
    assert (await serve(run, Request("hi", "osama"), gov)).status == "rejected"
    assert run.calls == 5

    # A second caller is unaffected: the limit exists to stop ONE caller
    # consuming the capacity of all the others.
    assert (await serve(run, Request("hi", "someone-else"), gov)).status == "ok"


async def test_without_a_governor_nothing_is_refused():
    """The governor is optional, and its absence must be visible in the code
    rather than implied by a default that quietly limits a measured run."""
    run = FakeRun()
    assert (await serve(run, Request("x" * 10_000))).status == "ok"
    assert run.calls == 1


# ------------------------------------------------- the boundary
async def test_one_bad_request_does_not_take_the_service_down():
    run = FakeRun(raises=ValueError("the page exploded"))
    resp = await serve(run, Request("q"))

    assert resp.status == "error"
    assert resp.detail == "internal error: ValueError"


async def test_the_caller_is_given_a_type_not_a_stack_trace():
    """A message can carry a file path, a URL, or a fragment of the prompt. The
    type name says what went wrong without saying where the server keeps it."""
    run = FakeRun(raises=RuntimeError("/home/osama/secret/key.pem not found"))
    resp = await serve(run, Request("q"))

    assert "secret" not in resp.detail
    assert "key.pem" not in resp.detail


async def test_every_response_carries_steps_tokens_and_latency():
    """Not instrumentation added later: the response IS the measurement. The
    health report has no other source of truth."""
    resp = await serve(FakeRun(steps=4, tokens=1200), Request("q"))
    assert resp.steps == 4 and resp.tokens == 1200
    assert resp.ms > 0
    assert resp.evidence_url == "https://example.com/"


# ------------------------------------------------- what counts as served
@pytest.mark.parametrize("stop_reason,served", [
    ("complete",         True),
    ("blocked",          True),    # it told the caller what it needs
    ("out_of_scope",     True),    # it told the caller this is not its job
    ("pending_approval", True),    # it told the caller a human must decide
    ("unterminated",     False),   # a guard silenced it; the caller got nothing
    ("ungrounded",       False),
    ("capped_steps",     False),
    ("capped_time",      False),
])
async def test_a_grounded_refusal_is_a_served_request_and_a_cap_is_not(
        stop_reason, served):
    """The judgement this module exists to make, as a table.

    Scoring `blocked` as a failure would make every future change an incentive
    to answer more often - which is precisely what the gates were built to
    prevent. The health report would become an argument for removing them.

    A cap is the other way round. Nothing was decided, the caller has nothing to
    act on, and it is not an error either: `stalled` is its own number so it
    cannot hide inside the other two.
    """
    resp = await serve(FakeRun(stop_reason=stop_reason), Request("q"))
    assert resp.status == "ok"
    assert resp.served is served


# ------------------------------------------------- the health report
def R(status="ok", ms=100.0, stop_reason="complete", tokens=0):
    return Response(status, "", ms=ms, stop_reason=stop_reason, tokens=tokens)


def test_p95_is_not_the_mean_and_that_is_the_whole_point():
    """A hundred requests: most fast, a few slow, ten very slow.

    The mean reads about one second, which sounds like a service with a minor
    problem. p50 says a typical request takes 150 ms. p95 says one request in
    ten takes nine seconds. Only the third of those is what the complaining
    users are describing, and a service tuned on the mean optimises for the
    eighty-five people who were never going to complain.
    """
    rows = [R(ms=150.0)] * 85 + [R(ms=400.0)] * 5 + [R(ms=9000.0)] * 10
    r = health(rows)

    mean = sum(x.ms for x in rows) / len(rows)
    assert 1000 < mean < 1100              # "about a second"
    assert r.p50_ms == 150.0               # typical: fast
    assert r.p95_ms == 9000.0              # the tail: nine seconds


def test_a_percentile_over_twenty_requests_is_a_number_not_a_measurement():
    """The honest limit of the metric, asserted so nobody has to rediscover it.

    At n = 20 the top 5% is a single request, so p95 is pinned to the 19th or
    20th value whatever the tail actually looks like. These two windows differ
    by a factor of sixty in their worst request and report the same p95.

    The first version of `percentile` was worse than this: it truncated, so the
    slow request was excluded from BOTH windows and the service looked faster
    than it was. The error was small, and always in the flattering direction.
    """
    mild     = health([R(ms=200.0)] * 19 + [R(ms=500.0)])
    terrible = health([R(ms=200.0)] * 19 + [R(ms=30_000.0)])

    assert mild.p95_ms == terrible.p95_ms == 200.0
    assert mild.requests == terrible.requests == 20


def test_the_rejection_rate_is_reported_beside_the_serve_rate_never_inside_it():
    """A service that rejects everything has no failures. Summing rejections
    into either success or error would make that look healthy, so they are
    three separate counts over one denominator."""
    r = health([R("rejected", ms=1.0)] * 8 + [R(ms=100.0)] * 2)

    assert r.rejected == 8 and r.served == 2 and r.errors == 0
    assert r.rejection_rate == 0.8
    assert r.serve_rate == 0.2
    assert r.served + r.rejected + r.errors + r.stalled == r.requests


def test_latency_includes_the_rejections_it_would_otherwise_flatter():
    """Rejections are fast by construction. Excluding them would improve every
    percentile in exactly the direction the governor pushes them, so a tighter
    limit would read as a faster service."""
    slow_only = health([R(ms=500.0)] * 10)
    with_rejections = health([R("rejected", ms=1.0)] * 10 + [R(ms=500.0)] * 10)

    assert slow_only.p50_ms == 500.0
    assert with_rejections.p50_ms == 1.0


def test_the_four_outcomes_partition_the_requests():
    r = health([R(), R("rejected"), R("error"), R(stop_reason="capped_steps")])
    assert (r.served, r.rejected, r.errors, r.stalled) == (1, 1, 1, 1)
    assert r.requests == 4


def test_endings_are_counted_by_name_not_just_totalled():
    """`capped` taught this project that a count without the names is a number
    nobody can act on. Two stalls for two different reasons need two fixes."""
    r = health([R(stop_reason="complete"), R(stop_reason="complete"),
                R(stop_reason="blocked"), R(stop_reason="capped_steps")])
    assert r.by_stop_reason == {"complete": 2, "blocked": 1, "capped_steps": 1}


def test_an_empty_window_reports_nothing_rather_than_a_perfect_score():
    """Zero requests is not 100% healthy. A monitor that reads a quiet window as
    a passing one is the `capabilities()` mistake: a plausible number produced
    by the absence of data."""
    r = health([])
    assert r.requests == 0
    assert r.serve_rate == 0.0 and r.rejection_rate == 0.0


def test_the_report_prints_without_a_traceback_on_an_empty_window():
    assert "requests      0" in format_health(health([]))
    assert "p95" in format_health(health([R()]))

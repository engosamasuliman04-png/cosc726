"""Aggregation across repeats. Week 11.

This lives in the package rather than in `scripts/evaluate.py` for one reason:
every instrument this project got wrong was one that nothing could call except
the harness itself. `mem_wrote` read the wrong field, `via_tool` read a
dict the prose exit also set, and `payload_landed` asked the trace instead of
the message - three numbers that were plausible, published and false, and none
of them had a unit test, because none of them was reachable from one.

A metric is a function. It takes rows and returns numbers, and a test can hand
it rows whose answer is known.
"""

from __future__ import annotations

THREE = ("pass@1", "pass@k", "pass^k")


def scorecard(rows: list[dict], labels: list[str], key: str = "correct") -> dict:
    """Week 11's three numbers, over the repeats of each task.

    They are three because they answer three different questions, and a project
    that reports one of them has not said which:

        pass@1   what a single run reported
        pass@k   did it EVER succeed
        pass^k   did it succeed EVERY time

    tau-bench introduced the third because an agent that works one time in eight
    is not a working agent, and the first two cannot tell you the difference. On
    `[True, False, True]` they read 100%, 100% and 0%.

    `pass@1` is the FIRST repeat, deliberately, and not an average over them. An
    average is the better estimate and the worse exhibit: this row exists to say
    "here is the number you would have published had you run once", and this
    project published exactly that number for its first fourteen runs - run O
    scored 3/4 and was written down before run P, three repeats of the same
    code, scored 4/12.

    `labels` is passed in rather than derived from `rows` so the ORDER is the
    task list's, and so a task that produced no rows at all is absent rather
    than silently scored. A task that never ran is not a task that failed.

    The flaky list is the most useful output here. A failing case has a cause you
    can find; a flaky case has a cause you will chase for a week if nobody named
    it flaky first - and at k=1 it is invisible, because it looks like whichever
    of its two outcomes you happened to draw.
    """
    per: dict[str, list[bool]] = {}
    for label in labels:
        got = [bool(r[key]) for r in rows if r["task"] == label]
        if got:
            per[label] = got
    if not per:
        return {}
    return {
        "n": len(per),
        "pass@1": sum(v[0] for v in per.values()),
        "pass@k": sum(any(v) for v in per.values()),
        "pass^k": sum(all(v) for v in per.values()),
        "flaky": sorted(l for l, v in per.items() if any(v) and not all(v)),
    }


def independent_repeats(temperature: float, vary_seed: bool) -> bool:
    """Whether a pass^k from these settings means anything.

    Greedy decoding never consults the seed, so at temperature 0 the repeats are
    one trajectory printed N times and pass^k equals pass@1 by construction. The
    harness already refuses this combination for `--attack`; here it is reported
    rather than refused, because repeating a pinned configuration is a
    legitimate check that the HARNESS is deterministic. It is simply not a
    variance measurement, and this project has already published one number that
    confused the two.
    """
    return bool(vary_seed and temperature > 0)

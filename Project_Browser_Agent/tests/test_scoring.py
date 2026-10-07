"""Week 11: the three numbers, and the one that disagrees with the other two.

The point of these tests is not that `sum` works. It is that pass@1, pass@k and
pass^k are DIFFERENT numbers on the same data, because a project that reports
one of them has published a number without saying which question it answers.
"""

import pytest

from browser_agent import independent_repeats, scorecard


def rows(**by_label):
    """`rows(T1=[True, False, True])` -> harness rows, in repeat order."""
    return [{"task": label, "correct": ok, "strict": ok}
            for label, oks in by_label.items()
            for ok in oks]


def test_the_three_numbers_disagree_on_the_same_data():
    """Week 11's slide, as an assertion. One flaky task out of one:
    the first run passed, so pass@1 is 100%; it passed at least once, so pass@k
    is 100%; it did not pass every time, so pass^k is 0%.

    This is the whole argument for reporting three numbers. A demo reports the
    second, a single run reports the first, and a user experiences the third.
    """
    sc = scorecard(rows(T1=[True, False, True]), ["T1"])
    assert (sc["pass@1"], sc["pass@k"], sc["pass^k"]) == (1, 1, 0)
    assert sc["flaky"] == ["T1"]


def test_pass_at_1_is_the_first_repeat_not_the_best_one():
    """If `pass@1` were "did any single run pass", it would be pass@k under
    another name. It is the FIRST run, which is the one a project that ran once
    would have published - and this project published exactly that for fourteen
    runs before it started repeating them."""
    assert scorecard(rows(T1=[False, True, True]), ["T1"])["pass@1"] == 0
    assert scorecard(rows(T1=[True, False, False]), ["T1"])["pass@1"] == 1


def test_a_task_that_always_passes_is_not_flaky_and_one_that_never_does_is_not_either():
    """Flaky means NEITHER passing nor failing. A consistently failing task has a
    cause you can go and find; calling it flaky would send you looking for
    variance that is not there."""
    sc = scorecard(rows(good=[True, True], bad=[False, False]), ["good", "bad"])
    assert sc["flaky"] == []
    assert (sc["pass@1"], sc["pass@k"], sc["pass^k"]) == (1, 1, 1)


def test_a_single_repeat_makes_all_three_identical():
    """k=1 is where the flaky task hides: with one run per task the three numbers
    cannot disagree, whatever the agent actually does."""
    sc = scorecard(rows(T1=[True], T2=[False]), ["T1", "T2"])
    assert sc["pass@1"] == sc["pass@k"] == sc["pass^k"] == 1
    assert sc["flaky"] == []


def test_a_task_that_never_ran_is_absent_rather_than_failed():
    """`--only` runs a subset. Scoring the absent tasks as failures would make
    every filtered run look like a regression."""
    sc = scorecard(rows(T1=[True]), ["T1", "T2 never ran"])
    assert sc["n"] == 1


def test_strict_is_scored_over_the_same_repeats_as_loose():
    """The two rows of the scorecard must come from the same runs. A strict rate
    computed over a different denominator is the `capped` mistake again: two
    numbers that look comparable and are not."""
    data = [{"task": "T1", "correct": True,  "strict": False},
            {"task": "T1", "correct": True,  "strict": True}]
    assert scorecard(data, ["T1"], "correct")["pass^k"] == 1
    assert scorecard(data, ["T1"], "strict")["pass^k"] == 0
    assert scorecard(data, ["T1"], "strict")["flaky"] == ["T1"]


def test_an_empty_run_returns_nothing_rather_than_zero_of_zero():
    assert scorecard([], ["T1"]) == {}


@pytest.mark.parametrize("temp,vary,ok", [
    (0.0, False, False),   # greedy, pinned seed: one trajectory printed N times
    (0.0, True,  False),   # greedy NEVER consults the seed - F14's second half
    (0.7, False, False),   # sampling, but every repeat draws the same seed
    (0.7, True,  True),
])
def test_only_one_of_four_settings_produces_independent_repeats(temp, vary, ok):
    """The pass^k number is meaningless unless the repeats differ. Three of these
    four combinations look like a variance measurement and are not, and the
    middle one is the trap: `--vary-seed` at temperature 0 varied nothing, and
    the identical token counts across three "attempts" are what gave it away."""
    assert independent_repeats(temp, vary) is ok

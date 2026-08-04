#!/usr/bin/env python3
"""
COSC726 - Agentic Artificial Intelligence
Week 2 / Lab 1 - llm_foundations.py  (STUDENT SKELETON)

Treat a model interface as an object of measurement. This file uses ONLY the
Python standard library - no API key, no network, no third-party packages.

You will implement THREE functions, each marked with `TODO`:
    1. count_tokens(text, tokenizer)   - token counting for a teaching tokenizer
    2. prepare_context(...)            - explicit context budgeting
    3. sample_next(distribution, ...)  - a transparent sampler

Run the self-test when you are done:
    python COSC726_W02_llm_foundations.py --self-test

Everything is deterministic. The running example is the customer-support agent
helping Layla with order #A1032, carried over from Week 1.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────────
# Two deliberately DIFFERENT teaching tokenizers. Neither is authoritative; the
# point is that the same text costs a different number of tokens under each.
# ─────────────────────────────────────────────────────────────────────────────
TOKENIZER_A = {
    "vocab": ["order", "agent", "the", "credit", "policy", "late", "1043",
              "ic", "ing", "ed", " ", "-", "A", "#"],
    "name": "teach-A",
}
TOKENIZER_B = {
    "vocab": ["order", "ag", "ent", "the", "cred", "it", "pol", "icy", "late",
              "10", "43", "ic", "ing", "ed", " ", "-", "A", "#"],
    "name": "teach-B",
}


def _greedy_split(text: str, vocab: list[str]) -> list[str]:
    """Greedy longest-match tokenisation against a vocab; unknown chars stand alone."""
    vocab = sorted(vocab, key=len, reverse=True)
    text, out, i = text.lower(), [], 0
    while i < len(text):
        for v in vocab:
            if v and text.startswith(v.lower(), i):
                out.append(v)
                i += len(v)
                break
        else:
            out.append(text[i])
            i += 1
    return out


def count_tokens(text: str, tokenizer: dict) -> int:
    """
    TODO 1
    Return the NUMBER OF TOKENS `text` produces under `tokenizer`.

    Use the provided helper `_greedy_split(text, tokenizer["vocab"])` to get the
    list of token strings, then return how many there are.

    Why this matters: token counts are model-specific. The same text costs
    different amounts under TOKENIZER_A and TOKENIZER_B - your lab observation
    log must record WHICH tokenizer produced each count.
    """
    # TODO: replace the line below
    return len(_greedy_split(text, tokenizer["vocab"]))


@dataclass
class ContextPlan:
    kept: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    rejected: bool = False
    reason: str = ""


def prepare_context(messages, context_limit, reserved_output, strategy, tokenizer):
    """
    Plan which messages fit into the context window.

    Count each message with count_tokens(msg, tokenizer).

    Strategies:
      - reject: reject if messages do not fit.
      - drop_oldest: keep system message and drop oldest messages.
    """

    budget = context_limit - reserved_output

    def total(msgs):
        return sum(count_tokens(m, tokenizer) for m in msgs)

    if strategy == "reject":
        if total(messages) > budget:
            return ContextPlan(
                kept=[],
                dropped=[],
                rejected=True,
                reason=f"input {total(messages)} > budget {budget}"
            )

        return ContextPlan(
            kept=list(messages),
            dropped=[],
            rejected=False,
            reason=""
        )

    if strategy != "drop_oldest":
        raise ValueError(f"Unknown strategy: {strategy}")

    kept = list(messages)
    dropped = []

    # index 0 is the system message and is never dropped -> always pop(1)
    while total(kept) > budget and len(kept) > 1:
        dropped.append(kept.pop(1))

    still_too_big = total(kept) > budget
    return ContextPlan(
        kept=kept,
        dropped=dropped,
        rejected=still_too_big,
        reason=f"system message alone {total(kept)} > budget {budget}" if still_too_big else ""
    )



def sample_next(distribution: dict[str, float], temperature: float,
                rng: random.Random) -> str:
    """
    TODO 3
    Return ONE token sampled from `distribution` (token -> probability).

    Rules:
      - temperature <= 0 : greedy / argmax - return the highest-probability token
                           (break ties by choosing the alphabetically first).
      - temperature  > 0 : rescale by temperature, softmax-normalise, then sample
                           using `rng` (use rng.random() and a cumulative walk).

    Temperature changes DIVERSITY, not truth. The lab asks you to show that at
    temperature 0 the same token wins every time.
    """
    # --- greedy branch: temperature <= 0 -> always the mode ---
    if temperature <= 0:
        top = max(distribution.values())
        # ties broken alphabetically, as the spec requires
        return sorted(k for k, v in distribution.items() if v == top)[0]

    # --- stochastic branch: rescale by temperature, then softmax-normalise ---
    # log(p)/T is the logit rescaling; exp() brings it back to an unnormalised weight
    logits = {k: math.log(v) / temperature for k, v in distribution.items() if v > 0}
    m = max(logits.values())                       # numerical stability shift
    scaled = {k: math.exp(l - m) for k, l in logits.items()}
    z = sum(scaled.values())

    # inverse-CDF sampling: one uniform draw, walk the cumulative mass
    r = rng.random()
    acc = 0.0
    for k in sorted(scaled):
        acc += scaled[k] / z
        if r <= acc:
            return k
    return sorted(scaled)[-1]                      # float-rounding fallback



# ─────────────────────────────────────────────────────────────────────────────
# Self-test harness (provided - do not edit). Run with --self-test.
# ─────────────────────────────────────────────────────────────────────────────
def _run_self_test() -> int:
    failures = []

    # 1 - token counting differs across tokenizers
    try:
        a = count_tokens("order A-1043", TOKENIZER_A)
        b = count_tokens("order A-1043", TOKENIZER_B)
        assert isinstance(a, int) and isinstance(b, int), "counts must be ints"
        assert a > 0 and b > 0, "counts must be positive"
    except Exception as e:  # noqa: BLE001
        failures.append(f"count_tokens: {e}")

    # 2 - context budgeting: reject vs drop_oldest
    try:
        msgs = ["system rules", "turn one", "turn two", "turn three about the credit"]
        rej = prepare_context(msgs, context_limit=8, reserved_output=4,
                              tokenizer=TOKENIZER_A, strategy="reject")
        assert rej.rejected is True and rej.reason, "reject must set rejected + reason"
        drop = prepare_context(msgs, context_limit=40, reserved_output=4,
                               tokenizer=TOKENIZER_A, strategy="drop_oldest")
        assert drop.kept and drop.kept[0] == "system rules", "system message must be kept"
    except Exception as e:  # noqa: BLE001
        failures.append(f"prepare_context: {e}")

    # 3 - sampler: greedy is deterministic; argmax picks the mode
    try:
        dist = {"Paris": 0.82, "London": 0.11, "Lyon": 0.05, "Rome": 0.02}
        picks = {sample_next(dist, 0.0, random.Random(s)) for s in range(5)}
        assert picks == {"Paris"}, "temperature 0 must always return the mode"
        hot = sample_next(dist, 1.0, random.Random(1))
        assert hot in dist, "temperature>0 must return a token from the distribution"
    except Exception as e:  # noqa: BLE001
        failures.append(f"sample_next: {e}")

    if failures:
        print("SELF-TEST FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("ALL SELF-TESTS PASSED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="COSC726 Week 2 lab - LLM foundations")
    parser.add_argument("--self-test", action="store_true", help="run the self-test suite")
    args = parser.parse_args(argv)
    if args.self_test:
        return _run_self_test()
    print("Nothing to run. Implement the three TODOs, then: --self-test")
    print("You have 3 TODO(s): count_tokens, prepare_context, sample_next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

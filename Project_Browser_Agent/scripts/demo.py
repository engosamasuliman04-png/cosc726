#!/usr/bin/env python3
"""The demonstration launcher. One command, a numbered menu, no flags.

    python scripts/demo.py

Everything here is reachable through the other scripts with the right flags.
What this adds is that nobody has to remember them - including the author, who
has twice pasted a file path into a shell because it looked like a command.

It is also the thing to run in front of an examiner. Each entry says what it
costs in minutes and what it is supposed to PROVE, so a demonstration that goes
badly is still a demonstration: the claim was stated before the run started.
"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable          # the interpreter running this file: the venv's

# label, minutes, what it proves, argv after the script name
MENU = [
    ("Open the web interface",       1,
     "Week 12's serving layer with a face: open http://localhost:8000",
     ["web.py"]),
    ("Web interface, on Wikipedia",  1,
     "the same, pointed at a site with real content",
     ["web.py", "--allow", "en.wikipedia.org",
      "--start", "https://en.wikipedia.org/wiki/Main_Page"]),
    ("Talk to the agent",            6, "the whole system, interactively",
     ["chat.py", "--save", "demo_chat.txt"]),
    ("Talk to it about Wikipedia",   6, "the same agent on a page with real content",
     ["chat.py", "--allow", "en.wikipedia.org",
      "--start", "https://en.wikipedia.org/wiki/Sudan", "--save", "demo_wiki.txt"]),
    ("Talk to it in Arabic (Arabic Wikipedia)", 6,
     "the same stack on ar.wikipedia.org - a demonstration, not a measurement",
     ["chat.py", "--allow", "ar.wikipedia.org",
      "--start", "https://ar.wikipedia.org/wiki/%D8%A7%D9%84%D8%B3%D9%88%D8%AF%D8%A7%D9%86",
      "--save", "demo_arabic.txt"]),
    ("Browse: open any Wikipedia article", 8,
     "open_url inside the allowlist - a general question, answered from a page",
     ["chat.py", "--allow", "en.wikipedia.org",
      "--start", "https://en.wikipedia.org/wiki/Main_Page",
      "--save", "demo_browse.txt"]),
    ("One question, with the browser visible", 4,
     "the gates refusing a live model, step by step",
     ["run_agent.py", "--headed", "--slow", "800", "--timeout", "180",
      "--deadline", "600", "--goal",
      "What is the exact heading text on this page?"]),
    ("Clean traffic vs messy traffic", 16,
     "the lab-to-production gap: identical agent, 75% and 0%",
     ["traffic.py", "--out", "demo_traffic.json"]),
    ("Injection: with the payload and without", 22,
     "whether the attack broke the task, or the agent breaks anyway",
     ["traffic.py", "--ab", "--temp", "0.7", "--out", "demo_ab.json"]),
    ("The full evaluation, 5 tasks x 3",  62,
     "pass@1, pass@k and pass^k - the three numbers",
     ["evaluate.py", "--cold", "--repeat", "3", "--vary-seed", "--temp", "0.7",
      "--refuse-claimed-action", "--out", "demo_eval.json"]),
    ("The evaluation with no model at all", 1,
     "the harness itself, in seconds, when the point is the scorecard",
     ["evaluate.py", "--client", "heuristic", "--repeat", "3",
      "--out", "demo_heuristic.json"]),
    ("The test suite",               1, "184 tests, none of which need a model",
     None),
]


def say(text=""):
    print(text, flush=True)


def check_model() -> bool:
    """Is Ollama up, and is the model loaded?

    The first live run of a session times out at 90 seconds because the weights
    are being read from disk, and that failure looks exactly like a broken
    agent - it cost an hour the first time. So the warm-up is part of the
    launcher rather than something to remember.
    """
    sys.path.insert(0, str(ROOT / "src"))
    from browser_agent import config
    from browser_agent.ollama_client import (HttpTransport, OllamaBackend,
                                             OllamaError)

    model = config.model()
    say(f"  model : {model}")
    say(f"  host  : {config.host()}")
    backend = OllamaBackend(model, transport=HttpTransport(timeout=180))
    try:
        caps = backend.capabilities()
    except OllamaError as e:
        say(f"\n  Ollama did not answer: {e}")
        say("  Start it, then run this again. On Windows that is the Ollama")
        say("  app in the system tray.")
        return False
    say(f"  tools : {'yes' if 'tools' in caps else 'no (prose path)'}")

    say("\n  waking the model (first call reads the weights from disk)...")
    t0 = time.time()
    try:
        backend.chat([{"role": "user", "content": "ok"}])
    except OllamaError as e:
        say(f"  the model did not answer: {e}")
        return False
    secs = time.time() - t0
    say(f"  awake in {secs:.0f}s")
    if secs > 60:
        say("  that is slow - this machine is running the model on the CPU.")
        say("  Expect each step of a run to take a minute or more.")
    return True


def run(argv: list[str]) -> None:
    cmd = [PY, str(ROOT / "scripts" / argv[0]), *argv[1:]]
    say("\n  " + " ".join(argv))
    say("  " + "-" * 60)
    # Inherited stdout, so the chat script can still read from the keyboard and
    # every run prints as it goes rather than in one block at the end.
    subprocess.run(cmd, cwd=ROOT)


def main():
    say("=" * 66)
    say("  Autonomous Web Browser Agent - COSC726")
    say("=" * 66)

    if not check_model():
        say("\n  Option 8 (the test suite) still works without a model.")

    while True:
        say()
        say("  " + "-" * 60)
        for i, (label, mins, proves, _) in enumerate(MENU, 1):
            say(f"  {i}. {label:<42} ~{mins} min")
            say(f"     {proves}")
        say("  0. quit")
        say("  " + "-" * 60)

        try:
            choice = input("\n  choose > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if choice in ("0", "q", "quit", "exit"):
            break
        if not choice.isdigit() or not 1 <= int(choice) <= len(MENU):
            say("  pick a number from the list.")
            continue

        label, mins, proves, argv = MENU[int(choice) - 1]
        say(f"\n  {label}")
        say(f"  this should show: {proves}")
        say(f"  about {mins} minute(s). Ctrl+C stops it.")

        if argv is None:
            subprocess.run([PY, "-m", "pytest"], cwd=ROOT)
            continue
        if mins >= 15:
            # The long ones hold the machine and must not be started by a
            # mistyped digit.
            ok = input(f"  this takes about {mins} minutes. type yes > ").strip()
            if ok.lower() not in ("yes", "y"):
                continue
        try:
            run(argv)
        except KeyboardInterrupt:
            say("\n  stopped.")

    say("\n  done.")


if __name__ == "__main__":
    main()

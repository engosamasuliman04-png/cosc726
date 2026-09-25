"""One place to name the model, so four scripts cannot disagree about it.

Precedence, highest first:
    1. an explicit --model on the command line
    2. the BROWSER_AGENT_MODEL environment variable
    3. a `model = ...` line in a `.env` file beside the project root
    4. DEFAULT_MODEL below

Set it once:

    # macOS / Linux
    export BROWSER_AGENT_MODEL=qwen3:1.7b

    # Windows PowerShell
    $env:BROWSER_AGENT_MODEL = "qwen3:1.7b"

    # or, portable and permanent: write a .env file in the project root
    echo "model = qwen3:1.7b" > .env
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MODEL = "qwen3:1.7b"
DEFAULT_HOST = "http://localhost:11434"

# Ollama needs the FULL tag, including the size. `qwen3` and `qwen3:1.7b` are not
# the same thing to the server, and a mismatch surfaces as HTTP 404 several calls
# later, where it reads like a code fault rather than a name fault.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _from_env_file(key: str) -> str | None:
    """Read `key = value` from .env. No dependency, no interpolation, no surprises."""
    if not _ENV_FILE.exists():
        return None
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().lower() == key:
            return v.strip().strip("'\"") or None
    return None


def model() -> str:
    return (os.environ.get("BROWSER_AGENT_MODEL")
            or _from_env_file("model")
            or DEFAULT_MODEL)


def host() -> str:
    return (os.environ.get("BROWSER_AGENT_HOST")
            or _from_env_file("host")
            or DEFAULT_HOST)


def source() -> str:
    """Where the model name came from - printed so a wrong name is traceable."""
    if os.environ.get("BROWSER_AGENT_MODEL"):
        return "BROWSER_AGENT_MODEL"
    if _from_env_file("model"):
        return f"{_ENV_FILE.name} file"
    return "DEFAULT_MODEL in config.py"

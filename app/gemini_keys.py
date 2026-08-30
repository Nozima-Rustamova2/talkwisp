"""Several Gemini keys, rotated when one runs out of daily quota.

Free-tier quota is counted per project per model per day, so separate keys are
separate budgets. Two full test-set runs exhausted a single key twice in one
day, and running dry mid-demo lands you on an unvalidated model or on nothing.

Rotation is failure-driven, not round-robin: a key is used until it returns 429,
then the next one is tried immediately. That keeps requests on one key while it
still has budget, instead of spreading load and exhausting all of them at once.

Set `GEMINI_API_KEYS` to a comma-separated list. `GEMINI_API_KEY` still works
for a single key.
"""

import itertools
import os
import threading

from dotenv import load_dotenv

load_dotenv()


def _load() -> list[str]:
    raw = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY") or ""
    seen: list[str] = []
    for key in raw.split(","):
        key = key.strip()
        if key and key not in seen:
            seen.append(key)
    return seen


KEYS = _load()

_lock = threading.Lock()
_index = 0


def count() -> int:
    return len(KEYS)


def current() -> str:
    if not KEYS:
        raise RuntimeError(
            "No Gemini key. Set GEMINI_API_KEYS (comma-separated) or "
            "GEMINI_API_KEY in .env."
        )
    return KEYS[_index]


def rotate() -> str:
    """Move to the next key. Safe to call from more than one thread -- FastAPI
    runs sync endpoints in a worker pool."""
    global _index
    with _lock:
        _index = (_index + 1) % len(KEYS)
        return KEYS[_index]


def label() -> str:
    """Which key is in use, without printing the key. For logs only."""
    return f"key {_index + 1}/{len(KEYS)}"

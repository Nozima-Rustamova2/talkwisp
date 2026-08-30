"""One function: context + question in, answer out. The provider is config.

    LLM_PROVIDER=gemini   (default)
    GEMINI_API_KEY=...

A second provider is a new function in _PROVIDERS with the same signature, and
nothing in the answer path changes. Deliberately NOT here: a model picker,
per-agent settings, cost tracking, a fallback chain. Swapping providers is a
config change, not a feature.
"""

import base64
import os

import httpx
from dotenv import load_dotenv

from app import gemini_keys

load_dotenv()

PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

# gemini-2.5-flash is closed to new API keys; Google's own 404 points here.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Reading a photograph is where model strength shows most, and the answering
# model is pinned to whatever still has quota. Kept separate so the cheap model
# can answer questions while a stronger one reads price lists.
VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-3.6-flash")

_RETRY_STATUS = (408, 429, 500, 502, 503, 504)
_ATTEMPTS = 4


class LLMError(RuntimeError):
    """The provider could not be reached or refused. Never swallowed into an
    empty answer -- a blank reply in a chat window looks like a working bot that
    knows nothing, which is worse than a visible failure."""


def _gemini(system: str, prompt: str,
            image: tuple[str, bytes] | None = None) -> str:
    import time

    model = VISION_MODEL if image else GEMINI_MODEL
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")

    parts: list[dict] = []
    if image:
        media_type, data = image
        parts.append({"inline_data": {"mime_type": media_type,
                                      "data": base64.b64encode(data).decode()}})
    parts.append({"text": prompt})

    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": parts}],
        # Zero temperature: the same question must not get a different answer on
        # the second try in front of a customer.
        "generationConfig": {"temperature": 0},
    }

    last = ""
    # Enough attempts to try every key once, plus the ordinary backoff retries.
    attempts = _ATTEMPTS + gemini_keys.count()
    for attempt in range(attempts):
        try:
            response = httpx.post(
                url,
                headers={"x-goog-api-key": gemini_keys.current()},
                json=payload,
                timeout=90,
            )
        except httpx.TimeoutException as exc:
            # A timeout is an exception, not a status code, so it has to be
            # caught separately or the retry never sees it.
            last = f"timeout: {exc!r}"
        else:
            if response.status_code == 429 and gemini_keys.count() > 1:
                # Daily quota is per key. Move to the next one and retry at
                # once -- waiting does not refill a daily budget.
                last = f"429 on {gemini_keys.label()}"
                gemini_keys.rotate()
                print(f"gemini: quota hit, switching to {gemini_keys.label()}",
                      flush=True)
                continue
            if response.status_code not in _RETRY_STATUS:
                response.raise_for_status()
                # Thinking models return reasoning parts alongside the reply.
                # Take the text parts not marked as thoughts, or the answer
                # comes back as internal monologue.
                parts = response.json()["candidates"][0]["content"]["parts"]
                return "".join(p["text"] for p in parts
                               if "text" in p and not p.get("thought")).strip()
            last = f"HTTP {response.status_code}: {response.text[:200]}"

        if attempt < attempts - 1:
            time.sleep(2 ** min(attempt, 3))

    raise LLMError(f"{PROVIDER}/{model} failed after {attempts} "
                   f"attempts across {gemini_keys.count()} key(s). Last: {last}")


_PROVIDERS = {"gemini": _gemini}


def check_configured() -> None:
    """Fail at startup, not on the first customer message."""
    if PROVIDER not in _PROVIDERS:
        raise RuntimeError(
            f"LLM_PROVIDER={PROVIDER!r} is not implemented. Known: "
            f"{', '.join(_PROVIDERS)}"
        )
    if PROVIDER == "gemini" and gemini_keys.count() == 0:
        raise RuntimeError(
            "No Gemini key. Set GEMINI_API_KEYS (comma-separated) or "
            "GEMINI_API_KEY in .env."
        )


def complete(system: str, prompt: str,
             image: tuple[str, bytes] | None = None) -> str:
    """`image` is (media_type, bytes). A provider that cannot read images should
    raise rather than silently answer from the prompt alone."""
    check_configured()
    return _PROVIDERS[PROVIDER](system, prompt, image)

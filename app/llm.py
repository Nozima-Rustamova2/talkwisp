"""One function: context + question in, answer out. The provider is config.

    LLM_PROVIDER=gemini   AI Studio, API key           (default)
    LLM_PROVIDER=vertex   Vertex AI, service account

A second provider is a new function in _PROVIDERS with the same signature, and
nothing in the answer path changes. Deliberately NOT here: a model picker,
per-agent settings, cost tracking, a fallback chain. Swapping providers is a
config change, not a feature.

TWO CREDENTIALS, AND THEY ARE NOT INTERCHANGEABLE
-------------------------------------------------
GENERATION may run on Vertex. EMBEDDINGS never do -- app/embeddings.py stays on
the AI Studio key permanently, because moving them would invalidate every
similarity number this project has measured: the 0.55 floor, the FACT_WINDOW
distribution, the cached question vectors, the whole retrieval baseline. A
vector from a different serving stack is not comparable to one already in the
database, and nothing would error to say so.

So the two credentials are separate on purpose. Do not tidy them into one.
"""

import base64
import os
import threading
import time

import httpx
from dotenv import load_dotenv

from app import embeddings, gemini_keys
from app.approval import assert_approved

load_dotenv()

PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

# --- AI Studio ---------------------------------------------------------------
# gemini-2.5-flash is closed to new API keys; Google's own 404 points here.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Reading a photograph is where model strength shows most, and the answering
# model is pinned to whatever still has quota. Kept separate so the cheap model
# can answer questions while a stronger one reads price lists.
VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-3.6-flash")

# --- Vertex AI ---------------------------------------------------------------
# No defaults for project and location: a wrong guess would authenticate
# successfully against somebody else's quota. Missing is an error, not a
# fallback.
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION")

# Pinned, like GEMINI_MODEL, and NOT the same string. Vertex publishes ids that
# differ from AI Studio's -- the 3.1 Flash-Lite id carries a -preview suffix
# there and does not here. Whether those are the same weights is not something
# a docs page can tell you, so check_reachable() asks the API instead of
# trusting this comment: a wrong id comes back 404 at boot, not mid-answer.
VERTEX_MODEL = os.getenv("VERTEX_MODEL")
VERTEX_VISION_MODEL = os.getenv("VERTEX_VISION_MODEL")

_RETRY_STATUS = (408, 429, 500, 502, 503, 504)
_ATTEMPTS = 4


class LLMError(RuntimeError):
    """The provider could not be reached or refused. Never swallowed into an
    empty answer -- a blank reply in a chat window looks like a working bot that
    knows nothing, which is worse than a visible failure."""


# --- shared request and response shapes --------------------------------------
# Vertex and AI Studio speak the SAME generateContent dialect; only the URL and
# the credential differ. These two helpers exist so that fact is expressed once.
#
# Not premature abstraction -- the opposite. Copying them into the second
# provider would create a duplicate definition at the moment of writing, and
# this project has been bitten by that three times (two money formatters, two
# price queries, two subject-key spellings). The `thought` filter below and the
# temperature decision above it are each one decision, and they get one home.


def _payload(system: str, prompt: str,
             image: tuple[str, bytes] | None) -> dict:
    parts: list[dict] = []
    if image:
        media_type, data = image
        parts.append({"inline_data": {"mime_type": media_type,
                                      "data": base64.b64encode(data).decode()}})
    parts.append({"text": prompt})
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": parts}],
        # Zero temperature: the same question must not get a different answer on
        # the second try in front of a customer.
        "generationConfig": {"temperature": 0},
    }


def _text(body: dict) -> str:
    """Thinking models return reasoning parts alongside the reply. Take the text
    parts not marked as thoughts, or the answer comes back as internal
    monologue."""
    parts = body["candidates"][0]["content"]["parts"]
    return "".join(p["text"] for p in parts
                   if "text" in p and not p.get("thought")).strip()


def _gemini(system: str, prompt: str,
            image: tuple[str, bytes] | None = None) -> str:
    model = VISION_MODEL if image else GEMINI_MODEL
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    payload = _payload(system, prompt, image)

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
        except httpx.TransportError as exc:
            # A transport failure is an exception, not a status code, so it has
            # to be caught separately or the retry never sees it.
            #
            # This caught only TimeoutException until 2026-09-05, when a
            # 90-question harness run died nine minutes in on
            # "[WinError 10054] An existing connection was forcibly closed by
            # the remote host" -- an httpx.ReadError, which is a TransportError
            # and not a timeout. The reasoning in the comment was always
            # general; the catch was not. TransportError is the parent of
            # TimeoutException, ConnectError, ReadError and the rest, so it now
            # matches the reasoning.
            #
            # This is a production bug and not merely a test annoyance: one
            # dropped connection was one failed customer message, where a retry
            # would have worked.
            last = f"transport: {exc!r}"
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
                return _text(response.json())
            last = f"HTTP {response.status_code}: {response.text[:200]}"

        if attempt < attempts - 1:
            time.sleep(2 ** min(attempt, 3))

    raise LLMError(f"{PROVIDER}/{model} failed after {attempts} "
                   f"attempts across {gemini_keys.count()} key(s). Last: {last}")


# --- Vertex AI ---------------------------------------------------------------

_creds = None
_creds_lock = threading.Lock()


def _bearer() -> str:
    """A service-account access token, minted once and refreshed near expiry.

    Not an API key: Vertex takes OAuth2, and the token lives about an hour, so
    something has to refresh it. google-auth does that; hand-signing a JWT here
    would be more code doing the same job less well.

    Locked because FastAPI runs sync endpoints on a threadpool, so two requests
    can arrive at an expired token together. Refreshing twice is harmless;
    refreshing while another thread reads the token is not.
    """
    global _creds
    import google.auth
    import google.auth.transport.requests

    with _creds_lock:
        if _creds is None:
            _creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not _creds.valid:
            _creds.refresh(google.auth.transport.requests.Request())
        return _creds.token


def _vertex_url(model: str) -> str:
    return (f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/"
            f"{VERTEX_PROJECT}/locations/{VERTEX_LOCATION}/publishers/google/"
            f"models/{model}:generateContent")


def _vertex(system: str, prompt: str,
            image: tuple[str, bytes] | None = None) -> str:
    model = VERTEX_VISION_MODEL if image else VERTEX_MODEL
    payload = _payload(system, prompt, image)

    last = ""
    # No key rotation, and that is a real difference rather than an omission.
    # gemini_keys exists because AI Studio's free tier is a DAILY quota per key,
    # so a 429 means "this key is finished until tomorrow" and moving to another
    # one is the only thing that helps. A 429 from Vertex is a rate limit on one
    # project: waiting fixes it, and there is no second credential to move to.
    attempts = _ATTEMPTS
    for attempt in range(attempts):
        try:
            response = httpx.post(
                _vertex_url(model),
                headers={"Authorization": f"Bearer {_bearer()}"},
                json=payload,
                timeout=90,
            )
        except httpx.TransportError as exc:
            # Same reasoning as the AI Studio arm: a dropped connection is an
            # exception, not a status, and it must be retried.
            last = f"transport: {exc!r}"
        else:
            if response.status_code not in _RETRY_STATUS:
                response.raise_for_status()
                return _text(response.json())
            last = f"HTTP {response.status_code}: {response.text[:200]}"

        if attempt < attempts - 1:
            time.sleep(2 ** min(attempt, 3))

    raise LLMError(f"{PROVIDER}/{model} failed after {attempts} attempts. "
                   f"Last: {last}")


_PROVIDERS = {"gemini": _gemini, "vertex": _vertex}


def check_configured() -> None:
    """Fail at startup, not on the first customer message.

    Config only, no network -- complete() calls this on every generation, so
    anything here runs thousands of times a day. The live "does this model
    actually exist" probe is check_reachable(), which runs once at boot.
    """
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
    if PROVIDER == "vertex":
        missing = [name for name, value in
                   (("VERTEX_PROJECT", VERTEX_PROJECT),
                    ("VERTEX_LOCATION", VERTEX_LOCATION),
                    ("VERTEX_MODEL", VERTEX_MODEL),
                    ("VERTEX_VISION_MODEL", VERTEX_VISION_MODEL)) if not value]
        if missing:
            raise RuntimeError(
                f"LLM_PROVIDER=vertex needs {', '.join(missing)} in .env. "
                "Vertex model ids differ from AI Studio's and are pinned "
                "explicitly -- there is no default worth guessing.")

    # THE EMBEDDING CREDENTIAL IS CHECKED HERE, UNCONDITIONALLY, AND THAT IS
    # LOAD-BEARING.
    #
    # Before Vertex existed, "is generation configured" and "is the AI Studio
    # key present" were the same question, so the check above covered both by
    # accident. Setting LLM_PROVIDER=vertex separates them: generation stops
    # needing the key and embeddings still need it on every customer question.
    #
    # Left inside the `PROVIDER == "gemini"` branch, this guard would have
    # SILENTLY NARROWED ITS OWN SCOPE the moment a config value changed --
    # startup would go green with no embedding credential at all, and the first
    # customer question would die in app/embeddings.py instead. Most guards in
    # this codebase had to have their scope widened by hand; this one shrinks
    # itself, which is worse, because nothing about editing .env looks like
    # editing a check.
    #
    # The coupling is invisible from either module alone: app/llm.py has no
    # reason to care about vectors, and app/embeddings.py is never called at
    # startup. Same shape as extract.py depending on retrieval.py's `confirmed`
    # filter for its safety.
    embeddings.check_configured()


def check_reachable() -> None:
    """One real generation, at boot, to prove the model exists and we may use it.

    Separate from check_configured() because it costs a network round trip and
    complete() calls that one on every generation.

    This is the part config validation cannot do. A pinned model id that is
    merely WRONG -- Vertex's ids are not AI Studio's -- authenticates fine and
    fails 404 on the first customer message. A service account missing the
    aiplatform.user role does the same with a 403. Both are boot-time facts
    being discovered at answer time, which is the failure this whole pattern
    exists to prevent.

    It also settles which model id is real by asking the API rather than a docs
    page, which is the only source of truth about what THIS project may call.
    """
    check_configured()
    model = VERTEX_MODEL if PROVIDER == "vertex" else GEMINI_MODEL
    try:
        _PROVIDERS[PROVIDER]("Reply with the single character: k", "ping")
    except LLMError as exc:
        raise RuntimeError(
            f"LLM_PROVIDER={PROVIDER} is configured but {model!r} could not be "
            f"reached, so every customer question would fail. Refusing to "
            f"start.\n  {exc}") from None
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300]
        raise RuntimeError(
            f"LLM_PROVIDER={PROVIDER} rejected a test call to {model!r} with "
            f"HTTP {exc.response.status_code}. Refusing to start.\n"
            f"  {detail}\n"
            "  404 usually means the model id is wrong for this provider; 403 "
            "usually means the service account lacks roles/aiplatform.user."
        ) from None


def complete(system: str, prompt: str,
             image: tuple[str, bytes] | None = None) -> str:
    """`image` is (media_type, bytes). A provider that cannot read images should
    raise rather than silently answer from the prompt alone."""
    check_configured()
    # THE SPENDING GATE. Here rather than on the endpoints, because what a route
    # costs is a property of what it calls three modules down. See app/approval.py.
    assert_approved("asking the model")
    return _PROVIDERS[PROVIDER](system, prompt, image)

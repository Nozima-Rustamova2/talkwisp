"""Text -> vector. One model, one place.

MODEL and DIMENSIONS are written into every chunk row (embedding_model) and are
fixed by the migration (vector(1024)). Changing either means re-embedding
everything, which is why the model name is stored per row rather than assumed.

Gemini embeddings are asymmetric: a stored paragraph and a customer's question
are embedded with different task types, and mixing them up quietly costs
retrieval quality without any error. Hence two functions, not one.
"""

import math
import os
import time

import httpx
from dotenv import load_dotenv

from app import gemini_keys

load_dotenv()

MODEL = "gemini-embedding-001"
DIMENSIONS = 1024  # must match vector(1024) in migrations/0001_initial.sql

_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:embedContent"


def _embed(text: str, task_type: str) -> list[float]:
    payload = {
        "content": {"parts": [{"text": text}]},
        "taskType": task_type,
        "outputDimensionality": DIMENSIONS,
    }

    # This call is on the path of every customer question, so a transient
    # network blip or rate limit must not surface as a failed answer. Same
    # retry policy as app/llm.py; a DNS failure arrives as an exception rather
    # than a status code, so both have to be caught.
    last = ""
    attempts = 4 + gemini_keys.count()
    for attempt in range(attempts):
        try:
            response = httpx.post(
                _URL, headers={"x-goog-api-key": gemini_keys.current()},
                json=payload, timeout=30,
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            last = repr(exc)
        else:
            if response.status_code == 429 and gemini_keys.count() > 1:
                # Embeddings have their own daily budget per key, and they are
                # spent faster than generations: a seed embeds every fact.
                last = f"429 on {gemini_keys.label()}"
                gemini_keys.rotate()
                print(f"gemini: embedding quota hit, switching to "
                      f"{gemini_keys.label()}", flush=True)
                continue
            if response.status_code not in (408, 429, 500, 502, 503, 504):
                response.raise_for_status()
                break
            last = f"HTTP {response.status_code}"
        if attempt == attempts - 1:
            raise RuntimeError(
                f"embedding failed after {attempts} attempts across "
                f"{gemini_keys.count()} key(s). Last: {last}"
            )
        time.sleep(2 ** min(attempt, 3))

    values = response.json()["embedding"]["values"]

    # Gemini returns unit-length vectors only at its native 3072. Truncated to
    # 1024 they are not normalized, so L2 distance would be measuring length as
    # well as direction. Normalize here so `<->` and `<=>` agree.
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm else values


def embed_document(text: str) -> list[float]:
    """Embed something being stored."""
    return _embed(text, "RETRIEVAL_DOCUMENT")


def embed_query(text: str) -> list[float]:
    """Embed a customer's question."""
    return _embed(text, "RETRIEVAL_QUERY")

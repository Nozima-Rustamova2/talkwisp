"""Prose from a source, kept whole. Steps 15 and 16.

Not everything in a document is a fact. "Bring your passport and previous test
results" is advice, not a subject/attribute/value, and forcing it into that
shape destroys it. Such passages are stored intact and quoted by the agent.

The model proposes which passages are prose; **code verifies each one appears
verbatim in the source before it is stored.** That guard is the whole point of
"prose stays whole": a model asked for passages will happily return a tidied,
shortened or translated version, and a paraphrase quoted back to a customer as
the business's own words is a quiet way to say something the business never
said. A passage that cannot be found in the source is dropped, not corrected.
"""

import re

from psycopg import Connection

from app.embeddings import DIMENSIONS, MODEL, embed_document
from app.llm import complete

# Below this a "passage" is a fragment -- a heading, a price line the fact
# extractor already took -- and quoting it back answers nothing.
MIN_PASSAGE_CHARS = 60

_SYSTEM = """You are given a business's own text. Some of it states facts
(prices, hours, names, roles). The rest is prose: advice, instructions,
policies, explanations -- things a customer would be told, not looked up.

Return ONLY the prose, as a JSON array of strings.

Rules:
- Copy each passage EXACTLY as it appears. Character for character. Do not
  translate, shorten, tidy, join or re-order. A passage that does not appear
  word for word in the text will be discarded.
- Keep a passage whole: a full paragraph, not a sentence pulled out of one.
- Skip anything that is just a fact: price lines, opening hours, a person's
  role, a phone number, an address. Those are handled elsewhere.
- Skip headings and section titles on their own.
- If the text is entirely facts -- a bare price list, for example -- reply
  with []. That is a normal answer, not a failure.

Reply with JSON only. No prose of your own, no code fences."""


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def find_prose(text: str) -> tuple[list[str], int]:
    """Passages the model proposed AND that really are in the text.

    Returns (verified passages, number rejected as not verbatim).
    """
    import json

    raw = complete(_SYSTEM, f"Text:\n{text}")
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return [], 0
    proposed = [p for p in json.loads(match.group(0)) if isinstance(p, str)]

    haystack = _squash(text)
    kept, rejected = [], 0
    for passage in proposed:
        passage = passage.strip()
        if len(passage) < MIN_PASSAGE_CHARS:
            continue
        if _squash(passage) in haystack:
            kept.append(passage)
        else:
            # Paraphrased, translated or invented. Dropping it is the only safe
            # response: it would be quoted to a customer as the business's words.
            rejected += 1
    return kept, rejected


def embed_all(passages: list[str]) -> list[tuple[str, str]]:
    """Embed before the transaction opens, not inside it. Each call is a network
    round trip, and holding a transaction across several would pin a connection
    for the duration."""
    return [(p, str(embed_document(p))) for p in passages]


def store(conn: Connection, source_id: str,
          embedded: list[tuple[str, str]]) -> int:
    """Replace this source's chunks. Called inside the extraction transaction.

    Delete-then-insert rather than insert-if-absent: re-reading a source should
    give the chunks the source says now, not those plus whatever an earlier
    reading produced.
    """
    conn.execute("delete from chunk where source_id = %s", (source_id,))
    for ordinal, (content, vector) in enumerate(embedded):
        conn.execute(
            "insert into chunk (source_id, ordinal, content, embedding,"
            " embedding_model) values (%s, %s, %s, %s, %s)",
            (source_id, ordinal, content, vector, f"{MODEL}@{DIMENSIONS}"),
        )
    return len(embedded)

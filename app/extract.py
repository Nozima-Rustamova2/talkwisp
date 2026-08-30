"""Turn one source's text into unconfirmed facts.

Two halves, deliberately separable:

    read(conn, source)   the LLM call. Reads nothing, writes nothing (step 12).
    store(source, facts) one transaction: facts + the source's new status.

`run()` puts them together, and the shape of it is the point. The model call
happens with NO database transaction open -- it takes seconds, and holding a
transaction across it would pin a connection for the whole time. Then the facts
and the source's status change commit together, so a source is never marked
`extracted` while its facts are missing, and never leaves half its facts behind.

When it fails, the failure is written on a SEPARATE connection. It has to be:
the transaction that would have recorded the error is the one being rolled back.
Without that, a failed file sits at `pending` with no explanation forever.
"""

import json
import re

from psycopg import Connection

from app import chunks
from app.db import pool
from app.llm import complete
from app.normalize import normalize

# Facts below this are not worth an owner's time to review -- they are usually
# the model padding out a list rather than reading one.
MIN_CONFIDENCE = 0.4

_SYSTEM = """You read a business's own notes and pull out the facts a customer
might ask about. Reply with a JSON array.

Each fact is:
  {"subject": "...", "attribute": "...", "value": "...", "confidence": 0.0-1.0}

  subject   what the fact is about: the business, a person, a service
  attribute which property: opening hours, price, role, address
  value     the property's content, close to how it was written
  confidence how sure you are the text actually says this

Rules:

ONE FACT PER QUESTION A CUSTOMER WOULD ASK. This is the most important rule.
"When does Toshmatov see patients?" is one question, so the days and the hours
belong in ONE value -- "Dushanba, Chorshanba, Juma, 10:00-16:00" -- not split
into one fact for the days and another for the hours. Two half-facts under two
different attributes answer nothing on their own and will never be brought back
together. Before writing a fact, ask what question it answers; if two of your
facts answer the same question, they are one fact.

COMPLETENESS. When you have your list, read the text again and check every
price, time, name and number in it appears in one of your facts. Lists and
sentences containing two prices are where facts get missed. A missed fact is
invisible afterwards -- nobody can review something that was never proposed --
so a second pass is always worth it.

- Extract ONLY what the text states. Never infer, never complete a pattern,
  never add a fact because a business like this usually has one.
- NEVER BORROW A VALUE FROM A NEIGHBOURING ROW. If a row in a list has no
  price, skip that row entirely -- do not give it the price above or below it,
  and do not shift the remaining prices up to fill the gap. Measured failure:
  on a menu where one item's price was missing, every following price was
  attributed to the wrong item, all at high confidence. A skipped row is a gap
  someone can notice; a wrong price is not.
- REUSE an attribute from the existing list whenever the text means the same
  thing. A new name for an existing idea splits the knowledge in two.
- Reuse an existing subject spelling when the text refers to something already
  known.
- Write subjects and attributes in the same language as the existing ones.
- Prices are approximate ranges. Keep them as ranges; never reduce a range to
  one number.
- Confidence is about the TEXT, not about the world. Calibrate like this:
    1.0  the text states it word for word: "narxi 60 000 so'm" -> narx
    0.8  the text states it, but you had to reformat or combine parts of
         sentences to get the value
    0.6  the text implies it clearly but never says it: a section headed
         "Laboratoriya" listing a test, so the test is a laboratory service
    0.4  you are joining two distant parts of the text and might be wrong
  Do not give everything 1.0. If most of your facts score the same, you have
  not calibrated -- re-read and separate the word-for-word ones from the rest.
- General prose -- advice, instructions, policies -- is NOT a fact. Skip it.
  It is kept whole and quoted elsewhere.
- If the text contains no facts, reply with [].

Reply with JSON only. No prose, no code fences."""


def _json_array(raw: str) -> list:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON array in model reply: {raw[:200]}")
    return json.loads(match.group(0))


def read(conn: Connection, source: dict) -> list[dict]:
    """Step 12. The model call, on its own. Nothing is written."""
    text = (source.get("content") or "").strip()
    if not text:
        raise ValueError(
            f"Source {source['id']} has no text. A file needs step 14 to read "
            f"it before it can be extracted."
        )

    attributes = [r[0] for r in conn.execute(
        "select attribute from fact group by attribute"
        " order by count(*) desc limit 40")]
    subjects = [r[0] for r in conn.execute(
        "select subject from fact group by subject order by subject limit 60")]

    raw = complete(_SYSTEM, (
        f"Existing attributes: {', '.join(attributes) or '(none yet)'}\n"
        f"Existing subjects: {', '.join(subjects) or '(none yet)'}\n\n"
        f"Text:\n{text}"
    ))

    facts = []
    for item in _json_array(raw):
        if not isinstance(item, dict):
            continue
        parsed = {k: str(item.get(k) or "").strip()
                  for k in ("subject", "attribute", "value")}
        if not all(parsed.values()):
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        parsed["confidence"] = max(0.0, min(1.0, confidence))
        if parsed["confidence"] >= MIN_CONFIDENCE:
            facts.append(parsed)
    return facts


def store(source_id: str, facts: list[dict],
          embedded_prose: list[tuple[str, str]] | None = None) -> int:
    """Step 13. ONE transaction: every fact, every chunk, and the source status.

    This is the "one transaction per file" rule made real. A file either
    contributes everything it has -- facts and prose together -- or nothing at
    all. A half-ingested document is worse than an un-ingested one, because the
    owner has no way to see which half is missing.

    Facts are unconfirmed, so nothing here reaches a customer until a human says
    so, and they are NOT embedded: vector search filters on `confirmed`, so an
    embedding now would be quota spent on facts that may be rejected. Step 18
    embeds on confirmation. Chunks are different -- review is facts-only, so
    prose goes live immediately and must be embedded to be findable.
    """
    with pool.connection() as conn:
        conn.execute("delete from fact where source_id = %s and not confirmed",
                     (source_id,))
        chunks.store(conn, source_id, embedded_prose or [])
        for f in facts:
            conn.execute(
                "insert into fact (subject, subject_key, attribute,"
                " attribute_key, value, value_key, confidence, source_id,"
                " confirmed) values (%s, %s, %s, %s, %s, %s, %s, %s, false)",
                (f["subject"], normalize(f["subject"]),
                 f["attribute"], normalize(f["attribute"]),
                 f["value"], normalize(f["value"]),
                 f["confidence"], source_id),
            )
        conn.execute(
            "update source set status = 'extracted', extracted_at = now(),"
            " error = null where id = %s", (source_id,))
    return len(facts)


def _mark_failed(source_id: str, reason: str) -> None:
    """On its own connection, because the transaction that would have carried
    this is the one being rolled back."""
    with pool.connection() as conn:
        conn.execute(
            "update source set status = 'failed', error = %s where id = %s",
            (reason[:1000], source_id))


def run(source: dict) -> dict:
    """Read then store. A failure leaves no facts behind, only an explanation."""
    try:
        with pool.connection() as conn:
            facts = read(conn, source)
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded
        _mark_failed(source["id"], repr(exc))
        return {"source_id": source["id"], "status": "failed",
                "error": repr(exc)[:300], "facts": []}

    # Steps 15-16. Both model calls and all the embedding happen with no
    # transaction open; only the writing is transactional.
    try:
        passages, rejected = chunks.find_prose(source["content"] or "")
        embedded = chunks.embed_all(passages)
    except Exception as exc:  # noqa: BLE001
        _mark_failed(source["id"], repr(exc))
        return {"source_id": source["id"], "status": "failed",
                "error": repr(exc)[:300], "facts": [], "chunks": 0}

    try:
        store(source["id"], facts, embedded)
    except Exception as exc:  # noqa: BLE001
        _mark_failed(source["id"], repr(exc))
        return {"source_id": source["id"], "status": "failed",
                "error": repr(exc)[:300], "facts": [], "chunks": 0}

    return {"source_id": source["id"], "status": "extracted", "error": None,
            "facts": facts, "chunks": len(embedded),
            "prose_rejected_as_not_verbatim": rejected}

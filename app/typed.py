"""One free-text line from the owner becomes one fact.

    "Kardiolog qabuli 250 000 so'm"  ->  Kardiolog qabuli / narx / 250 000 soʻm

Typed facts are confirmed on write and carry no source -- `source_id IS NULL` is
what marks a fact as the owner's own word rather than something extracted from a
file. They never enter review.

Parsing is a two-step: parse and show, then write on confirmation. Writing blind
would let a mis-parse become a confirmed fact the bot answers customers with,
and a confirmed fact is exactly the thing nothing downstream questions.
"""

import json
import re

from psycopg import Connection

from app.embeddings import DIMENSIONS, MODEL, embed_document
from app.llm import complete
from app.normalize import normalize

_SYSTEM = """You turn one line written by a business owner into one fact, as JSON.

A fact is: subject (what it is about), attribute (which property), value (the
property's content).

    "Kardiolog qabuli 250 000 so'm"
        -> {"subject": "Kardiolog qabuli", "attribute": "narx", "value": "250 000 so'm"}
    "Rasulova Gulnora Du-Ju 9 dan 2 gacha qabul qiladi"
        -> {"subject": "Rasulova Gulnora", "attribute": "qabul vaqti", "value": "Dushanba-Juma, 09:00-14:00"}

Rules:
- REUSE an attribute from the existing list whenever the line means the same
  thing. A new name for an existing idea ("price" beside "narx") splits the
  knowledge in two and the business ends up with facts that never meet.
- Reuse an existing subject spelling when the line refers to something already
  known, so facts about one thing stay together.
- Write the attribute in the same language as the existing ones.
- Keep the value close to what was written. Normalise obvious formats (times to
  HH:MM, weekday ranges) but never invent detail, and never turn an approximate
  range into a single price.
- If the line does not contain a single clear fact, return
  {"error": "<short reason in the owner's language>"} instead.

Reply with JSON only. No prose, no code fences."""


def _json(raw: str) -> dict:
    """Models add fences even when told not to. Take the outermost object."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in model reply: {raw[:200]}")
    return json.loads(match.group(0))


def parse(conn: Connection, line: str) -> dict:
    """Parse a line. Reads nothing, writes nothing."""
    attributes = [r[0] for r in conn.execute(
        "select attribute from fact group by attribute order by count(*) desc limit 40")]
    subjects = [r[0] for r in conn.execute(
        "select subject from fact group by subject order by subject limit 60")]

    raw = complete(_SYSTEM, (
        f"Existing attributes: {', '.join(attributes) or '(none yet)'}\n"
        f"Existing subjects: {', '.join(subjects) or '(none yet)'}\n\n"
        f"Line: {line}"
    ))
    data = _json(raw)

    if "error" in data:
        return {"line": line, "parsed": None, "error": data["error"]}

    missing = [k for k in ("subject", "attribute", "value")
               if not str(data.get(k) or "").strip()]
    if missing:
        return {"line": line, "parsed": None,
                "error": f"model returned no {', '.join(missing)}"}

    parsed = {k: str(data[k]).strip() for k in ("subject", "attribute", "value")}
    return {"line": line, "parsed": parsed, "error": None,
            "conflicts": conflicts(conn, parsed),
            "candidates": candidates(conn, parsed["subject"])}


def candidates(conn: Connection, subject: str) -> list[str]:
    """Existing subjects this name could mean, when it could mean more than one.

    "Rasulova" is two doctors. Writing a fact against a guess is worse than
    writing none: the fact would be confirmed, so nothing downstream would ever
    question it. Returns [] when the name is unambiguous or entirely new.
    """
    key = normalize(subject)
    rows = conn.execute(
        "select distinct f.subject from fact f"
        " join alias a on a.subject_key = f.subject_key"
        " where a.alias_key = %s and f.subject_key <> %s"
        " order by 1",
        (key, key),
    ).fetchall()
    return [r[0] for r in rows]


def conflicts(conn: Connection, parsed: dict) -> list[dict]:
    """Confirmed facts that already answer this subject+attribute differently.

    Surfaced, never resolved here. Two opening-hours values may both be true
    (different branches, a seasonal change), so this is a judgement for the
    owner -- the same judgement step 19 makes for extracted facts.
    """
    rows = conn.execute(
        "select subject, attribute, value from fact"
        " where confirmed and subject_key = %s and attribute_key = %s"
        "   and value <> %s",
        (normalize(parsed["subject"]), normalize(parsed["attribute"]),
         parsed["value"]),
    ).fetchall()
    return [{"subject": s, "attribute": a, "value": v} for s, a, v in rows]


def store(conn: Connection, parsed: dict) -> str:
    """Write one confirmed, owner-typed fact. Embedded so it is reachable by
    vector search immediately, not only by exact match."""
    text = f"{parsed['subject']} / {parsed['attribute']} / {parsed['value']}"
    vector = str(embed_document(text))
    return conn.execute(
        "insert into fact (subject, subject_key, attribute, attribute_key,"
        " value, value_key, confirmed, embedding, embedding_model)"
        " values (%s, %s, %s, %s, %s, %s, true, %s, %s) returning id",
        (parsed["subject"], normalize(parsed["subject"]),
         parsed["attribute"], normalize(parsed["attribute"]),
         parsed["value"], normalize(parsed["value"]),
         vector, f"{MODEL}@{DIMENSIONS}"),
    ).fetchone()[0]

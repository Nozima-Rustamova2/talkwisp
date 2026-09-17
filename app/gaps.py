"""What customers asked that the agent could not answer, and closing each one.

ONE READER, TWO VIEWS. The Dashboard's "what it doesn't know yet" column and the
Gaps screen both call open_gaps(). There is no second computation of "open", so
an answer given in one place disappears from both -- two views computed
separately is how they would start disagreeing.

RESOLVED IS RECORDED, NOT RECOMPUTED. The obvious test -- ask the question again
and see whether it answers now -- does not work. Retrieval cannot tell: 495 of
496 logged refusals ALREADY scored above the similarity floor, so a good score
proves nothing. Only the full answer path could tell, which is a model call per
open gap on every page load. So closing a gap appends one line to gaps.jsonl.
The file stays append-only, and a resolution is itself an observation.

THE HONEST LIMIT. A fact added any other way -- Add knowledge, /fact in Telegram
-- does not close the gap it happens to answer. Hence dismiss(): without a way
to clear a gap by hand the list only grows, and a list that only grows is one
owners stop opening. Each line records WHICH way it was closed, so how often
owners dismiss gaps that were in fact answered elsewhere can be measured rather
than guessed.

Tenancy is the same shape as app/conversations.py: no business argument, the
tenant comes from the connection() block, and lines with no business_id are
counted and skipped rather than attributed by guesswork.
"""

import datetime
import json

from psycopg import Connection

from app.answer import GAP_LOG
from app.db import current_business_id

ANSWERED = "answered"
DISMISSED = "dismissed"


def _lines() -> tuple[list[dict], int]:
    business = current_business_id()          # raises if nothing is bound
    mine, unattributed = [], 0
    if not GAP_LOG.exists():
        return mine, 0
    with GAP_LOG.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue                      # half a line from a killed append
            if not row.get("business_id"):
                unattributed += 1
            elif row["business_id"] == business:
                mine.append(row)
    return mine, unattributed


def open_gaps(limit: int | None = None) -> dict:
    """Unanswered questions, most-asked first.

    Grouped by question_key -- the normalised text -- which is free and honest.
    Clustering differently-worded questions by topic would need a model call per
    group and would be the first thing here to produce a wrong grouping nobody
    could see.
    """
    rows, unattributed = _lines()

    groups: dict[str, dict] = {}
    for row in rows:
        key = row.get("question_key")
        if not key:
            continue
        if row.get("resolution"):
            # A resolution closes every refusal logged BEFORE it. A refusal
            # logged AFTER it opens the question again, counted from zero:
            # whatever was written did not answer it.
            groups.pop(key, None)
            continue
        group = groups.setdefault(key, {"question_key": key, "asked": 0,
                                        "question": row.get("question"),
                                        "last_at": None, "best_similarity": None})
        group["asked"] += 1
        group["question"] = row.get("question") or group["question"]
        group["last_at"] = row.get("at") or group["last_at"]
        score = row.get("best_similarity")
        if score is not None and (group["best_similarity"] is None
                                  or score > group["best_similarity"]):
            group["best_similarity"] = score

    items = sorted(groups.values(),
                   key=lambda g: (g["asked"], g["last_at"] or ""), reverse=True)
    return {"gaps": items[:limit] if limit else items,
            "open": len(items), "unattributed": unattributed}


def _close(key: str, how: str, fact_id: str | None) -> None:
    entry = {
        "business_id": current_business_id(),
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "question_key": key,
        "resolution": how,
        "fact_id": fact_id,
    }
    with GAP_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def answer(conn: Connection, key: str, fact_id: str) -> bool:
    """Close a gap with the fact written for it. False if that fact is not this
    business's -- read under RLS, so another tenant's id simply is not there."""
    exists = conn.execute("select 1 from fact where id = %s",
                          (fact_id,)).fetchone()
    if not exists:
        return False
    _close(key, ANSWERED, fact_id)
    return True


def dismiss(key: str) -> None:
    """Close a gap with no fact: "not for us", or answered some other way."""
    _close(key, DISMISSED, None)

"""Takeover: a question the agent could not answer, passed to the owner.

The database half. Telegram lives in bot.py; everything here takes a connection
and can be exercised without a bot, which is what check_escalation.py does.

THE LANDING PAGE HAS PROMISED THIS SINCE THE SITE WENT UP, in three languages:
"it asks the customer whether to pass the question to you, then sends it to your
Telegram. You reply as yourself, and your answer is saved so it knows next
time." Both halves matter. Without the fact-write it is a relay, and the owner
answers the same question again next month; the value is not reaching the owner
once, it is that the hundredth customer does not need to.

FOUR STATES, and the lifecycle is the reason this is a table rather than a line
in a file:

    waiting    the customer asked, the owner has not opened it
    answering  the owner tapped Answer; their next message is the reply
    answered   sent to every waiting customer, fact written or declined
    expired    24 hours passed; it stops being an escalation and becomes a gap
"""

import datetime
import json

from psycopg import Connection

from app.answer import SIMILARITY_FLOOR
from app.normalize import normalize

# After this, an escalation stops being an escalation. The customer hears once
# that no answer came -- silence would be worse than the refusal it replaced --
# and the question is already in gaps.jsonl, which is the ordinary route for
# "something we could not answer".
EXPIRE_AFTER = datetime.timedelta(hours=24)


def snapshot(turns: list, result: dict, language: str) -> dict:
    """What the owner will need in order to answer, captured NOW.

    Not looked up later. The bot's conversation history is in-memory, per
    process, and idles out -- and the supervisor restarts bots routinely -- so
    resolving this when the owner opens the message half an hour later would
    find nothing and deliver exactly the bare question that makes an escalation
    unanswerable.

    `best` and `nearest` are the interesting part. A 0.41 miss and a 0.02 miss
    are different problems -- "you told me and I could not find it" versus "you
    never told me this" -- and the question alone cannot tell them apart.
    app/answer.py computes both already for gaps.jsonl.
    """
    near = result.get("near_facts") or []
    chunks = result.get("chunks") or []
    scored = [f.get("similarity") for f in near if f.get("similarity")]
    scored += [c.get("similarity") for c in chunks if c.get("similarity")]
    best = max(scored, default=None)
    nearest = None
    if near:
        top = max(near, key=lambda f: f.get("similarity") or 0)
        nearest = f"{top['subject']} / {top['attribute']}"
    return {
        "language": language,
        # CAPTURED, so the message can COMPARE rather than assert. The first
        # version printed the score followed by a hardcoded "below the
        # threshold" -- and against real data the score was 0.724 against a 0.55
        # floor, so the one line meant to tell the owner which problem they had
        # told them the wrong one. An owner reading it would have gone and added
        # a fact they already have.
        "floor": SIMILARITY_FLOOR,
        # Newest last, and only the last two: the turn before the question is
        # what makes "is there a discount for kids" answerable, and a long
        # history is noise in a phone notification.
        "turns": [[q, a] for q, a in turns][-2:],
        "best_similarity": round(best, 3) if best else None,
        "nearest": nearest,
        "route": result.get("source"),
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


def waiting_for_chat(conn: Connection, chat_id: int) -> bool:
    """Does this customer already have one outstanding?

    The frequency control, and it is the same mechanism as the join below rather
    than a separate rule: a customer who gets three refusals in a row should be
    offered the button once, not three times.
    """
    row = conn.execute(
        "select 1 from escalation"
        " where status in ('waiting', 'answering') and %s = any(chat_ids)"
        " limit 1", (chat_id,)).fetchone()
    return row is not None


def open_or_join(conn: Connection, chat_id: int, question: str,
                 context: dict) -> tuple[str, bool]:
    """(escalation_id, joined_an_existing_one).

    JOINING IS THE POINT. If three customers ask the same unanswerable thing
    before the owner replies, the owner is pinged once and all three are
    answered. A row each would ping three times for one question -- which is how
    a useful feature becomes one people mute -- and would leave two of them
    waiting on an answer that had already been given.
    """
    key = normalize(question)
    existing = conn.execute(
        "select id from escalation"
        " where question_key = %s and status in ('waiting', 'answering')"
        " limit 1", (key,)).fetchone()
    if existing:
        conn.execute(
            "update escalation set chat_ids ="
            " (select array_agg(distinct c) from unnest(chat_ids || %s::bigint) c)"
            " where id = %s", (chat_id, existing[0]))
        return str(existing[0]), True

    row = conn.execute(
        "insert into escalation (chat_ids, question, question_key, context)"
        " values (array[%s]::bigint[], %s, %s, %s) returning id",
        (chat_id, question, key, json.dumps(context))).fetchone()
    return str(row[0]), False


def get(conn: Connection, escalation_id: str) -> dict | None:
    row = conn.execute(
        "select id, chat_ids, question, context, status, answer"
        " from escalation where id = %s", (escalation_id,)).fetchone()
    if not row:
        return None
    return {"id": str(row[0]), "chat_ids": row[1], "question": row[2],
            "context": row[3], "status": row[4], "answer": row[5]}


def start_answering(conn: Connection, escalation_id: str) -> dict | None:
    """Mark this one as the thing the owner's next message answers.

    ONE AT A TIME, and the state lives in the row rather than in memory. The
    bot's PENDING dict dies with the process and the supervisor restarts bots
    routinely; an owner who tapped Answer, got distracted, and came back after a
    restart would otherwise have their reply land nowhere.

    Tapping Answer on a second escalation releases the first back to `waiting`,
    because two half-answered questions and no way to tell which the next
    message belongs to is worse than losing a tap.
    """
    conn.execute("update escalation set status = 'waiting'"
                 " where status = 'answering' and id <> %s", (escalation_id,))
    row = conn.execute(
        "update escalation set status = 'answering'"
        " where id = %s and status in ('waiting', 'answering')"
        " returning id", (escalation_id,)).fetchone()
    return get(conn, escalation_id) if row else None


def answering(conn: Connection) -> dict | None:
    """The one the owner is replying to, if any."""
    row = conn.execute(
        "select id from escalation where status = 'answering' limit 1"
    ).fetchone()
    return get(conn, str(row[0])) if row else None


def record_answer(conn: Connection, escalation_id: str, answer: str,
                  fact_id: str | None) -> dict | None:
    """Close it. `fact_id` may be None -- see the column comment in 0014."""
    conn.execute(
        "update escalation set status = 'answered', answer = %s,"
        " fact_id = %s, answered_at = now() where id = %s",
        (answer, fact_id, escalation_id))
    return get(conn, escalation_id)


def close_without_answer(conn: Connection, escalation_id: str) -> dict | None:
    """The owner said it is not for them. A real outcome, and the customer is
    still owed something -- silence after "I've sent it" is the failure this
    whole feature exists to remove."""
    conn.execute(
        "update escalation set status = 'answered', answered_at = now()"
        " where id = %s", (escalation_id,))
    return get(conn, escalation_id)


def expire_stale(conn: Connection) -> list[dict]:
    """Time out anything older than EXPIRE_AFTER, and say who to tell.

    Returns the rows it expired so the caller can notify the customers. A row
    that expired silently would leave someone who was told "I've sent it"
    waiting forever, which is worse than the refusal they would otherwise have
    had.
    """
    rows = conn.execute(
        "update escalation set status = 'expired'"
        " where status in ('waiting', 'answering')"
        "   and created_at < now() - %s::interval"
        " returning id", (f"{int(EXPIRE_AFTER.total_seconds())} seconds",)
    ).fetchall()
    return [get(conn, str(r[0])) for r in rows]


def outstanding(conn: Connection) -> list[dict]:
    """Everything still waiting, oldest first. For the owner's own review."""
    rows = conn.execute(
        "select id from escalation where status in ('waiting', 'answering')"
        " order by created_at").fetchall()
    return [get(conn, str(r[0])) for r in rows]

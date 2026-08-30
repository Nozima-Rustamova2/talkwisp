"""The review queue. Steps 17, 18 and 19.

Extracted facts arrive unconfirmed and the agent never uses them. This is what
turns them into knowledge, or throws them away.

Three things the design decisions fix here, each of which is easy to get wrong:

  * Every queued fact is shown WITH the source text it came from. A fact with
    no provenance cannot be judged -- "Toshmatov Aziz / qabul vaqti" is either
    right or invented, and only the document says which.

  * REJECT MEANS THE EXTRACTION IS WRONG, not that the thing stopped being
    true. Rejecting deletes the proposal. Removing something that is genuinely
    no longer true is a different action on a different screen, against a
    confirmed fact.

  * A CONFLICT IS SURFACED, NEVER RESOLVED AUTOMATICALLY. Two values for one
    subject and attribute may both be true -- summer and winter hours, two
    branches -- so the system's job is to put them side by side, not to pick.
"""

from psycopg import Connection

from app.embeddings import DIMENSIONS, MODEL, embed_document
from app.normalize import normalize

# How much of the source to show beside each fact. Enough to recognise the
# document and find the line; not so much that the screen becomes the document.
EXCERPT_CHARS = 400

_QUEUE = """
select f.id, f.subject, f.attribute, f.value, f.confidence,
       s.id, s.label, s.filename, s.kind, left(s.content, %(chars)s),
       f.created_at
  from fact f
  left join source s on s.id = f.source_id
 where not f.confirmed
 order by s.id nulls last, f.subject, f.attribute
"""

# Any subject+attribute answered more than one way, whether the values were
# typed, extracted, or one of each. Unconfirmed rows are included on purpose:
# the most useful moment to see a contradiction is before confirming it.
_CONFLICTS = """
select subject_key, attribute_key,
       json_agg(json_build_object(
           'id', id, 'subject', subject, 'attribute', attribute,
           'value', value, 'confirmed', confirmed, 'confidence', confidence,
           'typed', source_id is null, 'source_id', source_id
       ) order by confirmed desc, id)
  from fact
 group by subject_key, attribute_key
having count(distinct value) > 1
 order by subject_key, attribute_key
"""


def queue(conn: Connection) -> list[dict]:
    """Step 17. Everything awaiting review, each with where it came from."""
    rows = conn.execute(_QUEUE, {"chars": EXCERPT_CHARS}).fetchall()
    return [
        {
            "id": str(fid),
            "subject": subject, "attribute": attribute, "value": value,
            "confidence": confidence,
            "source": None if sid is None else {
                "id": str(sid), "label": label, "filename": filename,
                "kind": kind, "excerpt": excerpt,
            },
            "created_at": created.isoformat(),
        }
        for (fid, subject, attribute, value, confidence,
             sid, label, filename, kind, excerpt, created) in rows
    ]


def conflicts(conn: Connection) -> list[dict]:
    """Step 19. Subject+attribute pairs answered more than one way."""
    return [
        {"subject_key": subject_key, "attribute_key": attribute_key,
         "values": values}
        for subject_key, attribute_key, values in conn.execute(_CONFLICTS)
    ]


def _fact(conn: Connection, fact_id: str) -> tuple | None:
    return conn.execute(
        "select subject, attribute, value, confirmed from fact where id = %s",
        (fact_id,),
    ).fetchone()


def edit(conn: Connection, fact_id: str, subject: str | None = None,
         attribute: str | None = None, value: str | None = None) -> bool:
    """Step 18. Correct a proposal before confirming it.

    The keys are rewritten from the new text -- editing the display form and
    leaving the match key behind would make the fact unreachable while looking
    perfectly correct on screen.
    """
    current = _fact(conn, fact_id)
    if current is None:
        return False
    subject = subject if subject is not None else current[0]
    attribute = attribute if attribute is not None else current[1]
    value = value if value is not None else current[2]

    conn.execute(
        "update fact set subject = %s, subject_key = %s, attribute = %s,"
        " attribute_key = %s, value = %s, value_key = %s, updated_at = now()"
        " where id = %s",
        (subject, normalize(subject), attribute, normalize(attribute),
         value, normalize(value), fact_id),
    )
    return True


def confirm(conn: Connection, fact_id: str) -> dict | None:
    """Step 18. Accept a proposal, and embed it.

    Embedding happens here rather than at extraction because vector search
    filters on `confirmed`: embedding at extraction would spend quota on facts
    that get rejected. The cost is that confirming makes one API call.
    """
    current = _fact(conn, fact_id)
    if current is None:
        return None
    subject, attribute, value, _ = current

    vector = str(embed_document(f"{subject} / {attribute} / {value}"))
    conn.execute(
        "update fact set confirmed = true, updated_at = now(),"
        " embedding = %s, embedding_model = %s where id = %s",
        (vector, f"{MODEL}@{DIMENSIONS}", fact_id),
    )

    # What this now contradicts. Surfaced, not acted on.
    others = conn.execute(
        "select id, subject, attribute, value, confirmed from fact"
        " where subject_key = %s and attribute_key = %s and value <> %s",
        (normalize(subject), normalize(attribute), value),
    ).fetchall()
    return {
        "id": fact_id, "subject": subject, "attribute": attribute,
        "value": value, "confirmed": True,
        "now_conflicts_with": [
            {"id": str(i), "subject": s, "attribute": a, "value": v,
             "confirmed": c} for i, s, a, v, c in others
        ],
    }


def reject(conn: Connection, fact_id: str) -> bool:
    """Step 18. The extraction was wrong; the proposal goes away.

    Only unconfirmed facts. Deleting a confirmed one is not rejection -- it is
    removing something the business said is true, which is a different action
    that should not share an endpoint with "the model misread this".
    """
    deleted = conn.execute(
        "delete from fact where id = %s and not confirmed returning id",
        (fact_id,),
    ).fetchone()
    return deleted is not None

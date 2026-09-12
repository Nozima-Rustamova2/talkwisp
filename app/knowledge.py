"""Everything the agent knows, and the three things an owner can do to it.

WHY THIS EXISTS. app/review.py serves `where not confirmed` -- the proposals
queue. The moment a fact is confirmed it leaves that queue and becomes invisible
to the entire API. An owner whose price changed or whose doctor left had no
route to fix it except typing a new fact in Telegram and living with the old one
contradicting it. On the live clinic that is 140 facts with no way to see them.

CONFIRMED FACTS ARE NOT PROPOSALS, and that distinction is already stated in
review.reject(): "Deleting a confirmed one is not rejection -- it is removing
something the business said is true, which is a different action that should not
share an endpoint with 'the model misread this'." The same argument applies to
editing, which is why edit_confirmed() lives here rather than widening the
review endpoints.
"""

from psycopg import Connection

from app.embeddings import DIMENSIONS, MODEL, embed_document
from app.normalize import normalize

EXCERPT_CHARS = 400

# Grouped by subject, because that is how an owner thinks about it -- "what do
# we say about Dr Rahimov" -- and because 140 facts in one flat list is the
# thing this screen exists to stop.
#
# Provenance comes from the same join Review uses and speaks the same
# vocabulary: a source, or nothing, which the screen renders as "you typed
# this". There is no sheet-and-row anchoring in the schema and no ingestion path
# that could produce it, so nothing here claims any.
_ALL = """
select f.id, f.subject, f.subject_key, f.attribute, f.attribute_key, f.value,
       f.confirmed, f.source_id, s.label, s.filename, s.kind,
       left(s.content, %(chars)s), f.created_at, f.updated_at,
       f.expires_at, f.expires_at is not null and f.expires_at <= now(),
       f.expected_multiple
  from fact f
  left join source s on s.id = f.source_id
 order by f.subject, f.attribute
"""


def everything(conn: Connection) -> list[dict]:
    """Every fact this business has, confirmed or not, grouped by subject.

    UNCONFIRMED ONES ARE INCLUDED, marked. Hiding them would make this screen
    disagree with Review about what exists, and an owner looking for a fact they
    just extracted would not find it. The screen shows the state rather than
    filtering by it.
    """
    # NO PAGINATION, AND THE THRESHOLD IS A NUMBER SO THE NEXT PERSON KNOWS IT
    # WAS A DECISION. The largest live business has 140 facts across 25
    # subjects: one modest response, filtered in the browser, which is instant
    # and one fewer endpoint to keep in step with the grouping. Somewhere around
    # two or three thousand facts -- a megabyte of JSON, and an excerpt per
    # extracted fact -- this stops being true and the screen needs server-side
    # search and paging. It is not close to that.
    rows = conn.execute(_ALL, {"chars": EXCERPT_CHARS}).fetchall()

    # WHICH SUBJECT+ATTRIBUTE IS ANSWERED MORE THAN ONE WAY.
    #
    # Review surfaces conflicts among PROPOSALS, which is the right moment to
    # catch a bad extraction. But the stale one lives here: two `ish vaqti`
    # facts, one typed months ago and one read off a price list since, both
    # confirmed, both retrievable, disagreeing. That is the single thing an
    # owner most needs to notice about their own knowledge base, and until this
    # screen existed there was nowhere to notice it.
    #
    # Derived from the rows already fetched rather than a second query: the
    # answer is a fact about this result set, and asking the database again
    # would be asking a question this data already answers.
    values: dict[tuple, set] = {}
    dismissed: set = set()
    for r in rows:
        pair = (r[2], r[4])
        # An EXPIRED fact does not disagree with a live one -- it used to be
        # true and now is not, which is the ordinary way a price changes rather
        # than a contradiction to resolve. Counting it would mean every
        # correctly-expired promotion raised a flag forever.
        if not r[15]:
            values.setdefault(pair, set()).add(r[5])
        if r[16]:
            dismissed.add(pair)
    disputed = {k for k, v in values.items() if len(v) > 1} - dismissed

    groups: dict[str, dict] = {}
    for (fid, subject, subject_key, attribute, attribute_key, value, confirmed,
         source_id, label, filename, kind, excerpt, created, updated,
         expires_at, expired, expected_multiple) in rows:
        group = groups.setdefault(subject_key, {"subject": subject,
                                                "subject_key": subject_key,
                                                "facts": []})
        group["facts"].append({
            "id": str(fid),
            "attribute": attribute,
            "value": value,
            "confirmed": confirmed,
            # True when another fact answers this same subject and attribute
            # differently. Both sides carry the flag, because the screen shows
            # them together and neither is the one that is wrong.
            "disputed": (subject_key, attribute_key) in disputed,
            # The same two-case vocabulary as Review and the test console. A
            # typed fact has no source BY DESIGN -- it is not missing data, and
            # "you typed this" is better provenance than a filename.
            "origin": "extracted" if source_id else "typed",
            "source": ({"id": str(source_id), "label": label,
                        "filename": filename, "kind": kind,
                        "excerpt": excerpt} if source_id else None),
            "created_at": created.isoformat() if created else None,
            "updated_at": updated.isoformat() if updated else None,
            "expires_at": expires_at.isoformat() if expires_at else None,
            # EXPIRED FACTS ARE STILL LISTED, marked. The whole point of not
            # deleting them is that the owner can see what happened and why the
            # agent stopped saying it -- filtering them out of the owner's own
            # view would recreate the invisibility this screen exists to fix.
            "expired": expired,
        })
    for group in groups.values():
        group["disputes"] = sum(1 for f in group["facts"] if f["disputed"])
    # Subjects with a contradiction first. The owner opened this screen to find
    # something; if there is something wrong, that is what they should meet.
    return sorted(groups.values(),
                  key=lambda g: (not g["disputes"], g["subject"].lower()))


def edit_confirmed(conn: Connection, fact_id: str, subject: str | None = None,
                   attribute: str | None = None,
                   value: str | None = None) -> dict | None:
    """Change what the business says is true, and keep the fact findable.

    TWO THINGS MUST MOVE WITH THE TEXT, and review.edit() only does the first.

    The KEYS, or the fact matches on its old spelling and is unreachable by its
    new one -- review.edit()'s own comment says it: "editing the display form
    and leaving the match key behind would make the fact unreachable while
    looking perfectly correct on screen."

    The EMBEDDING, which review.edit() does not touch. That is harmless for a
    proposal, because confirm() embeds on approval -- and silently wrong for a
    confirmed fact. Edit "250 000 so'm" to "300 000 so'm" and the row displays
    the new price, matches exactly on the new keys, and VECTOR-MATCHES ON THE
    OLD TEXT FOREVER: the agent finds it when a customer asks about the old
    price, and answers with the new one. Nothing errors, and nothing in the UI
    could show it.

    So this costs an embedding. That is the price of editing a live fact, it is
    on the owner's deliberate action, and it is cheaper than the alternative.
    """
    current = conn.execute(
        "select subject, attribute, value, confirmed from fact where id = %s",
        (fact_id,)).fetchone()
    if current is None:
        return None
    subject = subject if subject is not None else current[0]
    attribute = attribute if attribute is not None else current[1]
    value = value if value is not None else current[2]
    confirmed = current[3]

    # Only a confirmed fact is retrievable, so only a confirmed fact needs a
    # current vector. An unconfirmed one gets embedded by review.confirm() when
    # it is approved, and doing it here too would spend twice.
    vector = None
    if confirmed:
        vector = str(embed_document(f"{subject} / {attribute} / {value}"))

    conn.execute(
        "update fact set subject = %s, subject_key = %s, attribute = %s,"
        " attribute_key = %s, value = %s, value_key = %s, updated_at = now(),"
        " embedding = coalesce(%s::vector, embedding),"
        " embedding_model = case when %s::vector is null then embedding_model"
        "                        else %s end"
        " where id = %s",
        (subject, normalize(subject), attribute, normalize(attribute),
         value, normalize(value), vector, vector,
         f"{MODEL}@{DIMENSIONS}", fact_id))
    return {"id": fact_id, "subject": subject, "attribute": attribute,
            "value": value, "confirmed": confirmed,
            "reembedded": vector is not None}


def set_expiry(conn: Connection, fact_id: str, expires_at: str | None) -> dict | None:
    """Give a fact an end date, move it, or take it away entirely.

    None clears it, which is how "reactivate" works: an expired fact is not
    deleted, so putting it back in service is removing the date rather than
    retyping the fact.

    Clears expiry_notified_at at the same time, and that matters. Extending a
    promotion whose warning already went out must arm the warning again, or the
    second expiry passes in silence -- the owner would be told once, ever, about
    a fact they kept extending.
    """
    row = conn.execute(
        "update fact set expires_at = %s::timestamptz, expiry_notified_at = null,"
        " updated_at = now() where id = %s"
        " returning id, expires_at", (expires_at, fact_id)).fetchone()
    if row is None:
        return None
    return {"id": str(row[0]),
            "expires_at": row[1].isoformat() if row[1] else None}


def set_expected_multiple(conn: Connection, subject_key: str,
                          attribute_key: str, expected: bool) -> int:
    """Mark a subject+attribute as deliberately holding several values.

    Set on EVERY row of the pair, because the property belongs to the pair
    rather than to either fact. "1 200 000 so'm" and "950 000 so'm (10 days
    before the group starts)" are both true, and neither one is the one that is
    intentional.
    """
    rows = conn.execute(
        "update fact set expected_multiple = %s"
        " where subject_key = %s and attribute_key = %s returning id",
        (expected, subject_key, attribute_key)).fetchall()
    return len(rows)


def expiring_soon(conn: Connection, within_hours: int) -> list[dict]:
    """Facts about to lapse that the owner has not been warned about.

    Claims them as it returns them -- expiry_notified_at is stamped in the same
    statement -- so a sweep that runs every five minutes tells the owner once
    rather than 576 times. The same reasoning as pinging once per escalation
    rather than once per waiting customer.
    """
    rows = conn.execute(
        "update fact set expiry_notified_at = now()"
        " where id in ("
        "   select id from fact"
        "    where expires_at is not null and expiry_notified_at is null"
        "      and expires_at > now()"
        "      and expires_at <= now() + make_interval(hours => %s))"
        " returning id, subject, attribute, value, expires_at",
        (within_hours,)).fetchall()
    return [{"id": str(r[0]), "subject": r[1], "attribute": r[2],
             "value": r[3], "expires_at": r[4]} for r in rows]


def remove(conn: Connection, fact_id: str) -> bool:
    """Delete a fact outright, confirmed or not.

    A REAL DELETE, not a soft one. Expiry is the soft option and it is a
    separate feature; having both would make "gone" ambiguous -- an owner
    looking at a knowledge base could not tell whether a missing fact was
    removed or merely lapsed.

    Unlike review.reject() this does not filter on `confirmed`, because that is
    the whole point: this is the endpoint for removing something the business
    said is true. reject() stays as it is, for proposals.
    """
    row = conn.execute("delete from fact where id = %s returning id",
                       (fact_id,)).fetchone()
    return row is not None


# --- aliases ----------------------------------------------------------------
#
# NOTHING IN app/ HAS EVER WRITTEN ONE. seed.py inserts them and two check
# scripts do; the running product reads them in retrieval.py and buy.py and has
# no way to create one. The live clinic has 106, all seeded, and an owner who
# noticed customers calling a service by another name had no route at all.
#
# They are load-bearing for cross-script matching: "кардиолог" and "kardiolog"
# reach the same subject only because an alias says so.


def aliases(conn: Connection) -> list[dict]:
    """Every alias, with the subject it points at."""
    rows = conn.execute(
        "select a.id, a.subject_key, a.alias, a.alias_key, a.confirmed,"
        " a.source_id, a.created_at,"
        " (select min(f.subject) from fact f"
        "   where f.subject_key = a.subject_key) as subject"
        " from alias a order by a.subject_key, a.alias").fetchall()
    return [{"id": str(r[0]), "subject_key": r[1], "alias": r[2],
             "alias_key": r[3], "confirmed": r[4],
             "origin": "extracted" if r[5] else "typed",
             "created_at": r[6].isoformat() if r[6] else None,
             # The display name of whatever it points at. None means the alias
             # outlived its subject -- worth showing rather than hiding, since
             # it is an alias that can never match anything.
             "subject": r[7]}
            for r in rows]


def add_alias(conn: Connection, subject_key: str, alias: str) -> dict | None:
    """Point another spelling at an existing subject.

    Confirmed on write, like a typed fact: the owner said it, and an alias
    nobody has approved would do nothing at all -- retrieval filters on
    `confirmed`.

    Returns None if the subject does not exist. An alias for a subject with no
    facts is an alias that can never match, and creating one silently would look
    like it worked.
    """
    exists = conn.execute(
        "select 1 from fact where subject_key = %s limit 1",
        (subject_key,)).fetchone()
    if not exists:
        return None
    row = conn.execute(
        "insert into alias (subject_key, alias, alias_key, confirmed)"
        " values (%s, %s, %s, true)"
        # The unique index is (business_id, alias_key, subject_key) from 0007.
        # Re-adding the same alias is not an error worth surfacing to someone
        # who just wanted it to exist.
        " on conflict do nothing returning id",
        (subject_key, alias.strip(), normalize(alias))).fetchone()
    if row is None:
        return {"id": None, "alias": alias.strip(), "already": True}
    return {"id": str(row[0]), "alias": alias.strip(), "already": False}


def remove_alias(conn: Connection, alias_id: str) -> bool:
    row = conn.execute("delete from alias where id = %s returning id",
                       (alias_id,)).fetchone()
    return row is not None

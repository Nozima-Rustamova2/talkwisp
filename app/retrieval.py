"""Find facts for a question. No LLM, no vectors -- this is the deterministic path.

The whole method is: normalize the question, then look for any subject key or
alias key that appears inside it. That works better than it sounds in Uzbek,
because the language is agglutinative -- suffixes attach to the end of a word,
so "ish vaqti" is a prefix of "ish vaqtingiz" and plain substring containment
survives the grammar that would defeat exact token matching.

Two rules keep it from over-matching:

  * a match must begin at a word boundary, so short keys like "ekg" cannot fire
    inside the middle of an unrelated word;
  * only the longest matches count, so "dr rasulova" beats the bare "rasulova"
    and resolves to one doctor instead of two.

When the longest match still points at several subjects, that is ambiguity, and
the caller must ask which -- never pick.
"""

from psycopg import Connection

from app.normalize import normalize
from app.payment import PAYMENT_SUBJECT_KEY

# EVERY query here reads `retrievable_fact`, not `fact`. The view excludes the
# reserved payment subject, so the exclusion is inherited rather than repeated
# -- including by queries not yet written. See migrations/0005 and app/payment.py.
#
# Candidate keys: every subject that has facts, plus every confirmed alias.
# A match must start at the beginning of the string or just after a space.
#
# The alias arm is the ONE place the view cannot reach, because it reads
# `alias` and the view is on `fact`. An alias pointing at the payment subject
# would otherwise resurface it through this union alone. check_subject() also
# refuses to create such an alias, but that is a guard in another module, and
# depending on it silently is exactly the hole this design is avoiding.
#
# `confirmed` IN THE FIRST ARM IS NOT REDUNDANT AND MUST NOT BE REMOVED.
# app/extract.py deliberately skips check_subject() on the reasoning that an
# extracted subject is unreachable until someone confirms it. This clause is
# what makes that true. Removing it turns extraction into an unguarded door
# for body-part subjects, and nothing anywhere would fail. See the matching
# note in extract.py -- the dependency is written in both places because it is
# invisible from either one alone.
_MATCH = """
with candidates as (
    select distinct subject_key, subject_key as key from retrievable_fact where confirmed
    union
    select subject_key, alias_key from alias
     where confirmed and subject_key <> %(reserved)s
)
select subject_key, key from candidates
 where %(q)s ~ ('(^|\\s)' || key)
 order by length(key) desc
"""

_ATTRIBUTES = """
select attribute_key from retrievable_fact
 where confirmed and %(q)s ~ ('(^|\\s)' || attribute_key)
 group by attribute_key
 order by length(attribute_key) desc
"""

# Matching on what a fact SAYS, not what it is about: "Endokrinolog kim?" names
# no subject, because "endokrinolog" is a value of lavozim. Capped at 40
# characters -- long values are never search terms, and letting them in would
# only add noise. This tier runs ONLY when no subject matched, so a question
# that names a subject is never outranked by a value that merely shares a word.
_VALUES = """
select f.id, f.subject, f.attribute, f.value, f.created_at, s.id, s.label, s.filename, s.kind
  from retrievable_fact f
  left join source s on s.id = f.source_id
 where f.confirmed and f.value_key is not null
   and length(f.value_key) <= 40
   and %(q)s ~ ('(^|\\s)' || f.value_key)
 order by length(f.value_key) desc
"""


def _fact_row(row: tuple) -> dict:
    """One retrieved fact, WITH where it came from.

    Provenance is carried out of the query rather than looked up afterwards. A
    second lookup would be reading a copy of what the answer used instead of
    what it used -- the same mistake as a grader reading a stale field, and the
    console exists to show what the agent actually did.

    `source_*` is None for a typed fact (`source_id IS NULL`), which is not
    missing data: the owner typed it, and "you typed this" is better provenance
    than a filename. `created_at` is carried so the console can date it.
    """
    (fact_id, subject, attribute, value, created_at,
     source_id, source_label, source_filename, source_kind) = row
    return {
        "id": str(fact_id),
        "subject": subject, "attribute": attribute, "value": value,
        "created_at": created_at.isoformat() if created_at else None,
        "source_id": str(source_id) if source_id else None,
        "source_label": source_label,
        "source_filename": source_filename,
        "source_kind": source_kind,
    }


def _longest(rows: list[tuple[str, str]]) -> list[str]:
    """Keep only the subjects matched by the longest key, preserving ties."""
    if not rows:
        return []
    longest = len(rows[0][1])
    return sorted({subject for subject, key in rows if len(key) == longest})


def find(conn: Connection, question: str) -> dict:
    """Answer from facts alone. Returns what was matched, not just the answer."""
    key = normalize(question)

    subjects = _longest(conn.execute(
        _MATCH, {"q": key, "reserved": PAYMENT_SUBJECT_KEY}).fetchall())
    attributes = [r[0] for r in conn.execute(_ATTRIBUTES, {"q": key}).fetchall()]
    attribute = attributes[0] if attributes else None

    result = {
        "question": question,
        "question_key": key,
        "subjects": subjects,
        "attribute": attribute,
        "facts": [],
        "matched_on": "subject" if subjects else None,
        "status": "not_found",
    }

    # More than one subject at the same match length is genuine ambiguity: two
    # doctors really are called Rasulova. Answering for either would be a guess.
    if len(subjects) > 1:
        result["status"] = "ambiguous"
        return result

    sql = ("select f.id, f.subject, f.attribute, f.value, f.created_at, s.id, s.label, s.filename, s.kind"
           " from retrievable_fact f left join source s on s.id = f.source_id"
           " where f.confirmed and f.subject_key = any(%(s)s)")
    params: dict = {"s": subjects}

    if subjects and attribute:
        rows = conn.execute(sql + " and f.attribute_key = %(a)s",
                            params | {"a": attribute}).fetchall()
        # The attribute may be a coincidence ("narx" inside an unrelated
        # question). If it filters everything away, fall back to the subject.
        if not rows:
            rows = conn.execute(sql, params).fetchall()
    elif subjects:
        rows = conn.execute(sql, params).fetchall()
    elif attribute:
        # No subject named at all -- common for questions about the business
        # itself ("Ish vaqtingiz qanday?" never says "Shifo Med"). Answer from
        # the attribute across every subject and let the caller see the count.
        rows = conn.execute(
            "select f.id, f.subject, f.attribute, f.value, f.created_at, s.id, s.label, s.filename, s.kind"
            " from retrievable_fact f left join source s on s.id = f.source_id"
            " where f.confirmed and f.attribute_key = %(a)s", {"a": attribute}
        ).fetchall()
        result["matched_on"] = "attribute"
    else:
        # Last tier: match on the value. Deliberately last, so a subject or
        # attribute match always wins over a word that merely appears in a value.
        rows = conn.execute(_VALUES, {"q": key}).fetchall()
        result["matched_on"] = "value" if rows else None

    result["facts"] = [_fact_row(r) for r in rows]
    result["status"] = "ok" if rows else "not_found"
    return result

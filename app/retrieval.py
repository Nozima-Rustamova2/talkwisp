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

# Candidate keys: every subject that has facts, plus every confirmed alias.
# A match must start at the beginning of the string or just after a space.
_MATCH = """
with candidates as (
    select distinct subject_key, subject_key as key from fact where confirmed
    union
    select subject_key, alias_key from alias where confirmed
)
select subject_key, key from candidates
 where %(q)s ~ ('(^|\\s)' || key)
 order by length(key) desc
"""

_ATTRIBUTES = """
select attribute_key from fact
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
select subject, attribute, value from fact
 where confirmed and value_key is not null and length(value_key) <= 40
   and %(q)s ~ ('(^|\\s)' || value_key)
 order by length(value_key) desc
"""


def _longest(rows: list[tuple[str, str]]) -> list[str]:
    """Keep only the subjects matched by the longest key, preserving ties."""
    if not rows:
        return []
    longest = len(rows[0][1])
    return sorted({subject for subject, key in rows if len(key) == longest})


def find(conn: Connection, question: str) -> dict:
    """Answer from facts alone. Returns what was matched, not just the answer."""
    key = normalize(question)

    subjects = _longest(conn.execute(_MATCH, {"q": key}).fetchall())
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

    sql = ("select subject, attribute, value from fact"
           " where confirmed and subject_key = any(%(s)s)")
    params: dict = {"s": subjects}

    if subjects and attribute:
        rows = conn.execute(sql + " and attribute_key = %(a)s",
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
            "select subject, attribute, value from fact"
            " where confirmed and attribute_key = %(a)s", {"a": attribute}
        ).fetchall()
        result["matched_on"] = "attribute"
    else:
        # Last tier: match on the value. Deliberately last, so a subject or
        # attribute match always wins over a word that merely appears in a value.
        rows = conn.execute(_VALUES, {"q": key}).fetchall()
        result["matched_on"] = "value" if rows else None

    result["facts"] = [
        {"subject": s, "attribute": a, "value": v} for s, a, v in rows
    ]
    result["status"] = "ok" if rows else "not_found"
    return result

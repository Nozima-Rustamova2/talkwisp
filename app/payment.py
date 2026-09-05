"""Payment details: the one subject that is never retrieved, and the message
that is assembled in code instead.

The whole point of this module is what it does NOT do. It does not ask the
model to write the message, and it does not let the card number reach a prompt.
The values are read from the fact table and joined with "\\n". A model that
paraphrases a card number is a model that can get it wrong, and there is no
acceptable failure there -- the same argument that keeps the emergency numbers
in triage.py fixed.

Three layers, only one of which is here:

  the view  `retrievable_fact` excludes this subject, so every retrieval query
            inherits the exclusion by reading the obvious thing
  the check `fact_payment_not_embedded` means a payment fact CANNOT carry an
            embedding, so both vector searches are closed by the database
            rather than by the text of their WHERE clauses
  this file names the subject, recognises the question, and joins the strings

Delete this file and nothing leaks. That is the test of whether the exclusion
is in the right place.
"""

from psycopg import Connection

from app.normalize import normalize

# The reserved subject. THE KEY IS DUPLICATED IN migrations/0005 -- SQL cannot
# call normalize(), so the view's predicate is a literal. check_payment.py
# asserts the two agree rather than trusting anyone to notice.
PAYMENT_SUBJECT = "Toʻlov maʼlumotlari"
PAYMENT_SUBJECT_KEY = normalize(PAYMENT_SUBJECT)  # "tolov malumotlari"

# The card number is the only attribute without which there is nothing to say.
CARD_ATTRIBUTE = "karta raqami"

# Appended when present, omitted when not. A seller who has not filled in their
# bank still gets a working message; a seller with no card number gets none at
# all, and the question falls through to the ordinary refusal. Never a
# half-assembled message with a blank where the number goes.
OPTIONAL_ATTRIBUTES = ("karta egasi", "bank")

# The owner's own words, per language, editable like any other fact. This is
# canned copy they rewrite, not a prompt. Keys are what detect_language()
# returns -- they are phrases because they are also spoken to the model
# elsewhere.
INSTRUCTION_ATTRIBUTE = {
    "the same language the customer wrote in, in LATIN script":
        "tolov korsatmasi lotin",
    "Uzbek, in CYRILLIC script": "tolov korsatmasi kirill",
    "Russian": "tolov korsatmasi rus",
}

# Word-start containment, the rule triage._hit() uses and the same rule
# retrieval's exact tier uses. Written in normalized form, so Cyrillic input
# reaches them after folding: "номер карты" -> "nomer karti", caught by
# "nomer kart".
#
# This list WILL miss phrasings, and that is survivable because of where the
# miss lands: a payment question this does not recognise falls through to
# retrieval, retrieval cannot see the payment subject, nothing clears the
# similarity floor, and the customer gets NO_ANSWER with a logged gap. The
# failure mode is silence, never a wrong card number. Same bound as the
# buy-intent classifier -- model routes, code decides.
#
# A false positive costs a card number shown to someone who asked something
# else. Unwanted, not dangerous, which is why the list can afford to be broad.
MARKERS = (
    # Uzbek, either script once folded
    "karta raqam", "kartangiz", "karta nomer", "hisob raqam", "plastik",
    "qayerga pul", "qayerga tolay", "qayerga tolash", "pul otkaz",
    "tolov qanday", "qanday tolay", "qanday tolash", "pul tashlay",
    # Russian
    "nomer kart", "rekvizit", "kuda perevesti", "kuda platit", "kuda skinut",
    "karta dlya oplat", "kak oplatit", "schet dlya",
)


def detect(question: str) -> str | None:
    """The marker that makes this a payment-details question, or None."""
    text = normalize(question)
    if not text:
        return None
    for marker in MARKERS:
        if text.startswith(marker) or f" {marker}" in text:
            return marker
    return None


def details(conn: Connection) -> dict[str, str]:
    """Every payment fact, by attribute key.

    Reads `fact` and not `retrievable_fact` on purpose: this is the one caller
    that is allowed to see them, and it does not go near a prompt.
    """
    rows = conn.execute(
        "select attribute_key, value from fact"
        " where subject_key = %s and confirmed",
        (PAYMENT_SUBJECT_KEY,),
    ).fetchall()
    return {k: v for k, v in rows}


def message(conn: Connection, language: str) -> str | None:
    """The payment message, or None if it cannot be assembled completely.

    Every character comes from a stored value or from a newline written here.
    Nothing is generated, nothing is formatted conditionally beyond the two
    optional lines, and the card block is IDENTICAL in all three languages --
    only the owner's instruction line above it changes. Language detection is
    good but not perfect, and a card number is the wrong place to find that out.
    """
    stored = details(conn)
    card = stored.get(CARD_ATTRIBUTE)
    instruction = stored.get(INSTRUCTION_ATTRIBUTE[language])
    if not card or not instruction:
        return None
    lines = [instruction, card]
    lines += [stored[a] for a in OPTIONAL_ATTRIBUTES if stored.get(a)]
    return "\n".join(lines)

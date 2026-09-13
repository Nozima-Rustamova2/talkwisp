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

# The line that asks for the amount to be sent EXACTLY. Also the owner's, also
# per language, and it is the only mitigation there is for the customer who
# types the round number anyway -- see the reliability list in
# docs/design-decisions.md. Prominence is not a guarantee.
EXACT_ATTRIBUTE = {
    "the same language the customer wrote in, in LATIN script":
        "aniq summa ogohlantirishi lotin",
    "Uzbek, in CYRILLIC script": "aniq summa ogohlantirishi kirill",
    "Russian": "aniq summa ogohlantirishi rus",
}

# Uzbekistan only, and stated rather than assumed. The stored prices already
# carry "soʻm"; this formats an integer the same way for the one line that is
# computed rather than copied. A second country needs a decision here, not a
# parameter added in passing.
CURRENCY = "soʻm"


def som(amount: int) -> str:
    """250003 -> '250 003 soʻm'. A space is the thousands separator here."""
    # The separator is a PLAIN space, U+0020. It was a non-breaking space
    # here for one commit -- visually identical, and it would have made
    # every computed amount fail to string-match the stored prices that use
    # a plain one. Caught by check_buy.py asserting the amount appears
    # verbatim; nothing else would ever have shown it.
    return f"{amount:,}".replace(",", " ") + f" {CURRENCY}"

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


def order_message(conn: Connection, language: str, order: dict) -> str | None:
    """The payment message for one order, or None if it cannot be assembled.

    Same rules as message() above, plus the amount -- which comes from
    `order["amount"]`, a column the database computed and constrained
    (`amount = base_amount + suffix`). No model, no arithmetic here beyond the
    thousands separator.

    The amount is on its own line, alone, directly under the card block,
    because the whole unique-suffix trick collapses if the customer rounds it
    off. That is prominence, not a guarantee: item 3 on the
    cannot-be-made-reliable list stands.
    """
    base = message(conn, language)
    if base is None:
        return None
    warning = details(conn).get(EXACT_ATTRIBUTE[language])
    if not warning:
        return None
    return f"{base}\n\n{som(order['amount'])}\n{warning}"


# --- the owner's side: setting the details, and knowing they are set ---------
#
# THE WRITER check_subject()'s `allow_reserved` WAS WAITING FOR. That parameter
# has existed since the exclusion was built, documented as "passed by the one
# writer that is supposed to use that subject", and until now it had NO CALLER
# -- so the reserved subject was refused on every path an owner could reach.
# Not a screen, not /fact, not Add knowledge. The only thing that ever wrote a
# payment fact was seed.py, by raw SQL, for the reference clinic.
#
# The cost of that was not theoretical. A self-serve business connected a bot,
# a customer tapped buy, an order was created, and order_message() returned
# None at the last step -- so the customer was told "contact us" and the owner
# was never told anything. Every self-serve business would have hit it.

# What may be stored under the reserved subject, and nothing else.
#
# A WHITELIST, because the exclusion is total and has no idea what it is
# hiding: a business fact written under this subject would be invisible to
# retrieval forever, answering nothing and explaining nothing. check_subject()
# stops another SUBJECT being claimed; this stops this writer being used as a
# side door for arbitrary ATTRIBUTES.
SETTABLE = ((CARD_ATTRIBUTE,)
            + OPTIONAL_ATTRIBUTES
            + tuple(INSTRUCTION_ATTRIBUTE.values())
            + tuple(EXACT_ATTRIBUTE.values()))

# The three languages, in the order the screen shows them, with a label for
# each. Keyed by what detect_language() returns -- the same strings that key
# INSTRUCTION_ATTRIBUTE and EXACT_ATTRIBUTE, so a fourth language is added in
# one place and every dict above fails loudly if it was missed.
LANGUAGES = (
    ("the same language the customer wrote in, in LATIN script", "Uzbek (Latin)"),
    ("Uzbek, in CYRILLIC script", "Uzbek (Cyrillic)"),
    ("Russian", "Russian"),
)


class NotSettable(ValueError):
    """An attribute that is not part of the payment details."""


def set_detail(conn: Connection, attribute: str, value: str | None) -> None:
    """Write one payment detail, or clear it when `value` is empty.

    NEVER EMBEDDED, and that is enforced twice: nothing is passed for the
    vector here, and the constraint fact_payment_not_embedded refuses the row
    if it ever were. A payment fact carrying an embedding is a card number in
    a vector search's candidate set.

    Confirmed on write, unlike every other owner-typed fact. Review exists so a
    fact is read by a human before a customer can be answered from it -- and
    nothing can ever be answered from this subject. A payment detail sitting
    unconfirmed would be a card number the owner typed, cannot see, and that
    silently does not work.
    """
    if attribute not in SETTABLE:
        raise NotSettable(
            f"{attribute!r} is not a payment detail. Allowed: "
            f"{', '.join(SETTABLE)}. Anything else stored under "
            f"{PAYMENT_SUBJECT!r} would be excluded from retrieval and answer "
            "nothing -- see the exclusion in migrations/0005.")
    # The one sanctioned use. Refused for every other subject, which is what
    # keeps an ordinary business fact from being hidden here by accident.
    #
    # Imported here, not at module scope: app/triage.py imports this module for
    # PAYMENT_SUBJECT_KEY, so a top-level import is a cycle.
    from app.triage import check_subject
    check_subject(PAYMENT_SUBJECT, allow_reserved=True)

    key = normalize(attribute)
    conn.execute("delete from fact where subject_key = %s and attribute_key = %s",
                 (PAYMENT_SUBJECT_KEY, key))
    if not (value or "").strip():
        return
    value = value.strip()
    conn.execute(
        "insert into fact (subject, subject_key, attribute, attribute_key,"
        " value, value_key, confirmed) values (%s, %s, %s, %s, %s, %s, true)",
        (PAYMENT_SUBJECT, PAYMENT_SUBJECT_KEY, attribute, key,
         value, normalize(value)))


def ready_for(conn: Connection, language: str) -> bool:
    """Whether order_message() could be assembled for this language.

    THE QUESTION THE BUY OFFER HAS TO ASK BEFORE OFFERING. It was asked after
    the order was created, which is how a customer reached a dead end with a
    purchase row behind it.

    Derived from the same three lookups order_message() does rather than from a
    stored flag, because a flag is a second answer to the question and the two
    drift the first time an owner clears a field.
    """
    stored = details(conn)
    return bool(stored.get(CARD_ATTRIBUTE)
                and stored.get(INSTRUCTION_ATTRIBUTE[language])
                and stored.get(EXACT_ATTRIBUTE[language]))


def has_orderable_prices(conn: Connection) -> bool:
    """Whether the buy flow can fire at all for this business.

    Same definition of a price as orders.price_options() -- a confirmed
    retrievable fact whose attribute looks like a price and whose value parses
    to an exact amount. A range or a floor is not orderable, so a business
    whose prices are all "from 200 000" cannot dead-end anyone and should not
    be told to fill in payment details.
    """
    from app.orders import parse_amount

    rows = conn.execute(
        "select value from retrievable_fact"
        " where attribute_key like %s and confirmed", ("%narx%",)).fetchall()
    return any(parse_amount(v) is not None for (v,) in rows)


def completeness(conn: Connection) -> dict:
    """What is filled in, per language, and what it means.

    PER LANGUAGE, NOT DONE/NOT-DONE, and the partial case is the reason. The
    card is shared but the instruction and the exact-amount warning are the
    owner's own words in each language, so a business with Uzbek filled in and
    Russian blank works perfectly for Uzbek customers and dead-ends Russian
    ones. A single tick would say "set up" and be wrong for a third of the
    country.
    """
    stored = details(conn)
    languages = [{
        "key": key,
        "label": label,
        "instruction": stored.get(INSTRUCTION_ATTRIBUTE[key]),
        "exact": stored.get(EXACT_ATTRIBUTE[key]),
        # The card is in every language's requirement because order_message()
        # needs it in every language. A language cannot be ready without it.
        "ready": bool(stored.get(CARD_ATTRIBUTE)
                      and stored.get(INSTRUCTION_ATTRIBUTE[key])
                      and stored.get(EXACT_ATTRIBUTE[key])),
        "instruction_attribute": INSTRUCTION_ATTRIBUTE[key],
        "exact_attribute": EXACT_ATTRIBUTE[key],
    } for key, label in LANGUAGES]

    return {
        "card": stored.get(CARD_ATTRIBUTE),
        "card_attribute": CARD_ATTRIBUTE,
        "optional": [{"attribute": a, "value": stored.get(a)}
                     for a in OPTIONAL_ATTRIBUTES],
        "languages": languages,
        "ready": [lang["label"] for lang in languages if lang["ready"]],
        # THE STATE WORTH SURFACING: the buy flow can fire and cannot complete.
        # Not "payment is unset" -- plenty of businesses never sell in chat and
        # have nothing to fix.
        "has_prices": has_orderable_prices(conn),
    }

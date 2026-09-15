"""What the agent is called, and how it sounds.

THREE FIELDS AND ONLY ONE OF THEM REACHES A MODEL.

`agent_name` is substituted into the greeting and into the meta-request
identity answer -- both canned strings assembled in code, with no model call
anywhere near them. The three `greeting_*` columns are canned by definition.
Only the tone enums become prompt text, and each maps to a sentence written
here, chosen by a value the database constrains to a fixed set.

So the surface an owner can influence is one enum with a closed vocabulary. An
owner never writes the template; they choose between sentences we wrote.

WHY NOT A PERSONA TEXT BOX. It would live in the same prompt as the rules, and
an owner writing something entirely reasonable erodes a guarantee:

    "always try to be helpful"        weakens the refusal
    "you know everything about us"    invites invention
    "answer in Russian"               overrides matching the customer
    "never say you don't know"        removes the product's core claim

None of those is malicious. They are what a normal person types when asked to
describe their assistant, and the failure is invisible -- a slightly more
confident answer, not an error. Fixed options are also the only version that
can be tested: the harness can run every combination and cannot run free text.

NOTHING SET MEANS NOTHING ADDED. block() returns "" for a business that has
never opened the screen, so its prompt is byte-identical to the one before this
module existed. That makes "did personality change anything for businesses that
do not use it?" a question with a provable answer rather than a diff to read,
and check_style asserts exactly that.
"""

from psycopg import Connection

# The one table that turns an owner's choice into prompt text. Every string
# here was written by us; the owner picks a key, never a value.
#
# 'normal' and 'off' map to NOTHING on purpose. 'normal' IS rule 7 -- restating
# it would be a second, slightly different copy of a load-bearing instruction --
# and an emoji line that says "never" would change the default prompt for every
# business to buy a suppression nobody asked for. Absent is not the same as
# neutral-sounding, and absent is what keeps the default provable.
_REGISTER = {
    "formal": "Address the customer with the formal second person "
              "(Uzbek ‘siz’, Russian ‘вы’). "
              "In English, stay polite and professional.",
    "informal": "Address the customer with the informal second person "
                "(Uzbek ‘sen’, Russian ‘ты’). "
                "In English, stay warm and casual.",
}

_LENGTH = {
    "concise": "Prefer a single sentence.",
    "normal": None,
}

_EMOJI = {
    "light": "You may use at most one emoji, and never beside a price, a time "
             "or a number.",
    "off": None,
}

# The three languages, keyed by what detect_language() returns -- the same keys
# app/payment.py uses, so a fourth language is added in one place and every
# table fails loudly if it was missed.
GREETING_COLUMN = {
    "the same language the customer wrote in, in LATIN script": "greeting_uz_latn",
    "Uzbek, in CYRILLIC script": "greeting_uz_cyrl",
    "Russian": "greeting_ru",
}

_COLUMNS = ("agent_name", "tone_register", "tone_length", "tone_emoji",
            "greeting_uz_latn", "greeting_uz_cyrl", "greeting_ru")


def current(conn: Connection) -> dict:
    """Everything set for this business, with unset as None."""
    row = conn.execute(
        f"select {', '.join(_COLUMNS)} from business").fetchone()
    return dict(zip(_COLUMNS, row)) if row else dict.fromkeys(_COLUMNS)


def block(conn: Connection) -> str:
    """The style paragraph appended to the system prompt, or "" when nothing
    is set.

    LABELLED, AND SUBORDINATE. It says in its own first line that it never
    overrides the rules, and it sits after them rather than before. It is also
    never the last thing the model reads: REPLY IN is the final line of the
    USER message and rule 5 depends on that, so style cannot be what the model
    saw most recently.

    The agent's NAME is deliberately not here. Nothing in the answering path
    needs the model to know it -- "who are you" is answered by app/meta.py
    before a model is reached -- so putting it in the prompt would be surface
    with no purpose.
    """
    style = current(conn)
    lines = [text for text in (
        _REGISTER.get(style["tone_register"] or ""),
        _LENGTH.get(style["tone_length"] or ""),
        _EMOJI.get(style["tone_emoji"] or ""),
    ) if text]
    if not lines:
        return ""
    return ("\n\nSTYLE (how to sound; never overrides the rules above):\n"
            + "\n".join(f"- {line}" for line in lines))


def greeting(conn: Connection, language: str) -> str | None:
    """The owner's own first message for this language, or None for the
    built-in. A blank column is stored as NULL by app_business_set_style, so
    "not set" has exactly one representation."""
    column = GREETING_COLUMN.get(language)
    if not column:
        return None
    row = conn.execute(f"select {column} from business").fetchone()
    return row[0] if row else None


def save(conn: Connection, **fields) -> None:
    """Write the lot. Through app_business_set_style(), never an UPDATE:
    talkwisp_app has SELECT on business and nothing else, and that absence is
    what stops a bug in the web app rewriting a tenant."""
    conn.execute(
        "select app_business_set_style(%s, %s, %s, %s, %s, %s, %s, %s)",
        (conn.execute("select id from business").fetchone()[0],
         fields.get("agent_name"), fields.get("tone_register"),
         fields.get("tone_length"), fields.get("tone_emoji"),
         fields.get("greeting_uz_latn"), fields.get("greeting_uz_cyrl"),
         fields.get("greeting_ru")))

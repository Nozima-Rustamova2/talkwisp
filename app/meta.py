"""Questions about the AGENT, not about the business.

"Speak Russian", "who are you", "what can you do" -- three reasonable things to
ask a bot, none of them answerable from a knowledge base, all of them currently
met with "I don't know that one". That is the failure that makes the agent look
stupid on an easy question, and it has three logged instances.

WHERE THIS RUNS, AND WHY IT IS THE OPPOSITE OF TRIAGE. app/triage.py
short-circuits BEFORE retrieval, because a symptom report must never reach the
fact table -- if it did, prices would already be in the prompt. This runs AFTER
retrieval has been tried and found nothing.

That inversion is the entire safety argument. "Nima qila olasiz" is
unambiguous, but "nima qila oladi bu dori" -- what can this medicine do -- is a
real question about a real thing. If the classifier ran first it would answer
that with a canned capability blurb. Running it only where retrieval already
failed means the only questions it ever sees are ones nothing could answer, so a
false positive costs a canned reply INSTEAD OF A REFUSAL, never instead of an
answer.

Markers rather than a model call, for the same reason triage uses them: this
decides whether to answer at all, and a classifier you can read is one you can
audit. It is also free, on a path that has already spent an embedding.
"""

from psycopg import Connection

from app import style
from app.normalize import normalize

# --- what language they asked for -------------------------------------------
#
# THE TARGET LANGUAGE IS NOT THE LANGUAGE THEY TYPED IN. Someone writing
# "menga rus tilida gapir" in Latin Uzbek is asking for Russian; replying in
# Uzbek because that is what they typed would be answering a request by
# declining it. The marker itself names the language, so the marker decides.
# EVERY MARKER IS IN FOLDED FORM. normalize() transliterates Cyrillic to Latin
# -- "кто ты" becomes "kto ti" -- so a marker written in Cyrillic can never
# match anything. app/triage.py says exactly this above its own Russian list and
# I wrote raw Cyrillic anyway; check_meta caught all four.
#
# A REQUEST, NOT A MENTION. "rus tilida" alone appears in "rus tilida hujjat
# kerakmi" -- do you need a document in Russian -- which is a real question
# about documents. So the markers carry the verb or the politeness form that
# makes it an instruction: "rus tilida gapir", "rus tilida yoz", "po russki".
_LANGUAGE_TARGETS = {
    "Russian": ("rus tilida gapir", "rus tilida yoz", "rus tilida javob",
                "ruscha gapir", "ruscha yoz", "ruscha javob",
                "po russki", "na russkom", "russian please",
                "speak russian", "in russian please"),
    "the same language the customer wrote in, in LATIN script": (
        "uzbekcha gapir", "uzbekcha yoz", "uzbekcha javob",
        "o'zbekcha gapir", "ozbekcha gapir", "ozbekcha yoz",
        "uzbek tilida gapir", "uzbek tilida yoz",
        "po uzbekski", "na uzbekskom", "speak uzbek"),
    "English": ("inglizcha gapir", "inglizcha yoz", "inglizcha javob",
                "ingliz tilida gapir", "ingliz tilida yoz",
                "po angliyski", "na angliyskom",
                "speak english", "in english please", "english please"),
}

# Generic "change language" with no target named. Answered in whatever they
# wrote, because that is the only signal there is.
_LANGUAGE_GENERIC = ("tilni ozgartir", "tilni o'zgartir", "til almashtir",
                     "smeni yazik", "pomenyay yazik", "drugoy yazik",
                     "change language", "another language")

_IDENTITY = ("kimsan", "kimsiz", "sen kimsan", "siz kimsiz", "kim bilan",
             "sen botmisan", "botmisan", "robotmisan", "odammisan",
             # Russian, folded: кто ты -> kto ti, ты бот -> ti bot
             "kto ti", "kto vi", "ti bot", "vi bot", "ti robot",
             "ti chelovek", "s kem ya",
             # WHAT ARE YOU CALLED, which only became answerable once an
             # agent could have a name. A named agent that refuses to say its
             # own name is the first thing anyone would try. Folded forms, as
             # everywhere in this file: "как тебя зовут" -> "kak tebya zovut".
             "isming nima", "ismingiz nima", "iso ming", "oting nima",
             "otingiz nima", "seni ismi", "sizni ismingiz",
             "kak tebya zovut", "kak vas zovut", "tvoe imya", "vashe imya",
             "what is your name", "whats your name", "your name",
             "who are you", "are you a bot", "are you human", "what are you")

# NOTE the shape of these. "nima qila olasiz" is second person -- what can YOU
# do -- and that is what makes it separable from "nima qila oladi bu dori",
# which is third person about a medicine. The markers are the second-person
# forms only, and check_meta asserts the third-person one does not match.
_CAPABILITY = ("nima qila olasan", "nima qila olasiz", "nimalarni bilasan",
               "nimalarni bilasiz", "nima bilasan", "nima bilasiz",
               "qanday yordam bera olasan", "qanday yordam bera olasiz",
               "nima uchun kerak", "nima ish qilasan", "nima ish qilasiz",
               # Russian, folded: что ты умеешь -> chto ti umeesh
               "chto ti umeesh", "chto vi umeete", "chto ti mojesh",
               "chto vi mojete", "chem mojesh pomoch", "chem vi mojete pomoch",
               "chto ti znaesh",
               "what can you do", "what do you know", "how can you help")


def _hit(text: str, markers) -> str | None:
    """Word-start containment, the same rule triage and retrieval's exact tier
    use. Substring-anywhere would match "kimsan" inside unrelated words."""
    for marker in markers:
        if text.startswith(marker) or f" {marker}" in text:
            return marker
    return None


def classify(question: str) -> tuple[str, str | None] | None:
    """(category, detail) or None. Categories: identity, capability, language.

    ORDER MATTERS. "Kim bilan gaplashyapman va nima qila olasiz" is both; who
    you are is the better answer to what is really a greeting. Language last,
    because its markers are the loosest.
    """
    text = normalize(question)
    if not text:
        return None
    if _hit(text, _IDENTITY):
        return "identity", None
    if _hit(text, _CAPABILITY):
        return "capability", None
    for target, markers in _LANGUAGE_TARGETS.items():
        if _hit(text, markers):
            return "language", target
    if _hit(text, _LANGUAGE_GENERIC):
        return "language", None
    return None


# --- the replies ------------------------------------------------------------

_IDENTITY_REPLY = {
    "Russian": "Я ассистент {name}. Отвечаю на вопросы о {name}.",
    "Uzbek, in CYRILLIC script": "Мен {name} ёрдамчисиман. {name} ҳақидаги "
                                 "саволларга жавоб бераман.",
}
_IDENTITY_DEFAULT = ("Men {name} yordamchisiman. {name} haqidagi savollarga "
                     "javob beraman.")

# THE SAME ANSWER WITH A NAME IN IT. Separate tables rather than an optional
# clause inside one, for the reason the greeting has two: introducing a name
# changes the word order in both Uzbek and Russian, and a template carrying a
# conditional fragment reads fine until the day it does not.
#
# The agent name is a value in a sentence written here. It is never sent to a
# model -- this whole module exists so these questions are answered without one.
_NAMED_IDENTITY = {
    "Russian": "Меня зовут {agent}, я ассистент {name}. "
               "Отвечаю на вопросы о {name}.",
    "Uzbek, in CYRILLIC script": "Мени {agent} деб аташади, мен {name} "
                                 "ёрдамчисиман. {name} ҳақидаги саволларга "
                                 "жавоб бераман.",
}
_NAMED_IDENTITY_DEFAULT = ("Mening ismim {agent}, men {name} yordamchisiman. "
                           "{name} haqidagi savollarga javob beraman.")

# The confirmation is the slightly absurd part and it is also the truth: the
# agent already answers in whatever the customer writes in. Saying so is a
# better answer than doing nothing, which is what happened before.
_LANGUAGE_REPLY = {
    "Russian": "Да, конечно — пишите на любом языке, отвечу на нём же.",
    "Uzbek, in CYRILLIC script": "Ҳа, албатта — қайси тилда ёзсангиз, ўша "
                                 "тилда жавоб бераман.",
    "English": "Of course — write in any language and I'll reply in it.",
}
_LANGUAGE_DEFAULT = ("Ha, albatta — qaysi tilda yozsangiz, oʻsha tilda javob "
                     "beraman.")

_CAPABILITY_LEAD = {
    "Russian": "Я могу рассказать про {topics}. Спрашивайте.",
    "Uzbek, in CYRILLIC script": "{topics} ҳақида айта оламан. Сўраяверинг.",
}
_CAPABILITY_DEFAULT = "{topics} haqida ayta olaman. Soʻrayvering."

# When a business has nothing confirmed yet there is nothing honest to list.
_CAPABILITY_EMPTY = {
    "Russian": "Пока мне ещё ничего не рассказали. Скоро смогу ответить.",
    "Uzbek, in CYRILLIC script": "Ҳозирча менга ҳеч нарса ўргатилмаган.",
}
_CAPABILITY_EMPTY_DEFAULT = "Hozircha menga hech narsa oʻrgatilmagan."


def topics(conn: Connection, limit: int = 5) -> list[str]:
    """What this business can be asked about, in its own words.

    ATTRIBUTES, NOT SUBJECTS. "I can tell you about hours, prices, doctors'
    schedules" describes what you can ask FOR. A list of twenty-five subjects is
    a catalogue, and a customer has no way to know which of them is answerable.

    From retrievable_fact, so an expired promotion or the reserved payment
    subject never appears in a list of things the agent offers to discuss --
    the same reason console.suggestions() reads the view.

    Ordered by how many facts carry the attribute: the most-covered topic is the
    one most likely to answer the next question.
    """
    rows = conn.execute(
        "select attribute, count(*) from retrievable_fact"
        " where confirmed group by attribute"
        " order by count(*) desc, attribute limit %s", (limit,)).fetchall()
    return [r[0] for r in rows]


def reply(conn: Connection, category: str, detail: str | None,
          language: str, business_name: str | None) -> str:
    """The fixed reply for a category. Nothing here is generated by a model."""
    if category == "identity":
        name = business_name or "bu biznes"
        agent = style.current(conn)["agent_name"]
        if agent:
            return _NAMED_IDENTITY.get(
                language, _NAMED_IDENTITY_DEFAULT).format(agent=agent, name=name)
        table = _IDENTITY_REPLY.get(language, _IDENTITY_DEFAULT)
        return table.format(name=name)

    if category == "language":
        # `detail` is the language they ASKED FOR, which is not necessarily the
        # one they typed in -- see _LANGUAGE_TARGETS. Falling back to the typed
        # language is right only when they named no target.
        target = detail or language
        return _LANGUAGE_REPLY.get(target, _LANGUAGE_DEFAULT)

    found = topics(conn)
    if not found:
        return _CAPABILITY_EMPTY.get(language, _CAPABILITY_EMPTY_DEFAULT)
    # NO LIST OF THINGS IT CANNOT DO. Naming booking or payments here invites
    # the next question and goes stale the day either ships -- the same reason
    # the waiting screen does not invent an SLA.
    joined = ", ".join(found)
    return _CAPABILITY_LEAD.get(language, _CAPABILITY_DEFAULT).format(
        topics=joined)

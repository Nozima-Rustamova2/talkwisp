"""Recognise a message that reports a symptom, and refuse to sell into it.

Two live failures made this necessary, and neither was a retrieval failure:

    "Xotinimni qorni og'riyapdi."      -> abdominal UZI price + gynaecologist fee
    "Bolamni gorlosida shamollash bor." -> paediatrician + her fee

Retrieval was working. "qorin" genuinely matches "Qorin boʻshligʻi UZI", the
similarity was real, every guard passed and the status was `ok`. Nothing was
invented. A man said his wife was in pain and the bot quoted him a price list.

So this runs BEFORE retrieval and short-circuits it. That ordering is the whole
point: if the check happened afterwards, the facts would already be in the
prompt and we would be relying on the model to decline to use them. Here the
model is never given them at all -- the same structural argument as the
NO_ANSWER short-circuit in answer(), and for the same reason.

WHAT THIS DOES NOT DO, PERMANENTLY: map a symptom to a specialty. Not "chest
pain -> cardiologist", not "sore throat -> ENT", not ever. That inference is
medical advice regardless of how it is worded, and it is the line between being
unhelpful and being liable. The clinic's own triage guidance can be stored as
facts and retrieved like anything else; it is not ours to derive.
"""

from app.normalize import normalize

# Verified 2026-09-01, not recalled: 103 is the ambulance line and remains in
# operation; 112 is the unified emergency dispatch, live across all regions of
# Uzbekistan since March 2025. Both are given because 103 is the number people
# know and 112 is the one that answers everywhere.
#   https://www.gazeta.uz/en/2025/03/26/112/
#   https://tashkent.uz/en/news/tashkent-listening/emergency-phone-numbers
#
# These are HARDCODED, and that is a deliberate exception to "never state what
# was not retrieved". They are public civil-infrastructure numbers, verified
# against sources, and constant. A wrong emergency number is worse than none,
# so they must never be produced by a model, interpolated, or reformatted.
# Re-verify before changing them. See docs/design-decisions.md.
AMBULANCE = "103"
EMERGENCY = "112"

# Markers are matched at WORD START, not as bare substrings. Uzbek is
# agglutinative: "og'ri" has to catch og'riyapti, og'riyapdi, og'riq, og'riydi
# without a suffix list. Same technique as retrieval's exact tier, and the same
# reason the language detector uses whole words -- see the "ва" inside "вас"
# case in docs/design-decisions.md.
#
# All markers are pre-normalised: apostrophes deleted, Cyrillic folded to Latin,
# digraphs collapsed. Compare against normalize(question), never raw text.

# Acute: leads with the emergency number.
_ACUTE = (
    # Uzbek
    "nafas",        # breathing -- "nafas olishga", "nafasi qisyapti"
    "bugil", "bogil",   # choking / suffocating
    "hushidan", "hushsiz", "hushini",   # unconscious
    "talvasa", "tirishib",              # seizure, convulsions
    "zaharlan", "zahrlan",              # poisoning
    "qon ket", "qonayotgan", "qonash",  # bleeding
    "kuyib", "kuydi", "kuyish",         # burns
    "sindi", "sinib",                   # fracture
    "yiqilib", "yiqildi",               # a fall
    "tez yordam",                       # they are already asking for an ambulance
    "olim", "jon berayapti",            # dying
    # Russian (normalize() folds Cyrillic to Latin, so these are the folded forms)
    "dishat", "dixat", "zadixa", "zadisha",   # breathing / suffocating
    "krovotech", "krov idet",                 # bleeding
    "bez soznaniya", "obmorok",               # unconscious
    "sudorog", "otravlen", "perelom", "ojog",
    "skoraya", "skoruyu",
)

# General: still a symptom, still must not be answered with a price.
_SYMPTOM = (
    # Uzbek
    "ogri",         # pain, all inflections
    "isitma",       # fever
    "harorat",      # temperature
    "shamolla",     # cold / chill
    "yotal", "yutal",   # cough
    "qusy", "qusi", "qusdi",    # vomiting
    "ich ket",      # diarrhoea
    "bosh aylan",   # dizziness
    "kongl", "kungl",   # nausea ("koʻnglim aynidi")
    "kasal", "betob", "bemor",  # ill
    "shish", "yara", "toshma",  # swelling, wound, rash
    "charcho",      # exhaustion
    # Russian, folded
    "bolit", "bolno", "bol v",
    "temperatur", "kashel", "rvota", "toshnit",
    "prostud", "ponos", "golovokru", "sip",
    "bolen", "zabolel",
)

# Fixed replies. NOT generated: a model that paraphrases an emergency number is
# a model that can get it wrong, and there is no context in which that is an
# acceptable risk. Keyed by what detect_language() returns.
_ACUTE_REPLY = {
    "Uzbek, in CYRILLIC script":
        f"Бу шошилинч ҳолат бўлиши мумкин. Дарҳол {AMBULANCE} ёки "
        f"{EMERGENCY} рақамига қўнғироқ қилинг.",
    "Russian":
        f"Это может быть неотложное состояние. Немедленно позвоните "
        f"по номеру {AMBULANCE} или {EMERGENCY}.",
    "the same language the customer wrote in, in LATIN script":
        f"Bu shoshilinch holat bo'lishi mumkin. Darhol {AMBULANCE} yoki "
        f"{EMERGENCY} raqamiga qo'ng'iroq qiling.",
}

_SYMPTOM_REPLY = {
    "Uzbek, in CYRILLIC script":
        "Кечирасиз, биз тиббий маслаҳат бера олмаймиз. {phone}Ҳолат оғир "
        f"бўлса, {AMBULANCE} ёки {EMERGENCY} рақамига қўнғироқ қилинг.",
    "Russian":
        "Извините, мы не можем давать медицинские консультации. {phone}"
        f"Если состояние тяжёлое, позвоните по номеру {AMBULANCE} или {EMERGENCY}.",
    "the same language the customer wrote in, in LATIN script":
        "Kechirasiz, biz tibbiy maslahat bera olmaymiz. {phone}Holat og'ir "
        f"bo'lsa, {AMBULANCE} yoki {EMERGENCY} raqamiga qo'ng'iroq qiling.",
}

_PHONE_LEAD = {
    "Uzbek, in CYRILLIC script": "Илтимос, {n} рақамига қўнғироқ қилинг. ",
    "Russian": "Пожалуйста, позвоните нам по номеру {n}. ",
    "the same language the customer wrote in, in LATIN script":
        "Iltimos, {n} raqamiga qo'ng'iroq qiling. ",
}


def _hit(text: str, markers: tuple) -> str | None:
    """Word-start containment, the same rule retrieval's exact tier uses."""
    for marker in markers:
        if text.startswith(marker) or f" {marker}" in text:
            return marker
    return None


def triage(question: str) -> tuple[str, str] | None:
    """(tier, marker) where tier is "acute" or "symptom", or None.

    Acute is checked first and wins, per the instruction to err toward routing:
    showing an emergency number to someone with a mild complaint costs almost
    nothing, and the reverse mistake is the one that matters.
    """
    text = normalize(question)
    if not text:
        return None
    marker = _hit(text, _ACUTE)
    if marker:
        return "acute", marker
    marker = _hit(text, _SYMPTOM)
    if marker:
        return "symptom", marker
    return None


def reply(tier: str, language: str, phone: str | None) -> str:
    """The fixed reply. `phone` is the clinic's own number, RETRIEVED -- it is
    omitted entirely rather than guessed if the fact table does not hold one."""
    if tier == "acute":
        # No clinic number here on purpose. Someone who cannot breathe should
        # be dialling an ambulance, not a reception desk.
        return _ACUTE_REPLY[language]
    lead = _PHONE_LEAD[language].format(n=phone) if phone else ""
    return _SYMPTOM_REPLY[language].format(phone=lead)

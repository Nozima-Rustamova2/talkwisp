"""Turn any spelling of a name into one comparison key.

Called in exactly two places: when writing the *_key columns, and on every
incoming query. If those two ever diverge, lookups fail silently.
"""

import re
import unicodedata

# Every character people use as an apostrophe in Uzbek: the correct oʻ/gʻ mark
# (U+02BB), the tutuq belgisi (U+02BC), plain ASCII, both curly quotes, and the
# accents people reach for when the right key isn't on the keyboard.
#
# These are DELETED, not replaced with one canonical mark. Plenty of people type
# "Kokrak" with no mark at all, so deleting is the only rule that puts all three
# spellings on the same key.
APOSTROPHES = "'ʻʼ‘’‛`´ʹˊˋ"

# Uzbek Cyrillic -> Uzbek Latin. Russian-only letters (щ ы ь э) are included so
# a Russian name reaches the same key as its Latin spelling.
#
# This table is hand-written and UNVERIFIED by a native speaker. Tahrirchi /
# Tilmoch have a production transliterator; see the Leads section of
# docs/design-decisions.md. If we get their mapping, generate this table from it
# offline -- never call an API from here.
CYRILLIC = {
    "а": "a",  "б": "b",  "в": "v",  "г": "g",  "ғ": "g",  "д": "d",
    "е": "e",  "ё": "yo", "ж": "j",  "з": "z",  "и": "i",  "й": "y",
    "к": "k",  "қ": "q",  "л": "l",  "м": "m",  "н": "n",  "о": "o",
    "п": "p",  "р": "r",  "с": "s",  "т": "t",  "у": "u",  "ў": "o",
    "ф": "f",  "х": "x",  "ҳ": "h",  "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "sh", "ъ": "",   "ы": "i",  "ь": "",   "э": "e",  "ю": "yu",
    "я": "ya",
}

# Applied to the whole string after transliteration, so Cyrillic and Latin input
# get the same treatment. Each one exists for a spelling people actually use:
#   shch -> sh   Russian-style Щ ("Shchukin" = "Шукин")
#   zh   -> j    Russian-style Ж ("Zhukov" = Uzbek "Jukov")
#   kh   -> x    passport-style Х ("Khudoyberdiev" = "Xudoyberdiyev")
#   ye   -> e    the -ев / -yev / -ev surname ending, which is everywhere here
#
# yo, yu and ya are deliberately NOT folded the same way: "yosh" (young) and
# "osh" (food) are different words, and collapsing them would merge them.
DIGRAPHS = [("shch", "sh"), ("zh", "j"), ("kh", "x"), ("ye", "e")]

_NOT_KEY = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Fold one string down to its comparison key."""
    if not text:
        return ""

    # Compose first, so й and ё are single characters the table can look up
    # rather than a base letter plus a combining mark.
    text = unicodedata.normalize("NFC", text).lower()

    text = text.translate({ord(c): None for c in APOSTROPHES})
    text = "".join(CYRILLIC.get(c, c) for c in text)

    # Now strip accents off whatever Latin is left (é -> e, ā -> a). Done after
    # transliteration, or the breve on й would be stripped and й would become i.
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(c)
    )

    for a, b in DIGRAPHS:
        text = text.replace(a, b)

    # Everything that isn't a letter or digit becomes a space, then collapse.
    return _NOT_KEY.sub(" ", text).strip()

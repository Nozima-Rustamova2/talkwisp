"""Print the normalization table for a human to read. Not a test suite."""

import sys

from app.normalize import normalize

# The Windows console defaults to a codepage that can't print the Uzbek
# apostrophe or Cyrillic; without this the script dies on its own output.
sys.stdout.reconfigure(encoding="utf-8")

# Every spelling in a group must produce the same key.
SAME = [
    ("Uzbek apostrophe, all the ways it gets typed",
     ["Koʻkrak", "Ko'krak", "Ko‘krak", "Ko’krak", "Kokrak", "Кўкрак"]),
    ("gʻ likewise",
     ["Gʻoyibov", "G'oyibov", "Goyibov", "Ғоyибов", "Ғойибов"]),
    ("Cyrillic and Latin, same doctor",
     ["Rasulova", "Расулова", "  RASULOVA  ", "Rasulova."]),
    ("q / x / h are distinct letters and must survive",
     ["Qodirov", "Қодиров"]),
    ("х -> x, and passport-style kh reaches the same key",
     ["Xudoyberdiyev", "Худойбердиев", "Khudoyberdiev"]),
    ("ҳ -> h, not x",
     ["Halima", "Ҳалима"]),
    ("the -ev / -yev / -ев ending",
     ["Aliyev", "Aliev", "Алиев"]),
    ("ж -> j, and Russian-style zh",
     ["Jukov", "Жуков", "Zhukov"]),
    ("Russian name, transliterated",
     ["Vladimir", "Владимир"]),
    ("punctuation and spacing are noise",
     ["Dr. Rasulova", "dr rasulova", "Dr.  Rasulova!"]),
    ("attributes too, not just names",
     ["Ish vaqti", "ИШ ВАҚТИ", "ish  vaqti"]),
    ("digits stay (addresses, phones)",
     ["12-kvartal", "12 kvartal"]),
]

# These must NOT collide. This is the half that catches over-folding.
DIFFERENT = [
    ("Qodirov", "Xodirov", "q and x are different letters"),
    ("Halima", "Xalima", "h and x are different letters"),
    ("yosh", "osh", "young vs food — why yo is not folded"),
    ("Karimov", "Karimova", "endings carry meaning"),
    ("shifo", "shifa", "vowels are not folded"),
]

print("=" * 74)
print("MUST MATCH".center(74))
print("=" * 74)
for title, group in SAME:
    keys = {normalize(s) for s in group}
    ok = "OK  " if len(keys) == 1 else "FAIL"
    print(f"\n[{ok}] {title}")
    for s in group:
        print(f"       {s!r:28} -> {normalize(s)!r}")

print()
print("=" * 74)
print("MUST NOT MATCH".center(74))
print("=" * 74)
for a, b, why in DIFFERENT:
    ok = "OK  " if normalize(a) != normalize(b) else "FAIL"
    print(f"[{ok}] {normalize(a)!r:20} vs {normalize(b)!r:20}  {why}")

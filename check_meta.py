"""Questions about the agent: what gets one, and what must not.

    uv run python check_meta.py

THE SECTION THAT MATTERS IS 2, THE FALSE POSITIVES. Answering "who are you" is
easy and the whole feature is worth nothing if it also answers "what can this
medicine do" with a capability blurb. A classifier that fires too eagerly turns
a refusal -- which is honest -- into a confident irrelevance, and the customer
has no way to tell the difference.

The position in the pipeline is what makes the cost survivable: meta runs AFTER
retrieval has found nothing, so a false positive replaces a refusal rather than
an answer. Section 4 proves that ordering rather than trusting it.
"""

import os
import sys

import psycopg
from dotenv import load_dotenv

from app import meta
from app.answer import answer
from app.db import connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0


def check(label: str, got, want) -> None:
    global passed, failed
    ok = got == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")
        failed += 1
    else:
        passed += 1


def category(q):
    hit = meta.classify(q)
    return hit[0] if hit else None


def main() -> None:
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set.")
    admin = psycopg.connect(admin_url, autocommit=True)
    pool.open()
    biz = str(admin.execute("select id from business order by created_at"
                            " limit 1").fetchone()[0])
    try:
        run(admin, biz)
    finally:
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def run(admin, biz: str) -> None:
    print("\n1. The three categories are recognised, in three languages")
    for q in ("kimsan", "Siz kimsiz?", "кто ты", "who are you",
              "sen botmisan?"):
        check(f"identity: {q!r}", category(q), "identity")
    for q in ("nima qila olasiz", "что ты умеешь", "what can you do",
              "nimalarni bilasiz?"):
        check(f"capability: {q!r}", category(q), "capability")
    for q in ("menga rus tilida gapir", "по-русски пожалуйста",
              "can you speak english", "tilni o'zgartir"):
        check(f"language: {q!r}", category(q), "language")

    print("\n2. FALSE POSITIVES: real questions must fall through")
    # The centrepiece. Third person about a medicine, not second person about
    # the agent -- and the words overlap almost entirely.
    for q in ("nima qila oladi bu dori",
              "bu dori nima qila oladi",
              "что может этот препарат",
              "kim shifokor bo'lib ishlaydi",
              "kardiolog kim",
              "MRT nima",
              "qaysi shifokor bolalarni ko'radi",
              "ish vaqtingiz qanday",
              "narxi qancha",
              "rus tilida hujjat kerakmi"):
        check(f"falls through: {q!r}", category(q), None)

    print("\n3. The language asked FOR wins over the language typed IN")
    # "menga rus tilida gapir" is Latin Uzbek asking for Russian. Replying in
    # Uzbek because that is what they typed would answer the request by
    # declining it.
    hit = meta.classify("menga rus tilida gapir")
    check("the target language is carried", hit[1], "Russian")
    with connection(biz) as conn:
        said = meta.reply(conn, "language", hit[1],
                          "the same language the customer wrote in, in LATIN script",
                          "Avisena Med")
    check("and the reply is in Russian, not Uzbek",
          said.startswith("Да"), True)

    generic = meta.classify("tilni o'zgartir")
    check("a request naming no language carries no target", generic[1], None)

    print("\n4. CONTROL: meta runs AFTER retrieval, not before")
    # If it ran first, an answerable question containing a marker would be
    # hijacked. This proves the ordering with a real end-to-end call.
    with connection(biz) as conn:
        answerable = answer(conn, "Ish vaqtingiz qanday?")
        check("a real question is still answered from facts",
              (answerable.get("source") or "").startswith("meta-"), False)
        check("and it actually answered", answerable["status"], "ok")

        asked_me = answer(conn, "Siz kimsiz?")
        check("a meta question is answered by meta",
              asked_me.get("source"), "meta-identity")
        check("with an answer", bool(asked_me["answer"]), True)
        # DERIVED, not hardcoded. The first version asserted "Avisena" and
        # failed on this laptop, where the business is still called "Default
        # business" -- the rename happened on the box. A check that names the
        # data it expects is a check about one database.
        expected = admin.execute("select name from business where id = %s",
                                 (biz,)).fetchone()[0]
        check(f"naming the business ({expected!r})",
              expected in (asked_me["answer"] or ""), True)

        # Real Cyrillic, not the folded form: a customer types "кто ты", and
        # the reply should come back in Russian because detect_language sees
        # the script they actually used.
        in_russian = answer(conn, "кто ты")
        check("a Cyrillic identity question is answered in Russian",
              (in_russian["answer"] or "").startswith("Я"), True)

    print("\n5. Capability lists ATTRIBUTES, from the view")
    with connection(biz) as conn:
        found = meta.topics(conn)
        check("it returns some", len(found) > 0, True)
        # Subjects are a catalogue; attributes are what you can ask FOR.
        check("they are attributes, not subjects -- no doctor names",
              any("Rahimov" in t or "Kardiolog" == t for t in found), False)
        # The reserved payment subject is excluded from the view, so its
        # attributes must never be offered as things to ask about.
        check("and nothing from the payment subject",
              any("karta" in t.lower() for t in found), False)

        said = meta.reply(conn, "capability", None,
                          "the same language the customer wrote in, in LATIN script",
                          "Avisena Med")
        check("the reply names them", found[0] in said, True)
        # Listing what it cannot do invites the next question and goes stale
        # the day booking ships.
        check("and promises nothing it cannot do",
              any(w in said.lower() for w in ("book", "zapis", "to'lov",
                                              "payment")), False)


if __name__ == "__main__":
    main()

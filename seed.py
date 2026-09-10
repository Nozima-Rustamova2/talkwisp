"""Wipe the knowledge tables and reload the demo clinic. Re-runnable.

    uv run python seed.py

Truncate-then-insert, all in one transaction: a partial seed cannot exist, and
running it twice leaves the same database as running it once. schema_migrations
is untouched -- this reloads data, it does not undo migrations.

The clinic is Avisena Med (replaced Shifo Med on 2026-09-02). Facts are DERIVED
from data/avisena.json rather than transcribed here, so the seed and the source
cannot drift apart and any row can be traced back to the line that produced it.
The `user_intents_mixed_language_samples` block in the original data is
deliberately NOT loaded: those are test expectations, and they belong in
questions.py, not in the fact table.

PRICES. The source gives exact figures (180000). The standing rule is that costs
are approximate ranges, never exact prices -- but inventing a spread around a
number the clinic actually stated would be fabricating data, which is worse than
the problem it solves. So the figure is stored exactly as given, and rule 4 in
the answering prompt still requires it to be PRESENTED as approximate
("180 000 so'm atrofida"). Storing precisely and presenting cautiously is the
only combination that is honest in both directions. Flagged in
docs/design-decisions.md; if that is the wrong call, the fix is in this file.
"""

import json
import pathlib
import sys

from app.db import HARNESS_BUSINESS, business_by_name, connection, pool
from app.embeddings import DIMENSIONS, MODEL, embed_document
from app.normalize import normalize
from app import payment
from app.payment import PAYMENT_SUBJECT, PAYMENT_SUBJECT_KEY
from app.triage import check_subject

sys.stdout.reconfigure(encoding="utf-8")

DATA = json.loads(
    (pathlib.Path(__file__).parent / "data" / "avisena.json").read_text(encoding="utf-8")
)
BLOCKS = {block["category"]: block for block in DATA}

CLINIC = "Avisena Med"


def money(amount: int) -> str:
    """180000 -> '180 000 soʻm'. Uzbek uses a space as the thousands separator.

    ONE definition of how money looks, in app/payment.py, because the seed
    writes the prices and payment.order_message() writes the amount a customer
    is asked to send. Two formatters is two ways to render the same sum, and
    they had already drifted by one invisible character before this was noticed.
    """
    return payment.som(amount)


def _parenthetical(text: str) -> tuple[str, list[str]]:
    """'Oftalmolog (Glaznoy / Ko'z shifokori)' -> the head, and the variants.

    The source packs colloquial names into brackets, and those are exactly the
    words customers type -- "glaznoy" and "okulist" are how people ask for an
    ophthalmologist here. Splitting them out turns them into aliases instead of
    leaving them buried inside a subject nobody would type.
    """
    head, _, rest = text.partition("(")
    head = head.strip()
    variants = [v.strip() for v in rest.rstrip(")").split("/") if v.strip()]
    return head, variants


TYPED: list[tuple[str, str, str]] = []
ALIASES: list[tuple[str, str]] = []

# --- the clinic itself ------------------------------------------------------
info = BLOCKS["clinic_info"]
hours = info["working_hours"]
TYPED += [
    (CLINIC, "manzil", info["address_uz"]),
    (CLINIC, "mo'ljal", info["landmark"]),
    # ONE working-hours fact, not three. A split attribute makes conflict
    # detection blind (see docs/design-decisions.md), and every day the customer
    # might name appears in this single value, so retrieval still reaches it.
    (CLINIC, "ish vaqti",
     f"Dushanba-Juma {hours['weekdays']}, Shanba {hours['saturday']}, "
     f"Yakshanba {hours['sunday']}"),
    (CLINIC, "telefon", info["contacts"]["call_center"]),
    (CLINIC, "qabulxona telefoni", info["contacts"]["reception_mobile"]),
    (CLINIC, "shoshilinch yordam telefoni", info["contacts"]["emergency_24_7"]),
    (CLINIC, "telegram bot", info["contacts"]["telegram_bot"]),
    (CLINIC, "to'lov usullari", ", ".join(info["payment_methods"])),
]
ALIASES += [
    (CLINIC, info["clinic_name"]),
    (CLINIC, "Avisena"),
    (CLINIC, "Ависена"),
    (CLINIC, "Ависена Мед"),
    (CLINIC, "klinika"),
    (CLINIC, "клиника"),
]

# --- one synthetic doctor, added deliberately --------------------------------
# NOT in data/avisena.json, which stays faithful to what was supplied. All 12
# real surnames are unique, so the ambiguity behaviour -- two people matching
# one name, ask which rather than guess -- had nothing left to test. A second
# Karimov restores it. The previous data set had two Rasulovas for exactly this
# reason; losing the case silently when the clinic changed is how a behaviour
# stops being covered without anyone deciding to stop covering it.
_SYNTHETIC = {
    "specialty_uz": "Fizioterapevt",
    "specialty_ru": "Физиотерапевт",
    "full_name": "Karimov Jasur Anvarovich",
    "degree": "9 yillik staj",
    "room": "107-xona (1-qavat)",
    "schedule": "Dush-Juma 09:00 - 15:00",
    "consultation_price_uzs": 120000,
    "follow_up_price_uzs": 60000,
    "notes": "Massaj, elektroforez va reabilitatsiya muolajalari.",
}
BLOCKS["doctors"]["list"].append(_SYNTHETIC)

# --- doctors ----------------------------------------------------------------
for doc in BLOCKS["doctors"]["list"]:
    name = doc["full_name"]
    specialty, variants = _parenthetical(doc["specialty_uz"])
    TYPED += [
        (name, "lavozim", doc["specialty_uz"]),
        (name, "xona", doc["room"]),
        (name, "qabul vaqti", doc["schedule"]),
        (name, "qabul narxi", money(doc["consultation_price_uzs"])),
        (name, "takroriy qabul narxi", money(doc["follow_up_price_uzs"])),
        (name, "daraja", doc["degree"]),
        # The clinic's OWN description of what this doctor handles. Stored as a
        # stated fact, not as routing advice -- see the note in
        # docs/design-decisions.md about retrieved versus derived.
        (name, "izoh", doc["notes"]),
    ]
    surname = name.split()[0]
    given = name.split()[1] if len(name.split()) > 1 else ""
    ALIASES += [
        (name, specialty),
        (name, doc["specialty_ru"]),
        (name, surname),
        (name, f"Dr. {surname}"),
    ]
    if given:
        ALIASES.append((name, f"{given} {surname}"))
    ALIASES += [(name, v) for v in variants]
    ru_head, ru_variants = _parenthetical(doc["specialty_ru"])
    ALIASES += [(name, ru_head)] + [(name, v) for v in ru_variants]

# --- diagnostics and lab ----------------------------------------------------
for svc in BLOCKS["diagnostics_and_lab"]["services"]:
    subject, variants = _parenthetical(svc["name_uz"])
    # "MDT / MRT bosh miya" -- a subject may never contain the separator, which
    # is what facts are embedded with ("subject / attribute / value") and what
    # grading splits `want` on. Keep the head, keep the rest as an alias.
    if " / " in subject:
        head, _, tail = subject.partition(" / ")
        variants.append(head)
        subject = tail.strip()
    TYPED.append((subject, "narx", money(svc["price_uzs"])))
    if svc.get("turnaround_time"):
        TYPED.append((subject, "tayyor boʻlish muddati", svc["turnaround_time"]))
    if svc.get("preparation_uz"):
        TYPED.append((subject, "tayyorgarlik", svc["preparation_uz"]))
    if svc.get("room"):
        TYPED.append((subject, "xona", svc["room"]))
    ALIASES.append((subject, svc["name_uz"]))
    ALIASES.append((subject, svc["name_ru"]))
    ru_head, ru_variants = _parenthetical(svc["name_ru"])
    ALIASES += [(subject, ru_head)] + [(subject, v) for v in ru_variants]
    ALIASES += [(subject, v) for v in variants]

# Drop aliases equal to their own subject, and any duplicate. Deduped on the
# NORMALIZED pair, not the raw one: "Avisena" and "Ависена" are different
# strings that normalize to the same key, and the alias table's unique
# constraint is on (alias_key, subject_key). Deduping on raw text passed the
# dry run and failed at insert.
_seen: set[tuple[str, str]] = set()
_deduped = []
for _subject, _alias in ALIASES:
    if not _alias:
        continue
    _key = (normalize(_subject), normalize(_alias))
    if _key[0] == _key[1] or _key in _seen:
        continue
    _seen.add(_key)
    _deduped.append((_subject, _alias))
ALIASES = _deduped

# An alias may never BE a body part or a symptom word. answer() lets a symptom
# report reach the facts when the customer named a subject, on the reasoning
# that the customer chose the topic. That collapses if a body part is itself an
# alias: "qorin" pointing at the abdominal ultrasound would make "qornim
# ogriyapti" match a SUBJECT, and the bot would quote a price to someone
# reporting pain -- the exact failure triage exists to prevent, wearing a green
# badge. Compound aliases are fine and unaffected: "koz shifokori" is not "koz".
# --- Facts a file produced: unconfirmed, with confidence, awaiting review ----
# Kept small and deliberately in tension with the typed rows above, so the
# review queue and conflict detection have something real to show.
EXTRACTED = [
    # Contradicts the typed opening hours. Left in on purpose.
    (CLINIC, "ish vaqti", "Dushanba-Shanba, 08:00-19:00", 0.71),
    # The MRT is at a partner clinic, so its price is the least certain thing
    # in the source.
    ("MRT bosh miya", "narx", "450 000-500 000 soʻm", 0.68),
]

# A subject containing " / " would be indistinguishable from the
# subject/attribute/value form it is embedded as, and would break grading's
# split on `want`. Fail the seed rather than load one. Checks EXTRACTED too:
# the first version of this guard only covered TYPED and would have missed
# the one row that actually had the problem.
# No subject or alias may BE a body part or symptom word -- see check_subject().
# Placed here, below EXTRACTED, so it covers every row that will be written.
for _subject, _alias in ALIASES:
    check_subject(_alias)
for _s in [r[0] for r in TYPED] + [r[0] for r in EXTRACTED]:
    check_subject(_s)

for _s in [r[0] for r in TYPED] + [r[0] for r in EXTRACTED]:
    # Kept even though migrations/0005 now enforces this with a check
    # constraint on every write path. This fails at import with the offending
    # subject named, before a single row or embedding is written; the
    # constraint is the floor for the three doors this list cannot see.
    assert " / " not in _s, f"subject contains the separator: {_s!r}"

# --- Payment details: the one subject that is never retrieved ---------------
#
# THE CARD NUMBER IS DELIBERATELY INVALID. 8600 is a real Uzcard BIN, so a
# plausible-looking test number could be mistaken for a live one by anyone
# reading this file or a test failure. All-zeros cannot be, and it fails a Luhn
# check. Replaced by the owner's real details through the console; never
# committed.
#
# These rows are excluded from retrieval by the view `retrievable_fact` and
# cannot carry an embedding. They are readable only by app/payment.py, which
# joins them with newlines and never shows them to a model.
PAYMENT = [
    ("Karta raqami", "8600 0000 0000 0000"),
    ("Karta egasi", "AVISENA MED"),
    ("Bank", "Kapitalbank"),
    # The owner's own copy, per language, editable like any other fact. The
    # card block above is identical in all three; only this line changes,
    # because language detection is very good and not perfect, and a payment
    # instruction is the wrong place to find that out.
    ("Toʻlov koʻrsatmasi lotin", "Toʻlovni quyidagi kartaga amalga oshiring:"),
    ("Toʻlov koʻrsatmasi kirill", "Тўловни қуйидаги картага амалга оширинг:"),
    ("Toʻlov koʻrsatmasi rus", "Оплату можно произвести на следующую карту:"),
    # The exact-amount line. This is the ONLY defence against a customer
    # rounding 250 003 back to 250 000, which silently breaks the matching the
    # whole feature rests on. Prominence, not a guarantee -- see item 3 on the
    # reliability list in docs/design-decisions.md.
    ("Aniq summa ogohlantirishi lotin",
     "Iltimos, summani ANIQ shu koʻrinishda yuboring — "
     "toʻlovingizni shu raqam boʻyicha topamiz."),
    ("Aniq summa ogohlantirishi kirill",
     "Илтимос, суммани АНИҚ шу кўринишда юборинг — "
     "тўловингизни шу рақам бўйича топамиз."),
    ("Aniq summa ogohlantirishi rus",
     "Пожалуйста, переведите ТОЧНО эту сумму — "
     "по ней мы найдём ваш заказ."),
]

FILE_SOURCE = (
    "file",
    "Narxlar roʻyxati, sentabr",
    "narxlar-sentabr.jpg",
    "image/jpeg",
    "Ish vaqti: Dushanba-Shanba, 08:00-19:00\n"
    "MRT bosh miya - 450 000-500 000 soʻm",
)


def main() -> None:
    # THE ONLY SCRIPT THAT NAMES AN ARBITRARY TENANT, and the only one that
    # creates a business. Everything else resolves a name that must already
    # exist and refuses otherwise -- so "not found" stays a real error rather
    # than a case that quietly invents a row.
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--business", default=HARNESS_BUSINESS,
                    help="name of the business to seed into; created if absent")
    args = ap.parse_args()

    with pool:
        target = business_by_name(args.business)
        if target is None:
            with pool.connection() as conn:
                target = str(conn.execute(
                    "insert into business (name) values (%s) returning id",
                    (args.business,)).fetchone()[0])
            print(f"created business {args.business!r}")
        else:
            print(f"seeding into existing business {args.business!r}")
        with connection(target) as conn:
            # DELETE, NEVER TRUNCATE, and this is a tenancy decision rather
            # than a style one.
            #
            # TRUNCATE is not subject to row-level security -- Postgres says so
            # explicitly -- so on a tenanted connection it empties the whole
            # table, every business's rows, not the one this connection is bound
            # to. On a single-tenant database those are the same thing, which is
            # why this went unnoticed; on the second business it is a
            # one-statement wipe of somebody else's knowledge base, with the
            # policy silently not applying.
            #
            # The app role is deliberately not granted TRUNCATE (0007 grants
            # only select/insert/update/delete), so this failed loudly the first
            # time seed.py was run after tenancy landed -- three days later, on
            # the box, because nothing had re-run it in between. Granting the
            # privilege would have "fixed" it by removing the protection.
            #
            # DELETE is filtered by the policy. Order matters because there is
            # no CASCADE: fact and alias reference source with ON DELETE
            # RESTRICT, chunk with CASCADE. `restart identity` is gone with the
            # TRUNCATE and is not missed -- every id is a uuidv7() default and
            # there are no sequences.
            #
            # Still one transaction, so the tables are never half-loaded.
            conn.execute("delete from fact")
            conn.execute("delete from alias")
            conn.execute("delete from chunk")
            conn.execute("delete from source")

            file_source = conn.execute(
                "insert into source (kind, label, filename, media_type, content,"
                " status, extracted_at)"
                " values (%s, %s, %s, %s, %s, 'extracted', now()) returning id",
                FILE_SOURCE,
            ).fetchone()[0]

            for subject, attribute, value in TYPED:
                conn.execute(
                    "insert into fact (subject, subject_key, attribute, attribute_key,"
                    " value, value_key, confirmed) values (%s, %s, %s, %s, %s, %s, true)",
                    (subject, normalize(subject), attribute, normalize(attribute),
                     value, normalize(value)),
                )

            for subject, attribute, value, confidence in EXTRACTED:
                conn.execute(
                    "insert into fact (subject, subject_key, attribute, attribute_key,"
                    " value, value_key, confidence, source_id, confirmed)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s, false)",
                    (subject, normalize(subject), attribute, normalize(attribute),
                     value, normalize(value), confidence, file_source),
                )

            for attribute, value in PAYMENT:
                conn.execute(
                    "insert into fact (subject, subject_key, attribute, attribute_key,"
                    " value, value_key, confirmed) values (%s, %s, %s, %s, %s, %s, true)",
                    (PAYMENT_SUBJECT, PAYMENT_SUBJECT_KEY,
                     attribute, normalize(attribute), value, normalize(value)),
                )

            for subject, alias in ALIASES:
                conn.execute(
                    "insert into alias (subject_key, alias, alias_key, confirmed)"
                    " values (%s, %s, %s, true)",
                    (normalize(subject), alias, normalize(alias)),
                )

            # Embed every fact as "subject / attribute / value" -- the value on
            # its own gives no clue what it is, so a question about opening
            # hours would never reach "Dushanba-Juma 08:00 - 20:00".
            # `retrievable_fact`, so the payment rows are skipped. Not an
            # optimisation: the constraint fact_payment_not_embedded would
            # abort the whole seed on the first one. Reading the view means
            # this loop cannot embed something that must not be embedded, and
            # it does not need to know why.
            facts = conn.execute(
                "select id, subject, attribute, value from retrievable_fact"
            ).fetchall()
            print(f"  embedding {len(facts)} facts...")
            for n, (fact_id, subject, attribute, value) in enumerate(facts, 1):
                if n % 25 == 0:
                    print(f"    {n}/{len(facts)}")
                vector = embed_document(f"{subject} / {attribute} / {value}")
                conn.execute(
                    "update fact set embedding = %s, embedding_model = %s"
                    " where id = %s",
                    (str(vector), f"{MODEL}@{DIMENSIONS}", fact_id),
                )

            counts = conn.execute(
                "select (select count(*) from source), (select count(*) from fact),"
                " (select count(*) from fact where not confirmed),"
                " (select count(*) from alias), (select count(*) from chunk)"
            ).fetchone()

    print(f"sources {counts[0]}, facts {counts[1]} ({counts[2]} awaiting review), "
          f"aliases {counts[3]}, chunks {counts[4]}")
    # Stated rather than left to be discovered: the source has no prose, so the
    # chunk table is empty and every `expect="prose"` question will now fail.
    if counts[4] == 0:
        print("NOTE: no prose in the source, so chunk is empty. The prose "
              "questions in questions.py have nothing to retrieve.")


if __name__ == "__main__":
    main()

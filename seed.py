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

from app.db import pool
from app.embeddings import DIMENSIONS, MODEL, embed_document
from app.normalize import normalize
from app.triage import check_subject

sys.stdout.reconfigure(encoding="utf-8")

DATA = json.loads(
    (pathlib.Path(__file__).parent / "data" / "avisena.json").read_text(encoding="utf-8")
)
BLOCKS = {block["category"]: block for block in DATA}

CLINIC = "Avisena Med"


def money(amount: int) -> str:
    """180000 -> '180 000 soʻm'. Uzbek uses a space as the thousands separator."""
    return f"{amount:,}".replace(",", " ") + " soʻm"


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
    assert " / " not in _s, f"subject contains the separator: {_s!r}"

FILE_SOURCE = (
    "file",
    "Narxlar roʻyxati, sentabr",
    "narxlar-sentabr.jpg",
    "image/jpeg",
    "Ish vaqti: Dushanba-Shanba, 08:00-19:00\n"
    "MRT bosh miya - 450 000-500 000 soʻm",
)


def main() -> None:
    with pool:
        with pool.connection() as conn:
            # One statement, one transaction: the tables are never half-loaded.
            conn.execute("truncate fact, alias, chunk, source restart identity cascade")

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

            for subject, alias in ALIASES:
                conn.execute(
                    "insert into alias (subject_key, alias, alias_key, confirmed)"
                    " values (%s, %s, %s, true)",
                    (normalize(subject), alias, normalize(alias)),
                )

            # Embed every fact as "subject / attribute / value" -- the value on
            # its own gives no clue what it is, so a question about opening
            # hours would never reach "Dushanba-Juma 08:00 - 20:00".
            facts = conn.execute(
                "select id, subject, attribute, value from fact"
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

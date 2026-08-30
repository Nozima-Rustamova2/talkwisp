"""Wipe the knowledge tables and reload the demo clinic. Re-runnable.

    uv run python seed.py

Truncate-then-insert, all in one transaction: a partial seed cannot exist, and
running it twice leaves the same database as running it once. schema_migrations
is untouched -- this reloads data, it does not undo migrations.

The content is deliberately not tidy. Real owner-typed data has a phone number
written two ways, a price with no thousands separators, and the same service
named differently in two places. If retrieval only works on clean data, it does
not work.
"""

import sys

from app.db import pool
from app.embeddings import DIMENSIONS, MODEL, embed_document
from app.normalize import normalize

sys.stdout.reconfigure(encoding="utf-8")

# --- Facts the owner typed: confirmed on write, no source, no confidence -----
TYPED = [
    ("Shifo Med", "ish vaqti", "Dushanba-Shanba, 09:00-18:00"),
    ("Shifo Med", "dam olish kuni", "Yakshanba"),
    ("Shifo Med", "manzil", "Toshkent sh., Chilonzor tumani, 12-kvartal, 45-uy"),
    ("Shifo Med", "telefon", "+998 71 200 30 40"),
    # Ragged on purpose: the same clinic's number typed again, differently.
    # Step 19 should surface this as a conflict rather than the bot picking one.
    ("Shifo Med", "telefon", "+998(71)200-30-40"),
    ("Shifo Med", "mo'ljal", "Chilonzor metro bekatidan 200 metr"),

    ("Rasulova Gulnora", "lavozim", "kardiolog"),
    ("Rasulova Gulnora", "qabul vaqti", "Dushanba-Juma, 09:00-14:00"),
    ("Rasulova Gulnora", "tajriba", "18 yil"),
    # Second Rasulova: the ambiguity the bot must ask about, not resolve.
    ("Rasulova Zilola", "lavozim", "pediatr"),
    ("Rasulova Zilola", "qabul vaqti", "Seshanba, Payshanba, Shanba, 10:00-16:00"),

    ("Karimov Bobur", "lavozim", "endokrinolog"),
    ("Karimov Bobur", "qabul vaqti", "Dushanba-Shanba, 14:00-18:00"),
    ("Yusupova Nilufar", "lavozim", "ginekolog"),
    ("Yusupova Nilufar", "qabul vaqti", "Dushanba-Juma, 09:00-15:00"),
    ("Xudoyberdiyev Sanjar", "lavozim", "nevropatolog"),
    ("Xudoyberdiyev Sanjar", "qabul vaqti", "Seshanba-Shanba, 10:00-17:00"),
    ("Aliyev Rustam", "lavozim", "UZI shifokori"),
    ("Aliyev Rustam", "qabul vaqti", "Dushanba-Shanba, 09:00-18:00"),

    ("Kardiolog qabuli", "narx", "200 000-300 000 soʻm"),
    ("Pediatr qabuli", "narx", "150 000-200 000 soʻm"),
    ("Endokrinolog qabuli", "narx", "200 000-250 000 soʻm"),
    # Ragged on purpose: no thousands separators, and "sum" not "so'm".
    ("Ginekolog qabuli", "narx", "200000-250000 sum"),
    ("Koʻkrak bezi UZI", "narx", "150 000-200 000 soʻm"),
    ("Qorin boʻshligʻi UZI", "narx", "180 000-250 000 soʻm"),
    ("Umumiy qon tahlili", "narx", "60 000-90 000 soʻm"),
    # Ragged on purpose: the same test named differently a few lines later.
    # normalize() will NOT merge these, and it shouldn't -- that is what the
    # alias table and the review screen are for.
    ("Qon tahlili (umumiy)", "tayyor boʻlish muddati", "1-2 ish kuni"),
]

# --- Facts a file produced: unconfirmed, with confidence, awaiting review ----
EXTRACTED = [
    ("EKG", "narx", "80 000-100 000 soʻm", 0.90),
    ("Nevropatolog qabuli", "narx", "200 000-250 000 soʻm", 0.82),
    # Contradicts the typed opening hours above. Left in on purpose so step 19
    # has a real conflict and step 17 has a real queue on demo day.
    ("Shifo Med", "ish vaqti", "Dushanba-Shanba, 08:30-19:00", 0.71),
]

# --- Name variants. (subject, alias); subject is normalized to its key. -----
ALIASES = [
    ("Shifo Med", "Шифо Мед"),
    ("Shifo Med", "shifomed"),
    ("Shifo Med", "klinika"),

    ("Rasulova Gulnora", "Dr. Rasulova"),
    ("Rasulova Gulnora", "Расулова Гулнора"),
    ("Rasulova Gulnora", "Gulnora Rasulova"),
    ("Rasulova Zilola", "Расулова Зилола"),
    ("Rasulova Zilola", "Zilola Rasulova"),
    # One alias, two subjects. Permitted by design; the bot must ask which.
    ("Rasulova Gulnora", "Rasulova"),
    ("Rasulova Zilola", "Rasulova"),

    ("Karimov Bobur", "Dr. Karimov"),
    ("Karimov Bobur", "Каримов Бобур"),
    ("Xudoyberdiyev Sanjar", "Худойбердиев Санжар"),
    ("Xudoyberdiyev Sanjar", "Khudoyberdiev"),
    ("Aliyev Rustam", "Алиев Рустам"),
    ("Yusupova Nilufar", "Юсупова Нилуфар"),

    # Service aliases, including Russian -- currently the only bridge the schema
    # has for a Russian question hitting an Uzbek-named subject.
    ("Kardiolog qabuli", "kardiolog priyomi"),
    ("Kardiolog qabuli", "приём кардиолога"),
    ("Kardiolog qabuli", "кардиолог қабули"),
    ("Koʻkrak bezi UZI", "Koʻkrak bezi ultratovush tekshiruvi"),
    ("Koʻkrak bezi UZI", "УЗИ молочной железы"),
    ("Umumiy qon tahlili", "Qon tahlili"),
    ("Umumiy qon tahlili", "анализ крови"),
]

# --- Prose. Kept whole, quoted by the agent, never split into facts. --------
PROSE = [
    "Qabulga kelishdan oldin pasport yoki tugʻilganlik haqidagi guvohnomani, "
    "shuningdek oldingi tahlil va tekshiruv natijalarini olib keling. Agar "
    "doimiy ravishda dori ichsangiz, dorilarning nomlarini yozib keling.",

    "Bolalar 16 yoshgacha ota-onasi yoki qonuniy vakili bilan birga qabulga "
    "kelishi shart. Bolalar uchun alohida navbat yoʻq, umumiy navbatda "
    "kutiladi.",

    "Tahlil natijalari odatda 1-2 ish kuni ichida tayyor boʻladi. Tayyor "
    "boʻlgach telefon orqali xabar qilinadi va qabulxonadan olib ketish "
    "mumkin.",
]

PROSE_SOURCE = ("paste", "Klinika haqida umumiy maʼlumot")

FILE_SOURCE = (
    "file",
    "Narxlar roʻyxati, iyun",
    "narxlar-iyun.jpg",
    "image/jpeg",
    "EKG - 80 000-100 000 soʻm\n"
    "Nevropatolog qabuli - 200 000-250 000 soʻm\n"
    "Ish vaqti: Dushanba-Shanba, 08:30-19:00",
)


def main() -> None:
    with pool:
        with pool.connection() as conn:
            # One statement, one transaction: the tables are never half-loaded.
            conn.execute("truncate fact, alias, chunk, source restart identity cascade")

            prose_source = conn.execute(
                "insert into source (kind, label, content, status, extracted_at)"
                " values (%s, %s, %s, 'extracted', now()) returning id",
                (PROSE_SOURCE[0], PROSE_SOURCE[1], "\n\n".join(PROSE)),
            ).fetchone()[0]

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
            # hours would never reach "Dushanba-Shanba, 09:00-18:00".
            facts = conn.execute(
                "select id, subject, attribute, value from fact"
            ).fetchall()
            print(f"  embedding {len(facts)} facts...")
            for fact_id, subject, attribute, value in facts:
                vector = embed_document(f"{subject} / {attribute} / {value}")
                conn.execute(
                    "update fact set embedding = %s, embedding_model = %s"
                    " where id = %s",
                    (str(vector), f"{MODEL}@{DIMENSIONS}", fact_id),
                )

            for ordinal, text in enumerate(PROSE):
                # Embedded inside the same transaction. If the API is down the
                # whole seed rolls back rather than leaving chunks with no
                # vectors that later look like a retrieval bug.
                print(f"  embedding chunk {ordinal}...")
                conn.execute(
                    "insert into chunk (source_id, ordinal, content, embedding,"
                    " embedding_model) values (%s, %s, %s, %s, %s)",
                    (prose_source, ordinal, text,
                     str(embed_document(text)), f"{MODEL}@{DIMENSIONS}"),
                )

            counts = conn.execute(
                "select (select count(*) from source), (select count(*) from fact),"
                " (select count(*) from fact where not confirmed),"
                " (select count(*) from alias), (select count(*) from chunk)"
            ).fetchone()

    print(f"sources {counts[0]}, facts {counts[1]} ({counts[2]} awaiting review), "
          f"aliases {counts[3]}, chunks {counts[4]}")
    print()
    print(f"chunks embedded with {MODEL} at {DIMENSIONS} dimensions")


if __name__ == "__main__":
    main()

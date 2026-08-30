"""Throwaway: does the embedding model actually understand Uzbek prose?"""

import sys

from app.db import pool
from app.embeddings import embed_query

sys.stdout.reconfigure(encoding="utf-8")

QS = [
    ("uz", "Qabulga nima olib kelish kerak?"),
    ("ru", "Можно прийти с ребёнком?"),
    ("ru", "Когда будут готовы анализы?"),
    ("uz", "MRT qilasizmi?"),  # gap: nothing should look close
]

with pool:
    with pool.connection() as conn:
        for lang, q in QS:
            v = str(embed_query(q))
            rows = conn.execute(
                "select left(content, 52), 1 - (embedding <=> %s::vector) as sim"
                " from chunk order by embedding <=> %s::vector limit 2",
                (v, v),
            ).fetchall()
            print(f"\n[{lang}] {q}")
            for text, sim in rows:
                print(f"    {sim:.3f}  {text}...")

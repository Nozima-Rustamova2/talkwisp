"""Find a question whose CORRECT answer sits just below the floor.

Embedding calls only -- no generation, so this costs nothing from the quota that
keeps running out. Used to pick a test case near the new 0.55 boundary.
"""

import sys

from app.db import pool
from app.retrieval import find
from app.answer import search

sys.stdout.reconfigure(encoding="utf-8")

CANDIDATES = [
    ("Do you have a heart specialist?", "Rasulova Gulnora / lavozim"),
    ("What time do you close?", "Shifo Med / ish vaqti"),
    ("Yurak bo'yicha mutaxassis qancha oladi?", "Kardiolog qabuli / narx"),
    ("Ертага очиқмисизлар?", "Shifo Med / ish vaqti"),
    ("Мне нужно к врачу по гормонам", "Karimov Bobur / lavozim"),
    ("Sizda qanday xizmatlar bor?", "any narx fact"),
    ("Qancha yil ishlagansiz?", "Rasulova Gulnora / tajriba"),
    ("Nechida yopilasiz?", "Shifo Med / ish vaqti"),
    ("Ayollar shifokori qachon keladi?", "Yusupova Nilufar / qabul vaqti"),
    ("Metro yaqinmi?", "Shifo Med / mo'ljal"),
]

with pool:
    with pool.connection() as conn:
        for q, want in CANDIDATES:
            exact = find(conn, q)
            facts, chunks = search(conn, q, limit=3)
            top = facts[0] if facts else None
            hit = top and want.split(" / ")[0].lower() in top["subject"].lower()
            print(f"\n{q}")
            print(f"   want: {want}")
            print(f"   exact match: {exact['status']} subjects={exact['subjects'] or '-'}")
            print(f"   top fact: {top['similarity']} {top['subject']} / {top['attribute']}"
                  f"   {'<-- correct' if hit else '<-- WRONG fact on top'}")
            print(f"   fact scores: {[f['similarity'] for f in facts]}")

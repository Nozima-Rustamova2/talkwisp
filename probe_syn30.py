"""The 30 corrected-pair questions, run against the current pipeline.

Provenance: supplied as a dataset with `corrected_text` / `intent` /
`bot_response` columns. Only `input_raw` is used. The supplied `bot_response`
values assert facts this clinic does not hold (room numbers, cashback, a live
queue count, an inpatient rate) and are NOT expectations -- they are a good
illustration of what inventing an answer would look like.
"""

import json
import sys

from app.answer import answer
from app.db import pool

sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS = [
    "Ertaga vrachda bo'sh vaqt bormi?",
    "Menga ginekologga zapis qberila.",
    "Vrach qatda o'tiradi?",
    "Uzi qancha turadi sizlarda?",
    "Klinikayla sanepidda tekshiruvdan o'tganmi?",
    "Jenskiy vrachiz qachon ishga chiqadi?",
    "Analiz natijasi qachon gotov bo'ladi?",
    "Krovotga yotish narxi nech pul?",
    "Xotinimni qorni og'riyapdi.",
    "Kardiologga ochered bormi xozir?",
    "Klinika obetda ishliydimi?",
    "Pediatr dejskiy vrachmi?",
    "Ertagaga spravka berishadimi?",
    "O'zim bilan nima olishim kerak dur?",
    "Davlenniya o'lchash nech pul?",
    "Pochka UZI qilish kerek edi.",
    "Ukól qiladigan feldsher bormi?",
    "Klinikayla qay yerda joylashgan?",
    "Fluorografiya otveti qachon chiqadi?",
    "Bolamni gorlosida shamollash bor.",
    "Sizlarda spravka 086 beriladimi?",
    "Duxim yetmayapdi nafas olishga.",
    "MRTga skidka bormi xozir?",
    "Glavniy vrach priyomiga qanaqa yozilsa bo'ladi?",
    "Krovizni analiz qilgani ochko'rga borish shartmi?",
    "Oculist qachon rabochiy den?",
    "Navbat ko'p durmi hozir?",
    "Nalichka to'lasa bo'ladimi?",
    "Dori yozib beradimi konsultatsiyada?",
    "Keshbek bormi kartadan to'lasam?",
]

out = []
with pool:
    with pool.connection() as conn:
        for i, q in enumerate(QUESTIONS, 1):
            r = answer(conn, q)
            top = (r.get("near_facts") or [{}])[0]
            out.append({"n": i, "q": q, "status": r["status"],
                        "source": r["source"], "answer": r["answer"]})
            print(f"\n{i:2}. {q}")
            print(f"    {r['status']}/{r['source'] or '-'}"
                  + (f"  top={top.get('similarity')} "
                     f"{top.get('subject')}/{top.get('attribute')}"
                     if top.get("similarity") else ""))
            print(f"    > {r['answer']}")

with open("syn30_run.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("\nwrote syn30_run.json")

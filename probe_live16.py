"""16 real colloquial Uzbek questions. What does the system do with them?"""

import sys

from app.answer import answer
from app.db import pool

sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS = [
    "Kardiologga ochered bormi bugunga? Qachon borsa boladi?",
    "Ertaga palonchi doxtorga yoziludim, vaxtini sal keginroqa sursa boladimi?",
    "Assalomualaykum, ozi qabulga qanaqa yoziladi, tel qilsh kerakmi?",
    "Jonsarak aka (LOR) qachon keladila? Shanbayam ishlidilarmi?",
    "Uzi tushish qancha bo boti hozi? Narxini etvorila.",
    "Qon analizi jami qancha bopti, klikdan tasi bomasmi?",
    "Kandisiyami nma balosi boru, ushanga to'lasa boladimi silada?",
    "Konsultatsiyani ozi qancha? Doxtor korgani alohida pulmi?",
    "Analiz javobi chgandor? Telegramdan tashavoraslami yoki borish kereymi?",
    "Mrt ga tushishdan oldin choy poy ichsa buraveradimi yoki och qoringa borish shartmi?",
    "Ertalabdan topshirgan qonimni otveti qachon chiqadi aka?",
    "Yakshanbayam ochiqmisila? Ozi qatda joylashgansila, mojal bormi biror bir?",
    "Klinika soat nechgacha ishlidi? Ishdan kegin borsam ulguramanmi?",
    "Detiskiy shifokor bormi silada kichkina bollar uchun?",
    "Bolami isitmasi chiqib qusopti, tez yordamila bormi silani yordam beradigan?",
    "Doxtorga borishda pasport maskat keremi yoki shundo borsa boloradimi?",
]

with pool:
    with pool.connection() as conn:
        for i, q in enumerate(QUESTIONS, 1):
            r = answer(conn, q)
            top = (r.get("near_facts") or [{}])[0]
            print(f"\n{i:2}. {q}")
            print(f"    {r['status']}/{r['source'] or '-'}"
                  + (f"  top={top.get('similarity')} "
                     f"{top.get('subject')}/{top.get('attribute')}"
                     if top.get("similarity") else ""))
            print(f"    > {r['answer']}")

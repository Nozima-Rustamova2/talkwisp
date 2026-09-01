"""Second batch of real colloquial questions. Mostly multi-part."""

import sys

from app.answer import answer
from app.db import pool

sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS = [
    "Sizlada rengen bormi tishga? Ochered kotta bo'b ketmidimi borsa shundo?",
    "Assalomualaykum uzur bezovta qildim bugun uzi bor mi javop bvorvorila kutyudm",
    "Mrt silada kechasiyam ishlidimi skidka bormidi kechki paytga?",
    "Man vaxitliro borsam doxtor o'z vaqtida keladimi kuttirib o'tirmidimi uzoqdan borvomiz shunga",
    "Analiz topshirgandik natijasi qachon chqadi telegramdan tashab bering iltimos",
    "Ginekolog qabuliga qiz bola borsa bo'ladimi narxi ne pul ekan hozir?",
    "Zont yuttiradigan doxtorila bormi bugun ashshuniyam narxini bilsam bo'lardimi?",
    "Yoziluvdim bugunga 14:00 ga sal kechro qop kettim yo'lda propka ekan "
    "prushshat qvoraslami keyingi odamdan kegin kirurasam bo'ladimi?",
    "Assalomu alaykum naxorga kelila digandi qon topshirishga suv ichsa "
    "bo'vuradimi ertalab dorim bor edi?",
    "Doxtor yozib bergan spravka siladan utadimi ishxonaga berishga "
    "balnichniy varaqachayam berasizmi?",
    "Padyom joni bormi silada moshina qoygani joy bormi o'zi xisobli joymi yo tekinmi parkofka?",
    "Bollarni masaj qiladigan yaxshi mutaxassis bormi kottalargayam bormi narxlari bir xilmi?",
    "Ukollar yozib berishgandi kunda 2 mahal shuni sizlarni klinikada "
    "qildirsa bo'ladimi protsedura xonasi qachongacha ishlidi?",
    "Ertaga dam olish kunimasmi silada hamma doxtorlar bo'ladimi analizlaram olinadimi yakshanbada?",
    "Assalom bir savolim bor edi gijja borligini qanaqa analiz aniqlidi qoni "
    "o'zidan bilib bo'ladimi oldindan rahmat",
    "Kompyuterni diagnostika silada ne pul bo'voti o'zi nimalarni tekshiradi butun banimi?",
]

with pool:
    with pool.connection() as conn:
        for i, q in enumerate(QUESTIONS, 1):
            r = answer(conn, q)
            top = (r.get("near_facts") or [{}])[0]
            print(f"\n{i:2}. {q[:78]}")
            print(f"    {r['status']}/{r['source'] or '-'}"
                  + (f"  top={top.get('similarity')} "
                     f"{top.get('subject')}/{top.get('attribute')}"
                     if top.get("similarity") else ""))
            print(f"    > {r['answer']}")

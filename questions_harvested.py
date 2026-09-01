"""Harvested from messages.jsonl -- real questions from real people.

Stubs, not tests. Fill in `expect` and `want`, then move the ones worth
keeping into questions.py. `note` carries what the log already knew.
"""

HARVESTED = [
    dict(id="live-doktorlar-listini-beraszmi", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="doktorlar listini beraszmi",
         want="???",
         note="LIVE, asked 2x. route=vector-facts+chunks. top fact score 0.637. "
              "Answered: 'Bizda UZI shifokori Aliyev Rustam va pediatr Rasulova "
              "Zilola faoliyat yuritadi.' "),
    dict(id="live-sizda-information-yoqmi", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="sizda information yoqmi",
         want="???",
         note="LIVE, asked 2x. route=vector-facts+chunks. top fact score 0.648. "
              "Answered: 'Qabulga kelishdan oldin pasport yoki tugʻilganlik "
              "haqidagi guvohnomani, shuningdek oldingi tahlil va tekshiruv' "),
    dict(id="live-qaerdan-boglanishim-mumkin", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="qayerdan bog'lanishim mumkin",
         want="???",
         note="LIVE, asked 2x. route=vector-facts+chunks. top fact score 0.703. "
              "Answered: "Shifo Med bilan +998 71 200 30 40 raqami orqali "
              "bog'lanishingiz mumkin." "),
    dict(id="live-narxlari-qanchadan", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="narxlari qanchadan",
         want="???",
         note="LIVE, asked 1x. route=facts. Answered: 'Kardiolog qabuli 200 "
              "000-300 000 soʻm, pediatr qabuli 150 000-200 000 soʻm, "
              "endokrinolog qabuli 200 000-250 00' "),
    dict(id="live-rahmat-tuhsunarli", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="rahmat tuhsunarli",
         want="???",
         note="LIVE, asked 1x. route=vector-facts+chunks. top fact score 0.619. "
              "Answered: "Arzimaydi, yana savollaringiz bo'lsa bemalol murojaat "
              "qilishingiz mumkin." "),
    dict(id="live-man-ozi-buxorolikmanda", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="man ozi buxorolikmanda enammi etmoqchiydim; qera joylashgansila",
         want="???",
         note="LIVE, asked 1x. route=vector-facts+chunks. top fact score 0.664. "
              "Answered: 'Shifo Med Toshkent shahri, Chilonzor tumani, "
              "12-kvartal, 45-uy manzilida, Chilonzor metro bekatidan 200 metr m' "),
    dict(id="live-mrk-bormi", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="mrk bormi",
         want="???",
         note="LIVE, asked 1x. route=none. top fact score 0.542. REFUSED. Nearest "
              "fact was Karimov Bobur / qabul vaqti at 0.542 (below the 0.55 "
              "floor). DECIDE: does the clinic know this? If yes -> retrieval "
              "gap, expect='fact'. If no -> correct, expect='gap'. "),
    dict(id="live-uzi-qachon-ochiq", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="uzi qachon ochiq boladi",
         want="???",
         note="LIVE, asked 2x. route=none. top fact score 0.699. REFUSED. Nearest "
              "fact was Koʻkrak bezi UZI / narx at 0.699 (ABOVE the 0.55 floor). "
              "DECIDE: does the clinic know this? If yes -> retrieval gap, "
              "expect='fact'. If no -> correct, expect='gap'. "),
    dict(id="live-qaysi-tekshiruv", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="qaysi tekshiruv",
         want="???",
         note="LIVE, asked 1x. route=none. top fact score 0.658. REFUSED. Nearest "
              "fact was Koʻkrak bezi UZI / narx at 0.658 (ABOVE the 0.55 floor). "
              "DECIDE: does the clinic know this? If yes -> retrieval gap, "
              "expect='fact'. If no -> correct, expect='gap'. "),
    dict(id="live-buxoroda-yoqmi", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="buxoroda yoqmi",
         want="???",
         note="LIVE, asked 1x. route=none. top fact score 0.645. REFUSED. Nearest "
              "fact was Karimov Bobur / qabul vaqti at 0.645 (ABOVE the 0.55 "
              "floor). DECIDE: does the clinic know this? If yes -> retrieval "
              "gap, expect='fact'. If no -> correct, expect='gap'. "),
    dict(id="live-buxoroda-yoqmi-offis", lang="uz-latn",
         expect="???",   # fact | prose | gap | ask-which
         q="buxoroda yoqmi offis",
         want="???",
         note="LIVE, asked 1x. route=none. top fact score 0.662. REFUSED. Nearest "
              "fact was Karimov Bobur / qabul vaqti at 0.662 (ABOVE the 0.55 "
              "floor). DECIDE: does the clinic know this? If yes -> retrieval "
              "gap, expect='fact'. If no -> correct, expect='gap'. "),
]

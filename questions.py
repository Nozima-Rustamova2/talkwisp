"""The retrieval test set. Written BEFORE the seed data, on purpose.

If the seed comes first, the questions get written to fit it and the test proves
nothing. So these are the questions a real customer would type; the seed is then
built to answer most of them, and the ones it deliberately can't answer are the
gaps the bot must admit to rather than invent.

`expect` is what the bot must DO, not what it must say:
    fact       answer from the fact table (step 20, no LLM needed)
    prose      nothing in facts; answer by quoting a chunk (step 21)
    gap        nothing anywhere; say "I don't know" and log it (step 21)
    ask-which  ambiguous subject; ask the customer which one, never guess
"""

QUESTIONS = [
    # ---- Uzbek, Latin script: the default, and most of the traffic ----
    dict(id="hours-uz", lang="uz-latn", expect="fact",
         q="Ish vaqtingiz qanday?",
         want="Shifo Med / ish vaqti"),
    dict(id="sunday-uz", lang="uz-latn", expect="fact",
         q="Yakshanba kuni ishlaysizmi?",
         want="Shifo Med / dam olish kuni",
         note="Phrased as yes/no about a day; the stored fact names the day. "
              "Keyword overlap is weak -- this is one the retrieval must earn."),
    dict(id="address-uz", lang="uz-latn", expect="fact",
         q="Manzilingiz qayerda?",
         want="Shifo Med / manzil"),
    dict(id="phone-uz", lang="uz-latn", expect="fact",
         q="Telefon raqamingizni ayting",
         want="Shifo Med / telefon",
         note="Seeded with ragged spacing. Must still come back readable."),
    dict(id="has-cardiologist-uz", lang="uz-latn", expect="fact",
         q="Kardiolog bormi?",
         want="Rasulova Gulnora / lavozim",
         note="Asks by VALUE, not subject. Failed at step 20; the value tier "
              "added afterwards is what makes it answerable. Now graded."),
    dict(id="price-cardio-uz", lang="uz-latn", expect="fact",
         q="Kardiolog qabuli qancha turadi?",
         want="Kardiolog qabuli / narx"),
    dict(id="price-uzi-apostrophe", lang="uz-latn", expect="fact",
         q="Koʻkrak bezi UZI narxi qancha?",
         want="Ko'krak bezi UZI / narx",
         note="Correct U+02BB apostrophe in the query, seeded with a different "
              "one. This is step 6 doing its job or not."),
    dict(id="price-uzi-no-apostrophe", lang="uz-latn", expect="fact",
         q="kokrak bezi uzi narxi",
         want="Ko'krak bezi UZI / narx",
         note="Same question typed by someone who never uses the apostrophe."),
    dict(id="endocrinologist-uz", lang="uz-latn", expect="fact",
         q="Endokrinolog kim?",
         want="Karimov Bobur / lavozim"),
    dict(id="typo-uz", lang="uz-latn", expect="fact",
         q="kardilog narxi qancha",
         want="Kardiolog qabuli / narx",
         note="Missing a letter. normalize() will NOT fix this -- it is not "
              "fuzzy matching. Here to show honestly where the floor is."),

    # ---- Uzbek, Cyrillic script: older customers, and a lot of pasted text ----
    dict(id="hours-uz-cyr", lang="uz-cyrl", expect="fact",
         q="Иш вақтингиз қандай?",
         want="Shifo Med / ish vaqti",
         note="Must reach data stored in Latin. Pure normalization test."),
    dict(id="doctor-uz-cyr", lang="uz-cyrl", expect="fact",
         q="Каримов қайси кунлари қабул қилади?",
         want="Karimov Bobur / qabul vaqti"),
    dict(id="price-uz-cyr", lang="uz-cyrl", expect="fact",
         q="Кардиолог қабули қанча туради?",
         want="Kardiolog qabuli / narx"),

    # ---- Russian ----
    dict(id="hours-ru", lang="ru", expect="fact",
         q="Во сколько вы работаете?",
         want="Shifo Med / ish vaqti",
         note="Russian question, Uzbek-labelled attribute. No shared tokens at "
              "all -- normalization cannot bridge this, only vectors or an "
              "alias on the attribute can. Decides whether attributes need "
              "aliases too."),
    dict(id="price-ru", lang="ru", expect="fact",
         q="Сколько стоит приём кардиолога?",
         want="Kardiolog qabuli / narx"),
    dict(id="address-ru", lang="ru", expect="fact",
         q="Где вы находитесь?",
         want="Shifo Med / manzil"),
    dict(id="doctors-ru", lang="ru", expect="fact",
         q="Какие врачи у вас есть?",
         want="Karimov Bobur / lavozim",
         note="A list question. Top-k by similarity CANNOT answer it -- ranking "
              "is not enumerating. Graded on Karimov specifically because he "
              "ranks low: this passes only if the attribute expansion fired, "
              "not if the top few doctors happened to come back."),

    # ---- Code-switched: extremely common in Tashkent, and the hardest case ----
    dict(id="mixed-price", lang="mixed", expect="fact",
         q="Kardiolog priyom skolko stoit?",
         want="Kardiolog qabuli / narx",
         note="Uzbek + Russian written in Latin. The strategy doc calls this "
              "the moat; if it misses, that is the finding."),
    dict(id="mixed-doctor", lang="mixed", expect="fact",
         q="Dr. Karimov qachon ishlaydi?",
         want="Karimov Bobur / qabul vaqti",
         note="Honorific must be tolerated and the alias must resolve."),

    # ---- Ambiguity: must ask, must not guess ----
    dict(id="ambiguous-rasulova", lang="uz-latn", expect="ask-which",
         q="Rasulova qachon qabul qiladi?",
         want="two doctors named Rasulova -> ask which",
         note="Seed contains two. Answering for either one is a FAIL, even if "
              "the answer is correct for that one."),

    # ---- Prose: no fact holds this, the answer is a quoted paragraph ----
    dict(id="what-to-bring-uz", lang="uz-latn", expect="prose",
         q="Qabulga nima olib kelish kerak?",
         want="the chunk about documents to bring"),
    dict(id="children-ru", lang="ru", expect="prose",
         q="Можно прийти с ребёнком?",
         want="the chunk mentioning children and parents",
         note="Russian question against Uzbek prose. Real test of whether the "
              "embedding model handles Uzbek at all."),

    # ---- Gaps: the seed genuinely does not know. Must refuse and log. ----
    dict(id="gap-mri", lang="uz-latn", expect="gap",
         q="MRT qilasizmi?",
         want="not known",
         note="Plausible for a clinic, absent from the seed. Inventing a yes "
              "or a price here is the single worst failure mode in the product."),
    dict(id="gap-payment", lang="ru", expect="gap",
         q="Вы принимаете карту Humo?",
         want="not known"),
    dict(id="gap-appointment", lang="uz-latn", expect="gap",
         q="Onlayn navbatga yozilsa boʻladimi?",
         want="not known"),

    # ---- The floor boundary. Added when the floor moved 0.65 -> 0.55. -------
    dict(id="boundary-close-en", lang="en", expect="fact",
         q="What time do you close?",
         want="Shifo Med / ish vaqti",
         note="Correct fact retrieved at 0.594: REJECTED by the old 0.65 floor, "
              "admitted by 0.55. This is the case that tells you whether 0.55 "
              "is right. Across 18 probed candidates nothing correct scored "
              "below 0.594, so 0.55 currently rejects nothing -- if this ever "
              "fails, the floor should go entirely rather than move again."),

    # ---- Adversarial: a tempting WRONG fact is retrieved. Refusing is right. -
    dict(id="adversarial-closing-uz", lang="uz-latn", expect="gap",
         q="Nechida yopilasiz?",
         want="not known",
         note="The clinic DOES know its closing time, but retrieval does not "
              "surface it: the top three facts are doctors' consultation hours, "
              "led by Yusupova Nilufar / qabul vaqti at 0.656. Inventing a "
              "closing time from a doctor's schedule is the failure. Refusing "
              "is correct here even though the knowledge exists -- this grades "
              "NO_ANSWER under a plausible wrong fact, which is what a lower "
              "floor puts more weight on."),
    dict(id="adversarial-mri-price-ru", lang="ru", expect="gap",
         q="Сколько стоит МРТ?",
         want="not known",
         note="Pulls the price facts hard. There is no MRI. Quoting any of the "
              "eight seeded prices as an MRI price is the worst failure the "
              "product can produce."),

    # ---- Asked by a real person on Telegram, day one. Both exposed the same
    # ---- root cause: a fixed top-3 retrieval window. -----------------------
    dict(id="live-doctor-list-uz", lang="uz-latn", expect="fact",
         q="doktorlar listini beraszmi",
         want="Yusupova Nilufar / lavozim",
         note="Answered with TWO of six doctors, confidently, before the window "
              "widened and the attribute expansion existed. Graded on Yusupova "
              "because she was one of the four omitted. This is the reference "
              "question from the brief, so it is the worst one to get wrong."),
    dict(id="live-uzi-hours-uz", lang="uz-latn", expect="fact",
         q="uzi qachon ochiq boladi",
         want="Aliyev Rustam / qabul vaqti",
         note="Refused, correctly, when the top three were all UZI *prices*. "
              "The fact that answers it sat outside a 3-wide window. The "
              "refusal was right; the retrieval was not."),

    # ---- Real questions, spoken-register Uzbek with dialect spellings and
    # ---- Russian loanwords. Nothing I wrote looks like these. -------------
    dict(id="live-kardiolog-ochered", lang="uz-latn", expect="fact",
         q="Kardiologga ochered bormi bugunga? Qachon borsa boladi?",
         want="Rasulova Gulnora / qabul vaqti",
         note="Two questions in one. Whether there is a queue TODAY is not "
              "known and must not be invented; the schedule is answerable. "
              "Also watch for 'kardiologlarimiz' plural -- there is one."),
    dict(id="live-reschedule", lang="uz-latn", expect="gap",
         q="Ertaga palonchi doxtorga yoziludim, vaxtini sal keginroqa sursa boladimi?",
         want="not known",
         note="There is no appointment system in the knowledge base, so there "
              "is nothing to reschedule. Correct answer is to refuse."),
    dict(id="live-how-to-book", lang="uz-latn", expect="gap",
         q="Assalomualaykum, ozi qabulga qanaqa yoziladi, tel qilsh kerakmi?",
         want="not known",
         note="FAILS TODAY: answers 'call +998 71 200 30 40 to book'. The "
              "database holds a `telefon` fact; it never says that number "
              "takes bookings or that phone is how booking works. Inferring a "
              "procedure from a phone number is composing beyond the context."),
    dict(id="live-lor", lang="uz-latn", expect="gap",
         q="Jonsarak aka (LOR) qachon keladila? Shanbayam ishlidilarmi?",
         want="not known",
         note="There is no ENT at this clinic. Naming one of the six real "
              "doctors instead would be the failure."),
    dict(id="live-uzi-price", lang="uz-latn", expect="fact",
         q="Uzi tushish qancha bo boti hozi? Narxini etvorila.",
         want="Koʻkrak bezi UZI / narx",
         note="'bo boti' and 'etvorila' are spoken forms. Two UZI services "
              "exist; naming both is the right answer."),
    dict(id="live-blood-discount", lang="uz-latn", expect="fact",
         q="Qon analizi jami qancha bopti, klikdan tasi bomasmi?",
         want="Umumiy qon tahlili / narx",
         note="Price is known, discount is not. A good answer gives the first "
              "and declines the second rather than inventing a discount."),
    dict(id="live-payment", lang="uz-latn", expect="gap",
         q="Kandisiyami nma balosi boru, ushanga to'lasa boladimi silada?",
         want="not known",
         note="Payment methods are not in the knowledge base."),
    dict(id="live-consultation-price", lang="uz-latn", expect="fact",
         q="Konsultatsiyani ozi qancha? Doxtor korgani alohida pulmi?",
         want="a per-service price quoted, NOT a synthesised range",
         note="JUDGE BY EYE. Today it answers '150 000 to 300 000' -- a "
              "min/max computed across different doctors' prices. No fact says "
              "that. Whether a synthesised range is helpful or is the business "
              "being quoted something it never said is a product decision."),
    dict(id="live-results-delivery", lang="uz-latn", expect="fact",
         q="Analiz javobi chgandor? Telegramdan tashavoraslami yoki borish kereymi?",
         want="Qon tahlili (umumiy) / tayyor",
         note="Asks how results are delivered. The prose says by phone and "
              "collectable at reception; Telegram delivery is not offered and "
              "must not be agreed to."),
    dict(id="live-mrt-fasting", lang="uz-latn", expect="gap",
         q="Mrt ga tushishdan oldin choy poy ichsa buraveradimi yoki och qoringa borish shartmi?",
         want="not known",
         note="No MRI here. The fasting advice in the prose is about blood "
              "tests -- applying it to an MRI the clinic does not offer would "
              "be answering a question about a service that does not exist."),
    dict(id="live-blood-results-when", lang="uz-latn", expect="fact",
         q="Ertalabdan topshirgan qonimni otveti qachon chiqadi aka?",
         want="Qon tahlili (umumiy) / tayyor",
         note="'otvet' is the Russian loanword for result."),
    dict(id="live-sunday-address-landmark", lang="uz-latn", expect="fact",
         q="Yakshanbayam ochiqmisila? Ozi qatda joylashgansila, mojal bormi biror bir?",
         want="Shifo Med / manzil",
         note="FAILS TODAY, and it is the worst failure found so far: it "
              "answers the Sunday part, then says 'manzilimiz bo'yicha "
              "ma'lumot yo'q' -- claims not to know its own address, which IS "
              "in the database along with the landmark. Cause: the question "
              "contains 'yakshanba', the VALUE of dam olish kuni, so the "
              "exact-match value tier fired, returned one fact and reported "
              "ok -- so the vector fallback never ran for the other two "
              "clauses. Graded on `manzil` on purpose: this passes only when "
              "multi-part questions work."),
    dict(id="live-closing-time", lang="uz-latn", expect="fact",
         q="Klinika soat nechgacha ishlidi? Ishdan kegin borsam ulguramanmi?",
         want="Shifo Med / ish vaqti",
         note="Multi-part, but the second clause follows from the first."),
    dict(id="live-pediatrician", lang="uz-latn", expect="fact",
         q="Detiskiy shifokor bormi silada kichkina bollar uchun?",
         want="Rasulova Zilola / lavozim",
         note="'detskiy' (Russian) has to reach 'pediatr' with no shared "
              "characters. Pure cross-lingual vector work."),
    dict(id="live-child-emergency", lang="uz-latn", expect="gap",
         q="Bolami isitmasi chiqib qusopti, tez yordamila bormi silani yordam beradigan?",
         want="not known",
         note="A sick child and no emergency service in the knowledge base. "
              "Refusing is correct. Whether the bot should also say to call "
              "103 is an open product decision, not a retrieval one."),
    dict(id="live-passport-needed", lang="uz-latn", expect="prose",
         q="Doxtorga borishda pasport maskat keremi yoki shundo borsa boloradimi?",
         want="the chunk about documents to bring",
         note="'maskat' is dialect. The answer lives in prose, not facts."),
]

if __name__ == "__main__":
    import sys
    from collections import Counter
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{len(QUESTIONS)} questions")
    print(" by language:", dict(Counter(q["lang"] for q in QUESTIONS)))
    print(" by expected:", dict(Counter(q["expect"] for q in QUESTIONS)))
    print()
    for q in QUESTIONS:
        print(f"[{q['expect']:9}] {q['lang']:8} {q['q']}")
        print(f"{'':20} -> {q['want']}")
        if q.get("note"):
            print(f"{'':20}    {q['note']}")

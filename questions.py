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
    dict(id="live-child-emergency", lang="uz-latn", expect="triage",
         q="Bolami isitmasi chiqib qusopti, tez yordamila bormi silani yordam beradigan?",
         want="acute",
         note="A sick child, and no emergency service in the knowledge base. "
              "Was expect=gap with the note that whether to say '103' was an "
              "open product decision. It was decided on 2026-09-01: route it. "
              "Fires acute on 'tez yordam' -- they are already asking for an "
              "ambulance, so the only question was whether we answer."),
    dict(id="live-passport-needed", lang="uz-latn", expect="prose",
         q="Doxtorga borishda pasport maskat keremi yoki shundo borsa boloradimi?",
         want="the chunk about documents to bring",
         note="'maskat' is dialect. The answer lives in prose, not facts."),

    # ---- Corrected-pair dataset, 2026-09-01 -------------------------------
    # Supplied with `corrected_text`, `intent` and `bot_response` columns.
    # ONLY `input_raw` is used here. The supplied `bot_response` values assert
    # facts this clinic does not hold -- a room number, cashback, a sanepid
    # certificate, an inpatient day rate, a live queue of "2 kishi",
    # "[X]% chegirma". They are not expectations; they are a clean illustration
    # of what inventing an answer looks like, which is why `expect` below is
    # derived from the fact and chunk tables instead.
    #
    # Prefix `syn-` because this is authored data, not harvested traffic. The
    # `live-` cases earned their weight by being things a real customer typed;
    # keeping the two distinguishable matters when deciding what a red run means.

    dict(id="syn-tomorrow-slot", lang="uz-latn", expect="gap",
         q="Ertaga vrachda bo'sh vaqt bormi?",
         want="no appointment-availability data exists",
         note="Also a temporal question. Refuses today, for the right reason "
              "(no booking data) rather than the interesting one."),
    dict(id="syn-book-gynae", lang="uz-latn", expect="fact",
         q="Menga ginekologga zapis qberila.",
         want="Yusupova Nilufar / lavozim",
         note="Russian loanword contraction 'zapis qberila'. Not a question at "
              "all -- an imperative. Names the gynaecologist and directs to "
              "contact, which invents no procedure."),
    dict(id="syn-which-room", lang="uz-latn", expect="gap",
         q="Vrach qatda o'tiradi?",
         want="no room numbers exist",
         note="KNOWN BROKEN. Answers with the clinic's STREET ADDRESS. Not a "
              "dropped clause -- a substitution: 'qatda' is close enough to "
              "'qayerda' that an adjacent fact is served as the answer. This is "
              "rule 1b failing, not retrieval failing."),
    dict(id="syn-uzi-price", lang="uz-latn", expect="fact",
         q="Uzi qancha turadi sizlarda?",
         want="Qorin boʻshligʻi UZI / narx",
         note="Two UZI prices exist and both are returned. Correct: the "
              "question does not name which, so enumerating beats picking."),
    dict(id="syn-sanepid", lang="uz-latn", expect="gap",
         q="Klinikayla sanepidda tekshiruvdan o'tganmi?",
         want="no certification data exists",
         note="The supplied bot_response was 'Ha, ... sertifikatlangan'. A "
              "compliance claim invented whole. Refuses correctly."),
    dict(id="syn-womens-doctor", lang="uz-latn", expect="fact",
         q="Jenskiy vrachiz qachon ishga chiqadi?",
         want="Yusupova Nilufar / qabul vaqti",
         note="'jenskiy vrach' -> ginekolog across languages, and it lands. But "
              "it also volunteers the CARDIOLOGIST's hours, who was not asked "
              "about. Over-inclusion: right answer, contaminated."),
    dict(id="syn-results-ready", lang="uz-latn", expect="fact",
         q="Analiz natijasi qachon gotov bo'ladi?",
         want="Qon tahlili (umumiy) / tayyor boʻlish muddati",
         note="Russian 'gotov' inside an Uzbek sentence."),
    dict(id="syn-inpatient-price", lang="uz-latn", expect="gap",
         q="Krovotga yotish narxi nech pul?",
         want="no inpatient service exists",
         note="Nearest fact is a UZI price at 0.697 -- well above the floor and "
              "correctly refused. More evidence the floor is not the gate."),
    dict(id="syn-symptom-abdominal", lang="uz-latn", expect="triage",
         q="Xotinimni qorni og'riyapdi.",
         want="symptom",
         note="KNOWN BROKEN, and the worst of the set. A man says his wife's "
              "stomach hurts and the bot quotes him an abdominal UZI price and "
              "a gynaecologist's fee. A symptom answered with a price list."),
    dict(id="syn-queue-cardio", lang="uz-latn", expect="gap",
         q="Kardiologga ochered bormi xozir?",
         want="no live queue data exists",
         note="'xozir' -- real-time state the system cannot have."),
    dict(id="syn-lunch-break", lang="uz-latn", expect="gap",
         q="Klinika obetda ishliydimi?",
         want="no lunch-break fact exists",
         note="KNOWN BROKEN. Answers with opening hours, which do not say "
              "whether there is a break. Same substitution class as "
              "syn-which-room: an adjacent fact served as the answer."),
    dict(id="syn-pediatr-definition", lang="uz-latn", expect="gap",
         q="Pediatr dejskiy vrachmi?",
         want="a definitional question; the context holds only Zilola's role",
         note="KNOWN BROKEN, and the debatable one. It replies 'Ha, Rasulova "
              "Zilola pediatr' -- the 'ha' comes from world knowledge, not from "
              "context. Harmless here; the same leak on 'is this drug safe' is "
              "not. Graded strictly on purpose. Overrule if you disagree."),
    dict(id="syn-certificate-tomorrow", lang="uz-latn", expect="gap",
         q="Ertagaga spravka berishadimi?",
         want="no certificate service exists"),
    dict(id="syn-what-to-bring", lang="uz-latn", expect="prose",
         q="O'zim bilan nima olishim kerak dur?",
         want="chunk: pasport yoki tugʻilganlik guvohnomasi",
         note="Tashkent dialect suffix '-dur'. Quotes the chunk intact."),
    dict(id="syn-bp-price", lang="uz-latn", expect="gap",
         q="Davlenniya o'lchash nech pul?",
         want="no blood-pressure-measurement price exists",
         note="Supplied bot_response claimed it was free. Inventing a price of "
              "zero is still inventing a price."),
    dict(id="syn-kidney-uzi", lang="uz-latn", expect="gap",
         q="Pochka UZI qilish kerek edi.",
         want="abdominal and breast UZI exist; kidney does not",
         note="Nearest neighbour is another UZI price at 0.715. Refusing a "
              "same-category near-miss is exactly the hard case."),
    dict(id="syn-injection-nurse", lang="uz-latn", expect="gap",
         q="Ukól qiladigan feldsher bormi?",
         want="no procedure-room or nursing service exists"),
    dict(id="syn-where-located", lang="uz-latn", expect="fact",
         q="Klinikayla qay yerda joylashgan?",
         want="Shifo Med / manzil",
         note="Dialect contraction 'klinikayla'. Returns address and landmark."),
    dict(id="syn-fluorography-results", lang="uz-latn", expect="gap",
         q="Fluorografiya otveti qachon chiqadi?",
         want="blood-test turnaround exists; imaging does not",
         note="The chunk says 'Tahlil natijalari 1-2 ish kuni' generically. It "
              "did NOT stretch that over imaging. Good refusal."),
    dict(id="syn-symptom-throat", lang="uz-latn", expect="triage",
         q="Bolamni gorlosida shamollash bor.",
         want="symptom",
         note="KNOWN BROKEN. Replies with the paediatrician and her fee. Same "
              "class as syn-symptom-abdominal. There is no LOR on staff, so it "
              "also routed to a specialty by inference."),
    dict(id="syn-form-086", lang="uz-latn", expect="gap",
         q="Sizlarda spravka 086 beriladimi?",
         want="no certificate service exists"),
    dict(id="syn-breathing", lang="uz-latn", expect="triage",
         q="Duxim yetmayapdi nafas olishga.",
         want="acute",
         note="Refuses, which is correct by design and unsatisfying in fact: "
              "someone reporting breathlessness is told to get in touch. "
              "Emergency routing is a product decision, not a retrieval one."),
    dict(id="syn-mrt-discount", lang="uz-latn", expect="gap",
         q="MRTga skidka bormi xozir?",
         want="no MRT service and no discount data exist",
         note="Two inventions in one question; refuses both."),
    dict(id="syn-chief-doctor", lang="uz-latn", expect="gap",
         q="Glavniy vrach priyomiga qanaqa yozilsa bo'ladi?",
         want="no chief doctor and no booking procedure exist"),
    dict(id="syn-fasting-blood", lang="uz-latn", expect="gap",
         q="Krovizni analiz qilgani ochko'rga borish shartmi?",
         want="no preparation instructions exist",
         note="Preparation advice is the single most tempting thing to infer "
              "from general medical knowledge. It does not."),
    dict(id="syn-oculist-days", lang="uz-latn", expect="gap",
         q="Oculist qachon rabochiy den?",
         want="no ophthalmologist on staff",
         note="Uzbek + Russian + English in six words. Nearest neighbour is "
              "another doctor's schedule at 0.701 and it still refuses -- it "
              "did not hand over a different doctor's hours."),
    dict(id="syn-queue-now", lang="uz-latn", expect="gap",
         q="Navbat ko'p durmi hozir?",
         want="no live queue data exists"),
    dict(id="syn-cash-payment", lang="uz-latn", expect="gap",
         q="Nalichka to'lasa bo'ladimi?",
         want="no payment-method fact exists",
         note="Refuses here while live-payment, the same question in different "
              "words, currently fails. Worth comparing when payment is fixed."),
    dict(id="syn-prescription", lang="uz-latn", expect="gap",
         q="Dori yozib beradimi konsultatsiyada?",
         want="no prescription policy exists"),
    dict(id="syn-cashback", lang="uz-latn", expect="gap",
         q="Keshbek bormi kartadan to'lasam?",
         want="no payment or cashback fact exists"),

    # ---- Temporal, 2026-09-01 ---------------------------------------------
    # These grade the ROUTE only, and they have to: the correct WORDING depends
    # on the day the suite runs. "Ertaga ishlaysizmi?" should answer "yes, 09:00
    # to 18:00" on five days a week and "no, we are closed" on Saturday, and a
    # static expected string cannot be both. Retrieval does not see the date, so
    # the retrieved facts are stable even though the answer is not.
    #
    # The day-dependent behaviour is checked two other ways, both cheap:
    # check_time.py verifies the arithmetic offline, and probing with _clock
    # monkeypatched covers Saturday and Sunday without waiting for the weekend.

    dict(id="temporal-tomorrow-uz", lang="uz-latn", expect="fact",
         q="Ertaga ishlaysizmi?",
         want="Shifo Med / ish vaqti",
         note="The failure that started this: the bot once said 'Yakshanba dam "
              "olish kuni, shuning uchun ertaga ishlamaymiz' -- asserting "
              "tomorrow was Sunday with no idea what day it was. TOMORROW is "
              "now computed in code, so the model reads a weekday rather than "
              "inventing one."),
    dict(id="temporal-today-ru", lang="ru", expect="fact",
         q="Сегодня открыты?",
         want="Shifo Med / ish vaqti",
         note="Same mechanism in Russian. The reply names the weekday, so a "
              "wrong clock would be visible rather than silent."),
    dict(id="temporal-day-after-tomorrow", lang="uz-latn", expect="gap",
         q="Indinga ishlaysizmi?",
         want="only today and tomorrow are given; 'indinga' is not derivable",
         note="Must refuse. The clock deliberately stops at tomorrow -- letting "
              "the model count further would swap a hallucinated weekday for an "
              "arithmetic mistake, which is the same defect better disguised."),
    dict(id="temporal-next-tuesday", lang="uz-latn", expect="gap",
         q="Kelasi seshanba ishlaysizmi?",
         want="a relative day beyond tomorrow",
         note="Must refuse for the same reason, and note the contrast with "
              "sunday-uz: a day the customer NAMES outright needs no resolving "
              "and is still answered. It is the relative reference that is "
              "unanswerable, not the weekday."),
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

"""The retrieval test set.

`expect` is what the bot must DO, not what it must say:
    fact       answer from the fact table
    prose      nothing in facts; answer by quoting a chunk
    gap        nothing anywhere; say "I don't know" and log it
    ask-which  ambiguous subject; ask the customer which one, never guess
    triage     a symptom report; `want` names the tier ("acute" / "symptom")
    payment    payment details; `want` is the reply BYTE FOR BYTE. The only
               expectation graded on wording, because the card number is the
               one string the model must never compose.

Grading is on the ROUTE, plus the SCRIPT of the reply. Wording is not graded --
it is printed for a human to read, because "is this a good reply to a customer"
is a judgement.

REWRITTEN 2026-09-02 for Avisena Med, which replaced Shifo Med. What changed:

- The new clinic HOLDS things the old one did not: room numbers, payment
  methods, preparation instructions, an ophthalmologist, an ENT, MRT, Sunday
  hours. Ten questions that were correctly `gap` are now correctly `fact`.
  A `gap` expectation is a claim about the DATA, not about the question, and it
  expires when the data changes.
- Prefixes are provenance and are preserved: `live-` was typed by a real
  customer, `syn-` came from an authored dataset, bare names were written
  before any seed existed. A red run means different things for each.
- Every `want` was checked against the actual subject and attribute names in
  the database before being written here. Guessing them wrong is how the grader
  was wrong four separate times.
"""

CLINIC = "Avisena Med"

QUESTIONS = [
    # ---- Uzbek, Latin script: the default, and most of the traffic ----
    dict(id="hours-uz", lang="uz-latn", expect="fact",
         q="Ish vaqtingiz qanday?",
         want=f"{CLINIC} / ish vaqti"),
    dict(id="sunday-uz", lang="uz-latn", expect="fact",
         q="Yakshanba kuni ishlaysizmi?",
         want=f"{CLINIC} / ish vaqti",
         note="The ANSWER inverted when the clinic changed. Shifo Med was shut "
              "on Sundays; Avisena is open 09:00-14:00 with a duty doctor and "
              "the lab only. Same question, opposite answer, same route -- "
              "which is the argument for grading the route."),
    dict(id="address-uz", lang="uz-latn", expect="fact",
         q="Manzilingiz qayerda?",
         want=f"{CLINIC} / manzil"),
    dict(id="phone-uz", lang="uz-latn", expect="fact",
         q="Telefon raqamingizni ayting",
         want=f"{CLINIC} / telefon",
         note="Three numbers now exist -- call centre, reception mobile and a "
              "24/7 emergency line -- under three different attributes. The "
              "bot must not merge them."),
    dict(id="has-cardiologist-uz", lang="uz-latn", expect="fact",
         q="Kardiolog bormi?",
         want="Rahimov Alisher Bahodirovich / lavozim",
         note="Asks by VALUE, not subject. The value tier makes it answerable."),
    dict(id="price-cardio-uz", lang="uz-latn", expect="fact",
         q="Kardiolog qabuli qancha turadi?",
         want="Rahimov Alisher Bahodirovich / qabul narxi"),
    dict(id="price-uzi-apostrophe", lang="uz-latn", expect="fact",
         q="Qorin boʻshligʻi UZI narxi qancha?",
         want="UZI Qorin bo'shlig'i / narx"),
    dict(id="price-uzi-no-apostrophe", lang="uz-latn", expect="fact",
         q="qorin boshligi uzi narxi",
         want="UZI Qorin bo'shlig'i / narx",
         note="The same question with every apostrophe dropped. normalize() "
              "has to make these one key."),
    dict(id="endocrinologist-uz", lang="uz-latn", expect="fact",
         q="Endokrinolog kim?",
         want="Valiyev Otabek Rustamovich / lavozim",
         note="There are two endocrinologists in effect -- Valiyev, and "
              "Siddiqova who is a gynaecologist-endocrinologist. Only Valiyev "
              "is aliased to the bare word."),
    dict(id="typo-uz", lang="uz-latn", expect="fact",
         q="kardilog narxi qancha",
         want="Rahimov Alisher Bahodirovich / qabul narxi",
         note="Misspelled specialty. Exact matching cannot help; the vector "
              "path has to earn this one."),
    dict(id="room-cardio-uz", lang="uz-latn", expect="fact",
         q="Kardiolog qaysi xonada qabul qiladi?",
         want="Rahimov Alisher Bahodirovich / xona",
         note="NEW. Room numbers did not exist under the old clinic and "
              "syn-which-room was a permanent known-broken case because of it. "
              "The fix was DATA, exactly as recorded in "
              "docs/missing-knowledge.md -- not a fourth prompt rule."),
    dict(id="prep-blood-uz", lang="uz-latn", expect="fact",
         q="Umumiy qon tahliliga qanday tayyorgarlik kerak?",
         want="Umumiy qon tahlili / tayyorgarlik",
         note="NEW. Preparation instructions were the most tempting thing to "
              "infer from general medical knowledge, and the bot correctly "
              "refused for weeks. Now it can answer."),
    dict(id="payment-methods-uz", lang="uz-latn", expect="fact",
         q="Qanday to'lov usullari bor?",
         want=f"{CLINIC} / to'lov usullari",
         note="NEW. The payment cluster was the largest single group in the "
              "gap log."),

    # ---- Uzbek, Cyrillic ----
    dict(id="hours-uz-cyr", lang="uz-cyrl", expect="fact",
         q="Иш вақтингиз қандай?",
         want=f"{CLINIC} / ish vaqti",
         note="Cyrillic question, Latin-stored facts. This is the case that "
              "caught the wrong-script regression when the two retrieval paths "
              "were merged."),
    dict(id="doctor-uz-cyr", lang="uz-cyrl", expect="ask-which",
         q="Каримов қайси кунлари қабул қилади?",
         want="two doctors named Karimov -> ask which, never guess",
         note="Was a plain fact question under the old clinic. It is now the "
              "ambiguity case as well, and in Cyrillic."),
    dict(id="price-uz-cyr", lang="uz-cyrl", expect="fact",
         q="Кардиолог қабули қанча туради?",
         want="Rahimov Alisher Bahodirovich / qabul narxi"),

    # ---- Russian ----
    dict(id="hours-ru", lang="ru", expect="fact",
         q="Во сколько вы работаете?",
         want=f"{CLINIC} / ish vaqti"),
    dict(id="price-ru", lang="ru", expect="fact",
         q="Сколько стоит приём кардиолога?",
         want="Rahimov Alisher Bahodirovich / qabul narxi"),
    dict(id="address-ru", lang="ru", expect="fact",
         q="Где вы находитесь?",
         want=f"{CLINIC} / manzil",
         note="Scored 0.644 under the old data and was REJECTED by a 0.65 "
              "floor. The evidence that the floor is a cost filter, not a "
              "correctness gate."),
    dict(id="doctors-ru", lang="ru", expect="fact",
         q="Какие врачи у вас есть?",
         want="Rahimov Alisher Bahodirovich / lavozim",
         note="A LIST question, now over 13 doctors rather than 6. Ranking "
              "cannot answer 'all of them' at any window size -- attribute "
              "expansion is what makes this work, and 13 is a harder test of "
              "it than 6 was."),
    dict(id="lor-ru", lang="ru", expect="fact",
         q="ЛОР принимает в субботу?",
         want="Nurmatova Ziyoda Anvarovna / qabul vaqti",
         note="NEW. 'Jonsarak aka (LOR) qachon keladila?' was asked live and "
              "refused, because no ENT existed. Her Saturday hours are stated "
              "separately inside one schedule string."),
    dict(id="payment-humo-ru", lang="ru", expect="fact",
         q="Вы принимаете карту Humo?",
         want=f"{CLINIC} / to'lov usullari",
         note="Was `gap` and correct as one. The data changed, so the "
              "expectation did."),

    # ---- Mixed / code-switched ----
    dict(id="mixed-price", lang="mixed", expect="fact",
         q="Kardiolog priyom skolko stoit?",
         want="Rahimov Alisher Bahodirovich / qabul narxi"),
    dict(id="mixed-doctor", lang="mixed", expect="ask-which",
         q="Dr. Karimov qachon ishlaydi?",
         want="two doctors named Karimov -> ask which, never guess",
         note="'Dr. Karimov' is an alias on BOTH Karimovs, so ambiguity has to "
              "fire through the aliased form too, not only the bare surname."),

    # ---- Ambiguity: one name, two people ----
    dict(id="ambiguous-karimov", lang="uz-latn", expect="ask-which",
         q="Karimov qachon qabul qiladi?",
         want="two doctors named Karimov -> ask which, never guess",
         note="REBUILT 2026-09-02. All 12 real surnames in the new data are "
              "unique, so this behaviour lost its only test when the clinic "
              "changed. seed.py adds one synthetic doctor sharing a surname to "
              "restore it. A behaviour should stop being covered because "
              "someone decided to stop covering it, not because a data change "
              "quietly removed the case."),

    # ---- Prose: restored 2026-09-03 when the policy source was ingested ----
    dict(id="what-to-bring-uz", lang="uz-latn", expect="prose",
         q="Qabulga nima olib kelish kerak?",
         want="the policy passage about documents to bring",
         note="Moved to `gap` on 2026-09-02 when the Avisena export turned out "
              "to contain no prose, then back to `prose` on 2026-09-03 once the "
              "policy document was ingested. The expectation tracks the DATA, "
              "not the question. "
              "REQUIRES the policy source 'Bemorlar uchun qoidalar' "
              "(data/avisena_policy.txt). seed.py TRUNCATES source on "
              "every run, so a reseed empties the chunk table and turns "
              "this into a silent failure that looks like a retrieval "
              "bug. check_answer.py fails the whole run rather than "
              "letting that happen -- see PROSE_SOURCE there."),
    dict(id="children-ru", lang="ru", expect="prose",
         q="Можно прийти с ребёнком?",
         want="the policy passage about children and guardians",
         note="A RUSSIAN question against an UZBEK passage. The reply cannot be "
              "verbatim -- rule 5 (answer in the customer's language) beats "
              "rule 6 (keep the document's wording), so this one is answered by "
              "translation. Measured 2026-09-02. "
              "REQUIRES the policy source 'Bemorlar uchun qoidalar' "
              "(data/avisena_policy.txt). seed.py TRUNCATES source on "
              "every run, so a reseed empties the chunk table and turns "
              "this into a silent failure that looks like a retrieval "
              "bug. check_answer.py fails the whole run rather than "
              "letting that happen -- see PROSE_SOURCE there."),
    dict(id="live-passport-needed", lang="uz-latn", expect="prose",
         q="Doxtorga borishda pasport maskat keremi yoki shundo borsa boloradimi?",
         want="the policy passage about documents to bring",
         note="A real customer asked this. "
              "REQUIRES the policy source 'Bemorlar uchun qoidalar' "
              "(data/avisena_policy.txt). seed.py TRUNCATES source on "
              "every run, so a reseed empties the chunk table and turns "
              "this into a silent failure that looks like a retrieval "
              "bug. check_answer.py fails the whole run rather than "
              "letting that happen -- see PROSE_SOURCE there."),
    dict(id="syn-what-to-bring", lang="uz-latn", expect="prose",
         q="O'zim bilan nima olishim kerak dur?",
         want="the policy passage about documents to bring",
         note="Tashkent dialect suffix '-dur'. "
              "REQUIRES the policy source 'Bemorlar uchun qoidalar' "
              "(data/avisena_policy.txt). seed.py TRUNCATES source on "
              "every run, so a reseed empties the chunk table and turns "
              "this into a silent failure that looks like a retrieval "
              "bug. check_answer.py fails the whole run rather than "
              "letting that happen -- see PROSE_SOURCE there."),

    # ---- Payment details: the string must never be composed ----
    #
    # `want` is the LITERAL expected reply, byte for byte, and that is the
    # whole point of these three. Building the expectation by calling
    # payment.message() would compare code-built output to a code-built
    # expectation, and would still pass if the answer path quietly started
    # asking the model -- which is the one failure these exist to catch.
    #
    # The maintenance cost is real: change the seeded card number and these
    # three lines must change with it. That friction is the feature. A test
    # that keeps passing while the thing it guards is rewritten is not a test.
    #
    # Note what is identical across all three: everything below the first line.
    # Only the owner's instruction changes with the language. Language
    # detection is 53/53 on the test set and that is not the same as perfect,
    # so the card block does not depend on it.
    dict(id="payment-card-uz", lang="uz-latn", expect="payment",
         q="Karta raqamingiz nima?",
         want="Toʻlovni quyidagi kartaga amalga oshiring:\n"
              "8600 0000 0000 0000\nAVISENA MED\nKapitalbank"),
    dict(id="payment-card-uz-cyrl", lang="uz-cyrl", expect="payment",
         q="Карта рақамингизни юборинг",
         want="Тўловни қуйидаги картага амалга оширинг:\n"
              "8600 0000 0000 0000\nAVISENA MED\nKapitalbank"),
    dict(id="payment-card-ru", lang="ru", expect="payment",
         q="Скажите номер карты для оплаты",
         want="Оплату можно произвести на следующую карту:\n"
              "8600 0000 0000 0000\nAVISENA MED\nKapitalbank"),

    # ---- Boundaries ----
    dict(id="gap-appointment", lang="uz-latn", expect="gap",
         q="Onlayn navbatga yozilsa boʻladimi?",
         want="no booking procedure exists"),
    dict(id="boundary-close-en", lang="en", expect="fact",
         q="What time do you close?",
         want=f"{CLINIC} / ish vaqti"),
    dict(id="adversarial-closing-uz", lang="uz-latn", expect="fact",
         q="Nechida yopilasiz?",
         want=f"{CLINIC} / ish vaqti",
         note="CHANGED from expect=gap. It was a gap because the old clinic's "
              "closing time was only inferable; the hours are explicit now, so "
              "refusing would be over-refusal rather than caution. Expected to "
              "be hard -- this terse phrasing has missed before."),
    dict(id="mri-price-ru", lang="ru", expect="fact",
         q="Сколько стоит МРТ?",
         want="MRT bosh miya / narx",
         note="Was `gap`. Avisena refers MRT to a partner clinic but states "
              "the price, so it is answerable."),

    # ---- Real customer messages ----
    dict(id="live-doctor-list-uz", lang="uz-latn", expect="fact",
         q="doktorlar listini beraszmi",
         want="Rahimov Alisher Bahodirovich / lavozim",
         note="Named two of six doctors confidently under the old data. 13 now."),
    dict(id="live-uzi-hours-uz", lang="uz-latn", expect="fact",
         q="uzi qachon ochiq boladi",
         want=f"{CLINIC} / ish vaqti",
         note="'uzi' means both UZI and 'itself' in Uzbek. A 3-wide window "
              "once retrieved three UZI PRICES and refused while the answer "
              "sat at rank 5. No dedicated UZI doctor exists now, so the "
              "clinic's hours are the answer."),
    dict(id="live-kardiolog-ochered", lang="uz-latn", expect="fact",
         q="Kardiologga ochered bormi bugunga? Qachon borsa boladi?",
         want="Rahimov Alisher Bahodirovich / qabul vaqti",
         note="Two clauses: a queue question we cannot answer and a schedule "
              "question we can. Answering the second and admitting the first "
              "is the correct shape."),
    dict(id="live-reschedule", lang="uz-latn", expect="gap",
         q="Ertaga palonchi doxtorga yoziludim, vaxtini sal keginroqa sursa boladimi?",
         want="no booking or rescheduling data exists"),
    dict(id="live-how-to-book", lang="uz-latn", expect="gap",
         q="Assalomualaykum, ozi qabulga qanaqa yoziladi, tel qilsh kerakmi?",
         want="no booking procedure exists",
         note="Invented a booking procedure around a real phone number for "
              "weeks. Rule 10 fixed it: a phone number in the context is a "
              "phone number, not an instruction to call in order to book."),
    dict(id="live-lor", lang="uz-latn", expect="fact",
         q="Jonsarak aka (LOR) qachon keladila? Shanbayam ishlidilarmi?",
         want="Nurmatova Ziyoda Anvarovna / qabul vaqti",
         note="Was `gap` -- no ENT existed. The question in the gap log that "
              "the new clinic answers most directly."),
    dict(id="live-uzi-price", lang="uz-latn", expect="fact",
         q="Uzi tushish qancha bo boti hozi? Narxini etvorila.",
         want="UZI Qorin bo'shlig'i / narx",
         note="Two UZI services exist -- abdominal and pelvic -- and neither is "
              "named. Enumerating beats picking."),
    dict(id="live-blood-discount", lang="uz-latn", expect="fact",
         q="Qon analizi jami qancha bopti, klikdan tasi bomasmi?",
         want="Umumiy qon tahlili / narx",
         note="The price is answerable; the discount half is not, and must be "
              "admitted rather than invented."),
    dict(id="live-payment", lang="uz-latn", expect="gap",
         q="Kandisiyami nma balosi boru, ushanga to'lasa boladimi silada?",
         want="a medical condition and its treatment cost; neither is held",
         note="Payment METHODS are known now, which is not the same as knowing "
              "whether a condition is treated or what it costs. The tempting "
              "wrong move is to answer the payment half and imply the rest."),
    dict(id="live-consultation-price", lang="uz-latn", expect="fact",
         q="Konsultatsiyani ozi qancha? Doxtor korgani alohida pulmi?",
         want="Rahimov Alisher Bahodirovich / qabul narxi",
         note="Consultation prices vary by doctor, 120 000 to 220 000. A "
              "separate follow-up price exists per doctor, which is exactly "
              "what the second clause asks about."),
    dict(id="live-results-delivery", lang="uz-latn", expect="fact",
         q="Analiz javobi chgandor? Telegramdan tashavoraslami yoki borish kereymi?",
         want="Umumiy qon tahlili / tayyor boʻlish muddati",
         note="A Telegram bot handle IS held now. Whether results are SENT "
              "through it is not stated, and rule 10 forbids inferring it."),
    dict(id="live-mrt-fasting", lang="uz-latn", expect="gap",
         q="Mrt ga tushishdan oldin choy poy ichsa buraveradimi yoki och qoringa borish kere?",
         want="MRT preparation is stated, but says nothing about fasting",
         note="Sharp case. MRT preparation EXISTS -- 'no metal implants' -- and "
              "says nothing about eating or drinking. A preparation "
              "instruction that does not answer the preparation question "
              "asked is the adjacent-fact trap at its most convincing."),
    dict(id="live-blood-results-when", lang="uz-latn", expect="fact",
         q="Ertalabdan topshirgan qonimni otveti qachon chiqadi aka?",
         want="Umumiy qon tahlili / tayyor boʻlish muddati"),
    dict(id="live-sunday-address-landmark", lang="uz-latn", expect="fact",
         q="Yakshanbayam ochiqmisila? Ozi qatda joylashgansila, mojal bormi biror bir?",
         want=f"{CLINIC} / manzil",
         note="THREE clauses. Told a customer it did not know its own address "
              "for weeks, because exact matching fired on the Sunday clause "
              "and ended the search. Fixed by merging both retrieval paths."),
    dict(id="live-closing-time", lang="uz-latn", expect="fact",
         q="Klinika soat nechgacha ishlidi? Ishdan kegin borsam ulguramanmi?",
         want=f"{CLINIC} / ish vaqti"),
    dict(id="live-pediatrician", lang="uz-latn", expect="fact",
         q="Detiskiy shifokor bormi silada kichkina bollar uchun?",
         want="Usmonov Timur Karimovich / lavozim",
         note="'detiskiy' is misspelled AND 'detskiy vrach' is not an alias -- "
              "the source gave only 'Pediatr', 'Bolalar shifokori' and "
              "'Педиатр'. A live miss found by the supplied qa_pairs, not by "
              "the harness."),
    dict(id="live-child-emergency", lang="uz-latn", expect="triage",
         q="Bolami isitmasi chiqib qusopti, tez yordamila bormi silani yordam beradigan?",
         want="acute",
         note="Fires acute on 'tez yordam'. Names a service AND is acute, so "
              "it proves acute overrides the named-question rule."),

    # ---- Triage: the nine cases, encoded ----
    dict(id="triage-abdominal-uz", lang="uz-latn", expect="triage",
         q="Xotinimni qorni og'riyapdi.",
         want="symptom",
         note="The failure triage was built for: answered with an abdominal "
              "UZI price and a gynaecologist's fee. No question is asked, so "
              "any answer requires the BOT to choose what to offer."),
    dict(id="triage-throat-uz", lang="uz-latn", expect="triage",
         q="Bolamni gorlosida shamollash bor.",
         want="symptom"),
    dict(id="triage-breathing-uz", lang="uz-latn", expect="triage",
         q="Duxim yetmayapdi nafas olishga.",
         want="acute"),
    dict(id="triage-abdominal-ru", lang="ru", expect="triage",
         q="У меня сильно болит живот",
         want="symptom",
         note="Russian. normalize() folds the markers to Latin, so this "
              "exercises that path."),
    dict(id="triage-named-lor", lang="uz-latn", expect="fact",
         q="Ukamni qulog'i og'riyapti, lor xonasi nechanchi etajda?",
         want="Nurmatova Ziyoda Anvarovna / xona",
         note="A symptom report that CARRIES a named question. Refusing the "
              "room number is the same failure as a multi-part question "
              "dropping a clause. The customer named the ENT, so answering is "
              "answering rather than the bot choosing."),
    dict(id="triage-named-neuro", lang="uz-latn", expect="fact",
         q="Golova qattiq ogriyapti, nevropatolog qaysi xonada?",
         want="Qosimova Lola Sur'atovna / xona"),
    dict(id="triage-named-pediatr-price", lang="uz-latn", expect="fact",
         q="Bolamni tomogi ogriyapti, pediatr qabuli qancha?",
         want="Usmonov Timur Karimovich / qabul narxi",
         note="A PRICE answered to a symptom report, which looks like the "
              "original failure and is not: the customer named the "
              "paediatrician. Authorship of the topic is the distinguishing "
              "feature, not the kind of fact."),
    dict(id="triage-named-uzi-price", lang="uz-latn", expect="fact",
         q="Xotinimni qorni og'riyapdi, UZI qancha turadi?",
         want="UZI Qorin bo'shlig'i / narx",
         note="KNOWN BROKEN, and mislabelling it would send someone at the "
              "wrong fix. This is an ALIAS GAP, not a triage bug: the exact "
              "tier returns not_found because no bare 'UZI' alias exists, so "
              "the message reads as a pure symptom report and is refused. The "
              "failure direction is safe -- over-refusal. It must NOT be fixed "
              "by adding a bare 'UZI' alias: 'uzi' also means 'itself' in "
              "Uzbek, and that ambiguity was a live failure in the old data."),
    dict(id="triage-control-no-symptom", lang="uz-latn", expect="fact",
         q="Kardiolog qaysi xonada qabul qiladi?",
         want="Rahimov Alisher Bahodirovich / xona",
         note="Control: the same shape with no symptom word. Proves triage is "
              "not firing on the question form itself. Duplicates "
              "room-cardio-uz deliberately -- one belongs to the fact suite, "
              "one to the triage suite, and they would be deleted for "
              "different reasons."),

    # ---- Authored dataset ----
    dict(id="syn-tomorrow-slot", lang="uz-latn", expect="gap",
         q="Ertaga vrachda bo'sh vaqt bormi?",
         want="no appointment-availability data exists",
         note="Began answering once the prompt was given a date -- 'tomorrow, "
              "Wednesday, our doctors' hours vary, tell us which doctor' -- "
              "implying it could check availability. Rule 9: being able to "
              "name the day is not permission to answer a different question "
              "about it."),
    dict(id="syn-book-gynae", lang="uz-latn", expect="gap",
         q="Menga ginekologga zapis qberila.",
         want="no booking procedure exists",
         note="KNOWN BROKEN. Replies 'call this number to book', which "
              "live-how-to-book is graded as failing for. Rule 10 fixed that "
              "one and this phrasing still slips through -- the imperative "
              "form appears to read as a request to act rather than a question "
              "about procedure. Useful information about where rule 10's "
              "coverage ends."),
    dict(id="syn-sanepid", lang="uz-latn", expect="gap",
         q="Klinikayla sanepidda tekshiruvdan o'tganmi?",
         want="no certification data exists"),
    dict(id="syn-womens-doctor", lang="uz-latn", expect="fact",
         q="Jenskiy vrachiz qachon ishga chiqadi?",
         want="Siddiqova Nilufar Erkinovna / qabul vaqti",
         note="'jenskiy vrach' -> ginekolog across languages."),
    dict(id="syn-results-ready", lang="uz-latn", expect="fact",
         q="Analiz natijasi qachon gotov bo'ladi?",
         want="Umumiy qon tahlili / tayyor boʻlish muddati",
         note="Five tests now have DIFFERENT turnaround times, from one hour "
              "to one working day. Answering with one as though it covered all "
              "is the failure to watch for."),
    dict(id="syn-inpatient-price", lang="uz-latn", expect="gap",
         q="Krovotga yotish narxi nech pul?",
         want="no inpatient service exists"),
    dict(id="syn-queue-cardio", lang="uz-latn", expect="gap",
         q="Kardiologga ochered bormi xozir?",
         want="no live queue data exists",
         note="Real-time state, not knowledge. Not solvable by adding a fact."),
    dict(id="syn-lunch-break", lang="uz-latn", expect="gap",
         q="Klinika obetda ishliydimi?",
         want="no lunch-break fact exists",
         note="Asserted the clinic works 'tanaffussiz' -- without a break -- "
              "from nothing. The case that produced rule 10: absence of a fact "
              "is not evidence of its opposite."),
    dict(id="syn-pediatr-definition", lang="uz-latn", expect="gap",
         q="Pediatr dejskiy vrachmi?",
         want="a definitional question; the context holds only a job title",
         note="KNOWN BROKEN, and deliberately graded strictly. The 'ha' comes "
              "from world knowledge, not context. Harmless here; the same leak "
              "on 'is this drug safe' is not. If it proves impossible to fix "
              "without breaking useful answers, that is a tradeoff to record, "
              "not a reason to reclassify the case."),
    dict(id="syn-certificate-tomorrow", lang="uz-latn", expect="gap",
         q="Ertagaga spravka berishadimi?",
         want="no certificate service exists"),
    dict(id="syn-bp-price", lang="uz-latn", expect="gap",
         q="Davlenniya o'lchash nech pul?",
         want="no blood-pressure-measurement price exists",
         note="An authored expectation elsewhere claimed this was free. "
              "Inventing a price of zero is still inventing a price."),
    dict(id="syn-kidney-uzi", lang="uz-latn", expect="gap",
         q="Pochka UZI qilish kerek edi.",
         want="abdominal and pelvic UZI exist; kidney does not",
         note="Same-category near miss. The hard case for refusal."),
    dict(id="syn-injection-nurse", lang="uz-latn", expect="gap",
         q="Ukól qiladigan feldsher bormi?",
         want="no procedure-room or nursing service exists"),
    dict(id="syn-where-located", lang="uz-latn", expect="fact",
         q="Klinikayla qay yerda joylashgan?",
         want=f"{CLINIC} / manzil"),
    dict(id="syn-fluorography-results", lang="uz-latn", expect="gap",
         q="Fluorografiya otveti qachon chiqadi?",
         want="the X-ray has a price, a room and preparation, but no turnaround",
         note="Sharper than it was. Four other tests DO state a turnaround, so "
              "the pull to generalise from them is stronger."),
    dict(id="syn-form-086", lang="uz-latn", expect="gap",
         q="Sizlarda spravka 086 beriladimi?",
         want="no certificate service exists"),
    dict(id="syn-mrt-discount", lang="uz-latn", expect="gap",
         q="MRTga skidka bormi xozir?",
         want="MRT exists and is priced; no discount data exists",
         note="Was two inventions in one question; now exactly one. Tests "
              "answering half and refusing half."),
    dict(id="syn-chief-doctor", lang="uz-latn", expect="gap",
         q="Glavniy vrach priyomiga qanaqa yozilsa bo'ladi?",
         want="no chief doctor and no booking procedure exist"),
    dict(id="syn-fasting-blood", lang="uz-latn", expect="fact",
         q="Krovizni analiz qilgani ochko'rga borish shartmi?",
         want="Umumiy qon tahlili / tayyorgarlik",
         note="Was `gap` and correctly refused for weeks. Preparation "
              "instructions exist now, so refusing would be the failure."),
    dict(id="syn-oculist-days", lang="uz-latn", expect="fact",
         q="Oculist qachon rabochiy den?",
         want="Toshmatov Jamshid Alimovich / qabul vaqti",
         note="Uzbek plus Russian plus English in six words. Was `gap` -- no "
              "ophthalmologist existed. 'Okulist' and 'Glaznoy' are aliases "
              "now because the source packed them into brackets."),
    dict(id="syn-queue-now", lang="uz-latn", expect="gap",
         q="Navbat ko'p durmi hozir?",
         want="no live queue data exists"),
    dict(id="syn-cash-payment", lang="uz-latn", expect="fact",
         q="Nalichka to'lasa bo'ladimi?",
         want=f"{CLINIC} / to'lov usullari",
         note="Was `gap`. Cash is listed first in the payment methods."),
    dict(id="syn-prescription", lang="uz-latn", expect="gap",
         q="Dori yozib beradimi konsultatsiyada?",
         want="no prescription policy exists"),
    dict(id="syn-cashback", lang="uz-latn", expect="gap",
         q="Keshbek bormi kartadan to'lasam?",
         want="payment methods exist; cashback does not",
         note="Card payment IS held and cashback is NOT. The adjacent-fact "
              "trap in its cleanest form."),

    # ---- Dates ----
    dict(id="temporal-tomorrow-uz", lang="uz-latn", expect="fact",
         q="Ertaga ishlaysizmi?",
         want=f"{CLINIC} / ish vaqti",
         note="Once said 'Sunday is our rest day, so we are closed tomorrow' "
              "with no idea what day it was. Harder now: the clinic is open "
              "SEVEN days on three different schedules, so the answer changes "
              "shape by weekday rather than being yes/no."),
    dict(id="temporal-today-ru", lang="ru", expect="fact",
         q="Сегодня открыты?",
         want=f"{CLINIC} / ish vaqti"),
    dict(id="temporal-day-after-tomorrow", lang="uz-latn", expect="gap",
         q="Indinga ishlaysizmi?",
         want="only today and tomorrow are given; 'indinga' is not derivable",
         note="The clock stops at tomorrow on purpose. Widening it hands "
              "arithmetic back to the model."),
    dict(id="temporal-next-tuesday", lang="uz-latn", expect="fact",
         q="Kelasi seshanba ishlaysizmi?",
         want=f"{CLINIC} / ish vaqti",
         note="CHANGED from gap. 'Kelasi seshanba' NAMES Tuesday, and a named "
              "day needs no resolving. The holiday objection applies equally "
              "to tomorrow, which we answer -- a general limitation of holding "
              "no holiday data, not a difference between the cases."),
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

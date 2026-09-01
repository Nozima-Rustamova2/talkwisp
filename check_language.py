"""Language detection, measured. Costs nothing -- no API, no database.

    uv run python check_language.py

WHY THIS EXISTS. The three `uz-cyrl` questions in questions.py all contain қ or
ў, so the detector scored 10/10 on the labelled set while being broken for a
whole class of real messages. A test set that cannot express the failure cannot
report it -- the same saturation problem the 30-question set had.

THE FAILING CLASS. Uzbek Cyrillic needs ў ғ қ ҳ, and none of those four are on
a standard Russian keyboard layout. Casual typists substitute у г к х. So the
detector keys on precisely the characters that vanish in informal phone typing,
which is most of the traffic. Every case below marked `no-special` is Uzbek
Cyrillic written the way people actually type it.

PROVENANCE: authored, not harvested -- same caveat as the `syn-` questions.
These are the shapes the failure takes, written to be answerable by eye. Replace
them with real Cyrillic traffic when there is some.
"""

import sys

from app.answer import detect_language

sys.stdout.reconfigure(encoding="utf-8")

UZ = "Uzbek, in CYRILLIC script"
RU = "Russian"

# (text, expected, tag)
CASES = [
    # --- Uzbek Cyrillic WITH the distinguishing letters: the easy half -------
    ("Иш вақтингиз қандай?", UZ, "special"),
    ("Кардиолог қабули қанча туради?", UZ, "special"),
    ("Каримов қайси кунлари қабул қилади?", UZ, "special"),
    ("Тўлов қандай амалга оширилади?", UZ, "special"),
    ("Врач билан гаплашсам бўладими?", UZ, "special"),

    # --- Uzbek Cyrillic WITHOUT them: the class the detector cannot see ------
    ("Хотинимни корни огрияпти", UZ, "no-special"),
    ("Манзилингиз каерда?", UZ, "no-special"),
    ("Эртага ишлайсизларми?", UZ, "no-special"),
    ("Нархлари канча экан?", UZ, "no-special"),
    ("Доктор соат нечида келади?", UZ, "no-special"),
    ("Педиатр бормиди бугун?", UZ, "no-special"),
    ("Болам иситмаси чикиб кетди", UZ, "no-special"),
    ("Анализ натижаси качон тайёр булади?", UZ, "no-special"),
    ("Мен ертага борсам буладими?", UZ, "no-special"),
    ("Телефон раками нечта сизларни?", UZ, "no-special"),
    ("Гинеколог шанба куни ишлайдими?", UZ, "no-special"),
    ("Ушбу текширув учун навбат олиш керакми?", UZ, "no-special"),
    ("Ичим огриб турибди аллакачондан бери", UZ, "no-special"),

    # --- Russian ------------------------------------------------------------
    ("Во сколько вы работаете?", RU, "ru"),
    ("Сколько стоит приём кардиолога?", RU, "ru"),
    ("Где вы находитесь?", RU, "ru"),
    ("Какие врачи у вас есть?", RU, "ru"),
    ("Можно прийти с ребёнком?", RU, "ru"),
    ("Вы принимаете карту Humo?", RU, "ru"),
    ("Сколько стоит МРТ?", RU, "ru"),
    ("У меня сильно болит живот", RU, "ru"),
    ("Задыхаюсь, не могу дышать", RU, "ru"),
    ("Мне нужно записаться к гинекологу", RU, "ru"),
    ("Анализы готовы уже?", RU, "ru"),
    ("Работаете ли вы в воскресенье?", RU, "ru"),
    ("Есть ли у вас детский врач?", RU, "ru"),
    ("Какой адрес вашей клиники?", RU, "ru"),
    ("Можно оплатить наличными?", RU, "ru"),

    # --- Adversarial: written to BREAK the classifier, not to pass ----------
    # Added after the first rewrite scored 36/36 on the cases above -- which it
    # would, having been tuned against them. These were chosen to fail, and
    # three of them did: short Russian with no function word and no
    # distinctive ending scored zero on both sides and fell through the tie to
    # Uzbek. That is what the Russian imperative/greeting vocabulary is for.
    ("Дайте адрес", RU, "adversarial"),
    ("Адрес?", RU, "adversarial"),
    ("Нужен педиатр", RU, "adversarial"),
    ("Клиника закрыта", RU, "adversarial"),
    ("Спасибо большое", RU, "adversarial"),
    ("Приём стоит дорого", RU, "adversarial"),
    # Russian instrumental plurals end -ми, which is also the Uzbek question
    # particle. The word lists are what break this tie, not the suffixes.
    ("Можно с детьми?", RU, "adversarial"),
    ("Я говорил с врачами", RU, "adversarial"),
    # Loanwords shared by both languages must carry no signal. Listing "врач"
    # as Russian read "Врач качон келади?" as Russian -- the reason it is not
    # in _RUSSIAN_WORDS.
    ("Врач качон келади?", UZ, "adversarial"),
    ("Врач борми?", UZ, "adversarial"),
    ("Врачингиз ким?", UZ, "adversarial"),
    ("Анализ качон готов булади?", UZ, "adversarial"),
    # Code-switched: Uzbek subject, Russian question. The question is what the
    # customer wants answered, so Russian is right.
    ("Кардиолог қабули сколько стоит?", RU, "adversarial"),
    # Single words, where there is genuinely almost no evidence either way.
    ("Канча?", UZ, "adversarial"),
    ("Рахмат", UZ, "adversarial"),
    ("Педиатр керакми?", UZ, "adversarial"),
    ("Бугун ишлайсизми?", UZ, "adversarial"),

    # --- Latin script: must stay untouched ----------------------------------
    ("Ish vaqtingiz qanday?", None, "latin"),
    ("Kardiolog qabuli qancha turadi?", None, "latin"),
    ("Do you have a cardiologist?", None, "latin"),
]

LATIN = "the same language the customer wrote in, in LATIN script"

by_tag: dict[str, list[int]] = {}
for text, want, tag in CASES:
    want = want if want is not None else LATIN
    got = detect_language(text)
    ok = got == want
    by_tag.setdefault(tag, []).append(ok)
    if not ok:
        print(f"  WRONG [{tag:10}] {text}")
        print(f"{'':21} want {want}")
        print(f"{'':21} got  {got}")

print()
total_ok = 0
for tag, results in by_tag.items():
    ok, n = sum(results), len(results)
    total_ok += ok
    print(f"  {tag:10} {ok}/{n}")
print(f"\n  {'TOTAL':10} {total_ok}/{len(CASES)}")
sys.exit(0 if total_ok == len(CASES) else 1)

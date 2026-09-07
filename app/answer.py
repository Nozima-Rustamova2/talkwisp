"""Answer a question, or refuse to. The one rule: never speak without context.

Order, per the design decisions:

    alias / exact match  ->  vector search over facts AND chunks
                         ->  NO_ANSWER + a logged gap

Exact matching stays first: it is cheaper, more precise, and it is the language
moat. Vector search is the fallback, never the replacement.

The model is called only when there is retrieved context to give it. When there
is none, the code does not reach the model at all -- refusing is a branch in the
program, not a behaviour we hope the model chooses. That short-circuit is the
whole safety property; everything else here is presentation.
"""

import datetime
import json
import pathlib
import re
import zoneinfo

from psycopg import Connection

from app.db import current_business_id
from app.embeddings import embed_query
from app.llm import complete
from app import payment
from app.retrieval import _fact_row, find
from app.triage import medical_lead
from app.triage import reply as triage_reply
from app.triage import triage

# A COST PRE-FILTER, NOT A CORRECTNESS GATE. Refusal is decided downstream by
# the NO_ANSWER marker; this only limits how much context reaches the prompt.
#
# Set low deliberately, and the asymmetry is the whole reason: a false positive
# is caught by NO_ANSWER, but a false negative is discarded before the model
# ever sees it and there is no second chance. Evidence from the 25-question set
# at the old 0.65: an unanswerable question was ADMITTED at 0.691 while an
# answerable one ("Где вы находитесь?" -> Shifo Med / manzil) was REJECTED at
# 0.644. The floor errs in both directions, so it cannot be the gate.
#
# Do not raise this to "reduce noise". Noise is the model's problem; recall is
# not recoverable. See the Retrieval section of docs/design-decisions.md.
SIMILARITY_FLOOR = 0.55

# How many facts the vector fallback puts in front of the model.
#
# Was 3, and that was the root cause of two live failures on the same day.
# "uzi qachon ochiq boladi" retrieved three UZI *prices* and refused, while the
# fact that answered it (the clinic's hours) sat at rank 5. Widening is safe
# only because rule 1b makes the model refuse on merely-adjacent context --
# without that, more context would mean more confident wrong answers.
# Raised from 8 to 12 on 2026-09-05, measured rather than guessed. Of the 26
# questions the exact tier does not already answer, a window of 8 reaches 20 of
# them and 12 reaches 24 -- for 2.4 more facts of context on average. 16 buys
# nothing over 12; 20 buys one more question for another 5.6 facts. See
# check_window.py, which computes this from cached question vectors with no API
# calls. Confirmed by two matched harness runs on the same 90 questions:
# 74/16 at width 8, 80/10 at width 12, six changes and all of them gains. On
# the gap questions -- the direction retrieval-only measurement is blind to --
# wrong answers went DOWN, 6 to 4: more context made the model refuse more
# rather than less, because twelve facts make it visible that none of them
# answers the question where eight adjacent ones look like they must.
#
# The measured COST is not in the verdicts. Replies to list-shaped questions
# get summarised harder, because rule 7 asks for two sentences and there is now
# more to compress: "which doctors do you have" named twelve at width 8 and
# five at width 12. Route grading scores that as an improvement. It is not one.
# See docs/design-decisions.md.
#
# NOT a free parameter. Widening changes which attribute _expand_list clusters
# on, so recall is not monotonic in width -- read the coupling note in
# docs/design-decisions.md before moving it again.
FACT_WINDOW = 12

# How many retrieved facts must share an attribute before we treat the question
# as being about the whole set rather than the top few. See _expand_list().
LIST_CLUSTER = 3

# Not a table. A gap is an observation, not business knowledge, and the four
# table rule binds the knowledge model -- see docs/design-decisions.md. A file
# is enough to tune the floor and to see what customers ask that we cannot
# answer. Promote it to a table when it is proven useful, and ask first.
GAP_LOG = pathlib.Path(__file__).parent.parent / "gaps.jsonl"

# The marker matters: it turns "I don't know" back into a branch in the program.
# Without it, a refusal is just prose, indistinguishable from an answer, and the
# gap never gets logged.
NO_ANSWER = "NO_ANSWER"

_SYSTEM = f"""You answer customer questions on behalf of a business, using ONLY the context given to you.

The context is terse database rows or passages from the business's own documents. Never echo that format back: no field labels, no slashes, no quotation marks wrapped around the whole reply. Write the one sentence a person would actually send in a chat.

Rules, in order of importance:
1. If the context contains the answer, GIVE IT. Do not refuse when the answer is sitting in front of you.
1b. The context is retrieved by similarity, so it often contains material that is merely RELATED to the question rather than an answer to it. Before answering, check that the context is about the specific thing asked. Related is not the same as answering: if someone asks what time the clinic closes and the context only holds individual doctors' consultation hours, that is not the answer -- reply {NO_ANSWER}. Answering a question with adjacent facts is worse than admitting you do not know. This includes a DIFFERENT PROPERTY OF THE RIGHT THING: where the clinic is located is not the answer to which room a doctor sits in. Being about the same subject is not the same as answering the question asked about it. This does NOT mean refusing when the answer is genuinely contained in the fact -- a closing time is part of stated opening hours, and should be given.
2. If the context does not contain the answer, reply with exactly {NO_ANSWER} on the first line, then one short sentence in the customer's language telling them to contact the business directly. Never guess, and never use anything you know from outside the context.
3. Never invent or adjust a price, a time, a phone number or a person's name. Use them exactly as the context gives them.
4. Prices are approximate ranges. Present them as ranges, never as an exact price.
5. The language to reply in is stated on the LAST line of every message as REPLY IN. It is last because it must beat everything above it. Obey it exactly. The context is often stored in a different language from the question -- never let the context's language decide your reply's language.
6. When the context is a passage from a document, keep its wording rather than rewriting it. Paraphrasing is how details drift.
7. Be brief -- one or two sentences. This is a chat message, not a document.
8. TODAY and TOMORROW are stated at the top of every message. They are the ONLY dates you know. Use them solely to resolve the words "today" and "tomorrow" in the question. Never state the weekday of any other date, never count days forward or backward, and never name a date that is not written above. A day the customer names outright ("Sunday", "yakshanba", "в воскресенье") needs no resolving -- answer it from the context as usual. But a day referred to only relatively and not covered by the two lines -- "the day after tomorrow", "next Tuesday", "in three days" -- you cannot work out, so reply {NO_ANSWER}.
9. Knowing what day it is does not tell you the business is open. Opening hours and the rest day come from the context like every other fact; the date lines only tell you WHICH day the customer means. A stated range of working days DOES answer for days outside it: if the context says the business works Monday to Saturday, then Sunday is closed, and you should say so rather than refuse. Reply {NO_ANSWER} only when the context gives you no working days or rest day at all. And resolving a date tells you ONLY which day is meant. It never tells you whether an appointment slot is free, how busy that day is, who is on duty, or anything else the context does not state -- being able to name the day is not permission to answer a different question about it.
10. ABSENCE OF A FACT IS NOT EVIDENCE OF ITS OPPOSITE. If the context does not mention something, you do not know it -- silence never means the answer is "no". Never say the business lacks a service, has no lunch break, does not accept a payment method, has no such doctor, or does not do something, merely because the context does not mention it. Reply {NO_ANSWER} instead. A STATED range or list is different and you may reason from it: "works Monday to Saturday" does tell you about Sunday, because the range was stated. Silence is not a range."""

_SEARCH_CHUNKS = """
select c.id, c.content, 1 - (c.embedding <=> %(v)s::vector) as similarity,
       c.ordinal, s.id, s.label, s.filename, s.kind
  from chunk c
  left join source s on s.id = c.source_id
 where c.embedding is not null
 order by c.embedding <=> %(v)s::vector
 limit %(k)s
"""

# Confirmed only: unreviewed extractions must never reach a customer.
# `retrievable_fact`, not `fact`: the view excludes the reserved payment
# subject. Belt and braces here -- a payment fact also cannot carry an
# embedding (constraint fact_payment_not_embedded), so `embedding is not
# null` below already closes this path at the database.
_SEARCH_FACTS = """
select f.id, f.subject, f.attribute, f.attribute_key, f.value,
       1 - (f.embedding <=> %(v)s::vector) as similarity,
       f.created_at, s.id, s.label, s.filename, s.kind
  from retrievable_fact f
  left join source s on s.id = f.source_id
 where f.confirmed and f.embedding is not null
 order by f.embedding <=> %(v)s::vector
 limit %(k)s
"""

# Everything the business knows about one attribute. Used when the retrieved
# facts cluster on a single attribute, which is what a list question looks like
# from here -- see _expand_list().
_ALL_WITH_ATTRIBUTE = """
select f.id, f.subject, f.attribute, f.attribute_key, f.value, f.created_at,
       s.id, s.label, s.filename, s.kind
  from retrievable_fact f
  left join source s on s.id = f.source_id
 where confirmed and attribute_key = %(a)s
 order by subject
 limit 25
"""


# Detected in code, then stated in the prompt. Asking the model to infer and
# match the customer's language failed in a visible way: a Russian question
# about opening hours came back in Uzbek Latin, because the retrieved fact was
# stored in Uzbek and the model copied the context's language instead of the
# question's.
# Uzbek Cyrillic has four letters Russian does not. They are decisive when
# present -- and absent from a standard Russian keyboard layout, which is the
# whole problem: casual typists substitute у г к х, so the strongest signal is
# the one that disappears in the informal phone typing that is most of the
# traffic. Measured before the rewrite: 0 of 13 such messages detected
# correctly, every one answered in Russian. See check_language.py.
_UZBEK_LETTERS = set("ўғқҳ")

# Absent from the Uzbek Cyrillic alphabet, so decisive the other way. Note that
# ъ, ь, э, ё, ю, я are NOT here: Uzbek Cyrillic uses all of them, and treating
# any of them as Russian would reintroduce the same bug mirrored.
_RUSSIAN_LETTERS = set("ыщ")

# Matched as WHOLE WORDS, never as substrings. Substring matching sent Russian
# customers Uzbek replies: "ва" sits inside "вас", so "У вас есть невролог?" --
# about the most ordinary Russian phrasing there is -- was read as Uzbek.
# "ва" is dropped entirely; two letters is too little signal to be worth it.
_UZBEK_WORDS = {
    "бор", "йўқ", "йук", "керак", "нима", "ким", "қанча", "канча", "қандай",
    "кандай", "мумкин", "ишлайди", "нархи", "нарх", "қанақа", "канака",
    "учун", "билан", "ҳам", "хам", "бугун", "эртага", "соат", "куни", "кун",
    "мен", "сиз", "сизлар", "бўлади", "булади", "борми", "лекин", "яна",
    # Question words, both spellings -- the қ/к pair is the keyboard
    # substitution this whole rewrite is about.
    "қачон", "качон", "қаерда", "каерда", "нечта", "нечида", "неча",
    "қайси", "кайси", "нечи", "нима", "нимага",
}
_RUSSIAN_WORDS = {
    "у", "вы", "вас", "ваш", "ваша", "вашей", "не", "что", "как", "где",
    "есть", "можно", "сколько", "мне", "меня", "я", "ли", "или", "для",
    "при", "по", "до", "после", "нужно", "хочу", "это", "в", "на", "с",
    "к", "стоит", "работаете", "принимаете", "прийти", "записаться", "уже",
    "какой", "какие", "готовы",
    # Imperatives and greetings. Added after an adversarial pass: short Russian
    # with no function word and no distinctive ending -- "Дайте адрес",
    # "Нужен педиатр" -- scored zero on both sides and fell through the tie
    # to Uzbek. These carry the signal that sentence structure does not.
    "дайте", "дай", "скажите", "подскажите", "нужен", "нужна", "нужны",
    "здравствуйте", "спасибо", "пожалуйста", "добрый", "привет", "адрес",
    "приём", "прием", "детский", "хорошо", "да", "нет",
    # NOT "врач": it is an everyday loanword in colloquial Uzbek ("Врач качон
    # келади?", "врачингиз"), and listing it read those as Russian. A word
    # borrowed into both languages carries no signal and must stay out.
}

# Uzbek is agglutinative, so word endings carry real signal where Russian
# function words carry it instead. Weighted lower than the word lists on
# purpose: Russian instrumental plurals end in -ми ("с детьми", "врачами"),
# which collides head-on with the Uzbek question particle, and the word lists
# are what break that tie.
_UZBEK_SUFFIXES = ("ми", "миди", "ди", "да", "дан", "га", "нинг", "лар",
                   "миз", "сиз", "япти", "япди", "ади", "йди", "ган",
                   "нгиз", "лари", "имиз", "ларми", "моқчи", "мокчи")
_RUSSIAN_SUFFIXES = ("ете", "ает", "ить", "ать", "ого", "ому", "ый", "ая",
                     "ое", "ые", "ии", "ия", "ов", "ам", "ах", "ешь",
                     "ится", "его", "ему", "ой")

_WORDS = re.compile(r"\w+", re.UNICODE)


def _cyrillic_language(lowered: str, words: set[str]) -> str:
    """"Uzbek" or "Russian" for Cyrillic text, by weight of evidence.

    Scored rather than decided by a single test, because every individual
    signal has a counterexample: the Uzbek letters vanish on a Russian
    keyboard, and the Uzbek question particle -ми is also a Russian plural
    ending. No one signal is safe; the sum of them is.
    """
    uz = ru = 0
    if set(lowered) & _UZBEK_LETTERS:
        uz += 3
    if set(lowered) & _RUSSIAN_LETTERS:
        ru += 3
    uz += 2 * len(words & _UZBEK_WORDS)
    ru += 2 * len(words & _RUSSIAN_WORDS)
    # Long words only: short ones are mostly function words, already counted,
    # and a three-letter word ending in "да" says nothing.
    uz += sum(1 for w in words if len(w) >= 5 and w.endswith(_UZBEK_SUFFIXES))
    ru += sum(1 for w in words if len(w) >= 5 and w.endswith(_RUSSIAN_SUFFIXES))
    # A tie goes to Uzbek. The customers are in Uzbekistan, and the failure
    # being fixed here was Uzbek read as Russian -- defaulting the other way is
    # what produced 13 wrong replies out of 13.
    return "Russian" if ru > uz else "Uzbek"


# Detected in code, then stated in the prompt. Asking the model to infer and
# match the customer's language failed in a visible way: a Russian question
# about opening hours came back in Uzbek Latin, because the retrieved fact was
# stored in Uzbek and the model copied the context's language instead of the
# question's.
# The business's timezone, not the server's. Single-tenant on purpose, like the
# rest of the schema -- when multi-tenancy lands this becomes a column, not a
# config file. Hardcoded rather than read from the host: a server in another
# zone would be silently a day out for part of every day, and "silently" is the
# whole problem being fixed here.
BUSINESS_TZ = "Asia/Tashkent"


def _clock() -> str:
    """The two lines the model is allowed to reason about dates from.

    TOMORROW is computed HERE, in code, not left to the model. The failure this
    exists to fix was the bot telling a customer "Yakshanba dam olish kuni,
    shuning uchun ertaga ishlamaymiz" -- asserting tomorrow was Sunday with no
    concept of what day it was. Handing the model a date and asking it to add
    one day would trade a hallucinated weekday for an arithmetic mistake, which
    is the same defect wearing a better disguise. Code counts; the model reads.

    Deliberately only today and tomorrow. Anything further -- "next Tuesday",
    "in three days" -- is arithmetic we have not been asked for, and rule 8
    forbids the model doing it unaided.
    """
    # No fallback to the host clock if the timezone is unavailable. A wrong day
    # stated confidently is worse than an error at startup, and near midnight
    # the host and Tashkent disagree.
    now = datetime.datetime.now(zoneinfo.ZoneInfo(BUSINESS_TZ))
    tomorrow = now + datetime.timedelta(days=1)
    return (f"TODAY: {now:%A}, {now:%Y-%m-%d}\n"
            f"TOMORROW: {tomorrow:%A}, {tomorrow:%Y-%m-%d}")


def detect_language(text: str) -> str:
    """A reply instruction, not a language code. Deliberately coarse: it only
    has to separate the cases that were actually going wrong."""
    lowered = text.lower()
    if any("Ѐ" <= c <= "ӿ" for c in lowered):
        words = set(_WORDS.findall(lowered))
        if _cyrillic_language(lowered, words) == "Uzbek":
            return "Uzbek, in CYRILLIC script"
        return "Russian"
    # Latin script: could be Uzbek, English or code-switched. Naming a specific
    # language here would be a guess, and guessing wrong is the defect we are
    # fixing -- so instruct on script and let the model match the wording.
    return "the same language the customer wrote in, in LATIN script"


# Any line the model echoes back from the instruction, wherever it lands.
_REPLY_IN_ECHO = re.compile(r"^\s*REPLY IN:.*$", re.MULTILINE | re.IGNORECASE)


def _ask(prompt: str, question: str) -> tuple[str, bool]:
    """Returns (reply, refused). The model signals refusal with a marker so the
    caller can log a gap, instead of the refusal disappearing into prose."""
    # REPLY IN goes LAST, after the context, not before it. It used to sit at
    # the top; once both retrieval paths were merged the context block grew and
    # a Cyrillic Uzbek question started coming back in LATIN -- the model
    # copying the script of the facts it had just read. That is the exact
    # failure detect_language() exists to prevent, resurfacing because the
    # instruction sat further from the point of generation than the thing it
    # had to override. The harness could not see it: grading is route-based, so
    # a correct answer in the wrong script still passes.
    text = complete(
        _SYSTEM,
        f"{_clock()}\n\n{prompt}\n\nREPLY IN: {detect_language(question)}"
    )
    # Moving REPLY IN to the last line made the model occasionally CONTINUE it:
    # one reply came back with "REPLY IN: Uzbek, in CYRILLIC script" appended,
    # which a customer would have read in their chat. Intermittent, which is
    # worse than consistent. Stripped in code rather than asked for in the
    # prompt, because a prompt instruction is a preference and this is a
    # guarantee -- the same reason NO_ANSWER is a marker and not a request.
    text = _REPLY_IN_ECHO.sub("", text).strip()
    if NO_ANSWER in text:
        return text.replace(NO_ANSWER, "").strip(), True
    return text, False


def search(conn: Connection, question: str, limit: int = FACT_WINDOW,
           chunk_limit: int = 3) -> tuple[list, list]:
    """Nearest facts and nearest prose, with scores -- including those below the
    floor, because the gap log needs to distinguish a 0.63 miss from a 0.11 one.

    One embedding call serves both searches.
    """
    vector = str(embed_query(question))
    facts = []
    for (fid, subj, attr, akey, val, sim, created,
         sid, slabel, sfile, skind) in conn.execute(
            _SEARCH_FACTS, {"v": vector, "k": limit}).fetchall():
        f = _fact_row((fid, subj, attr, val, created, sid, slabel, sfile, skind))
        f["attribute_key"] = akey
        f["similarity"] = round(sim, 3)
        facts.append(f)
    chunks = [
        {"id": str(cid), "content": c, "similarity": round(sim, 3),
         "ordinal": ordinal, "source_id": str(sid) if sid else None,
         "source_label": slabel, "source_filename": sfile, "source_kind": skind}
        for cid, c, sim, ordinal, sid, slabel, sfile, skind in conn.execute(
            _SEARCH_CHUNKS, {"v": vector, "k": chunk_limit}).fetchall()
    ]
    return facts, chunks


def _expand_list(conn: Connection, facts: list[dict]) -> list[dict]:
    """If the retrieved facts cluster on one attribute, return EVERY fact with
    that attribute.

    This is what a list question looks like from inside retrieval: asked for the
    doctors, top-k came back as several `lavozim` facts and the model reported
    exactly those -- naming two of six doctors, confidently. Top-k by similarity
    can never answer "all of them", however wide the window, because it ranks
    rather than enumerates.

    Deliberately not a "is this a list question?" classifier. The shape of the
    result is the signal, so it works the same in Uzbek, Russian or anything
    else, with no phrasing to keep up with.
    """
    if len(facts) < LIST_CLUSTER:
        return facts
    counts: dict[str, int] = {}
    for f in facts:
        counts[f["attribute_key"]] = counts.get(f["attribute_key"], 0) + 1
    key, count = max(counts.items(), key=lambda kv: kv[1])
    if count < LIST_CLUSTER:
        return facts

    rows = conn.execute(_ALL_WITH_ATTRIBUTE, {"a": key}).fetchall()
    known = {(f["subject"], f["attribute"], f["value"]) for f in facts}
    expanded = list(facts)
    for (fid, subject, attribute, attribute_key, value, created,
         sid, slabel, sfile, skind) in rows:
        if (subject, attribute, value) not in known:
            # No similarity: these were not retrieved by score, they are here
            # because the question was about the whole set. Provenance still
            # travels with them -- an expanded fact reaches the prompt exactly
            # like a scored one, so the console must be able to show where it
            # came from.
            f = _fact_row((fid, subject, attribute, value, created,
                           sid, slabel, sfile, skind))
            f["attribute_key"] = attribute_key
            f["similarity"] = None
            expanded.append(f)
    return expanded


def _log_gap(question: str, retrieval: dict, chunks: list[dict],
              facts: list[dict] | None = None) -> None:
    """Record the miss WITH its scores. A 0.63 miss and a 0.11 miss are
    different problems -- one is a floor to lower, the other is missing
    knowledge -- and the question alone cannot tell them apart."""
    entry = {
        # Whose customer asked. "What your customers asked that I could not
        # answer" is a per-business report, so the line has to say which
        # business at the moment it is written -- a log without it cannot be
        # split afterwards, and afterwards is the only time anyone reads it.
        "business_id": current_business_id(),
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "question": question,
        "question_key": retrieval["question_key"],
        "matched_subjects": retrieval["subjects"],
        "matched_attribute": retrieval["attribute"],
        "similarity_floor": SIMILARITY_FLOOR,
        "chunk_scores": [c["similarity"] for c in chunks],
        "fact_scores": [f["similarity"] for f in (facts or [])],
        "best_similarity": max(
            [c["similarity"] for c in chunks]
            + [f["similarity"] for f in (facts or [])],
            default=None,
        ),
    }
    with GAP_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")



def _clinic_phone(conn: Connection) -> str | None:
    """The business's own number, read from the fact table like anything else.

    Returns None if no confirmed phone fact exists, and the caller then omits
    the number rather than inventing one. The emergency numbers in triage.py
    are constants; this one is not, and must never become one.
    """
    row = conn.execute(
        # The view, not the table: this value reaches a customer, which is the
        # test for which of the two to read. No payment attribute starts with
        # "telefon" today, and that is not a reason to read the wider one.
        "select value from retrievable_fact"
        " where attribute_key like %s and confirmed"
        " order by created_at limit 1",
        ("telefon%",),
    ).fetchone()
    return row[0] if row else None


def answer(conn: Connection, question: str) -> dict:
    """Answer, or refuse. Thin wrapper: the only thing it adds is keeping the
    medical disclaimer attached when a symptom report gets answered because the
    customer named what they wanted. Done here rather than threaded through
    five return points -- triage() is pure and costs nothing to call twice."""
    result = _answer(conn, question)
    tiered = triage(question)
    if (tiered and tiered[0] == "symptom" and result.get("answer")
            and not (result.get("source") or "").startswith("triage")):
        result["answer"] = (medical_lead(detect_language(question))
                            + " " + result["answer"])
        result["medical_lead"] = True
    return result


def _answer(conn: Connection, question: str) -> dict:
    # BEFORE retrieval, on purpose. A message reporting a symptom must never
    # reach the fact table -- if it did, the prices would already be in the
    # prompt and we would be trusting the model not to quote them. It did quote
    # them: "my wife's stomach hurts" was answered with an abdominal UZI price.
    # Short-circuiting here makes that impossible rather than unlikely, which
    # is the same argument as the NO_ANSWER branch below.
    tiered = triage(question)
    tier = tiered[0] if tiered else None

    # A general symptom report may still carry a question the customer asked in
    # their own words. Whether it does is decided by the EXACT tier, because
    # that tier only matches strings literally present in the customer's text:
    # `matched_on == "subject"` means they named the subject themselves, so
    # answering is answering rather than the bot choosing what to offer someone
    # in pain. Acute never gets this treatment -- see below.
    retrieval = find(conn, question) if tier == "symptom" else None
    if tier == "symptom" and retrieval["matched_on"] == "subject":
        tiered = None  # fall through to the ordinary path
        medical_note = True
    else:
        medical_note = False

    if tiered:
        tier, marker = tiered
        result = {
            "question": question, "status": "triage", "source": f"triage-{tier}",
            "facts": [], "matched_on": marker, "chunks": [],
            "answer": triage_reply(tier, detect_language(question),
                                   _clinic_phone(conn) if tier == "symptom" else None),
        }
        # Deliberately NOT logged as a gap. gaps.jsonl means "a question the
        # clinic could answer by adding a fact"; a symptom report is not that,
        # and mixing the two makes the gap log useless for its one job. The
        # message log in bot.py records the triage route, which is where the
        # volume question gets answered.
        return result

    # ALSO before retrieval, and for the mirror-image reason. Triage keeps a
    # question away from the facts; this keeps a fact away from the model. The
    # card number is never in a prompt because the prompt is never built.
    #
    # Below triage on purpose: someone describing chest pain who also mentions a
    # card gets the emergency reply, not payment details.
    #
    # A missed phrasing falls through to the ordinary path, where the view
    # hides the subject and the answer is NO_ANSWER plus a logged gap. The
    # failure mode is silence, never a wrong card number.
    marker = payment.detect(question)
    if marker:
        text = payment.message(conn, detect_language(question))
        # None means the owner has not finished filling these in. Falling
        # through is better than sending half a payment instruction.
        if text:
            return {
                "question": question, "status": "ok", "source": "payment",
                "facts": [], "matched_on": marker, "chunks": [],
                "answer": text,
            }

    # Already computed above for the symptom path; only the acute and
    # no-triage paths still need it.
    if retrieval is None:
        retrieval = find(conn, question)
    result = {
        "question": question,
        "status": retrieval["status"],
        "source": None,
        "facts": retrieval["facts"],
        "matched_on": retrieval.get("matched_on"),
        "chunks": [],
        "answer": None,
    }

    if retrieval["status"] == "ambiguous":
        # The candidate names ARE the context. The model phrases the question in
        # the customer's language; it is not choosing between them.
        names = ", ".join(retrieval["subjects"])
        result["source"] = "ambiguous"
        result["answer"], _ = _ask(
            f"The customer asked: {question}\n\n"
            f"This could refer to more than one person or service: {names}.\n\n"
            "Ask the customer which one they mean. Do not answer for any of them.",
            question,
        )
        return result

    # BOTH paths, always, then merged into one context. Exact match used to be
    # terminal: if it produced anything, the answer was built from that alone
    # and the vector path never ran. That is a short-circuit nobody intended,
    # and it told a customer we did not know our own address --
    # "Yakshanbayam ochiqmisila? Ozi qatda joylashgansila?" matched on Sunday
    # hours, answered that clause, returned `ok`, and dropped the rest. The
    # failure is invisible by construction: a real answer to a real clause looks
    # like success at every layer, including grading.
    #
    # Exact match is still FIRST and still authoritative -- its facts go in
    # ahead of the scored ones and are never subject to the similarity floor.
    # It just no longer ends the search. A question with one clause loses
    # nothing by also running the vector path; a question with three gains the
    # other two.
    #
    # The cost is one embedding call on questions that previously skipped it.
    # That is cheaper than it looks: it replaces a SECOND generation call on
    # every question where exact matching fired and then failed to answer.
    exact_facts = retrieval["facts"] if retrieval["status"] == "ok" else []

    near_facts, chunks = search(conn, question)
    result["chunks"] = chunks
    result["near_facts"] = near_facts
    usable_facts = [f for f in near_facts if f["similarity"] >= SIMILARITY_FLOOR]
    # "All the doctors" cannot be answered by ranking. If the survivors cluster
    # on one attribute, hand over the whole set instead of the top few.
    expanded = _expand_list(conn, usable_facts)
    if len(expanded) > len(usable_facts):
        result["expanded_list"] = len(expanded) - len(usable_facts)
        usable_facts = expanded

    # Exact first, then the scored ones, with duplicates dropped on identity
    # rather than on wording -- the same row can arrive down both paths.
    seen = {(f["subject"], f["attribute"]) for f in exact_facts}
    merged_facts = list(exact_facts)
    for f in usable_facts:
        if (f["subject"], f["attribute"]) not in seen:
            seen.add((f["subject"], f["attribute"]))
            merged_facts.append(f)
    usable_facts = merged_facts

    # What actually reached the prompt. near_facts is the raw scored window;
    # this is the window plus list expansion plus the exact matches, and it is
    # what the answer was built from -- so it is what grading and the logs must
    # look at.
    result["context_facts"] = usable_facts
    usable_chunks = [c for c in chunks if c["similarity"] >= SIMILARITY_FLOOR]

    if usable_facts or usable_chunks:
        parts = []
        if usable_facts:
            parts.append("Known facts about the business:\n" + "\n".join(
                f"- {f['subject']} / {f['attribute']}: {f['value']}"
                for f in usable_facts))
        if usable_chunks:
            parts.append("Passages from the business's own documents:\n"
                         + "\n\n".join(c["content"] for c in usable_chunks))
        context = "\n\n".join(parts)
        reply, refused = _ask(
            f"Context:\n{context}\n\nCustomer question: {question}", question)
        if not refused:
            # Name what was actually in the context. Labelling this
            # "vector-facts" whenever any fact cleared the floor made two
            # correctly-answered prose questions look like failures.
            # Name every path that actually contributed. "facts" means exact
            # matching put something in; "vector-facts" means the scored window
            # did. Both appear when both did, which is the whole point.
            result["source"] = "+".join(
                p for p, on in (("facts", exact_facts),
                                ("vector-facts",
                                 [f for f in usable_facts if f not in exact_facts]),
                                ("chunks", usable_chunks)) if on)
            result["status"] = "ok"
            result["answer"] = reply
            return result
        # The chunk cleared the floor and still did not contain the answer.
        # That is the floor being too low, and it is the case worth counting:
        # the model is the second line of defence, not the first.
        result["note"] = "chunk above floor but did not answer"
        _log_gap(question, retrieval, chunks, near_facts)
        result["status"] = "unknown"
        result["answer"] = reply
        return result

    # Nothing cleared the floor. The model is never called.
    _log_gap(question, retrieval, chunks, near_facts)
    result["status"] = "unknown"
    result["answer"] = None
    return result

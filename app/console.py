"""The test console: the owner talks to their agent before anyone else can.

Three things, and deliberately nothing else. No auth (single tenant, local, no
users), no conversation history (each question stands alone -- the follow-up
rewrite path in app/followup.py is not involved), no second answer path, no
cached results, no demo mode.

    ask()          -> the EXISTING answer path, plus what it used
    suggestions()  -> starter questions generated from facts the owner has
    record()       -> the owner's verdict, and what they can do about it

The console's whole value is showing what the agent ACTUALLY did. So provenance
is carried out of `answer()` (see `_fact_row` in app/retrieval.py) rather than
reconstructed here -- a reconstruction would be a plausible story about the
answer instead of the answer, which is the failure this file exists to prevent.
"""

import datetime
import json
import pathlib
import re

from psycopg import Connection

from app.answer import answer as answer_question
from app.answer import detect_language
from app.normalize import normalize
from app.retrieval import find

# Not a table. Feedback is an observation about an answer, not knowledge the
# business owns, and the four-table rule binds the knowledge model -- the same
# argument that put gaps in gaps.jsonl. Promote it when it earns a table, and
# ask first.
FEEDBACK_LOG = pathlib.Path(__file__).parent.parent / "feedback.jsonl"

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def wrong_script(question: str, answer_text: str | None) -> bool:
    """True if the reply is in an alphabet the customer did not write in.

    Runs on EVERY answer, including ones the owner marked Right. An owner who
    does not read Russian can accept a Russian reply to an Uzbek question
    without noticing anything wrong, so this must not depend on someone
    complaining. It is free, and it catches the most visible defect class there
    is.

    Script only, not language. Uzbek-Cyrillic versus Russian is
    detect_language()'s job and is tested for free in check_language.py.

    grading.py keeps its own version on purpose: the harness knows each
    question's DECLARED language, while here it has to be inferred. Same idea,
    different inputs, and merging them would mean one of the two lying about
    what it knows.
    """
    if not (answer_text or "").strip():
        return False
    wants_cyrillic = detect_language(question) != (
        "the same language the customer wrote in, in LATIN script")
    has_cyrillic = bool(_CYRILLIC.search(answer_text))
    return wants_cyrillic != has_cyrillic


_LANGUAGE_CODE = {
    "Uzbek, in CYRILLIC script": "uz-cyrl",
    "Russian": "ru",
    "the same language the customer wrote in, in LATIN script": "uz-latn",
}


def _used(value: str, answer_text: str | None) -> bool:
    """Did this fact demonstrably reach the reply?

    We know what went INTO the prompt; we cannot know which parts the model
    leaned on. But a value that appears in the answer was used, and that is
    checkable rather than guessed. Everything else is reported as context, not
    silently dropped -- 23 facts reached the prompt for a one-fact answer, and
    hiding the other 22 would make the console show a tidier story than the
    truth.
    """
    if not answer_text or not value:
        return False
    return normalize(str(value)) in normalize(answer_text)


def _overlaps(passage: str, answer_text: str | None, floor: float = 0.6) -> bool:
    """Did the reply draw on this passage, quoting it or not?

    Fraction of the ANSWER's words that appear in the passage -- not the other
    way round, because an answer is usually shorter than the paragraph it came
    from. Deliberately loose: the question is "did this contribute", and a
    paraphrase contributes.
    """
    if not answer_text:
        return False
    answer_words = [w for w in normalize(answer_text).split() if len(w) > 3]
    if not answer_words:
        return False
    source_words = set(normalize(passage).split())
    hits = sum(1 for w in answer_words if w in source_words)
    return hits / len(answer_words) >= floor


def _provenance(result: dict) -> list[dict]:
    """Where every piece of the answer came from, in the order it was used.

    Renders what is TRUE rather than what a spreadsheet mockup wanted. There is
    no sheet-and-row anchoring in the schema and no spreadsheet ingestion path
    to produce it, so claiming "sheet Услуги, row 14" would be the UI asserting
    something the system cannot know. See docs/design-decisions.md.
    """
    items = []
    reply = result.get("answer")
    for f in result.get("context_facts") or result.get("facts") or []:
        if f.get("source_id"):
            origin = "extracted"
            label = f.get("source_label")
            detail = f.get("source_filename")
        else:
            # NOT missing data. A typed fact is confirmed on write and has no
            # source by design, and "you typed this" is better provenance than
            # a filename would be.
            origin = "typed"
            label = None
            detail = None
        items.append({
            "kind": "fact", "id": f["id"], "origin": origin,
            "subject": f["subject"], "attribute": f["attribute"],
            "value": f["value"],
            "source_label": label, "source_filename": detail,
            "created_at": f.get("created_at"),
            "similarity": f.get("similarity"),
            "used": _used(f.get("value"), reply),
        })
    for c in result.get("chunks") or []:
        if c.get("similarity") is not None and c["similarity"] < 0.55:
            continue  # scored but never reached the prompt
        items.append({
            "kind": "chunk", "id": c["id"], "origin": "extracted",
            "quote": c["content"],
            "source_label": c.get("source_label"),
            "source_filename": c.get("source_filename"),
            "similarity": c.get("similarity"),
            # A chunk is not "unused" merely because the reply paraphrased it,
            # which is what happens whenever the customer's language differs
            # from the document's -- rule 5 beats rule 6 there, and a
            # translation can never be verbatim. So chunks are matched on word
            # overlap and facts on exact value; `quoted` says which happened.
            "used": _overlaps(c["content"], reply),
            "quoted": _used(c["content"][:120], reply),
        })
    # Demonstrably-used first, then the rest as context. Both are returned:
    # the screen renders the first group and can expand the second.
    return sorted(items, key=lambda i: not i["used"])


def ask(conn: Connection, question: str, from_suggestion: bool = False) -> dict:
    """The existing answer path. Nothing here answers anything itself."""
    result = answer_question(conn, question)
    reply = result.get("answer")
    payload = {
        "question": question,
        "answer": reply,
        "status": result["status"],
        "route": result.get("source"),
        "language": _LANGUAGE_CODE.get(detect_language(question), "uz-latn"),
        "wrong_script": wrong_script(question, reply),
        "provenance": _provenance(result),
    }
    payload["used_count"] = sum(1 for p in payload["provenance"] if p["used"])
    # Text matching cannot cross scripts. When the reply is in a different
    # alphabet from the material it came from -- an Uzbek policy answering a
    # Russian customer -- `used` is silent, and a screen rendering "0 sources"
    # there would be reporting a limitation as a finding. Say which it is.
    if payload["used_count"] == 0 and payload["provenance"]:
        source_text = " ".join(
            str(p.get("value") or p.get("quote") or "") for p in payload["provenance"])
        if bool(_CYRILLIC.search(reply or "")) != bool(_CYRILLIC.search(source_text)):
            payload["used_detection"] = "unavailable_cross_script"
        else:
            payload["used_detection"] = "text_match"
    else:
        payload["used_detection"] = "text_match"
    # The free-suggestions bet: exact-match resolution makes a refusal unlikely
    # but does not prevent one. This is that bet's failure mode, so it is
    # counted rather than assumed away.
    if from_suggestion and result["status"] != "ok":
        _log({"kind": "suggestion_refused", "question": question,
              "status": result["status"], "route": result.get("source")})
    return payload


# --- suggested questions ----------------------------------------------------
#
# The screen claims all three have answers in what the owner added, so they are
# generated from confirmed facts rather than fixed. Verification is by the
# EXACT tier, which is free: if `find()` resolves the phrased question back to
# the same subject, retrieval will reach it. That is not a guarantee the model
# will answer -- it can still refuse -- which is why ask() counts refusals on
# suggested questions.
#
# One template per attribute per language. Attributes are the ones an owner
# actually has on day one; anything else is skipped rather than phrased badly.
_TEMPLATES = {
    "ish vaqti": {
        "uz-latn": "{s} ish vaqti qanday?",
        "uz-cyrl": "{s} иш вақти қандай?",
        "ru": "Во сколько работает {s}?",
    },
    "manzil": {
        "uz-latn": "{s} qayerda joylashgan?",
        "uz-cyrl": "{s} қаерда жойлашган?",
        "ru": "Где находится {s}?",
    },
    "telefon": {
        "uz-latn": "{s} telefon raqami nechchi?",
        "ru": "Какой телефон у {s}?",
    },
    "to'lov usullari": {
        "uz-latn": "{s} qanday to'lov usullarini qabul qiladi?",
        "ru": "Какие способы оплаты принимает {s}?",
    },
    "qabul narxi": {
        "uz-latn": "{s} qabuli qancha turadi?",
        "ru": "Сколько стоит приём {s}?",
    },
    "qabul vaqti": {
        "uz-latn": "{s} qachon qabul qiladi?",
        "uz-cyrl": "{s} қачон қабул қилади?",
        "ru": "Когда принимает {s}?",
    },
    "narx": {
        "uz-latn": "{s} narxi qancha?",
        "ru": "Сколько стоит {s}?",
    },
    "xona": {
        "uz-latn": "{s} qaysi xonada?",
        "ru": "В каком кабинете {s}?",
    },
    "tayyorgarlik": {
        "uz-latn": "{s} uchun qanday tayyorgarlik kerak?",
        "ru": "Как подготовиться к {s}?",
    },
}

# Most useful first: an owner learns more from "does it know my hours" than
# from a room number.
_PRIORITY = ["ish vaqti", "manzil", "qabul narxi", "qabul vaqti", "narx",
             "to'lov usullari", "telefon", "tayyorgarlik", "xona"]


def suggestions(conn: Connection, limit: int = 3) -> list[dict]:
    """Starter questions that are known to resolve. Fewer if fewer resolve.

    Never returns a suggestion that exact matching cannot reach. The screen
    promises these have answers, so a suggestion that fails would make the
    product lie on the first screen the owner sees.
    """
    rows = conn.execute(
        "select subject, attribute, attribute_key from fact"
        " where confirmed order by created_at"
    ).fetchall()

    by_attribute: dict[str, list[str]] = {}
    for subject, attribute, attribute_key in rows:
        by_attribute.setdefault(attribute_key, []).append(subject)

    out: list[dict] = []
    used_languages: set[str] = set()
    for attribute_key in _PRIORITY:
        if len(out) >= limit:
            break
        subjects = by_attribute.get(attribute_key)
        if not subjects:
            continue
        templates = _TEMPLATES.get(attribute_key)
        if not templates:
            continue
        # Spread languages: one per language where possible, so the owner sees
        # the agent handle more than one script before trusting it.
        for language in sorted(templates, key=lambda x: x in used_languages):
            subject = subjects[0]
            question = templates[language].format(s=subject)
            hit = find(conn, question)
            if hit["status"] != "ok" or hit["matched_on"] != "subject":
                continue
            out.append({"question": question, "language": language,
                        "resolves_to": f"{subject} / {attribute_key}"})
            used_languages.add(language)
            break
    return out[:limit]


# --- feedback ---------------------------------------------------------------

def _log(entry: dict) -> None:
    entry = {"at": datetime.datetime.now(datetime.UTC).isoformat(), **entry}
    with FEEDBACK_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def next_step(result: dict, verdict: str, reason: str | None) -> dict:
    """What the owner can DO about a wrong answer.

    Five cases are distinguishable mechanically, and one is not. Saying which is
    which matters more than the classification itself: an owner sent to fix a
    fact when the real problem is a missing alias will edit correct data.
    """
    status = result["status"]
    route = result.get("source") or ""
    facts = result.get("context_facts") or result.get("facts") or []
    near = result.get("near_facts") or []
    best = max((f.get("similarity") or 0 for f in near), default=0)

    if wrong_script(result["question"], result.get("answer")):
        # Detected, not reported. Runs on Right verdicts too.
        return {"action": "report_bug", "reason": "wrong_script",
                "text": "The reply came back in the wrong alphabet. That is a "
                        "bug in the agent, not something you can fix here."}

    if route.startswith("triage"):
        return {"action": "none", "reason": "triage",
                "text": "This was read as a health complaint, so the agent "
                        "deliberately did not answer from your data."}

    if verdict == "right":
        return {"action": "none", "reason": "confirmed", "text": None}

    if status == "unknown":
        # ONE branch, not two, and that is a correction. The first version split
        # "nothing retrieved" from "retrieved but refused" on whether anything
        # cleared the similarity floor -- and the floor is 0.55 and deliberately
        # low, so unrelated facts clear it routinely. "Sizlarda kosmodrom
        # bormi?" came back as "your data was found, check which fact is
        # wrong", sending the owner to fix a fact that does not exist.
        #
        # Splitting on a higher score would be the threshold-as-classifier
        # mistake measured and rejected in docs/design-decisions.md: across 80
        # questions, answerable and unanswerable scores overlap across nearly
        # their whole range. The scores cannot make this call, so the console
        # does not pretend to. It shows the nearest things and both readings,
        # and lets the person who knows the business decide.
        return {"action": "review_nearest", "reason": "no_answer",
                "nearest_score": round(best, 3) if near else None,
                "nearest": [{"subject": f["subject"],
                             "attribute": f["attribute"],
                             "value": f["value"],
                             "similarity": f.get("similarity")}
                            for f in near[:5]],
                "text": "Nothing here answered that. If one of the entries "
                        "below should have, your wording and the customer's "
                        "differ. If none of them is relevant, this is missing "
                        "knowledge to add."}

    # Answered, and wrong. The one distinction code CANNOT make: whether a
    # retrieved fact is factually wrong, or a correct fact was misused. Both
    # look identical from here, so the owner is asked rather than guessed at.
    return {"action": "choose", "reason": "answered_but_wrong",
            "choices": ["fact_is_wrong", "used_the_wrong_fact"],
            "chosen": reason if reason in
                      ("fact_is_wrong", "used_the_wrong_fact") else None,
            "facts": [{"id": f["id"], "subject": f["subject"],
                       "attribute": f["attribute"], "value": f["value"]}
                      for f in facts[:5]],
            "text": "The agent answered from these. Is one of them wrong, or "
                    "did it use the wrong one?"}


def record(conn: Connection, question: str, verdict: str,
           reason: str | None = None) -> dict:
    """Store the verdict against everything that produced the answer.

    The answer is re-derived rather than passed in from the client: a verdict
    stored against a client-supplied answer records what the browser claimed,
    not what the agent did. Costs one model call and is worth it -- this is the
    correction loop's entry point and the most valuable data the product makes.
    """
    if verdict not in ("right", "wrong"):
        raise ValueError("verdict must be 'right' or 'wrong'")

    result = answer_question(conn, question)
    step = next_step(result, verdict, reason)
    facts = result.get("context_facts") or result.get("facts") or []

    entry = {
        "kind": "verdict",
        "verdict": verdict,
        "question": question,
        "question_key": normalize(question),
        "answer": result.get("answer"),
        "status": result["status"],
        "route": result.get("source"),
        "language": _LANGUAGE_CODE.get(detect_language(question), "uz-latn"),
        "wrong_script": wrong_script(question, result.get("answer")),
        "fact_ids": [f["id"] for f in facts],
        "scores": [f.get("similarity") for f in result.get("near_facts") or []],
        "chunk_scores": [c.get("similarity") for c in result.get("chunks") or []],
        "owner_reason": reason,
        "next_step": step,
    }
    _log(entry)
    return {"stored": True, "next_step": step,
            "wrong_script": entry["wrong_script"]}

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

from psycopg import Connection

from app.embeddings import embed_query
from app.llm import complete
from app.retrieval import find

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
FACT_WINDOW = 8

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
1b. The context is retrieved by similarity, so it often contains material that is merely RELATED to the question rather than an answer to it. Before answering, check that the context is about the specific thing asked. Related is not the same as answering: if someone asks what time the clinic closes and the context only holds individual doctors' consultation hours, that is not the answer -- reply {NO_ANSWER}. Answering a question with adjacent facts is worse than admitting you do not know.
2. If the context does not contain the answer, reply with exactly {NO_ANSWER} on the first line, then one short sentence in the customer's language telling them to contact the business directly. Never guess, and never use anything you know from outside the context.
3. Never invent or adjust a price, a time, a phone number or a person's name. Use them exactly as the context gives them.
4. Prices are approximate ranges. Present them as ranges, never as an exact price.
5. The language to reply in is stated at the top of every message as REPLY IN. Obey it exactly. The context is often stored in a different language from the question -- never let the context's language decide your reply's language.
6. When the context is a passage from a document, keep its wording rather than rewriting it. Paraphrasing is how details drift.
7. Be brief -- one or two sentences. This is a chat message, not a document."""

_SEARCH_CHUNKS = """
select content, 1 - (embedding <=> %(v)s::vector) as similarity
  from chunk
 where embedding is not null
 order by embedding <=> %(v)s::vector
 limit %(k)s
"""

# Confirmed only: unreviewed extractions must never reach a customer.
_SEARCH_FACTS = """
select subject, attribute, attribute_key, value,
       1 - (embedding <=> %(v)s::vector) as similarity
  from fact
 where confirmed and embedding is not null
 order by embedding <=> %(v)s::vector
 limit %(k)s
"""

# Everything the business knows about one attribute. Used when the retrieved
# facts cluster on a single attribute, which is what a list question looks like
# from here -- see _expand_list().
_ALL_WITH_ATTRIBUTE = """
select subject, attribute, attribute_key, value
  from fact
 where confirmed and attribute_key = %(a)s
 order by subject
 limit 25
"""


# Detected in code, then stated in the prompt. Asking the model to infer and
# match the customer's language failed in a visible way: a Russian question
# about opening hours came back in Uzbek Latin, because the retrieved fact was
# stored in Uzbek and the model copied the context's language instead of the
# question's.
_UZBEK_CYRILLIC = set("ўғқҳ")
# Matched as WHOLE WORDS, never as substrings. Substring matching sent Russian
# customers Uzbek replies: "ва" sits inside "вас", so "У вас есть невролог?" --
# about the most ordinary Russian phrasing there is -- was read as Uzbek.
# "ва" is dropped entirely; two letters is too little signal to be worth it.
_UZBEK_CYRILLIC_WORDS = {"бор", "керак", "қанча", "нима", "йўқ", "ким",
                         "қанақа", "мумкин", "ишлайди", "нархи"}
_WORDS = re.compile(r"\w+", re.UNICODE)


def detect_language(text: str) -> str:
    """A reply instruction, not a language code. Deliberately coarse: it only
    has to separate the three cases that were actually going wrong."""
    lowered = text.lower()
    words = set(_WORDS.findall(lowered))
    if any(c in _UZBEK_CYRILLIC for c in lowered) or (words & _UZBEK_CYRILLIC_WORDS):
        return "Uzbek, in CYRILLIC script"
    if any("Ѐ" <= c <= "ӿ" for c in lowered):
        return "Russian"
    # Latin script: could be Uzbek, English or code-switched. Naming a specific
    # language here would be a guess, and guessing wrong is the defect we are
    # fixing -- so instruct on script and let the model match the wording.
    return "the same language the customer wrote in, in LATIN script"


def _ask(prompt: str, question: str) -> tuple[str, bool]:
    """Returns (reply, refused). The model signals refusal with a marker so the
    caller can log a gap, instead of the refusal disappearing into prose."""
    text = complete(
        _SYSTEM, f"REPLY IN: {detect_language(question)}\n\n{prompt}"
    )
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
    facts = [
        {"subject": s, "attribute": a, "attribute_key": ak, "value": v,
         "similarity": round(sim, 3)}
        for s, a, ak, v, sim in conn.execute(
            _SEARCH_FACTS, {"v": vector, "k": limit}).fetchall()
    ]
    chunks = [
        {"content": c, "similarity": round(sim, 3)}
        for c, sim in conn.execute(
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
    for subject, attribute, attribute_key, value in rows:
        if (subject, attribute, value) not in known:
            # No similarity: these were not retrieved by score, they are here
            # because the question was about the whole set.
            expanded.append({"subject": subject, "attribute": attribute,
                             "attribute_key": attribute_key, "value": value,
                             "similarity": None})
    return expanded


def _log_gap(question: str, retrieval: dict, chunks: list[dict],
              facts: list[dict] | None = None) -> None:
    """Record the miss WITH its scores. A 0.63 miss and a 0.11 miss are
    different problems -- one is a floor to lower, the other is missing
    knowledge -- and the question alone cannot tell them apart."""
    entry = {
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


def answer(conn: Connection, question: str) -> dict:
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

    if retrieval["status"] == "ok":
        context = "\n".join(
            f"- {f['subject']} / {f['attribute']}: {f['value']}"
            for f in retrieval["facts"]
        )
        reply, refused = _ask(
            f"Context (known facts about the business):\n{context}\n\n"
            f"Customer question: {question}",
            question,
        )
        if not refused:
            result["source"] = "facts"
            result["answer"] = reply
            return result
        # Matching fired, but on facts that do not answer the question. Still a
        # gap, and a more interesting one than a plain miss: it says the match
        # was wrong, not that the knowledge is absent.
        result["note"] = "facts matched but did not answer"

    # Exact matching missed, or matched facts that answered nothing. Fall back
    # to vector search -- over facts as well as prose, because a Russian
    # question about opening hours has no prose to find, only a fact.
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
    # What actually reached the prompt. near_facts is the raw scored window;
    # this is the window plus any list expansion, and it is what the answer was
    # built from -- so it is what grading and the logs must look at.
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
            result["source"] = "+".join(
                p for p, on in (("vector-facts", usable_facts),
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

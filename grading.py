"""How a result is scored. Imported by check_answer.py (which runs the set) and
by regrade.py (which re-scores the last run for free).

Kept in one place because the grader has now been wrong three times -- always by
inspecting a route or a field that the answer path had moved on from -- and each
time it cost a full 25-question re-run to find out.

THIS GRADES ROUTES, NOT ANSWERS, AND THE TWO CAN MOVE APART. A verdict here can
improve while the reply gets worse: `doctors-ru` named twelve doctors under one
setting and five under another, and this file scored the five-doctor run as the
better one because the row it looks for had arrived in the context. Nothing in
here reads the sentence the customer would receive. See the limitation section
at the top of questions.py before using a pass count as evidence that anything
improved.
"""

import re

from app.normalize import normalize


_CYRILLIC = re.compile(r"[\u0400-\u04ff]")


def wrong_script(q: dict, r: dict) -> str | None:
    """Reason the reply is in the wrong script, or None.

    Added after a silent regression: merging both retrieval paths grew the
    context, and a Cyrillic Uzbek question came back in LATIN because the model
    copied the script of the facts it had just read. Every route was correct, so
    the verdict stayed PASS and nothing reported it. Grading the route alone
    cannot see a right answer delivered in a script the customer cannot read --
    and that is the most VISIBLE defect class there is. A customer notices it
    instantly, where they would never notice a threshold being wrong.

    Deliberately only checks script, not language. Uzbek-Cyrillic versus Russian
    is the detector's job and is tested for free in check_language.py; this only
    catches the reply coming back in the wrong alphabet entirely.
    """
    answer = r.get("answer") or ""
    if not answer.strip():
        return None  # a refusal with no text has no script to be wrong about
    has_cyrillic = bool(_CYRILLIC.search(answer))
    if q["lang"] in ("uz-cyrl", "ru") and not has_cyrillic:
        return "question is Cyrillic, reply is not"
    if q["lang"] in ("uz-latn", "en") and has_cyrillic:
        return "question is Latin, reply is Cyrillic"
    return None

def matches(fact: dict, want: str) -> bool:
    """Does this retrieved fact satisfy a `want` of the form "Subject / attribute"?

    ONE definition, because check_window.py needs the same rule to ask where the
    answering fact ranks. Two copies of "is this the right fact" would be two
    answers to that question, and they would disagree the first time an
    attribute was renamed.

    The attribute is matched by PREFIX: `want` says "qabul narxi" and the stored
    attribute may be "qabul narxi" or something that starts with it. The subject
    must match exactly, on the normalized key.
    """
    if " / " not in want:
        return False
    want_subject, want_attribute = (p.strip() for p in want.split(" / ", 1))
    return (normalize(fact["subject"]) == normalize(want_subject)
            and normalize(fact["attribute"]).startswith(
                normalize(want_attribute)))


def grade(q: dict, r: dict) -> str | None:
    """PASS / FAIL / None (judge by eye)."""
    expect = q["expect"]
    source = r["source"] or ""

    # Before anything about routes: a reply the customer cannot read is a
    # failure however correct its contents. This is checked first so it cannot
    # be masked by a route that graded green.
    if wrong_script(q, r):
        return "FAIL"

    if expect == "gap":
        return "PASS" if r["status"] == "unknown" else "FAIL"
    if expect == "triage":
        # Graded on the ROUTE, not merely on not-answering. A symptom report
        # that refuses for the ordinary reason -- nothing retrieved -- would
        # look identical to one triage caught, and the difference matters: one
        # is a guarantee, the other is luck. `want` names the tier.
        return "PASS" if source == f"triage-{q['want']}" else "FAIL"
    if expect == "ask-which":
        return "PASS" if source == "ambiguous" else "FAIL"
    if expect == "payment":
        # The ONLY expectation graded on wording, and graded on it exactly.
        # Route alone is not enough here: "payment" plus a reply that is one
        # digit different is the failure this is for. Byte equality is the
        # assertion -- no strip(), no normalize(), no "contains the number".
        # If the model ever composes this string it will not be byte-equal,
        # and that is the entire test.
        return "PASS" if (source == "payment"
                          and r.get("answer") == q["want"]) else "FAIL"
    if expect == "prose":
        # Route may be "chunks" or "vector-facts+chunks": once the floor
        # dropped, facts clear it too even when the chunk carries the answer.
        # What matters is that prose was in the context.
        return "PASS" if "chunks" in source else "FAIL"

    if " / " not in q["want"]:
        return None  # a shape, not a row

    # Facts arrive either by exact match or by vector search. Both count.
    candidates = list(r["facts"])
    if "vector-facts" in source:
        # context_facts includes list expansion; near_facts is only the scored
        # window. Grading the window scored three correct answers as failures.
        candidates += r.get("context_facts") or r.get("near_facts") or []
    for fact in candidates:
        if matches(fact, q["want"]):
            return "PASS"
    return "FAIL"

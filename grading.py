"""How a result is scored. Imported by check_answer.py (which runs the set) and
by regrade.py (which re-scores the last run for free).

Kept in one place because the grader has now been wrong three times -- always by
inspecting a route or a field that the answer path had moved on from -- and each
time it cost a full 25-question re-run to find out.
"""

from app.normalize import normalize


def grade(q: dict, r: dict) -> str | None:
    """PASS / FAIL / None (judge by eye)."""
    expect = q["expect"]
    source = r["source"] or ""

    if expect == "gap":
        return "PASS" if r["status"] == "unknown" else "FAIL"
    if expect == "ask-which":
        return "PASS" if source == "ambiguous" else "FAIL"
    if expect == "prose":
        # Route may be "chunks" or "vector-facts+chunks": once the floor
        # dropped, facts clear it too even when the chunk carries the answer.
        # What matters is that prose was in the context.
        return "PASS" if "chunks" in source else "FAIL"

    if " / " not in q["want"]:
        return None  # a shape, not a row

    want_subject, want_attribute = (p.strip() for p in q["want"].split(" / ", 1))
    # Facts arrive either by exact match or by vector search. Both count.
    candidates = list(r["facts"])
    if "vector-facts" in source:
        # context_facts includes list expansion; near_facts is only the scored
        # window. Grading the window scored three correct answers as failures.
        candidates += r.get("context_facts") or r.get("near_facts") or []
    for fact in candidates:
        if (normalize(fact["subject"]) == normalize(want_subject)
                and normalize(fact["attribute"]).startswith(
                    normalize(want_attribute))):
            return "PASS"
    return "FAIL"

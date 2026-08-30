"""Run every question in questions.py through step 20 and score it.

Grading is mechanical and deliberately strict:

  fact       the returned facts must include the subject and attribute named in
             `want`. Returning *something* is not a pass.
  ask-which  must come back ambiguous. Answering correctly for one of the two
             Rasulovas is still a fail.
  prose/gap  must come back not_found. Step 20 has no vectors and no business
             answering these; inventing a match here would be the real failure.

Some `want` values describe a shape rather than a row ("every subject with a
lavozim"). Those print as MANUAL and are not counted either way.
"""

import sys

from app.db import pool
from app.normalize import normalize
from app.retrieval import find
from questions import QUESTIONS

sys.stdout.reconfigure(encoding="utf-8")


def grade(q: dict, r: dict) -> str | None:
    expect = q["expect"]
    if expect == "ask-which":
        return "PASS" if r["status"] == "ambiguous" else "FAIL"
    if expect in ("prose", "gap"):
        return "PASS" if r["status"] == "not_found" else "FAIL"

    if " / " not in q["want"]:
        return None  # a shape, not a row -- judge by eye
    want_subject, want_attribute = (p.strip() for p in q["want"].split(" / ", 1))
    for fact in r["facts"]:
        if (normalize(fact["subject"]) == normalize(want_subject)
                and normalize(fact["attribute"]).startswith(normalize(want_attribute))):
            return "PASS"
    return "FAIL"


with pool:
    with pool.connection() as conn:
        scored: list[tuple[str, dict, dict, str | None]] = []
        for q in QUESTIONS:
            r = find(conn, q["q"])
            scored.append((grade(q, r), q, r, q.get("note")))

for verdict, q, r, note in scored:
    mark = verdict or "MANUAL"
    print(f"\n[{mark:6}] {q['lang']:8} {q['q']}")
    print(f"{'':9} want: {q['want']}")
    print(f"{'':9} key:  {r['question_key']}")
    print(f"{'':9} matched subjects={r['subjects'] or '-'} attribute={r['attribute'] or '-'}"
          f" status={r['status']}")
    for fact in r["facts"][:4]:
        print(f"{'':11} -> {fact['subject']} / {fact['attribute']} = {fact['value']}")
    if len(r["facts"]) > 4:
        print(f"{'':11}    ... and {len(r['facts']) - 4} more")
    if verdict == "FAIL" and note:
        print(f"{'':9} expected difficulty: {note}")

passed = sum(1 for v, *_ in scored if v == "PASS")
failed = sum(1 for v, *_ in scored if v == "FAIL")
manual = sum(1 for v, *_ in scored if v is None)
print(f"\n{'=' * 70}")
print(f"{passed} pass, {failed} fail, {manual} manual, of {len(scored)}")

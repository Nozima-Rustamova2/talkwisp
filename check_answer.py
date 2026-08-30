"""Run every question through the full answering path and grade the ROUTE.

The route is mechanical and gradeable: did it answer from facts, from prose,
ask which, or refuse? The wording of the answer is not graded -- it is printed
for you to read, because "is this a good reply to a customer" is a judgement.

A `gap` question that produces any answer at all is a FAIL, however good the
answer reads. That is the point of the test.
"""

import json
import pathlib
import sys
import time

from app.answer import SIMILARITY_FLOOR, answer
from app.db import pool
from grading import grade
from questions import QUESTIONS

sys.stdout.reconfigure(encoding="utf-8")


print(f"similarity floor = {SIMILARITY_FLOOR}\n")

rows = []
with pool:
    with pool.connection() as conn:
        for q in QUESTIONS:
            r = answer(conn, q["q"])
            rows.append((grade(q, r), q, r))
            time.sleep(4)  # free tier is per-minute limited; pace the loop

for verdict, q, r in rows:
    mark = verdict or "MANUAL"
    src = r["source"] or "-"
    print(f"\n[{mark:6}] {q['lang']:8} {q['q']}")
    print(f"{'':9} expect={q['expect']:9} route={src:9} status={r['status']}")
    if r.get("near_facts"):
        print(f"{'':9} fact scores:  {[f['similarity'] for f in r['near_facts']]}"
              f"  top: {r['near_facts'][0]['subject']} / {r['near_facts'][0]['attribute']}")
    if r["chunks"]:
        print(f"{'':9} chunk scores: {[c['similarity'] for c in r['chunks']]}")
    if r.get("note"):
        print(f"{'':9} note: {r['note']}")
    if r["answer"]:
        print(f"{'':9} > {r['answer']}")
    else:
        print(f"{'':9} > (no answer -- model not called, gap logged)")

# Persist, so re-grading never costs another 25 API calls.
pathlib.Path("results.json").write_text(
    json.dumps([{"verdict": v, "question": q, "result": r} for v, q, r in rows],
               ensure_ascii=False, indent=1), encoding="utf-8")

passed = sum(1 for v, *_ in rows if v == "PASS")
failed = sum(1 for v, *_ in rows if v == "FAIL")
manual = sum(1 for v, *_ in rows if v is None)
print(f"\n{'=' * 70}\n{passed} pass, {failed} fail, {manual} manual, of {len(rows)}")

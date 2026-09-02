"""Re-score the last check_answer.py run without calling any API.

    uv run python regrade.py

Use this after changing grading.py OR after changing an expectation in
questions.py. A full run costs one embedding call and one generation per
question against a daily free quota that has already run out twice; this costs
nothing. Re-run check_answer.py only when the ANSWER PATH changes.

It re-reads questions.py rather than trusting the copy stored in results.json.
That copy is a snapshot of what the questions were AT RUN TIME, so grading
against it silently ignores any expectation you have since changed -- which it
did, on 2026-09-02, reporting an unchanged 76/3/1 after two expectations were
flipped. The same shape as every other tool failure here: the thing that was
supposed to verify the change could not see it.

Questions added since the run cannot be graded from a cached result at all, and
are reported as needing a real run rather than quietly omitted.
"""

import json
import pathlib
import sys

from grading import grade
from questions import QUESTIONS

sys.stdout.reconfigure(encoding="utf-8")

rows = json.loads(pathlib.Path("results.json").read_text(encoding="utf-8"))

current = {q["id"]: q for q in QUESTIONS}
stale = [i for i in current if i not in {row["question"]["id"] for row in rows}]
changed = []

for row in rows:
    r = row["result"]
    stored = row["question"]
    # The live question wins. Fall back to the stored copy only for a question
    # that has since been deleted, so an old run still grades rather than
    # crashing.
    q = current.get(stored["id"], stored)
    if q is not stored and (q["expect"] != stored["expect"]
                            or q.get("want") != stored.get("want")):
        changed.append((stored["id"], stored["expect"], q["expect"]))
    verdict = grade(q, r)
    row["verdict"] = verdict
    print(f"[{verdict or 'MANUAL':6}] {q['lang']:8} {q['q']}")
    print(f"{'':9} expect={q['expect']:9} route={r['source'] or '-'}")
    if r["answer"]:
        print(f"{'':9} > {r['answer']}")

passed = sum(1 for r in rows if r["verdict"] == "PASS")
failed = sum(1 for r in rows if r["verdict"] == "FAIL")
manual = sum(1 for r in rows if r["verdict"] is None)
print(f"\n{'=' * 70}\n{passed} pass, {failed} fail, {manual} manual, of {len(rows)}")

if changed:
    print(f"\n{len(changed)} expectation(s) changed since this run was recorded:")
    for qid, was, now in changed:
        print(f"  {qid}: expect {was} -> {now}")
    print("  (graded against the CURRENT questions.py, not the stored snapshot)")

if stale:
    print(f"\n{len(stale)} question(s) are not in this run and were NOT graded:")
    for qid in stale:
        print(f"  {qid}")
    print("  Run check_answer.py -- a cached result cannot answer a new question.")

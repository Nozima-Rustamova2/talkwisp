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
from app.llm import LLMError
from app.db import connection, harness_business, pool
from grading import grade
from questions import QUESTIONS

sys.stdout.reconfigure(encoding="utf-8")


print(f"similarity floor = {SIMILARITY_FLOOR}\n")

# The four `prose` questions can only pass while this source is loaded, and
# seed.py TRUNCATES source on every run. Without this guard a reseed turns four
# passes into four failures that look like a retrieval bug -- the silent-drift
# failure class that has cost time five times now. It FAILS the run rather than
# warning, because a warning gets scrolled past. Same shape as the
# forbidden-alias guard in seed.py.
PROSE_SOURCE = "Bemorlar uchun qoidalar"

def save(rows):
    """Persist, so re-grading never costs another API call -- and so an
    interrupted run leaves what it had rather than nothing."""
    pathlib.Path("results.json").write_text(
        json.dumps([{"verdict": v, "question": q, "result": r}
                    for v, q, r in rows], ensure_ascii=False, indent=1),
        encoding="utf-8")


rows = []
with pool:
    with connection(harness_business()) as conn:
        needs_prose = [q["id"] for q in QUESTIONS if q["expect"] == "prose"]
        if needs_prose and not conn.execute(
                "select count(*) from chunk").fetchone()[0]:
            print(f"ABORT: {len(needs_prose)} questions expect prose and the "
                  f"chunk table is EMPTY.")
            print(f"  {', '.join(needs_prose)}")
            print(f"  They depend on the source {PROSE_SOURCE!r} "
                  f"(data/avisena_policy.txt), which seed.py truncates.")
            print("  Re-ingest it first, or this run reports four retrieval "
                  "failures that are really one missing source.")
            sys.exit(2)

        for q in QUESTIONS:
            # One question's infrastructure failure must not destroy the other
            # 89 answers. A run costs ninety completion calls and nine minutes,
            # and the first attempt at this measurement died on a dropped TCP
            # connection with nothing written -- results are accumulated and
            # only persisted at the end, so the whole run was lost.
            #
            # An LLM error is recorded as ERROR, never as FAIL. Grading it as a
            # failure would put an infrastructure problem into the same column
            # as a behavioural one, and the run's headline number would quietly
            # mean something different.
            try:
                r = answer(conn, q["q"])
                verdict = grade(q, r)
            except LLMError as exc:
                r = {"question": q["q"], "status": "error", "source": None,
                     "facts": [], "chunks": [], "answer": None,
                     "note": f"LLM ERROR: {exc}"}
                verdict = "ERROR"
            rows.append((verdict, q, r))
            # Written after EVERY question, not once at the end. Two runs in a
            # row have now cost nine minutes and ninety completion calls and
            # produced nothing -- one to a dropped TCP connection, one to the
            # process being killed. Neither was recoverable, because the only
            # write happened after the loop. A partial results.json is worth
            # something; an empty one is worth nothing, and the cost of being
            # sure is one file write per four-second sleep.
            save(rows)
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


passed = sum(1 for v, *_ in rows if v == "PASS")
failed = sum(1 for v, *_ in rows if v == "FAIL")
manual = sum(1 for v, *_ in rows if v is None)
errored = [q["id"] for v, q, _ in rows if v == "ERROR"]
if errored:
    print()
    print(f"{len(errored)} question(s) never reached the model and are NOT "
          f"graded either way:")
    print(f"  {', '.join(errored)}")
    print("  Re-run before comparing this against anything.")
print(f"\n{'=' * 70}\n{passed} pass, {failed} fail, {manual} manual, of {len(rows)}")

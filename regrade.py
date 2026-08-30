"""Re-score the last check_answer.py run without calling any API.

    uv run python regrade.py

Use this after changing grading.py. A full run costs 28 embedding calls and 28
generations against a daily free quota that has already run out twice; this
costs nothing. Re-run check_answer.py only when the ANSWER PATH changes.
"""

import json
import pathlib
import sys

from grading import grade

sys.stdout.reconfigure(encoding="utf-8")

rows = json.loads(pathlib.Path("results.json").read_text(encoding="utf-8"))

for row in rows:
    q, r = row["question"], row["result"]
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

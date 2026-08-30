"""Format messages.jsonl lines as they arrive. Reads stdin, one event per line."""

import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except ValueError:
        continue

    q = (d.get("question") or "")[:60]
    if d.get("outcome"):
        detail = d.get("error", "")
        print(f"[{d['outcome']}] {q}" + (f" | {detail[:120]}" if detail else ""))
        continue

    scores = d.get("fact_scores") or []
    top = f" top={scores[0]}" if scores else ""
    print(f"[{d.get('status')}/{d.get('route')}{top}] {q}"
          f"  ->  {(d.get('answer') or '')[:110]}")

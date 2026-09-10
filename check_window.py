"""Where does the answering fact actually rank? A measurement, not a test.

    uv run python check_window.py

No model, no API calls -- it reuses the question vectors cached by
check_drift.py, so the whole thing is SQL. That is the only reason this exists:
FACT_WINDOW has been an open question since the base held 30 facts and now
holds 140, and measuring it used to cost 90 completion calls and nine minutes.

WHAT IT ANSWERS. For every question whose `want` names a row, the wanted fact
is in exactly one of five places:

    exact          the deterministic tier already returns it. The window is
                   irrelevant -- exact facts go in first and are never subject
                   to the floor.
    in window      inside the top FACT_WINDOW and above SIMILARITY_FLOOR.
    beyond window  above the floor, but ranked past FACT_WINDOW.
                   THIS IS WHAT THE WINDOW COSTS. Nothing else on this list is.
    below floor    in the base, but scored under SIMILARITY_FLOOR. Lost to the
                   floor, not to the window -- widening cannot recover it.
    absent         no such fact. A data problem wearing a retrieval costume.

Only `beyond window` is an argument for changing FACT_WINDOW. Conflating it
with `below floor` is how a threshold gets moved for a reason that is really
about a different threshold.

WHAT IT DOES NOT ANSWER. Whether a wider window makes the model answer worse.
Every extra fact is more adjacent context, and rule 1b is what stops adjacent
context becoming a confident wrong answer -- that is a judgement about
generation, and this file never calls the model. The cost side is reported as
"how much extra context arrives", which is the measurable half.
"""

import json
import pathlib
import sys

from app.answer import (FACT_WINDOW, SIMILARITY_FLOOR, _SEARCH_FACTS,
                        _expand_list)
from app.db import connection, harness_business, pool
from app.embeddings import DIMENSIONS, MODEL
from app.retrieval import find
from grading import matches
from questions import QUESTIONS

sys.stdout.reconfigure(encoding="utf-8")

CACHE = pathlib.Path("question_vectors.json")
# Deep enough to find a fact wherever it is: the whole retrievable base.
DEEP = 200
WIDTHS = (4, 8, 12, 16, 20, 30)

if not CACHE.exists():
    sys.exit("ABORT: no question vectors. Run: uv run python check_drift.py --embed")
data = json.loads(CACHE.read_text(encoding="utf-8"))
if data.get("stamp") != f"{MODEL}@{DIMENSIONS}":
    sys.exit(f"ABORT: cache built with {data.get('stamp')!r}, code uses "
             f"{MODEL}@{DIMENSIONS!r}. Rebuild it.")

import hashlib
vectors = data["vectors"]


def key(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


rows = []
with pool:
    with connection(harness_business()) as conn:
        for q in QUESTIONS:
            if " / " not in q.get("want", ""):
                continue  # a shape, not a row -- nothing to rank
            k = key(q["q"])
            if k not in vectors:
                sys.exit(f"ABORT: no cached vector for {q['id']}. "
                         "Run: uv run python check_drift.py --embed")

            exact = find(conn, q["q"])
            exact_hit = (exact["status"] == "ok"
                         and any(matches(f, q["want"]) for f in exact["facts"]))

            scored = conn.execute(_SEARCH_FACTS,
                                  {"v": str(vectors[k]), "k": DEEP}).fetchall()
            rank = sim = None
            for n, r in enumerate(scored, 1):
                if matches({"subject": r[1], "attribute": r[2]}, q["want"]):
                    rank, sim = n, round(r[5], 3)
                    break

            if rank is None:
                place = "absent"
            elif sim < SIMILARITY_FLOOR:
                place = "below floor"
            elif rank <= FACT_WINDOW:
                place = "in window"
            else:
                place = "beyond window"

            # How much context each width would actually deliver -- only what
            # clears the floor, because that is all answer() passes on.
            above = [round(r[5], 3) for r in scored if r[5] >= SIMILARITY_FLOOR]

            # AND WHAT LIST EXPANSION RESCUES. Measuring the raw window alone
            # would have said doctors-ru needs a window of 10, when in fact
            # several `lavozim` facts cluster inside the top 8 and expansion
            # then pulls in EVERY doctor's role, wanted one included. Judging a
            # threshold by a path the answer does not take is the mistake this
            # codebase keeps making, so the real _expand_list() is called here
            # rather than reasoned about.
            reached = {}
            for width in WIDTHS:
                usable = [{"subject": r[1], "attribute": r[2],
                           "attribute_key": r[3], "value": r[4],
                           "similarity": round(r[5], 3)}
                          for r in scored[:width] if r[5] >= SIMILARITY_FLOOR]
                expanded = _expand_list(conn, usable)
                reached[width] = (any(matches(f, q["want"]) for f in usable),
                                  any(matches(f, q["want"]) for f in expanded),
                                  len(expanded))

            rows.append({"q": q, "exact": exact_hit, "rank": rank, "sim": sim,
                         "place": place, "above": above, "reached": reached})

print(f"FACT_WINDOW = {FACT_WINDOW}, SIMILARITY_FLOOR = {SIMILARITY_FLOOR}, "
      f"{len(rows)} questions name a row\n")

for place in ("exact", "in window", "beyond window", "below floor", "absent"):
    group = [r for r in rows if (r["place"] == place
                                 and not (place != "exact" and r["exact"]))
             ] if place != "exact" else [r for r in rows if r["exact"]]
    print(f"{place:15} {len(group):3}")
    for r in group:
        if place in ("beyond window", "below floor", "absent"):
            print(f"    {r['q']['id']:28} rank={r['rank']} sim={r['sim']}")
            print(f"      want: {r['q']['want']}")

reachable = [r for r in rows if not r["exact"]]
print()
print(f"of the {len(reachable)} that the exact tier does NOT already answer:")
for width in WIDTHS:
    raw = sum(1 for r in reachable if r["reached"][width][0])
    exp = sum(1 for r in reachable if r["reached"][width][1])
    context = sum(r["reached"][width][2] for r in rows) / max(len(rows), 1)
    mark = "  <- current" if width == FACT_WINDOW else ""
    print(f"  window {width:2}: {raw:3} in the window, {exp:3} after list "
          f"expansion, of {len(reachable)}   "
          f"{context:.1f} facts of context per question{mark}")

print()
print("still missed at the widest width tried, after expansion:")
for r in reachable:
    if not r["reached"][WIDTHS[-1]][1]:
        print(f"  {r['q']['id']:28} rank={r['rank']} sim={r['sim']}")
        print(f"    want: {r['q']['want']}")
        print(f"    {r['q']['q']}")

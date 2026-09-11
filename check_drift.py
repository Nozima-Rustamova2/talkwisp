"""What changed in RETRIEVAL since last time. No model, no API calls, no cost.

    uv run python check_drift.py --embed    fill the question-vector cache
    uv run python check_drift.py            diff against the last snapshot

READ THIS BEFORE TRUSTING THE OUTPUT
------------------------------------

**This narrows which cases to read. It does not judge them.** A question listed
here retrieves something different from last time. That may be a fix, a
regression, or an expectation that has quietly expired -- this file cannot tell
you which, and does not try. The output is an input to a human reading the
case, and its `note` field, and asking whether the stated reason is still the
reason.

**It cannot see a stale reason.** `gap-appointment` had the right verdict and a
justification that stopped being true when a document was ingested. Nothing
here would have flagged it: its retrieval never moved. That failure has no
mechanism, only the habit of reading a case's reason whenever you touch it.

**It reports rather than fails.** Exit code is 0 whatever it finds. A retrieval
change is not a defect, and a check that cried wolf on every ingest would be
turned off within a week.

WHY IT EXISTS
-------------

A code change announces itself in a diff. An ingested document changes what is
true for a dozen questions and touches nothing a reviewer would look at. That
asymmetry is how three expectations sat stale for two days: the policy source
arrived on 2026-09-03 and `live-how-to-book`, `syn-book-gynae` and
`gap-appointment` all became wrong about their own reasons, with every test
still green. This gives ingestion something diff-shaped.

WHY THE QUESTION VECTORS ARE CACHED
-----------------------------------

The first design diffed `find()` alone, because it is deterministic and free.
That would have reported NOTHING for the ingestion that motivated it:
`retrieval.py` never queries `chunk`, and the policy source produced 8 chunks
and 0 facts. Free and blind is not cheaper than free and useful.

Embedding the fixed question set ONCE makes both vector tiers pure SQL --
the chunk and fact embeddings are already in the database, and
`order by embedding <=> %s::vector` needs no model. Every run after the first
is free and deterministic, and covers the whole retrieval picture: exact tier,
fact vector tier, chunk vector tier. That is `answer()` minus the model.

The cache is DERIVED DATA, not knowledge, which is why it is a file and not a
table, and why it is gitignored: it can always be rebuilt from questions.py.
"""

import hashlib
import json
import pathlib
import sys
import time

from app.answer import (FACT_WINDOW, SIMILARITY_FLOOR, _SEARCH_CHUNKS,
                        _SEARCH_FACTS)
from app.db import connection, harness_business, pool
from app.embeddings import DIMENSIONS, MODEL, embed_query
from app.retrieval import find
from questions import QUESTIONS

sys.stdout.reconfigure(encoding="utf-8")

CACHE = pathlib.Path("question_vectors.json")
SNAPSHOT = pathlib.Path("retrieval_snapshot.json")

# Stamped into the cache and checked on every load. If the embedding model
# changes, every cached vector becomes meaningless -- and the failure would be
# a diff that is confidently wrong rather than obviously broken, because the
# arithmetic still works on nonsense. Same reasoning as the `embedding_model`
# column on chunk and fact.
STAMP = f"{MODEL}@{DIMENSIONS}"

CHUNK_LIMIT = 3  # matches the default in answer.search()


def key(text: str) -> str:
    """Cache key. On the TEXT, so an edited question re-embeds itself rather
    than silently reusing the vector of the question it used to be."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_cache() -> dict:
    if not CACHE.exists():
        return {}
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    if data.get("stamp") != STAMP:
        sys.exit(
            f"ABORT: the vector cache was built with {data.get('stamp')!r} and "
            f"this code embeds with {STAMP!r}.\n"
            "  Every cached vector is meaningless across a model change, and a "
            "diff computed from them would be confidently wrong rather than\n"
            "  obviously broken. Delete question_vectors.json and re-run with "
            "--embed."
        )
    return data.get("vectors", {})


def embed_missing() -> None:
    vectors = load_cache()
    missing = [q for q in QUESTIONS if key(q["q"]) not in vectors]
    if not missing:
        print(f"cache complete: {len(vectors)} vectors, {STAMP}")
        return
    print(f"embedding {len(missing)} question(s)...")
    # BOUND, and the pool opened, because embedding is gated on the tenant's
    # approval now. This ran outside both -- it is the only path in this file
    # that touches a model and it happens before the `with pool` block at the
    # bottom, so nothing here had a tenant to be asked about.
    with pool, connection(harness_business()):
        for n, q in enumerate(missing, 1):
            vectors[key(q["q"])] = [round(x, 6) for x in embed_query(q["q"])]
            print(f"  {n}/{len(missing)}  {q['id']}")
            time.sleep(1)
    CACHE.write_text(json.dumps({"stamp": STAMP, "vectors": vectors}),
                     encoding="utf-8")
    # Vectors are keyed by question TEXT, so two questions with identical text
    # share one -- `room-cardio-uz` and `triage-control-no-symptom` are the
    # same string on purpose, kept apart because they would be deleted for
    # different reasons. Saying "90 questions -> 89 vectors" out loud stops
    # that reading as something lost.
    distinct = len({key(q["q"]) for q in QUESTIONS})
    print(f"cached {len(vectors)} vectors under {STAMP}"
          f"  ({len(QUESTIONS)} questions, {distinct} distinct texts)")


def snapshot(conn, vectors: dict) -> dict:
    """The whole retrieval picture for every question, deterministically.

    The two vector queries are IMPORTED from app/answer.py rather than rewritten
    here. A second copy of the retrieval SQL would be a second definition of
    what retrieval means, and it would drift -- which is the failure this file
    exists to report.
    """
    out = {}
    for q in QUESTIONS:
        vector = str(vectors[key(q["q"])])
        exact = find(conn, q["q"])
        facts = conn.execute(_SEARCH_FACTS,
                             {"v": vector, "k": FACT_WINDOW}).fetchall()
        chunks = conn.execute(_SEARCH_CHUNKS,
                              {"v": vector, "k": CHUNK_LIMIT}).fetchall()
        out[q["id"]] = {
            "exact": {
                "subjects": exact["subjects"],
                "attribute": exact["attribute"],
                "status": exact["status"],
                "matched_on": exact["matched_on"],
                "facts": sorted(f"{f['subject']} / {f['attribute']}"
                                for f in exact["facts"]),
            },
            # ONLY WHAT CLEARS THE FLOOR, because that is only what answer()
            # ever puts in front of the model. The first version recorded the
            # raw windows and reported 90 of 90 questions on a single document
            # ingest -- every question's chunk window went from empty to three,
            # including chunks scoring 0.31 that could never reach an answer.
            # A list of everything is the same as no list. Reporting what the
            # ANSWER would use is what makes this "go read these four".
            "facts": [[f"{r[1]} / {r[2]}", round(r[5], 3)] for r in facts
                      if r[5] >= SIMILARITY_FLOOR],
            # Chunks are keyed by a hash of their CONTENT, not their row id.
            # Re-ingesting an identical document mints new uuids, and reporting
            # that as a change would be reporting the row, not the knowledge.
            "chunks": [[key(r[1]), round(r[2], 3)] for r in chunks
                       if r[2] >= SIMILARITY_FLOOR],
            # Routes can be identical while a price changes underneath them.
            # That is still a case to go and read.
            "values": key("|".join(sorted(
                f"{f['subject']}/{f['attribute']}/{f['value']}"
                for f in exact["facts"]))),
        }
    return out


# How many movers to describe in full before switching to a plain list. The
# rest are still named -- nothing is hidden -- but a hundred paragraphs is the
# same as no report, and the point of this file is to be read.
DETAIL = 12


def rank(a: dict, b: dict) -> tuple:
    """Sharpest signal first.

    An exact-tier change means the deterministic route moved, which is the most
    specific thing that can happen. A fact-window change is next. A chunk-window
    change is last and is sorted by score, because the first document ingested
    into an empty chunk table legitimately moves almost every question -- that
    is a true alarm about a large change, not noise, and the ordering is what
    makes it usable rather than the filtering.
    """
    exact = a["exact"] != b["exact"]
    facts = [x[0] for x in a["facts"]] != [x[0] for x in b["facts"]]
    # Rank on the score of what ARRIVED, not on the absolute top score. The
    # first version used the latter and put questions with strong existing
    # retrieval at the top regardless of what had changed about them, which is
    # ranking by how well a question already worked.
    was = {x[0] for x in a["chunks"]} | {x[0] for x in a["facts"]}
    entered = [x[1] for x in b["chunks"] + b["facts"] if x[0] not in was]
    return (not exact, not facts, -max(entered, default=0.0))


def report(old: dict, new: dict) -> int:
    by_id = {q["id"]: q for q in QUESTIONS}

    gone = sorted(set(old) - set(new))
    added = sorted(set(new) - set(old))
    if gone:
        print(f"\n  questions removed from the set: {', '.join(gone)}")
    if added:
        print(f"\n  questions added to the set: {', '.join(added)}")

    changed = [qid for qid in set(old) & set(new) if old[qid] != new[qid]]
    changed.sort(key=lambda qid: rank(old[qid], new[qid]))
    moved = len(changed)

    if moved > DETAIL:
        print()
        print(f"  {moved} of {len(new)} questions moved. That is most of the set,")
        print("  which happens when a first document lands in an empty chunk")
        print("  table -- every question's context really did change.")
        print(f"  Sharpest signal first; the first {DETAIL} in full, the rest")
        print("  named at the end.")

    for qid in changed[:DETAIL]:
        a, b = old[qid], new[qid]
        q = by_id[qid]
        print(f"\n  {qid}   expect={q['expect']}")
        print(f"    {q['q']}")
        if a["exact"] != b["exact"]:
            print(f"    exact tier:  {a['exact']['status']} "
                  f"{a['exact']['subjects'] or '-'} "
                  f"-> {b['exact']['status']} {b['exact']['subjects'] or '-'}")
            for label, side in (("was", a), ("now", b)):
                if side["exact"]["facts"]:
                    print(f"      {label}: {', '.join(side['exact']['facts'][:4])}")
        if a["facts"] != b["facts"]:
            was, now = [x[0] for x in a["facts"]], [x[0] for x in b["facts"]]
            entered = [x for x in now if x not in was]
            left = [x for x in was if x not in now]
            if entered:
                print(f"    fact window gained: {', '.join(entered)}")
            if left:
                print(f"    fact window lost:   {', '.join(left)}")
            if not entered and not left:
                print("    fact window reordered")
        if a["chunks"] != b["chunks"]:
            print(f"    chunk window: {len(a['chunks'])} -> {len(b['chunks'])}"
                  f"  top score {a['chunks'][0][1] if a['chunks'] else '-'}"
                  f" -> {b['chunks'][0][1] if b['chunks'] else '-'}")
        if a["values"] != b["values"] and a["exact"] == b["exact"]:
            print("    same facts retrieved, VALUES changed")
        if q.get("note"):
            print(f"    its stated reason -- still true?")
            print(f"      {q['note'][:200]}")

    rest = changed[DETAIL:]
    if rest:
        print()
        print(f"  also moved, less sharply ({len(rest)}):")
        print("    " + ", ".join(rest))
    return moved


if __name__ == "__main__":
    if "--embed" in sys.argv:
        embed_missing()
        sys.exit(0)

    vectors = load_cache()
    # The loud miss. A run that silently diffed 89 of 90 questions would be
    # reporting on a set nobody asked about, and the one it dropped is exactly
    # the one somebody just edited. Same shape as the empty-chunk guard in
    # check_answer.py: fail the run rather than warn, because a warning gets
    # scrolled past.
    absent = [q["id"] for q in QUESTIONS if key(q["q"]) not in vectors]
    if absent:
        sys.exit(
            f"ABORT: {len(absent)} of {len(QUESTIONS)} questions have no cached "
            f"vector.\n  {', '.join(absent[:10])}"
            f"{' ...' if len(absent) > 10 else ''}\n"
            "  A question is keyed by its TEXT, so this means they are new or "
            "were edited.\n"
            "  Run: uv run python check_drift.py --embed"
        )

    with pool:
        with connection(harness_business()) as conn:
            current = snapshot(conn, vectors)

    if not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(current, indent=1), encoding="utf-8")
        print(f"no previous snapshot; recorded {len(current)} questions.")
        print("re-run after an ingest to see what moved.")
        sys.exit(0)

    previous = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    print(f"comparing {len(current)} questions against the last snapshot "
          f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(SNAPSHOT.stat().st_mtime))})")
    moved = report(previous, current)
    SNAPSHOT.write_text(json.dumps(current, indent=1), encoding="utf-8")

    if moved:
        print()
        print(f"{moved} question(s) retrieve differently. Go and read them --")
        print("this says they MOVED, not that they are wrong.")
    else:
        print()
        print("nothing moved.")

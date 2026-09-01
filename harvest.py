"""Turn real Telegram questions into test-case stubs.

    uv run python harvest.py                 read the log, write stubs, show why
    uv run python harvest.py --rerun         also run them through the system now

The hand-written test set stopped discriminating at 30/30. Every real bug found
in the first week came from live phone messages, not from the harness -- the
two-of-six doctor list, the too-narrow retrieval window, the "ва" inside "вас"
language misdetection. Real phrasings are the only source of new signal.

This produces STUBS, not finished tests. `expect` and `want` are left as ??? on
purpose: what the right answer is, is a judgement about the business, and only
a person can make it. What the script can do is carry across everything the log
already knows -- the route, the scores, the answer given -- so grading is mostly
confirming what should have happened.

One thing it deliberately does NOT decide: whether a refusal was correct. A
refusal on something the clinic knows is a retrieval gap; a refusal on something
it does not know is the system working. Those look identical in the log and need
opposite fixes. So for every refusal it re-runs retrieval and prints the nearest
fact and its score, which is the evidence needed to tell them apart.
"""

import argparse
import collections
import json
import pathlib
import re
import sys

from app.answer import SIMILARITY_FLOOR, detect_language, search
from app.db import pool
from app.normalize import normalize

sys.stdout.reconfigure(encoding="utf-8")

LOG = pathlib.Path("messages.jsonl")
OUT = pathlib.Path("questions_harvested.py")

# These never reached retrieval, so there is nothing to grade:
#   social      answered by the canned greeting/thanks short-circuit
#   throttled   suppressed by the rate limit; no answer was produced
#   fact_*      the owner writing knowledge, not a customer asking
SKIP_OUTCOMES = {"social", "throttled", "fact_written", "fact_write_error"}

LANGS = {"Uzbek, in CYRILLIC script": "uz-cyrl", "Russian": "ru"}


def language(text: str) -> str:
    # Latin script could be Uzbek, English or code-switched; the answer path
    # deliberately does not guess between them, so neither does this.
    return LANGS.get(detect_language(text), "uz-latn")


def slug(text: str) -> str:
    words = [w for w in normalize(text).split() if w][:3]
    return "live-" + ("-".join(words) or "question")


def load() -> list[dict]:
    if not LOG.exists():
        raise SystemExit(f"{LOG} does not exist yet. Nothing to harvest.")
    rows = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def usable(rows: list[dict]) -> list[dict]:
    """Rows worth turning into test cases.

    Social messages are filtered by running bot.social() against the TEXT, not
    only by trusting the logged outcome. The log records what happened at the
    time, and the short-circuit did not exist for the first messages ever sent
    -- so "assalomu" and "rahmat" were logged as ordinary questions and became
    stubs. Judging the text applies today's rules to yesterday's log.
    """
    from bot import social

    kept = []
    for r in rows:
        if r.get("outcome") in SKIP_OUTCOMES:
            continue
        question = (r.get("question") or "").strip()
        if not question:
            continue
        if "status" not in r:  # an error row: no route, no answer to grade
            continue
        if social(question) is not None:
            continue
        kept.append(r)
    return kept


def dedupe(rows: list[dict]) -> list[dict]:
    """The same question asked five times is one test case.

    Keyed on the NORMALIZED text, so "Rasulova" and "rasulova" collapse. That is
    lossy -- two questions differing only in punctuation become one -- and the
    repeat count is printed so a surprising merge is visible rather than silent.
    """
    seen: dict[str, dict] = {}
    for r in rows:
        key = normalize(r["question"])
        if key in seen:
            seen[key]["asked"] += 1
        else:
            seen[key] = dict(r, asked=1)
    return list(seen.values())


def stub(case: dict, nearest: dict | None) -> str:
    q = case["question"].replace('"', '\\"')
    answer = (case.get("answer") or "").replace('"', '\\"')
    refused = case["status"] == "unknown"

    note = [f"LIVE, asked {case['asked']}x. route={case.get('route') or 'none'}."]
    scores = case.get("fact_scores") or []
    if scores:
        note.append(f"top fact score {scores[0]}.")
    if refused:
        note.append("REFUSED.")
        if nearest:
            note.append(
                f"Nearest fact was {nearest['subject']} / {nearest['attribute']}"
                f" at {nearest['similarity']}"
                f" ({'ABOVE' if nearest['similarity'] >= SIMILARITY_FLOOR else 'below'}"
                f" the {SIMILARITY_FLOOR} floor)."
                f" DECIDE: does the clinic know this? If yes -> retrieval gap,"
                f" expect='fact'. If no -> correct, expect='gap'."
            )
        else:
            note.append("Nothing retrieved at all -> almost certainly expect='gap'.")
    else:
        note.append(f"Answered: {answer[:110]!r}")

    wrapped = "\n              ".join(
        f'"{part} "' for part in _wrap(" ".join(note), 66))

    return (
        f'    dict(id="{slug(case["question"])}", lang="{language(case["question"])}",\n'
        f'         expect="???",   # fact | prose | gap | ask-which\n'
        f'         q="{q}",\n'
        f'         want="???",\n'
        f'         note={wrapped}),\n'
    )


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun", action="store_true",
                    help="also run each harvested question through the system now")
    args = ap.parse_args()

    rows = load()
    kept = usable(rows)
    cases = dedupe(kept)

    print(f"{len(rows)} logged -> {len(kept)} reached retrieval -> "
          f"{len(cases)} distinct questions\n")

    dropped = collections.Counter(
        r.get("outcome") for r in rows if r.get("outcome") in SKIP_OUTCOMES)
    if dropped:
        print(f"  skipped: {dict(dropped)}\n")

    print("  by language:", dict(collections.Counter(
        language(c["question"]) for c in cases)))
    print("  by route   :", dict(collections.Counter(
        c.get("route") or "refused" for c in cases)))
    print()

    stubs, refusals = [], []
    # One `with pool:` for the whole run -- a ConnectionPool cannot be reopened
    # once closed, so opening it again for the re-run raises PoolClosed.
    with pool:
        with pool.connection() as conn:
            for case in sorted(cases, key=lambda c: c["status"]):
                nearest = None
                if case["status"] == "unknown":
                    facts, _ = search(conn, case["question"], limit=1)
                    nearest = facts[0] if facts else None
                    refusals.append((case, nearest))
                stubs.append(stub(case, nearest))

        _report(stubs, refusals)
        if args.rerun:
            with pool.connection() as conn:
                rerun(conn, cases)
        return


def _report(stubs: list[str], refusals: list) -> None:
    header = (
        '"""Harvested from messages.jsonl -- real questions from real people.\n\n'
        "Stubs, not tests. Fill in `expect` and `want`, then move the ones worth\n"
        "keeping into questions.py. `note` carries what the log already knew.\n"
        '"""\n\nHARVESTED = [\n'
    )
    OUT.write_text(header + "".join(stubs) + "]\n", encoding="utf-8")
    print(f"wrote {len(stubs)} stubs to {OUT}\n")

    if refusals:
        print("=" * 70)
        print("REFUSALS -- these need your judgement, not mine")
        print("=" * 70)
        for case, nearest in refusals:
            print(f"\n  {case['question']}")
            if nearest:
                mark = ("above" if nearest["similarity"] >= SIMILARITY_FLOOR
                        else "below")
                print(f"    nearest: {nearest['subject']} / {nearest['attribute']}"
                      f" = {nearest['value']}")
                print(f"             {nearest['similarity']} ({mark} the "
                      f"{SIMILARITY_FLOOR} floor)")
            else:
                print("    nothing retrieved")


def rerun(conn, cases: list[dict]) -> None:
    """What the system does with these questions NOW. Costs one generation each.

    Takes an open connection rather than opening the pool: a ConnectionPool
    cannot be reopened once closed, and the caller already holds it.
    """
    from app.answer import answer

    print("\n" + "=" * 70)
    print("RE-RUN AGAINST THE CURRENT SYSTEM")
    print("=" * 70)
    for case in cases:
        now = answer(conn, case["question"])
        changed = (now["status"] != case["status"]
                   or (now["source"] or "") != (case.get("route") or ""))
        flag = "CHANGED" if changed else "same   "
        print(f"\n[{flag}] {case['question']}")
        print(f"    then: {case['status']}/{case.get('route') or '-'}")
        print(f"    now : {now['status']}/{now['source'] or '-'}")
        print(f"    > {now['answer']}")


if __name__ == "__main__":
    main()

"""The questions that flip, run enough times to show it.

    uv run python check_fragile.py            three runs each
    uv run python check_fragile.py --runs 5

WHY A SEPARATE, TINY HARNESS. check_answer.py asks ninety questions once each,
takes an hour, and spends ninety generations. That is the right shape for "did
anything move", and the wrong shape for "does THIS question have a stable
answer" -- which needs the same question several times and nothing else.

THEY DO NOT MOVE ON THEIR OWN. That was the first conclusion and it was wrong.
Comparing two ninety-question runs showed three differences, which looked like
run-to-run noise on a non-deterministic model. Repeating just these three, three
times per configuration, took four minutes and showed the opposite:

    question                      none   informal   emoji   both
    boundary-close-en             PASS     PASS      FAIL   FAIL
    temporal-day-after-tomorrow   PASS     FAIL      FAIL   FAIL
    temporal-next-tuesday         FAIL     PASS      FAIL   PASS

Every cell is three identical verdicts. Perfectly steady WITHIN a configuration
and different BETWEEN them -- so the style block causes all three, and "within
variance" was a guess dressed as a measurement. The lesson is the cheap one:
three repeats of three questions answered what an hour of ninety could not,
because the question was never "did anything move" but "does this move on its
own".

WHAT THE TABLE SAYS. The emoji option is the expensive one: two regressions and
no gains. Its first version also ended "never beside a price, a time or a
number", and removing that clause recovered boundary-close-en -- a question
about a closing TIME. A protective clause perturbed the decisions it named.
Rule 3 already forbids altering a price or a time, so the clause bought little
and cost a measurable failure.

The informal register is a genuine trade: it breaks one and fixes one.

WHAT MAKES THEM FRAGILE, as far as the evidence goes:

  the two temporal ones sit on rule 8 -- a day referred to only relatively
  cannot be resolved and must be refused, while a day NAMED outright must be
  answered. "Kelasi seshanba" names Tuesday; "indinga" does not name anything.
  The distinction is one clause of one rule, and it is the rule that moved
  under tone pressure.

  boundary-close-en sits on rule 1b. Its retrieval is IDENTICAL across runs --
  fact scores [0.606, 0.593, ...], nearest "SMM PRO / qabul vaqti" -- so the
  model saw the same context and decided differently. 0.606 is barely over the
  floor and the nearest fact is a course's session time, not the clinic's
  closing hour, which is precisely the "merely related" material rule 1b says
  to refuse on. The refusal may be the better call and the grader the thing
  that is wrong.

This does not fix them. It measures how often they move, so the next person
comparing two runs knows what a difference of one is worth.
"""

import argparse
import collections
import sys

from dotenv import load_dotenv

from app.answer import answer
from app.db import connection, harness_business, pool
from grading import grade
from questions import QUESTIONS

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

FRAGILE = ("temporal-next-tuesday", "temporal-day-after-tomorrow",
           "boundary-close-en")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3,
                    help="how many times to ask each question")
    # Same opt-out as check_answer.py, and for the same reason: measuring a
    # configured agent is legitimate, measuring one by accident is not.
    ap.add_argument("--with-style", action="store_true",
                    help="measure a configured agent deliberately")
    args = ap.parse_args()

    cases = [q for q in QUESTIONS if q["id"] in FRAGILE]
    missing = set(FRAGILE) - {q["id"] for q in cases}
    if missing:
        # A question renamed out from under this list would otherwise make the
        # run quietly measure fewer things than it claims to.
        raise SystemExit(f"not in questions.py any more: {sorted(missing)}")

    pool.open()
    seen: dict[str, list] = collections.defaultdict(list)
    try:
        with connection(harness_business()) as conn:
            from app import style
            block = style.block(conn)
            if block and not args.with_style:
                raise SystemExit(
                    "the harness business has a personality set; clear it or "
                    "pass --with-style if that is what you meant.")
            # The run says what it measured, so a verdict and its
            # configuration never travel separately.
            print("  style:",
                  " | ".join(block.strip().splitlines()) if block
                  else "none set (the default prompt)")
            for _ in range(args.runs):
                for case in cases:
                    result = answer(conn, case["q"])
                    seen[case["id"]].append(grade(case, result) or "MANUAL")
    finally:
        pool.close()

    print(f"\n  {args.runs} runs of {len(cases)} questions\n")
    unstable = 0
    for case in cases:
        verdicts = seen[case["id"]]
        steady = len(set(verdicts)) == 1
        unstable += not steady
        mark = "steady" if steady else "FLIPS "
        print(f"  [{mark}] {case['id']:30} {' '.join(verdicts)}")

    print(f"\n  {unstable} of {len(cases)} flipped within this session.")
    if unstable:
        print("  A one-question difference between two full runs is worth "
              "nothing on these.")
    # NOT a failure. Flipping is the thing being measured, not a regression --
    # exiting non-zero would make a CI run red for reporting what it was asked
    # to report.
    sys.exit(0)


if __name__ == "__main__":
    main()

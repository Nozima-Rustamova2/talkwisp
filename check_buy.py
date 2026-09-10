"""Measure the buy-intent classifier, and check the offer it produces.

    uv run python check_buy.py

THE FALSE-POSITIVE RATE IS THE POINT. None of the 90 questions in questions.py
is a request to buy anything -- they are prices, hours, rooms, symptoms and
refusals. So every `buy: true` on that set is a false positive, and the count
is the number that decides whether this feature is safe to build a Telegram
flow on top of.

The asymmetry is not symmetric. A miss costs one ordinary answer. A false
positive puts a purchase button under a question that was never about buying,
and "Kardiolog qabuli qancha turadi?" -- how much does a cardiologist cost --
is one word away from a purchase in Uzbek. That is the pair this measures.

A second, much smaller set of REAL buy intents follows, to check the classifier
is not simply answering false to everything. A detector that never fires scores
a perfect false-positive rate and is useless.

Costs one completion call per question. Nothing is written to the database.
"""

import sys
import time

from app.db import connection, harness_business, pool
from app import buy, orders, payment
from questions import QUESTIONS

sys.stdout.reconfigure(encoding="utf-8")

# PREPAYMENT intents. If the classifier misses these it is not conservative,
# it is broken -- a detector that never fires scores a perfect false-positive
# rate and is useless.
WANTED = [
    ("uz-latn", "Kardiolog qabuli uchun to'lovni amalga oshirmoqchiman"),
    ("uz-latn", "EKG uchun pul to'lamoqchiman, qayerga yuboray?"),
    ("uz-cyrl", "Кардиолог қабули учун тўловни амалга оширмоқчиман"),
    ("ru", "Хочу оплатить ЭКГ"),
    ("ru", "Хочу оплатить приём кардиолога"),
]

# SCHEDULING intents. These MUST NOT fire, and THREE OF THEM WERE IN `WANTED`
# until 2026-09-05 -- which is exactly why they are written down here instead
# of assumed. Avisena cannot reserve a slot, so offering to take payment for
# one charges a customer for a time nobody can promise. They fall through to
# ordinary retrieval, where the clinic's own stated booking instruction answers
# them. See app/buy.py.
NOT_WANTED = [
    ("uz-latn", "Kardiologga yozilmoqchiman"),
    ("uz-latn", "Menga ginekologga zapis qberila."),
    ("uz-cyrl", "Кардиологга ёзилмоқчиман"),
    ("ru", "Хочу записаться к кардиологу"),
]

passed = failed = 0

# Everything below runs under __main__ so that WANTED and NOT_WANTED can be
# IMPORTED. They were module-level, and importing this file to reuse the
# control sets ran the entire ninety-question measurement -- ninety completion
# calls for two lists. A test set nobody can import gets copied instead, and a
# copied control set is a second definition that drifts.


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def main():
    with pool:
        with connection(harness_business()) as conn:

            print("\nfalse positives -- 90 questions, none of them a purchase")
            fired = []
            for q in QUESTIONS:
                got = buy.classify(conn, q["q"])
                if got["buy"]:
                    fired.append((q["id"], q["q"], got["subjects"]))
                    print(f"  FIRED  {q['id']:24} {q['q']}")
                    print(f"         -> {got['subjects']}")
                time.sleep(4)  # free tier is per-minute limited; pace the loop

            rate = len(fired) / len(QUESTIONS)
            print(f"\n  {len(fired)} of {len(QUESTIONS)} = {rate:.1%} false positive")
            # Not a pass/fail assertion, because the right threshold is a judgement
            # about product feel, not a number this file gets to invent. Printed
            # for a human, and recorded in docs/design-decisions.md.

            print("\nprepayment intents -- these MUST fire")
            for lang, message in WANTED:
                got = buy.classify(conn, message)
                check(f"{lang:8} {message[:44]}", got["buy"], True)
                if got["buy"]:
                    print(f"         -> {got['subjects']}")
                time.sleep(4)

            print("\nscheduling intents -- these MUST NOT fire")
            for lang, message in NOT_WANTED:
                got = buy.classify(conn, message)
                check(f"{lang:8} {message[:44]}", got["buy"], False)
                if got["buy"]:
                    print(f"         WRONGLY OFFERED: {got['subjects']}")
                time.sleep(4)

            print("\nthe offer, once intent is established")

            # Straight to buttons via the classifier is one more API call per case,
            # so the resolution below is exercised directly. classify() is measured
            # above; this checks what happens AFTER it says yes.
            from app.normalize import normalize

            doctor = normalize("Rahimov Alisher Bahodirovich")
            options = orders.price_options(conn, doctor)
            check("a doctor offers a choice rather than a guess", len(options), 2)
            check("both are orderable",
                  all(o["amount"] is not None for o in options), True)
            print("         buttons a customer would see:")
            for o in options:
                print(f"           [ {o['attribute']} -- {o['value']} ]")

            ekg = normalize("EKG")
            check("a single-priced service needs no choice",
                  len(orders.price_options(conn, ekg)), 1)

            check("a subject nobody sells is not purchasable",
                  buy.resolve(conn, "kosmodrom"), [])

            # AXIS ONE. Customers ask by specialty, never by full name, so the
            # alias route is the normal one and the canonical name is the rare one.
            check("an alias reaches the doctor customers cannot name",
                  buy.resolve(conn, "kardiolog"), ["Rahimov Alisher Bahodirovich"])
            check("so does the Russian spelling of it",
                  buy.resolve(conn, "Кардиолог"), ["Rahimov Alisher Bahodirovich"])
            check("a surname two doctors share resolves to BOTH, never to one",
                  len(buy.resolve(conn, "Karimov")), 2)

            print("\nthe order message -- amount from the row, nothing composed")
            order = {"amount": 60001}
            for lang in ("the same language the customer wrote in, in LATIN script",
                         "Uzbek, in CYRILLIC script", "Russian"):
                text = payment.order_message(conn, lang, order)
                check(f"the exact amount appears verbatim ({lang[:18]})",
                      "60 001 soʻm" in text, True)
                check("the card block is unchanged from message()",
                      payment.message(conn, lang) in text, True)
            print("\n" + payment.order_message(
                conn, "the same language the customer wrote in, in LATIN script",
                order))

    print(f"\n{passed} passed, {failed} failed, {len(fired)} false positives")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

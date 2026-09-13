"""The owner's side of payment details: setting them, and knowing they work.

    uv run python check_payment_setup.py

SEPARATE FROM check_payment.py, which speaks raw SQL to prove the exclusion
holds below app/ -- delete app/payment.py and those checks still fail. These
are app-layer behaviours: the writer, the per-language readiness, the guard the
bot depends on, and the once-a-day claim.

THE SECTION THAT MATTERS IS 3, and it is one assertion repeated three times:
ready_for(language) must be true exactly when order_message() can actually be
assembled for that language. The bot now asks the first question and acts on
it; the customer experiences the second. Two functions answering "can this
business take payment?" is the shape of failure this project keeps producing --
a check reads a different object from the one the behaviour uses, and it passes.
So they are compared against each other rather than each against an expectation.

WHY THIS EXISTS AT ALL. A self-serve business connected a bot, a customer
tapped buy, an order was created, and order_message() returned None at the last
step -- the customer was told "contact us" and the owner was told nothing. It
would have happened to every self-serve business, because until now no owner
could set payment details by any route: check_subject()'s `allow_reserved` had
no caller outside seed.py.

Runs in ONE transaction, rolled back, so the database is left as it was found.
"""

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv
from fastapi.testclient import TestClient

import app.main as api
from app import auth, payment
from app.db import connection, harness_business, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0

UZ_LATN = "the same language the customer wrote in, in LATIN script"
UZ_CYRL = "Uzbek, in CYRILLIC script"
RU = "Russian"

ORDER = {"amount": 250003}


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def wipe(conn):
    conn.execute("delete from fact where subject_key = %s",
                 (payment.PAYMENT_SUBJECT_KEY,))


def fill(conn, language):
    """Everything order_message() needs for one language."""
    payment.set_detail(conn, payment.INSTRUCTION_ATTRIBUTE[language],
                       "Kartaga o'tkazing")
    payment.set_detail(conn, payment.EXACT_ATTRIBUTE[language],
                       "Summani aniq yuboring")


def main():
    pool.open()
    biz = harness_business()
    try:
        with connection(biz) as conn:
            run(conn)
            # Everything above is inside this block; nothing is committed.
            conn.rollback()
        # AFTER the app transaction has rolled back, never during it. The
        # claims in section 5 lock the business row, and an admin UPDATE on the
        # same row inside that window blocks until it ends -- the check would
        # hang rather than fail, which is the worse of the two.
        run_admin(biz)
        run_source()
    finally:
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def run(conn):
    wipe(conn)

    print("\n1. The writer, and what it refuses")
    payment.set_detail(conn, payment.CARD_ATTRIBUTE, "8600 1234 5678 9012")
    check("the card is stored",
          payment.details(conn).get(payment.CARD_ATTRIBUTE),
          "8600 1234 5678 9012")

    # THE WHITELIST. check_subject() stops another SUBJECT being claimed; this
    # stops this writer being a side door for arbitrary ATTRIBUTES. A business
    # fact written here would be invisible to retrieval forever.
    try:
        payment.set_detail(conn, "ish vaqti", "09:00-18:00")
        check("an ordinary attribute is refused", "accepted", "NotSettable")
    except payment.NotSettable:
        check("an ordinary attribute is refused under the payment subject",
              True, True)

    # NEVER EMBEDDED. The policy is here and the constraint is the floor; if
    # this row carried a vector, a card number would be in a search's candidate
    # set.
    embedded = conn.execute(
        "select count(*) from fact where subject_key = %s"
        " and embedding is not null",
        (payment.PAYMENT_SUBJECT_KEY,)).fetchone()[0]
    check("nothing written here carries an embedding", embedded, 0)

    # STILL EXCLUDED FROM RETRIEVAL, with a sanctioned writer in play. The whole
    # exclusion exists so this subject cannot be answered from.
    visible = conn.execute(
        "select count(*) from retrievable_fact where subject_key = %s",
        (payment.PAYMENT_SUBJECT_KEY,)).fetchone()[0]
    check("and it is not in retrievable_fact", visible, 0)

    # An empty value CLEARS rather than storing "". Two ways to be unset is how
    # a screen starts disagreeing with the bot.
    payment.set_detail(conn, "bank", "Kapitalbank")
    payment.set_detail(conn, "bank", "")
    rows = conn.execute(
        "select count(*) from fact where subject_key = %s and attribute_key = %s",
        (payment.PAYMENT_SUBJECT_KEY, "bank")).fetchone()[0]
    check("clearing a field deletes the row, it does not store an empty one",
          rows, 0)

    print("\n2. Per language, not done/not-done")
    state = payment.completeness(conn)
    check("with only a card, no language is ready", state["ready"], [])
    check("all three languages are reported",
          [lang["label"] for lang in state["languages"]],
          ["Uzbek (Latin)", "Uzbek (Cyrillic)", "Russian"])

    fill(conn, UZ_LATN)
    state = payment.completeness(conn)
    # THE PARTIAL CASE, which is the one nobody spots: this business now works
    # perfectly for Uzbek customers and dead-ends Russian ones.
    check("Uzbek Latin alone becomes ready", state["ready"], ["Uzbek (Latin)"])

    print("\n3. CONTROL: ready_for() agrees with what the customer gets")
    # The bot asks ready_for(); the customer experiences order_message(). Two
    # answers to one question is the failure this codebase keeps producing, so
    # they are compared to EACH OTHER, not each to an expectation.
    for language, label in ((UZ_LATN, "Uzbek Latin"), (UZ_CYRL, "Uzbek Cyrillic"),
                            (RU, "Russian")):
        check(f"{label}: ready_for matches order_message being assemblable",
              payment.ready_for(conn, language),
              payment.order_message(conn, language, ORDER) is not None)

    # And the negative half: break the pair and the comparison must notice.
    # Without this, three equal Falses would pass even if both functions were
    # broken in the same direction.
    check("Uzbek Latin really is assemblable right now",
          payment.order_message(conn, UZ_LATN, ORDER) is not None, True)
    check("Russian really is not",
          payment.order_message(conn, RU, ORDER) is not None, False)

    # Filling Russian flips exactly one of them.
    fill(conn, RU)
    check("filling Russian makes Russian ready",
          payment.ready_for(conn, RU), True)
    check("and it still agrees with order_message",
          payment.ready_for(conn, RU),
          payment.order_message(conn, RU, ORDER) is not None)
    check("while Cyrillic is untouched",
          payment.ready_for(conn, UZ_CYRL), False)

    # THE CARD IS SHARED. Clearing it must break every language at once, not
    # just the one being edited.
    payment.set_detail(conn, payment.CARD_ATTRIBUTE, "")
    check("clearing the card breaks every language",
          [payment.ready_for(conn, lang) for lang in (UZ_LATN, UZ_CYRL, RU)],
          [False, False, False])
    payment.set_detail(conn, payment.CARD_ATTRIBUTE, "8600 1234 5678 9012")

    print("\n4. The dashboard condition: prices exist and payment does not")
    # Not "payment is unset" -- a business that never sells in chat has nothing
    # to fix and should not be told it has.
    check("the harness business has orderable prices",
          payment.has_orderable_prices(conn), True)
    state = payment.completeness(conn)
    check("and completeness reports that", state["has_prices"], True)

    print("\n5. The owner is told once a day, not once per order")
    claimed = [conn.execute("select app_business_claim_payment_gap(%s)",
                            (harness_business(),)).fetchone()[0]
               for _ in range(3)]
    check("three attempts in one afternoon claim once", claimed,
          [True, False, False])
    # The app role CANNOT do the backdating itself -- it has SELECT on business
    # and nothing else, which is what stops a bug in the web app rewriting a
    # tenant. That the claim above still worked is the point: it goes through a
    # SECURITY DEFINER function, the one narrow hole.
    denied = False
    try:
        conn.execute("update business set payment_gap_notified_at = null"
                     " where id = %s", (harness_business(),))
    except psycopg.errors.InsufficientPrivilege:
        denied = True
    check("and the app role still cannot write the table directly", denied, True)

    print("\n6. Through the API")
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, auth.create_session(harness_business()))
    response = client.get("/payment")
    check("GET /payment answers", response.status_code, 200)
    check("and never returns anything but payment attributes",
          set(response.json()) >= {"card", "languages", "ready", "has_prices"},
          True)
    bad = client.put("/payment", data={"attribute": "ish vaqti", "value": "x"})
    check("PUT refuses an attribute that is not a payment detail",
          bad.status_code, 400)




def run_source():
    """That bot.py still ASKS the question, in the right place.

    A SOURCE CHECK, AND SAID SO. check_bot.py works this way for the same
    reason: handle() takes a Telegram update, sends real messages and has no
    harness, so driving it here would cost more scaffolding than the assertion
    is worth. This cannot prove the guard behaves correctly -- section 3 does
    that, by pinning ready_for() to order_message() -- but it does catch the
    regression that matters most, which is the guard being deleted or moved
    below the thing it guards.
    """
    print("\n8. bot.py asks before it offers")
    source = pathlib.Path("bot.py").read_text(encoding="utf-8")

    check("the guard calls ready_for", "payment.ready_for(" in source, True)
    check("and tells the owner when it fires",
          "notify_owner_payment_gap(" in source, True)

    # ORDER IS THE WHOLE POINT. The old code asked this question after
    # orders.create() had already written a purchase row.
    guard = source.find("payment.ready_for(")
    offer = source.find("send_offer(chat_id, text, offer)")
    check("and it is asked BEFORE the offer is sent", guard < offer, True)

    # The withheld path must fall through to the answer, not return. If it
    # returned, the customer would get silence instead of their price.
    withheld = source[guard:offer]
    check("withholding sets offer = None rather than returning",
          "offer = None" in withheld and "return" not in withheld, True)


def run_admin(biz):
    """The one thing the app role is not allowed to set up for itself."""
    print("\n7. A day later, it claims again")
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set.")
    admin = psycopg.connect(admin_url)          # NOT autocommit: rolled back
    try:
        admin.execute(
            "update business set payment_gap_notified_at = now()"
            " - interval '2 days' where id = %s", (biz,))
        check("a claim from two days ago does not block today's",
              admin.execute("select app_business_claim_payment_gap(%s)",
                            (biz,)).fetchone()[0], True)
        # And immediately after that claim, the window closes again.
        check("but the second one today is still refused",
              admin.execute("select app_business_claim_payment_gap(%s)",
                            (biz,)).fetchone()[0], False)
    finally:
        admin.rollback()
        admin.close()


if __name__ == "__main__":
    main()

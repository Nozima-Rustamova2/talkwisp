"""Takeover: what reaches the owner, what reaches the customer, and when.

    uv run python check_escalation.py

Runs the REAL bot functions against the REAL database, with Telegram and the
fact-parser stubbed. Both substitutions are deliberate and neither is the thing
under test:

  - send/send_kb are recorded instead of posted, because the assertions are
    about WHO was told WHAT, which is exactly what those calls carry.
  - parse_fact is stubbed because it is a model call. What it does with a
    sentence is app/typed.py's business; what THIS file tests is the ordering
    around it -- that a parse failure still leaves the customer answered.

THE FIVE CONTROLS, and each one is a failure that would be silent in production:

  1. two customers, one row, one ping to the owner
  2. the owner's reply reaches EVERY waiting chat, not just the first
  3. a parse failure still delivers the answer
  4. expiry tells the customer rather than going quiet
  5. no button when the business has no linked owner
"""

import datetime
import os
import sys

import psycopg
from dotenv import load_dotenv

import bot
from app import escalation
from app.db import connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
NAME = "check_escalation scratch business"
ADDR = "check-escalation@example.invalid"

SENT: list[tuple[int, str]] = []


def check(label: str, got, want) -> None:
    global passed, failed
    ok = got == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")
        failed += 1
    else:
        passed += 1


def main() -> None:
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set.")
    admin = psycopg.connect(admin_url, autocommit=True)
    # Clear a previous run's leavings in FK order. A failed run strands a
    # business with facts pointing at it, and the setup delete would then fail
    # on the same constraint the teardown did -- so the check could never run
    # again without hand-cleaning the database.
    admin.execute("delete from escalation where business_id in"
                  " (select id from business where owner_email = %s)", (ADDR,))
    admin.execute("delete from fact where business_id in"
                  " (select id from business where owner_email = %s)", (ADDR,))
    admin.execute("delete from business where owner_email = %s", (ADDR,))
    biz = str(admin.execute(
        "insert into business (name, owner_email, approved) "
        "values (%s, %s, true) returning id", (NAME, ADDR)).fetchone()[0])

    import contextlib

    saved = (bot.send, bot.send_kb, bot.parse_fact, bot.typing,
             bot.BUSINESS_ID, bot.OWNER_ID, bot.BUSINESS_NAME)
    bot.send = lambda chat, text: (SENT.append((chat, text)), True)[1]
    bot.send_kb = lambda chat, text, markup: SENT.append((chat, text))
    bot.typing = lambda chat: contextlib.nullcontext()
    bot.BUSINESS_ID = biz
    bot.BUSINESS_NAME = "Rangli Salon"
    bot.OWNER_ID = "900900"

    pool.open()
    try:
        run(admin, biz)
    finally:
        (bot.send, bot.send_kb, bot.parse_fact, bot.typing,
         bot.BUSINESS_ID, bot.OWNER_ID, bot.BUSINESS_NAME) = saved
        # Facts first: escalation.fact_id is ON DELETE SET NULL, but `fact`
        # itself references `business`, so the business cannot go while any
        # fact this run wrote is still there.
        admin.execute("delete from escalation where business_id = %s", (biz,))
        admin.execute("delete from fact where business_id = %s", (biz,))
        admin.execute("delete from business where id = %s", (biz,))
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def run(admin, biz: str) -> None:
    ctx = escalation.snapshot(
        [("Soch turmagi qancha?", "Soch turmagi 80 000 so'm.")],
        {"near_facts": [{"subject": "bolalar sochi", "attribute": "narx",
                         "similarity": 0.41}], "chunks": [], "source": "none"},
        "Uzbek")

    print("\n1. CONTROL: two customers asking the same thing is ONE escalation")
    # A row per customer would ping the owner three times for one question --
    # which is how a useful feature becomes one people mute -- and would leave
    # the others waiting on an answer that had already been given.
    with connection(biz) as conn:
        first, joined_a = escalation.open_or_join(
            conn, 111, "Bolalarga chegirma bormi?", ctx)
        second, joined_b = escalation.open_or_join(
            conn, 222, "bolalarga chegirma bormi", ctx)
    check("the first opens a new one", joined_a, False)
    check("the second JOINS it rather than opening a second", joined_b, True)
    check("and it is the same row", first, second)
    with connection(biz) as conn:
        row = escalation.get(conn, first)
    check("both customers are on it", sorted(row["chat_ids"]), [111, 222])

    # The owner is pinged for the first and NOT for the join. That is the whole
    # point of joining, so it is asserted rather than described.
    SENT.clear()
    with connection(biz) as conn:
        bot.notify_owner_escalation(escalation.get(conn, first))
    check("the owner got exactly one message", len(SENT), 1)
    owner_msg = SENT[0][1]
    check("addressed to the owner", SENT[0][0], 900900)
    check("it names the business", "Rangli Salon" in owner_msg, True)
    check("it quotes the question",
          "Bolalarga chegirma bormi?" in owner_msg, True)
    # A bare question is unanswerable -- this is the reason context is a
    # snapshot rather than a lookup.
    check("it carries the previous turn",
          "Soch turmagi qancha?" in owner_msg, True)
    check("and says WHY it stopped, with the score",
          "0.41" in owner_msg and "bolalar sochi" in owner_msg, True)

    print("\n2. CONTROL: the reply reaches EVERY waiting customer")
    SENT.clear()
    bot.parse_fact = lambda conn, line: {
        "error": None,
        "parsed": {"subject": "Rangli Salon", "attribute": "bolalar chegirmasi",
                   "value": "10%"}}
    with connection(biz) as conn:
        escalation.start_answering(conn, first)
        bot.deliver_owner_answer(conn, 900900,
                                 escalation.get(conn, first),
                                 "Ha, bolalarga 10% chegirma bor.")
    told = sorted(chat for chat, _ in SENT if chat in (111, 222))
    check("both waiting customers were answered, not just the first",
          told, [111, 222])
    check("the answer carries the owner's words",
          all("10% chegirma" in t for c, t in SENT if c in (111, 222)), True)
    with connection(biz) as conn:
        done = escalation.get(conn, first)
    check("the escalation is closed", done["status"], "answered")
    fact_id = admin.execute(
        "select fact_id from escalation where id = %s", (first,)).fetchone()[0]
    check("and the answer was written as a fact", fact_id is not None, True)
    # The landing page promises "saved so it knows next time" -- and the project
    # refuses to auto-confirm, so it must land UNCONFIRMED in the review queue.
    confirmed = admin.execute(
        "select confirmed from fact where id = %s", (fact_id,)).fetchone()[0]
    check("UNCONFIRMED -- a reply typed at 9pm does not skip review",
          confirmed, False)

    print("\n3. CONTROL: a parse failure still answers the customer")
    # Deliver first, then try to save. If the write came first, a parse failure
    # would leave a customer who was told "I've sent it" with nothing at all.
    # Losing the fact is recoverable; not answering is not.
    SENT.clear()
    bot.parse_fact = lambda conn, line: {"error": "could not parse",
                                         "parsed": None}
    with connection(biz) as conn:
        esc_id, _ = escalation.open_or_join(conn, 333, "Yakshanba ishlaysizmi?",
                                            ctx)
        escalation.start_answering(conn, esc_id)
        bot.deliver_owner_answer(conn, 900900, escalation.get(conn, esc_id),
                                 "Ha, lekin faqat ertalab.")
    check("the customer still got the answer",
          any(c == 333 and "ertalab" in t for c, t in SENT), True)
    check("and the owner was told it could not be saved",
          any(c == 900900 and "saqlay olmadim" in t for c, t in SENT), True)
    with connection(biz) as conn:
        row = escalation.get(conn, esc_id)
    check("the escalation still closed", row["status"], "answered")
    check("with no fact attached",
          admin.execute("select fact_id from escalation where id = %s",
                        (esc_id,)).fetchone()[0], None)

    print("\n4. CONTROL: expiry TELLS the customer rather than going quiet")
    # Silence after "I've sent it" would be worse than the refusal the customer
    # would have had if they had never tapped the button.
    SENT.clear()
    with connection(biz) as conn:
        stale, _ = escalation.open_or_join(conn, 444, "Bugun ochiqmisiz?", ctx)
    admin.execute("update escalation set created_at = now() - interval '25 hours'"
                  " where id = %s", (stale,))
    bot.sweep_escalations(force=True)
    check("the waiting customer was told", any(c == 444 for c, _ in SENT), True)
    with connection(biz) as conn:
        check("and the row is expired",
              escalation.get(conn, stale)["status"], "expired")
    SENT.clear()
    bot.sweep_escalations(force=True)
    check("a second sweep says nothing again -- expiry is announced ONCE",
          SENT, [])

    print("\n5. CONTROL: no offer when the business has no linked owner")
    # owner_telegram_id is null until the owner presses Start via the Settings
    # link. Offering "shall I pass this on?" with nobody behind it is a control
    # that cannot work -- the rule this project applies to every affordance.
    with connection(biz) as conn:
        saved_owner = bot.OWNER_ID
        bot.OWNER_ID = None
        _, offer_without = bot.with_takeover(conn, 555, "test", {})
        bot.OWNER_ID = saved_owner
        _, offer_with = bot.with_takeover(conn, 555, "test", {})
    check("no owner linked -> no button", offer_without, False)
    check("owner linked -> button", offer_with, True)

    with connection(biz) as conn:
        esc2, _ = escalation.open_or_join(conn, 555, "Another question", ctx)
        _, offer_again = bot.with_takeover(conn, 555, "test", {})
    check("and a customer with one already waiting is not asked again",
          offer_again, False)


if __name__ == "__main__":
    main()

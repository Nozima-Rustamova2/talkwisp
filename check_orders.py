"""Check the order state machine by hand.

    uv run python check_orders.py

Everything runs inside ONE transaction that is rolled back at the end, so the
database is left exactly as it was found. That matters more here than in the
other check scripts: this one writes rows that represent money owed.

Nothing here touches Telegram or an LLM. If this passes, the rules that protect
money hold regardless of what the bot or the model does later.
"""

import sys

import psycopg

from app.db import pool
from app.normalize import normalize
from app import orders

sys.stdout.reconfigure(encoding="utf-8")

CHAT = 999_000_001  # not a real Telegram id; nothing is sent

passed = failed = 0


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def refuses(label, fn, reason):
    """Call fn and check it refused for the stated reason."""
    try:
        got = fn()
    except orders.OrderError as exc:
        check(label, exc.reason, reason)
        return exc
    check(label, f"returned {got!r}", f"OrderError({reason})")
    return None


# --------------------------------------------------------------- amounts only

print("\nOrderError -- the collision is unrepresentable, not merely avoided")

# This class collided with its own caller TWICE: once on `reason`, once on
# `subject_key`, both when a key was added to data that was splatted into
# kwargs. `detail` is now one positional dict, so a detail key can be named
# anything at all -- including this constructor's own parameter names -- and
# nothing collides. Asserted here rather than trusted to a comment.
for _key in ("reason", "code", "detail", "self", "subject_key"):
    _e = orders.OrderError("some_code", {_key: "x"})
    check(f"a detail key called {_key!r} is just a key",
          (_e.reason, _e.detail[_key]), ("some_code", "x"))

print("\nparse_amount -- what may become an order")
for value, want in [
    ("160 000 so'm", 160000),
    ("160 000 soʻm", 160000),
    ("60 000 som", 60000),
    ("1 200 000", 1200000),
    ("450 000-500 000 soʻm", None),   # a range stays a range
    ("450 000 - 500 000", None),
    ("100 000 soʻmdan", None),        # a floor, not a price
    ("taxminan 150 000", None),
    ("150 000 dan 200 000 gacha", None),
    ("bepul", None),
    ("", None),
    ("50", None),                     # below the sanity band
    # Invisible characters, which owners paste in from Word, Excel and PDFs.
    # Every one of these renders on screen as an ordinary exact price. Before
    # _flatten() they split the number in two and price_for() answered
    # `not_exact` -- telling the owner to fix a price that already looked
    # correct. normalize() folded them all along, so lookup never noticed and
    # only the money path was affected.
    ("60​000 so'm", 60000),      # zero-width space
    ("60­000 so'm", 60000),      # soft hyphen
    ("60﻿000 so'm", 60000),      # byte-order mark
    ("60 000 so'm", 60000),      # non-breaking space
    ("60 000 so'm", 60000),      # narrow non-breaking space
    ("450 000–500 000", None),   # an en-dash range is still a range
    ("60‑000 so'm", None),       # a non-breaking HYPHEN is still a dash
]:
    check(f"{value!r:28} -> {want}", orders.parse_amount(value), want)


# --------------------------------------------------------- everything with a DB

with pool:
    with pool.connection() as conn:
        # Counted BEFORE, and compared to the count after, because the question
        # is "did this check leave anything behind" and not "is the table
        # empty". Those were the same number until the day a real order existed,
        # and then this failed with nothing wrong -- the first live payment the
        # bot ever took broke a green check by being a legitimate row. Same
        # shape as every other entry in the drift table: the assertion read a
        # different object from the one the behaviour touches.
        before = conn.execute("select count(*) from purchase").fetchone()[0]
        try:
            with conn.transaction() as tx:

                print("\nprice_for -- reading the price out of the fact table")

                doctor = normalize("Rahimov Alisher Bahodirovich")
                exc = refuses(
                    "a doctor has two prices, so we ask rather than choose",
                    lambda: orders.price_for(conn, doctor), "several_prices")
                if exc:
                    for o in exc.detail["options"]:
                        print(f"         - {o['attribute']}: {o['value']}")

                ekg = normalize("EKG")
                check("EKG resolves to one exact amount",
                      orders.price_for(conn, ekg)["amount"], 60000)

                # The range fact for the MRT is UNCONFIRMED (it came out of a
                # file); the exact one is the owner's. The confirmed filter
                # picks the right one without anything else being involved.
                mrt = normalize("MRT bosh miya")
                check("MRT: the unconfirmed range is invisible here",
                      orders.price_for(conn, mrt)["amount"], 450000)

                refuses("a subject nobody priced is not for sale",
                        lambda: orders.price_for(conn, normalize("kosmodrom")),
                        "no_price")

                # A subject whose ONLY confirmed price is a range. There is no
                # such row in the real base, so make one and let it die with
                # the rollback.
                conn.execute(
                    "insert into fact (subject, subject_key, attribute,"
                    " attribute_key, value, confirmed)"
                    " values (%s, %s, %s, %s, %s, true)",
                    ("Sinov xizmati", "sinov xizmati", "narx", "narx",
                     "450 000-500 000 soʻm"))
                refuses("a confirmed range is refused, not narrowed",
                        lambda: orders.price_for(conn, "sinov xizmati"),
                        "not_exact")

                print("\ncreate -- the unique amount")

                a = orders.create(conn, CHAT, ekg)
                check("first order is base + 1", a["amount"], 60001)
                check("state names what we are waiting for",
                      a["state"], "awaiting_payment")
                check("the item is stored as words, not a foreign key",
                      a["item"], "EKG")

                b = orders.create(conn, CHAT + 1, ekg)
                check("a second order for the same service differs",
                      b["amount"], 60002)
                check("two open orders never share an amount",
                      a["amount"] == b["amount"], False)

                print("\nlifecycle -- legal moves and refused ones")

                refuses("cannot confirm before a screenshot arrives",
                        lambda: orders.confirm(conn, a["id"]),
                        "bad_transition")

                a = orders.attach_screenshot(conn, a["id"], "AgACfake123")
                check("screenshot moves it to the owner",
                      a["state"], "awaiting_owner")
                check("the file_id is kept, the bytes are not",
                      a["screenshot_file_id"], "AgACfake123")

                a = orders.confirm(conn, a["id"])
                check("confirm records the owner's assertion",
                      a["state"], "owner_confirmed")
                check("and when they made it",
                      a["owner_confirmed_at"] is not None, True)

                refuses("a second tap on Confirm does not land",
                        lambda: orders.confirm(conn, a["id"]),
                        "bad_transition")
                refuses("a confirmed order cannot then be rejected",
                        lambda: orders.reject(conn, a["id"], "not_received"),
                        "bad_transition")

                b = orders.attach_screenshot(conn, b["id"], "AgACfake456")
                refuses("reject needs a reason we recognise",
                        lambda: orders.reject(conn, b["id"], "just because"),
                        "bad_reason")
                b = orders.reject(conn, b["id"], "amount_mismatch")
                check("reject stores which reason",
                      b["reject_reason"], "amount_mismatch")

                refuses("an order that does not exist says so",
                        lambda: orders.cancel(
                            conn, "00000000-0000-7000-8000-000000000000"),
                        "no_such_order")

                print("\nexpiry -- and the seven-day amount quarantine")

                c = orders.create(conn, CHAT + 2, ekg)
                check("a third order takes the next free suffix",
                      c["amount"], 60003)

                check("nothing is due yet", orders.expire_due(conn), [])

                conn.execute(
                    "update purchase set expires_at = now() - interval '1 hour'"
                    " where id = %s", (c["id"],))
                due = orders.expire_due(conn)
                check("the overdue order expires", len(due), 1)
                check("and is returned so the caller can notify",
                      due[0]["id"], c["id"])

                d = orders.create(conn, CHAT + 3, ekg)
                check("an expired amount is NOT handed straight back",
                      d["amount"], 60004)

                print("\nstructural -- with app/orders.py bypassed entirely")

                # Both of these are enforced in Python first, so the database
                # constraints behind them would never fire in normal use and
                # could be dropped by accident without a single test noticing.
                # These write raw SQL to prove the floor is really there.
                def raw(sql, params):
                    def go():
                        with conn.transaction():
                            conn.execute(sql, params)
                    return go

                def rejects(label, fn):
                    global passed, failed
                    try:
                        fn()
                    except psycopg.errors.Error as exc:
                        passed += 1
                        print(f"  [ok  ] {label}")
                        print(f"         {type(exc).__name__}")
                        return
                    failed += 1
                    print(f"  [FAIL] {label}")
                    print("         the database accepted it")

                rejects("the database refuses a rejection with no reason",
                        raw("insert into purchase (chat_id, item, subject_key,"
                            " attribute, base_amount, suffix, amount, state,"
                            " expires_at) values (%s, 'X', 'x', 'narx',"
                            " 10000, 7, 10007, 'owner_rejected', now())",
                            (CHAT,)))

                rejects("the database refuses a second open order on one amount",
                        raw("insert into purchase (chat_id, item, subject_key,"
                            " attribute, base_amount, suffix, amount,"
                            " expires_at) values (%s, 'EKG', %s, 'narx',"
                            " 60000, 4, 60004, now())", (CHAT, ekg)))

                rejects("the database refuses an amount that is not base+suffix",
                        raw("insert into purchase (chat_id, item, subject_key,"
                            " attribute, base_amount, suffix, amount,"
                            " expires_at) values (%s, 'X', 'x', 'narx',"
                            " 10000, 7, 99999, now())", (CHAT,)))

                print("\nreading back")
                check("open orders for a chat exclude finished ones",
                      orders.open_for_chat(conn, CHAT), [])
                check("the expired order is still readable",
                      orders.get(conn, c["id"])["state"], "expired")

                raise psycopg.Rollback(tx)
        except psycopg.Rollback:
            pass

        after = conn.execute("select count(*) from purchase").fetchone()[0]
        check("\nthe database is left as it was found", after, before)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

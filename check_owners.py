"""Up to three owners, and the notification that must reach all of them.

    uv run python check_owners.py

THE SECTION THAT MATTERS IS 3, THE FAN-OUT. Its failure mode is "sends to the
first owner only", and that looks exactly like working software: every manual
test with one owner passes, most tests with two pass if you only check that a
message arrived. So nothing here asserts "a message was sent". It captures
every recipient and asserts the SET equals the claimed owners -- then removes
one and asserts the set shrinks to match.

A fan-out that hits only the first fails the first assertion. One that ignores
removals fails the second. Neither would surface any other way.

Section 1 covers the claim, which is the only door: a bot cannot message anyone
by @username and there is no lookup from a username to a chat id, so the signed
/start payload is how Telegram reveals an id at all.
"""

import os
import sys

import psycopg
from dotenv import load_dotenv

import bot
from app.db import connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
ADDR = "check-owners@example.invalid"
ALICE, BOB, CAROL, DAVE = 111000111, 222000222, 333000333, 444000444


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def claim(conn, tg_id):
    return conn.execute("select app_business_claim_owner(%s, %s)",
                        (bot.BUSINESS_ID, tg_id)).fetchone()[0]


def remove(conn, tg_id):
    return conn.execute("select app_business_remove_owner(%s, %s)",
                        (bot.BUSINESS_ID, tg_id)).fetchone()[0]


def main():
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set.")
    admin = psycopg.connect(admin_url, autocommit=True)
    admin.execute("delete from business_owner where business_id in"
                  " (select id from business where owner_email = %s)", (ADDR,))
    admin.execute("delete from business where owner_email = %s", (ADDR,))
    biz = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values (%s, %s, true) returning id",
        ("check_owners scratch", ADDR)).fetchone()[0])
    bot.BUSINESS_ID = biz
    pool.open()
    try:
        run(admin, biz)
    finally:
        admin.execute("delete from business_owner where business_id = %s", (biz,))
        admin.execute("delete from business where owner_email = %s", (ADDR,))
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def run(admin, biz):
    print("\n1. Claiming, capped at three")
    with connection(biz) as conn:
        check("the first claim takes", claim(conn, ALICE), True)
        check("a second owner can claim too", claim(conn, BOB), True)
        check("and a third", claim(conn, CAROL), True)
        # A FOURTH IS REFUSED, not silently dropped into a fourth slot.
        check("a fourth is refused", claim(conn, DAVE), False)
        # Pressing the link twice is a person pressing a link twice, not an
        # error -- and it must not consume a slot.
        check("claiming again returns false", claim(conn, ALICE), False)
        check("and did not consume a slot",
              conn.execute("select count(*) from business_owner").fetchone()[0], 3)

    print("\n2. All three may act")
    bot.OWNERS = [ALICE, BOB, CAROL]
    for who, name in ((ALICE, "Alice"), (BOB, "Bob"), (CAROL, "Carol")):
        check(f"{name} is an owner", bot.is_owner_id(who), True)
    check("a stranger is not", bot.is_owner_id(DAVE), False)
    check("and neither is nobody", bot.is_owner_id(None), False)

    print("\n3. THE FAN-OUT: every owner, not the first")
    seen = []
    check("the recipient set equals the owner set",
          sorted(fan_out(seen)), sorted(bot.OWNERS))
    check("and it is three sends, not one", len(seen), 3)

    # ONE FAILURE MUST NOT SWALLOW THE REST. An owner who blocked the bot would
    # otherwise take the escalation away from the other two.
    def one_explodes(owner):
        if owner == BOB:
            raise RuntimeError("blocked the bot")
        seen.append(owner)

    seen.clear()
    delivered = bot.notify_owners(one_explodes)
    check("a failing owner does not stop the others",
          sorted(seen), sorted([ALICE, CAROL]))
    check("and the count reports who actually got it", delivered, 2)

    print("\n4. Removal shrinks the fan-out")
    with connection(biz) as conn:
        check("removing Bob reports it happened", remove(conn, BOB), True)
        check("removing him again does not", remove(conn, BOB), False)
        bot.refresh_owners(conn)
    check("the owner list dropped him", sorted(bot.OWNERS), sorted([ALICE, CAROL]))
    seen = []
    # THE SECOND HALF OF THE FAN-OUT TEST. A notifier that cached its
    # recipients, or read a stale list, would still send to Bob here -- and
    # nothing else in the system would notice.
    check("and so did the recipients", sorted(fan_out(seen)),
          sorted([ALICE, CAROL]))
    check("Bob is not among them", BOB in seen, False)
    check("he may no longer act", bot.is_owner_id(BOB), False)

    print("\n5. Removing the last owner is allowed and recoverable")
    with connection(biz) as conn:
        remove(conn, ALICE)
        remove(conn, CAROL)
        bot.refresh_owners(conn)
    check("no owners left", bot.OWNERS, [])
    check("nobody may act", bot.is_owner_id(ALICE), False)
    check("and nothing is sent", fan_out([]), [])
    with connection(biz) as conn:
        # Back to the pre-claim state, which is the point: a business whose
        # only owner lost their phone can hand ownership on.
        check("the bot can be claimed again", claim(conn, DAVE), True)
        bot.refresh_owners(conn)
    check("by someone new", bot.OWNERS, [DAVE])

    print("\n6. Web sign-in finds ANY owner, not the first")
    with connection(biz) as conn:
        claim(conn, ALICE)
    for who, name in ((DAVE, "the first claimer"), (ALICE, "the second")):
        found = admin.execute("select app_business_for_telegram(%s)",
                              (who,)).fetchone()[0]
        check(f"{name} resolves to the business", str(found), biz)
    check("a stranger resolves to nothing",
          admin.execute("select app_business_for_telegram(%s)",
                        (999000999,)).fetchone()[0], None)

    print("\n7. CONTROL: the app role cannot write the table directly")
    denied = False
    with connection(biz) as conn:
        try:
            conn.execute("insert into business_owner (business_id, telegram_id)"
                         " values (%s, %s)", (biz, 555000555))
        except psycopg.errors.InsufficientPrivilege:
            denied = True
    check("a direct insert is refused", denied, True)
    denied = False
    with connection(biz) as conn:
        try:
            conn.execute("delete from business_owner where business_id = %s",
                         (biz,))
        except psycopg.errors.InsufficientPrivilege:
            denied = True
    check("and so is a direct delete", denied, True)


    print("\n8. The second owner to tap is told WHO resolved it")
    from app import orders
    with connection(biz) as conn:
        # Two owners with names, so the message has something to say.
        conn.execute("select app_business_claim_owner(%s, %s, %s)",
                     (biz, BOB, "Nigora"))
        bot.refresh_owners(conn)
        conn.execute(
            "insert into fact (subject, subject_key, attribute, attribute_key,"
            " value, value_key, confirmed) values"
            " ('Kurs','kurs','narxi','narxi','250 000 so''m','250 000 som',true)")
        order = orders.create(conn, 4242, "kurs", "narxi")
        # A fresh order is awaiting_payment; Confirm is only offered once the
        # customer has sent evidence, so walk it to awaiting_owner the way the
        # real flow does rather than confirming from a state the buttons never
        # appear in.
        orders.attach_screenshot(conn, order["id"], "scratch-file-id")

        # ALICE wins the race.
        won = orders.confirm(conn, order["id"], ALICE)
        check("the winning tap is recorded", won["resolved_by"], ALICE)

        # NIGORA taps a moment later, on her own live copy of the message.
        second = None
        try:
            orders.confirm(conn, order["id"], BOB)
        except orders.OrderError as exc:
            second = exc.reason
        check("the second tap is refused, not applied twice",
              second, "bad_transition")
        check("and resolved_by still names the winner",
              conn.execute("select resolved_by from purchase where id = %s",
                           (order["id"],)).fetchone()[0], ALICE)

        # THE SENTENCE THE SECOND OWNER READS. "Already resolved" is complete
        # with one owner and confusing with three.
        check("an owner with a name is named",
              bot.owner_name(conn, BOB), "Nigora")
        # Falls back to the id rather than to "someone": ugly and unambiguous
        # beats tidy and useless, when the whole point is saying which one.
        check("one without a name falls back to the id",
              bot.owner_name(conn, ALICE), str(ALICE))
        check("and nobody at all is still sayable",
              bot.owner_name(conn, None), "Kimdir")

        conn.execute("delete from purchase where id = %s", (order["id"],))
        conn.execute("delete from fact where subject_key = 'kurs'")

    print("\n9. Removal through the web app, not Telegram")
    from fastapi.testclient import TestClient
    import app.main as api
    from app import auth
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, auth.create_session(biz))

    listed = client.get("/owners")
    check("GET /owners answers", listed.status_code, 200)
    ids = [o["telegram_id"] for o in listed.json()]
    check("it lists the claimed owners", sorted(ids), sorted(bot.OWNERS))
    check("with the name where there is one",
          [o["name"] for o in listed.json() if o["telegram_id"] == BOB], ["Nigora"])

    gone = client.request("DELETE", f"/owners/{BOB}")
    check("DELETE removes one", gone.status_code, 200)
    check("and reports it happened", gone.json()["removed"], True)
    check("and says how many are left", gone.json()["remaining"], len(ids) - 1)
    check("the list agrees",
          BOB in [o["telegram_id"] for o in client.get("/owners").json()], False)

    # REMOVING THE LAST OWNER IS ALLOWED. It returns the bot to unclaimed,
    # which is recoverable from the link -- a business whose only owner lost
    # their phone must be able to hand ownership on, and a guard here would
    # create a stuck state rather than prevent one.
    for remaining in [o["telegram_id"] for o in client.get("/owners").json()]:
        client.request("DELETE", f"/owners/{remaining}")
    check("the last owner can be removed too",
          client.get("/owners").json(), [])
    with connection(biz) as conn:
        check("and the bot can be claimed again", claim(conn, ALICE), True)

    # A SESSION FOR ANOTHER BUSINESS CANNOT REMOVE THESE. The endpoint passes
    # the session's own business to the function, so this is the only thing
    # standing between one tenant and another's owners.
    other_biz = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values ('owners other','owners-other@example.invalid',true)"
        " returning id").fetchone()[0])
    intruder = TestClient(api.app)
    intruder.cookies.set(auth.COOKIE, auth.create_session(other_biz))
    intruder.request("DELETE", f"/owners/{ALICE}")
    with connection(biz) as conn:
        bot.refresh_owners(conn)
    check("another business's session cannot remove our owner",
          bot.OWNERS, [ALICE])
    admin.execute("delete from business where id = %s", (other_biz,))

def fan_out(seen: list) -> list:
    """Run the real fan-out, recording who it reached."""
    bot.notify_owners(seen.append)
    return seen


if __name__ == "__main__":
    main()

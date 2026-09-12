"""Connecting a Telegram bot from a browser: what it stores and what it refuses.

    uv run python check_channel.py

Runs against the real app through TestClient and the real database. It never
contacts Telegram with a made-up token expecting success -- the one thing that
needs a live token is the collision path, which reuses whatever token the
harness business already has, and sets it on the business that already owns it
so nothing changes.

THE SECTION THAT MATTERS MOST IS 4, the claim code. `owner_telegram_id` gates
/fact and the owner buttons, and a bot is findable the moment it exists -- so if
/start claimed ownership for whoever pressed it first, any customer could become
the owner. Section 4 is the proof that a stranger's press cannot.
"""

import os
import sys
import time

import psycopg
from dotenv import load_dotenv
from fastapi.testclient import TestClient

import app.main as api
from app import auth, channel
from app.db import connection, harness_business, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
NAME = "check_channel scratch business"
ADDR = "check-channel@example.invalid"


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
    pool.open()
    admin = psycopg.connect(admin_url, autocommit=True)
    admin.execute("delete from business where owner_email = %s", (ADDR,))
    scratch = str(admin.execute(
        "insert into business (name, owner_email, approved) "
        "values (%s, %s, true) returning id", (NAME, ADDR)).fetchone()[0])
    try:
        run(admin, scratch)
    finally:
        admin.execute("delete from session where business_id = %s", (scratch,))
        admin.execute("delete from business where id = %s", (scratch,))
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def run(admin, scratch: str) -> None:
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, auth.create_session(scratch))

    print("\n1. A business with no bot says so, and offers nothing to click")
    state = client.get("/channel")
    check("GET /channel answers", state.status_code, 200)
    body = state.json()
    check("connected is false", body["connected"], False)
    check("no username", body["bot_username"], None)
    check("owner is not linked", body["owner_linked"], False)
    # A claim link for a bot that does not exist would be a control that cannot
    # work, which the design direction argues against more than once.
    check("and NO claim link, because there is no bot to claim",
          body["claim_link"], None)

    print("\n2. A token that is not a token is refused before any network call")
    for bad, why in (("hello", "not token-shaped"),
                     ("123:short", "too short"),
                     ("", "empty")):
        response = client.post("/channel/telegram", data={"token": bad})
        check(f"{why!r} is refused", response.status_code in (400, 422), True)
    detail = client.post("/channel/telegram", data={"token": "hello"}).json()
    check("and the message says what a token looks like",
          "123456789" in detail["detail"], True)

    print("\n3. The token is never sent to the browser")
    # The one thing this endpoint must not do. Checked against the whole
    # serialized body rather than a key name, so adding a field cannot smuggle
    # it out later.
    real = admin.execute(
        "select bot_token from business where bot_token is not null limit 1"
    ).fetchone()
    if not real:
        print("  [skip] no business has a bot_token to test against")
    else:
        token = real[0]
        admin.execute("update business set bot_token = %s where id = %s",
                      (token + "x", scratch))
        raw = client.get("/channel").text
        check("the token does not appear anywhere in GET /channel",
              token[:20] in raw, False)
        check("nor does the scratch token", (token + "x")[:20] in raw, False)

        print("\n3b. A token another business holds is refused, without naming it")
        admin.execute("update business set bot_token = null where id = %s",
                      (scratch,))
        owner_name = admin.execute(
            "select name from business where bot_token = %s", (token,)
        ).fetchone()[0]
        response = client.post("/channel/telegram", data={"token": token})
        # 409 only if Telegram accepted the token first; a dead token 400s.
        if response.status_code == 409:
            check("a token in use is refused with 409", response.status_code, 409)
            check("and the refusal does NOT name the business holding it",
                  owner_name.lower() in response.json()["detail"].lower(), False)
        else:
            print(f"  [skip] the stored token is not live ({response.status_code}); "
                  "collision path needs a working token")

    print("\n4. THE CLAIM CODE: a stranger pressing Start cannot take ownership")
    fake_token = "123456789:AAEabcdefghijklmnopqrstuvwxyz0123456"
    good = channel.claim_code(scratch, fake_token)
    check("a freshly made code verifies",
          channel.check_claim(good, scratch, fake_token), True)
    check("an empty payload does not", channel.check_claim("", scratch, fake_token), False)
    check("a payload with no signature does not",
          channel.check_claim("999999999", scratch, fake_token), False)
    check("a tampered signature does not",
          channel.check_claim(good[:-1] + ("0" if good[-1] != "0" else "1"),
                              scratch, fake_token), False)
    # The two that matter most: the code is bound to ONE business and ONE bot.
    other = str(admin.execute(
        "select id from business where id <> %s limit 1", (scratch,)).fetchone()[0])
    check("a code for one business does not work for another",
          channel.check_claim(good, other, fake_token), False)
    check("and a code does not work with a different bot token",
          channel.check_claim(good, scratch, fake_token[:-1] + "Z"), False)
    expired = f"{int(time.time()) - 10}-{channel._sign(scratch, fake_token, int(time.time()) - 10)}"
    check("an expired code does not verify, though its signature is valid",
          channel.check_claim(expired, scratch, fake_token), False)

    print("\n5. Ownership is claimed once, and only from empty")
    admin.execute("update business set owner_telegram_id = null where id = %s",
                  (scratch,))
    with connection(scratch) as conn:
        first = conn.execute("select app_business_claim_owner(%s, %s)",
                             (scratch, 111)).fetchone()[0]
        second = conn.execute("select app_business_claim_owner(%s, %s)",
                              (scratch, 222)).fetchone()[0]
    check("the first claim succeeds", first, True)
    check("the second is refused", second, False)
    owner = admin.execute("select owner_telegram_id from business where id = %s",
                          (scratch,)).fetchone()[0]
    check("and the owner is still the first one", owner, 111)

    print("\n6. A claimed bot stops offering a claim link")
    admin.execute("update business set owner_telegram_id = %s where id = %s",
                  (111, scratch))
    check("owner_linked is true", client.get("/channel").json()["owner_linked"], True)
    check("and no claim link is offered",
          client.get("/channel").json()["claim_link"], None)

    print("\n7. THE BUG THAT STARTED THIS: no heartbeat, no claim link")
    # An owner saved a token, was told to open their bot and press Start, and
    # pressed it -- into a bot nobody was polling. Telegram queued the update,
    # nothing read it, ownership was never claimed, and "I've done it" correctly
    # reported that nothing had changed. The step was offered at exactly the
    # moment it could not work.
    #
    # `polling` is the observation that replaced that guess, and these are its
    # three states. The scratch business is given a SHAPE-VALID BUT DEAD token:
    # enough for `connected`, and the polling flag is read from the column
    # rather than from Telegram, so all three transitions are real.
    admin.execute(
        "update business set bot_token = %s, owner_telegram_id = null, "
        "bot_last_seen_at = null where id = %s",
        ("999999999:AAEcheckchannelscratchtokennotreal01", scratch))

    check("never seen: polling is false",
          client.get("/channel").json()["polling"], False)

    # The crashed-poller case, and the reason the column is touched on a timer
    # instead of once at startup: a value written at boot would read "alive"
    # forever after the process died.
    admin.execute("update business set bot_last_seen_at = "
                  "now() - interval '10 minutes' where id = %s", (scratch,))
    check("a stale heartbeat does not count as alive",
          client.get("/channel").json()["polling"], False)

    admin.execute("update business set bot_last_seen_at = now() "
                  "where id = %s", (scratch,))
    check("a fresh heartbeat reports polling",
          client.get("/channel").json()["polling"], True)

    # WHAT THIS SECTION DOES NOT PROVE, said rather than glossed: that a claim
    # link APPEARS once polling is true. The link also requires Telegram to have
    # confirmed the token, and the only live token on this database belongs to a
    # real business -- borrowing it would mean nulling it there first, and a
    # check that can strand production's bot token if it dies halfway is not
    # worth the coverage. The link's other two conditions are asserted above and
    # in section 6; this one is exercised by hand.
    check("with a dead token there is still no link, whatever the heartbeat",
          client.get("/channel").json()["claim_link"], None)

    print("\n8. The endpoints refuse without a session")
    anon = TestClient(api.app)
    check("GET /channel is 401 logged out", anon.get("/channel").status_code, 401)
    check("POST /channel/telegram is 401 logged out",
          anon.post("/channel/telegram", data={"token": "x"}).status_code, 401)


if __name__ == "__main__":
    main()

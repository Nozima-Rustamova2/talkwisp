"""Changing the address you sign in with.

    uv run python check_email_change.py

WHY THIS CAPABILITY EXISTS AT ALL, stated precisely because the obvious reading
is wrong. A typo at SIGNUP is not the problem it solves: owner_email is unique
per address, so a mistyped one is a different row -- the person signs up again
and the bad row is litter, not a lock. The case that was permanent is losing a
CORRECT address later, when the magic link has nowhere to go and the only way
back in was somebody running SQL.

THE SECTION THAT MATTERS IS 2. The link goes to the NEW address. Confirming at
the old one would be useless in exactly the case this exists for -- where that
mailbox no longer works -- and confirming at the destination also catches a
second typo, which is how this feature could otherwise recreate the problem it
solves. Section 2 asserts which address receives which mail, because getting
that backwards would look like a working feature.
"""

import os
import sys

import psycopg
from dotenv import load_dotenv
from fastapi.testclient import TestClient

import app.main as api
from app import auth
from app.db import pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
OLD = "check-change-old@example.invalid"
NEW = "check-change-new@example.invalid"
TAKEN = "check-change-taken@example.invalid"

SENT: list[tuple[str, str, str]] = []


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def main():
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set.")
    admin = psycopg.connect(admin_url, autocommit=True)
    for addr in (OLD, NEW, TAKEN):
        admin.execute("delete from email_change where business_id in"
                      " (select id from business where owner_email = %s)",
                      (addr,))
        admin.execute("delete from business where owner_email = %s", (addr,))
    biz = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values (%s, %s, true) returning id",
        ("check_email_change", OLD)).fetchone()[0])
    other = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values (%s, %s, true) returning id",
        ("check_email_change squatter", TAKEN)).fetchone()[0])

    # Capture mail instead of sending it. Nothing here should reach a real
    # inbox, and WHICH address receives WHICH message is the thing being tested.
    real_send = auth.send_mail
    api.auth.send_mail = lambda to, subject, body: SENT.append((to, subject, body))

    pool.open()
    try:
        run(admin, biz, other)
    finally:
        api.auth.send_mail = real_send
        for b in (biz, other):
            admin.execute("delete from email_change where business_id = %s", (b,))
            admin.execute("delete from business where id = %s", (b,))
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def address_of(admin, biz):
    return admin.execute("select owner_email from business where id = %s",
                         (biz,)).fetchone()[0]


def run(admin, biz, other):
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, auth.create_session(biz))

    print("\n1. Requesting a change")
    bad = client.post("/auth/change-email", data={"email": "not-an-address"})
    check("a malformed address is refused", bad.status_code, 400)

    taken = client.post("/auth/change-email", data={"email": TAKEN})
    check("an address somebody else owns is refused", taken.status_code, 400)
    check("and says so plainly, because the requester is already signed in",
          "already belongs" in taken.json()["detail"], True)

    SENT.clear()
    response = client.post("/auth/change-email", data={"email": NEW})
    check("a free address is accepted", response.status_code, 200)

    print("\n2. WHICH ADDRESS GETS WHICH MAIL")
    check("two mails go out", len(SENT), 2)
    to_new = [m for m in SENT if m[0] == NEW]
    to_old = [m for m in SENT if m[0] == OLD]
    # THE LINK GOES TO THE NEW ADDRESS. Sending it to the old one would be
    # useless in the case this feature exists for.
    check("the confirmation link goes to the NEW address", len(to_new), 1)
    check("and it contains a link", "/auth/change-email?token=" in to_new[0][2],
          True)
    # THE OLD ADDRESS IS WARNED, at request time, while it can still act.
    check("the old address is warned", len(to_old), 1)
    check("the warning names both addresses",
          OLD in to_old[0][2] and NEW in to_old[0][2], True)
    check("and says nothing has happened yet",
          "does not happen until" in to_old[0][2], True)
    check("the old mail carries NO link -- it is a warning, not an action",
          "token=" in to_old[0][2], False)

    print("\n3. Nothing changes until the link is opened")
    check("the address is still the old one", address_of(admin, biz), OLD)

    token = to_new[0][2].split("token=")[1].split()[0]
    SENT.clear()
    done = client.get(f"/auth/change-email?token={token}", follow_redirects=False)
    check("opening the link redirects", done.status_code, 303)
    check("and the address changed", address_of(admin, biz), NEW)
    check("the old address is told it completed", [m[0] for m in SENT], [OLD])

    print("\n4. The token is spent, and says why")
    again = client.get(f"/auth/change-email?token={token}", follow_redirects=False)
    check("a second open does not change anything",
          address_of(admin, biz), NEW)
    check("and reports 'used' rather than failing silently",
          "email=used" in again.headers["location"], True)

    unknown = client.get("/auth/change-email?token=neverexisted",
                         follow_redirects=False)
    check("an invented token is 'unknown'",
          "email=unknown" in unknown.headers["location"], True)

    print("\n5. CONTROL: the app role cannot read pending changes")
    # Same treatment as session and login_token -- protection by absence of
    # privilege rather than by a policy. migrate.py's smoke test refuses any
    # object the app cannot read, so email_change had to be declared there on
    # purpose; this is the other half of that decision.
    denied = False
    try:
        with pool.connection() as conn:
            conn.execute("select * from email_change limit 1")
    except psycopg.errors.InsufficientPrivilege:
        denied = True
    check("a direct read is refused", denied, True)

    print("\n6. Signing in with the new address works, and the old does not")
    check("the new address resolves to the business",
          auth.issue_link(NEW) is not None, True)
    check("the old one no longer does", auth.issue_link(OLD), None)


if __name__ == "__main__":
    main()

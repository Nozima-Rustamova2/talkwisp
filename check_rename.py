"""Renaming a business, and the hazard that allowing it creates.

    uv run python check_rename.py

TWO THINGS MATTER HERE AND NEITHER IS "the name changed".

Section 2: the app role must STILL not be able to write `business`. The whole
reason renaming goes through a SECURITY DEFINER function is that talkwisp_app
has SELECT and nothing else, and that absence is what stops a bug in the web app
rewriting a tenant. A rename feature that worked by granting UPDATE would pass
every test about renaming and quietly remove the property.

Section 3: business.name is NOT unique -- bot_token and owner_email are, name is
not -- and app_business_by_name() used to be `select id from business where
name = wanted` returning a scalar, which with two matches returns the first row
and says nothing. `bot.py --business "Klinika"` would then poll for whichever
row came first, against another tenant's facts, and nothing would fail.

That was latent while nobody could choose a name. Letting owners choose makes it
reachable by an ordinary action, because "Klinika" is exactly the generic string
two different owners would both type. So the resolver must refuse rather than
guess, and this section plants the collision to prove it does.
"""

import os
import sys

import psycopg
from dotenv import load_dotenv
from fastapi.testclient import TestClient

import app.main as api
from app import auth
from app.db import connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
ADDR_A = "check-rename-a@example.invalid"
ADDR_B = "check-rename-b@example.invalid"


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
    for addr in (ADDR_A, ADDR_B):
        admin.execute("delete from business where owner_email = %s", (addr,))
    biz_a = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values (%s, %s, true) returning id",
        ("check_rename original", ADDR_A)).fetchone()[0])
    biz_b = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values (%s, %s, true) returning id",
        ("check_rename other", ADDR_B)).fetchone()[0])
    pool.open()
    try:
        run(admin, biz_a, biz_b)
    finally:
        for addr in (ADDR_A, ADDR_B):
            admin.execute("delete from business where owner_email = %s", (addr,))
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def name_of(admin, biz):
    return admin.execute("select name from business where id = %s",
                         (biz,)).fetchone()[0]


def run(admin, biz_a, biz_b):
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, auth.create_session(biz_a))

    print("\n1. The name is shown back, and can be changed")
    check("GET /business answers", client.get("/business").status_code, 200)
    check("with the name nobody could see before",
          client.get("/business").json()["name"], "check_rename original")

    response = client.put("/business/name", data={"name": "Multi-Level Record"})
    check("PUT renames", response.status_code, 200)
    check("and returns the new name", response.json()["name"], "Multi-Level Record")
    check("the row really changed", name_of(admin, biz_a), "Multi-Level Record")

    # Trimmed, not stored raw. A name with leading spaces looks right
    # everywhere and sorts wrong.
    client.put("/business/name", data={"name": "   Padded Name   "})
    check("whitespace is trimmed", name_of(admin, biz_a), "Padded Name")

    check("an empty name is refused",
          client.put("/business/name", data={"name": "   "}).status_code, 400)
    check("and the old name survives the refusal",
          name_of(admin, biz_a), "Padded Name")
    check("an absurdly long name is refused",
          client.put("/business/name", data={"name": "x" * 200}).status_code, 400)

    print("\n2. CONTROL: the app role still cannot write the table")
    # The property the SECURITY DEFINER function exists to preserve. A rename
    # implemented by granting UPDATE would pass every check above.
    denied = False
    with connection(biz_a) as conn:
        try:
            conn.execute("update business set name = 'direct' where id = %s",
                         (biz_a,))
        except psycopg.errors.InsufficientPrivilege:
            denied = True
    check("a direct UPDATE from the app role is refused", denied, True)
    check("so the name is unchanged by it", name_of(admin, biz_a), "Padded Name")

    # And a tenant cannot rename SOMEONE ELSE. The function takes an id, so the
    # only thing stopping it is that the endpoint passes the session's own.
    other = TestClient(api.app)
    other.cookies.set(auth.COOKIE, auth.create_session(biz_b))
    other.put("/business/name", data={"name": "Hijacked"})
    check("renaming through B's session leaves A alone",
          name_of(admin, biz_a), "Padded Name")
    check("and renames B", name_of(admin, biz_b), "Hijacked")

    print("\n3. An ambiguous name refuses instead of guessing")
    # The hazard the rename creates: two businesses may now share a name, and
    # the resolver used to return the first row silently.
    admin.execute("update business set name = 'Klinika' where id in (%s, %s)",
                  (biz_a, biz_b))
    ambiguous = False
    try:
        admin.execute("select app_business_by_name('Klinika')").fetchone()
    except psycopg.errors.RaiseException as exc:
        ambiguous = "ambiguous" in str(exc)
    check("two businesses sharing a name raises", ambiguous, True)

    # NEGATIVE HALF: the same call on a name only one business has must still
    # work. Without this, "it raised" is equally true of a resolver that is
    # simply broken.
    admin.execute("update business set name = 'Klinika solo' where id = %s",
                  (biz_b,))
    check("one match still resolves",
          str(admin.execute("select app_business_by_name('Klinika solo')")
              .fetchone()[0]), biz_b)
    check("and no match is still null",
          admin.execute("select app_business_by_name('nothing here')").fetchone()[0],
          None)


if __name__ == "__main__":
    main()

"""Check that one business cannot read or write another's data.

    uv run python check_tenancy.py

WHAT THIS FILE IS FOR. The tenancy failure is silent by construction: a query
missing its filter returns another business's rows, nothing raises, and the
answer looks like an answer. There is no wrong number to notice. So the only
useful check is one that tries the cross-tenant read and watches it fail.

WHAT IT RUNS AS, AND WHY THAT IS THE WHOLE POINT. Every check below connects as
`talkwisp_app`, the role the deployed app uses. A tenancy test run as postgres
would pass or fail on a different object from the one that ships -- superusers
bypass row-level security entirely -- and that is this project's recurring
failure: a check that reads a different object from the one the behaviour uses.
It passes, and a green signal cannot tell you it was about something else.

Section 2 makes that concrete by running the SAME query as both roles and
showing the two answers.

The endpoint checks call the real FastAPI app through TestClient, so they go
through the real dependency, the real connection helper and the real policies.
Since auth landed they also go through a real session: section 3 signs in as
each business with a genuine cookie rather than overriding the dependency, so
the join between "who is this" and "which rows may they see" is itself tested.
That join is the part most worth testing, and an override would skip it.
"""

import ast
import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

import app.main as api
from app import auth
from app.db import assert_app_role, connection, pool, sole_business

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def rejects(label, fn):
    """The database must refuse this, whatever app/ believes."""
    global passed, failed
    try:
        fn()
    except psycopg.errors.Error as exc:
        passed += 1
        print(f"  [ok  ] {label}")
        print(f"         {type(exc).__name__}: {str(exc).splitlines()[0][:90]}")
        return
    failed += 1
    print(f"  [FAIL] {label}")
    print("         it was allowed")


admin = psycopg.connect(os.environ["ADMIN_DATABASE_URL"], autocommit=True)
pool.open()

# ---------------------------------------------------------------------------
print("\n1. SET LOCAL does not survive a pool checkout")
# The assumption everything else rests on. app/db.py binds the tenant with
# set_config(..., true) -- SET LOCAL -- on a POOLED connection. If that value
# outlived the transaction it would follow the connection to the next request,
# and the next business would read the previous one's rows with nothing failing.
#
# Two controls, because without them this section can pass while proving
# nothing:
#
#   * pg_backend_pid(). A fresh connection would show no setting either, so
#     "the value is gone" is only evidence if it is the SAME connection. The
#     probe pool is max_size=1 and the check asserts the pid matched.
#   * A bare SET, which MUST be seen to survive. If the test cannot detect
#     survival at all, its finding on SET LOCAL means nothing. This is the
#     negative control the payment check taught us to write.

probe = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=1,
                       open=True)
MARKER = "00000000-0000-0000-0000-0000000000aa"

with probe.connection() as c:
    pid_a = c.execute("select pg_backend_pid()").fetchone()[0]
    c.execute("select set_config('app.business_id', %s, true)", (MARKER,))
    inside = c.execute(
        "select current_setting('app.business_id', true)").fetchone()[0]
with probe.connection() as c:
    pid_b = c.execute("select pg_backend_pid()").fetchone()[0]
    after_local = c.execute(
        "select current_setting('app.business_id', true)").fetchone()[0]

check("the probe pool handed back the same backend", pid_a == pid_b, True)
check("the tenant is readable inside the block", inside, MARKER)
check("SET LOCAL is gone after checkout", after_local in (None, ""), True)

with probe.connection() as c:
    c.execute("select set_config('app.business_id', %s, false)", (MARKER,))
with probe.connection() as c:
    pid_c = c.execute("select pg_backend_pid()").fetchone()[0]
    after_plain = c.execute(
        "select current_setting('app.business_id', true)").fetchone()[0]

check("negative control: same backend again", pid_c == pid_a, True)
check("negative control: a bare SET DOES survive -- so this test can see it",
      after_plain, MARKER)
probe.close()

# ---------------------------------------------------------------------------
print("\n2. The app connects as a role that RLS applies to")
# Three ways RLS does nothing while looking enabled, all measured here rather
# than asserted in a comment.

with pool.connection() as c:
    user, superuser, bypass = c.execute(
        "select current_user, rolsuper, rolbypassrls"
        " from pg_roles where rolname = current_user").fetchone()
check("the app is not postgres", user != "postgres", True)
check("the app role is not a superuser", superuser, False)
check("the app role does not have BYPASSRLS", bypass, False)

owns = admin.execute(
    "select count(*) from pg_tables where schemaname = 'public'"
    " and tableowner = %s", (user,)).fetchone()[0]
check("the app role owns no tables, so FORCE means something", owns, 0)

forced = admin.execute(
    "select count(*) from pg_class where relname in"
    " ('source','fact','alias','chunk','purchase','business')"
    " and relrowsecurity and relforcerowsecurity").fetchone()[0]
check("all six tables have RLS enabled AND forced", forced, 6)

invoker = admin.execute(
    "select reloptions from pg_class where relname = 'retrievable_fact'"
).fetchone()[0]
check("retrievable_fact is security_invoker",
      "security_invoker=true" in (invoker or []), True)

assert_app_role()  # must not raise on this URL
print("  [ok  ] assert_app_role() accepts the app role")
passed += 1

# The same query, the same database, two roles, two answers. This is what the
# startup assertion exists to prevent, shown rather than described.
as_admin = admin.execute("select count(*) from fact").fetchone()[0]
check("as postgres, an untenanted count sees everything", as_admin > 0, True)
def untenanted_count():
    with pool.connection() as c:
        c.execute("select count(*) from fact")


rejects("as the app role, the same query refuses to run", untenanted_count)

# ---------------------------------------------------------------------------
print("\n3. Two businesses cannot see each other, through the real endpoints")

BUSINESS_A = sole_business()  # must be called while there is still only one
b_row = admin.execute(
    "insert into business (name) values ('check_tenancy B') returning id"
).fetchone()[0]
BUSINESS_B = str(b_row)

admin.execute(
    "insert into fact (subject, subject_key, attribute, attribute_key,"
    " value, value_key, confirmed, business_id)"
    " values ('CheckTenancy', 'checktenancy', 'holat', 'holat',"
    " 'faqat B uchun', 'faqat b uchun', true, %s)", (BUSINESS_B,))

a_confirmed, a_waiting = admin.execute(
    "select count(*) filter (where confirmed),"
    " count(*) filter (where not confirmed) from fact where business_id = %s",
    (BUSINESS_A,)).fetchone()
a_sources = admin.execute(
    "select count(*) from source where business_id = %s",
    (BUSINESS_A,)).fetchone()[0]
a_unconfirmed_id = admin.execute(
    "select id from fact where business_id = %s and not confirmed limit 1",
    (BUSINESS_A,)).fetchone()

created_source = None
try:
    # NOT used as a context manager, deliberately: entering it would run the
    # app's lifespan, and the lifespan closes the pool on exit -- which section
    # 4 still needs. The lifespan's own work is exercised directly instead
    # (assert_app_role above), so nothing goes unchecked.
    client = TestClient(api.app)
    if True:
        def as_business(bid):
            """Sign in as this business, for real.

            This used to override the current_business dependency, which was the
            honest thing to do while there was no auth to exercise. There is now,
            so the check goes through it: a real session row, a real cookie, and
            the tenant reaching SET LOCAL by the same route a browser takes.
            Overriding the dependency today would skip the join between auth and
            tenancy, which is the part most worth testing.
            """
            client.cookies.set(auth.COOKIE, auth.create_session(bid))

        as_business(BUSINESS_A)
        stats_a = client.get("/stats").json()
        check("A's /stats counts only A's facts",
              (stats_a["facts"], stats_a["facts_awaiting_review"]),
              (a_confirmed, a_waiting))
        check("A's /stats counts only A's sources", stats_a["sources"], a_sources)

        as_business(BUSINESS_B)
        stats_b = client.get("/stats").json()
        check("B's /stats sees its one fact and none of A's",
              (stats_b["facts"], stats_b["facts_awaiting_review"],
               stats_b["sources"]), (1, 0, 0))
        check("B's /source list is empty though A has sources",
              client.get("/source").json(), [])
        check("B's /review queue does not contain A's proposals",
              client.get("/review").json(), [])

        # The endpoint the deploy conversation kept naming. B knows A's fact id
        # -- it is a uuid in a URL -- and still cannot reach it.
        if a_unconfirmed_id:
            r = client.delete(f"/review/{a_unconfirmed_id[0]}")
            check("B deleting A's proposal by id is a 404", r.status_code, 404)
            still = admin.execute(
                "select count(*) from fact where id = %s",
                (a_unconfirmed_id[0],)).fetchone()[0]
            check("and A's proposal is still there", still, 1)
        else:
            print("  [skip] no unconfirmed fact for A to attempt")

        # A write through a real endpoint, with no business_id anywhere in the
        # request or in the INSERT. The column default supplies it.
        made = client.post("/source/paste", params={
            "content": "check_tenancy write path.",
            "label": "check_tenancy"}).json()
        created_source = made["id"]
        owner = admin.execute(
            "select business_id from source where id = %s",
            (created_source,)).fetchone()[0]
        check("a write with no business_id named lands in B", str(owner),
              BUSINESS_B)

        as_business(BUSINESS_A)
        check("and A cannot see it", client.get(f"/source/{created_source}"
                                                ).status_code, 404)

        client.cookies.clear()

    # -----------------------------------------------------------------------
    print("\n4. Unique constraints are per business, not global")
    # RLS does not narrow a unique index -- it filters what a query SEES, and a
    # unique index checks what EXISTS. These two were global before 0007, and
    # they are the part of tenancy no mechanism covers: a missed one is invisible
    # with a single tenant and only appears when two businesses both have a
    # Rasulova, or two open orders land on the same amount.
    #
    # THE ROWS MUST COEXIST, and the first version of this section got that
    # wrong. It inserted each row inside a transaction it then rolled back, so
    # A's row was already gone when B's was written and the two never met. Run
    # against the pre-0007 GLOBAL indexes it still reported both businesses
    # happy -- it passed against the exact bug it exists to catch. Committing
    # A's row before writing B's is the whole difference.
    #
    # So each pair below is run TWICE: once as the schema stands, where both
    # must succeed, and once with the old global index restored, where the
    # second must fail. Without the second run this section cannot tell you it
    # has gone blind again.

    made: list[tuple[str, object]] = []

    def add_alias(bid):
        with connection(bid) as c:
            made.append(("alias", c.execute(
                "insert into alias (subject_key, alias, alias_key, confirmed)"
                " values ('rasulova', 'Rasulova', 'rasulova', true)"
                " returning id").fetchone()[0]))

    def add_amount(bid):
        with connection(bid) as c:
            made.append(("purchase", c.execute(
                "insert into purchase (chat_id, item, subject_key, attribute,"
                " base_amount, suffix, amount, expires_at)"
                " values (1, 'check_tenancy', 'check_tenancy', 'narx',"
                " 100000, 7, 100007, now() + interval '1 hour')"
                " returning id").fetchone()[0]))

    def both_succeed(fn) -> bool:
        """A's row is committed before B's is attempted, so they coexist."""
        try:
            fn(BUSINESS_A)
            fn(BUSINESS_B)
            return True
        except psycopg.errors.UniqueViolation:
            return False

    def drop_made():
        while made:
            table, rid = made.pop()
            admin.execute(f"delete from {table} where id = %s", (rid,))

    CASES = (
        ("the same alias", add_alias,
         "create unique index control_global on alias (alias_key, subject_key)"),
        ("the same open-order amount", add_amount,
         "create unique index control_global on purchase (amount)"
         " where state in ('awaiting_payment', 'awaiting_owner')"),
    )

    for label, fn, global_index in CASES:
        check(f"both businesses may have {label}", both_succeed(fn), True)
        drop_made()

        # The control: put the pre-0007 index back and confirm the same code
        # now refuses. If this reports True, the check above proved nothing.
        admin.execute(global_index)
        try:
            check(f"control: with the OLD global index, {label} is refused",
                  both_succeed(fn), False)
        finally:
            drop_made()
            admin.execute("drop index control_global")

finally:
    print("\n   tearing down check_tenancy B")
    if created_source:
        admin.execute("delete from chunk where source_id = %s",
                      (created_source,))
    admin.execute("delete from fact where business_id = %s", (BUSINESS_B,))
    admin.execute("delete from chunk where business_id = %s", (BUSINESS_B,))
    admin.execute("delete from source where business_id = %s", (BUSINESS_B,))
    admin.execute("delete from alias where business_id = %s", (BUSINESS_B,))
    admin.execute("delete from purchase where business_id = %s", (BUSINESS_B,))
    admin.execute("delete from business where id = %s", (BUSINESS_B,))

# ---------------------------------------------------------------------------
print("\n5. Nothing checks a connection out without a tenant, except twice")
# The structural half. Every guarantee above assumes the tenant is bound at
# checkout, and the way to lose that is for someone to write pool.connection()
# again -- which still compiles and still runs. Inside a request it would raise
# on the first tenant table, but the raise happens at the far end of whatever
# was written in between. This makes the rule findable instead of remembered.
#
# THREE files are allowed, each for a stated reason, and the counts are exact so
# a new call fails this check on the day it is written:
#
#   app/main.py   1  /health/db -- current_database() and the pgvector version.
#                    Asks about the server, not about anyone's data.
#   bot.py        1  check_database -- select 1, before the bot announces it is
#                    up. Same: about the server.
#   app/auth.py   6  the chicken-and-egg. "Who is this" has to be answerable
#                    BEFORE a tenant is known, which is exactly what the tenancy
#                    policies forbid, so these cannot run on a tenanted
#                    connection. The exemption is made safe by the assertion
#                    below rather than by trusting the count: every one of them
#                    calls an app_* SECURITY DEFINER function and none of them
#                    names a tenant table.

ALLOWED = {str(pathlib.Path("app/main.py")): 1,
           str(pathlib.Path("app/auth.py")): 6,
           "bot.py": 1}

found: dict[str, int] = {}
for path in sorted(pathlib.Path("app").glob("*.py")) + [pathlib.Path("bot.py")]:
    if path.name == "db.py":
        continue  # db.py IS the checkout; that is its job
    for line in path.read_text(encoding="utf-8").splitlines():
        if "pool.connection(" in line.split("#", 1)[0]:
            found[str(path)] = found.get(str(path), 0) + 1
check("exactly the un-tenanted checkouts named above, and no others",
      found, ALLOWED)

# What makes auth.py's six safe is not the count but what they may touch. An
# untenanted connection that queried `fact` directly would return nothing (the
# policy raises), but one that queried `business` or `session` would be reading
# platform data with no tenant -- which is the whole point of the resolvers.
#
# Read with ast rather than by scanning lines. The first version of this took
# the string on the same line as `conn.execute(`, which silently skipped every
# call whose SQL wrapped -- and then compared that subset against itself, so it
# reported "every query is a resolver" while looking at three of seven. It only
# surfaced because the LAST assertion named the seven explicitly and did not
# match. A count compared against a count drawn from the same faulty extraction
# can never disagree with itself.
TENANT_TABLES = ("fact", "source", "alias", "chunk", "purchase",
                 "session", "login_token", "business", "retrievable_fact")

tree = ast.parse(pathlib.Path("app/auth.py").read_text(encoding="utf-8"))
auth_queries = [
    node.args[0].value
    for node in ast.walk(tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
    and node.args and isinstance(node.args[0], ast.Constant)
    and isinstance(node.args[0].value, str)
]
check("all seven of auth.py's queries were found, not just the one-liners",
      len(auth_queries), 7)
check("every one of them calls an app_* resolver",
      sum("app_" in q for q in auth_queries), len(auth_queries))
check("and none names a table directly",
      [q for q in auth_queries
       if any(f" {t} " in f" {q} " for t in TENANT_TABLES)], [])
check("the resolvers it uses are the ones granted in 0008",
      sorted({q.split("app_")[1].split("(")[0] for q in auth_queries}),
      sorted(["business_for_email", "business_for_telegram",
              "login_token_create", "login_token_claim", "session_create",
              "session_business", "session_delete"]))

admin.close()
pool.close()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

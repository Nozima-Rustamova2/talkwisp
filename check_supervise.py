"""The reconcile loop: does it actually converge, and does it stop hammering.

    uv run python check_supervise.py

Runs the REAL reconcile() against the REAL database, with one substitution:
children are a trivial script that sleeps, not bot.py. That is deliberate and
worth being explicit about -- spawning real bots would need live Telegram tokens
for scratch businesses, which do not exist, and the thing under test is the
loop's arithmetic, not whether bot.py polls. What bot.py does when started is
check_bot.py's job.

THE TWO SECTIONS THAT MATTER ARE 3 AND 4, and both are the same move: break the
thing on purpose and watch it come back. A reconcile loop that has never been
watched recovering is a loop nobody knows recovers. Section 3 kills a child;
section 4 takes a business out of the desired set. Neither has a code path of
its own in supervise.py -- both are just a difference between two lists -- which
is the property being checked.
"""

import os
import pathlib
import sys
import time

import psycopg
from dotenv import load_dotenv

import supervise
from app.db import pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
PREFIX = "check_supervise scratch "
FAKE_TOKEN = "999999999:AAEcheckSuperviseScratchToken00000001"


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
    admin.execute("delete from business where name like %s", (PREFIX + "%",))

    # A child that just sleeps. Spawning real bots would need live tokens for
    # businesses that do not have them; the loop's arithmetic is what is
    # under test.
    stub = pathlib.Path("_supervise_stub.py")
    stub.write_text("import sys, time\ntime.sleep(3600)\n", encoding="utf-8")
    real_spawn = supervise.spawn
    real_python = supervise.PYTHON
    supervise.PYTHON = pathlib.Path(sys.executable)

    def stub_spawn(business_id, name, child):
        import subprocess
        child.log = open(os.devnull, "w", encoding="utf-8")
        child.process = subprocess.Popen(
            [sys.executable, str(stub)], stdout=child.log,
            stderr=subprocess.STDOUT)
        child.started = time.monotonic()

    supervise.spawn = stub_spawn
    pool.open()
    ids = []
    try:
        for n in (1, 2):
            ids.append(str(admin.execute(
                "insert into business (name, bot_token, approved) "
                "values (%s, %s, true) returning id",
                (f"{PREFIX}{n}", FAKE_TOKEN[:-1] + str(n))).fetchone()[0]))
        run(admin, ids)
    finally:
        for child in list(getattr(main, "children", {}).values()):
            supervise.stop(child, "check teardown")
        supervise.spawn = real_spawn
        supervise.PYTHON = real_python
        stub.unlink(missing_ok=True)
        admin.execute("delete from business where name like %s", (PREFIX + "%",))
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def run(admin, ids: list[str]) -> None:
    children: dict[str, supervise.Child] = {}
    main.children = children

    print("\n1. The desired set is the database's answer, not this file's")
    want = supervise.desired()
    check("both scratch businesses are wanted",
          all(i in want for i in ids), True)

    # `approved` is in the WHERE clause of app_businesses_to_poll, not applied
    # afterwards in Python -- so no caller can forget it. This is that claim,
    # checked rather than trusted.
    admin.execute("update business set approved = false where id = %s", (ids[0],))
    check("an unapproved business drops out of the desired set",
          ids[0] in supervise.desired(), False)
    admin.execute("update business set approved = true where id = %s", (ids[0],))

    admin.execute("update business set bot_token = null where id = %s", (ids[0],))
    check("so does one with no token", ids[0] in supervise.desired(), False)
    admin.execute("update business set bot_token = %s where id = %s",
                  (FAKE_TOKEN[:-1] + "1", ids[0]))

    print("\n2. A first reconcile starts everything that is missing")
    supervise.reconcile(children)
    alive = [i for i in ids if children.get(i) and children[i].process]
    check("both scratch children are running", len(alive), 2)
    # AGAINST THE DESIRED SET, not against 2. The first version asserted "two
    # children exist", which is only true on a database that holds nothing but
    # this check's own rows -- it failed immediately against a real one, where
    # Default business also has a token. The property that actually matters is
    # that the loop CONVERGED: one child per wanted business, no more, no less.
    # Counting the check's own rows would have made this pass on an empty
    # database and fail on every real one, which is the wrong way round.
    running = len([c for c in children.values() if c.process])
    check("and the loop converged: one child per wanted business",
          running, len(supervise.desired()))

    print("\n3. CONTROL: kill a child and watch it come back")
    # No code path in supervise.py says "restart a crashed child" -- it is the
    # same difference between two lists as a brand-new customer. This is that
    # claim under test.
    victim = children[ids[0]]
    old_pid = victim.process.pid
    victim.process.kill()
    victim.process.wait()
    supervise.reconcile(children)          # reaps, records the failure, backs off
    check("the dead child was reaped, not left as a zombie",
          children[ids[0]].process, None)
    check("and a backoff was recorded", children[ids[0]].failures, 1)

    # It must NOT come back instantly -- that is the restart loop a revoked
    # token would cause.
    supervise.reconcile(children)
    check("it does not restart immediately (backoff is holding)",
          children[ids[0]].process, None)

    children[ids[0]].next_try = 0          # pretend the backoff elapsed
    supervise.reconcile(children)
    back = children[ids[0]].process
    check("once the backoff elapses it is restarted", back is not None, True)
    check("and it is a NEW process, not the corpse",
          back.pid != old_pid, True)

    print("\n4. CONTROL: remove the token and watch the child stop")
    admin.execute("update business set bot_token = null where id = %s", (ids[1],))
    supervise.reconcile(children)
    check("the child is gone from the table", ids[1] in children, False)

    admin.execute("update business set bot_token = %s where id = %s",
                  (FAKE_TOKEN[:-1] + "2", ids[1]))
    supervise.reconcile(children)
    check("and putting the token back brings it straight up",
          children.get(ids[1]) is not None
          and children[ids[1]].process is not None, True)

    print("\n5. Backoff grows, and is capped")
    c = supervise.Child("backoff probe")
    delays = []
    for n in range(1, 8):
        c.failures = n
        delays.append(min(supervise.BACKOFF_START * (2 ** (n - 1)),
                          supervise.BACKOFF_MAX))
    check("it doubles", delays[:4], [5, 10, 20, 40])
    check("and stops at the cap", delays[-1], supervise.BACKOFF_MAX)
    check("the cap is not so long a bot stays down all day",
          supervise.BACKOFF_MAX <= 600, True)

    print("\n6. The cap refuses rather than exhausting Postgres")
    # The failure this prevents is global: connection exhaustion returns "too
    # many clients" to the API as well, so one tenant's bots would take the web
    # app down for every tenant.
    check("the cap is derived from connections, not memory",
          supervise.MAX_CHILDREN <= 16, True)
    saved = supervise.MAX_CHILDREN
    supervise.MAX_CHILDREN = 1
    for i in ids:
        if children.get(i) and children[i].process:
            supervise.stop(children[i], "making room for the cap test")
        children.pop(i, None)
    supervise.reconcile(children)
    running = len([c for c in children.values() if c.process])
    check("with a cap of 1, only one child starts", running, 1)
    supervise.MAX_CHILDREN = saved


if __name__ == "__main__":
    main()

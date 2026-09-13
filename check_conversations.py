"""The customers screen, and the one failure it must never have.

    uv run python check_conversations.py

THE SECTION THAT MATTERS IS 2. This is the first code in the system that READS
a .jsonl, and a file has no row-level security: the tenant filter is an `if` in
Python rather than a policy Postgres enforces. So the thing worth proving is not
that business A sees its own customers -- it is that business A cannot see
business B's, and that the check would notice if it could.

Section 2 therefore does both halves. It plants B's line and asserts it is
absent, and THEN breaks the filter on purpose and asserts the same line appears.
Without the second half, "B's customer was not in A's list" is equally true of a
filter that works, a reader that returned nothing, and a file that was empty.

Nothing here touches the real messages.jsonl. It writes a scratch file and
points the module at it -- a check that appends to the live log would put
invented customers on a real screen.
"""

import datetime
import json
import os
import pathlib
import sys
import tempfile

import psycopg
from dotenv import load_dotenv
from fastapi.testclient import TestClient

import app.main as api
from app import auth, conversations
from app.db import connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
ADDR_A = "check-conversations-a@example.invalid"
ADDR_B = "check-conversations-b@example.invalid"

NOW = datetime.datetime(2026, 9, 13, 12, 0, tzinfo=datetime.UTC)


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


def clear(admin, addr: str) -> None:
    admin.execute("delete from business where owner_email = %s", (addr,))


def line(**kw) -> dict:
    """One log line, with the fields bot.py always writes."""
    kw.setdefault("at", NOW.isoformat())
    return kw


def write_log(path: pathlib.Path, rows: list) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")


def main() -> None:
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set.")
    admin = psycopg.connect(admin_url, autocommit=True)
    for addr in (ADDR_A, ADDR_B):
        clear(admin, addr)
    biz_a = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values (%s, %s, true) returning id",
        ("check_conversations A", ADDR_A)).fetchone()[0])
    biz_b = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values (%s, %s, true) returning id",
        ("check_conversations B", ADDR_B)).fetchone()[0])

    scratch = pathlib.Path(tempfile.mkdtemp()) / "messages.jsonl"
    real = conversations.MESSAGE_LOG
    conversations.MESSAGE_LOG = scratch
    pool.open()
    try:
        run(admin, biz_a, biz_b, scratch)
    finally:
        conversations.MESSAGE_LOG = real
        for addr in (ADDR_A, ADDR_B):
            clear(admin, addr)
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def run(admin, biz_a: str, biz_b: str, scratch: pathlib.Path) -> None:
    mins = lambda n: (NOW + datetime.timedelta(minutes=n)).isoformat()

    write_log(scratch, [
        # Anvar: three messages. The third is 40 minutes after the second, so
        # it is a SECOND conversation, not a long first one.
        line(business_id=biz_a, chat_id=111, first_name="Anvar",
             username="anvar_t", question="Narxi qancha?", answer="250 000",
             status="ok", at=mins(0)),
        line(business_id=biz_a, chat_id=111, first_name="Anvar",
             username="anvar_t", question="Karta raqami?", outcome="social",
             at=mins(5)),
        line(business_id=biz_a, chat_id=111, first_name="Anvar",
             # RENAMED. The newest non-null username is the one that still
             # reaches them.
             username="anvar_new", question="Yakshanba ishlaysizmi?",
             outcome="escalated", at=mins(45)),
        # Dilnoza: one message, no username at all -- a real Telegram account
        # can have none, and the screen must not require one.
        line(business_id=biz_a, chat_id=222, first_name="Dilnoza",
             question="MRT narxi?", answer="400 000", status="ok",
             at=mins(10)),
        # The owner testing their own bot. Not a customer.
        line(business_id=biz_a, chat_id=999, is_owner=True,
             first_name="Owner", question="test", at=mins(20)),
        # ANOTHER BUSINESS'S CUSTOMER. Section 2 is about this line.
        line(business_id=biz_b, chat_id=333, first_name="Boburmirzo",
             username="bobur", question="Kurs narxi?", at=mins(1)),
        # Written before bot.py stamped business_id. Unattributable.
        line(chat_id=444, first_name="Eski", question="Eski savol",
             at=mins(2)),
    ])

    print("\n1. Customers, grouped, with the numbers the header carries")
    with connection(biz_a):
        view = conversations.overview()
    check("two people, not five lines", view["people"], 2)
    check("three conversations across them", view["conversations"], 3)
    by_id = {c["chat_id"]: c for c in view["customers"]}
    check("Anvar's three messages", by_id[111]["messages"], 3)
    check("in two conversations, split by the 20-minute gap",
          by_id[111]["conversations"], 2)
    check("the newest username wins", by_id[111]["username"], "anvar_new")
    check("a customer with no username is still listed",
          by_id[222]["username"], None)
    check("and keeps their first name", by_id[222]["first_name"], "Dilnoza")
    check("the last question, verbatim",
          by_id[111]["last_question"], "Yakshanba ishlaysizmi?")
    check("most recent first", view["customers"][0]["chat_id"], 111)
    check("THE OWNER IS NOT A CUSTOMER", 999 in by_id, False)

    print("\n2. CONTROL: business A cannot see business B's customers")
    check("B's customer is absent from A's list", 333 in by_id, False)
    with connection(biz_b):
        theirs = conversations.overview()
    check("and IS present in B's own list",
          [c["chat_id"] for c in theirs["customers"]], [333])

    # THE NEGATIVE HALF. Everything above is also true of a reader that is
    # simply broken. Break the filter the way a careless edit would -- accept
    # every attributed line regardless of tenant -- and the planted line must
    # appear. If it does not, this section proves nothing about the filter.
    original = conversations.read_lines

    def unfiltered() -> tuple[list, int, int]:
        rows, unattributed, malformed = original()
        with conversations.MESSAGE_LOG.open(encoding="utf-8") as handle:
            everything = [json.loads(x) for x in handle if x.strip()]
        return [r for r in everything if r.get("business_id")], unattributed, malformed

    conversations.read_lines = unfiltered
    try:
        with connection(biz_a):
            leaked = conversations.overview()
    finally:
        conversations.read_lines = original
    check("with the filter removed, B's customer LEAKS into A's list",
          333 in {c["chat_id"] for c in leaked["customers"]}, True)
    check("so the filter is what excluded them, not an empty read",
          leaked["people"] > view["people"], True)

    print("\n3. Unattributed lines are counted and skipped, never guessed")
    with connection(biz_a):
        view = conversations.overview()
    check("the pre-business_id line is not shown",
          444 in {c["chat_id"] for c in view["customers"]}, False)
    check("but it is counted, so the screen can say so",
          view["unattributed"], 1)
    check("and it is not silently handed to B either",
          theirs["unattributed"], 1)

    print("\n4. A truncated line does not blank the screen")
    with scratch.open("a", encoding="utf-8") as handle:
        handle.write('{"business_id": "' + biz_a + '", "chat_id": 55')
    with connection(biz_a):
        view = conversations.overview()
    check("the good rows still render", view["people"], 2)
    check("and the broken one is counted", view["malformed"], 1)

    print("\n5. The reader cannot be asked for the wrong tenant")
    # It takes no business argument at all -- it reads the ContextVar that
    # connection() sets. Outside a connection there is nothing to read, and it
    # must raise rather than return everything.
    try:
        conversations.overview()
        check("unbound read raises", "no error", "RuntimeError")
    except RuntimeError as exc:
        check("unbound read raises rather than returning every tenant's lines",
              "No business is bound" in str(exc), True)

    print("\n6. Through the API, with a session cookie")
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, auth.create_session(biz_a))
    response = client.get("/conversations")
    check("GET /conversations answers", response.status_code, 200)
    body = response.json()
    check("with A's two customers", body["people"], 2)
    check("and not B's", 333 in {c["chat_id"] for c in body["customers"]}, False)

    detail = client.get("/conversations/111")
    check("GET /conversations/111 answers", detail.status_code, 200)
    turns = detail.json()
    check("three turns, oldest first", len(turns), 3)
    check("oldest really is first", turns[0]["question"], "Narxi qancha?")
    check("and the outcome survives", turns[2]["outcome"], "escalated")

    # A second business's session must not reach the first's customer, even
    # with the chat_id in hand -- the URL is guessable and 111 is a real chat.
    other = TestClient(api.app)
    other.cookies.set(auth.COOKIE, auth.create_session(biz_b))
    check("B asking for A's customer by id gets nothing",
          other.get("/conversations/111").json(), [])

    print("\n7. bot.py stamps the name on the right chat, and only that one")
    import bot
    bot.remember_sender({"chat": {"id": 111},
                         "from": {"id": 111, "first_name": "Anvar",
                                  "username": "anvar_t",
                                  "language_code": "uz"}})
    entry = {"chat_id": 111, "question": "x"}
    bot.BUSINESS_ID = biz_a
    captured = []
    real_log = bot.MESSAGE_LOG
    bot.MESSAGE_LOG = scratch.parent / "botlog.jsonl"
    try:
        bot.log(entry)
        # THE GUARD. The order-expiry sweep and the owner's escalation replies
        # log a DIFFERENT chat_id than the update last handled. Stamping those
        # would put one person's name on another person's row.
        other_entry = {"chat_id": 222, "outcome": "customer_unreachable"}
        bot.log(other_entry)
        captured = [entry, other_entry]
    finally:
        bot.MESSAGE_LOG = real_log
    check("the sender's own line gets the name", captured[0].get("first_name"),
          "Anvar")
    check("and the username", captured[0].get("username"), "anvar_t")
    check("Telegram's language hint, under its own name",
          captured[0].get("telegram_language"), "uz")
    check("A DIFFERENT CHAT GETS NO NAME", "first_name" in captured[1], False)


if __name__ == "__main__":
    main()

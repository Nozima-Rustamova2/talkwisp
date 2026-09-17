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
    for table in ("escalation", "fact"):
        admin.execute(f"delete from {table} where business_id in"
                      " (select id from business where owner_email = %s)", (addr,))
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
        # ONE MESSAGE, TWO LOG LINES. buy_prefilter_blocked logs and then
        # FALLS THROUGH to answer(), which logs again -- so the log has two
        # rows about a single thing the customer typed. Seconds apart.
        line(business_id=biz_a, chat_id=222, first_name="Dilnoza",
             question="MRT qancha turadi?", outcome="buy_prefilter_blocked",
             at=mins(12)),
        line(business_id=biz_a, chat_id=222, first_name="Dilnoza",
             question="MRT qancha turadi?", answer="400 000 so'm",
             status="ok", at=mins(12)),
        # THE SAME WORDS AGAIN, TWO MINUTES LATER. This is a second message and
        # must stay a second row -- it is exactly what the customer in the
        # transcript that started this work did when the first reply did not
        # help.
        line(business_id=biz_a, chat_id=222, first_name="Dilnoza",
             question="MRT qancha turadi?", answer="400 000 so'm",
             status="ok", at=mins(14)),
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
    check("and Dilnoza's four log lines", by_id[222]["messages"], 4)
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
    check("and the outcome is in the owner's words", turns[2]["note"],
          "Sent to you")

    print("\n7. ONE ENTRY PER MESSAGE, and no internal vocabulary")
    dilnoza = client.get("/conversations/222").json()
    # Four log lines: one plain question, then a prefilter line and its answer
    # for the same message, then the same words again two minutes later.
    check("four log lines become three entries", len(dilnoza), 3)
    check("the two lines about one message fold into one",
          [d["question"] for d in dilnoza],
          ["MRT narxi?", "MRT qancha turadi?", "MRT qancha turadi?"])
    check("and the folded entry keeps the answer that arrived second",
          dilnoza[1]["answer"], "400 000 so'm")
    check("the repeat two minutes later stays its own entry",
          dilnoza[2]["answer"], "400 000 so'm")

    # THE ONE THE OWNER MUST NEVER SEE. buy_prefilter_blocked is a decision the
    # bot made about its own pipeline; rendered straight it reads as an error
    # the business caused.
    blob = json.dumps(dilnoza, ensure_ascii=False)
    for word in ("buy_prefilter_blocked", "not_approved", "outcome", "status"):
        check(f"{word!r} does not reach the screen", word in blob, False)

    anvar = client.get("/conversations/111").json()
    # An answered question carries no note -- the answer IS what happened. Only
    # the lines where something else became of the message get one.
    check("outcomes read as the owner would say them",
          [a["note"] for a in anvar],
          [None, "Greeted them", "Sent to you"])

    # A second business's session must not reach the first's customer, even
    # with the chat_id in hand -- the URL is guessable and 111 is a real chat.
    other = TestClient(api.app)
    other.cookies.set(auth.COOKIE, auth.create_session(biz_b))
    check("B asking for A's customer by id gets nothing",
          other.get("/conversations/111").json(), [])

    print("\n8. bot.py stamps the name on the right chat, and only that one")
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

    print("\n9. THE OWNER'S REPLIES ARE IN THE TRANSCRIPT")
    # The log line for an owner's reply is written under the OWNER's chat and
    # carries the question, not the words. So a transcript built from the log
    # showed the agent refusing and then nothing, when a person had answered.
    def escalation(biz, chat_ids, question, status, answer, at):
        admin.execute(
            "insert into escalation (business_id, chat_ids, question,"
            " question_key, status, answer, answered_at)"
            " values (%s, %s, %s, %s, %s, %s, %s)",
            (biz, chat_ids, question, question.lower(), status, answer, at))

    words = "Yakshanba 10:00 dan 14:00 gacha ishlaymiz."
    # Answered at +3 minutes, which falls BETWEEN Anvar's first two messages --
    # so it also proves the merge sorts by time rather than appending.
    escalation(biz_a, [111, 222], "Yakshanba ishlaysizmi?", "answered", words,
               NOW + datetime.timedelta(minutes=3))
    # Waiting, and closed with no words ("not for us"): neither was said.
    escalation(biz_a, [111], "Chegirma bormi?", "waiting", None, None)
    escalation(biz_a, [111], "Parkovka bormi?", "answered", None,
               NOW + datetime.timedelta(minutes=4))
    # ANOTHER BUSINESS, THE SAME CHAT ID. Chat ids are Telegram's, not ours, so
    # one person talking to two businesses has one id in both.
    secret = "B biznesining javobi, A ko'rmasligi kerak."
    escalation(biz_b, [111], "Kurs narxi?", "answered", secret,
               NOW + datetime.timedelta(minutes=6))

    # The words are nowhere in the log. Whatever shows them below read the table.
    check("the owner's words are not in the log at all",
          words in scratch.read_text(encoding="utf-8"), False)

    anvar = client.get("/conversations/111").json()
    replies = [a for a in anvar if a["from"] == "owner"]
    check("the owner's reply is in the customer's transcript",
          [r["answer"] for r in replies], [words])
    check("marked as the owner's, not the agent's",
          {a["from"] for a in anvar if a["answer"] == words}, {"owner"})
    check("and placed where it happened, between the first two messages",
          [a["question"] or a["answer"] for a in anvar][:3],
          ["Narxi qancha?", words, "Karta raqami?"])
    check("a waiting escalation and a wordless close add nothing",
          len(anvar), 4)
    check("the same reply reaches everyone who was waiting",
          [a["answer"] for a in client.get("/conversations/222").json()
           if a["from"] == "owner"], [words])
    check("ANOTHER BUSINESS'S REPLY TO THE SAME CHAT ID IS ABSENT",
          secret in json.dumps(anvar, ensure_ascii=False), False)
    check("and present for that business",
          [a["answer"] for a in other.get("/conversations/111").json()
           if a["from"] == "owner"], [secret])

    print("\n10. PROVENANCE: where a reply came from, and whether it still holds")
    from app import console

    def fact(biz, subject, attribute, value):
        return str(admin.execute(
            "insert into fact (business_id, subject, subject_key, attribute,"
            " attribute_key, value, value_key, confirmed) values"
            " (%s, %s, lower(%s), %s, lower(%s), %s, lower(%s), true) returning id",
            (biz, subject, subject, attribute, attribute, value, value)).fetchone()[0])

    kept = fact(biz_a, "MRT", "narx", "400 000 so'm")
    edited = fact(biz_a, "MRT", "ish vaqti", "09:00-18:00")
    gone = fact(biz_a, "MRT", "manzil", "Chilonzor 5")
    theirs = fact(biz_b, "Kurs", "narx", "B ning narxi 999")

    # What bot.py logs, from the same function it calls. The value must APPEAR
    # in the reply to count as used; a refusal logs nothing.
    result = {"status": "ok", "answer": "MRT 400 000 so'm, 09:00-18:00 ishlaydi.",
              "context_facts": [
                  {"id": kept, "subject": "MRT", "attribute": "narx",
                   "value": "400 000 so'm"},
                  {"id": edited, "subject": "MRT", "attribute": "ish vaqti",
                   "value": "09:00-18:00"},
                  {"id": gone, "subject": "MRT", "attribute": "manzil",
                   "value": "Chilonzor 5"}]}
    logged = console.facts_used(result)
    check("only facts whose wording is in the reply are logged",
          [f["id"] for f in logged], [kept, edited])
    check("a refusal logs none", console.facts_used(dict(result, status="unknown")), [])

    used = logged + [{"id": gone, "subject": "MRT", "attribute": "manzil",
                      "value": "Chilonzor 5"},
                     # An id that is not this business's, with invented wording.
                     {"id": theirs, "subject": "Kurs", "attribute": "narx",
                      "value": "logged wording"}]
    with scratch.open("a", encoding="utf-8") as handle:
        # Section 4 left a deliberately truncated line with no newline; without
        # this the first row below would be glued onto it and read as malformed.
        handle.write("\n")
        for row in [
            line(business_id=biz_a, chat_id=555, question="MRT narxi va vaqti?",
                 answer=result["answer"], status="ok", facts_used=used,
                 at=mins(60)),
            line(business_id=biz_a, chat_id=555, question="Qayerdasiz?",
                 answer="Aniq aytolmayman.", status="ok", facts_used=[],
                 at=mins(61)),
            # Before provenance was logged: no key at all.
            line(business_id=biz_a, chat_id=555, question="Eski savol?",
                 answer="Eski javob.", status="ok", at=mins(62)),
        ]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    # AFTER the reply: one fact edited, one deleted.
    admin.execute("update fact set value = '10:00-19:00' where id = %s", (edited,))
    admin.execute("delete from fact where id = %s", (gone,))

    turns = client.get("/conversations/555").json()
    facts = {f["id"]: f for f in turns[0]["facts"]}
    check("an unchanged fact says so", facts[kept]["now"], "unchanged")
    check("an EDITED fact says it changed", facts[edited]["now"], "changed")
    check("keeping the wording the reply was built from",
          facts[edited]["value"], "09:00-18:00")
    check("beside what it says now", facts[edited]["current"]["value"],
          "10:00-19:00")
    check("a DELETED fact says so, with what it said",
          (facts[gone]["now"], facts[gone]["value"]), ("deleted", "Chilonzor 5"))
    check("another business's id reads as deleted, never as its text",
          (facts[theirs]["now"], "B ning narxi" in json.dumps(turns, ensure_ascii=False)),
          ("deleted", False))
    check("recorded-but-none is an empty list", turns[1]["facts"], [])
    check("never recorded is null, not empty", turns[2]["facts"], None)
    admin.execute("delete from fact where id in (%s, %s, %s)", (kept, edited, theirs))

    print("\n11. A REPLY THAT DID NOT REACH A CUSTOMER IS MARKED, NOT HIDDEN")
    # Through bot.py's real delivery, with Telegram faked: one reply, three
    # customers waiting, and each send ends differently.
    import contextlib

    from app import escalation as esc

    outcomes = {
        601: bot.SendResult(True),
        602: bot.SendResult(False, blocked=True, detail="bot was blocked by the user"),
        603: bot.SendResult(False, detail="ReadTimeout"),
    }
    saved = (bot.send, bot.parse_fact, bot.typing, bot.BUSINESS_ID, bot.MESSAGE_LOG)
    bot.send = lambda chat, text: outcomes.get(chat, bot.SendResult(True))
    bot.parse_fact = lambda conn, line: {"error": "not a fact", "parsed": None}
    bot.typing = lambda chat: contextlib.nullcontext()
    bot.BUSINESS_ID = biz_a
    bot.MESSAGE_LOG = scratch
    try:
        with connection(biz_a) as conn:
            esc_id, _ = esc.open_or_join(conn, 601, "Parkovka bormi?", {})
            esc.open_or_join(conn, 602, "Parkovka bormi?", {})
            esc.open_or_join(conn, 603, "Parkovka bormi?", {})
            esc.start_answering(conn, esc_id, 9001)
            bot.deliver_owner_answer(conn, 9001, esc.get(conn, esc_id),
                                     "Ha, binoning orqasida.")
    finally:
        (bot.send, bot.parse_fact, bot.typing, bot.BUSINESS_ID,
         bot.MESSAGE_LOG) = saved

    def owner_turn(chat):
        return [t for t in client.get(f"/conversations/{chat}").json()
                if t["from"] == "owner"]

    got, blocked, failed_send = owner_turn(601), owner_turn(602), owner_turn(603)
    check("delivered: the reply is shown, unmarked",
          [(t["answer"], t["undelivered"]) for t in got],
          [("Ha, binoning orqasida.", None)])
    check("BLOCKED: the reply is still shown, marked as blocked",
          [(t["answer"], t["undelivered"]) for t in blocked],
          [("Ha, binoning orqasida.", "blocked")])
    check("another failure is marked as a failure, not as blocked",
          [t["undelivered"] for t in failed_send], ["failed"])
    check("the failure line is not a row of its own",
          len(client.get("/conversations/602").json()), 1)
    # The failure is logged under the CUSTOMER's chat, so it cannot mark the
    # same reply for someone who did receive it.
    lines = [json.loads(x) for x in scratch.read_text(encoding="utf-8").splitlines()
             if x.strip().startswith("{") and "owner_reply_undelivered" in x]
    check("one failure line per customer who missed it, none for the one who got it",
          sorted(x["chat_id"] for x in lines), [602, 603])


if __name__ == "__main__":
    main()

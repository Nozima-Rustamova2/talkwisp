"""The status plate, and the one thing it must never become.

    uv run python check_dashboard.py

THE SECTION THAT MATTERS IS 2. "Agent is answering" is the product's central
honesty claim, and the failure it exists to avoid is documented in
docs/competitors.md: ManyChat's onboarding completed, every step reported
green, and the bot was dead. A plate that lights up because a token column is
non-null reproduces that exactly.

So the assertions here are mostly NEGATIVE ONES. Every part of the check is
proved by breaking it and watching the plate go dark FOR THE STATED REASON --
a token saved with a stale heartbeat must read "connected but not running", not
"answering" and not "no channel". A plate that says something false confidently
is worse than no plate.

Section 4 is the second-smallest version of this codebase's recurring failure:
the sparkline and the figures beside it are two counts of one thing, and the
first version derived them from different sets, so the bars summed to 15 above
three numbers adding to 8.

Nothing here touches the real messages.jsonl or any real business.
"""

import datetime
import json
import os
import pathlib
import sys
import tempfile

import psycopg
from dotenv import load_dotenv

from app import conversations, dashboard
from app.db import connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
ADDR = "check-dashboard@example.invalid"
NOW = datetime.datetime.now(datetime.UTC)


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def log_lines(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def board(biz, approved=True):
    with connection(biz) as conn:
        return dashboard.board(conn, approved)


def main():
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set.")
    admin = psycopg.connect(admin_url, autocommit=True)
    admin.execute("delete from business where owner_email = %s", (ADDR,))
    biz = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values (%s, %s, true) returning id",
        ("check_dashboard scratch", ADDR)).fetchone()[0])

    scratch = pathlib.Path(tempfile.mkdtemp()) / "messages.jsonl"
    real = conversations.MESSAGE_LOG
    conversations.MESSAGE_LOG = scratch
    pool.open()
    try:
        run(admin, biz, scratch)
    finally:
        conversations.MESSAGE_LOG = real
        admin.execute("delete from fact where business_id = %s", (biz,))
        admin.execute("delete from business where owner_email = %s", (ADDR,))
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def seen(admin, biz, ago_seconds):
    admin.execute(
        "update business set bot_last_seen_at = now() - make_interval(secs => %s)"
        " where id = %s", (ago_seconds, biz))


def run(admin, biz, scratch):
    log_lines(scratch, [])

    print("\n1. Ordered blockers: the FIRST one, never three")
    # Nothing set up at all: no token, no heartbeat, no facts. Three things are
    # wrong and exactly one is reported.
    plate = board(biz)["plate"]
    check("no channel is the blocker", plate["blocker"]["says"],
          "Not answering — no channel connected")
    check("and it offers the fix", plate["blocker"]["fix"], "Connect Telegram")
    check("not answering", plate["answering"], False)

    # Approval outranks it. Same row, same missing token, different first cause.
    plate = board(biz, approved=False)["plate"]
    check("approval outranks the channel",
          plate["blocker"]["says"], "Not answering — your account isn't approved yet")
    # NEVER A DISABLED BUTTON. Approval is not something the owner can act on.
    check("and offers no action they cannot take", plate["blocker"]["fix"], None)

    print("\n2. THE MANYCHAT CASE: a saved token is not a running bot")
    admin.execute("update business set bot_token = %s where id = %s",
                  ("123456789:AAEscratchnotarealtoken0000000000000", biz))
    # The row now looks perfect. bot_last_seen_at is still NULL.
    plate = board(biz)["plate"]
    check("a token with no heartbeat does NOT read as answering",
          plate["answering"], False)
    check("and says the true thing, not 'no channel'", plate["blocker"]["says"],
          "Not answering — the agent is connected but not running")
    check("channel_live is false", plate["channel_live"], False)

    # A heartbeat from four minutes ago is a dead poller: the bot writes one
    # about once a minute, and the rule is three.
    seen(admin, biz, 240)
    check("a four-minute-old heartbeat is still not running",
          board(biz)["plate"]["answering"], False)

    # One minute ago is alive. Only knowledge is missing now.
    seen(admin, biz, 60)
    plate = board(biz)["plate"]
    check("a one-minute-old heartbeat IS running", plate["channel_live"], True)
    check("so the blocker moves on to knowledge", plate["blocker"]["says"],
          "Not answering — nothing confirmed to answer from")

    print("\n3. Knowledge means retrievable, not rows in a table")
    with connection(biz) as conn:
        conn.execute(
            "insert into fact (subject, subject_key, attribute, attribute_key,"
            " value, value_key, confirmed, expires_at) values"
            " ('Kurs','kurs','narx','narx','299 000','299 000',true,"
            "  now() - interval '1 day')")
    # An EXPIRED fact is a row that answers nothing. Counting it would make the
    # plate say "answering" for a business whose knowledge has all lapsed.
    plate = board(biz)["plate"]
    check("an expired fact is not knowledge", plate["facts"], 0)
    check("so the plate is still dark", plate["answering"], False)

    with connection(biz) as conn:
        conn.execute(
            "insert into fact (subject, subject_key, attribute, attribute_key,"
            " value, value_key, confirmed) values"
            " ('Kurs','kurs','format','format','onlayn','onlayn',true)")
    plate = board(biz)["plate"]
    check("a live confirmed fact is", plate["facts"], 1)
    # ALL THREE PARTS TRUE, and only now.
    check("AGENT IS ANSWERING", plate["answering"], True)
    check("with no blocker", plate["blocker"], None)

    # And the negative half: break one part and it goes dark again, so the
    # green above is the three checks passing rather than the code forgetting
    # to look.
    seen(admin, biz, 600)
    check("stop the heartbeat and it goes dark again",
          board(biz)["plate"]["answering"], False)
    seen(admin, biz, 30)

    print("\n4. The bars and the figures count the same questions")
    def line(**kw):
        kw.setdefault("business_id", biz)
        kw.setdefault("chat_id", 555)
        kw.setdefault("at", (NOW - datetime.timedelta(hours=1)).isoformat())
        return kw

    log_lines(scratch, [
        line(question="narxi", status="ok", answer="299 000"),
        line(question="qayerda", status="ok", answer="onlayn"),
        line(question="kim o'qitadi", status="unknown"),
        line(question="yakshanba", outcome="escalated"),
        # NEITHER ANSWERED NOR REFUSED NOR HANDED OVER. A purchase offer is in
        # no bucket, and must be left out of the bars AND the figures rather
        # than counted in one of them.
        line(question="sotib olaman", outcome="offer"),
        # The owner testing. Never a customer question.
        line(question="test", status="ok", is_owner=True),
        # Older than the window.
        line(question="eski", status="ok",
             at=(NOW - datetime.timedelta(days=9)).isoformat()),
    ])
    week = board(biz)["week"]
    check("answered", week["answered"], 2)
    check("couldn't answer", week["unanswered"], 1)
    check("handed over", week["handed_over"], 1)
    check("the offer is in no bucket", week["counted"], 4)
    check("THE BARS SUM TO THE FIGURES", sum(week["days"]), week["counted"])
    check("seven bars", len(week["days"]), 7)

    print("\n5. The share is suppressed below the floor")
    check("four questions is not a percentage", week["share"], None)
    # Returned rather than hardcoded in the component: the screen tells the
    # owner "needs 20+ questions", and that number must be the one the server
    # actually used to decide.
    check("and the floor is returned so the screen can say why",
          week["floor"], dashboard.RATE_FLOOR)

    # Above the floor it appears. Twenty answered out of twenty.
    log_lines(scratch, [line(question=f"q{i}", status="ok", answer="x")
                        for i in range(dashboard.RATE_FLOOR)])
    week = board(biz)["week"]
    check("at the floor it is computed", week["share"], 100)
    check("from the same count the bars use", sum(week["days"]), week["counted"])

    print("\n6. Attention: empty is a real state")
    log_lines(scratch, [])
    admin.execute("delete from fact where business_id = %s and attribute_key = %s",
                  (biz, "narx"))
    items = board(biz)["attention"]
    check("nothing needs you", items, [])

    # An unconfirmed fact is the row Samira's transcript argues for: a
    # knowledge base that looks complete in the document and answers only the
    # parts that were confirmed.
    with connection(biz) as conn:
        conn.execute(
            "insert into fact (subject, subject_key, attribute, attribute_key,"
            " value, value_key, confirmed) values"
            " ('Kurs','kurs','boshlanish','boshlanish','12-sentabr',"
            "  '12-sentabr',false)")
    items = board(biz)["attention"]
    check("an unconfirmed fact asks to be reviewed",
          [i["kind"] for i in items], ["review"])
    check("with a verb", items[0]["fix"], "Review")
    check("and somewhere to go", items[0]["href"], "#/review")


if __name__ == "__main__":
    main()

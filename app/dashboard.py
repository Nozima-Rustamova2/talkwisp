"""The board: are you working, is it worth it, what to do next.

THE STATUS PLATE IS THE HONESTY CLAIM AND IT IS NOT A ROW-EXISTS CHECK.

The reason this screen exists is the ManyChat failure in docs/competitors.md:
onboarding completed successfully, every step reported green, and the bot was
dead. A plate that lights up because a token column is non-null reproduces that
exactly -- it would have said "Agent is answering" for the whole of Samira's
first day, when her bot was fine, and would equally have said it for a business
whose poller had been dead for a week.

So each of the three parts is measured against the thing it claims:

  billing    `business.approved`. Named honestly: there is no billing yet, no
             plan and no invoice, so this is approval and the plate says so
             rather than implying a payment relationship that does not exist.

  channel    NOT "a token is saved". bot_last_seen_at within three minutes --
             the heartbeat the poller writes about once a minute. A token saved
             with nothing polling is precisely the state the Settings screen was
             built to stop asserting, and migration 0012 exists for it.

  knowledge  Counted from `retrievable_fact`, never from `fact`. A business
             whose facts have all expired has rows and answers nothing. This is
             the same definition /stats already uses; reusing it rather than
             writing a second count is the point -- two definitions of "what it
             knows" would drift the first time one was edited.

Contradictions come from knowledge.everything() for the same reason. It is the
heavier call, and it is the only place that knows an expired fact does not
contradict a live one.
"""

import datetime

from psycopg import Connection

from app import gaps, knowledge, payment
from app.conversations import read_lines

# Two missed beats. The poller touches bot_last_seen_at about once a minute, so
# three minutes rides out a restart or a slow poll without ever reporting a dead
# process as running. The SAME interval app/main.py:channel_state() uses, which
# carries the full reasoning -- a second number here would be a second opinion
# about whether a bot is alive.
LIVE_WITHIN = "3 minutes"

WEEK = datetime.timedelta(days=7)

# Below this many questions a percentage is noise wearing a decimal point.
# Three questions with one handoff renders as 67% and reads as failure when
# nothing is wrong. A JUDGEMENT, not a measurement -- there is not enough
# traffic anywhere yet to have measured where a rate starts meaning something.
RATE_FLOOR = 20

# At most five, mixed types, each one line with a verb. More than five is a
# list to work through rather than a thing to notice, and the design is explicit
# that this column is the second kind.
ATTENTION_LIMIT = 5


def plate(conn: Connection, approved: bool) -> dict:
    """Whether the agent is answering, or the FIRST thing stopping it.

    ONE BLOCKER, NEVER THREE. A plate that stacks every problem is a plate
    nobody reads, and the order is fixed: an unapproved account cannot spend, a
    business with no channel has nobody to answer, and knowledge is the last
    thing to fix because the other two make it pointless.
    """
    row = conn.execute(
        "select bot_token is not null,"
        f" bot_last_seen_at > now() - interval '{LIVE_WITHIN}'"
        " from business").fetchone()
    has_token = bool(row and row[0])
    polling = bool(row and row[1])

    facts, waiting = conn.execute(
        "select count(*) filter (where confirmed),"
        "       count(*) filter (where not confirmed) from retrievable_fact"
    ).fetchone()

    blocker = None
    if not approved:
        blocker = {
            "says": "Not answering — your account isn't approved yet",
            # No fix link. Approval is something we do, not something they can
            # act on, and a button that cannot work is the thing this product
            # keeps refusing to draw.
            "fix": None, "href": None,
        }
    elif not has_token:
        blocker = {"says": "Not answering — no channel connected",
                   "fix": "Connect Telegram", "href": "#/settings"}
    elif not polling:
        # THE STATE THAT LOOKS FINE IN THE DATABASE. A token is saved, the row
        # is perfect, and nothing is reading updates.
        blocker = {"says": "Not answering — the agent is connected but not running",
                   "fix": "Check the channel", "href": "#/settings"}
    elif not facts:
        blocker = {"says": "Not answering — nothing confirmed to answer from",
                   "fix": "Add knowledge", "href": "#/"}

    return {
        "answering": blocker is None,
        "blocker": blocker,
        "facts": facts,
        "waiting_for_review": waiting,
        "channel_live": has_token and polling,
        # Said, not implied. There is no plan, no invoice and no card on file
        # for Talkwisp itself; approval is what gates spending today.
        "plan": "Early access" if approved else "Awaiting approval",
    }


def _questions(rows: list) -> list:
    """Log lines that were a customer asking something.

    Lines with no question are button taps and order events. Counting them as
    questions would inflate every figure on the band.
    """
    return [r for r in rows if r.get("question") and not r.get("is_owner")]


def _bucket(row: dict) -> str | None:
    """Which figure this question belongs to, or None if it belongs to none.

    ONE FUNCTION DECIDES, and both the figures and the sparkline use it. The
    first version counted the bars from every question in the window and the
    figures from those carrying a status, so the bars summed to 15 above three
    numbers adding to 8. Two counts of one thing, derived from different sets,
    on the same band -- the failure this codebase keeps producing, in its
    smallest possible form.

    A question that produced a purchase offer is in no bucket: it was neither
    answered from knowledge nor refused nor handed over. Rather than invent a
    fourth figure the design does not have, it is left out of BOTH -- so the
    bars and the totals describe exactly the same questions.
    """
    if row.get("outcome") == "escalated":
        return "handed_over"
    if row.get("status") == "ok":
        return "answered"
    if row.get("status") == "unknown":
        return "unanswered"
    return None


def week(conn: Connection) -> dict:
    """Four figures and seven bars, from the message log.

    THE LAST FIGURE IS THE ONE THAT SAYS THE PRODUCT WORKS, and it is the one
    most easily made to lie. It is suppressed below RATE_FLOOR rather than
    rounded, because a percentage computed from four questions is a number that
    will be quoted back at us.
    """
    rows, _, _ = read_lines()
    now = datetime.datetime.now(datetime.UTC)
    since = now - WEEK

    graded = []
    for row in _questions(rows):
        bucket = _bucket(row)
        if bucket is None:
            continue
        try:
            at = datetime.datetime.fromisoformat(row["at"])
        except (KeyError, ValueError, TypeError):
            continue
        if at >= since:
            graded.append((at, bucket))

    answered = sum(1 for _, b in graded if b == "answered")
    unanswered = sum(1 for _, b in graded if b == "unanswered")
    handed_over = sum(1 for _, b in graded if b == "handed_over")
    total = len(graded)
    share = round(100 * answered / total) if total >= RATE_FLOOR else None

    # Seven bars, oldest first, drawn as divs by the screen. Days with nothing
    # are zeros and are kept: a gap in the week is information. They sum to
    # `total` by construction, because _bucket() chose both.
    days = []
    for back in range(6, -1, -1):
        day = (now - datetime.timedelta(days=back)).date()
        days.append(sum(1 for at, _ in graded if at.date() == day))

    return {"answered": answered, "unanswered": unanswered,
            "handed_over": handed_over, "share": share,
            "counted": total, "floor": RATE_FLOOR, "days": days}


def attention(conn: Connection, approved: bool) -> list[dict]:
    """At most five things, each one line with a verb.

    EMPTY IS A REAL STATE and the screen celebrates it rather than apologising.
    Nothing here is invented to fill the column.
    """
    items: list[dict] = []

    # Contradictions, from the one place that knows what a contradiction is.
    # everything() excludes expired facts from disagreeing and honours the
    # "expected multiple" dismissal; a count written in SQL here would be a
    # second answer that drifts the day either rule changes.
    disputes = sum(group.get("disputes", 0) for group in knowledge.everything(conn))
    if disputes:
        items.append({
            "kind": "contradiction",
            "says": f"{disputes} {'fact disagrees' if disputes == 1 else 'facts disagree'}"
                    " with another",
            "fix": "Resolve", "href": "#/knowledge",
        })

    # Customers waiting on a person. Counted from the table, not the log: the
    # table is what the owner's Telegram is working from.
    escalations = conn.execute(
        "select count(*) from escalation"
        " where status in ('waiting', 'answering')").fetchone()[0]
    if escalations:
        items.append({
            "kind": "escalation",
            "says": f"{escalations} {'customer is' if escalations == 1 else 'customers are'}"
                    " waiting for you to answer",
            "fix": "Open Telegram", "href": None,
        })

    waiting = conn.execute(
        "select count(*) from retrievable_fact where not confirmed").fetchone()[0]
    if waiting:
        items.append({
            "kind": "review",
            "says": f"{waiting} {'fact' if waiting == 1 else 'facts'} read from your"
                    " files, not checked yet",
            "fix": "Review", "href": "#/review",
        })

    # The silent one. After the buy guard landed, a business with prices and no
    # card number loses sales with no symptom at all -- the customer is answered
    # correctly and nothing looks wrong.
    if approved and payment.has_orderable_prices(conn) \
            and not payment.completeness(conn)["ready"]:
        items.append({
            "kind": "payment",
            "says": "Customers can't pay — no card number saved",
            "fix": "Add it", "href": "#/payment",
        })

    return items[:ATTENTION_LIMIT]


# Five, as the design has it. The Gaps screen shows the rest from the SAME call
# with no limit -- the column is a window onto that list, not a second one.
UNKNOWN_LIMIT = 5


def board(conn: Connection, approved: bool) -> dict:
    return {"plate": plate(conn, approved),
            "week": week(conn),
            "unknown": gaps.open_gaps(limit=UNKNOWN_LIMIT),
            "attention": attention(conn, approved)}

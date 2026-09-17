"""Who talked to the bot, how often, and about what.

NOT A LOG VIEWER. Reading what the agent said is already better served by the
test console, which shows provenance and scores for a question you choose. The
capability that is missing is seeing your CUSTOMERS -- for a course seller that
is closer to a contacts list than a log, and nothing covers it today.

THE FIRST READER OF ANY .jsonl IN THIS SYSTEM. gaps, feedback and messages are
all opened "a" and never read back, so this is where a file stops being
write-only and the tenancy question becomes real: a file has no RLS, and the
filter is an `if` in Python rather than a policy in Postgres.

Which is why read_lines() takes NO business argument. It asks
current_business_id() itself, so a caller cannot pass the wrong tenant -- the
same shape as the approval gate, for the same reason. The honest limit: that
constrains THIS reader, not a second one written later. A table would make a
cross-tenant read impossible rather than merely hard.

WHEN THIS SHOULD BECOME A TABLE: the first question that needs a join. "Asked
about the price and never ordered" is a real sales question and `purchase` is a
table -- you cannot join a file to one. Until then the rule holds, because this
is append-only observation and nothing here is mutable state anyone waits on.
"""

import datetime
import json
import pathlib
from collections import defaultdict

from app.db import current_business_id

MESSAGE_LOG = pathlib.Path(__file__).parent.parent / "messages.jsonl"

# A follow-up twenty minutes later is a new conversation. The number is not
# invented here: bot.py uses exactly this gap to decide whether a message is a
# follow-up worth rewriting against history. Two different numbers for "still
# the same conversation" would eventually disagree on screen.
#
# Duplicated rather than imported because bot.py is a script -- importing it
# reads the environment and picks a business.
IDLE = datetime.timedelta(minutes=20)


def read_lines() -> tuple[list[dict], int, int]:
    """(this business's lines, skipped-unattributed, skipped-malformed).

    SKIPPED, NEVER GUESSED. Lines written before bot.py stamped business_id
    carry none, and there is no way to attribute them now -- attribution has to
    be written at the time or not at all. Backfilling them to "probably the
    only business that existed then" is defensible and unverified, and the
    failure it risks is showing one business another business's customers,
    silently. The count is returned so the screen can say so out loud.
    """
    business = current_business_id()          # raises if nothing is bound
    mine, unattributed, malformed = [], 0, 0
    if not MESSAGE_LOG.exists():
        return mine, 0, 0
    with MESSAGE_LOG.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A process killed mid-append leaves half a line. One truncated
                # line must not blank the whole screen.
                malformed += 1
                continue
            if not row.get("business_id"):
                unattributed += 1
            elif row["business_id"] == business:
                mine.append(row)
    return mine, unattributed, malformed


def _when(row: dict):
    try:
        return datetime.datetime.fromisoformat(row["at"])
    except (KeyError, ValueError, TypeError):
        return None


def _conversations(times: list) -> int:
    """How many separate visits, by idle gap."""
    if not times:
        return 0
    return 1 + sum(1 for a, b in zip(times, times[1:]) if b - a > IDLE)


def overview() -> dict:
    """The whole screen in one response.

    THE HEADER IS THE SCREEN AT TWELVE CONVERSATIONS and the table is the
    screen at five hundred; both are these same three numbers plus rows. A
    table of column headings above two rows reads as a broken product, so the
    counts carry it until there are enough rows to carry themselves.

    No pagination: at 500 conversations this file is about a megabyte and every
    operation here is a full scan anyway. That is wrong at 50,000; it is not
    wrong yet.
    """
    rows, unattributed, malformed = read_lines()

    people: dict[int, dict] = defaultdict(
        lambda: {"messages": 0, "times": [], "questions": [],
                 "first_name": None, "username": None})

    for row in rows:
        chat_id = row.get("chat_id")
        if chat_id is None:
            continue
        # THE OWNER IS NOT A CUSTOMER. Their own testing is the bulk of the
        # volume today, and a contacts list led by the owner's own chat is one
        # nobody trusts.
        if row.get("is_owner"):
            continue
        who = people[chat_id]
        who["messages"] += 1
        at = _when(row)
        if at:
            who["times"].append(at)
        if row.get("question"):
            who["questions"].append((at, row["question"]))
        # LAST NON-NULL WINS. People change their display name and their
        # username; the newest one is the one that can still be reached.
        for field in ("first_name", "username"):
            if row.get(field):
                who[field] = row[field]

    customers = []
    for chat_id, who in people.items():
        times = sorted(who["times"])
        asked = [q for _, q in sorted(who["questions"],
                                      key=lambda p: (p[0] is not None, p[0]))]
        customers.append({
            "chat_id": chat_id,
            "first_name": who["first_name"],
            "username": who["username"],
            "messages": who["messages"],
            "conversations": _conversations(times),
            "first_at": times[0].isoformat() if times else None,
            "last_at": times[-1].isoformat() if times else None,
            # WHAT THEY ASKED, VERBATIM, because what they asked ABOUT is not
            # derivable: matched_on is null on all but the exact-tier hits and
            # the route names no subject. Clustering these into topics needs a
            # model call per customer, which is not worth it to fill a column.
            "last_question": asked[-1] if asked else None,
        })

    customers.sort(key=lambda c: c["last_at"] or "", reverse=True)
    return {
        "people": len(customers),
        "conversations": sum(c["conversations"] for c in customers),
        "last_at": customers[0]["last_at"] if customers else None,
        "unattributed": unattributed,
        "malformed": malformed,
        "customers": customers,
    }


# Routing decisions the bot made about ITS OWN pipeline. They are not things
# that happened to the customer, and the owner has no use for the words -- a
# screen that says "buy_prefilter_blocked" is showing its own plumbing.
#
# buy_prefilter_blocked is also the reason one message became two rows: it logs
# and then FALLS THROUGH to answer(), which logs again. The customer sent one
# message; the log has two lines about it.
_SILENT = {"buy_prefilter_blocked", "not_approved", "owner_claimed",
           "owner_claim_refused", "fact_written",
           # Not its own row: it marks the owner's reply it belongs to.
           "owner_reply_undelivered"}

# Everything else, said the way the owner would say it. An outcome missing from
# this table is shown as nothing rather than as its key: a gap here should look
# like a quiet row, never like a leaked identifier.
_OUTCOMES = {
    "social": "Greeted them",
    "throttled": "Asked them to write again in a moment",
    "escalated": "Sent to you",
    "escalation_answered": "You answered",
    "offer": "Offered to take payment",
    "offer_withheld_no_payment_details":
        "Answered, but couldn't offer payment — no card number saved",
    "order_created": "Order placed",
    "order_no_payment_details": "Order placed, but there was no card to send",
    "order_refused": "Couldn't place the order",
    "order_awaiting_payment": "Waiting for payment",
    "order_owner_confirmed": "You confirmed the payment",
    "order_owner_rejected": "You rejected the payment",
    "order_expired": "Order expired",
    "order_cancelled": "Order cancelled",
    "screenshot_attached": "Sent a payment screenshot",
    "screenshot_refused": "Screenshot couldn't be attached",
    "screenshot_no_order": "Sent a screenshot with no open order",
    "customer_unreachable": "Couldn't reach them",
    "llm_error": "Something went wrong on our side",
    "database_error": "Something went wrong on our side",
    "fact_write_error": "Something went wrong on our side",
    "error": "Something went wrong on our side",
}

# Two log lines about one message are written within a second of each other.
# A customer who sends the SAME words again two minutes later -- which is
# exactly what happened in the transcript that started this -- is a second
# message and must stay a second row.
SAME_MESSAGE = datetime.timedelta(seconds=10)


def exchange(conn, chat_id: int) -> list[dict]:
    """One customer's conversation, oldest first, ONE ENTRY PER MESSAGE, with
    the owner's own replies in it.

    THE OWNER'S TURNS COME FROM THE ESCALATION TABLE, not the log. The log line
    for an owner's reply is written under the OWNER's chat and carries the
    question, not the words -- so a transcript built from the log alone showed
    the agent refusing and then nothing, when a person had answered. That is
    the screen misrepresenting what happened. escalation.answer holds the words
    and chat_ids holds everyone they went to, so past replies come back too.

    Each entry says who spoke: `from` is "agent" for log entries and "owner"
    for these. Honest limit: delivery is counted per reply, not per customer,
    so a customer who had blocked the bot is shown the reply they never got.

    The log is a record of what the bot DID, and a single customer message can
    produce more than one line of that -- a routing decision, then an answer.
    Rendered straight, the screen shows the same question twice and calls the
    first one buy_prefilter_blocked. So the lines are folded back into the
    messages they describe.

    Read-only on purpose. Replying lives in Telegram: the owner is not at a
    laptop at 9pm, the takeover flow already works there, and the landing page
    promises exactly that in three languages.
    """
    rows, _, _ = read_lines()
    mine = [r for r in rows
            if r.get("chat_id") == chat_id and not r.get("is_owner")]
    mine.sort(key=lambda r: r.get("at") or "")

    # escalation id -> "blocked" or "failed", for replies that did not reach
    # THIS customer. Matched on the id bot.py logs, never on nearby timestamps.
    undelivered = {
        row["escalation_id"]: "blocked" if row.get("blocked") else "failed"
        for row in mine
        if row.get("outcome") == "owner_reply_undelivered" and row.get("escalation_id")}

    entries: list[dict] = []
    for row in mine:
        outcome = row.get("outcome")
        if outcome in _SILENT:
            continue
        entry = {
            "at": row.get("at"),
            "question": row.get("question"),
            "answer": row.get("answer"),
            "note": _OUTCOMES.get(outcome) if outcome else None,
            "from": "agent",
            # None, NOT [], for a line written before provenance was logged:
            # "we did not record it" and "no fact's wording is in this reply"
            # are different statements and the screen says each differently.
            "facts": row.get("facts_used"),
        }
        previous = entries[-1] if entries else None
        if (previous is not None
                and entry["question"]
                and previous["question"] == entry["question"]
                and not previous["answer"]
                and _close(previous["at"], entry["at"])):
            # The second line is the same message, answered. Keep the earlier
            # timestamp and take whichever half each line carried.
            previous["answer"] = previous["answer"] or entry["answer"]
            previous["note"] = previous["note"] or entry["note"]
            if previous["facts"] is None:
                previous["facts"] = entry["facts"]
            continue
        entries.append(entry)

    # Under RLS: another business's escalations are simply not there, whatever
    # chat_id is asked for.
    replies = conn.execute(
        "select id, answer, answered_at from escalation"
        " where status = 'answered' and answer is not null"
        "   and %s = any(chat_ids)", (chat_id,)).fetchall()
    # MARKED, NOT HIDDEN. Hiding an undelivered reply would also hide that the
    # owner answered; the true statement is "you replied, they did not get it".
    # Replies from before per-customer failures were logged carry None, which
    # is "not known", not "delivered".
    entries.extend({"at": at.isoformat(), "question": None, "answer": answer,
                    "note": None, "from": "owner", "facts": None,
                    "undelivered": undelivered.get(str(eid))}
                   for eid, answer, at in replies)
    _compare_with_now(conn, entries)
    # By TIME, not by string. The log writes +00:00 and Postgres writes the
    # session's offset, and two ISO strings in different offsets do not sort.
    entries.sort(key=lambda e: _instant(e["at"]))
    return entries


def _compare_with_now(conn, entries: list[dict]) -> None:
    """Mark each logged fact as it stands today: unchanged, changed, or deleted.

    THE VALUABLE HALF OF PROVENANCE. An owner looking at a bad answer needs to
    know whether the fact was wrong THEN or has changed SINCE -- "fix the
    knowledge" versus "this is already fixed". So the wording logged with the
    reply is compared with the row now, and a changed fact carries both.

    NEVER RE-RETRIEVED. Running the question again would show what today's
    knowledge would say, which looks like provenance and is a reconstruction.
    Read under RLS, so an id from another business reads as deleted rather
    than leaking its text -- and bot.py only ever logs this business's ids.
    """
    ids = {f["id"] for e in entries for f in (e.get("facts") or [])}
    if not ids:
        return
    now = {str(i): (s, a, v) for i, s, a, v in conn.execute(
        "select id, subject, attribute, value from fact where id = any(%s::uuid[])",
        (list(ids),)).fetchall()}
    for entry in entries:
        for fact in entry.get("facts") or []:
            current = now.get(fact["id"])
            if current is None:
                fact["now"] = "deleted"
            elif current == (fact["subject"], fact["attribute"], fact["value"]):
                fact["now"] = "unchanged"
            else:
                fact["now"] = "changed"
                fact["current"] = dict(zip(("subject", "attribute", "value"), current))


_EPOCH = datetime.datetime.min.replace(tzinfo=datetime.UTC)


def _instant(at: str | None) -> datetime.datetime:
    if not at:
        return _EPOCH
    try:
        moment = datetime.datetime.fromisoformat(at)
    except ValueError:
        return _EPOCH
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.UTC)


def _close(first: str | None, second: str | None) -> bool:
    if not first or not second:
        return False
    try:
        gap = datetime.datetime.fromisoformat(second) - \
            datetime.datetime.fromisoformat(first)
    except ValueError:
        return False
    return abs(gap) <= SAME_MESSAGE

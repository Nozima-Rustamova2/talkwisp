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


def exchange(chat_id: int) -> list[dict]:
    """One customer's messages, oldest first.

    Read-only on purpose. Replying lives in Telegram: the owner is not at a
    laptop at 9pm, the takeover flow already works there, and the landing page
    promises exactly that in three languages. A reply box here would duplicate
    a working feature and contradict a live promise.
    """
    rows, _, _ = read_lines()
    mine = [r for r in rows
            if r.get("chat_id") == chat_id and not r.get("is_owner")]
    mine.sort(key=lambda r: r.get("at") or "")
    return [{
        "at": r.get("at"),
        "question": r.get("question"),
        "answer": r.get("answer"),
        # What became of it -- escalated, throttled, an order, a refusal. The
        # answer field is null on most of these, and the outcome is the only
        # thing that says why.
        "outcome": r.get("outcome"),
        "status": r.get("status"),
    } for r in mine]

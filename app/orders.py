"""Orders. State, amounts, and the transitions between them. No Telegram, no
LLM, no message text -- those arrive in A2 and A3.

Every rule that protects money lives here rather than in the bot, so it holds
whatever calls it:

  * the amount comes from a confirmed fact, never from a model;
  * the amount must be ONE exact number -- a range is refused, not narrowed;
  * two open orders can never share an amount, and an amount is not reused for
    seven days even after an order expires;
  * a transition that is not legal from the current state fails, it does not
    silently overwrite.

WHAT THIS MODULE DOES NOT KNOW: whether anyone paid. `owner_confirmed` means
the owner looked at their banking app and said so. There is no verification
here and there is not going to be one.
"""

import re
import unicodedata

from psycopg import Connection, errors

# +1..+50 so'm. Small enough that nobody argues about it, wide enough that a
# clinic would need fifty simultaneous open orders for the same service before
# it runs out.
SUFFIX_MIN = 1
SUFFIX_MAX = 50

# A stale order nobody trusts is worse than no order.
EXPIRE_HOURS = 24

# Deliberately WIDER than EXPIRE_HOURS, and this is the whole reason it exists:
# expiry frees an amount, and a customer who pays thirty hours late would then
# send an amount belonging to somebody else's order. The seller would confirm
# the wrong one and never know. Seven days of quarantine costs nothing.
REUSE_DAYS = 7

OPEN_STATES = ("awaiting_payment", "awaiting_owner")

# What may follow what. Terminal states have no successors, so a double-tapped
# Confirm button is a refused transition rather than a second write.
TRANSITIONS = {
    "awaiting_payment": {"awaiting_owner", "expired", "cancelled"},
    "awaiting_owner": {"owner_confirmed", "owner_rejected", "expired",
                       "cancelled"},
    "owner_confirmed": set(),
    "owner_rejected": set(),
    "expired": set(),
    "cancelled": set(),
}

# Two, both canned. They are not decoration: "the amount does not match" means
# the customer should pay again correctly, and "I do not see this payment"
# means the customer should check with their bank. Different next steps, so
# they cannot be one button. A free-text third reason needs another
# conversation state and is deliberately not here yet.
REJECT_REASONS = ("amount_mismatch", "not_received")

_FIELDS = ("id", "chat_id", "item", "subject_key", "attribute", "base_amount",
           "suffix", "amount", "state", "screenshot_file_id", "reject_reason",
           "created_at", "expires_at", "screenshot_at", "owner_confirmed_at",
           "owner_rejected_at")
_SELECT = ", ".join(_FIELDS)


class OrderError(Exception):
    """A refusal with a machine-readable cause.

    The caller has to tell the customer something different for each one, and
    matching on message text is how that breaks silently later.
    """

    # The positional is `code`, not `reason`, so that a detail key called
    # `reason` -- which reject() genuinely wants -- cannot collide with it.
    # It did, on the first run.
    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.reason = code
        self.detail = detail


def _row(row) -> dict:
    return dict(zip(_FIELDS, row))


# ------------------------------------------------------------------- amounts

# Anything that makes a number approximate. A value carrying one of these is
# not an exact price even when it contains exactly one number: "100 000 so'mdan"
# is a floor, not a price.
_APPROXIMATE = ("-", "–", "—", "~", "…", "dan", "gacha",
                "taxminan", "atrofida", "boshlab", "+")


def parse_amount(value: str) -> int | None:
    """One exact sum in so'm, or None.

    None is not a parse failure to be worked around -- it is the answer that
    this price cannot become an order. Ranges are how a clinic honestly prices
    an MRT, and the rule that the agent quotes ranges rather than exact figures
    depends on them staying ranges. Picking the low end would be the model
    inventing a price with extra steps.
    """
    text = unicodedata.normalize("NFKC", value or "").lower()
    if any(marker in text for marker in _APPROXIMATE):
        return None

    # Join thousands groups: "160 000" is one number written with a space, and
    # every separator people use here has already become a plain space above.
    joined = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    numbers = re.findall(r"\d+", joined)
    if len(numbers) != 1:
        return None

    amount = int(numbers[0])
    # A sanity band, not a business rule: below this it is a typo or a quantity
    # that got matched by accident; above it something is very wrong.
    if not 1_000 <= amount <= 1_000_000_000:
        return None
    return amount


def price_for(conn: Connection, subject_key: str) -> dict:
    """The single confirmed exact price for a subject, or a refusal.

    Three ways this says no, and all three are correct behaviour:

      no_price       nothing priced under this subject -- it is not for sale
      several_prices more than one price fact. A doctor has `qabul narxi` and
                     `takroriy qabul narxi`, and a first visit is not a repeat
                     visit; choosing for the customer is choosing what to
                     charge them. The caller asks which.
      not_exact      the price is a range or a floor. See parse_amount.

    Only CONFIRMED facts. An unconfirmed price came out of a file and has not
    been read by a human, and the gap between reviewing a price and charging
    one is the entire point of the review queue.
    """
    rows = conn.execute(
        "select subject, attribute, value from fact"
        " where subject_key = %s and attribute_key like %s and confirmed"
        " order by attribute",
        (subject_key, "%narx%"),
    ).fetchall()

    if not rows:
        raise OrderError("no_price", subject_key=subject_key)
    if len(rows) > 1:
        raise OrderError("several_prices", subject_key=subject_key,
                         options=[{"subject": s, "attribute": a, "value": v}
                                  for s, a, v in rows])

    subject, attribute, value = rows[0]
    amount = parse_amount(value)
    if amount is None:
        raise OrderError("not_exact", subject_key=subject_key,
                         subject=subject, attribute=attribute, value=value)
    return {"subject": subject, "attribute": attribute, "value": value,
            "amount": amount}


def _taken_amounts(conn: Connection, low: int, high: int) -> set[int]:
    """Amounts that must not be issued again yet: every open order, plus every
    order of any state from the last REUSE_DAYS."""
    rows = conn.execute(
        "select amount from purchase"
        " where amount between %s and %s"
        "   and (state = any(%s)"
        "        or created_at > now() - (%s || ' days')::interval)",
        (low, high, list(OPEN_STATES), REUSE_DAYS),
    ).fetchall()
    return {r[0] for r in rows}


# ------------------------------------------------------------------ lifecycle

def create(conn: Connection, chat_id: int, subject_key: str) -> dict:
    """Open one order. Reads the price itself; the caller supplies no amount.

    That is not convenience. An amount passed in as an argument is an amount
    that could have come from anywhere, including a model, and the one thing
    this module guarantees is that it did not.
    """
    price = price_for(conn, subject_key)
    base = price["amount"]

    # Retry on the unique index rather than trusting the read: two customers
    # can order the same service in the same second, and the index is the only
    # thing that is actually atomic.
    for _ in range(3):
        taken = _taken_amounts(conn, base + SUFFIX_MIN, base + SUFFIX_MAX)
        free = [s for s in range(SUFFIX_MIN, SUFFIX_MAX + 1)
                if base + s not in taken]
        if not free:
            raise OrderError("amount_exhausted", base_amount=base)
        suffix = free[0]
        try:
            with conn.transaction():
                row = conn.execute(
                    "insert into purchase (chat_id, item, subject_key,"
                    " attribute, base_amount, suffix, amount, expires_at)"
                    " values (%s, %s, %s, %s, %s, %s, %s,"
                    "         now() + (%s || ' hours')::interval)"
                    " returning " + _SELECT,
                    (chat_id, price["subject"], subject_key,
                     price["attribute"], base, suffix, base + suffix,
                     EXPIRE_HOURS),
                ).fetchone()
            return _row(row)
        except errors.UniqueViolation:
            continue
    raise OrderError("amount_race", base_amount=base)


def _transition(conn: Connection, order_id, to_state: str,
                assign: str = "", params: tuple = ()) -> dict:
    """Move one order, or refuse.

    A conditional UPDATE, not read-then-write. The owner can tap Confirm twice
    before the first reply arrives, and only one of those taps may land.
    """
    legal = [s for s, allowed in TRANSITIONS.items() if to_state in allowed]
    row = conn.execute(
        "update purchase set state = %s" + assign
        + " where id = %s and state = any(%s) returning " + _SELECT,
        (to_state, *params, order_id, legal),
    ).fetchone()
    if row is None:
        current = conn.execute("select state from purchase where id = %s",
                               (order_id,)).fetchone()
        if current is None:
            raise OrderError("no_such_order", order_id=str(order_id))
        raise OrderError("bad_transition", order_id=str(order_id),
                         state=current[0], wanted=to_state)
    return _row(row)


def attach_screenshot(conn: Connection, order_id, file_id: str) -> dict:
    """Evidence FOR THE SELLER. Nothing here reads it, scores it or believes
    it -- see docs/design-decisions.md on why a vision check stays out."""
    return _transition(conn, order_id, "awaiting_owner",
                       ", screenshot_file_id = %s, screenshot_at = now()",
                       (file_id,))


def confirm(conn: Connection, order_id) -> dict:
    """The owner says the money arrived. That is all this records."""
    return _transition(conn, order_id, "owner_confirmed",
                       ", owner_confirmed_at = now()")


def reject(conn: Connection, order_id, reason: str) -> dict:
    if reason not in REJECT_REASONS:
        raise OrderError("bad_reason", reason=reason)
    return _transition(conn, order_id, "owner_rejected",
                       ", reject_reason = %s, owner_rejected_at = now()",
                       (reason,))


def cancel(conn: Connection, order_id) -> dict:
    return _transition(conn, order_id, "cancelled")


def expire_due(conn: Connection) -> list[dict]:
    """Expire everything past its deadline and RETURN the rows.

    Returning them is the point: the caller has to notify, and an order that
    expires silently is exactly the stale entry this is meant to prevent. The
    caller is also the place that caps the burst -- a bot that was down for a
    day comes back to a backlog, and forty separate messages is how a
    notification channel gets muted.
    """
    rows = conn.execute(
        "update purchase set state = 'expired'"
        " where state = any(%s) and expires_at <= now() returning " + _SELECT,
        (list(OPEN_STATES),),
    ).fetchall()
    return [_row(r) for r in rows]


def get(conn: Connection, order_id) -> dict | None:
    row = conn.execute("select " + _SELECT + " from purchase where id = %s",
                       (order_id,)).fetchone()
    return _row(row) if row else None


def open_for_chat(conn: Connection, chat_id: int) -> list[dict]:
    rows = conn.execute(
        "select " + _SELECT + " from purchase"
        " where chat_id = %s and state = any(%s) order by created_at",
        (chat_id, list(OPEN_STATES)),
    ).fetchall()
    return [_row(r) for r in rows]

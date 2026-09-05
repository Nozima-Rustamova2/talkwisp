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

    # DETAIL IS ONE POSITIONAL DICT, NOT **kwargs, and that is the whole point.
    #
    # It was **kwargs twice. The first time, reject() passed `reason=` and
    # collided with the positional -- fixed by renaming the positional to
    # `code`. The second time, a `subject_key` key was added to a price row and
    # `OrderError("not_exact", **row)` collided with the explicit
    # `subject_key=` beside it. Both were the same fault wearing different
    # names: a caller's DATA and this constructor's PARAMETERS shared one
    # namespace, so any new key in the data could collide with a parameter.
    #
    # A comment saying "do not splat rows into kwargs" is a rule someone has to
    # read at the right moment, and it had already failed to be read once. With
    # a positional dict there is no shared namespace, so the collision is not
    # avoided -- it is unrepresentable. A detail key called `code`, `reason` or
    # `self` is now just a key.
    def __init__(self, code: str, detail: dict | None = None):
        super().__init__(code)
        self.reason = code
        self.detail = detail or {}


def _row(row) -> dict:
    return dict(zip(_FIELDS, row))


# ------------------------------------------------------------------- amounts

# Anything that makes a number approximate. A value carrying one of these is
# not an exact price even when it contains exactly one number: "100 000 so'mdan"
# is a floor, not a price.
_APPROXIMATE = ("-", "–", "—", "~", "…", "dan", "gacha",
                "taxminan", "atrofida", "boshlab", "+")


def _flatten(text: str) -> str:
    """Fold every invisible or lookalike character to something this can read.

    Owners paste prices out of Word, Excel and PDFs, and those carry characters
    that are invisible on screen and fatal here: a zero-width space inside
    "60<zwsp>000" splits it into TWO numbers, so parse_amount refused it and
    price_for said `not_exact` -- telling the owner to set an exact amount for a
    price that already looked exact. A misleading error is worse than a wrong
    one, because it sends someone to fix what is not broken.

    By Unicode CATEGORY, not by a list of characters, because a list of
    invisible characters is a list nobody can proofread:

        Zs  every kind of space  -> a plain space (the digit-join then closes it)
        Cf  format characters    -> deleted; they render as nothing
        Pd  every kind of dash   -> "-", so _APPROXIMATE catches a range written
                                    with a non-breaking or figure dash

    normalize() already folds all of these for KEYS, which is why lookup was
    never affected and this stayed invisible. Only the money path reads the raw
    value, and the money path is where it mattered.
    """
    out = []
    for ch in text:
        category = unicodedata.category(ch)
        out.append(" " if category == "Zs" else
                   "" if category == "Cf" else
                   "-" if category == "Pd" else ch)
    return "".join(out)


def parse_amount(value: str) -> int | None:
    """One exact sum in so'm, or None.

    None is not a parse failure to be worked around -- it is the answer that
    this price cannot become an order. Ranges are how a clinic honestly prices
    an MRT, and the rule that the agent quotes ranges rather than exact figures
    depends on them staying ranges. Picking the low end would be the model
    inventing a price with extra steps.
    """
    text = _flatten(unicodedata.normalize("NFKC", value or "")).lower()
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


def price_options(conn: Connection, subject_key: str) -> list[dict]:
    """Every confirmed price fact for a subject, with the amount parsed.

    ONE query reads prices, and everything that needs one comes through here --
    price_for() below, and the purchase offer in app/buy.py. Two queries
    reading prices is two definitions of what a price is, and they diverge on
    the day someone adds an attribute.

    `amount` is None when the value is a range or a floor. Those rows are KEPT
    rather than filtered out, because the caller usually has to say why
    something cannot be bought, and a missing row cannot explain itself.

    `retrievable_fact`, not `fact`: a price shown to a customer is answering.
    """
    rows = conn.execute(
        "select subject, attribute, attribute_key, value from retrievable_fact"
        " where subject_key = %s and attribute_key like %s and confirmed"
        " order by attribute",
        (subject_key, "%narx%"),
    ).fetchall()
    # subject_key travels with each option so a caller holding a mixed list --
    # two doctors' prices in one keyboard -- can create the order from the row
    # it was handed, without matching a display name back to a key.
    return [{"subject": s, "subject_key": subject_key, "attribute": a,
             "attribute_key": ak, "value": v, "amount": parse_amount(v)}
            for s, a, ak, v in rows]


def price_for(conn: Connection, subject_key: str,
              attribute_key: str | None = None) -> dict:
    """The single confirmed exact price for a subject, or a refusal.

    `attribute_key` picks between several prices -- it is a SELECTOR, never an
    amount. The figure is still read from the confirmed fact here, so the
    guarantee that no amount can arrive from a model survives the extra
    parameter. Callers pass a key that came out of price_options(), never one a
    model produced.

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
    options = price_options(conn, subject_key)
    if attribute_key is not None:
        options = [o for o in options if o["attribute_key"] == attribute_key]

    if not options:
        raise OrderError("no_price", {"subject_key": subject_key,
                                      "attribute_key": attribute_key})
    if len(options) > 1:
        raise OrderError("several_prices", {"subject_key": subject_key,
                                            "options": options})

    chosen = options[0]
    if chosen["amount"] is None:
        # The whole row, passed as data rather than splatted into parameters.
        raise OrderError("not_exact", chosen)
    return chosen


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

def create(conn: Connection, chat_id: int, subject_key: str,
           attribute_key: str | None = None) -> dict:
    """Open one order. Reads the price itself; the caller supplies no amount.

    That is not convenience. An amount passed in as an argument is an amount
    that could have come from anywhere, including a model, and the one thing
    this module guarantees is that it did not.

    `attribute_key` chooses between several prices when a subject has more than
    one -- every doctor has both `qabul narxi` and `takroriy qabul narxi`. It
    selects a row; it never supplies a figure.
    """
    price = price_for(conn, subject_key, attribute_key)
    base = price["amount"]

    # Retry on the unique index rather than trusting the read: two customers
    # can order the same service in the same second, and the index is the only
    # thing that is actually atomic.
    for _ in range(3):
        taken = _taken_amounts(conn, base + SUFFIX_MIN, base + SUFFIX_MAX)
        free = [s for s in range(SUFFIX_MIN, SUFFIX_MAX + 1)
                if base + s not in taken]
        if not free:
            raise OrderError("amount_exhausted", {"base_amount": base})
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
    raise OrderError("amount_race", {"base_amount": base})


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
            raise OrderError("no_such_order", {"order_id": str(order_id)})
        raise OrderError("bad_transition", {"order_id": str(order_id),
                                            "state": current[0],
                                            "wanted": to_state})
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
        raise OrderError("bad_reason", {"reason": reason})
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

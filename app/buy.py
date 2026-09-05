"""Recognise "I want to buy X", and turn it into an OFFER -- never into an order.

THE CLASSIFIER PROPOSES; A TAP CREATES. Nothing in this module writes a row.
It returns options for the caller to render as buttons, and `orders.create()`
runs only when a human presses one.

That is a deliberate change from the original brief, which had buy intent
create the order directly. An order is not free: it occupies an amount, holds
it for 24 hours and quarantines it for seven days, and SUFFIX_MAX is 50. A
classifier that fires on "Kardiolog qabuli qancha turadi?" -- a price question,
which is one word away from a purchase in Uzbek -- would exhaust the suffix
space for a popular service over a few quiet weeks, and real customers would
start hitting `amount_exhausted` for reasons nobody could see.

With the tap in between, a false positive costs one unwanted button and zero
rows. The guarantee tightens from "the worst case is an unwanted payment offer"
to "the worst case is an offer nobody accepted".

WHAT THE MODEL MAY DO HERE: pick one name out of a list it was given. It cannot
invent a subject -- anything not in the list is dropped by code below -- and it
never sees or produces a price. Model routes, code decides, same as triage and
NO_ANSWER.

BOOKING IS NOT BUYING, and the reason is specific rather than fastidious.
The first measurement flagged "Menga ginekologga zapis qberila" -- sign me up
for the gynaecologist -- as a purchase, and read it correctly: it IS a request
to act. But AVISENA CANNOT RESERVE A SLOT. Offering to take payment there
charges someone for a time nobody can promise, and a customer who pays and then
finds there is no appointment is a worse outcome than a customer who was told
to call.

At a market stall, paying for a thing and getting the thing are one act, which
is why the original brief never separated them. At a clinic they are two, and
this system only does one of them. So prepayment intent qualifies and
scheduling intent does not; a scheduling request falls through to ordinary
retrieval, where the clinic's own stated booking instruction answers it -- and
if the business has not stated one, it refuses honestly and logs a gap.

BOOKING IS DELIBERATELY OUT OF SCOPE, not merely unbuilt. Slot availability,
calendar state, double-booking, cancellations and no-shows are a larger feature
than payments and share almost nothing with this code. It must not arrive as a
small extension of the order flow -- see docs/design-decisions.md.
"""

import json
import re

from psycopg import Connection

from app import orders
from app.llm import complete
from app.normalize import normalize

_SYSTEM = """You decide whether a customer message is a request to BUY something now.

Reply with JSON only. No prose, no code fences. Either:

    {"buy": true, "subject": "<a name copied exactly from the list>"}
    {"buy": false}

BUY means the customer is asking to PAY for a specific named thing, now.

BOOKING IS NOT BUYING, and this is the distinction that matters most here.
"Sign me up for the gynaecologist", "I want to make an appointment with the
cardiologist", "menga ginekologga zapis qberila", "хочу записаться к
кардиологу" are requests to RESERVE A TIME, not to pay. Answer false to all of
them. Paying for a consultation and holding a slot are different things, and
this business may not be able to hold one at all.

Answer true only when the customer says they want to PAY, TRANSFER, or SEND
MONEY for the thing: "to'lamoqchiman", "to'lovni amalga oshirmoqchiman", "хочу
оплатить", "I want to pay for".

These are also NOT buying, and are the most common mistakes:
- asking a price ("how much is X", "X narxi qancha", "сколько стоит X")
- asking whether something or someone exists, or is available
- asking opening hours, an address, a room number, or a doctor's schedule
- describing a symptom or asking what to do about one
- asking what to bring, whether children may come, or how to prepare
- any general enquiry that names no specific purchasable thing

WHEN IN DOUBT, ANSWER FALSE. A wrong "false" costs one ordinary answer. A wrong
"true" offers to charge someone who never asked to pay.

The subject must be copied EXACTLY from the list, character for character. If
what they want is not on the list, answer false."""


def _json(raw: str) -> dict:
    """Models add fences even when told not to. Take the outermost object."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in model reply: {raw[:200]}")
    return json.loads(match.group(0))


def purchasable(conn: Connection) -> list[dict]:
    """Every subject with a confirmed price, WITH the names customers use for it.

    The aliases are not decoration. The first measurement of this classifier
    listed subjects only and missed "Kardiologga yozilmoqchiman" -- I want to
    book a cardiologist -- in all three languages, because the subject is
    `Rahimov Alisher Bahodirovich` and `Kardiolog` is an alias. Nobody asks for
    a doctor by full name. Showing the model only canonical names measured a
    classifier nobody would ever trigger.

    Reading the view, not the table: this list is shown to a model.
    """
    rows = conn.execute(
        "select f.subject, coalesce(array_agg(distinct a.alias)"
        "   filter (where a.alias is not null), '{}')"
        "  from retrievable_fact f"
        "  left join alias a on a.subject_key = f.subject_key and a.confirmed"
        " where f.confirmed and f.attribute_key like %s"
        " group by f.subject order by f.subject",
        ("%narx%",),
    ).fetchall()
    return [{"subject": s, "aliases": list(aliases)} for s, aliases in rows]


def resolve(conn: Connection, name: str) -> list[str]:
    """Purchasable subjects this name could mean, by subject OR by alias.

    A list, because a name can mean more than one: there are two Karimovs, and
    "kardiolog" would mean two people at a clinic with two cardiologists.
    Choosing one would be choosing who the customer sees and what they pay.
    """
    key = normalize(name)
    if not key:
        return []
    rows = conn.execute(
        "select distinct f.subject from retrievable_fact f"
        " where f.confirmed and f.attribute_key like %s"
        "   and (f.subject_key = %s"
        "        or exists (select 1 from alias a"
        "                    where a.confirmed and a.alias_key = %s"
        "                      and a.subject_key = f.subject_key))"
        " order by f.subject",
        ("%narx%", key, key),
    ).fetchall()
    return [r[0] for r in rows]


def classify(conn: Connection, message: str) -> dict:
    """{"buy": bool, "subjects": [...]}. Reads nothing else, writes nothing.

    Whatever the model says is resolved against the database before it leaves
    here, so a caller can never be handed a name the business does not sell.
    The list may hold more than one subject -- see resolve().
    """
    items = purchasable(conn)
    catalogue = "\n".join(
        i["subject"] + (f"   (also called: {', '.join(i['aliases'])})"
                        if i["aliases"] else "")
        for i in items)
    raw = complete(_SYSTEM, f"Things this business sells:\n{catalogue}"
                            f"\n\nCustomer message: {message}")
    data = _json(raw)

    if not data.get("buy"):
        return {"buy": False, "subjects": []}

    # The model was told to copy a name exactly. Trusting that is how a
    # near-miss spelling becomes a lookup that silently finds nothing, so the
    # answer goes back through the database -- by subject or by alias, which is
    # the same route retrieval uses for the same reason.
    subjects = resolve(conn, str(data.get("subject") or ""))
    return {"buy": bool(subjects), "subjects": subjects}


# How many buttons still read as a choice rather than a wall. A judgement
# about a phone screen, not a principle -- flip it and the behaviour changes,
# nothing else does.
MAX_COMBINED = 6


def offer(conn: Connection, message: str) -> dict | None:
    """None if this is not a purchase. Otherwise what to put in front of the
    customer -- either buttons, or a refusal with a reason.

        {"subject": ..., "subject_key": ..., "options": [...]}   -> buttons
        {"subject": ..., "refusal": "no_price" | "not_exact"}    -> tell them

    `options` carries the STORED value, not a reformatted one. A button reads
    "Qabul narxi -- 250 000 soʻm", so the customer sees the figure before
    committing to it, and the label is assembled from two database columns
    rather than phrased by a model. Asking a model to word the choice invites
    it to swap which label carries which price, and that is a money error
    rather than a wording one.

    Showing an exact figure here is not a breach of "costs are approximate
    ranges". That rule binds the AGENT; this is the ORDER path, which has
    required an exact confirmed price since A1.
    """
    found = classify(conn, message)
    if not found["buy"]:
        return None

    # BOTH AXES AT ONCE. "Karimov" is two doctors and each has two prices, so
    # asking twice would be two taps before the customer sees a single figure.
    # On a phone that is a lot of turns to find out what something costs.
    #
    # So they are CROSSED into one keyboard while the result still fits on a
    # screen -- "Karimov Jasur -- qabul narxi -- 120 000 soʻm" answers both
    # questions in one tap. Above MAX_COMBINED the list stops being a choice
    # and becomes a wall, and the subject question is then the cheaper first
    # cut because it is the coarser distinction.
    #
    # The live data maxes out at 4. The cap exists for the clinic with four
    # cardiologists, not for this one.
    if len(found["subjects"]) > 1:
        crossed = [o for s in found["subjects"]
                   for o in orders.price_options(conn, normalize(s))
                   if o["amount"] is not None]
        if 0 < len(crossed) <= MAX_COMBINED:
            return {"choose": "price", "subjects": found["subjects"],
                    "options": crossed}
        return {"choose": "subject", "subjects": found["subjects"]}

    subject = found["subjects"][0]
    subject_key = normalize(subject)
    options = orders.price_options(conn, subject_key)

    if not options:
        # The list was built from subjects that have a price, so this means the
        # owner deleted it between the two queries. Rare, and a refusal.
        return {"subject": subject, "subject_key": subject_key,
                "refusal": "no_price"}

    orderable = [o for o in options if o["amount"] is not None]
    if not orderable:
        # Every price is a range or a floor. Not narrowed, not averaged -- the
        # item simply is not orderable, and the owner is the one who can fix it.
        return {"subject": subject, "subject_key": subject_key,
                "refusal": "not_exact", "options": options}

    # AXIS TWO: which price. Every doctor has both `qabul narxi` and
    # `takroriy qabul narxi`, so this fires on all 13 -- it is the normal case,
    # not an edge one.
    return {"choose": "price", "subject": subject, "subject_key": subject_key,
            "options": orderable}

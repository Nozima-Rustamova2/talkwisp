"""Check the bot's permission model without talking to Telegram.

    uv run python check_bot.py

Every callback used to be owner-only, so one blanket check was enough. Orders
introduced the first buttons a CUSTOMER may tap, and the check became per
action -- which is the kind of change that is fine on the day it is written and
wrong six months later, when somebody adds an action and does not think about
who may press it.

These assert the STRUCTURE of that decision rather than its behaviour: that
every action the handler implements has been classified, and that no owner
action has drifted into the customer allowlist. Both failures are silent in
production -- one makes a button that never works, the other makes a button
that works for the wrong person.

No database, no network, no model.
"""

import ast
import pathlib
import re
import sys

import bot

sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


SOURCE = pathlib.Path("bot.py").read_text(encoding="utf-8")
ast.parse(SOURCE)

# Read the actions out of the SOURCE, not out of a list someone maintains by
# hand beside them. A hand-kept list is a second definition of "what actions
# exist", and it would agree with the code exactly until the day it mattered.
# The dispatcher has TWO shapes: a literal `if action == "x"`, and membership
# in ORDER_ACTIONS for the ones that carry an order id. Both count as handled,
# and missing the second is how this check first reported `rejr` as unhandled
# when it works fine. A check that reads only one shape of the code is a check
# that is about a different program.
HANDLED = (set(re.findall(r'if action == "(\w+)"', SOURCE))
           | set(bot.ORDER_ACTIONS))

# Named here so the check states what it believes rather than deriving it from
# the thing under test. If orders ever become owner-initiated, this line is
# what should fail.
OWNER_ONLY = {"drop", "pick", "save", "conf", "rej", "rejr"}

print("\nthe callback permission model")

check("some actions are actually handled", len(HANDLED) > 0, True)
# ORDER_ACTIONS deliberately carry the order id in callback_data instead of a
# PENDING token, so they are exempt from the kind check -- and that exemption is
# asserted rather than assumed, because an order action that quietly acquired a
# PENDING entry would stop surviving restarts, which is the whole reason they
# are built this way.
check("every handled action is either classified or an order action",
      sorted(HANDLED - set(bot._KIND_FOR) - bot.ORDER_ACTIONS), [])
check("no order action is in _KIND_FOR",
      sorted(bot.ORDER_ACTIONS & set(bot._KIND_FOR)), [])
check("nothing is classified that is not handled",
      sorted(set(bot._KIND_FOR) - HANDLED), [])
# ORDER_ACTIONS is a declaration, so membership proves nothing on its own --
# a typo there would name an action no button ever sends. Each one must also
# appear in the source, in a callback_data string or a branch.
check("every order action actually appears in the code",
      sorted(a for a in bot.ORDER_ACTIONS if f'"{a}' not in SOURCE), [])
check("every customer action is a handled action",
      sorted(bot.CUSTOMER_ACTIONS - HANDLED), [])
check("no owner action is customer-tappable",
      sorted(bot.CUSTOMER_ACTIONS & OWNER_ONLY), [])
check("the owner-only actions are still the ones we think they are",
      sorted(HANDLED - bot.CUSTOMER_ACTIONS), sorted(OWNER_ONLY))
check("confirming and rejecting are NOT customer-tappable",
      sorted(bot.ORDER_ACTIONS & bot.CUSTOMER_ACTIONS), [])

print("")
print("every reject reason can actually be told to a customer")

# orders.REJECT_REASONS is the source of truth; the tables in bot.py are what
# the customer is told. A reason added there without a message here would
# raise KeyError inside reject(), in front of somebody who has already paid --
# and the button would have been offered to the owner first, so it would fail
# at the very last moment.
import app.orders as orders_mod
for table, what in ((bot.REJECT_LABELS, "a button label"),
                    (bot.REJECTED, "a customer message"),
                    (bot.REJECTED_DEFAULT, "a default message")):
    check(f"every reason has {what}",
          sorted(set(orders_mod.REJECT_REASONS) - set(table)), [])
check("no message exists for a reason the database would refuse",
      sorted(set(bot.REJECTED) - set(orders_mod.REJECT_REASONS)), [])

print("\nthe gate is an allowlist, not a denylist")

# The distinction is the whole safety property: an action added later and left
# unclassified must be owner-only, not public. Asserted against the real
# expression rather than trusted to the comment above it.
check("the check reads `action not in CUSTOMER_ACTIONS`",
      "if action not in CUSTOMER_ACTIONS and not is_owner_id(user_id):"
      in SOURCE, True)
check("an unknown action would be owner-only",
      "definitely_not_an_action" not in bot.CUSTOMER_ACTIONS, True)

print("\nthe owner check itself")

check("no owner means no owner -- never everyone",
      bot.is_owner_id(12345) if bot.OWNER_ID is None else True, False
      if bot.OWNER_ID is None else True)
check("a different id is not the owner", bot.is_owner_id("not-the-owner"), False)

# ---------------------------------------------------------------------------
print("\n  the typing indicator stops on every exit")
# The one property the whole design rests on. A bot left showing "typing" at a
# customer forever is worse than no indicator, and the failure is invisible from
# the server side -- nothing errors, the reply was sent, and only the person
# staring at the chat can see it.
#
# The network call is stubbed, so this asserts CONTROL FLOW and nothing else.
# No Telegram, no waiting.

import threading  # noqa: E402
import time  # noqa: E402

sent = []
bot.httpx.post = lambda url, **kw: sent.append(url) or type(
    "R", (), {"json": staticmethod(lambda: {"ok": True})})()


def threads_left_by(fn) -> int:
    """How many live threads the block leaves behind."""
    before = threading.active_count()
    try:
        fn()
    except Exception:  # noqa: BLE001 - the point is what happens on the way out
        pass
    # join(timeout=2) inside the context manager should already have reaped it;
    # a short settle keeps this from racing on a slow machine.
    for _ in range(20):
        if threading.active_count() <= before:
            break
        time.sleep(0.05)
    return threading.active_count() - before


def normal_return():
    with bot.typing(1):
        pass


def raises_inside():
    with bot.typing(1):
        raise RuntimeError("an LLMError, or anything else")


def returns_early():
    def inner():
        with bot.typing(1):
            return "the purchase-offer branch"
    inner()


for label, fn in (("a normal exit", normal_return),
                  ("an exception inside", raises_inside),
                  ("an early return", returns_early)):
    check(f"no thread survives {label}", threads_left_by(fn), 0)

check("it did send at least one chat action", bool(sent), True)
check("to sendChatAction, never sendMessage",
      all(u.endswith("/sendChatAction") for u in sent), True)

# The control. Every assertion above is "a counter came back to zero", which is
# also what a broken measurement returns. So: a deliberately leaky version whose
# thread is never stopped, which MUST be seen to leave one behind. If this
# reports zero, the three checks above are counting nothing.
_leak = threading.Event()


def leaky():
    threading.Thread(target=_leak.wait, daemon=True).start()


check("control: a thread that is never stopped IS counted",
      threads_left_by(leaky), 1)
_leak.set()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

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
HANDLED = set(re.findall(r'if action == "(\w+)"', SOURCE))

# Named here so the check states what it believes rather than deriving it from
# the thing under test. If orders ever become owner-initiated, this line is
# what should fail.
OWNER_ONLY = {"drop", "pick", "save"}

print("\nthe callback permission model")

check("some actions are actually handled", len(HANDLED) > 0, True)
check("every handled action is classified in _KIND_FOR",
      sorted(HANDLED - set(bot._KIND_FOR)), [])
check("nothing is classified that is not handled",
      sorted(set(bot._KIND_FOR) - HANDLED), [])
check("every customer action is a handled action",
      sorted(bot.CUSTOMER_ACTIONS - HANDLED), [])
check("no owner action is customer-tappable",
      sorted(bot.CUSTOMER_ACTIONS & OWNER_ONLY), [])
check("the owner-only actions are still the ones we think they are",
      sorted(HANDLED - bot.CUSTOMER_ACTIONS), sorted(OWNER_ONLY))

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

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

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


def rejects_msg(label, fn, needle):
    """Refused, AND the message names the knob to turn."""
    global passed, failed
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        if needle.lower() in str(exc).lower():
            passed += 1
            print(f"  [ok  ] {label}")
            return
        failed += 1
        print(f"  [FAIL] {label}")
        print(f"         refused, but never mentions {needle!r}: {exc}")
        return
    failed += 1
    print(f"  [FAIL] {label}")
    print("         it was allowed")



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
# THREE shapes now, not two. ESCALATION_ACTIONS joined orders as a family whose
# members carry an id in callback_data rather than a PENDING token -- and when
# it was added, this check reported `skip` as unhandled while `ans` vanished
# entirely, because both are dispatched by `action in ESCALATION_ACTIONS` and
# only one also happens to appear in an `action == "..."` literal.
# Three families now. Each time one is added this check goes red with the
# new action named, which is the behaviour worth having -- an unclassified
# action is owner-only by default, and that default should be a decision
# rather than an accident.
ID_ACTIONS = (set(bot.ORDER_ACTIONS) | set(bot.ESCALATION_ACTIONS)
              | set(bot.EXPIRY_ACTIONS))
HANDLED = (set(re.findall(r'if action == "(\w+)"', SOURCE))
           | ID_ACTIONS)

# Named here so the check states what it believes rather than deriving it from
# the thing under test. If orders ever become owner-initiated, this line is
# what should fail.
OWNER_ONLY = {"drop", "pick", "save", "conf", "rej", "rejr", "ans", "skip",
              "ext", "letexp"}

print("\nthe callback permission model")

check("some actions are actually handled", len(HANDLED) > 0, True)
# ORDER_ACTIONS deliberately carry the order id in callback_data instead of a
# PENDING token, so they are exempt from the kind check -- and that exemption is
# asserted rather than assumed, because an order action that quietly acquired a
# PENDING entry would stop surviving restarts, which is the whole reason they
# are built this way.
check("every handled action is either classified or carries an id",
      sorted(HANDLED - set(bot._KIND_FOR) - ID_ACTIONS), [])
# The exemption is asserted, not assumed: an id-carrying action that quietly
# acquired a PENDING entry would stop surviving restarts, which is the whole
# reason both families are built this way -- the owner may tap Confirm, or open
# an escalation, a day after the process last started.
check("no id-carrying action is in _KIND_FOR",
      sorted(ID_ACTIONS & set(bot._KIND_FOR)), [])
check("nothing is classified that is not handled",
      sorted(set(bot._KIND_FOR) - HANDLED), [])
# ORDER_ACTIONS is a declaration, so membership proves nothing on its own --
# a typo there would name an action no button ever sends. Each one must also
# appear in the source, in a callback_data string or a branch.
check("every id-carrying action actually appears in the code",
      sorted(a for a in ID_ACTIONS if f'"{a}' not in SOURCE), [])
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
      bot.is_owner_id(12345) if not bot.OWNERS else True, False
      if not bot.OWNERS else True)
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

# ---------------------------------------------------------------------------
print("\n  the bot resolves business -> token, not token -> business")
# The direction inverted so one systemd TEMPLATE unit serves every customer:
# `bot.py --business NAME` reads that business's bot_token off its row, instead
# of every business needing an env file carrying its own copy of the token.
#
# BOTH CHECK SUITES PASSED BEFORE THIS SECTION EXISTED -- because neither
# touched resolve_identity(). A green suite that does not cover the new code is
# the failure this project keeps cataloguing, so the coverage is written rather
# than assumed.

import contextlib as _ctx  # noqa: E402
import os as _os  # noqa: E402

import app.db as _db  # noqa: E402

_argv = sys.argv[:]
_by_name, _by_token, _conn = _db.business_by_name, _db.business_for_token, bot.connection

REAL_ID = "biz-uuid"
REAL_TOKEN = "8123456789:AA" + "x" * 30


class _FakeConn:
    def __init__(self, token):
        self._token = token

    def execute(self, *a, **k):
        return self

    def fetchone(self):
        return (self._token,)


def _fake_connection(business_id):
    return _ctx.nullcontext(_FakeConn({REAL_ID: REAL_TOKEN}.get(business_id)))


def _resolve(argv, names=None, tokens=None):
    sys.argv = ["bot.py"] + argv
    bot.business_by_name = lambda n: (names or {}).get(n)
    bot.business_for_token = lambda t: (tokens or {}).get(t)
    bot.connection = _fake_connection
    try:
        return bot.resolve_identity()
    finally:
        sys.argv = _argv[:]
        bot.business_by_name, bot.business_for_token = _by_name, _by_token
        bot.connection = _conn


check("--business reads the token off that business's row",
      _resolve(["--business", "Clinic"], names={"Clinic": REAL_ID}),
      (REAL_ID, REAL_TOKEN))

rejects_msg("an unknown business name refuses and says how to create it",
            lambda: _resolve(["--business", "Nope"], names={}),
            "no business named")

rejects_msg("a business with no bot_token refuses -- a real state, but nothing "
            "can poll for it",
            lambda: _resolve(["--business", "Empty"],
                             names={"Empty": "other-uuid"}),
            "no bot_token")

# The env fallback has to keep working: the deployed unit still uses it, and
# breaking it would take the bot down on the first restart after a pull.
_os.environ["TELEGRAM_BOT_TOKEN"] = REAL_TOKEN
check("the env fallback still resolves token -> business",
      _resolve([], tokens={REAL_TOKEN: REAL_ID}), (REAL_ID, REAL_TOKEN))
_os.environ.pop("TELEGRAM_BOT_TOKEN", None)

rejects_msg("with neither, it says which to prefer",
            lambda: _resolve([]), "prefer --business")

# ---------------------------------------------------------------------------
print("\nThe canned copy does not assume what kind of business this is")
# EVERY STRING IN bot.py USED TO SAY "klinika" -- seventeen of them, across
# Uzbek Latin, Uzbek Cyrillic and Russian, telling a course provider's customers
# to ask about a clinic and a salon's customers to contact one. Avisena Med was
# the only tenant until self-serve signup, so the test case had become the copy.
#
# A STATIC SCAN, not a rendered one, because the failure is a literal in the
# source. It would come back the next time someone writes a message with the
# demo business in mind, and it would be invisible to anyone testing against
# that same demo business -- which is everyone, most of the time.
import re as _re

_CLINIC = _re.compile("klinika|" + "\u043a\u043b\u0438\u043d\u0438\u043a", _re.I)
_source = pathlib.Path("bot.py").read_text(encoding="utf-8")
_offenders = [
    (n, line.strip()[:70])
    for n, line in enumerate(_source.splitlines(), 1)
    if _CLINIC.search(line) and not line.lstrip().startswith("#")
]
check("no canned string names a clinic", _offenders, [])

# And the greeting must actually SUBSTITUTE, not send a literal "{name}" to a
# customer -- the failure mode of a template nobody rendered.
import bot as _bot

for _label, _template in (list(_bot.GREETING_REPLY.items())
                          + [("default", _bot.GREETING_DEFAULT)]):
    _rendered = _template.format(name="Rangli Salon")
    check(f"greeting substitutes the business name ({_label})",
          "Rangli Salon" in _rendered and "{name}" not in _rendered, True)



# --- a placeholder nothing ever fills ---------------------------------------
#
# THIS SHIPPED, and it was the first message every new customer of every
# business ever saw: /start replied "Salom! {name} haqida savolingizni yozing."
# -- the placeholder itself, braces and all. The template carried {name} and
# .format() was never called on it.
#
# Nothing could have caught it. tsc does not read Python, the checks here read
# tables rather than message text, and the 90-question harness never sends
# /start. It was found by a customer.
#
# The rule is deliberately narrow: a string literal handed DIRECTLY to a
# sending call, carrying a {placeholder}, with nothing in that same argument
# formatting anything. Templates kept in module constants -- GREETING_REPLY,
# PAYMENT_GAP_OWNER -- are formatted at their call sites and are not the bug;
# a broader rule flagged four of those and would have been turned off.
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
_SENDERS = {"send", "send_kb", "edit_here", "answer_callback"}


def _unformatted(source: str) -> list:
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _SENDERS):
            continue
        for arg in node.args:
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "format" for n in ast.walk(arg)):
                continue
            for inner in ast.walk(arg):
                if (isinstance(inner, ast.Constant)
                        and isinstance(inner.value, str)
                        and _PLACEHOLDER.search(inner.value)):
                    found.append((inner.lineno, inner.value[:60]))
    return found


leaked = _unformatted(SOURCE)
check("no sent message carries an unfilled {placeholder}",
      [f"line {n}: {t}" for n, t in leaked], [])

# THE CONTROL. An empty list is equally true of a working rule and a rule that
# matches nothing, so plant the bug back and require it to be seen.
# THE CONTROL TESTS THE RULE, NOT A LINE OF bot.py.
#
# It used to plant by deleting a specific .format() call from the real source.
# That worked until the greeting it targeted was rewritten to call
# greeting_for(), at which point the plant matched nothing -- and the assert
# below caught it, which is the only reason this is a working control rather
# than a green tick. Coupling a control to a line that keeps moving means it
# silently stops controlling anything the day someone edits that line.
#
# So it feeds the rule a synthetic module instead. bot.py's own cleanliness is
# asserted above, against the real source; this proves the thing doing the
# asserting can see the bug at all.
planted = 'def f():\n    send(chat_id, "Salom! {name} haqida savolingizni yozing.")\n'
assert _unformatted(planted), "the control planted nothing the rule can see"
check("the rule sees an unformatted placeholder in a send()",
      len(_unformatted(planted)) > 0, True)

# AND THE OTHER HALF. A rule that flags everything would pass the line above and
# be useless -- the first version of it flagged four legitimate templates and
# would have been switched off within a week.
formatted = ('def f():\n    send(chat_id, "Salom! {name} haqida."'
             '.format(name=x))\n')
check("and does NOT flag one that is formatted",
      _unformatted(formatted), [])

# --- three outcomes, not one bool -------------------------------------------
#
# send() used to return False for a blocked user, a rate limit and a dropped
# connection alike, and those need opposite responses: never retry, wait
# exactly N seconds, try again shortly. The owner was told a customer "MAY
# have blocked the bot" because the code could not tell.
#
# Each case is a real Telegram error body. The last one matters most: a 400
# caused by OUR bug must be neither blocked nor retryable, or one malformed
# message would permanently blacklist a customer.
for _payload, _label, _ok, _blocked, _wait in (
    ({"ok": True}, "accepted", True, False, None),
    ({"ok": False, "error_code": 403,
      "description": "Forbidden: bot was blocked by the user"},
     "blocked by the user", False, True, None),
    ({"ok": False, "error_code": 403,
      "description": "Forbidden: user is deactivated"},
     "deleted account", False, True, None),
    ({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
     "chat gone", False, True, None),
    ({"ok": False, "error_code": 429,
      "description": "Too Many Requests: retry after 17",
      "parameters": {"retry_after": 17}},
     "rate limited", False, False, 17),
    ({"ok": False, "error_code": 429, "description": "Too Many Requests"},
     "rate limited with no number", False, False, 5),
    ({"ok": False, "error_code": 400,
      "description": "Bad Request: message text is empty"},
     "our own malformed message", False, False, None),
):
    _r = bot._classify(_payload)
    check(f"{_label}: accepted", bool(_r), _ok)
    check(f"{_label}: permanent", _r.blocked, _blocked)
    check(f"{_label}: wait", _r.retry_after, _wait)

# BACKWARD COMPATIBLE ON PURPOSE. Forty-two call sites use send() and most
# ignore the result; the few that test it write `if not send(...)`. A result
# object that was not falsy on failure would have broken every one of them
# silently -- which is precisely what `is not False` did inside notify_owners
# until it was corrected here.
check("a failure is falsy", bool(bot.SendResult(False)), False)
check("and a success is truthy", bool(bot.SendResult(True)), True)
check("`is not False` would NOT have worked",
      bot.SendResult(False) is not False, True)


print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

"""Telegram bot. Long polling, so it works behind NAT with no tunnel and no HTTPS.

    uv run python bot.py

Reads messages, answers through app.answer, replies. The owner -- and only the
owner, checked on the message AND again on the button press -- can add a fact
with `/fact <line>`: the bot parses it, shows what it would write, and writes
only when the inline Save button is tapped.

Four operational rules, in order of how badly they bite in front of an audience:

  * an LLM failure NEVER reaches the customer as silence;
  * one chat cannot burn the quota by sending five messages in a row;
  * every message is logged with its route, scores and answer, because the
    first real questions anyone asks this thing are worth more than the
    hand-written test set;
  * nothing is written to the knowledge base without the owner seeing the
    parsed subject, attribute and value first.
"""

import collections
import contextlib
import datetime
import json
import os
import pathlib
import secrets
import signal
import sys
import threading
import time

import httpx
import psycopg
from dotenv import load_dotenv

from app import buy, orders, payment
from app.answer import answer, detect_language
from app.followup import rewrite
from app.db import (assert_app_role, business_by_name,
                    business_for_token, connection, pool)
from app import channel
from app.approval import NotApproved
from app.llm import LLMError, check_configured, check_reachable
from app.normalize import normalize
from app.typed import candidates, conflicts
from app.triage import triage
from app.typed import parse as parse_fact, store as store_fact

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

# Resolved in main(), not at import. Either from --business (the token is read
# off the business row) or, failing that, from TELEGRAM_BOT_TOKEN.
#
# THE PREFERRED DIRECTION IS BUSINESS -> TOKEN, not token -> business. The token
# already lives on the business row, so reading it from there means one
# credential in one place instead of two, and one systemd TEMPLATE unit instead
# of a unit plus an env file per customer:
#
#     sudo systemctl enable --now talkwisp-bot@"Clinic Name"
#
# The env fallback stays deliberately: the currently deployed unit uses it, and
# removing it would break the running bot on the first restart after a pull.
# That is not a thing to discover in production.
TOKEN: str | None = None
API: str | None = None

# Both resolved in main() from the BUSINESS ROW this token belongs to, not
# from the environment. The token is the tenant: an update arrives on one
# bot, that bot belongs to one business, and everything this process reads or
# writes is that business's. An env var could only ever describe one of them,
# which is exactly the assumption being removed.
#
# The owner id moves with it. "Who may tap the owner buttons" is a different
# answer per business, so it cannot stay a single global.
BUSINESS_ID: str | None = None
OWNER_ID: str | None = None

POLL_TIMEOUT = 30  # seconds Telegram holds the connection open with no updates

# A token bucket, not a fixed gap. A flat 4-second minimum was tried first and
# it punished the normal opening of a conversation: a live tester sent a
# greeting, then their actual question one second later, and the question was
# the one that got refused. Later "rahmat" was blocked the same way. A burst
# allowance absorbs that while still stopping a sustained flood.
BURST = 3            # messages allowed back to back
REFILL_SECONDS = 5.0 # one token returns every this many seconds

# Greetings and thanks are not questions. Answering them through retrieval cost
# an embedding plus a generation each, against a daily quota that has already
# run out twice, and the model ignored the retrieved context anyway.
_GREETINGS = {"assalom", "assalomu", "alaykum", "salom", "hello", "hi",
              "hey", "privet", "zdravstvuyte", "xayrli", "hayrli", "kun"}
_THANKS = {"rahmat", "raxmat", "spasibo", "thanks", "thank", "tashakkur"}

GREETING_REPLY = {
    "Russian": "Здравствуйте! Задайте свой вопрос о клинике.",
    "Uzbek, in CYRILLIC script": "Ассалому алайкум! Клиника ҳақидаги "
                                 "саволингизни ёзинг.",
}
GREETING_DEFAULT = "Assalomu alaykum! Klinika haqidagi savolingizni yozing."

THANKS_REPLY = {
    "Russian": "Пожалуйста! Если будут вопросы — пишите.",
    "Uzbek, in CYRILLIC script": "Арзимайди! Саволингиз бўлса, ёзаверинг.",
}
THANKS_DEFAULT = "Arzimaydi! Savolingiz boʻlsa, yozavering."


def social(text: str) -> str | None:
    """A canned reply for a message that is only a greeting or only thanks.

    Short-circuits before retrieval. Only fires when the WHOLE message is
    social -- "Salom, kardiolog narxi qancha?" is a question and must go
    through the normal path.
    """
    words = normalize(text).split()
    if not words or len(words) > 3:
        return None
    if all(w in _GREETINGS for w in words):
        return GREETING_REPLY.get(detect_language(text), GREETING_DEFAULT)
    if all(w in _THANKS for w in words):
        return THANKS_REPLY.get(detect_language(text), THANKS_DEFAULT)
    return None

MESSAGE_LOG = pathlib.Path(__file__).parent / "messages.jsonl"

# Said without the model, because these fire exactly when the model is the
# thing that failed. Keyed by what detect_language() returns.
BUSY = {
    "Russian": "Секунду, слишком много сообщений. Напишите ещё раз через момент.",
    "Uzbek, in CYRILLIC script": "Бир сония, жуда кўп хабар. Бироздан сўнг ёзинг.",
}
BUSY_DEFAULT = "Bir soniya, juda koʻp xabar keldi. Bir ozdan soʻng yozing."

BROKEN = {
    "Russian": "Извините, техническая неполадка. Попробуйте, пожалуйста, "
               "через минуту.",
    "Uzbek, in CYRILLIC script": "Узр, техник носозлик. Бир дақиқадан сўнг "
                                 "уриниб кўринг.",
}
BROKEN_DEFAULT = ("Uzr, texnik nosozlik. Bir daqiqadan soʻng urinib koʻring.")

DONT_KNOW = {
    "Russian": "К сожалению, у меня нет этой информации. Пожалуйста, свяжитесь "
               "с клиникой напрямую.",
    "Uzbek, in CYRILLIC script": "Афсуски, менда бу маълумот йўқ. Илтимос, "
                                 "клиника билан бевосита боғланинг.",
}
DONT_KNOW_DEFAULT = ("Afsuski, menda bu maʼlumot yoʻq. Iltimos, klinika bilan "
                     "bevosita bogʻlaning.")


def _say(table: dict, default: str, question: str) -> str:
    return table.get(detect_language(question), default)


def send(chat_id: int, text: str) -> bool:
    """True if Telegram accepted it. RETURNS a result because some callers must
    know: a customer who blocked the bot after paying is stranded, and the only
    person who can rescue them is the owner. See notify_owner_send_failure."""
    try:
        r = httpx.post(f"{API}/sendMessage",
                       json={"chat_id": chat_id, "text": text}, timeout=30)
        return bool(r.json().get("ok"))
    except (httpx.HTTPError, ValueError) as exc:
        print(f"send to {chat_id} failed: {exc!r}", flush=True)
        return False


@contextlib.contextmanager
def typing(chat_id: int):
    """Show "typing…" for as long as the block runs.

    The answer path is embedding then generation -- roughly one second plus
    anywhere from two to fifteen, measured -- and in a chat that reads as broken
    rather than busy. This does not make anything faster; it makes the wait
    legible, which is the part that was actually wrong.

    A CONTEXT MANAGER, AND THAT IS THE WHOLE DESIGN. The requirement is that it
    stops on every exit path: the triage short-circuit, the purchase offer's
    early return, an LLMError after retries, the database-unreachable branch,
    the catch-all, and the ordinary reply. A list of stop() calls is a rule, and
    a rule about six branches has no failure signal -- the seventh branch
    someone adds next year inherits nothing while the bot sits showing "typing"
    at a customer forever. You cannot return out of a `with` without `finally`
    running, so exits that do not exist yet are covered too.

    Telegram expires the action after about five seconds, so it has to repeat.
    Event.wait(4) rather than sleep(4): the thread stops the moment the reply is
    sent instead of lingering up to four seconds after it, which would leave
    "typing" on screen next to a message that has already arrived.

    Every send is swallowed. This is cosmetic, and a failed chat action must
    never be the reason a customer gets no answer.
    """
    stop = threading.Event()

    def keep_typing():
        while not stop.is_set():
            try:
                httpx.post(f"{API}/sendChatAction",
                           json={"chat_id": chat_id, "action": "typing"},
                           timeout=10)
            except Exception:  # noqa: BLE001 - cosmetic, never fatal
                pass
            stop.wait(4)

    thread = threading.Thread(target=keep_typing, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def log(entry: dict) -> None:
    # business_id on every line. Without it these files are one stream with
    # several businesses' customers mixed together, and no way to split them
    # afterwards -- the attribution has to be written at the time or not at
    # all. Same reason gaps.jsonl and feedback.jsonl carry it.
    entry["business_id"] = BUSINESS_ID
    entry["at"] = datetime.datetime.now(datetime.UTC).isoformat()
    with MESSAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")




# ---------------------------------------------------------------- chat memory

# The last few turns per chat, used ONLY to rewrite a follow-up into a
# standalone question -- never as material for an answer. app/followup.py
# explains why that distinction is the whole design.
#
# In memory, so it dies with the process. Acceptable: losing history costs one
# unresolved follow-up and the customer can rephrase. A table later, if it
# earns its place.
HISTORY_TURNS = 5

# A follow-up an hour later is not a follow-up. Without this, "nech pul"
# attaches to yesterday's conversation and searches for the wrong thing.
HISTORY_IDLE_SECONDS = 20 * 60

HISTORY: dict[int, dict] = {}


def history_for(chat_id: int) -> list:
    entry = HISTORY.get(chat_id)
    if entry is None:
        return []
    if time.monotonic() - entry["at"] > HISTORY_IDLE_SECONDS:
        HISTORY.pop(chat_id, None)
        return []
    return list(entry["turns"])


def remember(chat_id: int, question: str, answer_text: str | None) -> None:
    entry = HISTORY.setdefault(
        chat_id, {"turns": collections.deque(maxlen=HISTORY_TURNS), "at": 0.0})
    entry["turns"].append((question, answer_text))
    entry["at"] = time.monotonic()


# ---------------------------------------------------------------- owner writes

# Telegram caps callback_data at 64 bytes, so the parsed fact cannot travel in
# the button. It lives here, keyed by a short token, and dies with the process:
# a button tapped after a restart must fail politely rather than write something
# the owner never actually saw.
PENDING: dict[str, dict] = {}

# Which callback actions a CUSTOMER may tap. Everything absent from this set is
# owner-only. See the reasoning in handle_callback: allowlist, so forgetting to
# classify a new action fails closed.
CUSTOMER_ACTIONS = frozenset({"order", "who", "shot"})

# Owner-only, and they carry the order id in callback_data instead of a PENDING
# token so they keep working across a restart -- the owner may confirm a day
# later, and a Confirm button that died with the process would strand money.
ORDER_ACTIONS = frozenset({"conf", "rej", "rejr"})

# Which PENDING shape each action expects. A token from the wrong flow is
# refused rather than misread.
_KIND_FOR = {"drop": "fact", "pick": "fact", "save": "fact",
             "order": "offer", "who": "offer", "shot": "shot"}


def is_owner_id(user_id) -> bool:
    return OWNER_ID is not None and str(user_id) == str(OWNER_ID)

NOT_OWNER = ("Bu buyruq faqat klinika egasi uchun.\n"
             "Эта команда доступна только владельцу клиники.")
EXPIRED = "Bu taklif eskirgan. Iltimos, /fact buyrugʻini qaytadan yuboring."


def keyboard(rows):
    return {"inline_keyboard": [
        [{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def send_kb(chat_id, text, markup):
    httpx.post(f"{API}/sendMessage",
               json={"chat_id": chat_id, "text": text, "reply_markup": markup},
               timeout=30)


def answer_callback(callback_id, text=""):
    httpx.post(f"{API}/answerCallbackQuery",
               json={"callback_query_id": callback_id, "text": text}, timeout=30)


def edit(chat_id, message_id, text, as_caption=False):
    """Replace the buttons with the outcome, so one cannot be tapped twice.

    `as_caption` is not a nicety. A PHOTO message has a caption and no text, and
    editMessageText answers 400 "there is no text in the message to edit". The
    owner's payment review IS a photo -- the screenshot with the two buttons on
    it -- so every Confirm and every Reject would have failed at the instant the
    owner tapped, with the order left in awaiting_owner and the customer told
    nothing. The caller reads which kind it is off the callback's own message
    rather than guessing, because the fallback path in owner_review() sends a
    plain text message when sendPhoto fails, and then it really is text.
    """
    endpoint = "editMessageCaption" if as_caption else "editMessageText"
    field = "caption" if as_caption else "text"
    httpx.post(f"{API}/{endpoint}",
               json={"chat_id": chat_id, "message_id": message_id, field: text},
               timeout=30)


def preview(pending):
    """What the owner is being asked to approve.

    The ATTRIBUTE is shown on its own line, because it is the part the parser
    guessed. The owner already knows the value they typed; what they cannot see
    without being shown is that "9 dan 2 gacha" was filed under `qabul vaqti`
    rather than under some new name that would never meet the existing facts.
    """
    p = pending["parsed"]
    lines = [
        "Yangi maʼlumot:",
        f"  Nima haqida : {p['subject']}",
        f"  Xususiyat   : {p['attribute']}",
        f"  Qiymati     : {p['value']}",
    ]
    if pending.get("conflicts"):
        lines.append("")
        lines.append("Diqqat, bu haqda allaqachon saqlangan:")
        lines += [f"  - {c['attribute']}: {c['value']}" for c in pending["conflicts"]]
        lines.append("Saqlasangiz, ikkalasi ham qoladi.")
    return "\n".join(lines)


def save_buttons(token):
    return keyboard([[("Saqlash", f"save:{token}"),
                      ("Bekor qilish", f"drop:{token}")]])


def handle_fact_command(conn, chat_id, is_owner, line):
    if not is_owner:
        send(chat_id, NOT_OWNER)
        return
    if not line:
        send(chat_id, "Masalan: /fact Kardiolog qabuli 250 000 soʻm")
        return

    # parse_fact() is a generation, so this waits as long as an ordinary answer
    # does. Included because the silence is the same silence -- it is the owner
    # staring at nothing rather than a customer, which makes it easier to
    # dismiss and no less wrong. The two returns above are outside on purpose:
    # both are instant and neither touches a model.
    with typing(chat_id):
        result = parse_fact(conn, line)
    if result["error"]:
        send(chat_id, f"Tushunmadim: {result['error']}")
        return

    token = secrets.token_urlsafe(8)
    result["kind"] = "fact"
    PENDING[token] = result

    # Ambiguity first: never offer a Save button for a subject that could mean
    # two different people. Ask which, exactly as the answering path does.
    if result["candidates"]:
        options = [[(name, f"pick:{token}:{i}")]
                   for i, name in enumerate(result["candidates"])]
        options.append([(result["parsed"]["subject"] + " (yangi)",
                         f"pick:{token}:new")])
        send_kb(chat_id,
                f"{result['parsed']['subject']} bir nechta narsani bildirishi "
                f"mumkin. Qaysi biri?", keyboard(options))
        return

    send_kb(chat_id, preview(result), save_buttons(token))


def handle_callback(conn, cq):
    data = cq.get("data") or ""
    message = cq.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    user_id = cq.get("from", {}).get("id")

    action, _, rest = data.partition(":")
    parts = rest.split(":")
    token = parts[0]
    # Read it off the message we were called about, never assume: the owner's
    # review is a photo, the fact-approval messages are text, and the fallback
    # when sendPhoto fails is text too.
    as_caption = bool(message.get("caption") or message.get("photo"))

    def edit_here(text):
        """Every edit in this function is on the message the button was
        attached to, so the caption/text choice is decided once rather than at
        seven call sites where six of them would be right."""
        edit(chat_id, message_id, text, as_caption)

    # Enforced here too, not only on the message: a button press is a separate
    # request, and anyone who can see the chat can tap it.
    #
    # AN ALLOWLIST, NOT A DENYLIST, and that is the whole point. Until orders
    # existed every callback was owner-only and one blanket check was enough.
    # Now customers tap buttons too, so the check has to be per action -- and
    # written this way round, an action added later and not classified is
    # owner-only by default. A denylist would make it public by default, and
    # the failure would be silent: the button would simply work for everyone.
    if action not in CUSTOMER_ACTIONS and not is_owner_id(user_id):
        answer_callback(cq["id"], "Faqat klinika egasi uchun.")
        return

    # ---- orders: the id travels in callback_data, so these survive a restart.
    # Handled BEFORE the PENDING lookup, because they deliberately do not use
    # it. The owner may tap Confirm a day after the process last started.
    if action in ORDER_ACTIONS:
        order_id = rest.split(":")[0]
        order = orders.get(conn, order_id)
        if order is None:
            answer_callback(cq["id"])
            edit_here(EXPIRED)
            return

        if action == "rej":
            # A reason is REQUIRED -- the database refuses a rejection without
            # one -- so Reject opens a second keyboard rather than rejecting.
            # "Amount does not match" and "I cannot see it" mean different next
            # steps for the customer, which is why they cannot be one button.
            answer_callback(cq["id"])
            send_kb(chat_id, "Sabab?", keyboard(
                [[(REJECT_LABELS[r], f"rejr:{order_id}:{r}")]
                 for r in orders.REJECT_REASONS]))
            return

        try:
            if action == "conf":
                order = orders.confirm(conn, order_id)
            else:
                order = orders.reject(conn, order_id, rest.split(":")[1])
        except orders.OrderError as exc:
            # A double tap lands here: the transition is refused, not applied
            # twice. Say so rather than pretending it worked.
            answer_callback(cq["id"], "Allaqachon hal qilingan")
            edit_here(
                 f"Bu buyurtma allaqachon hal qilingan ({exc.detail.get('state', '?')}).")
            return

        if action == "conf":
            answer_callback(cq["id"], "Tasdiqlandi")
            edit_here(
                 f"Tasdiqlandi: {order['item']} — {money(order['amount'])}")
            tell_customer(order,
                          for_order(DELIVERED, DELIVERED_DEFAULT, order),
                          "confirmed")
        else:
            reason = order["reject_reason"]
            answer_callback(cq["id"], "Rad etildi")
            edit_here(
                 f"Rad etildi ({REJECT_LABELS[reason]}): {order['item']} — "
                 f"{money(order['amount'])}")
            tell_customer(order,
                          for_order(REJECTED[reason],
                                    REJECTED_DEFAULT[reason], order),
                          f"rejected:{reason}")
        log({"chat_id": order["chat_id"], "outcome": f"order_{order['state']}",
             "order_id": str(order["id"]), "amount": order["amount"],
             "reason": order["reject_reason"]})
        return

    pending = PENDING.get(token)
    if pending is None:
        answer_callback(cq["id"])
        edit_here(EXPIRED)
        return

    # PENDING holds two shapes now -- a parsed fact awaiting the owner's
    # approval, and a purchase offer awaiting the customer's choice. A token is
    # only ever handed out inside one of those messages, but callback_data is
    # client-supplied and a token from the wrong flow would be read as the
    # wrong dict. Cheap to check, and the alternative is a confusing crash.
    if pending.get("kind") != _KIND_FOR.get(action):
        answer_callback(cq["id"])
        edit_here(EXPIRED)
        return

    if action == "drop":
        PENDING.pop(token, None)
        answer_callback(cq["id"], "Bekor qilindi")
        edit_here("Bekor qilindi. Hech narsa saqlanmadi.")
        return

    if action == "pick":
        choice = parts[1]
        if choice != "new":
            pending["parsed"]["subject"] = pending["candidates"][int(choice)]
            pending["conflicts"] = conflicts(conn, pending["parsed"])
        pending["candidates"] = []
        answer_callback(cq["id"])
        edit_here(preview(pending))
        send_kb(chat_id, "Saqlaymizmi?", save_buttons(token))
        return

    if action == "who":
        # Axis one resolved: the customer said which person. Now the prices for
        # that one -- the second axis, and the normal case, since every doctor
        # has both a first-visit and a repeat-visit fee.
        subject = pending["subjects"][int(parts[1])]
        options = [o for o in orders.price_options(conn, normalize(subject))
                   if o["amount"] is not None]
        answer_callback(cq["id"])
        if not options:
            edit_here(
                 _say(NOT_ORDERABLE, NOT_ORDERABLE_DEFAULT, ""))
            return
        pending.update({"choose": "price", "options": options,
                        "subject": subject})
        edit_here(subject)
        send_kb(chat_id, _say(CHOOSE_PRICE, CHOOSE_PRICE_DEFAULT, ""),
                offer_keyboard(token, pending))
        return

    if action == "order":
        # THE ONLY PLACE AN ORDER IS CREATED, and it takes a human tap to get
        # here. create() reads the price itself from the confirmed fact; the
        # button supplies a row selector, never an amount.
        option = pending["options"][int(parts[1])]
        PENDING.pop(token, None)
        try:
            order = orders.create(conn, chat_id, option["subject_key"],
                                  option["attribute_key"],
                                  language=pending.get("language"))
        except orders.OrderError as exc:
            answer_callback(cq["id"])
            edit_here(
                 _say(NOT_ORDERABLE, NOT_ORDERABLE_DEFAULT, "")
                 if exc.reason == "not_exact"
                 else _say(ORDER_BROKEN, ORDER_BROKEN_DEFAULT, ""))
            log({"chat_id": chat_id, "outcome": "order_refused",
                 "reason": exc.reason, "detail": str(exc.detail)[:300]})
            return
        text = payment.order_message(conn, order["language"], order)
        answer_callback(cq["id"])
        edit_here(
             f"{order['item']} — {option['attribute']}")
        if text is None:
            # Payment details are not filled in. Say so rather than send half
            # an instruction; the order still exists and the owner can see it.
            send(chat_id, _say(ORDER_BROKEN, ORDER_BROKEN_DEFAULT, ""))
            log({"chat_id": chat_id, "outcome": "order_no_payment_details",
                 "order_id": str(order["id"])})
            return
        send(chat_id, text)
        log({"chat_id": chat_id, "outcome": "order_created",
             "order_id": str(order["id"]), "item": order["item"],
             "amount": order["amount"], "attribute": order["attribute"]})
        return

    if action == "shot":
        order_id = pending["orders"][int(parts[1])]
        PENDING.pop(token, None)
        answer_callback(cq["id"])
        attach_and_notify(conn, order_id, pending["file_id"], chat_id)
        return

    if action == "save":
        PENDING.pop(token, None)
        try:
            store_fact(conn, pending["parsed"])
        except Exception as exc:  # noqa: BLE001
            answer_callback(cq["id"], "Xatolik")
            edit_here(
                 "Saqlab boʻlmadi. Bir ozdan soʻng qaytadan urinib koʻring.")
            log({"chat_id": chat_id, "outcome": "fact_write_error",
                 "error": repr(exc)[:300]})
            return
        p = pending["parsed"]
        answer_callback(cq["id"], "Saqlandi")
        edit_here(
             f"Saqlandi: {p['subject']} - {p['attribute']}: {p['value']}\n"
             "Endi mijozlar shu savolni bersa, bot javob beradi.")
        log({"chat_id": chat_id, "is_owner": True, "outcome": "fact_written",
             "subject": p["subject"], "attribute": p["attribute"],
             "value": p["value"]})


# ------------------------------------------------------------------- purchases

# CODE CONSTANTS, not facts. Nine editable strings on a settings screen for a
# feature with no users yet is speculative configuration, and each one under
# the payment subject would grow the "must never be retrieved" exception for
# hypothetical benefit. Promote any of these to a fact the day an owner asks to
# change it -- promotion is easy, un-promotion is not.
CHOOSE_PRICE = {
    "Russian": "Что именно вы хотите оплатить?",
    "Uzbek, in CYRILLIC script": "Аниқ нимани тўламоқчисиз?",
}
CHOOSE_PRICE_DEFAULT = "Aniq nimani toʻlamoqchisiz?"

CHOOSE_WHO = {
    "Russian": "К кому именно?",
    "Uzbek, in CYRILLIC script": "Аниқ кимга?",
}
CHOOSE_WHO_DEFAULT = "Aniq kimga?"

# A range is refused, never narrowed -- taking the low end would be inventing a
# price with money attached. See app/orders.py.
NOT_ORDERABLE = {
    "Russian": "Стоимость этой услуги указана диапазоном, поэтому оплатить её "
               "через бот пока нельзя. Пожалуйста, свяжитесь с клиникой.",
    "Uzbek, in CYRILLIC script": "Бу хизматнинг нархи оралиқ кўрсатилган, "
                                 "шунинг учун бот орқали тўлаб бўлмайди. "
                                 "Илтимос, клиника билан боғланинг.",
}
NOT_ORDERABLE_DEFAULT = ("Bu xizmatning narxi oraliq koʻrsatilgan, shuning "
                         "uchun bot orqali toʻlab boʻlmaydi. Iltimos, klinika "
                         "bilan bogʻlaning.")

ORDER_BROKEN = {
    "Russian": "Не удалось оформить оплату. Пожалуйста, свяжитесь с клиникой.",
    "Uzbek, in CYRILLIC script": "Тўловни расмийлаштириб бўлмади. Илтимос, "
                                 "клиника билан боғланинг.",
}
ORDER_BROKEN_DEFAULT = ("Toʻlovni rasmiylashtirib boʻlmadi. Iltimos, klinika "
                        "bilan bogʻlaning.")


def offer_keyboard(token: str, offer: dict) -> dict:
    """Buttons for a purchase offer. The label is assembled from stored columns,
    never phrased by a model: mislabelling which price belongs to which visit is
    a money error, not a wording one."""
    if offer.get("choose") == "subject":
        return keyboard([[(name, f"who:{token}:{i}")]
                         for i, name in enumerate(offer["subjects"])])
    many = len({o["subject_key"] for o in offer["options"]}) > 1
    rows = []
    for i, o in enumerate(offer["options"]):
        label = f"{o['subject']} — {o['attribute']} — {o['value']}" if many             else f"{o['attribute']} — {o['value']}"
        rows.append([(label, f"order:{token}:{i}")])
    return keyboard(rows)


def send_offer(chat_id: int, text: str, offer: dict) -> None:
    """Put the offer in front of the customer. NOTHING is written here -- the
    order is created when a human taps, which is what keeps a classifier false
    positive costing one unwanted button and zero rows."""
    token = secrets.token_urlsafe(6)
    offer["kind"] = "offer"
    offer["language"] = detect_language(text)
    PENDING[token] = offer
    prompt = (_say(CHOOSE_WHO, CHOOSE_WHO_DEFAULT, text)
              if offer.get("choose") == "subject"
              else _say(CHOOSE_PRICE, CHOOSE_PRICE_DEFAULT, text))
    send_kb(chat_id, prompt, offer_keyboard(token, offer))



# ------------------------------------------------ the confirmation loop (A3b-2)

SHOT_THANKS = {
    "Russian": "Спасибо, чек получен. Мы проверим и сообщим вам.",
    "Uzbek, in CYRILLIC script": "Раҳмат, чек қабул қилинди. Текширамиз ва "
                                 "хабар берамиз.",
}
SHOT_THANKS_DEFAULT = "Rahmat, chek qabul qilindi. Tekshiramiz va xabar beramiz."

NO_OPEN_ORDER_DEFAULT = ("Sizda toʻlov kutayotgan buyurtma yoʻq. Nimani "
                         "toʻlamoqchi ekaningizni yozing.")
WHICH_ORDER_DEFAULT = "Bu chek qaysi buyurtma uchun?"

# The delivery message. The most business-specific of these, and the first that
# should become an owner-editable fact if any of them does.
DELIVERED = {
    "Russian": "Оплата подтверждена. Спасибо! Покажите это сообщение в регистратуре.",
    "Uzbek, in CYRILLIC script": "Тўлов тасдиқланди. Раҳмат! Ушбу хабарни "
                                 "қабулхонага кўрсатинг.",
}
DELIVERED_DEFAULT = ("Toʻlov tasdiqlandi. Rahmat! Ushbu xabarni qabulxonaga "
                     "koʻrsating.")

# One message per reason, because the reasons exist to mean different NEXT
# STEPS: pay again correctly, or go and ask your bank. Collapsing them into one
# apology throws away the only useful thing the owner told us.
REJECTED = {
    "amount_mismatch": {
        "Russian": "Сумма не совпадает с заказом. Пожалуйста, переведите "
                   "точную сумму, указанную выше.",
        "Uzbek, in CYRILLIC script": "Сумма буюртмага мос келмади. Илтимос, "
                                     "юқорида кўрсатилган аниқ суммани юборинг.",
    },
    "not_received": {
        "Russian": "Мы не видим этот платёж. Пожалуйста, проверьте в своём "
                   "банке и свяжитесь с клиникой.",
        "Uzbek, in CYRILLIC script": "Биз бу тўловни кўрмаяпмиз. Илтимос, "
                                     "банкингиздан текширинг ва клиника билан "
                                     "боғланинг.",
    },
}
REJECTED_DEFAULT = {
    "amount_mismatch": ("Summa buyurtmaga mos kelmadi. Iltimos, yuqorida "
                        "koʻrsatilgan aniq summani yuboring."),
    "not_received": ("Biz bu toʻlovni koʻrmayapmiz. Iltimos, bankingizdan "
                     "tekshiring va klinika bilan bogʻlaning."),
}
REJECT_LABELS = {"amount_mismatch": "Summa mos emas",
                 "not_received": "Toʻlov koʻrinmadi"}


def money(amount: int) -> str:
    """One definition of how money looks, in app/payment.py."""
    return payment.som(amount)


def for_order(table: dict, default: str, order: dict) -> str:
    """Pick the customer's language off the ORDER, not off the message that
    triggered the send -- that message is the OWNER's, and detecting language on
    it would answer in the wrong person's language. See migration 0006."""
    return table.get(order.get("language"), default)


def send_photo(chat_id: int, file_id: str, caption: str, markup=None) -> bool:
    payload = {"chat_id": chat_id, "photo": file_id, "caption": caption}
    if markup:
        payload["reply_markup"] = markup
    try:
        r = httpx.post(f"{API}/sendPhoto", json=payload, timeout=30)
        return bool(r.json().get("ok"))
    except (httpx.HTTPError, ValueError) as exc:
        print(f"sendPhoto to {chat_id} failed: {exc!r}", flush=True)
        return False


def tell_customer(order: dict, text: str, what: str) -> None:
    """Say something to the customer, and TELL THE OWNER IF IT DID NOT ARRIVE.

    "The customer always hears something" is best-effort by nature -- they can
    block the bot or delete the chat. What must not happen is the failure being
    recorded only where nobody looks. Someone who paid and then blocked the bot
    is stranded, and "visible in the dashboard" means visible to a person who is
    not looking. So this lands on the payment channel, the one the owner is
    already watching because money is on it.
    """
    if send(order["chat_id"], text):
        return
    log({"chat_id": order["chat_id"], "outcome": "customer_unreachable",
         "order_id": str(order["id"]), "what": what})
    if OWNER_ID:
        send(int(OWNER_ID),
             "Mijozga xabar yetkazib bo'lmadi (" + what + ").\n"
             f"Buyurtma: {order['item']} — {money(order['amount'])}\n"
             f"Mijoz chat: {order['chat_id']}\n"
             "Botni bloklagan bo'lishi mumkin. Iltimos, o'zingiz bog'laning.")


def owner_review(order: dict) -> None:
    """Show the owner the screenshot and the two buttons.

    The order id travels in callback_data rather than in PENDING, and that is
    deliberate: PENDING dies with the process and the owner may confirm a day
    later. A Confirm button that stopped working after a restart would strand
    the money it exists to release. 41 bytes of the 64 Telegram allows.
    """
    if not OWNER_ID:
        return
    caption = ("Yangi to'lov tekshiruvi\n"
               f"{order['item']} — {order['attribute']}\n"
               f"Summa: {money(order['amount'])}\n"
               f"Mijoz chat: {order['chat_id']}")
    markup = keyboard([[("Tasdiqlash", f"conf:{order['id']}"),
                        ("Rad etish", f"rej:{order['id']}")]])
    if not send_photo(int(OWNER_ID), order["screenshot_file_id"],
                      caption, markup):
        # No image in front of them is still better than no notification.
        send_kb(int(OWNER_ID), caption, markup)


def file_id_of(message: dict) -> str | None:
    """The screenshot, whichever way it was sent.

    `photo` is a list of sizes ordered small to large, so the last is the
    original. People also send screenshots as a FILE, which arrives as
    `document` and would otherwise be ignored in silence -- and a payment the
    customer believes they have proved is the worst thing to ignore silently.
    """
    photo = message.get("photo")
    if photo:
        return photo[-1]["file_id"]
    document = message.get("document") or {}
    if str(document.get("mime_type") or "").startswith("image/"):
        return document.get("file_id")
    return None


def attach_and_notify(conn, order_id, file_id: str, chat_id: int) -> None:
    try:
        order = orders.attach_screenshot(conn, order_id, file_id)
    except orders.OrderError as exc:
        log({"chat_id": chat_id, "outcome": "screenshot_refused",
             "reason": exc.reason, "order_id": str(order_id)})
        send(chat_id, NO_OPEN_ORDER_DEFAULT)
        return
    send(chat_id, for_order(SHOT_THANKS, SHOT_THANKS_DEFAULT, order))
    owner_review(order)
    log({"chat_id": chat_id, "outcome": "screenshot_attached",
         "order_id": str(order["id"]), "amount": order["amount"]})


def handle_screenshot(conn, chat_id: int, file_id: str) -> None:
    """Evidence FOR THE SELLER. Nothing reads it, scores it or believes it."""
    open_orders = [o for o in orders.open_for_chat(conn, chat_id)
                   if o["state"] == "awaiting_payment"]
    if not open_orders:
        send(chat_id, NO_OPEN_ORDER_DEFAULT)
        log({"chat_id": chat_id, "outcome": "screenshot_no_order"})
        return
    if len(open_orders) > 1:
        # Attaching to the wrong one would have the owner confirm a payment
        # against an order the customer never meant. The amounts differ by
        # construction, so the customer can tell them apart.
        token = secrets.token_urlsafe(6)
        PENDING[token] = {"kind": "shot", "file_id": file_id,
                          "orders": [str(o["id"]) for o in open_orders]}
        send_kb(chat_id, WHICH_ORDER_DEFAULT,
                keyboard([[(f"{o['item']} — {money(o['amount'])}",
                            f"shot:{token}:{i}")]
                          for i, o in enumerate(open_orders)]))
        return
    attach_and_notify(conn, open_orders[0]["id"], file_id, chat_id)


def handle(conn, message: dict, last_seen: dict) -> None:
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id")
    text = (message.get("text") or "").strip()

    # A payment screenshot arrives with NO TEXT, and this function used to
    # return immediately on that -- so a customer who paid and sent proof got
    # silence, which is the worst possible reply to that particular message.
    # Checked before the owner test and before the throttle: evidence of a
    # payment is not a question and must not be rate-limited away.
    file_id = file_id_of(message)
    if file_id:
        handle_screenshot(conn, chat_id, file_id)
        return

    if not text:
        return

    is_owner = is_owner_id(user_id)

    if text.startswith("/fact"):
        handle_fact_command(conn, chat_id, is_owner,
                            text[len("/fact"):].strip())
        return

    if text.startswith("/start"):
        # THE PAYLOAD IS NOT DECORATION. `/start <code>` from the Settings
        # screen is how a business claims its own bot: Telegram will not tell
        # you a user id from a bot token, so the owner has to message the bot
        # and the bot has to recognise them.
        #
        # Signed with this bot's own token, so a stranger who presses Start
        # cannot claim it -- which matters because a bot is findable the moment
        # it exists, and `owner_telegram_id` gates /fact and the owner buttons.
        # Single use is the database's job, not this code's: 0011 only writes
        # `where owner_telegram_id is null`, so a second claim is refused
        # however good its code is.
        payload = text[len("/start"):].strip()
        if payload and channel.check_claim(payload, BUSINESS_ID, TOKEN):
            user_id = (message.get("from") or {}).get("id")
            claimed = False
            if user_id is not None:
                # The connection handle() was GIVEN, not a new one. handle() is
                # already inside a connection() block, so checking out a second
                # would be the nested checkout app/db.py warns about -- the one
                # that deadlocks a five-connection pool under concurrency.
                claimed = conn.execute(
                    "select app_business_claim_owner(%s, %s)",
                    (BUSINESS_ID, user_id)).fetchone()[0]
            if claimed:
                global OWNER_ID
                OWNER_ID = str(user_id)
                send(chat_id,
                     "Tayyor — bu bot endi sizga bogʻlandi.\n"
                     "Готово — бот привязан к вам.")
                log({"chat_id": chat_id, "is_owner": True,
                     "outcome": "owner_claimed"})
                return
            # A valid code that claimed nothing means somebody already owns
            # this bot. Say so rather than silently falling through to the
            # greeting, which would look like the link did not work.
            send(chat_id, "Bu botning egasi allaqachon bor.\n"
                          "У этого бота уже есть владелец.")
            log({"chat_id": chat_id, "is_owner": is_owner,
                 "outcome": "owner_claim_refused"})
            return

        send(chat_id, "Salom! Klinika haqida savolingizni yozing.\n"
                      "Здравствуйте! Напишите свой вопрос о клинике."
                      + ("\n\n(Siz egasi sifatida tanildingiz.)" if is_owner else ""))
        return

    canned = social(text)
    if canned is not None:
        send(chat_id, canned)
        log({"chat_id": chat_id, "is_owner": is_owner, "question": text,
             "outcome": "social"})
        return

    now = time.monotonic()
    tokens, last = last_seen.get(chat_id, (float(BURST), now))
    tokens = min(BURST, tokens + (now - last) / REFILL_SECONDS)
    if tokens < 1.0:
        last_seen[chat_id] = (tokens, now)
        send(chat_id, _say(BUSY, BUSY_DEFAULT, text))
        log({"chat_id": chat_id, "is_owner": is_owner, "question": text,
             "outcome": "throttled"})
        return
    last_seen[chat_id] = (tokens - 1.0, now)

    # Everything from here to the reply is wrapped, and the placement is the
    # decision. Not earlier: the social short-circuit and the throttle sit above
    # this line and both answer instantly with no model call, so an indicator
    # there would flash and vanish, or worse, promise an answer to someone who
    # has just been rate-limited. Not later: most of the wait is over by the
    # time retrieval finishes, so starting after it defeats the point.
    #
    # triage() and buy.preflight() are inside, and they are free -- pure Python,
    # measured at 0.00s. They cost nothing to include and including them means
    # the indicator is up before the first call that can block.
    with typing(chat_id):

        # A purchase, maybe. TRIAGE OUTRANKS COMMERCE: someone describing chest
        # pain who also says "to'lash" gets the emergency reply, never a payment
        # offer. triage() is pure and costs nothing, so it is asked first.
        #
        # preflight() is a COST filter -- see app/buy.py. Its block is logged, with
        # the message, so the miss rate can be measured by replaying blocked
        # messages through the classifier offline. An unmeasurable filter drifts
        # silently, and nothing here can know what it threw away.
        prefilter = buy.preflight(text) if triage(text) is None else None
        if prefilter:
            try:
                offer = buy.offer(conn, text)
            except LLMError:
                offer = None  # fall through and answer the question normally
            if offer is not None:
                if offer.get("refusal"):
                    send(chat_id, _say(NOT_ORDERABLE, NOT_ORDERABLE_DEFAULT, text))
                else:
                    send_offer(chat_id, text, offer)
                log({"chat_id": chat_id, "is_owner": is_owner, "question": text,
                     "outcome": "offer", "prefilter": prefilter,
                     "refusal": offer.get("refusal"),
                     "subjects": offer.get("subjects")
                                 or [offer.get("subject")]})
                return
        else:
            log({"chat_id": chat_id, "is_owner": is_owner, "question": text,
                 "outcome": "buy_prefilter_blocked"})

        # Resolve a follow-up into a standalone question. After the social
        # short-circuit and after the throttle, so neither spends a model call.
        # The output is a QUESTION -- everything downstream is untouched.
        asked, was_rewritten = rewrite(history_for(chat_id), text)

        try:
            result = answer(conn, asked)
        except LLMError as exc:
            # Quota gone, or the provider is down. Say something honest -- a bot
            # that goes quiet reads as broken software; this reads as software.
            send(chat_id, _say(BROKEN, BROKEN_DEFAULT, text))
            log({"chat_id": chat_id, "is_owner": is_owner, "question": text,
                 "outcome": "llm_error", "error": str(exc)[:300]})
            return
        except NotApproved as exc:
            # Same treatment as the database being gone, for the same reason:
            # the customer gets the generic apology -- a stranger messaging a
            # clinic has no business being told about the clinic's account --
            # and the operator gets a line that says exactly what to do. Without
            # this it lands in the catch-all below as "error", which is how a
            # one-command fix turns into an afternoon of reading logs.
            print(f"NOT APPROVED, so nothing can be answered: {exc}", flush=True)
            send(chat_id, _say(BROKEN, BROKEN_DEFAULT, text))
            log({"chat_id": chat_id, "is_owner": is_owner, "question": text,
                 "outcome": "not_approved"})
            return
        except psycopg.OperationalError as exc:
            # Say it where the operator will see it. A customer-facing apology is
            # not enough: the person running the demo needs to know the database
            # went away, not just that "something" failed.
            print(f"DATABASE UNREACHABLE: {exc!r}", flush=True)
            send(chat_id, _say(BROKEN, BROKEN_DEFAULT, text))
            log({"chat_id": chat_id, "is_owner": is_owner, "question": text,
                 "outcome": "database_error", "error": repr(exc)[:300]})
            return
        except Exception as exc:  # noqa: BLE001 - the bot must not die on one message
            send(chat_id, _say(BROKEN, BROKEN_DEFAULT, text))
            log({"chat_id": chat_id, "is_owner": is_owner, "question": text,
                 "outcome": "error", "error": repr(exc)[:300]})
            return

        reply = result["answer"] or _say(DONT_KNOW, DONT_KNOW_DEFAULT, text)
        send(chat_id, reply)
        remember(chat_id, text, reply)

        # Same shape as results.json, so the real questions can be graded the same
        # way the hand-written ones are. `asked` is logged separately from
        # `question`: when a follow-up goes wrong, what reached retrieval is the
        # thing worth seeing, not what the customer typed.
        log({
            "chat_id": chat_id, "user_id": user_id, "is_owner": is_owner,
            "question": text,
            "asked": asked if was_rewritten else None,
            "rewritten": was_rewritten,
            "status": result["status"],
            "route": result["source"],
            "matched_on": result.get("matched_on"),
            "fact_scores": [f["similarity"] for f in result.get("near_facts", [])],
            "chunk_scores": [c["similarity"] for c in result.get("chunks", [])],
            "answer": reply,
        })



def check_database() -> None:
    """Fail at startup, not on the first customer message.

    The dangerous failure is not the database being down -- it is the bot
    staying up while it is. Every question then gets a technical-error reply,
    and from the outside that looks like a broken product rather than a stopped
    container. This has happened twice; both times the bot was cheerfully
    polling.

    The container's restart policy is `unless-stopped`, so it returns on its own
    once the Docker engine runs. What it cannot do is start the engine.
    """
    try:
        # Short timeout: 30 seconds of silence before an error message is its
        # own kind of unhelpful when you are standing in front of people.
        # The one pool.connection() left in the codebase, and it asks
        # about the SERVER, not about anyone's data. Nothing tenant-
        # scoped is readable on it: every policy raises without a
        # business bound.
        with pool.connection(timeout=5) as conn:
            conn.execute("select 1")
    except Exception as exc:  # noqa: BLE001 - any failure to reach it is fatal
        raise RuntimeError(
            "Cannot reach the database, so the bot would answer every question "
            "with a technical error. Refusing to start.\n"
            "  Is Docker Desktop running? The talkwisp-db container returns by "
            "itself once the engine is up.\n"
            f"  {exc!r}"
        ) from None


# How often the poller records that it is alive, and how long a recorded
# heartbeat is trusted for. The loop wakes at least every POLL_TIMEOUT seconds,
# so 60 is comfortably more often than the 180 the API treats as "alive" -- two
# missed beats before anything reports the bot as down, which absorbs a restart
# and a slow poll without ever claiming a dead process is running.
HEARTBEAT_SECONDS = 60
_last_beat = 0.0


def heartbeat(force: bool = False) -> None:
    """Record that a poller is alive for this business.

    Throttled, because the loop ticks every 30 seconds and a write per tick
    buys nothing -- the reader's threshold is three minutes.

    NEVER FATAL. A bot that stopped answering customers because it could not
    write a status column would be the status reporting breaking the thing it
    reports on. A missed beat costs one screen saying "not switched on yet"
    slightly too early, which is recoverable; a crashed poller is not.
    """
    global _last_beat
    now = time.monotonic()
    if not force and now - _last_beat < HEARTBEAT_SECONDS:
        return
    try:
        with connection(BUSINESS_ID) as conn:
            conn.execute("select app_business_touch_bot(%s)", (BUSINESS_ID,))
        _last_beat = now
    except Exception as exc:  # noqa: BLE001 - see the docstring
        print(f"heartbeat failed (not fatal): {exc!r}", flush=True)


# Set by the SIGTERM handler, read at the top of each loop iteration.
#
# WHY THIS EXISTS AT ALL. There was no signal handling here, so the default
# applied: SIGTERM killed the process instantly, mid-anything. That dropped an
# in-flight generation with the money already spent, and left the typing
# indicator running in a customer's chat until a replacement answered.
#
# It did not lose the message, which is the part that surprised me: `offset` is
# a local variable and is only transmitted on the NEXT getUpdates, so Telegram
# never saw the batch confirmed and redelivers it. The real cost was DUPLICATE
# answers -- kill a process on update three of three and the restart re-answers
# one and two.
#
# Finishing the current update and exiting between iterations turns that
# duplicate into a clean handover, which matters now that a supervisor stops and
# starts these routinely rather than only when a human runs systemctl.
_stopping = False


def _stop(signum, frame) -> None:  # noqa: ARG001
    global _stopping
    _stopping = True
    print("stopping after this update.", flush=True)


def resolve_identity() -> tuple[str, str]:
    """(business_id, token). --business wins; the env token is the fallback.

    business -> token is the direction that scales: the token is already on the
    row, so one systemd template unit serves every customer and there is no
    per-business env file to drift out of step with the database.
    """
    import argparse

    ap = argparse.ArgumentParser(description="Talkwisp Telegram agent bot.")
    ap.add_argument("--business",
                    help="business name; its bot_token is read from the row")
    # BY ID, FOR ANYTHING AUTOMATED. business.name is NOT unique and self-serve
    # signup lets a stranger type their own, so two rows can be called "Avisena
    # Med" -- the same collision onboard.py --approve refuses to guess at.
    # Resolving a name inside the supervisor could start a poller for the wrong
    # tenant WITH THAT TENANT'S TOKEN, answering that tenant's customers. The
    # name stays for humans typing it; machines pass an id.
    ap.add_argument("--business-id", dest="business_id",
                    help="business id; what the supervisor passes")
    args = ap.parse_args()

    if args.business_id:
        business_id = args.business_id
        with connection(business_id) as conn:
            row = conn.execute("select bot_token, name from business").fetchone()
        if not row:
            raise RuntimeError(f"No business with id {business_id!r}.")
        if not row[0]:
            raise RuntimeError(f"{row[1]!r} has no bot_token.")
        return business_id, row[0]

    if args.business:
        business_id = business_by_name(args.business)
        if business_id is None:
            raise RuntimeError(
                f"No business named {args.business!r}. Create it first:\n"
                f"  uv run python onboard.py --name {args.business!r} ...")
        # RLS lets a tenanted connection read its OWN business row, which is
        # exactly this and nothing wider.
        with connection(business_id) as conn:
            row = conn.execute("select bot_token from business").fetchone()
        if not row or not row[0]:
            raise RuntimeError(
                f"{args.business!r} has no bot_token. A business with no channel "
                "connected is a real state -- the dashboard is built to show it "
                "-- but nothing can poll for it.\n"
                "  Add one from @BotFather:  onboard.py --name ... --token ...")
        return business_id, row[0]

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Neither --business nor TELEGRAM_BOT_TOKEN. Prefer --business: the "
            "token is already on the business row, and reading it from there "
            "keeps one credential in one place.")
    business_id = business_for_token(token)
    if business_id is None:
        raise RuntimeError(
            "No business owns this bot token, so there is no way to tell whose "
            "questions these are. Use --business, or set bot_token on the row.")
    return business_id, token


def main() -> None:
    global TOKEN, API, BUSINESS_ID, OWNER_ID

    # Fail now, not on the first customer message. The second call
    # spends one tiny generation to prove the pinned model answers for
    # this credential -- a wrong Vertex model id authenticates fine and
    # 404s at answer time, which is a boot-time fact found too late.
    check_configured()
    check_reachable()

    offset = None
    last_seen: dict[int, tuple[float, float]] = {}
    with pool:
        # Everything is checked BEFORE announcing that the bot is up, so the
        # last line printed is always true.
        check_database()
        print("database reachable.")

        # A superuser bypasses every row-level policy, so on that connection
        # this bot would answer one business's customers out of another
        # business's facts and nothing would fail. Boot failure, not a warning.
        assert_app_role()

        BUSINESS_ID, TOKEN = resolve_identity()
        API = f"https://api.telegram.org/bot{TOKEN}"

        # getMe BEFORE announcing anything, so the last line printed names the
        # bot that is actually about to poll rather than the one somebody
        # believes is configured. A token that is valid but belongs to a
        # different bot than intended is otherwise invisible -- which is how
        # seven landing-page links ended up pointing at a stranger's bot.
        me = httpx.get(f"{API}/getMe", timeout=30).json()
        if not me.get("ok"):
            raise RuntimeError(f"Telegram rejected the token: {me}")

        with connection(BUSINESS_ID) as conn:
            name, OWNER_ID = conn.execute(
                "select name, owner_telegram_id from business").fetchone()
        print(f"serving {name}.")
        if not OWNER_ID:
            print("business.owner_telegram_id is not set -- /fact will refuse "
                  "everyone, including you.")
        print(f"@{me['result']['username']} polling. Ctrl-C to stop.")
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        # Once before the loop, so a freshly started bot is visible to the
        # Settings screen immediately rather than up to a minute later -- that
        # first minute is exactly when the owner is sitting on the screen
        # waiting to be told they can press Start.
        heartbeat(force=True)

        # A bot answers one update at a time in this single-threaded loop, so it
        # can never hold more than one connection -- max_size 5 was a ceiling it
        # could not reach and a share of a budget it did not need. The real
        # ceiling on tenants is Postgres connections, not memory: max_connections
        # is 40 on this box, so five per bot allowed about six bots under load,
        # and exhausting it takes the API down for everyone with "too many
        # clients", not just the newest bot.
        pool.resize(min_size=1, max_size=2)

        while not _stopping:
            # Top of the loop, not after a successful poll: a bot that is up but
            # getting network errors from Telegram is still running, and
            # reporting it as dead would send the owner to fix the wrong thing.
            heartbeat()
            try:
                params = {"timeout": POLL_TIMEOUT}
                if offset is not None:
                    params["offset"] = offset
                response = httpx.get(f"{API}/getUpdates", params=params,
                                     timeout=POLL_TIMEOUT + 15)
                updates = response.json().get("result", [])
            except httpx.HTTPError as exc:
                # Network blip. Wait and keep polling; do not kill the bot.
                print(f"poll failed: {exc!r}")
                time.sleep(3)
                continue

            for update in updates:
                if _stopping:
                    # Mid-batch. The remaining updates were never confirmed to
                    # Telegram, so the replacement process receives them.
                    break
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    with connection(BUSINESS_ID) as conn:
                        handle_callback(conn, update["callback_query"])
                    continue
                message = update.get("message")
                if not message:
                    continue
                with connection(BUSINESS_ID) as conn:
                    handle(conn, message, last_seen)


if __name__ == "__main__":
    main()

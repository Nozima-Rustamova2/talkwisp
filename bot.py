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

import datetime
import json
import os
import pathlib
import secrets
import sys
import time

import httpx
from dotenv import load_dotenv

from app.answer import answer, detect_language
from app.normalize import normalize
from app.db import pool
import psycopg

from app.llm import LLMError, check_configured
from app.typed import candidates, conflicts
from app.typed import parse as parse_fact, store as store_fact

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Optional. Numeric Telegram user id. The owner is recognised now so that the
# step 23 write path has an identity to trust; it grants nothing yet.
OWNER_ID = os.getenv("TELEGRAM_OWNER_ID")

API = f"https://api.telegram.org/bot{TOKEN}"
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


def send(chat_id: int, text: str) -> None:
    httpx.post(f"{API}/sendMessage",
               json={"chat_id": chat_id, "text": text}, timeout=30)


def log(entry: dict) -> None:
    entry["at"] = datetime.datetime.now(datetime.UTC).isoformat()
    with MESSAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")



# ---------------------------------------------------------------- owner writes

# Telegram caps callback_data at 64 bytes, so the parsed fact cannot travel in
# the button. It lives here, keyed by a short token, and dies with the process:
# a button tapped after a restart must fail politely rather than write something
# the owner never actually saw.
PENDING: dict[str, dict] = {}

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


def edit(chat_id, message_id, text):
    """Replace the buttons with the outcome, so one cannot be tapped twice."""
    httpx.post(f"{API}/editMessageText",
               json={"chat_id": chat_id, "message_id": message_id, "text": text},
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

    result = parse_fact(conn, line)
    if result["error"]:
        send(chat_id, f"Tushunmadim: {result['error']}")
        return

    token = secrets.token_urlsafe(8)
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

    # Enforced here too, not only on the message: a button press is a separate
    # request, and anyone who can see the chat can tap it.
    if not (OWNER_ID is not None and str(user_id) == str(OWNER_ID)):
        answer_callback(cq["id"], "Faqat klinika egasi uchun.")
        return

    action, _, rest = data.partition(":")
    parts = rest.split(":")
    token = parts[0]
    pending = PENDING.get(token)
    if pending is None:
        answer_callback(cq["id"])
        edit(chat_id, message_id, EXPIRED)
        return

    if action == "drop":
        PENDING.pop(token, None)
        answer_callback(cq["id"], "Bekor qilindi")
        edit(chat_id, message_id, "Bekor qilindi. Hech narsa saqlanmadi.")
        return

    if action == "pick":
        choice = parts[1]
        if choice != "new":
            pending["parsed"]["subject"] = pending["candidates"][int(choice)]
            pending["conflicts"] = conflicts(conn, pending["parsed"])
        pending["candidates"] = []
        answer_callback(cq["id"])
        edit(chat_id, message_id, preview(pending))
        send_kb(chat_id, "Saqlaymizmi?", save_buttons(token))
        return

    if action == "save":
        PENDING.pop(token, None)
        try:
            store_fact(conn, pending["parsed"])
        except Exception as exc:  # noqa: BLE001
            answer_callback(cq["id"], "Xatolik")
            edit(chat_id, message_id,
                 "Saqlab boʻlmadi. Bir ozdan soʻng qaytadan urinib koʻring.")
            log({"chat_id": chat_id, "outcome": "fact_write_error",
                 "error": repr(exc)[:300]})
            return
        p = pending["parsed"]
        answer_callback(cq["id"], "Saqlandi")
        edit(chat_id, message_id,
             f"Saqlandi: {p['subject']} - {p['attribute']}: {p['value']}\n"
             "Endi mijozlar shu savolni bersa, bot javob beradi.")
        log({"chat_id": chat_id, "is_owner": True, "outcome": "fact_written",
             "subject": p["subject"], "attribute": p["attribute"],
             "value": p["value"]})


def handle(conn, message: dict, last_seen: dict) -> None:
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id")
    text = (message.get("text") or "").strip()
    if not text:
        return

    is_owner = OWNER_ID is not None and str(user_id) == str(OWNER_ID)

    if text.startswith("/fact"):
        handle_fact_command(conn, chat_id, is_owner,
                            text[len("/fact"):].strip())
        return

    if text.startswith("/start"):
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

    try:
        result = answer(conn, text)
    except LLMError as exc:
        # Quota gone, or the provider is down. Say something honest -- a bot
        # that goes quiet reads as broken software; this reads as software.
        send(chat_id, _say(BROKEN, BROKEN_DEFAULT, text))
        log({"chat_id": chat_id, "is_owner": is_owner, "question": text,
             "outcome": "llm_error", "error": str(exc)[:300]})
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

    # Same shape as results.json, so the real questions can be graded the same
    # way the hand-written ones are.
    log({
        "chat_id": chat_id, "user_id": user_id, "is_owner": is_owner,
        "question": text,
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


def main() -> None:
    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Get one from @BotFather and add it "
            "to .env."
        )
    check_configured()  # fail now, not on the first customer message

    me = httpx.get(f"{API}/getMe", timeout=30).json()
    if not me.get("ok"):
        raise RuntimeError(f"Telegram rejected the token: {me}")

    offset = None
    last_seen: dict[int, tuple[float, float]] = {}
    with pool:
        # Everything is checked BEFORE announcing that the bot is up, so the
        # last line printed is always true.
        check_database()
        print("database reachable.")
        if not OWNER_ID:
            print("TELEGRAM_OWNER_ID not set -- /fact will refuse everyone, "
                  "including you.")
        print(f"@{me['result']['username']} polling. Ctrl-C to stop.")
        while True:
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
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    with pool.connection() as conn:
                        handle_callback(conn, update["callback_query"])
                    continue
                message = update.get("message")
                if not message:
                    continue
                with pool.connection() as conn:
                    handle(conn, message, last_seen)


if __name__ == "__main__":
    main()

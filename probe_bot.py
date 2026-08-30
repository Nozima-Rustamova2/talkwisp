"""Exercise bot.handle() without Telegram. Sends nothing; prints what it would."""

import sys
import time

import bot
from app.db import pool

sys.stdout.reconfigure(encoding="utf-8")

sent: list[tuple[int, str]] = []
bot.send = lambda chat_id, text: sent.append((chat_id, text))
bot.MIN_SECONDS_BETWEEN = 2.0

CHAT = 999
MESSAGES = [
    "/start",
    "Иш вақтингиз қандай?",
    "Сколько стоит МРТ?",          # gap -> canned Russian "don't know"
    "Kardiolog bormi?",            # sent immediately -> throttled
]

with pool:
    with pool.connection() as conn:
        for text in MESSAGES:
            bot.handle(conn, {"chat": {"id": CHAT}, "from": {"id": 1},
                              "text": text}, bot_last_seen := getattr(
                                  bot, "_probe_seen", {}))
            bot._probe_seen = bot_last_seen
            print(f"\n>>> {text}")
            print(f"<<< {sent[-1][1] if sent else '(nothing)'}")

        print("\n--- now wait past the rate limit and retry the throttled one ---")
        time.sleep(2.1)
        bot.handle(conn, {"chat": {"id": CHAT}, "from": {"id": 1},
                          "text": "Kardiolog bormi?"}, bot._probe_seen)
        print(f"<<< {sent[-1][1]}")

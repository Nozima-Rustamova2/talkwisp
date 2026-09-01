"""Multi-turn conversations through bot.handle(). Sends nothing to Telegram.

The 46-question harness cannot test this: it calls answer() directly and never
passes through the rewrite. These are the sequences that matter.
"""

import sys

import bot
from app.db import pool

sys.stdout.reconfigure(encoding="utf-8")

sent: list[str] = []
bot.send = lambda cid, t: sent.append(t)

CONVERSATIONS = [
    # The case that started this: a follow-up with no subject of its own.
    ["Kardiolog qabuli qancha turadi?", "Koʻproq maʼlumot ber"],
    # Two words, no reference word -- the length rule has to catch it.
    ["Ginekolog qabuli haqida ayting", "Nech pul"],
    # A reference word inside a longer question.
    ["Rasulova Gulnora qachon qabul qiladi?", "Shanba kuni ham ishlaydimi u?"],
    # MUST NOT be rewritten: self-contained questions in the middle of a chat.
    ["Manzilingiz qayerda?", "Yakshanba kuni ishlaysizmi?"],
    # A follow-up referring to something never discussed -> leave it alone.
    ["Telefon raqamingiz nechchi?", "Uni qancha turadi?"],
]

with pool:
    with pool.connection() as conn:
        for n, turns in enumerate(CONVERSATIONS, 1):
            chat = 9000 + n
            print(f"\n{'=' * 68}\nconversation {n}")
            for turn in turns:
                before = len(sent)
                bot.handle(conn, {"chat": {"id": chat}, "from": {"id": 1},
                                  "text": turn}, {})
                reply = sent[-1] if len(sent) > before else "(nothing)"
                hist = bot.history_for(chat)
                print(f"\n  customer : {turn}")
                # What actually reached retrieval, which is the thing worth seeing.
                asked, changed = bot.rewrite(hist[:-1], turn)
                if changed:
                    print(f"  REWRITTEN: {asked}")
                else:
                    print(f"  (not rewritten)")
                print(f"  bot      : {reply[:150]}")

print(f"\n{'=' * 68}")
print("expiry: a follow-up after the idle window must not attach")
bot.HISTORY[9999] = {"turns": bot.collections.deque([("Kardiolog narxi?", "200 000")]),
                     "at": bot.time.monotonic() - (bot.HISTORY_IDLE_SECONDS + 10)}
print(f"  history_for(9999) after {bot.HISTORY_IDLE_SECONDS}s idle: "
      f"{bot.history_for(9999)}")

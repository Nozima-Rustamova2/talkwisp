"""Drive the owner write path without Telegram. Sends nothing; prints instead."""

import sys

import bot
from app.db import pool

sys.stdout.reconfigure(encoding="utf-8")

OWNER, STRANGER = 111, 222
bot.OWNER_ID = str(OWNER)

out: list[str] = []
bot.send = lambda cid, t: out.append(f"TEXT: {t}")
bot.send_kb = lambda cid, t, kb: out.append(
    f"TEXT: {t}\nBUTTONS: {[b['text'] + ' -> ' + b['callback_data'] for r in kb['inline_keyboard'] for b in r]}")
bot.edit = lambda cid, mid, t: out.append(f"EDIT: {t}")
bot.answer_callback = lambda cid, t="": out.append(f"TOAST: {t}") if t else None


def show(label):
    print(f"\n=== {label} ===")
    for line in out:
        print(line)
    out.clear()


def press(conn, data, user=OWNER):
    bot.handle_callback(conn, {"id": "cb", "data": data,
                               "from": {"id": user},
                               "message": {"message_id": 1,
                                           "chat": {"id": 1}}})


with pool:
    with pool.connection() as conn:
        bot.handle_fact_command(conn, 1, False, "MRT narxi 400 000 so'm")
        show("a stranger tries to write")

        bot.handle_fact_command(conn, 1, True, "MRT narxi 400 000-500 000 so'm")
        show("owner types a new fact")
        token = next(iter(bot.PENDING))

        press(conn, f"save:{token}", user=STRANGER)
        show("a stranger taps Save on the owner's message")

        press(conn, f"save:{token}")
        show("owner taps Save")

        bot.handle_fact_command(conn, 1, True, "Rasulova 20 yil tajriba")
        show("owner names an ambiguous subject")
        token = next(iter(bot.PENDING))

        press(conn, f"pick:{token}:0")
        show("owner picks the first Rasulova")

        press(conn, f"drop:{token}")
        show("owner cancels")

        press(conn, "save:gone")
        show("a button tapped after a restart")

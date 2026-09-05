# Smoke test: the payment loop against a real Telegram

Everything in the payment flow is verified structurally and by the deterministic
checks. **Nothing in it has sent a real message.** `sendPhoto`, `callback_data`
round-trips and inline keyboards are only really tested by a person tapping
them, and they are exactly the things that work on paper and return `400` in
practice.

## Before you start

**Two accounts, or a second device.** The owner check runs on
`TELEGRAM_OWNER_ID`, and the customer half of this flow has to come from
somebody who is *not* the owner. Running both sides from one account either
fails or passes for the wrong reason, which is worse.

    uv run python migrate.py     # 0006 must be applied
    uv run python bot.py         # leave it running; it prints every failure

Watch the terminal as much as the chat. `send`, `sendPhoto` and the database
all print there when they fail, and a silent chat with a loud terminal is a
different diagnosis from a silent chat with a quiet one.

## The happy path

| # | As | Do | Expect |
|---|----|----|--------|
| 1 | customer | `Kardiolog qabuli uchun to'lovni amalga oshirmoqchiman` | two buttons: `qabul narxi — 180 000 soʻm`, `takroriy qabul narxi — 90 000 soʻm` |
| 2 | customer | tap the first | the buttons are replaced by the item, then the payment message: instruction, card block, then the amount **alone on its own line** |
| 3 | — | check the amount | it must end in a suffix, e.g. `180 001 soʻm`, never the round number |
| 4 | customer | send any photo | "Rahmat, chek qabul qilindi…" |
| 5 | owner | — | the screenshot arrives with a caption and two buttons |
| 6 | owner | tap `Tasdiqlash` | **caption** changes to `Tasdiqlandi: …`; customer gets the delivery message |

Step 6 is the one that was broken until it was read carefully:
`editMessageText` cannot edit a photo. If the caption does not change, that
regressed.

## The paths nobody designed

These are what real customers actually do, and none of them is a designed
route. Each one has a defined correct behaviour.

**Double-tap Confirm.** Tap `Tasdiqlash` twice, fast — this is what a person on
a slow connection does. The second tap must land on a refused transition and
say `Bu buyurtma allaqachon hal qilingan`, not confirm twice and not silently
do nothing. The state machine guarantees one write; this checks the message
says so.

**Screenshot with no order.** As a fresh customer who has ordered nothing, send
a photo. Expect `Sizda toʻlov kutayotgan buyurtma yoʻq…` — not silence, which
is what happened before A3b-2, and not a crash.

**Two screenshots for one order.** Send a second photo after step 4. The order
is already `awaiting_owner`, so `attach_screenshot` refuses the transition: the
customer should be told something and the owner should NOT get a second review
for the same order.

**Two open orders, then a screenshot.** Order twice without paying, then send
one photo. Expect a keyboard asking which order it belongs to. Attaching to the
wrong one would have the owner confirm a payment the customer never made.

**Reject, and read the reason.** Tap `Rad etish` — a second keyboard must
appear with two reasons, because the database refuses a rejection without one.
Pick `Summa mos emas`; the customer must be told to send the exact amount
again, not a generic apology.

**Block the bot, then confirm.** As the customer, block the bot. As the owner,
confirm an order for that chat. The owner must receive
`Mijozga xabar yetkazib bo'lmadi` on the payment channel. This is the one that
matters most and the one nobody would think to try: a customer who paid and
then blocked the bot is stranded, and a failure recorded only in a log is
visible to a person who is not looking.

**A payment question, not a purchase.** Send `Karta raqamingiz nima?`. The
reply must be byte-identical to the stored template, with no purchase buttons —
the card number must never be composed by the model, and asking for it is not
buying anything.

**Triage outranks commerce.** Send `Ko'kragim og'riyapti, to'lashim kerakmi?`
— chest pain plus a payment word. Expect the emergency number, never a payment
offer.

## Afterwards

    uv run python -c "from app.db import pool; \
      pool.open(); c = pool.connection().__enter__(); \
      print(c.execute('select state, count(*) from purchase group by 1').fetchall())"

Test orders are real rows. They hold their amounts out of circulation for seven
days, which is harmless on a demo database and worth knowing before doing this
against a real one.

`messages.jsonl` holds every route taken, including `buy_prefilter_blocked` for
messages the cost filter refused to classify. Those are the input to measuring
the filter's miss rate later.

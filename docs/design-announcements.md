# Announcements

**Status: designed, not built. Blocked on a legal question.**

Owner sends a message to people who have talked to their bot.

---

## Scope — announcements, not marketing

A course starting, a schedule change, a closure. Things a customer would want to
know and be annoyed to have missed.

Not promotions. That distinction is what keeps a customer's bot from being
restricted, and it has to be structural rather than a rule in this document.

## The gate: block and opt-out rate, not a subject box

Three mechanisms were considered:

- **Requiring a stated topic** — theatre. An owner types "course update" and
  sends a promotion. It adds a field and a false sense of having addressed the
  problem, which is worse than nothing because it makes the doc feel true.
- **A hard cap** (one per seven days) — the one unbypassable control, because
  the harm is volume. Worth having as a floor.
- **The chosen mechanism:** if the previous announcement lost more than a
  threshold of its recipients to blocks and opt-outs, the next is refused until
  the owner explicitly acknowledges it.

The third is self-calibrating, targets the actual harm rather than a proxy, and
costs nothing extra because blocks and opt-outs must be recorded anyway. A
business sending genuine schedule changes never sees it; one sending promotions
hits it on the second send.

**Tying it to a knowledge change is not a gate** — "50% off" is expressible as a
fact, so a determined marketer adds one first. Use it as the default compose
path instead: an "announce this" action on a confirmed fact that just changed,
with free compose as the fallback. Shapes the common case without pretending to
constrain.

## Compose lives on the web

Takeover and payment confirmation are in Telegram because they're *reactive* —
something happened, the owner answers, and the phone is where they already are.

A broadcast is *initiated*, irreversible, and goes to everyone. The confirm step
needs a recipient count, a rendered preview and a moment's consideration, and a
phone is the context that most encourages the impulsive send this feature exists
to discourage. The opt-out list and blocked counts want a screen too.

Cost, stated: an owner who lives in Telegram never discovers it. Fix by having
the bot mention it once, not by moving compose.

## Mechanics

**Two tables.** `announcement` (body, created, sent, status) and
`announcement_delivery` (one row per recipient, state pending/sent/blocked/
failed). The delivery table *is* the progress — a restart mid-broadcast resumes
by reading pending rows, with no in-memory state to lose. Same reasoning that
put the escalation claim in a row rather than in `PENDING`.

**Pacing** as a sweep in the poll loop, like the expiry sweep: N deliveries per
tick, conservatively under Telegram's limits, honouring 429's `retry_after`
rather than guessing.

**`send()` has to grow a reason.** Today it returns a bare bool, so a user who
blocked the bot is indistinguishable from a network blip. 403 "bot was blocked
by the user" is permanent and must mark that recipient forever; 429 is wait; a
network error is retry. **This is a defect in existing code independent of this
feature and worth fixing regardless.**

> Done on 2026-09-16 in `fc20b4b`, ahead of the rest of this document.
> `send()` returns a `SendResult` that is falsy on failure and carries
> `blocked` and `retry_after`. Changing the return type immediately exposed a
> silent miscount in `notify_owners`, which counted deliveries with
> `is not False` — an object is never the literal `False`, so every failed send
> counted as delivered. See `check_bot.py` for all seven Telegram error shapes.

**Opt-out is announcement-scoped, not bot-scoped.** Someone who opts out of
announcements must still get answers to their own questions — otherwise one tap
silently breaks the product for them.

## Audience selection — v1 and later

**v1: everyone who has started a conversation with this bot.** That's the whole
audience available, and it's what the first version sends to.

**Later: targeted subsets.** Not by typing usernames — a bot cannot message
someone by `@username`, there's no lookup from username to chat ID, and a bot
can only write to people who have started a chat with it. A username field would
accept input and silently never deliver, the same dead end as the owner-ID
field.

Instead, select from people already known, using data already held:

- everyone who bought a specific course (`purchase` filtered by item)
- people who asked about something and never bought
- people active in the last N days

Better than typing: no spelling errors, no unreachable recipients, and an
accurate count before sending.

**A second bot token does not help.** The constraint is per-bot — a second bot
starts with zero recipients, because every `chat_id` held belongs to
conversations with the first one. Broadcast only works through the bot that
already has the history.

## The legal question — answer before building

Telegram's Bot API only lets a bot message someone who has started it, so we're
technically permitted. That's a technical gate, not a consent record, and
Telegram's enforcement against unsolicited messaging is report-driven — so the
block and opt-out rate is the compliance signal in practice as well as the
product one.

**Not asserted, needs a lawyer:**

1. Does "messaged the bot once" constitute consent for non-transactional
   messages under Uzbek law?
2. Does an opt-out satisfy it, or is opt-in required?
3. Does storing chat ids plus names for this purpose trigger data-localisation
   or registration duties? (That already applies to what's stored today; this
   feature makes the list operationally central.)
4. **Does a transactional message differ?** "Your course starts tomorrow" to
   someone who paid may sit on a different side of the line than a broadcast to
   everyone who ever asked a question. If so, the transactional subset may be
   buildable before the general one.

Get this answered **before** building. Opt-out versus opt-in is the shape of the
feature, not a late edit — it changes the consent model, the copy and the
recipient set.

Same conversation should cover the governing-law line in the terms page.

## Also considered, deliberately not built

**Templates** — a few starting points for common announcement shapes (schedule
change, class starting, closure). Cheap, reduces the blank-box problem, and
nudges toward announcement-shaped messages. Worth adding once the feature
exists.

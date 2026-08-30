# Screen A — Dashboard home

Design intent. States behaviour the backend has to support, not markup.
Prototype HTML in `prototypes/` is a visual reference with fake data — never
build an API to match the mock rows.

---

## Who lands here

A clinic owner on a phone or a cheap laptop, twice a week, for about forty
seconds. They want one sentence of reassurance and one thing to do.

An agency operator wants the same board denser, with keyboard access.

---

## Shell

Left rail on desktop (Dashboard, Knowledge base, Gaps, Conversations, Test
console, Templates, Settings), collapsing to a bottom bar on mobile. Rail width
is set by its longest Russian string — never truncated.

Top bar: agent switcher as a plain text button ("Shifo Med — front desk"),
deliberately low-key because most owners have one agent. Language switcher
(UZ · RU · EN). Account.

---

## Board, top to bottom

### 1. Status plate

Full width, first thing the eye hits. Large line: **Agent is answering**.
Beneath it, one row of small facts: channels live, confirmed fact count, plan
status.

**Live is a three-part check, not billing alone.** The plate says "Agent is
answering" only when billing is active AND a channel is connected AND there is
confirmed knowledge. Otherwise it names the specific blocker with the fix inline
("Not answering — no channel connected").

**Blockers are ordered, and only the first is shown:**
1. Payment overdue
2. No channel connected
3. No confirmed knowledge

Never stack three problems on the plate.

**The one primary action lives on this plate:**
- Empty account → `Create AI agent`
- Populated, gaps exist → `Answer 41 open questions`
- Populated, no gaps → `Test your agent`

Never a disabled button. If there's nothing to do, the slot collapses.

### 2. This week, in four figures

One horizontal band, hairline-divided, no charts: questions answered, questions
it couldn't answer, conversations handed to a human, and share answered without
the owner. Each number links to the filtered list behind it.

The last figure is what tells them the product works — rightmost and largest.

**Suppress the percentage below a traffic floor.** Three questions with one
handoff reads as 67% and looks like failure when nothing is wrong. Show raw
counts until there's enough volume for a rate to mean anything.

A seven-bar sparkline strip beneath, drawn as plain divs. No chart library,
nothing animated.

### 3. Two columns — the actual dashboard

**Left: What it doesn't know yet.** The reason they come back. Five real customer
questions in the customer's own words, mixed scripts as they arrived, each with
how many people asked it, sorted by frequency. One action per row: `Add answer`.

**`Add answer` opens an inline composer on this screen.** It does not navigate to
the Gaps screen. This is the whole reason the dashboard earns a return visit —
the loop closes in one place, in under a minute, on a phone. Do not refactor this
into a route.

Footer link to the full list.

**Right: Needs your attention.** At most five items, mixed types, each one line
with a verb: conflicting facts → Resolve; channel disconnected → Reconnect;
customers asked for a human → Open.

Empty is a genuine and celebrated state: "Nothing needs you."

If the owner left Add knowledge with unconfirmed facts, this column catches it.

### 4. Footer strip

Quiet: last knowledge update, test your agent, invite a colleague.

---

## Hierarchy in one sentence

Are you working → is it worth it → what to do next → housekeeping.

---

## States

**Empty (first login).** Status plate reads "No agent yet". Everything below
collapses to a single panel: one line of what happens next, one solid
`Create AI agent` button, a muted link to templates. No fake zeros, no ghost
charts.

**Live but starved** (channel connected, few facts). Figures band present and
honest (0 questions). The gap column shows missing basics — opening hours,
address, price range — instead of asked questions, because nobody has asked yet.

**Loading.** Hairline skeleton rules in the cells. No shimmer, no spinner —
cheap on a mid-range Android.

**Error.** The affected block only, degraded in place with a retry link. The
status plate never shows an error; it shows last known state with a timestamp.

**Operator variant.** Same layout, denser. The agent switcher becomes a stacked
list of client accounts in the rail with per-account gap counts. `j`/`k` moves
through the gap list, Enter opens the composer.

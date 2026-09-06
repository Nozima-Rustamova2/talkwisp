# Talkwisp — project instructions

## Visual direction: read `docs/design-direction.md` before designing anything

The direction is **Telegram-fluent utility** — white surfaces on a cool ground
(`#f4f6f9`), one saturated blue (`#1f6feb`), rounded soft-shadowed cards instead
of frames, **Manrope** as the only typeface. Tokens are in
`docs/design-direction.md`. Product decisions are in `docs/design-decisions.md`.

## The Industry design system is DETACHED. Do not build against it.

Chosen 2 Sep 2026 and then dropped the same day after it read wrong on two
screens (full diagnosis in `docs/design-direction.md`). If an Industry design
system is bound to this project, or its skill instructions say to load
`_ds/industry-*/_ds_bundle.js`, **that attachment is stale and this file wins**:

- Do not link `_ds/industry-*/styles.css` or load its bundle in new work.
- Do not use `var(--color-*)` / `var(--font-*)` Industry tokens, `.blueprint`,
  `.corner` registration marks, Barlow or Barlow Condensed.

**No screen references `_ds/` any more.** The folder can be deleted once the
project-level attachment is unbound; nothing in the build depends on it.

## Migration status — the project is mid-migration, do not assume consistency

| File | Direction |
| --- | --- |
| `Sign In.dc.html` | **1B** — Telegram-fluent utility |
| `Onboarding Business Type.dc.html` | **1B** — step 1 |
| `Onboarding Add Knowledge.dc.html` | **1B** — step 2, momentum composition |
| `Onboarding Review.dc.html` | **1B** — step 3, accepted-by-default. **Stale: the conflict block asks a question the backend refuses to answer — see below** |
| `Onboarding Test.dc.html` | **1B** — step 4, celebration lives here |
| `Onboarding Telegram.dc.html` | **1B** — step 5, connect + live self-test |
| `Add Knowledge.dc.html` | **1B** — retrofitted 2 Sep |
| `Dashboard.dc.html` | **1B** — retrofitted 3 Sep, last one migrated |
| `Payment Details.dc.html` | **1B** — Settings, not the knowledge base |
| `Direction Options.dc.html` | Comparison doc, both directions, frozen |
| `Onboarding Intent.dc.html` | Cut screen, record only, do not rebuild |

Migration is **complete** — every live screen is on 1B. `Onboarding
Intent.dc.html` is a cut-screen record and `Direction Options.dc.html` is a
frozen comparison; neither is product. Keep this table current.

**Onboarding is five steps, 0–5** (sign in, business type, add knowledge,
review, test, connect Telegram). There is no step 6: a screen whose only job is
handing off is the same failure as the cut intent screen. Going live is a
consent moment on step 5's **Take it live →** button, which states the
consequence — *"Your customers can message @shifomed_bot from the moment you
tap this"* — and lands the owner on the dashboard.

**The knowledge-input machinery now exists twice** — dense in
`Add Knowledge.dc.html`, sparse in `Onboarding Add Knowledge.dc.html`. That
is 2 of 3 repeats under the no-abstraction-until-three rule. At the third, make
it one component with a density prop and mount it from both; until then, any
change to the drop zone, the typed zone, the long-paste offer, or the
confirmed-vs-extracted asymmetry must be made in BOTH files.

## The owner has TWO modes, not one — don't design as if there's only one

Everything built so far is the owner **configuring** the agent: knowledge,
review, testing, connecting. A manual-payment-confirmation feature is coming
and it is the owner **operating** it — money at stake, a customer waiting, a
decision to make now. Do not let the next screen assume configuration is the
only mode.

Known consequences, decided before anything is built:

- **Operating may deserve its own nav place**, not a corner of Settings.
  Settings is where you configure; orders are where you operate.
- **The decision happens in Telegram, not the dashboard.** A seller isn't at a
  laptop at 9pm. The dashboard's job is history and the state of open orders —
  never the confirm/reject decision itself.
- **"Needs your attention" holds THREE kinds of item, not one homogeneous
  list:** a payment awaiting confirmation (money + deadline + customer
  waiting), a forwarded question (customer waiting, no money), and a knowledge
  conflict (nobody waiting). Ranked by **who is waiting**, expressed as elapsed
  time on the item — a fact, not a severity colour. Waiting items sit in their
  own block above the list; conflicts stay in the list below it.
- **Waiting decays; escalations expire into gaps.** A forwarded question stops
  being urgent after 24 hours (the customer has gone) and moves to the gap list
  with its history intact; payments expire on their own commercial deadline.
  The waiting group is never capped — hiding a waiting customer behind *show
  more* is the failure the feature exists to prevent — while conflicts carry
  the five-item cap instead.
- **"Nothing needs you" now has two meanings.** No waiting items but conflicts
  remaining is *nothing urgent*, not *nothing to do*: use *"Nobody's waiting on
  you."* and keep the old phrase only for a genuinely empty card.
- **Gaps and escalations are different things.** A gap is *"add this when you
  get a chance"*; an escalation is *"someone asked and is still waiting"*. The
  dashboard's gap list must not silently absorb escalations — but they converge,
  because answering an escalation writes the fact that closes the gap.
- **Forwarded questions are answered by the owner AS THEMSELVES**, visibly: the
  customer sees a person answered, not the agent. The reply goes both to the
  customer and into the knowledge base.
- **Confirmations and forwarded questions share one Telegram chat** and the
  backend keeps them visually distinct. Whatever the dashboard does must match
  that distinction.
- **Order state names are fixed vocabulary:** `awaiting_payment`,
  `awaiting_owner`, `owner_confirmed`, `owner_rejected`, `expired`,
  `cancelled`. **Never "paid" or "verified".** We cannot verify a payment —
  confirmation records that the OWNER ASSERTED it, not that money arrived. The
  schema holds that line and the UI must too: copy reads *"confirmed by you"*,
  never *"paid"*.
- **A card number is a fact that must never be retrieved** — a documented
  category, structural rather than cosmetic. **Decided: payment details live in
  Settings, not the knowledge base.** Every other stored fact exists *in order
  to be retrieved*; a card number exists to be recited to one customer at one
  moment. It also fails the KB's interaction model — no aliases, no provenance
  beyond *you typed this*, no confidence, no conflict resolution (two card
  numbers means one is wrong, not "which is current"). Putting it there would
  need a permanent exclusion rule in every retrieval path, context window and
  provenance panel. The knowledge base instead carries **one pointer row** —
  *"Payment instructions · managed in Settings →"* — so an owner searching for
  what the agent knows about paying isn't told nothing.
- **The card number is NOT masked.** The seller broadcasts it to every customer
  who asks, so hiding it from its owner is theatre and costs a tap every time
  they check it against their bank app. Shown in full, grouped in fours.
- **Card validation is digits and length only — never a checksum.** A validator
  that rejects a correct card number is a dead end with money behind it; one
  that accepts a wrong number surfaces the moment a customer says the transfer
  failed. Asymmetric costs, so take the safe side. Luhn on Uzcard/Humo only
  after verifying against real BINs, and only ever as a warning.
- **Changing the card with orders in flight is a DECISION, not a notice.** When
  any order is `awaiting_payment`, the change flow asks: *let those finish on
  the old card* (default) or *send those customers the new details*. Both are
  legitimate depending on whether the old card still works, and only the owner
  knows which. Silently breaking in-flight orders is the expensive failure.
- **Design is ahead of the backend on payments — two things are now specified
  by design, not by the brief.** (1) The template contract is exactly
  `{{card}}`, `{{name}}`, `{{amount}}`; message assembly must substitute
  those tokens. (2) The in-flight decision requires **an order to record the
  card details it was told to pay to** — a `purchase`-level field, cheapest to
  add while the table is new. **It must be a SNAPSHOT of the details as sent,
  never a foreign key to current settings** — a pointer means changing the card
  retroactively rewrites what every past order claims it asked for, which makes
  the in-flight decision meaningless and past orders unauditable. Same
  reasoning as the escalation context snapshot.
- **One payment destination — for interface reasons only. THE SCHEMA OBJECTION
  IS RETIRED.** It originally read: two destinations require an order to record
  which one it was sent to, and that's schema, so it can't be retrofitted as
  UI. That argument died when `purchase` gained the destination snapshot for
  the in-flight decision — the same field multiple destinations would have
  needed. The hard part (confirmation knowing which account to check) is
  therefore already solved, and multiple destinations are now **cheaper** than
  when we rejected them. What remains is UI complexity across every state on
  the payment-details screen: a per-destination default, which one each
  template quotes, and the in-flight decision multiplied per destination.
  That is a materially weaker argument than the original, so **"build one" is
  now a scope judgement, not a structural constraint** — if a customer asks for
  two, this is much easier to overturn than the old note implied.
- **Payment details are a new surface of stored facts** (card number,
  cardholder name, bank, per-language instruction text). They belong wherever
  facts are edited, but a card number in a list beside opening hours wants
  different treatment — masked by default, and never in a provenance quote.

## A conflict is NOT a question — `Onboarding Review.dc.html` is out of date

Decided 6 Sep 2026 while building the real Review screen against the backend.

The prototype's conflict block asks **"A consultation reads two different
prices. Which is current?"** and offers two buttons. **The backend refuses to
answer that question, on purpose.** `/conflicts` surfaces subject+attribute
pairs answered more than one way and *never resolves them*, because two
opening-hours values may both be true — a clinic really can have different
Saturday hours, and a price really can differ by doctor.

So the built screen shows a contradiction **beside** the proposal as
information, and says so: *"Keeping this one does not remove the other. Both may
be true — the agent is told about both."* Keep confirms; nothing is deleted.

**This is the design conforming to the backend, not the backend growing a
feature to match a mockup**, and that is the right direction — a two-button
choice would have invented a resolution the system does not have, and the owner
would have believed the losing value was gone.

Two things follow for anyone editing the prototype:

- `Onboarding Review.dc.html` still draws the two buttons. It is **stale on this
  point**. Retrofit it or mark it, but do not treat it as the spec.
- **The same question will come back on the dashboard's knowledge-conflict
  item.** That item is *"these two disagree, here they both are"*, never *"pick
  one"*. The only resolving action available is editing or removing one of the
  facts, which is a knowledge-base action, not a conflict-screen one.

Also decided by the same build: **there is no Undo on Keep or Remove.**
Confirming has no inverse endpoint, and `DELETE /review/{id}` deliberately
refuses confirmed facts — rejecting means *the extraction was wrong*, not *the
thing stopped being true*. The prototype offers Undo on both. Don't draw an Undo
the API cannot honour; Remove asks once instead.

## Density is now doing the work styling used to do

One visual language, two densities:

- **Momentum screens** (auth, onboarding) come out **sparse** — one elevated
  card on the ground, one question, large heading, generous padding, no rail, no
  step component.
- **Ledger screens** (dashboard, review, knowledge, gaps, conversations) come
  out **dense** — tight rows, hairline row rules inside one card, a consistent
  grid, one accented action per row.

If a momentum screen starts reading as a settings page, that is the earlier
failure returning through a different door.

## Non-negotiables on every screen

- **Honest state.** Nothing claims to be working when it isn't. An agent is
  "answering" only when billing is active AND a channel is connected AND there
  is confirmed knowledge.
- **Every fact has a source**, one click to see where it came from — but only at
  the granularity actually stored. Check before drawing a provenance line:
  owner-typed (*"You typed this"* — the common case), file-level (label +
  filename, **no sheet/row/page**), or prose (label + the passage quoted).
  Row-level provenance arrives with spreadsheet ingestion; don't draw it yet.
- **Trilingual** — Uzbek (Latin), Russian, English. Russian runs ~30% longer;
  Cyrillic and Latin appear in the same row. No fixed-width labels.
- **Mid-range Android, 360px, slow connections.** No heavy animation, **no
  spinners** — buttons state what they're doing in their own labels
  (*Sending…*, *Saving…*). Never lose the user's work.
- **Approximate costs** ("200 000–300 000 UZS"), never exact prices.
- Internal design vocabulary never appears in the UI.

## How to work here

One screen at a time, in order. Describe it in words first — layout, hierarchy,
what the user does first, and its states — and wait for a go before building.
Then build it static with realistic fake data (Uzbek and Russian clinic names,
som ranges, mixed scripts, some deliberately wrong low-confidence extractions).
No API wiring, no routing, no state library, no component abstraction until a
pattern repeats three times. After each screen: show it, list what's missing,
stop. Ask when something is a genuine preference call.

# Talkwisp — design decisions

Running record of what's settled. Written 2 Sep 2026, from the decisions made
across screens A, C and B2. Update this when a decision changes; don't rely on
chat memory.

## Product shape

- **Talkwisp** — an AI agent platform for businesses in Uzbekistan. The agent
  answers repeat customer questions from knowledge the owner supplies.
- **Primary user:** a non-technical business owner automating repetitive
  questions. Canonical case is a clinic asked constantly about opening hours,
  which doctors work there, and roughly what a visit costs. Also salons,
  tutoring centres, service businesses. Phone or cheap laptop.
- **Secondary user:** an agency operator running several client accounts on
  desktop. Wants density and keyboard access. Design for the owner; add
  affordances for the operator.
- **Not our customer:** grocery or high-SKU retail. This is not a product
  catalog.
- **Costs are approximate** ("200 000–300 000 UZS"), never exact. No catalog
  tables, no price-precision UI anywhere.

## Scope model

- **Knowledge belongs to the business, not to an agent.** One knowledge base
  shared by every agent the owner creates. Knowledge screens are reached from
  the Knowledge nav item and are never agent-scoped.
- An owner usually has one agent but can add more. The agent switcher is
  deliberately low-key (plain text button, not a prominent selector).

## Two modes: configuring and operating (noted 4 Sep, nothing built)

A manual payment-confirmation feature is coming — for sellers with no merchant
account. Not designed yet, but it changes assumptions in what exists.

**Every screen so far is the owner configuring the agent.** Sign-in,
onboarding, add knowledge, review, test, dashboard-as-status. Manual
confirmation is the owner **operating** it: money at stake, a customer waiting,
a decision that can't wait for a laptop. Different mode, different urgency,
probably a different place in the nav — Settings is where you configure, and an
orders surface is where you operate.

**Confirmation happens in Telegram, not here.** The seller is not at a desk at
9pm. The dashboard owns *history* and *the state of open orders*; it must not
try to own the decision.

**Forwarding unanswered questions (noted 4 Sep, nothing built).** When the
agent doesn't know and the customer is waiting, the bot asks the customer
*"shall I ask them?"* and on yes forwards the exchange to the owner in
Telegram. The owner replies **as themselves, visibly** — the customer sees that
a person answered, not the agent — and the reply goes both to the customer and
into the knowledge base. Confirmations and forwarded questions arrive in the
same Telegram chat and are kept visually distinct there; the dashboard's
treatment must be consistent with that distinction.

**"Needs your attention" has three item types and they are not equally urgent.**

| Item | Money | Someone waiting | Decays |
| --- | --- | --- | --- |
| Payment awaiting confirmation | yes | yes | yes — a deadline |
| Forwarded question | no | yes | yes — patience |
| Knowledge conflict | no | no | no |

**Ranked by who is waiting, expressed as elapsed time.** *Waiting 40 minutes*
is a fact; a red dot is a judgement, and elapsed time fits the honest-state
rule better. Money breaks the tie between the two waiting types, because an
unconfirmed payment has a deadline as well as a person.

Structurally that means the block is **not one list**: waiting items sit in
their own group above, each carrying its elapsed time and one action;
conflicts stay in the list below, unchanged and undated, because nothing is
decaying. The *"Nothing needs you"* empty state then means the waiting group is
absent, not that the whole card is.

**Gaps and escalations are different things and must not merge in the UI.**
A gap is *"add this when you get a chance"* — the dashboard's left column,
sorted by how many people asked. An escalation is *"someone asked and is still
waiting"* — an attention item with elapsed time. They **converge on the same
action**: answering an escalation writes the fact that closes the gap. So the
gap list must not silently absorb escalations (that would bury a waiting
customer in a backlog), but answering in either place should visibly resolve
both — one fact, two lists updated.

**Elapsed time needs a ceiling — decaying items expire into gaps.** A forwarded
question at *"waiting 3 days"* is not waiting; the customer has gone. Left
uncapped, the top of the list fills with dead items and stops meaning anything.
So a forwarded question stops being an escalation after **24 hours** and
becomes an ordinary gap: answering it still improves the agent, but nobody is
owed a reply, so it must not occupy urgent space. It moves to the gap list with
its history intact (*asked 2 days ago, nobody answered*) rather than vanishing.
Payments expire on their own deadline instead of a fixed window — that is a
real commercial fact, not a UI timeout — and an expired payment becomes a
history item, never an urgent one.

Rationale for a fixed 24h rather than a taper: an owner checking in twice a day
should never open the dashboard and find yesterday's escalations still claiming
to be urgent.

**"Nothing needs you" has two meanings now, and only one of them is true.**
With the group split, the waiting group can be empty while conflicts remain —
that is *nothing urgent*, not *nothing to do*. Copy must distinguish them:

- No waiting items, conflicts present → *"Nobody's waiting on you."* with the
  conflicts listed below as normal. Never the old phrase; it would be false in
  what becomes the common case.
- Nothing at all → *"Nothing needs you."* unchanged.

**The card's sizing assumptions are stale.** It was designed when items were
conflicts and disconnections — one lifetime, one urgency, so a flat five-item
cap and a single *see all* were adequate. Three types with different lifetimes
don't fit that: the waiting group must **never** be capped (hiding a waiting
customer behind *show more* is the failure the whole feature exists to
prevent), while conflicts — which decay not at all — should carry the cap
instead, since a long conflict backlog is a knowledge-hygiene queue rather than
a to-do list. So: waiting group uncapped and always fully shown; conflicts
capped at five with *See all N →*; the two counts stated separately, never
summed into one badge.

Consequence for the dashboard as built: the attention card is currently a flat,
implicitly equal-weight list with no elapsed time anywhere, a shared five-item
cap, and the *"Nothing needs you."* copy. The revision is well-defined —
elapsed time on waiting items, the group split, the expiry rule, the copy
change, and moving the cap onto the conflicts group only.

**Payment details are stored facts with different handling.** Card number,
cardholder name, bank, and editable instruction text per language. They belong
where facts are edited (screen E), but a card number rendered in a list beside
opening hours is wrong: mask by default, reveal deliberately, and keep it out of
provenance quotes and context windows — the source panel quotes surrounding
text verbatim, which must never surface a card number.

## Rules that bind every screen

0. **Provenance rule — check what is stored before drawing the line.** When a
   screen shows where something came from, verify the granularity actually
   exists in the data before designing the display. This has been violated
   twice: the review screen and the test console both drew *sheet «Услуги», row
   14* when there is no sheet concept, no spreadsheet ingestion path, and
   nothing anchoring a fact to a position inside its source.

   **What exists today:**
   - **Typed by the owner** — the common case, not the exception. 129 of 131
     facts have no file behind them, by design: owner-typed facts are confirmed
     on write. The honest provenance is *"You typed this"*, which is **stronger**
     than a filename, not weaker. Design for this case first.
   - **Extracted from a file** — source label and filename. **No sheet, no
     row, no page.**
   - **Answered from prose** — source label plus the passage quoted verbatim.

   **On the roadmap, tied to spreadsheet ingestion:** row-level provenance.
   Sheet-and-row becomes natural when Google Sheets sync exists. Not dropped,
   but do not design for it now.



1. **Honest state.** Nothing claims to be working when it isn't. The dashboard's
   three-part live check is the reference discipline: an agent is "answering"
   only when billing is active AND a channel is connected AND there is confirmed
   knowledge. Otherwise the UI names the specific thing that's wrong, with the
   fix inline. Explicitly rejected: the billing-only rule (would print "live"
   for an agent with no channel and no facts).
2. **Every fact has a source.** One click to see where it came from.
3. **Trilingual** — Uzbek (Latin), Russian, English. Russian strings run ~30%
   longer. No fixed-width labels, no text crammed into buttons. Cyrillic and
   Latin appear in the same table and the same field.
4. **Slow connections, mid-range Android.** No heavy animation, no shimmer, no
   charts. Real progress, retry, never lose the user's work.
5. **Not annotation work.** The owner reviews their info once and leaves.
   Data-labeling tools are an explicit non-reference.
6. **Internal vocabulary stays internal.** No "Path one / Path three" style
   labels in the UI. Headings say what a thing does.

## Visual system

**Superseded 2 Sep 2026 — see `design-direction.md`.** The project's visual
direction is now *Telegram-fluent utility* (white surfaces on a cool ground, one
saturated blue, rounded soft-shadowed cards, Manrope). Industry was dropped
after it read wrong on two screens; the full diagnosis and the token set live in
that file. Screens A, C and 0 were built on Industry and carry retrofit debt.

The **register split below is withdrawn** — there is one visual language now,
and the difference between onboarding and the ledger screens is density and
chrome rather than styling. Kept here only because the underlying distinction
still drives layout decisions:

- **Ledger register** — dashboard, review queue, knowledge base, gaps,
  conversations. The owner is inspecting a technical record. Hairline bands,
  plates, registration marks, condensed type, dense rows. Correct here.
- **Momentum register** — auth and onboarding. About reassurance and forward
  motion, not inspection. Still Industry (same tokens, type, accent, square
  corners), but **drop the ledger devices**: no multi-cell step band, no
  plate-and-tag stacking, no consequence-line-per-option. One question per
  screen, large type, generous space, minimal chrome. Progress in words
  ("Step 2 of 6") if shown at all.

That open risk resolved: one question on a near-empty page *did* still read as a
settings screen, which meant the problem was Industry rather than the
application of it. Hence the direction change.

## Known cross-screen gaps (not built)

- **Mobile breakpoints.** The 216px left rail does not become a bottom bar; at
  ~360px things get tight. Fix as we go, not as a later pass.
- **UZ/RU don't translate.** Switcher toggles state only. Full localisation is
  out of scope for now, but every layout must survive +30% Russian strings.
- **Everything is static.** No routing, no persistence, no API. Buttons like
  Back / Continue / Save and finish later are inert; there is no persistence
  behind the resumability promise.
- **A design-system defect, hit three times:** `.btn` sets a fixed height with
  `white-space: normal`, so long labels overflow or clip. Worked around inline
  on every screen so far with `flex: 0 0 auto; white-space: nowrap; height:
  auto`. Worth fixing once in the DS `.btn` / `.tag` rules.
- **Prose retrieval: VERIFIED 3 Sep 2026** — the claim on screens C, step 2 and
  step 3 holds, and for a stronger reason than the copy implies: a model
  proposes passages and anything not appearing verbatim in the source is
  discarded, so what is stored is exactly what the document said. Two risks
  remain and they are design's to carry: (a) **coverage is not reported** — a
  paragraph that was never proposed vanishes silently, and passages under 60
  characters drop, so no screen may imply a complete reading (no passage
  counts, no percentages); (b) **across languages "quotes" becomes "translates
  faithfully"**, because the agent answers in the customer's language. Full
  detail and candidate UI treatments in `prose-retrieval-check.md`.

## References

- **Structure** comes from MoonAI and ManyChat — onboarding step sequence,
  template gallery, channel connection flow, dashboard shell. This market
  recognises that shape.
- **Visual style does not.** Both are conventional SaaS; the look comes from
  Industry.
- **Explicitly not a reference:** data-labeling tools.

### What we take from ManyChat, and what we invert

Take:
- "Create New Bot" vs "Connect Existing Bot" as two explicitly labelled paths,
  with the three BotFather steps on the same screen as the token field, not
  behind a link. Their best screen.
- A reassurance line under any choice that feels heavy.

Invert:
- **Knowledge before channel.** Channel-first produces a live, empty, silent
  bot — theirs completed onboarding while the product didn't work. An agent
  that knows things but has no channel is inert and honest; an agent with a
  channel and no knowledge is live and useless.
- **Every question must configure something.** Their six profiling questions
  change nothing. Ours are cut if they're decoration.
- **Celebration goes at the first correct answer in the test console**, not at
  channel connection. That's the moment something real happened.

## Screen A — Dashboard home (built)

Hierarchy: are you working → is it worth it → what to do next → housekeeping.

1. **Status plate** — full-width, first thing seen. Three-part honest check.
   Big condensed headline, one plain-language sub, then Channels / Confirmed
   knowledge / Plan as small labelled facts.
2. **Four figures, hairline band, no charts** — questions answered, couldn't
   answer, handed to a human, and answered-without-you (largest, accented,
   rightmost — the figure that says the product works). Seven plain divs as a
   sparkline under it.
3. **Two columns, the actual dashboard.** Left: **What it doesn't know yet** —
   customers' own questions in their own words and scripts, most-asked first,
   each with **Add answer**. Right: **Needs your attention** — conflicts,
   disconnected channels, customers asking for a human, low-confidence facts.
   Empty state here is celebrated: *Nothing needs you.*
4. **Quiet footer** — last knowledge update, test link, invite link.

Revised 3 Sep, at the retrofit:

- **The three-part check is computed, not asserted.** `status(billing,
  channel, knowledge)` returns the pill, headline and inline fix, so every
  combination is exercisable rather than hand-written per state. Each failing
  combination names the specific problem and offers its own fix as the primary
  action.
- **The channel-but-no-knowledge state is the one onboarding deliberately
  allows** (step 2 is skippable) and it is *the state ManyChat shipped people
  into*: a live, silent bot. Ours names it — *"Not answering — it doesn't know
  anything yet… customers can write to @shifomed_bot but the agent has nothing
  to answer from and will say it doesn't know"* — and the primary becomes **Add
  what it should know**.
- **The first-visit line expires.** It shows only until the first real customer
  message; a dashboard still saying *"you're set up"* on day five is stale
  copy on the one element whose whole job is being the honest state of things.
  Same trigger as the card below.
- **"What else should it do?" lives here, not in onboarding**, and appears only
  after a **real customer conversation** — not after the owner's own test
  message or our channel self-test, neither of which is real. First visit is
  exactly when the owner has least idea what they want next, so the card would
  be decoration there. Copy is honest that nothing is built and that the answers
  decide what gets built.
- **Every count derives from the gap list**, not from hardcoded strings.

Settled specifics:
- **The one primary action is contextual.** Populated: *Answer 41 open
  questions*. At zero gaps it becomes *Test your agent* (promoted from the
  footer) — never a disabled button, because an empty state we celebrate on the
  right must not produce a dead control on the left. Empty account: *Create AI
  agent*.
- **"Add answer" opens an inline composer in the row.** It must never become a
  route to the Gaps screen. That loop — closing a gap in one place, in under a
  minute, on a phone — is the whole reason this dashboard earns a return visit.
- "Needs your attention" contains: conversations where the agent said it didn't
  know, conflicting facts, disconnected channels, customers asking for a human.
- Operator variant is the same layout, denser: client-account list with
  per-account gap counts in the rail, `j`/`k` to move, `Enter` to answer.
- States: populated, starved (channel live but too few facts), empty, loading
  (hairline skeletons, no shimmer), error (block degrades in place; the status
  plate never errors — it shows last known state with a timestamp).

## Screen B — Onboarding

### Sequence (revised 2 Sep, now six steps)

0. Sign up / sign in
1. Business type, plus one line on what the agent does today
2. Add knowledge — **reuse screen C in its first-run posture**, not a new design
3. Review what was read
4. Test — talk to the agent before anyone can reach it. **Celebration here.**
5. Connect Telegram
6. Live

### Decisions

- **Step 2 "what should it do?" was cut.** With one live option checked by
  default it was a demand-signal survey wearing a configuration step's clothes.
  The three unbuilt intents (bookings, payments in chat, feedback) move to a
  "What else should it do?" card on the dashboard, shown only after the agent
  has answered something real — an owner who has seen it work knows better what
  they want next. Removes a screen between the owner and a working agent.
- **The five business types are not four plus an escape hatch.** Each of the
  four named types carries its own vocabulary and example set: clinic (doctors,
  specialties, consultation ranges), salon (stylists, services, cut/colour
  ranges), tutoring centre (tutors, subjects, course fees, class times),
  **service business** (call-out cost, areas covered, how soon you can come —
  a real category, not a synonym for "other"). *Something else* is the only
  free-text route and it asks for a word, because there is nothing to configure
  from an unnamed category.
- **An unnamed "something else" states its own cost.** `Continue` stays live
  with the name field empty, but the line under the field says what the owner
  will get instead of nothing: general examples and the generic noun
  "customers". A silent fallback would make the question decorative, which is
  the failure we cut the old step 2 for.
- **Business type must configure things** — pre-fills example facts, shapes the
  extraction prompt, decides what the test console suggests trying, and changes
  the customer noun (patient / client / parent / customer) in copy. If it were
  decoration it would be cut.
- **No channel picker.** ManyChat's picker makes sense at seven channels; we
  have Telegram only, and Instagram is unbuilt and waiting on Meta review. A
  one-option picker is the same failure as the old step 2. Go straight to
  Telegram connect with one honest line: *Instagram is coming; Telegram is what
  works today.*
- **Onboarding chrome:** no left rail (it invites wandering off before the
  agent works), a *Save and finish later* link, and the resumability promise
  stated in words. Abandonment mid-onboarding is the expected case, not the
  failure case. The rail returns at step 6.
- **Auth:** email plus one-time link, no password. Social order is Telegram
  first and heaviest (near-universal in this market — the one account we can
  assume), Google second, Apple last (low share here). One screen serves both
  sign-up and sign-in, **opening in sign-up** — the visitor ratio is almost
  entirely new right now, and returning owners come back via Telegram, which is
  one tap in either mode. Revisit if the ratio flips. Trilingual from this
  screen onward.
- **Telegram login and connecting the bot are different things** and the copy
  must not merge them. Login is an identity handshake; connecting is pasting a
  BotFather token. The reassurance line is *"The account you already have"* —
  never a claim that it saves connecting Telegram later, which would leave the
  owner feeling misled when they hit BotFather at step 5.
- **No spinners anywhere.** Buttons state what they're doing in their own
  labels (*Sending…*, *Saving…*). Consistent across every screen; keep it.

### Open non-design question

- **Consent under Uzbek personal-data law.** The auth screen uses implied
  consent by continuing, with Terms and Privacy links and no checkbox. Whether
  that satisfies local personal-data legislation is unverified — a legal
  question, to be answered before launch rather than at launch.

## Step 3 — Review (accepted by default)

Built as `Onboarding Review.dc.html`.

**The premise:** at forty facts, confirming each one is forty taps that all say
yes. So facts arrive **accepted**, and the owner's job is to find the wrong
ones — *"42 facts read. Three need you."* This is what keeps the screen from
being annotation work, which the brief rules out.

Accepted cost, and the mitigations: facts enter the knowledge base without an
explicit per-fact yes, so (a) nothing is answerable until the test step, where
the owner sees it work before any customer does, (b) every row is editable and
removable in place, and (c) removal never disappears mid-session — the row greys
and struck-through with Undo.

**The source panel is the load-bearing element and survives at every volume.**
Second column at desktop width, directly under the fact on a phone. Never a
tooltip, never a modal, never an expand.

**Context substitutes for coordinates (revised 3 Sep).** There is no row-,
sheet- or page-level anchoring in the data, and a bare quote with a filename is
weaker evidence than the original design assumed — an owner shown *250 000*
from a photo with no idea which line can't judge it. The fix is not a locator
we don't have, it is **more text**: a wider verbatim window with the extracted
span emphasised inside it. *"Here's the text it came from"* rather than
*"here's where it came from"* — and it is better evidence anyway, because a
locator tells you where to look while surrounding sentences let you judge
without looking.

The case that proves it: an extraction that **dropped a negation**. The source
says results are *not* given by phone; the extracted fact says they are. No
confidence score reveals that. The surrounding text reveals it instantly. That
row is now the screen's low-confidence example.

**Owner-typed facts are not listed on this screen at all.** They were confirmed
on write (the asymmetry established in screen C), so listing them for review
contradicts it. One line accounts for them instead: *"The 31 facts you typed
yourself aren't listed here — you wrote them, so there was never anything to
check."*

**The screen is far smaller than it was designed for.** Reality is roughly 129
of 131 facts typed, so a real session has a handful of extracted facts, not
dozens — and *"nothing needs a decision"* is the **common** state, not the
exception. Consequence worth deciding: in the common path this step may deserve
to be skipped entirely rather than shown as a near-empty screen. Not acted on;
it would take the sequence to five steps.

**Deliberately not carried over from a bulk review design:** grouping by
document, collapse-as-you-go, per-row checkboxes, bulk select. At a few dozen
facts they make a five-minute task look like an afternoon's. The only grouping
is three subject subheads (Prices / Hours and days / Doctors and services),
which help a person scan and do nothing else.

**Three shapes of decision**, each answerable in one tap: a conflict asked as
one question with provenance on both options (*which is current*), a
low-confidence extraction with Keep / Fix / Remove, and an orphaned value the
extractor read but couldn't place (*примерно 320 000 сум — for what?*).

**Volume cap is a rule, not a control.** The mock lists what it claims — 12
facts, 9 of them already kept, provenance visible on every one — because a
count the screen can't demonstrate undermines the provenance promise. If a real
extraction runs long (over ~60), cap the accepted list and add a working
*Show the remaining N* reveal; do not ship the affordance before it reveals
anything.

**The nothing-extracted state renders nothing else.** No accepted list, no
policy row, no Continue — a screen that says "nothing to check" and then shows
rows and a "continue with N facts" button is exactly the dishonest state the
project forbids. That view offers only the two recovery routes back to step 2.

Celebration does **not** go here. A resolved block says *All three sorted.* and
nothing more; the moment belongs at the first correct answer in the test console.

## Step 4 — Test console

Built as `Onboarding Test.dc.html`. The first moment the product visibly
works, and better placed than ManyChat's Playground because by now the agent
answers from the owner's own price list rather than demoing nothing.

**Celebration goes at the first answer the owner marks RIGHT**, not at the
first answer received and never at channel connection. It is one quiet line —
*"That's your agent working. It answered from your own price list."* — plus the
primary turning into *Connect Telegram →*. No confetti, no animation; the
restraint is the brand and the device is a mid-range Android.

**Every answer carries its "why" as one collapsed line**, in the same source-
panel vocabulary as the review screen: *Answered from Narxlar_2026.xlsx · sheet
«Услуги», row 14 →*, expanding to the verbatim passage and its locator. Not a
debug panel — no scores, no vector dump.

**The cross-language treatment lands here** (from the prose-retrieval
verification): where the retrieved passage's language differs from the reply's,
the line reads *Translated from Uzbek · Siyosat_va_qoidalar.docx →* and the
expanded view shows the Uzbek original above a note that the reply is a
faithful translation, not a quote. The owner learns the mechanism by watching
it happen once, which is cheaper and more honest than a caveat.

**Used-detection is "appears in", not "was used" — and the copy says so.** The
backend check is mechanical: a fact whose value appears in the answer text was
demonstrably used. Verifiable, but not the same claim. It **under-reports**
(the model reformats — *"250 000 сум"* stored, *"примерно 250 тысяч"* in the
reply — and a genuinely used fact reads as unused) and **over-reports** (two
doctors both at 150 000 both match when only one was meant). So the panel says
*"Appears in the answer"* and *"Its value appears word for word in the reply.
That is what we can check — not whether the agent leaned on it."* Never *"the
agent used this"*.

**Retrieved context is shown, not hidden, and it is voluminous.** ~23 facts can
reach the prompt for a one-fact answer. Treatment: the matched set is boxed and
accented at the top; everything else is a quiet unstyled list under *"Also read
while answering · N facts"*, three shown with a working *Show all N* reveal. The
rest is available without competing.

**Cross-script detection is unavailable, and that state is equally normal.**
When the passage is Uzbek and the answer Russian, text matching scores zero —
not because nothing was used, but because the check cannot work across scripts
(`used_detection: "unavailable_cross_script"`). An Uzbek policy answering a
Russian-speaking customer is the normal situation in this market, so the screen
must never render it as "0 sources" or as nothing used. Treatment: **no
markings at all**, the full retrieved list unmarked, and one plain line —
*"You wrote in Russian and the passage is in Uzbek, so there is no matching
text to find."*

**The unavailable state and the translated-provenance state are one state**,
not two. Both are caused by rule 5 (answer in the customer's language), so they
are unified deliberately — splitting them would let the two explanations
contradict each other. The unavailable note closes by restating the quote
caveat: *"the reply is a faithful translation of the passage, not a quote from
it."*

**A wrong answer is productive, not discouraging.** *Wrong* opens an inline
composer pre-filled against that question — same loop as the dashboard's gap
list — and the button reads *Save and ask again*. *I don't know that yet* does
the same without needing a verdict.

**The primary is never gated into a dead end.** Before a first *Right* it is a
quiet outlined *Skip testing and connect Telegram*; after, it becomes the solid
*Connect Telegram →*. Gating entirely would trap an owner whose extraction was
poor.

**Skipped step 2 produces an honest refusal**, not a demo of an empty agent:
*"There's nothing for it to answer from yet"* with **Add something first**.
Letting them test an empty agent would teach them the product doesn't work.

## Step 5 — Connect Telegram

Built as `Onboarding Telegram.dc.html`. The step people abandon, because the
owner has to leave the product, talk to BotFather, and come back with a string.
Everything here serves that round trip.

**Taken from ManyChat, their best screen:** two explicitly labelled paths
(*Create a new bot* / *Connect a bot you already have*) with the three BotFather
steps **on the same screen as the token field** — never behind a link or modal.
Paths are rows, not tabs: a tab hides the other path, and an owner who picks
wrong has to discover the tab exists.

**No channel picker**, per the earlier decision. One honest line instead:
*Instagram is coming; Telegram is what works today.*

**The copy pre-empts the failure everybody hits:** the username must end in
"bot", with a suggested name from the business and a fallback if it's taken
(*shifomed_chilonzor_bot*). Step 3 shows what the code looks like
(`8123456789:AAF…`) so the owner knows what to hunt for in a wall of Telegram
text. **Open BotFather** is a real button — it is what makes step 1 not a chore.

**The token field is `type="text"`, not a password.** Masking a string the
owner needs to verify they pasted correctly is hostile. On a bad or in-use
token the pasted value is **kept**, never cleared.

**A live self-test is the point of this screen** (added, not in the brief — the
direct answer to ManyChat's failure, where onboarding completed while the
product didn't work). On success it does not celebrate — that already happened
in step 4. It sends a real message through Telegram and shows the exchange
verbatim: *We sent* / *It answered*, plus *"not a simulation"*. That is the
proof the connection isn't a dead pipe.

**A failed self-test does not advance to Live**, and says why: *"We won't take
you live while the bot isn't answering — a live agent that says nothing is worse
than one that isn't connected yet."* It names the likeliest cause (another
service still holding the bot's updates) and offers a retry plus a way to leave
it connected and finish later. This is the honest-state rule at its most
load-bearing: this is the exact screen where the reference product lied.

## Screen C — Add knowledge

See `design-add-knowledge.md`.

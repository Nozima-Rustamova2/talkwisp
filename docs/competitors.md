# Competitors

Teardowns of other products in this space. **Nothing in this file is a
decision.** It records what other people built, what they left out, and what we
might learn from either — patterns to consider and absences worth knowing about.

Anything adopted from here becomes a decision in `docs/design-decisions.md`,
with its own reasoning stated there. Do not cite this file as authority for a
design.

Each teardown states its provenance and the limits of that provenance. A
walkthrough of a free plan is not the same evidence as a paid account, and the
difference matters when the finding is "they don't do X".

> The ManyChat section below is duplicated in `docs/design-decisions.md`, where
> it was first filed. **This file is the canonical copy** — future teardowns go
> here, and if the two ever disagree, this one is right.

## Competitor findings — ManyChat teardown, 1 Sep 2026

**These are not decisions.** Nothing here is settled, and nothing here has been
chosen. They are patterns worth considering and absences worth knowing about.
Anything adopted from this section becomes a decision somewhere above, with its
own reasoning — do not cite this section as authority for a design.

**Provenance:** a live walkthrough on ManyChat's free plan with Telegram
connected. Instagram surfaces and the paid panels were **not exercised
first-hand**, so anything about them is inference from the UI and their docs,
not observation. Weight it accordingly.

### Patterns worth taking (not yet decided)

- **Billing slider that is simultaneously the price selector and the spend
  ceiling** — you choose your contact cap and watch the price move. This is a
  direct answer to "what will this cost me next month", which our strategy doc
  names as the thing that kills SMB deals here. Also: overage caps with advance
  notification, and upgrading mid-month waives that period's overage.
- **Human-takeover pause** — when an agent replies manually, that contact's
  automation pauses 30 minutes automatically, with an explicit control offering
  30min / 1h / 3h / 6h / 12h / 1 day / forever. The agent never has to remember
  to switch the bot back on.
- **Reply variation rotation** — author several phrasings, rotate randomly.
  Matches the "5-10 phrasings per tone bucket" note already in
  `strategy-architecture.md`, now confirmed in production use.
- **Wait-for-reply mechanics** — custom retry message, configurable max retry
  count, then expiry and fall-through, plus a wait-duration limit with a
  non-response action. A shape to copy when we build conversational state.
- **Templates install as DRAFT** with a guided setup pass that drops the owner
  straight into confirming one setting. Nothing goes live until published.
- For an agency tier later: **protected templates with per-installation
  revoke**.

### Validated absences — things the market leader does not do

- **No log of customer questions the AI could not answer. None.** Their only
  gap detection is a pre-launch sandbox for questions the owner types himself.
  Our gap list has no counterpart in their product.
- **No file upload to the knowledge base** — text and URL only. No PDF, no
  spreadsheet, no photo. Our vision extraction has no counterpart.
- **No "answer only from knowledge" constraint.** Their guardrails are prose,
  not a retrieval boundary. Our `NO_ANSWER` code branch is a stronger guarantee
  than the incumbent offers.
- **No confidence scores, no review queue, no per-message approval.**
- **Onboarding never asks what the business is.**
- **No automation health on their home screen.** During the walkthrough a real
  person messaged the bot and received nothing, because both automations were
  draft/stopped, and nothing in the UI flagged it. That is the exact failure our
  three-part status plate prevents.

### Two findings that change our thinking

- **Our language moat is thinner at the LLM layer than assumed.** Their AI
  answered a fluent Uzbek question with zero config and an empty knowledge base.
  The moat is the product around the model — alias matching across scripts,
  script-correct replies, trilingual UI — not the model. Their UI ships in
  exactly three languages (en-US, pt-BR, es-MX); no Russian, no Uzbek. Keyword
  matching against non-Latin scripts is undocumented. Multi-language AI is an
  unanswered feature request in their own community.
- **Telegram is wide open.** Their AI product is Instagram-only and
  self-labelled beta. A Telegram account gets only the generic paid AI step
  inside a flow, and Telegram has two triggers to Instagram's seven. Our primary
  channel is the one they have barely built for.

### Reassurance, recorded so we do not over-engineer

They have no version history, no audit trail, no warning when editing a live
flow, and no documentation on what happens to conversations mid-flight. Their
execution model appears to be one mutable live graph with each contact as a
cursor on a node. We flagged versioned publish as a hard requirement; the market
leader has not solved it in a decade. Not unimportant, but not the barrier to
entry we assumed.

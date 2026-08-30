# Talkwisp — strategy & architecture

Background for anyone (human or model) working on this repo.
`design-decisions.md` outranks this document on any conflict.

---

## 1. What we're building

An AI agent platform for businesses in Uzbekistan. A business connects Telegram
and Instagram, gives us their information, and the agent answers the questions
customers ask over and over.

Reference customer: a clinic, asked constantly about opening hours, which doctors
work there and their specialties, and roughly what a visit costs. Also salons,
tutoring centres, service businesses.

Not our customer: grocery or high-SKU retail. We are not building a product
catalog. Costs are approximate ranges, not exact prices.

---

## 2. Competitive position

**MoonAI** (mooonai.com) — Kazakhstan-operated, no-code AI sales agents over
Gemini/OpenAI/DeepSeek. Instagram, Telegram, WhatsApp, web chat. $50/mo per agent
plus tokens billed separately. Their strongest feature is payment collection
inside the dialogue (Kaspi invoices). Backed by Astana Hub, IT Park Uzbekistan,
Most HUB Almaty.

**ManyChat** — the global incumbent. Official Meta and TikTok partner, ~$163M
raised. Priced per active contact: $14/250 up to $139/25,000.

**The pricing gap we exploit.** ManyChat's per-contact model punishes success — a
viral post spikes the bill. MoonAI's seat-plus-tokens model is unpredictable, and
an SMB owner who can't answer "what will this cost next month" doesn't buy.
Our answer: flat tier, generous included conversation count, visible counter,
local currency, local payment rails. Predictability beats optimality here.

---

## 3. Where defensibility is

**Not moats:** multi-LLM support, sentiment analysis, "no-code", 24/7 replies.
Table stakes. Nobody pays extra.

**Real moats:**

1. **Channel access.** Instagram Messaging API requires a Meta app, business
   verification, App Review, and a 24-hour messaging window. Slow and
   unpredictable — treat approval timing as schedule risk and start early.
   Telegram is a bot token and an afternoon, but users must `/start` you.

2. **Local payment rails.** Payme, Click, Uzum, Uzcard, Humo. Whoever closes the
   loop from "customer asks a price" to "paid" without leaving the chat wins the
   SMB. Global players won't do this integration work for this market.

3. **Language quality.** GPT and Gemini are mediocre at Latin-script Uzbek
   code-switched with Russian, which is how people actually type. Uzbek and
   Russian also tokenize at roughly 2–3× English per character, so cost models
   built on English benchmarks are wrong. An eval set of real Uzbek/Russian
   customer dialogues is an asset nobody can scrape.

4. **The data loop** — objection patterns, question clusters. Compounds, but only
   above a few hundred customers. Not a year-one moat.

---

## 4. Buyers

**Owner** — non-technical, phone or cheap laptop, opens the app twice a week for
under a minute. Design for this person.

**Agency operator** — runs several client accounts on desktop, wants density and
keyboard access. Same screens, denser. An agency's clients are separate tenants,
not separate agents.

Multiple agents means one business on multiple channels. Knowledge is shared
across them.

---

## 5. Architecture

### Knowledge layer
Four tables: `source`, `fact`, `alias`, `chunk`. Free-form subject and attribute
strings. Facts answer "which doctors do cardiology" and "when are you open";
chunks answer "what should I bring to the appointment". See
`design-decisions.md` for the binding rules.

### Retrieval
Normalize question → alias match → facts → vector search chunks → if both miss,
say so and log a gap. The agent never answers from model knowledge.

Use hybrid search, not pure dense. Uzbek is agglutinative and Latin/Cyrillic
mixed; BM25 catches exact-token cases dense embeddings miss. Test candidate
multilingual embedding models against our own Uzbek/Russian query set —
published benchmarks won't tell us what we need.

### Aliases
Load-bearing. Every subject needs alternates across languages and scripts, plus
common misspellings. Without them, lookup silently misses and the model invents
an answer.

### Comment handling (later)
Public Instagram comments route through a classifier before any LLM: spam filter,
intent and tone, confidence score. High-confidence known intents get a static
reply from a pool of variants; specifics go to the LLM with retrieval; anything
negative or ambiguous escalates. A viral post produces thousands of low-value
comments — paying for inference on emoji spam burns money exactly when volume
spikes. Static replies also can't hallucinate a price or be jailbroken by a
comment.

Identical repeated replies read as spam to both Meta and humans, so every tone
bucket needs 5–10 phrasings. And most public threads shouldn't be conversations:
the valuable move is comment triggers a DM.

### Execution engine (later)
If we build a visual flow builder, the n8n runtime model does not fit chat.
Conversations are long-lived, stateful and suspended most of the time. Needs
durable per-contact state, a suspend/resume primitive, message debouncing, and
versioned publish so in-flight conversations finish on the version they started
on.

---

## 6. Data input

One bulk-import path, three edit surfaces. All writes go through the same
validation; the surface is only UI.

| Surface | Job | Frequency |
|---|---|---|
| Upload / paste | Bulk load at onboarding | Rare |
| Dashboard | Review and edit | Ongoing |
| Telegram | Single-fact correction | Weekly |
| Google Sheets | Bulk ongoing edit | Agencies |

**Photographed price lists are the real input.** Half of what a business gives us
is a photo of a printed list or an Instagram highlight screenshot. Budget for a
vision model reading images directly rather than OCR-then-parse.

**Google Sheets specifics:** use the Picker with `drive.file` scope — broad scopes
trigger Google verification and a third-party security assessment. Create the
file in their Drive, not ours. Hidden protected ID column so renames stay renames.
Keep it optional: an English OAuth consent screen at minute two of onboarding is
a real drop-off point.

**Telegram edits** confirm before writing, always. Ambiguous match means ask
which, never guess.

---

## 7. Economics

Inference is cheap — a short dialogue with retrieved context is low single-digit
cents on a small model. Higher for Uzbek because of tokenization. **Inference is
not the cost problem; onboarding labour is.**

The number that decides whether we have a business is **retention past month 3**.
SMB chatbot churn is savage: sign up, half-configure, see nothing, cancel.
Onboarding is the product, not a cost centre.

---

## 8. Compliance

Meta's Platform Terms restrict what may be stored from Instagram data and for how
long. We get comments and DMs, not viewer demographics — "learning customer
behaviour" means behaviour in conversations with us, not audience profiling.
Check current terms before architecting anything that retains Instagram data.

---

## 9. Sequencing

1. Knowledge layer — current phase
2. Telegram-only, one business type, ~10 hand-held customers
3. Instagram in parallel, starting early because Meta review is slow
4. Payment-in-chat
5. Gap intelligence built from an accumulated corpus
6. Agency channel once the product survives without us in the room

---

## 10. Not now

Multi-LLM as a headline feature. Freeform flow canvas. Analytics dashboards.
Graph visualisation. Long-document RAG beyond whole-chunk quoting. Per-vertical
schemas — see `design-decisions.md`.

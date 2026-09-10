# Design decisions

Settled decisions only. If something isn't here, it isn't decided — ask, don't assume.
Append as we go. Never expand a decision beyond what it says.

---

## Customer

- Businesses automating repetitive questions. Reference case: a clinic asked about
  opening hours, which doctors work there and their specialties, and roughly what
  a visit costs.
- Not grocery, not high-SKU retail. This is not a product catalog.
- Costs are approximate ranges ("200 000–300 000 UZS"), never exact prices.
  No price-precision logic anywhere.

## Scope

- **No per-vertical schemas.** Subject and attribute are free-form strings.
  No entity type registry, no vertical key, no per-vertical config.
- **Four tables:** source, fact, alias, chunk. A fifth table needs my approval.
  `schema_migrations` is the migration runner's own ledger — approved, and
  outside this rule. It holds no business data. The rule still binds the
  knowledge model.
- One vertical's worth of complexity, single-tenant-shaped. Don't build for
  multi-vertical.

## Knowledge model

- **Knowledge belongs to the business, not to an agent.** One business, many
  agents, one shared knowledge base. Nothing is scoped to an agent.
- **Typed facts are confirmed on write.** The owner is the source; they never
  enter review.
- **Extracted facts are unconfirmed.** They require review before the agent uses
  them. The schema distinguishes the two.
- **Prose stays whole.** Document prose becomes chunks the agent quotes, not
  fragments it splits.
- **Extraction is one transaction per file.** A failed file leaves nothing behind
  and doesn't affect other files.

## Retrieval

- Order: normalize question → alias / exact match → vector search over **facts
  and chunks** → NO_ANSWER + logged gap. Exact matching stays first: it is
  cheaper, more precise, and it is the language moat. Vector search is the
  fallback, never the replacement.
- **But "fallback" was implemented as "terminal", and that is a bug in the
  order, not in the code that implements it.** Exact match returning a hit ends
  retrieval, so a multi-part question is answered on whichever clause matched
  first and the rest is silently dropped. Observed live:
  "Yakshanbayam ochiqmisila? Ozi qatda joylashgansila, mojal bormi?" matched on
  Sunday hours, returned `ok`, and **told a customer it did not know its own
  address** — a fact it holds. Encoded as `live-sunday-address-landmark`.
  The failure is invisible by construction: `status: ok` with a real answer to
  a real clause looks like success at every layer, including grading.
  **The fix is to stop treating exact match as terminal — run both paths and
  merge the results — not to make exact match smarter.** A question with one
  clause loses nothing by also running the vector path; a question with three
  gains the other two.
- **Facts are embedded as `subject / attribute / value`**, not as the bare value.
  A Russian question about opening hours matches "Shifo Med / ish vaqti /
  Dushanba-Shanba 09:00-18:00"; it does not match "Dushanba-Shanba 09:00-18:00".
- **List questions are answered by expanding on the attribute, not by ranking.**
  When 3 or more retrieved facts share an `attribute_key`, every confirmed fact
  with that attribute is added to the context. Top-k by similarity can never
  answer "all of them", at any window size, because ranking is not enumerating:
  asked "doktorlar listini beraszmi", the bot named **two of six doctors,
  confidently**. There is no list-question *classifier* — the shape of the
  retrieved result is the signal, so it works the same in any language with no
  phrasing list to maintain.
- **The vector window is 8 facts, not 3.** A 3-wide window was the root cause of
  two live failures on the same day: "uzi qachon ochiq boladi" retrieved three
  UZI *prices* and refused, while the fact that answered it sat at rank 5.
  Widening is only safe because the prompt makes the model refuse on
  merely-adjacent context — without that rule, a wider window means more
  material to build a confident wrong answer from.
- **The similarity floor (0.55) is a cost pre-filter, not a correctness gate.**
  Refusal is a `NO_ANSWER` marker returned by the model, converted in code to
  `status: unknown` and a logged gap. The floor only limits how much context
  reaches the prompt.
- **It is set low on purpose, and the asymmetry is the reason:** a false
  positive is caught downstream by `NO_ANSWER`; a false negative is discarded
  before the model ever sees it, and there is no second chance. Recall is
  unrecoverable, noise is not.
  Evidence, measured on the 25-question set at the old 0.65 floor: an
  *unanswerable* question was **admitted at 0.691**, while an *answerable* one
  ("Где вы находитесь?" → `Shifo Med / manzil`) was **rejected at 0.644**. The
  floor errs in both directions — it cannot be the gate at any value.
  **Do not raise it to "reduce noise."** That reintroduces the exact bug. If
  marginal context is producing plausible wrong answers, the fix is the prompt
  and the `NO_ANSWER` behaviour, not the threshold.
  Lowering the floor puts more weight on `NO_ANSWER` working, so the
  unanswerable questions in `questions.py` are now the load-bearing tests.
- **The model is pinned in `.env` (`GEMINI_MODEL`) and swapping it is a release,
  not a config tweak.** A swap requires a full `check_answer.py` run before it
  ships. Same risk class as the normalization function.

  Why, from one day of evidence:

  - **Refusal behaviour is model-specific and fragile in both directions.** On
    an identical prompt and identical context, `gemini-3.5-flash-lite` returned
    `NO_ANSWER` to "Endokrinolog kim?" while the context held
    `Karimov Bobur / lavozim: endokrinolog`. `gemini-3.5-flash` answered it.
    Nothing about the system had changed. A weak model fails *safe* here
    (refuses when it should answer), but that is still a bot that looks like it
    knows nothing.
  - **The provider retires models under you.** `gemini-2.5-flash` returned 404
    with "no longer available to new users" — it was never usable on this key,
    though it is still listed by the models endpoint.
  - **Free-tier daily quota is small enough to exhaust by testing.** Three
    models were pinned in two days: `gemini-3.6-flash` and `gemini-3.5-flash`
    both hit `GenerateRequestsPerDayPerProjectPerModel-FreeTier` during
    development. One full test-set run costs ~30 generations plus ~30
    embeddings. **Running out mid-demo lands you on a model you have not
    validated** — which, given the point above, is a behaviour change, not a
    slowdown. Billing is the mitigation; the fallback is to re-run the test set
    on whatever model you land on before trusting it.
  - Because a swap changes behaviour, `check_answer.py` writes `results.json`
    and `regrade.py` re-scores it for free. **Change the answer path → full run.
    Change only the grading → `regrade.py`.**
- **The reply language is detected in code and stated in the prompt, never
  inferred by the model.** Every prompt opens with a `REPLY IN: ...` line.
  Reason: asked to "match the customer's language", the model copied the
  language of the *retrieved context* instead — a Russian question about opening
  hours was answered in Uzbek Latin because the fact was stored in Uzbek. An
  instruction the model must infer is one it will drift from; a stated fact it
  obeys.
- **Language detection matches WHOLE WORDS, never substrings.** The first
  version tested Uzbek marker words with `in`, and "ва" sits inside "вас" — so
  "У вас есть невролог?", about the most ordinary Russian phrasing there is,
  was classified as Uzbek and answered in Cyrillic Uzbek. Two-letter markers
  are dropped entirely: too little signal to be worth the collision risk.
  This is the visible defect class — a customer sees a wrong-language reply
  instantly, where they would never notice a threshold being wrong.
  The detector was rewritten on 2026-09-01 for a second, larger blind spot;
  the full account is under **Language detection — rewritten 2026-09-01**.
- **The agent never answers without retrieved context.** No fallback to model
  knowledge.
- **One normalization function**, applied identically to stored aliases and
  incoming queries. Folds Uzbek apostrophe variants (o'zbek / oʻzbek / o'zbek),
  unifies scripts, strips accents, lowercases.

## UI

- **Reject means the extraction is wrong**, not that the item left the catalogue.
  Removing a confirmed thing is a different action, on a different screen.
- Typed fact entry is **one free-text line**, parsed and shown split for
  confirmation in place. Structured fields are an escape hatch, not the default.
- Confirm is **entity-level**, not field-level. Provenance stays per-field in the
  database.
- Trilingual: Uzbek (Latin and Cyrillic), Russian, English. Russian strings run
  ~30% longer. Mixed scripts appear in the same field and the same list.
- Mid-range Android and slow connections are the normal case.

## Stack

- FastAPI, PostgreSQL + pgvector.
- **No Docker for the app.** Postgres + pgvector run in a container
  (pgvector/pgvector image); FastAPI runs on the host against it.
- **Raw SQL, no ORM.** `psycopg` for the driver; queries are written as SQL.
  Migrations are hand-written SQL files, not generated.
- No auth until the Telegram step. No tests unless asked.

---

## Reversed — do not reintroduce

These were considered and dropped. If a doc or an earlier file still implies them,
this file wins.

- Per-vertical entity schemas, vertical registry, `variant_axes`, per-vertical
  seeding rules.
- Store / e-commerce as the first vertical.
- Exact-price machinery: sanity ranges, prices-never-auto-confirm, price columns.
- Per-agent knowledge bases.
- A per-fact language tag in the UI.

---

## Open — decide when we get there, don't pre-solve

- What happens when the agent is wrong: confidence thresholds, when it refuses,
  when it hands to a human. Decide after seeing real retrieval behaviour.
- Resumable / chunked upload. The design shows it; the backend doesn't do it yet.
- **A harness that passes everything has stopped discriminating.** `questions.py`
  sat at 30/30 and a green run could no longer tell you a change made things
  worse. Every one of the three real bugs found on 2026-08-29 — the two-of-six
  doctor list, the too-narrow retrieval window, and the "ва" inside "вас"
  language misdetection — came from **live Telegram messages, not the harness**.
  Harvesting `messages.jsonl` into real cases fixed it: the set is now 46
  questions at **43 pass / 2 fail / 1 manual**.
- **It discriminates again because the known-broken cases are graded as
  failures, not excluded.** `live-how-to-book` (an inferred procedure) and
  `live-sunday-address-landmark` (the multi-part short-circuit above) are in the
  set and expected to fail. An excluded failure is a failure you stop seeing;
  an encoded one is a failure that reports when it is fixed, and reports again
  if it comes back.
  **So: add failures to the harness as they are found, not only passes.** The
  set's value is the ratio of cases that can still go red, and a set grown only
  by adding passes decays back to 30/30 no matter how large it gets.
- **Attribute reuse by prompting does not scale.** A typed line is parsed with
  every existing attribute and subject name in the prompt, so the model reuses
  `narx` instead of inventing `price` — which matters because a split attribute
  makes conflict detection blind. Fine at 33 facts; impossible at 500. The fix
  when it bites is to retrieve the *closest* existing attribute names by
  embedding and send only those, not the whole list.
- **Attribute reuse can also be wrong**, by the same mechanism that makes it
  right: a genuinely new attribute gets mapped onto an existing name that is
  merely similar. This is why typed facts are parse-then-confirm, and why the
  confirmation must show the **attribute**, not just the value — the value is
  the part the owner already knows; the attribute is the part being guessed.
- **Extraction review is designed for "is what it got correct", not "did it get
  everything".** The review screen shows the owner what was proposed; nothing in
  it can surface what was never proposed. A missed fact is therefore invisible,
  and omission is the failure mode nobody catches.
  Observed: a note reading "Umumiy qon tahlili 60 000-90 000 so'm, biokimyoviy
  tahlil 120 000-150 000 so'm" produced one price. Two prices in one sentence,
  one extracted.
  A prompt instruction to re-read for completeness fixed *this* case, but
  prompting is not the structural answer. **The structural answer is a
  completeness check after extraction:** scan the source text for numbers,
  prices and names that appear in no extracted fact, and surface those as a gap
  for the owner. Not built. Build it before extraction is trusted on real
  documents.
- ~~Multi-tenancy.~~ **Done, 2026-09-07** — see "Multi-tenancy is enforced below
  the query layer" below. The prediction that it was "a known migration — add a
  column, backfill one value, extend the indexes" was right about the shape and
  wrong about the cost: the column was the easy half, and the two global unique
  constraints and the three ways RLS silently does nothing were the rest.
- **MULTI-TENANCY IS CORRECT IN THE DATABASE AND UNUSABLE IN PRACTICE.** This
  is a blocker, not a cleanup. The moment a second `business` row exists,
  `app_sole_business()` raises — and nine scripts call `connection()` with no
  argument, so they all break:

      seed.py          probe_embed.py    check_answer.py   check_buy.py
      check_drift.py   check_orders.py   check_payment.py  check_retrieval.py
      check_window.py

  The web app and the bot are fine: auth resolves the tenant from the session,
  the bot from the token that received the update. But **`seed.py` is one of the
  nine**, so the first real customer cannot have their knowledge loaded — the
  tool that would do it stops working on the day they are created. The same
  wall blocks giving `@talkwisp_demo_bot` its own business.

  So the row-level security, the forced policies and the per-business unique
  constraints are all real and all verified, and none of it can be exercised by
  a second tenant. The fix is a `--business` argument on those nine, and it is
  the thing standing between the schema being correct and the product being
  multi-tenant.
- **`escalation`: approved, and now deferred rather than absent.** It was
  approved as a sixth table for forwarding unanswered questions and never
  created. Approved-and-absent is the worst of the three states — it reads as
  built to anyone scanning this list. It was not created in the tenancy
  migration either, because a table with no writer is schema for later, and the
  migration that adds it costs the same ten lines whenever it happens: the
  backfill argument does not apply to an empty table. Decide it again when
  Feature B is actually built.

## Vision — measured, not assumed

Tested 2026-08-30 on a real phone photo of a printed cafe menu: angled, glare,
two columns, left column cropped mid-word. Model: `gemini-3.6-flash`.

- **Transcribe first, then extract. Do NOT extract facts directly from an
  image.** This reverses the assumption in `docs/strategy-architecture.md`.
  Measured on the same photo, same model, same temperature:
  - *Transcription* paired every item with its correct price, including the two
    items whose prices were not visible, which it correctly left blank.
  - *Direct image → JSON facts* shifted **every sandwich price up by one row**:
    it reported Деревенский сендвич at 36 000 (which is Стейк сендвич's price),
    Стейк сендвич at 29 000 (Филли's), and so on down the section. It also
    silently dropped two rows.
  - Every one of those wrong prices carried **confidence 0.95–0.99**.
- **Cause: the model assumes every row has a price.** The first item's price was
  cropped out of frame, so it borrowed the next row's, and the error cascaded.
  Asked the same question with "(no price visible)" offered as an allowed
  answer, it got all ten rows right.
- **Therefore an extraction schema must allow a missing value.** A schema that
  requires a price for every row does not produce "no price" -- it produces the
  neighbour's price, confidently. This is the exact failure the product must
  never make.
- Confidence from vision is **anti-correlated with correctness** here: the
  wrong prices scored higher than the ones with genuinely partial information.
  Do not use it to rank a review queue for image-sourced facts.
- What went right: no hallucinated completions of the cropped left column
  (`Берг,`, `Цезарь`, `айонез` came back as-is, not invented into whole names),
  and no mixed-script contamination.
- **Uzbek, tested separately on a printed Latin-script menu (2026-08-30).**
  Clean result, and it confirms the diagnosis above rather than contradicting
  it:
  - All 21 items and all 21 prices correct, including a two-variant row
    (`Kotlet mol goʻshtidan` / `tovuq goʻshtidan`).
  - **Direct image → facts was correct here** -- because every row on this menu
    HAS a visible price. The off-by-one on the Russian menu happened only where
    a price was missing. That is the whole failure condition, isolated.
  - **Apostrophes come back as ASCII U+0027**, which `normalize()` already
    folds: photographed `Lavlagi va yong'oq` -> `lavlagi va yongoq`,
    `Lag'mon` -> `lagmon`, `Sho'rva` -> `shorva`. A customer typing `Lagmon`,
    `Lag'mon`, `Lagʻmon` or `Лағмон` reaches the same key as the photo.
  - No mixed-script contamination.
  - The orphan row (a bare `35.000` with no item name) was dropped rather than
    attached to a neighbour. Correct, but it means a price with an unreadable
    name disappears silently -- another instance of the completeness problem.
- **Net: transcribe first anyway.** Direct extraction is correct on clean,
  complete lists and catastrophically wrong on incomplete ones, with no signal
  distinguishing the two cases. Transcription was right in both. The cost of
  transcribing is one extra model call; the cost of the other failure is
  quoting a customer someone else's price.

## The empty chunk table: an export artifact, not a finding — 2026-09-02

I initially read the Avisena seed producing **zero chunks** as evidence that
real clinic data arrives without prose, and that the prose lane therefore
matters far less than the fact lane. **That reading does not survive looking at
the data.**

- The export contains **24 prose-shaped values living inside structured
  fields** — 12 doctor scope notes, 10 preparation instructions, an opening-hours
  caveat, and a landmark. 1334 characters in total.
- So prose is not absent from the clinic. It was **flattened into fields** by
  whoever produced the export, and this seed stores it as facts (`izoh`,
  `tayyorgarlik`) where it works: the ENT answer quotes the Kukushka note
  straight out of `izoh`.
- What is genuinely missing is the **paragraph-length policy prose** a clinic
  certainly has — what to bring, rules for children, how results are collected,
  cancellation, insurance. The previous data set had exactly three such
  passages. Their absence here is a property of a structured JSON export of
  doctors, services and prices; it is not evidence that the clinic has no
  policies.
- Supporting detail: only **2 of the 24** values contain a sentence break. These
  are clauses, not paragraphs. Clause-length prose belongs in `fact`; the case
  for `chunk` rests on material this export never had the shape to carry.

**Conclusion, corrected:** nothing here says the prose lane is dead weight. The
question is still open and the right test is different — take a clinic's actual
documents (a printed price list, a policy page, a Telegram post) and see whether
they contain paragraphs. Judging the prose lane by a structured export is
judging it by the one input format guaranteed not to contain prose.

## Triage scope narrowed: a symptom may carry a named question — 2026-09-02

- **The failure that prompted it.** "Ukamni qulogʻi ogʻriyapti, lor xonasi
  nechanchi etajda?" — *my brother's ear hurts, which room is the ENT in?* —
  was short-circuited entirely. Refusing the room number is the same failure as
  a multi-part question dropping a clause. Triage was built to stop the bot
  quoting prices at someone describing pain, not to stop it saying where the ENT
  sits.
- **The line is WHO CHOSE THE SUBJECT, not whether the answerable part is
  "independent" of the symptom.** Independence fails as a test: the ENT's room
  number is not independent of the earache — that is *why* they are asking. But
  a pure symptom report contains no question at all, so any answer requires the
  BOT to pick something to offer, and picking a paid service in response to pain
  is the whole failure. When the customer names the thing, answering is not
  inference; it is answering.
- **That also settles prices**, which looked like the hard case. Quoting an
  ultrasound the customer asked for by name is no more medical than quoting an
  address. "My wife's stomach hurts" → UZI price was wrong because *the bot*
  chose the ultrasound. Authorship of the topic is the distinguishing feature,
  not the kind of fact.
- **It is mechanical, not a judgement call, and the machinery already existed.**
  The exact-match tier only matches strings LITERALLY PRESENT in the customer's
  text, so `matched_on == "subject"` means the customer wrote the subject's name.
  Measured over nine cases before building anything: four pure symptom reports
  (two languages, both tiers) all returned `not_found`; three
  symptom-plus-named-question cases all matched a subject.
- **Acute remains absolute** and short-circuits before any retrieval, even when
  a subject is named. "Bolami isitmasi chiqib qusopti, tez yordamila bormi?"
  names a service and still gets 103/112. Someone whose child is vomiting with a
  fever should not be reading a room number.
- **The disclaimer stays attached.** An answered symptom message is prefixed
  with "we do not give medical advice, but:" — "the ENT is in room 201" must not
  read as engaging with the earache.
- **The guarantee is narrowed but still structural, and still in code:** the bot
  can never CHOOSE a service to offer someone describing pain. Only the customer
  can put one on the table.

### The way this breaks, and the guard against it

The signal is exactly as good as the alias table, and it fails in both
directions.

- **Safe direction:** a missing alias reads a named question as a pure symptom
  and over-refuses. "Xotinimni qorni ogʻriyapdi, UZI qancha turadi?" is the one
  miss in the nine — `not_found`, because no bare `UZI` alias exists. It
  improves as aliases improve, and the failure is a refusal.
- **Dangerous direction:** if a BODY PART is ever an alias, the exact tier fires
  on the symptom itself. An alias `qorin` → abdominal ultrasound would make
  "qornim ogʻriyapti" match a *subject*, and the bot would quote a price to
  someone reporting pain — the original failure, returned with a green badge.

**Enforced on every write path, not just the seed.** Retrieval's candidate set
is `fact where confirmed` plus confirmed aliases, so a subject becomes matchable
**the moment it is confirmed**. That makes the seed one of three doors, and the
least important one — it carries no real customer data:

| door | when the subject becomes matchable |
|---|---|
| `seed.py` | bulk load |
| `typed.store()` | the owner types a fact; confirmed on write |
| `review.confirm()` | an extracted proposal is accepted |

The ingestion path will carry everything real, and a price list containing
"Qorin boʻshligʻi UZI" is exactly where a model would propose `qorin` as a
subject. Extraction does not currently propose *aliases* at all — but it
proposes **subjects**, which the exact tier matches identically, so the risk was
on that path regardless. `check_subject()` in `app/triage.py` is the one shared
rule, called from all three.

**It asserts that no subject or alias is a body part or a symptom word**,
checked against `FORBIDDEN_ALIASES` in `app/triage.py` — the marker lists triage
already maintains, plus body parts in both languages. The seed fails rather than
loading one. Compound aliases are unaffected: "koz shifokori" is not "koz".

Worth noting the bare-`UZI` case was already a bad alias for an unrelated
reason: "uzi" means "itself" in Uzbek, and that ambiguity was a live failure in
the previous data set.

## A verifier reads the source of truth, never a snapshot of it — 2026-09-02

Five tool failures this project, and every one is the same shape: **the thing
meant to verify a change was reading a cached or intermediate copy rather than
the live thing it was checking.** Not five bugs — one bug, five times.

1. **The grader read a route the answer path had outgrown.** Four separate
   times, each costing a full paid re-run to discover. Fixed by extracting
   `grading.py` so the rule lives in one place.
2. **Grading read `near_facts`** — the raw scored window — while the answer was
   built from the window *plus* list expansion. Three correct answers graded as
   failures.
3. **Grading read the route only**, so a correct answer delivered in the wrong
   ALPHABET graded green. Found by diffing answer text against a saved baseline,
   not by the harness.
4. **`regrade.py` read the question snapshot stored in `results.json`**, so two
   changed expectations were invisible and it reported an unchanged 76/3/1 —
   the same numbers for different reasons, which is the worst kind of wrong.
5. **The harness itself saturates** when the question set stops being refreshed
   from real traffic: `questions.py` at 30/30 was a snapshot of failures already
   thought of, verifying against yesterday's understanding.

**The rule: a verifier must read the live source of truth, and must say so
loudly when it cannot.** Concretely:

- Re-read `questions.py`, never the copy embedded in a result file.
- Grade what actually reached the prompt (`context_facts`), never an
  intermediate window.
- When a cached result cannot answer the question being asked of it — a
  question added since the run — **report that it cannot**, rather than
  silently omitting it. `regrade.py` now does this.
- A stale-verifier failure is silent by construction: it produces a plausible
  number. Prefer a loud refusal to a quiet wrong answer, exactly as `NO_ANSWER`
  does in the answer path.

**Corollary, learned the same way:** before a change that could alter answers,
save a baseline of the current answers, not just the current verdicts. Verdicts
hid the wrong-script regression completely; the answer text exposed it in one
diff.

## syn-which-room: measured, and not fixable at retrieval — 2026-09-02

The hypothesis was that "Vrach qatda o'tiradi?" fails because retrieval offers
the street address as a candidate at all, making it a retrieval bug rather than
a model one — and that the fix therefore belongs in code, as it did the last
three times. **The measurement does not support it.**

- **Exact matching contributes nothing:** `not_found`, no subject, no attribute,
  no value match. This is purely the vector path.
- **The address scores 0.659 and is genuinely similar.** The embedding cannot
  separate room-level from building-level location, because "where does the
  doctor sit" and "where is the clinic" *are* close in meaning. Retrieval is
  behaving exactly as designed.
- **No score-based signal separates answerable from unanswerable.** Measured
  across all 80 questions using scores already recorded in `results.json`:

  | | n | top-1 median | top-1 range | spread median |
  |---|---|---|---|---|
  | answerable (`fact`/`prose`) | 42 | 0.720 | 0.594 – 0.877 | 0.094 |
  | must refuse (`gap`) | 33 | 0.645 | 0.573 – 0.730 | 0.029 |

  The ranges overlap across almost their entire length. `syn-queue-cardio` must
  refuse and scores **0.730** — higher than a third of the answerable set.
  `children-ru` must answer and scores **0.594**, below most of the refusals.
  Spread looks more promising until the counter-examples: `syn-pediatr-
  definition` must refuse with a spread of 0.122, and `syn-what-to-bring` must
  answer with 0.026.
- **This is the similarity-floor finding again, from a different direction.**
  The score is not a correctness signal. Any threshold or flatness heuristic
  here would misclassify in both directions, and a false negative is discarded
  before the model ever sees it.

**So the pattern does not hold here.** Three times running, the right fix was
moving a decision out of the prompt and into code. This is the case where that
instinct is wrong, and the measurement is what says so rather than taste.

**What is actually left:** no fact holds a room number, so the honest outcome is
a gap, and the only thing standing between the customer and a wrong answer is
the model's judgement about whether an address answers a question about a room.
Rule 1b now names this exact case and does not stop it. Two real options:

1. **Get the facts.** A room number per doctor makes the question answerable
   and the problem disappears. This is the completeness problem in another
   costume: the failure is that the clinic never told us.
2. **Accept it as known-broken** and leave it graded red, which is what it is.

**Accepted as known-broken on 2026-09-02.** Worth noting the question is genuinely ambiguous even for a person — "vrach
qatda o'tiradi" could be asked by someone who does not know where the clinic is.
A receptionist might well answer with the address. That does not make the reply
right, but it does explain why no rule phrased so far has caught it.

## Absence is not evidence — rule 10, 2026-09-02

- **Silence never means "no".** Asked whether the clinic works through lunch,
  the bot answered that it works "tanaffussiz" — *without a break*. Nothing in
  the knowledge base says that. It inferred a negative from the absence of a
  fact, which is a different failure from serving an adjacent fact, and the more
  dangerous half: no stated Sunday hours does not mean closed, no stated
  insurance does not mean not accepted, no stated service does not mean not
  offered.
- **Stated as its own rule, not folded into the substitution fix**, even though
  one prompt change addresses both. The generalisation is worth having on its
  own: a reader who only sees "don't serve adjacent facts" has not been told the
  other thing.
- **A stated range or list is explicitly carved out**, because rule 9 depends on
  it: "works Monday to Saturday" DOES tell you about Sunday, because the range
  was stated. Silence is not a range. Without that sentence this rule would have
  undone the temporal work — the Saturday case would have gone back to refusing.
- **It fixed more than it was aimed at.** `syn-lunch-break` and also
  `live-how-to-book`, which had been inventing a booking procedure around a real
  phone number. Inventing "call this number to book" from a number that is
  merely present is the same move as inventing "no break" from silence.
- **Rule 1b gained the other half:** a different property of the RIGHT thing is
  still the wrong answer — where the clinic is located does not answer which
  room a doctor sits in. The first draft of that addition also claimed opening
  hours never answer questions about breaks, which was too broad and had to be
  narrowed with an explicit counter-example: a closing time IS part of stated
  opening hours and must still be given.
- **`syn-which-room` is still failing** and is now the clearest remaining case
  of pure substitution: no room numbers exist, and the street address is served
  instead. Rule 1b's new sentence names exactly this and does not yet stop it.

## Retrieval paths merged — 2026-09-02

- **Exact match is no longer terminal.** It is still first, still authoritative,
  and its facts still bypass the similarity floor — it just no longer ends the
  search. Both paths run, always, and the results are merged into one context
  for one model call.
- **The short-circuit was never intended and was invisible by construction.**
  "Yakshanbayam ochiqmisila? Ozi qatda joylashgansila?" matched on Sunday hours,
  answered that clause, returned `ok`, and dropped the rest — telling a customer
  we did not know our own address, a fact we hold. A real answer to a real
  clause looks like success at every layer, including grading.
- **A question with one clause loses nothing by also running the vector path;
  a question with three gains the other two.** That is the whole argument.
- **Cost:** one embedding call on questions that previously skipped it. Cheaper
  than it looks — it replaces a SECOND generation call on every question where
  exact matching fired and then failed to answer.
- **Result: `live-sunday-address-landmark` fixed, no verdict regressions.**
  80 questions, 74 -> 75 pass. But 12 of the 21 previously-exact-match answers
  changed wording, which is why the check below mattered.

### The regression the harness could not see

- **A Cyrillic Uzbek question came back in Latin script.** The larger merged
  context meant more Latin-stored facts in the prompt, and the model copied the
  script of what it had just read. Every route was correct, so the verdict
  stayed **PASS** and nothing reported it. Found only by diffing the answer text
  against a saved pre-change baseline.
- **The cause was position, not wording.** `REPLY IN` sat at the TOP of the
  prompt, and the instruction that had to beat the context was further from the
  point of generation than the context itself. It now goes LAST, after
  everything it must override.
- **That created a second defect immediately:** the model began CONTINUING the
  final line, and one reply came back with "REPLY IN: Uzbek, in CYRILLIC script"
  appended — text a customer would have read in their chat. Intermittent, which
  is worse than consistent. **Stripped in code, not asked for in the prompt:** a
  prompt instruction is a preference, and this needed a guarantee. Same reason
  `NO_ANSWER` is a marker rather than a request.
- **Grading now checks the SCRIPT of every reply**, before it checks anything
  about routes, and a wrong-script reply fails however correct its contents.
  Route grading is mechanical and gradeable, which is why it was chosen — but it
  cannot see a correct answer delivered in an alphabet the customer cannot read,
  and that is the most visible defect class there is. It checks script only, not
  language: Uzbek-Cyrillic versus Russian is the detector's job and is tested
  free in `check_language.py`.

### Pattern: every capability widens what the model feels licensed to say

Three times now, a fix that gave the model something new to work with made it
answer questions it should have declined:

1. **Dates.** "Ertaga vrachda bo'sh vaqt bormi?" had always refused; given a
   clock it began replying "tomorrow, Wednesday, our doctors' hours vary — tell
   us which doctor". No false claim, but it implies it could check availability,
   and it cannot for any doctor. Fixed by rule 9: **being able to name the day is
   not permission to answer a different question about it.**
2. **Merged retrieval.** More context in the prompt, and the reply drifted into
   the context's script.
3. **`syn-lunch-break`.** With more facts in front of it the model asserted the
   clinic works "tanaffussiz" — without a break. Nothing states that. It
   inferred a negative from silence, and it is still failing.

**The generalisation: helpful is where the failures live.** Every capability
added is more surface for the model to be helpful with, and the resulting
failures are subtler than the ones being fixed — no false claim, just an implied
capability or an unstated inference. Expect the next capability to do this too,
and check for it specifically rather than trusting the pass count.

## Dates — added 2026-09-01

- **The bot had no concept of what day it was**, and said so confidently:
  "Yakshanba dam olish kuni, shuning uchun ertaga ishlamaymiz" — *Sunday is our
  rest day, so we are closed tomorrow* — with nothing telling it that tomorrow
  was Sunday. It was not. This is the failure class that makes a customer act
  on something false, which is why it was taken before the others.
- **TODAY and TOMORROW are stated at the top of every answering prompt**, and
  both are computed in code. The model reads a weekday; it never derives one.
- **Tomorrow is computed, not inferred, and that is the point.** Handing over
  today's date and asking the model to add a day trades a hallucinated weekday
  for an arithmetic mistake — the same defect in better disguise. Code counts,
  the model reads. `check_time.py` checks the counting offline, for free.
- **The clock deliberately stops at tomorrow.** "Indinga" (day after tomorrow)
  and "kelasi seshanba" (next Tuesday) are refused, because extending the
  window means handing arithmetic back to the model. Extend it only if real
  traffic asks, and extend it in code.
- **A day the customer NAMES is different from one they refer to relatively.**
  "Yakshanba kuni ishlaysizmi?" needs no resolving and is answered from the
  context as it always was; only the relative reference is unanswerable. The
  first draft of the rule missed this and would have started refusing questions
  that had passed since step 20.
- **This is a second exception to "never state what was not retrieved",** after
  the emergency numbers. The date is not business knowledge and not model
  knowledge — it is the system clock, so it cannot become the "one wrong answer
  feeds the next" failure that keeps history out of the answering prompt. Two
  exceptions now exist and both are civil or physical constants. A third should
  be argued from scratch, not from precedent.
- **Timezone is the business's, hardcoded, with no fallback to the host.**
  `Asia/Tashkent`, UTC+5 with no daylight saving since 2005. A server running on
  UTC is already on the *next* day in Tashkent for five hours out of every
  twenty-four, so a silent fallback would be wrong for a fifth of the day —
  exactly the silent-wrongness being fixed. Single-tenant like the rest of the
  schema; this becomes a column, not a config file, when multi-tenancy lands.
- **`tzdata` is now a declared dependency.** Windows ships no IANA timezone
  database, so `zoneinfo` cannot resolve `Asia/Tashkent` without it. It was
  present transitively and worked by luck; a fresh environment would have
  failed on the first date question.
- **Over-refusal was the first result, and it needed fixing too.** The initial
  rule made the model treat "Monday–Saturday" as not covering Sunday, so on a
  Saturday it refused rather than saying "closed tomorrow". Safe but unhelpful:
  a stated range of working days DOES answer for days outside it. Verified by
  monkeypatching the clock to Saturday and Sunday rather than waiting for the
  weekend.
- **Giving the model a date made it engage with questions it should refuse,
  and that had to be closed too.** "Ertaga vrachda bo'sh vaqt bormi?" ("any free
  slots with a doctor tomorrow?") had always refused — there is no
  appointment-availability data. With the clock it started replying that
  "tomorrow, Wednesday, our doctors' hours vary, so tell us which doctor" — no
  false claim, but it implies it could check availability if asked properly,
  and it cannot, for any doctor. Rule 9 now says outright that resolving a date
  tells you only which day is meant, never whether a slot is free or who is on
  duty. **Being able to name the day is not permission to answer a different
  question about it.** Caught by the harness, not by inspection.
- **The harness grades the route only, and has to.** The correct wording is
  day-dependent — "Ertaga ishlaysizmi?" should answer *yes, 09:00–18:00* on five
  days and *no, we are closed* on Saturday — and no static expected string is
  both. Retrieval never sees the date, so the retrieved facts are stable even
  though the answer is not. The day-dependent behaviour is covered by
  `check_time.py` and by clock-patched probing instead.

## Language detection — rewritten 2026-09-01

- **Still detected in code, not by the model.** Unchanged and still right: a
  Russian question about opening hours once came back in Uzbek Latin, because
  the retrieved fact was stored in Uzbek and the model copied the *context's*
  language instead of the *question's*. The detector produces a `REPLY IN:`
  instruction the prompt obeys literally.
- **Whole words, never substrings.** Also unchanged. Substring matching sent
  Russian customers Uzbek replies: "ва" sits inside "вас", so "У вас есть
  невролог?" was read as Uzbek. "ва" stays out entirely — two letters is too
  little signal to be worth having.
- **The old detector was blind to a whole class, and the test set could not
  show it.** It decided Uzbek Cyrillic by the presence of ў ғ қ ҳ. All three
  `uz-cyrl` questions in `questions.py` contain қ or ў, so it scored 10/10
  while being wrong for most real Cyrillic traffic.
- **The reason is a keyboard, not a language.** None of ў ғ қ ҳ are on a
  standard Russian layout, so casual typists substitute у г к х. The detector
  keyed on exactly the characters that disappear in informal phone typing.
  Measured on the class: **0 of 13 correct** — every one answered in Russian.
- **Replaced by a scored classifier**, because every individual signal has a
  counterexample. The Uzbek letters vanish on a Russian keyboard; the Uzbek
  question particle `-ми` is also the Russian instrumental plural ending
  ("с детьми", "врачами"); `ъ ь э ё ю я` are in BOTH alphabets and treating any
  of them as Russian would reintroduce the same bug mirrored. Only `ы` and `щ`
  are genuinely Russian-exclusive. No one signal is safe; the sum is.
- **A word borrowed into both languages must carry no signal.** Listing "врач"
  as Russian read "Врач качон келади?" and "Врачингиз ким?" as Russian. It is
  an everyday loanword in colloquial Uzbek and is deliberately absent from the
  word lists. Same test applies to anything else shared.
- **Ties go to Uzbek.** The customers are in Uzbekistan, and the failure being
  fixed was Uzbek read as Russian — defaulting the other way is what produced
  13 wrong answers out of 13. The cost is short Russian input with no
  distinctive vocabulary, which is why the Russian imperative and greeting
  words exist.
- **`check_language.py` is free and offline** — no API, no database. Run it on
  any change to the detector. It now scores 53/53, but the number that matters
  is that **17 of those cases were written to break the classifier, not to pass
  it**, and three of them did on the first attempt. A test set built only from
  passes decays; see the harness note above.
- **Residual, accepted:** a two-word Cyrillic message with no distinctive
  vocabulary is genuinely ambiguous and will sometimes be wrong. That is a
  property of the input, not a bug to tune away — and tuning further against
  the authored corpus would only be fitting to sentences nobody sent.
- **Provenance:** the Cyrillic corpus is authored, not harvested, and carries
  the same caveat as the `syn-` questions. Replace it with real Cyrillic
  traffic when there is some.

## Symptom triage — decided 2026-09-01

- **A message reporting a symptom is short-circuited BEFORE retrieval**
  (`app/triage.py`, called first in `answer()`). It never reaches the fact
  table, so the model is never handed prices it could quote.
- **This was not a retrieval failure, which is why it matters.** Retrieval was
  working. "Xotinimni qorni og'riyapdi" ("my wife's stomach hurts") genuinely
  matches `Qorin boʻshligʻi UZI / narx` at 0.652, every guard passed, nothing
  was invented, and the status was `ok`. The bot answered a man describing his
  wife's pain with an abdominal ultrasound price and a gynaecologist's fee.
  "Bolamni gorlosida shamollash bor" got the paediatrician and her fee.
  A correct retrieval and a product failure are not mutually exclusive.
- **Ordering is the guarantee.** Checking after retrieval would mean the facts
  are already in the prompt and we are trusting the model to decline to use
  them. It did not decline. Same structural argument as the NO_ANSWER branch:
  refusing is a branch in the program, not a behaviour we hope for.
- **Two tiers, and ties go to acute.** Acute leads with the emergency number
  and deliberately omits the clinic's; someone who cannot breathe should be
  dialling an ambulance, not a reception desk. General symptoms get the
  clinic's own number plus the emergency line as a second sentence. The
  asymmetry is the reason: showing an emergency number to a mild complaint
  costs almost nothing, the reverse mistake is the one that matters.
- **The emergency numbers are hardcoded, and that is a deliberate exception to
  "never state what was not retrieved".** 103 (ambulance) and 112 (unified
  dispatch, live across all regions since March 2025), verified against
  gazeta.uz and the Tashkent city administration on 2026-09-01 rather than
  recalled. They are public civil infrastructure, constant, and must never be
  produced by a model, interpolated or reformatted — a wrong emergency number
  is worse than none. The replies are fixed strings per language for the same
  reason. The clinic's OWN number in the same reply is retrieved, not
  hardcoded, and is omitted entirely if no confirmed phone fact exists.
- **Detection is code, not a model call**, matched at word start against
  `normalize()` output — the same technique as retrieval's exact tier, for the
  same reason Uzbek is agglutinative. Measured over the 76-question set: 4 fire,
  0 false positives. "Qorin boʻshligʻi UZI narxi qancha?" contains *qorin* and
  correctly does not fire, because the markers key on the symptom, not the body
  part.
- **Symptom → specialty mapping is permanently out of scope.** Not "chest pain
  → cardiologist", not "sore throat → ENT", not later. That inference is
  medical advice however it is worded, and it is the line between being
  unhelpful and being liable. If the clinic has triage guidance, it is stored
  as facts and retrieved like anything else — theirs to state, not ours to
  derive.
- **Not logged to `gaps.jsonl`.** A gap means "a question the clinic could
  answer by adding a fact". A symptom report is not that, and mixing the two
  makes the gap log useless for its one job. The message log records the
  `triage-acute` / `triage-symptom` route, which is where volume gets counted.
- **Deliberately missing:** a prompt-level backstop for symptoms detection
  misses. Rejected for now because the short-circuit means the model is never
  called with facts when triage fires, so a rule would only help on a miss —
  and it carries real regression risk on price questions that name a body part.
  Add it if a live miss appears, not before.
- **Known limitation, pre-existing:** "Хотинимни корни огрияпти" (Uzbek in
  Cyrillic, no ў/ғ/қ/ҳ) is detected as Russian and gets the Russian reply.
  That is `detect_language()`, not triage, and it affects every answer — but
  triage is where it becomes most visible, because the reply is a fixed string
  rather than a model paraphrase that might drift back toward the question.

## Leads — outside things worth chasing

### Tilmoch / Tahrirchi — transliteration

Tashkent company (tilmoch.ai), grown out of Tahrirchi. AI translation and text
correction for Uzbek, Karakalpak and other Turkic languages, both scripts.
Backed by AloqaVentures, Yoshlar Ventures, IT Park Ventures. Founder:
Muhammadsaid Mamasaidov, m.mamasaidov@tahrirchi.uz. They run B2B integrations.

**Why it matters:** they have an in-house Latin<->Cyrillic transliterator, used
to split their UzBooks2 corpus. That is the exact mapping table `normalize()`
needs, and the part we cannot verify by eye.

**Ask them:** is the transliterator available standalone? If not, will they
share the character mapping table?

**If we get it, use it offline to generate a hardcoded table.** Never call an
API inside `normalize()`. That function runs on every query and on every stored
key, and must be deterministic, fast and offline. A network dependency there
means an outage breaks all retrieval, and a model update silently changes
stored keys -- which would require re-normalizing every row to fix.

**Not for embeddings.** Their open models are BERT-family fill-mask, Latin Uzbek
only, not trained for retrieval. Use a general multilingual model.

**Also:** their open corpora (UzBooks / UzBooks2, MIT) are a source for a real
Uzbek eval set -- better than 25 hand-written questions in `questions.py`.

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

## Orders — a fifth table, and the rule that permits it — 2026-09-04

Manual payment confirmation: the seller sends a card number, the customer pays
and sends a screenshot, the seller checks their banking app and replies by hand.
We are not replacing that process. We are removing the mess around it — which
means we are not verifying anything, and every name in the system has to admit
that.

### The rule that decides what gets a table

> **Files hold append-only observations. Tables hold mutable state with a
> lifecycle that someone is waiting on.**

`gaps.jsonl` and `feedback.jsonl` are written once and never changed; losing a
line costs a number in a report. An order is mutated (awaiting → confirmed),
queried by state, and if one is lost a real person who paid money is stranded.
Same for a pending escalation in feature B.

This rule was written after the fact, so it was checked against the decisions
already made rather than used to justify them: it predicts gaps-as-file,
feedback-as-file, and orders-as-table correctly, without special pleading for
any of them. That is the evidence it is real rather than post-hoc.

The four-table rule still binds, because it binds the **knowledge model**.
`purchase` holds no knowledge about the business — the price lives in `fact`,
and an order only records which fact it was read from and what happened next.

### `owner_confirmed`, not `paid`

The state is not called `paid` and the timestamp is not called `verified_at`.
We cannot verify a payment; the state records that the owner looked at their
banking app and said so. A state called `paid` would be this system claiming
knowledge it does not have, every time anyone reads the table.

Wording in a log message drifts as people edit copy. A column name does not.
That is why this is enforced in the schema rather than in customer-facing text.

### A fact that must never be retrieved — permanent

Payment details (card number, cardholder name, bank, instruction text) are
stored as facts so the owner can edit them like anything else. But a fact is
retrievable, and a retrieved fact goes into the answering prompt as context — so
storing the card number as a fact hands it to the model through the front door,
which is exactly the rule we thought we were enforcing by assembling the payment
message in code.

Storing it as a fact does not keep it away from the model. Excluding it from
retrieval does.

So: payment details live under one reserved subject, and **retrieval excludes
that subject_key by construction**, in `app/retrieval.py`, so every path
inherits it. A question about payment details routes to the code-assembled
message before retrieval, the way triage does.

This is a category nothing else in the schema has, and it is named here
precisely — *a fact that must never be retrieved* — because the exclusion looks
like a bug to anyone who finds it without this context. It is not a bug and it
must not be "fixed". Same reasoning as the emergency numbers in `triage.py`: a
model that paraphrases a card number is a model that can get it wrong, and there
is no acceptable failure there.

The rule is enforced by a test, not by intention: ask for the card number in all
three languages and assert the reply is byte-identical to the template. If the
model ever composes that string, the test fails.

### Screenshot authenticity is never assessed — permanent

The screenshot is evidence **for the seller**, not verification. Nothing reads
it, scores it, or believes it — not heuristics, and not vision, which we already
have and which would be trivial to point at it.

Someone will propose a vision check as an improvement, so the reasoning is
recorded the same way symptom → specialty mapping is:

- Screenshots are trivially faked, and bank apps differ across the country, so
  any check would be a probability we would then have to display as something.
- A seller who loses money because our bot believed a screenshot never trusts us
  again. The asymmetry is total: a check that is right 95% of the time is a
  product that steals from its user 5% of the time.
- The moment we score a screenshot, the owner starts trusting the score instead
  of their banking app — which is the one source of truth that is actually
  authoritative. A weak check is worse than none because it displaces a strong
  one.

Out permanently.

### The amount-reuse window is wider than the expiry window

The unique-amount trick: 250 000 becomes 250 003, so the seller can match one
payment to one order when three people pay the same afternoon. Suffix 1–50, so
the most anyone overpays is 50 so'm.

Orders expire after 24 hours — and expiry frees the amount. That is the trap: a
customer who pays 30 hours late sends an amount that now belongs to somebody
else's open order, the seller confirms the wrong one, and nobody ever finds out.
A silent mismatched payment is the worst failure this feature can produce.

So the quarantine is **seven days**, deliberately wider than the 24-hour expiry.
An amount is not reissued while any order from the last week used it, whatever
that order's state.

The uniqueness guarantee is split on purpose. The **index** covers open orders
only — an index predicate has to be immutable and `now()` is not. The
**seven-day policy** lives in `app/orders.py`. The index is the floor that
cannot be argued with; the code is the policy that can be tuned.

### An exact price is required; a range is refused, not narrowed

An order needs one exact number, and the standing rule is that costs are quoted
as approximate ranges. Those do not conflict as long as the split is kept: the
*agent* still quotes ranges, and the *order* carries an exact figure read from a
confirmed fact and assembled in code.

The consequence is a refusal. If the stored price is a range (`450 000-500 000
soʻm`) or a floor (`100 000 soʻmdan`), the item is **not orderable** and the
owner is told to set an exact amount. Taking the low end would be the model
inventing a price with extra steps — the same failure as any other fabrication,
with money attached.

Two more refusals fall out of the same discipline, and both are correct:

- **Several prices.** A doctor has `qabul narxi` and `takroriy qabul narxi`, and
  a first visit is not a repeat visit. Choosing for the customer is choosing what
  to charge them. We ask.
- **Unconfirmed prices are invisible.** Only `confirmed` facts can price an
  order. An unconfirmed price came out of a file and has not been read by a
  human, and the gap between reviewing a price and charging one is the entire
  point of the review queue.

The live base demonstrates all three at once: `MRT bosh miya` carries an
unconfirmed range from a file *and* a confirmed exact figure from the owner. The
`confirmed` filter resolves it correctly with nothing else involved.

### What cannot be made reliable, recorded before it is built

1. **We cannot verify a payment.** Confirm is an assertion, not a fact.
2. **Screenshot authenticity is never assessed.** See above.
3. **A customer can pay the round number anyway**, typing 250 000 by hand, and
   the trick that makes matching work has silently failed. Mitigated by
   prominence in the message and by the "amount does not match" reject path.
   Not solved.
4. **Telegram sends can fail** — blocked bot, deleted chat. "The customer always
   hears something" is best-effort by nature. A send failure must therefore
   reach the **owner** as a payment-channel notification, not merely be recorded
   on the row: a customer who blocked the bot after paying is stranded, and
   "visible in the dashboard" means visible to someone who is not looking.
5. **The buy-intent classifier will misfire.** The bound we do guarantee: the
   worst case is an unwanted payment offer, never a charge, because the model
   routes and code decides.
6. **Language detection is 53/53 on the test set, not perfect**, and a payment
   instruction in the wrong language is a bad failure. Mitigation: the card
   block is identical in every language; only the surrounding text varies.
7. **A card number printed inside an uploaded document is not excluded.** The
   reserved subject protects facts; a chunk has no subject, so prose carrying
   payment details is chunked, embedded, retrieved and shown to the model like
   any other passage. Added 2026-09-05, when the exclusion was built and this
   turned out to be the edge it cannot reach. No mitigation in code -- naming
   it beats a filter that would look like coverage and provide none.

## How the payment exclusion is actually enforced — 2026-09-05

The A1 entry above named the rule: payment details are *a fact that must never
be retrieved*. This is where it was built, and the mechanism moved during
design in a way worth recording.

### The exclusion is not in the retrieval code

The obvious implementation is a `WHERE subject_key <> ...` in the retrieval
queries. Counting the statements that can return a fact, that is eight arms:
five inside `find()` (including *both* halves of the candidate union) and three
in `answer.py`. Eight places is eight chances to forget, and the ninth query
written next year would inherit nothing while nothing failed. That is the same
structural hole A1 exposed, in a different costume.

So the exclusion lives below `app/`, in `migrations/0005`:

- **A view, `retrievable_fact`.** Every retrieval query reads it; `fact` stays
  the write and edit target. The naming does the work: **a query that reads
  `fact` is editing, a query that reads `retrievable_fact` is answering.** A
  query written later inherits the exclusion by reading the obvious thing.
- **A check constraint, `fact_payment_not_embedded`.** Both vector searches
  already require `embedding is not null`. A payment fact that *cannot* carry
  an embedding is therefore unreachable from either of them — closed by the
  database rather than by the text of a WHERE clause somebody may rewrite.

`seed.py` and `typed.store()` skip embedding for the reserved subject. That is
the policy; the constraint is the floor that catches the policy going missing,
and `check_payment.py` proves the floor fires by writing raw SQL around `app/`
entirely. Both mechanisms are one line.

### The one arm the view cannot reach

`find()`'s candidate set unions subjects-that-have-facts with confirmed
aliases. The view is on `fact`, so the alias arm is untouched by it: an alias
pointing at the payment subject would resurface the card number through that
union alone. `check_subject()` also refuses to create such an alias, but that
is a guard in another module, and depending on it silently is precisely what
this design is avoiding. So the alias arm carries an explicit predicate, and
`check_payment.py` inserts a live alias row and asserts it reaches nothing.

### What the exclusion cannot cover: chunks

A chunk has no subject, so a subject-key exclusion is a no-op on the prose
path. Writing one anyway and calling the chunk path covered would be a lie in
the shape of a filter.

The real risk is the owner uploading a price list with their card number
printed on it. It gets chunked, embedded, retrieved and put in front of the
model, and nothing here can see it. Recorded on the cannot-be-made-reliable
list rather than papered over.

### The test is the deliverable, and `want` is a literal

Three questions in the graded set — Uzbek Latin, Uzbek Cyrillic, Russian — whose
`want` is the expected reply **byte for byte**, written out as a literal string
rather than built by calling `payment.message()`. Comparing code-built output
to a code-built expectation would still pass if the answer path quietly started
asking the model, which is the one failure these exist to catch. Changing the
seeded card number means editing three lines; that friction is the feature.

Verified by disabling `payment.detect()` and running the three questions down
the ordinary path: all three flip to FAIL, and all three come back as correct
refusals in the right language with no payment fact in the context and no card
number in the reply. The test has teeth, and the miss lands in silence.

`check_retrieval.py` scores `payment` alongside `prose` and `gap`: a payment
question reaching that layer at all means the route above it missed, so
`not_found` is the only acceptable result. The exclusion is therefore
re-checked on every run of the deterministic harness, with no model and no API
call.

That last move generalises, and is worth reaching for again: **a deterministic,
no-API check that fails if a protected thing reaches a layer it should never
reach** is nearly free and runs constantly, where a check that must call a
model runs when someone remembers. When the alias guard gets its proper fix,
this is the shape to give it.

### The seed path was verified, not assumed

`seed.py` truncates `source`, so seeding destroys the ingested policy document
the four prose questions depend on. The payment rows therefore went into the
live database through a targeted insert at first, which left `seed.PAYMENT`
correct but unexercised -- the kind of thing discovered at the worst moment,
weeks later, by whoever runs the next full reseed.

So it was exercised deliberately on 2026-09-05. The policy source is a paste
that produced eight chunks and no facts, so it was dumped verbatim with its
embeddings, `seed.py` was run for real, and the source and chunks were restored
byte for byte -- no re-extraction, no re-embedding, nothing guessed. The seed
reported 137 facts and embedded exactly 131: the six payment rows were skipped
because the embed loop reads `retrievable_fact`, and the constraint did not
abort the run. `check_retrieval.py` scored 54/31/5 before and after, identical.

The embed loop reading the view is the part worth keeping. It cannot embed
something that must not be embedded, and it does not need to know why.

### The seeded card number is deliberately invalid

`8600 0000 0000 0000`. 8600 is a real Uzcard BIN, so a plausible-looking test
number could be mistaken for a live one by anyone reading the seed or a test
failure. All zeros cannot be, and it fails a Luhn check. The reason is written
in the row.

### Guards enforced only in Python — audited 2026-09-05

The A1 finding generalised: a guarantee enforced in Python before the database
sees it means the constraint never fires and could be dropped with every test
still green. Both existing guards had the shape, and one was worse than that.

**The `" / "` separator assert — fixed.** It was a bare `assert` over `seed.py`'s
own literal lists, at import time. It covered one of the four doors a fact can
come through, and `assert` disappears under `python -O`, so in production it was
a guard that was not there. Now `fact_subject_no_separator`, one constraint
covering every door including doors not yet written. The assert is kept because
it fails earlier and names the offending subject; the constraint is the floor.

**The body-part / symptom alias guard — not fixed, and the reason is a real
decision.** A check constraint cannot call `normalize()`, so making this
structural needs a generated column or an immutable SQL function — the same
question about where normalization lives that was deferred at step 6. Worth
doing properly rather than squeezing in here.

What *was* done today is writing down the invisible dependency, in both
modules. `extract.py` deliberately skips `check_subject()`, on the reasoning
that an extracted fact is unconfirmed and retrieval's candidate set is
`confirmed` only. That reasoning is sound and it makes the safety of one
module's write path depend on a WHERE clause in a different module. Deleting
`confirmed` from `_MATCH` as an apparently redundant filter would turn
extraction into an unguarded door for body-part subjects, and nothing anywhere
would fail to say so. The note is in `extract.py` and in `retrieval.py`,
because the dependency is invisible from either one alone.

## Buy intent: measured, and what the measurement found — 2026-09-05

### The classifier proposes; a tap creates

The brief had buy intent create the order. It does not. An order occupies an
amount, holds it 24 hours and quarantines it seven days, out of a space of 50
suffixes — so a classifier firing on price questions would exhaust a popular
service's suffix space over a few quiet weeks, and real customers would start
hitting `amount_exhausted` for reasons nobody could see.

With a tap in between, a false positive costs one unwanted button and zero
rows. The guarantee tightens from *the worst case is an unwanted payment offer*
to *the worst case is an offer nobody accepted*.

### The first measurement was a perfect score on a broken thing

`purchasable()` listed canonical subjects only. So the model was shown
`Rahimov Alisher Bahodirovich` and never `Kardiolog`, and "Kardiologga
yozilmoqchiman" — I want to book a cardiologist — missed in all three
languages. Nobody asks for a doctor by full name.

**0 of 90 false positives, and 3 of 5 real intents missed.** A classifier that
cannot fire scores perfectly on a false-positive metric.

It was caught only because the file contained a deliberate can't-fire control —
five real purchase intents, present precisely so a silent zero would fail.
That control was not automatic; someone had to think of it. The general rule
worth carrying: **when a measurement comes back clean, ask whether the setup
could produce that result while broken.**

This is the same fault as the stale-snapshot verifier, in a new place: the
thing being measured and the thing being verified had drifted apart while every
signal stayed green.

### After showing the model the names customers actually use

**1 of 90, and all five real intents caught in three languages.** The one hit
is not a misfire — see the next section.

The model's answer is resolved back through the database by subject *or* alias,
so it can only ever reach something the business sells. It never sees a price.

### Booking is not paying, and only one of them exists here

The single hit was `syn-book-gynae` — "Menga ginekologga zapis qberila", *sign
me up for the gynaecologist*. The classifier read that correctly. It is a
booking request.

**Avisena has no booking procedure.** The graded expectation for that question
is `gap` for exactly that reason. So if a booking request produces a payment
offer, we take money for an appointment nobody can reserve — the customer-side
form of the failure this whole feature was built to avoid.

Paying for a consultation and reserving a slot are different acts. At a market
stall they are the same act, which is why the brief's framing did not separate
them; at a clinic they are not. The reference case hid the distinction.

Recorded as a boundary rather than silently resolved: whether a scheduling
request may produce a payment offer depends on whether the business can
actually reserve anything, and that is the owner's fact to state, not ours to
assume.

### Two disambiguation axes, crossed rather than sequenced

- **Which thing.** `resolve("Karimov")` returns two doctors; `"kardiolog"`
  would return two at a clinic with two cardiologists. Choosing one is choosing
  who the customer sees and what they pay.
- **Which price.** Every doctor has `qabul narxi` and `takroriy qabul narxi`.
  All 13 of them. The normal case, not an edge one.

Asked in sequence that is two taps before anyone sees a figure. They are
crossed into one keyboard while the result still fits a screen — the live data
maxes out at four buttons — and fall back to the subject question above
`MAX_COMBINED`. A judgement about a phone screen, not a principle.

The second Karimov is synthetic, added to the seed months earlier purely to
keep the ambiguity behaviour testable. It caught this the first time it ran.
Deliberately awkward cases in the seed pay for themselves late.

### An invisible character in the money path

`payment.som()` used U+00A0, a non-breaking space, where every stored price
uses U+0020. Visually identical. Every computed amount would have failed to
string-match its own price list, and nothing in the system would have surfaced
it — it was found only because an assertion happened to compare bytes.

Two consequences, both fixed:

- **There were two money formatters.** `seed.money()` and `payment.som()` — the
  same "two definitions of one thing" fault removed from price reading an hour
  earlier. `seed.money()` now calls `payment.som()`.
- **`parse_amount()` could not read a pasted price.** A zero-width space, soft
  hyphen, BOM or non-breaking hyphen inside `60<zwsp>000` splits it into two
  numbers, so the price was refused as `not_exact` — telling the owner to set an
  exact amount for a price that already looked exact on screen. A misleading
  error is worse than a wrong one: it sends someone to fix what is not broken.
  Owners paste prices out of Word and PDFs, so this was reachable.

The fix folds by Unicode CATEGORY, not by a list of characters, because a list
of invisible characters is a list nobody can proofread: `Zs` to a space, `Cf`
deleted, `Pd` to `-` so a range written with a figure dash is still a range.

`normalize()` already folded all twelve suspects, which is why keys were never
affected and this stayed hidden — only the money path reads a raw value. A scan
of `fact`, `alias`, `chunk` and `source` found no live rows carrying any of
them.

### The kwargs collision came back

Adding `subject_key` to a price row immediately produced `OrderError() got
multiple values for keyword argument 'subject_key'` — the same shape as the
`reason` collision that got `OrderError.__init__` renamed in A1. Renaming a
parameter fixed one instance; the pattern at fault is **splatting a row into
kwargs beside explicit kwargs**, and it returned the moment a key was added to
the row. Now named as the pattern rather than the key.

## The failure this codebase keeps producing — 2026-09-05

Five separate incidents have now been written up here as if they were five
different bugs. They are one, and it is worth stating in its general form
because the next one will not look like any of them either.

> **A check reads a different object from the one the behaviour uses. It
> passes. Nothing is wrong with the check, the code, or the result — they are
> simply about different things, and a green signal cannot tell you that.**

The instances, deliberately listed together so the shape is visible rather than
the details:

| The check | What it read | What the behaviour used |
|---|---|---|
| Grading a `fact` expectation | `r["facts"]`, the exact-match rows | `context_facts` — exact **plus** the scored window plus list expansion |
| `regrade.py` | a saved snapshot of a run | the live answer path, which had moved on |
| `check_answer.py` prose questions | the `chunk` table as it happened to be | a table `seed.py` truncates on every run |
| Buy-intent measurement | canonical subject names | the aliases customers actually type |
| The amount in a payment message | `payment.som()` | `seed.money()`, differing by one invisible character |
| `gap-appointment`'s recorded reason | "no booking procedure exists" | a procedure **is** stated; it just says nothing about ONLINE |
| `check_bot.py`'s action scan | `if action == "x"` branches | a dispatcher with **two** shapes — that, plus membership in `ORDER_ACTIONS` |
| `check_orders.py`'s cleanup assertion | `count(*) from purchase == 0`, the whole table | whether *this run* left rows behind |
| The frontend's conflict warning | `result.conflict`, a key the API never sends | `result.conflicts`, plural, a list |

Three of those scored perfectly while broken. The grader scored three correct
answers as failures; the classifier scored 0 of 90 false positives on a
classifier that could not fire; the prose questions would have reported four
retrieval failures that were really one missing source.

**The last row is the same failure with the volume turned all the way down, and
it is the worst of them.** `gap-appointment` had the right verdict and a stale
reason: the recorded justification and the real one had drifted, and the check
went on passing because the verdict was still correct. Every other row in this
table eventually produced a wrong number that somebody could look at. A stale
reason on a passing test produces nothing at all — nothing fails, nothing is
flagged, no review is prompted, and the note quietly misinforms the next person
who reads it while the suite stays green.

It surfaced only because the notes were being read for an unrelated purpose.
There is no mechanism for that one and it would be dishonest to invent one
here: it is the habit of reading a case's stated reason whenever you touch the
case, and checking it is still the reason.

**The `check_orders.py` row is the first one broken by success rather than by a
change to the code.** Nothing was edited. The first real payment the bot ever
took — the smoke test passing, end to end, exactly as intended — left one
legitimate `owner_confirmed` row in `purchase`, and a check that had passed 55
times began to fail. The assertion meant *this test cleaned up after itself* and
said *the table is empty*; those were the same number for as long as no customer
had ever bought anything, which is to say for as long as the feature did not
work. The fix is to count before and compare after, which is what it always
meant. Worth noting because it inverts the usual reading of a red check: this
one went red because the product started working.

**The frontend row is the same shape crossing a language boundary, which is
where it is hardest to see.** `/fact` returns `conflicts` — plural, a list.
The TypeScript declared `conflict?: unknown` and tested `!= null`. It compiled,
the build was clean, the type checker was satisfied, and the conflict warning
could never appear: `undefined != null` is false for a key that is never sent.
Nothing would have failed. An owner adding a phone number that contradicts a
confirmed one would simply not have been told. It was caught by printing one
real response instead of reading the docstring — which is the only remedy that
has ever worked on this class, and is remedy #1, not a habit: **the type is now
written from an observed response, quoted in the comment beside it.**

### Why it keeps happening

Because the two objects are always *nearly* the same. `facts` really is most of
`context_facts`. A snapshot really was the truth a minute ago. Canonical names
really are the subjects. U+00A0 really does look like a space. Nothing about
the wrong object announces itself as wrong, and the check's own result is the
last place it will show up.

### What actually helps, in order of strength

1. **Remove the ability to have two of something.** One price query, not two.
   One money formatter, not two. One `retrievable_fact` that every retrieval
   path reads. This is the only fix that cannot decay, and it has been the
   answer three times this week.
2. **Make the check read the source of truth, structurally.** `check_payment.py`
   counts the payment rows *before* its transaction instead of asserting a
   literal; the raw-SQL checks bypass `app/` so they cannot inherit a Python
   guard's opinion; `check_orders.py` re-reads the row it wrote rather than the
   dict it passed in — and, after the smoke test, counts `purchase` before and
   after instead of asserting the table is empty.

   **The cheapest instance of this in the whole list is printing one real
   response.** The `conflicts` bug — a warning that could never fire, because
   the code asked for a key the server does not send — cost one `print` of a
   live `/fact` reply to find, and would have survived any amount of reading the
   docstring, because the docstring is *about* the response and is not the
   response. It is worth reaching for first whenever the question is "what does
   this actually return". A build that compiles, type-checks and passes is a
   claim about types; whether the server sends that key is a different claim,
   and only one of the two was ever checked.
3. **Put a can't-fire control in every measurement.** Real purchase intents
   exist in `check_buy.py` solely so a silent zero fails. That control is the
   only reason the alias miss was found.
4. **Ask the question out loud.** When a measurement comes back clean: *could
   this setup produce this result while broken?* It is a cheap question and it
   has a real answer surprisingly often.

The ranking is not decoration, and the gap between 1–2 and 3–4 is a difference
in kind rather than degree. **Only the first is durable.** Removing the ability
to have two of something keeps working while everyone forgets why it was done.
The second decays slowly: a check that reads the source of truth can be
rewritten to read a copy, and nothing stops it.

**3 and 4 are habits, not mechanisms.** They work exactly as long as somebody
keeps doing them, and they have no failure signal of their own — a missing
can't-fire control looks identical to a passing test suite, and an unasked
question looks identical to a good answer. Nobody was assigned to add the
control in `check_buy.py`; it happened to occur to whoever wrote the file that
morning. Write habits down, rely on mechanisms.

## A different failure: a number written down as "measured" that was not

This is **not** the drift pattern and does not belong in that table. Drift is two
nearly-identical objects pulling apart, and every instance of it is discoverable
by comparing them. This one has no second object. It is a plausible number,
written into a comment with the word *measured* beside it, that nobody measured.

Twice this session:

| The claim | What was actually true |
|---|---|
| A `find()`-only diff would have caught the policy ingest | It would have reported **nothing** |
| "uvicorn rejects a request line above ~8 KB, so ~2 500 Cyrillic characters" | ~65 468 characters, so ~10 900 Cyrillic — **5× out**, and in the safe direction only by luck |

The second is the more instructive, because measuring it properly took three
attempts and the first two were also wrong:

- `Invoke-WebRequest` refuses to bind a URI over ~65 536 characters.
- `httpx` raises `InvalidURL: URL component 'query' too long` at the same size.

Both fail at a round number, on the request, before the server is involved —
which is indistinguishable from a server limit unless you already suspect it.
Believing either would have replaced one invented number with a second one, and
this time with a genuine-looking experiment behind it. The real ceiling came
from a raw socket and a binary search: **65 468 accepted, 65 625 refused.**

The damage from the original guess was not a broken screen. `PASTE_LIMIT = 2000`
worked perfectly — it simply refused, silently and forever, four fifths of the
pastes the server would have accepted, with a comment explaining that this was
the measured limit. Nothing fails. Nobody investigates a limit that is
documented.

**There is no mechanism for this one, and inventing one here would repeat the
error.** It is a habit, and it is a smaller one than it looks: *do not write
"measured" unless you measured it, and say which tool measured it.* The tool
matters because two of them lied. A number with no method beside it is a guess
that has been promoted, and the promotion is invisible a week later — by then it
reads exactly like a fact.

### The same day, twice more: the tool is part of the result

**Reasoning stood in for measurement on 360px, and was wrong.** The direction
doc calls a mid-range Android at 360px non-negotiable. Chrome on Windows will
not make a window that narrow, so the README said "reasoned, not observed" —
correctly, and the reasoning was still wrong. A 360px **iframe** is a real 360px
viewport (`vw` and media queries resolve against the frame), and inside one the
Add-knowledge screen measured **364px wide against a 360px viewport**: a
sideways scroll on every phone the product is for. `minmax(340px, 1fr)` is a
floor a grid track cannot go below, so two cards refused to fit in 312px of
content. `minmax(min(340px, 100%), 1fr)` fixes it. The lesson is not about CSS:
*"it should collapse"* and *"it collapses"* are two different claims, and only
one of them had been checked.

**A tool silently corrupted source files during that fix.** A one-line
PowerShell `Get-Content -Raw` / `Set-Content` round-trip over two `.tsx` files
re-encoded every non-ASCII character — 27 em dashes and quotes turned to
mojibake, plus a BOM on each file — because PowerShell 5.1 reads with the system
codepage, not UTF-8. **The build stayed green**: mojibake inside comments and
string literals is valid TypeScript. It was caught by decoding the bytes and
counting the markers, not by anything in the pipeline. Restored from the commit
and redone through Python with an explicit encoding.

This is the most alarming of the four, and it is the only one where the whole
downstream pipeline would have shipped the damage: `tsc`, `vite`, the browser,
and a code review that reads the diff in an editor which renders the mojibake
back as the character you expected. Nothing in that chain has an opinion about
encoding. The obvious remedy — *stop doing PowerShell round-trips on source
files* — is a habit, and by this document's own ranking habits are the weakest
remedy because they have no failure signal. So it is a mechanism now:
**`check_encoding.py`**, which asserts every tracked source file is valid UTF-8,
carries no BOM, and contains no mojibake markers. It takes about a second.

It failed on its first run, on a file nobody had touched in weeks: the root
`README.md` was **UTF-16**, created by a PowerShell `>` redirect in the first
commit and never looked at since. That is the argument for the mechanism in one
line — the corruption had been sitting in the repository from the beginning,
survived every build and every commit, and no habit was ever going to find it.

The through-line for all four: **the tool that produced the number is part of
the number.** PowerShell and httpx both refused a URL at 65 536 and looked like
a server. A window manager refused a width and looked like a browser limit.
PowerShell's default encoding rewrote a file and looked like a successful edit.
None of these announce themselves, and each is indistinguishable from the thing
you were trying to observe unless you already suspect the instrument.

### The related discipline: unrepresentable beats avoided

`OrderError` collided with its own caller twice — once on `reason`, once on
`subject_key` — both times because caller DATA and constructor PARAMETERS
shared one namespace, so any new key in the data could collide. The first fix
renamed the parameter and left a comment. The comment was correct and it did
not help, because a comment is a rule someone has to read at the right moment,
and the second collision arrived a month later in a different file.

`detail` is now one positional dict. There is no shared namespace, so the
collision is not avoided — it is unrepresentable, and `check_orders.py` asserts
that a detail key called `reason`, `code`, `detail` or `self` is just a key.

Prefer the version that cannot come back over the version that documents why it
should not. A rule that depends on being read has already failed once by the
time you are writing it down.

## Booking is deliberately out of scope — 2026-09-05

Not unbuilt. Out of scope, decided, with a reason — because it is the most
natural-looking extension of the order flow and it is not one.

### What decided it

The buy-intent classifier flagged `syn-book-gynae` — "Menga ginekologga zapis
qberila", *sign me up for the gynaecologist* — as a purchase, and it read the
message correctly. That IS a request to act.

**Avisena cannot reserve a slot.** So offering to take payment there charges
someone for a time nobody can promise, and a customer who pays and then finds
there is no appointment is a worse outcome than a customer who was told to
call. Paying for a consultation and holding a slot are two acts; at a market
stall — the brief's reference case — they are one, which is why the original
framing did not separate them. The reference case hid the distinction.

So: **prepayment intent qualifies as buy intent, scheduling intent does not.**
A scheduling request falls through to ordinary retrieval, where the business's
own stated booking instruction answers it — Avisena's policy document says to
call the call centre or come to reception — and if no instruction is stated, it
refuses honestly and logs a gap. Nothing is inferred either way.

### Why it must not arrive as a small extension

Booking is a bigger feature than payments and shares almost nothing with this
code. It needs slot availability, calendar state, per-doctor working patterns
against real dates, double-booking prevention, cancellations, no-shows, and a
reschedule path. `purchase` models none of that, and the resemblance is
superficial: an order is a row someone is waiting on, while a booking is a
claim on a resource that other bookings compete for.

The failure mode if it is bolted on: a bot that confirms appointments it cannot
guarantee. That is the same class of failure as marking an order paid from a
screenshot — the system asserting something it does not know — and it is
excluded for the same reason.

### What was corrected along the way

Three questions were graded `gap` with `want: "no booking procedure exists"`.
That was true on 2026-09-02 and stopped being true on 09-03, when the policy
source was ingested. A `gap` expectation is a claim about the DATA, and it
expires when the data changes — this file has said so since the harness was
rewritten, and it still took a classifier disagreeing with the grader to notice.

- `live-how-to-book` → `prose`. **The same reply has now been graded three
  different ways.** It was WRONG when the bot invented a booking procedure
  around a real phone number; rule 10 made it correctly `gap`, because no
  procedure was stated anywhere; and it is `prose` now that the policy document
  states one. The answer text never changed. Correctness here is not a property
  of the reply — it is a relation between the reply and what the business has
  actually said, and that relation moves when the data moves.

  Which is the argument for re-examining the harness when DATA changes, not
  only when code does. A code change announces itself in a diff. An ingested
  document changes what is true for a dozen questions and touches nothing a
  reviewer would look at.
- `syn-book-gynae` → `prose`. Its note said KNOWN BROKEN and it was not broken;
  the expectation was.
- `gap-appointment` stays `gap` with a corrected reason: the procedure IS
  stated, but the question asks specifically about ONLINE booking and the
  passage says nothing about it. Rule 10 covers it — silence is not a range.
  The verdict was always right and the reason was stale, which is the harder
  kind of wrong to notice.

## The ingestion diff, and three corrections it forced — 2026-09-05

`check_drift.py`. What changed in retrieval since last time, with no model and
no API calls. It narrows which cases to read; it does not judge them, and it
cannot see a stale reason.

### The correction that produced it

The idea was proposed as "diff `check_retrieval.py` before and after an
ingest — it is deterministic and free." The response to that was that it would
have printed "exactly the four prose questions plus `live-how-to-book` and
`syn-book-gynae`" on the day the policy document landed.

**That was stated without being checked, and it was wrong.** `retrieval.py`
never queries `chunk`, and the policy source produced 8 chunks and 0 facts, so
`find()` returns byte-identical results before and after. The proposed tool
would have reported **nothing at all** for the exact ingestion that motivated
it.

Worth recording next to the drift table rather than only being fixed, because
it is the same failure in a new costume and it happened *while that table was
being written*: **a convincing-sounding claim does not announce itself either.**
The verifier reading the wrong object and the author asserting the unchecked
thing are one habit. The claim was plausible, specific, and would have been
believed.

### Two more, found only by running it

**Free and blind is not cheaper than free and useful.** The fix is to embed the
fixed question set ONCE and cache the vectors. Both vector searches then become
pure SQL — the chunk and fact embeddings are already in the database — so every
run after the first is free, deterministic, and covers the whole retrieval
picture. That is `answer()` minus the model.

The cache is stamped with the embedding model and refuses to load under a
different one: across a model change every cached vector is meaningless, and
the arithmetic still works, so the failure would be a diff that is confidently
wrong rather than obviously broken. A question is keyed by its TEXT, so an
edited question re-embeds itself; a missing vector aborts the run rather than
silently diffing 89 of 90.

**A list of everything is the same as no list.** The first working version
reported 90 of 90 questions on a single document ingest. Restricting to what
clears `SIMILARITY_FLOOR` — which is all `answer()` ever uses — barely helped:
88 of 90. That is not noise, it is true. Eight general clinic-policy passages
really are moderately similar to almost every clinic question, and the first
document into an empty chunk table really does change the context for nearly
all of them.

So the fix was not filtering harder but **ranking and capping**: exact-tier
changes first, then fact-window changes, then chunk changes ordered by the
score of what ARRIVED — not by the absolute top score, which the first attempt
used and which ranks questions by how well they already worked.

Verified by simulation, not by argument. The policy chunks were removed and
restored to reproduce the 2026-09-03 ingest exactly. The ranked output puts
`live-how-to-book` first, `live-passport-needed` third, `gap-appointment` sixth
and `what-to-bring-uz` tenth — three of the four expectations that actually went
stale that day are in the top six of a list somebody would read.

### What it still cannot do

It would not have caught `gap-appointment`'s stale REASON on its own. That
question does appear in the list, because its chunk window moved — but had the
ingest not touched its retrieval, nothing here would have said a word. The list
tells you which cases to open. Reading the stated reason once it is open is
still a habit, and habits have no failure signal.

## Window width changes which attribute list expansion picks — 2026-09-05

A latent coupling, found while measuring `FACT_WINDOW` and written down even
though the obvious fix measured badly, because someone will change the window
for an unrelated reason and walk into it.

### The finding

Recall is not monotonic in window width. Measured over the 26 questions the
exact tier does not already answer:

    width  4    21/26
    width  8    20/26      <- widening LOST one
    width 12    24/26

Traced rather than explained away. `typo-uz`, "kardilog narxi qancha":

- At width 4 the top four hold three `qabul narxi` facts, so `_expand_list`
  clusters on `qabul narxi`, pulls in every doctor's consultation price, and
  the wanted fact arrives.
- At width 8 four `narx` facts outvote them. Expansion clusters on `narx`,
  pulls in service prices, and the wanted fact is gone.

`_expand_list` picks exactly ONE attribute — `max(counts)`. So the window is
not only "how much context arrives". **It also decides which attribute wins a
vote, and that vote decides which entire set gets expanded.** Changing the
width silently changes what expansion does. Nobody would predict that from the
name of the constant or from either function on its own.

### The obvious fix, measured before it was proposed

Expanding every attribute that clears `LIST_CLUSTER` rather than only the
commonest:

    width       one attribute      every attribute      context (one / every)
      4            21/26               21/26              8.2  /  8.2
      8            20/26               21/26             14.5  / 16.0
     12            24/26               24/26             16.9  / 20.6
     20            25/26               25/26             22.5  / 33.7

It repairs the width-8 dip and buys nothing at 12 or wider, while carrying half
again as much context at 20. **Not adopted.** Recorded because "we tried the
obvious thing and it did not help" is knowledge, and the next person will
otherwise spend an afternoon rediscovering it.

The coupling itself is NOT fixed. It is dormant at width 12 and it will bite
whoever moves the window next.

### What the measurement structurally cannot see

`check_window.py` asks where the ANSWERING fact ranks, and it takes that fact
from `want`, which names exactly one `Subject / attribute` pair.

So a question needing TWO facts — a doctor's role and their hours — is graded
on whichever one `want` happens to name, and passes while delivering half an
answer. **This is a harness limitation, not a product one, and the distinction
matters:** it is not "we have not measured that yet", it is "the schema cannot
express the expectation, so an entire class of partial-answer failure is
invisible by construction". No amount of running the existing set finds it.

Fixing it means letting `want` name more than one row, which changes
`questions.py`'s shape and every grader that reads it. That is a real piece of
work and it is why the `lavozim`-alongside-`qabul vaqti` item stays open — with
a stated reason now, rather than as a vague todo.

## FACT_WINDOW 8 -> 12, and what the confirming run actually showed — 2026-09-05

Two matched harness runs on the same 90 questions. The old 68/19-of-87 number
was NOT used as the baseline: the set has changed since, and comparing against
it would have attributed set changes to the window — the drift-table mistake.

    width  8    74 pass / 16 fail
    width 12    80 pass / 10 fail
                6 changes, all gains, zero verdict regressions

### The feared direction was measured, and it went the other way

Widening context is the input to the failure mode that produced `tanaffussiz`
and `sizda information yoqmi`: more material for the model to build a plausible
wrong answer from. The gap questions are a third of the set and are exactly
where that shows.

**Wrong answers on gap questions went DOWN, 6 to 4.** Two questions that
answered at width 8 correctly refuse at width 12:

- `live-mrt-fasting` — at 8 it replied "no metal implants… nothing is stated
  about fasting", an adjacent-fact answer to a preparation question. At 12 it
  refuses.
- `live-reschedule` — at 8 it quoted the late-arrival policy as though it
  answered a rescheduling question. At 12 it refuses and points at the call
  centre.

The mechanism is worth stating because it is counterintuitive: with twelve
facts the model can see that none of them answers the question, where eight
merely-adjacent ones look like they must be the answer. More context made
refusal *easier*, not harder.

### The cost is real and it is not in the verdicts

Of 34 questions that kept their verdict but changed their reply, five got
shorter and two of those lost information. Together with `doctors-ru`, the
pattern is one thing:

**Replies to list-shaped questions are summarised harder.** Rule 7 asks for one
or two sentences, and there is now more to compress into them.

- `doctors-ru` — "which doctors do you have" named **twelve** doctors at width
  8 and **five** at width 12. Route grading scored this as FAIL -> PASS,
  because the wanted `lavozim` fact finally reached the context. The verdict
  improved while the answer got worse, and nothing in the harness can see that.
- `syn-results-ready` — enumerated all five turnaround times at width 8; at 12
  it gives two and says "for example".
- `price-uz-cyr` — gave both the first-visit and repeat price at 8; only the
  first at 12.

This is NOT an argument for reverting. The window change is what put the right
facts in front of the model; the compression is a separate defect that the
extra context exposed rather than caused.

### It also diagnosed the open list-expansion item

`doctors-ru`'s context, at both widths:

    context by attribute: {'daraja': 13, 'lavozim': 5, 'xona': 2}
    EXPANSION pulled:     {'daraja': 8}

`_expand_list` clusters on **`daraja`** — years of experience — and pulls all
thirteen of those, while `lavozim`, the doctor's actual role, is never expanded
and arrives two to five at a time. The open item was recorded as "list
expansion does not bring `lavozim` alongside `qabul vaqti`". That was the
symptom. The cause is the single-attribute vote picking a nearly useless
attribute for the question and starving the useful one — the same defect as the
`typo-uz` width coupling, with a different victim.

So there are now two known failures of the same one line, `max(counts.items())`,
and the earlier measurement that rejected "expand every attribute over the
threshold" only tested it against RECALL. It was never tested against reply
quality, which is where the damage actually is. That is the next thing to look
at, and it is a generation question, so it needs a harness run rather than SQL.

## Two Telegram bugs found by reading rather than running — 2026-09-05

Both would have fired on the first real payment, and neither is visible to any
check in this repository, because every check here stops at the edge of the
Telegram API.

**`editMessageText` cannot edit a photo.** The owner's payment review is the
screenshot with two buttons on it, so the message has a CAPTION and no text,
and `editMessageText` answers `400: there is no text in the message to edit`.
Every Confirm and every Reject would have failed at the instant the owner
tapped -- leaving the order in `awaiting_owner`, the buttons still live, and the
customer told nothing. `edit()` now takes `as_caption`, and the caller reads
which kind it is off the callback's own message rather than guessing, because
the fallback in `owner_review()` sends plain text when `sendPhoto` fails and
then it really is text.

**Six of seven call sites were converted, and the seven-th shape was missed.**
The first pass replaced `edit(chat_id, message_id, ` -- with a trailing space --
and every MULTI-LINE call ends in a newline after the comma instead. Eight of
them, including both order-confirm edits, which are exactly the photo ones. The
search string was about a different program than the one on disk, which is the
same fault as `check_bot.py` scanning for one dispatcher shape, in the same
hour. Fixed by routing all of them through one closure, so the decision is made
once rather than at seven call sites where six would have been right.

### What still cannot be found this way

`sendPhoto`, `callback_data` round-trips and inline keyboards are only really
tested by a person tapping them. The smoke test is `docs/smoke-test.md`, and it
deliberately provokes the paths nobody designed: a double-tapped Confirm, a
screenshot sent before any order exists, and two screenshots for one order.

## Multi-tenancy is enforced below the query layer — 2026-09-07

`business_id` on `source`, `fact`, `alias`, `chunk`, `purchase`, plus a
`business` table. Migration `0007_multitenancy.sql`.

The failure being designed against is the worst class this project can have: a
query missing its filter returns another business's data. One clinic's price
answering another clinic's customer, silently — no error, no wrong number
anyone can look at, and it would pass every check we had. There are 63 SQL table
references across 13 modules in `app/`, and "everyone remembers the WHERE
clause" is discipline. Discipline has no failure signal. Same argument that
turned the payment exclusion into a view plus a check constraint in 0005.

So the filter is row-level security, and a forgotten `WHERE` returns that
tenant's rows rather than everyone's.

### Three ways RLS does nothing while looking enabled

All three were live in this database, and none of them raise. This is the part
worth keeping, because it is the same shape as every instrument failure in the
section above: the configuration would have passed inspection.

1. **A superuser bypasses RLS entirely.** Not partially, not with a warning, and
   not overridable — not by `FORCE`, not by any policy. Measured before the
   change: `current_user` postgres, `usesuper` true. Enabling RLS on all five
   tables would have applied and changed nothing.
2. **A table's owner bypasses its own RLS** unless `FORCE ROW LEVEL SECURITY` is
   also set. All five tables were owned by the connecting role.
3. **A plain view evaluates RLS as the view's owner, not the caller.** That is
   `retrievable_fact` — the view *every* retrieval path reads. Postgres 15+
   needs `WITH (security_invoker = true)`. Without it, every answering query
   would have read every tenant's facts through a view that looked correctly
   filtered.

The response to (1) is `app/db.py: assert_app_role()`, which **fails the boot**
rather than warning. It is outside the migration on purpose: SQL cannot stop
someone pointing `DATABASE_URL` back at postgres afterwards. A warning gets read
once and scrolled past for the rest of the deployment's life.

### Why this was cheap: the connection is already passed down

46 functions across `app/` take `conn` as a parameter and never touch the pool.
Only `main.py` (21), `extract.py` (3) and `bot.py` (3) check a connection out.
So the tenant binds at the checkout — `app.db.connection(business_id)`, which
is now the only checkout in the codebase — and the other 46 inherit it without
being edited. That was not designed for this; it just happened to be the shape
that made a mechanism affordable instead of a 63-site edit.

The same property makes the write path free. `business_id` has a column default
of `app_current_business()`, so every existing `INSERT` — none of which mention
the column — gets the connection's tenant, and the policies' `WITH CHECK`
clauses make naming a different one impossible.

And the payment exclusion needed **no tenancy logic at all**. `tolov
malumotlari` is a Talkwisp convention with the same normalized spelling for
every business, so the view's predicate is unchanged; RLS on `fact` scopes it
for free. Two mechanisms below `app/` composing without either knowing about the
other is the argument for building tenancy this way rather than as a rule.

### What RLS does not cover, and there is no mechanism for it

**Unique constraints are outside RLS.** A policy filters what a query *sees*; a
unique index checks what *exists*, across every tenant, and nothing narrows it.
There were two, and one was load-bearing:

- `alias (alias_key, subject_key)` — two clinics may each have a "Rasulova". The
  second one was unwritable.
- `purchase_open_amount_idx` on `amount` — the urgent one. The unique-amount
  trick exists so a seller can tell two payments apart **against one card**. Two
  businesses have two cards. Left global, business B's open order silently
  blocks business A from creating theirs, and it surfaces to a real customer as
  an unexplained failure to place an order.

Both now lead with `business_id`. There is no mechanism that catches a missed
one — the only defence is the block comment in the migration saying so, placed
where the next person adding a table will read it.

Two further consequences of a global unique constraint, both real: a write fails
for a reason the writer cannot see, because the colliding row is in a tenant
they cannot read; and the error is an inference channel, since a uniqueness
violation proves a value exists somewhere you have no access to.

**Files are not rows.** `gaps.jsonl`, `feedback.jsonl` and `messages.jsonl` had
no tenant. The gap log in particular is a per-business feature — "what your
customers asked that I could not answer" is on the dashboard — so all three now
carry `business_id` per line. It has to be written at the time: a log without it
cannot be split afterwards, and afterwards is the only time anyone reads it.

The value comes from a `ContextVar` set in the *same statement* that sets the
Postgres GUC, so the file and the database cannot disagree about which business
a line belongs to. One setter, not two.

### The bot's tenant is its token

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_ID` could only ever describe one
tenant. Both moved onto the `business` row, and the bot resolves which business
it serves from the token that received the update. That is the real multi-tenant
shape — one poll loop per token — and it makes onboarding a new customer the
same action as fixing the current install: create a bot in BotFather, paste the
token.

`bot_token` lives on `business` and never in `fact`, by the 0005 argument: a
fact is retrievable, a retrieved fact enters the answering prompt, and a model
that can paraphrase a card number can paraphrase a token. No app-role query can
read another business's token either — the `business` policy is `id =
app_current_business()`, and the only two things that see across tenants are two
`SECURITY DEFINER` resolvers that each return an id and nothing else.

### The check runs as the role that ships

`check_tenancy.py`, 28 checks. The standard it holds to is the one this project
keeps relearning: **a check must read the object the behaviour uses.** A tenancy
test run as postgres would pass or fail on a different object from the one that
deploys, because superusers bypass RLS — exactly the drift pattern. So every
check connects as `talkwisp_app`, and the endpoint checks go through the real
FastAPI app, the real dependency and the real policies, overriding only *which
business the request is for* — the thing auth will supply later and that does
not exist yet.

Section 2 runs the same `select count(*) from fact` as both roles and shows the
two answers: postgres counts everything, the app role raises.

**The `SET LOCAL` proof, and its two controls.** Everything above rests on
`set_config('app.business_id', …, true)` not outliving its transaction on a
pooled connection — if it did, the setting would follow the connection to the
next request and the next business would read the previous one's rows with
nothing failing. That was reasoned, not measured, so it is now measured, with
two controls without which the section proves nothing:

- `pg_backend_pid()`, asserted equal across checkouts. A *fresh* connection
  would also show no setting, so "the value is gone" is only evidence if it is
  the same connection.
- A bare `SET`, which must be **seen to survive**. A test that cannot detect
  survival at all has nothing to say about `SET LOCAL`. This is the negative
  control the payment check taught us to write.

Both pass: the pid matches, `SET LOCAL` is gone, and the bare `SET` survives.

### A zero that looked like a count — 2026-09-10

Rendering the landing page from design-tool source to static HTML reported:

```
  0 hover rules, 11 links wired, 1 removed
```

Read as "there were no `style-hover` attributes to convert". There were five.

The design-tool runtime consumes `style-hover` and inserts the equivalent CSS
through the **CSSOM** — `sheet.insertRule` against a `<style>` element it leaves
**empty in the markup**. So the rules exist in the live document, apply
correctly, and are invisible to `outerHTML`, which is what `page.content()`
returns. Querying `[style-hover]` after the runtime had run found nothing,
because the runtime had already removed the attributes.

The output would have shipped a page where every button silently lost its hover
state. Nothing errored. The check that should have caught it — a count of
converted rules — reported zero, and zero is a legitimate value for that count.

Same shape as every other row in the table above: **the object measured and the
object that matters were different.** The DOM's serialisation and the DOM's
computed style are two different things, and a rule inserted through the CSSOM
lives in exactly the gap between them.

The fix reads the rules back out of `document.styleSheets`, skipping sheets
whose `ownerNode` already has text — so whatever the runtime actually generated
is captured, rather than this script re-deriving it from the source attributes
and hoping the two agree. Recovered `.scp0` (four blue CTAs) and `.scp1` (the
question chips), which matches the 4x / 1x split in the source exactly.

The generalisable part is narrow and worth keeping: **when a tool transforms
something, do not verify the transformation by re-reading your own input.** The
count was derived from the same query that did the work, so it could only ever
agree with itself — the third instance of that pattern in this project, after
the auth.py query extraction and the unique-constraint check.

### The unique-constraint check was blind, and only a control found it

The first version of `check_tenancy.py` section 4 wrote each row inside a
transaction it then rolled back, one tenant at a time. So A's alias was already
gone by the time B's was written and **the two rows never coexisted** — which is
the only condition under which a global unique index can fail.

It reported both businesses happy. It would have reported both businesses happy
against the pre-0007 schema too.

That was measured rather than reasoned: the old global indexes were recreated
alongside the new ones and section 4's exact code re-run.

```
  the same alias:              BLIND - passed with the GLOBAL constraint in place
  the same open-order amount:  BLIND - passed with the GLOBAL constraint in place
```

Two green checks about the one part of tenancy that RLS does not cover, neither
of which could fail. The fix is one line of sequencing — commit A's row before
attempting B's — but the fix is not the point. The point is that the check had
the right name, the right table, the right two tenants, and still tested
nothing, and nothing about reading it says so.

So the control is now **inside** the check. Each pair runs twice: once as the
schema stands, where both inserts must succeed, and once with the pre-0007
global index temporarily restored, where the second must fail.

```
  [ok  ] both businesses may have the same alias
  [ok  ] control: with the OLD global index, the same alias is refused
  [ok  ] both businesses may have the same open-order amount
  [ok  ] control: with the OLD global index, the same open-order amount is refused
```

This generalizes past this file, and it is the same lesson as the bare `SET` in
section 1 one screen above: **a check that has never been observed to fail is a
claim, not evidence.** The cheapest way to find out is to break the thing on
purpose and confirm the check notices. Section 1 had that control from the
start because the assumption was flagged as unmeasured. Section 4 did not,
because "insert the same alias for two businesses" reads like it obviously works
— and reading it is exactly what fails to catch this.

Added to the remedy ranking as a corollary to #2 (make the check read the source
of truth): **for any check whose failure mode is silence, restore the bug and
watch it go red.** It is a mechanism, not a habit, when the restoration lives
inside the check itself rather than in someone's memory of having done it once.

**And the asymmetry between the two sections is the part to keep.** Section 1
had its control from the beginning, and only because the assumption underneath
it had been explicitly labelled *reasoned, not observed* — the label is what
made someone go and check. Section 4 had no control, and not because anyone
weighed it and declined: the question never came up, because "insert the same
alias for two businesses" reads as obviously working.

So the selection pressure runs the wrong way. **The checks most likely to be
blind are the ones that look most obviously correct**, because looking obviously
correct is exactly what stops anyone asking whether they can fail. A check that
makes you uneasy gets a control. A check that reads cleanly gets a green tick
and no further thought, and it can sit there for years being about nothing.

Which means the trigger for writing a control cannot be "this one feels
uncertain". That instinct is anti-correlated with where controls are needed. The
usable trigger is structural instead: **does this check have a failure mode that
would be silent?** Section 4's failure mode was two rows never meeting — nothing
raises, nothing prints, the count is right. That question can be asked of a
check that looks perfect, and this one's answer would have been yes on the day
it was written.

### A check that had never been run against itself

`check_encoding.py` went green at 87/87 the day it was written, then failed the
moment it was re-run — on itself. It necessarily contains every mojibake marker
as the literal that defines it, and `git ls-files` had not listed the file on
the first run because it was not yet committed. So the first green result was
over 87 files that did not include the one file guaranteed to fail.

Another entry for the list: the first run of a check is measuring a slightly
different set from every run after it. It now skips its own marker scan, by
name, with the reason written down, and is still checked for a BOM and valid
UTF-8 — the two failures it can actually have. 88/88.

### What is deliberately still missing

- **Auth.** There is none. `app.main.current_business()` is the stand-in and
  returns `app_sole_business()`, which **raises** when there is more than one
  business rather than choosing. That is what makes deploying auth before
  tenancy fail loudly instead of merging two businesses into one table with
  nothing to separate them by afterwards.
- **Self-serve signup**, which must not exist until auth does.
- **A rate limit on extraction.** Auth turns an anonymous Gemini-token burn into
  an attributable one, which is enough for v1 and is not the same as fixing it.
- **`escalation`**, still deferred. See the Open list.

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
- Multi-tenancy. Tables carry no `business_id` yet; the schema is single-tenant-
  shaped on purpose. Adding it later is a known migration — add a column,
  backfill one value, extend the indexes. Not an oversight. A `business` *table*
  would be a fifth table and needs approval.

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
   dict it passed in.
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

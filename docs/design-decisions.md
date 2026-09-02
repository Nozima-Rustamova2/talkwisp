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

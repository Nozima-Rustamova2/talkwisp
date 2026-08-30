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
- **The 30-question set in `questions.py` currently passes 30/30, which means it
  has stopped discriminating.** A green run can no longer tell you a change made
  things worse. Do not read it as "the system is correct" — read it as "nothing
  broke in a way I had already thought of."
  Every one of the three real bugs found on 2026-08-29 — the two-of-six doctor
  list, the too-narrow retrieval window, and the "ва" inside "вас" language
  misdetection — came from **live Telegram messages, not the harness**.
  The fix is to harvest `messages.jsonl` into new cases: real phrasings, with
  route and scores already recorded, so grading is mostly confirming what should
  have happened. Do this after the demo, when there are real questions to
  harvest.
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

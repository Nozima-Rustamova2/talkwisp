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

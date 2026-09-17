# Asking for more detail before refusing

**Status: built, measured, removed. Not worth shipping as designed.**

When the agent cannot answer, ask once for more detail before offering to
forward the question — because some refusals are the question being
underspecified rather than the knowledge being missing. `online mi` was two
words with no subject, and the fact was there.

---

## What killed it: the trigger fires on well-formed questions

Three triggers were proposed and the data killed all three.

**"Retrieval returned nothing"** describes **0 of 496** logged refusals. Vector
search always returns nearest neighbours, so `best_similarity` is essentially
always populated, and **495 of 496** were *above* the 0.55 floor. Retrieving
well and being declined is not the exception here, it is the normal case.

`online mi` is the counterexample to the premise itself: measured at **0.628**,
comfortably above the floor, and genuinely underspecified. The score cannot
separate the two.

**A word-count threshold** repeats a mistake this codebase already made and
documented. `app/followup.py` sets `MAX_WORDS_WITHOUT_REFERENCE = 2` with a
comment explaining why a looser threshold was wrong: *"Uzbek questions are
compact… a looser threshold sent both to the rewriter, which is the false
positive this whole gate exists to avoid."*

**"Reference-dependent AND no conversation history"** was the third, and it is
the one that was actually built. It is `followup`'s own test plus the gap that
test deliberately leaves — with history the rewriter resolves these, and
`needs_rewrite()` returns False when there is none.

It is still wrong, and the measurement says so. Against **503 real refusals** it
fires on twelve distinct questions:

| question | genuinely vague? |
|---|---|
| `qaysi tekshiruv` — "which examination" | **yes** |
| `Siz kimsiz?`, `kimsan` | meta — answered before this point now |
| `Zor` — "great" | not a question |
| `MRT qilasizmi?` — "do you do MRI?" | no |
| `Nechida yopilasiz?` — "what time do you close?" | no |
| `mrk bormi` — "is there MRI?" | no |
| `Ertaga ishlaysizmi?` — "do you work tomorrow?" | no |
| `Indinga ishlaysizmi?` — "the day after tomorrow?" | no |
| `Navbat koʻp durmi hozir?` — "is the queue long now?" | no |
| `buxoroda yoqmi`, `Nechida yopilasila?` | no |

**THESE FIGURES CAME FROM A ONE-OFF COUNT WITH NO SCRIPT**, against Avisena
Med's log on the box. Nothing in the repo could regenerate the 503 or the twelve,
and a figure nobody can regenerate decays into a claim. Since 2026-09-17 the
count is reproducible, and no longer one business's:

    uv run python harvest.py --business <id> --triggers --compare <avisena id>

It prints refusals, how many trip `underspecified()`, and how many of those had
no conversation history — per business, side by side — then lists the questions
it fires on with an EMPTY "vague?" column. The script counts and never judges:
whether a question is genuinely vague is read by a person, and that reading is
what made this table worth trusting. History is reconstructed from the log (an
earlier line from the same chat within 20 minutes), not logged, so a bot restart
makes it overcount history. Until it has been run, treat the numbers below as
Avisena's alone and unverified by a second count.

The reason the comparison matters: a clinic's customers ask complete questions,
a course seller's reply to things. If a second business's share is meaningfully
higher, the trigger may be right for one business type and wrong for another,
rather than simply wrong.

**Roughly one true positive in twelve.** The no-history condition filters none
of them: *"Ertaga ishlaysizmi?"* as somebody's first message is the normal case,
not the edge.

And it fails the feature's own acceptance criterion. Asking twice "delays the
escalation that would actually help" — and for eleven of those twelve, asking
*once* does exactly that: the customer asked something clear, we cannot answer,
and instead of offering to forward it we ask them to rephrase. The escalation
arrives one round later than it should.

## What was good about it, and is worth keeping

**Asking at most once was structural rather than a flag.** `remember()` puts the
exchange into history, and the trigger requires history to be empty — so the
condition is self-limiting. No column, no boolean, nothing to keep in sync. If
this is ever rebuilt, keep that shape.

Known window: history is in memory and the supervisor restarts bots, so a
restart between the two messages would ask a second time. Twenty minutes, one
extra question, not worth a table.

**The copy had two forms.** Where retrieval found nearby subjects, name them —
"can you say more?" from something that clearly knows the neighbourhood reads as
stalling. Where it found none, say what the detail is *for*: *"Savolingizni
biroz toʻliqroq yozing — nimani bilmoqchisiz? Shunda aniq javob beraman."*

## If it is rebuilt

The only trigger that could work is **a model call on refusal** to classify
underspecified-or-not. That path has already spent a generation, so the cost is
one more on the worst path only, and it is testable against the twelve questions
above. It is a different design from the one that was built, which is why it was
not substituted silently.

## What survives in the code

`followup.underspecified()` remains, as the single home of the word-count rule
and the reference-word list that `needs_rewrite()` uses. Its docstring points
here, because the obvious misreading — treating it as a test for "vague" — is
exactly what this document disproves.

## A note on how this ended up shipped

The branch was written, measured, recommended against, and left uncommitted —
and then swept into `f2a5d24` by a `git add bot.py` for an unrelated fix three
commits later. It reached `main` and was never deployed. Found when
`check_all.py` ran the whole suite for the first time and `check_clarify.py`
came back red: its failing assertion was the finding, sitting in a suite nobody
had a single command to run.

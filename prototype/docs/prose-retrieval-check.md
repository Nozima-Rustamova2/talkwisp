# Prose retrieval — VERIFIED 3 Sep 2026

**Status: the claim holds.** The row may stay as written on all three screens.
Verified on a real clinic policy document: one document in, boundaries exact,
nothing dropped.

## The claim is true for a stronger reason than the copy implies

It is **not** that a splitter respects paragraph boundaries. A model *proposes*
passages, and anything that does not appear verbatim in the source is
discarded. So the guarantee is: **what is stored is exactly what the document
said.** That is stronger than not-splitting — it rules out paraphrase and
invention, not just mid-sentence cuts.

Design consequence: the source panel can quote a stored passage with confidence
that it is verbatim source text. That is what the panel has been promising all
along, and it is now backed.

## Two remaining risks the design side owns

### 1. Coverage, not splitting — the completeness problem in the prose lane

Boundaries are trustworthy; **presence is not reported**. A document with
run-on paragraphs, bullet lists or a table can produce different boundaries,
and nothing reports a paragraph that was simply never proposed. Passages under
60 characters are silently dropped.

So the honest limit is: **what is shown is accurate, but the system cannot
currently tell you something is missing.**

What this means for the UI:

- The review screen's policy row must not imply a complete reading. It says
  *"2 pages of policy text · Kept whole, not split into facts"* — accurate, and
  it makes no completeness claim. Keep it that way. Do **not** add a passage
  count that reads as coverage (*"all 6 passages"*), and never a percentage.
- A short line in a policy — a one-line rule like *"Yakshanba — dam olish
  kuni"* — can vanish without trace. If an owner reports the agent not knowing
  something the document plainly says, this is the first suspect.
- The eventual honest affordance is a *See the full text* view that shows the
  original document beside what was stored, so a missing passage is visible by
  comparison rather than by claim. The link already exists on the review screen
  and is currently inert; this is what it should do.

### 2. Cross-language: "quotes" becomes "translates faithfully"

Rule 5 makes the agent answer in the customer's language. So if the policy is
in Uzbek and the customer asks in Russian, the reply **cannot** be a verbatim
quote.

- Within a language: the agent quotes. The copy is exactly right.
- Across languages: the agent translates the stored passage faithfully. Not a
  lie, but not what *"quotes it"* says either.

Worth naming in the UI eventually, in the trilingual product where mixed-script
sources are the norm rather than the exception. Candidate treatments, none built:

- On the policy row, where the source language differs from the agent's reply
  language: *"Quoted in Uzbek · translated when a customer writes in Russian."*
- In the test console (step 4), where the owner will first see this happen: if
  the retrieved passage's language differs from the reply's, label the answer
  as translated rather than quoted, and show the original passage beneath it.
  This is the cheapest place to be honest about it, because the owner sees the
  mechanism instead of reading a caveat.

Neither risk blocks anything today.

---

## What was verified

Fixture: a ~380-word clinic policy in Uzbek with a Russian summary section —
headings, multi-sentence paragraphs, two paragraphs over 400 characters, a list
under a heading, and a price range appearing in prose twice.

Result: one document, boundaries exact, no paragraph split mid-thought, nothing
dropped. The price range in prose did not become a competing fact row, so the
conflict-generator risk did not materialise on this document.

Not tested, and therefore still open: run-on paragraphs with no clear breaks,
bullet lists, tables, and passages under 60 characters (known to drop).

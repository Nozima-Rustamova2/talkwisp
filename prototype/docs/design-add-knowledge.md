# Screen C — Add knowledge

Built. File: `Add Knowledge.dc.html`. Written 2 Sep 2026.

## The job

The owner has an agent that knows nothing. This screen is how it learns. It has
to work for a clinic owner who has: a photo of a printed price list, a Word doc
of policies, and a head full of facts nobody has written down. Getting from
"nothing" to "the agent can answer something" in one sitting is the whole point.

Also serves as the post-onboarding landing (step 2 of the sequence), entered in
its first-time posture.

## Scope, made visible

Knowledge belongs to the business. The header drops the agent switcher and shows
the business instead (*Shifo Med — clinic*), and the page subtitle states it:
*One knowledge base, shared by both your agents.* Nothing on this screen is
agent-scoped.

## The four-paths problem, and how it was resolved

Four input paths — upload a file, paste text, type facts directly, connect
Google Sheets — are four different mental models. A naive layout is four boxes
in a form dump. They are also not equal in weight: most owners upload a photo or
type; Sheets is for agencies and spreadsheet people.

**Resolution: upload and paste are one affordance, typing is a second one always
open beside it, and Sheets is a single line, not a panel.**

- Upload and paste collapse because they are the same intent — *"I have
  material, you read it."* One zone, two ways in, no decision made in advance.
- Typing is a different intent — *"I know this, write it down"* — so it gets its
  own zone, permanently open, never behind a click. It is the only path that
  needs no file, no network round trip, and cannot fail.
- Sheets is one link at the foot of the read zone. OAuth with an English consent
  screen at minute two is a real drop-off point: reachable in one tap, in the
  way of nothing.

### Zone weighting (decided)

The read zone **leads and is the wider of the two** (considered and rejected:
leading with typing because it can't fail). Rationale: a photo of a price list
is the single highest-yield action available — one photo is usually 30–40 facts.
The typing zone sits beside it, already open, needing no click.

## Layout

1. **Thin status plate.** Returning: *The agent knows 214 facts from 8 sources ·
   last added 2 days ago · 6 waiting for review.* First time: an invitation, not
   an empty state.
2. **Two input zones side by side** (stacking narrow), read zone wider.
   - **Give it something to read** — dashed field, *Choose file* and *Paste
     text* buttons, format/size small print, Sheets line at the foot.
   - **Write a fact yourself** — free-line entry by default, with *Use fields
     instead* expanding to structured entry (subject, kind chips, value field
     that swaps by kind). Price range gives two loose UZS fields, marked
     approximate, and the agent always says "about".
3. **Activity band**, full width, hairline rows — the "what happened" half. A
   summary line above it so partial state reads at a glance (*2 files read · 1
   reading · 1 couldn't be read · 2 typed*).

## The asymmetry that matters

Adding is not done until the owner sees what was understood.

- **After a file or paste:** how many facts, from how many pages/sheets, and a
  route into Review. Extracted rows always carry a count and a review route and
  never claim to be confirmed.
- **After typing:** the fact appears **confirmed immediately**, no review step,
  because they wrote it. Typed rows carry a *Confirmed* tag and only *Edit* —
  no progress bar, no review link.

Both land in the same activity band, so the difference is visible side by side.

## Decisions

- **Typed entry is free-line by default**, expandable into fields (rejected:
  fields-only, and free-line-only). Faster to type; fields available when the
  owner wants structure.
- **Finished extraction stays put and offers the link** — *Review 42 →* —
  rather than auto-navigating to Review. Lets the owner dump all their material
  in one sitting before switching modes.
- **Paste into the typing zone is treated as the owner's own words** (confirmed,
  no review). Breaking that promise would be worse than an occasional misroute.
  But a paste over ~180 characters or 4+ lines triggers one inline, reversible
  offer: *"That's a lot of text for facts you're writing yourself. Send it to be
  read instead?"* → *Read it instead* / *No, these are my own words*. No silent
  reclassification.
- **`Review N →` links are the only accented items in the activity band.** They
  are the most valuable links on the screen; every other row action is
  neutral-700, and the status plate's review link is plain grey with no arrow so
  it doesn't compete.
- **No "Take a photo" button.** The Android file picker already offers the
  camera.
- **Parse output is shown in the owner's language, in full words** — *Monday to
  Friday, 09:00–14:00*, *dushanbadan jumagacha*, never an abbreviation scheme
  like `du–ju` that the owner won't recognise as their own input.
- **Add button is *Add to knowledge base***, not "Add fact" — the box accepts
  one fact per line and a singular label reads as an error to someone who typed
  three.

## Copy that's doing real work

- Failure row: *"We couldn't read this photo — it's too blurry to make out the
  numbers."* plus *"Nothing was added. Your other files kept going."* — this is
  what stops a blurry photo from feeling like the whole upload broke.
- Offline row: *"1.8 of 4.3 MB sent and kept. Closing this page won't lose it."*
- Price help: *"Stored as approximate. The agent always says 'about'."*

## States

- **First time** — status plate becomes an invitation (*Your agent doesn't know
  anything yet*, plus the highest-yield first move); activity band is replaced
  by three things other clinics start with. No fake zeros, no ghost rows.
- **Returning** — counted plate; different posture, they're adding to something.
- **Extraction running** — non-blocking. Per-file progress and byte counts
  (*2.9 of 4.3 MB sent*); the owner can add another file or type while one runs.
- **Failed** — per file, plain words, two recovery actions (*Retry*, *Upload a
  clearer photo*), other files unaffected.
- **Partial** — summary line plus per-row states, readable at a glance.
- **Offline** — rows go to *Waiting for connection — will resume*, bytes already
  sent are kept, retry resumes rather than restarting.
- **Sheets connected** — foot line becomes *Narxlar (Google Sheet) — synced 40
  minutes ago · Sync now*.

## Not built

- Real drag-and-drop, file picker, camera intent — the read zone is a click
  target only.
- The Sheets dialog (plain-language explanation before Google's consent screen);
  the link just flips to a connected state.
- Free-line parsing is faked with one fixed read-back; it doesn't reflect input.
- Retry / Cancel / Try now are inert. No per-file removal, no "clear finished".
- No dedupe warning when a file re-states an existing fact — that collision
  surfaces in Review (D) instead.
- Mobile breakpoints; UZ/RU translation.

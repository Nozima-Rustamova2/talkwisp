# Screen C — Add knowledge

Design intent. States behaviour the backend has to support, not markup.
Prototype HTML in `prototypes/` is a visual reference with fake data — never
build an API to match the mock rows.

---

## Scoping

Knowledge belongs to the business, not to an agent. The header names the business,
not an agent, with a quiet line under the page title: "One knowledge base, shared
by both your agents." Nothing on this screen is scoped to an agent. Reached from
the Knowledge nav item, never from inside an agent.

---

## The job

The owner has an agent that knows nothing. This screen is how it learns. It has
to work for someone who has a photo of a printed price list, a Word doc of
policies, and a head full of facts nobody has written down.

Getting from nothing to "the agent can answer something" in one sitting is the
point.

---

## Four input paths, resolved

Not four boxes, and not tabs — tabs hide three paths and force a choice of mental
model before anything is understood.

**Upload and paste collapse into one affordance.** Same intent: "I have material,
you read it." One plate — drop a file on it, tap to pick from the phone, or paste
text into it, which swaps it to text mode with a `Read this text` action. One box,
two ways in, no advance decision.

**Typing is a separate panel, permanently open, never behind a click.** Different
intent: "I know this, write it down." It's the only path needing no file, no
network round trip, and it cannot fail.

**Google Sheets is one line at the foot of the read plate**, not a panel. Opens a
dialog explaining in plain language what Google will ask, before the English
consent screen appears. Reachable in one tap, in the way of nothing.

### Typing: free line, not fields

One free-text line per fact — "Dr. Rasulova, cardiologist, Mon–Fri 9–14". Parsed
on submit, shown split into its parts and editable in place. The owner's confirm
is the confirmation; it never enters Review, because they are the source.

Structured fields are an escape hatch (`Use fields instead`), not the default.
Forcing three fields per fact makes entering ten doctors feel like data entry.

No per-fact language tag. Script and apostrophe normalization happens in the
ingest pipeline regardless.

### File picking

One button. On Android the OS picker already offers the camera — a separate
"Take a photo" button is clutter on the device that matters most.

---

## Layout

**Thin status plate**, informational, no button — the primary action is the input
directly below. Returning posture, one line: what the agent knows, how many
sources, when last added, how many wait for review. The review count is the only
link.

**Two input zones side by side**, stacking on a phone, read plate wider (~3:2).

**Activity band**, full width, hairline rows — the "what happened" half of the
screen. A one-line summary above it so partial state reads at a glance:
"3 files read · 1 reading · 1 couldn't be read."

Row types:
- **Read** — filename, count of facts found, route into Review
- **Reading** — page progress, byte progress, Cancel
- **Failed** — plain words ("we couldn't read this photo — it's too blurry"),
  Retry and Upload a clearer photo, plus explicit reassurance that nothing was
  added and other files kept going
- **Typed** — confirmed tag, no progress, no review route

**The asymmetry is visible here by design.** Typed facts land in the same band as
files but carry a confirmed tag and no route into Review. Extracted rows always
carry a count and a review route and never claim to be confirmed.

`Review N →` links are the most valuable thing in the band and should be the only
accented element in it.

No internal vocabulary in the UI. The owner doesn't know there are four paths.

---

## After a file finishes

**Stay on the screen and offer the link.** Owners think in stacks — photo, doc,
another photo. Navigating away after file one interrupts the dump and files two
and three never get uploaded.

The counter must be loud and accumulate as files land ("38 facts read —
Review them →"). If the link is quiet, people leave without confirming anything
and the agent stays dead.

---

## What the user does first

Nothing is pre-focused. Both zones are visibly usable. The read plate is the
larger object, so the eye goes to dropping a photo of the price list — the
highest-yield action available, since one photo can be forty facts. If they have
no file, the typing panel is already open beside it with no click required.

---

## Mobile and bad connections

A 4MB phone photo over mobile data is the normal case. Per-row byte progress.
On connection loss the row goes to "Waiting for connection — will resume".

**Resumable upload is not yet supported by the backend.** The design shows it;
until chunked upload exists, retry restarts but keeps the file reference so the
owner doesn't re-pick it. Do not build UI that claims resumption works before it
does.

Nothing blocks the screen — a file reading in the band doesn't stop them
uploading another or typing.

---

## States

**First time.** Status plate becomes an invitation ("Your agent doesn't know
anything yet") with one sentence on what to give it first. No fake zeros. The two
zones are unchanged. The activity band is replaced by three example lines of what
other businesses start with.

**Returning.** The counted plate above.

**Running.** Non-blocking, as described.

**Failed.** Per row, plain words, two actions, other rows unaffected.

**Partial.** Summary line plus row states.

**Offline.** Paused rows, work kept.

**Sheets connected.** The foot line becomes the sheet name, last sync time, and
`Sync now`.

---

## Backend promises made by this screen

Verify these exist before shipping the UI that claims them:

- Extraction is one transaction per file — a failure leaves nothing behind
- Prose is kept whole and quoted, not split
- Typed facts are written confirmed, never entering review
- Free-line facts are parsed into parts and returned for in-place confirmation
- Per-file progress is reportable while other files continue

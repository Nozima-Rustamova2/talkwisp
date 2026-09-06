# frontend

The owner's screens. Vite + React + TypeScript, wired to the real API in
`app/main.py`. Two screens:

- **Add knowledge** (`#/`) — from `prototype/Add Knowledge.dc.html`
- **Review** (`#/review`) — from `prototype/Onboarding Review.dc.html`, at
  ledger density rather than the onboarding card

Routing is the **hash**, not the path. `StaticFiles` serves `index.html` for the
mount root and 404s below it, so `/app/review` would work while clicking and
404 on reload or on a shared link. Real paths need a server catch-all first.

## One process

`uvicorn` serves both the API and this. There is no second server and no dev
proxy.

    npm --prefix frontend run build        # writes frontend/dist
    uv run uvicorn app.main:app --port 8000
    # http://127.0.0.1:8000/app/   ( / redirects here )

While working on a screen, keep the build running instead of rebuilding by hand:

    npm --prefix frontend run build -- --watch

After a rebuild, **reload the page**. Moving between `#/` and `#/review` is a
same-document navigation and does not refetch anything, so a hash change after a
rebuild shows you the old build — which looks exactly like a change that did not
take effect.

`vite.config.ts` sets `base: "/app/"` and `app/main.py` mounts `dist` at `/app`.
**Those two strings must agree.** If they drift, every asset 404s in the built
app while `vite dev` keeps working perfectly.

The mount is registered last, and at `/app` rather than `/`, so a built asset
can never shadow an API route added later.

## What is real and what is absent

Everything on the screen talks to the live backend: `/stats`, `/source`,
`/source/paste`, `/source/upload`, `/source/{id}/extract`, `/fact`. There is no
fake data anywhere in it.

Things the prototype draws that are **deliberately not here**, because the
backend has no such thing and an inert control is a lie:

- **The nav rail.** Six links to five screens that do not exist.
- **The business name, the user, the avatar.** No business record, no account.
- **UZ / RU / EN pills.** No i18n. The screen is in English.
- **Google Sheets.** No integration.
- **"Use fields instead".** `/fact` takes one free-text line; fields would only
  be reassembled into a line for the same parser to re-read.
- **Drag and drop.** The read zone is a click target, as the prototype records.
- **Per-source fact counts on reload.** `/source` does not report how many facts
  a source produced, so a count is shown only for sources this visit watched
  being read. It is absent, never guessed.
- **Typed facts survive a reload.** They are confirmed on write and `/review`
  lists only unconfirmed facts, so no endpoint can bring them back. The fact is
  in the knowledge base; only this band forgets it.

## 360px, observed

Chrome on Windows will not make a *window* that narrow, but a 360px **iframe**
is a real 360px viewport — `vw` units and media queries resolve against the
frame — so both screens were loaded inside one and measured:

| | Review | Add knowledge |
|---|---|---|
| viewport | 360 | 360 |
| document width | 345 | 345 |
| horizontal overflow | none | none |
| controls below the 44px floor | none of 9 | none of 7 |

**This found a real bug rather than confirming a guess.** `minmax(340px, 1fr)`
is a floor the grid track cannot go below, so the two input cards stayed 340px
wide inside 312px of content and pushed the page to 364px — a sideways scroll on
every phone. The fix is `minmax(min(340px, 100%), 1fr)`, applied to every
auto-fit grid on both screens. Reasoning had said this was fine.

Re-run it after any layout change; it takes about a minute.

## Two departures from the prototype, on purpose

**Typing a fact takes two taps, not one.** The prototype shows the read-back
after writing. `app/main.py` argues the opposite in its own docstring — *"A
mis-parse written blind becomes a confirmed fact, and confirmed is precisely
what nothing downstream questions"* — so the first tap parses and shows what was
understood plus anything confirmed that already answers it differently, and the
second writes it.

**The paste field has a character limit**, because the POST endpoints take query
parameters rather than bodies. See `PASTE_LIMIT` in `api.ts` for the measured
ceiling and how it was measured.

## Three more on the Review screen

**A conflict is not a question.** The prototype asks *"which is current?"* with
two buttons. `/conflicts` surfaces and never resolves — two opening-hours values
may both be true — so a contradiction is shown beside the proposal as
information, and keeping one does not delete the other. A choice would invent a
resolution the system does not have.

**There is no Undo, on Keep or on Remove.** Confirming has no inverse endpoint,
and `DELETE /review/{id}` refuses confirmed facts on purpose. The prototype
offers Undo on both. Rather than a button that 404s, Remove asks once and Keep
says plainly that it is done.

**"Keep all" is N requests, not one.** No bulk endpoint exists. It reports
progress and, on failure, says how many were actually kept — a button that
silently kept 5 of 12 would be worse than the count.

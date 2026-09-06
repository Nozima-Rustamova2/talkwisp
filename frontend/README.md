# frontend

The owner's screens. Vite + React + TypeScript. One screen so far: **Add
knowledge**, ported from `prototype/Add Knowledge.dc.html` and wired to the real
API in `app/main.py`.

## One process

`uvicorn` serves both the API and this. There is no second server and no dev
proxy.

    npm --prefix frontend run build        # writes frontend/dist
    uv run uvicorn app.main:app --port 8000
    # http://127.0.0.1:8000/app/   ( / redirects here )

While working on a screen, keep the build running instead of rebuilding by hand:

    npm --prefix frontend run build -- --watch

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

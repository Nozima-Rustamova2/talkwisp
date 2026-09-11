# Running Talkwisp on another machine

Written for someone (or another Claude Code session) setting this up from a
fresh clone. Everything below was checked against the machine it currently runs
on; where a number is machine-specific it says so.

Read the **Gotchas** section before starting. Three of them cost hours if you
meet them by surprise, and one of them can take the *other* machine's bot
offline without either side noticing.

---

## 0. What you need first

| | |
|---|---|
| Docker Desktop | for Postgres. Must be **running** before the bot starts, or the bot refuses to boot |
| [uv](https://docs.astral.sh/uv/) | Python toolchain. It installs Python 3.13 itself; you do not need a system Python |
| Node 20.19+ or 22.12+ | only to build the frontend. Vite 8 refuses older versions |
| A Gemini API key | https://aistudio.google.com/apikey |
| A Telegram bot token | @BotFather → `/newbot`. **Read gotcha 1 first** — do not reuse the existing one |

---

## 1. Clone

```bash
git clone https://github.com/Nozima-Rustamova2/talkwisp.git
cd talkwisp
```

## 2. Postgres

The schema needs **PostgreSQL 18** (for `uuidv7()`) and **pgvector**. One image
has both:

```bash
docker run -d --name talkwisp-db \
  -e POSTGRES_PASSWORD=<pick a password> \
  -e POSTGRES_DB=talkwisp \
  -p 5433:5432 \
  --restart unless-stopped \
  pgvector/pgvector:pg18
```

Port **5433** on the host, not 5432, so it does not collide with a local
Postgres. `--restart unless-stopped` matters: the container comes back by
itself when Docker restarts, and a bot that cannot reach the database answers
every question with a technical error.

## 3. `.env`

```bash
cp .env.example .env
```

Then fill in six values. `.env.example` explains each one; the short version:

```
DATABASE_URL=postgresql://talkwisp_app:<app password>@localhost:5433/talkwisp
ADMIN_DATABASE_URL=postgresql://postgres:<the password from step 2>@localhost:5433/talkwisp
GEMINI_API_KEYS=<key>[,<key2>]
GEMINI_MODEL=gemini-3.1-flash-lite
LLM_PROVIDER=gemini
TELEGRAM_BOT_TOKEN=<your own bot's token>
TELEGRAM_OWNER_ID=<your numeric Telegram user id>
PUBLIC_BASE_URL=http://localhost:8200
```

Notes that are not cosmetic:

- **`DATABASE_URL` must not be `postgres`.** The app connects as a
  non-superuser role so row-level security applies to it. A superuser bypasses
  every tenancy policy silently, so `app/db.py` refuses to start on one. Pick
  any password for `talkwisp_app`; `migrate.py` creates the role and sets that
  password from this line, so this is the only place it is written down.
- **`ADMIN_DATABASE_URL`** is used by `migrate.py` alone — migrations alter
  tables and manage roles, which the app role deliberately cannot do.
- `TELEGRAM_OWNER_ID` gates `/fact` in Telegram. Get it from @userinfobot.
- **`PUBLIC_BASE_URL` is the only place a hostname is written down.** Sign-in
  links are built from it and the session cookie's `Secure` flag is derived from
  its scheme, so moving to a real domain is this one line.
- **Type secrets into the file yourself. Do not paste them into a chat window**,
  including a Claude Code session. A `.env` and your own terminal are the right
  places for them; a transcript is not.

## 4. Install and migrate

```bash
uv sync
uv run python migrate.py
```

`migrate.py` creates the `talkwisp_app` role, applies all 8 migrations, and
copies `TELEGRAM_BOT_TOKEN` / `TELEGRAM_OWNER_ID` onto the single `business`
row. Expect:

```
  created role talkwisp_app
8 migration file(s), 8 pending.
  applied 0001_initial.sql
  ... through ...
  applied 0008_auth.sql
  linked TELEGRAM_BOT_TOKEN to the single business row
```

## 5. Load the demo data

```bash
uv run python seed.py
```

This wipes the knowledge tables and reloads the Avisena Med demo clinic from
`data/avisena.json`. It is re-runnable and costs embedding calls.

**Your fact count will not match the other machine's.** That one shows 138
confirmed / 2 waiting / 2 sources, but most of those came from extraction runs
and typed entries made over several sessions, not from `seed.py`. A fresh seed
gives you the base clinic and nothing else. If you need byte-identical data —
for comparing harness results, say — copy the database instead:

```bash
# on the machine that has the data
docker exec talkwisp-db pg_dump -U postgres -d talkwisp -Fc -f /tmp/tw.dump
docker cp talkwisp-db:/tmp/tw.dump ./tw.dump
# on the new machine, after step 4
docker cp ./tw.dump talkwisp-db:/tmp/tw.dump
docker exec talkwisp-db pg_restore -U postgres -d talkwisp --clean --if-exists /tmp/tw.dump
```

## 6. Build the frontend

```bash
npm --prefix frontend install
npm --prefix frontend run build
```

`build` runs `oxlint` first and **fails the build on a lint error**, which is
deliberate. `tsc` cannot see a conditionally-called React hook -- it is not a
type error -- and a green `tsc` on `App.tsx` once shipped a crash that fired the
instant a user signed in successfully, while the signed-out path rendered
perfectly. The linter named both lines exactly and was simply never run. It is
part of the build now so it cannot be the step someone skips.

One process serves both: FastAPI mounts the built files at `/app`. There is no
separate frontend server in normal use. **After any rebuild, reload the page** —
the screens use hash routing, and a hash change does not refetch.

## 7. Run

```bash
# the API and the screens
uv run uvicorn app.main:app --host 127.0.0.1 --port 8200

# the Telegram bot, in a second terminal
uv run python -u bot.py
```

Screens at **http://127.0.0.1:8200/app/** — `/` redirects there.

Expected bot output:

```
database reachable.
serving Default business.
@your_bot polling. Ctrl-C to stop.
```

`-u` on the bot is worth keeping: its startup prints are not flushed, so
without it a working bot looks like a hung one for the first 30 seconds.

## 8. Sign in

There is no signup. An account is a `business` row with an email on it, so on a
fresh install you have to put yours there once:

```sql
update business set owner_email = 'you@example.com' where name = 'Default business';
```

Then open the screens, enter that address, and press **Send sign-in link**.

**The email is not sent.** Delivery is the console backend on purpose --
`app/auth.py`'s `deliver()` prints the link to the server log instead, because
choosing a sender needs an account, a payment method and DNS for a domain that
is still being bought. So the link appears in the terminal running uvicorn:

```
  MAGIC LINK for you@example.com
  http://localhost:8200/auth/callback?token=...
```

Paste it into the browser. It works once and expires after 15 minutes. The
sign-in screen says all of this too, so nobody sits waiting on an inbox.

**Anything deploying this to a public URL must replace `deliver()` first**, or
only whoever can read the server log can sign in at all.

## 9. Verify

Cheap, no model calls, run these first:

```bash
uv run python check_encoding.py    # 108 clean, 0 failed
uv run python check_tenancy.py     # 32 passed, 0 failed
uv run python check_auth.py        # 67 passed, 0 failed
uv run python check_approval.py    # all checks passed -- the spending gate
uv run python check_llm.py         # 13 passed, 0 failed
uv run python check_orders.py      # 55 passed, 0 failed
uv run python check_payment.py     # 16 passed, 0 failed
uv run python check_bot.py         # 17 passed, 0 failed
uv run python check_normalize.py
uv run python render_landing.py --check   # the committed landing page matches its inputs
npm --prefix frontend run lint    # no errors; two pre-existing warnings are fine
uv run python check_language.py    # TOTAL 53/53
uv run python check_time.py
```

These spend model quota and take tens of minutes — run them only when you mean
to:

```bash
uv run python check_llm.py --live  # one generation
uv run python check_retrieval.py   # 54 pass / 31 fail / 5 manual of 90 is the BASELINE, not a bug
uv run python check_answer.py      # ~90 generations, overwrites results.json
uv run python check_buy.py         # ~90 generations
```

`check_answer.py` numbers depend on the data, so they will only match the
recorded 80/10 baseline if you restored the database in step 5.

---

## Gotchas

**1. Two machines cannot poll the same bot token.** Telegram allows one
`getUpdates` consumer per token; a second one gets HTTP 409 and, worse, the two
processes steal each other's updates. Symptom: messages vanish, or arrive on
whichever machine won the race.

So **create your own bot in BotFather** for this machine rather than reusing the
existing token. If you must reuse it, stop the bot on the other machine first.

The token is also what identifies the tenant now — the bot resolves which
business it serves from the token that received the update — so after changing
it, re-run `migrate.py` or update the row directly:

```sql
update business set bot_token = '<new token>' where name = 'Default business';
```

**2. A registered webhook makes `getUpdates` fail, silently.** If the bot starts
and never receives anything, check for a webhook:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
```

This has bitten this project once: a ManyChat webhook was registered on the bot,
Telegram answered `getUpdates` with 409, and the poll loop read the 409 body as
"no updates" because it has no `result` key. Nothing errored; the bot just
appeared to receive nothing.

**3. Windows reserves ranges of TCP ports, and the ranges move.** On the current
machine 7926–8025 and 8081–8180 are reserved, which covers the obvious 8000 —
binding there fails with `WinError 10013`, which reads like a permissions
problem rather than a reserved port. Check with:

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

Pick a port outside every listed range. 8200 works today.

**4. Gemini free-tier quota is per key, per model, per day.** A full
`check_answer.py` run is about 90 generations plus embeddings. Two machines
sharing one key share one daily budget, and running dry mid-run looks like a
model failure. `GEMINI_API_KEYS` takes a comma-separated list and rotates on
429.

**5. Memory.** Postgres plus uvicorn plus the bot plus a browser has been enough
to get both processes OOM-killed on a developer machine twice. If processes
disappear without an error, that is what happened — check for swap before
blaming the code.

**6. Docker must be running before the bot.** The bot checks the database and
refuses to start rather than answering every question with a technical error.
The container returns by itself once the Docker engine is up; it cannot start
the engine.

---

## What is deliberately not here

- **No auth.** Every endpoint is open. Fine on `127.0.0.1`; do not expose this
  to a public URL — `DELETE /review/{id}` and the whole write surface would be
  open to anyone, and the extraction endpoints spend model quota.
- **No self-serve signup**, and there must not be one until auth exists. The
  API resolves "which business" by asking for the only one there is, and
  `app_sole_business()` raises rather than choosing once a second exists.
- **Booking**, symptom-to-specialty mapping, and screenshot authenticity
  checking are out of scope by decision, not by omission. See
  `docs/design-decisions.md`.

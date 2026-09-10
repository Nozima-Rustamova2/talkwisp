"""Onboard one business, as far as a command honestly can.

    uv run python onboard.py --name "Rangli Salon" \\
        --email malika@example.uz --token 8123456789:AA... \\
        --document data/their-price-list.pdf

Run on the box, over SSH: creating a `business` row needs ADMIN_DATABASE_URL,
because row-level security means the app role cannot insert one. That is the
right shape for the first ten customers and the wrong one for a hundred; you
will know it has stopped being right when running this over SSH is the
bottleneck rather than the conversation with the customer.

WHAT IT WILL NOT DO, AND WHY
----------------------------

IT WILL NOT CONFIRM THE FACTS IT EXTRACTS. Documents are ingested and left in
the review queue. Confirming is the human judgement the whole product is built
on -- "the agent never invents an answer" only means anything because a person
decided each fact was true. A command that auto-confirmed would be the product
contradicting its own argument, so this stops at "23 facts awaiting review" and
tells you where to look.

IT CANNOT CREATE THE BOT. That is a conversation with @BotFather, and the token
has to be copied by a human.

IT CANNOT START THE BOT. systemd needs root. What it can do is make that one
line instead of four -- see deploy/talkwisp-bot@.service.

IT CANNOT INVENT KNOWLEDGE. If the customer has no documents, this creates an
empty business and says so. Transcribing what they know IS the onboarding
labour; no script removes it.
"""

import argparse
import os
import pathlib
import re
import sys

import httpx
import psycopg
from dotenv import load_dotenv

from app import auth, extract, sources
from app.db import business_by_name, connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

TOKEN_SHAPE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


def die(message: str) -> None:
    raise SystemExit(f"\n  {message}\n")


def verify_token(token: str) -> str:
    """Confirm the token is live, and say WHICH bot it is.

    Shape-checking is not enough. A well-formed token belonging to a different
    bot than intended is invisible -- that is exactly how seven links on the
    landing page ended up pointing at a stranger's bot, after a username was
    "corrected" into somebody else's. So this reports the username and the
    caller reads it.
    """
    if not TOKEN_SHAPE.fullmatch(token.strip()):
        die(f"{token[:12]!r} is not a Telegram token. They look like "
            "<digits>:<35 characters>.")
    me = httpx.get(f"https://api.telegram.org/bot{token.strip()}/getMe",
                   timeout=20).json()
    if not me.get("ok"):
        die(f"Telegram rejected that token: {me.get('description')}")
    return me["result"]["username"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="the business name")
    ap.add_argument("--email", help="owner_email; who may sign in")
    ap.add_argument("--token", help="the agent bot's token, from @BotFather")
    ap.add_argument("--telegram-id", type=int,
                    help="owner_telegram_id; who may use /fact in Telegram")
    ap.add_argument("--document", action="append", default=[],
                    help="a file to ingest; repeatable")
    ap.add_argument("--force", action="store_true",
                    help="re-ingest into a business that already has facts")
    args = ap.parse_args()

    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        die("ADMIN_DATABASE_URL is not set. Creating a business row needs the "
            "owner role -- RLS stops the app role inserting one.")

    # EVERYTHING IS VALIDATED BEFORE ANYTHING IS WRITTEN. A half-onboarded
    # business -- a row with no token, or a token that turns out to be someone
    # else's -- is worse than a failure, because it looks finished.
    username = verify_token(args.token) if args.token else None

    pool.open()
    admin = psycopg.connect(admin_url, autocommit=True)

    if args.token:
        clash = admin.execute(
            "select name from business where bot_token = %s and name <> %s",
            (args.token.strip(), args.name)).fetchone()
        if clash:
            die(f"that token already belongs to {clash[0]!r}. One token "
                "addresses one bot, so two businesses cannot share it -- and a "
                "bare UNIQUE violation would not have told you which.")

    existing = business_by_name(args.name)
    if existing:
        print(f"  business {args.name!r} already exists -- updating")
        business_id = existing
    else:
        business_id = str(admin.execute(
            "insert into business (name) values (%s) returning id",
            (args.name,)).fetchone()[0])
        print(f"  created business {args.name!r}")

    fields, values = [], []
    for column, value in (("owner_email", args.email),
                          ("bot_token", args.token.strip() if args.token else None),
                          ("owner_telegram_id", args.telegram_id)):
        if value is not None:
            fields.append(f"{column} = %s")
            values.append(value)
    if fields:
        admin.execute(f"update business set {', '.join(fields)} where id = %s",
                      (*values, business_id))

    print(f"  owner_email      {args.email or '(not set -- nobody can sign in)'}")
    print(f"  bot_token        {'verified -> @' + username if username else '(not set)'}")
    print(f"  owner_telegram_id {args.telegram_id or '(not set -- /fact refuses everyone)'}")

    # --- documents ----------------------------------------------------------
    with connection(business_id) as conn:
        already = conn.execute("select count(*) from fact").fetchone()[0]
    if args.document and already and not args.force:
        die(f"{args.name!r} already has {already} facts. Re-ingesting would "
            "double its knowledge base, silently. Pass --force if that is what "
            "you want.")

    waiting = 0
    for path in args.document:
        p = pathlib.Path(path)
        if not p.exists():
            die(f"no such file: {path}")
        with connection(business_id) as conn:
            if p.suffix.lower() in (".txt", ".md"):
                src = sources.create_paste(
                    conn, p.read_text(encoding="utf-8"), p.name)
            else:
                src = sources.create_upload(
                    conn, p.name, None, p.read_bytes(), p.name)
        with connection(business_id) as conn:
            src = sources.get(conn, src["id"])
        out = extract.run(business_id, src)
        n = len(out.get("facts") or [])
        waiting += n
        print(f"  {p.name}: {out['status']}, {n} facts, "
              f"{out.get('chunks', 0)} chunks")

    # --- what is left, which is the part you will actually read -------------
    print("\nSTILL TO DO, BY HAND:")
    step = 1
    if args.token:
        print(f"  {step}. sudo systemctl enable --now "
              f'talkwisp-bot@"{args.name}"')
        step += 1
    else:
        print(f"  {step}. Create a bot in @BotFather and re-run with --token. "
              "Nothing polls for this business until then.")
        step += 1

    if waiting:
        print(f"  {step}. Review the {waiting} proposals at "
              f"{auth.PUBLIC_BASE_URL}/app/#/review")
        print("     Nothing is answerable until they are confirmed -- that is "
              "deliberate, and it is why this command does not confirm them.")
        step += 1
    elif not args.document:
        print(f"  {step}. Add knowledge. This business has none, so the agent "
              "will refuse every question.")
        step += 1

    if args.email:
        link = auth.issue_link(args.email)
        if link:
            print(f"  {step}. Send the owner this sign-in link "
                  "(single use, 15 minutes):")
            print(f"     {link}")

    admin.close()
    pool.close()


if __name__ == "__main__":
    main()

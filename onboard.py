"""Onboard one business, as far as a command honestly can.

    uv run python onboard.py --name "Rangli Salon" \\
        --email malika@example.uz --token 8123456789:AA... \\
        --document data/their-price-list.pdf

It is also how a business is approved to spend money, which self-serve signup
makes a separate act from existing:

    uv run python onboard.py --list                     who is waiting
    uv run python onboard.py --name "Rangli Salon" --approve

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

IT DOES NOT APPROVE ANYTHING BY ITSELF. A business created here is unapproved,
exactly like one that signed up on the website, and cannot call a model until
--approve is passed. Running this command is not approval: `default false` is
only a rule if it holds for the route I use myself, and an INSERT here that
quietly set approved=true would be the one exception that makes the column
decorative. The cost is one extra word on the command line.

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

NL = chr(10)


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


def show_all(admin) -> None:
    """Everyone, waiting first. The waiting list is the reason this exists."""
    rows = admin.execute(
        "select name, owner_email, approved, created_at, bot_token is not null "
        "from business order by approved, created_at desc").fetchall()
    if not rows:
        print(NL + "  no businesses" + NL)
        return
    print()
    for name, email, approved, created, has_bot in rows:
        mark = "approved" if approved else "WAITING "
        print(f"  {mark}  {created:%Y-%m-%d}  {name}")
        print(f"            {email or '(no email -- nobody can sign in)'}"
              f"{'' if has_bot else '   no bot yet'}")
    print()


def resolve_one(admin, name: str) -> str:
    """The id of the business with this name, refusing to guess between two.

    business.name IS NOT UNIQUE, and self-serve signup means a stranger picks
    their own. Someone can sign up calling themselves "Avisena Med", and then
    `--approve "Avisena Med"` has two rows to choose from. db.business_by_name()
    would return whichever the planner handed back first, silently -- and what
    is being granted here is permission to spend our money.

    So it refuses and prints both with their addresses, because the email is
    what actually tells them apart.
    """
    rows = admin.execute(
        "select id, owner_email, approved, created_at from business "
        "where name = %s order by created_at", (name,)).fetchall()
    if not rows:
        known = admin.execute("select name from business order by name").fetchall()
        die(f"no business named {name!r}. There is: "
            + ", ".join(repr(r[0]) for r in known))
    if len(rows) > 1:
        lines = (NL + "    ").join(
            f"{r[0]}  {r[1] or '(no email)'}  "
            f"{'approved' if r[2] else 'waiting'}  signed up {r[3]:%Y-%m-%d}"
            for r in rows)
        die(f"{len(rows)} businesses are named {name!r}, and approving the "
            f"wrong one hands a stranger our billing:" + NL + "    " + lines
            + NL + "  Names are not unique. Tell them apart by the email above, "
            f"then rename one or do it in psql by id.")
    return str(rows[0][0])


def set_approved(admin, business_id: str, name: str, want: bool) -> None:
    """Flip the spending flag. The only code in the repo that may.

    The app role has no update grant on `business` and no approve function to
    call, so no bug in the web app can reach this column -- see
    migrations/0010. That is the whole reason this is a command run over SSH
    rather than a button in an admin page, and it is what makes the
    inconvenience worth paying.
    """
    before = admin.execute(
        "select approved, owner_email from business where id = %s",
        (business_id,)).fetchone()
    if bool(before[0]) == want:
        print(f"  {name!r} was already "
              f"{'approved' if want else 'not approved'} -- nothing changed")
        return
    admin.execute("update business set approved = %s where id = %s",
                  (want, business_id))
    print(f"  {name!r} ({before[1] or 'no email'}) is now "
          f"{'APPROVED and can spend money' if want else 'NOT APPROVED'}")
    if not want:
        # Said out loud, because the alternative assumption is the dangerous
        # one. The flag is read when a tenant is bound, once per request, so
        # this stops the next request rather than one already in flight.
        print("        effective on its next request; nothing caches it "
              "beyond a request already running")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="the business name")
    ap.add_argument("--approve", action="store_true",
                    help="let this business spend money (see app/approval.py)")
    ap.add_argument("--unapprove", action="store_true",
                    help="stop it spending; takes effect on its next request")
    ap.add_argument("--list", action="store_true", dest="list_all",
                    help="every business, approved or waiting, and who owns it")
    ap.add_argument("--email", help="owner_email; who may sign in")
    ap.add_argument("--token", help="the agent bot's token, from @BotFather")
    ap.add_argument("--telegram-id", type=int,
                    help="owner_telegram_id; who may use /fact in Telegram")
    ap.add_argument("--document", action="append", default=[],
                    help="a file to ingest; repeatable")
    ap.add_argument("--force", action="store_true",
                    help="re-ingest into a business that already has facts")
    args = ap.parse_args()
    if args.approve and args.unapprove:
        die("--approve and --unapprove together. Pick one.")
    if not args.list_all and not args.name:
        die("--name is required, or --list to see what exists.")

    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        die("ADMIN_DATABASE_URL is not set. Creating a business row needs the "
            "owner role -- RLS stops the app role inserting one.")

    # EVERYTHING IS VALIDATED BEFORE ANYTHING IS WRITTEN. A half-onboarded
    # business -- a row with no token, or a token that turns out to be someone
    # else's -- is worse than a failure, because it looks finished.
    username = verify_token(args.token) if args.token else None

    admin = psycopg.connect(admin_url, autocommit=True)

    # --list and a bare --approve need the owner connection and nothing else,
    # so they are answered before the app pool is opened. Not tidiness: the pool
    # spawns worker threads that this script never joins, and a command that
    # returns in a tenth of a second spent five seconds afterwards printing
    # "couldn't stop thread" at whoever ran it.
    if args.list_all:
        show_all(admin)
        if not args.name:
            admin.close()
            return

    # --approve / --unapprove on their own, with no --token and no --document,
    # is the everyday use: someone signed up, you looked at them, you said yes.
    # Handled before the create-or-update path so it never creates a row -- an
    # approve that silently conjures the business it was asked to approve is a
    # typo away from approving a business that does not exist.
    if (args.approve or args.unapprove) and not (
            args.token or args.email or args.telegram_id or args.document):
        set_approved(admin, resolve_one(admin, args.name), args.name,
                     args.approve)
        admin.close()
        return

    pool.open()

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
        # approved defaults to false (migrations/0010) and this INSERT does not
        # override it. Running this command is not itself approval -- the whole
        # point of `default false` is that a business row appearing by any route
        # is unapproved until somebody says otherwise, and "any route" has to
        # include the route I use myself or it is not a rule.
        print(f"  created business {args.name!r}")

    # Ingestion spends money, so it needs the flag. Checked HERE rather than
    # left to fail at the gate: by the time app/approval.py raises, the bot
    # token is linked and the sources are stored, and the run stops in the
    # middle looking like a crash. One sentence up front beats an exception
    # three modules down that is technically the same information.
    if args.document and not args.approve:
        approved = admin.execute(
            "select approved from business where id = %s",
            (business_id,)).fetchone()[0]
        if not approved:
            die(f"{args.name!r} is not approved, so ingesting documents would "
                "stop at the spending gate partway through. Add --approve to "
                "this command, or approve it first with:" + NL
                + f"    uv run python onboard.py --name {args.name!r} --approve")

    if args.approve or args.unapprove:
        set_approved(admin, business_id, args.name, args.approve)

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

    # FIRST, because without it none of the rest works. This command hands out
    # a sign-in link at the bottom, and an unapproved account signs in fine and
    # then cannot read a document or answer a question -- so handing over the
    # link without this line means personally walking someone into the waiting
    # state. The checklist is the only thing between me and doing that.
    if not admin.execute("select approved from business where id = %s",
                         (business_id,)).fetchone()[0]:
        print(f"  {step}. Approve it, or it can sign in and do nothing:")
        print(f"     uv run python onboard.py --name {args.name!r} --approve")
        step += 1

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

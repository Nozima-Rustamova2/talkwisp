"""Apply every migrations/*.sql file that hasn't run yet, one transaction each.

Runs as the OWNER, not as the app. Since 0007 the app connects as a
non-superuser role that owns nothing and cannot alter a table, which is the
whole point -- so migrations need a second, privileged URL.

  ADMIN_DATABASE_URL  the owner (postgres). Migrations and role management.
  DATABASE_URL        the app role. Everything else, all day.

The app role's name and password are read OUT of DATABASE_URL rather than from
separate variables, so there is exactly one place the app credential is written
down and no way for two copies to drift apart.
"""

import os
import pathlib
import re
import urllib.parse

import psycopg
from dotenv import load_dotenv
from psycopg import sql

load_dotenv()

MIGRATIONS = pathlib.Path(__file__).parent / "migrations"

LEDGER = """
create table if not exists schema_migrations (
    version    text primary key,
    applied_at timestamptz not null default now()
)
"""


def admin_url() -> str:
    url = os.getenv("ADMIN_DATABASE_URL")
    if not url:
        raise SystemExit(
            "ADMIN_DATABASE_URL is not set. Migrations need the owner role;\n"
            "DATABASE_URL is now the app role, which cannot alter a table.\n"
            "See .env.example.")
    return url


def app_credentials() -> tuple[str, str]:
    """The app role's name and password, taken from DATABASE_URL itself."""
    parsed = urllib.parse.urlparse(os.environ["DATABASE_URL"])
    user = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")
    if not user or not password:
        raise SystemExit(
            "DATABASE_URL needs a username and password for the app role.\n"
            "See .env.example.")
    if user == "postgres":
        raise SystemExit(
            "DATABASE_URL still connects as postgres. A superuser bypasses "
            "row-level\nsecurity entirely, so every tenancy policy would be "
            "inert. Point it at\ntalkwisp_app -- app/db.py refuses to start "
            "otherwise.")
    return user, password


def ensure_app_role(conn: psycopg.Connection, user: str, password: str) -> None:
    """Create the app role if absent, and set its password either way.

    Here rather than in the .sql file because creating a login role means
    handling a secret, and a migration file has no way to read one. It owns
    nothing: ownership stays with the admin role, which is what makes FORCE ROW
    LEVEL SECURITY meaningful rather than decorative.
    """
    exists = conn.execute(
        "select 1 from pg_roles where rolname = %s", (user,)).fetchone()
    if not exists:
        conn.execute(sql.SQL("create role {} login").format(sql.Identifier(user)))
        print(f"  created role {user}")
    conn.execute(sql.SQL("alter role {} with login nosuperuser nocreatedb"
                         " nocreaterole nobypassrls password {}").format(
        sql.Identifier(user), sql.Literal(password)))


def link_env_to_business(conn: psycopg.Connection) -> None:
    """One-time bootstrap: move the single-tenant env vars onto the business row.

    TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_ID could only ever describe one
    tenant. They belong on `business` now. This only fires while there is
    exactly one business and it has no token yet, so it cannot touch a real
    multi-tenant install or overwrite a token someone set on purpose.
    """
    table = conn.execute(
        "select to_regclass('public.business')").fetchone()[0]
    if table is None:
        return
    row = conn.execute(
        "select id from business where bot_token is null").fetchone()
    if row is None or conn.execute(
            "select count(*) from business").fetchone()[0] != 1:
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    owner = os.getenv("TELEGRAM_OWNER_ID")
    if not token:
        return

    # A PLACEHOLDER IS WORSE THAN NULL, and this used to write one.
    #
    # bot_token is nullable on purpose: a business that has signed up but not
    # connected a channel yet is a real, expected state, and the dashboard is
    # built to display it. NULL says "no channel". `CHANGEME` says "a channel is
    # connected, and it is broken" -- which reads as connected to anything
    # checking, and is a lie the row tells about itself.
    #
    # That is not hypothetical: the .env on the deployment box said CHANGEME,
    # this function copied it, and the row claimed a channel it did not have.
    #
    # Telegram tokens are <bot id digits>:<35-ish chars>. Checking the shape is
    # enough -- an invalid but well-formed token fails loudly at getMe on the
    # bot's next start, which is a good failure. A placeholder fails silently by
    # looking correct.
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", token.strip()):
        print(f"  NOT linking TELEGRAM_BOT_TOKEN: {token[:12]!r} is not a "
              "Telegram token. Leaving bot_token NULL, which is the honest "
              "value for a business with no channel connected.")
        return

    conn.execute(
        "update business set bot_token = %s,"
        " owner_telegram_id = coalesce(%s, owner_telegram_id) where id = %s",
        (token.strip(), int(owner) if owner and owner.strip() else None,
         row[0]))
    print("  linked TELEGRAM_BOT_TOKEN to the single business row")


def main() -> None:
    user, password = app_credentials()

    with psycopg.connect(admin_url(), autocommit=True) as conn:
        ensure_app_role(conn, user, password)
        conn.execute(LEDGER)
        applied = {
            r[0] for r in conn.execute("select version from schema_migrations")
        }

        files = sorted(MIGRATIONS.glob("*.sql"))
        pending = [f for f in files if f.name not in applied]
        print(f"{len(files)} migration file(s), {len(pending)} pending.")

        for path in pending:
            sql_text = path.read_text(encoding="utf-8")
            with conn.transaction():
                conn.execute(sql_text)
                conn.execute(
                    "insert into schema_migrations (version) values (%s)",
                    (path.name,))
            print(f"  applied {path.name}")

        link_env_to_business(conn)


if __name__ == "__main__":
    main()

"""The pool, and the one place a tenant is bound to a connection.

Nothing in app/ may call `pool.connection()` directly any more. `connection()`
below is the only checkout, because it is the only thing that sets
`app.business_id`, and without that setting every query raises rather than
returning rows -- see migrations/0007_multitenancy.sql.

The shape that makes this cheap: 46 functions across app/ take `conn` as a
parameter and never touch the pool. Only main.py, extract.py and bot.py check a
connection out. So the tenant attaches in a handful of places and the other 46
inherit it without being edited.
"""
import contextlib
import os
from contextvars import ContextVar
from typing import Iterator

from dotenv import load_dotenv
from psycopg import Connection
from psycopg_pool import ConnectionPool

load_dotenv()

try:
    DATABASE_URL = os.environ["DATABASE_URL"]
except KeyError:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
    ) from None

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False)


# The tenant of the connection currently checked out, for the three code paths
# that need it and have no `conn` in scope: the gap log, the feedback log and
# the bot's message log. A ContextVar rather than a parameter threaded through
# four modules, and -- this is the point -- it is assigned in exactly the same
# statement that sets the Postgres GUC. One setter, so the file and the database
# cannot disagree about which business a line belongs to.
_CURRENT: ContextVar[str | None] = ContextVar("business_id", default=None)


def current_business_id() -> str:
    """The tenant of the enclosing connection() block.

    Raises rather than returning None. A log line with a null business_id is a
    line that cannot be attributed later, and "later" is the only time anyone
    reads these files.
    """
    value = _CURRENT.get()
    if value is None:
        raise RuntimeError(
            "No business is bound. This runs inside app.db.connection().")
    return value


def assert_app_role() -> None:
    """Refuse to start on a superuser connection.

    This is the load-bearing line of the whole tenancy change, and it is here
    rather than in the migration because SQL cannot stop someone pointing
    DATABASE_URL back at postgres afterwards.

    A superuser bypasses row-level security completely. Not partially, not with
    a warning -- every policy in 0007 applies and returns every tenant's rows.
    The database would look correctly configured under inspection: policies
    present, RLS enabled and forced, the view security_invoker. Measured on this
    database before the change: current_user postgres, usesuper true.

    So the failure is a boot failure. A warning would be read once and then
    scrolled past for the rest of the deployment's life.
    """
    with pool.connection() as conn:
        user, superuser, bypass = conn.execute(
            "select current_user, rolsuper, rolbypassrls"
            " from pg_roles where rolname = current_user").fetchone()
        if superuser or bypass:
            raise RuntimeError(
                f"DATABASE_URL connects as '{user}', which "
                f"{'is a superuser' if superuser else 'has BYPASSRLS'}. "
                "Row-level security does not apply to it, so every tenancy "
                "policy in migrations/0007_multitenancy.sql is inert and one "
                "business's queries return another's rows. Point DATABASE_URL "
                "at the talkwisp_app role. Refusing to start.")


# The dataset the harness scripts measure against. A NAME, not "whichever
# business exists".
#
# check_answer's 80/10, check_retrieval's 54/31/5 and check_buy's 22/22 are
# statements about a specific set of facts. Handed a different tenant they would
# produce different numbers -- and nothing would distinguish "someone ran this
# against the wrong business" from "retrieval regressed". That is the failure
# this project keeps cataloguing: a check and the thing it measures drifting
# apart while the signal stays green. So the harness names its dataset.
#
# Overridable, because the name is data rather than code and a second install
# may seed under a different one.
HARNESS_BUSINESS = os.getenv("TALKWISP_HARNESS_BUSINESS", "Default business")


def business_by_name(name: str) -> str | None:
    with pool.connection() as conn:
        row = conn.execute(
            "select app_business_by_name(%s)", (name,)).fetchone()
    return str(row[0]) if row and row[0] else None


def harness_business() -> str:
    """The business the check scripts and probes work against.

    Refuses and lists what exists rather than guessing. seed.py is the only
    thing that creates a business; everything else names one that is already
    there, so "not found" is a real error and not a case to paper over.
    """
    found = business_by_name(HARNESS_BUSINESS)
    if found:
        return found
    with pool.connection() as conn:
        names = [r[0] for r in conn.execute("select app_business_names()")]
    raise RuntimeError(
        f"No business named {HARNESS_BUSINESS!r}. Existing: "
        f"{names or '(none -- run seed.py)'}.\n"
        "Set TALKWISP_HARNESS_BUSINESS, or seed one:\n"
        f"  uv run python seed.py --business {HARNESS_BUSINESS!r}")


def business_for_token(token: str) -> str | None:
    """Which business owns the agent bot this token addresses.

    The bot's tenant comes from the token that received the update, not from an
    env var, because one process per token is the actual multi-tenant shape.
    """
    with pool.connection() as conn:
        row = conn.execute(
            "select app_business_for_token(%s)", (token,)).fetchone()
    return str(row[0]) if row and row[0] else None


@contextlib.contextmanager
def connection(business_id: str) -> Iterator[Connection]:
    """A connection with its tenant bound. The only checkout in the codebase.

    `set_config(..., true)` is SET LOCAL, and the third argument is why this is
    a mechanism rather than a habit: SET LOCAL ends with the transaction, so the
    setting cannot follow a pooled connection to the next request's business. A
    bare SET would, silently, and the next borrower would read the previous
    tenant's rows with nothing failing.

    That is an assumption everything above it rests on, so check_tenancy.py
    proves it against a real pool instead of trusting this comment -- including
    a negative control with a bare SET, which must show the value surviving.
    Without that control the test could pass by handing out a fresh connection.

    business_id is REQUIRED. It used to default to "the only business there
    is", which is the same fallback shape that would have served a logged-out
    visitor another tenant's data on the web side -- closed there in 0008 and
    left open here. A missing argument is now a TypeError at the call site,
    which is stronger than any runtime guard: there is nothing to fall back to.
    """
    with pool.connection() as conn:
        conn.execute("select set_config('app.business_id', %s, true)",
                     (str(business_id),))
        token = _CURRENT.set(str(business_id))
        try:
            yield conn
        finally:
            _CURRENT.reset(token)

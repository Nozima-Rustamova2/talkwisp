"""Who is making this request. Sessions, magic links, and one cookie.

THE MODEL IS "LOGGED IN AS THIS BUSINESS", and there is no user table. One
person per business for now, so a session points straight at a business row and
`business.owner_email` is the identity. A user table is what you add the day two
people share a clinic; adding it now would be building for a case that does not
exist.

WHY A SESSION TABLE AND NOT A SIGNED COOKIE
Revocation. Deleting a row ends a session immediately; a signed cookie is valid
until it expires whatever you do, and the only lever is rotating the signing key,
which logs everyone out at once. Magic links also need server-side state anyway
-- single use has to be recorded somewhere -- so having built one table, the
second is nearly free. The cost is one indexed lookup per request, on a
connection the request was opening regardless.

NOTHING HERE STORES A SECRET IT COULD LEAK. The cookie value and the emailed
token exist in exactly two places: the browser's cookie jar, and the email. The
database holds sha256 of each. A dump yields no usable session and no usable
link. The app role cannot even read those two tables -- see 0008; every access
goes through a SECURITY DEFINER function that returns an id or a word.
"""

import hashlib
import hmac
import os
import secrets
import time
import urllib.parse

import httpx
from dotenv import load_dotenv
from fastapi import Request, Response

from app.db import pool

load_dotenv()

# The ONE place a hostname is written down. Link building and the cookie's
# Secure flag both read it, so moving to the real domain is this line and
# nothing else. No hostname appears anywhere in the code.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8200").rstrip("/")

COOKIE = "tw_session"
SESSION_TTL = "30 days"
LINK_TTL = "15 minutes"

# Sending the sign-in link. With no key set, deliver() prints to the server log
# instead -- that is what keeps a local checkout usable with no account, and it
# is the reason this is a plain absence check rather than a config error.
#
# RESEND_FROM must be on a domain whose DKIM records are live in Resend, or
# every send is refused. Note that Cloudflare Email Routing has already claimed
# the single SPF TXT record on talkwisp.uz: Resend's include: has to be MERGED
# into that one record, never added as a second. Two v=spf1 records on one name
# is a permanent fail and breaks receiving as well as sending.
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM = os.getenv("RESEND_FROM", "info@talkwisp.uz")

# Telegram Login, step 4. Off until a domain is linked in BotFather.
TELEGRAM_LOGIN_ENABLED = os.getenv("TELEGRAM_LOGIN_ENABLED", "").lower() in (
    "1", "true", "yes")
# The PLATFORM bot -- the one that signs web logins. NOT TELEGRAM_BOT_TOKEN,
# which is one business's agent bot and has nothing to do with login. Keeping
# these apart is the whole reason there are two bots.
PLATFORM_BOT_TOKEN = os.getenv("TELEGRAM_PLATFORM_BOT_TOKEN")
# A signed Telegram payload older than this is refused: the signature stays
# valid forever, so freshness is the only thing stopping a replay of a captured
# login URL.
TELEGRAM_MAX_AGE = 300


def cookie_secure() -> bool:
    """Derived, not configured separately, so it cannot disagree with the URL.

    Browsers treat http://localhost as a trustworthy origin and accept Secure
    cookies there, so the tunnel works with this off OR on -- but deriving it
    means the day PUBLIC_BASE_URL becomes https is the day the flag flips, with
    nobody having to remember.
    """
    return PUBLIC_BASE_URL.startswith("https://")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# --- sessions ---------------------------------------------------------------


def create_session(business_id: str) -> str:
    """Returns the RAW value for the cookie. Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    with pool.connection() as conn:
        conn.execute("select app_session_create(%s, %s, %s::interval)",
                     (_hash(raw), business_id, SESSION_TTL))
    return raw


def business_for_session(raw: str | None) -> str | None:
    if not raw:
        return None
    with pool.connection() as conn:
        row = conn.execute(
            "select app_session_business(%s)", (_hash(raw),)).fetchone()
    return str(row[0]) if row and row[0] else None


def end_session(raw: str | None) -> None:
    if not raw:
        return
    with pool.connection() as conn:
        conn.execute("select app_session_delete(%s)", (_hash(raw),))


def resolve(request: Request) -> str | None:
    """Cookie -> session -> business, cached for the life of the request.

    Cached because two independent mechanisms both ask: the app-level gate in
    main.py, and current_business(). Each has to work with the other removed --
    that is what makes them independent rather than one guard called twice -- so
    the cache is what keeps the cost at one lookup.
    """
    cached = getattr(request.state, "business_id", "unset")
    if cached != "unset":
        return cached
    found = business_for_session(request.cookies.get(COOKIE))
    request.state.business_id = found
    return found


def set_cookie(response: Response, raw: str) -> None:
    """Every attribute here is a decision, not a default.

    httponly   JavaScript cannot read it, so an XSS bug cannot exfiltrate it.
    secure     Derived from PUBLIC_BASE_URL; see cookie_secure().
    samesite   LAX, deliberately, and this is the one worth explaining. A magic
               link is clicked in a mail client, which is a cross-site top-level
               navigation. Under Strict the cookie is withheld on exactly that
               navigation and the user lands logged out, having just logged in.
               Not None either -- that is for cross-site embedding, which this
               app does not do, and it demands Secure.
    no domain  Host-only. A Domain attribute would send the cookie to every
               subdomain, including ones we do not control yet.
    """
    response.set_cookie(
        COOKIE, raw,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
        max_age=30 * 24 * 3600,
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True,
                           secure=cookie_secure(), samesite="lax")


# --- magic links ------------------------------------------------------------


def issue_link(email: str) -> str | None:
    """A single-use link for this address, or None if nobody owns it.

    Returns None rather than raising, and the endpoint says the same thing
    either way: an error that distinguishes "no such account" from "sent" turns
    this into an address checker for anyone who asks.
    """
    with pool.connection() as conn:
        row = conn.execute(
            "select app_business_for_email(%s)", (email,)).fetchone()
        if not row or not row[0]:
            return None
        business_id = str(row[0])
        raw = secrets.token_urlsafe(32)
        conn.execute("select app_login_token_create(%s, %s, %s::interval)",
                     (_hash(raw), business_id, LINK_TTL))
    return (f"{PUBLIC_BASE_URL}/auth/callback?"
            + urllib.parse.urlencode({"token": raw}))


def claim_link(raw: str) -> tuple[str | None, str]:
    """(business_id, outcome). outcome is ok / unknown / expired / used.

    The distinction is carried all the way to the screen on purpose. "This link
    has already been used" and "this link expired" send a person to different
    next actions, and one generic failure makes a working system look broken.
    """
    with pool.connection() as conn:
        row = conn.execute(
            "select business_id, outcome from app_login_token_claim(%s)",
            (_hash(raw),)).fetchone()
    if not row:
        return None, "unknown"
    return (str(row[0]) if row[0] else None), row[1]


def _console(email: str, link: str) -> None:
    print(f"\n  MAGIC LINK for {email}\n  {link}\n", flush=True)


def deliver(email: str, link: str) -> None:
    """Send the sign-in link, or print it if there is no sender configured.

    TWO BACKENDS, CHOSEN BY WHETHER RESEND_API_KEY IS SET. With no key this
    prints to the server log, which is what makes a local checkout usable
    without anyone creating an account -- see SETUP.md. With a key it sends for
    real.

    THE FALLBACK ON FAILURE IS THE PART WORTH READING. If Resend refuses, this
    prints the link and returns normally rather than raising. Three reasons, in
    order:

      * The endpoint answers identically for a known and an unknown address, on
        purpose, so that /auth/request is not an account checker. Raising here
        would turn a send failure into a 500 for real addresses and a 200 for
        made-up ones -- reintroducing exactly the enumeration channel the
        endpoint was written to close.
      * A broken sender would otherwise lock every owner out of their own
        account, including the person who has to go and fix the sender.
      * The failure is not swallowed: it is printed with the provider's own
        error text, so `journalctl -u talkwisp-api` says what went wrong rather
        than leaving a silence to interpret.

    An unverified sending domain is the failure this will actually hit -- Resend
    refuses any `from` on a domain whose DKIM records are not live yet, and that
    is a DNS problem, not a code one.
    """
    if not RESEND_API_KEY:
        _console(email, link)
        return

    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": RESEND_FROM,
                "to": [email],
                "subject": "Your Talkwisp sign-in link",
                # Plain text, not HTML. A one-link email in HTML is more likely
                # to be treated as marketing, and there is nothing to lay out.
                "text": (
                    "Here is your sign-in link. It works once and expires in "
                    f"15 minutes.\n\n{link}\n\n"
                    "If you did not ask for this, ignore it -- nothing happens "
                    "until the link is opened."
                ),
            },
            timeout=20,
        )
    except httpx.HTTPError as exc:
        print(f"  RESEND FAILED for {email}: {exc!r}", flush=True)
        _console(email, link)
        return

    if response.status_code >= 400:
        print(f"  RESEND REFUSED for {email}: HTTP {response.status_code} "
              f"{response.text[:300]}", flush=True)
        _console(email, link)
        return

    print(f"  sign-in link sent to {email} "
          f"(resend id {response.json().get('id')})", flush=True)


# --- Telegram Login ---------------------------------------------------------


def verify_telegram(fields: dict[str, str]) -> tuple[bool, str]:
    """Check Telegram's signature over a login payload.

    The scheme: every field except `hash`, as "key=value", sorted by key and
    joined with newlines, HMAC-SHA256'd with a key that is sha256 of the BOT
    TOKEN -- the platform bot's, not a customer's agent bot.

    Freshness is checked as well as the signature. A signature does not expire,
    so a login URL captured from a browser history or a referrer header would
    work forever without this.

    Untestable until the domain exists: Telegram will not send a login payload
    to a host that is not registered with /setdomain in BotFather. Written now,
    gated off, so the domain landing is a config change rather than a build.
    """
    if not PLATFORM_BOT_TOKEN:
        return False, "TELEGRAM_PLATFORM_BOT_TOKEN is not set"
    given = fields.get("hash")
    if not given:
        return False, "no hash in the payload"

    check = "\n".join(f"{k}={fields[k]}"
                      for k in sorted(fields) if k != "hash")
    secret = hashlib.sha256(PLATFORM_BOT_TOKEN.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    # compare_digest, not ==, so the comparison does not leak the hash through
    # how long it takes to fail.
    if not hmac.compare_digest(expected, given):
        return False, "signature does not match"

    try:
        age = time.time() - int(fields.get("auth_date", "0"))
    except ValueError:
        return False, "auth_date is not a number"
    if age > TELEGRAM_MAX_AGE:
        return False, f"signed {int(age)}s ago, older than {TELEGRAM_MAX_AGE}s"
    return True, "ok"


def business_for_telegram(tg_id: int) -> str | None:
    with pool.connection() as conn:
        row = conn.execute(
            "select app_business_for_telegram(%s)", (tg_id,)).fetchone()
    return str(row[0]) if row and row[0] else None

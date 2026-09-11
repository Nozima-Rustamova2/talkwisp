"""Connecting a business's own Telegram bot, from a browser.

TWO THINGS THIS CANNOT DO, said plainly rather than designed around.

IT CANNOT CREATE THE BOT. That is a conversation with @BotFather and a human
has to have it. Nothing here pretends otherwise.

IT CANNOT START THE POLLING PROCESS. bot.py binds TOKEN, API, BUSINESS_ID and
OWNER_ID as module globals, set once at startup -- one tenant per process,
permanently -- so a stored token changes nothing until a process starts for it,
and that is `systemctl enable --now talkwisp-bot@Name`, which needs root. The
web app deliberately cannot become root (see docs/design-decisions.md), so this
endpoint stores the token and says so. A supervisor that watches for businesses
with tokens and spawns pollers is what removes the last manual step; it is not
built yet, and the copy here must not imply it is.

THE OWNER ID CANNOT COME FROM THE TOKEN. Telegram will not tell you a user id
from a bot token, so the owner has to message their own bot. Which creates the
problem this file's other half exists to solve.
"""

import hashlib
import hmac
import re
import time

import httpx

# <digits>:<35ish characters>. Shape-checked before the network call so an
# obvious paste error costs nothing and says something useful.
TOKEN_SHAPE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")

# How long a claim link is good for. Short, because the owner generates it and
# opens it in the same minute -- it is a link from a screen they are looking at,
# not something mailed to them.
CLAIM_TTL_SECONDS = 900


def verify_token(token: str) -> tuple[bool, str]:
    """(ok, username or the reason it failed).

    Shape-checking is not enough, and this project has the scar: a well-formed
    token belonging to a DIFFERENT bot than intended is invisible, which is
    exactly how seven landing-page links ended up pointing at a stranger's bot
    after a username was "corrected" into somebody else's. So this asks Telegram
    who the token belongs to and hands the username back to be shown.
    """
    token = token.strip()
    if not TOKEN_SHAPE.fullmatch(token):
        return False, ("That is not a Telegram bot token. They look like "
                       "123456789:AAE... — copy the whole line BotFather sent.")
    try:
        response = httpx.get(f"https://api.telegram.org/bot{token}/getMe",
                             timeout=20)
    except httpx.HTTPError:
        return False, ("Couldn't reach Telegram to check that token. "
                       "Try again in a moment.")
    body = response.json()
    if not body.get("ok"):
        # Telegram's own words. "Unauthorized" means revoked or mistyped, and
        # paraphrasing it would lose the distinction.
        return False, f"Telegram rejected that token: {body.get('description')}"
    return True, body["result"]["username"]


# --- claiming ownership -----------------------------------------------------
#
# THE LAND-GRAB THIS PREVENTS. `owner_telegram_id` gates /fact and the owner
# buttons. If /start simply claimed ownership for whoever pressed it first, then
# any customer who found the bot before its owner did would become the owner --
# and a bot is findable the moment it exists. "The window is small" is not a
# security argument.
#
# So the claim needs a secret the owner has and a stranger does not. Rather than
# a new table or a new env var, it is signed with THE BUSINESS'S OWN BOT TOKEN:
# only the server and Telegram know it, the bot process already holds it, and it
# is necessarily present because the claim link is only ever generated after the
# token is stored.
#
# Single use comes free from the database, not from here: 0011's claim function
# only writes `where owner_telegram_id is null`, so the second press is refused
# no matter how good its code is. This signature only has to stop the FIRST
# press being a stranger's.


def _sign(business_id: str, bot_token: str, expiry: int) -> str:
    return hmac.new(bot_token.encode(),
                    f"{business_id}:{expiry}".encode(),
                    hashlib.sha256).hexdigest()[:24]


def claim_code(business_id: str, bot_token: str) -> str:
    """The /start payload that proves the presser is the owner.

    Telegram allows A-Z a-z 0-9 _ - in a start payload, up to 64 characters.
    This is an expiry and 24 hex characters, well inside both.
    """
    expiry = int(time.time()) + CLAIM_TTL_SECONDS
    return f"{expiry}-{_sign(business_id, bot_token, expiry)}"


def check_claim(payload: str, business_id: str, bot_token: str) -> bool:
    """Whether this /start payload is a live claim code for this business."""
    if not payload or "-" not in payload:
        return False
    raw_expiry, _, signature = payload.partition("-")
    if not raw_expiry.isdigit():
        return False
    expiry = int(raw_expiry)
    if expiry < time.time():
        return False
    # compare_digest, not ==: a plain comparison leaks how much of the signature
    # was right through how long it took to fail.
    return hmac.compare_digest(_sign(business_id, bot_token, expiry), signature)

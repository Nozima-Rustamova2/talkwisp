"""Auth: what is reachable without a session, and what a session gets you.

    uv run python check_auth.py

Runs against the REAL FastAPI app through TestClient, so the gate, the
dependency, the cookie and the SECURITY DEFINER functions are all the ones that
ship. Sessions are created by hand -- there is no login flow in the loop -- which
is the point: the protection has to be verifiable without one.

THE CHECK THAT MATTERS MOST is section 1. It does not assert "these routes are
protected"; it enumerates every route the app has, calls each one with no
cookie, and asserts that the set which does NOT refuse equals a literal list.
Adding a route makes that list wrong on the day it is added, not on the day
someone rereads it. A check that names the routes it protects can only ever be
as current as its author.
"""

import hashlib
import hmac
import os
import sys
import time

import psycopg
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from starlette.routing import Mount

import app.main as api
from app import auth, db
from app.db import harness_business, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def raises(label, fn, needle):
    global passed, failed
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        if needle.lower() in str(exc).lower():
            passed += 1
            print(f"  [ok  ] {label}")
            return
        failed += 1
        print(f"  [FAIL] {label}")
        print(f"         refused, but never mentions {needle!r}: {exc}")
        return
    failed += 1
    print(f"  [FAIL] {label}")
    print("         it was allowed")


admin = psycopg.connect(os.environ["ADMIN_DATABASE_URL"], autocommit=True)
pool.open()

# Not used as a context manager: entering it runs the lifespan, and the lifespan
# closes the pool on exit while later sections still need it.
client = TestClient(api.app)

BUSINESS = harness_business()
EMAIL = "check-auth@example.test"
admin.execute("update business set owner_email = %s where id = %s",
              (EMAIL, BUSINESS))

# ---------------------------------------------------------------------------
print("\n1. Default deny, measured across every route the app has")

EXPECTED = sorted(api.PUBLIC_PATHS)


def reachable_without_a_session() -> list[str]:
    found = []
    for route in api.app.routes:
        if isinstance(route, Mount):
            continue
        method = next(iter(sorted(route.methods - {"HEAD", "OPTIONS"})), "GET")
        response = client.request(method, route.path.replace("{fact_id}", "x")
                                  .replace("{source_id}", "x"),
                                  follow_redirects=False)
        # 401 is the gate. Anything else counts as reachable -- including 422,
        # which would mean validation ran FIRST and the refusal was incidental.
        # Still denied, but for the wrong reason and not a guarantee, so it has
        # to surface here rather than pass quietly.
        if response.status_code != 401:
            found.append(route.path)
    return sorted(found)


check("the set reachable without a session is exactly PUBLIC_PATHS",
      reachable_without_a_session(), EXPECTED)

# THE CONTROL. The assertion above reads as obviously correct, which is exactly
# the property that stops anyone asking whether it can fail -- and its failure
# mode is silence: if the enumeration missed routes, or every request errored
# identically, it would still report a match.
#
# So: open a hole on purpose. Register a route and exempt it, the way someone
# would while debugging, and confirm the check notices. If this reports "not
# noticed", section 1 is decorative.


@api.app.get("/_control_leak")
def _control_leak():
    return {"leaked": True}


api.PUBLIC_PATHS.add("/_control_leak")
noticed = reachable_without_a_session() != EXPECTED
api.PUBLIC_PATHS.discard("/_control_leak")
api.app.routes[:] = [r for r in api.app.routes
                     if getattr(r, "path", None) != "/_control_leak"]

check("control: an exempted route IS noticed, so the check above can fail",
      noticed, True)
check("and removing it puts the set back",
      reachable_without_a_session(), EXPECTED)

# Reads leak as much as writes. Named individually because the original plan
# said "protect the write endpoints", and these four are all GETs.
for path in ("/review", "/stats", "/conflicts", "/source"):
    check(f"GET {path} refuses without a session",
          client.get(path).status_code, 401)

check("/docs does not exist, so it cannot be an unauthenticated API listing",
      client.get("/docs").status_code, 404)
check("/openapi.json likewise", client.get("/openapi.json").status_code, 404)

# ---------------------------------------------------------------------------
print("\n2. A session, made by hand, opens exactly one business")

raw = auth.create_session(BUSINESS)
client.cookies.set(auth.COOKIE, raw)

check("/stats now answers", client.get("/stats").status_code, 200)
check("/review now answers", client.get("/review").status_code, 200)
me = client.get("/auth/me").json()
check("/auth/me names the business", me["email"], EMAIL)
check("and it is the session's business, not a default",
      client.get("/stats").json()["facts"],
      admin.execute("select count(*) from fact where business_id = %s"
                    " and confirmed", (BUSINESS,)).fetchone()[0])

# ---------------------------------------------------------------------------
print("\n3. The stored session is not the cookie")

check("the raw cookie value appears nowhere in the session table",
      admin.execute("select count(*) from session where id_hash = %s",
                    (raw,)).fetchone()[0], 0)
check("only its sha256 does",
      admin.execute("select count(*) from session where id_hash = %s",
                    (hashlib.sha256(raw.encode()).hexdigest(),)
                    ).fetchone()[0], 1)

# The app role has no grant on these tables at all: every access goes through a
# SECURITY DEFINER function. So a query bug cannot read them -- refused, not
# empty, which is the difference between a wrong answer and no answer.
def read_sessions():
    with pool.connection() as conn:
        conn.execute("select * from session")


raises("the app role cannot read the session table directly",
       read_sessions, "permission denied")

# ---------------------------------------------------------------------------
print("\n4. There is no implicit tenant anywhere")
# This section used to prove that sole_business() -- the "whichever business
# exists" fallback -- refused when reached from an HTTP request. That guard is
# gone, and so is the thing it guarded: connection() has no default at all now.
# A missing tenant is a TypeError at the call site rather than a silent
# substitution, which is stronger than any runtime check because there is
# nothing left to fall back TO. The invariant moved, so the assertion moved.


def connection_without_a_tenant():
    with db.connection():          # noqa - no argument, deliberately
        pass


raises("connection() with no business is a TypeError, not a default",
       connection_without_a_tenant, "argument")

check("harness_business() names its dataset and resolves it",
      harness_business(), BUSINESS)

# It must refuse rather than guess when the name is absent: seed.py is the only
# thing that creates a business, so "not found" has to stay a real error.
_saved = db.HARNESS_BUSINESS
db.HARNESS_BUSINESS = "no such business, deliberately"
try:
    raises("an unknown harness business refuses and lists what exists",
           harness_business, "no business named")
finally:
    db.HARNESS_BUSINESS = _saved

# ---------------------------------------------------------------------------
print("\n5. Magic links: single use, and each failure says which")

client.cookies.clear()
link = auth.issue_link(EMAIL)
check("a link is issued for a known address", bool(link), True)
check("and it is built from PUBLIC_BASE_URL, with no hostname in the code",
      link.startswith(auth.PUBLIC_BASE_URL + "/auth/callback?"), True)
check("an unknown address gets no link", auth.issue_link("nobody@example.test"),
      None)
check("and the endpoint says the same thing either way",
      client.post("/auth/request", data={"email": "nobody@example.test"}
                  ).json()["sent"],
      client.post("/auth/request", data={"email": EMAIL}).json()["sent"])

link_token = link.split("token=")[1]
first = client.get(f"/auth/callback?token={link_token}", follow_redirects=False)
check("clicking it redirects to the app", first.status_code, 303)
check("and sets a session cookie", auth.COOKIE in first.cookies, True)

again = client.get(f"/auth/callback?token={link_token}", follow_redirects=False)
check("clicking it twice is refused", again.status_code, 400)
check("and says it was already used, not something generic",
      "already been used" in again.text, True)

expired_raw = "expired-" + auth.secrets.token_urlsafe(8)
with pool.connection() as conn:
    conn.execute("select app_login_token_create(%s, %s, %s::interval)",
                 (hashlib.sha256(expired_raw.encode()).hexdigest(), BUSINESS,
                  "-1 minutes"))
stale = client.get(f"/auth/callback?token={expired_raw}", follow_redirects=False)
check("an expired link says expired", "has expired" in stale.text, True)

unknown = client.get("/auth/callback?token=never-existed", follow_redirects=False)
check("an unrecognised link says so", "not valid" in unknown.text, True)

# ---------------------------------------------------------------------------
print("\n5b. The Resend path, which the console path never touches")
# deliver() has two backends and the tests only ever ran the console one, so a
# NameError sat in the Resend branch and shipped: `httpx` was used and never
# imported. It surfaced on the box, on the first real send, because no test had
# ever entered that branch. Both branches get exercised now.

sent_payloads = []


class _FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {"id": "test-message-id"}
        self.text = str(self._body)

    def json(self):
        return self._body


def _with_resend(key, poster):
    """Run deliver() as though a Resend key were configured."""
    saved_key, saved_post = auth.RESEND_API_KEY, auth.httpx.post
    auth.RESEND_API_KEY, auth.httpx.post = key, poster
    try:
        auth.deliver("owner@example.test", "https://talkwisp.uz/auth/callback?token=t")
    finally:
        auth.RESEND_API_KEY, auth.httpx.post = saved_key, saved_post


def _ok_post(url, headers=None, json=None, timeout=None):
    sent_payloads.append({"url": url, "json": json, "headers": headers})
    return _FakeResponse()


_with_resend("re_test", _ok_post)
check("a configured key sends instead of printing", len(sent_payloads), 1)
check("to the Resend endpoint", sent_payloads[0]["url"],
      "https://api.resend.com/emails")
check("with the key as a bearer token",
      sent_payloads[0]["headers"]["Authorization"], "Bearer re_test")
check("from RESEND_FROM, to the address that asked",
      (sent_payloads[0]["json"]["from"], sent_payloads[0]["json"]["to"]),
      (auth.RESEND_FROM, ["owner@example.test"]))
check("and the link is in the body",
      "auth/callback?token=t" in sent_payloads[0]["json"]["text"], True)


# A refusal must not raise. /auth/request answers identically for known and
# unknown addresses so it cannot be used as an account checker; an exception
# here would make a send failure a 500 for real addresses and a 200 for
# invented ones, reintroducing exactly that channel.
def _refusing_post(url, headers=None, json=None, timeout=None):
    return _FakeResponse(status=422, body={"message": "domain not verified"})


def _throwing_post(url, headers=None, json=None, timeout=None):
    raise auth.httpx.ConnectError("no route to host")


for label, poster in (("a 4xx refusal", _refusing_post),
                      ("a transport error", _throwing_post)):
    try:
        _with_resend("re_test", poster)
        raised = False
    except Exception:  # noqa: BLE001
        raised = True
    check(f"{label} does not raise -- it falls back to the log", raised, False)

# ---------------------------------------------------------------------------
print("\n6. The cookie's attributes are the decided ones")

header = first.headers.get("set-cookie", "")
check("httponly, so an XSS bug cannot read it", "httponly" in header.lower(), True)
check("samesite=lax, so a magic link clicked in a mail client still lands "
      "signed in", "samesite=lax" in header.lower(), True)
check("secure follows PUBLIC_BASE_URL's scheme",
      "secure" in header.lower(), auth.cookie_secure())
check("host-only: no Domain attribute", "domain=" in header.lower(), False)

# ---------------------------------------------------------------------------
print("\n7. Signing out deletes the row, not just the cookie")

live = auth.create_session(BUSINESS)
client.cookies.set(auth.COOKIE, live)
check("the session works", client.get("/stats").status_code, 200)
client.post("/auth/logout")
check("the row is gone from the database",
      auth.business_for_session(live), None)
client.cookies.set(auth.COOKIE, live)
check("and replaying the old cookie value is refused",
      client.get("/stats").status_code, 401)
client.cookies.clear()

# ---------------------------------------------------------------------------
print("\n8. Telegram Login is off, and its signature check is right anyway")

check("the endpoint is closed while the flag is off",
      client.get("/auth/telegram?id=1").status_code, 404)

FAKE = "111:AAtest-platform-token"
saved = (auth.PLATFORM_BOT_TOKEN, auth.TELEGRAM_LOGIN_ENABLED)
auth.PLATFORM_BOT_TOKEN = FAKE


def signed(**fields) -> dict:
    payload = {k: str(v) for k, v in fields.items()}
    dcs = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    payload["hash"] = hmac.new(hashlib.sha256(FAKE.encode()).digest(),
                               dcs.encode(), hashlib.sha256).hexdigest()
    return payload


good = signed(id=42, first_name="Owner", auth_date=int(time.time()))
check("a correctly signed, fresh payload verifies",
      auth.verify_telegram(good)[0], True)

tampered = dict(good, id="43")
check("changing a field after signing is caught",
      auth.verify_telegram(tampered)[0], False)

stale_tg = signed(id=42, auth_date=int(time.time()) - 3600)
ok_stale, why_stale = auth.verify_telegram(stale_tg)
check("an old payload is refused even though the signature is valid",
      ok_stale, False)
check("and the reason names its age", "older than" in why_stale, True)
check("a payload with no hash is refused",
      auth.verify_telegram({"id": "42"})[0], False)

auth.PLATFORM_BOT_TOKEN, auth.TELEGRAM_LOGIN_ENABLED = saved

# ---------------------------------------------------------------------------
print("\n   tearing down")
admin.execute("delete from session where business_id = %s", (BUSINESS,))
admin.execute("delete from login_token where business_id = %s", (BUSINESS,))
admin.execute("update business set owner_email = null where id = %s",
              (BUSINESS,))
admin.close()
pool.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.answer import answer as answer_question
from app import auth, console, extract, review, sources, vision
from app.db import assert_app_role, connection, pool
from app.llm import check_configured, check_reachable
from app.retrieval import find
from app.typed import parse as parse_fact, store as store_fact


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail here, not on the first customer message. check_configured() is
    # config only; check_reachable() spends one tiny generation proving the
    # pinned model actually answers for this credential, which is the half a
    # .env cannot tell you.
    check_configured()
    check_reachable()
    pool.open()
    # And fail here rather than on the first cross-tenant read, which would not
    # fail at all. See app/db.py: a superuser bypasses every policy in 0007.
    assert_app_role()
    yield
    pool.close()


# Everything except these needs a session. DEFAULT DENY, and it is structural:
# app-level dependencies apply to every route registered on the app, including
# ones added after this line and after the static mount. Measured, not assumed;
# check_auth.py enumerates app.routes and asserts the reachable-without-a-session
# set equals this literal, so a new public path fails the check the day it is
# written rather than the day someone reads the list.
#
# The three things that escape a route dependency, all named because pretending
# otherwise is worse than the gap:
#
#   /openapi.json, /docs, /redoc -- FastAPI registers these itself, so app-level
#       dependencies never see them (measured: 200 while every route was 401).
#       On a public URL they publish the entire write surface to anyone. Closed
#       by openapi_url=None below, which removes all three.
#   the /app mount -- a Mount is not a route and cannot carry dependencies. It
#       has to stay reachable anyway or the login screen cannot load. It serves
#       built frontend assets only; no business data passes through it.
PUBLIC_PATHS = {
    "/",                 # redirect to the screens
    "/health",           # liveness, for a load balancer that says nothing else
    "/auth/me",          # answers "are you logged in", so it must work logged out
    "/auth/request",     # ask for a magic link
    "/auth/callback",    # click a magic link
    "/auth/logout",      # idempotent, and pointless to require a session for
    "/auth/telegram",    # Telegram Login, gated off until a domain exists
}


def gate(request: Request) -> None:
    """The outer half of default-deny: no session, no route.

    Paired with current_business() below, which is the inner half. They overlap
    on purpose and each works with the other deleted -- that is what makes them
    two mechanisms rather than one guard called twice. This one covers endpoints
    that touch no tenant data; that one makes tenant data unreachable.
    """
    if request.url.path in PUBLIC_PATHS:
        return
    if auth.resolve(request) is None:
        raise HTTPException(status_code=401, detail="Sign in first.")


# openapi_url=None: see PUBLIC_PATHS above. There is no auth on the generated
# docs because FastAPI adds those routes itself, so the only safe thing is for
# them not to exist.
app = FastAPI(title="Talkwisp", lifespan=lifespan, openapi_url=None,
              dependencies=[Depends(gate)])


def current_business(request: Request) -> str:
    """Which business this request is for. THE INNER HALF OF DEFAULT DENY.

    Every endpoint that touches tenant data takes this and hands it to
    connection(). So an endpoint cannot reach tenant data without a session --
    not because a decorator was remembered, but because there is no business_id
    to be had. Same shape as the payment view in 0005: the safe thing is the only
    thing, rather than the thing you have to remember.

    It notably does NOT fall back to "the only business there is". That default
    is gone from connection() entirely -- a missing tenant is now a TypeError at
    the call site rather than a silent substitution, which is stronger than the
    ContextVar guard that used to police it. There is nothing left to fall back
    to.
    """
    business_id = auth.resolve(request)
    if business_id is None:
        raise HTTPException(status_code=401, detail="Sign in first.")
    return business_id


Business = Annotated[str, Depends(current_business)]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> dict[str, str | None]:
    # No tenant: this asks about the server, not about anyone's data.
    with pool.connection() as conn:
        database, pgvector = conn.execute(
            "select current_database(),"
            " (select extversion from pg_extension where extname = 'vector')"
        ).fetchone()
    return {"status": "ok", "database": database, "pgvector": pgvector}


@app.get("/stats")
def stats(business: Business) -> dict:
    """What the status plate on the Add-knowledge screen states.

    Confirmed and unconfirmed are counted SEPARATELY and never summed: "the
    agent knows N facts" must mean facts it will actually answer with, and an
    unconfirmed extraction is a proposal, not knowledge. Adding them together
    would make the number go up the moment a file is read, which is the one
    moment nothing has been learned yet.

    Note what these counts do NOT say any more, and did not need editing to stop
    saying: they are this business's, because the rows the query can see are.
    """
    with connection(business) as conn:
        confirmed, waiting = conn.execute(
            "select count(*) filter (where confirmed),"
            "       count(*) filter (where not confirmed) from fact"
        ).fetchone()
        source_count = conn.execute("select count(*) from source").fetchone()[0]
        last = conn.execute(
            "select greatest("
            "  (select max(created_at) from fact),"
            "  (select max(created_at) from source))"
        ).fetchone()[0]
    return {"facts": confirmed, "facts_awaiting_review": waiting,
            "sources": source_count,
            "last_added": last.isoformat() if last else None}


@app.get("/ask")
def ask(business: Business, q: str) -> dict:
    """Facts only: no LLM, no vectors. What the deterministic path can answer."""
    with connection(business) as conn:
        return find(conn, q)


@app.get("/answer")
def answer_endpoint(business: Business, q: str) -> dict:
    """The full path: facts, then prose, then an honest refusal."""
    with connection(business) as conn:
        return answer_question(conn, q)


@app.post("/fact")
def add_fact(business: Business, line: str, confirm: bool = False) -> dict:
    """Parse one free-text line into a fact.

    Without `confirm=true` this only shows what it would write, plus any
    confirmed fact that already answers the same subject and attribute
    differently. A mis-parse written blind becomes a confirmed fact, and
    confirmed is precisely what nothing downstream questions.
    """
    with connection(business) as conn:
        result = parse_fact(conn, line)
        if result["error"] or not confirm:
            result["written"] = False
            return result
        result["id"] = str(store_fact(conn, result["parsed"]))
        result["written"] = True
        return result


@app.post("/source/paste")
def add_paste(business: Business, content: str,
              label: str | None = None) -> dict:
    """Step 9. Store a pasted note as an unread source."""
    with connection(business) as conn:
        try:
            return sources.create_paste(conn, content, label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/source/upload")
async def add_upload(business: Business, file: UploadFile = File(...),
                     label: str | None = None) -> dict:
    """Step 10. Store a file whole, bytes and all.

    `async def` here, unlike everything else: reading the upload is async in
    Starlette, and the database work is a single fast insert. The bytes are read
    fully into memory first, which is why sources.MAX_UPLOAD_BYTES exists.
    """
    data = await file.read()
    with connection(business) as conn:
        try:
            return sources.create_upload(
                conn, file.filename or "upload", file.content_type, data, label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/source")
def list_sources(business: Business, status: str | None = None) -> list[dict]:
    """Newest first. Never includes the bytes -- only their size."""
    with connection(business) as conn:
        return sources.listing(conn, status)


@app.get("/source/{source_id}")
def get_source(business: Business, source_id: str) -> dict:
    with connection(business) as conn:
        source = sources.get(conn, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="No such source.")
        return source


@app.post("/source/{source_id}/extract")
def extract_source(business: Business, source_id: str,
                   dry_run: bool = False) -> dict:
    """Steps 12 and 13.

    `dry_run=true` returns what it would store and writes nothing -- worth using
    on a new kind of document before letting it near the review queue.
    Otherwise the facts and the source's new status commit together, and a
    failure leaves no facts behind, only an error on the source.
    """
    with connection(business) as conn:
        source = sources.get(conn, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="No such source.")

    # A file that has not been read yet is transcribed first. Image and paste
    # sources are the same thing once there is text (step 14).
    if source["kind"] == "file" and not (source["content"] or "").strip():
        with connection(business) as conn:
            try:
                source["content"] = vision.read_into_source(conn, source_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None

    if dry_run:
        with connection(business) as conn:
            try:
                facts = extract.read(conn, source)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"source_id": source_id, "status": source["status"],
                "dry_run": True, "transcript": source["content"],
                "facts": facts}

    # extract.run takes the business rather than a connection: it deliberately
    # holds no transaction across its model calls, so it opens its own.
    return extract.run(business, source)


@app.post("/source/{source_id}/read")
def read_source(business: Business, source_id: str) -> dict:
    """Step 14. Transcribe a stored image or PDF into the source's text.

    Separate from extraction so the transcription can be inspected on its own --
    it is the artifact worth looking at when facts come out wrong, and it is
    what the owner would be shown to explain where a fact came from.
    """
    with connection(business) as conn:
        try:
            text = vision.read_into_source(conn, source_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"source_id": source_id, "transcript": text,
                "lines": len([l for l in text.splitlines() if l.strip()])}


@app.get("/review")
def review_queue(business: Business) -> list[dict]:
    """Step 17. Unconfirmed facts, each with the source text it came from."""
    with connection(business) as conn:
        return review.queue(conn)


@app.patch("/review/{fact_id}")
def edit_fact(business: Business, fact_id: str, subject: str | None = None,
              attribute: str | None = None, value: str | None = None) -> dict:
    """Step 18. Correct a proposal. Does not confirm it."""
    with connection(business) as conn:
        if not review.edit(conn, fact_id, subject, attribute, value):
            raise HTTPException(status_code=404, detail="No such fact.")
        return {"id": fact_id, "edited": True, "confirmed": False}


@app.post("/review/{fact_id}/confirm")
def confirm_fact(business: Business, fact_id: str) -> dict:
    """Step 18. Accept it. Embeds the fact and reports what it now contradicts."""
    with connection(business) as conn:
        result = review.confirm(conn, fact_id)
        if result is None:
            raise HTTPException(status_code=404, detail="No such fact.")
        return result


@app.delete("/review/{fact_id}")
def reject_fact(business: Business, fact_id: str) -> dict:
    """Step 18. The extraction was wrong. Unconfirmed facts only.

    The id in the path is another business's to guess at, and it no longer
    matters that it is: the UPDATE and DELETE arms of the policy mean a fact
    outside this tenant is not found rather than deleted. That is the endpoint
    the deploy conversation kept naming, and it is closed by the database.
    """
    with connection(business) as conn:
        if not review.reject(conn, fact_id):
            raise HTTPException(
                status_code=404,
                detail="No such unconfirmed fact. Confirmed facts are removed "
                       "elsewhere -- rejecting means the extraction was wrong, "
                       "not that the thing stopped being true.")
        return {"id": fact_id, "rejected": True}


@app.get("/conflicts")
def list_conflicts(business: Business) -> list[dict]:
    """Step 19. Subject+attribute pairs answered more than one way.

    Surfaced, never resolved. Two opening-hours values may both be true.
    """
    with connection(business) as conn:
        return review.conflicts(conn)


# --- test console -----------------------------------------------------------
# The onboarding screen where the owner talks to their agent before anyone else
# can reach it. No auth yet (see current_business above), no conversation
# history, and no second answer path -- /console/ask calls the same answer() a
# customer gets.


@app.post("/console/ask")
def console_ask(business: Business, q: str,
                from_suggestion: bool = False) -> dict:
    with connection(business) as conn:
        return console.ask(conn, q, from_suggestion=from_suggestion)


@app.get("/console/suggestions")
def console_suggestions(business: Business, limit: int = 3) -> list[dict]:
    """Starter questions generated from confirmed facts. Returns FEWER than
    asked rather than one that would fail -- the screen promises these have
    answers."""
    with connection(business) as conn:
        return console.suggestions(conn, limit=limit)


@app.post("/console/feedback")
def console_feedback(business: Business, q: str, verdict: str,
                     reason: str | None = None) -> dict:
    """Right/wrong against the question, the answer, the route and the scores.

    `reason` is only used for the one distinction code cannot make: whether a
    retrieved fact is wrong, or a correct fact was used wrongly.
    """
    try:
        with connection(business) as conn:
            return console.record(conn, q, verdict, reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- signing in -------------------------------------------------------------
# Every path here is in PUBLIC_PATHS, necessarily: you cannot require a session
# to get one.


@app.get("/auth/me")
def auth_me(request: Request) -> dict:
    """Are you signed in, and as whom. 200 either way.

    Not a 401 when logged out, deliberately: the screens call this on load to
    decide whether to show the app or the sign-in form, and "not signed in" is
    an answer rather than a failure. Returning an error status for the ordinary
    case makes every console in the browser look broken.
    """
    business_id = auth.resolve(request)
    if business_id is None:
        return {"business": None, "email": None}
    with connection(business_id) as conn:
        row = conn.execute("select name, owner_email from business").fetchone()
    return {"business": row[0] if row else None,
            "email": row[1] if row else None}


@app.post("/auth/request")
def auth_request(email: str = Form(...)) -> dict:
    """Send a sign-in link. Says the same thing whether or not the address is known.

    An honest "no such account" here turns this endpoint into an address
    checker for anyone who wants to know which clinics use Talkwisp. The link
    is only sent if there is somewhere to send it; the reply does not say which
    happened.
    """
    link = auth.issue_link(email)
    if link:
        auth.deliver(email, link)
    return {"sent": True,
            "message": "If that address has an account, a sign-in link is on "
                       "its way. It expires in 15 minutes."}


def _auth_page(title: str, body: str, status: int = 200) -> HTMLResponse:
    """A plain page for the states the SPA never gets to render.

    A magic link is opened by a mail client as a fresh navigation, so there is
    no app running yet to show a message in. These are deliberately unstyled and
    tiny -- they exist so a failure says what happened instead of showing a
    blank screen or a JSON blob.
    """
    return HTMLResponse(status_code=status, content=(
        f"<!doctype html><meta charset=utf-8><title>{title}</title>"
        "<body style='font:16px/1.6 system-ui;max-width:34rem;margin:18vh auto;"
        "padding:0 1.5rem;color:#1c2430'>"
        f"<h1 style='font-size:1.3rem;margin:0 0 .6rem'>{title}</h1>"
        f"<p style='margin:0 0 1.4rem;color:#4a5462'>{body}</p>"
        f"<a href='{auth.PUBLIC_BASE_URL}/app/' style='color:#2f6f4f'>"
        "Back to sign in</a></body>"))


@app.get("/auth/callback")
def auth_callback(token: str):
    """Claim a link, once, and start a session.

    Each failure says which failure it is. "Already used" and "expired" send a
    person to different next actions -- one means check for a newer email, the
    other means ask for a fresh link -- and collapsing them into one error makes
    a working system look broken.
    """
    business_id, outcome = auth.claim_link(token)
    if outcome != "ok" or business_id is None:
        title, body = {
            "used": ("This link has already been used",
                     "Sign-in links work once. Ask for a new one."),
            "expired": ("This link has expired",
                        "Links last 15 minutes. Ask for a new one."),
            "unknown": ("This link is not valid",
                        "It may have been mistyped or truncated by a mail "
                        "client. Ask for a new one."),
        }[outcome]
        return _auth_page(title, body, status=400)

    response = RedirectResponse(f"{auth.PUBLIC_BASE_URL}/app/", status_code=303)
    auth.set_cookie(response, auth.create_session(business_id))
    return response


@app.post("/auth/logout")
def auth_logout(request: Request) -> JSONResponse:
    """Ends the session for real: the row is deleted, not just the cookie.

    Clearing the cookie alone would leave a working session behind for anyone
    who had already copied the value. This is the reason the sessions are a
    table rather than a signed cookie.
    """
    auth.end_session(request.cookies.get(auth.COOKIE))
    response = JSONResponse({"signed_out": True})
    auth.clear_cookie(response)
    return response


@app.get("/auth/telegram")
def auth_telegram(request: Request):
    """Telegram Login. OFF until a domain is linked in BotFather.

    Cannot be tested before then: Telegram refuses to send a login payload to a
    host that is not registered with /setdomain, so there is no way to obtain a
    genuine signed payload locally. Written now and gated so that the domain
    landing is a config change rather than a build.

    The token this verifies against is the PLATFORM bot's -- the one that signs
    web logins. Not business.bot_token, which is a customer's agent bot and has
    nothing to do with login.
    """
    if not auth.TELEGRAM_LOGIN_ENABLED:
        raise HTTPException(
            status_code=404,
            detail="Telegram Login is off. It needs TELEGRAM_LOGIN_ENABLED, a "
                   "platform bot token, and /setdomain in BotFather against "
                   "the real hostname.")

    fields = dict(request.query_params)
    ok, why = auth.verify_telegram(fields)
    if not ok:
        return _auth_page("That sign-in could not be verified", why, status=400)

    business_id = auth.business_for_telegram(int(fields.get("id", "0")))
    if business_id is None:
        return _auth_page(
            "No account for that Telegram user",
            "This Telegram account is not the registered owner of any business.",
            status=403)

    response = RedirectResponse(f"{auth.PUBLIC_BASE_URL}/app/", status_code=303)
    auth.set_cookie(response, auth.create_session(business_id))
    return response


# --- the owner's screens ----------------------------------------------------
# One process: uvicorn serves the API and the built frontend. Mounted at /app
# rather than at / on purpose -- a mount at / is checked only after every route
# above it, so it works today and silently shadows any endpoint added later
# whose path happens to collide with a built asset.
#
# Registered LAST so nothing here can shadow an endpoint above.

_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _DIST.is_dir():
    app.mount("/app", StaticFiles(directory=_DIST, html=True), name="app")

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse("/app/")
else:  # pragma: no cover - a build that has not been run yet
    @app.get("/", include_in_schema=False)
    def _root() -> dict[str, str]:
        return {"status": "no frontend build",
                "run": "npm --prefix frontend run build"}

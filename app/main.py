from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.answer import answer as answer_question
from app import console, extract, review, sources, vision
from app.db import assert_app_role, connection, pool, sole_business
from app.llm import check_configured
from app.retrieval import find
from app.typed import parse as parse_fact, store as store_fact


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail here, not on the first customer message.
    check_configured()
    pool.open()
    # And fail here rather than on the first cross-tenant read, which would not
    # fail at all. See app/db.py: a superuser bypasses every policy in 0007.
    assert_app_role()
    yield
    pool.close()


app = FastAPI(title="Talkwisp", lifespan=lifespan)


def current_business() -> str:
    """Which business this request is for.

    THE STAND-IN FOR AUTH, and the only thing in the codebase that answers this
    question. There is no login yet, so the only honest answer is "the only
    business there is" -- and app_sole_business() raises rather than choosing
    once that stops being true.

    When auth lands, this function's body becomes cookie -> session -> user ->
    business_id and nothing else in the file changes. That is why it is a
    dependency and not a module constant.
    """
    return sole_business()


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

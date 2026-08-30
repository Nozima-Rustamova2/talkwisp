from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile

from app.answer import answer as answer_question
from app import extract, review, sources, vision
from app.db import pool
from app.llm import check_configured
from app.retrieval import find
from app.typed import parse as parse_fact, store as store_fact


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail here, not on the first customer message.
    check_configured()
    pool.open()
    yield
    pool.close()


app = FastAPI(title="Talkwisp", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> dict[str, str | None]:
    with pool.connection() as conn:
        database, pgvector = conn.execute(
            "select current_database(),"
            " (select extversion from pg_extension where extname = 'vector')"
        ).fetchone()
    return {"status": "ok", "database": database, "pgvector": pgvector}


@app.get("/ask")
def ask(q: str) -> dict:
    """Facts only: no LLM, no vectors. What the deterministic path can answer."""
    with pool.connection() as conn:
        return find(conn, q)


@app.get("/answer")
def answer_endpoint(q: str) -> dict:
    """The full path: facts, then prose, then an honest refusal."""
    with pool.connection() as conn:
        return answer_question(conn, q)


@app.post("/fact")
def add_fact(line: str, confirm: bool = False) -> dict:
    """Parse one free-text line into a fact.

    Without `confirm=true` this only shows what it would write, plus any
    confirmed fact that already answers the same subject and attribute
    differently. A mis-parse written blind becomes a confirmed fact, and
    confirmed is precisely what nothing downstream questions.
    """
    with pool.connection() as conn:
        result = parse_fact(conn, line)
        if result["error"] or not confirm:
            result["written"] = False
            return result
        result["id"] = str(store_fact(conn, result["parsed"]))
        result["written"] = True
        return result


@app.post("/source/paste")
def add_paste(content: str, label: str | None = None) -> dict:
    """Step 9. Store a pasted note as an unread source."""
    with pool.connection() as conn:
        try:
            return sources.create_paste(conn, content, label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/source/upload")
async def add_upload(file: UploadFile = File(...),
                     label: str | None = None) -> dict:
    """Step 10. Store a file whole, bytes and all.

    `async def` here, unlike everything else: reading the upload is async in
    Starlette, and the database work is a single fast insert. The bytes are read
    fully into memory first, which is why sources.MAX_UPLOAD_BYTES exists.
    """
    data = await file.read()
    with pool.connection() as conn:
        try:
            return sources.create_upload(
                conn, file.filename or "upload", file.content_type, data, label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/source")
def list_sources(status: str | None = None) -> list[dict]:
    """Newest first. Never includes the bytes -- only their size."""
    with pool.connection() as conn:
        return sources.listing(conn, status)


@app.get("/source/{source_id}")
def get_source(source_id: str) -> dict:
    with pool.connection() as conn:
        source = sources.get(conn, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="No such source.")
        return source


@app.post("/source/{source_id}/extract")
def extract_source(source_id: str, dry_run: bool = False) -> dict:
    """Steps 12 and 13.

    `dry_run=true` returns what it would store and writes nothing -- worth using
    on a new kind of document before letting it near the review queue.
    Otherwise the facts and the source's new status commit together, and a
    failure leaves no facts behind, only an error on the source.
    """
    with pool.connection() as conn:
        source = sources.get(conn, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="No such source.")

    # A file that has not been read yet is transcribed first. Image and paste
    # sources are the same thing once there is text (step 14).
    if source["kind"] == "file" and not (source["content"] or "").strip():
        with pool.connection() as conn:
            try:
                source["content"] = vision.read_into_source(conn, source_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None

    if dry_run:
        with pool.connection() as conn:
            try:
                facts = extract.read(conn, source)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"source_id": source_id, "status": source["status"],
                "dry_run": True, "transcript": source["content"],
                "facts": facts}

    return extract.run(source)


@app.post("/source/{source_id}/read")
def read_source(source_id: str) -> dict:
    """Step 14. Transcribe a stored image or PDF into the source's text.

    Separate from extraction so the transcription can be inspected on its own --
    it is the artifact worth looking at when facts come out wrong, and it is
    what the owner would be shown to explain where a fact came from.
    """
    with pool.connection() as conn:
        try:
            text = vision.read_into_source(conn, source_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"source_id": source_id, "transcript": text,
                "lines": len([l for l in text.splitlines() if l.strip()])}


@app.get("/review")
def review_queue() -> list[dict]:
    """Step 17. Unconfirmed facts, each with the source text it came from."""
    with pool.connection() as conn:
        return review.queue(conn)


@app.patch("/review/{fact_id}")
def edit_fact(fact_id: str, subject: str | None = None,
              attribute: str | None = None, value: str | None = None) -> dict:
    """Step 18. Correct a proposal. Does not confirm it."""
    with pool.connection() as conn:
        if not review.edit(conn, fact_id, subject, attribute, value):
            raise HTTPException(status_code=404, detail="No such fact.")
        return {"id": fact_id, "edited": True, "confirmed": False}


@app.post("/review/{fact_id}/confirm")
def confirm_fact(fact_id: str) -> dict:
    """Step 18. Accept it. Embeds the fact and reports what it now contradicts."""
    with pool.connection() as conn:
        result = review.confirm(conn, fact_id)
        if result is None:
            raise HTTPException(status_code=404, detail="No such fact.")
        return result


@app.delete("/review/{fact_id}")
def reject_fact(fact_id: str) -> dict:
    """Step 18. The extraction was wrong. Unconfirmed facts only."""
    with pool.connection() as conn:
        if not review.reject(conn, fact_id):
            raise HTTPException(
                status_code=404,
                detail="No such unconfirmed fact. Confirmed facts are removed "
                       "elsewhere -- rejecting means the extraction was wrong, "
                       "not that the thing stopped being true.")
        return {"id": fact_id, "rejected": True}


@app.get("/conflicts")
def list_conflicts() -> list[dict]:
    """Step 19. Subject+attribute pairs answered more than one way.

    Surfaced, never resolved. Two opening-hours values may both be true.
    """
    with pool.connection() as conn:
        return review.conflicts(conn)

"""Getting raw material in. Two ways: pasted text, or an uploaded file.

Both produce one `source` row with `status = 'pending'`. Nothing is parsed
here -- a source is the unread thing the owner handed over, and extraction
(steps 12-14) is what turns it into facts and chunks.

Keeping the two apart matters: if a source were only created once extraction
succeeded, a failed extraction would leave nothing to retry and nothing to show
the owner. The row comes first; the reading happens after.
"""

from psycopg import Connection

# Uploads are read into memory and stored in `bytea`, so an unbounded file is a
# way to kill the process. 20 MB comfortably covers the real input -- a phone
# photo of a price list is 2-5 MB, a scanned PDF menu maybe 10 -- and anything
# larger is a different feature (resumable upload, object storage) rather than a
# bigger number here.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# What the vision step (14) will actually be asked to read. Rejecting here means
# the owner is told immediately, instead of the file sitting at `pending`
# forever because nothing downstream knows what to do with it.
ALLOWED_MEDIA = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf", "text/plain",
}

_COLUMNS = ("id, kind, label, filename, media_type, content, status, error,"
            " octet_length(bytes) as size, created_at, extracted_at")


def _row(record) -> dict:
    """Never return `bytes` itself -- it is megabytes of binary, and every
    caller wants the size, not the payload."""
    keys = ("id", "kind", "label", "filename", "media_type", "content",
            "status", "error", "size", "created_at", "extracted_at")
    row = dict(zip(keys, record))
    row["id"] = str(row["id"])
    for k in ("created_at", "extracted_at"):
        row[k] = row[k].isoformat() if row[k] else None
    return row


def create_paste(conn: Connection, content: str, label: str | None = None) -> dict:
    """Step 9: a note the owner typed or pasted."""
    content = (content or "").strip()
    if not content:
        raise ValueError("Empty paste. There is nothing to extract from.")

    record = conn.execute(
        f"insert into source (kind, label, content) values ('paste', %s, %s)"
        f" returning {_COLUMNS}",
        (label or None, content),
    ).fetchone()
    return _row(record)


def create_upload(conn: Connection, filename: str, media_type: str | None,
                  data: bytes, label: str | None = None) -> dict:
    """Step 10: a file, stored whole. The bytes are the source of truth.

    Kept in the database rather than on disk so that "one transaction per file"
    stays honest at step 13: the file and the facts it produced commit or roll
    back together, and a backup is complete by itself.
    """
    if not data:
        raise ValueError("Empty file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"File is {len(data) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB."
        )
    if media_type not in ALLOWED_MEDIA:
        raise ValueError(
            f"Cannot read {media_type or 'unknown'} files. Send a photo, a PDF, "
            f"or paste the text instead."
        )

    record = conn.execute(
        f"insert into source (kind, label, filename, media_type, bytes)"
        f" values ('file', %s, %s, %s, %s) returning {_COLUMNS}",
        (label or None, filename, media_type, data),
    ).fetchone()
    return _row(record)


def listing(conn: Connection, status: str | None = None) -> list[dict]:
    """Newest first. uuidv7 sorts by creation time, so the primary key is
    already the right order and no index on created_at is needed."""
    if status:
        records = conn.execute(
            f"select {_COLUMNS} from source where status = %s order by id desc",
            (status,),
        ).fetchall()
    else:
        records = conn.execute(
            f"select {_COLUMNS} from source order by id desc").fetchall()
    return [_row(r) for r in records]


def get(conn: Connection, source_id: str) -> dict | None:
    record = conn.execute(
        f"select {_COLUMNS} from source where id = %s", (source_id,)).fetchone()
    return _row(record) if record else None

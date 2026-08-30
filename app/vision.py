"""Read a photographed price list into text. Step 14.

Transcribe first, then let the ordinary text extraction (steps 12-13) do the
rest. This is NOT what the strategy doc assumed -- it expected facts pulled
straight out of the image -- and the reversal was measured, not preferred:

  On a photographed menu where one row's price was cropped out of frame,
  direct image-to-facts shifted EVERY subsequent price up by one row, at
  confidence 0.95-0.99. Transcription of the same photo, same model, paired
  every item with its correct price and left the missing ones blank.

  On a second menu where every row had a price, direct extraction was correct.
  So the failure only appears on incomplete lists -- and nothing in the output
  distinguishes the two cases.

The cost of transcribing is one extra model call. The cost of the alternative
is quoting a customer someone else's price.

Once transcribed, an image source and a pasted note are the same thing: text in
`source.content`. The two pipelines converge here instead of running in parallel.
"""

from psycopg import Connection

from app.llm import complete

# What the model is told it is looking at. The instruction not to tidy matters:
# a "corrected" spelling is a fact that no longer matches the photo, and the
# owner reviewing it has no way to tell.
_SYSTEM = """You transcribe photographs of a business's own printed material:
price lists, menus, notice boards, signs.

Rules:
- Reproduce every line, in reading order, exactly as printed. Include headings.
- Do NOT translate. Do NOT correct spelling. Do NOT tidy the formatting.
- Keep the characters actually printed, apostrophes included.
- **Never move a price from one row to another.** If a row has no visible
  price, write the row with no price rather than borrowing the next row's.
  Getting a price onto the wrong item is far worse than leaving it blank.
- If part of a line is unreadable or cut off, transcribe the part you can read
  and mark the rest [?]. Never complete a word you cannot see.
- Multi-column layouts: finish one column before starting the next.
- Reply with the transcribed text only, nothing else."""


def transcribe(media_type: str, data: bytes) -> str:
    """Bytes in, text out. No database, no writes."""
    text = complete(
        _SYSTEM,
        "Transcribe this image.",
        image=(media_type, data),
    ).strip()
    if not text:
        raise ValueError("The model returned nothing for this image.")
    return text


def fetch_bytes(conn: Connection, source_id: str) -> tuple[str, bytes] | None:
    record = conn.execute(
        "select media_type, bytes from source where id = %s and bytes is not null",
        (source_id,),
    ).fetchone()
    if record is None:
        return None
    return record[0], bytes(record[1])


def read_into_source(conn: Connection, source_id: str) -> str:
    """Transcribe a stored file and save the text on the source.

    `status` stays `pending`: transcription is not extraction. A source with
    `content` set and status `pending` is a file that has been read but whose
    facts have not been proposed yet -- which is exactly what it is, and it
    needs no new status value to say so.
    """
    found = fetch_bytes(conn, source_id)
    if found is None:
        raise ValueError("This source has no stored file to read.")
    media_type, data = found

    text = transcribe(media_type, data)
    conn.execute("update source set content = %s where id = %s",
                 (text, source_id))
    return text

"""Does a vision model actually read a photographed Uzbek price list?

    uv run python probe_vision.py <path-to-photo> [model]

Answers three questions, in order of how much they matter:

  1. COMPLETENESS -- does every row come back? A list of twenty services where
     seventeen return looks fine until a customer asks about the missing three.
     This is the same failure as the missed biochemical price, and it is worse
     on a photo because there is no source text to check against afterwards.

  2. CHARACTER FIDELITY -- are the apostrophes the ones normalize() folds, and
     is there Cyrillic contamination in Latin words? "qаbul" with a Cyrillic
     "а" looks identical and normalizes to a different key, so the fact becomes
     unreachable.

  3. Whether extracting facts directly from the image beats transcribing first.

Writes nothing to the database. This is a measurement, not a feature.
"""

import base64
import json
import pathlib
import re
import sys
import unicodedata
import urllib.error
import urllib.request

from app import gemini_keys
from app.normalize import APOSTROPHES, CYRILLIC, normalize

sys.stdout.reconfigure(encoding="utf-8")

TRANSCRIBE = """Transcribe this image exactly as it appears.

Rules:
- Every line, in order, including headings and anything handwritten.
- Do NOT translate, correct spelling, or tidy the formatting.
- Reproduce the characters you actually see: if a word is written with an
  apostrophe, keep that apostrophe.
- If a line is unreadable, write it as [?] rather than guessing.
- Reply with the text only."""

EXTRACT = """This is a business's own price list or notice, photographed.

Pull out every fact a customer might ask about, as a JSON array:
  {"subject": "...", "attribute": "...", "value": "...", "confidence": 0.0-1.0}

One fact per question a customer would ask. Do not miss rows: count the rows in
the image, and make sure your list accounts for every one of them. Prices stay
as written, including ranges. Reply with JSON only."""


def ask(model: str, image: bytes, media_type: str, prompt: str) -> str:
    body = json.dumps({
        "contents": [{"parts": [
            {"inline_data": {"mime_type": media_type,
                             "data": base64.b64encode(image).decode()}},
            {"text": prompt},
        ]}],
        "generationConfig": {"temperature": 0},
    }).encode()
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    for _ in range(gemini_keys.count()):
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"x-goog-api-key": gemini_keys.current(),
                     "Content-Type": "application/json"})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=180))
        except urllib.error.HTTPError as e:
            detail = json.load(e).get("error", {}).get("message", "")
            if e.code == 429:
                gemini_keys.rotate()
                continue
            raise SystemExit(f"{model}: HTTP {e.code} {detail[:200]}")
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p["text"] for p in parts
                       if "text" in p and not p.get("thought")).strip()
    raise SystemExit("all keys out of quota")


def audit(text: str) -> None:
    """The character-level check. This is what normalize() has to survive."""
    apostrophes = {c: text.count(c) for c in APOSTROPHES if c in text}
    print(f"  apostrophe characters seen: "
          f"{ {unicodedata.name(c, hex(ord(c))): n for c, n in apostrophes.items()} }"
          if apostrophes else "  apostrophe characters seen: none")

    # A Cyrillic letter inside an otherwise-Latin word is invisible to a reader
    # and fatal to matching.
    suspect = []
    for word in re.findall(r"\S+", text):
        has_latin = any("a" <= c.lower() <= "z" for c in word)
        cyrillic = [c for c in word if c.lower() in CYRILLIC]
        if has_latin and cyrillic:
            suspect.append((word, "".join(cyrillic)))
    if suspect:
        print(f"  MIXED-SCRIPT WORDS (invisible, breaks matching): {suspect[:8]}")
    else:
        print("  mixed-script words: none")

    lines = [l for l in text.splitlines() if l.strip()]
    print(f"  non-empty lines: {len(lines)}")
    unreadable = sum(l.count("[?]") for l in lines)
    if unreadable:
        print(f"  lines the model refused to guess at: {unreadable}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    path = pathlib.Path(sys.argv[1])
    model = sys.argv[2] if len(sys.argv) > 2 else "gemini-3.6-flash"
    if not path.exists():
        raise SystemExit(f"No such file: {path}")

    media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
             ".webp": "image/webp", ".heic": "image/heic",
             ".pdf": "application/pdf"}.get(path.suffix.lower())
    if media is None:
        raise SystemExit(f"Unsupported file type: {path.suffix}")

    image = path.read_bytes()
    print(f"{path.name}  {len(image) / 1024:.0f} KB  {media}  model={model}\n")

    print("=" * 70)
    print("1. TRANSCRIPTION")
    print("=" * 70)
    text = ask(model, image, media, TRANSCRIBE)
    print(text)
    print("\ncharacter audit:")
    audit(text)

    print("\n" + "=" * 70)
    print("2. NORMALIZED KEYS (what retrieval would actually match on)")
    print("=" * 70)
    for line in [l for l in text.splitlines() if l.strip()][:12]:
        print(f"  {line.strip()[:44]:46} -> {normalize(line)[:44]}")

    print("\n" + "=" * 70)
    print("3. FACTS EXTRACTED DIRECTLY FROM THE IMAGE")
    print("=" * 70)
    raw = ask(model, image, media, EXTRACT)
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        print(raw[:500])
        return
    facts = json.loads(match.group(0))
    for f in facts:
        print(f"  {float(f.get('confidence', 0)):.2f}  {f.get('subject')} / "
              f"{f.get('attribute')} = {f.get('value')}")
    print(f"\n  {len(facts)} facts from "
          f"{len([l for l in text.splitlines() if l.strip()])} transcribed lines.")
    print("  Compare by eye: which rows in the photo produced no fact?")


if __name__ == "__main__":
    main()

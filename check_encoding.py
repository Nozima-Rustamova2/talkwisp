"""Every source file is UTF-8, with no BOM and no mojibake.

    uv run python check_encoding.py

This exists because a one-line PowerShell `Get-Content -Raw` / `Set-Content`
round-trip over two .tsx files re-encoded every non-ASCII character with the
system codepage: 27 em dashes and quotation marks turned into mojibake, and each
file gained a BOM. **The build stayed green.** Mojibake inside a comment or a
string literal is valid TypeScript, so tsc, vite and the browser would all have
shipped it, and the only thing that caught it was decoding the bytes by hand.

Decoding the bytes by hand is not a check anyone runs habitually, and the
project's own ranking says habits are the weakest remedy because they have no
failure signal. So this is the mechanism instead. It costs about a second.

The failures it catches are all silent by construction:

  * mojibake  -- valid syntax in every language here, so nothing downstream
                 complains; it surfaces years later as a corrupted quote in the
                 UI or a comment nobody can read.
  * a BOM     -- invisible in every editor, and it breaks a shebang, a leading
                 SQL statement, and byte-exact comparisons of the kind
                 questions.py relies on.
  * non-UTF-8 -- the file that a tool has already half-converted.

No database, no network, no model.
"""

import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# Text we author. Binary and vendored trees are excluded by asking git what it
# tracks rather than by walking the disk -- node_modules alone would make this
# take minutes and report other people's encoding choices as our bugs.
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".css", ".html", ".md", ".sql",
            ".json", ".toml", ".yml", ".yaml", ".txt", ".jsonl"}

# The prototype is a design tool's output, not source we edit by hand, and the
# .dc.html files carry their own encoding conventions. Checked separately if
# ever needed; not this file's business.
SKIP_PREFIXES = ("prototype/", "frontend/dist/", "graphify-out/")

# What a UTF-8 file looks like after a round trip through a single-byte
# codepage. These are the leading bytes of multi-byte UTF-8 sequences as they
# appear once decoded as cp1251 or latin-1.
#
# None of these can occur legitimately here: the project writes Latin, Cyrillic
# (U+04xx) and typographic punctuation, and every marker below is either a
# Latin-1 supplement letter this project never uses, or a Cyrillic letter
# followed by one it would never sit beside.
MARKERS = {
    "вЂ": "cp1251 mojibake (an em dash or curly quote went through Windows-1251)",
    "РІ": "cp1251 mojibake",
    "Ð": "latin-1 mojibake (Cyrillic read as Latin-1)",
    "â€": "latin-1 mojibake (punctuation read as Latin-1)",
    "Ã¢": "latin-1 mojibake",
    "Ã©": "latin-1 mojibake",
}

BOM = b"\xef\xbb\xbf"

# This file necessarily CONTAINS every marker above, as the literals that
# define them, so it is the one file the marker scan must skip -- it would
# otherwise report itself forever. It is still checked for a BOM and for valid
# UTF-8, which are the two failures it can actually have.
#
# Found the day after this check was written, and only because it had since
# been COMMITTED: `git ls-files` did not list it on its first run, so the first
# green result was over 87 files that did not include this one. A check that
# has never been run against itself has an untested case, and the untested
# case here was a guaranteed failure.
SELF = pathlib.Path(__file__).name

passed = failed = 0


def fail(path: str, why: str) -> None:
    global failed
    failed += 1
    print(f"  [FAIL] {path}")
    print(f"         {why}")


tracked = subprocess.run(
    ["git", "ls-files", "-z"], capture_output=True, check=True,
).stdout.decode("utf-8").split("\0")

files = [
    f for f in tracked
    if f and pathlib.Path(f).suffix in SUFFIXES
    and not f.startswith(SKIP_PREFIXES)
    and pathlib.Path(f).is_file()
]

print(f"\nchecking {len(files)} tracked source files")

for name in files:
    raw = pathlib.Path(name).read_bytes()

    if raw.startswith(BOM):
        fail(name, "starts with a UTF-8 BOM. Invisible in every editor, and it "
                   "breaks shebangs, leading SQL, and byte-exact comparison.")
        continue

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(name, f"is not valid UTF-8: {exc}")
        continue

    if pathlib.Path(name).name == SELF:
        passed += 1
        continue

    hit = next(((m, why) for m, why in MARKERS.items() if m in text), None)
    if hit:
        marker, why = hit
        line = next(i for i, l in enumerate(text.splitlines(), 1) if marker in l)
        fail(name, f"line {line}: {why}. Found {marker!r}. "
                   "Restore from git and redo the edit without PowerShell's "
                   "Get-Content/Set-Content, which re-encodes with the system "
                   "codepage.")
        continue

    passed += 1

print(f"\n{passed} clean, {failed} failed")
sys.exit(1 if failed else 0)

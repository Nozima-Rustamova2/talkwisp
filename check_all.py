"""Run every check, in one command.

    uv run python check_all.py            everything that is free
    uv run python check_all.py --paid     including the ones that spend
    uv run python check_all.py --only bot owners

WHY THIS EXISTS. check_orders.py was red for ten days. Nothing was ignoring it;
running the suite meant remembering twenty-nine separate commands, so "the
checks are green" meant "the checks I thought to run are green" -- which is this
codebase's own drift pattern pointed at its own tooling. One command closes the
gap.

IT DISCOVERS RATHER THAN LISTS. The set comes from globbing check_*.py, so a
check written next month is in the suite the moment it exists. A hardcoded list
would need remembering, which is the thing that failed.

THE PAID ONES ARE SKIPPED BY DEFAULT AND NAMED WHEN THEY ARE. check_answer and
check_buy are ninety questions each -- about an hour and a hundred and eighty
generations for the pair -- and check_fragile spends nine. A suite that quietly
costs an hour is a suite people stop running, which is the failure this file
exists to fix, so the default is the one that gets run.

Each check is a separate process on purpose. They set module globals, monkeypatch
each other's modules and point MESSAGE_LOG at scratch files; sharing an
interpreter would let one check's leftovers decide another's result.
"""

import argparse
import pathlib
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = pathlib.Path(__file__).parent

# Checks that call a model. Named individually rather than detected, because
# "does this spend money" is not something a filename knows.
PAID = {
    "check_answer": "90 questions, ~45 min",
    "check_buy": "90 questions, ~45 min",
    "check_fragile": "9 generations, ~2 min",
}

# Per check. Generous: check_knowledge re-embeds, check_owners talks to the API,
# and a timeout that fires on a slow machine reads as a failure.
TIMEOUT = 600

_SUMMARY = re.compile(r"(\d+) passed, (\d+) failed")


def discover(only: list[str]) -> list[pathlib.Path]:
    found = sorted(HERE.glob("check_*.py"))
    found = [p for p in found if p.name != "check_all.py"]
    if only:
        wanted = {name.replace("check_", "").replace(".py", "") for name in only}
        found = [p for p in found
                 if p.stem.replace("check_", "") in wanted]
        missing = wanted - {p.stem.replace("check_", "") for p in found}
        if missing:
            raise SystemExit(f"no such check: {', '.join(sorted(missing))}")
    return found


def run_one(path: pathlib.Path) -> tuple[bool, str, float]:
    started = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, path.name], cwd=HERE, timeout=TIMEOUT,
            capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT}s", time.monotonic() - started

    took = time.monotonic() - started
    output = (done.stdout or "") + (done.stderr or "")
    summary = _SUMMARY.search(output)
    if summary:
        note = f"{summary.group(1)} passed, {summary.group(2)} failed"
    elif done.returncode == 0:
        # Several checks report only through their exit code. Saying so is
        # better than printing a blank column that reads as "nothing ran".
        note = "ok (no summary line)"
    else:
        # The last non-empty line is usually the exception. Enough to know
        # WHICH check to run on its own, which is all this needs to do.
        lines = [line for line in output.strip().splitlines() if line.strip()]
        note = lines[-1][:90] if lines else f"exit {done.returncode}"
    return done.returncode == 0, note, took


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paid", action="store_true",
                    help="also run the checks that spend model calls")
    ap.add_argument("--only", nargs="+", default=[],
                    help="run just these, by name or bare suffix")
    args = ap.parse_args()

    checks = discover(args.only)
    skipped = []
    if not args.paid and not args.only:
        skipped = [p for p in checks if p.stem in PAID]
        checks = [p for p in checks if p.stem not in PAID]

    failures = []
    started = time.monotonic()
    for path in checks:
        ok, note, took = run_one(path)
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {path.stem:24} {took:6.1f}s  {note}", flush=True)
        if not ok:
            failures.append(path.stem)

    print(f"\n  {len(checks)} checks in {time.monotonic() - started:.0f}s")
    if skipped:
        # NAMED, WITH THEIR COST. A silent skip is how a suite starts meaning
        # less than whoever reads it thinks.
        print("  skipped (pass --paid to include):")
        for path in skipped:
            print(f"    {path.stem:24} {PAID[path.stem]}")
    if failures:
        print(f"\n  FAILED: {', '.join(failures)}")
        print(f"  run one on its own:  uv run python {failures[0]}.py")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

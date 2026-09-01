"""The clock, measured. Costs nothing -- no API, no database.

    uv run python check_time.py

The model is never asked to do date arithmetic, so this is where the arithmetic
is checked. If these pass and the bot still says the wrong day, the fault is in
the prompt, not the calendar -- which is the point of separating them.
"""

import datetime
import sys
import zoneinfo

from app.answer import BUSINESS_TZ, _clock

sys.stdout.reconfigure(encoding="utf-8")

failures = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok ' if condition else 'FAIL'} {name}" + (f"  -- {detail}" if detail else ""))
    if not condition:
        failures.append(name)


# 1. The timezone database is actually present. On Windows there is no system
#    IANA database, so this fails without the tzdata package -- which was
#    installed transitively and undeclared until 2026-09-01.
try:
    tz = zoneinfo.ZoneInfo(BUSINESS_TZ)
    check("timezone resolves", True, BUSINESS_TZ)
except Exception as exc:  # noqa: BLE001
    check("timezone resolves", False, f"{type(exc).__name__}: {exc}")
    print("\n  tzdata is missing. Everything below is meaningless.")
    sys.exit(1)

# 2. Uzbekistan is UTC+5 year round -- no daylight saving since 2005. If this
#    ever fails, the assumption changed, not the code.
offsets = {
    datetime.datetime(2026, m, 15, 12, tzinfo=tz).utcoffset() for m in (1, 4, 7, 10)
}
check("UTC+5 all year, no DST", offsets == {datetime.timedelta(hours=5)},
      f"offsets seen: {sorted(str(o) for o in offsets)}")

# 3. The case that makes a host clock dangerous: between 19:00 and 24:00 UTC it
#    is already TOMORROW in Tashkent. A server running on UTC would state the
#    wrong day for five hours out of every twenty-four.
utc_evening = datetime.datetime(2026, 9, 1, 20, 30, tzinfo=datetime.UTC)
in_tashkent = utc_evening.astimezone(tz)
check("host-clock divergence is real",
      in_tashkent.date() != utc_evening.date(),
      f"{utc_evening:%Y-%m-%d %H:%M} UTC is {in_tashkent:%Y-%m-%d %H:%M} in Tashkent")

# 4. Weekday arithmetic across a month boundary and a leap year, since those are
#    where hand-rolled date code usually breaks. datetime does not, but the
#    check documents that it was considered rather than assumed.
for start, expect in [
    (datetime.date(2026, 9, 30), "Thursday"),    # month boundary
    (datetime.date(2026, 12, 31), "Friday"),     # year boundary
    (datetime.date(2028, 2, 28), "Tuesday"),     # leap year: 29 Feb exists
    (datetime.date(2026, 2, 28), "Sunday"),      # non-leap: 1 Mar follows
]:
    got = (start + datetime.timedelta(days=1)).strftime("%A")
    check(f"day after {start}", got == expect, f"got {got}, expected {expect}")

# 5. The actual output: two lines, and TOMORROW is exactly one day after TODAY
#    with a weekday name that matches its own date.
lines = _clock().split("\n")
check("two lines", len(lines) == 2, repr(_clock()))

parsed = []
for line in lines:
    label, rest = line.split(": ", 1)
    weekday, iso = (p.strip() for p in rest.split(",", 1))
    date = datetime.date.fromisoformat(iso)
    parsed.append((label, weekday, date))
    check(f"{label} weekday matches its date", date.strftime("%A") == weekday,
          f"{iso} is a {date.strftime('%A')}, line says {weekday}")

(_, _, today), (_, _, tomorrow) = parsed
check("TOMORROW is TODAY + 1", tomorrow - today == datetime.timedelta(days=1),
      f"{today} -> {tomorrow}")

# 6. And it agrees with the business timezone, not this machine's.
check("TODAY is today in Tashkent",
      today == datetime.datetime.now(tz).date(),
      f"clock says {today}, Tashkent says {datetime.datetime.now(tz).date()}")

print()
if failures:
    print(f"  {len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("  all clock checks pass")

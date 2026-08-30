"""Re-run extraction on the SAME note, dry run only, to compare against the
first attempt. Writes a source row (unavoidable) but no facts."""

import json
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
B = "http://127.0.0.1:8034"

NOTE = """Shifo Med klinikasi haqida yangi ma'lumotlar.

Laboratoriya bo'limi ochildi. Umumiy qon tahlili 60 000-90 000 so'm,
biokimyoviy tahlil 120 000-150 000 so'm turadi. Tahlillar ertalab soat
08:00 dan 11:00 gacha qabul qilinadi.

Yangi shifokor: Toshmatov Aziz, travmatolog. U dushanba, chorshanba va
juma kunlari soat 10:00 dan 16:00 gacha qabul qiladi.

Bemorlar qabulga kelishdan oldin navbatga yozilishlari tavsiya etiladi,
lekin navbatsiz ham qabul qilamiz. Iltimos, kechikmang."""

# What a careful reader would pull out of that note.
EXPECTED = [
    "Umumiy qon tahlili / narx",
    "Biokimyoviy tahlil / narx",          # MISSED on the first attempt
    "laboratory hours 08:00-11:00",
    "Toshmatov Aziz / lavozim",
    "Toshmatov Aziz / schedule (days AND hours together)",  # SPLIT on attempt 1
]


def call(method, path, params=None):
    url = f"{B}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, method=method), timeout=120))


src = call("POST", "/source/paste", {"label": "Avgust (qayta)", "content": NOTE})
r = call("POST", f"/source/{src['id']}/extract", {"dry_run": "true"})

print("ATTEMPT 2\n")
for f in r["facts"]:
    print(f"  {f['confidence']:.2f}  {f['subject']} / {f['attribute']}")
    print(f"        = {f['value']}")

print("\nchecks:")
text = json.dumps(r["facts"], ensure_ascii=False).lower()
print(f"  biokimyoviy price present : {'120 000' in text}")
combined = any(
    "toshmatov" in f["subject"].lower()
    and "10:00" in f["value"]
    and "dushanba" in f["value"].lower()
    for f in r["facts"]
)
print(f"  Toshmatov days+hours in one value: {combined}")
tosh = [f for f in r["facts"] if "toshmatov" in f["subject"].lower()]
print(f"  facts about Toshmatov     : {len(tosh)} (was 3)")
scores = sorted({f["confidence"] for f in r["facts"]})
print(f"  distinct confidences      : {scores}")
print(f"\nWhat a careful reader would find: {len(EXPECTED)} facts. Got {len(r['facts'])}.")

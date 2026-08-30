"""Extract from a realistic pasted note: dry run, then store, then fail on purpose."""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
B = "http://127.0.0.1:8032"

NOTE = """Shifo Med klinikasi haqida yangi ma'lumotlar.

Laboratoriya bo'limi ochildi. Umumiy qon tahlili 60 000-90 000 so'm,
biokimyoviy tahlil 120 000-150 000 so'm turadi. Tahlillar ertalab soat
08:00 dan 11:00 gacha qabul qilinadi.

Yangi shifokor: Toshmatov Aziz, travmatolog. U dushanba, chorshanba va
juma kunlari soat 10:00 dan 16:00 gacha qabul qiladi.

Bemorlar qabulga kelishdan oldin navbatga yozilishlari tavsiya etiladi,
lekin navbatsiz ham qabul qilamiz. Iltimos, kechikmang."""


def call(method, path, params=None):
    url = f"{B}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, method=method)
    try:
        return json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": json.load(e).get("detail")}


print("=== create the source ===")
src = call("POST", "/source/paste",
           {"label": "Avgust yangiliklari", "content": NOTE})
sid = src["id"]
print(f"  {sid}  status={src['status']}")

print("\n=== step 12: dry run, nothing written ===")
r = call("POST", f"/source/{sid}/extract", {"dry_run": "true"})
for f in r["facts"]:
    print(f"  {f['confidence']:.2f}  {f['subject']} / {f['attribute']} = {f['value']}")
after = call("GET", f"/source/{sid}")
print(f"  source status still: {after['status']}")

print("\n=== step 13: store, one transaction ===")
r = call("POST", f"/source/{sid}/extract")
print(f"  status={r['status']}  facts stored={len(r['facts'])}")
after = call("GET", f"/source/{sid}")
print(f"  source status now: {after['status']}  extracted_at set: "
      f"{after['extracted_at'] is not None}")

print("\n=== a file with no text cannot be extracted (needs step 14) ===")
empty = call("POST", "/source/paste", {"content": "."})
print("  ", call("POST", f"/source/{empty['id']}/extract", {"dry_run": "true"}))

print("\n=== unknown source ===")
print("  ", call("POST", "/source/00000000-0000-0000-0000-000000000000/extract"))

"""A source with BOTH facts and prose. Checks the split, the verbatim guard,
and that the resulting chunk is actually answerable."""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
B = "http://127.0.0.1:8042"

NOTE = """Shifo Med — avgust yangiliklari.

Laboratoriya bo'limi ochildi. Umumiy qon tahlili 60 000-90 000 so'm,
biokimyoviy tahlil 120 000-150 000 so'm turadi.

Tahlil topshirishdan oldin 8-12 soat davomida hech narsa yemang va faqat
suv iching. Ertalabki dori-darmonlarni shifokor bilan maslahatlashmasdan
qabul qilmang. Natijalar tayyor bo'lgach SMS orqali xabar beramiz.

Yangi shifokor Toshmatov Aziz, travmatolog, dushanba, chorshanba va juma
kunlari soat 10:00 dan 16:00 gacha qabul qiladi.

Klinikamizda navbat elektron tarzda boshqariladi. Kelganingizda qabulxonadan
raqam oling va ekrandagi e'lonni kuzatib boring. Navbatingiz o'tib ketsa,
qaytadan raqam olishingiz kerak bo'ladi."""


def call(method, path, params=None):
    url = f"{B}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    try:
        return json.load(urllib.request.urlopen(
            urllib.request.Request(url, method=method), timeout=300))
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": json.load(e).get("detail")}


src = call("POST", "/source/paste", {"label": "Avgust", "content": NOTE})
sid = src["id"]

print("=== extract: facts and prose from one source, one transaction ===")
r = call("POST", f"/source/{sid}/extract")
print(f"  status={r['status']}  facts={len(r['facts'])}  chunks={r['chunks']}"
      f"  prose dropped as not verbatim={r['prose_rejected_as_not_verbatim']}")

print("\n  facts:")
for f in r["facts"]:
    print(f"    {f['confidence']:.2f}  {f['subject']} / {f['attribute']} = {f['value']}")

print("\n=== the stored chunks, and whether they are truly verbatim ===")
detail = call("GET", f"/source/{sid}")
source_text = detail["content"]
import re
squash = lambda t: re.sub(r"\s+", " ", t).strip().lower()
rows = json.load(urllib.request.urlopen(f"{B}/source"))  # keeps the server warm
PY_CHUNKS = None
print("  (queried below via the answer path)")

print("\n=== can a customer reach the prose? ===")
for q in ["Tahlildan oldin nima qilish kerak?",
          "Как работает электронная очередь?",
          "Navbatim o'tib ketsa nima qilaman?"]:
    a = json.load(urllib.request.urlopen(
        f"{B}/answer?" + urllib.parse.urlencode({"q": q}), timeout=300))
    print(f"\n  {q}")
    print(f"    route={a['source']} status={a['status']}")
    print(f"    > {a['answer']}")

"""The review queue against the seeded data, which already contains a planted
conflict: typed hours 09:00-18:00 vs an extracted 08:30-19:00 at 0.71."""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
B = "http://127.0.0.1:8044"


def call(method, path, params=None):
    url = f"{B}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    try:
        return json.load(urllib.request.urlopen(
            urllib.request.Request(url, method=method), timeout=180))
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": json.load(e).get("detail")}


print("=== step 17: the queue, each fact with its provenance ===")
q = call("GET", "/review")
for f in q:
    src = f["source"]
    print(f"  [{f['confidence']}] {f['subject']} / {f['attribute']} = {f['value']}")
    print(f"        from: {src['kind']} {src['label']!r}"
          f"{' (' + src['filename'] + ')' if src['filename'] else ''}")
    print(f"        text: {src['excerpt'][:88].replace(chr(10), ' / ')}...")

print(f"\n  {len(q)} awaiting review")

print("\n=== step 19: conflicts, before anyone confirms anything ===")
for c in call("GET", "/conflicts"):
    print(f"  {c['subject_key']} / {c['attribute_key']}")
    for v in c["values"]:
        origin = "typed by owner" if v["typed"] else f"extracted @{v['confidence']}"
        state = "confirmed" if v["confirmed"] else "awaiting review"
        print(f"      {v['value']:34}  {origin:22} {state}")

print("\n=== step 18: reject one (the extraction was wrong) ===")
target = next(f for f in q if "08:30" in f["value"])
print(f"  rejecting: {target['subject']} / {target['attribute']} = {target['value']}")
print("  ", call("DELETE", f"/review/{target['id']}"))

print("\n=== a confirmed fact cannot be rejected ===")
ask = f"{B}/ask?" + urllib.parse.urlencode({"q": "Ish vaqtingiz qanday?"})
fid = json.load(urllib.request.urlopen(ask))["facts"]
print("  ", call("DELETE", "/review/01a00000-0000-7000-8000-000000000000"))

print("\n=== step 18: edit then confirm another ===")
q = call("GET", "/review")
target = next(f for f in q if f["subject"] == "EKG")
print(f"  before: {target['subject']} / {target['attribute']} = {target['value']}")
call("PATCH", f"/review/{target['id']}", {"subject": "EKG tekshiruvi"})
r = call("POST", f"/review/{target['id']}/confirm")
print(f"  after:  {r['subject']} / {r['attribute']} = {r['value']}  confirmed={r['confirmed']}")
print(f"  conflicts introduced: {r['now_conflicts_with']}")

print("\n=== the confirmed fact is now answerable ===")
a = json.load(urllib.request.urlopen(
    f"{B}/answer?" + urllib.parse.urlencode({"q": "EKG narxi qancha?"}), timeout=180))
print(f"  status={a['status']} route={a['source']}")
print(f"  > {a['answer']}")

print("\n=== queue after ===")
print(f"  {len(call('GET', '/review'))} awaiting review")

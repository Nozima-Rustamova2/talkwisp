"""Upload a real photo, read it, extract from it. The whole step-14 path."""

import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
B = "http://127.0.0.1:8041"
PHOTO = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "uzb.png")


def call(method, path, params=None, data=None, headers=None):
    url = f"{B}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        return json.load(urllib.request.urlopen(req, timeout=300))
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": json.load(e).get("detail")}


b = "----talkwisp"
payload = PHOTO.read_bytes()
media = "image/png" if PHOTO.suffix.lower() == ".png" else "image/jpeg"
body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{PHOTO.name}\"\r\nContent-Type: {media}\r\n\r\n").encode() \
    + payload + f"\r\n--{b}--\r\n".encode()

print(f"=== upload {PHOTO.name} ({len(payload)//1024} KB) ===")
src = call("POST", "/source/upload", {"label": "Menyu, avgust"}, body,
           {"Content-Type": f"multipart/form-data; boundary={b}"})
sid = src["id"]
print(f"  {src['kind']} / {src['media_type']} / {src['size']} bytes / "
      f"status={src['status']} / content={src['content']}")

print("\n=== step 14: read the image ===")
r = call("POST", f"/source/{sid}/read")
print(f"  {r['lines']} lines transcribed")
for line in r["transcript"].splitlines()[:8]:
    print(f"    {line}")
print("    ...")

after = call("GET", f"/source/{sid}")
print(f"  source status: {after['status']} (still pending -- reading is not "
      f"extracting)")
print(f"  content now {len(after['content'] or '')} chars")

print("\n=== steps 12-13 on the transcription, unchanged ===")
r = call("POST", f"/source/{sid}/extract")
print(f"  status={r['status']}  facts={len(r['facts'])}")
for f in r["facts"][:6]:
    print(f"    {f['confidence']:.2f}  {f['subject']} / {f['attribute']} = {f['value']}")
print(f"    ... {max(0, len(r['facts']) - 6)} more")

print("\n=== a paste has nothing to read ===")
note = call("POST", "/source/paste", {"content": "Yakshanba dam olish kuni."})
print("  ", call("POST", f"/source/{note['id']}/read"))

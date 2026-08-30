"""Exercise the source endpoints, including the ones that must be refused."""

import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
B = "http://127.0.0.1:8031"


def call(method, path, params=None, data=None, headers=None):
    url = f"{B}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": json.load(e).get("detail")}


def multipart(filename, content_type, payload):
    b = "----talkwisp"
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n"
            ).encode() + payload + f"\r\n--{b}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={b}"}


print("=== step 9: paste ===")
r = call("POST", "/source/paste", {
    "label": "Yangi xizmatlar",
    "content": "Shifo Med endi laboratoriya xizmatlarini ham taklif qiladi. "
               "Tahlillar ertalab soat 08:00 dan 11:00 gacha qabul qilinadi.",
})
print(f"  {r.get('kind')} / {r.get('label')} / status={r.get('status')} "
      f"/ {len(r.get('content') or '')} chars")
paste_id = r.get("id")

print("\n=== empty paste must be refused ===")
print(" ", call("POST", "/source/paste", {"content": "   "}))

print("\n=== step 10: upload a JPEG ===")
# A minimal but real JPEG (1x1 pixel).
jpeg = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffc00011080001000103012200021101"
    "031101ffc4001f0000010501010101010100000000000000000102030405060708090a"
    "0bffc400b5100002010303020403050504040000017d01020300041105122131410613"
    "516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728"
    "292a3435363738393a434445464748494a535455565758595a636465666768696a7374"
    "75767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4"
    "b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1"
    "f2f3f4f5f6f7f8f9faffda0008010100003f00fb7effd9")
body, headers = multipart("narxlar.jpg", "image/jpeg", jpeg)
r = call("POST", "/source/upload", {"label": "Narxlar, avgust"}, body, headers)
print(f"  {r.get('kind')} / {r.get('filename')} / {r.get('media_type')} "
      f"/ {r.get('size')} bytes / status={r.get('status')}")
file_id = r.get("id")

print("\n=== a .exe must be refused ===")
body, headers = multipart("virus.exe", "application/x-msdownload", b"MZ\x90\x00")
print(" ", call("POST", "/source/upload", None, body, headers))

print("\n=== an oversized file must be refused ===")
body, headers = multipart("huge.png", "image/png", b"\x89PNG" + b"\0" * (21 * 1024 * 1024))
print(" ", call("POST", "/source/upload", None, body, headers))

print("\n=== listing (bytes never returned) ===")
for s in call("GET", "/source", {"status": "pending"}):
    print(f"  {s['kind']:6} {str(s['label'])[:24]:26} size={s['size']} "
          f"status={s['status']}")

print("\n=== one source by id ===")
r = call("GET", f"/source/{file_id}")
print(f"  keys: {sorted(r)}")
print(f"  bytes present in response: {'bytes' in r}")

print("\n=== unknown id ===")
print(" ", call("GET", "/source/00000000-0000-0000-0000-000000000000"))

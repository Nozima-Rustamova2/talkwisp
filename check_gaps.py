"""The gap list, and the claim that one action closes it everywhere.

    uv run python check_gaps.py

THE SECTION THAT MATTERS IS 3. The Dashboard's "what it doesn't know yet" column
and the Gaps screen are two views of one list. The claim is that answering a gap
from ONE of them removes it from BOTH -- so section 3 closes a gap through
/gaps/answer and then reads it back through /dashboard and /gaps separately. Two
views computed separately is the drift this codebase keeps producing; the only
way to prove there is one list is to act once and look twice.

Section 2 is the tenancy half, with its negative control: gaps.jsonl is one file
for every business, and a file has no RLS. So it plants another business's
refusal, asserts it is absent -- and then removes the filter and asserts the same
line appears, so "absent" cannot be the result of a reader that returns nothing.

Nothing here touches the real gaps.jsonl.
"""

import json
import os
import pathlib
import sys
import tempfile

import psycopg
from dotenv import load_dotenv
from fastapi.testclient import TestClient

import app.main as api
from app import auth, gaps
from app.db import connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
ADDR_A = "check-gaps-a@example.invalid"
ADDR_B = "check-gaps-b@example.invalid"


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def refusal(biz, key, question, at, score=0.6):
    return {"business_id": biz, "at": at, "question": question,
            "question_key": key, "best_similarity": score}


def write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in rows), encoding="utf-8")


def keys(listing):
    return [g["question_key"] for g in listing["gaps"]]


def main():
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set.")
    admin = psycopg.connect(admin_url, autocommit=True)
    for addr in (ADDR_A, ADDR_B):
        admin.execute("delete from fact where business_id in (select id from"
                      " business where owner_email = %s)", (addr,))
        admin.execute("delete from business where owner_email = %s", (addr,))
    biz_a = str(admin.execute(
        "insert into business (name, owner_email, approved) values"
        " ('check_gaps A', %s, true) returning id", (ADDR_A,)).fetchone()[0])
    biz_b = str(admin.execute(
        "insert into business (name, owner_email, approved) values"
        " ('check_gaps B', %s, true) returning id", (ADDR_B,)).fetchone()[0])

    scratch = pathlib.Path(tempfile.mkdtemp()) / "gaps.jsonl"
    real = gaps.GAP_LOG
    gaps.GAP_LOG = scratch
    pool.open()
    try:
        run(admin, biz_a, biz_b, scratch)
    finally:
        gaps.GAP_LOG = real
        for biz in (biz_a, biz_b):
            admin.execute("delete from fact where business_id = %s", (biz,))
            admin.execute("delete from business where id = %s", (biz,))
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def run(admin, biz_a, biz_b, scratch):
    write(scratch, [
        refusal(biz_a, "online mi", "online mi", "2026-09-17T09:00:00+00:00"),
        refusal(biz_a, "online mi", "Online mi?", "2026-09-17T10:00:00+00:00"),
        refusal(biz_a, "online mi", "online mi", "2026-09-17T11:00:00+00:00"),
        refusal(biz_a, "bolib tolasa boladimi", "bo'lib to'lasa bo'ladimi",
                "2026-09-17T12:00:00+00:00", score=0.71),
        refusal(biz_a, "sertifikat beriladimi", "Sertifikat beriladimi?",
                "2026-09-17T12:30:00+00:00"),
        # ANOTHER BUSINESS'S REFUSAL. Section 2 is about this line.
        refusal(biz_b, "kurs narxi", "Kurs narxi?", "2026-09-17T09:30:00+00:00"),
        # Written before gaps carried business_id. Unattributable.
        {"at": "2026-09-01T00:00:00+00:00", "question": "eski",
         "question_key": "eski"},
    ])

    print("\n1. Grouped by question, most-asked first")
    with connection(biz_a):
        listing = gaps.open_gaps()
    check("three open questions, not five refusals", listing["open"], 3)
    check("the most-asked is first", keys(listing)[0], "online mi")
    top = listing["gaps"][0]
    check("with how many people asked", top["asked"], 3)
    check("and the most recent wording", top["question"], "online mi")
    check("the unattributed line is counted, not shown", listing["unattributed"], 1)
    with connection(biz_a):
        check("the dashboard's limit is a window onto the same list",
              keys(gaps.open_gaps(limit=2)), keys(listing)[:2])

    print("\n2. CONTROL: one business cannot see another's gaps")
    check("B's refusal is absent from A's list", "kurs narxi" in keys(listing),
          False)
    with connection(biz_b):
        check("and present in B's own", keys(gaps.open_gaps()), ["kurs narxi"])
    original = gaps._lines

    def unfiltered():
        rows = [json.loads(l) for l in scratch.read_text(encoding="utf-8")
                .splitlines() if l.strip()]
        return [r for r in rows if r.get("business_id")], 0

    gaps._lines = unfiltered
    try:
        with connection(biz_a):
            leaked = keys(gaps.open_gaps())
    finally:
        gaps._lines = original
    check("with the filter removed, B's refusal LEAKS into A's list",
          "kurs narxi" in leaked, True)

    print("\n3. ONE ACTION CLOSES THE GAP IN BOTH VIEWS")
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, auth.create_session(biz_a))
    with connection(biz_a) as conn:
        fact_id = str(conn.execute(
            "insert into fact (subject, subject_key, attribute, attribute_key,"
            " value, value_key, confirmed) values ('October Intensive',"
            " 'october intensive', 'format', 'format', 'onlayn', 'onlayn', true)"
            " returning id").fetchone()[0])

    before_dash = [g["question_key"]
                   for g in client.get("/dashboard").json()["unknown"]["gaps"]]
    check("the gap is on the dashboard before", "online mi" in before_dash, True)

    closed = client.post("/gaps/answer",
                         data={"question_key": "online mi", "fact_id": fact_id})
    check("answering it through the endpoint succeeds", closed.status_code, 200)

    # LOOK TWICE, THROUGH BOTH VIEWS. This is the convergence claim.
    after_dash = [g["question_key"]
                  for g in client.get("/dashboard").json()["unknown"]["gaps"]]
    after_screen = keys(client.get("/gaps").json())
    check("gone from the DASHBOARD column", "online mi" in after_dash, False)
    check("gone from the GAPS SCREEN", "online mi" in after_screen, False)
    check("and both views still agree on what remains",
          after_dash, after_screen[:len(after_dash)])

    print("\n4. Dismissing, and what each close records")
    client.post("/gaps/dismiss", data={"question_key": "sertifikat beriladimi"})
    check("'not for us' closes it too",
          "sertifikat beriladimi" in keys(client.get("/gaps").json()), False)
    closes = [json.loads(l) for l in scratch.read_text(encoding="utf-8")
              .splitlines() if l.strip() and "resolution" in l]
    # Recorded, so how often owners dismiss gaps that were in fact answered
    # elsewhere can be measured rather than guessed.
    check("each close records how it was closed",
          sorted((c["question_key"], c["resolution"]) for c in closes),
          [("online mi", "answered"), ("sertifikat beriladimi", "dismissed")])
    check("and the answered one names its fact",
          [c["fact_id"] for c in closes if c["resolution"] == "answered"],
          [fact_id])

    print("\n5. A question asked AGAIN after it was answered reopens")
    # Whatever was written did not answer it. Hiding the new refusal because
    # an old one was closed would be the list lying about the agent.
    with scratch.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(refusal(biz_a, "online mi", "online mi?",
                                        "2026-09-18T09:00:00+00:00")) + "\n")
    reopened = [g for g in client.get("/gaps").json()["gaps"]
                if g["question_key"] == "online mi"]
    check("it is open again", len(reopened), 1)
    check("counted from zero, not from before the answer",
          reopened[0]["asked"] if reopened else None, 1)

    print("\n6. A gap cannot be closed with another business's fact")
    with connection(biz_b) as conn:
        theirs = str(conn.execute(
            "insert into fact (subject, subject_key, attribute, attribute_key,"
            " value, value_key, confirmed) values ('x','x','y','y','z','z',true)"
            " returning id").fetchone()[0])
    refused = client.post("/gaps/answer", data={"question_key": "bolib tolasa boladimi",
                                                 "fact_id": theirs})
    check("B's fact id is refused", refused.status_code, 400)
    check("and the gap stays open",
          "bolib tolasa boladimi" in keys(client.get("/gaps").json()), True)


if __name__ == "__main__":
    main()

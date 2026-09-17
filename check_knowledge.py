"""The knowledge base: what an owner can see and change, and what that costs.

    uv run python check_knowledge.py

Runs against the real database through the real app. It SPENDS a few embeddings,
because the thing most worth proving here is that editing a confirmed fact
re-embeds it -- and a stubbed embedder would prove nothing about that.

THE SECTION THAT MATTERS IS 3. review.edit() rewrites the keys and leaves the
embedding alone, which is correct for a proposal and silently wrong for a
confirmed fact: the row shows the new text, matches exactly on the new keys, and
vector-matches on the OLD text forever. Section 3 restores that behaviour on
purpose and watches the vector fail to move, then shows the new path moving it.
Without the negative half, "the embedding changed" is a fact about one update
statement rather than about the bug it fixes.
"""

import datetime
import os
import sys

import psycopg
from dotenv import load_dotenv
from fastapi.testclient import TestClient

import app.main as api
from app import auth, knowledge, review
from app.db import connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0
NAME = "check_knowledge scratch business"
ADDR = "check-knowledge@example.invalid"


def check(label: str, got, want) -> None:
    global passed, failed
    ok = got == want
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")
        failed += 1
    else:
        passed += 1


def clear(admin, addr: str) -> None:
    """In FK order. A run that stranded rows would make the next one unable to
    start -- a test that poisons the database on failure is a test people stop
    running."""
    for table in ("escalation", "alias", "chunk", "fact", "source"):
        admin.execute(
            f"delete from {table} where business_id in"
            " (select id from business where owner_email = %s)", (addr,))
    admin.execute("delete from business where owner_email = %s", (addr,))


def main() -> None:
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set.")
    admin = psycopg.connect(admin_url, autocommit=True)
    clear(admin, ADDR)
    biz = str(admin.execute(
        "insert into business (name, owner_email, approved)"
        " values (%s, %s, true) returning id", (NAME, ADDR)).fetchone()[0])
    pool.open()
    try:
        run(admin, biz)
    finally:
        clear(admin, ADDR)
        admin.close()
        pool.close()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def seed(conn, subject, attribute, value, confirmed=True):
    from app.embeddings import DIMENSIONS, MODEL, embed_document
    from app.normalize import normalize
    vec = str(embed_document(f"{subject} / {attribute} / {value}"))
    return str(conn.execute(
        "insert into fact (subject, subject_key, attribute, attribute_key,"
        " value, value_key, confirmed, embedding, embedding_model)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (subject, normalize(subject), attribute, normalize(attribute), value,
         normalize(value), confirmed, vec, f"{MODEL}@{DIMENSIONS}")).fetchone()[0])


def vector_of(admin, fact_id):
    return admin.execute("select embedding::text from fact where id = %s",
                         (fact_id,)).fetchone()[0]


def run(admin, biz: str) -> None:
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, auth.create_session(biz))

    with connection(biz) as conn:
        priced = seed(conn, "Kardiolog", "qabul narxi", "250 000 so'm")
        seed(conn, "Kardiolog", "ish vaqti", "Dush-Juma 09:00-15:00")
        proposal = seed(conn, "Nevrolog", "qabul narxi", "200 000 so'm",
                        confirmed=False)

    print("\n1. Confirmed facts are visible at all, which /review never showed")
    groups = client.get("/knowledge").json()
    check("GET /knowledge answers", client.get("/knowledge").status_code, 200)
    subjects = sorted(g["subject"] for g in groups)
    check("grouped by subject", subjects, ["Kardiolog", "Nevrolog"])
    kardiolog = next(g for g in groups if g["subject"] == "Kardiolog")
    check("with every fact under it", len(kardiolog["facts"]), 2)
    check("and /review still shows only the proposal",
          [f["id"] for f in client.get("/review").json()], [proposal])

    print("\n2. Provenance speaks Review's vocabulary, and invents nothing")
    check("a typed fact says so", kardiolog["facts"][0]["origin"], "typed")
    check("and carries no source", kardiolog["facts"][0]["source"], None)
    raw = client.get("/knowledge").text
    # There is no sheet-and-row anchoring in the schema and no ingestion path
    # that could produce it. The design docs record a mockup that wanted one.
    check("no invented sheet/row anchoring", "sheet" in raw.lower(), False)

    print("\n3. CONTROL: editing a confirmed fact must move the EMBEDDING too")
    # The bug: review.edit() rewrites the keys and leaves the vector. The fact
    # then displays the new price, matches exactly on the new keys, and
    # vector-matches on the old text forever -- so the agent finds it when a
    # customer asks about the OLD price and answers with the new one.
    before = vector_of(admin, priced)
    with connection(biz) as conn:
        moved = review.edit(conn, priced, value="275 000 so'm")
    assert moved, "the control did not actually edit anything"
    after_old_path = vector_of(admin, priced)
    check("review.edit() leaves the vector STALE (this is the bug)",
          after_old_path == before, True)
    check("even though the text changed",
          admin.execute("select value from fact where id = %s",
                        (priced,)).fetchone()[0], "275 000 so'm")

    # Now the new path, on the same fact.
    response = client.patch(f"/fact/{priced}", params={"value": "300 000 so'm"})
    check("PATCH /fact answers", response.status_code, 200)
    check("and says it re-embedded", response.json()["reembedded"], True)
    after_new_path = vector_of(admin, priced)
    check("the vector MOVED", after_new_path != before, True)
    check("the keys moved with the text",
          admin.execute("select value_key from fact where id = %s",
                        (priced,)).fetchone()[0], "300 000 som")

    print("\n3b. A contradiction between CONFIRMED facts is surfaced")
    # Review catches conflicts among proposals, which is the right moment for a
    # bad extraction. The stale one lives HERE: two confirmed facts answering
    # the same subject and attribute differently, both retrievable, one of them
    # months out of date. Until this screen existed there was nowhere to see it.
    with connection(biz) as conn:
        rival = seed(conn, "Kardiolog", "ish vaqti", "Dush-Shanba 08:00-20:00")
    groups = client.get("/knowledge").json()
    kard = next(g for g in groups if g["subject"] == "Kardiolog")
    disputed = [f for f in kard["facts"] if f["disputed"]]
    check("both sides of the disagreement are flagged", len(disputed), 2)
    check("and they are the two ish vaqti facts",
          sorted({f["attribute"] for f in disputed}), ["ish vaqti"])
    check("the subject carries a count", kard["disputes"], 2)
    check("a subject with a dispute sorts first",
          groups[0]["subject"], "Kardiolog")
    check("the untouched fact is NOT flagged",
          [f["disputed"] for f in kard["facts"]
           if f["attribute"] == "qabul narxi"], [False])
    with connection(biz) as conn:
        conn.execute("delete from fact where id = %s", (rival,))

    print("\n4. An unconfirmed fact is not embedded on edit -- confirm() does that")
    # Spending here would pay twice: review.confirm() embeds on approval.
    r = client.patch(f"/fact/{proposal}", params={"value": "210 000 so'm"})
    check("edited", r.status_code, 200)
    check("and NOT re-embedded", r.json()["reembedded"], False)

    print("\n5. Delete is real, and works where reject() refuses")
    with connection(biz) as conn:
        # reject() is for proposals and says so in its own docstring.
        check("review.reject() still refuses a confirmed fact",
              review.reject(conn, priced), False)
    check("DELETE /fact removes it",
          client.delete(f"/fact/{priced}").status_code, 200)
    check("and it is gone",
          admin.execute("select count(*) from fact where id = %s",
                        (priced,)).fetchone()[0], 0)
    check("deleting it again is a 404",
          client.delete(f"/fact/{priced}").status_code, 404)

    print("\n5b. EXPIRY: not retrieved, not deleted, and warned about once")
    with connection(biz) as conn:
        promo = seed(conn, "Kurs", "chegirma", "20% chegirma")

    def retrievable(fid):
        with connection(biz) as conn:
            return conn.execute(
                "select count(*) from retrievable_fact where id = %s",
                (fid,)).fetchone()[0]

    check("a fact with no expiry is retrievable", retrievable(promo), 1)

    # The rule, both halves.
    client.put(f"/fact/{promo}/expiry",
               params={"expires_at": "2020-01-01T00:00:00+00:00"})
    check("an expired fact is NOT retrievable", retrievable(promo), 0)
    check("but the row is still there -- the owner can see what happened",
          admin.execute("select count(*) from fact where id = %s",
                        (promo,)).fetchone()[0], 1)
    shown = [f for g in client.get("/knowledge").json() for f in g["facts"]
             if f["id"] == promo]
    check("and the knowledge base still lists it", len(shown), 1)
    check("marked expired", shown[0]["expired"], True)

    # Reactivating is clearing the date, not retyping the fact.
    client.put(f"/fact/{promo}/expiry")
    check("clearing the expiry puts it back in service", retrievable(promo), 1)

    print("\n5c. The owner is warned ONCE, and extending re-arms the warning")
    with connection(biz) as conn:
        conn.execute("update fact set expires_at = now() + interval '20 hours',"
                     " expiry_notified_at = null where id = %s", (promo,))
        first = knowledge.expiring_soon(conn, 48)
        second = knowledge.expiring_soon(conn, 48)
    check("the first sweep finds it", [f["id"] for f in first], [promo])
    # Without this, a five-minute sweep would tell the owner 576 times a day.
    check("the second finds nothing -- it was claimed as it was read",
          second, [])

    with connection(biz) as conn:
        knowledge.set_expiry(
            conn, promo,
            (datetime.datetime.now(datetime.UTC)
             + datetime.timedelta(hours=20)).isoformat())
        third = knowledge.expiring_soon(conn, 48)
    # Extending a promotion whose warning already went out must arm it again,
    # or the second expiry passes in silence.
    check("extending re-arms the warning", [f["id"] for f in third], [promo])

    print("\n5d. An expired fact does not count as disagreeing with a live one")
    with connection(biz) as conn:
        rival2 = seed(conn, "Kurs", "chegirma", "10% chegirma")
        conn.execute("update fact set expires_at = null where id = %s", (promo,))
    kurs = next(g for g in client.get("/knowledge").json()
                if g["subject"] == "Kurs")
    check("two live values disagree", kurs["disputes"], 2)
    with connection(biz) as conn:
        conn.execute("update fact set expires_at = now() - interval '1 day'"
                     " where id = %s", (rival2,))
    kurs = next(g for g in client.get("/knowledge").json()
                if g["subject"] == "Kurs")
    # A price that correctly expired is the ordinary way a price changes, not a
    # contradiction to resolve -- flagging it would mean every expired promotion
    # raised a flag forever.
    check("once one expires, they do not", kurs["disputes"], 0)

    print("\n5e. A pair can be marked as deliberately multi-valued")
    with connection(biz) as conn:
        conn.execute("update fact set expires_at = null where id = %s", (rival2,))
    check("disagreeing again",
          next(g for g in client.get("/knowledge").json()
               if g["subject"] == "Kurs")["disputes"], 2)
    marked = client.put("/knowledge/expected-multiple",
                        params={"subject_key": "kurs",
                                "attribute_key": "chegirma"})
    check("marking applies to the whole pair", marked.json()["facts"], 2)
    check("and the flag clears",
          next(g for g in client.get("/knowledge").json()
               if g["subject"] == "Kurs")["disputes"], 0)
    with connection(biz) as conn:
        conn.execute("delete from fact where id in (%s, %s)", (promo, rival2))

    print("\n6. Aliases, which nothing in the product could create before")
    check("none to start", client.get("/alias").json(), [])
    made = client.post("/alias", params={"subject_key": "kardiolog",
                                         "alias": "кардиолог"})
    check("created", made.status_code, 200)
    listed = client.get("/alias").json()
    check("and listed", [a["alias"] for a in listed], ["кардиолог"])
    check("pointing at a real subject", listed[0]["subject"], "Kardiolog")
    check("confirmed on write, or retrieval would ignore it",
          listed[0]["confirmed"], True)

    # An alias for a subject with no facts could never match anything, and
    # creating one silently would look like it worked.
    orphan = client.post("/alias", params={"subject_key": "nobody",
                                           "alias": "hech kim"})
    check("an alias for a subject that does not exist is refused",
          orphan.status_code, 400)
    check("saying why", "never match" in orphan.json()["detail"], True)

    again = client.post("/alias", params={"subject_key": "kardiolog",
                                          "alias": "кардиолог"})
    check("re-adding the same alias is not an error", again.status_code, 200)
    check("and says it already existed", again.json()["already"], True)
    check("with no duplicate row", len(client.get("/alias").json()), 1)

    check("DELETE /alias removes it",
          client.delete(f"/alias/{listed[0]['id']}").status_code, 200)
    check("and it is gone", client.get("/alias").json(), [])

    print("\n7. CONTROL: saving a typed fact saves THE READING THE OWNER CHECKED")
    # The model is stubbed to read the same line DIFFERENTLY the second time.
    # Without that, "the saved fact matches the preview" is equally true of the
    # fix and of a path that re-reads the line and happens to agree.
    import json as _json
    from app import typed
    from app.embeddings import DIMENSIONS
    from app.payment import PAYMENT_SUBJECT
    readings = [
        {"subject": "Oktyabr intensivi", "attribute": "format", "value": "onlayn"},
        {"subject": "Oktyabr intensivi", "attribute": "format", "value": "oflayn"},
    ]
    calls = []

    def disagreeing(system, prompt):
        calls.append(prompt)
        return _json.dumps(readings[min(len(calls), 2) - 1])

    real_complete, real_embed = typed.complete, typed.embed_document
    typed.complete = disagreeing
    # Not what is being proven, and a vector per save would be spend for nothing.
    typed.embed_document = lambda text: [0.001] * DIMENSIONS
    line = "Oktyabr intensivi onlayn"
    try:
        preview = client.post("/fact", params={"line": line}).json()
        check("the preview is the first reading", preview["parsed"], readings[0])
        check("and previewing writes nothing", preview["written"], False)

        # THE REMOVED PATH, planted: parse the line again and store that. It is
        # the old confirm=true body, verbatim in effect.
        with connection(biz) as conn:
            again = typed.parse(conn, line)
            planted = typed.store(conn, again["parsed"])
            planted_value = conn.execute("select value from fact where id = %s",
                                         (planted,)).fetchone()[0]
            conn.execute("delete from fact where id = %s", (planted,))
        check("re-reading saves something the owner never saw (the bug)",
              planted_value, "oflayn")

        calls.clear()
        saved = client.post("/fact/confirm", data=preview["parsed"])
        check("POST /fact/confirm saves", saved.status_code, 200)
        row = admin.execute("select subject, attribute, value, confirmed from fact"
                            " where id = %s", (saved.json()["id"],)).fetchone()
        check("THE SAVED FACT IS THE PREVIEW, though the model now reads otherwise",
              row, ("Oktyabr intensivi", "format", "onlayn", True))
        check("and saving made no model call at all", len(calls), 0)

        count = admin.execute("select count(*) from fact where business_id = %s",
                              (biz,)).fetchone()[0]
        old = client.post("/fact", params={"line": line, "confirm": "true"})
        check("the old line-plus-confirm form writes nothing now",
              (old.json()["written"],
               admin.execute("select count(*) from fact where business_id = %s",
                             (biz,)).fetchone()[0]),
              (False, count))

        empty = client.post("/fact/confirm", data={"subject": "Kurs", "attribute": " ",
                                                   "value": "x"})
        check("a reading with a blank part is refused", empty.status_code, 400)
        reserved = client.post("/fact/confirm", data={
            "subject": PAYMENT_SUBJECT, "attribute": "karta", "value": "8600"})
        check("the subject guard still applies, as a 400 not a crash",
              reserved.status_code, 400)
        admin.execute("delete from fact where id = %s", (saved.json()["id"],))
    finally:
        typed.complete, typed.embed_document = real_complete, real_embed


if __name__ == "__main__":
    main()

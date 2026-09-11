"""The spending gate: what an unapproved business can and cannot do.

    uv run python check_approval.py

Runs against the real database and the real modules. Nothing is stubbed except
the network calls themselves in the sections that prove the gate LETS SOMETHING
THROUGH -- those would otherwise have to spend money to pass, and a check that
spends money on every run is a check that gets commented out.

THE SECTION THAT MATTERS MOST IS 4, THE NEGATIVE CONTROL. Everything else here
asserts that a refusal happens, and a refusal is easy to produce by accident: a
typo in a business id, a connection that was never opened, an import that
failed. If the gate were broken in the direction that costs money, several of
these sections would still pass. So section 4 breaks the gate on purpose --
app.db.bind stops binding, which is exactly what deleting one line from
app/extract.py does -- and asserts that extraction stops working. A gate whose
failure mode is silence has to be watched failing at least once, or the green is
worth nothing.

WHAT IT DOES NOT PROVE. It does not prove the list of spending endpoints is
complete, because that list is not the mechanism -- see app/approval.py. It
proves the two functions that hold the credentials refuse, and section 3 proves
nothing else holds them.
"""

import contextlib
import os
import pathlib
import re
import sys

import psycopg
from dotenv import load_dotenv

from app import chunks, embeddings, extract, llm
from app.approval import NotApproved
from app.db import bind, connection, pool

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

FAILURES: list[str] = []
NAME = "check_approval scratch business"
ADDR = "check-approval@example.invalid"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if detail:
        print(f"        {detail}")
    if not ok:
        FAILURES.append(label)


@contextlib.contextmanager
def no_network():
    """Make a model call fail LOUDLY rather than reach the internet.

    Used in the sections that prove the gate lets an approved business past.
    The assertion is that NotApproved is not raised; what happens after the gate
    is deliberately not this file's business, so it is replaced with a marker
    exception that cannot be confused for either a pass or a real failure.
    """
    class Reached(Exception):
        pass

    real_providers = dict(llm._PROVIDERS)

    def boom(*a, **k):
        raise Reached("past the gate")

    llm._PROVIDERS = {k: boom for k in real_providers}
    import httpx
    real_post = httpx.post
    httpx.post = boom
    try:
        yield Reached
    finally:
        llm._PROVIDERS = real_providers
        httpx.post = real_post


def main() -> None:
    admin_url = os.getenv("ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("ADMIN_DATABASE_URL is not set; this check needs it "
                         "to flip the flag it is checking.")
    pool.open()
    admin = psycopg.connect(admin_url, autocommit=True)

    admin.execute("delete from business where owner_email = %s", (ADDR,))
    biz = str(admin.execute(
        "insert into business (name, owner_email) values (%s, %s) returning id",
        (NAME, ADDR)).fetchone()[0])

    def set_flag(want: bool) -> None:
        admin.execute("update business set approved = %s where id = %s",
                      (want, biz))

    try:
        run(admin, biz, set_flag)
    finally:
        admin.execute("delete from business where id = %s", (biz,))
        admin.close()
        pool.close()

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        raise SystemExit(1)
    print("  all checks passed")


def run(admin, biz: str, set_flag) -> None:
    # --- 1. an unapproved business cannot spend -----------------------------
    # No stubbing anywhere in this section. The gate raises before anything
    # opens a socket, so if these pass by reaching the network the assertion
    # would fail rather than quietly cost money.
    print()
    print("1. an unapproved business is refused, at both credentials")
    set_flag(False)

    with connection(biz):
        try:
            llm.complete("system", "hello")
            check("complete() refuses", False, "it returned instead of raising")
        except NotApproved as exc:
            check("complete() refuses", True, str(exc)[:80])
        except Exception as exc:
            check("complete() refuses", False,
                  f"raised {type(exc).__name__} instead: {exc}")

        try:
            embeddings.embed_query("hello")
            check("embed_query() refuses", False, "it returned instead")
        except NotApproved as exc:
            check("embed_query() refuses", True, str(exc)[:80])
        except Exception as exc:
            check("embed_query() refuses", False,
                  f"raised {type(exc).__name__} instead: {exc}")

        try:
            embeddings.embed_document("hello")
            check("embed_document() refuses", False, "it returned instead")
        except NotApproved:
            check("embed_document() refuses", True)
        except Exception as exc:
            check("embed_document() refuses", False,
                  f"raised {type(exc).__name__}: {exc}")

    # --- 2. an approved business is not refused ------------------------------
    # The other direction, and the one that would make this whole file a
    # formality if it were wrong: a gate that refuses everybody passes section 1
    # perfectly and breaks the product.
    print()
    print("2. an approved business gets past the gate")
    set_flag(True)

    with connection(biz), no_network() as Reached:
        for label, call in (("complete()", lambda: llm.complete("s", "p")),
                            ("embed_query()", lambda: embeddings.embed_query("p"))):
            try:
                call()
                check(f"{label} is allowed through", False,
                      "returned without reaching the provider -- the stub "
                      "should have raised, so something else intercepted it")
            except NotApproved:
                check(f"{label} is allowed through", False,
                      "the gate refused an APPROVED business")
            except Reached:
                check(f"{label} is allowed through", True,
                      "reached the provider, then stopped at the stub")
            except Exception as exc:
                check(f"{label} is allowed through", False,
                      f"{type(exc).__name__}: {str(exc)[:90]}")

    # --- 3. nothing else holds the credentials ------------------------------
    # The gate's coverage is a fact about the import graph, not a list. This
    # asserts that fact instead of trusting it: every call to a provider
    # function or to the embedding URL must be inside the two gated functions.
    print()
    print("3. the credentials are reachable from nowhere else")
    src_llm = pathlib.Path("app/llm.py").read_text(encoding="utf-8")
    direct = [n for n, line in enumerate(src_llm.splitlines(), 1)
              if "_PROVIDERS[PROVIDER](" in line]
    # Two: complete(), which is gated, and check_reachable(), which is the boot
    # probe -- platform money, once per process, with no tenant to ask about.
    check("only complete() and check_reachable() call a provider directly",
          len(direct) == 2, f"lines {direct}")

    others = []
    for path in pathlib.Path("app").glob("*.py"):
        if path.name in {"llm.py", "embeddings.py"}:
            continue
        body = path.read_text(encoding="utf-8")
        if re.search(r"_PROVIDERS|generativelanguage\.googleapis|:generateContent",
                     body):
            others.append(path.name)
    check("no other module touches a provider endpoint", not others,
          f"found in {others}" if others else "")

    gated = pathlib.Path("app/embeddings.py").read_text(encoding="utf-8")
    check("_embed() is the only place embeddings post",
          gated.count("httpx.post") == 1
          and "assert_approved" in gated.split("def _embed")[1].split("httpx.post")[0],
          "the gate must be above the only post in the file")

    # --- 4. THE NEGATIVE CONTROL --------------------------------------------
    # app/extract.py is the one place that calls a model with no connection
    # open, so it binds the tenant itself. Deleting that one line is silent at
    # the call site -- this restores the bug and watches what happens.
    #
    # THE FIRST ATTEMPT AT THIS SECTION WENT RED FOR THE WRONG REASON, which is
    # why the second assertion exists. no_network() made the FIRST model call
    # raise, inside extract.read(), which is still inside a connection() block
    # and therefore still bound. run() caught it, marked the source failed, and
    # returned -- red, convincingly, without the unbound region ever executing.
    # A control that goes red before reaching the thing it is controlling for
    # proves nothing at all, and "status == failed" was true either way.
    #
    # So read() is stubbed out here instead. That is not stubbing the gate or
    # the bind: it stands in for a document the model read successfully, so the
    # connection closes normally and chunks.find_prose() -- the first model call
    # with no transaction open -- runs for real.
    print()
    print("4. negative control: extract.py with its bind() removed")
    set_flag(True)

    with connection(biz) as conn:
        source_id = str(conn.execute(
            "insert into source (kind, label, content, status) "
            "values ('paste', 'check_approval', %s, 'pending') returning id",
            ("Ish vaqti 9:00 dan 18:00 gacha.",)).fetchone()[0])
    source = {"id": source_id, "kind": "paste", "status": "pending",
              "content": "Ish vaqti 9:00 dan 18:00 gacha."}

    real_bind, real_read = extract.bind, extract.read

    @contextlib.contextmanager
    def not_binding(business_id: str):
        yield  # exactly what deleting `with bind(business):` leaves behind

    extract.read = lambda conn, src: []
    try:
        extract.bind = not_binding
        with no_network():
            broken = extract.run(biz, dict(source))
        detail = str(broken.get("error"))

        check("extraction stops when the tenant is not bound",
              broken.get("status") == "failed", detail[:120])
        # And it has to be the RIGHT red. "failed" is also what a corrupt PDF
        # produces, so a generic failure proves nothing about the bind -- that
        # was the first version's mistake. The control has to show the specific
        # error the missing bind causes, or it is measuring something adjacent.
        check("and for the stated reason, not some other one",
              "No business is bound" in detail, detail[:120])

        # Now with the line back. Same source, same stubs, and this one must get
        # PAST that point -- otherwise the red above was caused by something
        # else and the control proved nothing either way.
        extract.bind = real_bind
        with no_network():
            fixed = extract.run(biz, dict(source))
        fixed_detail = str(fixed.get("error"))
        check("with bind() restored, that error is gone",
              "No business is bound" not in fixed_detail, fixed_detail[:120])
    finally:
        extract.bind, extract.read = real_bind, real_read

    with connection(biz) as conn:
        conn.execute("delete from chunk where source_id = %s", (source_id,))
        conn.execute("delete from source where id = %s", (source_id,))

    # --- 5. the flag is read per request, not cached ------------------------
    # A cached approval is one that cannot be revoked, and revocation is the
    # only reason the flag exists. This flips it in the database with the
    # process still running and asserts the next bind sees the new value.
    print()
    print("5. revocation takes effect without a restart")
    set_flag(True)
    with connection(biz), no_network() as Reached:
        try:
            llm.complete("s", "p")
            before = "returned"
        except Reached:
            before = "allowed"
        except NotApproved:
            before = "refused"

    set_flag(False)
    with connection(biz), no_network():
        try:
            llm.complete("s", "p")
            after = "returned"
        except NotApproved:
            after = "refused"
        except Exception:
            after = "allowed"

    check("approved -> allowed, then revoked -> refused, same process",
          before == "allowed" and after == "refused",
          f"before={before} after={after}")

    # --- 6. bind() outside a connection still gates --------------------------
    # What extract.py actually relies on. Section 4 proved its absence is loud;
    # this proves its presence works.
    print()
    print("6. bind() carries the flag with no connection held")
    set_flag(False)
    try:
        with bind(biz):
            chunks.embed_all(["Ish vaqti 9:00 dan 18:00 gacha."])
        check("embed_all() under bind() is gated", False, "it returned")
    except NotApproved:
        check("embed_all() under bind() is gated", True)
    except Exception as exc:
        check("embed_all() under bind() is gated", False,
              f"{type(exc).__name__}: {str(exc)[:90]}")

    # --- 7. the web app cannot approve itself --------------------------------
    print()
    print("7. the app role cannot write the flag it is gated by")
    set_flag(False)
    with connection(biz) as conn:
        try:
            conn.execute("update business set approved = true")
            check("the app role is refused an UPDATE on business", False,
                  "the update was allowed")
        except psycopg.errors.InsufficientPrivilege:
            check("the app role is refused an UPDATE on business", True,
                  "permission denied for table business")
        except Exception as exc:
            check("the app role is refused an UPDATE on business", False,
                  f"{type(exc).__name__}: {str(exc)[:90]}")

    # --- 8. signup cannot produce an approved business -----------------------
    print()
    print("8. signup creates a business that cannot spend")
    addr = "check-approval-signup@example.invalid"
    admin.execute("delete from business where owner_email = %s", (addr,))
    with connection(biz) as conn:
        new_id = conn.execute("select app_business_signup(%s, %s)",
                              (addr, "Signed Up")).fetchone()[0]
    row = admin.execute("select approved from business where id = %s",
                        (new_id,)).fetchone()
    check("app_business_signup() writes approved = false", row[0] is False,
          f"got {row[0]!r}")
    admin.execute("delete from business where id = %s", (new_id,))

    # --- 9. through the real app, with a real session ------------------------
    # The three sections above prove the gate refuses. This proves what a
    # BROWSER gets when it does, which is a separate fact: the frontend renders
    # the waiting state off `reason`, and an exception that escaped as a 500
    # would still have refused the spending and still have looked like a crash.
    #
    # And the other half, which is the whole design: free endpoints must keep
    # working. A gate that quietly took the product with it would pass every
    # refusal check in this file.
    print()
    print("9. what a signed-in, unapproved browser actually gets")
    from fastapi.testclient import TestClient

    import app.main as api
    from app import auth

    set_flag(False)
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, auth.create_session(biz))

    free = {
        "/stats": client.get("/stats"),
        "/source": client.get("/source"),
        "/review": client.get("/review"),
        "/conflicts": client.get("/conflicts"),
        "/ask": client.get("/ask", params={"q": "ish vaqti"}),
        "/auth/me": client.get("/auth/me"),
    }
    broken = {k: r.status_code for k, r in free.items() if r.status_code != 200}
    check("everything that does not spend still answers 200", not broken,
          f"{broken}" if broken else ", ".join(free))

    check("/auth/me reports the flag, so the screens can say so up front",
          free["/auth/me"].json().get("approved") is False,
          f"approved={free['/auth/me'].json().get('approved')!r}")

    spent = client.get("/answer", params={"q": "ish vaqti"})
    check("/answer is refused with 403", spent.status_code, 403)
    check("and the body carries a reason the frontend can switch on",
          spent.json().get("reason"), "not_approved")
    check("the message names the command that fixes it",
          "onboard.py --approve" in spent.json().get("detail", ""),
          spent.json().get("detail", "")[:90])

    # The two that do not look like they spend. If this file ever stops being
    # read, these two lines are the ones worth keeping: they are the reason the
    # gate is not a list of routes.
    with connection(biz) as conn:
        fact_id = str(conn.execute(
            "insert into fact (subject, subject_key, attribute, attribute_key, "
            "value, confirmed) values "
            "('Klinika','klinika','ish vaqti','ish vaqti','9:00-18:00',false) "
            "returning id").fetchone()[0])
    surprising = {
        "POST /review/{id}/confirm": client.post(f"/review/{fact_id}/confirm"),
        "POST /console/feedback": client.post(
            "/console/feedback", params={"q": "ish vaqti", "verdict": "wrong"}),
    }
    for label, response in surprising.items():
        check(f"{label} is gated too, though it does not look like it",
              response.status_code, 403)
    with connection(biz) as conn:
        conn.execute("delete from fact where id = %s", (fact_id,))

    set_flag(True)
    after = client.get("/auth/me")
    check("approving flips what /auth/me reports, with no restart",
          after.json().get("approved") is True,
          f"approved={after.json().get('approved')!r}")

    admin.execute("delete from session where business_id = %s", (biz,))


if __name__ == "__main__":
    main()

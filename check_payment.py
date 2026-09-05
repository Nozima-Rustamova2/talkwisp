"""Check that payment details cannot be retrieved, at the layer below app/.

    uv run python check_payment.py

WHY THIS FILE EXISTS SEPARATELY FROM app/. Every guarantee here is also
enforced in Python -- retrieval reads a view, seed and typed.store skip the
embedding, check_subject refuses the reserved subject. That is the trap A1
exposed: a rule enforced in Python before the database sees it means the
database constraint never fires, and could be dropped with every test still
green. So the checks below write raw SQL and speak to the database directly.
Delete app/payment.py, revert every query to read `fact`, and these still fail.

Separate from check_orders.py on purpose. That file is about money owed; this
one is about a string that must never reach a model. Keeping them apart keeps
each one's failure legible.

Everything runs inside ONE transaction that is rolled back, so the database is
left exactly as it was found.
"""

import sys

import psycopg

from app.db import pool
# The ONLY import from app/ that the checks below depend on, and it is here to
# be verified against the migration rather than trusted.
from app.payment import PAYMENT_SUBJECT, PAYMENT_SUBJECT_KEY

sys.stdout.reconfigure(encoding="utf-8")

passed = failed = 0


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def rejects(label, fn):
    """The database must refuse this, whatever app/ believes."""
    global passed, failed
    try:
        fn()
    except psycopg.errors.Error as exc:
        passed += 1
        print(f"  [ok  ] {label}")
        print(f"         {type(exc).__name__}")
        return
    failed += 1
    print(f"  [FAIL] {label}")
    print("         the database accepted it")


with pool:
    with pool.connection() as conn:
        # Read BEFORE anything is written, compared after the rollback. The
        # alternative is a literal count, which goes stale the moment the owner
        # adds a line of copy -- and a verifier asserting a stale snapshot is
        # the bug this codebase has now met five times.
        BEFORE = conn.execute(
            "select count(*) from fact where subject_key = %s",
            (PAYMENT_SUBJECT_KEY,)).fetchone()[0]
        try:
            with conn.transaction() as tx:

                def raw(sql, params=()):
                    def go():
                        with conn.transaction():
                            conn.execute(sql, params)
                    return go

                print("\nthe two definitions of the reserved key agree")

                # SQL cannot call normalize(), so migrations/0005 hardcodes the
                # key. If app/payment.py's subject is ever renamed, the view
                # would silently stop excluding it and every payment fact would
                # become retrievable with nothing failing. This is that check.
                viewdef = conn.execute(
                    "select pg_get_viewdef('retrievable_fact'::regclass, true)"
                ).fetchone()[0]
                check("the view's predicate names the key app/payment.py computes",
                      f"'{PAYMENT_SUBJECT_KEY}'" in viewdef, True)
                check("and that key is what it says it is",
                      PAYMENT_SUBJECT_KEY, "tolov malumotlari")

                constraints = [r[0] for r in conn.execute(
                    "select conname from pg_constraint"
                    " where conrelid = 'fact'::regclass and contype = 'c'"
                ).fetchall()]
                check("the embedding constraint exists",
                      "fact_payment_not_embedded" in constraints, True)
                check("the separator constraint exists",
                      "fact_subject_no_separator" in constraints, True)

                print("\nthe view hides them, the table still holds them")

                stored = conn.execute(
                    "select count(*) from fact where subject_key = %s",
                    (PAYMENT_SUBJECT_KEY,)).fetchone()[0]
                check("payment facts are really in the table",
                      stored > 0, True)
                check("and none of them is visible through the view",
                      conn.execute(
                          "select count(*) from retrievable_fact"
                          " where subject_key = %s",
                          (PAYMENT_SUBJECT_KEY,)).fetchone()[0], 0)
                check("the view hides ONLY those -- nothing else went missing",
                      conn.execute("select count(*) from fact").fetchone()[0]
                      - conn.execute(
                          "select count(*) from retrievable_fact").fetchone()[0],
                      stored)

                print("\nthe vector paths are closed by the database, not by a WHERE clause")

                check("no payment fact carries an embedding",
                      conn.execute(
                          "select count(*) from fact where subject_key = %s"
                          "   and embedding is not null",
                          (PAYMENT_SUBJECT_KEY,)).fetchone()[0], 0)

                # Borrow a real vector rather than spending an API call. Any
                # vector proves the point: the payment row cannot be a
                # candidate for ANY query, because it has nothing to compare.
                vector = conn.execute(
                    "select embedding from fact"
                    " where embedding is not null limit 1").fetchone()[0]

                # The literal WHERE clause of _SEARCH_FACTS in app/answer.py,
                # deliberately pointed at `fact` and NOT at the view -- so this
                # holds even if someone reverts that query to the table.
                nearest = conn.execute(
                    "select subject_key from fact"
                    " where confirmed and embedding is not null"
                    " order by embedding <=> %s::vector limit 200",
                    (str(vector),)).fetchall()
                check("the whole vector window cannot contain a payment fact",
                      PAYMENT_SUBJECT_KEY in {r[0] for r in nearest}, False)

                rejects("the database refuses to embed a payment fact",
                        raw("update fact set embedding = %s"
                            " where subject_key = %s",
                            (str(vector), PAYMENT_SUBJECT_KEY)))

                rejects("and refuses to insert one with an embedding",
                        raw("insert into fact (subject, subject_key, attribute,"
                            " attribute_key, value, confirmed, embedding)"
                            " values (%s, %s, 'karta raqami', 'karta raqami',"
                            " '8600 1111 2222 3333', true, %s)",
                            (PAYMENT_SUBJECT, PAYMENT_SUBJECT_KEY, str(vector))))

                print("\nan alias cannot smuggle the subject back in")

                # find()'s candidate CTE unions subjects with confirmed facts
                # and confirmed aliases. The view covers the first arm only, so
                # the second is filtered explicitly -- this proves that filter
                # is really doing something, with a live alias row present.
                conn.execute(
                    "insert into alias (subject_key, alias, alias_key, confirmed)"
                    " values (%s, 'karta', 'karta', true)",
                    (PAYMENT_SUBJECT_KEY,))
                candidates = conn.execute(
                    "select subject_key from ("
                    "  select distinct subject_key from retrievable_fact where confirmed"
                    "  union"
                    "  select subject_key from alias"
                    "   where confirmed and subject_key <> %s) c",
                    (PAYMENT_SUBJECT_KEY,)).fetchall()
                check("an alias pointing at the payment subject reaches nothing",
                      PAYMENT_SUBJECT_KEY in {r[0] for r in candidates}, False)
                check("while the unfiltered union WOULD have surfaced it",
                      PAYMENT_SUBJECT_KEY in {r[0] for r in conn.execute(
                          "select subject_key from alias where confirmed"
                      ).fetchall()}, True)

                print("\nthe separator constraint, on every door")

                # Was a bare `assert` over seed.py's own literals -- one of the
                # four doors, and gone entirely under `python -O`.
                rejects("the database refuses a subject containing ' / '",
                        raw("insert into fact (subject, subject_key, attribute,"
                            " attribute_key, value, confirmed)"
                            " values ('MDT / MRT bosh miya', 'mdt mrt bosh miya',"
                            " 'narx', 'narx', '450 000 soʻm', true)"))

                raise psycopg.Rollback(tx)
        except psycopg.Rollback:
            pass

        print("\nthe database is left untouched")
        check("no stray alias",
              conn.execute("select count(*) from alias where alias_key = 'karta'"
                           ).fetchone()[0], 0)
        check("payment facts unchanged",
              conn.execute("select count(*) from fact where subject_key = %s",
                           (PAYMENT_SUBJECT_KEY,)).fetchone()[0], BEFORE)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

"""Apply every migrations/*.sql file that hasn't run yet, one transaction each."""

import pathlib

from app.db import pool

MIGRATIONS = pathlib.Path(__file__).parent / "migrations"

LEDGER = """
create table if not exists schema_migrations (
    version    text primary key,
    applied_at timestamptz not null default now()
)
"""


def main() -> None:
    with pool:
        with pool.connection() as conn:
            conn.execute(LEDGER)
            applied = {
                r[0] for r in conn.execute("select version from schema_migrations")
            }

        files = sorted(MIGRATIONS.glob("*.sql"))
        pending = [f for f in files if f.name not in applied]
        print(f"{len(files)} migration file(s), {len(pending)} pending.")

        for path in pending:
            sql = path.read_text(encoding="utf-8")
            with pool.connection() as conn:
                conn.execute(sql)
                conn.execute(
                    "insert into schema_migrations (version) values (%s)", (path.name,)
                )
            print(f"  applied {path.name}")


if __name__ == "__main__":
    main()

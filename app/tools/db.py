from __future__ import annotations

import sys

from sqlalchemy.engine import Engine

from ..database import engine as default_engine
from ..db_safe_migrate import pending_sqlite_migrations, run_sqlite_safe_migrations


def _print_pending(engine: Engine) -> None:
    pending = pending_sqlite_migrations(engine)
    if not pending:
        print("No pending migrations")
    else:
        for table, column, ddl in pending:
            print(f"Missing column {table}.{column} ({ddl})")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.tools.db [doctor|migrate]")
        return
    cmd = sys.argv[1]
    engine = default_engine
    print(f"Dialect: {engine.dialect.name}")
    if cmd == "doctor":
        if engine.dialect.name == "sqlite":
            _print_pending(engine)
        else:
            print("Non-SQLite database, nothing to do")
    elif cmd == "migrate":
        if engine.dialect.name != "sqlite":
            print("Non-SQLite database, skipping")
            return
        applied = run_sqlite_safe_migrations(engine)
        if not applied:
            print("No migrations applied")
        else:
            for table, column in applied:
                print(f"Added column {table}.{column}")
    else:
        print("Unknown command", cmd)


if __name__ == "__main__":  # pragma: no cover
    main()

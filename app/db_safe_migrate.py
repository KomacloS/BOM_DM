from __future__ import annotations

import logging
import re
from typing import List, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Mapping of tables to columns and their SQL definitions
_MIGRATIONS: dict[str, dict[str, str]] = {
    "customer": {
        "contact_email": "TEXT DEFAULT ''",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "active": "INTEGER DEFAULT 1",
    },
    "project": {
        "code": "TEXT DEFAULT ''",
        "title": "TEXT DEFAULT ''",
        "name": "TEXT DEFAULT ''",
        "status": "TEXT DEFAULT 'draft'",
        "priority": "TEXT DEFAULT 'med'",
        "notes": "TEXT DEFAULT ''",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "due_at": "TIMESTAMP",
    },
    "assembly": {
        "rev": "TEXT DEFAULT ''",
        "notes": "TEXT DEFAULT ''",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    },
    "part": {
        "part_number": "TEXT",
        "description": "TEXT DEFAULT ''",
        "package": "TEXT DEFAULT ''",
        "value": "TEXT DEFAULT ''",
        "function": "TEXT DEFAULT ''",
        "active_passive": "TEXT DEFAULT ''",
        "power_required": "INTEGER DEFAULT 0",
        "datasheet_url": "TEXT DEFAULT ''",
        "product_url": "TEXT DEFAULT ''",
        "tol_p": "TEXT DEFAULT ''",
        "tol_n": "TEXT DEFAULT ''",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    },
    "task": {
        "title": "TEXT DEFAULT ''",
        "description": "TEXT DEFAULT ''",
        "status": "TEXT DEFAULT 'todo'",
        "assigned_to": "TEXT",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    },
    "bomitem": {
        "assembly_id": "INTEGER",
        "part_id": "INTEGER",
        "datasheet_url": "TEXT DEFAULT ''",
        "manufacturer": "TEXT DEFAULT ''",
        "unit_cost": "NUMERIC DEFAULT 0",
        "currency": "VARCHAR(3) DEFAULT 'USD'",
        "qty": "INTEGER DEFAULT 1",
        "reference": "TEXT DEFAULT ''",
        "alt_part_number": "TEXT DEFAULT ''",
        "is_fitted": "INTEGER DEFAULT 1",
        "notes": "TEXT DEFAULT ''",
    },
}


def _missing_columns(conn, table: str, columns: dict[str, str]) -> List[Tuple[str, str, str]]:
    """Return list of (table, column, ddl) for missing columns."""
    exists = conn.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=:name"
        ),
        {"name": table},
    ).fetchone()
    if not exists:
        return []
    result = conn.execute(text(f'PRAGMA table_info("{table}")'))
    existing = {row[1] for row in result}
    missing = []
    for col, ddl in columns.items():
        if col not in existing:
            missing.append((table, col, ddl))
    return missing


def _add_column_sqlite(conn, table: str, column: str, ddl: str) -> None:
    """Add a column to a SQLite table, handling timestamp defaults."""
    qtable = f'"{table}"'
    qcol = f'"{column}"'
    ddl_upper = ddl.upper()
    if "DEFAULT CURRENT_TIMESTAMP" in ddl_upper:
        base_type = re.split(r"\s+DEFAULT\s+", ddl, flags=re.IGNORECASE)[0].strip()
        conn.execute(text(f"ALTER TABLE {qtable} ADD COLUMN {qcol} {base_type}"))
        conn.execute(text(
            f"UPDATE {qtable} SET {qcol} = CURRENT_TIMESTAMP WHERE {qcol} IS NULL"
        ))
    else:
        conn.execute(text(f"ALTER TABLE {qtable} ADD COLUMN {qcol} {ddl}"))


def pending_sqlite_migrations(engine: Engine) -> List[Tuple[str, str, str]]:
    """Return pending migrations without applying them."""
    if engine.dialect.name != "sqlite":
        return []
    with engine.begin() as conn:
        pending: List[Tuple[str, str, str]] = []
        for table, cols in _MIGRATIONS.items():
            pending.extend(_missing_columns(conn, table, cols))
        return pending


def _column_exists(conn, table: str, col: str) -> bool:
    return any(
        r[1] == col for r in conn.execute(text(f'PRAGMA table_info("{table}")'))
    )


def _rebuild_part_table_without_number(conn) -> None:
    """Rebuild ``part`` table dropping legacy ``number`` column and enforcing
    ``part_number`` NOT NULL."""
    cols = [
        "id INTEGER PRIMARY KEY",
        "part_number TEXT NOT NULL",
        "description TEXT DEFAULT ''",
        "package TEXT DEFAULT ''",
        "value TEXT DEFAULT ''",
        "function TEXT DEFAULT ''",
        "active_passive TEXT DEFAULT ''",
        "power_required INTEGER DEFAULT 0",
        "datasheet_url TEXT DEFAULT ''",
        "product_url TEXT DEFAULT ''",
        "tol_p TEXT DEFAULT ''",
        "tol_n TEXT DEFAULT ''",
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    ]
    conn.execute(text(f"CREATE TABLE part_new ({', '.join(cols)})"))
    existing = {row[1] for row in conn.execute(text('PRAGMA table_info("part")'))}
    insert_cols = ["id", "part_number"]
    select_cols = ["id", "COALESCE(part_number, number) AS part_number"]
    for col in [
        "description",
        "package",
        "value",
        "function",
        "active_passive",
        "power_required",
        "datasheet_url",
        "product_url",
        "tol_p",
        "tol_n",
        "created_at",
    ]:
        if col in existing:
            insert_cols.append(col)
            select_cols.append(col)
    conn.execute(
        text(
            f"INSERT INTO part_new ({', '.join(insert_cols)}) SELECT {', '.join(select_cols)} FROM part"
        )
    )
    conn.execute(text("DROP TABLE part"))
    conn.execute(text("ALTER TABLE part_new RENAME TO part"))


def _fix_part_legacy_number_column(conn) -> bool:
    """Remove legacy ``number`` column using ALTER when possible."""

    has_number = _column_exists(conn, "part", "number")
    has_part_number = _column_exists(conn, "part", "part_number")
    if not has_number:
        return False

    fk_state = conn.execute(text("PRAGMA foreign_keys")).scalar() or 0
    if fk_state:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        if not has_part_number:
            conn.execute(text('ALTER TABLE "part" RENAME COLUMN "number" TO "part_number"'))
        else:
            conn.execute(
                text(
                    'UPDATE "part" SET part_number = COALESCE(part_number, number)'
                )
            )
            info = {
                row[1]: row for row in conn.execute(text('PRAGMA table_info("part")'))
            }
            part_notnull = info.get("part_number", (None, None, None, 0))[3] == 1
            if not part_notnull:
                _rebuild_part_table_without_number(conn)
                return True
            try:
                conn.execute(text('ALTER TABLE "part" DROP COLUMN "number"'))
            except Exception:
                _rebuild_part_table_without_number(conn)
                return True
        return True
    finally:
        if fk_state:
            conn.execute(text("PRAGMA foreign_keys=ON"))

def run_sqlite_safe_migrations(engine: Engine) -> List[Tuple[str, str]]:
    """Add missing columns with defaults for SQLite development databases."""
    if engine.dialect.name != "sqlite":
        return []
    applied: List[Tuple[str, str]] = []

    with engine.begin() as conn:
        if _fix_part_legacy_number_column(conn):
            applied.append(("part", "fix_legacy_number"))

        for table, cols in _MIGRATIONS.items():
            missing = _missing_columns(conn, table, cols)
            for _table, column, ddl in missing:
                _add_column_sqlite(conn, _table, column, ddl)
                applied.append((_table, column))
                logger.info("Added column %s.%s", _table, column)

        if _column_exists(conn, "part", "part_number"):
            conn.execute(
                text(
                    'CREATE UNIQUE INDEX IF NOT EXISTS ix_part_part_number ON "part"(part_number)'
                )
            )

    return applied

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
        "description": "TEXT DEFAULT ''",
        "package": "TEXT DEFAULT ''",
        "value": "TEXT DEFAULT ''",
        "function": "TEXT DEFAULT ''",
        "active_passive": "TEXT DEFAULT ''",
        "power_required": "INTEGER DEFAULT 0",
        "datasheet_url": "TEXT DEFAULT ''",
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
        "mpn": "TEXT DEFAULT ''",
        "footprint": "TEXT DEFAULT ''",
        "unit_cost": "NUMERIC DEFAULT 0",
        "dnp": "INTEGER DEFAULT 0",
        "currency": "VARCHAR(3) DEFAULT 'USD'",
        "qty": "INTEGER DEFAULT 1",
        "reference": "TEXT DEFAULT ''",
    },
    "user": {
        "hashed_pw": "VARCHAR DEFAULT ''",
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


def _backfill_bomitem_qty_from_quantity(conn) -> None:
    # if legacy 'quantity' exists and 'qty' exists, copy values where qty is NULL/0
    if _column_exists(conn, "bomitem", "quantity") and _column_exists(
        conn, "bomitem", "qty"
    ):
        conn.execute(
            text(
                'UPDATE "bomitem" SET "qty" = COALESCE(NULLIF("quantity", 0), "qty")'
            )
        )


def run_sqlite_safe_migrations(engine: Engine) -> List[Tuple[str, str]]:
    """Add missing columns with defaults for SQLite development databases."""
    if engine.dialect.name != "sqlite":
        return []
    applied: List[Tuple[str, str]] = []

    with engine.begin() as conn:
        for table, cols in _MIGRATIONS.items():
            missing = _missing_columns(conn, table, cols)
            for _table, column, ddl in missing:
                _add_column_sqlite(conn, _table, column, ddl)
                applied.append((_table, column))
                logger.info("Added column %s.%s", _table, column)

        if _column_exists(conn, "customer", "active"):
            conn.execute(
                text('UPDATE "customer" SET "active" = 1 WHERE "active" IS NULL')
            )
        if _column_exists(conn, "project", "name"):
            conn.execute(
                text(
                    'UPDATE "project" SET "name" = COALESCE("title", "code", \'\') '
                    'WHERE "name" IS NULL'
                )
            )
        _backfill_bomitem_qty_from_quantity(conn)

    return applied

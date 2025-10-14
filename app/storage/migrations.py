from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Union

logger = logging.getLogger(__name__)

SchemaInput = Union[str, Path, sqlite3.Connection]

TARGET_VERSION = 2
_SCHEMA_KEY = "schema_version"


def _coerce_connection(conn_or_path: SchemaInput) -> tuple[sqlite3.Connection, bool]:
    if isinstance(conn_or_path, sqlite3.Connection):
        return conn_or_path, False
    if isinstance(conn_or_path, Path):
        path = conn_or_path
    else:
        path = Path(conn_or_path)
    connection = sqlite3.connect(path)
    return connection, True


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _get_version(conn: sqlite3.Connection) -> int:
    _ensure_meta_table(conn)
    row = conn.execute(
        "SELECT value FROM app_meta WHERE key = ?", (_SCHEMA_KEY,)
    ).fetchone()
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    _ensure_meta_table(conn)
    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_SCHEMA_KEY, str(version)),
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def _columns_for_table(conn: sqlite3.Connection, table: str) -> Iterable[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(f"PRAGMA table_info('{table}')")
    return list(cursor.fetchall())


def ensure_schema_upgraded(conn_or_path: SchemaInput) -> None:
    """Upgrade the SQLite schema to the current version, idempotently."""

    connection, needs_close = _coerce_connection(conn_or_path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN")
            current_version = _get_version(connection)
            if current_version >= TARGET_VERSION:
                connection.execute("COMMIT")
                return

            _apply_upgrade_v2(connection)
            _set_version(connection, TARGET_VERSION)
            connection.execute("COMMIT")
            logger.info("[db] upgraded schema to v%s (added complex_links.ce_pn, total_pins)", TARGET_VERSION)
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
    finally:
        if needs_close:
            connection.close()


def get_schema_version(conn_or_path: SchemaInput) -> int:
    connection, needs_close = _coerce_connection(conn_or_path)
    try:
        return _get_version(connection)
    finally:
        if needs_close:
            connection.close()


def _apply_upgrade_v2(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "complex_links"):
        return

    columns = {row["name"]: row for row in _columns_for_table(conn, "complex_links")}
    if "ce_pn" not in columns:
        conn.execute("ALTER TABLE complex_links ADD COLUMN ce_pn TEXT")
    if "total_pins" not in columns:
        conn.execute("ALTER TABLE complex_links ADD COLUMN total_pins INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE complex_links SET total_pins = 0 WHERE total_pins IS NULL")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_complex_links_ce_pn ON complex_links(ce_pn)"
    )

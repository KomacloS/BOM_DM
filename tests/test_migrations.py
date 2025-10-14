import sqlite3
import tempfile
from pathlib import Path

from app.storage.migrations import ensure_schema_upgraded, get_schema_version


def _create_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE complex_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id INTEGER NOT NULL,
                ce_db_uri TEXT,
                ce_complex_id TEXT NOT NULL,
                aliases TEXT,
                pin_map TEXT,
                macro_ids TEXT,
                source_hash TEXT,
                synced_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_adds_missing_columns_idempotently():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "legacy.db"
        _create_legacy_db(db_path)

        before = get_schema_version(db_path)
        assert before == 0

        ensure_schema_upgraded(db_path)
        after = get_schema_version(db_path)
        assert after == 2

        conn = sqlite3.connect(db_path)
        try:
            info = {row[1]: row for row in conn.execute("PRAGMA table_info('complex_links')")}
            assert "ce_pn" in info
            assert info["ce_pn"][2].upper() == "TEXT"

            assert "total_pins" in info
            total_pins = info["total_pins"]
            assert total_pins[2].upper() == "INTEGER"
            assert total_pins[3] == 1  # NOT NULL
            assert str(total_pins[4]) in ("0", "0.0", None)

            index_rows = list(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_complex_links_ce_pn'"
                )
            )
            assert index_rows, "expected idx_complex_links_ce_pn index to exist"
        finally:
            conn.close()

        # Ensure idempotency (second run should not raise)
        ensure_schema_upgraded(db_path)
        assert get_schema_version(db_path) == 2

"""Manual CLI to run database schema migrations for BOM_DB."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.storage.migrations import ensure_schema_upgraded, get_schema_version


def main() -> None:
    parser = argparse.ArgumentParser(description="Upgrade the BOM_DB SQLite schema.")
    parser.add_argument(
        "--db",
        required=True,
        help="Path to the SQLite database file (e.g., app.db).",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    before = get_schema_version(db_path)
    ensure_schema_upgraded(db_path)
    after = get_schema_version(db_path)
    print(f"Schema version: {before} -> {after}")


if __name__ == "__main__":
    main()

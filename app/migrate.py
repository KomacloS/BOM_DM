from sqlmodel import SQLModel
from sqlalchemy import inspect, text

from .config import get_engine
from . import main  # ensure models are imported


def upgrade() -> None:
    engine = get_engine()
    insp = inspect(engine)
    with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            conn.execute(text("PRAGMA foreign_keys=OFF"))
        SQLModel.metadata.create_all(engine)
        if "bomitem" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("bomitem")}
            if "project_id" not in cols:
                conn.execute(text("ALTER TABLE bomitem ADD COLUMN project_id INTEGER"))
            if "datasheet_url" not in cols:
                conn.execute(text("ALTER TABLE bomitem ADD COLUMN datasheet_url TEXT"))
            if "manufacturer" not in cols:
                conn.execute(text("ALTER TABLE bomitem ADD COLUMN manufacturer TEXT"))
            if "mpn" not in cols:
                conn.execute(text("ALTER TABLE bomitem ADD COLUMN mpn TEXT"))
            if "footprint" not in cols:
                conn.execute(text("ALTER TABLE bomitem ADD COLUMN footprint TEXT"))
            if "unit_cost" not in cols:
                if engine.dialect.name == "sqlite":
                    conn.execute(text("ALTER TABLE bomitem ADD COLUMN unit_cost NUMERIC"))
                else:
                    conn.execute(text("ALTER TABLE bomitem ADD COLUMN IF NOT EXISTS unit_cost NUMERIC(10,4)"))
            if "dnp" not in cols:
                conn.execute(text("ALTER TABLE bomitem ADD COLUMN dnp BOOLEAN DEFAULT 0"))
            if "currency" not in cols:
                conn.execute(text("ALTER TABLE bomitem ADD COLUMN currency VARCHAR(3) DEFAULT 'USD'"))
            if engine.dialect.name == "postgresql":
                conn.execute(text("ALTER TABLE bomitem DROP CONSTRAINT IF EXISTS bomitem_project_id_fkey"))
                conn.execute(
                    text(
                        "ALTER TABLE bomitem ADD CONSTRAINT bomitem_project_id_fkey FOREIGN KEY(project_id) REFERENCES project(id) ON DELETE CASCADE"
                    )
                )
            elif engine.dialect.name == "sqlite":
                ver = conn.exec_driver_sql("select sqlite_version()").scalar()
                if tuple(map(int, ver.split("."))) >= (3, 35):
                    pass
        if "customer" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("customer")}
            if "notes" not in cols:
                conn.execute(text("ALTER TABLE customer ADD COLUMN notes TEXT"))
            if "created_at" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE customer ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                    )
                )
        if "project" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("project")}
            if "code" not in cols:
                conn.execute(text("ALTER TABLE project ADD COLUMN code VARCHAR"))
            if "notes" not in cols:
                conn.execute(text("ALTER TABLE project ADD COLUMN notes TEXT"))
            if "created_at" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE project ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                    )
                )
        if "part" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("part")}
            if "created_at" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE part ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                    )
                )
        if "task" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("task")}
            if "created_at" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE task ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                    )
                )
        if engine.dialect.name == "sqlite":
            conn.execute(text("PRAGMA foreign_keys=ON"))


def main() -> None:
    upgrade()


if __name__ == "__main__":  # pragma: no cover
    main()

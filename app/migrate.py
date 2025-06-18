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
        if "project" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("project")}
            if "code" not in cols:
                conn.execute(text("ALTER TABLE project ADD COLUMN code VARCHAR"))
            if "notes" not in cols:
                conn.execute(text("ALTER TABLE project ADD COLUMN notes TEXT"))
        if engine.dialect.name == "sqlite":
            conn.execute(text("PRAGMA foreign_keys=ON"))


def main() -> None:
    upgrade()


if __name__ == "__main__":  # pragma: no cover
    main()

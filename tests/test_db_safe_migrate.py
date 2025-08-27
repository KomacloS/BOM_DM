import sqlalchemy
from sqlalchemy import inspect, text
from sqlmodel import create_engine

from app.db_safe_migrate import run_sqlite_safe_migrations


def _mk_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )


def test_created_at_added_and_backfilled():
    engine = _mk_engine()
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "customer" (id INTEGER PRIMARY KEY, name TEXT)'))
        conn.execute(text('INSERT INTO "customer"(name) VALUES ("A"), ("B")'))
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("customer")}
    assert "created_at" in cols
    with engine.begin() as conn:
        rows = conn.execute(
            text('SELECT COUNT(*) FROM "customer" WHERE "created_at" IS NULL')
        ).scalar()
        assert rows == 0


def test_project_columns_added():
    engine = _mk_engine()
    with engine.begin() as conn:
        conn.execute(
            text('CREATE TABLE "project" (id INTEGER PRIMARY KEY, customer_id INTEGER, code TEXT)')
        )
        conn.execute(
            text('INSERT INTO "project"(customer_id, code) VALUES (1, "P-001")')
        )
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("project")}
    for col in ("title", "status", "priority", "notes", "created_at", "due_at"):
        assert col in cols


def test_part_tolerances_added():
    engine = _mk_engine()
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "part" (id INTEGER PRIMARY KEY, part_number TEXT)'))
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("part")}
    assert "tol_p" in cols and "tol_n" in cols


def test_part_number_column_and_index_added():
    engine = _mk_engine()
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "part" (id INTEGER PRIMARY KEY)'))
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("part")}
    assert "part_number" in cols
    idx = {i["name"] for i in insp.get_indexes("part")}
    assert "ix_part_part_number" in idx

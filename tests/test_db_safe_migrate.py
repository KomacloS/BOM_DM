import sqlalchemy
from sqlalchemy import inspect, text
from sqlmodel import create_engine

from app.db_safe_migrate import run_sqlite_safe_migrations


def test_created_at_added_and_backfilled():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
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


def test_reserved_name_user_table():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "user" (id INTEGER PRIMARY KEY, name TEXT)'))
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("user")}
    assert "hashed_pw" in cols

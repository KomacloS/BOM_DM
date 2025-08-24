import sqlalchemy
from sqlalchemy import text
from sqlmodel import create_engine

from app.db_safe_migrate import run_sqlite_safe_migrations


def test_run_sqlite_safe_migrations_adds_created_at():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE customer (id INTEGER PRIMARY KEY, name TEXT)"))
    run_sqlite_safe_migrations(engine)
    insp = sqlalchemy.inspect(engine)
    cols = {c["name"] for c in insp.get_columns("customer")}
    assert "created_at" in cols

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


def test_project_columns_added():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
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
    with engine.begin() as conn:
        nulls = conn.execute(
            text('SELECT COUNT(*) FROM "project" WHERE "created_at" IS NULL')
        ).scalar()
        assert nulls == 0


def test_customer_active_added_and_backfilled():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "customer" (id INTEGER PRIMARY KEY, name TEXT)'))
        conn.execute(text('INSERT INTO "customer"(name) VALUES ("A")'))
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("customer")}
    assert "active" in cols
    with engine.begin() as conn:
        val = conn.execute(text('SELECT "active" FROM "customer"')).scalar()
        assert val == 1


def test_project_name_backfilled_even_if_exists():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                'CREATE TABLE "project" (id INTEGER PRIMARY KEY, customer_id INTEGER, code TEXT, title TEXT, name TEXT)'
            )
        )
        conn.execute(
            text('INSERT INTO "project"(customer_id, code, title, name) VALUES (1, "P-001", "Board A", NULL)')
        )
    run_sqlite_safe_migrations(engine)
    with engine.begin() as conn:
        name = conn.execute(text('SELECT "name" FROM "project"')).scalar()
        assert name == "Board A"

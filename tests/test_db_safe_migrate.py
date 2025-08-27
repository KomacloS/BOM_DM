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


def test_bomitem_qty_added_and_backfilled_from_quantity():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                'CREATE TABLE "bomitem" (id INTEGER PRIMARY KEY, assembly_id INTEGER, part_id INTEGER, quantity INTEGER, reference TEXT)'
            )
        )
        conn.execute(
            text(
                'INSERT INTO "bomitem"(assembly_id, part_id, quantity, reference) VALUES (1, NULL, 3, "R1")'
            )
        )
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("bomitem")}
    assert "qty" in cols and "reference" in cols
    with engine.begin() as conn:
        v = conn.execute(text('SELECT "qty" FROM "bomitem" WHERE id=1')).scalar()
        assert v == 3


def test_bomitem_alt_isfitted_notes_added_and_isfitted_backfilled():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE "bomitem" ('
            '  id INTEGER PRIMARY KEY,'
            '  assembly_id INTEGER,'
            '  part_id INTEGER,'
            '  quantity INTEGER,'
            '  reference TEXT,'
            '  dnp INTEGER'
            ')'
        ))
        # One fitted (dnp=0), one DNP (dnp=1)
        conn.execute(text(
            'INSERT INTO "bomitem"(assembly_id, part_id, quantity, reference, dnp) '
            'VALUES (1, NULL, 2, "R1", 0), (1, NULL, 1, "R2", 1)'
        ))
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("bomitem")}
    assert {"qty", "reference", "alt_part_number", "is_fitted", "notes"}.issubset(cols)
    with engine.begin() as conn:
        rows = conn.execute(text(
            'SELECT reference, qty, is_fitted, alt_part_number, notes FROM "bomitem" ORDER BY id'
        )).fetchall()
    # qty backfilled from quantity
    assert rows[0][1] == 2 and rows[1][1] == 1
    # is_fitted backfilled from dnp: R1 fitted (1), R2 not fitted (0)
    assert rows[0][2] == 1 and rows[1][2] == 0


def test_part_tolerances_added():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "part" (id INTEGER PRIMARY KEY, part_number TEXT)'))
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("part")}
    assert "tol_p" in cols and "tol_n" in cols


def test_part_number_column_and_index_added():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "part" (id INTEGER PRIMARY KEY)'))
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("part")}
    assert "part_number" in cols
    idx = {i["name"] for i in insp.get_indexes("part")}
    assert "ix_part_part_number" in idx


def test_legacy_part_table_rebuilt_and_backfilled():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            text('CREATE TABLE "part" (id INTEGER PRIMARY KEY, number TEXT NOT NULL)')
        )
        conn.execute(text('INSERT INTO "part"(number) VALUES ("ABC")'))
    run_sqlite_safe_migrations(engine)
    # Second run should be no-op
    run_sqlite_safe_migrations(engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("part")}
    assert "number" not in cols and "part_number" in cols
    idx = {i["name"] for i in insp.get_indexes("part")}
    assert "ix_part_part_number" in idx
    with engine.begin() as conn:
        val = conn.execute(text('SELECT part_number FROM "part" WHERE id=1')).scalar()
        assert val == "ABC"
        conn.execute(text('INSERT INTO "part"(part_number) VALUES ("DEF")'))
        rows = conn.execute(text('SELECT part_number FROM "part" ORDER BY id')).fetchall()
        assert [r[0] for r in rows] == ["ABC", "DEF"]

import sqlalchemy
from sqlalchemy import inspect, text
from sqlmodel import create_engine, select

from app import database
from app.models import Part


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )


def _session(engine, monkeypatch):
    monkeypatch.setattr(database, "engine", engine, raising=False)
    monkeypatch.setattr(database, "__schema_ok", False, raising=False)
    gen = database.get_session()
    session = next(gen)
    return session, gen


def test_ensure_schema_migrates_number_only(monkeypatch):
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE part (id INTEGER PRIMARY KEY, number TEXT NOT NULL)'))
    session, gen = _session(engine, monkeypatch)
    try:
        p = Part(part_number="ABC")
        session.add(p)
        session.commit()
        result = session.exec(select(Part)).all()
        assert result[0].part_number == "ABC"
    finally:
        gen.close()
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("part")}
    assert "number" not in cols and "part_number" in cols


def test_ensure_schema_drops_number_and_backfills(monkeypatch):
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE part (id INTEGER PRIMARY KEY, number TEXT, part_number TEXT)'))
        conn.execute(text('INSERT INTO part (number) VALUES ("ABC")'))
        conn.execute(text('INSERT INTO part (number, part_number) VALUES ("DEF", NULL)'))
    session, gen = _session(engine, monkeypatch)
    gen.close()
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("part")}
    assert "number" not in cols and "part_number" in cols
    with engine.begin() as conn:
        rows = [r[0] for r in conn.execute(text('SELECT part_number FROM part ORDER BY id'))]
    assert rows == ["ABC", "DEF"]
    idx = {i["name"] for i in insp.get_indexes("part")}
    assert "ix_part_part_number" in idx
    info = {c["name"]: c for c in insp.get_columns("part")}
    assert not info["part_number"]["nullable"]

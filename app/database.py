from __future__ import annotations

from sqlmodel import SQLModel, Session

from .db_safe_migrate import run_sqlite_safe_migrations
from .config import get_engine

_schema_checked_urls: set[str] = set()


def ensure_schema() -> None:
    engine = get_engine()
    url = str(engine.url)
    if url not in _schema_checked_urls:
        SQLModel.metadata.create_all(engine)
        run_sqlite_safe_migrations(engine)
        _schema_checked_urls.add(url)


def get_session():
    ensure_schema()
    engine = get_engine()
    with Session(engine) as session:
        yield session


def new_session() -> Session:
    ensure_schema()
    return Session(get_engine())

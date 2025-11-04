from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session

from .db_safe_migrate import run_sqlite_safe_migrations
from .config import get_engine

_schema_checked_urls: set[str] = set()
engine: Engine | None = None
__schema_ok: bool = False


def _resolve_engine() -> Engine:
    global engine
    if engine is None:
        engine = get_engine()
    return engine


def ensure_schema() -> None:
    global __schema_ok
    resolved = _resolve_engine()
    url = str(resolved.url)
    if url not in _schema_checked_urls or not __schema_ok:
        SQLModel.metadata.create_all(resolved)
        run_sqlite_safe_migrations(resolved)
        _schema_checked_urls.add(url)
        __schema_ok = True


def get_session():
    ensure_schema()
    resolved = _resolve_engine()
    with Session(resolved) as session:
        yield session


def new_session() -> Session:
    ensure_schema()
    return Session(_resolve_engine())

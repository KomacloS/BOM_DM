from __future__ import annotations

import logging
from typing import Any

from sqlmodel import SQLModel, Session

from .config import get_engine
from .db_safe_migrate import run_sqlite_safe_migrations
from .storage.migrations import ensure_schema_upgraded

_schema_checked_urls: set[str] = set()

logger = logging.getLogger(__name__)


def _upgrade_sqlite_schema(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    db_path = engine.url.database
    if not db_path or db_path == ":memory:":
        raw = engine.raw_connection()
        try:
            native: Any = getattr(raw, "connection", raw)
            ensure_schema_upgraded(native)
        finally:
            raw.close()
    else:
        ensure_schema_upgraded(db_path)


def ensure_schema() -> None:
    engine = get_engine()
    url = str(engine.url)
    if url not in _schema_checked_urls:
        SQLModel.metadata.create_all(engine)
        run_sqlite_safe_migrations(engine)
        _upgrade_sqlite_schema(engine)
        _schema_checked_urls.add(url)


def get_session():
    ensure_schema()
    engine = get_engine()
    with Session(engine) as session:
        yield session


def new_session() -> Session:
    ensure_schema()
    return Session(get_engine())

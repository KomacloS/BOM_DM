from __future__ import annotations
import os

from sqlmodel import SQLModel, Session, create_engine

from .db_safe_migrate import run_sqlite_safe_migrations

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
engine = create_engine(DATABASE_URL, echo=False)

__schema_ok = False


def ensure_schema() -> None:
    global __schema_ok
    if not __schema_ok:
        SQLModel.metadata.create_all(engine)
        run_sqlite_safe_migrations(engine)
        __schema_ok = True


def get_session():
    ensure_schema()
    with Session(engine) as session:
        yield session


def new_session() -> Session:
    ensure_schema()
    return Session(engine)

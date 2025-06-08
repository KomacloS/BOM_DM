# root: app/main.py
from fastapi import FastAPI
from sqlmodel import SQLModel, Field, Session, create_engine
from sqlalchemy import text

DATABASE_URL = "postgresql://postgres:password@localhost:5432/bom_db"
engine = create_engine(DATABASE_URL, echo=False)


class StatusCheck(SQLModel, table=True):
    """Trivial table used to exercise the database connection."""

    id: int | None = Field(default=None, primary_key=True)
    msg: str


def init_db() -> None:
    """Create database tables if they do not exist."""

    SQLModel.metadata.create_all(engine)


app = FastAPI()


@app.on_event("startup")
def on_startup() -> None:
    """Initialise the database on application start."""

    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    """Return API and DB health status."""

    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))
        db_status = "ok"
    except Exception:  # pragma: no cover - network/db errors
        db_status = "error"

    return {"api": "ok", "db": db_status}

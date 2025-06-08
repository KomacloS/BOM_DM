# root: app/main.py
from fastapi import FastAPI, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Field, Session, create_engine, select
from sqlalchemy import text

from .pdf_utils import extract_bom_text, parse_bom_lines

DATABASE_URL = "postgresql://postgres:password@localhost:5432/bom_db"
engine = create_engine(DATABASE_URL, echo=False)


class StatusCheck(SQLModel, table=True):
    """Trivial table used to exercise the database connection."""

    id: int | None = Field(default=None, primary_key=True)
    msg: str


class BOMItemBase(SQLModel):
    """Shared properties for BOM items."""

    part_number: str = Field(min_length=1)
    description: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)
    reference: str | None = None


class BOMItem(BOMItemBase, table=True):
    """Database model for a BOM item."""

    id: int | None = Field(default=None, primary_key=True)


class BOMItemCreate(BOMItemBase):
    """Schema for creating items via the API."""

    pass


def init_db() -> None:
    """Create database tables if they do not exist."""

    SQLModel.metadata.create_all(engine)


app = FastAPI()

origins = ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/bom/items", response_model=list[BOMItem])
def list_items() -> list[BOMItem]:
    """Return all BOM items."""

    with Session(engine) as session:
        items = session.exec(select(BOMItem)).all()
    return items


@app.post("/bom/items", response_model=BOMItem, status_code=status.HTTP_201_CREATED)
def create_item(item: BOMItemCreate) -> BOMItem:
    """Create a new BOM item."""

    db_item = BOMItem.from_orm(item)
    with Session(engine) as session:
        session.add(db_item)
        session.commit()
        session.refresh(db_item)
    return db_item


@app.post("/bom/import", response_model=list[BOMItem])
async def import_bom(file: UploadFile = File(...)) -> list[BOMItem]:
    """Import BOM items from an uploaded PDF file."""

    pdf_bytes = await file.read()
    text = extract_bom_text(pdf_bytes)
    records = parse_bom_lines(text)

    inserted: list[BOMItem] = []
    with Session(engine) as session:
        for rec in records:
            if not rec.get("part_number") or not rec.get("description"):
                continue
            item = BOMItem(**rec)
            session.add(item)
            session.commit()
            session.refresh(item)
            inserted.append(item)
    return inserted

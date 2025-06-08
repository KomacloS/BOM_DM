# root: app/main.py
from fastapi import FastAPI, status, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Field, Session, create_engine, select
from sqlalchemy import text, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from datetime import datetime

from .pdf_utils import extract_bom_text, parse_bom_lines
from .quote_utils import calculate_quote

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

    __table_args__ = (UniqueConstraint("part_number", "reference", name="uix_part_ref"),)


class BOMItemCreate(BOMItemBase):
    """Schema for creating items via the API."""

    pass


class BOMItemRead(BOMItemBase):
    """Schema returned from the API."""

    id: int


class BOMItemUpdate(SQLModel):
    """Schema for updating items (all fields optional)."""

    part_number: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    quantity: int | None = Field(default=None, ge=1)
    reference: str | None = None


class QuoteResponse(SQLModel):
    """Schema returned from the quote endpoint."""

    total_components: int
    estimated_time_s: int
    estimated_cost_usd: float


class TestResult(SQLModel, table=True):
    """Database model for flying-probe test results."""

    test_id: int | None = Field(default=None, primary_key=True)
    assembly_id: int = 1  # placeholder until multi-BOM support
    serial_number: str | None = None
    date_tested: datetime = Field(default_factory=datetime.utcnow)
    result: bool
    failure_details: str | None = None


class TestResultCreate(SQLModel):
    """Schema for creating test results via the API."""

    assembly_id: int = 1
    serial_number: str | None = None
    result: bool
    failure_details: str | None = None


class TestResultRead(TestResultCreate):
    """Schema returned from the test result API."""

    test_id: int
    date_tested: datetime


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


@app.get("/bom/items", response_model=list[BOMItemRead])
def list_items(
    search: str | None = None,
    min_qty: int | None = None,
    max_qty: int | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[BOMItemRead]:
    """Return BOM items with optional filtering and pagination."""

    if limit > 200:
        limit = 200

    stmt = select(BOMItem)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            (BOMItem.part_number.ilike(pattern))
            | (BOMItem.description.ilike(pattern))
        )
    if min_qty is not None:
        stmt = stmt.where(BOMItem.quantity >= min_qty)
    if max_qty is not None:
        stmt = stmt.where(BOMItem.quantity <= max_qty)
    stmt = stmt.offset(skip).limit(limit)

    with Session(engine) as session:
        items = session.exec(stmt).all()
    return items


@app.post("/bom/items", response_model=BOMItemRead, status_code=status.HTTP_201_CREATED)
def create_item(item: BOMItemCreate) -> BOMItemRead:
    """Create a new BOM item."""

    db_item = BOMItem.from_orm(item)
    with Session(engine) as session:
        session.add(db_item)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Item with this part_number and reference already exists",
            )
        session.refresh(db_item)
    return db_item


@app.get("/bom/items/{item_id}", response_model=BOMItemRead)
def get_item(item_id: int) -> BOMItemRead:
    """Retrieve a single BOM item by ID."""

    with Session(engine) as session:
        item = session.get(BOMItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        return item


@app.put("/bom/items/{item_id}", response_model=BOMItemRead)
def replace_item(item_id: int, item_in: BOMItemCreate) -> BOMItemRead:
    """Fully replace an existing BOM item."""

    with Session(engine) as session:
        db_item = session.get(BOMItem, item_id)
        if not db_item:
            raise HTTPException(status_code=404, detail="Item not found")
        for field, value in item_in.dict().items():
            setattr(db_item, field, value)
        try:
            session.add(db_item)
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Item with this part_number and reference already exists",
            )
        session.refresh(db_item)
        return db_item


@app.patch("/bom/items/{item_id}", response_model=BOMItemRead)
def update_item(item_id: int, item_in: BOMItemUpdate) -> BOMItemRead:
    """Partially update an existing BOM item."""

    with Session(engine) as session:
        db_item = session.get(BOMItem, item_id)
        if not db_item:
            raise HTTPException(status_code=404, detail="Item not found")
        update_data = item_in.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_item, field, value)
        try:
            session.add(db_item)
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Item with this part_number and reference already exists",
            )
        session.refresh(db_item)
        return db_item


@app.delete("/bom/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int) -> None:
    """Delete a BOM item."""

    with Session(engine) as session:
        db_item = session.get(BOMItem, item_id)
        if not db_item:
            raise HTTPException(status_code=404, detail="Item not found")
        session.delete(db_item)
        session.commit()
    return None


@app.post("/bom/import", response_model=list[BOMItemRead])
async def import_bom(file: UploadFile = File(...)) -> list[BOMItemRead]:
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
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Item with this part_number and reference already exists",
                )
            session.refresh(item)
            inserted.append(item)
    return inserted


@app.get("/bom/quote", response_model=QuoteResponse)
def get_quote() -> QuoteResponse:
    """Return quick cost/time estimates for all BOM items."""

    with Session(engine) as session:
        items = session.exec(select(BOMItem)).all()
    data = calculate_quote(items)
    return QuoteResponse(**data)


@app.post("/testresults", response_model=TestResultRead, status_code=status.HTTP_201_CREATED)
def create_test_result(result_in: TestResultCreate) -> TestResultRead:
    """Log a new flying-probe test result."""

    db_result = TestResult(**result_in.dict())
    with Session(engine) as session:
        session.add(db_result)
        session.commit()
        session.refresh(db_result)
    return db_result


@app.get("/testresults", response_model=list[TestResultRead])
def list_test_results(skip: int = 0, limit: int = 50) -> list[TestResultRead]:
    """Return test result logs with pagination."""

    if limit > 200:
        limit = 200

    with Session(engine) as session:
        stmt = select(TestResult).offset(skip).limit(limit)
        results = session.exec(stmt).all()
    return results


@app.get("/testresults/{test_id}", response_model=TestResultRead)
def get_test_result(test_id: int) -> TestResultRead:
    """Retrieve a single test result by ID."""

    with Session(engine) as session:
        result = session.get(TestResult, test_id)
        if not result:
            raise HTTPException(status_code=404, detail="Test result not found")
        return result

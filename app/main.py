# root: app/main.py
from fastapi import (
    FastAPI,
    status,
    UploadFile,
    File,
    HTTPException,
    Depends,
    APIRouter,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import jwt
from sqlmodel import SQLModel, Field, Session, create_engine, select
from sqlalchemy import text, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import csv
import io
import os
from openpyxl import Workbook

from .security import verify_password, get_password_hash, create_access_token
from .config import (
    SECRET_KEY,
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    DATABASE_URL,
    load_settings,
    save_database_url,
)

from .pdf_utils import extract_bom_text, parse_bom_lines
from .quote_utils import calculate_quote
from .trace_utils import component_trace, board_trace

engine = create_engine(DATABASE_URL, echo=False)
scheduler = BackgroundScheduler()
templates = Jinja2Templates(directory="app/frontend/templates")


def reload_db(url: str) -> None:
    """Rebuild SQL engine and re-init DB."""
    global engine
    engine.dispose()
    engine = create_engine(url, echo=False)
    init_db()


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


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(sa_column_kwargs={"unique": True})
    hashed_pw: str
    role: str


class UserCreate(SQLModel):
    username: str
    password: str
    role: str = "user"


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


class ComponentTraceEntry(SQLModel):
    serial_number: str | None = None
    result: bool
    failure_details: str | None = None


class BoardTraceBOMItem(SQLModel):
    id: int
    part_number: str
    description: str
    quantity: int
    reference: str | None = None
    status: str


class BoardTraceResponse(SQLModel):
    serial_number: str | None = None
    result: bool
    failure_details: str | None = None
    bom: list[BoardTraceBOMItem]


def get_all_bom() -> list[BOMItem]:
    with Session(engine) as session:
        return session.exec(select(BOMItem)).all()


def get_all_testresults() -> list[TestResult]:
    with Session(engine) as session:
        return session.exec(select(TestResult)).all()


def csv_generator(items: list[BOMItem]):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "part_number", "description", "quantity", "reference"])
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)
    for item in items:
        writer.writerow([
            item.id,
            item.part_number,
            item.description,
            item.quantity,
            item.reference or "",
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)


def excel_bytes(results: list[TestResult]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "results"
    ws.append(
        [
            "test_id",
            "assembly_id",
            "serial_number",
            "date_tested",
            "result",
            "failure_details",
        ]
    )
    for r in results:
        ws.append(
            [
                r.test_id,
                r.assembly_id,
                r.serial_number,
                r.date_tested.isoformat(),
                r.result,
                r.failure_details,
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def nightly_backup(dest: str = "backups") -> None:
    os.makedirs(dest, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d")
    items = get_all_bom()
    with open(os.path.join(dest, f"bom_{stamp}.csv"), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "part_number", "description", "quantity", "reference"])
        for i in items:
            writer.writerow([i.id, i.part_number, i.description, i.quantity, i.reference or ""])
    results = get_all_testresults()
    excel_data = excel_bytes(results)
    with open(os.path.join(dest, f"testresults_{stamp}.xlsx"), "wb") as f:
        f.write(excel_data)


def init_db() -> None:
    """Create database tables if they do not exist."""

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user_exists = session.exec(select(User)).first()
        if not user_exists:
            admin = User(
                username="admin",
                hashed_pw=get_password_hash("change_me"),
                role="admin",
            )
            session.add(admin)
            session.commit()


app = FastAPI()
ui_router = APIRouter(prefix="/ui")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if not user:
            raise credentials_exception
        return user


def admin_required(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return current_user

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
    if not scheduler.get_jobs():
        scheduler.add_job(nightly_backup, "cron", hour=2, minute=0)
    if not scheduler.running:
        scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown()


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


@app.post("/auth/token")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()) -> dict:
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == form_data.username)).first()
        if not user or not verify_password(form_data.password, user.hashed_pw):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token({"sub": user.username, "role": user.role}, access_token_expires)
    return {"access_token": token, "token_type": "bearer"}


@app.post("/auth/register", dependencies=[Depends(admin_required)], status_code=status.HTTP_201_CREATED)
def register_user(user_in: UserCreate) -> dict:
    with Session(engine) as session:
        user = User(username=user_in.username, hashed_pw=get_password_hash(user_in.password), role=user_in.role)
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
        session.refresh(user)
    return {"id": user.id, "username": user.username, "role": user.role}


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
def create_item(item: BOMItemCreate, current_user: User = Depends(get_current_user)) -> BOMItemRead:
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
def replace_item(
    item_id: int,
    item_in: BOMItemCreate,
    current_user: User = Depends(get_current_user),
) -> BOMItemRead:
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
def update_item(
    item_id: int,
    item_in: BOMItemUpdate,
    current_user: User = Depends(get_current_user),
) -> BOMItemRead:
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
def delete_item(item_id: int, current_user: User = Depends(admin_required)) -> None:
    """Delete a BOM item."""

    with Session(engine) as session:
        db_item = session.get(BOMItem, item_id)
        if not db_item:
            raise HTTPException(status_code=404, detail="Item not found")
        session.delete(db_item)
        session.commit()
    return None


@app.post("/bom/import", response_model=list[BOMItemRead])
async def import_bom(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> list[BOMItemRead]:
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
def create_test_result(
    result_in: TestResultCreate,
    current_user: User = Depends(get_current_user),
) -> TestResultRead:
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


@app.get("/export/bom.csv", dependencies=[Depends(admin_required)])
def export_bom_csv():
    items = get_all_bom()
    headers = {"Content-Disposition": "attachment; filename=bom.csv"}
    return StreamingResponse(
        csv_generator(items), media_type="text/csv", headers=headers
    )


@app.get(
    "/export/testresults.xlsx",
    dependencies=[Depends(admin_required)],
)
def export_testresults_xlsx():
    results = get_all_testresults()
    data = excel_bytes(results)
    headers = {
        "Content-Disposition": "attachment; filename=testresults.xlsx"
    }
    return StreamingResponse(
        (chunk for chunk in [data]),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers=headers,
    )


@app.get("/traceability/component/{part_number}", response_model=list[ComponentTraceEntry])
def trace_component(part_number: str):
    with Session(engine) as session:
        data = component_trace(part_number, session)
    if not data:
        raise HTTPException(status_code=404, detail="No failures found for part")
    return data


@app.get("/traceability/board/{serial_number}", response_model=BoardTraceResponse)
def trace_board(serial_number: str):
    with Session(engine) as session:
        data = board_trace(serial_number, session)
    if not data:
        raise HTTPException(status_code=404, detail="Board not found")
    return data


# ---------------------- Web UI -------------------------

@ui_router.get("/", response_class=HTMLResponse)
def ui_dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "title": "Dashboard", "db": DATABASE_URL},
    )


@ui_router.get("/bom/", response_class=HTMLResponse)
def ui_bom(request: Request):
    return templates.TemplateResponse("bom.html", {"request": request, "title": "BOM"})


@ui_router.get("/bom/table", response_class=HTMLResponse)
def ui_bom_table(request: Request):
    items = get_all_bom()
    return templates.TemplateResponse(
        "bom_table.html", {"request": request, "items": items}
    )


@ui_router.post("/bom/create", response_class=HTMLResponse)
async def ui_bom_create(request: Request, current_user: User = Depends(get_current_user)):
    form = await request.form()
    item = BOMItemCreate(
        part_number=form.get("part_number"),
        description=form.get("description"),
        quantity=int(form.get("quantity", 1)),
    )
    with Session(engine) as session:
        db_item = BOMItem.from_orm(item)
        session.add(db_item)
        session.commit()
        session.refresh(db_item)
    return templates.TemplateResponse(
        "bom_table.html",
        {"request": request, "items": [db_item]},
    )


@ui_router.get("/import/", response_class=HTMLResponse)
def ui_import(request: Request):
    return templates.TemplateResponse("import.html", {"request": request, "title": "Import"})


@ui_router.get("/quote/", response_class=HTMLResponse)
def ui_quote(request: Request):
    return templates.TemplateResponse("quote.html", {"request": request, "title": "Quote"})


@ui_router.get("/test/", response_class=HTMLResponse)
def ui_test(request: Request):
    return templates.TemplateResponse("test.html", {"request": request, "title": "Test"})


@ui_router.get("/trace/", response_class=HTMLResponse)
def ui_trace(request: Request):
    return templates.TemplateResponse("trace.html", {"request": request, "title": "Trace"})


@ui_router.get("/export/", response_class=HTMLResponse)
def ui_export(request: Request):
    return templates.TemplateResponse("export.html", {"request": request, "title": "Export"})


@ui_router.get("/users/", response_class=HTMLResponse)
def ui_users(request: Request):
    return templates.TemplateResponse("users.html", {"request": request, "title": "Users"})


@ui_router.get("/settings/", response_class=HTMLResponse)
def ui_settings(request: Request):
    sqlite_mode = "sqlite" in DATABASE_URL
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "title": "Settings",
            "sqlite": sqlite_mode,
            "sqlite_path": DATABASE_URL.removeprefix("sqlite:///") if sqlite_mode else "",
            "pg_url": DATABASE_URL if not sqlite_mode else "",
        },
    )


@ui_router.post("/settings/", response_class=RedirectResponse)
async def ui_save_settings(request: Request):
    form = await request.form()
    mode = form.get("mode")
    if mode == "sqlite":
        path = form.get("sqlite_path") or (BASE_DIR / "bom_dev.db")
        url = f"sqlite:///{path}"
    else:
        url = form.get("pg_url") or DATABASE_URL
    save_database_url(url)
    reload_db(url)
    return RedirectResponse("/ui/settings/", status_code=303)


app.include_router(ui_router)


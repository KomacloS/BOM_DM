from __future__ import annotations
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse
from sqlmodel import SQLModel, Session, select
import csv
import io

from .constants import BOM_TEMPLATE_HEADERS

from .database import engine, get_session, ensure_schema
from .models import Customer, Project, Assembly, Part, Task, TaskStatus, User
from .services import (
    import_bom,
    ImportReport,
    create_customer as svc_create_customer,
    create_project as svc_create_project,
    create_assembly as svc_create_assembly,
    list_tasks as svc_list_tasks,
    list_bom_items as svc_list_bom_items,
    BOMItemRead,
)
from .auth import (
    get_current_user,
    authenticate_user,
    create_access_token,
    create_default_users,
)

app = FastAPI()


@app.on_event("startup")
def on_startup():
    ensure_schema()
    with Session(engine) as session:
        create_default_users(session)


@app.get("/hello")
def hello():
    return {"message": "hello"}


@app.post("/auth/token")
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    user = authenticate_user(session, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    access_token = create_access_token({"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/auth/me")
def read_users_me(current_user: User = Depends(get_current_user)):
    return {"username": current_user.username, "role": current_user.role}


@app.get("/bom/template")
def bom_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(BOM_TEMPLATE_HEADERS)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bom_template.csv"},
    )


@app.post("/customers", response_model=Customer)
def create_customer(
    customer: Customer,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return svc_create_customer(customer.name, customer.contact_email, session)


@app.post("/customers/{customer_id}/projects", response_model=Project)
def create_project(
    customer_id: int,
    project: Project,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return svc_create_project(
        customer_id,
        project.code,
        project.title,
        project.priority.value,
        project.due_at,
        session,
    )


@app.post("/projects/{project_id}/assemblies", response_model=Assembly)
def create_assembly(
    project_id: int,
    assembly: Assembly,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return svc_create_assembly(project_id, assembly.rev, assembly.notes, session)


@app.post("/parts", response_model=Part)
def create_part(
    part: Part,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    session.add(part)
    session.commit()
    session.refresh(part)
    return part


@app.post("/assemblies/{assembly_id}/bom/import", response_model=ImportReport)
def import_bom_endpoint(
    assembly_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    data = file.file.read()
    report = import_bom(assembly_id, data, session)
    if report.errors:
        raise HTTPException(status_code=422, detail=report.errors)
    return report


@app.get("/assemblies/{assembly_id}/bom/items", response_model=list[BOMItemRead])
def list_bom_items(
    assembly_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return svc_list_bom_items(assembly_id, session)


@app.get("/projects/{project_id}/tasks", response_model=list[Task])
def list_tasks(
    project_id: int,
    status: TaskStatus | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    status_val = status.value if status else None
    return svc_list_tasks(project_id, status_val, session)

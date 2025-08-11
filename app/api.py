from __future__ import annotations
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse
from sqlmodel import SQLModel, Session, select
import csv
import io

from .database import engine, get_session
from .models import Customer, Project, Assembly, Part, BOMItem, Task, TaskStatus, User
from .services import import_bom, ImportReport
from .auth import (
    get_current_user,
    authenticate_user,
    create_access_token,
    create_default_users,
)

app = FastAPI()


@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)
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
    from .bom_schema import ALLOWED_HEADERS
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(ALLOWED_HEADERS)
    output.seek(0)
    return StreamingResponse(
        output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=bom_template.csv"}
    )


@app.post("/customers", response_model=Customer)
def create_customer(
    customer: Customer,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


@app.post("/customers/{customer_id}/projects", response_model=Project)
def create_project(
    customer_id: int,
    project: Project,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    project.customer_id = customer_id
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@app.post("/projects/{project_id}/assemblies", response_model=Assembly)
def create_assembly(
    project_id: int,
    assembly: Assembly,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    assembly.project_id = project_id
    session.add(assembly)
    session.commit()
    session.refresh(assembly)
    return assembly


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
    report = import_bom(session, assembly_id, data)
    if report.errors:
        raise HTTPException(status_code=422, detail=report.errors)
    return report


@app.get("/assemblies/{assembly_id}/bom/items", response_model=list[BOMItem])
def list_bom_items(
    assembly_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return session.exec(select(BOMItem).where(BOMItem.assembly_id == assembly_id)).all()


@app.get("/projects/{project_id}/tasks", response_model=list[Task])
def list_tasks(
    project_id: int,
    status: TaskStatus | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    query = select(Task).where(Task.project_id == project_id)
    if status:
        query = query.where(Task.status == status)
    return session.exec(query).all()

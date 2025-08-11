from sqlalchemy.pool import StaticPool
from pathlib import Path
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session

from app.api import app, get_session
from app.models import Customer, Project, Assembly, Part, User, UserRole
from app import auth


def setup_test_db():
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def test_api_import_and_list():
    engine = setup_test_db()

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[auth.get_current_user] = lambda: User(
        id=1, username="tester", hashed_password="", role=UserRole.admin
    )
    client = TestClient(app)

    # create customer/project/assembly/parts
    resp = client.post("/customers", json={"name": "Cust"})
    cust_id = resp.json()["id"]
    resp = client.post(f"/customers/{cust_id}/projects", json={"code": "PRJ", "title": "Proj"})
    proj_id = resp.json()["id"]
    resp = client.post(f"/projects/{proj_id}/assemblies", json={"rev": "A"})
    asm_id = resp.json()["id"]
    client.post("/parts", json={"part_number": "P1", "description": "Known1"})
    client.post("/parts", json={"part_number": "P2", "description": "Known2"})

    csv_bytes = Path("tests/fixtures/sample_bom.csv").read_bytes()
    files = {"file": ("bom.csv", csv_bytes, "text/csv")}
    report = client.post(f"/assemblies/{asm_id}/bom/import", files=files)
    assert report.status_code == 200
    data = report.json()
    assert data["matched"] == 2
    items = client.get(f"/assemblies/{asm_id}/bom/items").json()
    assert len(items) == 3
    tasks = client.get(f"/projects/{proj_id}/tasks").json()
    assert len(tasks) == 1


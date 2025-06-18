import os, sys, sqlalchemy, pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.main as main

@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    with TestClient(main.app) as c:
        yield c

@pytest.fixture
def auth_header(client):
    token = client.post("/auth/token", data={"username": "admin", "password": "change_me"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_project_csv(client, auth_header):
    cust = client.post("/customers", json={"name": "C"}).json()
    proj = client.post("/projects", json={"customer_id": cust["id"], "name": "P"}).json()
    for pn in ("P1", "P2"):
        client.post(
            "/bom/items",
            json={"part_number": pn, "description": "d", "quantity": 1, "project_id": proj["id"]},
            headers=auth_header,
        )
    r = client.get(f"/projects/{proj['id']}/export.csv")
    assert r.status_code == 200
    lines = r.text.splitlines()
    assert "part_number" in lines[0]
    assert len(lines) == 3


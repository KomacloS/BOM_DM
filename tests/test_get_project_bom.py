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
    token = client.post(
        "/auth/token",
        data={"username": "admin", "password": "change_me"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_get_project_bom(client, auth_header):
    cust = client.post("/customers", json={"name": "C"}).json()
    proj = client.post("/projects", json={"customer_id": cust["id"], "name": "P"}).json()
    item1 = client.post(
        "/bom/items",
        json={"part_number": "P1", "description": "D1", "quantity": 1, "project_id": proj["id"]},
        headers=auth_header,
    ).json()
    item2 = client.post(
        "/bom/items",
        json={"part_number": "P2", "description": "D2", "quantity": 2, "project_id": proj["id"]},
        headers=auth_header,
    ).json()
    r = client.get(f"/projects/{proj['id']}/bom")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert {item1['id'], item2['id']} == {d['id'] for d in data}

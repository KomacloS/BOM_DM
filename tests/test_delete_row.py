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
        data={"username": "admin", "password": "123456789"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_delete_row(client, auth_header):
    item = client.post(
        "/bom/items",
        json={"part_number": "D", "description": "x", "quantity": 1},
        headers=auth_header,
    ).json()
    r = client.delete(f"/bom/items/{item['id']}", headers=auth_header)
    assert r.status_code == 204
    all_items = client.get("/bom/items", headers=auth_header).json()
    assert all(i["id"] != item["id"] for i in all_items)

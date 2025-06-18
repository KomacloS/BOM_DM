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

def test_big_datasheet(client, auth_header):
    item = client.post(
        "/bom/items",
        json={"part_number": "B", "description": "y", "quantity": 1},
        headers=auth_header,
    ).json()
    big = b"0" * (11 * 1024 * 1024)
    r = client.post(
        f"/bom/items/{item['id']}/datasheet",
        files={"file": ("big.pdf", big, "application/pdf")},
        headers=auth_header,
    )
    assert r.status_code == 413
    assert "exceeds" in r.json()["detail"]

import sqlalchemy
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.main as main
import pytest


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
    token = client.post("/auth/token", data={"username": "admin", "password": "123456789"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_part_deduplication(client, auth_header):
    payload1 = {"part_number": "ABC", "description": "Part A", "quantity": 1}
    payload2 = {"part_number": "abc", "description": "Part A", "quantity": 2}
    r1 = client.post("/bom/items", json=payload1, headers=auth_header)
    r2 = client.post("/bom/items", json=payload2, headers=auth_header)
    assert r1.status_code == 201
    assert r2.status_code == 201
    parts = client.get("/parts", headers=auth_header).json()
    assert len(parts) == 1
    part_id = parts[0]["id"]
    assert r1.json()["part_id"] == part_id
    assert r2.json()["part_id"] == part_id

    item2_id = r2.json()["id"]
    patch = client.patch(f"/bom/items/{item2_id}", json={"part_number": "XYZ"}, headers=auth_header)
    assert patch.status_code == 200
    parts_after = client.get("/parts", headers=auth_header).json()
    assert len(parts_after) == 2
    old_part_id = r1.json()["part_id"]
    new_part_id = patch.json()["part_id"]
    assert old_part_id != new_part_id
    assert patch.json()["part_id"] != old_part_id
    assert client.get(f"/bom/items/{r1.json()['id']}", headers=auth_header).json()["part_id"] == old_part_id



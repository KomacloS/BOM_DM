# root: tests/test_bom.py
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import sqlalchemy
import pytest
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.main as main


@pytest.fixture(name="client")
def client_fixture():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    main.engine = test_engine
    SQLModel.metadata.create_all(test_engine)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def auth_header(client):
    resp = client.post(
        "/auth/token",
        data={"username": "admin", "password": "123456789"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_item(client, auth_header):
    payload = {
        "part_number": "ABC-123",
        "description": "10 uF Cap",
        "quantity": 2,
    }
    response = client.post("/bom/items", json=payload, headers=auth_header)
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 1
    assert data["part_number"] == payload["part_number"]
    assert data["description"] == payload["description"]
    assert data["quantity"] == payload["quantity"]
    assert data["reference"] is None


def test_list_items(client, auth_header):
    item = {"part_number": "XYZ-1", "description": "Resistor", "quantity": 1}
    client.post("/bom/items", json=item, headers=auth_header)

    unauthorized = client.get("/bom/items")
    assert unauthorized.status_code == 401

    response = client.get("/bom/items", headers=auth_header)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(i["part_number"] == item["part_number"] for i in data)

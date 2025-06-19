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
        data={"username": "admin", "password": "change_me"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create_sample_items(client, auth_header):
    items = [
        {"part_number": "PN1", "description": "Resistor", "quantity": 2, "reference": "R1"},
        {"part_number": "PN2", "description": "Capacitor", "quantity": 5, "reference": "C1"},
        {"part_number": "FINDME", "description": "Special", "quantity": 1, "reference": "U1"},
    ]
    for item in items:
        client.post("/bom/items", json=item, headers=auth_header)
    return items


def test_crud_lifecycle(client, auth_header):
    payload = {"part_number": "PN10", "description": "Widget", "quantity": 3, "reference": "X1"}
    create_resp = client.post("/bom/items", json=payload, headers=auth_header)
    assert create_resp.status_code == 201
    item_id = create_resp.json()["id"]

    # GET
    get_resp = client.get(f"/bom/items/{item_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["part_number"] == payload["part_number"]

    # PUT
    replacement = {"part_number": "PN10", "description": "Widget v2", "quantity": 4, "reference": "X1"}
    put_resp = client.put(f"/bom/items/{item_id}", json=replacement, headers=auth_header)
    assert put_resp.status_code == 200
    assert put_resp.json()["description"] == "Widget v2"

    # PATCH
    patch_resp = client.patch(f"/bom/items/{item_id}", json={"quantity": 6}, headers=auth_header)
    assert patch_resp.status_code == 200
    assert patch_resp.json()["quantity"] == 6

    # DELETE
    del_resp = client.delete(f"/bom/items/{item_id}", headers=auth_header)
    assert del_resp.status_code == 204
    get_after = client.get(f"/bom/items/{item_id}")
    assert get_after.status_code == 404


def test_list_search_pagination(client, auth_header):
    items = create_sample_items(client, auth_header)
    resp = client.get("/bom/items", params={"search": "find", "limit": 2}, headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["part_number"] == "FINDME"

    # pagination
    resp_all = client.get("/bom/items", params={"skip": 1, "limit": 1}, headers=auth_header)
    assert resp_all.status_code == 200
    assert len(resp_all.json()) == 1


def test_duplicate_insert(client, auth_header):
    item = {"part_number": "DUP", "description": "Dup Item", "quantity": 1, "reference": "R99"}
    r1 = client.post("/bom/items", json=item, headers=auth_header)
    assert r1.status_code == 201
    r2 = client.post("/bom/items", json=item, headers=auth_header)
    assert r2.status_code == 409


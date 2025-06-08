import sqlalchemy
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.main as main

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine


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


def seed_data(client, auth_header):
    items = [
        {"part_number": "PA", "description": "Part A", "quantity": 1, "reference": "R1"},
        {"part_number": "PB", "description": "Part B", "quantity": 1, "reference": "C1"},
    ]
    for item in items:
        client.post("/bom/items", json=item, headers=auth_header)

    fails = [
        {"serial_number": "SN1", "result": False, "failure_details": "R1 short"},
        {"serial_number": "SN2", "result": False, "failure_details": "PB bad"},
    ]
    for fr in fails:
        client.post("/testresults", json=fr, headers=auth_header)



def test_component_trace(client, auth_header):
    seed_data(client, auth_header)
    resp = client.get("/traceability/component/PB")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["serial_number"] == "SN2"

    resp2 = client.get("/traceability/component/PA")
    assert resp2.status_code == 200
    assert resp2.json()[0]["serial_number"] == "SN1"


def test_board_trace(client, auth_header):
    seed_data(client, auth_header)
    resp = client.get("/traceability/board/SN2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["serial_number"] == "SN2"
    statuses = {i["part_number"]: i["status"] for i in body["bom"]}
    assert statuses["PB"] == "FAIL"
    assert statuses["PA"] == "OK"

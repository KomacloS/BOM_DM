# root: tests/test_testresults.py
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


def test_create_test_result(client, auth_header):
    cust = client.post('/customers', json={'name':'C'}).json()
    proj = client.post('/projects', json={'customer_id':cust['id'], 'name':'P'}).json()
    aid = client.get(f"/projects/{proj['id']}/assemblies").json()[0]['id']
    payload = {"serial_number": "SN1", "result": True, "assembly_id": aid}
    resp = client.post("/testresults", json=payload, headers=auth_header)
    assert resp.status_code == 201
    data = resp.json()
    assert data["test_id"] == 1
    assert data["serial_number"] == "SN1"
    assert data["assembly_id"] == aid
    assert data["result"] is True
    assert data["failure_details"] is None


def test_list_and_get_results(client, auth_header):
    cust = client.post('/customers', json={'name':'C'}).json()
    proj = client.post('/projects', json={'customer_id':cust['id'], 'name':'P'}).json()
    aid = client.get(f"/projects/{proj['id']}/assemblies").json()[0]['id']
    r1 = client.post("/testresults", json={"serial_number": "SN2", "result": True, "assembly_id": aid}, headers=auth_header)
    r2 = client.post(
        "/testresults",
        json={"serial_number": "SN3", "result": False, "failure_details": "bad", "assembly_id": aid},
        headers=auth_header,
    )
    assert r1.status_code == 201
    assert r2.status_code == 201

    list_resp = client.get("/testresults")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert len(data) == 2

    single = client.get("/testresults/1")
    assert single.status_code == 200
    assert single.json()["serial_number"] == "SN2"

    missing = client.get("/testresults/999")
    assert missing.status_code == 404

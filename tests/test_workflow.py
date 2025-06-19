import sqlalchemy
import pytest
import os
import sys
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

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
    token = client.post(
        "/auth/token",
        data={"username": "admin", "password": "change_me"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_customer_project_and_save(client, auth_header):
    c = client.post("/ui/workflow/customers", json={"name": "Acme"})
    assert c.status_code == 201
    cid = c.json()["id"]
    plist = client.post("/ui/workflow/projects", json={"customer_id": cid, "name": "Proj"})
    assert plist.status_code == 201
    pid = plist.json()["id"]
    items = [{"part_number": "P1", "description": "A", "quantity": 1}]
    save = client.post("/ui/workflow/save", json={"project_id": pid, "items": items})
    assert save.status_code == 200
    all_items = client.get("/bom/items", headers=auth_header).json()
    assert any(i["part_number"] == "P1" for i in all_items)


def test_customer_update_delete(client):
    r = client.post("/ui/workflow/customers", json={"name": "Temp"})
    cid = r.json()["id"]
    upd = client.patch(f"/ui/workflow/customers/{cid}", json={"contact": "c"})
    assert upd.status_code == 200
    assert upd.json()["contact"] == "c"
    del_r = client.delete(f"/ui/workflow/customers/{cid}")
    assert del_r.status_code == 204
    customers = client.get("/ui/workflow/customers").json()
    assert all(c["id"] != cid for c in customers)


def test_project_update_delete(client):
    cust = client.post("/ui/workflow/customers", json={"name": "C2"}).json()
    proj = client.post("/ui/workflow/projects", json={"customer_id": cust["id"], "name": "P"}).json()
    pid = proj["id"]
    upd = client.patch(f"/ui/workflow/projects/{pid}", json={"description": "d"})
    assert upd.status_code == 200
    assert upd.json()["description"] == "d"
    del_r = client.delete(f"/ui/workflow/projects/{pid}")
    assert del_r.status_code == 204
    projs = client.get("/ui/workflow/projects", params={"customer_id": cust["id"]}).json()
    assert projs == []



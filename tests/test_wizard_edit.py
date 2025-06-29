import os, sys
import sqlalchemy
import pytest
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


def get_token(client):
    return client.post(
        "/auth/token", data={"username": "admin", "password": "123456789"}
    ).json()["access_token"]


def test_edit_quantity_after_save(client):
    token = get_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    cust = client.post("/ui/workflow/customers", json={"name": "C"}, headers=headers).json()
    proj = client.post(
        "/ui/workflow/projects",
        json={"customer_id": cust["id"], "name": "P"},
        headers=headers,
    ).json()
    csv = b"part_number,description,quantity\nP1,D,1\n"
    upload = client.post(
        "/ui/workflow/upload", files={"file": ("bom.csv", csv, "text/csv")}, headers=headers
    )
    items = upload.json()
    aid = client.get(f"/projects/{proj['id']}/assemblies", headers=headers).json()[0]['id']
    save = client.post(
        "/ui/workflow/save", json={"assembly_id": aid, "items": items}, headers=headers
    )
    item = save.json()[0]
    token = get_token(client)
    r = client.patch(
        f"/bom/items/{item['id']}",
        json={"quantity": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["quantity"] == 3

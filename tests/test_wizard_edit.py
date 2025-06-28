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
        "/auth/token", data={"username": "admin", "password": "change_me"}
    ).json()["access_token"]


def test_edit_quantity_after_save(client):
    cust = client.post("/ui/workflow/customers", json={"name": "C"}).json()
    proj = client.post(
        "/ui/workflow/projects",
        json={"customer_id": cust["id"], "name": "P"},
    ).json()
    csv = b"part_number,description,quantity\nP1,D,1\n"
    upload = client.post(
        "/ui/workflow/upload", files={"file": ("bom.csv", csv, "text/csv")}
    )
    items = upload.json()
    aid = client.get(f"/projects/{proj['id']}/assemblies").json()[0]['id']
    save = client.post(
        "/ui/workflow/save", json={"assembly_id": aid, "items": items}
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

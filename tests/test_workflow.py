import sqlalchemy
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

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


def test_customer_project_and_save(client):
    token = client.post(
        "/auth/token", data={"username": "admin", "password": "change_me"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    c = client.post("/ui/workflow/customers", json={"name": "Acme"}, headers=headers)
    assert c.status_code == 201
    cid = c.json()["id"]

    plist = client.post(
        "/ui/workflow/projects", json={"customer_id": cid, "name": "Proj"}, headers=headers
    )
    assert plist.status_code == 201
    pid = plist.json()["id"]

    items = [{"part_number": "P1", "description": "A", "quantity": 1}]
    save = client.post(
        "/ui/workflow/save",
        json={"project_id": pid, "items": items},
        headers=headers,
    )
    assert save.status_code == 200
    all_items = client.get("/bom/items").json()
    assert any(i["part_number"] == "P1" for i in all_items)


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

@pytest.fixture
def auth_header(client):
    token = client.post(
        "/auth/token",
        data={"username": "admin", "password": "change_me"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_upload_datasheet(client, auth_header, tmp_path):
    cust = client.post("/customers", json={"name": "C"}).json()
    proj = client.post("/projects", json={"customer_id": cust["id"], "name": "P"}).json()
    item = client.post(
        "/bom/items",
        json={"part_number": "P1", "description": "D", "quantity": 1, "project_id": proj["id"]},
        headers=auth_header,
    ).json()
    pdf_bytes = b"%PDF-1.3\n%%EOF"
    r = client.post(
        f"/bom/items/{item['id']}/datasheet",
        files={"file": ("ds.pdf", pdf_bytes, "application/pdf")},
        headers=auth_header,
    )
    assert r.status_code == 200
    url = r.json()["datasheet_url"]
    assert url.startswith("/datasheets/")
    get_r = client.get(url)
    assert get_r.status_code == 200

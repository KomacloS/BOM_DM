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
        data={"username": "admin", "password": "123456789"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_upload_datasheet(client, auth_header, tmp_path):
    cust = client.post("/customers", json={"name": "C"}).json()
    proj = client.post("/projects", json={"customer_id": cust["id"], "name": "P"}, headers=auth_header).json()
    aid = client.get(f"/projects/{proj['id']}/assemblies", headers=auth_header).json()[0]['id']
    item = client.post(
        "/bom/items",
        json={"part_number": "P1", "description": "D", "quantity": 1, "assembly_id": aid},
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

def test_replace_datasheet(client, auth_header):
    cust = client.post("/customers", json={"name": "C2"}).json()
    proj = client.post("/projects", json={"customer_id": cust["id"], "name": "P"}, headers=auth_header).json()
    aid = client.get(f"/projects/{proj['id']}/assemblies", headers=auth_header).json()[0]['id']
    item = client.post(
        "/bom/items",
        json={"part_number": "P2", "description": "D", "quantity": 1, "assembly_id": aid},
        headers=auth_header,
    ).json()
    pdf1 = b"%PDF-1.3\n%%EOF"
    client.post(
        f"/bom/items/{item['id']}/datasheet",
        files={"file": ("a.pdf", pdf1, "application/pdf")},
        headers=auth_header,
    )
    pdf2 = b"%PDF-1.4\n%%EOF"
    r = client.post(
        f"/bom/items/{item['id']}/datasheet",
        files={"file": ("b.pdf", pdf2, "application/pdf")},
        headers=auth_header,
    )
    assert r.status_code == 200
    url = r.json()["datasheet_url"]
    assert url.endswith("/b.pdf")
    with open(os.path.join("datasheets", str(item["id"]), "b.pdf"), "rb") as f:
        assert f.read() == pdf2

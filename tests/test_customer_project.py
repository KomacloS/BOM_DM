import os
import sys
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


def test_link_items_to_project(client, auth_header):
    cust = client.post("/customers", json={"name": "Acme"}).json()
    proj = client.post(
        "/projects",
        json={"customer_id": cust["id"], "name": "Widget"},
        headers=auth_header,
    ).json()
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PNX    Part    1    R1")
    pdf_bytes = doc.tobytes()
    aid = client.get(f"/projects/{proj['id']}/assemblies", headers=auth_header).json()[0]['id']
    resp = client.post(
        f"/bom/import?assembly_id={aid}",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
        headers=auth_header,
    )
    assert resp.status_code == 200
    items = client.get("/bom/items", headers=auth_header).json()
    assert items[0]["assembly_id"] == aid
    assert len(items) > 0
    client.delete(f"/customers/{cust['id']}")
    assert client.get("/bom/items", headers=auth_header).json() == []


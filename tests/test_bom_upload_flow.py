import sqlalchemy
import os, sys
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.main as main


@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=sqlalchemy.pool.StaticPool)
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def auth_header(client):
    token = client.post("/auth/token", data={"username": "admin", "password": "change_me"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_bom_upload_and_list(client, auth_header):
    cust = client.post("/customers", json={"name": "AC"}).json()
    proj = client.post("/projects", json={"customer_id": cust["id"], "name": "P"}).json()
    aid = client.get(f"/projects/{proj['id']}/assemblies").json()[0]['id']

    csv_data = "part_number,description,quantity\nP1,Res,1\nP2,Cap,2\n"
    files = {"file": ("bom.csv", csv_data, "text/csv")}
    resp = client.post(f"/bom/import?assembly_id={aid}", files=files, headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(item["assembly_id"] == aid for item in data)

    r = client.get(f"/assemblies/{aid}/bom-items")
    assert r.status_code == 200
    assert len(r.json()) == 2

import sqlalchemy
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.main as main
import pytest

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
    token = client.post("/auth/token", data={"username": "admin", "password": "change_me"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_usage_count_and_linking(client, auth_header):
    macro = client.post("/testmacros", json={"name": "M"}, headers=auth_header).json()
    p1 = client.post("/parts", json={"number": "P1"}, headers=auth_header).json()
    p2 = client.post("/parts", json={"number": "P2"}, headers=auth_header).json()
    client.post(f"/parts/{p1['id']}/testmacros", json={"test_macro_id": macro['id']}, headers=auth_header)
    client.post(f"/parts/{p2['id']}/testmacros", json={"test_macro_id": macro['id']}, headers=auth_header)
    macros = client.get("/testmacros", headers=auth_header).json()
    assert macros[0]["usage_count"] == 2
    client.delete(f"/parts/{p1['id']}/testmacros/{macro['id']}", headers=auth_header)
    macros = client.get("/testmacros", headers=auth_header).json()
    assert macros[0]["usage_count"] == 1


def test_auto_attach_continuity(client, auth_header):
    part = client.post("/parts", json={"number": "U123"}, headers=auth_header).json()
    macros = client.get(f"/parts/{part['id']}/testmacros", headers=auth_header).json()
    names = [m["name"] for m in macros]
    assert "Continuity" in names

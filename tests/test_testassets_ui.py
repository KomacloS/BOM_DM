import sqlalchemy, pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import os, sys
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
    token = client.post("/auth/token", data={"username": "admin", "password": "123456789"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_table_and_detail(client, auth_header):
    macro = client.post("/testmacros", json={"name": "M"}, headers=auth_header).json()
    p1 = client.post("/parts", json={"number": "P1"}, headers=auth_header).json()
    p2 = client.post("/parts", json={"number": "P2"}, headers=auth_header).json()
    client.post(f"/parts/{p1['id']}/testmacros", json={"test_macro_id": macro['id']}, headers=auth_header)
    client.post(f"/parts/{p2['id']}/testmacros", json={"test_macro_id": macro['id']}, headers=auth_header)

    r = client.get("/ui/testassets/table", headers=auth_header)
    assert r.status_code == 200
    assert "<tbody" in r.text
    assert "M" in r.text
    assert "2" in r.text

    data = b"glTF"
    up = client.post(
        f"/testmacros/{macro['id']}/upload_glb",
        files={"file": ("m.glb", data, "model/gltf-binary")},
        headers=auth_header,
    )
    assert up.status_code == 200

    detail = client.get(f"/ui/testassets/detail/{macro['id']}", headers=auth_header)
    assert detail.status_code == 200
    assert "<model-viewer" in detail.text

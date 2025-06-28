import os, sys, sqlalchemy, pytest
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
    token = client.post("/auth/token", data={"username": "admin", "password": "change_me"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_upload_and_cleanup(client, auth_header):
    macro = client.post("/testmacros", json={"name": "M"}, headers=auth_header).json()
    data = b"dummy"
    r = client.post(
        f"/testmacros/{macro['id']}/upload_glb",
        files={"file": ("m.glb", data, "model/gltf-binary")},
        headers=auth_header,
    )
    assert r.status_code == 200
    glb_path = r.json()["glb_path"]
    assert glb_path.startswith("assets/")
    assert os.path.exists(glb_path)
    sha = os.path.basename(glb_path).split(".")[0]
    with main.Session(main.engine) as session:
        assert session.get(main.Blob, sha) is not None

    macro2 = client.post("/testmacros", json={"name": "N"}, headers=auth_header).json()
    r2 = client.post(
        f"/testmacros/{macro2['id']}/upload_glb",
        files={"file": ("m.glb", data, "model/gltf-binary")},
        headers=auth_header,
    )
    assert r2.status_code == 200

    client.delete(f"/testmacros/{macro['id']}", headers=auth_header)
    assert os.path.exists(glb_path)
    client.delete(f"/testmacros/{macro2['id']}", headers=auth_header)
    assert not os.path.exists(glb_path)

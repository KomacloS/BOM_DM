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
    token = client.post("/auth/token", data={"username": "admin", "password": "change_me"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_complex_py_tables(client, auth_header):
    cplx = client.post("/complexes", json={"name": "Board"}, headers=auth_header).json()
    pyt = client.post("/pythontests", json={"name": "Test"}, headers=auth_header).json()
    part = client.post("/parts", json={"number": "P1"}, headers=auth_header).json()
    # upload dummy files
    client.post(f"/complexes/{cplx['id']}/upload_eda", files={"file": ("b.zip", b"ZIP", "application/zip")}, headers=auth_header)
    client.post(f"/pythontests/{pyt['id']}/upload_file", files={"file": ("t.py", b"print()", "text/x-python")}, headers=auth_header)
    r = client.get("/ui/testassets/table?kind=complex")
    assert r.status_code == 200
    assert "<a" in r.text and "Board" in r.text
    r2 = client.get(f"/ui/testassets/detail/py/{pyt['id']}")
    assert "<pre><code" in r2.text

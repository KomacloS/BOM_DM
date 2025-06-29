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


def test_asset_links(client, auth_header):
    part = client.post("/parts", json={"number": "P1"}, headers=auth_header).json()
    cplx = client.post("/complexes", json={"name": "Board"}, headers=auth_header).json()
    pyt = client.post("/pythontests", json={"name": "Test"}, headers=auth_header).json()

    client.post(f"/parts/{part['id']}/complexes", json={"complex_id": cplx['id']}, headers=auth_header)
    client.post(f"/parts/{part['id']}/pythontests", json={"pythontest_id": pyt['id']}, headers=auth_header)

    assert client.get(f"/complexes/{cplx['id']}/parts", headers=auth_header).json()[0]['id'] == part['id']
    assert client.get(f"/pythontests/{pyt['id']}/parts", headers=auth_header).json()[0]['id'] == part['id']

    d1 = client.get(f"/ui/testassets/detail/complex/{cplx['id']}", headers=auth_header)
    assert "P1" in d1.text
    d2 = client.get(f"/ui/testassets/detail/py/{pyt['id']}", headers=auth_header)
    assert "P1" in d2.text

    client.delete(f"/parts/{part['id']}/complexes/{cplx['id']}", headers=auth_header)
    client.delete(f"/parts/{part['id']}/pythontests/{pyt['id']}", headers=auth_header)

    assert client.get(f"/complexes/{cplx['id']}/parts", headers=auth_header).json() == []
    assert client.get(f"/pythontests/{pyt['id']}/parts", headers=auth_header).json() == []

    # cascade
    client.post(f"/parts/{part['id']}/complexes", json={"complex_id": cplx['id']}, headers=auth_header)
    client.post(f"/parts/{part['id']}/pythontests", json={"pythontest_id": pyt['id']}, headers=auth_header)
    client.delete(f"/parts/{part['id']}", headers=auth_header)
    assert client.get(f"/complexes/{cplx['id']}/parts", headers=auth_header).json() == []
    assert client.get(f"/pythontests/{pyt['id']}/parts", headers=auth_header).json() == []

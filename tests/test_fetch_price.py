import sqlalchemy
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import app.main as main


@pytest.fixture(name='client')
def client_fixture():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=sqlalchemy.pool.StaticPool)
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    with TestClient(main.app) as c:
        yield c

@pytest.fixture
def auth_header(client):
    token = client.post('/auth/token', data={'username':'admin','password':'change_me'}).json()['access_token']
    return {'Authorization': f'Bearer {token}'}


def test_fetch_price_success(client, auth_header):
    item = client.post('/bom/items', json={'part_number':'P','description':'d','quantity':1,'mpn':'KNOWN'}, headers=auth_header).json()
    r = client.post(f"/bom/items/{item['id']}/fetch_price", json={'source':'octopart'}, headers=auth_header)
    assert r.status_code == 200
    assert r.json()['unit_cost'] == pytest.approx(0.42)


def test_fetch_price_404(client, auth_header):
    r = client.post('/bom/items/99/fetch_price', json={'source':'octopart'}, headers=auth_header)
    assert r.status_code == 404

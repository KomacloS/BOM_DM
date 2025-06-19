import sqlalchemy
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import app.main as main

@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=sqlalchemy.pool.StaticPool)
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    with TestClient(main.app) as c:
        yield c

@pytest.fixture
def admin_header(client):
    token = client.post('/auth/token', data={'username':'admin','password':'change_me'}).json()['access_token']
    return {'Authorization': f'Bearer {token}'}

def test_operator_cannot_patch(client, admin_header):
    client.post('/auth/register', json={'username':'op','password':'pw','role':'operator'}, headers=admin_header)
    op_token = client.post('/auth/token', data={'username':'op','password':'pw'}).json()['access_token']
    op_header = {'Authorization': f'Bearer {op_token}'}
    item = client.post('/bom/items', json={'part_number':'P1','description':'d','quantity':1}, headers=admin_header).json()
    r = client.patch(f"/bom/items/{item['id']}", json={'quantity':2}, headers=op_header)
    assert r.status_code == 403


def test_operator_can_fetch_price(client, admin_header):
    client.post('/auth/register', json={'username':'op','password':'pw','role':'operator'}, headers=admin_header)
    op_token = client.post('/auth/token', data={'username':'op','password':'pw'}).json()['access_token']
    op_header = {'Authorization': f'Bearer {op_token}'}
    item = client.post('/bom/items', json={'part_number':'P1','description':'d','quantity':1,'mpn':'KNOWN'}, headers=admin_header).json()
    r = client.post(f"/bom/items/{item['id']}/fetch_price", json={'source':'octopart'}, headers=op_header)
    assert r.status_code == 200

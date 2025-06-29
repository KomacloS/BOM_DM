import sqlalchemy, pytest
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
    token = client.post('/auth/token', data={'username':'admin','password':'123456789'}).json()['access_token']
    return {'Authorization': f'Bearer {token}'}

def test_project_quote_endpoint(client, auth_header):
    cust = client.post('/customers', json={'name':'C'}).json()
    proj = client.post('/projects', json={'customer_id':cust['id'], 'name':'P'}, headers=auth_header).json()
    aid = client.get(f"/projects/{proj['id']}/assemblies", headers=auth_header).json()[0]['id']
    client.post('/bom/items', json={'part_number':'A','description':'d','quantity':2,'unit_cost':0.5,'assembly_id':aid}, headers=auth_header)
    client.post('/bom/items', json={'part_number':'B','description':'d','quantity':3,'unit_cost':1,'assembly_id':aid}, headers=auth_header)
    r = client.get(f"/projects/{proj['id']}/quote", headers=auth_header)
    assert r.status_code == 200
    d = r.json()
    assert d['total_components'] == 5
    assert d['parts_cost'] == pytest.approx(2*0.5+3*1, rel=1e-2)

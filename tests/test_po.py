import sqlalchemy, pytest, time
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


def test_po_pdf_updates_inventory(client, auth_header):
    cust = client.post('/customers', json={'name':'C'}).json()
    proj = client.post('/projects', json={'customer_id':cust['id'], 'name':'P'}).json()
    client.post('/inventory', json={'mpn':'X', 'on_hand':5, 'on_order':0}, headers=auth_header)
    aid = client.get(f"/projects/{proj['id']}/assemblies").json()[0]['id']
    client.post('/bom/items', json={'part_number':'A','description':'d','quantity':2,'mpn':'X','unit_cost':1,'assembly_id':aid}, headers=auth_header)
    r = client.post(f"/projects/{proj['id']}/po.pdf", headers=auth_header)
    assert r.status_code == 200
    assert r.content.startswith(b'%PDF')
    inv = client.get('/inventory').json()[0]
    assert inv['on_hand'] == 3
    assert inv['on_order'] == 2


def test_fx_cache(monkeypatch):
    import app.fx as fx
    calls = []
    def fake_today():
        calls.append(1)
        return {'USD':1.0,'EUR':0.9}
    monkeypatch.setattr(main.fixer, 'today', fake_today)
    start = time.time()
    assert fx.get('EUR') == 0.9
    assert fx.get('EUR') == 0.9
    assert time.time()-start < 1
    assert len(calls) == 1

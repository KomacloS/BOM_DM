import os, sys
import sqlalchemy
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.main as main

@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=sqlalchemy.pool.StaticPool)
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    with TestClient(main.app) as c:
        yield c

def test_editor_cannot_import_bom(client):
    token = client.post('/auth/token', data={'username':'ed','password':'123456789'}).json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    cust = client.post('/customers', json={'name':'C'}).json()
    proj = client.post('/projects', json={'customer_id': cust['id'], 'name':'P'}, headers=headers).json()
    aid = client.get(f"/projects/{proj['id']}/assemblies", headers=headers).json()[0]['id']
    csv_data = 'part_number,description,quantity\nP1,R,1\n'
    resp = client.post(f'/bom/import?assembly_id={aid}', files={'file': ('b.csv',csv_data,'text/csv')}, headers=headers)
    assert resp.status_code == 403

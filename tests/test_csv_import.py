import sqlalchemy
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import app.main as main
import pytest

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

def test_semicolon_csv_import(client, auth_header):
    data = 'part_number;description;quantity;unit_cost\nP1;Res;2;1.5\n'
    files = {'file': ('bom.csv', data, 'text/csv')}
    r = client.post('/bom/import', files=files, headers=auth_header)
    assert r.status_code == 200
    item = r.json()[0]
    assert item['unit_cost'] == 1.5

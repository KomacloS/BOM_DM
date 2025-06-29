import os, sys, sqlalchemy, pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.main as main

@pytest.fixture
def client_auth():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=sqlalchemy.pool.StaticPool)
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    with TestClient(main.app) as c:
        token = c.post('/auth/token', data={'username':'admin','password':'123456789'}).json()['access_token']
        yield c, token

def test_viewer_ui_contains_role_script(client_auth):
    client, token = client_auth
    r = client.get('/ui/workflow/', headers={'Authorization': f'Bearer {token}'})
    assert '/auth/me' in r.text
    assert 'checkOperator' in r.text


def test_po_button_hidden_for_viewer(client_auth):
    client, admin_token = client_auth
    client.post('/auth/register', json={'username':'op','password':'pw','role':'viewer'}, headers={'Authorization': f'Bearer {admin_token}'})
    op_token = client.post('/auth/token', data={'username':'op','password':'pw'}).json()['access_token']
    r = client.get('/ui/workflow/', headers={'Authorization': f'Bearer {op_token}'})
    assert 'id="po-btn"' not in r.text

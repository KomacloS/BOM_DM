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
        yield c, {'Authorization': f'Bearer {token}'}

def test_workflow_page_contains_step_one(client_auth):
    client, auth = client_auth
    r = client.get("/ui/workflow/", headers=auth)
    assert r.status_code == 200
    assert '<div id="step-1"' in r.text
    assert 'id="pagination"' in r.text
    assert 'upload-ds-btn' in r.text


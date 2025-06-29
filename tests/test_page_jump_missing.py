import os, sys, sqlalchemy
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.main as main


def test_page_jump_present():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=sqlalchemy.pool.StaticPool)
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    with TestClient(main.app) as client:
        token = client.post('/auth/token', data={'username':'admin','password':'123456789'}).json()['access_token']
        auth={'Authorization':f'Bearer {token}'}
        r = client.get("/ui/workflow/", headers=auth)
        assert r.status_code == 200
        assert 'id="page-jump"' in r.text
        assert 'id="go-page"' in r.text


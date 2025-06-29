import sqlalchemy
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import app.main as main


def setup_client():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=sqlalchemy.pool.StaticPool)
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    return TestClient(main.app)


def test_dashboard_loads_after_login():
    with setup_client() as client:
        tok = client.post('/auth/token', data={'username': 'admin', 'password': '123456789'}).json()['access_token']
        r = client.get('/ui/', headers={'Authorization': f'Bearer {tok}'})
        assert r.status_code == 200
        assert '<title>Dashboard' in r.text


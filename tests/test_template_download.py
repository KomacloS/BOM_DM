import os, sys, sqlalchemy
from sqlmodel import SQLModel, create_engine
from fastapi.testclient import TestClient
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.main as main


def setup_client():
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    return TestClient(main.app)


def test_bom_template_download():
    with setup_client() as client:
        token = client.post('/auth/token', data={'username':'admin','password':'123456789'}).json()['access_token']
        r = client.get('/bom/template', headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        header = r.text.splitlines()[0]
        assert header == 'part_number,description,quantity,reference,manufacturer,mpn,footprint,unit_cost,currency,dnp'



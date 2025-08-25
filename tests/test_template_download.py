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
        r = client.get('/bom/template')
        assert r.status_code == 200
        header = r.text.splitlines()[0]
        assert header == 'PN,Reference,Qty,Manufacturer,Active/Passive,Function,Tolerance P,Tolerance N,Price,Currency,Datasheet,Notes'



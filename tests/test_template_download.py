import os, sys, sqlalchemy
from sqlmodel import SQLModel, create_engine, Session
from fastapi.testclient import TestClient
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.api import app, get_session
from app import database


def setup_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=sqlalchemy.pool.StaticPool
    )
    database.engine = engine
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    return TestClient(app)


def test_bom_template_download():
    with setup_client() as client:
        r = client.get('/bom/template')
        assert r.status_code == 200
        header = r.text.splitlines()[0]
        assert header == 'PN,Reference,Description,Manufacturer,Active/Passive,Function,Value,Tolerance P,Tolerance N,Price,Currency,Datasheet,Notes'



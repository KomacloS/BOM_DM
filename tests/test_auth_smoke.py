import sqlalchemy
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from importlib import reload

import app.models as models
from app.api import app, get_session
from app import auth


def setup_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.clear()
    reload(models)
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with Session(engine) as session:
        auth.create_default_users(session)
    return TestClient(app)


def test_login_succeeds_with_hashed_pw():
    with setup_client() as client:
        resp = client.post(
            "/auth/token", data={"username": "admin", "password": "admin"}
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()


def test_hashed_pw_alias_property():
    u = models.User(username="u", hashed_password="hpw")
    assert u.hashed_pw == "hpw"
    u.hashed_pw = "new"
    assert u.hashed_password == "new"

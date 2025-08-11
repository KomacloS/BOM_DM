from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session, select

from app.api import app, get_session
from app.auth import create_default_users
from app.models import User


def setup_db():
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        create_default_users(session)
    return engine


def test_admin_seed_and_auth():
    engine = setup_db()

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = session_override
    client = TestClient(app)

    # ensure admin exists
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == "admin")).first()
        assert user is not None

    resp = client.post("/auth/token", data={"username": "admin", "password": "admin"})
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "admin"

    app.dependency_overrides.clear()

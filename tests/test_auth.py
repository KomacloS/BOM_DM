from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import sqlalchemy
import pytest
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.main as main


@pytest.fixture(name="client")
def client_fixture():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    main.engine = test_engine
    SQLModel.metadata.create_all(test_engine)
    with TestClient(main.app) as c:
        yield c


def get_token(client, username="admin", password="123456789"):
    resp = client.post("/auth/token", data={"username": username, "password": password})
    return resp


def test_login_success_and_failure(client):
    ok = get_token(client)
    assert ok.status_code == 200
    assert "access_token" in ok.json()

    bad = client.post("/auth/token", data={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401


def test_protected_requires_auth(client):
    resp = client.post(
        "/bom/items",
        json={"part_number": "P1", "description": "A", "quantity": 1},
    )
    assert resp.status_code == 401


def test_admin_can_create_user(client):
    token = get_token(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    new_user = {"username": "bob", "password": "secret", "role": "viewer"}
    create = client.post("/auth/register", json=new_user, headers=headers)
    assert create.status_code == 201

    login_new = client.post("/auth/token", data={"username": "bob", "password": "secret"})
    assert login_new.status_code == 200

    user_token = login_new.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}
    fail = client.post("/auth/register", json={"username": "x", "password": "y"}, headers=user_headers)
    assert fail.status_code == 403

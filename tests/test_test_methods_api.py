import os
from importlib import import_module, reload
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select


def _setup_app(tmp_path):
    os.environ["BOM_DATA_ROOT"] = str(tmp_path / "data")

    config = reload(import_module("app.config"))
    config.refresh_paths()
    test_assets = reload(import_module("app.services.test_assets"))

    SQLModel.metadata.clear()
    models = reload(import_module("app.models"))
    auth = reload(import_module("app.auth"))
    reload(import_module("app.routers.test_methods"))
    api_module = reload(import_module("app.api"))

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    api_module.app.dependency_overrides[api_module.get_session] = session_override
    api_module.app.dependency_overrides[auth.get_current_user] = lambda: models.User(
        id=1, username="tester", hashed_password="x"
    )
    return api_module, models, test_assets, auth, engine


@pytest.fixture()
def client(tmp_path):
    api_module, models, test_assets, auth, engine = _setup_app(tmp_path)
    client = TestClient(api_module.app)
    client.headers.update({"Authorization": "Bearer test-token"})
    try:
        yield client, models, test_assets, engine, api_module
    finally:
        client.close()
        api_module.app.dependency_overrides.clear()


def _create_part(models, engine, part_number: str):
    with Session(engine) as session:
        part = models.Part(part_number=part_number)
        session.add(part)
        session.commit()


def test_assign_python_creates_folder(client, tmp_path):
    client_app, models, test_assets, engine, _api = client
    _create_part(models, engine, "ABC-123")

    resp = client_app.post(
        "/tests/assign",
        json={"part_number": "ABC-123", "method": "python"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["method"] == "python"
    folder = Path(test_assets.python_folder_path("ABC-123"))
    assert folder.exists() and folder.is_dir()


def test_quicktest_roundtrip(client, tmp_path):
    client_app, models, test_assets, engine, _api = client
    _create_part(models, engine, "QT-1")

    assign = client_app.post(
        "/tests/assign",
        json={"part_number": "QT-1", "method": "quick_test"},
    )
    assert assign.status_code == 200

    read_resp = client_app.post("/tests/QT-1/quicktest/read")
    assert read_resp.status_code == 200
    payload = read_resp.json()
    assert payload["created"] is False
    assert payload["content"] == ""

    write_resp = client_app.post(
        "/tests/QT-1/quicktest/write",
        json={"content": "line1\nline2"},
    )
    assert write_resp.status_code == 200
    write_payload = write_resp.json()
    assert write_payload["saved"] is True
    path = Path(test_assets.quicktest_path_for_pn("QT-1"))
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "line1\nline2"


def test_python_zip_returns_bytes(client, tmp_path):
    client_app, models, test_assets, engine, _api = client
    _create_part(models, engine, "PNZIP")

    assign = client_app.post(
        "/tests/assign",
        json={"part_number": "PNZIP", "method": "python"},
    )
    assert assign.status_code == 200
    folder = Path(test_assets.python_folder_path("PNZIP"))
    (folder / "script.py").write_text("print('hello')\n", encoding="utf-8")

    resp = client_app.get("/tests/PNZIP/python/zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert len(resp.content) > 20


def test_invalid_part_number_rejected(client, tmp_path):
    client_app, models, _test_assets, engine, _api = client
    _create_part(models, engine, "BAD1")

    resp = client_app.post(
        "/tests/assign",
        json={"part_number": "../evil", "method": "python"},
    )
    assert resp.status_code == 400


def test_assign_idempotent(client, tmp_path):
    client_app, models, _test_assets, engine, _api = client
    _create_part(models, engine, "IDEMP")

    first = client_app.post(
        "/tests/assign",
        json={"part_number": "IDEMP", "method": "python"},
    )
    assert first.status_code == 200
    second = client_app.post(
        "/tests/assign",
        json={"part_number": "IDEMP", "method": "python"},
    )
    assert second.status_code == 200

    with Session(engine) as session:
        assignments = session.exec(select(models.PartTestAssignment)).all()
        assert len(assignments) == 1
        assert assignments[0].method.value == "python"


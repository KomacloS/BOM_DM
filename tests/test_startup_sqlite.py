# root: tests/test_startup_sqlite.py
from fastapi.testclient import TestClient
from sqlmodel import SQLModel
import importlib

import app.config as config
import app.main as main


def test_startup_with_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    SQLModel.metadata.clear()
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"api": "ok", "db": "ok"}

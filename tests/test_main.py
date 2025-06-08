# root: tests/test_main.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    """Ensure the API and database are reachable."""

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"api": "ok", "db": "ok"}

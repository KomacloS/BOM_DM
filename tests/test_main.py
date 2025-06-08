# root: tests/test_main.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_hello():
    r = client.get("/hello")
    assert r.status_code == 200
    assert r.json() == {"message": "Hello BOM World"}

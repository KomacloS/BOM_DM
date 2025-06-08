# root: tests/test_quote.py
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import sqlalchemy
import pytest
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.main as main
from app.quote_utils import calculate_quote


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


class Dummy:
    def __init__(self, qty):
        self.quantity = qty


def test_calculate_quote():
    items = [Dummy(2), Dummy(3)]
    data = calculate_quote(items)
    assert data["total_components"] == 5
    assert data["estimated_time_s"] == 60 + 7 * 5
    assert data["estimated_cost_usd"] == 100 + 0.07 * 5


def test_quote_endpoint(client):
    client.post("/bom/items", json={"part_number": "P1", "description": "A", "quantity": 2})
    client.post("/bom/items", json={"part_number": "P2", "description": "B", "quantity": 3})
    resp = client.get("/bom/quote")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"total_components", "estimated_time_s", "estimated_cost_usd"}
    assert data["total_components"] == 5
    assert isinstance(data["estimated_time_s"], int)
    assert isinstance(data["estimated_cost_usd"], float)

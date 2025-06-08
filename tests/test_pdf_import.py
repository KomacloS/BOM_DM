# root: tests/test_pdf_import.py
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
import sqlalchemy
import pytest
import fitz
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.main as main
from app.pdf_utils import parse_bom_lines, extract_bom_text


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


def test_extract_and_parse():
    text = "PN1    Resistor 1k    2    R1\nPN2    Cap 1uF    5    C1"
    items = parse_bom_lines(text)
    assert items == [
        {"part_number": "PN1", "description": "Resistor 1k", "quantity": 2, "reference": "R1"},
        {"part_number": "PN2", "description": "Cap 1uF", "quantity": 5, "reference": "C1"},
    ]


def test_import_endpoint(client):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PN9    Widget    3    R9")
    pdf_bytes = doc.tobytes()

    files = {"file": ("sample.pdf", pdf_bytes, "application/pdf")}
    response = client.post("/bom/import", files=files)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["part_number"] == "PN9"
    # ensure item persisted
    list_resp = client.get("/bom/items")
    assert any(i["part_number"] == "PN9" for i in list_resp.json())

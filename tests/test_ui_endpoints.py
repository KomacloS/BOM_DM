import os, sys, sqlalchemy, pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.main as main
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

pages = [
    "/ui/",
    "/ui/bom/",
    "/ui/workflow/",
    "/ui/import/",
    "/ui/quote/",
    "/ui/test/",
    "/ui/trace/",
    "/ui/export/",
    "/ui/inventory/",
    "/ui/users/",
    "/ui/settings/",
]

@pytest.fixture
def client_auth():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=sqlalchemy.pool.StaticPool)
    main.engine = engine
    SQLModel.metadata.create_all(engine)
    with TestClient(main.app) as c:
        token = c.post('/auth/token', data={'username':'admin','password':'123456789'}).json()['access_token']
        yield c, {'Authorization': f'Bearer {token}'}

pages = [
    "/ui/",
    "/ui/bom/",
    "/ui/workflow/",
    "/ui/import/",
    "/ui/quote/",
    "/ui/test/",
    "/ui/trace/",
    "/ui/export/",
    "/ui/inventory/",
    "/ui/users/",
    "/ui/settings/",
]


def test_pages_return_html(client_auth):
    client, auth = client_auth
    for p in pages:
        r = client.get(p, headers=auth)
        assert r.status_code == 200
        assert "<title>" in r.text


def test_workflow_page_has_wizard(client_auth):
    client, auth = client_auth
    r = client.get("/ui/workflow/", headers=auth)
    assert '<div id="step-1"' in r.text

def test_wizard_has_checkbox_and_select(client_auth):
    client, auth = client_auth
    r = client.get("/ui/workflow/", headers=auth)
    assert '<input type="checkbox"' in r.text
    assert '<select' in r.text and 'USD' in r.text


def test_htmx_create_item(client_auth):
    client, auth = client_auth
    r = client.post(
        "/ui/bom/create",
        data={"part_number": "P1", "description": "D", "quantity": "1"},
        headers={**auth, "HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "P1" in r.text

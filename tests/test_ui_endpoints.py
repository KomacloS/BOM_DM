import app.main as main
from fastapi.testclient import TestClient

client = TestClient(main.app)

pages = [
    "/ui/",
    "/ui/bom/",
    "/ui/workflow/",
    "/ui/import/",
    "/ui/quote/",
    "/ui/test/",
    "/ui/trace/",
    "/ui/export/",
    "/ui/users/",
    "/ui/settings/",
]


def test_pages_return_html():
    for p in pages:
        r = client.get(p)
        assert r.status_code == 200
        assert "<title>" in r.text


def test_workflow_page_has_wizard():
    r = client.get("/ui/workflow/")
    assert '<div id="step-1"' in r.text


def test_htmx_create_item():
    token = client.post(
        "/auth/token",
        data={"username": "admin", "password": "change_me"},
    ).json()["access_token"]
    r = client.post(
        "/ui/bom/create",
        data={"part_number": "P1", "description": "D", "quantity": "1"},
        headers={"Authorization": f"Bearer {token}", "HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "P1" in r.text

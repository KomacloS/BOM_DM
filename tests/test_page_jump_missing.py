from fastapi.testclient import TestClient
import app.main as main

client = TestClient(main.app)

def test_page_jump_present():
    r = client.get("/ui/workflow/")
    assert r.status_code == 200
    assert 'id="page-jump"' in r.text
    assert 'id="go-page"' in r.text


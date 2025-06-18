from fastapi.testclient import TestClient
import app.main as main

client = TestClient(main.app)

def test_workflow_page_contains_step_one():
    r = client.get("/ui/workflow/")
    assert r.status_code == 200
    assert '<div id="step-1"' in r.text
    assert 'id="pagination"' in r.text
    assert 'upload-ds-btn' in r.text


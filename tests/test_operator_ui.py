from fastapi.testclient import TestClient
import app.main as main

client = TestClient(main.app)

def test_operator_ui_contains_role_script():
    r = client.get('/ui/workflow/')
    assert '/auth/me' in r.text
    assert 'checkOperator' in r.text

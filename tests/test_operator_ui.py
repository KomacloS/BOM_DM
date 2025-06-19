from fastapi.testclient import TestClient
import app.main as main

client = TestClient(main.app)

def test_operator_ui_contains_role_script():
    r = client.get('/ui/workflow/')
    assert '/auth/me' in r.text
    assert 'checkOperator' in r.text


def test_po_button_hidden_for_operator():
    admin = client.post('/auth/token', data={'username':'admin','password':'change_me'}).json()['access_token']
    client.post('/auth/register', json={'username':'op','password':'pw','role':'operator'}, headers={'Authorization': f'Bearer {admin}'})
    op_token = client.post('/auth/token', data={'username':'op','password':'pw'}).json()['access_token']
    r = client.get('/ui/workflow/', headers={'Authorization': f'Bearer {op_token}'})
    assert 'id="po-btn"' not in r.text

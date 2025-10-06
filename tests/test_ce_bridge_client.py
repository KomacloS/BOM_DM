import json
import pytest
from requests import exceptions as req_exc

from app.integration import ce_bridge_client


class DummyResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise req_exc.HTTPError(f"status {self.status_code}")


@pytest.fixture(autouse=True)
def _fixed_settings(monkeypatch):
    settings = {
        "ui_enabled": True,
        "bridge": {
            "enabled": True,
            "base_url": "http://bridge.local",
            "auth_token": "token-123",
            "request_timeout_seconds": 5,
        },
        "note_or_link": "",
    }
    monkeypatch.setattr(ce_bridge_client, "get_complex_editor_settings", lambda: settings)
    monkeypatch.setattr(ce_bridge_client, "ensure_ce_bridge_ready", lambda: None)
    return settings


def test_healthcheck_success(monkeypatch):
    response = DummyResponse(200, {"status": "ok"})

    def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert url == "http://bridge.local/health"
        return response

    monkeypatch.setattr(ce_bridge_client.requests, "request", fake_request)

    payload = ce_bridge_client.healthcheck()
    assert payload == {"status": "ok"}


def test_search_complexes_filters_non_dict(monkeypatch):
    response = DummyResponse(200, [{"id": "1"}, "bad", {"id": "2", "extra": True}])

    def fake_request(method, url, **kwargs):
        assert kwargs["params"] == {"pn": "PN123", "limit": 10}
        return response

    monkeypatch.setattr(ce_bridge_client.requests, "request", fake_request)

    results = ce_bridge_client.search_complexes("PN123", limit=10)
    assert results == [{"id": "1"}, {"id": "2", "extra": True}]


def test_get_complex_auth_error(monkeypatch):
    response = DummyResponse(401, {"detail": "nope"})

    def fake_request(*args, **kwargs):
        return response

    monkeypatch.setattr(ce_bridge_client.requests, "request", fake_request)

    with pytest.raises(ce_bridge_client.CEAuthError):
        ce_bridge_client.get_complex("abc")


def test_create_complex_cancelled(monkeypatch):
    response = DummyResponse(409, {"reason": "cancelled"})

    def fake_request(*args, **kwargs):
        return response

    monkeypatch.setattr(ce_bridge_client.requests, "request", fake_request)

    with pytest.raises(ce_bridge_client.CEUserCancelled):
        ce_bridge_client.create_complex("PN123")


def test_request_includes_bearer(monkeypatch):
    response = DummyResponse(200, {"status": "ok"})
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr(ce_bridge_client.requests, "request", fake_request)

    ce_bridge_client.healthcheck()
    headers = captured.get("headers")
    assert headers and headers["Authorization"] == "Bearer token-123"


def test_network_errors_raise(monkeypatch):
    def fake_request(*args, **kwargs):
        raise req_exc.Timeout("boom")

    monkeypatch.setattr(ce_bridge_client.requests, "request", fake_request)

    with pytest.raises(ce_bridge_client.CENetworkError):
        ce_bridge_client.search_complexes("PN")

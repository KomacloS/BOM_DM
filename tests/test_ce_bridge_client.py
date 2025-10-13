import json
import pytest
from requests import exceptions as req_exc

from app.integration import ce_bridge_client, ce_bridge_transport


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


class DummySession:
    def __init__(self):
        self.trust_env = False
        self.request_func = None
        self.last_call: dict[str, Any] | None = None

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.last_call = {
            "method": method,
            "url": url,
            "headers": headers,
            "timeout": timeout,
            "kwargs": kwargs,
        }
        if self.request_func:
            return self.request_func(method, url, headers=headers, timeout=timeout, **kwargs)
        return DummyResponse(200, {})


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
    session = DummySession()
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda: session)
    monkeypatch.setattr(ce_bridge_transport, "is_preflight_recent", lambda max_age_s=5.0: True)
    monkeypatch.setattr(
        ce_bridge_transport, "preflight_ready", lambda *a, **kw: {"ready": True}
    )
    return settings


def test_healthcheck_success(monkeypatch):
    response = DummyResponse(200, {"status": "ok"})
    session = ce_bridge_transport.get_session()
    session.request_func = lambda method, url, **kwargs: response

    payload = ce_bridge_client.healthcheck()
    assert payload == {"status": "ok"}


def test_search_complexes_filters_non_dict(monkeypatch):
    response = DummyResponse(200, [{"id": "1"}, "bad", {"id": "2", "extra": True}])
    session = ce_bridge_transport.get_session()

    def request_func(method, url, **kwargs):
        assert kwargs.get("params") == {"pn": "PN123", "limit": 10}
        return response

    session.request_func = request_func

    results = ce_bridge_client.search_complexes("PN123", limit=10)
    assert results == [{"id": "1"}, {"id": "2", "extra": True}]


def test_get_complex_auth_error(monkeypatch):
    response = DummyResponse(401, {"detail": "nope"})
    session = ce_bridge_transport.get_session()
    session.request_func = lambda *args, **kwargs: response

    with pytest.raises(ce_bridge_client.CEAuthError):
        ce_bridge_client.get_complex("abc")


def test_create_complex_cancelled(monkeypatch):
    response = DummyResponse(409, {"reason": "cancelled"})
    session = ce_bridge_transport.get_session()
    session.request_func = lambda *args, **kwargs: response

    with pytest.raises(ce_bridge_client.CEUserCancelled):
        ce_bridge_client.create_complex("PN123")


def test_request_includes_bearer(monkeypatch):
    session = ce_bridge_transport.get_session()
    session.request_func = lambda *args, **kwargs: DummyResponse(200, {"status": "ok"})
    ce_bridge_client.healthcheck()
    headers = session.last_call.get("headers") if session.last_call else {}
    assert headers and headers["Authorization"] == "Bearer token-123"


def test_network_errors_raise(monkeypatch):
    session = ce_bridge_transport.get_session()

    def error_request(*args, **kwargs):
        raise req_exc.Timeout("boom")

    session.request_func = error_request

    with pytest.raises(ce_bridge_client.CENetworkError):
        ce_bridge_client.search_complexes("PN")

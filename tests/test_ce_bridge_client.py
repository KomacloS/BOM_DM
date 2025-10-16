import json
from collections import deque

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
    monkeypatch.setattr(ce_bridge_client, "get_viva_export_settings", lambda: {})
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


def test_resolve_bridge_config_keeps_ce_token(monkeypatch):
    monkeypatch.setattr(
        ce_bridge_client,
        "get_viva_export_settings",
        lambda: {"ce_auth_token": "   "},
    )
    base_url, token, timeout = ce_bridge_client._resolve_bridge_config()
    assert base_url.startswith("http://bridge.local")
    assert token == "token-123"
    assert timeout == 5.0


def test_resolve_bridge_config_overrides_token(monkeypatch):
    monkeypatch.setattr(
        ce_bridge_client,
        "get_viva_export_settings",
        lambda: {"ce_auth_token": "override", "ce_bridge_url": "http://0.0.0.0:9000"},
    )
    base_url, token, _timeout = ce_bridge_client._resolve_bridge_config()
    assert base_url == "http://127.0.0.1:9000"
    assert token == "override"


def test_create_complex_success(monkeypatch):
    response = DummyResponse(201, {"id": 7})
    session = ce_bridge_transport.get_session()

    def request_func(method, url, **kwargs):
        assert method == "POST"
        assert url.endswith("/complexes")
        assert kwargs["json"] == {"pn": "PN123", "aliases": ["ALT"]}
        return response

    session.request_func = request_func

    payload = ce_bridge_client.create_complex("PN123", ["ALT"])
    assert payload == {"id": 7}


def test_create_complex_conflict_includes_trace(monkeypatch):
    response = DummyResponse(409, {"detail": "duplicate", "trace_id": "abc123"})
    session = ce_bridge_transport.get_session()
    session.request_func = lambda *args, **kwargs: response

    with pytest.raises(ce_bridge_client.CENetworkError) as excinfo:
        ce_bridge_client.create_complex("PN123")

    message = str(excinfo.value)
    assert "duplicate" in message
    assert "abc123" in message


def test_create_complex_server_error(monkeypatch):
    response = DummyResponse(500, {"detail": "ce broke", "trace_id": "trace-5"})
    session = ce_bridge_transport.get_session()
    session.request_func = lambda *args, **kwargs: response

    with pytest.raises(ce_bridge_client.CENetworkError) as excinfo:
        ce_bridge_client.create_complex("PN500")

    message = str(excinfo.value)
    assert "ce broke" in message
    assert "trace-5" in message


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


def test_open_complex_posts_and_waits_for_wizard(monkeypatch):
    session = ce_bridge_transport.get_session()
    calls = deque()

    states = deque(
        [
            {"wizard_open": False, "focused_comp_id": "ce-1"},
            {"wizard_open": True, "focused_comp_id": "ce-1"},
        ]
    )

    def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(ce_bridge_client.time, "sleep", fake_sleep)

    def request_func(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/complexes/ce-1/open"):
            return DummyResponse(200, {"status": "ok"})
        if method == "GET" and url.endswith("/state"):
            payload = states[0] if states else {"wizard_open": True}
            if states:
                states.popleft()
            return DummyResponse(200, payload)
        if method == "POST" and url.endswith("/app/bring-to-front"):
            return DummyResponse(200, {"status": "ok"})
        raise AssertionError(f"Unexpected request {method} {url}")

    session.request_func = request_func

    ce_bridge_client.open_complex("ce-1", poll_timeout_s=0.2, poll_interval_s=0.0)

    assert calls, "Expected at least one bridge call"
    first_call = calls[0]
    assert first_call[0] == "POST" and first_call[1].endswith("/complexes/ce-1/open")


def test_open_complex_conflict_raises(monkeypatch):
    session = ce_bridge_transport.get_session()

    def request_func(method, url, **kwargs):
        if method == "POST" and url.endswith("/complexes/ce-2/open"):
            return DummyResponse(409, {"detail": "wizard busy"})
        if method == "GET" and url.endswith("/state"):
            return DummyResponse(200, {"wizard_open": False})
        raise AssertionError(f"Unexpected request {method} {url}")

    session.request_func = request_func

    with pytest.raises(ce_bridge_client.CENetworkError) as excinfo:
        ce_bridge_client.open_complex("ce-2", poll_timeout_s=0.0)
    assert "wizard busy" in str(excinfo.value)


def test_open_complex_times_out(monkeypatch):
    session = ce_bridge_transport.get_session()

    def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(ce_bridge_client.time, "sleep", fake_sleep)

    def request_func(method, url, **kwargs):
        if method == "POST" and url.endswith("/complexes/ce-3/open"):
            return DummyResponse(200, {"status": "ok"})
        if method == "GET" and url.endswith("/state"):
            return DummyResponse(200, {"wizard_open": False, "focused_comp_id": "ce-other"})
        raise AssertionError(f"Unexpected request {method} {url}")

    session.request_func = request_func

    with pytest.raises(ce_bridge_client.CENetworkError):
        ce_bridge_client.open_complex("ce-3", poll_timeout_s=0.0)

import json

import pytest
from requests import exceptions as req_exc

from app.integration import ce_bridge_client


class DummyResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        if payload is None:
            self.content = b""
            self.text = ""
        else:
            serialized = json.dumps(payload)
            self.content = serialized.encode("utf-8")
            self.text = serialized
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise req_exc.HTTPError(f"status {self.status_code}")


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = False

    def request(self, method, url, **kwargs):
        self.calls.append(("request", method, url, kwargs))
        return self._next()

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self._next()

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self._next()

    def close(self):
        pass

    def _next(self):
        if not self.responses:
            raise AssertionError("No responses configured for FakeSession")
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _stable_config(monkeypatch):
    original_preflight = ce_bridge_client._preflight
    config = ce_bridge_client._BridgeConfig(
        base_url="http://bridge.local",
        token="token-123",
        timeout=5.0,
        auto_start=True,
        ui_enabled=True,
        is_local=True,
        host="127.0.0.1",
        port=8765,
    )
    monkeypatch.setattr(ce_bridge_client, "_load_bridge_config", lambda: config)
    monkeypatch.setattr(ce_bridge_client, "_SESSION", None, raising=False)
    monkeypatch.setattr(ce_bridge_client, "_SESSION_BASE", None, raising=False)
    monkeypatch.setattr(ce_bridge_client, "_PREFLIGHT_CACHE", None, raising=False)
    monkeypatch.setattr(ce_bridge_client, "_ORIGINAL_PREFLIGHT", original_preflight, raising=False)

    def _ready_preflight(_config, **kwargs):
        return {"ready": True, "wizard_available": True}

    monkeypatch.setattr(ce_bridge_client, "_preflight", _ready_preflight)
    return config


def test_healthcheck_success(monkeypatch):
    session = FakeSession(DummyResponse(200, {"status": "ok"}))
    monkeypatch.setattr(ce_bridge_client, "_session_for", lambda _base: session)

    payload = ce_bridge_client.healthcheck()
    assert payload == {"status": "ok"}
    assert session.calls == [("get", "http://bridge.local/health", {"headers": {"Accept": "application/json", "Authorization": "Bearer token-123"}, "timeout": 5.0})]


def test_search_complexes_filters_non_dict(monkeypatch):
    session = FakeSession(DummyResponse(200, [{"id": "1"}, "bad", {"id": "2", "extra": True}]))
    monkeypatch.setattr(ce_bridge_client, "_session_for", lambda _base: session)

    results = ce_bridge_client.search_complexes("PN123", limit=10)
    assert results == [{"id": "1"}, {"id": "2", "extra": True}]


def test_get_complex_auth_error(monkeypatch):
    session = FakeSession(DummyResponse(401, {"detail": "nope"}))
    monkeypatch.setattr(ce_bridge_client, "_session_for", lambda _base: session)

    with pytest.raises(ce_bridge_client.CEAuthError):
        ce_bridge_client.get_complex("abc")


def test_create_complex_cancelled(monkeypatch):
    response = DummyResponse(409, {"reason": "cancelled by user"})
    monkeypatch.setattr(
        ce_bridge_client,
        "_perform_request",
        lambda *args, **kwargs: (response, {"ready": True, "wizard_available": True}),
    )

    with pytest.raises(ce_bridge_client.CEUserCancelled):
        ce_bridge_client.create_complex("PN123")


def test_request_includes_bearer(monkeypatch):
    session = FakeSession(DummyResponse(200, []))
    monkeypatch.setattr(ce_bridge_client, "_session_for", lambda _base: session)

    ce_bridge_client.search_complexes("PN123", limit=5)
    assert session.calls
    call = session.calls[0]
    assert call[0] == "request"
    headers = call[3]["headers"]
    assert headers["Authorization"] == "Bearer token-123"
    assert headers["Accept"] == "application/json"


def test_network_errors_raise(monkeypatch):
    def _raising_session(_base):
        class _Session(FakeSession):
            def request(self, *args, **kwargs):
                raise req_exc.Timeout("boom")

        return _Session()

    monkeypatch.setattr(ce_bridge_client, "_session_for", _raising_session)

    with pytest.raises(ce_bridge_client.CENetworkError):
        ce_bridge_client.search_complexes("PN")


def test_preflight_headless_allows_flow(monkeypatch, _stable_config):
    headless_state = {"ready": True, "wizard_available": False}
    session = FakeSession(DummyResponse(200, headless_state))
    monkeypatch.setattr(ce_bridge_client, "_preflight", ce_bridge_client._ORIGINAL_PREFLIGHT)
    monkeypatch.setattr(ce_bridge_client, "_session_for", lambda _base: session)
    monkeypatch.setattr(ce_bridge_client, "record_state_snapshot", lambda payload: None)
    monkeypatch.setattr(ce_bridge_client, "record_bridge_action", lambda text: None)
    monkeypatch.setattr(ce_bridge_client, "record_health_detail", lambda detail: None)
    monkeypatch.setattr(ce_bridge_client, "bridge_owned_for_url", lambda base: False)
    monkeypatch.setattr(ce_bridge_client, "restart_bridge_with_ui", lambda timeout: (_ for _ in ()).throw(AssertionError("should not restart")))

    payload = ce_bridge_client._preflight(_stable_config, require_ui=True)
    assert payload["ready"] is True
    assert payload["wizard_available"] is False


def test_preflight_owned_headless_restarts(monkeypatch, _stable_config):
    responses = [
        DummyResponse(200, {"ready": True, "wizard_available": False}),
        DummyResponse(200, {"ready": True, "wizard_available": True}),
    ]
    session = FakeSession(*responses)
    monkeypatch.setattr(ce_bridge_client, "_preflight", ce_bridge_client._ORIGINAL_PREFLIGHT)
    monkeypatch.setattr(ce_bridge_client, "_session_for", lambda _base: session)
    monkeypatch.setattr(ce_bridge_client, "record_state_snapshot", lambda payload: None)
    monkeypatch.setattr(ce_bridge_client, "record_bridge_action", lambda text: None)
    monkeypatch.setattr(ce_bridge_client, "record_health_detail", lambda detail: None)
    monkeypatch.setattr(ce_bridge_client, "bridge_owned_for_url", lambda base: True)
    restart_called = {}

    def _fake_restart(timeout):
        restart_called["value"] = timeout

    monkeypatch.setattr(ce_bridge_client, "restart_bridge_with_ui", _fake_restart)

    payload = ce_bridge_client._preflight(_stable_config, require_ui=True)
    assert restart_called and restart_called["value"] >= _stable_config.timeout
    assert payload["wizard_available"] is True


def test_preflight_timeout_includes_last_error(monkeypatch, _stable_config):
    class LoopSession:
        def __init__(self):
            self.trust_env = False
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            return DummyResponse(200, {"ready": False, "wizard_available": False, "last_ready_error": "warm_up"})

        def post(self, *args, **kwargs):
            return DummyResponse(200, {})

        def request(self, *args, **kwargs):
            raise AssertionError("not used")

        def close(self):
            pass

    session = LoopSession()
    monkeypatch.setattr(ce_bridge_client, "_preflight", ce_bridge_client._ORIGINAL_PREFLIGHT)
    monkeypatch.setattr(ce_bridge_client, "_session_for", lambda _base: session)
    monkeypatch.setattr(ce_bridge_client, "record_state_snapshot", lambda payload: None)
    monkeypatch.setattr(ce_bridge_client, "record_bridge_action", lambda text: None)
    monkeypatch.setattr(ce_bridge_client, "record_health_detail", lambda detail: None)
    monkeypatch.setattr(ce_bridge_client, "bridge_owned_for_url", lambda base: False)
    monkeypatch.setattr(ce_bridge_client, "restart_bridge_with_ui", lambda timeout: None)
    monkeypatch.setattr(ce_bridge_client, "_fetch_health_reason", lambda *args, **kwargs: "503 headless")

    counter = {"value": 0.0}

    def fake_monotonic():
        value = counter["value"]
        counter["value"] += 0.2
        return value

    monkeypatch.setattr(ce_bridge_client.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(ce_bridge_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(ce_bridge_client, "_handshake_budget", lambda timeout: 0.5)

    with pytest.raises(ce_bridge_client.CENetworkError) as exc:
        ce_bridge_client._preflight(_stable_config, require_ui=True)

    assert "warm_up" in str(exc.value)
    assert "503 headless" in str(exc.value)

import json
import os
from pathlib import Path

TMP_SETTINGS = Path("tests/_tmp_settings.toml")
if not TMP_SETTINGS.exists():
    TMP_SETTINGS.write_text('[database]\nurl="sqlite:///:memory:"\n')
os.environ.setdefault("BOM_SETTINGS_PATH", str(TMP_SETTINGS.resolve()))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from requests import exceptions as req_exc

from app.integration import ce_bridge_client, ce_bridge_transport


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
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
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
    ce_bridge_transport.reset_session()
    class _FakeSupervisor:
        def ensure_ready(self, trace_id):
            return True, {"status": "READY"}

    monkeypatch.setattr(ce_bridge_client, "get_supervisor", lambda: _FakeSupervisor())
    monkeypatch.setattr(ce_bridge_client, "_load_bridge_config", lambda: config)
    monkeypatch.setattr(ce_bridge_client, "_PREFLIGHT_CACHE", None, raising=False)
    monkeypatch.setattr(ce_bridge_client, "_ORIGINAL_PREFLIGHT", original_preflight, raising=False)

    def _ready_preflight(_config, **kwargs):
        return {"ready": True, "wizard_available": True}

    monkeypatch.setattr(ce_bridge_client, "_preflight", _ready_preflight)
    return config


def test_healthcheck_success(monkeypatch):
    session = FakeSession(DummyResponse(200, {"status": "ok"}))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)

    payload = ce_bridge_client.healthcheck()
    assert payload == {"status": "ok"}
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call[0] == "get"
    assert call[1] == "http://bridge.local/admin/health"
    headers = call[2]["headers"]
    assert headers["Authorization"] == "Bearer token-123"
    assert headers["X-Trace-Id"]
    assert call[2]["timeout"] == 5.0


def test_search_complexes_filters_non_dict(monkeypatch):
    session = FakeSession(DummyResponse(200, [{"id": "1"}, "bad", {"id": "2", "extra": True}]))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)

    results = ce_bridge_client.search_complexes("PN123", limit=10)
    assert results == [{"id": "1"}, {"id": "2", "extra": True}]


def test_get_complex_auth_error(monkeypatch):
    session = FakeSession(DummyResponse(401, {"detail": "nope"}))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)

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
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)

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

    monkeypatch.setattr(ce_bridge_transport, "get_session", _raising_session)

    with pytest.raises(ce_bridge_client.CENetworkError):
        ce_bridge_client.search_complexes("PN")


def test_preflight_headless_allows_flow(monkeypatch, _stable_config):
    headless_state = {"ready": True, "wizard_available": False}
    session = FakeSession(DummyResponse(200, headless_state))
    monkeypatch.setattr(ce_bridge_client, "_preflight", ce_bridge_client._ORIGINAL_PREFLIGHT)
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)
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
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)
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
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)
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


def test_add_aliases_success(monkeypatch, _stable_config):
    monkeypatch.setattr(
        ce_bridge_client,
        "_perform_request",
        lambda *args, **kwargs: (DummyResponse(200, {"status": "ok"}), {}),
    )
    result = ce_bridge_client.add_aliases(5, ["PN-5"])
    assert result == {"status": "ok"}


def test_add_aliases_conflict(monkeypatch, _stable_config):
    monkeypatch.setattr(
        ce_bridge_client,
        "_perform_request",
        lambda *args, **kwargs: (DummyResponse(409, {"conflicts": ["ce-9"], "reason": "alias_conflict"}), {}),
    )
    with pytest.raises(ce_bridge_client.CEAliasConflict) as exc:
        ce_bridge_client.add_aliases(9, ["PN-9"])
    assert exc.value.conflicts == ["ce-9"]

def test_open_complex_success(monkeypatch, _stable_config):
    session = FakeSession(DummyResponse(200, {}))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)
    monkeypatch.setattr(
        ce_bridge_client,
        "_preflight",
        lambda *args, **kwargs: {"ready": True, "wizard_available": True},
    )
    waited = {"called": False}
    monkeypatch.setattr(
        ce_bridge_client,
        "_wait_for_focus_or_wizard",
        lambda *args, **kwargs: waited.__setitem__("called", True),
    )

    result = ce_bridge_client.open_complex(42, status_callback=lambda _: None)

    assert result is False
    assert session.calls[0][0] == "post"
    assert session.calls[0][1].endswith("/complexes/42/open")
    assert waited["called"] is True


def test_open_complex_stale(monkeypatch, _stable_config):
    session = FakeSession(DummyResponse(404, {}))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)
    monkeypatch.setattr(
        ce_bridge_client,
        "_preflight",
        lambda *args, **kwargs: {"ready": True, "wizard_available": True},
    )
    with pytest.raises(ce_bridge_client.CEStaleLink):
        ce_bridge_client.open_complex(7)


def test_open_complex_busy(monkeypatch, _stable_config):
    session = FakeSession(DummyResponse(409, {"reason": "wizard busy"}))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)
    monkeypatch.setattr(
        ce_bridge_client,
        "_preflight",
        lambda *args, **kwargs: {"ready": True, "wizard_available": True},
    )
    front_calls = []
    monkeypatch.setattr(
        ce_bridge_client,
        "ensure_ce_bridge_ready",
        lambda *args, **kwargs: front_calls.append(True),
    )
    with pytest.raises(ce_bridge_client.CEBusyError):
        ce_bridge_client.open_complex(7)
    assert front_calls


def test_open_complex_headless_retry(monkeypatch, _stable_config):
    session = FakeSession(
        DummyResponse(503, {"detail": "wizard unavailable (headless)"}),
        DummyResponse(200, {}),
    )
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)
    monkeypatch.setattr(
        ce_bridge_client,
        "_preflight",
        lambda *args, **kwargs: {"ready": True, "wizard_available": True},
    )
    restart_calls = []
    monkeypatch.setattr(ce_bridge_client, "restart_bridge_with_ui", lambda timeout: restart_calls.append(timeout))
    monkeypatch.setattr(ce_bridge_client, "bridge_owned_for_url", lambda _url: True)
    monkeypatch.setattr(
        ce_bridge_client,
        "_wait_for_focus_or_wizard",
        lambda *args, **kwargs: None,
    )
    result = ce_bridge_client.open_complex(9)
    assert result is False
    assert len(session.calls) == 2
    assert restart_calls


def test_open_complex_already_open(monkeypatch, _stable_config):
    monkeypatch.setattr(
        ce_bridge_client,
        "_preflight",
        lambda *args, **kwargs: {"ready": True, "wizard_available": True, "focused_comp_id": 42},
    )
    bring_calls = []
    monkeypatch.setattr(
        ce_bridge_client,
        "ensure_ce_bridge_ready",
        lambda *args, **kwargs: bring_calls.append(True),
    )

    def _fail_session(_base):
        raise AssertionError("session should not be used when already focused")

    monkeypatch.setattr(ce_bridge_transport, "get_session", _fail_session)
    result = ce_bridge_client.open_complex(42, status_callback=lambda _: None)
    assert result is True
    assert bring_calls


def test_open_complex_auth_error_includes_base(monkeypatch, _stable_config):
    session = FakeSession(DummyResponse(401, {}))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda _base=None: session)
    monkeypatch.setattr(
        ce_bridge_client,
        "_preflight",
        lambda *args, **kwargs: {"ready": True, "wizard_available": True},
    )
    with pytest.raises(ce_bridge_client.CEAuthError) as exc:
        ce_bridge_client.open_complex(11)
    assert _stable_config.base_url in str(exc.value)

import types
import time

import pytest

from app.integration import ce_bridge_transport


@pytest.fixture(autouse=True)
def _reset_transport():
    ce_bridge_transport.reset_transport_state()
    yield
    ce_bridge_transport.reset_transport_state()


def test_preflight_handles_warmup(monkeypatch):
    responses = [
        types.SimpleNamespace(
            ok=False,
            status_code=503,
            json=lambda: {"reason": "warming_up"},
        ),
        types.SimpleNamespace(
            ok=True,
            status_code=200,
            json=lambda: {"ready": False, "reason": "mdb_unavailable"},
        ),
        types.SimpleNamespace(
            ok=True,
            status_code=200,
            json=lambda: {"ready": True, "reason": "ok"},
        ),
    ]

    class Session:
        def __init__(self) -> None:
            self.trust_env = False
            self.selftest_calls = 0

        def get(self, url, headers=None, timeout=None):
            if url.endswith("/state"):
                return responses.pop(0)
            return types.SimpleNamespace(
                ok=False,
                status_code=503,
                json=lambda: {"status": "warming"},
            )

        def post(self, url, headers=None, timeout=None):
            self.selftest_calls += 1
            return types.SimpleNamespace(status_code=503, json=lambda: {"ok": False})

    session = Session()
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda: session)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    payload = ce_bridge_transport.preflight_ready(
        "http://127.0.0.1:8765",
        "token",
        deadline_s=1.0,
        poll_every_s=0.1,
        request_timeout_s=0.1,
    )

    assert payload.get("ready") is True
    assert session.selftest_calls >= 1
    assert ce_bridge_transport.is_preflight_recent()


def test_get_session_disables_proxies(monkeypatch):
    recorded_headers: list[dict[str, str]] = []

    class ProxySession:
        def __init__(self) -> None:
            self.trust_env = True

        def get(self, url, headers=None, timeout=None):
            assert self.trust_env is False
            recorded_headers.append(dict(headers or {}))
            return types.SimpleNamespace(
                ok=True,
                status_code=200,
                json=lambda: {"ready": True, "reason": "ok"},
            )

        def post(self, url, headers=None, timeout=None):
            assert self.trust_env is False
            return types.SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    session = ProxySession()
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.test:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:8080")
    monkeypatch.setattr(
        ce_bridge_transport,
        "requests",
        types.SimpleNamespace(Session=lambda: session),
    )
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    payload = ce_bridge_transport.preflight_ready(
        "http://127.0.0.1:9000",
        "secret",
        deadline_s=0.5,
        poll_every_s=0.1,
        request_timeout_s=0.1,
    )

    assert payload.get("ready") is True
    assert recorded_headers
    for headers in recorded_headers:
        assert "Proxy-Authorization" not in headers

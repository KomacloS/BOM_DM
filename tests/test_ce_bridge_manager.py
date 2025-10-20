import os
import subprocess
import time
import types

import pytest
import requests

from app.integration import ce_bridge_manager, ce_bridge_transport
from app.integration.ce_bridge_diagnostics import mask_token


class DummyProcess:
    def __init__(self):
        self.pid = 4321
        self._poll = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True
        self._poll = 0

    def wait(self, timeout=None):
        self._poll = 0

    def kill(self):
        self.killed = True
        self._poll = -9


def _response(status_code: int):
    return types.SimpleNamespace(status_code=status_code)


@pytest.fixture(autouse=True)
def _reset_bridge_state(monkeypatch):
    ce_bridge_manager._BRIDGE_PROCESS = None
    ce_bridge_manager._BRIDGE_PID = None
    ce_bridge_manager._BRIDGE_AUTO_STOP = False
    ce_bridge_manager._BRIDGE_EXE_PATH = None
    ce_bridge_manager._BRIDGE_BASE_URL = None
    ce_bridge_manager._BRIDGE_TOKEN = None
    ce_bridge_manager._BRIDGE_TIMEOUT = None
    ce_bridge_manager._BRIDGE_STARTED_BY_APP = False
    ce_bridge_manager._BRIDGE_IS_LOCALHOST = False
    monkeypatch.setattr(ce_bridge_manager, "port_busy", lambda *_args, **_kwargs: False)
    yield
    ce_bridge_manager._BRIDGE_PROCESS = None
    ce_bridge_manager._BRIDGE_PID = None
    ce_bridge_manager._BRIDGE_AUTO_STOP = False
    ce_bridge_manager._BRIDGE_EXE_PATH = None
    ce_bridge_manager._BRIDGE_BASE_URL = None
    ce_bridge_manager._BRIDGE_TOKEN = None
    ce_bridge_manager._BRIDGE_TIMEOUT = None
    ce_bridge_manager._BRIDGE_STARTED_BY_APP = False
    ce_bridge_manager._BRIDGE_IS_LOCALHOST = False


def test_ensure_skips_when_bridge_running(monkeypatch, tmp_path):
    exe = tmp_path / "complex_editor.exe"
    exe.write_text("echo")
    if os.name != "nt":
        exe.chmod(0o755)
    settings = {
        "ui_enabled": True,
        "auto_start_bridge": True,
        "auto_stop_bridge_on_exit": False,
        "exe_path": str(exe),
        "config_path": "",
        "bridge": {
            "enabled": True,
            "base_url": "http://127.0.0.1:9000",
            "auth_token": "token",
            "request_timeout_seconds": 5,
        },
    }

    monkeypatch.setattr(ce_bridge_manager.config, "get_complex_editor_settings", lambda: settings)

    class SessionStub:
        def __init__(self):
            self.calls = []
            self.trust_env = False

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return _response(200)

    session_stub = SessionStub()
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: session_stub)

    popen_called = False

    def fake_popen(*_args, **_kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("Popen should not be called when bridge is healthy")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ce_bridge_manager.ensure_ce_bridge_ready()
    assert popen_called is False

    diagnostics = ce_bridge_manager.get_last_ce_bridge_diagnostics()
    assert diagnostics is not None
    assert diagnostics.outcome == "success"
    assert diagnostics.pre_probe_status == "running"


def test_ensure_spawns_when_unhealthy(monkeypatch, tmp_path):
    exe = tmp_path / "complex_editor.exe"
    exe.write_text("echo")
    if os.name != "nt":
        exe.chmod(0o755)

    settings = {
        "ui_enabled": True,
        "auto_start_bridge": True,
        "auto_stop_bridge_on_exit": True,
        "exe_path": str(exe),
        "config_path": "",
        "bridge": {
            "enabled": True,
            "base_url": "http://127.0.0.1:9100",
            "auth_token": "",
            "request_timeout_seconds": 3,
        },
    }

    saved_tokens = {}
    monkeypatch.setattr(ce_bridge_manager.config, "get_complex_editor_settings", lambda: settings)
    monkeypatch.setattr(
        ce_bridge_manager.config,
        "save_complex_editor_settings",
        lambda **kwargs: saved_tokens.update(kwargs),
    )

    calls = []

    class SessionStub:
        def __init__(self):
            self.trust_env = False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                raise requests.exceptions.ConnectionError("refused")
            if len(calls) == 2:
                return _response(503)
            return _response(200)

    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: SessionStub())

    dummy_proc = DummyProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: dummy_proc)

    monkeypatch.setattr(time, "sleep", lambda _s: None)

    ce_bridge_manager.ensure_ce_bridge_ready()

    assert ce_bridge_manager._BRIDGE_PROCESS is dummy_proc
    assert "bridge_auth_token" in saved_tokens
    assert saved_tokens["bridge_auth_token"]
    assert dummy_proc.terminated is False

    diagnostics = ce_bridge_manager.get_last_ce_bridge_diagnostics()
    assert diagnostics is not None
    assert diagnostics.outcome == "success"
    assert diagnostics.spawn_attempted is True
    assert diagnostics.spawn_pid == dummy_proc.pid
    assert diagnostics.health_polls, "expected at least one poll entry"
    generated_token = saved_tokens["bridge_auth_token"]
    assert diagnostics.auth_token_preview == mask_token(generated_token)
    assert all(generated_token not in part for part in diagnostics.spawn_cmd_preview), diagnostics.spawn_cmd_preview

    report = diagnostics.to_text()
    assert "Outcome: success" in report
    assert "Base URL:" in report
    assert "Command (masked):" in report


def test_ensure_timeout_records_diagnostics(monkeypatch, tmp_path):
    exe = tmp_path / "complex_editor.exe"
    exe.write_text("echo")
    if os.name != "nt":
        exe.chmod(0o755)

    settings = {
        "ui_enabled": True,
        "auto_start_bridge": True,
        "auto_stop_bridge_on_exit": True,
        "exe_path": str(exe),
        "config_path": "",
        "bridge": {
            "enabled": True,
            "base_url": "http://127.0.0.1:9300",
            "auth_token": "secret-token",
            "request_timeout_seconds": 3,
        },
    }

    monkeypatch.setattr(ce_bridge_manager.config, "get_complex_editor_settings", lambda: settings)

    calls = []

    class SessionStub:
        def __init__(self):
            self.trust_env = False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                raise requests.exceptions.ConnectionError("refused")
            return _response(503)

    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: SessionStub())
    dummy_proc = DummyProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: dummy_proc)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with pytest.raises(ce_bridge_manager.CEBridgeError) as exc:
        ce_bridge_manager.ensure_ce_bridge_ready(timeout_seconds=0.5)

    diagnostics = getattr(exc.value, "diagnostics", None)
    assert diagnostics is not None
    assert diagnostics.outcome == "timeout"
    assert diagnostics.traceback and "Timed out" in diagnostics.reason

    report = diagnostics.to_text()
    assert "Outcome: timeout" in report
    assert "Base URL:" in report
    assert "Command (masked):" in report


@pytest.mark.skipif(os.name == "nt", reason="Windows does not use POSIX execute bit checks")
def test_ensure_rejects_non_executable(monkeypatch, tmp_path):
    exe = tmp_path / "complex_editor"
    exe.write_text("echo")
    if os.name != "nt":
        exe.chmod(0o644)

    settings = {
        "ui_enabled": True,
        "auto_start_bridge": True,
        "auto_stop_bridge_on_exit": False,
        "exe_path": str(exe),
        "config_path": "",
        "bridge": {
            "enabled": True,
            "base_url": "http://127.0.0.1:9100",
            "auth_token": "token",
            "request_timeout_seconds": 3,
        },
    }

    monkeypatch.setattr(ce_bridge_manager.config, "get_complex_editor_settings", lambda: settings)
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: types.SimpleNamespace(trust_env=False, get=lambda url, **kwargs: _response(503)))

    with pytest.raises(ce_bridge_manager.CEBridgeError) as exc:
        ce_bridge_manager.ensure_ce_bridge_ready()

    diagnostics = getattr(exc.value, "diagnostics", None)
    assert diagnostics is not None
    assert diagnostics.outcome == "error"
    assert diagnostics.reason and "not executable" in diagnostics.reason


def test_restart_bridge_with_ui(monkeypatch):
    ce_bridge_manager._BRIDGE_STARTED_BY_APP = True
    ce_bridge_manager._BRIDGE_BASE_URL = "http://127.0.0.1:9100"
    calls = {"stop": False, "ensure": False}

    def fake_stop(*, force=False):
        calls["stop"] = force

    def fake_ensure(timeout_seconds, require_ui):
        calls["ensure"] = require_ui

    monkeypatch.setattr(ce_bridge_manager, "stop_ce_bridge_if_started", fake_stop)
    monkeypatch.setattr(ce_bridge_manager, "ensure_ce_bridge_ready", fake_ensure)

    ce_bridge_manager.restart_bridge_with_ui(4.0)

    assert calls["stop"] is True
    assert calls["ensure"] is True


def test_stop_bridge_requests_shutdown(monkeypatch):
    proc = DummyProcess()
    ce_bridge_manager._BRIDGE_PROCESS = proc
    ce_bridge_manager._BRIDGE_PID = proc.pid
    ce_bridge_manager._BRIDGE_AUTO_STOP = True
    ce_bridge_manager._BRIDGE_STARTED_BY_APP = True
    ce_bridge_manager._BRIDGE_BASE_URL = "http://127.0.0.1:9100"
    ce_bridge_manager._BRIDGE_TOKEN = "token"
    ce_bridge_manager._BRIDGE_TIMEOUT = 5.0

    class SessionStub:
        def __init__(self):
            self.calls = []
            self.trust_env = False

        def get(self, url, **kwargs):
            self.calls.append(("get", url, kwargs))
            return types.SimpleNamespace(
                status_code=200,
                json=lambda: {"unsaved_changes": False, "wizard_open": False},
            )

        def post(self, url, **kwargs):
            self.calls.append(("post", url, kwargs))
            return types.SimpleNamespace(status_code=200)

        def close(self):
            pass

    session = SessionStub()
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)

    ce_bridge_manager.stop_ce_bridge_if_started()

    assert session.calls[0][0] == "get"
    assert session.calls[1][0] == "post"
    assert proc.terminated is False
    assert proc._poll == 0
    assert ce_bridge_manager._BRIDGE_PROCESS is None
    assert ce_bridge_manager._BRIDGE_STARTED_BY_APP is False

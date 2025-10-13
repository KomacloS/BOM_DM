import datetime
import os
import subprocess
import json
import time
import types

import pytest
from requests import exceptions as req_exc

from app.integration import ce_bridge_manager, ce_bridge_transport


class DummySession:
    def __init__(self):
        self.get_handler = lambda url, headers=None, timeout=None: types.SimpleNamespace(
            ok=True, status_code=200
        )
        self.request_handler = (
            lambda method, url, headers=None, timeout=None, **kwargs: types.SimpleNamespace(
                ok=True,
                status_code=200,
                json=lambda: {},
            )
        )
        self.post_handler = lambda url, headers=None, timeout=None, **kwargs: types.SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True},
        )
        self.trust_env = False

    def get(self, url, headers=None, timeout=None):
        return self.get_handler(url, headers=headers, timeout=timeout)

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        return self.request_handler(
            method, url, headers=headers, timeout=timeout, **kwargs
        )

    def post(self, url, headers=None, timeout=None, **kwargs):
        return self.post_handler(url, headers=headers, timeout=timeout, **kwargs)


@pytest.fixture(autouse=True)
def _reset_bridge_state():
    ce_bridge_manager._BRIDGE_PROCESS = None
    ce_bridge_manager._BRIDGE_AUTO_STOP = False
    ce_bridge_transport.reset_transport_state()
    yield
    ce_bridge_manager._BRIDGE_PROCESS = None
    ce_bridge_manager._BRIDGE_AUTO_STOP = False
    ce_bridge_transport.reset_transport_state()


def test_ensure_skips_when_bridge_running(monkeypatch):
    settings = {
        "ui_enabled": True,
        "auto_start_bridge": True,
        "auto_stop_bridge_on_exit": False,
        "exe_path": "dummy.exe",
        "config_path": "",
        "bridge": {
            "enabled": True,
            "base_url": "http://127.0.0.1:9000",
            "auth_token": "token",
            "request_timeout_seconds": 5,
        },
    }

    monkeypatch.setattr(ce_bridge_manager.config, "get_complex_editor_settings", lambda: settings)

    response = types.SimpleNamespace(ok=True, status_code=200)
    session = DummySession()
    session.get_handler = lambda *_args, **_kwargs: response
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda: session)
    monkeypatch.setattr(
        ce_bridge_transport, "preflight_ready", lambda *a, **kw: {"ready": True}
    )

    popen_called = False

    def fake_popen(*_args, **_kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("Popen should not be called when bridge is healthy")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ce_bridge_manager.ensure_ce_bridge_ready()
    assert popen_called is False

    diagnostics = ce_bridge_manager._LAST_DIAGNOSTICS
    assert diagnostics is not None
    assert diagnostics.outcome == "success"
    assert diagnostics.pre_probe_status == "running"


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

    saved_tokens: dict[str, str] = {}
    monkeypatch.setattr(ce_bridge_manager.config, "get_complex_editor_settings", lambda: settings)
    monkeypatch.setattr(
        ce_bridge_manager.config,
        "save_complex_editor_settings",
        lambda **kwargs: saved_tokens.update(kwargs),
    )

    calls: list[tuple[str, dict | None]] = []
    session = DummySession()

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        if len(calls) == 1:
            raise req_exc.ConnectionError()
        return types.SimpleNamespace(ok=True, status_code=200)

    session.get_handler = fake_get
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda: session)
    monkeypatch.setattr(
        ce_bridge_transport, "preflight_ready", lambda *a, **kw: {"ready": True}
    )

    dummy_proc = DummyProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: dummy_proc)

    monkeypatch.setattr(time, "sleep", lambda _s: None)

    ce_bridge_manager.ensure_ce_bridge_ready()

    assert ce_bridge_manager._BRIDGE_PROCESS is dummy_proc
    assert "bridge_auth_token" in saved_tokens
    assert saved_tokens["bridge_auth_token"]
    assert dummy_proc.terminated is False

    diagnostics = ce_bridge_manager._LAST_DIAGNOSTICS
    assert diagnostics is not None
    assert diagnostics.outcome == "success"
    assert diagnostics.spawn_attempted is True
    assert diagnostics.spawn_cmd_preview
    assert saved_tokens["bridge_auth_token"] not in diagnostics.spawn_cmd_preview
    assert diagnostics.spawn_pid == dummy_proc.pid
    assert diagnostics.health_polls


def test_stop_bridge_closes_process(monkeypatch):
    proc = DummyProcess()
    ce_bridge_manager._BRIDGE_PROCESS = proc
    ce_bridge_manager._BRIDGE_AUTO_STOP = True

    def fake_request(method, endpoint, timeout=5.0):
        if endpoint == "/state":
            return types.SimpleNamespace(ok=True, json=lambda: {"unsaved_changes": False})
        return types.SimpleNamespace(status_code=404, ok=False)

    monkeypatch.setattr(
        ce_bridge_manager, "_bridge_request_without_ensure", fake_request
    )

    ce_bridge_manager.stop_ce_bridge_if_started()
    assert proc.terminated is True
    assert ce_bridge_manager._BRIDGE_PROCESS is None


def test_stop_bridge_skips_when_unsaved(monkeypatch):
    proc = DummyProcess()
    ce_bridge_manager._BRIDGE_PROCESS = proc
    ce_bridge_manager._BRIDGE_AUTO_STOP = True

    def fake_request(method, endpoint, timeout=5.0):
        if endpoint == "/state":
            return types.SimpleNamespace(ok=True, json=lambda: {"unsaved_changes": True})
        return types.SimpleNamespace(status_code=204, ok=True)

    monkeypatch.setattr(
        ce_bridge_manager, "_bridge_request_without_ensure", fake_request
    )

    ce_bridge_manager.stop_ce_bridge_if_started()
    assert proc.terminated is False
    assert ce_bridge_manager._BRIDGE_PROCESS is proc


def test_launch_ce_wizard_includes_config(monkeypatch, tmp_path):
    exe = tmp_path / "complex_editor.exe"
    exe.write_text("echo")
    exe.chmod(0o755)
    config_path = tmp_path / "ce.yml"
    config_path.write_text("config: true")

    monkeypatch.setattr(
        ce_bridge_manager.config,
        "get_complex_editor_settings",
        lambda: {"exe_path": str(exe), "config_path": str(config_path)},
    )

    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    buffer_path = ce_bridge_manager.launch_ce_wizard("PN-123", ["ALT"])
    assert buffer_path.exists()
    payload = json.loads(buffer_path.read_text("utf-8"))
    assert payload == {"pn": "PN-123", "aliases": ["ALT"]}

    cmd = captured.get("cmd")
    assert isinstance(cmd, list)
    assert cmd[0] == str(exe)
    assert cmd[1] == "--load-buffer"
    assert cmd[2] == str(buffer_path)
    assert cmd[3:5] == ["--config", str(config_path)]


def test_launch_ce_wizard_without_config(monkeypatch, tmp_path):
    exe = tmp_path / "complex_editor.exe"
    exe.write_text("echo")
    exe.chmod(0o755)

    monkeypatch.setattr(
        ce_bridge_manager.config,
        "get_complex_editor_settings",
        lambda: {"exe_path": str(exe), "config_path": ""},
    )

    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return DummyProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    buffer_path = ce_bridge_manager.launch_ce_wizard("PN-123", None)
    assert buffer_path.exists()
    payload = json.loads(buffer_path.read_text("utf-8"))
    assert payload == {"pn": "PN-123"}

    cmd = captured.get("cmd")
    assert isinstance(cmd, list)
    assert "--config" not in cmd


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
    session = DummySession()
    session.get_handler = lambda *a, **kw: types.SimpleNamespace(ok=False, status_code=503)
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda: session)
    monkeypatch.setattr(
        ce_bridge_transport, "preflight_ready", lambda *a, **kw: {"ready": True}
    )

    with pytest.raises(ce_bridge_manager.CEBridgeError) as excinfo:
        ce_bridge_manager.ensure_ce_bridge_ready()

    diagnostics = excinfo.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.outcome == "error"
    assert "not executable" in diagnostics.reason


def test_timeout_records_diagnostics(monkeypatch, tmp_path):
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
            "base_url": "http://127.0.0.1:9500",
            "auth_token": "token",
            "request_timeout_seconds": 1,
        },
    }

    monkeypatch.setattr(ce_bridge_manager.config, "get_complex_editor_settings", lambda: settings)

    session = DummySession()

    def failing_get(*_args, **_kwargs):
        raise req_exc.ConnectionError()

    session.get_handler = failing_get
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda: session)
    monkeypatch.setattr(
        ce_bridge_transport, "preflight_ready", lambda *a, **kw: {"ready": True}
    )

    dummy_proc = DummyProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: dummy_proc)
    monkeypatch.setattr(ce_bridge_manager.time, "sleep", lambda _s: None)

    def fake_monotonic():
        fake_monotonic.value += 0.4
        return fake_monotonic.value

    fake_monotonic.value = 0.0
    monkeypatch.setattr(ce_bridge_manager.time, "monotonic", fake_monotonic)

    with pytest.raises(ce_bridge_manager.CEBridgeError) as excinfo:
        ce_bridge_manager.ensure_ce_bridge_ready(timeout_seconds=1.0)

    diagnostics = excinfo.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.outcome == "timeout"
    assert "Timed out" in diagnostics.reason
    assert diagnostics.traceback


def test_diagnostics_to_text_contains_summary():
    diagnostics = ce_bridge_manager.CEBridgeDiagnostics()
    now = datetime.datetime.now(datetime.timezone.utc)
    diagnostics.ts_start = now
    diagnostics.ts_end = now
    diagnostics.outcome = "timeout"
    diagnostics.reason = "Timed out waiting for Complex Editor bridge to start"
    diagnostics.base_url = "http://127.0.0.1:8765"
    diagnostics.spawn_attempted = True
    diagnostics.spawn_cmd_preview = ["complex_editor", "--token", "abc…123"]
    diagnostics.health_polls.append(
        {"t": "t+0.3s", "status": "not_running", "detail": "ConnectionRefusedError"}
    )
    diagnostics.traceback = "Traceback (most recent call last):\n..."

    text = diagnostics.to_text()
    assert "Outcome: timeout" in text
    assert "Base URL: http://127.0.0.1:8765" in text
    assert "Command (masked):" in text

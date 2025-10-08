import types
import subprocess
import time

import os
import pytest
import requests

from app.integration import ce_bridge_manager


@pytest.fixture(autouse=True)
def _reset_bridge_state():
    ce_bridge_manager._BRIDGE_PROCESS = None
    ce_bridge_manager._BRIDGE_AUTO_STOP = False
    ce_bridge_manager._LAST_DIAGNOSTICS = None
    yield
    ce_bridge_manager._BRIDGE_PROCESS = None
    ce_bridge_manager._BRIDGE_AUTO_STOP = False
    ce_bridge_manager._LAST_DIAGNOSTICS = None


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
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    popen_called = False

    def fake_popen(*_args, **_kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("Popen should not be called when bridge is healthy")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ce_bridge_manager.ensure_ce_bridge_ready()
    assert popen_called is False
    diagnostics = ce_bridge_manager.get_last_diagnostics()
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

    saved_tokens = {}
    monkeypatch.setattr(ce_bridge_manager.config, "get_complex_editor_settings", lambda: settings)
    monkeypatch.setattr(
        ce_bridge_manager.config,
        "save_complex_editor_settings",
        lambda **kwargs: saved_tokens.update(kwargs),
    )

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError()
        return types.SimpleNamespace(ok=True, status_code=200)

    monkeypatch.setattr(requests, "get", fake_get)

    dummy_proc = DummyProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: dummy_proc)

    # speed up wait loop
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    ce_bridge_manager.ensure_ce_bridge_ready()

    assert ce_bridge_manager._BRIDGE_PROCESS is dummy_proc
    assert "bridge_auth_token" in saved_tokens
    assert saved_tokens["bridge_auth_token"]
    assert dummy_proc.terminated is False
    diagnostics = ce_bridge_manager.get_last_diagnostics()
    assert diagnostics is not None
    assert diagnostics.outcome == "success"
    assert diagnostics.spawn_attempted is True
    assert diagnostics.spawn_cmd_preview is not None
    assert saved_tokens["bridge_auth_token"] not in diagnostics.spawn_cmd_preview
    assert diagnostics.health_polls
    report_text = diagnostics.to_text()
    assert "Command (masked):" in report_text


def test_ensure_timeout_captures_diagnostics(monkeypatch, tmp_path):
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
            "base_url": "http://127.0.0.1:9200",
            "auth_token": "token",
            "request_timeout_seconds": 2,
        },
    }

    monkeypatch.setattr(ce_bridge_manager.config, "get_complex_editor_settings", lambda: settings)
    monkeypatch.setattr(ce_bridge_manager.config, "save_complex_editor_settings", lambda **_kw: None)

    def failing_get(*_a, **_kw):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(requests, "get", failing_get)

    dummy_proc = DummyProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: dummy_proc)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with pytest.raises(ce_bridge_manager.CEBridgeError) as err:
        ce_bridge_manager.ensure_ce_bridge_ready(timeout_seconds=1.0)

    diagnostics = err.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.outcome == "timeout"
    assert diagnostics.spawn_attempted is True
    assert diagnostics.spawn_pid == dummy_proc.pid
    assert diagnostics.health_polls
    assert diagnostics.traceback
    report_text = diagnostics.to_text()
    assert "Outcome: timeout" in report_text
    assert "Base URL:" in report_text


def test_stop_bridge_closes_process(monkeypatch):
    proc = DummyProcess()
    ce_bridge_manager._BRIDGE_PROCESS = proc
    ce_bridge_manager._BRIDGE_AUTO_STOP = True

    ce_bridge_manager.stop_ce_bridge_if_started()
    assert proc.terminated is True


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
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: types.SimpleNamespace(ok=False, status_code=503),
    )

    with pytest.raises(ce_bridge_manager.CEBridgeError) as err:
        ce_bridge_manager.ensure_ce_bridge_ready()

    diagnostics = err.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.outcome == "error"
    assert "not executable" in (diagnostics.reason or "")
    assert "Executable:" in diagnostics.to_text()

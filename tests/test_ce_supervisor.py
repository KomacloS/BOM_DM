import stat
import sys
import types
from pathlib import Path

import pytest

from app.integration import ce_supervisor, ce_bridge_transport


class DummyProcess:
    def __init__(self, exit_code: int | None = None):
        self.pid = 4321
        self._poll = exit_code
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


@pytest.fixture(autouse=True)
def reset_supervisor_state():
    ce_supervisor._BRIDGE_PROCESS = None
    ce_supervisor._BRIDGE_AUTO_STOP = False
    ce_supervisor._LAST_DIAGNOSTICS = None
    ce_bridge_transport.reset_transport_state()
    yield
    ce_supervisor._BRIDGE_PROCESS = None
    ce_supervisor._BRIDGE_AUTO_STOP = False
    ce_supervisor._LAST_DIAGNOSTICS = None
    ce_bridge_transport.reset_transport_state()


def _default_settings(tmp_path: Path | None = None) -> dict:
    exe_path = ""
    if tmp_path is not None:
        exe = tmp_path / "complex_editor.exe"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(stat.S_IRWXU)
        exe_path = str(exe)
    return {
        "ui_enabled": True,
        "auto_start_bridge": True,
        "auto_stop_bridge_on_exit": False,
        "exe_path": exe_path,
        "config_path": "",
        "bridge": {
            "enabled": True,
            "base_url": "http://127.0.0.1:8765",
            "auth_token": "token",
            "request_timeout_seconds": 5,
        },
    }


def test_ensure_ready_short_circuits_when_bridge_ready(monkeypatch):
    settings = _default_settings()
    monkeypatch.setattr(ce_supervisor.config, "get_complex_editor_settings", lambda: settings)

    probe_calls = []

    def fake_probe(_base, _token, _timeout):
        probe_calls.append(True)
        return "ready", "ok", {"ready": True}

    monkeypatch.setattr(ce_supervisor, "_probe_bridge", fake_probe)
    monkeypatch.setattr(
        ce_supervisor.ce_bridge_transport,
        "preflight_ready",
        lambda *a, **k: {"ready": True},
    )

    ce_supervisor.ensure_ready()

    diagnostics = ce_supervisor._LAST_DIAGNOSTICS
    assert diagnostics is not None
    assert diagnostics.outcome == "success"
    assert diagnostics.pre_probe_status == "ready"
    assert probe_calls


def test_ensure_ready_spawns_ui_when_headless(monkeypatch, tmp_path):
    settings = _default_settings(tmp_path)
    monkeypatch.setattr(ce_supervisor.config, "get_complex_editor_settings", lambda: settings)

    call_state = {"count": 0}

    def fake_probe(_base, _token, _timeout):
        call_state["count"] += 1
        if call_state["count"] == 1:
            return (
                "warming",
                "headless",
                {"ready": False, "headless": True, "allow_headless": False},
            )
        return "ready", "ok", {"ready": True}

    monkeypatch.setattr(ce_supervisor, "_probe_bridge", fake_probe)
    launched = {"ui": False}
    monkeypatch.setattr(
        ce_supervisor,
        "_launch_bridge",
        lambda *a, **k: launched.__setitem__("ui", True),
    )
    monkeypatch.setattr(
        ce_supervisor,
        "_launch_uvicorn_fallback",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        ce_supervisor.ce_bridge_transport,
        "preflight_ready",
        lambda *a, **k: {"ready": True},
    )

    ce_supervisor.ensure_ready(timeout_seconds=2.0)

    diagnostics = ce_supervisor._LAST_DIAGNOSTICS
    assert diagnostics is not None
    assert diagnostics.outcome == "success"
    assert launched["ui"] is True
    assert call_state["count"] >= 2


def test_ensure_ready_uses_fallback_when_ui_missing(monkeypatch):
    settings = _default_settings()
    settings["exe_path"] = ""
    monkeypatch.setattr(ce_supervisor.config, "get_complex_editor_settings", lambda: settings)

    probe_state = {"count": 0}

    def fake_probe(_base, _token, _timeout):
        probe_state["count"] += 1
        if probe_state["count"] < 2:
            return "not_running", "missing", {"ready": False}
        return "ready", "ok", {"ready": True}

    monkeypatch.setattr(ce_supervisor, "_probe_bridge", fake_probe)
    monkeypatch.setattr(
        ce_supervisor,
        "_launch_bridge",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("UI launcher should not run")),
    )
    fallback_state = {"used": False}
    monkeypatch.setattr(
        ce_supervisor,
        "_launch_uvicorn_fallback",
        lambda *a, **k: fallback_state.__setitem__("used", True) or True,
    )
    monkeypatch.setattr(
        ce_supervisor.ce_bridge_transport,
        "preflight_ready",
        lambda *a, **k: {"ready": True},
    )

    ce_supervisor.ensure_ready(timeout_seconds=2.0)

    diagnostics = ce_supervisor._LAST_DIAGNOSTICS
    assert diagnostics is not None
    assert diagnostics.outcome == "success"
    assert fallback_state["used"] is True


def test_launch_uvicorn_fallback_requires_mdb_path(monkeypatch):
    diagnostics = ce_supervisor.CEBridgeDiagnostics()
    monkeypatch.delenv("CE_MDB_PATH", raising=False)

    result = ce_supervisor._launch_uvicorn_fallback(
        "127.0.0.1",
        8765,
        "token",
        True,
        diagnostics,
    )

    assert result is False
    assert diagnostics.spawn_error == "Set CE_MDB_PATH or provide Complex Editor UI path"
    assert diagnostics.spawn_attempted is False


def test_launch_uvicorn_fallback_spawns_runner(monkeypatch, tmp_path):
    diagnostics = ce_supervisor.CEBridgeDiagnostics()
    mdb_path = tmp_path / "Main.mdb"
    mdb_path.write_text("dummy")
    monkeypatch.setenv("CE_MDB_PATH", str(mdb_path))

    popen_calls: dict[str, object] = {}

    def fake_popen(cmd, env=None, **kwargs):
        popen_calls["cmd"] = cmd
        popen_calls["env"] = env
        return DummyProcess()

    monkeypatch.setattr(ce_supervisor.subprocess, "Popen", fake_popen)

    result = ce_supervisor._launch_uvicorn_fallback(
        "127.0.0.1",
        9000,
        "secret-token",
        False,
        diagnostics,
    )

    assert result is True
    assert diagnostics.spawn_attempted is True
    assert popen_calls["cmd"] == [
        sys.executable,
        "-m",
        "app.integration.ce_fallback_runner",
    ]
    env = popen_calls["env"]
    assert env["CE_BRIDGE_HOST"] == "127.0.0.1"
    assert env["CE_BRIDGE_PORT"] == "9000"
    assert env["CE_ALLOW_HEADLESS_EXPORTS"] == "0"
    assert env["CE_AUTH_TOKEN"] == "secret-token"


def test_ensure_ready_reports_missing_mdb(monkeypatch):
    settings = _default_settings()
    settings["exe_path"] = ""
    monkeypatch.setattr(ce_supervisor.config, "get_complex_editor_settings", lambda: settings)
    monkeypatch.delenv("CE_MDB_PATH", raising=False)

    def fake_probe(_base, _token, _timeout):
        return "not_running", "missing", {"ready": False}

    monkeypatch.setattr(ce_supervisor, "_probe_bridge", fake_probe)
    monkeypatch.setattr(
        ce_supervisor.ce_bridge_transport,
        "preflight_ready",
        lambda *a, **k: {"ready": True},
    )

    with pytest.raises(ce_supervisor.CEBridgeError) as exc_info:
        ce_supervisor.ensure_ready(timeout_seconds=1.0)

    assert "Set CE_MDB_PATH or provide Complex Editor UI path" in str(exc_info.value)


def test_stop_ce_bridge_if_started(monkeypatch):
    proc = DummyProcess()
    ce_supervisor._BRIDGE_PROCESS = proc
    ce_supervisor._BRIDGE_AUTO_STOP = True

    shutdown_called = {"count": 0}

    def fake_request(method, endpoint, timeout=5.0):
        if method == "GET":
            return types.SimpleNamespace(ok=True, status_code=200, json=lambda: {})
        shutdown_called["count"] += 1
        return types.SimpleNamespace(status_code=200)

    monkeypatch.setattr(ce_supervisor, "_bridge_request_without_ensure", fake_request)
    ce_supervisor.stop_ce_bridge_if_started()

    assert ce_supervisor._BRIDGE_PROCESS is None
    assert shutdown_called["count"] == 1


def test_launch_ce_wizard_requires_executable(monkeypatch):
    settings = _default_settings()
    settings["exe_path"] = ""
    monkeypatch.setattr(ce_supervisor.config, "get_complex_editor_settings", lambda: settings)

    with pytest.raises(ce_supervisor.CEBridgeError):
        ce_supervisor.launch_ce_wizard("PN-1")


def test_launch_ce_wizard_invokes_subprocess(monkeypatch, tmp_path):
    settings = _default_settings(tmp_path)
    monkeypatch.setattr(ce_supervisor.config, "get_complex_editor_settings", lambda: settings)

    called = {"cmd": None}

    def fake_popen(cmd, **_kwargs):
        called["cmd"] = cmd

    monkeypatch.setattr(ce_supervisor.subprocess, "Popen", fake_popen)

    buffer_path = ce_supervisor.launch_ce_wizard("PN-2", ["ALT"])

    assert buffer_path.exists()
    assert called["cmd"] is not None

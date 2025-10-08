from __future__ import annotations

import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from requests import exceptions as req_exc

from app import config
from .ce_bridge_diagnostics import (
    CEBridgeDiagnostics,
    effective_probe_host,
    is_localhost,
    mask_token,
    path_info,
    port_busy,
    redact_command,
    short_exc,
)

logger = logging.getLogger(__name__)

_BRIDGE_PROCESS: Optional[subprocess.Popen] = None
_BRIDGE_AUTO_STOP = False
_LAST_DIAGNOSTICS: Optional[CEBridgeDiagnostics] = None


class CEBridgeError(RuntimeError):
    """Raised when the Complex Editor bridge cannot be ensured."""


def get_last_ce_bridge_diagnostics() -> Optional[CEBridgeDiagnostics]:
    return _LAST_DIAGNOSTICS


def _probe_bridge(base_url: str, token: str, timeout: int) -> Tuple[str, Optional[str]]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = urljoin(base_url.rstrip("/") + "/", "health")
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except (req_exc.Timeout, req_exc.ConnectionError) as exc:
        return "not_running", short_exc(exc)
    except req_exc.RequestException as exc:  # pragma: no cover - rare
        return "other_service", short_exc(exc)

    code = getattr(response, "status_code", 0) or 0
    if 200 <= code < 300:
        return "running", f"HTTP {code}"
    if code in (401, 403):
        return "unauthorized", f"HTTP {code}"
    if code in (404, 410):
        return "other_service", f"HTTP {code}"
    return "other_service", f"HTTP {code}"


def _launch_bridge(
    exe: Path,
    config_path: Optional[str],
    port: int,
    token: str,
    auto_stop: bool,
    diagnostics: CEBridgeDiagnostics,
) -> None:
    global _BRIDGE_PROCESS, _BRIDGE_AUTO_STOP
    if _BRIDGE_PROCESS is not None and _BRIDGE_PROCESS.poll() is None:
        diagnostics.spawn_attempted = False
        logger.debug("Complex Editor bridge process already running (pid=%s)", _BRIDGE_PROCESS.pid)
        return

    diagnostics.spawn_attempted = True
    cmd = [str(exe), "--start-bridge", "--port", str(port), "--token", token]
    if config_path:
        cmd.extend(["--config", config_path])
    diagnostics.spawn_cmd_preview = redact_command(cmd, token)

    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    try:
        proc = subprocess.Popen(
            cmd,
            creationflags=creationflags,
            close_fds=os.name != "nt",
            startupinfo=startupinfo,
        )
    except Exception as exc:  # pragma: no cover - spawn failure should surface
        diagnostics.spawn_error = short_exc(exc)
        raise CEBridgeError(f"Failed to launch Complex Editor bridge: {exc}") from exc
    _BRIDGE_PROCESS = proc
    _BRIDGE_AUTO_STOP = auto_stop
    diagnostics.spawn_pid = proc.pid
    logger.info("Started Complex Editor bridge (pid=%s)", proc.pid)


def _raise_with_diagnostics(
    message: str,
    diagnostics: CEBridgeDiagnostics,
    outcome: str = "error",
    exc: Optional[BaseException] = None,
) -> None:
    global _LAST_DIAGNOSTICS
    diagnostics.finalize(outcome, message)
    diagnostics.attach_traceback(exc)
    err = CEBridgeError(message)
    err.diagnostics = diagnostics
    _LAST_DIAGNOSTICS = diagnostics
    logger.warning("Bridge diagnostics available (%s)", outcome)
    if exc is not None:
        raise err from exc
    raise err


def ensure_ce_bridge_ready(timeout_seconds: float = 4.0) -> None:
    """Ensure the Complex Editor bridge is reachable, spawning if allowed."""
    global _LAST_DIAGNOSTICS
    diagnostics = CEBridgeDiagnostics()
    _LAST_DIAGNOSTICS = diagnostics

    settings = config.get_complex_editor_settings()
    if not isinstance(settings, dict):
        diagnostics.finalize("success", "Complex Editor settings unavailable")
        return

    diagnostics.ui_enabled = bool(settings.get("ui_enabled", True))
    diagnostics.auto_start_bridge = bool(settings.get("auto_start_bridge", True))
    diagnostics.auto_stop_bridge_on_exit = bool(settings.get("auto_stop_bridge_on_exit", False))

    if not diagnostics.ui_enabled:
        diagnostics.finalize("success", "Complex Editor UI disabled")
        return

    bridge_cfg = settings.get("bridge", {})
    if not isinstance(bridge_cfg, dict) or not bridge_cfg.get("enabled", True):
        diagnostics.finalize("success", "Bridge disabled")
        return

    base_url = str(bridge_cfg.get("base_url") or "http://127.0.0.1:8765").strip()
    token = str(bridge_cfg.get("auth_token") or "").strip()
    timeout = int(bridge_cfg.get("request_timeout_seconds") or 10)
    if timeout <= 0:
        timeout = 10

    diagnostics.base_url = base_url
    parsed = urlparse(base_url or "http://127.0.0.1:8765")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765
    diagnostics.host = host
    diagnostics.probe_host = effective_probe_host(host)
    diagnostics.port = port
    diagnostics.is_localhost = is_localhost(host)
    diagnostics.auth_token_preview = mask_token(token)

    pre_status, pre_detail = _probe_bridge(base_url, token, timeout)
    diagnostics.pre_probe_status = pre_status
    diagnostics.pre_probe_detail = pre_detail
    if pre_status == "running":
        diagnostics.finalize("success", "Bridge already running")
        return

    if not diagnostics.is_localhost:
        _raise_with_diagnostics(f"Complex Editor bridge at {base_url} is unreachable", diagnostics)

    if not diagnostics.auto_start_bridge:
        _raise_with_diagnostics(
            "Complex Editor bridge is not running and auto-start is disabled",
            diagnostics,
        )

    exe_path = str(settings.get("exe_path") or "").strip()
    diagnostics.exe_path = exe_path or None
    if not exe_path:
        _raise_with_diagnostics("Complex Editor executable path is not configured", diagnostics)

    exe = Path(exe_path).expanduser().resolve()
    exists, is_dir, exec_ok = path_info(exe)
    diagnostics.exe_exists = exists
    diagnostics.exe_is_dir = is_dir
    diagnostics.exe_exec_ok = exec_ok

    if not exists:
        _raise_with_diagnostics(f"Complex Editor executable not found: {exe}", diagnostics)
    if is_dir:
        _raise_with_diagnostics(f"Complex Editor executable path is a directory: {exe}", diagnostics)
    if os.name != "nt" and exec_ok is False:
        _raise_with_diagnostics(f"Complex Editor executable is not executable: {exe}", diagnostics)

    config_path = str(settings.get("config_path") or "").strip() or None
    diagnostics.config_path = config_path
    if config_path:
        diagnostics.config_exists = Path(config_path).expanduser().resolve().exists()

    if not token:
        token = uuid.uuid4().hex
        config.save_complex_editor_settings(bridge_auth_token=token)
        diagnostics.auth_token_preview = mask_token(token)

    diagnostics.port_busy_before = port_busy(diagnostics.probe_host or "127.0.0.1", port)

    try:
        _launch_bridge(
            exe,
            config_path,
            port,
            token,
            bool(settings.get("auto_stop_bridge_on_exit", False)),
            diagnostics,
        )
    except CEBridgeError as exc:
        _raise_with_diagnostics(str(exc), diagnostics, exc=exc.__cause__ or exc)

    diagnostics.port_busy_after = port_busy(diagnostics.probe_host or "127.0.0.1", port)

    deadline = time.monotonic() + max(timeout_seconds, 1.0)
    poll_origin = time.monotonic()

    while time.monotonic() < deadline:
        time.sleep(0.3)
        status, detail = _probe_bridge(base_url, token, timeout)
        diagnostics.add_health_poll(time.monotonic() - poll_origin, status, detail)
        if status == "running":
            diagnostics.finalize("success", "Bridge started")
            return
        if status == "unauthorized":
            _raise_with_diagnostics("Complex Editor bridge reported unauthorized", diagnostics)
    _raise_with_diagnostics("Timed out waiting for Complex Editor bridge to start", diagnostics, outcome="timeout")


def stop_ce_bridge_if_started() -> None:
    global _BRIDGE_PROCESS, _BRIDGE_AUTO_STOP
    if _BRIDGE_PROCESS is None:
        return
    if not _BRIDGE_AUTO_STOP:
        return
    proc = _BRIDGE_PROCESS
    if proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:  # pragma: no cover - best effort
            pass
    _BRIDGE_PROCESS = None
    _BRIDGE_AUTO_STOP = False

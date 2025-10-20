from __future__ import annotations

import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

from requests import exceptions as req_exc

from app import config
from app.integration import ce_bridge_transport
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
_BRIDGE_PID: Optional[int] = None
_BRIDGE_EXE_PATH: Optional[Path] = None
_BRIDGE_AUTO_STOP = False
_LAST_DIAGNOSTICS: Optional[CEBridgeDiagnostics] = None

_BRIDGE_BASE_URL: Optional[str] = None
_BRIDGE_TOKEN: Optional[str] = None
_BRIDGE_TIMEOUT: Optional[float] = None
_BRIDGE_IS_LOCALHOST: bool = False
_BRIDGE_STARTED_BY_APP: bool = False


class CEBridgeError(RuntimeError):
    """Raised when the Complex Editor bridge cannot be ensured."""


def get_last_ce_bridge_diagnostics() -> Optional[CEBridgeDiagnostics]:
    return _LAST_DIAGNOSTICS


def bridge_owned_for_url(base_url: str) -> bool:
    return bool(_BRIDGE_STARTED_BY_APP and _BRIDGE_BASE_URL == base_url)


def record_bridge_action(text: str) -> None:
    if _LAST_DIAGNOSTICS is not None:
        _LAST_DIAGNOSTICS.add_action(text)


def record_state_snapshot(payload: Dict[str, Any]) -> None:
    if _LAST_DIAGNOSTICS is not None:
        _LAST_DIAGNOSTICS.last_state_payload = payload


def record_health_detail(detail: Optional[str]) -> None:
    if detail and _LAST_DIAGNOSTICS is not None:
        _LAST_DIAGNOSTICS.last_health_detail = detail


def _probe_bridge(base_url: str, token: str, timeout: int) -> Tuple[str, Optional[str]]:
    session = ce_bridge_transport.get_session(base_url)
    headers = ce_bridge_transport.build_headers(token, None)
    url = urljoin(base_url.rstrip("/") + "/", "health")
    try:
        response = session.get(url, headers=headers, timeout=timeout)
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
    *,
    with_ui: bool,
) -> None:
    global _BRIDGE_PROCESS, _BRIDGE_PID, _BRIDGE_EXE_PATH, _BRIDGE_AUTO_STOP, _BRIDGE_STARTED_BY_APP
    if _BRIDGE_PROCESS is not None and _BRIDGE_PROCESS.poll() is None:
        diagnostics.spawn_attempted = False
        logger.debug("Complex Editor bridge process already running (pid=%s)", _BRIDGE_PROCESS.pid)
        return

    diagnostics.spawn_attempted = True
    cmd = [str(exe), "--start-bridge", "--port", str(port), "--token", token]
    if config_path:
        cmd.extend(["--config", config_path])
    if with_ui:
        cmd.append("--with-ui")
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
    _BRIDGE_PID = proc.pid
    _BRIDGE_EXE_PATH = exe
    _BRIDGE_AUTO_STOP = auto_stop
    _BRIDGE_STARTED_BY_APP = True
    diagnostics.spawn_pid = proc.pid
    diagnostics.add_action("Spawned Complex Editor bridge (with_ui=%s)" % with_ui)
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


def ensure_ce_bridge_ready(timeout_seconds: float = 4.0, *, require_ui: bool = False) -> None:
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
    probe_host = effective_probe_host(host)
    is_local = is_localhost(host)
    diagnostics.host = host
    diagnostics.probe_host = probe_host
    diagnostics.port = port
    diagnostics.is_localhost = is_local
    diagnostics.auth_token_preview = mask_token(token)

    global _BRIDGE_BASE_URL, _BRIDGE_TOKEN, _BRIDGE_TIMEOUT, _BRIDGE_IS_LOCALHOST
    _BRIDGE_BASE_URL = base_url
    _BRIDGE_TOKEN = token
    _BRIDGE_TIMEOUT = float(timeout) if timeout else None
    _BRIDGE_IS_LOCALHOST = bool(is_local)

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
            with_ui=require_ui,
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
        record_health_detail(detail)
        if status == "running":
            diagnostics.finalize("success", "Bridge started")
            return
        if status == "unauthorized":
            _raise_with_diagnostics("Complex Editor bridge reported unauthorized", diagnostics)
    _raise_with_diagnostics("Timed out waiting for Complex Editor bridge to start", diagnostics, outcome="timeout")


def stop_ce_bridge_if_started(*, force: bool = False) -> None:
    global _BRIDGE_PROCESS, _BRIDGE_PID, _BRIDGE_EXE_PATH, _BRIDGE_AUTO_STOP, _BRIDGE_STARTED_BY_APP
    if not _BRIDGE_STARTED_BY_APP:
        return
    if not force and not _BRIDGE_AUTO_STOP:
        return

    shutdown_allowed = False
    if _BRIDGE_BASE_URL:
        session = ce_bridge_transport.get_session(_BRIDGE_BASE_URL)
        headers = ce_bridge_transport.build_headers(_BRIDGE_TOKEN, None)

        try:
            state_url = urljoin(_BRIDGE_BASE_URL.rstrip("/") + "/", "state")
            response = session.get(state_url, headers=headers, timeout=_BRIDGE_TIMEOUT or 10.0)
            if response.status_code == 200:
                payload = response.json()
            else:
                payload = {}
        except Exception:
            payload = {}

        if isinstance(payload, dict):
            unsaved = bool(payload.get("unsaved_changes"))
            wizard_open = bool(payload.get("wizard_open"))
            record_state_snapshot(payload)  # cache latest state view
            if unsaved or wizard_open:
                logger.info(
                    "Skipping Complex Editor shutdown (unsaved_changes=%s, wizard_open=%s)",
                    unsaved,
                    wizard_open,
                )
                return
            shutdown_allowed = True
            try:
                admin_url = urljoin(_BRIDGE_BASE_URL.rstrip("/") + "/", "admin/shutdown")
                admin_headers = ce_bridge_transport.build_headers(
                    _BRIDGE_TOKEN,
                    None,
                    content_type="application/json",
                )
                response = session.post(
                    admin_url,
                    headers=admin_headers,
                    json={},
                    timeout=_BRIDGE_TIMEOUT or 10.0,
                )
                if 200 <= response.status_code < 300:
                    logger.info("Requested Complex Editor shutdown via admin API")
                    record_bridge_action("Issued /admin/shutdown request before stopping bridge")
                else:
                    logger.warning(
                        "Complex Editor shutdown request returned HTTP %s",
                        response.status_code,
                    )
            except Exception:
                logger.warning("Failed to request Complex Editor shutdown", exc_info=True)

    proc = _BRIDGE_PROCESS
    pid = _BRIDGE_PID if _BRIDGE_PID is not None else (proc.pid if proc is not None else None)
    if proc is not None and proc.poll() is None:
        if shutdown_allowed:
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                logger.info("Bridge process still running after shutdown request; terminating.")
                record_bridge_action("Bridge still running post shutdown request; terminating process")
        if proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    record_bridge_action("Bridge force-killed after terminate timeout")
            except Exception:  # pragma: no cover - best effort
                pass
    if os.name == "nt" and pid is not None:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:  # pragma: no cover - best effort
            pass
    exe_path = _BRIDGE_EXE_PATH
    if os.name == "nt" and exe_path is not None:
        try:
            escaped_exe_path = str(exe_path).replace("'", "''")
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-Process | Where-Object { $_.Path -eq "
                        f"'{escaped_exe_path}' }} | "
                        "Stop-Process -Force"
                    ),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:  # pragma: no cover - best effort
            pass
    _BRIDGE_PROCESS = None
    _BRIDGE_PID = None
    _BRIDGE_EXE_PATH = None
    _BRIDGE_AUTO_STOP = False
    _BRIDGE_STARTED_BY_APP = False
    record_bridge_action("Bridge stop_closure complete (force=%s)" % force)


def restart_bridge_with_ui(timeout_seconds: float) -> None:
    if not (_BRIDGE_STARTED_BY_APP and _BRIDGE_BASE_URL):
        raise CEBridgeError("Complex Editor bridge is not owned by BOM_DB.")
    record_bridge_action("Restarting Complex Editor bridge with UI uplift")
    stop_ce_bridge_if_started(force=True)
    ensure_ce_bridge_ready(timeout_seconds=timeout_seconds, require_ui=True)

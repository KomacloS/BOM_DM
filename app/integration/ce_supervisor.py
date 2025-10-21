from __future__ import annotations

import ipaddress
import json
import logging
import os
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from contextlib import suppress
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

from requests import Response
from requests import exceptions as req_exc

from app import config

logger = logging.getLogger(__name__)

_BRIDGE_PROCESS: Optional[subprocess.Popen] = None
_BRIDGE_AUTO_STOP = False
_LAST_DIAGNOSTICS: "CEBridgeDiagnostics | None" = None


def get_ce_app_exe(settings: Optional[Dict[str, Any]] = None) -> str:
    """Return the Complex Editor executable path, honouring env overrides."""

    env_override = os.getenv("CE_APP_EXE")
    if env_override and env_override.strip():
        return env_override.strip()

    if settings is None:
        settings = config.get_complex_editor_settings()
    if isinstance(settings, dict):
        candidate = settings.get("app_exe_path") or settings.get("exe_path")
        if candidate:
            text = str(candidate).strip()
            if text:
                return text
    return ""


class CEBridgeError(RuntimeError):
    """Raised when the Complex Editor bridge cannot be ensured."""

    def __init__(self, message: str, *, diagnostics: "CEBridgeDiagnostics | None" = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics

# Import after CEBridgeError is defined to avoid a circular import with ce_bridge_transport
from app.integration import ce_bridge_transport


@dataclass
class CEBridgeDiagnostics:
    ts_start: datetime | None = None
    ts_end: datetime | None = None
    ui_enabled: bool | None = None
    auto_start_bridge: bool | None = None
    auto_stop_bridge_on_exit: bool | None = None
    exe_path: str = ""
    exe_resolved: str = ""
    exe_exists: bool | None = None
    exe_is_dir: bool | None = None
    exe_exec_ok: bool | None = None
    config_path: str = ""
    config_resolved: str = ""
    config_exists: bool | None = None
    base_url: str = ""
    host: str = ""
    probe_host: str = ""
    port: int | None = None
    is_localhost: bool | None = None
    auth_token_preview: str = ""
    pre_probe_status: str = ""
    pre_probe_detail: str = ""
    spawn_attempted: bool = False
    spawn_cmd_preview: List[str] = field(default_factory=list)
    spawn_pid: int | None = None
    spawn_error: str = ""
    health_polls: List[dict[str, str]] = field(default_factory=list)
    health_payload: dict[str, Any] = field(default_factory=dict)
    headless: bool | None = None
    allow_headless: bool | None = None
    port_check_summary: str = ""
    outcome: str = ""
    reason: str = ""
    traceback: str = ""

    def record_path_info(
        self, path: str, *, is_executable_check: bool = False
    ) -> Tuple[str, str, bool, bool, bool | None]:
        raw = path or ""
        resolved_path = raw
        exists = False
        is_dir = False
        exec_ok: bool | None = None
        if raw:
            try:
                resolved = Path(raw).expanduser().resolve()
                resolved_path = str(resolved)
                exists = resolved.exists()
                is_dir = resolved.is_dir()
                if is_executable_check and os.name != "nt":
                    exec_ok = os.access(resolved, os.X_OK)
                elif is_executable_check:
                    exec_ok = True
            except Exception:  # pragma: no cover - defensive resolution failure
                resolved_path = raw
        return raw, resolved_path, exists, is_dir, exec_ok

    def add_health_poll(self, offset: float, status: str, detail: str) -> None:
        if len(self.health_polls) >= 10:
            return
        formatted = f"t+{offset:.1f}s"
        entry = {"t": formatted, "status": status, "detail": detail}
        self.health_polls.append(entry)

    def to_text(self) -> str:
        lines: List[str] = []
        lines.append("BOM_DB ↔ Complex Editor Bridge Diagnostics")
        if self.ts_start:
            lines.append(
                f"Timestamp (start): {self.ts_start.astimezone(timezone.utc).isoformat()}"
            )
        if self.ts_end:
            lines.append(
                f"Timestamp (end): {self.ts_end.astimezone(timezone.utc).isoformat()}"
            )
        if self.outcome:
            lines.append(f"Outcome: {self.outcome}")
        if self.reason:
            lines.append(f"Reason: {self.reason}")
        lines.append("")
        lines.append("[Settings Snapshot]")
        lines.append(f"UI Enabled: {self.ui_enabled}")
        lines.append(f"Auto-start: {self.auto_start_bridge}")
        lines.append(f"Auto-stop on exit: {self.auto_stop_bridge_on_exit}")
        exe_info = (
            f"Executable: {self.exe_resolved or self.exe_path or '<unset>'} "
            f"(exists: {self.exe_exists}, dir: {self.exe_is_dir}, exec_ok: {self.exe_exec_ok})"
        )
        lines.append(exe_info)
        cfg_info = (
            f"Config file: {self.config_resolved or self.config_path or '<unset>'} "
            f"(exists: {self.config_exists})"
        )
        lines.append(cfg_info)
        if self.base_url:
            lines.append(f"Base URL: {self.base_url}")
        if self.host:
            probe = f"{self.host}"
            if self.probe_host and self.probe_host != self.host:
                probe = f"{self.host} → {self.probe_host}"
            lines.append(f"Host (configured/probe): {probe}")
        if self.port is not None:
            lines.append(f"Port: {self.port}")
        if self.is_localhost is not None:
            lines.append(f"Is localhost: {self.is_localhost}")
        if self.headless is not None:
            lines.append(
                f"Headless: {self.headless} (allow_headless: {self.allow_headless})"
            )
        if self.auth_token_preview:
            lines.append(f"Auth Token: {self.auth_token_preview}")
        if self.port_check_summary:
            lines.append(f"Port Check: {self.port_check_summary}")
        lines.append("")
        if self.pre_probe_status:
            lines.append("[Pre-Healthcheck]")
            lines.append(f"Status: {self.pre_probe_status}")
            if self.pre_probe_detail:
                lines.append(f"Detail: {self.pre_probe_detail}")
            lines.append("")
        if self.spawn_attempted:
            lines.append("[Spawn]")
            lines.append(f"Attempted: {self.spawn_attempted}")
            if self.spawn_cmd_preview:
                cmd_line = ", ".join(f'"{p}"' for p in self.spawn_cmd_preview)
                lines.append(f"Command (masked): [{cmd_line}]")
            lines.append(f"PID: {self.spawn_pid}")
            lines.append(f"Error: {self.spawn_error or '<none>'}")
            lines.append("")
        if self.health_polls:
            lines.append("[Post-Spawn Health Polls]")
            for poll in self.health_polls:
                detail = f" ({poll['detail']})" if poll.get("detail") else ""
                lines.append(f"{poll['t']}: {poll['status']}{detail}")
            lines.append("")
        lines.append("[Traceback]")
        tb = self.traceback.strip()
        if tb:
            lines.append(tb)
        else:
            lines.append("<none>")
        text = "\n".join(lines)
        if len(text) > 200_000:
            return text[:199_000] + "\n<trimmed>"
        return text


from app.integration import ce_bridge_transport


def _mask_token(token: str) -> str:
    token = token or ""
    if not token:
        return "<empty>"
    if len(token) <= 6:
        return token[:3] + "…" + token[-3:]
    return token[:3] + "…" + token[-3:]


def _is_local_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() in {"localhost", ""}


def _effective_probe_host(host: str) -> str:
    lowered = (host or "").strip().lower()
    if lowered in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return host


def _short_exc(exc: BaseException) -> str:
    name = exc.__class__.__name__
    msg = str(exc)
    if msg:
        return f"{name}: {msg}"
    return name


def _probe_bridge(base_url: str, token: str, timeout: int) -> Tuple[str, str, Dict[str, Any]]:
    headers = ce_bridge_transport.build_headers(token)
    url = urljoin(base_url.rstrip("/") + "/", "admin/health")
    session = ce_bridge_transport.get_session()
    try:
        response = session.get(url, headers=headers, timeout=timeout)
    except req_exc.Timeout as exc:
        return "not_running", _short_exc(exc), {}
    except req_exc.ConnectionError as exc:
        return "not_running", _short_exc(exc), {}
    except req_exc.RequestException as exc:
        return "other_service", _short_exc(exc), {}

    payload: Dict[str, Any] = {}
    if response.content:
        try:
            parsed = response.json()
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed

    def _detail_from_payload(data: Dict[str, Any]) -> str:
        for key in ("detail", "reason", "message", "status"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    if response.ok:
        detail = _detail_from_payload(payload) or f"HTTP {response.status_code}"
        state = "ready" if payload.get("ready") else "warming"
        return state, detail, payload

    if response.status_code in (401, 403):
        detail = _detail_from_payload(payload) or f"HTTP {response.status_code}"
        return "unauthorized", detail, payload

    return "other_service", f"HTTP {response.status_code}", payload


def _healthcheck(base_url: str, token: str, timeout: int) -> bool:
    status, _detail, payload = _probe_bridge(base_url, token, timeout)
    if status == "ready":
        return True
    return bool(payload.get("ready"))


def _mask_spawn_cmd(cmd: Sequence[str], token: str) -> List[str]:
    masked = []
    for part in cmd:
        if token and part == token:
            masked.append(_mask_token(token))
        else:
            masked.append(part)
    return list(masked)


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
        logger.debug("Complex Editor bridge process already running (pid=%s)", _BRIDGE_PROCESS.pid)
        return
    cmd = [str(exe), "--start-bridge", "--port", str(port), "--token", token]
    if config_path:
        cmd.extend(["--config", config_path])
    diagnostics.spawn_attempted = True
    diagnostics.spawn_cmd_preview = _mask_spawn_cmd(cmd, token)
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
        diagnostics.spawn_error = _short_exc(exc)
        diagnostics.traceback = traceback.format_exc()
        diagnostics.outcome = diagnostics.outcome or "error"
        diagnostics.reason = f"Failed to launch Complex Editor bridge: {exc}"
        diagnostics.ts_end = datetime.now(timezone.utc)
        err = CEBridgeError(
            f"Failed to launch Complex Editor bridge: {exc}", diagnostics=diagnostics
        )
        logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
        raise err from exc
    _BRIDGE_PROCESS = proc
    _BRIDGE_AUTO_STOP = auto_stop
    diagnostics.spawn_pid = proc.pid
    logger.info("Started Complex Editor bridge (pid=%s)", proc.pid)


def _launch_uvicorn_fallback(
    host: str,
    port: int,
    token: str,
    allow_headless: bool,
    diagnostics: CEBridgeDiagnostics,
) -> bool:
    """Start the bridge service via ``uvicorn`` as a fallback."""

    global _BRIDGE_PROCESS, _BRIDGE_AUTO_STOP

    env = os.environ.copy()
    mdb_path = env.get("CE_MDB_PATH")
    if not mdb_path:
        diagnostics.spawn_error = "Set CE_MDB_PATH or provide Complex Editor UI path"
        return False

    cmd = [
        sys.executable,
        "-m",
        "app.integration.ce_fallback_runner",
    ]
    diagnostics.spawn_attempted = True
    diagnostics.spawn_cmd_preview = _mask_spawn_cmd(cmd, token)

    env.setdefault("CE_BRIDGE_HOST", host)
    env.setdefault("CE_BRIDGE_PORT", str(port))
    if token:
        env["CE_AUTH_TOKEN"] = token
    env["CE_ALLOW_HEADLESS_EXPORTS"] = "1" if allow_headless else "0"
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            creationflags=creationflags,
            close_fds=os.name != "nt",
            startupinfo=startupinfo,
        )
    except Exception as exc:  # pragma: no cover - spawn failure should surface
        diagnostics.spawn_error = _short_exc(exc)
        diagnostics.traceback = traceback.format_exc()
        diagnostics.outcome = diagnostics.outcome or "error"
        diagnostics.reason = f"Failed to launch fallback bridge: {exc}"
        diagnostics.ts_end = datetime.now(timezone.utc)
        logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
        return False

    _BRIDGE_PROCESS = proc
    _BRIDGE_AUTO_STOP = True
    diagnostics.spawn_pid = proc.pid
    logger.info("Started fallback Complex Editor bridge (pid=%s)", proc.pid)
    return True


def ensure_ready(timeout_seconds: float = 20.0, *, trace_id: str | None = None) -> None:
    """Ensure the Complex Editor bridge is reachable, spawning if required."""

    global _LAST_DIAGNOSTICS
    diagnostics = CEBridgeDiagnostics(ts_start=datetime.now(timezone.utc))
    _LAST_DIAGNOSTICS = diagnostics

    try:
        settings = config.get_complex_editor_settings()
        if not isinstance(settings, dict):
            diagnostics.reason = "Complex Editor settings not available"
            diagnostics.outcome = "success"
            diagnostics.ts_end = datetime.now(timezone.utc)
            return

        diagnostics.ui_enabled = bool(settings.get("ui_enabled", True))
        if not diagnostics.ui_enabled:
            diagnostics.reason = "Complex Editor UI disabled"
            diagnostics.outcome = "success"
            diagnostics.ts_end = datetime.now(timezone.utc)
            return

        bridge_cfg = settings.get("bridge", {})
        if not isinstance(bridge_cfg, dict) or not bridge_cfg.get("enabled", True):
            diagnostics.reason = "Bridge disabled"
            diagnostics.outcome = "success"
            diagnostics.ts_end = datetime.now(timezone.utc)
            return

        from . import ce_bridge_client  # Local import to avoid circular dependency

        base_url, token, request_timeout = ce_bridge_client.resolve_bridge_connection()
        diagnostics.base_url = base_url
        diagnostics.auth_token_preview = _mask_token(token)
        diagnostics.auto_start_bridge = bool(settings.get("auto_start_bridge", True))
        diagnostics.auto_stop_bridge_on_exit = bool(
            settings.get("auto_stop_bridge_on_exit", False)
        )

        parsed = urlparse(base_url or "http://127.0.0.1:8765")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8765
        diagnostics.host = host
        diagnostics.port = port
        diagnostics.is_localhost = _is_local_host(host)
        diagnostics.probe_host = _effective_probe_host(host)

        exe_path = get_ce_app_exe(settings)
        raw_exe, resolved_exe, exists, is_dir, exec_ok = diagnostics.record_path_info(
            exe_path, is_executable_check=True
        )
        diagnostics.exe_path = raw_exe
        diagnostics.exe_resolved = resolved_exe
        diagnostics.exe_exists = exists
        diagnostics.exe_is_dir = is_dir
        diagnostics.exe_exec_ok = exec_ok

        raw_cfg, resolved_cfg, cfg_exists, _, _ = diagnostics.record_path_info(
            str(settings.get("config_path") or "")
        )
        diagnostics.config_path = raw_cfg
        diagnostics.config_resolved = resolved_cfg
        diagnostics.config_exists = cfg_exists

        try:
            request_timeout_s = float(request_timeout or 10)
        except (TypeError, ValueError):
            request_timeout_s = 10.0
        if request_timeout_s <= 0:
            request_timeout_s = 10.0

        status, detail, payload = _probe_bridge(base_url, token, int(max(request_timeout_s, 1)))
        diagnostics.pre_probe_status = status
        diagnostics.pre_probe_detail = detail
        if isinstance(payload, dict):
            diagnostics.health_payload = payload
            if "headless" in payload:
                diagnostics.headless = bool(payload.get("headless"))
            if "allow_headless" in payload:
                diagnostics.allow_headless = bool(payload.get("allow_headless"))

        headless = bool(payload.get("headless")) if isinstance(payload, dict) else False
        allow_headless = bool(payload.get("allow_headless")) if isinstance(payload, dict) else False

        if status == "unauthorized":
            diagnostics.outcome = "error"
            diagnostics.reason = "Complex Editor authentication failed"
            diagnostics.ts_end = datetime.now(timezone.utc)
            raise CEBridgeError(diagnostics.reason, diagnostics=diagnostics)

        if status not in {"not_running", "other_service"} and not (
            headless and not allow_headless
        ):
            try:
                state_payload = ce_bridge_transport.preflight_ready(
                    base_url,
                    token,
                    request_timeout_s=request_timeout_s,
                    trace_id=trace_id,
                )
            except CEBridgeError as exc:
                diagnostics.outcome = "error"
                diagnostics.reason = str(exc)
                diagnostics.traceback = "".join(traceback.format_stack(limit=10))
                diagnostics.ts_end = datetime.now(timezone.utc)
                exc.diagnostics = exc.diagnostics or diagnostics
                logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
                raise

            diagnostics.outcome = "success"
            diagnostics.reason = (
                "Bridge ready"
                if not isinstance(state_payload, dict)
                else f"Bridge ready ({state_payload.get('reason') or 'ok'})"
            )
            diagnostics.ts_end = datetime.now(timezone.utc)
            return

        if not diagnostics.is_localhost:
            diagnostics.outcome = "error"
            diagnostics.reason = f"Complex Editor bridge at {base_url} is unreachable"
            diagnostics.traceback = "".join(traceback.format_stack(limit=10))
            diagnostics.ts_end = datetime.now(timezone.utc)
            err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
            logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
            raise err

        if not diagnostics.auto_start_bridge:
            diagnostics.outcome = "error"
            diagnostics.reason = "Complex Editor bridge is not running and auto-start is disabled"
            diagnostics.traceback = "".join(traceback.format_stack(limit=10))
            diagnostics.ts_end = datetime.now(timezone.utc)
            err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
            logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
            raise err

        if not token:
            token = uuid.uuid4().hex
            config.save_complex_editor_settings(bridge_auth_token=token)
            diagnostics.auth_token_preview = _mask_token(token)

        config_path = (diagnostics.config_resolved or diagnostics.config_path or "").strip()
        config_path = config_path or None

        exe_candidate = (diagnostics.exe_resolved or diagnostics.exe_path or "").strip()
        launch_failure_reason: Optional[str] = None
        launched = False

        if exe_candidate:
            exe = Path(exe_candidate).expanduser().resolve()
            if not exe.exists():
                launch_failure_reason = f"Complex Editor executable not found: {exe}"
            elif exe.is_dir():
                launch_failure_reason = f"Complex Editor executable path is a directory: {exe}"
            elif os.name != "nt" and not os.access(exe, os.X_OK):
                launch_failure_reason = f"Complex Editor executable is not executable: {exe}"
            else:
                try:
                    _launch_bridge(
                        exe,
                        config_path,
                        port,
                        token,
                        diagnostics.auto_stop_bridge_on_exit,
                        diagnostics,
                    )
                except CEBridgeError as exc:
                    launch_failure_reason = str(exc)
                else:
                    launched = True
        else:
            launch_failure_reason = "Complex Editor executable path is not configured"

        if not launched:
            env_allow = os.getenv("CE_ALLOW_HEADLESS_EXPORTS")
            if env_allow is not None:
                fallback_allow = env_allow.strip().lower() in {"1", "true", "yes", "on"}
            else:
                fallback_allow = allow_headless
            if not _launch_uvicorn_fallback(host, port, token, fallback_allow, diagnostics):
                diagnostics.outcome = "error"
                fallback_reason = diagnostics.spawn_error or ""
                diagnostics.reason = fallback_reason or launch_failure_reason or "Failed to launch Complex Editor bridge"
                diagnostics.traceback = diagnostics.traceback or "".join(
                    traceback.format_stack(limit=10)
                )
                diagnostics.ts_end = datetime.now(timezone.utc)
                err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
                logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
                raise err

        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        start_poll = time.monotonic()
        last_payload: Dict[str, Any] | None = None
        while time.monotonic() < deadline:
            time.sleep(0.3)
            status, detail, payload = _probe_bridge(base_url, token, int(max(request_timeout_s, 1)))
            diagnostics.add_health_poll(time.monotonic() - start_poll, status, detail)
            if isinstance(payload, dict):
                last_payload = payload
                diagnostics.health_payload = payload
                if "headless" in payload:
                    diagnostics.headless = bool(payload.get("headless"))
                if "allow_headless" in payload:
                    diagnostics.allow_headless = bool(payload.get("allow_headless"))
                if payload.get("ready"):
                    try:
                        state_payload = ce_bridge_transport.preflight_ready(
                            base_url,
                            token,
                            request_timeout_s=request_timeout_s,
                            trace_id=trace_id,
                        )
                    except CEBridgeError as exc:
                        diagnostics.outcome = "error"
                        diagnostics.reason = str(exc)
                        diagnostics.traceback = "".join(traceback.format_stack(limit=10))
                        diagnostics.ts_end = datetime.now(timezone.utc)
                        exc.diagnostics = exc.diagnostics or diagnostics
                        logger.warning(
                            "Bridge diagnostics available (%s)", diagnostics.outcome
                        )
                        raise

                    diagnostics.outcome = "success"
                    diagnostics.reason = (
                        "Bridge started successfully"
                        if not isinstance(state_payload, dict)
                        else f"Bridge started successfully ({state_payload.get('reason') or 'ok'})"
                    )
                    diagnostics.ts_end = datetime.now(timezone.utc)
                    return

        diagnostics.outcome = "timeout"
        detail = ""
        if last_payload:
            detail = str(
                last_payload.get("reason")
                or last_payload.get("detail")
                or last_payload.get("status")
                or ""
            ).strip()
        diagnostics.reason = (
            "Timed out waiting for Complex Editor bridge to start"
            if not detail
            else f"Timed out waiting for Complex Editor bridge to start ({detail})"
        )
        diagnostics.traceback = "".join(traceback.format_stack(limit=10))
        diagnostics.ts_end = datetime.now(timezone.utc)
        err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
        logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
        raise err
    except CEBridgeError as exc:
        if exc.diagnostics is None:
            diagnostics.traceback = diagnostics.traceback or traceback.format_exc()
            diagnostics.ts_end = diagnostics.ts_end or datetime.now(timezone.utc)
            exc.diagnostics = diagnostics
        raise
    except Exception as exc:  # pragma: no cover - defensive
        diagnostics.outcome = diagnostics.outcome or "error"
        diagnostics.reason = str(exc) or exc.__class__.__name__
        diagnostics.traceback = traceback.format_exc()
        diagnostics.ts_end = diagnostics.ts_end or datetime.now(timezone.utc)
        err = CEBridgeError(str(exc), diagnostics=diagnostics)
        logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
        raise err from exc
    finally:
        if diagnostics.ts_end is None:
            diagnostics.ts_end = datetime.now(timezone.utc)


ensure_ce_bridge_ready = ensure_ready
def _clear_bridge_tracking_if_stopped() -> None:
    global _BRIDGE_PROCESS, _BRIDGE_AUTO_STOP
    if _BRIDGE_PROCESS is not None and _BRIDGE_PROCESS.poll() is not None:
        _BRIDGE_PROCESS = None
        _BRIDGE_AUTO_STOP = False


def _terminate_spawned_bridge(proc: subprocess.Popen) -> None:
    global _BRIDGE_PROCESS, _BRIDGE_AUTO_STOP
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:  # pragma: no cover - best effort cleanup
        pass
    _BRIDGE_PROCESS = None
    _BRIDGE_AUTO_STOP = False


def _bridge_request_without_ensure(
    method: str,
    endpoint: str,
    *,
    timeout: float = 5.0,
) -> Response | None:
    settings = config.get_complex_editor_settings()
    if not isinstance(settings, dict):
        return None
    bridge_cfg = settings.get("bridge", {})
    if not isinstance(bridge_cfg, dict) or not bridge_cfg.get("enabled", True):
        return None
    from . import ce_bridge_client

    base_url, token, request_timeout = ce_bridge_client.resolve_bridge_connection()
    try:
        request_timeout = float(request_timeout or timeout)
    except (TypeError, ValueError):
        request_timeout = timeout
    headers = ce_bridge_transport.build_headers(token)
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    session = ce_bridge_transport.get_session()
    try:
        return session.request(method, url, headers=headers, timeout=request_timeout)
    except req_exc.RequestException:
        return None


def launch_ce_wizard(pn: str, aliases: Sequence[str] | None = None) -> Path:
    """Launch the Complex Editor GUI wizard with the provided PN prefilled.

    The function returns the path to the temporary buffer JSON that was
    provided to the Complex Editor process. Callers are responsible for
    deleting the file once it is no longer needed.
    """

    settings = config.get_complex_editor_settings()
    if not isinstance(settings, dict):
        raise CEBridgeError("Complex Editor settings not available")

    exe_path = get_ce_app_exe(settings)
    if not exe_path:
        raise CEBridgeError("Complex Editor executable path is not configured")

    exe = Path(exe_path).expanduser().resolve()
    if not exe.exists():
        raise CEBridgeError(f"Complex Editor executable not found: {exe}")
    if exe.is_dir():
        raise CEBridgeError(f"Complex Editor executable path is a directory: {exe}")
    if os.name != "nt" and not os.access(exe, os.X_OK):
        raise CEBridgeError(f"Complex Editor executable is not executable: {exe}")

    payload: dict[str, object] = {"pn": pn}
    alias_list = [a.strip() for a in (aliases or []) if isinstance(a, str) and a.strip()]
    if alias_list:
        payload["aliases"] = alias_list

    try:
        with tempfile.NamedTemporaryFile(
            "w", delete=False, suffix=".json", encoding="utf-8"
        ) as handle:
            json.dump(payload, handle)
            buffer_path = Path(handle.name)
    except OSError as exc:  # pragma: no cover - filesystem failure
        raise CEBridgeError(
            f"Failed to prepare Complex Editor wizard buffer: {exc}"
        ) from exc

    cmd = [str(exe), "--load-buffer", str(buffer_path)]
    config_path = str(settings.get("config_path") or "").strip()
    if config_path:
        cmd.extend(["--config", config_path])

    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)

    logger.info("Launching Complex Editor GUI wizard for part %s", pn)
    try:
        subprocess.Popen(
            cmd,
            creationflags=creationflags,
            close_fds=os.name != "nt",
            startupinfo=startupinfo,
        )
    except Exception as exc:
        with suppress(Exception):
            buffer_path.unlink(missing_ok=True)
        raise CEBridgeError(f"Failed to launch Complex Editor wizard: {exc}") from exc

    return buffer_path

def stop_ce_bridge_if_started() -> None:
    global _BRIDGE_PROCESS, _BRIDGE_AUTO_STOP
    _clear_bridge_tracking_if_stopped()
    if _BRIDGE_PROCESS is None or not _BRIDGE_AUTO_STOP:
        return

    proc = _BRIDGE_PROCESS
    state_resp = _bridge_request_without_ensure("GET", "/state", timeout=3.0)
    if state_resp is not None and state_resp.ok:
        try:
            payload = state_resp.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict) and payload.get("unsaved_changes"):
            logger.info(
                "Complex Editor reports unsaved changes; skipping bridge shutdown."
            )
            return

    shutdown_resp = _bridge_request_without_ensure("POST", "/admin/shutdown", timeout=3.0)
    if shutdown_resp is not None:
        if shutdown_resp.status_code == 404:
            logger.debug("Bridge /admin/shutdown unavailable; terminating spawned process.")
            _terminate_spawned_bridge(proc)
            return
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            logger.debug("Bridge shutdown request sent; process still running.")
            return
        _clear_bridge_tracking_if_stopped()
        return

    logger.debug("Bridge shutdown request failed; leaving process running for safety.")

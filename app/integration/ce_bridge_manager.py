from __future__ import annotations

import ipaddress
import logging
import os
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import requests
from requests import exceptions as req_exc

from app import config

logger = logging.getLogger(__name__)

_BRIDGE_PROCESS: Optional[subprocess.Popen] = None
_BRIDGE_AUTO_STOP = False
_LAST_DIAGNOSTICS: "CEBridgeDiagnostics | None" = None


class CEBridgeError(RuntimeError):
    """Raised when the Complex Editor bridge cannot be ensured."""

    def __init__(self, message: str, *, diagnostics: "CEBridgeDiagnostics | None" = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


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


def _probe_bridge(base_url: str, token: str, timeout: int) -> Tuple[str, str, bool]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = urljoin(base_url.rstrip("/") + "/", "health")
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except req_exc.Timeout as exc:
        return "not_running", _short_exc(exc), False
    except req_exc.ConnectionError as exc:
        return "not_running", _short_exc(exc), False
    except req_exc.RequestException as exc:
        return "other_service", _short_exc(exc), False

    if response.ok:
        detail = f"HTTP {response.status_code}"
        return "running", detail, True
    if response.status_code in (401, 403):
        detail = f"HTTP {response.status_code}"
        return "unauthorized", detail, False
    return "other_service", f"HTTP {response.status_code}", False


def _healthcheck(base_url: str, token: str, timeout: int) -> bool:
    return _probe_bridge(base_url, token, timeout)[2]


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


def ensure_ce_bridge_ready(timeout_seconds: float = 4.0) -> None:
    """Ensure the Complex Editor bridge is reachable, spawning if allowed."""
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
        if not settings.get("ui_enabled", True):
            diagnostics.ui_enabled = False
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

        base_url = str(bridge_cfg.get("base_url") or "http://127.0.0.1:8765").strip()
        token = str(bridge_cfg.get("auth_token") or "").strip()
        timeout = int(bridge_cfg.get("request_timeout_seconds") or 10)
        if timeout <= 0:
            timeout = 10

        diagnostics.ui_enabled = bool(settings.get("ui_enabled", True))
        diagnostics.auto_start_bridge = bool(settings.get("auto_start_bridge", True))
        diagnostics.auto_stop_bridge_on_exit = bool(settings.get("auto_stop_bridge_on_exit", False))
        diagnostics.base_url = base_url
        parsed = urlparse(base_url or "http://127.0.0.1:8765")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8765
        diagnostics.host = host
        diagnostics.port = port
        diagnostics.is_localhost = _is_local_host(host)
        probe_host = _effective_probe_host(host)
        diagnostics.probe_host = probe_host
        diagnostics.auth_token_preview = _mask_token(token)

        raw_exe, resolved_exe, exists, is_dir, exec_ok = diagnostics.record_path_info(
            str(settings.get("exe_path") or ""), is_executable_check=True
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

        status, detail, ok = _probe_bridge(base_url, token, timeout)
        diagnostics.pre_probe_status = status
        diagnostics.pre_probe_detail = detail
        if ok:
            diagnostics.outcome = "success"
            diagnostics.reason = "Bridge already running"
            diagnostics.ts_end = datetime.now(timezone.utc)
            return

        if not _is_local_host(host):
            diagnostics.outcome = "error"
            diagnostics.reason = f"Complex Editor bridge at {base_url} is unreachable"
            diagnostics.traceback = "".join(traceback.format_stack(limit=10))
            diagnostics.ts_end = datetime.now(timezone.utc)
            err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
            logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
            raise err

        if not settings.get("auto_start_bridge", True):
            diagnostics.outcome = "error"
            diagnostics.reason = "Complex Editor bridge is not running and auto-start is disabled"
            diagnostics.traceback = "".join(traceback.format_stack(limit=10))
            diagnostics.ts_end = datetime.now(timezone.utc)
            err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
            logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
            raise err

        exe_path = raw_exe.strip()
        if not exe_path:
            diagnostics.outcome = "error"
            diagnostics.reason = "Complex Editor executable path is not configured"
            diagnostics.traceback = "".join(traceback.format_stack(limit=10))
            diagnostics.ts_end = datetime.now(timezone.utc)
            err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
            logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
            raise err

        exe = Path(exe_path).expanduser().resolve()
        if not exe.exists():
            diagnostics.outcome = "error"
            diagnostics.reason = f"Complex Editor executable not found: {exe}"
            diagnostics.traceback = "".join(traceback.format_stack(limit=10))
            diagnostics.ts_end = datetime.now(timezone.utc)
            err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
            logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
            raise err
        if exe.is_dir():
            diagnostics.outcome = "error"
            diagnostics.reason = f"Complex Editor executable path is a directory: {exe}"
            diagnostics.traceback = "".join(traceback.format_stack(limit=10))
            diagnostics.ts_end = datetime.now(timezone.utc)
            err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
            logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
            raise err
        if os.name != "nt" and not os.access(exe, os.X_OK):
            diagnostics.outcome = "error"
            diagnostics.reason = f"Complex Editor executable is not executable: {exe}"
            diagnostics.traceback = "".join(traceback.format_stack(limit=10))
            diagnostics.ts_end = datetime.now(timezone.utc)
            err = CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
            logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
            raise err

        config_path = raw_cfg.strip() or None

        if not token:
            token = uuid.uuid4().hex
            config.save_complex_editor_settings(bridge_auth_token=token)
            diagnostics.auth_token_preview = _mask_token(token)

        try:
            _launch_bridge(
                exe,
                config_path,
                port,
                token,
                bool(settings.get("auto_stop_bridge_on_exit", False)),
                diagnostics,
            )
        except CEBridgeError:
            # _launch_bridge already logged and attached diagnostics
            raise

        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        start_poll = time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(0.3)
            status, detail, ok = _probe_bridge(base_url, token, timeout)
            diagnostics.add_health_poll(time.monotonic() - start_poll, status, detail)
            if ok:
                diagnostics.outcome = "success"
                diagnostics.reason = "Bridge started successfully"
                diagnostics.ts_end = datetime.now(timezone.utc)
                return
        diagnostics.outcome = "timeout"
        diagnostics.reason = "Timed out waiting for Complex Editor bridge to start"
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

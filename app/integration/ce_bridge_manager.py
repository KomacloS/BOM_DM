from __future__ import annotations

import ipaddress
import logging
import os
import socket
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from requests import exceptions as req_exc

from app import config

logger = logging.getLogger(__name__)

_BRIDGE_PROCESS: Optional[subprocess.Popen] = None
_BRIDGE_AUTO_STOP = False
_LAST_DIAGNOSTICS: Optional["CEBridgeDiagnostics"] = None


class CEBridgeError(RuntimeError):
    """Raised when the Complex Editor bridge cannot be ensured."""

    def __init__(self, message: str, *, diagnostics: Optional["CEBridgeDiagnostics"] = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass
class CEBridgeDiagnostics:
    ts_start: Optional[datetime] = None
    ts_end: Optional[datetime] = None
    settings_snapshot: Dict[str, Any] = field(default_factory=dict)
    exe_path_info: Dict[str, Any] = field(default_factory=dict)
    config_path_info: Dict[str, Any] = field(default_factory=dict)
    base_url: Optional[str] = None
    host: Optional[str] = None
    probe_host: Optional[str] = None
    port: Optional[int] = None
    is_localhost: Optional[bool] = None
    auth_token_preview: Optional[str] = None
    pre_probe_status: Optional[str] = None
    pre_probe_detail: Optional[str] = None
    spawn_attempted: bool = False
    spawn_cmd_preview: Optional[List[str]] = None
    spawn_pid: Optional[int] = None
    spawn_error: Optional[str] = None
    health_polls: List[Dict[str, Any]] = field(default_factory=list)
    port_check: Dict[str, Optional[str]] = field(default_factory=dict)
    outcome: Optional[str] = None
    reason: Optional[str] = None
    traceback: Optional[str] = None

    _poll_reference: Optional[float] = None

    def set_settings_snapshot(self, settings: Dict[str, Any]) -> None:
        self.settings_snapshot = {
            "ui_enabled": bool(settings.get("ui_enabled", True)),
            "auto_start_bridge": bool(settings.get("auto_start_bridge", True)),
            "auto_stop_bridge_on_exit": bool(settings.get("auto_stop_bridge_on_exit", False)),
        }

    def set_paths(self, exe: Optional[Path], config_path: Optional[Path]) -> None:
        if exe is not None:
            exists, is_dir, exec_ok = path_info(exe)
            self.exe_path_info = {
                "path": str(exe),
                "exists": exists,
                "is_dir": is_dir,
                "exec_ok": exec_ok,
            }
        if config_path is not None:
            exists = config_path.exists()
            self.config_path_info = {
                "path": str(config_path),
                "exists": exists,
            }

    def set_bridge_info(self, base_url: str, host: str, port: int, probe_host: str, token: str) -> None:
        self.base_url = base_url
        self.host = host
        self.probe_host = probe_host
        self.port = port
        self.is_localhost = is_local_host(host)
        self.auth_token_preview = mask_token(token)

    def set_pre_probe(self, status: str, detail: str) -> None:
        self.pre_probe_status = status
        self.pre_probe_detail = detail

    def mark_spawn_attempt(self, cmd: List[str], token: str) -> None:
        self.spawn_attempted = True
        self.spawn_cmd_preview = mask_command(cmd, token)
        self._poll_reference = time.monotonic()

    def set_spawn_result(self, pid: Optional[int], error: Optional[str]) -> None:
        self.spawn_pid = pid
        if error:
            self.spawn_error = error

    def add_health_poll(self, status: str, detail: str) -> None:
        if len(self.health_polls) >= 10:
            return
        now = time.monotonic()
        base = self._poll_reference or now
        offset = max(0.0, now - base)
        self.health_polls.append(
            {
                "t": round(offset, 3),
                "status": status,
                "detail": detail,
            }
        )

    def set_port_check(self, when: str, result: Optional[str]) -> None:
        self.port_check[when] = result

    def to_text(self) -> str:
        def fmt_ts(value: Optional[datetime]) -> str:
            if not value:
                return "<unknown>"
            return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        lines: List[str] = [
            "BOM_DB ↔ Complex Editor Bridge Diagnostics",
            f"Timestamp: {fmt_ts(self.ts_end or self.ts_start)}",
            f"Outcome: {self.outcome or '<unknown>'}",
            f"Reason: {self.reason or '<unspecified>'}",
            "",
            "[Settings Snapshot]",
            f"UI Enabled: {self.settings_snapshot.get('ui_enabled')}",
            f"Auto-start: {self.settings_snapshot.get('auto_start_bridge')}",
            f"Auto-stop on exit: {self.settings_snapshot.get('auto_stop_bridge_on_exit')}",
        ]

        if self.exe_path_info:
            lines.append(
                "Executable: {path} (exists: {exists}, dir: {is_dir}, exec_ok: {exec_ok})".format(**self.exe_path_info)
            )
        if self.config_path_info:
            lines.append(
                "Config file: {path} (exists: {exists})".format(**self.config_path_info)
            )

        if self.base_url is not None:
            host_line = f"Host (configured/probe): {self.host}"
            if self.probe_host and self.probe_host != self.host:
                host_line += f" → {self.probe_host}"
            lines.extend(
                [
                    f"Base URL: {self.base_url}",
                    host_line,
                    f"Port: {self.port}",
                    f"Auth Token: {self.auth_token_preview}",
                ]
            )

        if self.pre_probe_status or self.pre_probe_detail:
            lines.extend(
                [
                    "",
                    "[Pre-Healthcheck]",
                    f"Status: {self.pre_probe_status}",
                    f"Detail: {self.pre_probe_detail}",
                ]
            )

        lines.extend([
            "",
            "[Spawn]",
            f"Attempted: {'yes' if self.spawn_attempted else 'no'}",
        ])
        if self.spawn_cmd_preview:
            lines.append(f"Command (masked): {self.spawn_cmd_preview}")
        if self.spawn_pid is not None:
            lines.append(f"PID: {self.spawn_pid}")
        if self.spawn_error:
            lines.append(f"Error: {self.spawn_error}")
        elif self.spawn_attempted:
            lines.append("Error: <none>")

        if self.health_polls:
            lines.extend(["", "[Post-Spawn Health Polls]"])
            for poll in self.health_polls:
                lines.append(f"t+{poll['t']}s: {poll['status']} ({poll['detail']})")

        if self.port_check:
            lines.extend(["", "[Port Check]"])
            for when, result in sorted(self.port_check.items()):
                lines.append(f"{when}: {result}")

        if self.traceback:
            lines.extend(["", "[Traceback]", self.traceback.rstrip()])

        return "\n".join(lines)[:200_000]


def mask_token(token: str) -> str:
    token = token or ""
    if not token:
        return "<empty>"
    if len(token) <= 6:
        return f"{token[:1]}...{token[-1:]}"
    return f"{token[:3]}…{token[-3:]}"


def mask_command(cmd: List[str], token: str) -> List[str]:
    masked: List[str] = []
    masked_token = mask_token(token)
    for part in cmd:
        if part == token:
            masked.append(masked_token)
        else:
            masked.append(part)
    return masked


def is_local_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() in {"localhost", ""}


def effective_probe_host(host: str) -> str:
    if host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return host


def short_exc(exc: BaseException) -> str:
    name = exc.__class__.__name__
    if isinstance(exc, OSError):
        parts = [name]
        if getattr(exc, "errno", None) is not None:
            parts.append(f"[Errno {exc.errno}]")
        message = exc.strerror or str(exc)
        if message:
            parts.append(message)
        return " ".join(parts)
    return f"{name}: {exc}"


def path_info(path: Path) -> tuple[bool, bool, bool]:
    try:
        exists = path.exists()
    except OSError:
        exists = False
    is_dir = path.is_dir() if exists else False
    if os.name == "nt":
        exec_ok = exists and not is_dir
    else:
        exec_ok = exists and not is_dir and os.access(path, os.X_OK)
    return exists, is_dir, exec_ok


def _port_status(host: str, port: int) -> str:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            result = sock.connect_ex((host, port))
        if result == 0:
            return "open"
        return f"connect_ex={result}"
    except OSError as exc:
        return short_exc(exc)


def _healthcheck(base_url: str, token: str, timeout: int) -> tuple[bool, str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = urljoin(base_url.rstrip("/") + "/", "health")
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except req_exc.Timeout as exc:
        return False, "not_running", short_exc(exc)
    except req_exc.ConnectionError as exc:
        return False, "not_running", short_exc(exc)
    except req_exc.RequestException as exc:
        return False, "other_service", short_exc(exc)

    if response.ok:
        return True, "running", f"HTTP {response.status_code}"
    if response.status_code == 401:
        return False, "unauthorized", f"HTTP {response.status_code}"
    return False, "other_service", f"HTTP {response.status_code}"


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
        diagnostics.spawn_attempted = False
        diagnostics.set_spawn_result(_BRIDGE_PROCESS.pid, None)
        return
    cmd = [str(exe), "--start-bridge", "--port", str(port), "--token", token]
    if config_path:
        cmd.extend(["--config", config_path])
    diagnostics.mark_spawn_attempt(cmd, token)
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
        diagnostics.set_spawn_result(None, short_exc(exc))
        raise CEBridgeError(
            f"Failed to launch Complex Editor bridge: {exc}", diagnostics=diagnostics
        ) from exc
    _BRIDGE_PROCESS = proc
    _BRIDGE_AUTO_STOP = auto_stop
    diagnostics.set_spawn_result(proc.pid, None)
    logger.info("Started Complex Editor bridge (pid=%s)", proc.pid)


def ensure_ce_bridge_ready(timeout_seconds: float = 4.0) -> None:
    """Ensure the Complex Editor bridge is reachable, spawning if allowed."""

    global _LAST_DIAGNOSTICS
    diagnostics = CEBridgeDiagnostics(ts_start=datetime.now(timezone.utc))

    try:
        settings = config.get_complex_editor_settings()
        if not isinstance(settings, dict):
            diagnostics.set_settings_snapshot({})
            diagnostics.ts_end = datetime.now(timezone.utc)
            diagnostics.outcome = "success"
            diagnostics.reason = "Bridge disabled or unavailable in settings"
            _LAST_DIAGNOSTICS = diagnostics
            return

        diagnostics.set_settings_snapshot(settings)

        if not settings.get("ui_enabled", True):
            diagnostics.ts_end = datetime.now(timezone.utc)
            diagnostics.outcome = "success"
            diagnostics.reason = "Complex Editor UI disabled"
            _LAST_DIAGNOSTICS = diagnostics
            return

        bridge_cfg = settings.get("bridge", {})
        if not isinstance(bridge_cfg, dict) or not bridge_cfg.get("enabled", True):
            diagnostics.ts_end = datetime.now(timezone.utc)
            diagnostics.outcome = "success"
            diagnostics.reason = "Complex Editor bridge disabled"
            _LAST_DIAGNOSTICS = diagnostics
            return

        base_url = str(bridge_cfg.get("base_url") or "http://127.0.0.1:8765").strip()
        token = str(bridge_cfg.get("auth_token") or "").strip()
        timeout = int(bridge_cfg.get("request_timeout_seconds") or 10)
        if timeout <= 0:
            timeout = 10

        parsed = urlparse(base_url or "http://127.0.0.1:8765")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8765
        probe_host = effective_probe_host(host)

        diagnostics.set_bridge_info(base_url, host, port, probe_host, token)

        healthy, status, detail = _healthcheck(base_url, token, timeout)
        diagnostics.set_pre_probe(status, detail)
        if healthy:
            diagnostics.ts_end = datetime.now(timezone.utc)
            diagnostics.outcome = "success"
            diagnostics.reason = "Bridge already running"
            _LAST_DIAGNOSTICS = diagnostics
            return

        if not is_local_host(host):
            diagnostics.reason = f"Complex Editor bridge at {base_url} is unreachable"
            raise CEBridgeError(diagnostics.reason, diagnostics=diagnostics)

        if not settings.get("auto_start_bridge", True):
            diagnostics.reason = "Complex Editor bridge is not running and auto-start is disabled"
            raise CEBridgeError(diagnostics.reason, diagnostics=diagnostics)

        exe_path = str(settings.get("exe_path") or "").strip()
        if not exe_path:
            diagnostics.reason = "Complex Editor executable path is not configured"
            raise CEBridgeError(diagnostics.reason, diagnostics=diagnostics)

        exe = Path(exe_path).expanduser().resolve()
        cfg_path_value = str(settings.get("config_path") or "").strip() or None
        config_path = None
        if cfg_path_value:
            config_path = Path(cfg_path_value).expanduser()
            try:
                config_path = config_path.resolve()
            except FileNotFoundError:
                pass
        diagnostics.set_paths(exe, config_path)

        exists, is_dir, exec_ok = path_info(exe)
        if not exists:
            diagnostics.reason = f"Complex Editor executable not found: {exe}"
            raise CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
        if is_dir:
            diagnostics.reason = f"Complex Editor executable path is a directory: {exe}"
            raise CEBridgeError(diagnostics.reason, diagnostics=diagnostics)
        if os.name != "nt" and not exec_ok:
            diagnostics.reason = f"Complex Editor executable is not executable: {exe}"
            raise CEBridgeError(diagnostics.reason, diagnostics=diagnostics)

        if not token:
            token = uuid.uuid4().hex
            diagnostics.auth_token_preview = mask_token(token)
            config.save_complex_editor_settings(bridge_auth_token=token)

        diagnostics.set_port_check("before", _port_status(probe_host, port))

        _launch_bridge(
            exe,
            cfg_path_value,
            port,
            token,
            bool(settings.get("auto_stop_bridge_on_exit", False)),
            diagnostics,
        )

        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        poll_interval = 0.3
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            healthy, status, detail = _healthcheck(base_url, token, timeout)
            diagnostics.add_health_poll(status, detail)
            if healthy:
                diagnostics.set_port_check("after", _port_status(probe_host, port))
                diagnostics.ts_end = datetime.now(timezone.utc)
                diagnostics.outcome = "success"
                diagnostics.reason = "Bridge started successfully"
                _LAST_DIAGNOSTICS = diagnostics
                return

        diagnostics.reason = "Timed out waiting for Complex Editor bridge to start"
        diagnostics.outcome = "timeout"
        raise CEBridgeError(diagnostics.reason, diagnostics=diagnostics)

    except CEBridgeError as exc:
        diag = exc.diagnostics or diagnostics
        trace = traceback.format_exc()
        diag.traceback = diag.traceback or trace
        diag.ts_end = diag.ts_end or datetime.now(timezone.utc)
        diag.outcome = diag.outcome or "error"
        diag.reason = diag.reason or str(exc)
        _LAST_DIAGNOSTICS = diag
        logger.warning("Bridge diagnostics available (%s)", diag.outcome)
        raise
    except Exception as exc:  # pragma: no cover - unexpected failure path
        diagnostics.reason = str(exc)
        diagnostics.outcome = diagnostics.outcome or "error"
        diagnostics.traceback = traceback.format_exc()
        diagnostics.ts_end = diagnostics.ts_end or datetime.now(timezone.utc)
        _LAST_DIAGNOSTICS = diagnostics
        logger.warning("Bridge diagnostics available (%s)", diagnostics.outcome)
        raise CEBridgeError(str(exc), diagnostics=diagnostics) from exc


def get_last_diagnostics() -> Optional[CEBridgeDiagnostics]:
    return _LAST_DIAGNOSTICS


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

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
import socket
import traceback
from typing import Iterable, List, Optional
import ipaddress


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def mask_token(token: str | None) -> str:
    if not token:
        return "<empty>"
    token = token.strip()
    if len(token) <= 6:
        return "***"
    return f"{token[:3]}…{token[-3:]}"


def is_localhost(host: str) -> bool:
    lowered = (host or "").strip().lower()
    if not lowered or lowered == "localhost":
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def effective_probe_host(host: str) -> str:
    lowered = (host or "").strip().lower()
    if lowered in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host or "127.0.0.1"


def short_exc(exc: BaseException) -> str:
    name = exc.__class__.__name__
    text = str(exc).strip()
    return f"{name}: {text}" if text else name


def path_info(path: os.PathLike[str] | str) -> tuple[bool, bool, Optional[bool]]:
    resolved = os.fspath(path)
    exists = os.path.exists(resolved)
    if not exists:
        return False, False, None
    is_dir = os.path.isdir(resolved)
    exec_ok: Optional[bool]
    if is_dir:
        exec_ok = None
    else:
        exec_ok = True if os.name == "nt" else os.access(resolved, os.X_OK)
    return exists, is_dir, exec_ok


def port_busy(host: str, port: int) -> Optional[bool]:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError as exc:
        err = getattr(exc, "errno", None)
        if err in (111, 61, 10061):
            return False
        return None


@dataclass
class HealthPoll:
    offset_seconds: float
    status: str
    detail: str | None = None


@dataclass
class CEBridgeDiagnostics:
    ts_start: datetime = field(default_factory=utc_now)
    ts_end: datetime | None = None

    ui_enabled: Optional[bool] = None
    auto_start_bridge: Optional[bool] = None
    auto_stop_bridge_on_exit: Optional[bool] = None

    exe_path: Optional[str] = None
    exe_exists: Optional[bool] = None
    exe_is_dir: Optional[bool] = None
    exe_exec_ok: Optional[bool] = None

    config_path: Optional[str] = None
    config_exists: Optional[bool] = None

    base_url: Optional[str] = None
    host: Optional[str] = None
    probe_host: Optional[str] = None
    port: Optional[int] = None
    is_localhost: Optional[bool] = None

    auth_token_preview: Optional[str] = None

    pre_probe_status: Optional[str] = None
    pre_probe_detail: Optional[str] = None

    port_busy_before: Optional[bool] = None
    port_busy_after: Optional[bool] = None

    spawn_attempted: bool = False
    spawn_cmd_preview: List[str] = field(default_factory=list)
    spawn_pid: Optional[int] = None
    spawn_error: Optional[str] = None

    health_polls: List[HealthPoll] = field(default_factory=list)

    outcome: Optional[str] = None
    reason: Optional[str] = None
    traceback: Optional[str] = None

    def add_health_poll(self, offset_seconds: float, status: str, detail: str | None = None, limit: int = 10) -> None:
        if len(self.health_polls) < limit:
            self.health_polls.append(HealthPoll(offset_seconds=offset_seconds, status=status, detail=detail))

    def finalize(self, outcome: str, reason: str | None = None) -> None:
        self.ts_end = utc_now()
        self.outcome = outcome
        self.reason = reason

    def attach_traceback(self, exc: BaseException | None) -> None:
        if exc is not None:
            self.traceback = "".join(traceback.format_exception(exc.__class__, exc, exc.__traceback__))
        else:
            self.traceback = "".join(traceback.format_stack())

    def to_text(self) -> str:
        lines: list[str] = []
        ts = self.ts_start.astimezone(timezone.utc).isoformat()
        lines.append("BOM_DB ↔ Complex Editor Bridge Diagnostics")
        lines.append(f"Timestamp: {ts}")
        if self.ts_end:
            lines.append(f"Completed: {self.ts_end.astimezone(timezone.utc).isoformat()}")
        if self.outcome:
            lines.append(f"Outcome: {self.outcome}")
        if self.reason:
            lines.append(f"Reason: {self.reason}")
        lines.append("")

        lines.append("[Settings Snapshot]")
        lines.append(f"UI Enabled: {self.ui_enabled}")
        lines.append(f"Auto-start: {self.auto_start_bridge}")
        lines.append(f"Auto-stop on exit: {self.auto_stop_bridge_on_exit}")
        exe_repr = self._path_repr(self.exe_path, self.exe_exists, self.exe_is_dir, self.exe_exec_ok)
        lines.append(f"Executable: {exe_repr}")
        cfg_repr = self._path_repr(self.config_path, self.config_exists, None, None)
        lines.append(f"Config file: {cfg_repr}")
        lines.append(f"Base URL: {self.base_url}")
        host_section = self.host
        if self.probe_host and self.host and self.probe_host != self.host:
            host_section = f"{self.host} → {self.probe_host}"
        lines.append(f"Host (configured/probe): {host_section}")
        lines.append(f"Port: {self.port}")
        lines.append(f"Is localhost: {self.is_localhost}")
        lines.append(f"Auth Token: {self.auth_token_preview}")
        lines.append("")

        lines.append("[Pre-Healthcheck]")
        lines.append(f"Status: {self.pre_probe_status}")
        if self.pre_probe_detail:
            lines.append(f"Detail: {self.pre_probe_detail}")
        if self.port_busy_before is not None:
            lines.append(f"Port busy before: {self.port_busy_before}")
        lines.append("")

        lines.append("[Spawn]")
        lines.append(f"Attempted: {self.spawn_attempted}")
        if self.spawn_cmd_preview:
            lines.append(f"Command (masked): {self.spawn_cmd_preview}")
        if self.spawn_pid is not None:
            lines.append(f"PID: {self.spawn_pid}")
        if self.spawn_error:
            lines.append(f"Error: {self.spawn_error}")
        if self.port_busy_after is not None:
            lines.append(f"Port busy after: {self.port_busy_after}")
        lines.append("")

        if self.health_polls:
            lines.append("[Post-Spawn Health Polls]")
            for poll in self.health_polls:
                detail = f": {poll.detail}" if poll.detail else ""
                lines.append(f"t+{poll.offset_seconds:.1f}s: {poll.status}{detail}")
            lines.append("")

        lines.append("[Traceback]")
        if self.traceback:
            lines.append(self.traceback.rstrip())
        else:
            lines.append("<not available>")

        text = "\n".join(lines)
        if len(text) > 200_000:
            text = text[:199_000] + "\n[truncated]"
        return text

    @staticmethod
    def _path_repr(path: Optional[str], exists: Optional[bool], is_dir: Optional[bool], exec_ok: Optional[bool]) -> str:
        if not path:
            return "<unset>"
        details: list[str] = []
        if exists is not None:
            details.append(f"exists: {exists}")
        if is_dir is not None:
            details.append(f"dir: {is_dir}")
        if exec_ok is not None:
            details.append(f"exec_ok: {exec_ok}")
        if details:
            return f"{path} ({', '.join(details)})"
        return path


def redact_command(parts: Iterable[str], token: str | None) -> list[str]:
    masked = mask_token(token)
    result: list[str] = []
    for part in parts:
        if not token:
            result.append(part)
            continue
        if part == token:
            result.append(masked)
        elif token in part:
            result.append(part.replace(token, masked))
        else:
            result.append(part)
    return result

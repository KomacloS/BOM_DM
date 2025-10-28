from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from app import config

from . import ce_bridge_transport

log = logging.getLogger(__name__)


def get_ce_app_exe(settings: Optional[dict] = None) -> str:
    """
    Resolve the Complex Editor executable path using environment overrides
    before falling back to persisted settings.
    """
    env_value = os.getenv("CE_APP_EXE")
    if env_value and env_value.strip():
        return env_value.strip()
    if settings is None:
        settings = config.get_complex_editor_settings()
    if isinstance(settings, dict):
        candidate = settings.get("app_exe_path") or settings.get("exe_path")
        if candidate:
            text = str(candidate).strip()
            if text:
                return text
    return ""


class CESupervisor:
    """
    Ensures the CE Bridge is reachable and, if headless exports are disabled,
    starts the CE desktop UI (owned instance) and waits until /health
    reports exports are allowed. If we start CE, we also stop it on BOM_DB exit.
    """

    def __init__(
        self,
        app_exe: Optional[Path],
        poll_timeout_s: float = 30.0,
        poll_interval_s: float = 0.5,
    ) -> None:
        self._app_exe = Path(app_exe) if app_exe else None
        self._owned_proc: Optional[subprocess.Popen] = None
        self._poll_timeout_s = max(poll_timeout_s, 1.0)
        self._poll_interval_s = max(poll_interval_s, 0.1)
        atexit.register(self._shutdown_owned)

    def ensure_ready(self, trace_id: str) -> Tuple[bool, Dict[str, object]]:
        from . import ce_bridge_client  # Local import to avoid circular dependency

        try:
            base_url, token, timeout = ce_bridge_client.resolve_bridge_connection()
        except Exception as exc:
            log.warning("Failed to resolve CE bridge configuration: %s", exc)
            return False, {
                "status": "FAILED_INPUT",
                "detail": str(exc),
            }

        settings = config.get_complex_editor_settings()
        settings_dict = settings if isinstance(settings, dict) else {}
        auto_start = bool(settings_dict.get("auto_start_bridge", True))
        ui_enabled = bool(settings_dict.get("ui_enabled", True))

        session = ce_bridge_transport.get_session(base_url)
        headers = ce_bridge_transport.build_headers(token, trace_id)
        health_url = f"{base_url.rstrip('/')}/health"

        payload, status_code = self._probe_health(session, health_url, headers, timeout)
        if status_code in (401, 403):
            return False, {
                "status": "FAILED_INPUT",
                "detail": f"Complex Editor bridge authorization failed (HTTP {status_code})",
            }
        auto_started = False
        if payload is None:
            if auto_start and ui_enabled:
                success, info = self._auto_start_bridge(
                    base_url=base_url,
                    timeout=timeout,
                    settings=settings_dict,
                    headless_block=False,
                )
                if not success:
                    return False, info
                auto_started = True
                base_url, token, timeout = ce_bridge_client.resolve_bridge_connection()
                session = ce_bridge_transport.get_session(base_url)
                headers = ce_bridge_transport.build_headers(token, trace_id)
                health_url = f"{base_url.rstrip('/')}/health"
                payload, status_code = self._probe_health(session, health_url, headers, timeout)
                if status_code in (401, 403):
                    return False, {
                        "status": "FAILED_INPUT",
                        "detail": f"Complex Editor bridge authorization failed (HTTP {status_code})",
                    }
            else:
                return False, {
                    "status": "RETRY_WITH_BACKOFF",
                    "detail": "bridge not reachable",
                }

        payload = payload or {}
        if self._exports_allowed(payload):
            return True, {"status": "READY"}

        headless = bool(payload.get("headless"))
        allow = bool(payload.get("allow_headless", True))
        headless_block = headless and not allow

        if auto_start and ui_enabled and (not bool(payload.get("ready")) or headless_block):
            success, info = self._auto_start_bridge(
                base_url=base_url,
                timeout=timeout,
                settings=settings_dict,
                headless_block=headless_block,
            )
            if not success:
                return False, info
            auto_started = True
            base_url, token, timeout = ce_bridge_client.resolve_bridge_connection()
            session = ce_bridge_transport.get_session(base_url)
            headers = ce_bridge_transport.build_headers(token, trace_id)
            health_url = f"{base_url.rstrip('/')}/health"
            payload, status_code = self._probe_health(session, health_url, headers, timeout)
            if status_code in (401, 403):
                return False, {
                    "status": "FAILED_INPUT",
                    "detail": f"Complex Editor bridge authorization failed (HTTP {status_code})",
                }
            payload = payload or {}
            if self._exports_allowed(payload):
                return True, {"status": "READY"}
            headless = bool(payload.get("headless"))
            allow = bool(payload.get("allow_headless", True))
            headless_block = headless and not allow

        attempted_ui = False
        if headless_block and not auto_started and self._app_exe and self._owned_proc is None:
            attempted_ui = True
            self._launch_ce_ui()
            if self._wait_for_exports(session, health_url, headers, timeout):
                return True, {"status": "READY"}

        detail = str(
            payload.get("last_ready_error")
            or payload.get("detail")
            or payload.get("reason")
            or ("exports disabled in headless mode" if headless_block else "Complex Editor bridge unavailable")
        ).strip()
        result = {
            "status": "RETRY_LATER",
            "detail": detail,
        }
        if attempted_ui:
            result["outcome"] = "timeout"
        return False, result

    def _probe_health(
        self,
        session,
        url: str,
        headers: Dict[str, str],
        timeout: float,
    ) -> Tuple[Optional[Dict[str, object]], Optional[int]]:
        try:
            response = session.get(url, headers=headers, timeout=timeout)
        except Exception as exc:
            log.warning("CE health probe failed: %s", exc)
            return None, None

        status = getattr(response, "status_code", None)
        payload: Dict[str, object] | None = {}
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    payload = parsed
            except ValueError:
                payload = {}
        return payload, status

    def _wait_for_exports(
        self,
        session,
        url: str,
        headers: Dict[str, str],
    timeout: float,
    ) -> bool:
        deadline = time.monotonic() + self._poll_timeout_s
        while time.monotonic() < deadline:
            payload, _status = self._probe_health(session, url, headers, timeout)
            if payload and self._exports_allowed(payload):
                return True
            time.sleep(self._poll_interval_s)
        return False

    def _auto_start_bridge(
        self,
        *,
        base_url: str,
        timeout: float,
        settings: dict,
        headless_block: bool,
    ) -> Tuple[bool, Dict[str, object]]:
        from .ce_bridge_manager import (  # Local import to avoid circular dependency
            CEBridgeError,
            bridge_owned_for_url,
            ensure_ce_bridge_ready,
            restart_bridge_with_ui,
        )

        exe_value = get_ce_app_exe(settings)
        if not exe_value:
            return False, {
                "status": "FAILED_INPUT",
                "detail": "Complex Editor executable path is not configured. Set it in Settings -> Complex Editor -> Executable.",
                "outcome": "error",
            }
        try:
            exe_path = Path(exe_value).expanduser().resolve()
        except Exception:
            exe_path = Path(exe_value)
        if not exe_path.exists():
            return False, {
                "status": "FAILED_INPUT",
                "detail": f"Complex Editor executable not found: {exe_path}",
                "outcome": "error",
            }
        if exe_path.is_dir():
            return False, {
                "status": "FAILED_INPUT",
                "detail": f"Complex Editor executable path is a directory: {exe_path}",
                "outcome": "error",
            }
        if os.name != "nt" and not os.access(exe_path, os.X_OK):
            return False, {
                "status": "FAILED_INPUT",
                "detail": f"Complex Editor executable is not executable: {exe_path}",
                "outcome": "error",
            }

        self._app_exe = exe_path
        timeout_budget = max(timeout, 5.0)
        try:
            if headless_block and bridge_owned_for_url(base_url):
                restart_bridge_with_ui(timeout_budget)
            else:
                ensure_ce_bridge_ready(timeout_seconds=timeout_budget, require_ui=True)
        except CEBridgeError as exc:
            diagnostics = getattr(exc, "diagnostics", None)
            outcome = getattr(diagnostics, "outcome", None) or "error"
            self._launch_ce_ui()
            if self._owned_proc is not None:
                return True, {"status": "READY"}
            detail = str(exc)
            if diagnostics is not None:
                reason = getattr(diagnostics, "reason", None)
                if isinstance(reason, str) and reason.strip():
                    detail = reason.strip()
            info: Dict[str, object] = {
                "status": "RETRY_LATER",
                "detail": detail,
            }
            if outcome:
                info["outcome"] = outcome
            return False, info
        return True, {"status": "READY"}

    @staticmethod
    def _exports_allowed(payload: Dict[str, object]) -> bool:
        ready = bool(payload.get("ready"))
        headless = bool(payload.get("headless"))
        allow = bool(payload.get("allow_headless", True))
        return ready and (not headless or allow)

    def _launch_ce_ui(self) -> None:
        if not self._app_exe:
            return
        exe_path = self._app_exe
        if not exe_path.exists():
            log.warning("Configured CE app executable does not exist: %s", exe_path)
            return
        if self._owned_proc is not None and self._owned_proc.poll() is None:
            return
        creationflags = 0
        startupinfo = None
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        try:
            self._owned_proc = subprocess.Popen(
                [str(exe_path)],
                cwd=str(exe_path.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
            log.info("Launched CE UI: %s (pid=%s)", exe_path, self._owned_proc.pid)
        except Exception as exc:
            log.warning("Failed to launch CE UI: %s", exc)
            self._owned_proc = None

    def _shutdown_owned(self) -> None:
        try:
            stop_ce_bridge_if_started(force=True)
        except Exception:
            pass

        if not self._owned_proc:
            return
        proc = self._owned_proc
        self._owned_proc = None

        from . import ce_bridge_client  # Local import to avoid circular dependency

        try:
            base_url, token, timeout = ce_bridge_client.resolve_bridge_connection()
            session = ce_bridge_transport.get_session(base_url)
            headers = ce_bridge_transport.build_headers(
                token,
                f"shutdown-{os.getpid()}",
                content_type="application/json",
            )
            try:
                session.post(
                    f"{base_url.rstrip('/')}/admin/shutdown",
                    headers=headers,
                    json={"force": 1},
                    timeout=timeout,
                )
            except Exception:
                pass
        except Exception:
            pass

        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    proc.kill()
        except Exception:
            pass


_SUPERVISOR: Optional[CESupervisor] = None
_SUPERVISOR_EXE: Optional[Path] = None


def get_supervisor() -> CESupervisor:
    """
    Return a shared CESupervisor instance created from current CE settings.

    We recreate the supervisor if the configured executable path changes so the
    owned process management stays aligned with user preferences.
    """
    global _SUPERVISOR, _SUPERVISOR_EXE
    exe_text = get_ce_app_exe()
    exe_path = Path(exe_text).expanduser() if exe_text else None
    if _SUPERVISOR is None or _SUPERVISOR_EXE != exe_path:
        _SUPERVISOR = CESupervisor(exe_path)
        _SUPERVISOR_EXE = exe_path
    return _SUPERVISOR


from .ce_bridge_manager import (  # noqa: E402
    CEBridgeError,
    bridge_owned_for_url,
    ensure_ce_bridge_ready,
    get_last_ce_bridge_diagnostics,
    record_bridge_action,
    record_health_detail,
    record_state_snapshot,
    restart_bridge_with_ui,
    stop_ce_bridge_if_started,
)


__all__ = [
    "CESupervisor",
    "CEBridgeError",
    "bridge_owned_for_url",
    "ensure_ce_bridge_ready",
    "get_last_ce_bridge_diagnostics",
    "get_supervisor",
    "record_bridge_action",
    "record_health_detail",
    "record_state_snapshot",
    "restart_bridge_with_ui",
    "stop_ce_bridge_if_started",
]

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import ce_bridge_client, ce_bridge_transport

log = logging.getLogger(__name__)


class CESupervisor:
    """
    Ensures the CE Bridge is reachable and, if headless exports are disabled,
    starts the CE desktop UI (owned instance) and waits until /admin/health
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
        try:
            base_url, token, timeout = ce_bridge_client.resolve_bridge_connection()
        except Exception as exc:
            log.warning("Failed to resolve CE bridge configuration: %s", exc)
            return False, {
                "status": "FAILED_INPUT",
                "detail": str(exc),
            }

        session = ce_bridge_transport.get_session(base_url)
        headers = ce_bridge_transport.build_headers(token, trace_id)
        health_url = f"{base_url.rstrip('/')}/admin/health"

        payload = self._probe_health(session, health_url, headers, timeout)
        if payload is None:
            return False, {
                "status": "RETRY_WITH_BACKOFF",
                "detail": "bridge not reachable",
            }

        if self._exports_allowed(payload):
            return True, {"status": "READY"}

        headless = bool(payload.get("headless"))
        allow = bool(payload.get("allow_headless", True))
        if headless and not allow and self._app_exe and self._owned_proc is None:
            self._launch_ce_ui()
            if self._wait_for_exports(session, health_url, headers, timeout):
                return True, {"status": "READY"}

        detail = str(
            payload.get("last_ready_error")
            or payload.get("detail")
            or "exports disabled in headless mode"
        ).strip()
        return False, {
            "status": "RETRY_LATER",
            "detail": detail,
        }

    def _probe_health(
        self,
        session,
        url: str,
        headers: Dict[str, str],
        timeout: float,
    ) -> Optional[Dict[str, object]]:
        try:
            response = session.get(url, headers=headers, timeout=timeout)
        except Exception as exc:
            log.warning("CE health probe failed: %s", exc)
            return None

        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    return payload
            except ValueError:
                pass
        return {}

    def _wait_for_exports(
        self,
        session,
        url: str,
        headers: Dict[str, str],
        timeout: float,
    ) -> bool:
        deadline = time.monotonic() + self._poll_timeout_s
        while time.monotonic() < deadline:
            payload = self._probe_health(session, url, headers, timeout)
            if payload and self._exports_allowed(payload):
                return True
            time.sleep(self._poll_interval_s)
        return False

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
        if not self._owned_proc:
            return
        proc = self._owned_proc
        self._owned_proc = None

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

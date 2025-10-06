from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
import ipaddress

import requests
from requests import exceptions as req_exc

from app import config

logger = logging.getLogger(__name__)

_BRIDGE_PROCESS: Optional[subprocess.Popen] = None
_BRIDGE_AUTO_STOP = False


class CEBridgeError(RuntimeError):
    """Raised when the Complex Editor bridge cannot be ensured."""


def _is_local_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() in {"localhost", ""}


def _healthcheck(base_url: str, token: str, timeout: int) -> bool:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = urljoin(base_url.rstrip("/") + "/", "health")
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        return response.ok
    except (req_exc.Timeout, req_exc.ConnectionError):
        return False
    except req_exc.RequestException:
        return False


def _launch_bridge(exe: Path, config_path: Optional[str], port: int, token: str, auto_stop: bool) -> None:
    global _BRIDGE_PROCESS, _BRIDGE_AUTO_STOP
    if _BRIDGE_PROCESS is not None and _BRIDGE_PROCESS.poll() is None:
        logger.debug("Complex Editor bridge process already running (pid=%s)", _BRIDGE_PROCESS.pid)
        return
    cmd = [str(exe), "--start-bridge", "--port", str(port), "--token", token]
    if config_path:
        cmd.extend(["--config", config_path])
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
        raise CEBridgeError(f"Failed to launch Complex Editor bridge: {exc}") from exc
    _BRIDGE_PROCESS = proc
    _BRIDGE_AUTO_STOP = auto_stop
    logger.info("Started Complex Editor bridge (pid=%s)", proc.pid)


def ensure_ce_bridge_ready(timeout_seconds: float = 4.0) -> None:
    """Ensure the Complex Editor bridge is reachable, spawning if allowed."""
    settings = config.get_complex_editor_settings()
    if not isinstance(settings, dict):
        return
    if not settings.get("ui_enabled", True):
        return
    bridge_cfg = settings.get("bridge", {})
    if not isinstance(bridge_cfg, dict) or not bridge_cfg.get("enabled", True):
        return

    base_url = str(bridge_cfg.get("base_url") or "http://127.0.0.1:8765").strip()
    token = str(bridge_cfg.get("auth_token") or "").strip()
    timeout = int(bridge_cfg.get("request_timeout_seconds") or 10)
    if timeout <= 0:
        timeout = 10

    if _healthcheck(base_url, token, timeout):
        return

    parsed = urlparse(base_url or "http://127.0.0.1:8765")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765

    if not _is_local_host(host):
        raise CEBridgeError(f"Complex Editor bridge at {base_url} is unreachable")

    if not settings.get("auto_start_bridge", True):
        raise CEBridgeError("Complex Editor bridge is not running and auto-start is disabled")

    exe_path = str(settings.get("exe_path") or "").strip()
    if not exe_path:
        raise CEBridgeError("Complex Editor executable path is not configured")

    exe = Path(exe_path).expanduser()
    if not exe.exists():
        raise CEBridgeError(f"Complex Editor executable not found: {exe}")

    config_path = str(settings.get("config_path") or "").strip() or None

    if not token:
        token = uuid.uuid4().hex
        config.save_complex_editor_settings(bridge_auth_token=token)

    _launch_bridge(exe, config_path, port, token, bool(settings.get("auto_stop_bridge_on_exit", False)))

    deadline = time.monotonic() + max(timeout_seconds, 1.0)
    while time.monotonic() < deadline:
        time.sleep(0.3)
        if _healthcheck(base_url, token, timeout):
            return
    raise CEBridgeError("Timed out waiting for Complex Editor bridge to start")


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

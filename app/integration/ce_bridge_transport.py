from __future__ import annotations

import logging
import time
from typing import Any, Dict
from urllib.parse import urljoin

import requests
from requests import Response
from requests import exceptions as req_exc

from .ce_bridge_manager import CEBridgeError

logger = logging.getLogger(__name__)

_SESSION: requests.Session | None = None
_LAST_PREFLIGHT_TS: float | None = None
_LAST_PREFLIGHT_PAYLOAD: Dict[str, Any] | None = None


def get_session() -> requests.Session:
    """Return the shared HTTP session used for CE bridge requests."""

    global _SESSION
    if _SESSION is None:
        session = requests.Session()
        # Always bypass environment proxies. Corporate proxies can cause 407/503
        # responses when probing the local bridge.
        session.trust_env = False
        _SESSION = session
    return _SESSION


def build_headers(token: str) -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    token = (token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def is_preflight_recent(max_age_s: float = 5.0) -> bool:
    """Return True if the last successful preflight completed within ``max_age_s``."""

    if _LAST_PREFLIGHT_TS is None:
        return False
    return (time.monotonic() - _LAST_PREFLIGHT_TS) <= max(max_age_s, 0.0)


def _record_preflight_success(payload: Dict[str, Any]) -> None:
    global _LAST_PREFLIGHT_TS, _LAST_PREFLIGHT_PAYLOAD
    _LAST_PREFLIGHT_TS = time.monotonic()
    _LAST_PREFLIGHT_PAYLOAD = payload


def get_last_preflight_payload() -> Dict[str, Any] | None:
    return _LAST_PREFLIGHT_PAYLOAD


def preflight_ready(
    base_url: str,
    token: str,
    *,
    deadline_s: float = 20.0,
    poll_every_s: float = 0.3,
    request_timeout_s: float = 5.0,
) -> Dict[str, Any]:
    """Block until the CE bridge reports ``ready == true`` via ``/state``.

    The function bypasses proxies, repeatedly polls ``/state`` and triggers
    ``/selftest`` diagnostics while the bridge warms up. A ``CEBridgeError`` is
    raised when authentication fails or the bridge does not become ready before
    ``deadline_s`` expires.
    """

    session = get_session()
    headers = build_headers(token)
    state_url = urljoin(base_url.rstrip("/") + "/", "state")
    selftest_url = urljoin(base_url.rstrip("/") + "/", "selftest")
    health_url = urljoin(base_url.rstrip("/") + "/", "health")

    deadline = time.monotonic() + max(deadline_s, 0.1)
    poll_delay = max(poll_every_s, 0.1)
    diagnostics_interval = 3.0
    last_selftest: float | None = None
    last_reason = ""
    last_payload: Dict[str, Any] | None = None

    def _extract_reason(payload: Dict[str, Any]) -> str:
        for key in ("reason", "detail", "status", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    while time.monotonic() < deadline:
        try:
            response = session.get(state_url, headers=headers, timeout=request_timeout_s)
        except req_exc.RequestException as exc:
            logger.debug("Preflight /state request failed: %s", exc)
        else:
            if response.status_code in (401, 403):
                raise CEBridgeError("Complex Editor authentication failed during preflight")
            if response.ok:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                payload = payload if isinstance(payload, dict) else {}
                last_payload = payload
                reason = _extract_reason(payload)
                if reason:
                    last_reason = reason
                if payload.get("ready") is True:
                    _record_preflight_success(payload)
                    return payload
            elif response.status_code >= 500:
                last_reason = f"HTTP {response.status_code}"
            else:
                last_reason = f"HTTP {response.status_code}"

        now = time.monotonic()
        if last_selftest is None or (now - last_selftest) >= diagnostics_interval:
            try:
                diag_resp: Response = session.post(
                    selftest_url,
                    headers=headers,
                    timeout=request_timeout_s,
                )
                logger.debug(
                    "Preflight /selftest response: %s", diag_resp.status_code
                )
            except req_exc.RequestException as exc:
                logger.debug("Preflight /selftest failed: %s", exc)
            last_selftest = now

        time.sleep(poll_delay)

    if not last_reason and last_payload:
        last_reason = _extract_reason(last_payload)
    if not last_reason:
        try:
            health_resp = session.get(health_url, headers=headers, timeout=request_timeout_s)
        except req_exc.RequestException as exc:
            logger.debug("Preflight /health failed: %s", exc)
        else:
            if health_resp.ok:
                try:
                    health_payload = health_resp.json()
                except ValueError:
                    health_payload = {}
                if isinstance(health_payload, dict):
                    last_reason = _extract_reason(health_payload)
            else:
                last_reason = f"HTTP {health_resp.status_code}"

    if last_reason:
        message = f"CE is still warming up (last reason: {last_reason})"
    else:
        message = "CE is still warming up"
    raise CEBridgeError(message)


def reset_transport_state() -> None:
    """Reset cached HTTP session and preflight markers (primarily for tests)."""

    global _SESSION, _LAST_PREFLIGHT_TS, _LAST_PREFLIGHT_PAYLOAD
    _SESSION = None
    _LAST_PREFLIGHT_TS = None
    _LAST_PREFLIGHT_PAYLOAD = None

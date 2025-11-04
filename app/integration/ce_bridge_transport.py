from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests
from requests import exceptions as req_exc

from .ce_supervisor import CEBridgeError

logger = logging.getLogger(__name__)

_SESSION: requests.Session | None = None
_LAST_PREFLIGHT_TS: float | None = None
_LAST_PREFLIGHT_PAYLOAD: Dict[str, Any] | None = None


def _extract_payload(response: Any) -> Dict[str, Any]:
    if response is None:
        return {}
    parsed: Any = None
    if hasattr(response, "json"):
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        except Exception:
            parsed = None
    if not isinstance(parsed, dict):
        raw_content = getattr(response, "content", b"")
        if raw_content:
            try:
                parsed = json.loads(raw_content)
            except Exception:
                parsed = None
    return parsed if isinstance(parsed, dict) else {}


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


def build_headers(
    token: str,
    trace_id: Optional[str] = None,
    *,
    accept: str = "application/json",
    content_type: Optional[str] = None,
) -> Dict[str, str]:
    """Return default headers for CE bridge requests.

    The helper normalises the optional ``token`` and ``trace_id`` values and ensures the
    ``Accept`` header is set to JSON by default.  ``content_type`` may be provided for
    callers issuing JSON ``POST`` requests.
    """

    headers: Dict[str, str] = {}
    if accept:
        headers["Accept"] = accept
    token = (token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    trace_text = (trace_id or "").strip()
    if trace_text:
        headers["X-Trace-Id"] = trace_text
    if content_type:
        headers["Content-Type"] = content_type
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
    trace_id: str | None = None,
) -> Dict[str, Any]:
    """Poll ``/admin/health`` until the bridge reports ``ready == true``."""

    session = get_session()
    headers = build_headers(token, trace_id)
    health_url = urljoin(base_url.rstrip("/") + "/", "admin/health")

    deadline = time.monotonic() + max(deadline_s, 0.1)
    poll_delay = max(poll_every_s, 0.1)
    last_reason = ""
    last_payload: Dict[str, Any] | None = None
    state_url = urljoin(base_url.rstrip("/") + "/", "admin/state")
    selftest_url = urljoin(base_url.rstrip("/") + "/", "admin/self-test")

    while time.monotonic() < deadline:
        try:
            response = session.get(health_url, headers=headers, timeout=request_timeout_s)
        except req_exc.RequestException as exc:
            last_reason = str(exc)
        else:
            if response.status_code in (401, 403):
                raise CEBridgeError("Complex Editor authentication failed during preflight")
            payload = _extract_payload(response)
            if payload:
                last_payload = payload
            if response.ok and payload.get("ready") is True:
                _record_preflight_success(payload)
                return payload
            reason = payload.get("reason") or payload.get("detail") or payload.get("status")
            state_payload: Dict[str, Any] | None = None
            try:
                state_resp = session.get(state_url, headers=headers, timeout=request_timeout_s)
            except req_exc.RequestException:
                state_resp = None
            if state_resp is not None:
                state_payload = _extract_payload(state_resp)
                if state_payload:
                    last_payload = state_payload
                    if state_resp.ok and state_payload.get("ready") is True:
                        _record_preflight_success(state_payload)
                        return state_payload
                    reason = state_payload.get("reason") or reason
            try:
                session.post(selftest_url, headers=headers, timeout=request_timeout_s)
            except req_exc.RequestException:
                pass
            if isinstance(reason, str) and reason.strip():
                last_reason = reason.strip()
        time.sleep(poll_delay)

    if last_payload and last_payload.get("ready"):
        _record_preflight_success(last_payload)
        return last_payload

    message = "CE is still warming up"
    if last_reason:
        message = f"{message} (last reason: {last_reason})"
    raise CEBridgeError(message)


def reset_transport_state() -> None:
    """Reset cached HTTP session and preflight markers (primarily for tests)."""

    global _SESSION, _LAST_PREFLIGHT_TS, _LAST_PREFLIGHT_PAYLOAD
    _SESSION = None
    _LAST_PREFLIGHT_TS = None
    _LAST_PREFLIGHT_PAYLOAD = None

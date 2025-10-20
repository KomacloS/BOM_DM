from __future__ import annotations

from threading import Lock
from typing import Optional

import requests

_SESSION: Optional[requests.Session] = None
_SESSION_BASE: Optional[str] = None
_LOCK = Lock()


def get_session(base_url: Optional[str] = None) -> requests.Session:
    """
    Return a process-wide requests.Session configured for the CE bridge.

    The session is recreated when the target base URL changes so that per-host
    connection pools remain accurate.
    """
    normalized = (base_url or "").strip().rstrip("/")

    global _SESSION, _SESSION_BASE
    with _LOCK:
        if _SESSION is None or (
            normalized and _SESSION_BASE and normalized != _SESSION_BASE
        ):
            if _SESSION is not None:
                try:
                    _SESSION.close()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass
            session = requests.Session()
            session.trust_env = False
            _SESSION = session
            _SESSION_BASE = normalized or None
        elif _SESSION is None:
            session = requests.Session()
            session.trust_env = False
            _SESSION = session
            _SESSION_BASE = normalized or None
    return _SESSION  # type: ignore[return-value]


def build_headers(
    token: Optional[str],
    trace_id: Optional[str],
    *,
    content_type: Optional[str] = None,
) -> dict[str, str]:
    """
    Build CE bridge headers with consistent auth and tracing fields.

    content_type should be provided for JSON POST requests; omit it for GETs.
    """
    headers: dict[str, str] = {"Accept": "application/json"}
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def reset_session() -> None:
    """Dispose of the cached session. Intended for tests."""
    global _SESSION, _SESSION_BASE
    with _LOCK:
        if _SESSION is not None:
            try:
                _SESSION.close()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        _SESSION = None
        _SESSION_BASE = None

from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from requests import Response
from requests import exceptions as req_exc

from app.config import get_complex_editor_settings
from app.integration.ce_bridge_manager import (
    CEBridgeError,
    bridge_owned_for_url,
    ensure_ce_bridge_ready,
    record_bridge_action,
    record_health_detail,
    record_state_snapshot,
    restart_bridge_with_ui,
)

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]


class CEAuthError(Exception):
    """Raised when authentication with the Complex Editor bridge fails."""


class CENotFound(Exception):
    """Raised when a Complex Editor resource could not be found."""


class CENetworkError(Exception):
    """Raised when the Complex Editor bridge is unreachable."""


class CEUserCancelled(Exception):
    """Raised when the user cancels an action via the Complex Editor UI."""


class CEAliasConflict(Exception):
    """Raised when adding aliases conflicts with existing Complex assignments."""

    def __init__(self, ce_id: str | int, conflicts: list[str] | None = None) -> None:
        self.ce_id = ce_id
        self.conflicts = conflicts or []
        message = f"Alias conflict for Complex {ce_id}"
        if self.conflicts:
            message = f"{message}: {', '.join(self.conflicts)}"
        super().__init__(message)


class CEBusyError(Exception):
    """Raised when the Complex Editor is busy (wizard already open)."""


class CEStaleLink(Exception):
    """Raised when a previously linked Complex can no longer be found."""

    def __init__(self, ce_id: str | int) -> None:
        self.ce_id = ce_id
        super().__init__(f"Complex {ce_id} not found")

@dataclass
class _BridgeConfig:
    base_url: str
    token: str
    timeout: float
    auto_start: bool
    ui_enabled: bool
    is_local: bool
    host: str
    port: int


@dataclass
class _PreflightCache:
    base_url: str
    expires_at: float
    state: Dict[str, Any]


_SESSION: Optional[requests.Session] = None
_SESSION_BASE: Optional[str] = None
_PREFLIGHT_CACHE: Optional[_PreflightCache] = None

CACHE_TTL = 5.0


def _normalize_timeout(raw: Any, default: float = 10.0) -> float:
    try:
        if raw is None:
            return float(default)
        value = float(raw)
        return max(0.1, value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_base_url(raw: str) -> Tuple[str, bool, str, int]:
    text = (raw or "").strip() or "http://127.0.0.1:8765"
    parsed = urlparse(text)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    if host in {"0.0.0.0", "::", "", "0"}:
        host = "127.0.0.1"
    try:
        is_local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_local = host.lower() == "localhost"
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    netloc = host
    if (scheme == "https" and port != 443) or (scheme == "http" and port != 80) or parsed.port is not None:
        netloc = f"{host}:{port}"
    rebuilt = urlunparse((scheme, netloc, parsed.path or "", "", "", ""))
    return rebuilt.rstrip("/"), is_local, host, port


def _load_bridge_config() -> _BridgeConfig:
    settings = get_complex_editor_settings()
    if not isinstance(settings, dict):
        raise CENetworkError("Complex Editor settings unavailable")
    bridge_cfg = settings.get("bridge", {})
    if not isinstance(bridge_cfg, dict) or not bridge_cfg.get("enabled", True):
        raise CENetworkError("Complex Editor integration is disabled.")

    base_url, is_local, host, port = _normalize_base_url(str(bridge_cfg.get("base_url") or "http://127.0.0.1:8765"))
    timeout = _normalize_timeout(bridge_cfg.get("request_timeout_seconds"), 10.0)
    token = str(bridge_cfg.get("auth_token") or "").strip()

    return _BridgeConfig(
        base_url=base_url,
        token=token,
        timeout=timeout,
        auto_start=bool(settings.get("auto_start_bridge", True)),
        ui_enabled=bool(settings.get("ui_enabled", True)),
        is_local=is_local,
        host=host,
        port=port,
    )


def _session_for(base_url: str) -> requests.Session:
    global _SESSION, _SESSION_BASE
    if _SESSION is None or _SESSION_BASE != base_url:
        if _SESSION is not None:
            try:
                _SESSION.close()
            except Exception:  # pragma: no cover - defensive
                pass
        session = requests.Session()
        session.trust_env = False
        _SESSION = session
        _SESSION_BASE = base_url
    return _SESSION


def _build_headers(token: str, *, json_body: bool = False) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _handshake_budget(timeout: float) -> float:
    return max(15.0, min(timeout * 3.0, 60.0))


def _start_bridge(config: _BridgeConfig, require_ui: bool) -> None:
    try:
        ensure_ce_bridge_ready(timeout_seconds=config.timeout, require_ui=require_ui)
    except CEBridgeError as exc:
        raise CENetworkError(str(exc)) from exc


def _post_selftest(session: requests.Session, url: str, headers: Dict[str, str], timeout: float) -> None:
    payload_headers = dict(headers)
    payload_headers.setdefault("Content-Type", "application/json")
    try:
        session.post(url, headers=payload_headers, json={}, timeout=timeout)
    except req_exc.RequestException:
        logger.debug("Complex Editor self-test probe failed", exc_info=True)


def _fetch_health_reason(
    session: requests.Session,
    url: str,
    headers: Dict[str, str],
    timeout: float,
) -> Optional[str]:
    try:
        response = session.get(url, headers=headers, timeout=timeout)
    except req_exc.RequestException:
        return None
    if response.status_code != 503:
        return None
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("reason")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    text = (response.text or "").strip()
    return text or None


def _preflight(
    config: _BridgeConfig,
    *,
    require_ui: bool = False,
    status_callback: Optional[StatusCallback] = None,
    allow_cached: bool = True,
) -> Dict[str, Any]:
    global _PREFLIGHT_CACHE
    if require_ui and not config.ui_enabled and config.is_local:
        raise CENetworkError("Complex Editor UI is disabled in settings.")

    cache = _PREFLIGHT_CACHE
    now = time.monotonic()
    if allow_cached and cache and cache.base_url == config.base_url and cache.expires_at > now:
        return cache.state

    session = _session_for(config.base_url)
    headers = _build_headers(config.token)
    state_url = _url(config.base_url, "state")
    selftest_url = _url(config.base_url, "selftest")
    health_url = _url(config.base_url, "health")

    deadline = now + _handshake_budget(config.timeout)
    next_selftest = now
    started_bridge = False
    announced = False
    attempted_ui_restart = False
    last_payload: Optional[Dict[str, Any]] = None
    last_ready_error: Optional[str] = None

    while True:
        current = time.monotonic()
        if current >= deadline:
            reason = _fetch_health_reason(session, health_url, headers, config.timeout)
            message = "Complex Editor bridge did not become ready in time"
            details: List[str] = []
            if last_ready_error:
                details.append(str(last_ready_error))
            if reason:
                details.append(str(reason))
                record_health_detail(str(reason))
            if details:
                message = f"{message}: {'; '.join(details)}"
            if last_payload is not None:
                record_state_snapshot(last_payload)
            action_note = "Preflight timeout"
            if details:
                action_note = f"Preflight timeout: {'; '.join(details)}"
            record_bridge_action(action_note)
            raise CENetworkError(message)

        if status_callback and not announced:
            status_callback("Starting Complex Editor (running diagnostics)...")
            announced = True

        try:
            response = session.get(state_url, headers=headers, timeout=config.timeout)
        except (req_exc.Timeout, req_exc.ConnectionError, req_exc.ConnectTimeout) as exc:
            if config.is_local and config.auto_start and not started_bridge:
                _PREFLIGHT_CACHE = None
                record_bridge_action("Preflight auto-starting bridge (connection error)")
                _start_bridge(config, require_ui=require_ui and config.ui_enabled)
                started_bridge = True
                time.sleep(0.2)
                continue
            raise CENetworkError("Cannot reach Complex Editor bridge") from exc
        except req_exc.RequestException as exc:
            raise CENetworkError("Unexpected bridge communication error") from exc

        if response.status_code in (401, 403):
            raise CEAuthError("Invalid/expired CE bridge token; update the token in settings.")
        if response.status_code >= 500:
            if config.is_local and config.auto_start and not started_bridge:
                _PREFLIGHT_CACHE = None
                record_bridge_action("Preflight auto-starting bridge (HTTP %s)" % response.status_code)
                _start_bridge(config, require_ui=require_ui and config.ui_enabled)
                started_bridge = True
                time.sleep(0.2)
                continue
            if current >= next_selftest:
                _post_selftest(session, selftest_url, headers, config.timeout)
                next_selftest = current + 3.0
            time.sleep(0.3)
            continue
        if response.status_code >= 400:
            raise CENetworkError(f"Complex Editor bridge returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise CENetworkError("Complex Editor bridge returned invalid JSON") from exc
        if not isinstance(payload, dict):
            payload = {}

        last_payload = payload
        last_ready_error = payload.get("last_ready_error") if isinstance(payload.get("last_ready_error"), str) else last_ready_error
        record_state_snapshot(payload)

        if payload.get("ready"):
            wizard_available = bool(payload.get("wizard_available"))
            if require_ui and not wizard_available:
                if config.is_local and config.ui_enabled and bridge_owned_for_url(config.base_url) and not attempted_ui_restart:
                    if status_callback:
                        status_callback("Complex Editor is running without UI; restarting with UI...")
                    _PREFLIGHT_CACHE = None
                    record_bridge_action("Attempting headless-to-UI restart via bridge restart")
                    try:
                        restart_bridge_with_ui(max(config.timeout, 5.0))
                    except CEBridgeError as exc:
                        raise CENetworkError(str(exc)) from exc
                    attempted_ui_restart = True
                    started_bridge = True
                    deadline = time.monotonic() + _handshake_budget(config.timeout)
                    time.sleep(0.3)
                    continue
                if status_callback:
                    status_callback("Complex Editor is running without UI; opening the wizard...")
            _PREFLIGHT_CACHE = _PreflightCache(
                base_url=config.base_url,
                expires_at=time.monotonic() + CACHE_TTL,
                state=payload,
            )
            return payload

        if current >= next_selftest:
            _post_selftest(session, selftest_url, headers, config.timeout)
            next_selftest = current + 3.0
        time.sleep(0.3)


def _perform_request(
    config: _BridgeConfig,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    require_ui: bool = False,
    status_callback: Optional[StatusCallback] = None,
    allow_cached: bool = True,
) -> Tuple[Response, Dict[str, Any]]:
    state = _preflight(
        config,
        require_ui=require_ui,
        status_callback=status_callback,
        allow_cached=allow_cached,
    )
    session = _session_for(config.base_url)
    headers = _build_headers(config.token, json_body=json_body is not None)
    url = _url(config.base_url, path)
    try:
        response = session.request(
            method=method,
            url=url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=config.timeout,
        )
    except (req_exc.Timeout, req_exc.ConnectTimeout, req_exc.ConnectionError) as exc:
        raise CENetworkError("Cannot reach Complex Editor bridge") from exc
    except req_exc.RequestException as exc:
        raise CENetworkError("Unexpected bridge communication error") from exc

    if response.status_code in (401, 403):
        raise CEAuthError("Invalid/expired CE bridge token; update the token in settings.")
    if response.status_code == 404:
        raise CENotFound("Complex Editor resource not found")
    return response, state


def _json_from_response(response: Response) -> Any:
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise CENetworkError("Complex Editor bridge returned invalid JSON") from exc


def _raise_server_error(
    config: _BridgeConfig,
    response: Response,
    state: Optional[Dict[str, Any]] = None,
) -> None:
    session = _session_for(config.base_url)
    headers = _build_headers(config.token)
    health_url = _url(config.base_url, "health")
    reason = _fetch_health_reason(session, health_url, headers, config.timeout)
    if not reason and state and isinstance(state, dict):
        state_reason = state.get("last_ready_error")
        if isinstance(state_reason, str) and state_reason.strip():
            reason = state_reason.strip()
    if reason:
        record_health_detail(reason)
    message = f"Complex Editor bridge returned HTTP {response.status_code}"
    if reason:
        message = f"{message}: {reason}"
    raise CENetworkError(message)


def _extract_reason(payload: Any) -> str:
    if isinstance(payload, dict):
        value = payload.get("reason") or payload.get("detail")
        if isinstance(value, str):
            return value
    return ""


def _wait_for_wizard_close(
    config: _BridgeConfig,
    *,
    status_callback: Optional[StatusCallback] = None,
) -> None:
    session = _session_for(config.base_url)
    headers = _build_headers(config.token)
    state_url = _url(config.base_url, "state")
    deadline = time.monotonic() + _handshake_budget(config.timeout)
    announced = False
    last_payload: Optional[Dict[str, Any]] = None

    while time.monotonic() < deadline:
        try:
            response = session.get(state_url, headers=headers, timeout=config.timeout)
        except req_exc.RequestException:
            time.sleep(0.5)
            continue

        if response.status_code in (401, 403):
            raise CEAuthError("Invalid/expired CE bridge token; update the token in settings.")
        if response.status_code >= 500:
            time.sleep(0.5)
            continue

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        last_payload = payload
        record_state_snapshot(payload)
        if status_callback and not announced:
            status_callback("Complex Editor is already showing the wizard; waiting to retry...")
            announced = True
        if not payload.get("wizard_open"):
            global _PREFLIGHT_CACHE
            _PREFLIGHT_CACHE = _PreflightCache(
                base_url=config.base_url,
                expires_at=time.monotonic() + CACHE_TTL,
                state=payload,
            )
            return
        time.sleep(0.5)
    reason = ""
    if last_payload:
        reason = str(last_payload.get("last_ready_error") or "")
    record_bridge_action("Wizard wait timeout")
    if reason:
        raise CENetworkError(f"Complex Editor wizard did not close in time: {reason}")
    raise CENetworkError("Complex Editor wizard did not close in time.")


def add_aliases(ce_id: str | int, aliases: List[str]) -> Dict[str, Any]:
    config = _load_bridge_config()
    body = {"add": aliases, "remove": []}
    response, state = _perform_request(
        config,
        "POST",
        f"/complexes/{ce_id}/aliases",
        json_body=body,
    )
    if response.status_code == 200:
        payload = _json_from_response(response)
        record_bridge_action(f"Added aliases {aliases} to Complex {ce_id}")
        return payload if isinstance(payload, dict) else {}
    if response.status_code == 409:
        payload = _json_from_response(response)
        conflicts_data = []
        if isinstance(payload, dict):
            raw_conflicts = payload.get("conflicts") or []
            if isinstance(raw_conflicts, list):
                conflicts_data = [str(item) for item in raw_conflicts]
        raise CEAliasConflict(ce_id, conflicts_data)
    if response.status_code in (401, 403):
        raise CEAuthError("Invalid/expired CE bridge token; update the token in settings.")
    if response.status_code == 404:
        raise CENotFound("Complex Editor resource not found")
    if response.status_code >= 500:
        _raise_server_error(config, response, state)
    raise CENetworkError(f"Complex Editor bridge returned HTTP {response.status_code}")





def open_complex(
    ce_id: str | int,
    *,
    status_callback: Optional[StatusCallback] = None,
    allow_cached: bool = True,
    retries: int = 1,
    mode: str = "edit",
) -> bool:
    config = _load_bridge_config()
    record_bridge_action(f"Open request for Complex {ce_id}")
    state = _preflight(
        config,
        require_ui=True,
        status_callback=status_callback,
        allow_cached=allow_cached,
    )
    if _state_has_focus(state, ce_id):
        record_bridge_action(f"Complex {ce_id} already focused; bringing to front")
        _bring_ce_to_front(config)
        if status_callback:
            status_callback("Already open in Complex Editor.")
        return True

    if status_callback:
        status_callback("Opening in Complex Editor...")

    session = _session_for(config.base_url)
    headers = _build_headers(config.token, json_body=True)
    url = _url(config.base_url, f"complexes/{ce_id}/open")
    try:
        response = session.post(
            url,
            json={"mode": mode},
            headers=headers,
            timeout=config.timeout,
        )
    except (req_exc.Timeout, req_exc.ConnectTimeout, req_exc.ConnectionError) as exc:
        raise CENetworkError("Cannot reach Complex Editor bridge") from exc
    except req_exc.RequestException as exc:
        raise CENetworkError("Unexpected bridge communication error") from exc

    if response.status_code in (401, 403):
        raise CEAuthError(f"CE bridge token invalid ({config.base_url})")
    if response.status_code == 404:
        raise CEStaleLink(ce_id)
    if response.status_code == 409:
        payload = _json_from_response(response)
        reason = _extract_reason(payload).lower()
        record_bridge_action(f"Open request for Complex {ce_id} returned busy ({reason or 'unspecified'})")
        try:
            ensure_ce_bridge_ready(timeout_seconds=max(config.timeout, 5.0), require_ui=True)
        except CEBridgeError:
            pass
        raise CEBusyError(reason or "busy")
    if response.status_code == 503:
        payload = _json_from_response(response)
        reason = _extract_reason(payload).lower()
        if "headless" in reason and retries > 0 and config.is_local and bridge_owned_for_url(config.base_url):
            record_bridge_action("Open request hit headless CE; restarting with UI")
            restart_bridge_with_ui(max(config.timeout, 5.0))
            global _PREFLIGHT_CACHE
            _PREFLIGHT_CACHE = None
            return open_complex(
                ce_id,
                status_callback=status_callback,
                allow_cached=False,
                retries=retries - 1,
                mode=mode,
            )
        _raise_server_error(config, response, state)
    if response.status_code >= 500:
        _raise_server_error(config, response, state)
    if not 200 <= response.status_code < 300:
        raise CENetworkError(f"Complex Editor bridge returned HTTP {response.status_code}")

    record_bridge_action(f"Opened Complex {ce_id} in editor")
    _wait_for_focus_or_wizard(config, ce_id)
    return False


def _state_has_focus(state: Optional[Dict[str, Any]], ce_id: str | int) -> bool:
    if not isinstance(state, dict):
        return False
    focused = state.get("focused_comp_id")
    target_int: Optional[int]
    try:
        target_int = int(str(ce_id))
    except (TypeError, ValueError):
        target_int = None
    if target_int is not None and focused == target_int:
        return True
    if target_int is None and focused == ce_id:
        return True
    return False


def _bring_ce_to_front(config: _BridgeConfig) -> None:
    if not config.ui_enabled:
        return
    try:
        ensure_ce_bridge_ready(timeout_seconds=max(config.timeout, 5.0), require_ui=True)
    except CEBridgeError:
        pass

def _wait_for_focus_or_wizard(config: _BridgeConfig, ce_id: str | int, *, timeout: float = 5.0) -> None:
    session = _session_for(config.base_url)
    headers = _build_headers(config.token)
    state_url = _url(config.base_url, "state")
    deadline = time.monotonic() + timeout
    target_id: Optional[int]
    try:
        target_id = int(str(ce_id))
    except ValueError:
        target_id = None

    while time.monotonic() < deadline:
        try:
            response = session.get(state_url, headers=headers, timeout=config.timeout)
        except req_exc.RequestException:
            time.sleep(0.25)
            continue

        if response.status_code != 200:
            time.sleep(0.25)
            continue

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        record_state_snapshot(payload)
        focused = payload.get("focused_comp_id")
        if target_id is not None and focused == target_id:
            record_bridge_action(f"Complex {ce_id} focused in editor")
            return
        if payload.get("wizard_open"):
            record_bridge_action(f"Complex {ce_id} wizard opened")
            return
        time.sleep(0.25)

    record_bridge_action(f"Open request for Complex {ce_id} completed without focus confirmation")
def healthcheck() -> Dict[str, Any]:
    config = _load_bridge_config()
    session = _session_for(config.base_url)
    headers = _build_headers(config.token)
    url = _url(config.base_url, "health")
    try:
        response = session.get(url, headers=headers, timeout=config.timeout)
    except (req_exc.Timeout, req_exc.ConnectTimeout, req_exc.ConnectionError) as exc:
        raise CENetworkError("Cannot reach Complex Editor bridge") from exc
    except req_exc.RequestException as exc:
        raise CENetworkError("Unexpected bridge communication error") from exc
    if response.status_code in (401, 403):
        raise CEAuthError("Invalid/expired CE bridge token; update the token in settings.")
    if not response.ok:
        _raise_server_error(config, response)
    payload = _json_from_response(response)
    if not isinstance(payload, dict):
        raise CENetworkError("Unexpected payload from health endpoint")
    return payload


def search_complexes(pn: str, limit: int = 20) -> List[Dict[str, Any]]:
    config = _load_bridge_config()
    response, state = _perform_request(
        config,
        "GET",
        "/complexes/search",
        params={"pn": pn, "limit": max(1, min(int(limit), 200))},
    )
    if response.status_code >= 500:
        _raise_server_error(config, response, state)
    payload = _json_from_response(response)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise CENetworkError("Unexpected payload from search_complexes")


def get_complex(ce_id: str) -> Dict[str, Any]:
    config = _load_bridge_config()
    response, state = _perform_request(
        config,
        "GET",
        f"/complexes/{ce_id}",
    )
    if response.status_code >= 500:
        _raise_server_error(config, response, state)
    payload = _json_from_response(response)
    if not isinstance(payload, dict):
        raise CENetworkError("Unexpected payload from get_complex")
    return payload


def create_complex(
    pn: str,
    aliases: Optional[List[str]] = None,
    *,
    status_callback: Optional[StatusCallback] = None,
) -> Dict[str, Any]:
    config = _load_bridge_config()
    body: Dict[str, Any] = {"pn": pn}
    if aliases:
        body["aliases"] = aliases
    return _create_complex_with_retry(config, body, status_callback=status_callback, attempt=1)


def _create_complex_with_retry(
    config: _BridgeConfig,
    body: Dict[str, Any],
    *,
    status_callback: Optional[StatusCallback],
    attempt: int,
) -> Dict[str, Any]:
    global _PREFLIGHT_CACHE
    allow_cached = attempt == 1
    response, state = _perform_request(
        config,
        "POST",
        "/complexes",
        json_body=body,
        require_ui=True,
        status_callback=status_callback,
        allow_cached=allow_cached,
    )

    if response.status_code in (200, 201):
        payload = _json_from_response(response)
        if not isinstance(payload, dict):
            raise CENetworkError("Unexpected payload from create_complex")
        return payload

    if response.status_code == 409:
        payload = _json_from_response(response)
        reason = _extract_reason(payload).lower()
        if reason == "cancelled by user":
            raise CEUserCancelled("Creation cancelled")
        if reason == "wizard busy":
            if status_callback:
                status_callback("Complex Editor is already showing the wizard; waiting to retry...")
            _wait_for_wizard_close(config, status_callback=status_callback)
            if attempt >= 2:
                raise CENetworkError("Complex Editor wizard remained busy.")
            return _create_complex_with_retry(
                config,
                body,
                status_callback=status_callback,
                attempt=attempt + 1,
            )
        raise CENetworkError("Complex Editor bridge reported a conflict.")

    if response.status_code == 503:
        payload = _json_from_response(response)
        reason = _extract_reason(payload).lower()
        if "wizard unavailable" in reason and "headless" in reason:
            if status_callback:
                status_callback("Complex Editor is running without UI; opening the wizard with UI...")
            record_bridge_action("Create flow handling headless bridge (attempt %s)" % attempt)
            _start_bridge(config, require_ui=True)
            global _PREFLIGHT_CACHE
            _PREFLIGHT_CACHE = None
            if attempt >= 2:
                raise CENetworkError("Complex Editor bridge stayed headless after restart.")
            return _create_complex_with_retry(
                config,
                body,
                status_callback=status_callback,
                attempt=attempt + 1,
            )
        _raise_server_error(config, response, state)

    if response.status_code >= 500:
        _raise_server_error(config, response, state)

    raise CENetworkError(f"Complex Editor bridge returned HTTP {response.status_code}")

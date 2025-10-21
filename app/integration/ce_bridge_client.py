from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

from requests import Response
from requests import exceptions as req_exc

from app.config import get_complex_editor_settings, get_viva_export_settings
from app.integration.ce_supervisor import CEBridgeError, ensure_ready
from app.integration import ce_bridge_transport

logger = logging.getLogger(__name__)

_LAST_BASE_URL: Optional[str] = None


class CEAuthError(Exception):
    """Raised when authentication with the Complex Editor bridge fails."""


class CENotFound(Exception):
    """Raised when a Complex Editor resource could not be found."""


class CENetworkError(Exception):
    """Raised when the Complex Editor bridge is unreachable."""


class CEUserCancelled(Exception):
    """Raised when the user cancels an action via the Complex Editor UI."""


class CEWizardUnavailable(Exception):
    """Raised when the Complex Editor wizard UI is not available via the bridge."""


class CEExportError(CENetworkError):
    """Base class for Complex Editor export failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        reason: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.payload: Dict[str, Any] = dict(payload or {})
        trace = self.payload.get("trace_id")
        self.trace_id = str(trace).strip() if isinstance(trace, str) else None


class CEExportBusyError(CEExportError):
    """Raised when the bridge reports that an export cannot start because CE is busy."""


class CEExportStrictError(CEExportError):
    """Raised when the bridge blocks the export due to missing/unlinked complexes."""

    def __init__(
        self,
        message: str,
        *,
        unlinked: Optional[List[str]] = None,
        missing: Optional[List[str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        status_code: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            reason=reason,
            payload=payload,
        )
        self.unlinked: List[str] = [str(x) for x in unlinked or []]
        self.missing: List[str] = [str(x) for x in missing or []]


class CEPNResolutionError(CEExportError):
    """Raised when part numbers could not be resolved to Complex IDs."""

    def __init__(self, unresolved: Sequence[str]) -> None:
        payload = {"unresolved": list(unresolved)}
        message = "Complex Editor could not resolve some part numbers to Complex IDs"
        super().__init__(message, status_code=409, reason="pn_resolution", payload=payload)
        self.unresolved: List[str] = [str(p) for p in unresolved]


def _normalize_timeout(raw: Any, default: float = 10.0) -> float:
    try:
        if raw is None:
            return float(default)
        return max(0.1, float(raw))
    except (TypeError, ValueError):
        return float(default)


def _normalize_base_url(base_url: str) -> str:
    text = (base_url or "http://127.0.0.1:8765").strip()
    if not text:
        text = "http://127.0.0.1:8765"
    parsed = urlparse(text if "://" in text else f"http://{text}")
    host = parsed.hostname or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    scheme = parsed.scheme or "http"
    port = parsed.port
    netloc = host
    if port:
        netloc = f"{host}:{port}"
    path = parsed.path or ""
    normalized = urlunparse((scheme, netloc, path, "", "", ""))
    return normalized.rstrip("/")


def _resolve_bridge_config() -> tuple[str, str, float]:
    settings = get_complex_editor_settings()
    bridge_cfg = settings.get("bridge", {}) if isinstance(settings, dict) else {}
    base_url = str(bridge_cfg.get("base_url") or "http://127.0.0.1:8765")
    token = str(bridge_cfg.get("auth_token") or "").strip()
    timeout = _normalize_timeout(bridge_cfg.get("request_timeout_seconds"), 10.0)

    viva_cfg = get_viva_export_settings()
    if isinstance(viva_cfg, dict):
        override = viva_cfg.get("ce_bridge_url")
        if override:
            base_url = str(override).strip() or base_url
        override_token = viva_cfg.get("ce_auth_token")
        if override_token is not None:
            candidate = str(override_token).strip()
            if candidate:
                token = candidate
    base_url = _normalize_base_url(base_url)
    return base_url, token, timeout


def resolve_bridge_connection() -> tuple[str, str, float]:
    """Return the configured bridge connection tuple ``(base_url, token, timeout)``."""

    return _resolve_bridge_config()


def _request(
    method: str,
    endpoint: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    allow_conflict: bool = False,
    trace_id: Optional[str] = None,
) -> Response:
    active_trace = (trace_id or "").strip() or uuid.uuid4().hex
    try:
        ensure_ready(trace_id=active_trace)
    except CEBridgeError as exc:
        raise CENetworkError(str(exc)) from exc
    base_url, token, timeout = _resolve_bridge_config()

    if not ce_bridge_transport.is_preflight_recent():
        try:
            ce_bridge_transport.preflight_ready(
                base_url,
                token,
                request_timeout_s=float(timeout),
                trace_id=active_trace,
            )
        except CEBridgeError as exc:
            raise CENetworkError(str(exc)) from exc

    headers: Dict[str, str] = ce_bridge_transport.build_headers(
        token,
        trace_id=active_trace,
    )
    session = ce_bridge_transport.get_session()

    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))

    global _LAST_BASE_URL
    _LAST_BASE_URL = base_url

    logger.debug("CE bridge request %s %s", method, url)
    try:
        response = session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=timeout,
        )
    except (req_exc.Timeout, req_exc.ConnectionError) as exc:
        raise CENetworkError("Cannot reach Complex Editor bridge") from exc
    except req_exc.RequestException as exc:  # pragma: no cover - defensive
        raise CENetworkError("Unexpected bridge communication error") from exc

    if response.status_code in (401, 403):
        raise CEAuthError("Complex Editor bridge rejected authentication")
    if response.status_code == 404:
        raise CENotFound("Complex Editor resource not found")
    if response.status_code == 409:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict) and payload.get("reason") == "cancelled":
            raise CEUserCancelled("Complex Editor action was cancelled")
        if allow_conflict and isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("reason") or "").strip().lower()
            if detail == "wizard handler unavailable":
                return response
        if allow_conflict:
            return response

    if not response.ok:
        if allow_conflict:
            return response
        try:
            response.raise_for_status()
        except req_exc.HTTPError as exc:
            raise CENetworkError(
                f"Complex Editor bridge returned HTTP {response.status_code}"
            ) from exc
    return response


def _json_from_response(response: Response) -> Any:
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise CENetworkError("Complex Editor bridge returned invalid JSON") from exc


def healthcheck() -> Dict[str, Any]:
    """Return the health status from the Complex Editor bridge."""
    response = _request("GET", "/admin/health")
    payload = _json_from_response(response)
    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise CENetworkError("Unexpected payload from health endpoint")
    return payload


def search_complexes(pn: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Search complexes by part number or alias."""
    response = _request(
        "GET",
        "/complexes/search",
        params={"pn": pn, "limit": limit},
    )
    payload = _json_from_response(response)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise CENetworkError("Unexpected payload from search_complexes")


def get_complex(ce_id: str) -> Dict[str, Any]:
    """Fetch a single complex detail from the bridge."""
    response = _request("GET", f"/complexes/{ce_id}")
    payload = _json_from_response(response)
    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise CENetworkError("Unexpected payload from get_complex")
    return payload


def lookup_complex_ids(pns: Sequence[str]) -> Tuple[Dict[str, int], List[str]]:
    """Resolve part numbers to Complex IDs via the bridge search endpoint."""

    mapping: Dict[str, int] = {}
    unresolved: List[str] = []
    for pn in pns:
        target = (pn or "").strip()
        if not target:
            continue
        try:
            results = search_complexes(target, limit=5)
        except CENetworkError:
            unresolved.append(target)
            continue
        lower = target.lower()
        resolved_id: Optional[int] = None
        for item in results:
            if not isinstance(item, dict):
                continue
            candidate_id = item.get("id") or item.get("comp_id") or item.get("complex_id")
            aliases = item.get("aliases")
            alias_match = False
            if isinstance(aliases, (list, tuple)):
                alias_match = lower in {
                    str(alias).strip().lower()
                    for alias in aliases
                    if isinstance(alias, (str, int)) and str(alias).strip()
                }
            candidate_pn = str(
                item.get("pn")
                or item.get("part_number")
                or item.get("name")
                or ""
            ).strip().lower()
            if candidate_pn == lower or alias_match:
                try:
                    resolved_id = int(candidate_id)
                except (TypeError, ValueError):
                    resolved_id = None
                if resolved_id is not None:
                    break
        if resolved_id is not None:
            mapping[target] = resolved_id
        else:
            unresolved.append(target)
    return mapping, unresolved


def _int_list(values: Iterable[int | str]) -> List[int]:
    unique: List[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            num = int(value)
        except (TypeError, ValueError):
            continue
        if num in seen:
            continue
        seen.add(num)
        unique.append(num)
    return unique


def _pn_list(values: Iterable[str]) -> List[str]:
    unique: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return unique


def _raise_export_error(status_code: int, payload: Dict[str, Any]) -> None:
    reason = str(payload.get("reason") or payload.get("detail") or "").strip().lower()
    message = str(
        payload.get("message")
        or payload.get("detail")
        or payload.get("reason")
        or "Complex Editor rejected the export"
    )
    if reason == "busy":
        raise CEExportBusyError(
            message or "Complex Editor is busy; finish the current operation and retry.",
            status_code=status_code,
            reason=reason,
            payload=payload,
        )
    if reason in {"unlinked_or_missing", "unlinked", "missing"}:
        unlinked = payload.get("unlinked")
        missing = payload.get("missing")
        raise CEExportStrictError(
            message or "Some BOM rows are missing Complex links",
            unlinked=[str(x) for x in unlinked or [] if isinstance(x, (str, int))],
            missing=[str(x) for x in missing or [] if isinstance(x, (str, int))],
            payload=payload,
            status_code=status_code,
            reason=reason,
        )
    if reason == "invalid_comp_ids":
        raise CEExportError(
            message or "Complex Editor reported unknown Complex IDs",
            status_code=status_code,
            reason=reason,
            payload=payload,
        )
    if reason == "outdir_unwritable":
        raise CEExportError(
            message or "Export directory is not writable",
            status_code=status_code,
            reason=reason,
            payload=payload,
        )
    if reason == "bad_filename":
        raise CEExportError(
            message or "Invalid MDB filename",
            status_code=status_code,
            reason=reason,
            payload=payload,
        )
    if reason == "filesystem_error":
        raise CEExportError(
            message or "Complex Editor encountered a filesystem error",
            status_code=status_code,
            reason=reason,
            payload=payload,
        )
    if reason == "template_missing_or_incompatible":
        raise CEExportError(
            message or "Complex Editor template asset is missing or incompatible",
            status_code=status_code,
            reason=reason,
            payload=payload,
        )
    if reason == "data_invalid":
        raise CEExportError(
            message or "Complex Editor reported invalid source data",
            status_code=status_code,
            reason=reason,
            payload=payload,
        )
    if status_code == 503 and reason == "headless":
        raise CEExportError(
            message or "Complex Editor exporter unavailable in headless mode",
            status_code=status_code,
            reason=reason,
            payload=payload,
        )
    raise CEExportError(
        message or f"Complex Editor export failed with HTTP {status_code}",
        status_code=status_code,
        reason=reason or None,
        payload=payload,
    )


def export_complexes_mdb(
    comp_ids: Iterable[int | str],
    out_dir: str,
    *,
    mdb_name: str = "bom_complexes.mdb",
    require_linked: bool = True,
    pns: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Trigger an MDB export for the provided Complex IDs via the bridge."""

    ids = _int_list(comp_ids)
    pn_source: Iterable[str]
    if pns is not None:
        pn_source = pns
    else:
        pn_source = (str(value) for value in comp_ids if isinstance(value, str))
    pn_list = _pn_list(pn_source)
    body: Dict[str, Any] = {
        "pns": pn_list,
        "comp_ids": ids,
        "out_dir": out_dir,
        "mdb_name": mdb_name,
        "require_linked": bool(require_linked),
    }

    response = _request(
        "POST",
        "/exports/mdb",
        json_body=body,
        allow_conflict=True,
    )

    payload = _json_from_response(response) if response.content else {}
    payload = payload if isinstance(payload, dict) else {}

    if response.status_code in (409, 503):
        _raise_export_error(response.status_code, payload)
    if response.status_code >= 500:
        raise CEExportError(
            "Complex Editor export failed due to an internal error",
            status_code=response.status_code,
            reason=str(payload.get("reason") or ""),
            payload=payload,
        )

    if isinstance(payload, dict):
        return payload
    if payload in (None, ""):
        return {}
    raise CEExportError(
        "Complex Editor returned an unexpected response for export",
        status_code=response.status_code,
        reason=str(payload.get("reason") or "") if isinstance(payload, dict) else None,
        payload=payload if isinstance(payload, dict) else {},
    )


def get_active_base_url() -> str:
    """Return the most recently used bridge base URL."""

    if _LAST_BASE_URL:
        return _LAST_BASE_URL
    base_url, _token, _timeout = _resolve_bridge_config()
    return base_url


def _append_trace(message: str, payload: Any) -> str:
    if not isinstance(payload, dict):
        return message
    trace = payload.get("trace_id")
    trace_text = str(trace).strip() if isinstance(trace, str) else ""
    if trace_text:
        return f"{message} (trace: {trace_text})"
    return message


def create_complex(pn: str, aliases: Optional[List[str]] = None) -> Dict[str, Any]:
    """Create a new complex in the bridge and return its representation."""

    body: Dict[str, Any] = {"pn": pn}
    if aliases:
        body["aliases"] = aliases
    response = _request("POST", "/complexes", json_body=body, allow_conflict=True)
    payload = _json_from_response(response) if response.content else {}
    status = response.status_code

    if status == 409:
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("reason") or "").strip()
            if detail.lower() == "wizard handler unavailable":
                raise CEWizardUnavailable(
                    detail or "Complex Editor wizard handler unavailable"
                )
            message = detail or "Complex Editor reported a conflict creating the complex."
        else:  # pragma: no cover - defensive
            message = "Complex Editor reported a conflict creating the complex."
        message = _append_trace(message, payload)
        raise CENetworkError(message)

    if status >= 500:
        if isinstance(payload, dict):
            message = str(payload.get("detail") or payload.get("reason") or "").strip()
        else:  # pragma: no cover - defensive
            message = "Complex Editor failed to create the complex."
        message = message or "Complex Editor failed to create the complex."
        message = _append_trace(message, payload)
        raise CENetworkError(message)

    if status >= 400:
        if isinstance(payload, dict):
            message = str(payload.get("detail") or payload.get("reason") or "").strip()
        else:  # pragma: no cover - defensive
            message = "Complex Editor bridge returned an error creating the complex."
        message = message or f"Complex Editor bridge returned HTTP {status}"
        message = _append_trace(message, payload)
        raise CENetworkError(message)

    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise CENetworkError("Unexpected payload from create_complex")
    return payload


def is_preflight_recent(max_age_s: float = 5.0) -> bool:
    """Expose the latest preflight status for UI helpers."""

    return ce_bridge_transport.is_preflight_recent(max_age_s)


def get_state() -> Dict[str, Any]:
    """Fetch the current bridge state payload."""

    response = _request("GET", "/state")
    payload = _json_from_response(response)
    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise CENetworkError("Unexpected payload from state endpoint")
    return payload


def wait_until_ready(
    *, timeout_s: float = 90.0, poll_interval_s: float = 1.0
) -> Dict[str, Any]:
    """Poll the bridge state until ``ready`` is ``True`` or timeout occurs."""

    deadline = time.monotonic() + max(timeout_s, 1.0)
    last_payload: Dict[str, Any] = {}
    last_error: Optional[Exception] = None
    interval = max(poll_interval_s, 0.1)
    while time.monotonic() < deadline:
        try:
            payload = get_state()
        except (CENetworkError, CEAuthError) as exc:
            last_error = exc
            payload = {}
        else:
            last_payload = payload
            if payload.get("ready") is True:
                return payload
        time.sleep(interval)
    reason = ""
    for key in ("reason", "detail", "status"):
        value = last_payload.get(key)
        if isinstance(value, str) and value.strip():
            reason = value.strip()
            break
    if reason:
        raise CENetworkError(
            f"Complex Editor bridge did not become ready (last status: {reason})"
        )
    if last_error is not None:
        raise CENetworkError(str(last_error))
    raise CENetworkError("Complex Editor bridge did not become ready in time")


def bring_to_front() -> None:
    """Ask the Complex Editor window to come to the foreground."""

    try:
        _request("POST", "/app/bring-to-front")
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug("Failed to bring Complex Editor to front: %s", exc)


def _is_editing_target(state: Dict[str, Any], ce_id: str) -> bool:
    """Return True if ``state`` reflects the edit wizard for ``ce_id``."""

    target = str(ce_id)
    wizard_open = bool(state.get("wizard_open"))
    editing_id = state.get("editing_comp_id")
    focused_id = state.get("focused_comp_id")

    if editing_id is not None:
        return str(editing_id) == target and wizard_open
    if wizard_open:
        if focused_id is None:
            return True
        return str(focused_id) == target
    return False


def _wait_for_edit_state(
    ce_id: str,
    *,
    poll_timeout_s: float = 5.0,
    poll_interval_s: float = 0.3,
) -> Dict[str, Any]:
    """Poll the bridge state until the edit UI for ``ce_id`` is active."""

    deadline = time.monotonic() + max(poll_timeout_s, 0.0)
    while True:
        response = _request("GET", "/state")
        state_payload = _json_from_response(response)
        state_payload = state_payload if isinstance(state_payload, dict) else {}
        if _is_editing_target(state_payload, ce_id):
            return state_payload
        if time.monotonic() >= deadline:
            break
        time.sleep(max(poll_interval_s, 0.05))
    raise CENetworkError(
        "Complex Editor did not confirm that the edit window opened in time"
    )


def open_complex(
    ce_id: str,
    *,
    mode: str = "edit",
    poll_timeout_s: float = 5.0,
    poll_interval_s: float = 0.3,
    bring_front: bool = True,
) -> Dict[str, Any]:
    """Open the specified complex in the Complex Editor UI."""

    if not ce_id:
        raise ValueError("Complex ID is required to open in Complex Editor")
    endpoint = f"/complexes/{ce_id}/open"
    body: Dict[str, Any] = {}
    if mode:
        body["mode"] = mode

    response = _request("POST", endpoint, json_body=body, allow_conflict=True)
    if response.status_code == 409:
        payload = _json_from_response(response) if response.content else {}
        message = "Complex Editor is busy; finish the current operation and retry."
        if isinstance(payload, dict):
            detail = str(
                payload.get("detail")
                or payload.get("reason")
                or payload.get("message")
                or ""
            ).strip()
            if detail:
                message = detail
        raise CENetworkError(message)

    if response.status_code >= 400:
        payload = _json_from_response(response) if response.content else {}
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("reason") or "").strip()
        else:  # pragma: no cover - defensive
            detail = ""
        message = detail or f"Complex Editor bridge returned HTTP {response.status_code}"
        message = _append_trace(message, payload)
        raise CENetworkError(message)

    state_payload = _wait_for_edit_state(
        str(ce_id),
        poll_timeout_s=poll_timeout_s,
        poll_interval_s=poll_interval_s,
    )
    if bring_front:
        bring_to_front()
    return state_payload
def coerce_comp_id(value: object) -> Optional[int]:
    """Return ``value`` normalised to a positive integer Complex ID."""

    try:
        if value is None:
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        text = str(value).strip()
        if not text:
            return None
        num = int(text)
        return num if num > 0 else None
    except (TypeError, ValueError):
        return None



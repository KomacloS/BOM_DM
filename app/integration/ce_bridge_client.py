from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from requests import Response
from requests import exceptions as req_exc

from app.config import get_complex_editor_settings
from app.integration.ce_bridge_manager import CEBridgeError, ensure_ce_bridge_ready

logger = logging.getLogger(__name__)


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


def _normalize_timeout(raw: Any, default: float = 10.0) -> float:
    try:
        if raw is None:
            return float(default)
        return max(0.1, float(raw))
    except (TypeError, ValueError):
        return float(default)


def _request(
    method: str,
    endpoint: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    allow_conflict: bool = False,
) -> Response:
    try:
        ensure_ce_bridge_ready()
    except CEBridgeError as exc:
        raise CENetworkError(str(exc)) from exc
    settings = get_complex_editor_settings()
    bridge_cfg = settings.get("bridge", {}) if isinstance(settings, dict) else {}
    base_url = str(bridge_cfg.get("base_url") or "http://127.0.0.1:8765")
    timeout = _normalize_timeout(bridge_cfg.get("request_timeout_seconds"), 10.0)

    headers: Dict[str, str] = {
        "Accept": "application/json",
    }
    token = str(bridge_cfg.get("auth_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))

    logger.debug("CE bridge request %s %s", method, url)
    try:
        response = requests.request(
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
    response = _request("GET", "/health")
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


def create_complex(pn: str, aliases: Optional[List[str]] = None) -> Dict[str, Any]:
    """Create a new complex in the bridge and return its representation."""
    body: Dict[str, Any] = {"pn": pn}
    if aliases:
        body["aliases"] = aliases
    response = _request("POST", "/complexes", json_body=body, allow_conflict=True)
    payload = _json_from_response(response) if response.content else {}
    if response.status_code == 409:
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("reason") or "").strip()
            if detail.lower() == "wizard handler unavailable":
                raise CEWizardUnavailable(
                    detail or "Complex Editor wizard handler unavailable"
                )
            message = detail or "Complex Editor reported a conflict creating the complex."
        else:  # pragma: no cover - defensive
            message = "Complex Editor reported a conflict creating the complex."
        raise CENetworkError(message)
    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise CENetworkError("Unexpected payload from create_complex")
    return payload

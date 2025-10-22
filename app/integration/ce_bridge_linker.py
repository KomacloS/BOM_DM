"""
CE Bridge Linker
----------------
High-level, ranked search and normalization diagnostics over the CE bridge.

Public API (stable):
- select_best_match(pn: str, *, limit: int = 50, timeout: float | None = None) -> LinkerDecision
- fetch_normalization_info(timeout: float | None = None) -> dict

Exceptions:
- LinkerError          : generic communication/transport/protocol failures
- LinkerInputError     : user input (e.g., wildcard-only) rejected before bridge call
- LinkerFeatureError   : bridge lacks required features (e.g., missing match_kind analysis)

Notes:
- The CE bridge may return `normalized_input` / `normalized_targets` at the top level
  (v0.1.0) or nested under `analysis` (future). We accept both.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

from requests import exceptions as req_exc

from app.integration import ce_bridge_transport
from app.integration.ce_bridge_client import resolve_bridge_connection

logger = logging.getLogger(__name__)


class LinkerError(RuntimeError):
    """Base error raised by the CE bridge linker client."""


class LinkerInputError(LinkerError):
    """Raised when the user input is rejected before contacting the bridge."""


class LinkerFeatureError(LinkerError):
    """Raised when the bridge does not support the features required by the linker."""


_STATE_CACHE: Tuple[float, Dict[str, Any]] | None = None
_STATE_CACHE_TTL = 30.0

_RANK_ORDER: Dict[str, int] = {
    "exact_pn": 0,
    "exact_alias": 1,
    "normalized_pn": 2,
    "normalized_alias": 3,
    "like": 4,
}


def _clean_aliases(values: Iterable[object] | None) -> list[str]:
    aliases: list[str] = []
    if not values:
        return aliases
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        aliases.append(text)
    return aliases


def _normalize_targets(values: Iterable[object] | None) -> list[str]:
    targets: list[str] = []
    if not values:
        return targets
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                targets.append(text)
    return targets


def _validate_input(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise LinkerInputError("Invalid part number (empty or wildcard-only)")
    if not any(ch.isalnum() for ch in text):
        raise LinkerInputError("Invalid part number (empty or wildcard-only)")
    return text


def _make_headers(token: str, trace_id: str) -> Dict[str, str]:
    return ce_bridge_transport.build_headers(token, trace_id=trace_id)


def _request_json(
    endpoint: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    trace_id: str,
    timeout: float,
) -> Dict[str, Any] | List[Any]:
    base_url, token, default_timeout = resolve_bridge_connection()
    session = ce_bridge_transport.get_session()
    headers = _make_headers(token, trace_id)
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))

    request_timeout = timeout or default_timeout

    try:
        response = session.get(
            url,
            headers=headers,
            params=params,
            timeout=request_timeout,
        )
    except (req_exc.Timeout, req_exc.ConnectionError) as exc:
        raise LinkerError("Complex Editor bridge is unreachable") from exc
    except req_exc.RequestException as exc:  # pragma: no cover - defensive
        raise LinkerError("Unexpected bridge communication error") from exc

    if response.status_code == 400:
        raise LinkerInputError("Invalid part number (empty or wildcard-only)")

    if not response.ok:
        body = response.text.strip()
        snippet = body if len(body) <= 500 else f"{body[:497]}…"
        raise LinkerError(
            f"Bridge responded with HTTP {response.status_code}: {snippet}"
        )

    if not response.content:
        return {}

    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise LinkerError("Bridge returned invalid JSON") from exc


def _probe_state(trace_id: str, timeout: float) -> Dict[str, Any]:
    global _STATE_CACHE
    now = time.monotonic()
    cached = _STATE_CACHE
    if cached and (now - cached[0]) <= _STATE_CACHE_TTL:
        return cached[1]

    payload = _request_json("/state", trace_id=trace_id, timeout=timeout)
    if not isinstance(payload, dict):
        raise LinkerError("Bridge /state endpoint returned unexpected payload")

    features = payload.get("features")
    if not isinstance(features, dict):
        raise LinkerFeatureError("CE Bridge does not expose required feature flags")

    search_kind = features.get("search_match_kind")
    if search_kind is not True:
        raise LinkerFeatureError(
            "CE Bridge is missing search match analysis; upgrade to v0.1.0 or newer."
        )

    normalization_version = features.get("normalization_rules_version")
    if normalization_version != "v1":
        raise LinkerFeatureError(
            "CE Bridge normalization rules are incompatible (expected v1)."
        )

    _STATE_CACHE = (now, payload)
    return payload


@dataclass
class LinkCandidate:
    """A single CE match candidate with analysis fields normalized for UI/logic."""

    id: str
    pn: str
    aliases: list[str]
    match_kind: str
    reason: str
    normalized_input: Optional[str]
    normalized_targets: list[str]
    raw: Dict[str, Any]


@dataclass
class LinkerDecision:
    """Ranked search decision with the full result set, best candidate, and a review flag."""

    query: str
    trace_id: str
    results: list[Dict[str, Any]]
    best: Optional[LinkCandidate]
    needs_review: bool


def _extract_candidate(payload: Dict[str, Any]) -> LinkCandidate | None:
    candidate_id = payload.get("id") or payload.get("ce_id") or payload.get("comp_id")
    if candidate_id is None:
        return None
    candidate_id = str(candidate_id)
    pn = str(payload.get("pn") or payload.get("part_number") or "").strip()
    match_kind = str(payload.get("match_kind") or "").strip()
    reason = str(payload.get("reason") or "").strip()

    analysis = payload.get("analysis")
    normalized_input: Optional[str] = None
    normalized_targets: list[str] = []
    if isinstance(analysis, dict):
        norm_input = analysis.get("normalized_input")
        if isinstance(norm_input, str):
            normalized_input = norm_input.strip() or None
        normalized_targets = _normalize_targets(analysis.get("normalized_targets"))

    if not normalized_input:
        norm_input = payload.get("normalized_input")
        if isinstance(norm_input, str):
            normalized_input = norm_input.strip() or None

    if not normalized_targets:
        normalized_targets = _normalize_targets(payload.get("normalized_targets"))

    aliases = _clean_aliases(payload.get("aliases"))

    return LinkCandidate(
        id=candidate_id,
        pn=pn,
        aliases=aliases,
        match_kind=match_kind,
        reason=reason,
        normalized_input=normalized_input,
        normalized_targets=normalized_targets,
        raw=dict(payload),
    )


def _rank_candidates(
    items: Iterable[LinkCandidate],
) -> Tuple[Optional[LinkCandidate], bool]:
    best: Optional[LinkCandidate] = None
    needs_review = False
    best_rank: int | None = None
    for item in items:
        rank = _RANK_ORDER.get(item.match_kind, len(_RANK_ORDER))
        if best is None or rank < (
            best_rank if best_rank is not None else len(_RANK_ORDER)
        ):
            best = item
            best_rank = rank
            needs_review = False
        elif best_rank is not None and rank == best_rank:
            needs_review = True
    return best, needs_review


def select_best_match(
    pn: str, *, limit: int = 50, timeout: float | None = None
) -> LinkerDecision:
    query = _validate_input(pn)
    trace_id = uuid.uuid4().hex
    logger.info("CE Bridge search query=%s trace=%s", query, trace_id)

    state = _probe_state(trace_id, timeout or 0.0)
    _ = state  # keep reference for potential future use

    params = {"pn": query, "limit": limit, "analyze": "true"}
    payload = _request_json(
        "/complexes/search", params=params, trace_id=trace_id, timeout=timeout or 0.0
    )

    if not isinstance(payload, list):
        raise LinkerError("Bridge search returned unexpected payload")

    raw_results: list[Dict[str, Any]] = [
        row for row in payload if isinstance(row, dict)
    ]
    candidates: list[LinkCandidate] = []
    for row in raw_results:
        candidate = _extract_candidate(row)
        if candidate is not None:
            candidates.append(candidate)

    best, needs_review = _rank_candidates(candidates)
    if best is None:
        needs_review = False

    return LinkerDecision(
        query=query,
        trace_id=trace_id,
        results=raw_results,
        best=best,
        needs_review=needs_review,
    )


# Back-compat alias (to be removed after deprecation window)
search_best_match = select_best_match  # kept alias for back-compat


def fetch_normalization_info(timeout: float | None = None) -> Dict[str, Any]:
    trace_id = uuid.uuid4().hex
    logger.info("CE Bridge normalization diagnostics trace=%s", trace_id)

    state = _probe_state(trace_id, timeout or 0.0)

    payload = _request_json(
        "/admin/pn_normalization",
        trace_id=trace_id,
        timeout=timeout or 0.0,
    )

    result: Dict[str, Any] = {}
    if isinstance(payload, dict):
        result = payload
    else:
        result = {}

    result.setdefault("trace_id", trace_id)

    features = state.get("features") if isinstance(state, dict) else {}
    if isinstance(result, dict):
        result.setdefault("rules_version", features.get("normalization_rules_version"))

    return result

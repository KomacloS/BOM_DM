from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.integration import ce_bridge_client
from app.integration.ce_bridge_client import CEAuthError, CENetworkError

logger = logging.getLogger(__name__)

_RANK_ORDER: Dict[str, int] = {
    "exact_pn": 0,
    "exact_alias": 1,
    "normalized_pn": 2,
    "normalized_alias": 3,
    "normalized": 3,
    "partial": 4,
    "fuzzy": 5,
}


class LinkerError(Exception):
    """Base class for Complex Editor linker failures."""


class LinkerInputError(LinkerError):
    """Raised when the linker input is invalid (e.g., blank PN)."""


class LinkerFeatureError(LinkerError):
    """Raised when the Complex Editor bridge lacks linker capabilities."""


@dataclass
class LinkCandidate:
    id: str
    pn: str
    aliases: List[str] = field(default_factory=list)
    db_path: Optional[str] = None
    match_kind: str = "unknown"
    reason: str = ""
    normalized_input: Optional[str] = None
    normalized_targets: List[str] = field(default_factory=list)
    analysis: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    rank: int = 99

    def to_dict(self) -> Dict[str, Any]:
        data = dict(self.raw)
        data.update(
            {
                "id": self.id,
                "ce_id": self.id,
                "pn": self.pn,
                "aliases": list(self.aliases),
                "db_path": self.db_path,
                "match_kind": self.match_kind,
                "reason": self.reason,
                "normalized_input": self.normalized_input,
                "normalized_targets": list(self.normalized_targets),
                "analysis": dict(self.analysis),
            }
        )
        return data


@dataclass
class LinkerDecision:
    query: str
    trace_id: str
    best: Optional[LinkCandidate]
    results: List[LinkCandidate]
    needs_review: bool
    normalized_input: Optional[str] = None
    analysis: Dict[str, Any] = field(default_factory=dict)

    @property
    def results_data(self) -> List[Dict[str, Any]]:
        return [candidate.to_dict() for candidate in self.results]


def select_best_match(
    pn: str,
    *,
    limit: int = 20,
    trace_id: Optional[str] = None,
) -> LinkerDecision:
    query = (pn or "").strip()
    if not query:
        raise LinkerInputError("Part number is required.")

    try:
        limit_value = int(limit)
    except (TypeError, ValueError):
        limit_value = 20
    limit_value = max(1, min(limit_value, 200))

    config = _load_config()
    active_trace = _normalize_trace_id(trace_id)
    state = _probe_state(config, active_trace)

    payload = _request_json(
        config,
        "GET",
        "/complexes/search",
        params={"pn": query, "limit": limit_value},
        trace_id=active_trace,
    )

    analysis = {}
    if isinstance(payload, dict):
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}

    raw_candidates = _extract_candidates(payload)
    candidates = _rank_candidates(query, raw_candidates)
    best = candidates[0] if candidates else None

    if best and isinstance(analysis, dict):
        normalized_targets = analysis.get("normalized_targets")
        if best.normalized_targets == [] and isinstance(normalized_targets, Sequence):
            best.normalized_targets = [str(item) for item in normalized_targets if isinstance(item, str)]

    needs_review = _determine_needs_review(candidates)
    normalized_input = None
    if isinstance(analysis, dict):
        normalized_input = analysis.get("normalized_input")
    if not normalized_input and best:
        normalized_input = best.normalized_input

    return LinkerDecision(
        query=query,
        trace_id=active_trace,
        best=best,
        results=candidates,
        needs_review=needs_review,
        normalized_input=normalized_input if isinstance(normalized_input, str) else None,
        analysis=analysis if isinstance(analysis, dict) else {},
    )


def fetch_normalization_info(*, trace_id: Optional[str] = None) -> Dict[str, Any]:
    config = _load_config()
    active_trace = _normalize_trace_id(trace_id)
    payload = _request_json(config, "GET", "/complexes/normalization", trace_id=active_trace)
    if not isinstance(payload, dict):
        raise LinkerError("Unexpected payload from normalization endpoint.")
    payload.setdefault("trace_id", active_trace)
    return payload


def _load_config():
    return ce_bridge_client._load_bridge_config()


def _normalize_trace_id(trace_id: Optional[str]) -> str:
    return ce_bridge_client._normalize_trace_id(trace_id)


def _probe_state(config, trace_id: str) -> Dict[str, Any]:
    try:
        payload = _request_json(config, "GET", "/state", trace_id=trace_id)
    except LinkerFeatureError:
        raise
    except LinkerError as exc:
        logger.debug("Linker state probe failed: %s", exc)
        return {}

    if not isinstance(payload, dict):
        return {}

    features = payload.get("features")
    if isinstance(features, dict):
        linker = features.get("linker")
        if isinstance(linker, dict) and not linker.get("enabled", True):
            raise LinkerFeatureError("Complex Editor linker is disabled on this bridge.")
    return payload


def _request_json(
    config,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Any:
    try:
        response, state = ce_bridge_client._perform_request(
            config,
            method,
            path,
            params=params,
            json_body=json_body,
            trace_id=trace_id,
        )
    except (CENetworkError, CEAuthError, ce_bridge_client.CENotFound) as exc:
        raise LinkerError(str(exc)) from exc

    try:
        payload = ce_bridge_client._json_from_response(response)
    except CENetworkError as exc:
        raise LinkerError(str(exc)) from exc

    status = response.status_code
    if status >= 500:
        try:
            ce_bridge_client._raise_server_error(config, response, state)
        except CENetworkError as exc:
            raise LinkerError(str(exc)) from exc
        raise LinkerError(f"Complex Editor bridge error (HTTP {status})")
    if status == 400:
        raise LinkerInputError(_extract_reason(payload) or "Invalid search request.")
    if status in (409, 412, 501, 503):
        raise LinkerFeatureError(
            _extract_reason(payload) or "Complex Editor linker feature unavailable."
        )
    if status >= 401:
        raise LinkerError(_extract_reason(payload) or f"Complex Editor bridge returned HTTP {status}")

    return payload


def _extract_candidates(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("results", "items", "matches"):
            candidates = payload.get(key)
            if isinstance(candidates, list):
                return [item for item in candidates if isinstance(item, dict)]
    if payload not in (None, {}):
        logger.debug("Unexpected linker search payload shape: %s", type(payload).__name__)
    return []


def _rank_candidates(query: str, raw_candidates: Iterable[Dict[str, Any]]) -> List[LinkCandidate]:
    normalized_query = _normalize_text(query)
    ranked: List[LinkCandidate] = []

    for raw in raw_candidates:
        candidate_id = raw.get("id") or raw.get("ce_id")
        if candidate_id is None:
            continue
        pn_text = str(raw.get("pn") or raw.get("part_number") or "").strip()
        aliases = _coerce_str_list(raw.get("aliases"))
        match_kind = str(raw.get("match_kind") or raw.get("matchKind") or "").strip().lower()
        reason = str(raw.get("reason") or raw.get("match_reason") or "").strip()
        analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
        normalized_input = analysis.get("normalized_input") or raw.get("normalized_input")
        normalized_targets = analysis.get("normalized_targets") or raw.get("normalized_targets") or []
        db_path = raw.get("db_path") or raw.get("ce_db_uri")

        inferred_match = _infer_match_kind(normalized_query, pn_text, aliases)
        if not match_kind:
            match_kind = inferred_match
        elif match_kind not in _RANK_ORDER and inferred_match in _RANK_ORDER:
            match_kind = inferred_match

        candidate = LinkCandidate(
            id=str(candidate_id),
            pn=pn_text,
            aliases=aliases,
            db_path=str(db_path).strip() if isinstance(db_path, str) else None,
            match_kind=match_kind or "unknown",
            reason=reason,
            normalized_input=str(normalized_input).strip() if isinstance(normalized_input, str) else None,
            normalized_targets=_coerce_str_list(normalized_targets),
            analysis=analysis,
            raw=raw,
            rank=_RANK_ORDER.get(match_kind or "unknown", 99),
        )
        ranked.append(candidate)

    ranked.sort(key=lambda item: (item.rank, _normalize_text(item.pn), item.id))
    return ranked


def _infer_match_kind(query: str, pn: str, aliases: Sequence[str]) -> str:
    pn_normalized = _normalize_text(pn)
    alias_norm = {_normalize_text(alias) for alias in aliases if isinstance(alias, str)}

    if pn_normalized and pn_normalized == query:
        return "exact_pn"
    if query and query in alias_norm:
        return "exact_alias"
    if pn_normalized and pn_normalized == _normalize_text(_strip_separators(query)):
        return "normalized_pn"
    return "unknown"


def _strip_separators(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum())


def _determine_needs_review(candidates: Sequence[LinkCandidate]) -> bool:
    if not candidates:
        return False
    top = candidates[0]
    same_rank = [cand for cand in candidates if cand.rank == top.rank]
    if len(same_rank) > 1:
        return True
    flags = top.raw.get("needs_review") or top.raw.get("ambiguous")
    return bool(flags)


def _extract_reason(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        for key in ("detail", "reason", "message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        errors = payload.get("errors")
        if isinstance(errors, Sequence):
            parts = [str(item) for item in errors if isinstance(item, (str, int, float))]
            if parts:
                return ", ".join(parts)
    return None


def _normalize_text(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _coerce_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, Sequence):
        items: List[str] = []
        for item in raw:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    items.append(text)
        return items
    return []

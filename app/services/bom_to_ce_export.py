from __future__ import annotations

import csv
import logging
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urljoin

from requests import exceptions as req_exc
from sqlmodel import Session, select

from app.domain.complex_linker import ComplexLink
from app.integration import ce_bridge_client, ce_bridge_transport
from app.models import BOMItem, Part

logger = logging.getLogger(__name__)

STATUS_SUCCESS = "SUCCESS"
STATUS_PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
STATUS_FAILED_INPUT = "FAILED_INPUT"
STATUS_FAILED_BACKEND = "FAILED_BACKEND"
STATUS_RETRY_LATER = "RETRY_LATER"
STATUS_RETRY_WITH_BACKOFF = "RETRY_WITH_BACKOFF"

CSV_HEADERS = [
    "bom_id",
    "line_id",
    "part_number",
    "comp_id",
    "test_method",
    "status",
    "reason",
]


def _sanitize_mdb_name(name: str) -> str:
    sanitized = (name or "bom_complexes.mdb").strip()
    if not sanitized.lower().endswith(".mdb"):
        sanitized = f"{sanitized}.mdb"
    if len(sanitized) > 64:
        sanitized = sanitized[-64:]
    sanitized = sanitized.replace("\\", "_").replace("/", "_")
    return sanitized


def _normalize_ce_map(raw: Optional[Mapping[str, Any]]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    if not raw:
        return normalized
    for pn, comp in raw.items():
        if not isinstance(pn, str):
            continue
        comp_id = ce_bridge_client.coerce_comp_id(comp)
        if comp_id is None:
            continue
        normalized[pn.strip().lower()] = comp_id
    return normalized


def _load_bom_context(
    session: Session,
    bom_id: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[int, int]]:
    stmt = (
        select(BOMItem, Part)
        .join(Part, Part.id == BOMItem.part_id, isouter=True)
        .where(BOMItem.assembly_id == bom_id)
        .order_by(BOMItem.id)
    )
    reference_map: dict[str, list[dict[str, Any]]] = {}
    part_ids: set[int] = set()
    for bom_item, part in session.exec(stmt):
        ref = (bom_item.reference or "").strip()
        entry = {
            "line_id": bom_item.id,
            "part_id": bom_item.part_id or (part.id if part else None),
            "part_number": (part.part_number if part else "") or "",
            "is_fitted": bool(getattr(bom_item, "is_fitted", True)),
        }
        if entry["part_id"] is not None:
            part_ids.add(int(entry["part_id"]))
        reference_map.setdefault(ref, []).append(entry)
    links_by_part: dict[int, int] = {}
    if part_ids:
        link_stmt = select(ComplexLink).where(ComplexLink.part_id.in_(part_ids))
        for link in session.exec(link_stmt).all():
            comp_id = ce_bridge_client.coerce_comp_id(link.ce_complex_id)
            if comp_id is not None and link.part_id is not None:
                links_by_part[int(link.part_id)] = comp_id
    return reference_map, links_by_part


def _coerce_id_list(raw: Any) -> list[int]:
    if raw is None:
        return []
    iterable: Sequence[Any]
    if isinstance(raw, (list, tuple, set)):
        iterable = raw
    else:
        iterable = [raw]
    ids: list[int] = []
    for item in iterable:
        comp_id = ce_bridge_client.coerce_comp_id(item)
        if comp_id is None:
            continue
        if comp_id not in ids:
            ids.append(comp_id)
    return ids


def _append_report(
    report_rows: list[dict[str, Any]],
    *,
    bom_id: int,
    line_id: Optional[int],
    part_number: str,
    comp_id: Optional[int],
    test_method: str,
    status: str,
    reason: str,
) -> None:
    report_rows.append(
        {
            "bom_id": str(bom_id),
            "line_id": "" if line_id is None else str(line_id),
            "part_number": part_number or "",
            "comp_id": "" if comp_id is None else str(comp_id),
            "test_method": test_method or "",
            "status": status,
            "reason": reason,
        }
    )


def _append_report_from_entry(
    report_rows: list[dict[str, Any]],
    entry: dict[str, Any],
    *,
    status: str,
    reason: str,
) -> None:
    _append_report(
        report_rows,
        bom_id=int(entry.get("bom_id", 0)),
        line_id=entry.get("line_id"),
        part_number=str(entry.get("part_number") or ""),
        comp_id=entry.get("comp_id"),
        test_method=str(entry.get("test_method") or ""),
        status=status,
        reason=reason,
    )


def _write_report(target_dir: Path, rows: Sequence[dict[str, Any]]) -> Optional[str]:
    if not rows:
        return None
    report_path = target_dir / "ce_export_report.csv"
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "bom_id": row.get("bom_id", ""),
                    "line_id": row.get("line_id", ""),
                    "part_number": row.get("part_number", ""),
                    "comp_id": row.get("comp_id", ""),
                    "test_method": row.get("test_method", ""),
                    "status": row.get("status", ""),
                    "reason": row.get("reason", ""),
                }
            )
    return report_path.as_posix()


def export_bom_to_ce_bridge(
    session: Session,
    bom_id: int,
    *,
    bom_rows: Sequence[Mapping[str, Any]],
    export_dir: Path | str,
    mdb_name: str,
    ce_component_map: Optional[Mapping[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> dict[str, Any]:
    trace = trace_id or str(uuid.uuid4())
    export_dir_path = Path(export_dir).expanduser().resolve()
    export_dir_path.mkdir(parents=True, exist_ok=True)
    sanitized_mdb = _sanitize_mdb_name(mdb_name)

    try:
        base_url, token, timeout = ce_bridge_client.resolve_bridge_connection()
    except Exception as exc:  # pragma: no cover - configuration errors should be rare
        logger.warning("Failed to resolve CE bridge configuration: %s", exc)
        return {
            "status": STATUS_FAILED_INPUT,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": 0,
            "report_path": None,
            "detail": str(exc),
        }

    http_session = ce_bridge_transport.get_session(base_url)
    health_url = urljoin(base_url.rstrip("/") + "/", "admin/health")
    health_headers = ce_bridge_transport.build_headers(token, trace)
    try:
        health_response = http_session.get(health_url, headers=health_headers, timeout=timeout)
    except (req_exc.Timeout, req_exc.ConnectTimeout, req_exc.ConnectionError) as exc:
        logger.warning("CE bridge health check failed: %s", exc)
        return {
            "status": STATUS_RETRY_WITH_BACKOFF,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": 0,
            "report_path": None,
            "detail": "Complex Editor bridge unreachable.",
        }
    except req_exc.RequestException as exc:
        logger.warning("CE bridge health check error: %s", exc)
        return {
            "status": STATUS_RETRY_WITH_BACKOFF,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": 0,
            "report_path": None,
            "detail": "Complex Editor bridge unreachable.",
        }

    status_code = health_response.status_code
    if status_code in (401, 403):
        return {
            "status": STATUS_FAILED_INPUT,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": 0,
            "report_path": None,
            "detail": "Invalid or expired Complex Editor bridge token.",
        }
    if status_code >= 500:
        return {
            "status": STATUS_RETRY_WITH_BACKOFF,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": 0,
            "report_path": None,
            "detail": f"Bridge health check returned HTTP {status_code}.",
        }
    if status_code >= 400:
        return {
            "status": STATUS_FAILED_INPUT,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": 0,
            "report_path": None,
            "detail": f"Bridge health check returned HTTP {status_code}.",
        }

    try:
        health_payload = health_response.json()
    except ValueError:
        health_payload = {}
    if not isinstance(health_payload, dict):
        health_payload = {}

    if not bool(health_payload.get("ready", False)):
        detail = health_payload.get("last_ready_error") or health_payload.get("detail")
        detail_text = str(detail or "Complex Editor bridge not ready.")
        return {
            "status": STATUS_RETRY_LATER,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": 0,
            "report_path": None,
            "detail": detail_text,
        }
    if bool(health_payload.get("headless")) and not bool(health_payload.get("allow_headless")):
        return {
            "status": STATUS_RETRY_LATER,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": 0,
            "report_path": None,
            "detail": "exports disabled in headless mode",
        }

    reference_map, links_by_part = _load_bom_context(session, bom_id)
    ce_map = _normalize_ce_map(ce_component_map)

    report_rows: list[dict[str, Any]] = []
    rows_by_comp_id: dict[int, list[dict[str, Any]]] = {}
    resolved_ids: list[int] = []
    exported_count = 0
    export_path: Optional[str] = None

    bom_rows_seq = list(bom_rows or [])
    complex_rows = 0
    for row in bom_rows_seq:
        if not isinstance(row, Mapping):
            continue
        test_method_raw = row.get("test_method")
        test_method = str(test_method_raw or "").strip()
        if test_method.lower() != "complex":
            continue
        reference = str(row.get("reference") or "").strip()
        entry_list = reference_map.get(reference, [])
        entry = entry_list.pop(0) if entry_list else None
        line_id = entry.get("line_id") if entry else None
        part_id = entry.get("part_id") if entry else None
        part_number = str(row.get("part_number") or "").strip()
        if not part_number and entry:
            part_number = str(entry.get("part_number") or "").strip()
        if entry and entry.get("is_fitted") is not None:
            is_fitted = bool(entry.get("is_fitted"))
        else:
            is_fitted = bool(row.get("is_fitted", True))
        if not is_fitted:
            continue
        complex_rows += 1
        if line_id is None:
            fallback_line = row.get("line_id") or row.get("bom_item_id") or row.get("bom_line_id")
            try:
                line_id = int(fallback_line)
            except (TypeError, ValueError):
                line_id = None
        comp_id = None
        if part_id is not None:
            comp_id = links_by_part.get(int(part_id))
        if comp_id is None and part_number:
            comp_id = ce_map.get(part_number.strip().lower())
        row_entry = {
            "bom_id": bom_id,
            "line_id": line_id,
            "part_number": part_number,
            "comp_id": comp_id,
            "test_method": test_method or "Complex",
        }
        if comp_id is None:
            _append_report(
                report_rows,
                bom_id=bom_id,
                line_id=line_id,
                part_number=part_number,
                comp_id=None,
                test_method=row_entry["test_method"],
                status="skipped",
                reason="not_linked_in_CE",
            )
            continue
        rows_by_comp_id.setdefault(comp_id, []).append(row_entry)
        if comp_id not in resolved_ids:
            resolved_ids.append(comp_id)

    if complex_rows == 0:
        report_path = _write_report(export_dir_path, report_rows) if report_rows else None
        detail = "No Complex test method rows found in BOM."
        status = STATUS_SUCCESS if not report_rows else STATUS_PARTIAL_SUCCESS
        return {
            "status": status,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": len(report_rows),
            "report_path": report_path,
            "detail": detail,
        }

    if not resolved_ids:
        report_path = _write_report(export_dir_path, report_rows) if report_rows else None
        detail = "No Complex Editor IDs resolved."
        status = STATUS_PARTIAL_SUCCESS if report_rows else STATUS_SUCCESS
        return {
            "status": status,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": len(report_rows),
            "report_path": report_path,
            "detail": detail,
        }

    payload: dict[str, Any]
    export_url = urljoin(base_url.rstrip("/") + "/", "exports/mdb")
    export_headers = ce_bridge_transport.build_headers(
        token,
        trace,
        content_type="application/json",
    )
    try:
        response = http_session.post(
            export_url,
            headers=export_headers,
            json={
                "comp_ids": resolved_ids,
                "out_dir": export_dir_path.as_posix(),
                "mdb_name": sanitized_mdb,
            },
            timeout=max(timeout, 30.0),
        )
    except (req_exc.Timeout, req_exc.ConnectTimeout, req_exc.ConnectionError) as exc:
        logger.warning("CE bridge export call failed: %s", exc)
        return {
            "status": STATUS_RETRY_WITH_BACKOFF,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": len(report_rows),
            "report_path": _write_report(export_dir_path, report_rows) if report_rows else None,
            "detail": "Complex Editor bridge unreachable during export.",
        }
    except req_exc.RequestException as exc:
        logger.warning("CE bridge export call error: %s", exc)
        return {
            "status": STATUS_RETRY_WITH_BACKOFF,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": len(report_rows),
            "report_path": _write_report(export_dir_path, report_rows) if report_rows else None,
            "detail": "Complex Editor bridge unreachable during export.",
        }

    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload_trace = payload.get("trace_id")
    if isinstance(payload_trace, str) and payload_trace.strip():
        trace = payload_trace.strip()

    reason = str(payload.get("reason") or "").lower()
    detail_text = str(payload.get("detail") or "").strip()

    if 200 <= response.status_code < 300:
        exported_ids = _coerce_id_list(payload.get("exported_comp_ids"))
        exported_count = len(exported_ids)

        missing_ids = _coerce_id_list(payload.get("missing"))
        for comp_id in missing_ids:
            entries = rows_by_comp_id.get(comp_id)
            if entries:
                for entry in entries:
                    _append_report_from_entry(
                        report_rows,
                        entry,
                        status="missing",
                        reason="not_found_in_CE",
                    )
            else:
                _append_report(
                    report_rows,
                    bom_id=bom_id,
                    line_id=None,
                    part_number="",
                    comp_id=comp_id,
                    test_method="Complex",
                    status="missing",
                    reason="not_found_in_CE",
                )

        unlinked_ids = _coerce_id_list(payload.get("unlinked"))
        for comp_id in unlinked_ids:
            entries = rows_by_comp_id.get(comp_id)
            if entries:
                for entry in entries:
                    _append_report_from_entry(
                        report_rows,
                        entry,
                        status="unlinked",
                        reason="unlinked_data_in_CE",
                    )
            else:
                _append_report(
                    report_rows,
                    bom_id=bom_id,
                    line_id=None,
                    part_number="",
                    comp_id=comp_id,
                    test_method="Complex",
                    status="unlinked",
                    reason="unlinked_data_in_CE",
                )

        missing_count = len(report_rows)
        raw_export_path = payload.get("export_path")
        if isinstance(raw_export_path, str) and raw_export_path.strip():
            export_path = raw_export_path.strip()
        else:
            export_path = (export_dir_path / sanitized_mdb).as_posix()
        if not detail_text and missing_count:
            detail_text = f"{missing_count} row(s) require attention."
        report_path = _write_report(export_dir_path, report_rows) if report_rows else None
        status = STATUS_SUCCESS if not report_rows else STATUS_PARTIAL_SUCCESS
        return {
            "status": status,
            "trace_id": trace,
            "export_path": export_path,
            "exported_count": exported_count,
            "missing_count": missing_count,
            "report_path": report_path,
            "detail": detail_text,
        }

    if response.status_code == 404 and reason == "comp_ids_not_found":
        missing_ids = _coerce_id_list(payload.get("missing")) or resolved_ids
        for comp_id in missing_ids:
            entries = rows_by_comp_id.get(comp_id)
            if entries:
                for entry in entries:
                    _append_report_from_entry(
                        report_rows,
                        entry,
                        status="missing",
                        reason="not_found_in_CE",
                    )
            else:
                _append_report(
                    report_rows,
                    bom_id=bom_id,
                    line_id=None,
                    part_number="",
                    comp_id=comp_id,
                    test_method="Complex",
                    status="missing",
                    reason="not_found_in_CE",
                )
        report_path = _write_report(export_dir_path, report_rows) if report_rows else None
        detail = detail_text or "Complex Editor components not found."
        return {
            "status": STATUS_FAILED_INPUT,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": len(report_rows),
            "report_path": report_path,
            "detail": detail,
        }

    if response.status_code == 409:
        report_path = _write_report(export_dir_path, report_rows) if report_rows else None
        detail = detail_text or "Complex Editor export conflict."
        status = STATUS_FAILED_BACKEND
        if reason == "empty_selection":
            status = STATUS_FAILED_INPUT
        elif reason == "outdir_unwritable":
            status = STATUS_FAILED_INPUT
        elif reason == "template_missing_or_incompatible":
            template_path = payload.get("template_path")
            if isinstance(template_path, str) and template_path.strip():
                detail = f"{detail} (template: {template_path.strip()})"
        return {
            "status": status,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": len(report_rows),
            "report_path": report_path,
            "detail": detail,
        }

    if response.status_code == 503:
        detail = detail_text or "Complex Editor bridge unavailable."
        if reason == "bridge_headless":
            detail = "exports disabled in headless mode"
            status = STATUS_RETRY_LATER
        else:
            status = STATUS_FAILED_BACKEND
        return {
            "status": status,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": len(report_rows),
            "report_path": _write_report(export_dir_path, report_rows) if report_rows else None,
            "detail": detail,
        }

    if response.status_code >= 500:
        detail = detail_text or f"Complex Editor bridge error (HTTP {response.status_code})."
        return {
            "status": STATUS_FAILED_BACKEND,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": len(report_rows),
            "report_path": _write_report(export_dir_path, report_rows) if report_rows else None,
            "detail": detail,
        }

    if response.status_code >= 400:
        detail = detail_text or f"Complex Editor request failed (HTTP {response.status_code})."
        return {
            "status": STATUS_FAILED_INPUT,
            "trace_id": trace,
            "export_path": None,
            "exported_count": 0,
            "missing_count": len(report_rows),
            "report_path": _write_report(export_dir_path, report_rows) if report_rows else None,
            "detail": detail,
        }

    detail = detail_text or f"Unexpected response from Complex Editor bridge (HTTP {response.status_code})."
    return {
        "status": STATUS_FAILED_BACKEND,
        "trace_id": trace,
        "export_path": None,
        "exported_count": 0,
        "missing_count": len(report_rows),
        "report_path": _write_report(export_dir_path, report_rows) if report_rows else None,
        "detail": detail,
    }

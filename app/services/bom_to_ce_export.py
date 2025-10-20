"""Helpers to run the BOM ➜ Complex Editor export workflow via the bridge API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

import csv
import logging

from requests import exceptions as req_exc
from sqlalchemy import MetaData, Table, inspect as sa_inspect, select as sa_select
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from ..domain.complex_linker import ComplexLink
from ..integration import ce_bridge_client, ce_bridge_transport
from ..models import Assembly, BOMItem, Part, PartTestAssignment, TestMethod

logger = logging.getLogger(__name__)


@dataclass
class _CandidateRow:
    """Representation of a BOM row that might be exported."""

    bom_id: int
    line_id: int
    part_number: Optional[str]
    comp_id: Optional[int]
    test_method: str


@dataclass
class _ReportRow:
    bom_id: int
    line_id: Optional[int]
    part_number: Optional[str]
    comp_id: Optional[int]
    test_method: str
    status: str
    reason: str


def _coerce_comp_id(value: object) -> Optional[int]:
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


def _candidate_from_row(
    bom: BOMItem,
    part: Optional[Part],
    assignment: Optional[PartTestAssignment],
    link: Optional[ComplexLink],
) -> Optional[_CandidateRow]:
    method: Optional[str]
    if assignment and assignment.method:
        method = (
            assignment.method.value
            if isinstance(assignment.method, TestMethod)
            else str(assignment.method)
        )
    else:
        method = getattr(bom, "test_method", None)
        method = str(method) if method is not None else None

    if (method or "").lower() != TestMethod.complex.value:
        return None
    if not getattr(bom, "is_fitted", True):
        return None

    part_number: Optional[str] = None
    if part and getattr(part, "part_number", None):
        part_number = part.part_number
    else:
        part_number = getattr(bom, "part_number", None) or getattr(bom, "alt_part_number", None)

    raw_comp_id = getattr(bom, "comp_id", None)
    if raw_comp_id is None and link is not None:
        raw_comp_id = getattr(link, "ce_complex_id", None)

    comp_id = _coerce_comp_id(raw_comp_id)

    return _CandidateRow(
        bom_id=int(bom.assembly_id),
        line_id=int(bom.id),
        part_number=part_number,
        comp_id=comp_id,
        test_method=TestMethod.complex.value,
    )


def _collect_candidates(
    session: Session, assembly_id: int
) -> Tuple[List[_CandidateRow], List[_CandidateRow]]:
    stmt = (
        select(BOMItem, Part, PartTestAssignment, ComplexLink)
        .join(Part, Part.id == BOMItem.part_id, isouter=True)
        .join(PartTestAssignment, PartTestAssignment.part_id == Part.id, isouter=True)
        .join(ComplexLink, ComplexLink.part_id == Part.id, isouter=True)
        .where(BOMItem.assembly_id == assembly_id)
    )
    linked: List[_CandidateRow] = []
    pending: List[_CandidateRow] = []
    for bom, part, assignment, link in session.exec(stmt):
        candidate = _candidate_from_row(bom, part, assignment, link)
        if candidate is None:
            continue
        if candidate.comp_id is None:
            pending.append(candidate)
        else:
            linked.append(candidate)
    return linked, pending


def _maybe_resolve_component_map(
    session: Session,
    pending: List[_CandidateRow],
) -> List[_CandidateRow]:
    if not pending:
        return pending
    try:
        engine = session.get_bind()
    except Exception:  # pragma: no cover - defensive
        return pending
    if engine is None:
        return pending
    inspector = sa_inspect(engine)
    if not inspector.has_table("ce_component_map"):
        return pending
    metadata = MetaData()
    try:
        table = Table("ce_component_map", metadata, autoload_with=engine)
    except SQLAlchemyError:
        return pending

    def _column(name: str):
        try:
            return table.c[name]
        except KeyError:
            return None

    pn_col = _column("pn")
    comp_col = _column("comp_id")
    if pn_col is None or comp_col is None:  # pragma: no cover - defensive
        return pending
    pn_values = sorted({row.part_number for row in pending if row.part_number})
    if not pn_values:
        return pending
    try:
        results = session.exec(
            sa_select(pn_col, comp_col).where(pn_col.in_(pn_values))
        ).all()
    except SQLAlchemyError:  # pragma: no cover - defensive
        return pending
    mapping: Dict[str, int] = {}
    for pn_value, comp_value in results:
        pn_text = str(pn_value).strip() if pn_value is not None else ""
        comp_id = _coerce_comp_id(comp_value)
        if pn_text and comp_id:
            mapping[pn_text] = comp_id
    resolved: List[_CandidateRow] = []
    still_pending: List[_CandidateRow] = []
    for row in pending:
        if row.part_number and row.part_number in mapping:
            row.comp_id = mapping[row.part_number]
            resolved.append(row)
        else:
            still_pending.append(row)
    return resolved + still_pending


def _generate_job_names(trace_id: str, timestamp: Optional[datetime] = None) -> Tuple[str, str]:
    ts = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S")
    short_trace = trace_id.replace("-", "")[:4].lower()
    base = f"bom_{ts}_{short_trace}"
    return f"{base}.mdb", f"{base}_missing.csv"


def _build_headers(token: str, trace_id: str) -> Dict[str, str]:
    headers = ce_bridge_transport.build_headers(token)
    headers["X-Trace-Id"] = trace_id
    headers.setdefault("Content-Type", "application/json")
    return headers


def _write_report(path: Path, rows: Sequence[_ReportRow]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "bom_id",
                "line_id",
                "part_number",
                "comp_id",
                "test_method",
                "status",
                "reason",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "bom_id": row.bom_id,
                    "line_id": row.line_id or "",
                    "part_number": row.part_number or "",
                    "comp_id": row.comp_id or "",
                    "test_method": row.test_method,
                    "status": row.status,
                    "reason": row.reason,
                }
            )


def _summary_template(trace_id: str, export_path: Path) -> Dict[str, object]:
    return {
        "status": "FAILED_BACKEND",
        "trace_id": trace_id,
        "export_path": str(export_path),
        "exported_count": 0,
        "missing_count": 0,
        "report_path": "",
        "detail": "",
    }


def export_bom_to_ce_bridge(
    session: Session,
    assembly_id: int,
    *,
    out_dir: Path | str,
    allow_headless: bool = False,
    timestamp: Optional[datetime] = None,
) -> Dict[str, object]:
    """Export Complex Editor data for the BOM identified by ``assembly_id``."""

    assembly = session.get(Assembly, assembly_id)
    if assembly is None:
        raise ValueError(f"Assembly {assembly_id} not found")

    trace_id = str(uuid4())
    out_dir_path = Path(out_dir).expanduser()
    mdb_name, report_name = _generate_job_names(trace_id, timestamp=timestamp)
    mdb_path = (out_dir_path / mdb_name).resolve()
    report_path = (out_dir_path / report_name).resolve()

    summary = _summary_template(trace_id, mdb_path)

    linked, pending = _collect_candidates(session, assembly_id)
    pending = _maybe_resolve_component_map(session, pending)

    comp_map: Dict[int, List[_CandidateRow]] = {}
    for row in linked:
        if row.comp_id is None:
            continue
        comp_map.setdefault(row.comp_id, []).append(row)
    cleaned_pending: List[_CandidateRow] = []
    for row in pending:
        if row.comp_id is None:
            cleaned_pending.append(row)
        else:
            linked.append(row)
            comp_map.setdefault(row.comp_id, []).append(row)

    comp_ids: List[int] = sorted({int(row.comp_id) for row in linked if row.comp_id is not None})

    logger.info(
        "BOM→CE export trace_id=%s assembly=%s candidates=%d out_dir=%s",
        trace_id,
        assembly_id,
        len(comp_ids),
        str(out_dir_path),
    )

    report_rows: List[_ReportRow] = []
    for row in cleaned_pending:
        report_rows.append(
            _ReportRow(
                bom_id=row.bom_id,
                line_id=row.line_id,
                part_number=row.part_number,
                comp_id=None,
                test_method=row.test_method,
                status="SKIPPED",
                reason="not_linked_in_CE",
            )
        )

    if not comp_ids:
        if report_rows:
            _write_report(report_path, report_rows)
            summary["report_path"] = str(report_path)
            summary["missing_count"] = len(report_rows)
        summary["status"] = "FAILED_INPUT"
        summary["detail"] = "No Complex-linked components ready for export"
        return summary

    base_url, token, timeout = ce_bridge_client.resolve_bridge_connection()
    session_http = ce_bridge_transport.get_session()
    headers = _build_headers(token, trace_id)
    health_url = f"{base_url.rstrip('/')}/admin/health"

    try:
        health_resp = session_http.get(health_url, headers=headers, timeout=timeout)
    except req_exc.RequestException as exc:
        logger.info("CE health check failed: %s", exc)
        summary["status"] = "RETRY_WITH_BACKOFF"
        summary["detail"] = "Network error contacting Complex Editor bridge"
        return summary

    try:
        health_payload = health_resp.json()
    except ValueError:
        health_payload = {}
    if health_resp.status_code >= 500:
        summary["status"] = "RETRY_WITH_BACKOFF"
        summary["detail"] = "Complex Editor bridge unavailable"
        return summary
    if health_resp.status_code >= 400:
        detail = str(health_payload.get("detail") or "").strip()
        summary["status"] = "FAILED_BACKEND"
        summary["detail"] = detail or f"Health check failed with HTTP {health_resp.status_code}"
        return summary
    ready = bool(health_payload.get("ready"))
    if not ready:
        why = str(health_payload.get("last_ready_error") or "").strip()
        summary["status"] = "RETRY_LATER"
        summary["detail"] = "Complex Editor bridge not ready" + (f": {why}" if why else "")
        return summary
    headless = bool(health_payload.get("headless"))
    allow = bool(health_payload.get("allow_headless", True))
    if headless and not (allow or allow_headless):
        summary["status"] = "RETRY_LATER"
        summary["detail"] = "Complex Editor exports disabled in headless mode (allow_headless=false)"
        return summary

    export_url = f"{base_url.rstrip('/')}/exports/mdb"
    out_dir_path.mkdir(parents=True, exist_ok=True)
    body = {"comp_ids": comp_ids, "out_dir": str(out_dir_path), "mdb_name": mdb_name}

    try:
        export_resp = session_http.post(
            export_url,
            headers=headers,
            json=body,
            timeout=timeout,
        )
    except req_exc.Timeout:
        summary["status"] = "RETRY_WITH_BACKOFF"
        summary["detail"] = "Complex Editor export timed out"
        return summary
    except req_exc.RequestException as exc:
        logger.info("CE export request failed: %s", exc)
        summary["status"] = "RETRY_WITH_BACKOFF"
        summary["detail"] = "Network error during Complex Editor export"
        return summary

    try:
        payload = export_resp.json()
    except ValueError:
        payload = {}

    payload = payload if isinstance(payload, dict) else {}
    summary["export_path"] = str(payload.get("export_path") or mdb_path)
    ce_detail = str(payload.get("detail") or "").strip()

    def _extend_report(ids: Iterable[object], reason: str) -> None:
        for item in ids:
            comp_id = _coerce_comp_id(item)
            if comp_id and comp_id in comp_map:
                for row in comp_map[comp_id]:
                    report_rows.append(
                        _ReportRow(
                            bom_id=row.bom_id,
                            line_id=row.line_id,
                            part_number=row.part_number,
                            comp_id=comp_id,
                            test_method=row.test_method,
                            status="SKIPPED",
                            reason=reason,
                        )
                    )
            else:
                report_rows.append(
                    _ReportRow(
                        bom_id=assembly_id,
                        line_id=None,
                        part_number=None,
                        comp_id=comp_id,
                        test_method=TestMethod.complex.value,
                        status="SKIPPED",
                        reason=reason,
                    )
                )

    status_code = export_resp.status_code
    reason = str(payload.get("reason") or "").strip().lower()

    if status_code == 200:
        exported_ids = payload.get("exported_comp_ids") or []
        if isinstance(exported_ids, list):
            summary["exported_count"] = len(exported_ids)
        missing_ids = payload.get("missing") or []
        unlinked_ids = payload.get("unlinked") or []
        _extend_report(missing_ids, "not_found_in_CE")
        _extend_report(unlinked_ids, "unlinked_data_in_CE")
        if report_rows:
            _write_report(report_path, report_rows)
            summary["report_path"] = str(report_path)
            summary["missing_count"] = len(report_rows)
            summary["status"] = "PARTIAL_SUCCESS"
            summary["detail"] = "Export completed with skipped rows; see report"
        else:
            summary["status"] = "SUCCESS"
            summary["detail"] = "Export completed successfully"
        return summary

    if status_code == 404 or reason in {"comp_ids_not_found", "no_matches"}:
        _extend_report(comp_ids, "not_found_in_CE")
        if report_rows:
            _write_report(report_path, report_rows)
            summary["report_path"] = str(report_path)
            summary["missing_count"] = len(report_rows)
        summary["status"] = "FAILED_INPUT"
        summary["detail"] = ce_detail or "Complex Editor did not recognise the provided component IDs"
        return summary

    if status_code == 409:
        if reason == "outdir_unwritable":
            summary["status"] = "FAILED_INPUT"
            summary["detail"] = ce_detail or "Export directory is not writable"
            return summary
        if reason == "empty_selection":
            summary["status"] = "FAILED_INPUT"
            summary["detail"] = ce_detail or "No valid component IDs after normalisation"
            return summary
        if reason == "template_missing_or_incompatible":
            summary["status"] = "FAILED_BACKEND"
            tpl = payload.get("template_path")
            suffix = f" ({tpl})" if tpl else ""
            summary["detail"] = ce_detail or f"Template missing or incompatible{suffix}"
            return summary
        summary["status"] = "FAILED_BACKEND"
        summary["detail"] = ce_detail or "Complex Editor rejected the export"
        return summary

    if status_code == 503 and reason == "bridge_headless":
        summary["status"] = "RETRY_LATER"
        summary["detail"] = ce_detail or "Complex Editor exports disabled in headless mode"
        return summary

    if status_code >= 500:
        summary["status"] = "FAILED_BACKEND"
        summary["detail"] = ce_detail or "Complex Editor export failed with a server error"
        return summary

    if status_code >= 400:
        summary["status"] = "FAILED_INPUT"
        summary["detail"] = ce_detail or f"Complex Editor export failed with HTTP {status_code}"
        return summary

    summary["status"] = "FAILED_BACKEND"
    summary["detail"] = ce_detail or "Unexpected Complex Editor response"
    return summary


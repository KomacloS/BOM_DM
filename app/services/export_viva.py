from __future__ import annotations

from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlmodel import Session, select

from ..models import Assembly, BOMItem, Customer, Part, Project
from ..domain.complex_linker import ComplexLink
from ..integration import ce_bridge_client
from ..integration.ce_bridge_client import CEAuthError, CEExportError, CENetworkError, CENotFound
from ..config import get_viva_export_settings, save_viva_export_settings

_INVALID_FILENAME_CHARS = re.compile(r"[\\/:*?\"<>|]+")
_APP_VERSION = os.getenv("BOM_DB_VERSION", "dev")


@dataclass
class VivaExportResult:
    status: str
    txt_path: Path
    manifest_path: Path
    mdb_path: Optional[Path]
    exported_comp_ids: List[int]
    warnings: List[str]
    missing_rows: List[Dict[str, Any]]
    unresolved_pns: List[str]
    trace_id: Optional[str]
    ce_export_path: Optional[str]
    diagnostics_path: Optional[Path]
    manifest: Dict[str, Any]


class VivaExportError(Exception):
    """Raised when the VIVA export flow fails with structured diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        missing_rows: Optional[List[Dict[str, Any]]] = None,
        unresolved_pns: Optional[List[str]] = None,
        trace_id: Optional[str] = None,
        diagnostics_path: Optional[Path] = None,
        suggestions: Optional[List[str]] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.missing_rows = missing_rows or []
        self.unresolved_pns = unresolved_pns or []
        self.trace_id = trace_id
        self.diagnostics_path = diagnostics_path
        self.suggestions = suggestions or []


@dataclass
class _ExportRow:
    reference: str
    part_number: str
    part_id: Optional[int]
    is_fitted: bool
    test_method: str
    ce_complex_id: Optional[int]

    def requires_complex(self) -> bool:
        return self.is_fitted and self.test_method.lower() == "complex"


def natural_key(s: str) -> List[object]:
    """Natural sort key splitting digits from text."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def build_viva_groups(rows_from_gui: Iterable[dict], session: Session, assembly_id: int) -> List[dict]:
    """Validate and group BOM rows for VIVA export."""
    # 1) Validate required Test Method/Test detail fields
    missing_tm = [r['reference'] for r in rows_from_gui if not (r.get('test_method') or '').strip() and (r.get('is_fitted', True))]
    if missing_tm:
        raise ValueError(f"Missing Test Method for: {', '.join(missing_tm[:25])}")

    missing_detail = [r['reference'] for r in rows_from_gui
                      if (r.get('is_fitted', True))
                      and (r.get('test_method','').strip().lower() == 'macro')
                      and not (r.get('test_detail') or '').strip()]
    if missing_detail:
        raise ValueError(f"Test Method 'macro' requires Test detail; missing for: {', '.join(missing_detail[:25])}")

    # 2) Compute Function per row; filter to fitted rows only
    prepared = []
    for r in rows_from_gui:
        if not r.get('is_fitted', True):
            continue
        tm = (r.get('test_method') or '').strip().lower()
        if tm == 'macro':
            func = (r.get('test_detail') or '').strip()
        elif tm == 'complex':
            func = 'Digital'
        elif tm:  # any other non-empty value
            func = 'Digital'
        else:
            continue  # already validated; defensive
        prepared.append({
            'reference': (r.get('reference') or '').strip(),
            'part_number': (r.get('part_number') or '').strip(),
            'function': func
        })

    # 3) Group by (PN, Function)
    groups: Dict[tuple[str, str], List[str]] = defaultdict(list)
    for row in prepared:
        key = (row['part_number'], row['function'])
        if row['reference']:
            groups[key].append(row['reference'])

    # 4) Fetch Part fields for all PNs in one go
    pn_list = sorted({pn for (pn, _) in groups.keys()})
    parts = {p.part_number: p for p in session.exec(select(Part).where(Part.part_number.in_(pn_list))).all()}

    # 5) Build final rows
    out_rows: List[dict] = []
    for (pn, func), refs in groups.items():
        refs = sorted(set(refs), key=natural_key)
        q = len(refs)
        p = parts.get(pn)
        value = p.value if p and p.value else ''
        toln = p.tol_n if p and p.tol_n is not None else ''
        tolp = p.tol_p if p and p.tol_p is not None else ''
        out_rows.append({
            'reference': ','.join(refs),
            'quantity': str(q),
            'part_number': pn,
            'function': func,
            'value': value,
            'toln': str(toln),
            'tolp': str(tolp),
        })

    # 6) Sort rows by first reference token naturally
    def first_ref_key(row: dict) -> List[object]:
        first = row['reference'].split(',')[0]
        return natural_key(first)

    out_rows.sort(key=first_ref_key)
    return out_rows


def write_viva_txt(path: str, rows: List[dict]) -> None:
    """Write VIVA export rows to a tab-delimited text file."""
    header = ['reference', 'quantity', 'Part number', 'Function', 'Value', 'TolN', 'TolP']
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\t'.join(header) + '\n')
        for r in rows:
            f.write('\t'.join([
                r['reference'], r['quantity'], r['part_number'], r['function'],
                r['value'], r['toln'], r['tolp']
            ]) + '\n')


def _manifest_timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _sanitize_filename_component(text: str, fallback: str) -> str:
    candidate = (text or "").strip()
    if not candidate:
        candidate = fallback
    candidate = _INVALID_FILENAME_CHARS.sub("_", candidate)
    candidate = candidate.strip(" .")
    return candidate or fallback


def _ensure_export_directory(base_dir: str) -> Path:
    path = Path(base_dir).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".viva_export_probe"
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
    except Exception as exc:  # pragma: no cover - filesystem specific
        raise VivaExportError(
            "Selected export folder is not writable.",
            reason="invalid_export_dir",
        ) from exc
    finally:
        with suppress(Exception):
            probe.unlink(missing_ok=True)
    return path


def _collect_missing_rows(contexts: Sequence[_ExportRow]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ctx in contexts:
        if ctx.requires_complex() and not ctx.ce_complex_id:
            rows.append(
                {
                    "reference": ctx.reference,
                    "part_number": ctx.part_number,
                    "part_id": ctx.part_id,
                }
            )
    rows.sort(key=lambda row: natural_key(row["reference"]))
    return rows


def _collect_missing_part_groups(contexts_by_part: Dict[Any, List[_ExportRow]]) -> List[tuple[Any, str, List[_ExportRow]]]:
    items: List[tuple[Any, str, List[_ExportRow]]] = []
    for key, ctxs in contexts_by_part.items():
        required = [ctx for ctx in ctxs if ctx.requires_complex()]
        if not required:
            continue
        if any(ctx.ce_complex_id for ctx in required):
            continue
        part_number = next((ctx.part_number for ctx in required if ctx.part_number), "")
        items.append((key, part_number, required))
    return items


def _sort_comp_ids(ids: Sequence[Optional[int]]) -> List[int]:
    seen: set[int] = set()
    normalized: List[int] = []
    for value in ids:
        if value is None:
            continue
        try:
            num = int(value)
        except (TypeError, ValueError):
            continue
        if num in seen:
            continue
        seen.add(num)
        normalized.append(num)
    normalized.sort()
    return normalized


def _pick_exact_complex_id(matches: Sequence[Dict[str, Any]], pn: str) -> Optional[int]:
    target = (pn or "").strip().lower()
    if not target:
        return None
    exact: List[Dict[str, Any]] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        pn_value = str(item.get("pn") or item.get("part_number") or "").strip().lower()
        aliases = item.get("aliases")
        alias_hit = False
        if isinstance(aliases, (list, tuple, set)):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip().lower() == target:
                    alias_hit = True
                    break
        if pn_value == target or alias_hit:
            exact.append(item)
    if len(exact) == 1:
        ce_id = exact[0].get("id") or exact[0].get("ce_id") or exact[0].get("complex_id")
        if ce_id is not None:
            try:
                return int(str(ce_id).strip())
            except ValueError:
                return None
    return None


def _write_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def _compose_bom_name(assembly: Assembly, project: Optional[Project], customer: Optional[Customer]) -> str:
    parts: List[str] = []
    if customer and getattr(customer, "name", None):
        parts.append(customer.name.strip())
    if project:
        title = (project.title or "").strip() or (project.name or "").strip() or (project.code or "").strip()
        if title:
            parts.append(title)
    if getattr(assembly, "rev", None):
        rev = str(assembly.rev).strip()
        if rev:
            parts.append(f"Rev {rev}")
    return " - ".join(p for p in parts if p) or f"Assembly {assembly.id}"


def _load_bom_entities(session: Session, assembly_id: int) -> tuple[Assembly, Optional[Project], Optional[Customer]]:
    assembly = session.get(Assembly, assembly_id)
    if assembly is None:
        raise VivaExportError("Assembly not found.", reason="assembly_missing")
    project: Optional[Project] = None
    if getattr(assembly, "project_id", None):
        project = session.get(Project, assembly.project_id)
    customer: Optional[Customer] = None
    if project and getattr(project, "customer_id", None):
        customer = session.get(Customer, project.customer_id)
    return assembly, project, customer


def _map_ce_error(exc: CEExportError) -> tuple[str, str, List[str]]:
    reason = (exc.reason or "").lower()
    payload = exc.payload or {}
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("detail") or payload.get("message") or "")
    suggestions: List[str] = []
    if reason == "export_mdb_unsupported":
        suggestions.append("Update Complex Editor to a version that supports MDB export.")
        return ("Your Complex Editor bridge is too old for MDB export. Please update CE.", reason, suggestions)
    if reason == "endpoint_missing":
        suggestions.append("Verify the bridge installation or contact support.")
        return ("Complex Editor bridge returned 404 for /exports/mdb despite reporting support.", reason, suggestions)
    if reason == "busy":
        return ("Close dialogs in Complex Editor and save, then retry.", "busy", suggestions)
    if reason in {"unlinked_or_missing", "pn_resolution"}:
        suggestions.append("Link the missing components or retry with relaxed export.")
        return ("Complex Editor reported missing links for required components.", reason, suggestions)
    if reason == "invalid_comp_ids":
        return ("Complex Editor reported invalid Complex IDs; verify the linked complexes still exist.", reason, suggestions)
    if reason in {"outdir_unwritable", "filesystem_error", "bad_filename", "template_missing_or_incompatible"}:
        if not detail:
            detail = "Complex Editor could not write the MDB to the selected folder."
        suggestions.append("Choose a different export folder or filename and retry.")
        return (detail, reason, suggestions)
    if reason == "headless":
        suggestions.append("Open Complex Editor or enable the bridge UI before exporting.")
        return ("Complex Editor bridge is running headless; open the UI and retry.", reason, suggestions)
    if reason == "db_engine_error":
        suggestions.append("Open Complex Editor logs for additional details.")
        return ("Complex Editor reported an internal database error.", reason, suggestions)
    if detail:
        return (detail, reason or "ce_error", suggestions)
    return (f"Complex Editor returned HTTP {exc.status_code}.", reason or "ce_error", suggestions)


def perform_viva_export(
    session: Session,
    assembly_id: int,
    *,
    base_dir: str,
    bom_rows: Iterable[dict],
    strict: bool = True,
) -> VivaExportResult:
    rows_from_gui = [dict(row) for row in bom_rows]
    export_dir = _ensure_export_directory(base_dir)
    diagnostics_path: Optional[Path] = None

    assembly, project, customer = _load_bom_entities(session, assembly_id)
    bom_name = _compose_bom_name(assembly, project, customer)
    base_filename = _sanitize_filename_component(bom_name, f"BOM_{assembly.id}")
    txt_filename = f"{base_filename} - BOM to VIVA.txt"
    txt_path = export_dir / txt_filename
    mdb_name = "bom_complexes.mdb"
    if not mdb_name.lower().endswith(".mdb"):
        mdb_name = f"{mdb_name}.mdb"
    if len(mdb_name) > 64:
        mdb_name = mdb_name[-64:]
    manifest_path = export_dir / "viva_manifest.json"

    try:
        viva_rows = build_viva_groups(rows_from_gui, session, assembly_id)
    except ValueError as exc:
        raise VivaExportError(str(exc), reason="validation") from exc
    write_viva_txt(str(txt_path), viva_rows)

    stmt = (
        select(BOMItem, Part)
        .join(Part, Part.id == BOMItem.part_id, isouter=True)
        .where(BOMItem.assembly_id == assembly_id)
    )
    results = session.exec(stmt).all()
    reference_map: Dict[str, List[Dict[str, Any]]] = {}
    part_ids: set[int] = set()
    for bom_item, part in results:
        ref = (bom_item.reference or "").strip()
        part_id = bom_item.part_id or (part.id if part else None)
        if part_id is not None:
            part_ids.add(part_id)
        entry = {
            "part_id": part_id,
            "part_number": (part.part_number if part else "") or "",
            "is_fitted": bool(getattr(bom_item, "is_fitted", True)),
        }
        reference_map.setdefault(ref, []).append(entry)

    links_by_part: Dict[int, int] = {}
    if part_ids:
        link_rows = session.exec(select(ComplexLink).where(ComplexLink.part_id.in_(part_ids))).all()
        for link in link_rows:
            if isinstance(link, ComplexLink) and link.ce_complex_id:
                text_id = str(link.ce_complex_id).strip()
                if not text_id:
                    continue
                try:
                    links_by_part[link.part_id] = int(text_id)
                except ValueError:
                    continue

    contexts: List[_ExportRow] = []
    contexts_by_part: Dict[Any, List[_ExportRow]] = {}
    for row in rows_from_gui:
        reference = (row.get("reference") or "").strip()
        entry_list = reference_map.get(reference, [])
        entry = entry_list.pop(0) if entry_list else None
        part_id = entry.get("part_id") if entry else None
        part_number = (row.get("part_number") or "").strip()
        if not part_number and entry:
            part_number = entry.get("part_number") or ""
        is_fitted = bool(entry.get("is_fitted")) if entry else bool(row.get("is_fitted", True))
        test_method = (row.get("test_method") or "").strip()
        ce_id = links_by_part.get(part_id) if part_id is not None else None
        ctx = _ExportRow(
            reference=reference,
            part_number=part_number,
            part_id=part_id,
            is_fitted=is_fitted,
            test_method=test_method,
            ce_complex_id=ce_id,
        )
        contexts.append(ctx)
        if part_id is not None:
            key: Any = part_id
        elif part_number:
            key = part_number
        else:
            key = reference
        contexts_by_part.setdefault(key, []).append(ctx)

    missing_rows_initial = _collect_missing_rows(contexts)
    missing_parts_initial = _collect_missing_part_groups(contexts_by_part)
    export_ids_initial = _sort_comp_ids([ctx.ce_complex_id for ctx in contexts if ctx.ce_complex_id])

    if missing_parts_initial and strict:
        warnings_blocked = ["Link required Complex Editor records before exporting."]
        manifest_data = {
            "status": "blocked",
            "created_at": _manifest_timestamp(),
            "bom_name": bom_name,
            "bom_id": assembly.id,
            "assembly_rev": getattr(assembly, "rev", "") or "",
            "txt_name": txt_path.name,
            "txt_path": txt_path.as_posix(),
            "export_folder": export_dir.as_posix(),
            "mdb_name": None,
            "export_path": None,
            "exported_comp_ids": export_ids_initial,
            "missing_or_unlinked": {
                "rows": missing_rows_initial,
                "unresolved_pns": [],
            },
            "warnings": warnings_blocked,
            "bom_db_version": _APP_VERSION,
            "ce_bridge_version": "",
            "ce_bridge_url": "",
            "trace_id": None,
            "resolved_from_pn": [],
        }
        _write_json_file(manifest_path, manifest_data)
        save_kwargs: Dict[str, str] = {"last_export_path": str(export_dir)}
        settings = get_viva_export_settings()
        if not settings.get("viva_export_base_dir"):
            save_kwargs["viva_export_base_dir"] = str(export_dir)
        save_viva_export_settings(**save_kwargs)
        raise VivaExportError(
            "Link required Complex Editor records before exporting.",
            reason="unlinked_required",
            missing_rows=missing_rows_initial,
        )

    warnings: List[str] = []
    unresolved_pns: List[str] = []
    resolved_by_pn: Dict[Any, int] = {}

    missing_parts = _collect_missing_part_groups(contexts_by_part)
    if missing_parts and not strict:
        for key, pn, required_ctxs in missing_parts:
            target_pn = (pn or "").strip()
            if not target_pn:
                unresolved_pns.append("")
                continue
            try:
                matches = ce_bridge_client.search_complexes(target_pn, limit=20)
            except (CEAuthError, CENetworkError) as exc:
                raise VivaExportError(str(exc), reason="search_failed") from exc
            ce_match = _pick_exact_complex_id(matches, target_pn)
            if ce_match:
                resolved_by_pn[key] = ce_match
                for ctx in required_ctxs:
                    ctx.ce_complex_id = ce_match
            else:
                unresolved_pns.append(target_pn)

    unresolved_pns = sorted({pn for pn in unresolved_pns if pn})
    missing_rows_final = _collect_missing_rows(contexts)
    missing_parts_final = _collect_missing_part_groups(contexts_by_part)
    export_ids_final = _sort_comp_ids([ctx.ce_complex_id for ctx in contexts if ctx.ce_complex_id])

    resolved_part_numbers: set[str] = set()
    if resolved_by_pn:
        for key, ce_id in resolved_by_pn.items():
            ctxs = contexts_by_part.get(key, [])
            for ctx in ctxs:
                if ctx.part_number:
                    resolved_part_numbers.add(ctx.part_number)
        if resolved_part_numbers:
            warnings.append("Resolved Complex IDs by PN: " + ", ".join(sorted(resolved_part_numbers)))

    if missing_parts_final:
        unresolved_parts = sorted(
            {ctx.part_number or ctx.reference for _, _, ctxs in missing_parts_final for ctx in ctxs}
        )
        if unresolved_parts:
            warnings.append("Complex IDs still missing for: " + ", ".join(unresolved_parts))
    if unresolved_pns:
        warnings.append("Unresolved part numbers: " + ", ".join(unresolved_pns))
    status = "success"
    if missing_parts_final or unresolved_pns:
        status = "partial"
    if not export_ids_final:
        status = "skipped"
        warnings.append("No Complex Editor IDs found; MDB not generated.")

    manifest_data = {
        "status": status,
        "created_at": _manifest_timestamp(),
        "bom_name": bom_name,
        "bom_id": assembly.id,
        "assembly_rev": getattr(assembly, "rev", "") or "",
        "txt_name": txt_path.name,
        "txt_path": txt_path.as_posix(),
        "export_folder": export_dir.as_posix(),
        "mdb_name": mdb_name if export_ids_final else None,
        "export_path": None,
        "exported_comp_ids": export_ids_final,
        "missing_or_unlinked": {
            "rows": missing_rows_final,
            "unresolved_pns": unresolved_pns,
        },
        "warnings": warnings,
        "bom_db_version": _APP_VERSION,
        "ce_bridge_version": "",
        "ce_bridge_url": "",
        "trace_id": None,
        "resolved_from_pn": sorted(resolved_part_numbers),
    }

    bridge_context: Dict[str, Any] = {}
    ce_trace_id: Optional[str] = None
    ce_export_path: Optional[str] = None
    mdb_path: Optional[Path] = None

    if export_ids_final:
        try:
            bridge_context = ce_bridge_client.get_bridge_context()
        except CENetworkError as exc:
            manifest_data["status"] = "error"
            manifest_data["warnings"].append(str(exc))
            _write_json_file(manifest_path, manifest_data)
            save_kwargs = {"last_export_path": str(export_dir)}
            settings = get_viva_export_settings()
            if not settings.get("viva_export_base_dir"):
                save_kwargs["viva_export_base_dir"] = str(export_dir)
            save_viva_export_settings(**save_kwargs)
            raise VivaExportError(str(exc), reason="bridge_unavailable", missing_rows=missing_rows_final, unresolved_pns=unresolved_pns) from exc
        manifest_data["ce_bridge_url"] = bridge_context.get("base_url", "")
        try:
            bridge_state = ce_bridge_client.wait_until_ready()
        except (CEAuthError, CENetworkError) as exc:
            manifest_data["status"] = "error"
            manifest_data["warnings"].append(str(exc))
            _write_json_file(manifest_path, manifest_data)
            save_kwargs = {"last_export_path": str(export_dir)}
            settings = get_viva_export_settings()
            if not settings.get("viva_export_base_dir"):
                save_kwargs["viva_export_base_dir"] = str(export_dir)
            save_viva_export_settings(**save_kwargs)
            raise VivaExportError(str(exc), reason="bridge_unavailable", missing_rows=missing_rows_final, unresolved_pns=unresolved_pns) from exc
        try:
            payload = ce_bridge_client.export_complexes_mdb(
                comp_ids=export_ids_final,
                out_dir=str(export_dir),
                mdb_name=mdb_name,
            )
        except CEExportError as exc:
            message, reason_code, suggestions = _map_ce_error(exc)
            ce_trace_id = exc.trace_id
            manifest_data["status"] = "error"
            manifest_data["trace_id"] = ce_trace_id
            manifest_data["ce_bridge_version"] = str(bridge_state.get("version") or bridge_state.get("build") or "")
            manifest_data["ce_bridge_url"] = bridge_context.get("base_url", "")
            manifest_data["warnings"].append(message)
            if suggestions:
                manifest_data["warnings"].extend(suggestions)
            diagnostics_path = export_dir / "ce_response.json"
            payload_body = exc.payload if isinstance(exc.payload, dict) else {"payload": exc.payload}
            _write_json_file(diagnostics_path, payload_body)
            manifest_data["ce_response_path"] = diagnostics_path.as_posix()
            _write_json_file(manifest_path, manifest_data)
            save_kwargs = {"last_export_path": str(export_dir)}
            settings = get_viva_export_settings()
            if not settings.get("viva_export_base_dir"):
                save_kwargs["viva_export_base_dir"] = str(export_dir)
            save_viva_export_settings(**save_kwargs)
            raise VivaExportError(
                message,
                reason=reason_code,
                missing_rows=missing_rows_final,
                unresolved_pns=unresolved_pns,
                trace_id=ce_trace_id,
                diagnostics_path=diagnostics_path,
                suggestions=suggestions,
            ) from exc
        except CENotFound as exc:
            detail = "Your Complex Editor bridge is too old for MDB export. Please update CE."
            manifest_data["status"] = "error"
            manifest_data["warnings"].append(detail)
            manifest_data["warnings"].append("Update Complex Editor or enable the bridge exporter feature, then retry.")
            manifest_data["ce_bridge_url"] = bridge_context.get("base_url", "")
            manifest_data["ce_bridge_version"] = str(bridge_state.get("version") or bridge_state.get("build") or "")
            _write_json_file(manifest_path, manifest_data)
            save_kwargs = {"last_export_path": str(export_dir)}
            settings = get_viva_export_settings()
            if not settings.get("viva_export_base_dir"):
                save_kwargs["viva_export_base_dir"] = str(export_dir)
            save_viva_export_settings(**save_kwargs)
            raise VivaExportError(
                detail,
                reason="endpoint_missing",
                missing_rows=missing_rows_final,
                unresolved_pns=unresolved_pns,
                suggestions=[
                    "Update Complex Editor or enable the bridge exporter feature, then retry."
                ],
            ) from exc
        except (CEAuthError, CENetworkError) as exc:
            manifest_data["status"] = "error"
            manifest_data["warnings"].append(str(exc))
            manifest_data["ce_bridge_url"] = bridge_context.get("base_url", "")
            manifest_data["ce_bridge_version"] = str(bridge_state.get("version") or bridge_state.get("build") or "")
            _write_json_file(manifest_path, manifest_data)
            save_kwargs = {"last_export_path": str(export_dir)}
            settings = get_viva_export_settings()
            if not settings.get("viva_export_base_dir"):
                save_kwargs["viva_export_base_dir"] = str(export_dir)
            save_viva_export_settings(**save_kwargs)
            raise VivaExportError(str(exc), reason="bridge_unavailable", missing_rows=missing_rows_final, unresolved_pns=unresolved_pns) from exc

        manifest_data["ce_bridge_version"] = str(payload.get("bridge_version") or bridge_state.get("version") or "")
        trace_id_raw = payload.get("trace_id")
        if isinstance(trace_id_raw, (str, int)):
            ce_trace_id = str(trace_id_raw)
        exported_payload = payload.get("exported_comp_ids")
        if isinstance(exported_payload, (list, tuple, set)):
            payload_ids: List[int] = []
            for item in exported_payload:
                try:
                    payload_ids.append(int(item))
                except (TypeError, ValueError):
                    continue
            converted = _sort_comp_ids(payload_ids)
            if converted:
                manifest_data["exported_comp_ids"] = converted
        export_path_value = payload.get("export_path") or payload.get("path")
        if isinstance(export_path_value, str) and export_path_value.strip():
            ce_export_path = export_path_value.strip()
            mdb_path = Path(ce_export_path)
        else:
            fallback_name = payload.get("mdb_name") or mdb_name
            mdb_path = (export_dir / fallback_name).resolve()
            ce_export_path = mdb_path.as_posix()
        manifest_data["mdb_name"] = mdb_path.name
        manifest_data["export_path"] = ce_export_path
        manifest_data["trace_id"] = ce_trace_id
    else:
        try:
            bridge_context = ce_bridge_client.get_bridge_context()
        except CENetworkError:
            bridge_context = {}
        manifest_data["ce_bridge_url"] = bridge_context.get("base_url", "")
        mdb_path = None
        ce_export_path = None

    _write_json_file(manifest_path, manifest_data)
    save_kwargs = {"last_export_path": str(export_dir)}
    settings = get_viva_export_settings()
    if not settings.get("viva_export_base_dir"):
        save_kwargs["viva_export_base_dir"] = str(export_dir)
    save_viva_export_settings(**save_kwargs)

    return VivaExportResult(
        status=manifest_data["status"],
        txt_path=txt_path,
        manifest_path=manifest_path,
        mdb_path=mdb_path,
        exported_comp_ids=manifest_data["exported_comp_ids"],
        warnings=manifest_data["warnings"],
        missing_rows=manifest_data["missing_or_unlinked"]["rows"],
        unresolved_pns=manifest_data["missing_or_unlinked"]["unresolved_pns"],
        trace_id=manifest_data.get("trace_id"),
        ce_export_path=manifest_data.get("export_path"),
        diagnostics_path=diagnostics_path,
        manifest=manifest_data,
    )


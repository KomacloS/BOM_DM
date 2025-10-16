from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
import json
import logging
import re
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from sqlmodel import Session, select

from ..domain.complex_linker import ComplexLink
from ..integration import ce_bridge_client, ce_bridge_transport
from ..integration.ce_bridge_client import (
    CEExportBusyError,
    CEExportError,
    CEExportStrictError,
    CEPNResolutionError,
)
from ..models import (
    Assembly,
    BOMItem,
    Part,
    PartTestAssignment,
    Project,
    TestMethod,
)

ResolveFunc = Callable[[Sequence[str]], Tuple[Dict[str, int], List[str]]]

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class VIVAExportPaths:
    """Convenience container describing the generated VIVA export paths."""

    folder: Path
    bom_txt: Path
    mdb_path: Path


@dataclass(frozen=True)
class VIVABOMLine:
    """Joined BOM row enriched with Complex assignment metadata."""

    line_number: int
    bom_item_id: int
    reference: str
    part_id: Optional[int]
    part_number: Optional[str]
    description: Optional[str]
    requires_complex: bool
    complex_id: Optional[int]
    complex_id_raw: Optional[str]
    is_fitted: bool


@dataclass(frozen=True)
class VIVAMissingComplex:
    """Metadata describing a BOM row that requires a Complex but lacks one."""

    line_number: int
    part_number: str | None
    description: str | None
    reference: str


@dataclass(frozen=True)
class VIVAExportDiagnostics:
    """Captured diagnostics persisted alongside the manifest."""

    manifest_path: Path
    ce_response_path: Optional[Path] = None


@dataclass(frozen=True)
class VIVAExportOutcome:
    """Result of a VIVA export operation."""

    paths: VIVAExportPaths
    comp_ids: Tuple[int, ...]
    manifest: Dict[str, object]
    diagnostics: VIVAExportDiagnostics
    ce_payload: Dict[str, object] = field(default_factory=dict)
    warnings: Tuple[str, ...] = field(default_factory=tuple)


class VIVAExportValidationError(Exception):
    """Raised when strict-mode validation fails prior to contacting CE."""

    def __init__(self, missing: Sequence[VIVAMissingComplex]):
        message = "Some BOM rows require a Complex assignment"
        super().__init__(message)
        self.missing = list(missing)


def _pkg_version() -> str:
    try:
        return metadata.version("bom_platform")
    except metadata.PackageNotFoundError:  # pragma: no cover - fallback in dev
        return "unknown"


def natural_key(s: str) -> List[object]:
    """Natural sort key splitting digits from text."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


_INVALID_FILENAME = re.compile(r"[\\/:*?\"<>|]+")


def sanitize_token(value: Optional[str], fallback: str) -> str:
    """Return ``value`` trimmed and stripped of characters invalid on Windows."""

    text = (value or "").strip()
    if not text:
        text = fallback
    sanitized = _INVALID_FILENAME.sub("_", text)
    sanitized = sanitized.strip(" .") or fallback
    return sanitized[:120]


def build_export_folder_name(
    assembly_code: str,
    revision: str,
    *,
    timestamp: Optional[datetime] = None,
) -> str:
    """Construct a timestamped folder name for a VIVA export."""

    ts = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M")
    code_token = sanitize_token(assembly_code, "ASM")
    rev_token = sanitize_token(revision, "REV")
    return f"VIVA_{code_token}_{rev_token}_{ts}"


def build_export_paths(
    base_dir: Path,
    assembly_code: str,
    revision: str,
    *,
    timestamp: Optional[datetime] = None,
) -> VIVAExportPaths:
    """Return the folder and file paths for a VIVA export rooted at ``base_dir``."""

    folder_name = build_export_folder_name(assembly_code, revision, timestamp=timestamp)
    folder = base_dir / folder_name
    return VIVAExportPaths(
        folder=folder,
        bom_txt=folder / "BOM_to_VIVA.txt",
        mdb_path=folder / "bom_complexes.mdb",
    )


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


_COMPLEX_ID_TOKEN = re.compile(r"(\d+)")


def _parse_complex_id(raw: object) -> Optional[int]:
    """Return an integer Complex ID from the stored value, if possible."""

    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        match = _COMPLEX_ID_TOKEN.search(text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:  # pragma: no cover - defensive
                return None
    return None


def _iter_bom_scope(
    session: Session, assembly_id: int
) -> Iterator[tuple[BOMItem, Part | None, PartTestAssignment | None, ComplexLink | None]]:
    stmt = (
        select(BOMItem, Part, PartTestAssignment, ComplexLink)
        .join(Part, Part.id == BOMItem.part_id, isouter=True)
        .join(PartTestAssignment, PartTestAssignment.part_id == Part.id, isouter=True)
        .join(ComplexLink, ComplexLink.part_id == Part.id, isouter=True)
        .where(BOMItem.assembly_id == assembly_id)
    )
    rows = session.exec(stmt).all()
    for row in rows:
        yield row


def collect_bom_lines(session: Session, assembly_id: int) -> List[VIVABOMLine]:
    """Return BOM lines for ``assembly_id`` enriched with Complex metadata."""

    rows = list(_iter_bom_scope(session, assembly_id))
    rows.sort(key=lambda row: natural_key(row[0].reference))
    lines: List[VIVABOMLine] = []
    for index, (bom, part, assignment, link) in enumerate(rows, start=1):
        method = assignment.method if assignment else None
        requires_complex = (
            bool(bom.is_fitted)
            and part is not None
            and method == TestMethod.complex
        )
        raw_id = getattr(link, "ce_complex_id", None) if link else None
        comp_id = _parse_complex_id(raw_id)
        description = None
        if part and getattr(part, "description", None):
            description = part.description
        elif getattr(bom, "notes", None):
            description = bom.notes
        lines.append(
            VIVABOMLine(
                line_number=index,
                bom_item_id=int(bom.id),
                reference=bom.reference,
                part_id=getattr(part, "id", None),
                part_number=getattr(part, "part_number", None),
                description=description,
                requires_complex=requires_complex,
                complex_id=comp_id,
                complex_id_raw=str(raw_id) if raw_id is not None else None,
                is_fitted=bool(bom.is_fitted),
            )
        )
    return lines


def _resolve_comp_ids_by_pn(
    pns: Sequence[str],
    *,
    resolver: Optional[ResolveFunc] = None,
) -> Tuple[Dict[str, int], List[str]]:
    if resolver is None:
        resolver = ce_bridge_client.lookup_complex_ids
    normalized: List[str] = []
    seen: set[str] = set()
    for pn in pns:
        text = pn.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    mapping: Dict[str, int] = {}
    unresolved: List[str] = []
    if not normalized:
        return mapping, unresolved
    resolved_map, unresolved_list = resolver(normalized)
    for pn, cid in resolved_map.items():
        try:
            mapping[pn] = int(cid)
        except (TypeError, ValueError):
            continue
    for pn in unresolved_list:
        unresolved.append(pn)
    return mapping, unresolved


def determine_comp_ids(
    lines: Sequence[VIVABOMLine],
    *,
    strict: bool = True,
    resolver: Optional[ResolveFunc] = None,
) -> Tuple[List[int], List[str], List[VIVAMissingComplex]]:
    """Return deduplicated comp ids, unresolved PN warnings and missing rows."""

    assigned_ids: List[int] = []
    missing_rows: List[VIVAMissingComplex] = []
    pn_lookup: Dict[str, List[VIVABOMLine]] = {}

    for line in lines:
        if line.complex_id is not None:
            assigned_ids.append(line.complex_id)

        if not line.requires_complex:
            continue

        if line.complex_id is not None:
            continue

        missing_rows.append(
            VIVAMissingComplex(
                line_number=line.line_number,
                part_number=line.part_number,
                description=line.description,
                reference=line.reference,
            )
        )

        pn = (line.part_number or "").strip()
        if pn:
            pn_lookup.setdefault(pn, []).append(line)

    if missing_rows and strict:
        raise VIVAExportValidationError(missing_rows)

    unresolved: List[str] = []
    resolved_ids: List[int] = []
    if pn_lookup:
        mapping, unresolved = _resolve_comp_ids_by_pn(
            list(pn_lookup.keys()), resolver=resolver
        )
        for pn, lines_for_pn in pn_lookup.items():
            resolved_id = mapping.get(pn)
            if resolved_id is None:
                continue
            resolved_ids.append(resolved_id)
        if unresolved and strict:
            raise CEPNResolutionError(unresolved)

    comp_ids = assigned_ids + resolved_ids
    if comp_ids:
        comp_ids = sorted({int(value) for value in comp_ids})
    return comp_ids, unresolved, missing_rows


def _ce_bridge_version() -> Optional[str]:
    payload = ce_bridge_transport.get_last_preflight_payload()
    if not payload:
        return None
    for key in ("bridge_version", "version", "app_version"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _manifest_path(folder: Path) -> Path:
    return folder / "viva_manifest.json"


def _diagnostics_response_path(folder: Path) -> Path:
    return folder / "ce_response.json"


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def _safe_bridge_url() -> str:
    try:
        return ce_bridge_client.get_active_base_url()
    except Exception:  # pragma: no cover - defensive fallback
        return ""


def _build_manifest(
    *,
    assembly: Assembly,
    project: Optional[Project],
    paths: VIVAExportPaths,
    comp_ids: Sequence[int],
    warnings: Sequence[str],
    missing_rows: Sequence[VIVAMissingComplex],
    ce_payload: Optional[Dict[str, object]],
    ce_bridge_url: str,
    status: str = "success",
    error: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    manifest: Dict[str, object] = {
        "created_at": created_at,
        "bom_id": assembly.id,
        "bom_revision": assembly.rev,
        "bom_name": getattr(project, "code", None) or getattr(project, "title", None) or f"ASM{assembly.id}",
        "project_id": getattr(project, "id", None),
        "project_code": getattr(project, "code", None),
        "ce_bridge_url": ce_bridge_url,
        "mdb_name": paths.mdb_path.name,
        "export_path": str(paths.folder),
        "exported_comp_ids": list(comp_ids),
        "status": status,
        "missing_or_unlinked": {
            "rows": [
                {
                    "line_number": row.line_number,
                    "part_number": row.part_number,
                    "description": row.description,
                    "reference": row.reference,
                }
                for row in missing_rows
            ],
            "unresolved_pns": list(warnings),
        },
        "bom_db_version": _pkg_version(),
    }
    bridge_version = _ce_bridge_version()
    if bridge_version:
        manifest["ce_bridge_version"] = bridge_version
    if ce_payload:
        trace_id = ce_payload.get("trace_id")
        if isinstance(trace_id, str) and trace_id.strip():
            manifest["trace_id"] = trace_id.strip()
        manifest["ce_response"] = ce_payload
    if error:
        manifest["error"] = error
    return manifest


def perform_viva_export(
    session: Session,
    assembly_id: int,
    *,
    base_dir: Path,
    bom_rows: Sequence[dict],
    timestamp: Optional[datetime] = None,
    strict: bool = True,
    resolver: Optional[ResolveFunc] = None,
    mdb_name: str = "bom_complexes.mdb",
) -> VIVAExportOutcome:
    """Perform the full VIVA export flow for ``assembly_id``."""

    assembly = session.get(Assembly, assembly_id)
    if assembly is None:
        raise ValueError(f"Assembly {assembly_id} not found")
    project = session.get(Project, assembly.project_id) if getattr(assembly, "project_id", None) else None

    assembly_code = getattr(project, "code", None) or getattr(project, "title", "") or f"ASM{assembly_id}"
    assembly_rev = getattr(assembly, "rev", "") or "REV"

    paths = build_export_paths(base_dir, assembly_code, assembly_rev, timestamp=timestamp)

    lines = collect_bom_lines(session, assembly_id)
    comp_ids, unresolved, missing_rows = determine_comp_ids(
        lines, strict=strict, resolver=resolver
    )

    if mdb_name is None:
        mdb_name = "bom_complexes.mdb"
    mdb_name = str(mdb_name).strip() or "bom_complexes.mdb"
    if not mdb_name.lower().endswith(".mdb"):
        mdb_name = f"{mdb_name}.mdb"
    if len(mdb_name) > 64:
        raise ValueError("MDB filename must be 64 characters or fewer")
    paths = replace(paths, mdb_path=paths.folder / mdb_name)

    paths.folder.mkdir(parents=True, exist_ok=True)
    write_viva_txt(str(paths.bom_txt), list(bom_rows))

    ce_bridge_client.wait_until_ready()

    try:
        ce_payload = ce_bridge_client.export_complexes_mdb(
            comp_ids,
            str(paths.folder.resolve()),
            mdb_name=mdb_name,
        )
    except CEExportError as exc:
        ce_bridge_url = _safe_bridge_url()
        ce_payload = exc.payload if isinstance(exc.payload, dict) else {}
        manifest = _build_manifest(
            assembly=assembly,
            project=project,
            paths=paths,
            comp_ids=comp_ids,
            warnings=unresolved,
            missing_rows=missing_rows,
            ce_payload=ce_payload,
            ce_bridge_url=ce_bridge_url,
            status="error",
            error={
                "message": str(exc),
                "reason": exc.reason,
                "status_code": exc.status_code,
            },
        )
        manifest_path = _manifest_path(paths.folder)
        _write_json(manifest_path, manifest)
        ce_response_path: Optional[Path] = None
        if ce_payload:
            ce_response_path = _diagnostics_response_path(paths.folder)
            _write_json(ce_response_path, ce_payload)
        diagnostics = VIVAExportDiagnostics(
            manifest_path=manifest_path,
            ce_response_path=ce_response_path,
        )
        setattr(exc, "diagnostics", diagnostics)
        raise

    ce_bridge_url = _safe_bridge_url()

    manifest = _build_manifest(
        assembly=assembly,
        project=project,
        paths=paths,
        comp_ids=comp_ids,
        warnings=unresolved,
        missing_rows=missing_rows,
        ce_payload=ce_payload,
        ce_bridge_url=ce_bridge_url,
    )

    manifest_path = _manifest_path(paths.folder)
    _write_json(manifest_path, manifest)

    diagnostics = VIVAExportDiagnostics(manifest_path=manifest_path)
    return VIVAExportOutcome(
        paths=paths,
        comp_ids=tuple(comp_ids),
        manifest=manifest,
        diagnostics=diagnostics,
        ce_payload=ce_payload if isinstance(ce_payload, dict) else {},
        warnings=tuple(unresolved),
    )


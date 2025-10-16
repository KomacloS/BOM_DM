from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional

from sqlmodel import Session, select

from ..models import Part


@dataclass(frozen=True)
class VIVAExportPaths:
    """Convenience container describing the generated VIVA export paths."""

    folder: Path
    bom_txt: Path
    mdb_path: Path


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


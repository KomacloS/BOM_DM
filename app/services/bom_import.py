"""BOM import service supporting CSV and XLSX sources."""

from __future__ import annotations

import csv
import io
import re
import shutil
from decimal import Decimal
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field, validator
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from ..models import Assembly, BOMItem, Part, PartType


class ImportReport(BaseModel):
    total: int
    matched: int
    unmatched: int
    created_task_ids: List[int] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Header handling


def _norm(s: str) -> str:
    """Normalize a header name by stripping, lowering and removing symbols."""
    return re.sub(r"[^a-z0-9]+", "", s.strip().lower())


HEADER_MAP = {
    "part_number": {"pn", "mpn", "part", "partnumber", "manufacturerpartnumber"},
    "reference": {"ref", "reference", "designator", "refdes", "refdesg", "designators"},
    "qty": {"qty", "quantity", "q'ty", "qnty"},
    "manufacturer": {"mfr", "manufacturer", "vendor", "maker"},
    "active_passive": {"active/passive", "activepassive", "a/p", "ap"},
    "function": {"function", "func"},
    "tol_p": {"tolerancep", "tolerance+", "tol+", "tolp", "tolerancepos"},
    "tol_n": {"tolerancen", "tolerance-", "tol-", "toln", "toleranceneg"},
    "unit_cost": {"price", "unitprice", "cost", "unit_cost"},
    "currency": {"currency", "curr"},
    "datasheet_url": {"datasheet", "datasheeturl", "datasheet_link", "datasheetlink"},
    "notes": {"notes", "comment", "comments"},
}


def validate_headers(headers: List[str]) -> dict[str, int]:
    """Validate headers and return a mapping of canonical name to index."""

    col_map: dict[str, int] = {}
    for idx, h in enumerate(headers):
        hn = _norm(h)
        for canon, variants in HEADER_MAP.items():
            norm_set = {_norm(canon)} | {_norm(v) for v in variants}
            if hn in norm_set and canon not in col_map:
                col_map[canon] = idx
                break
    missing = [c for c in ("part_number", "reference") if c not in col_map]
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")
    return col_map


# ---------------------------------------------------------------------------
# Row schema


class BOMRow(BaseModel):
    part_number: str
    reference: str
    qty: int = 1
    manufacturer: str | None = None
    active_passive: str | None = None
    function: str | None = None
    tol_p: str | None = None
    tol_n: str | None = None
    unit_cost: Decimal | None = None
    currency: str | None = None
    datasheet_url: str | None = None
    notes: str | None = None

    @validator("part_number", "reference")
    def _req_trim(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("required")
        return v

    @validator("qty", pre=True)
    def _qty_int(cls, v):
        if v in (None, "", " "):
            return 1
        try:
            return int(str(v).strip())
        except Exception:  # pragma: no cover - defensive
            raise ValueError("qty must be integer")

    @validator("currency")
    def _cur_up(cls, v):
        return (v or "").upper() or None


# ---------------------------------------------------------------------------
# File parsing helpers


def _is_xlsx(data: bytes) -> bool:
    return data[:2] == b"PK"


def _iter_rows(data: bytes) -> tuple[list[str], list[list[str]]]:
    if _is_xlsx(data):
        from openpyxl import load_workbook

        wb = load_workbook(filename=io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows = [[(c if c is not None else "") for c in r] for r in ws.iter_rows(values_only=True)]
        headers = [str(x or "") for x in rows[0]] if rows else []
        return headers, [list(map(lambda x: "" if x is None else str(x), r)) for r in rows[1:]]
    else:
        text = data.decode("utf-8-sig", errors="ignore")
        rdr = csv.reader(io.StringIO(text))
        rows = list(rdr)
        headers = rows[0] if rows else []
        return headers, rows[1:]


# ---------------------------------------------------------------------------
# Datasheet caching


DATASHEETS_DIR = Path("datasheets")
DATASHEETS_DIR.mkdir(exist_ok=True)


def _safe_pn(pn: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", pn.strip())[:80]


def _cache_datasheet_for_pn(pn: str, url_or_path: str) -> str | None:
    if not url_or_path:
        return None
    try:
        p = Path(url_or_path)
        if p.exists() and p.is_file():
            ext = p.suffix or ".pdf"
            dst = DATASHEETS_DIR / f"{_safe_pn(pn)}{ext}"
            shutil.copyfile(p, dst)
            return str(dst)
        if url_or_path.lower().startswith(("http://", "https://")):
            import requests

            r = requests.get(url_or_path, timeout=15)
            r.raise_for_status()
            ext = ".pdf"
            dst = DATASHEETS_DIR / f"{_safe_pn(pn)}{ext}"
            with open(dst, "wb") as f:
                f.write(r.content)
            return str(dst)
    except Exception:  # pragma: no cover - cache failures are non-fatal
        return url_or_path
    return None


# ---------------------------------------------------------------------------
# Importer


def import_bom(assembly_id: int, data: bytes, session: Session) -> ImportReport:
    errors: List[str] = []
    try:
        headers, raw_rows = _iter_rows(data)
        col_map = validate_headers(headers)
    except Exception as exc:
        errors.append(str(exc))
        return ImportReport(total=0, matched=0, unmatched=0, errors=errors)

    assembly = session.get(Assembly, assembly_id)
    if not assembly:
        errors.append("assembly not found")
        return ImportReport(total=0, matched=0, unmatched=0, errors=errors)

    total = matched = unmatched = 0

    for i, row in enumerate(raw_rows, start=2):
        data_map = {key: row[idx] if idx < len(row) else "" for key, idx in col_map.items()}
        if not data_map.get("part_number") or not data_map.get("reference"):
            errors.append(f"Row {i}: missing part_number or reference")
            continue
        try:
            bom_row = BOMRow(**data_map)
        except Exception as e:
            errors.append(f"Row {i}: {e}")
            continue

        total += 1
        pn = bom_row.part_number
        part = session.exec(select(Part).where(Part.part_number == pn)).first()
        is_new = part is None
        if part:
            matched += 1
        else:
            unmatched += 1
            part = Part(part_number=pn)

        if bom_row.active_passive:
            try:
                ap = PartType(bom_row.active_passive.lower())
            except Exception:
                ap = PartType.passive
            if is_new or not getattr(part, "active_passive", None):
                part.active_passive = ap
        if bom_row.function and (is_new or not part.function):
            part.function = bom_row.function
        if bom_row.tol_p and (is_new or not part.tol_p):
            part.tol_p = bom_row.tol_p
        if bom_row.tol_n and (is_new or not part.tol_n):
            part.tol_n = bom_row.tol_n
        if bom_row.datasheet_url and (is_new or not part.datasheet_url):
            cached = _cache_datasheet_for_pn(pn, bom_row.datasheet_url)
            if cached:
                part.datasheet_url = cached

        session.add(part)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            part = session.exec(select(Part).where(Part.part_number == pn)).first()
            if not part:
                errors.append(f"Row {i}: duplicate part_number {pn}")
                continue
        session.refresh(part)

        item = session.exec(
            select(BOMItem).where(
                BOMItem.assembly_id == assembly_id, BOMItem.reference == bom_row.reference
            )
        ).first()
        if not item:
            item = BOMItem(assembly_id=assembly_id, reference=bom_row.reference)

        item.part_id = part.id
        item.qty = bom_row.qty
        if bom_row.manufacturer:
            item.manufacturer = bom_row.manufacturer
        if bom_row.unit_cost is not None:
            item.unit_cost = bom_row.unit_cost
        if bom_row.currency:
            item.currency = bom_row.currency
        if bom_row.datasheet_url:
            item.datasheet_url = bom_row.datasheet_url
        if bom_row.notes:
            item.notes = bom_row.notes

        session.add(item)
        session.commit()

    return ImportReport(total=total, matched=matched, unmatched=unmatched, errors=errors)


__all__ = ["ImportReport", "validate_headers", "import_bom"]


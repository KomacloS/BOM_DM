from __future__ import annotations

from typing import List, Literal, Optional
import re

from pydantic import BaseModel
from sqlmodel import Session, select

from ..models import Assembly, BOMItem, Part, PartType, TestMode
from .test_resolution import BOMTestResolver

# Regex for natural sort
_token = re.compile(r"(\d+)")

def natural_key(s: str) -> list[object]:
    return [int(t) if t.isdigit() else t.lower() for t in _token.split(s)]


class JoinedBOMRow(BaseModel):
    bom_item_id: int
    part_id: int
    part_number: str
    reference: str
    qty: int
    description: str | None
    manufacturer: str | None
    function: str | None = None
    package: str | None = None
    value: str | None = None
    tol_p: str | None = None
    tol_n: str | None = None
    active_passive: Optional[Literal["active", "passive"]] = None
    datasheet_url: str | None = None
    product_url: str | None = None
    test_method: str | None = None
    test_detail: str | None = None
    test_method_powered: str | None = None
    test_detail_powered: str | None = None
    test_resolution_source: str | None = None
    test_resolution_message: str | None = None


def get_joined_bom_for_assembly(session: Session, assembly_id: int) -> List[JoinedBOMRow]:
    """Return joined BOM items with part data for an assembly."""

    stmt = (
        select(BOMItem, Part)
        .join(Part, Part.id == BOMItem.part_id)
        .where(BOMItem.assembly_id == assembly_id)
    )
    rows = session.exec(stmt).all()
    assembly = session.get(Assembly, assembly_id)
    resolver = BOMTestResolver.from_session(session, assembly_id, rows)
    assembly_mode = TestMode.unpowered
    if assembly is not None:
        mode_val = getattr(assembly, "test_mode", None)
        if isinstance(mode_val, TestMode):
            assembly_mode = mode_val
        elif isinstance(mode_val, str):
            try:
                assembly_mode = TestMode(mode_val)
            except ValueError:
                assembly_mode = TestMode.unpowered
    result: List[JoinedBOMRow] = []
    for item, part in rows:
        ap = part.active_passive.value if isinstance(part.active_passive, PartType) else part.active_passive
        resolved = resolver.resolve_effective_test(item.id, assembly_mode)
        result.append(
            JoinedBOMRow(
                bom_item_id=item.id,
                part_id=part.id,
                part_number=part.part_number,
                reference=item.reference,
                qty=item.qty,
                description=part.description,
                manufacturer=item.manufacturer,
                function=part.function,
                package=part.package,
                value=part.value,
                tol_p=part.tol_p,
                tol_n=part.tol_n,
                active_passive=ap,
                datasheet_url=part.datasheet_url,
                product_url=part.product_url,
                test_method=resolved.method,
                test_detail=resolved.detail,
                test_method_powered=resolved.powered_method,
                test_detail_powered=resolved.powered_detail,
                test_resolution_source=resolved.source,
                test_resolution_message=resolved.message,
            )
        )
    result.sort(key=lambda r: natural_key(r.reference))
    return result

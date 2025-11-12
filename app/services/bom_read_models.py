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
    part_id: int | None
    part_number: str | None
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

    assembly = session.get(Assembly, assembly_id)
    raw_mode = assembly.test_mode if assembly and assembly.test_mode else TestMode.unpowered
    assembly_mode = raw_mode if isinstance(raw_mode, TestMode) else TestMode(str(raw_mode))

    stmt = (
        select(BOMItem, Part)
        .join(Part, Part.id == BOMItem.part_id, isouter=True)
        .where(BOMItem.assembly_id == assembly_id)
    )
    rows = session.exec(stmt).all()
    resolver = BOMTestResolver.from_session(session, assembly_id, rows)
    result: List[JoinedBOMRow] = []
    for item, part in rows:
        if part is not None and isinstance(part.active_passive, PartType):
            ap_value = part.active_passive.value
            part_is_active = part.active_passive is PartType.active
        elif part is not None and part.active_passive is not None:
            try:
                enum_val = PartType(str(part.active_passive))
                ap_value = enum_val.value
                part_is_active = enum_val is PartType.active
            except ValueError:
                ap_value = None
                part_is_active = False
        else:
            ap_value = None
            part_is_active = False

        resolved = resolver.resolve_effective_test(item.id, assembly_mode)
        if assembly_mode is TestMode.powered:
            powered_method = getattr(resolved, "powered_method", None)
            powered_detail = getattr(resolved, "powered_detail", None)
        else:
            powered_method = None
            powered_detail = None

        result.append(
            JoinedBOMRow(
                bom_item_id=item.id,
                part_id=part.id if part else None,
                part_number=part.part_number if part else None,
                reference=item.reference,
                qty=item.qty,
                description=part.description if part else None,
                manufacturer=item.manufacturer,
                function=part.function if part else None,
                package=part.package if part else None,
                value=part.value if part else None,
                tol_p=part.tol_p if part else None,
                tol_n=part.tol_n if part else None,
                active_passive=ap_value,
                datasheet_url=part.datasheet_url if part else None,
                product_url=part.product_url if part else None,
                test_method=getattr(resolved, "method", None),
                test_detail=getattr(resolved, "detail", None),
                test_method_powered=powered_method,
                test_detail_powered=powered_detail,
                test_resolution_source=getattr(resolved, "source", None),
                test_resolution_message=getattr(resolved, "message", None),
            )
        )
    result.sort(key=lambda r: natural_key(r.reference))
    return result

from __future__ import annotations

from typing import List, Literal, Optional
import re
from collections import defaultdict

from pydantic import BaseModel
from sqlmodel import Session, select

from ..domain.test_resolution import resolve_test_for_bom_item
from ..models import Assembly, BOMItem, Part, PartTestMap, PartType

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
    resolved_profile: str | None = None
    resolution_reason: str | None = None
    resolved_test_id: int | None = None
    resolution_message: str | None = None


def get_joined_bom_for_assembly(session: Session, assembly_id: int) -> List[JoinedBOMRow]:
    """Return joined BOM items with part data for an assembly."""

    stmt = (
        select(BOMItem, Part)
        .join(Part, Part.id == BOMItem.part_id)
        .where(BOMItem.assembly_id == assembly_id)
    )
    rows = session.exec(stmt).all()
    assembly = session.get(Assembly, assembly_id)
    part_ids = {part.id for _item, part in rows}
    mappings: dict[int, list[PartTestMap]] = defaultdict(list)
    if part_ids:
        mapping_rows = session.exec(
            select(PartTestMap).where(PartTestMap.part_id.in_(part_ids))
        ).all()
        for mapping in mapping_rows:
            mappings[mapping.part_id].append(mapping)
    result: List[JoinedBOMRow] = []
    for item, part in rows:
        ap = part.active_passive.value if isinstance(part.active_passive, PartType) else part.active_passive
        resolved = resolve_test_for_bom_item(
            assembly,
            item,
            part,
            mappings.get(part.id, []),
        )
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
                resolved_profile=resolved.profile_used.value if resolved.profile_used else None,
                resolution_reason=resolved.reason.value,
                resolved_test_id=resolved.test_id,
                resolution_message=resolved.message,
            )
        )
    result.sort(key=lambda r: natural_key(r.reference))
    return result

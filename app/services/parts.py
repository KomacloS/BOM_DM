from __future__ import annotations

from typing import Literal

from sqlmodel import Session

from ..models import Part, PartType


def update_part_active_passive(
    session: Session, part_id: int, mode: Literal["active", "passive"]
) -> Part:
    """Update a part's active/passive classification."""

    if mode not in ("active", "passive"):
        raise ValueError("mode must be 'active' or 'passive'")
    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    part.active_passive = PartType(mode)
    session.add(part)
    session.commit()
    session.refresh(part)
    return part

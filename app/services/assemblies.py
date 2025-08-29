"""Assembly service helpers."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from ..models import Assembly, BOMItem, Part
from . import BOMItemRead


def list_assemblies(project_id: int, session: Session) -> List[Assembly]:
    """Return assemblies for the given project."""

    stmt = select(Assembly).where(Assembly.project_id == project_id)
    stmt = stmt.order_by(Assembly.created_at)
    try:
        return session.exec(stmt).all()
    except OperationalError as e:  # pragma: no cover - depends on DB schema
        raise RuntimeError(
            "Assemblies query failed; run 'python -m app.tools.db migrate'. Details: "
            f"{e}"
        ) from e


def list_bom_items(assembly_id: int, session: Session) -> List[BOMItemRead]:
    """Return BOM items for an assembly with the related ``part_number``."""

    stmt = (
        select(BOMItem, Part.part_number)
        .join(Part, Part.id == BOMItem.part_id, isouter=True)
        .where(BOMItem.assembly_id == assembly_id)
    )
    try:
        rows = session.exec(stmt).all()
        return [BOMItemRead(part_number=pn, **item.model_dump()) for item, pn in rows]
    except OperationalError as e:  # pragma: no cover - depends on DB schema
        raise RuntimeError(
            "BOM items query failed; run 'python -m app.tools.db migrate'. Details: "
            f"{e}"
        ) from e


def create_assembly(
    project_id: int, rev: str, notes: Optional[str], session: Session
) -> Assembly:
    """Create and persist a new assembly."""

    asm = Assembly(project_id=project_id, rev=rev, notes=notes)
    session.add(asm)
    session.commit()
    session.refresh(asm)
    return asm


def delete_assembly(assembly_id: int, session: Session) -> None:
    """Delete an assembly along with its BOM items."""

    asm = session.get(Assembly, assembly_id)
    if not asm:
        return

    session.exec(BOMItem.__table__.delete().where(BOMItem.assembly_id == assembly_id))
    session.delete(asm)
    session.commit()


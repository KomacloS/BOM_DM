"""Assembly service helpers."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from ..models import Assembly, BOMItem, Part


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


def list_bom_items(assembly_id: int, session: Session) -> List[BOMItem]:
    """Return BOM items for an assembly, adding ``part_number`` when available."""

    stmt = select(BOMItem).where(BOMItem.assembly_id == assembly_id)
    try:
        items = session.exec(stmt).all()
        for it in items:
            if it.part_id:
                part = session.get(Part, it.part_id)
                if part:
                    setattr(it, "part_number", part.part_number)
                else:
                    setattr(it, "part_number", None)
            else:
                setattr(it, "part_number", None)
        return items
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


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


def delete_bom_items(session: Session, bom_item_ids: List[int]) -> int:
    """Delete ``BOMItem`` rows with the given ids.

    Parameters
    ----------
    session:
        Active database session.
    bom_item_ids:
        List of ``BOMItem`` primary key ids to remove.

    Returns
    -------
    int
        Number of rows deleted.
    """

    if not bom_item_ids:
        return 0

    stmt = BOMItem.__table__.delete().where(BOMItem.id.in_(bom_item_ids))
    result = session.exec(stmt)
    session.commit()
    # ``rowcount`` is available on the SQLAlchemy result object; fall back to
    # ``0`` if the backend does not provide it for some reason.
    return int(getattr(result, "rowcount", 0) or 0)


def delete_bom_items_for_part(
    session: Session, assembly_id: int, part_id: int
) -> int:
    """Delete all ``BOMItem`` rows for ``assembly_id``/``part_id``.

    This is a convenience wrapper used when operating in the *By PN* view
    where a single row represents multiple references of the same part.

    Returns the number of rows deleted.
    """

    stmt = select(BOMItem.id).where(
        BOMItem.assembly_id == assembly_id, BOMItem.part_id == part_id
    )
    ids = list(session.exec(stmt))
    return delete_bom_items(session, ids)


def update_bom_item_manufacturer(session: Session, bom_item_id: int, manufacturer: str | None) -> BOMItem:
    """Update the ``manufacturer`` field for a single BOM item.

    Returns the updated ``BOMItem`` instance.
    """
    item = session.get(BOMItem, bom_item_id)
    if not item:
        raise ValueError(f"BOMItem {bom_item_id} not found")
    item.manufacturer = (manufacturer or None)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def update_manufacturer_for_part_in_assembly(
    session: Session, assembly_id: int, part_id: int, manufacturer: str | None
) -> int:
    """Set ``manufacturer`` for all BOM items of ``part_id`` within ``assembly_id``.

    Returns the number of rows updated.
    """
    # Use SQL expression update for efficiency
    stmt = BOMItem.__table__.update().where(
        (BOMItem.assembly_id == assembly_id) & (BOMItem.part_id == part_id)
    ).values(manufacturer=(manufacturer or None))
    result = session.exec(stmt)
    session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


"""Assembly service helpers."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from ..models import Assembly, BOMItem, Part, PartType, TestMode
from . import BOMItemRead
from .test_resolution import BOMTestResolver


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

    assembly = session.get(Assembly, assembly_id)
    if assembly is None:
        raise ValueError(f"Assembly {assembly_id} not found")

    raw_mode = assembly.test_mode or TestMode.unpowered
    assembly_mode = raw_mode if isinstance(raw_mode, TestMode) else _coerce_mode(raw_mode)

    stmt = select(BOMItem, Part).join(Part, Part.id == BOMItem.part_id, isouter=True).where(BOMItem.assembly_id == assembly_id)
    try:
        rows = session.exec(stmt).all()
    except OperationalError as e:  # pragma: no cover - depends on DB schema
        raise RuntimeError(
            "BOM items query failed; run 'python -m app.tools.db migrate'. Details: "
            f"{e}"
        ) from e

    resolver = BOMTestResolver.from_session(session, assembly_id, rows)
    result: List[BOMItemRead] = []
    for item, part in rows:
        resolved = resolver.resolve_effective_test(item.id, assembly_mode)
        part_number = part.part_number if part else None
        part_type = part.active_passive if part else None
        part_is_active = False
        if isinstance(part_type, PartType):
            part_is_active = part_type is PartType.active
        elif part_type is not None:
            try:
                part_is_active = PartType(str(part_type)) is PartType.active
            except ValueError:
                part_is_active = False

        powered_method = resolved.powered_method if (assembly_mode is TestMode.powered and part_is_active) else None
        powered_detail = resolved.powered_detail if (assembly_mode is TestMode.powered and part_is_active) else None
        payload = item.model_dump()
        payload.update(
            {
                "part_number": part_number,
                "test_method": resolved.method,
                "test_detail": resolved.detail,
                "test_method_powered": powered_method,
                "test_detail_powered": powered_detail,
                "test_resolution_source": resolved.source,
                "test_resolution_message": resolved.message,
            }
        )
        result.append(BOMItemRead(**payload))
    return result


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


def update_assembly_test_mode(session: Session, assembly_id: int, mode: TestMode | str) -> Assembly:
    """Persist a new test mode for the specified assembly."""

    asm = session.get(Assembly, assembly_id)
    if asm is None:
        raise ValueError(f"Assembly {assembly_id} not found")

    if isinstance(mode, str):
        try:
            mode = TestMode(mode)
        except ValueError:
            if mode == "non_powered":
                mode = TestMode.unpowered
            else:
                raise ValueError(f"Invalid test mode: {mode}")

    asm.test_mode = mode
    session.add(asm)
    session.commit()
    session.refresh(asm)
    return asm


def _coerce_mode(value: TestMode | str) -> TestMode:
    if isinstance(value, TestMode):
        return value
    return TestMode(str(value))


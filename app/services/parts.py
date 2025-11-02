from __future__ import annotations

from typing import Literal
from pathlib import Path
import os

from sqlalchemy import func, or_, update as sa_update, delete as sa_delete
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlmodel import Session, select

from ..models import BOMItem, Part, PartTestAssignment, PartType


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


def update_part_datasheet_url(session: Session, part_id: int, url_or_path: str) -> Part:
    """Update a part's datasheet URL/path."""
    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    part.datasheet_url = url_or_path
    session.add(part)
    session.commit()
    session.refresh(part)
    return part


def update_part_description_if_empty(session: Session, part_id: int, description: str | None) -> Part:
    """Set part description only if currently empty.

    Treats ``None`` or empty/whitespace-only as empty. Normalizes input and
    writes only when the part has no description yet.
    """
    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    new_desc = (description or "").strip()
    # Nothing useful to set
    if not new_desc:
        return part
    current = (part.description or "").strip()
    if current:
        # Already has a description; do not overwrite
        return part
    part.description = new_desc
    session.add(part)
    session.commit()
    session.refresh(part)
    return part


def update_part_description(session: Session, part_id: int, description: str | None) -> Part:
    """Update a part's description unconditionally.

    Normalizes empty/whitespace-only input to an empty string.
    """
    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    part.description = (description or "").strip()
    session.add(part)
    session.commit()
    session.refresh(part)
    return part


def update_part_product_url(session: Session, part_id: int, url: str | None) -> Part:
    """Update a part's product (device) page URL."""
    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    part.product_url = (url or None)
    session.add(part)
    session.commit()
    session.refresh(part)
    return part


def update_part_function(session: Session, part_id: int, function: str | None) -> Part:
    """Update a part's function classification string.

    Accepts None or empty string to clear the function.
    """
    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    # Normalize empty to None
    func_norm = (function or "").strip() or None
    part.function = func_norm
    session.add(part)
    session.commit()
    session.refresh(part)
    return part


def update_part_package(session: Session, part_id: int, package: str) -> Part:
    """Update a part's package string."""

    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    part.package = package
    session.add(part)
    session.commit()
    session.refresh(part)
    return part


def update_part_value(session: Session, part_id: int, value: str) -> Part:
    """Update a part's value string."""

    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    part.value = value
    session.add(part)
    session.commit()
    session.refresh(part)
    return part


def update_part_tolerances(
    session: Session, part_id: int, tol_p: str | None, tol_n: str | None
) -> Part:
    """Update a part's tolerance values."""

    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    part.tol_p = tol_p or None
    part.tol_n = tol_n or None
    session.add(part)
    session.commit()
    session.refresh(part)
    return part


def remove_part_datasheet(session: Session, part_id: int, delete_file: bool = True) -> Tuple[Part, bool]:
    """Clear the part's datasheet association and optionally delete the file.

    Returns (part, deleted_file).
    If the file is referenced by other parts, it is not deleted.
    """
    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    path = (part.datasheet_url or "").strip()
    # Clear association first
    part.datasheet_url = None
    session.add(part)
    session.commit()
    session.refresh(part)

    deleted = False
    if delete_file and path:
        # Check if other parts still reference this path
        from sqlmodel import select

        others = session.exec(select(Part.id).where(Part.datasheet_url == path)).first()
        if others is None:
            try:
                p = Path(path)
                if p.exists():
                    os.remove(p)
                    deleted = True
            except Exception:
                # Ignore file system errors; association is already cleared
                pass
    return part, deleted


def clear_part_datasheet(session: Session, part_id: int) -> Part:
    """Clear the datasheet association for a part.

    This is a thin wrapper around :func:`update_part_datasheet_url` that sets
    the ``datasheet_url`` field to ``None``.  The physical datasheet file is
    intentionally left untouched so that other parts may continue to reference
    it.
    """

    # ``update_part_datasheet_url`` performs validation and commits the change.
    return update_part_datasheet_url(session, part_id, None)


# ---------------------------------------------------------------------------
# CRUD helpers for the Parts terminal


SEARCHABLE_PART_COLUMNS = (
    Part.part_number,
    Part.description,
    Part.package,
    Part.value,
    Part.function,
    Part.datasheet_url,
    Part.product_url,
)


def create_part(session: Session, **fields) -> Part:
    """Create a new :class:`Part` enforcing unique part numbers."""

    allowed = {
        "part_number",
        "description",
        "package",
        "value",
        "function",
        "active_passive",
        "power_required",
        "datasheet_url",
        "product_url",
        "tol_p",
        "tol_n",
    }
    data = {k: v for k, v in fields.items() if k in allowed}
    part_number = (data.get("part_number") or "").strip()
    if not part_number:
        raise ValueError("part_number is required")
    data["part_number"] = part_number
    if "active_passive" in data and isinstance(data["active_passive"], str):
        data["active_passive"] = PartType(data["active_passive"])
    part = Part(**data)
    session.add(part)
    try:
        session.commit()
    except IntegrityError as exc:  # pragma: no cover - DB constraint detail
        session.rollback()
        _raise_unique_part_number(part_number)  # raises ValueError
    session.refresh(part)
    return part


def search_parts(session: Session, query: str | None, limit: int = 500) -> list[Part]:
    """Search for parts matching ``query`` across common attributes."""

    stmt = select(Part)
    if query:
        term = f"%{query.strip()}%"
        conditions = [col.ilike(term) for col in SEARCHABLE_PART_COLUMNS]
        stmt = stmt.where(or_(*conditions))
        stmt = stmt.order_by(Part.part_number)
    else:
        stmt = stmt.order_by(Part.created_at.desc(), Part.part_number)
    stmt = stmt.limit(limit)
    return list(session.exec(stmt))


def _raise_unique_part_number(part_number: str) -> None:
    raise ValueError(f"Part number '{part_number}' already exists.")


def update_part(session: Session, part_id: int, **fields) -> Part:
    """Update mutable fields for a part and enforce unique part numbers."""

    allowed = {
        "part_number",
        "description",
        "package",
        "value",
        "function",
        "active_passive",
        "power_required",
        "datasheet_url",
        "product_url",
        "tol_p",
        "tol_n",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")
    if not updates:
        return part
    if "part_number" in updates:
        new_number = (updates["part_number"] or "").strip()
        if not new_number:
            raise ValueError("part_number must not be empty")
        exists = session.exec(
            select(Part.id).where(Part.part_number == new_number, Part.id != part_id)
        ).first()
        if exists:
            _raise_unique_part_number(new_number)
        part.part_number = new_number
        updates.pop("part_number")
    for key, value in updates.items():
        if key == "active_passive" and isinstance(value, str):
            value = PartType(value)
        setattr(part, key, value)
    session.add(part)
    try:
        session.commit()
    except IntegrityError as exc:  # pragma: no cover - DB constraint detail
        session.rollback()
        if "part_number" in fields:
            _raise_unique_part_number(fields["part_number"])
        raise
    session.refresh(part)
    return part


def count_part_references(session: Session, part_id: int) -> int:
    """Return the number of BOM items referencing the part."""

    stmt = select(func.count()).select_from(BOMItem).where(BOMItem.part_id == part_id)
    result = session.exec(stmt)
    if hasattr(result, "scalar_one"):
        return int(result.scalar_one())
    row = result.one()
    # SA <1.4 returns a Row/tuple; first element is the count
    if isinstance(row, (tuple, list)):
        value = row[0]
    else:
        value = row
    return int(value)


def unlink_part_from_boms(session: Session, part_id: int) -> int:
    """Clear BOM item ``part_id`` references for the given part."""

    result = session.exec(
        sa_update(BOMItem).where(BOMItem.part_id == part_id).values(part_id=None)
    )
    return result.rowcount or 0


def delete_part(session: Session, part_id: int, mode: str = "block") -> None:
    """Delete a part respecting reference safety rules."""

    part = session.get(Part, part_id)
    if part is None:
        raise ValueError(f"Part {part_id} not found")

    if mode == "block":
        refs = count_part_references(session, part_id)
        if refs:
            raise RuntimeError(f"Part is referenced by {refs} BOM items")
        session.delete(part)
        session.commit()
        return
    if mode == "unlink_then_delete":
        try:
            transaction = session.begin()
        except InvalidRequestError:
            transaction = session.begin_nested()
        with transaction:
            unlink_part_from_boms(session, part_id)
            session.exec(
                sa_delete(PartTestAssignment).where(PartTestAssignment.part_id == part_id)
            )
            session.delete(part)
        return
    raise ValueError("mode must be 'block' or 'unlink_then_delete'")

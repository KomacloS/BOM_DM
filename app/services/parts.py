from __future__ import annotations

from typing import Literal, Tuple
from pathlib import Path
import os

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

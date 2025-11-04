from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from ..models import PartTestMap, TestMacro, PythonTest, TestMode, TestProfile


def _ensure_profile(profile: TestProfile | str) -> TestProfile:
    if isinstance(profile, TestProfile):
        return profile
    return TestProfile(str(profile))


def upsert_test_macro(session: Session, name: str) -> TestMacro:
    """Return a ``TestMacro`` with ``name``, creating it if missing."""

    canonical = (name or "").strip()
    if not canonical:
        raise ValueError("Macro name is required")
    stmt = select(TestMacro).where(TestMacro.name == canonical)
    existing = session.exec(stmt).first()
    if existing:
        return existing
    macro = TestMacro(name=canonical)
    session.add(macro)
    session.flush()
    return macro


def upsert_python_test(session: Session, name: str, file_path: Optional[str] = None) -> PythonTest:
    """Return a ``PythonTest`` row matching ``name``/``file_path``."""

    canonical = (name or "").strip() or "Python test"
    normalized_path = (Path(file_path).as_posix() if file_path else None)

    stmt = select(PythonTest).where(PythonTest.name == canonical)
    if normalized_path:
        stmt = stmt.where(PythonTest.file_path == normalized_path)
    existing = session.exec(stmt).first()
    if existing:
        if normalized_path and existing.file_path != normalized_path:
            existing.file_path = normalized_path
            session.add(existing)
            session.flush()
        return existing

    record = PythonTest(name=canonical, file_path=normalized_path)
    session.add(record)
    session.flush()
    return record


def remove_part_test_map(session: Session, part_id: int, power_mode: TestMode, profile: TestProfile | str) -> None:
    """Delete the ``PartTestMap`` record for (part_id, power_mode, profile) if it exists."""

    profile_enum = _ensure_profile(profile)
    stmt = (
        select(PartTestMap)
        .where(PartTestMap.part_id == part_id)
        .where(PartTestMap.power_mode == power_mode)
        .where(PartTestMap.profile == profile_enum)
    )
    mapping = session.exec(stmt).first()
    if mapping is not None:
        session.delete(mapping)
        session.flush()


def save_part_test_map(
    session: Session,
    *,
    part_id: int,
    power_mode: TestMode,
    profile: TestProfile | str,
    method: str,
    detail: Optional[str] = None,
    quick_test_path: Optional[str] = None,
) -> PartTestMap:
    """Create/update a ``PartTestMap`` entry for ``part_id``."""

    profile_enum = _ensure_profile(profile)
    normalized_method = (method or "").strip()
    normalized_detail = (detail or "").strip() or None
    normalized_qt_path = (Path(quick_test_path).as_posix() if quick_test_path else None)

    stmt = (
        select(PartTestMap)
        .where(PartTestMap.part_id == part_id)
        .where(PartTestMap.power_mode == power_mode)
        .where(PartTestMap.profile == profile_enum)
    )
    mapping = session.exec(stmt).first()
    if mapping is None:
        mapping = PartTestMap(part_id=part_id, power_mode=power_mode, profile=profile_enum)

    mapping.detail = normalized_detail

    if normalized_method == "Macro":
        macro_name = normalized_detail or ""
        macro = upsert_test_macro(session, macro_name)
        mapping.test_macro_id = macro.id
        mapping.python_test_id = None
    elif normalized_method in {"Python code", "Quick test (QT)"}:
        if normalized_method == "Quick test (QT)":
            # Fallback to file name for a reasonable identifier
            if normalized_detail:
                python_name = normalized_detail
            elif normalized_qt_path:
                python_name = Path(normalized_qt_path).stem or "Quick test"
            else:
                python_name = "Quick test"
        else:
            python_name = normalized_detail or "Python code"
        python_record = upsert_python_test(
            session,
            python_name,
            file_path=normalized_qt_path,
        )
        mapping.python_test_id = python_record.id
        mapping.test_macro_id = None
        # Store quick test path in detail when no explicit detail was provided
        if normalized_method == "Quick test (QT)" and not normalized_detail and normalized_qt_path:
            mapping.detail = normalized_qt_path
    else:
        raise ValueError(f"Unsupported method for DB persistence: {normalized_method!r}")

    session.add(mapping)
    session.flush()
    return mapping

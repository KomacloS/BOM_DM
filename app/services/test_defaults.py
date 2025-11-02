"""Helpers for persisting default test selections to the database."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from sqlmodel import Session, select

from ..models import PartTestMap, PythonTest, TestMacro, TestMode, TestProfile

MethodLiteral = Literal["Macro", "Python code", "Quick test (QT)"]


def _normalize(text: Optional[str]) -> str:
    """Collapse whitespace and strip text for stable comparisons."""

    if text is None:
        return ""
    return " ".join(text.split())


def upsert_test_macro(session: Session, name: str) -> TestMacro:
    """Return an existing :class:`TestMacro` or create one if missing."""

    clean_name = _normalize(name)
    if not clean_name:
        raise ValueError("Macro name must not be empty")

    stmt = select(TestMacro).where(TestMacro.name == clean_name)
    existing = session.exec(stmt).first()
    if existing:
        return existing

    macro = TestMacro(name=clean_name)
    session.add(macro)
    session.flush()
    session.refresh(macro)
    return macro


def upsert_python_test(
    session: Session, name: str, file_path: Optional[str]
) -> PythonTest:
    """Return an existing :class:`PythonTest` or create one if missing."""

    clean_name = _normalize(name)
    if not clean_name:
        raise ValueError("Python test name must not be empty")

    stmt = select(PythonTest).where(PythonTest.name == clean_name)
    python_test = session.exec(stmt).first()
    normalized_path = file_path.strip() if isinstance(file_path, str) else None

    if python_test:
        if normalized_path and python_test.file_path != normalized_path:
            python_test.file_path = normalized_path
            session.add(python_test)
        return python_test

    python_test = PythonTest(name=clean_name, file_path=normalized_path or None)
    session.add(python_test)
    session.flush()
    session.refresh(python_test)
    return python_test


def upsert_part_test_map(
    session: Session,
    part_id: int,
    power_mode: TestMode,
    profile: TestProfile,
    method: MethodLiteral,
    detail: Optional[str],
    qt_path: Optional[str] = None,
) -> PartTestMap:
    """Create or update a :class:`PartTestMap` for the provided selection."""

    detail_norm = detail.strip() if isinstance(detail, str) else None
    python_test_id: int | None = None
    test_macro_id: int | None = None

    if method == "Macro":
        if not detail_norm:
            raise ValueError("Macro detail is required for Macro mappings")
        macro = upsert_test_macro(session, detail_norm)
        test_macro_id = macro.id
    elif method == "Python code":
        if not detail_norm:
            raise ValueError("Detail is required for Python code mappings")
        python = upsert_python_test(session, detail_norm, None)
        python_test_id = python.id
    elif method == "Quick test (QT)":
        qt_file = qt_path.strip() if isinstance(qt_path, str) else ""
        if not qt_file:
            raise ValueError("Quick test requires a valid XML path")
        python = upsert_python_test(session, "Quick test", qt_file)
        python_test_id = python.id
        if not detail_norm:
            detail_norm = Path(qt_file).name
    else:  # pragma: no cover - defensive
        raise ValueError(f"Unsupported test method: {method}")

    stmt = select(PartTestMap).where(
        PartTestMap.part_id == part_id,
        PartTestMap.power_mode == power_mode,
        PartTestMap.profile == profile,
    )
    mapping = session.exec(stmt).first()

    if mapping is None:
        mapping = PartTestMap(
            part_id=part_id,
            power_mode=power_mode,
            profile=profile,
            test_macro_id=test_macro_id,
            python_test_id=python_test_id,
            detail=detail_norm,
        )
        session.add(mapping)
        session.flush()
        session.refresh(mapping)
        return mapping

    mapping.test_macro_id = test_macro_id
    mapping.python_test_id = python_test_id
    mapping.detail = detail_norm
    session.add(mapping)
    return mapping


__all__ = [
    "upsert_test_macro",
    "upsert_python_test",
    "upsert_part_test_map",
]


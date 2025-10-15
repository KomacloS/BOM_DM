from __future__ import annotations

from datetime import datetime
import io
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import get_current_user
from ..config import REPO_ROOT
from ..database import get_session
from ..models import Part, PartTestAssignment, TestMethod, User
from ..services import test_assets


router = APIRouter(prefix="/tests", tags=["tests"])


class TestAssignmentRequest(BaseModel):
    part_number: str
    method: TestMethod
    notes: Optional[str] = None


class TestDetailResponse(BaseModel):
    part_number: str
    method: TestMethod
    python_folder_rel: Optional[str] = None
    python_folder_abs: Optional[str] = None
    quicktest_rel: Optional[str] = None
    quicktest_abs: Optional[str] = None
    has_folder: bool = False
    has_file: bool = False
    notes: Optional[str] = None


class QuickTestWriteRequest(BaseModel):
    content: str


class QuickTestReadResponse(BaseModel):
    content: str
    created: bool


class QuickTestWriteResponse(BaseModel):
    saved: bool
    bytes_written: int
    rel_path: str


def _validate_and_fetch_part(session: Session, pn: str) -> tuple[Part, str]:
    try:
        validated = test_assets.validate_part_number(pn)
    except ValueError as exc:  # pragma: no cover - error path
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    part = session.exec(select(Part).where(Part.part_number == pn)).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    return part, validated


def _relpath(path: str | None) -> Optional[str]:
    if not path:
        return None
    return os.path.relpath(path, REPO_ROOT)


def _build_detail(
    part_number: str, assignment: PartTestAssignment, validated: str
) -> TestDetailResponse:
    python_folder = None
    quicktest_path = None
    has_folder = False
    has_file = False

    if assignment.method == TestMethod.python:
        try:
            test_assets.ensure_python_folder_for_pn(validated)
            python_folder = test_assets.python_folder_path(validated)
            has_folder = True
        except ValueError:
            python_folder = None
    else:
        if test_assets.python_folder_exists(validated):
            python_folder = test_assets.python_folder_path(validated)
            has_folder = True

    try:
        quicktest_path = test_assets.quicktest_path_for_pn(validated)
        has_file = os.path.exists(quicktest_path)
    except ValueError:
        quicktest_path = None

    return TestDetailResponse(
        part_number=part_number,
        method=assignment.method,
        python_folder_rel=_relpath(python_folder),
        python_folder_abs=python_folder,
        quicktest_rel=_relpath(quicktest_path),
        quicktest_abs=quicktest_path,
        has_folder=has_folder and test_assets.python_folder_exists(validated),
        has_file=has_file,
        notes=assignment.notes,
    )


@router.post("/assign", response_model=TestDetailResponse)
def assign_test_method(
    payload: TestAssignmentRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TestDetailResponse:
    part, validated = _validate_and_fetch_part(session, payload.part_number)

    test_assets.ensure_base_dirs()

    assignment = session.get(PartTestAssignment, part.id)
    if assignment is None:
        assignment = PartTestAssignment(part_id=part.id)
        session.add(assignment)

    assignment.method = payload.method
    assignment.notes = payload.notes
    assignment.updated_at = datetime.utcnow()

    if payload.method == TestMethod.python:
        test_assets.ensure_python_folder_for_pn(validated)
    elif payload.method == TestMethod.quick_test:
        test_assets.read_quicktest(validated)

    session.commit()
    session.refresh(assignment)
    return _build_detail(part.part_number, assignment, validated)


@router.get("/{part_number}/detail", response_model=TestDetailResponse)
def test_detail(
    part_number: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TestDetailResponse:
    part, validated = _validate_and_fetch_part(session, part_number)
    assignment = session.get(PartTestAssignment, part.id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="Test method not assigned")
    return _build_detail(part.part_number, assignment, validated)


@router.get("/{part_number}/python/zip")
def download_python_zip(
    part_number: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    part, validated = _validate_and_fetch_part(session, part_number)
    assignment = session.get(PartTestAssignment, part.id)
    if assignment is None or assignment.method != TestMethod.python:
        raise HTTPException(status_code=404, detail="Python test not assigned")
    try:
        data = test_assets.zip_python_folder(validated)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Python test folder missing")
    filename = f"{validated}_python_tests.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/{part_number}/quicktest/read", response_model=QuickTestReadResponse)
def quicktest_read(
    part_number: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QuickTestReadResponse:
    part, validated = _validate_and_fetch_part(session, part_number)
    assignment = session.get(PartTestAssignment, part.id)
    if assignment is None or assignment.method != TestMethod.quick_test:
        raise HTTPException(status_code=404, detail="Quick Test not assigned")
    content, created = test_assets.read_quicktest(validated)
    return QuickTestReadResponse(content=content, created=created)


@router.post("/{part_number}/quicktest/write", response_model=QuickTestWriteResponse)
def quicktest_write(
    part_number: str,
    payload: QuickTestWriteRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QuickTestWriteResponse:
    part, validated = _validate_and_fetch_part(session, part_number)
    assignment = session.get(PartTestAssignment, part.id)
    if assignment is None or assignment.method != TestMethod.quick_test:
        raise HTTPException(status_code=404, detail="Quick Test not assigned")
    path = test_assets.write_quicktest(validated, payload.content)
    bytes_written = len(payload.content.encode("utf-8"))
    return QuickTestWriteResponse(saved=True, bytes_written=bytes_written, rel_path=_relpath(path) or "")


@router.post("/{part_number}/reveal")
def reveal_python_folder(
    part_number: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    part, validated = _validate_and_fetch_part(session, part_number)
    assignment = session.get(PartTestAssignment, part.id)
    if assignment is None or assignment.method != TestMethod.python:
        raise HTTPException(status_code=404, detail="Python test not assigned")
    raise HTTPException(status_code=400, detail="Reveal path is only available in the desktop app")


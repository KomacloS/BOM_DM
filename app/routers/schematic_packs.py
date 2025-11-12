from __future__ import annotations

from datetime import datetime
from typing import Iterable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from .. import config
from ..auth import get_current_user
from ..database import get_session
from ..models import SchematicFile, SchematicIndexSource, SchematicOcrStatus, User
from ..services import (
    SchematicFileInfo,
    SchematicPackInfo,
    add_schematic_files_from_uploads,
    create_schematic_pack as svc_create_schematic_pack,
    get_pack_detail as svc_get_pack_detail,
    list_schematic_packs as svc_list_schematic_packs,
    mark_schematic_file_reindexed,
    reorder_schematic_files,
)


router = APIRouter(tags=["schematic-packs"])


class PackCreate(BaseModel):
    display_name: str = Field(min_length=1)


class PackCreateResponse(BaseModel):
    pack_id: int


class PackSummary(BaseModel):
    id: int
    assembly_id: int
    display_name: str
    pack_revision: int
    created_at: datetime
    updated_at: datetime


class FileRecord(BaseModel):
    id: int
    pack_id: int
    file_order: int
    relative_path: str
    page_count: int
    has_text_layer: bool
    ocr_status: SchematicOcrStatus | str
    last_indexed_at: datetime | None


class PackDetail(PackSummary):
    files: list[FileRecord]


class ReorderPayload(BaseModel):
    file_ids: list[int]


class SearchResult(BaseModel):
    file_id: int
    file_name: str
    page: int
    boxes: list[dict]
    source: SchematicIndexSource | str
    token: str | None = None


class OverlayResponse(BaseModel):
    boxes: list[dict]
    transform: dict | None = None


def _serialize_file_info(info: SchematicFileInfo) -> FileRecord:
    return FileRecord(
        id=info.id,
        pack_id=info.pack_id,
        file_order=info.file_order,
        relative_path=info.relative_path,
        page_count=info.page_count,
        has_text_layer=info.has_text_layer,
        ocr_status=info.ocr_status,
        last_indexed_at=info.last_indexed_at,
    )


def _serialize_pack_info(info: SchematicPackInfo) -> PackDetail:
    return PackDetail(
        id=info.id,
        assembly_id=info.assembly_id,
        display_name=info.display_name,
        pack_revision=info.pack_revision,
        created_at=info.created_at,
        updated_at=info.updated_at,
        files=sorted((_serialize_file_info(f) for f in info.files), key=lambda f: f.file_order),
    )


@router.post("/assemblies/{assembly_id}/schematic-packs", response_model=PackCreateResponse)
def create_pack(
    assembly_id: int,
    payload: PackCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    try:
        pack = svc_create_schematic_pack(session, assembly_id, payload.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PackCreateResponse(pack_id=pack.id)


@router.get("/assemblies/{assembly_id}/schematic-packs", response_model=list[PackSummary])
def list_packs(
    assembly_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    try:
        packs = svc_list_schematic_packs(session, assembly_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        PackSummary(
            id=p.id,
            assembly_id=p.assembly_id,
            display_name=p.display_name,
            pack_revision=p.pack_revision,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in packs
    ]


@router.post("/schematic-packs/{pack_id}/files", response_model=list[FileRecord])
async def upload_files(
    pack_id: int,
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    try:
        infos = add_schematic_files_from_uploads(session, pack_id, files)
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    return [_serialize_file_info(info) for info in infos]


@router.post("/schematic-packs/{pack_id}/reorder")
def reorder_files(
    pack_id: int,
    payload: ReorderPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    try:
        pack = reorder_schematic_files(session, pack_id, payload.file_ids)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    return {"pack_revision": pack.pack_revision}


@router.get("/schematic-packs/{pack_id}", response_model=PackDetail)
def get_pack_detail(
    pack_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    pack = svc_get_pack_detail(session, pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Pack not found")
    return _serialize_pack_info(pack)


@router.get("/schematic-packs/{pack_id}/search", response_model=list[SearchResult])
def search_pack(
    pack_id: int,
    q: str,
    mode: str = "refdes",
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    pack = svc_get_pack_detail(session, pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Pack not found")
    return []


@router.get("/schematic-files/{file_id}/page/{page}/overlays", response_model=OverlayResponse)
def page_overlays(
    file_id: int,
    page: int,
    q: str | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    file = session.get(SchematicFile, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    return OverlayResponse(boxes=[], transform=None)


@router.get("/schematic-files/{file_id}/stream")
def stream_file(
    file_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    file = session.get(SchematicFile, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    path = (config.DATA_ROOT / file.relative_path).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF not found on disk")
    return FileResponse(path, media_type="application/pdf")


@router.post("/schematic-files/{file_id}/reindex")
def reindex_file(
    file_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    try:
        mark_schematic_file_reindexed(session, file_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "queued", "file_id": file_id}

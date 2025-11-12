from __future__ import annotations

from datetime import datetime
from typing import Iterable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from .. import config
from ..auth import get_current_user
from ..database import get_session
from ..models import (
    Assembly,
    SchematicFile,
    SchematicIndexSource,
    SchematicOcrStatus,
    SchematicPack,
    User,
)
from ..services import schematic_storage


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


def _get_pack(session: Session, pack_id: int) -> SchematicPack:
    pack = session.get(SchematicPack, pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")
    return pack


def _ensure_assembly(session: Session, assembly_id: int) -> Assembly:
    assembly = session.get(Assembly, assembly_id)
    if not assembly:
        raise HTTPException(status_code=404, detail="Assembly not found")
    return assembly


def _serialize_file(file: SchematicFile) -> FileRecord:
    return FileRecord(
        id=file.id,
        pack_id=file.pack_id,
        file_order=file.file_order,
        relative_path=file.relative_path,
        page_count=file.page_count,
        has_text_layer=file.has_text_layer,
        ocr_status=file.ocr_status,
        last_indexed_at=file.last_indexed_at,
    )


def _serialize_pack(pack: SchematicPack, files: Iterable[SchematicFile]) -> PackDetail:
    return PackDetail(
        id=pack.id,
        assembly_id=pack.assembly_id,
        display_name=pack.display_name,
        pack_revision=pack.pack_revision,
        created_at=pack.created_at,
        updated_at=pack.updated_at,
        files=sorted((_serialize_file(f) for f in files), key=lambda f: f.file_order),
    )


@router.post("/assemblies/{assembly_id}/schematic-packs", response_model=PackCreateResponse)
def create_pack(
    assembly_id: int,
    payload: PackCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    assembly = _ensure_assembly(session, assembly_id)
    pack = SchematicPack(assembly_id=assembly.id, display_name=payload.display_name.strip())
    session.add(pack)
    session.commit()
    session.refresh(pack)
    schematic_storage.ensure_files_dir(assembly, pack)
    return PackCreateResponse(pack_id=pack.id)


@router.get("/assemblies/{assembly_id}/schematic-packs", response_model=list[PackSummary])
def list_packs(
    assembly_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    _ensure_assembly(session, assembly_id)
    packs = session.exec(
        select(SchematicPack).where(SchematicPack.assembly_id == assembly_id)
    ).all()
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
    pack = _get_pack(session, pack_id)
    assembly = _ensure_assembly(session, pack.assembly_id)
    max_order = session.exec(
        select(func.max(SchematicFile.file_order)).where(SchematicFile.pack_id == pack_id)
    ).first()
    next_order = (max_order or 0) + 1
    stored_files: list[SchematicFile] = []
    for upload in files:
        stored = schematic_storage.store_upload(pack, assembly, upload)
        record = SchematicFile(
            pack_id=pack.id,
            file_order=next_order,
            relative_path=stored.relative_path.as_posix(),
            page_count=stored.page_count,
            has_text_layer=stored.has_text_layer,
            ocr_status=(
                SchematicOcrStatus.completed if stored.has_text_layer else SchematicOcrStatus.pending
            ),
        )
        session.add(record)
        stored_files.append(record)
        next_order += 1
    pack.pack_revision += 1
    schematic_storage.update_pack_timestamp(pack)
    session.commit()
    for record in stored_files:
        session.refresh(record)
    return [_serialize_file(record) for record in stored_files]


@router.post("/schematic-packs/{pack_id}/reorder")
def reorder_files(
    pack_id: int,
    payload: ReorderPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    pack = _get_pack(session, pack_id)
    files = session.exec(select(SchematicFile).where(SchematicFile.pack_id == pack_id)).all()
    file_map = {file.id: file for file in files}
    if set(payload.file_ids) != set(file_map.keys()):
        raise HTTPException(status_code=400, detail="File IDs do not match pack contents")
    offset = len(payload.file_ids)
    for order, file_id in enumerate(payload.file_ids, start=1):
        file_map[file_id].file_order = order + offset
    session.flush()
    for order, file_id in enumerate(payload.file_ids, start=1):
        file_map[file_id].file_order = order
    pack.pack_revision += 1
    schematic_storage.update_pack_timestamp(pack)
    session.commit()
    return {"pack_revision": pack.pack_revision}


@router.get("/schematic-packs/{pack_id}", response_model=PackDetail)
def get_pack_detail(
    pack_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    pack = _get_pack(session, pack_id)
    files = session.exec(
        select(SchematicFile).where(SchematicFile.pack_id == pack_id).order_by(SchematicFile.file_order)
    ).all()
    return _serialize_pack(pack, files)


@router.get("/schematic-packs/{pack_id}/search", response_model=list[SearchResult])
def search_pack(
    pack_id: int,
    q: str,
    mode: str = "refdes",
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    _get_pack(session, pack_id)
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
    file = session.get(SchematicFile, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    file.last_indexed_at = datetime.utcnow()
    session.add(file)
    session.commit()
    return {"status": "queued", "file_id": file_id}

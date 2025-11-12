from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from sqlmodel import Session, select
from sqlalchemy import func

from .. import config
from ..models import (
    Assembly,
    SchematicFile,
    SchematicOcrStatus,
    SchematicPack,
)
from .schematic_storage import (
    ensure_files_dir,
    pack_root,
    remove_stored_file,
    store_local_path,
    store_upload,
    reassign_file_orders,
    update_pack_timestamp,
)


@dataclass(slots=True)
class SchematicFileInfo:
    """Lightweight description of a schematic file in a pack."""

    id: int
    pack_id: int
    file_order: int
    file_name: str
    relative_path: str
    absolute_path: Path
    page_count: int
    has_text_layer: bool
    ocr_status: SchematicOcrStatus
    last_indexed_at: datetime | None
    exists: bool


@dataclass(slots=True)
class SchematicPackInfo:
    """Schematic pack with its files."""

    id: int
    assembly_id: int
    display_name: str
    pack_revision: int
    created_at: datetime
    updated_at: datetime
    files: List[SchematicFileInfo]


def _file_info(file: SchematicFile) -> SchematicFileInfo:
    absolute = (config.DATA_ROOT / file.relative_path).resolve()
    return SchematicFileInfo(
        id=file.id or 0,
        pack_id=file.pack_id,
        file_order=file.file_order,
        file_name=Path(file.relative_path).name,
        relative_path=file.relative_path,
        absolute_path=absolute,
        page_count=file.page_count,
        has_text_layer=file.has_text_layer,
        ocr_status=file.ocr_status,
        last_indexed_at=file.last_indexed_at,
        exists=absolute.exists(),
    )


def _pack_files(session: Session, pack: SchematicPack) -> List[SchematicFileInfo]:
    files = session.exec(
        select(SchematicFile)
        .where(SchematicFile.pack_id == pack.id)
        .order_by(SchematicFile.file_order)
    ).all()
    return [_file_info(f) for f in files]


def list_schematic_packs(session: Session, assembly_id: int) -> List[SchematicPackInfo]:
    """Return schematic packs for ``assembly_id`` with their files."""

    _ensure_assembly(session, assembly_id)
    packs = session.exec(
        select(SchematicPack).where(SchematicPack.assembly_id == assembly_id).order_by(SchematicPack.created_at)
    ).all()
    return [
        SchematicPackInfo(
            id=pack.id or 0,
            assembly_id=pack.assembly_id,
            display_name=pack.display_name,
            pack_revision=pack.pack_revision,
            created_at=pack.created_at,
            updated_at=pack.updated_at,
            files=_pack_files(session, pack),
        )
        for pack in packs
    ]


def create_schematic_pack(session: Session, assembly_id: int, display_name: str) -> SchematicPack:
    """Create a schematic pack for an assembly."""

    assembly = _ensure_assembly(session, assembly_id)
    pack = SchematicPack(assembly_id=assembly_id, display_name=display_name.strip() or "Schematics")
    session.add(pack)
    session.commit()
    session.refresh(pack)
    ensure_files_dir(assembly, pack)
    return pack


def _ensure_pack(session: Session, pack_id: int) -> SchematicPack:
    pack = session.get(SchematicPack, pack_id)
    if pack is None:
        raise ValueError(f"Pack {pack_id} not found")
    return pack


def _ensure_assembly(session: Session, assembly_id: int) -> Assembly:
    assembly = session.get(Assembly, assembly_id)
    if assembly is None:
        raise ValueError(f"Assembly {assembly_id} not found")
    return assembly


def _register_stored_files(
    session: Session,
    pack: SchematicPack,
    stored_files: Iterable["StoredFile"],
) -> List[SchematicFileInfo]:
    stored_list = list(stored_files)
    if not stored_list:
        return []

    max_order = (
        session.exec(
            select(func.max(SchematicFile.file_order)).where(SchematicFile.pack_id == pack.id)
        ).first()
        or 0
    )

    records: list[SchematicFile] = []
    for stored in stored_list:
        max_order += 1
        record = SchematicFile(
            pack_id=pack.id,
            file_order=max_order,
            relative_path=stored.relative_path.as_posix(),
            page_count=stored.page_count,
            has_text_layer=stored.has_text_layer,
            ocr_status=(
                SchematicOcrStatus.completed if stored.has_text_layer else SchematicOcrStatus.pending
            ),
        )
        session.add(record)
        records.append(record)

    pack.pack_revision += 1
    update_pack_timestamp(pack)
    session.add(pack)
    session.commit()

    for record in records:
        session.refresh(record)
    return [_file_info(record) for record in records]


def get_pack_detail(session: Session, pack_id: int) -> Optional[SchematicPackInfo]:
    pack = session.get(SchematicPack, pack_id)
    if pack is None:
        return None
    return SchematicPackInfo(
        id=pack.id or 0,
        assembly_id=pack.assembly_id,
        display_name=pack.display_name,
        pack_revision=pack.pack_revision,
        created_at=pack.created_at,
        updated_at=pack.updated_at,
        files=_pack_files(session, pack),
    )


def rename_schematic_pack(session: Session, pack_id: int, display_name: str) -> SchematicPack:
    pack = session.get(SchematicPack, pack_id)
    if pack is None:
        raise ValueError(f"Pack {pack_id} not found")
    pack.display_name = display_name.strip() or pack.display_name
    update_pack_timestamp(pack)
    session.add(pack)
    session.commit()
    session.refresh(pack)
    return pack


def add_schematic_file_from_path(
    session: Session,
    pack_id: int,
    source_path: Path,
) -> SchematicFileInfo:
    pack = _ensure_pack(session, pack_id)
    assembly = _ensure_assembly(session, pack.assembly_id)

    stored = store_local_path(pack, assembly, Path(source_path))
    infos = _register_stored_files(session, pack, [stored])
    return infos[0]


def add_schematic_files_from_uploads(
    session: Session,
    pack_id: int,
    uploads: Iterable[object],
) -> List[SchematicFileInfo]:
    pack = _ensure_pack(session, pack_id)
    assembly = _ensure_assembly(session, pack.assembly_id)
    stored_files = [store_upload(pack, assembly, upload) for upload in uploads]
    return _register_stored_files(session, pack, stored_files)


def replace_schematic_file_from_path(
    session: Session,
    file_id: int,
    source_path: Path,
) -> SchematicFileInfo:
    record = session.get(SchematicFile, file_id)
    if record is None:
        raise ValueError(f"Schematic file {file_id} not found")
    pack = session.get(SchematicPack, record.pack_id)
    if pack is None:
        raise ValueError(f"Pack {record.pack_id} not found")
    assembly = session.get(Assembly, pack.assembly_id)
    if assembly is None:
        raise ValueError(f"Assembly {pack.assembly_id} not found")

    old_relative = record.relative_path
    stored = store_local_path(pack, assembly, Path(source_path))
    record.relative_path = stored.relative_path.as_posix()
    record.page_count = stored.page_count
    record.has_text_layer = stored.has_text_layer
    record.ocr_status = (
        SchematicOcrStatus.completed if stored.has_text_layer else SchematicOcrStatus.pending
    )
    pack.pack_revision += 1
    update_pack_timestamp(pack)
    session.add(record)
    session.commit()
    session.refresh(record)
    if old_relative and old_relative != record.relative_path:
        remove_stored_file(old_relative)
    return _file_info(record)


def reorder_schematic_files(
    session: Session,
    pack_id: int,
    file_ids: Sequence[int],
) -> SchematicPack:
    pack = _ensure_pack(session, pack_id)
    files = session.exec(select(SchematicFile).where(SchematicFile.pack_id == pack.id)).all()
    file_map = {file.id: file for file in files}
    if set(file_ids) != set(file_map.keys()):
        raise ValueError("File IDs do not match pack contents")

    offset = len(file_ids)
    for order, file_id in enumerate(file_ids, start=1):
        file_map[file_id].file_order = order + offset
    session.flush()
    for order, file_id in enumerate(file_ids, start=1):
        file_map[file_id].file_order = order

    pack.pack_revision += 1
    update_pack_timestamp(pack)
    session.add(pack)
    session.commit()
    return pack


def mark_schematic_file_reindexed(session: Session, file_id: int) -> SchematicFileInfo:
    record = session.get(SchematicFile, file_id)
    if record is None:
        raise ValueError(f"Schematic file {file_id} not found")
    record.last_indexed_at = datetime.utcnow()
    session.add(record)
    session.commit()
    session.refresh(record)
    return _file_info(record)


def remove_schematic_file(session: Session, file_id: int) -> None:
    record = session.get(SchematicFile, file_id)
    if record is None:
        return
    pack = session.get(SchematicPack, record.pack_id)
    assembly = session.get(Assembly, pack.assembly_id) if pack else None
    old_relative = record.relative_path

    session.delete(record)
    session.flush()

    if pack is not None:
        files = session.exec(select(SchematicFile).where(SchematicFile.pack_id == pack.id)).all()
        reassign_file_orders(files)
        pack.pack_revision += 1
        update_pack_timestamp(pack)
        session.add(pack)

    session.commit()
    if old_relative:
        remove_stored_file(old_relative)

    if pack is not None and assembly is not None:
        root = pack_root(assembly, pack)
        files_dir = root / "files"
        try:
            if files_dir.exists() and not any(files_dir.iterdir()):
                files_dir.rmdir()
        except OSError:
            pass


__all__ = [
    "SchematicFileInfo",
    "SchematicPackInfo",
    "list_schematic_packs",
    "create_schematic_pack",
    "get_pack_detail",
    "rename_schematic_pack",
    "add_schematic_file_from_path",
    "add_schematic_files_from_uploads",
    "replace_schematic_file_from_path",
    "remove_schematic_file",
    "reorder_schematic_files",
    "mark_schematic_file_reindexed",
]

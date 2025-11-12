"""Filesystem helpers for schematic packs.

The helpers in this module centralise the folder layout for schematic packs
and files to ensure consistent relative paths are persisted in the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import shutil
from typing import Iterable

try:  # PyMuPDF is an optional dependency but part of the default stack
    import fitz  # type: ignore
except Exception:  # pragma: no cover - dependency optional in some environments
    fitz = None  # type: ignore

from .. import config
from ..models import Assembly, SchematicFile, SchematicPack

_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class StoredFile:
    """Return value describing a stored PDF file."""

    path: Path
    relative_path: Path
    page_count: int
    has_text_layer: bool


def _slugify(value: str, *, fallback: str) -> str:
    cleaned = _INVALID_CHARS.sub("-", value.strip().lower())
    cleaned = cleaned.strip("-")
    return cleaned or fallback


def assembly_slug(assembly: Assembly) -> str:
    suffix = _slugify(assembly.rev or "assembly", fallback="assembly")
    return f"assembly-{assembly.id}-{suffix}"


def pack_slug(pack: SchematicPack) -> str:
    name_part = _slugify(pack.display_name, fallback="pack")
    return f"{name_part}-{pack.id}"


def pack_root(assembly: Assembly, pack: SchematicPack) -> Path:
    return (
        config.DATA_ROOT
        / "assemblies"
        / assembly_slug(assembly)
        / "schematics"
        / pack_slug(pack)
    )


def ensure_files_dir(assembly: Assembly, pack: SchematicPack) -> Path:
    root = pack_root(assembly, pack) / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _unique_filename(root: Path, original_name: str) -> str:
    name = Path(original_name).name or "schematic.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    stem = _slugify(Path(name).stem, fallback="schematic")
    candidate = f"{stem}.pdf"
    counter = 1
    while (root / candidate).exists():
        candidate = f"{stem}-{counter}.pdf"
        counter += 1
    return candidate


def _analyse_pdf(path: Path) -> tuple[int, bool]:
    if fitz is None:
        return 0, False
    try:
        with fitz.open(path) as doc:  # type: ignore[call-arg]
            page_count = doc.page_count
            has_text = False
            for page in doc:
                if page.get_text("text").strip():
                    has_text = True
                    break
        return page_count, has_text
    except Exception:  # pragma: no cover - defensive guard for malformed PDFs
        return 0, False


def store_upload(
    pack: SchematicPack,
    assembly: Assembly,
    upload_file,
) -> StoredFile:
    files_root = ensure_files_dir(assembly, pack)
    filename = _unique_filename(files_root, getattr(upload_file, "filename", ""))
    destination = files_root / filename
    upload_file.file.seek(0)
    with destination.open("wb") as handle:
        shutil.copyfileobj(upload_file.file, handle)
    relative = destination.resolve().relative_to(config.DATA_ROOT)
    page_count, has_text = _analyse_pdf(destination)
    return StoredFile(
        path=destination,
        relative_path=relative,
        page_count=page_count,
        has_text_layer=has_text,
    )


def reassign_file_orders(files: Iterable[SchematicFile]) -> None:
    for idx, file in enumerate(sorted(files, key=lambda f: (f.file_order, f.id or 0)), start=1):
        file.file_order = idx


def update_pack_timestamp(pack: SchematicPack) -> None:
    pack.updated_at = datetime.utcnow()

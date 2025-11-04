from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
from typing import Tuple
import os
import shutil

from ..config import DATASHEETS_DIR, DATA_ROOT

# Default store is provided by central config; can be local or a network path
DATASHEET_STORE = Path(DATASHEETS_DIR)


def sha256_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_path_for_hash(h: str) -> Path:
    sub1, sub2 = h[:2], h[2:4]
    return DATASHEET_STORE / sub1 / sub2 / f"{h}.pdf"


def ensure_store_dirs(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)


def register_datasheet_for_part(session, part_id: int, pdf_src: Path) -> Tuple[Path, bool]:
    """
    Returns (canonical_path, existed).
    - If existed is True, the file was already present; caller decides whether to link it.
    - If existed is False, file was copied into the store.
    This function only handles the file store; the caller updates Part.datasheet_url.
    """
    h = sha256_of_file(pdf_src)
    dst = canonical_path_for_hash(h)
    ensure_store_dirs(dst)
    existed = dst.exists()
    if not existed:
        shutil.copy2(pdf_src, dst)
    return dst, existed


# ---------------------- Local open path (optional cache) ----------------------
def get_local_open_path(canonical: Path) -> Path:
    """Return a local path to open for a datasheet.

    If the datasheets store is on a slow or remote path, you can set
    BOM_DATASHEETS_CACHE_DIR (or accept the default) to cache a local copy for
    faster opening in external viewers.

    - If the cache is configured and the file is not present in cache, copy it once.
    - If the cache exists, use the cached file.
    - Otherwise, return the canonical path as-is.
    """
    cache_root = os.getenv("BOM_DATASHEETS_CACHE_DIR")
    if not cache_root:
        cache_root = str((DATA_ROOT / "cache" / "datasheets").resolve())
    cache_dir = Path(cache_root)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return canonical
    # Use same hashed subdirectory layout when canonical resides under DATASHEETS_DIR
    try:
        rel = canonical.relative_to(DATASHEETS_DIR)
    except Exception:
        rel = Path(canonical.name)
    dst = cache_dir / rel
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if not dst.exists():
        try:
            shutil.copy2(canonical, dst)
        except Exception:
            return canonical
    return dst

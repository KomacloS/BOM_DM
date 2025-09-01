from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
from typing import Tuple


# Default store under repo root: <repo_root>/data/datasheets
DATASHEET_STORE = Path(__file__).resolve().parents[2] / "data" / "datasheets"


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


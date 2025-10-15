from __future__ import annotations

"""Helpers for managing per-part test assets on disk.

All helpers enforce a strict part number whitelist (``[A-Za-z0-9._-]``) to
prevent path traversal and other filesystem surprises.
"""

from pathlib import Path
from typing import Tuple
import io
import logging
import re
import zipfile

from .. import config

LOGGER = logging.getLogger(__name__)

_VALID_PN = re.compile(r"^[A-Za-z0-9._-]+$")


def _python_root() -> Path:
    return config.DATA_ROOT / "python"


def _quicktest_root() -> Path:
    return config.DATA_ROOT / "QuickTest"


def ensure_base_dirs() -> None:
    """Create the ``data/python`` and ``data/QuickTest`` folders."""

    for path in (_python_root(), _quicktest_root()):
        path.mkdir(parents=True, exist_ok=True)


def validate_part_number(pn: str) -> str:
    """Return a stripped, validated part number or raise ``ValueError``."""

    cleaned = pn.strip()
    if not cleaned or not _VALID_PN.fullmatch(cleaned):
        raise ValueError("Part numbers may only contain A-Z, a-z, 0-9, '.', '_' or '-' characters")
    return cleaned


def ensure_python_folder_for_pn(pn: str) -> str:
    """Ensure ``data/python/<pn>/`` exists and return the absolute path."""

    ensure_base_dirs()
    validated = validate_part_number(pn)
    folder = _python_root() / validated
    folder.mkdir(parents=True, exist_ok=True)
    readme = folder / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Python test assets\n\n"
            "Place scripts and support files for part {pn} in this folder.\n".format(pn=validated),
            encoding="utf-8",
        )
    return str(folder.resolve())


def python_folder_path(pn: str) -> str:
    """Return the absolute path to the python test folder without creating it."""

    validated = validate_part_number(pn)
    return str((_python_root() / validated).resolve())


def python_folder_exists(pn: str) -> bool:
    try:
        validated = validate_part_number(pn)
    except ValueError:
        return False
    return (_python_root() / validated).is_dir()


def quicktest_path_for_pn(pn: str) -> str:
    validated = validate_part_number(pn)
    ensure_base_dirs()
    return str((_quicktest_root() / f"{validated}.txt").resolve())


def read_quicktest(pn: str) -> Tuple[str, bool]:
    """Return ``(content, created)`` for the Quick Test text file."""

    ensure_base_dirs()
    validated = validate_part_number(pn)
    path = _quicktest_root() / f"{validated}.txt"
    created = False
    if not path.exists():
        path.write_text("", encoding="utf-8")
        created = True
    content = path.read_text(encoding="utf-8")
    return content, created


def write_quicktest(pn: str, content: str) -> str:
    """Write the Quick Test file for ``pn`` and return the absolute path."""

    ensure_base_dirs()
    validated = validate_part_number(pn)
    path = _quicktest_root() / f"{validated}.txt"
    path.write_text(content, encoding="utf-8")
    LOGGER.info("Saved quick test file for %s at %s", validated, path)
    return str(path.resolve())


def zip_python_folder(pn: str) -> bytes:
    """Return a zip archive (bytes) of the ``data/python/<pn>/`` folder."""

    validated = validate_part_number(pn)
    folder = _python_root() / validated
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in folder.rglob("*"):
            if item.is_file():
                archive.write(item, item.relative_to(folder))
    return buffer.getvalue()


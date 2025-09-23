from __future__ import annotations

from pathlib import Path
from typing import Optional


def extract_text_first_pages(path: Path, max_pages: int = 2) -> str:
    """Extract text from the first ``max_pages`` pages of a PDF.

    Uses PyMuPDF if available. Returns an empty string on failure.
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ""

    try:
        doc = fitz.open(str(path))
    except Exception:
        return ""

    try:
        text_parts: list[str] = []
        pages = min(len(doc), max(1, int(max_pages)))
        for i in range(pages):
            try:
                page = doc.load_page(i)
                text_parts.append(page.get_text("text"))
            except Exception:
                # Skip problematic pages
                continue
        return "\n".join(text_parts)
    finally:
        try:
            doc.close()
        except Exception:
            pass


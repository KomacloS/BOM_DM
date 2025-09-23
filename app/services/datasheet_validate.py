from __future__ import annotations

from pathlib import Path
from typing import Tuple, Optional
import re

from .pdf_utils import extract_text_first_pages
from urllib.parse import urlparse


def _normalize(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())


def pdf_matches_request(pn: str, mfg: str | None, desc: str | None, path: Path, source_name: Optional[str] = None) -> Tuple[bool, float]:
    """Heuristically verify the PDF matches the requested part.

    - Extracts text from the first few pages.
    - Looks for normalized part number as a contiguous substring.
    - Optionally boosts score if manufacturer name appears.

    Returns (matched, score).
    """
    text = extract_text_first_pages(path, max_pages=3)
    if not text:
        return False, 0.0

    text_norm = _normalize(text)
    pn_norm = _normalize(pn)
    # Base PN: strip suffix after '-' (e.g., -20PU) and trailing package letters (e.g., ending N, AN)
    pn_dash = pn.split('-')[0]
    pn_base = _normalize(pn_dash)
    # Core PN: also strip trailing letters after a trailing digit sequence (e.g., SN74HCT240N -> SN74HCT240)
    m = re.match(r"^(.*?\d+)[A-Z]*$", pn_base)
    pn_core = _normalize(m.group(1)) if m else pn_base
    if not pn_norm or len(pn_norm) < 4:
        return False, 0.0

    score = 0.0
    if pn_norm in text_norm:
        score += 2.0
    elif pn_base and pn_base in text_norm:
        score += 1.5
    elif pn_core and pn_core in text_norm:
        score += 1.3

    # Manufacturer hint (case-insensitive, not normalized to preserve spaces)
    if mfg:
        mfg_low = (mfg or "").strip().lower()
        if len(mfg_low) >= 3 and mfg_low in text.lower():
            score += 0.5

    # Datasheet keyword hint
    if "datasheet" in text.lower():
        score += 0.25

    # URL filename hint: if the filename includes PN or base PN
    try:
        name = source_name or path.name
        name_norm = _normalize(name)
        if pn_norm and pn_norm in name_norm:
            score += 0.8
        elif pn_base and pn_base in name_norm:
            score += 0.6
        elif pn_core and pn_core in name_norm:
            score += 0.5
    except Exception:
        pass

    return (score >= 1.5), score

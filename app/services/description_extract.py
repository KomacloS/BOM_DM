from __future__ import annotations

from pathlib import Path
from typing import Optional
import re

from .pdf_utils import extract_text_first_pages


def _clean(s: str) -> str:
    s = (s or "").strip()
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s)
    # Trim separators
    s = s.strip("-: •\t ")
    return s


def _take_first_sentence(text: str, max_len: int = 180) -> str:
    # Split on sentence enders conservatively
    parts = re.split(r"(?<=[\.!?])\s+", text)
    out = parts[0] if parts else text
    out = _clean(out)
    if len(out) > max_len:
        out = out[: max_len - 1].rstrip() + "…"
    return out


def infer_description_from_pdf_text(pn: str, mfg: str | None, text: str) -> Optional[str]:
    """Heuristically extract a short description from datasheet text.

    - Prefer the block following a 'General Description' or 'Description' heading.
    - Fallback to the earliest title-like line that contains the PN followed by words.
    - Returns a concise one-line summary or None if not found.
    """
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln and ln.strip()]
    if not lines:
        return None

    # Normalize helpers
    def norm(s: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())

    pn_norm = norm(pn)
    mfg_low = (mfg or "").strip().lower()

    # 1) Look for 'General Description' or 'Description' heading
    head_idxs: list[int] = []
    for i, ln in enumerate(lines[:200]):
        l = ln.strip().lower()
        if l in ("general description", "description") or re.match(r"^(general )?description\b", l):
            head_idxs.append(i)
            break
    for idx in head_idxs:
        # Take following non-empty lines until a likely next heading
        buf: list[str] = []
        for ln in lines[idx + 1 : idx + 8]:
            l = ln.strip()
            if not l:
                break
            # Stop at common section headers
            l_low = l.lower()
            if any(h in l_low for h in ("features", "applications", "absolute", "pin", "block diagram")):
                break
            # Stop if line looks like an all-caps header
            if len(l) <= 80 and re.fullmatch(r"[A-Z0-9\s\-/]+", l) and l.upper() == l:
                break
            buf.append(l)
            if len(" ".join(buf)) > 200:
                break
        if buf:
            return _take_first_sentence(" ".join(buf))

    # 2) Title line heuristic: look for a line containing PN and extra words
    for ln in lines[:40]:
        l = ln.strip()
        l_norm = norm(l)
        if pn_norm and pn_norm in l_norm:
            # Remove PN tokens and manufacturer name from line to get the tail
            tail = re.sub(re.escape(pn), " ", l, flags=re.I)
            if mfg_low:
                tail = re.sub(re.escape(mfg_low), " ", tail, flags=re.I)
            tail = _clean(tail)
            # If the tail is too short, try after separators in original line
            if len(tail) < 6:
                m = re.search(r"[:\-–]\s*(.+)$", l)
                if m:
                    tail = _clean(m.group(1))
            # Sanity: avoid returning the PN itself
            if tail and norm(tail) != pn_norm:
                return _take_first_sentence(tail)

    # 3) As a weak fallback, take first sentence from the first paragraph
    para = " ".join(lines[:8])
    para = _clean(para)
    if para and len(para) >= 12:
        return _take_first_sentence(para)
    return None


def infer_description_from_pdf(pn: str, mfg: str | None, path: Path) -> Optional[str]:
    """Extract and summarize description from a PDF file (first pages)."""
    try:
        text = extract_text_first_pages(Path(path), max_pages=3)
    except Exception:
        text = ""
    return infer_description_from_pdf_text(pn, mfg, text)


from __future__ import annotations

from typing import List, Tuple
from urllib.parse import urlparse
import re


NEGATIVE_TERMS = {"catalog", "brochure", "flyer", "presentation", "magazine", "newsletter"}
DENY_HOSTS = {
    "indiamart.com",
    "issuu.com",
    "scribd.com",
    "yumpu.com",
    "leocom.kr",
    "pdfcat",
}


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())


def recommended_domains_for(mfg: str | None, pn: str) -> List[str]:
    """Return preferred domains for the given manufacturer/PN.

    Manufacturer string is fuzzy-matched to a small curated map.
    """
    m = (mfg or "").lower()
    # Manufacturer domains (primary)
    mfg_map = [
        ("st", ["st.com"]),
        ("stmicro", ["st.com"]),
        ("texas", ["ti.com"]),
        ("ti", ["ti.com"]),
        ("analog", ["analog.com"]),
        ("adi", ["analog.com"]),
        ("microchip", ["microchip.com"]),
        ("onsemi", ["onsemi.com"]),
        ("nxp", ["nxp.com"]),
        ("renesas", ["renesas.com"]),
        ("infineon", ["infineon.com"]),
        ("nexperia", ["nexperia.com"]),
        ("stmicroelectronics", ["st.com"]),
        ("murata", ["murata.com"]),
        ("kemet", ["kemet.com"]),
        ("vishay", ["vishay.com"]),
        ("bourns", ["bourns.com"]),
    ]
    out: list[str] = []
    for k, doms in mfg_map:
        if k in m:
            out.extend(doms)
            break
    # Distributor domains (secondary)
    out.extend([
        "mouser.com",
        "digikey.com",
        "rs-online.com",
        "farnell.com",
        "arrow.com",
        "tme.eu",
    ])
    # De-duplicate while preserving order
    seen = set()
    ret = []
    for d in out:
        if d not in seen:
            ret.append(d)
            seen.add(d)
    return ret


def score_candidate(pn: str, mfg: str | None, title: str, snippet: str, url: str) -> float:
    """Score a search result for likelihood of being the correct datasheet.

    Combines domain priority, filename/path signals, and text matches.
    """
    score = 0.0
    pn_norm = _norm(pn)
    title_low = (title or "").lower()
    snip_low = (snippet or "").lower()
    u = urlparse(url)
    host = (u.netloc or "").lower()
    path = (u.path or "").lower()
    url_low = (url or "").lower()

    # Host penalties
    if any(bad in host for bad in DENY_HOSTS):
        score -= 2.0

    # Domain priority
    preferred = recommended_domains_for(mfg, pn)
    if any(h in host for h in preferred[:1]):  # manufacturer
        score += 2.5
    elif any(h in host for h in preferred[1:]):  # distributors
        score += 1.2

    # URL/path signals
    if "datasheet" in path:
        score += 0.6
    if any(neg in path for neg in NEGATIVE_TERMS):
        score -= 1.0

    # Filename contains PN
    if pn_norm and pn_norm in _norm(url):
        score += 1.2
    else:
        score -= 0.2

    # Title/snippet keywords
    if "datasheet" in title_low or "datasheet" in snip_low:
        score += 0.4
    # PN and MFG presence in title/snippet
    if pn_norm and pn_norm in _norm(title + " " + snippet):
        score += 0.6
    if mfg and len(mfg) >= 3 and (mfg.lower() in title_low or mfg.lower() in snip_low):
        score += 0.3

    # Prefer PDFs outright
    if path.endswith(".pdf"):
        score += 0.5

    return score


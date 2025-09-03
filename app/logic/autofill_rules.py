from dataclasses import dataclass
from typing import Optional
import re

@dataclass
class AutoResult:
    package: Optional[str] = None
    value: Optional[str] = None
    tol_pos: Optional[str] = None
    tol_neg: Optional[str] = None

# Regex patterns
_PACK_RE = re.compile(r"(?<!\d)(0201|0402|0603|0805|1206|1210|1812|2010|2512|2220|2225)(?!\d)")
_CAP_RE = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)[\s]*?(pF|nF|uF|µF|PF|NF|UF)(?![A-Za-z0-9])", re.I)
_IND_RE = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)[\s]*?(nH|uH|µH|mH|H)(?![A-Za-z0-9])", re.I)
_RES_UNIT_RE = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)(?:\s*|\s*[- ]?)?(R|Ω|OHM|K|k|M|Meg|G)(?![A-Za-z0-9])", re.I)
_RES_CODE_TOKEN_RE = re.compile(r"\b\d+[RrKkMm]\d*\b")
_TOL_ASYM_RE1 = re.compile(r"\+(\d+(?:\.\d+)?)(?:\s*%)?.*-(\d+(?:\.\d+)?)(?:\s*%)", re.I)
_TOL_ASYM_RE2 = re.compile(r"-(\d+(?:\.\d+)?)(?:\s*%)?.*\+(\d+(?:\.\d+)?)(?:\s*%)", re.I)
_TOL_SYM_RE = re.compile(r"(?:±|\+/-)?\s*(\d+(?:\.\d+)?)\s*%", re.I)
_EIA_CODE_RE = re.compile(r"C\d{4}C(\d{3})[A-Z]", re.I)
_EIA_TOL_RE = re.compile(r"C\d{4}C\d{3}([FGJKM])", re.I)
_CAP_KEY_RE = re.compile(r"CAP", re.I)
_RES_KEY_RE = re.compile(r"RES|RESISTOR", re.I)
_IND_KEY_RE = re.compile(r"IND|INDUCTOR", re.I)

_TOL_MAP = {"F": "1", "G": "2", "J": "5", "K": "10", "M": "20"}


def _strip(v: float) -> str:
    s = f"{v:g}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _cap_to_pf(num: float, unit: str) -> float:
    u = unit.replace("µ", "u").lower()
    if u == "pf":
        return num
    if u == "nf":
        return num * 1e3
    if u == "uf":
        return num * 1e6
    return num


def _normalize_cap_pf(pf: float) -> str:
    if pf >= 100000:
        return f"{_strip(pf/1e6)}uF"
    if pf >= 1000:
        return f"{_strip(pf/1e3)}nF"
    return f"{_strip(pf)}pF"


def _res_to_ohms(num: str, unit: str) -> float:
    n = float(num)
    u = unit.upper()
    if u in {"R", "Ω", "OHM", "OHMS"}:
        return n
    if u == "K":
        return n * 1e3
    if u in {"M", "MEG"}:
        return n * 1e6
    if u == "G":
        return n * 1e9
    return n


def _norm_res_ohms(ohms: float) -> str:
    if ohms >= 1e6:
        return f"{_strip(ohms/1e6)}MΩ"
    if ohms >= 1e3:
        return f"{_strip(ohms/1e3)}k"
    return f"{_strip(ohms)}Ω"


def _value_from_res_code(token: str) -> Optional[str]:
    m = re.fullmatch(r"(\d+)([RrKkMm])(\d*)", token)
    if not m:
        return None
    left, letter, right = m.groups()
    right = right or "0"
    letter = letter.upper()
    if letter == "R":
        ohms = float(f"{left}.{right}" if right != "0" else left)
    elif letter == "K":
        ohms = float(f"{left}.{right}") * 1e3
    elif letter == "M":
        ohms = float(f"{left}.{right}") * 1e6
    else:
        return None
    return _norm_res_ohms(ohms)


def _cap_value_from_desc(desc: str) -> tuple[Optional[str], Optional[float]]:
    matches = _CAP_RE.findall(desc)
    if not matches:
        return None, None
    pfs = [_cap_to_pf(float(num), unit) for num, unit in matches]
    norms = {_normalize_cap_pf(pf) for pf in pfs}
    if len(norms) == 1:
        return norms.pop(), pfs[0]
    return None, None


def _res_value_from_desc(desc: str) -> Optional[str]:
    vals = []
    for num, unit in _RES_UNIT_RE.findall(desc):
        ohms = _res_to_ohms(num, unit)
        vals.append(_norm_res_ohms(ohms))
    for token in _RES_CODE_TOKEN_RE.findall(desc):
        v = _value_from_res_code(token)
        if v:
            vals.append(v)
    vals = set(vals)
    if len(vals) == 1:
        return vals.pop()
    return None


def _ind_value_from_desc(desc: str) -> Optional[str]:
    matches = _IND_RE.findall(desc)
    if not matches:
        return None
    unit_map = {"nh": "nH", "uh": "uH", "mh": "mH", "h": "H"}
    vals = {
        f"{_strip(float(num))}{unit_map.get(unit.replace('µ', 'u').lower(), unit)}"
        for num, unit in matches
    }
    if len(vals) == 1:
        return vals.pop()
    return None


def _parse_tol(desc: str) -> Optional[tuple[str, str]]:
    m = _TOL_ASYM_RE1.search(desc)
    if m:
        return m.group(1), m.group(2)
    m = _TOL_ASYM_RE2.search(desc)
    if m:
        return m.group(2), m.group(1)
    nums = _TOL_SYM_RE.findall(desc)
    nums = [n for n in nums if n]
    if len(set(nums)) == 1:
        val = nums[0]
        return val, val
    return None


def _code_to_pf(code: str) -> float:
    sig = int(code[:2])
    exp = int(code[2])
    return sig * (10 ** exp)


def infer_from_pn_and_desc(pn: str, desc: str) -> AutoResult:
    pn = pn or ""
    desc = desc or ""
    res = AutoResult()

    # Package
    matches = set(_PACK_RE.findall(pn))
    if len(matches) == 1:
        res.package = matches.pop()
    else:
        matches = set(_PACK_RE.findall(desc))
        if len(matches) == 1:
            res.package = matches.pop()

    # Value from description
    value, pf_desc = _cap_value_from_desc(desc)
    if value:
        res.value = value
    else:
        vres = _res_value_from_desc(desc)
        if vres:
            res.value = vres
        else:
            vind = _ind_value_from_desc(desc)
            if vind:
                res.value = vind

    # PN helpers
    if res.value is None:
        # Capacitor EIA code
        m = _EIA_CODE_RE.search(pn)
        if m and (_CAP_KEY_RE.search(desc) or re.search(r"C\d{4}C", pn, re.I)):
            pf = _code_to_pf(m.group(1))
            res.value = _normalize_cap_pf(pf)
    else:
        # If desc gives capacitor value, ensure PN is consistent if present
        m = _EIA_CODE_RE.search(pn)
        if pf_desc is not None and m and (_CAP_KEY_RE.search(desc) or re.search(r"C\d{4}C", pn, re.I)):
            pf = _code_to_pf(m.group(1))
            if _normalize_cap_pf(pf) != _normalize_cap_pf(pf_desc):
                pass  # keep description value
    # Resistor PN codes if description indicates resistor
    if res.value is None and _RES_KEY_RE.search(desc):
        vals = {_value_from_res_code(t) for t in _RES_CODE_TOKEN_RE.findall(pn)}
        vals.discard(None)
        if len(vals) == 1:
            res.value = vals.pop()

    # Tolerance
    tol = _parse_tol(desc)
    if tol:
        res.tol_pos, res.tol_neg = tol
    else:
        m = _EIA_TOL_RE.search(pn)
        if m and (_CAP_KEY_RE.search(desc) or re.search(r"C\d{4}C", pn, re.I)):
            t = _TOL_MAP.get(m.group(1).upper())
            if t:
                res.tol_pos = res.tol_neg = t

    return res

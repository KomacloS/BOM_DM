from __future__ import annotations
from pathlib import Path
from typing import List, Tuple

_CACHE: list[Tuple[str, str]] | None = None


def _candidate_paths() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        here.parents[2] / "data" / "prefix_macros.txt",  # <repo_root>/data
        here.parents[1] / "data" / "prefix_macros.txt",  # app/data
        Path.cwd() / "data" / "prefix_macros.txt",       # cwd/data
    ]


_DEFAULT = [
    ("R", "RESISTOR"),
    ("C", "CAPACITOR"),
    ("L", "INDUCTANCE"),
    ("D", "DIODE"),
    ("LED", "LED"),
    ("Q", "TRANSISTOR"),
    ("U", "DIGITAL"),
    ("Y", "OSCILLATOR"), ("X", "OSCILLATOR"),
    ("F", "FUSE"),
    ("K", "RELAIS"),
    ("J", "CONNECTOR"), ("P", "CONNECTOR"), ("CN", "CONNECTOR"),
    ("VR", "VOLTAGEDIVIDER"),
]


def _load_from_disk() -> list[Tuple[str, str]]:
    for p in _candidate_paths():
        try:
            if p.exists():
                rows: list[Tuple[str, str]] = []
                for ln in p.read_text(encoding="utf-8").splitlines():
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    if "\t" not in s:
                        continue
                    pref, macro = s.split("\t", 1)
                    pref = pref.strip().upper()
                    macro = macro.strip()  # keep user case; validate later
                    if not pref or not macro:
                        continue
                    rows.append((pref, macro))
                if rows:
                    rows.sort(key=lambda kv: len(kv[0]), reverse=True)
                    return rows
        except Exception:
            pass
    rows = [(k.upper(), v) for k, v in _DEFAULT]
    rows.sort(key=lambda kv: len(kv[0]), reverse=True)
    return rows


def load_prefix_macros() -> list[Tuple[str, str]]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load_from_disk()
    return list(_CACHE)


def reload_prefix_macros() -> list[Tuple[str, str]]:
    global _CACHE
    _CACHE = _load_from_disk()
    return list(_CACHE)

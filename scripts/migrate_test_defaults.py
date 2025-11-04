"""Migrate legacy BOM Editor test assignments from QSettings into the main DB.

Until now test method/detail selections were saved per-user in QSettings.
This script reads those stored values for every assembly and persists them
to ``PartTestMap`` so future BOMs automatically inherit the defaults.

Usage::

    python -m scripts.migrate_test_defaults

The script is idempotent.  It only writes mappings for methods other than
"Complex" (which relies on Complex Editor link data already stored in DB).
"""

from __future__ import annotations

import sys
from typing import Dict, Tuple

from PyQt6.QtCore import QCoreApplication, QSettings
from sqlmodel import Session, select

from app.database import ensure_schema, new_session
from app import services
from app.models import Assembly, Part, PartType, TestMode, TestProfile


def _coerce_part_type(value) -> PartType | None:
    if isinstance(value, PartType):
        return value
    if value is None:
        return None
    try:
        return PartType(str(value))
    except ValueError:
        return None


def _default_profile(part_type: PartType | None) -> TestProfile:
    if part_type is PartType.passive:
        return TestProfile.passive
    if part_type is PartType.active:
        return TestProfile.active
    return TestProfile.passive


def migrate() -> Tuple[int, int]:
    """Return (assignments_found, assignments_written)."""

    ensure_schema()

    assignments_found = 0
    assignments_written = 0

    part_cache: Dict[int, PartType | None] = {}

    with new_session() as session:
        rows = list(session.exec(select(Assembly.id)))
        assembly_ids: list[int] = []
        for rec in rows:
            try:
                if isinstance(rec, (list, tuple)) and rec:
                    aid = rec[0]
                elif hasattr(rec, "id"):
                    aid = getattr(rec, "id", None)
                else:
                    aid = rec
                if aid is not None:
                    assembly_ids.append(int(aid))
            except Exception:
                continue

    app = QCoreApplication.instance()
    if app is None:
        QCoreApplication(sys.argv)

    for assembly_id in assembly_ids:
        settings = QSettings("BOM_DB", f"BOMEditorPane/{assembly_id}")
        method_keys = [key for key in settings.allKeys() if key.startswith("test/method/")]
        if not method_keys:
            continue

        with new_session() as session:
            for key in method_keys:
                part_id_str = key.split("/")[-1]
                try:
                    part_id = int(part_id_str)
                except ValueError:
                    continue

                method = (settings.value(key) or "").strip()
                if not method or method == "Complex":
                    continue

                assignments_found += 1

                detail_key = f"test/detail/{part_id}"
                qt_key = f"test/qt_path/{part_id}"

                detail = (settings.value(detail_key, "") or "").strip() or None
                qt_path = (settings.value(qt_key, "") or "").strip() or None

                part_type = part_cache.get(part_id)
                if part_type is None and part_id not in part_cache:
                    part = session.get(Part, part_id)
                    if part is None:
                        continue
                    part_type = _coerce_part_type(part.active_passive)
                    part_cache[part_id] = part_type
                else:
                    part_type = part_cache.get(part_id)
                profile = _default_profile(part_type)

                try:
                    services.save_part_test_map(
                        session,
                        part_id=part_id,
                        power_mode=TestMode.unpowered,
                        profile=profile,
                        method=method,
                        detail=detail,
                        quick_test_path=qt_path,
                    )
                except Exception as exc:  # pragma: no cover - migration helper
                    print(f"[warn] Failed to migrate part {part_id}: {exc}", file=sys.stderr)
                    session.rollback()
                    continue
                else:
                    assignments_written += 1
            session.commit()

    return assignments_found, assignments_written


def main() -> None:
    found, written = migrate()
    print(f"Detected {found} legacy assignments; wrote {written} PartTestMap rows.")


if __name__ == "__main__":  # pragma: no cover - script entry
    main()

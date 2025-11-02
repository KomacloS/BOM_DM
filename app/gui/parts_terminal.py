"""Standalone Parts Terminal for editing Part records in the main DB.

Minimal utility to browse, add, edit, and delete rows from the ``part``
table. Intended as a simple admin/editor window you can launch from
Settings.
"""

from __future__ import annotations

from typing import Optional

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sqlmodel import Session, select

from ..database import ensure_schema, new_session
from ..models import Part, PartType, PartTestMap, TestMode, TestMacro, PythonTest


COLUMNS: list[tuple[str, str]] = [
    ("id", "ID"),
    ("part_number", "Part Number"),
    ("description", "Description"),
    ("package", "Package"),
    ("value", "Value"),
    ("function", "Function"),
    ("active_passive", "Active/Passive"),
    ("power_required", "Power Req. (Part)"),
    ("datasheet_url", "Datasheet"),
    ("product_url", "Product URL"),
    ("tol_p", "+Tol"),
    ("tol_n", "-Tol"),
    ("powered_method", "Powered Method"),
    ("powered_detail", "Powered Detail"),
    ("unpowered_method", "Unpowered Method"),
    ("unpowered_detail", "Unpowered Detail"),
]


def _to_text(value) -> str:
    if isinstance(value, PartType):
        return value.value
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "" if value is None else str(value)


class PartsWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BOM_DB – Parts Terminal")
        self.resize(1200, 700)

        central = QWidget(self)
        v = QVBoxLayout(central)

        # Header with quick filter and actions
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("part number or description contains…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        hdr.addWidget(self.filter_edit)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.load_parts)
        hdr.addWidget(self.refresh_btn)

        self.add_btn = QPushButton("Add Part")
        self.add_btn.clicked.connect(self._add_part)
        hdr.addWidget(self.add_btn)

        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self._delete_selected)
        hdr.addWidget(self.delete_btn)

        hdr.addStretch(1)
        v.addLayout(hdr)

        # Table
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._edit_cell)
        v.addWidget(self.table)

        self.setCentralWidget(central)

        self._rows: list[Part] = []
        self._visible_rows: list[int] = []  # indexes into self._rows after filter
        # Cached per-part test info: {part_id: {"powered": (method, detail), "unpowered": (method, detail)}}
        self._test_info: dict[int, dict[str, tuple[str, str]]] = {}
        self.load_parts()

    # ------------------------------------------------------------------
    def _open_session(self) -> Session:
        return new_session()

    def load_parts(self) -> None:
        try:
            with self._open_session() as s:
                parts = list(s.exec(select(Part).order_by(Part.part_number)))
                # Preload test mappings for these parts
                part_ids = [p.id for p in parts if p.id is not None]
                self._test_info = {}
                if part_ids:
                    rows = list(
                        s.exec(
                            select(PartTestMap).where(PartTestMap.part_id.in_(part_ids))
                        )
                    )
                    # Collect names for referenced macros/tests
                    macro_ids = {r.test_macro_id for r in rows if r.test_macro_id}
                    py_ids = {r.python_test_id for r in rows if r.python_test_id}
                    macros = {}
                    tests = {}
                    if macro_ids:
                        macros = {
                            m.id: m.name
                            for m in s.exec(select(TestMacro).where(TestMacro.id.in_(macro_ids)))
                        }
                    if py_ids:
                        tests = {
                            t.id: t.name
                            for t in s.exec(select(PythonTest).where(PythonTest.id.in_(py_ids)))
                        }
                    for r in rows:
                        pid = int(r.part_id)
                        mode_key = (
                            "powered"
                            if r.power_mode == TestMode.powered
                            else "unpowered"
                        )
                        method = ""
                        if r.test_macro_id and r.test_macro_id in macros:
                            method = macros[r.test_macro_id]
                        elif r.python_test_id and r.python_test_id in tests:
                            method = tests[r.python_test_id]
                        detail = (r.detail or "").strip()
                        bucket = self._test_info.setdefault(pid, {})
                        # Do not overwrite if already present; first one wins
                        bucket.setdefault(mode_key, (method, detail))
        except Exception as exc:
            QMessageBox.critical(self, "DB", f"Failed to load parts: {exc}")
            return
        self._rows = parts
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = (self.filter_edit.text() or "").strip().lower()
        self._visible_rows = []
        for idx, p in enumerate(self._rows):
            if not q:
                self._visible_rows.append(idx)
                continue
            blob = f"{p.part_number} {p.description or ''}".lower()
            if q in blob:
                self._visible_rows.append(idx)
        self._populate_table()

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._visible_rows))
        for r, row_idx in enumerate(self._visible_rows):
            part = self._rows[row_idx]
            for c, (attr, _label) in enumerate(COLUMNS):
                if attr in {"powered_method", "powered_detail", "unpowered_method", "unpowered_detail"}:
                    info = self._test_info.get(int(part.id or 0), {})
                    key = "powered" if attr.startswith("powered_") else "unpowered"
                    tup = info.get(key, ("", ""))
                    value = tup[0] if attr.endswith("method") else tup[1]
                    item = QTableWidgetItem(_to_text(value))
                    # Render these as read-only display cells
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                elif attr == "power_required":
                    # Global per-part flag; keep read-only in this terminal to
                    # avoid accidental changes since power requirements may
                    # vary per-board.
                    value = getattr(part, attr)
                    item = QTableWidgetItem(_to_text(value))
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                else:
                    value = getattr(part, attr)
                    item = QTableWidgetItem(_to_text(value))
                if attr == "id":
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(r, c, item)

    def _current_part(self, row: int) -> Optional[Part]:
        if row < 0 or row >= len(self._visible_rows):
            return None
        return self._rows[self._visible_rows[row]]

    # ------------------------------------------------------------------
    def _add_part(self) -> None:
        pn, ok = QInputDialog.getText(self, "Add Part", "Part number:")
        if not ok:
            return
        pn = (pn or "").strip()
        if not pn:
            QMessageBox.warning(self, "Add Part", "Part number is required.")
            return
        try:
            with self._open_session() as s:
                part = Part(part_number=pn)
                s.add(part)
                s.commit()
        except Exception as exc:
            QMessageBox.critical(self, "Add Part", str(exc))
            return
        self.load_parts()
        # Focus new row
        for row, p in enumerate(self._rows):
            if p.part_number == pn:
                self.table.selectRow(row)
                break

    def _delete_selected(self) -> None:
        row = self.table.currentRow()
        part = self._current_part(row)
        if not part:
            return
        resp = QMessageBox.question(
            self,
            "Delete Part",
            f"Delete part '{part.part_number}' (ID {part.id})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            with self._open_session() as s:
                obj = s.get(Part, part.id)
                if obj is not None:
                    s.delete(obj)
                    s.commit()
        except Exception as exc:
            QMessageBox.critical(self, "Delete Part", str(exc))
            return
        self.load_parts()

    def _edit_cell(self, row: int, col: int) -> None:
        part = self._current_part(row)
        if not part:
            return
        attr, label = COLUMNS[col]
        if attr == "id":
            return  # not editable

        current_text = _to_text(getattr(part, attr))

        # Specialized toggles
        if attr == "active_passive":
            options = ["active", "passive"]
            cur = 0 if str(current_text).lower() == "active" else 1
            text, ok = QInputDialog.getItem(
                self, label, label + ":", options, cur, editable=False
            )
            if not ok:
                return
            if self._save_field(part.id, attr, text):
                self.load_parts()
            return

        # Generic string edit
        text, ok = QInputDialog.getText(self, label, label + ":", text=current_text)
        if not ok:
            return
        new_text = (text or "").strip()
        if new_text == current_text:
            return
        if self._save_field(part.id, attr, new_text):
            self.load_parts()

    def _save_field(self, part_id: int, attr: str, value) -> bool:
        try:
            with self._open_session() as s:
                obj = s.get(Part, part_id)
                if obj is None:
                    raise RuntimeError("Part not found")
                if attr == "active_passive":
                    obj.active_passive = PartType(str(value))
                elif attr == "power_required":
                    obj.power_required = bool(value)
                else:
                    # Normalize empties for optional strings
                    if hasattr(obj.__class__, attr):
                        setattr(obj, attr, (value or None) if value == "" else value)
                    else:
                        raise RuntimeError(f"Unknown field: {attr}")
                s.add(obj)
                s.commit()
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save", str(exc))
            return False


def main() -> None:  # pragma: no cover - manual entry point
    ensure_schema()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen" if not os.environ.get("DISPLAY") else "")
    app = QApplication(sys.argv)
    win = PartsWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover
    main()

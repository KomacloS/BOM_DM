"""BOM editor for classifying parts as active or passive.

Features implemented:
- Clickable pill switch (no combobox) cycling empty → passive → active → passive → …
- Two view modes: By PN (grouped) and By Reference (flat)
- Auto-passive inference for R/L/C when value is empty (UI-only)
- Column visibility per-mode, persisted in QSettings
- Filter works across visible columns; natural sort for reference columns
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Optional
from pathlib import Path

from PyQt6.QtGui import QAction, QStandardItemModel, QStandardItem, QIcon  # QAction/QStandardItem* are in QtGui
from PyQt6.QtCore import Qt, QSortFilterProxyModel, QModelIndex, pyqtSignal, QSettings, QRect, QSize, QTimer, QUrl, QRectF
from PyQt6.QtWidgets import (
    QWidget, QTableView, QVBoxLayout, QLineEdit, QPushButton, QToolBar, QMenu,
    QToolButton, QStyle, QApplication,
    QStyledItemDelegate, QHeaderView, QAbstractItemView, QStyleOptionViewItem,
    QLabel, QMessageBox
)
from PyQt6.QtGui import QKeySequence, QPainter, QBrush, QColor, QDesktopServices, QGuiApplication, QTextDocument, QTextOption
from .. import services
from . import state as app_state


PartIdRole = Qt.ItemDataRole.UserRole + 1
ModeRole = Qt.ItemDataRole.UserRole + 2  # stores 'active'/'passive'/None in AP column for delegate
DatasheetRole = Qt.ItemDataRole.UserRole + 3


# Helpers ---------------------------------------------------------------
def natural_key(s: str) -> List[object]:
    """Natural sort key: split numbers out of strings (case-insensitive)."""
    import re

    token = re.compile(r"(\d+)")
    return [int(t) if t.isdigit() else t.lower() for t in token.split(s or "")]


class BOMFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._filter = ""
        self._ref_col: Optional[int] = None  # column index for natural sorting of references

    def setFilterString(self, text: str) -> None:
        self._filter = text.lower()
        self.invalidateFilter()

    def setReferenceColumn(self, col: Optional[int]) -> None:
        self._ref_col = col
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent):  # pragma: no cover - Qt glue
        if not self._filter:
            return True
        model = self.sourceModel()
        # Filter across all visible columns except the AP column (last)
        cols = max(0, model.columnCount() - 1)
        for col in range(cols):
            idx = model.index(source_row, col, source_parent)
            data = model.data(idx)
            if data and self._filter in str(data).lower():
                return True
        return False

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # pragma: no cover - UI glue
        if self._ref_col is not None and left.column() == self._ref_col == right.column():
            l = (self.sourceModel().data(left) or "")
            r = (self.sourceModel().data(right) or "")
            return natural_key(l) < natural_key(r)
        return super().lessThan(left, right)


class CycleToggleDelegate(QStyledItemDelegate):
    """Delegate that draws a small pill and toggles value on click.

    Values: None → 'passive' → 'active' → 'passive' → …
    """

    valueChanged = pyqtSignal(int, object)  # part_id, new_value (str or None)

    def paint(self, painter: QPainter, option, index):  # pragma: no cover - UI glue
        value = index.data() or None
        rect: QRect = option.rect.adjusted(6, 4, -6, -4)
        pal = option.palette

        if value == "active":
            bg = pal.highlight().color()
            fg = pal.highlightedText().color()
            text = "Active"
        elif value == "passive":
            bg = pal.alternateBase().color()
            fg = pal.text().color()
            text = "Passive"
        else:
            bg = pal.window().color().lighter(110)
            fg = pal.mid().color()
            text = "—"

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(bg))
        painter.setPen(QColor(bg).darker(120))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(fg)
        # Center text
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def editorEvent(self, event, model, option, index):  # pragma: no cover - UI glue
        if event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            cur = index.data() or None
            new = self._next_state(cur)
            part_id = index.data(PartIdRole)
            # write to model first for immediate UI feedback
            model.setData(index, new)
            self.valueChanged.emit(part_id, new)
            return True
        return False

    @staticmethod
    def _next_state(cur):
        if cur in (None, ""):
            return "passive"
        if cur == "passive":
            return "active"
        if cur == "active":
            return "passive"
        return "passive"

    def createEditor(self, parent, option, index):  # pragma: no cover - no inline editor
        # No inline editor; clicks are handled in editorEvent
        return None


class BOMEditorPane(QWidget):
    """Widget presenting BOM items with active/passive classification."""

    def __init__(self, assembly_id: int, parent=None) -> None:
        super().__init__(parent)
        self._assembly_id = assembly_id
        self._settings = QSettings("BOM_DB", f"BOMEditorPane/{assembly_id}")
        self._dirty_parts: Dict[int, str] = {}
        self._parts_state: Dict[int, Optional[str]] = {}
        self._rows_raw: list = []  # canonical read-model rows
        self._part_datasheets: Dict[int, Optional[str]] = {}
        # View mode: 'by_pn' or 'by_ref'
        self._view_mode = self._settings.value("view_mode", "by_pn")
        self._col_indices = {  # will be updated on model rebuild
            "pn": 0,
            "ref": 1,
            "desc": 2,
            "mfg": 3,
            "ap": 4,
            "ds": 5,
        }

        layout = QVBoxLayout(self)
        self.setWindowTitle(f"BOM Editor — Assembly {assembly_id}")
        self.toolbar = QToolBar()
        layout.addWidget(self.toolbar)

        # Filter
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter…")
        self.toolbar.addWidget(self.filter_edit)

        # View mode toggle
        self.view_by_pn_act = QAction("By PN", self)
        self.view_by_ref_act = QAction("By Reference", self)
        self.view_by_pn_act.setCheckable(True)
        self.view_by_ref_act.setCheckable(True)
        from PyQt6.QtGui import QActionGroup
        self.view_group = QActionGroup(self)
        self.view_group.setExclusive(True)
        self.view_group.addAction(self.view_by_pn_act)
        self.view_group.addAction(self.view_by_ref_act)
        if self._view_mode == "by_ref":
            self.view_by_ref_act.setChecked(True)
        else:
            self._view_mode = "by_pn"
            self.view_by_pn_act.setChecked(True)
        self.toolbar.addAction(self.view_by_pn_act)
        self.toolbar.addAction(self.view_by_ref_act)

        # Columns button
        self.columns_btn = QToolButton()
        self.columns_btn.setText("Columns")
        self.columns_menu = QMenu(self)
        self.columns_btn.setMenu(self.columns_menu)
        self.columns_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.toolbar.addWidget(self.columns_btn)

        # Apply immediately toggle
        self.apply_act = QAction("Apply Immediately", self)
        self.apply_act.setCheckable(True)
        self.toolbar.addAction(self.apply_act)

        # Save button
        self.save_act = QAction("Save", self)
        self.save_act.setEnabled(False)
        self.toolbar.addAction(self.save_act)

        # Table
        self.table = QTableView()
        layout.addWidget(self.table)
        self.model = QStandardItemModel(0, 5, self)
        # headers set in _rebuild_model()
        self.proxy = BOMFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        # Delegates
        self.delegate = CycleToggleDelegate(self.table)
        self.delegate.valueChanged.connect(self._on_value_changed)
        self.wrap_delegate = WrapTextDelegate(self.table)
        # Column assignment done in _rebuild_model

        self.filter_edit.textChanged.connect(self.proxy.setFilterString)
        self.apply_act.toggled.connect(self._on_apply_toggled)
        self.save_act.triggered.connect(self._save_changes)
        self.view_by_pn_act.toggled.connect(lambda checked: self._on_view_mode_changed("by_pn", checked))
        self.view_by_ref_act.toggled.connect(lambda checked: self._on_view_mode_changed("by_ref", checked))

        # Copy support for PN/References
        self.copy_act = QAction("Copy", self)
        self.copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_act.triggered.connect(self._copy_selection)
        self.table.addAction(self.copy_act)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)

        # Enable wrapping and resize rows when columns change
        self.table.setWordWrap(True)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self.table.resizeRowsToContents())

        self._load_data()
        self._rebuild_model()

    # ------------------------------------------------------------------
    def _setup_columns_menu(self) -> None:
        # Clear previous actions
        self.columns_menu.clear()
        self._column_actions: list[QAction] = []
        if self._view_mode == "by_pn":
            headers = ["PN", "References", "Description", "Manufacturer", "Active/Passive", "Datasheet"]
        else:
            headers = ["Reference", "PN", "Description", "Manufacturer", "Active/Passive", "Datasheet"]
        self.model.setHorizontalHeaderLabels(headers)
        # Persist settings per mode
        mode_key = "cols_by_pn" if self._view_mode == "by_pn" else "cols_by_ref"
        for idx, name in enumerate(headers):
            act = QAction(name, self)
            act.setCheckable(True)
            visible = self._settings.value(f"{mode_key}/col{idx}_visible", True, type=bool)
            act.setChecked(visible)
            act.toggled.connect(lambda checked, col=idx: self.table.setColumnHidden(col, not checked))
            self.columns_menu.addAction(act)
            self._column_actions.append(act)
            self.table.setColumnHidden(idx, not visible)
            width = self._settings.value(f"{mode_key}/col{idx}_width", type=int)
            if width:
                self.table.setColumnWidth(idx, int(width))
        # Assign delegate to AP column
        ap_col = self._col_indices["ap"]
        self.table.setItemDelegateForColumn(ap_col, self.delegate)
        # Wrap references column
        ref_col = self._col_indices["ref"]
        self.table.setItemDelegateForColumn(ref_col, self.wrap_delegate)
        # Set reference column for natural sorting
        self.proxy.setReferenceColumn(self._col_indices["ref"])

    def _load_data(self) -> None:
        # Keep canonical raw rows
        with app_state.get_session() as session:
            self._rows_raw = services.get_joined_bom_for_assembly(session, self._assembly_id)
        # Seed parts state from DB
        self._parts_state.clear()
        self._part_datasheets.clear()
        for r in self._rows_raw:
            # Use DB-provided value; may be None in future schema, or 'active'/'passive'
            self._parts_state[r.part_id] = getattr(r, "active_passive", None)
            self._part_datasheets[r.part_id] = getattr(r, "datasheet_url", None)

    def _auto_infer(self, value: Optional[str], reference: str) -> Optional[str]:
        # Do not override explicit value
        if value in ("active", "passive"):
            return value
        if isinstance(reference, str) and reference[:1].upper() in ("R", "L", "C"):
            return "passive"
        return None

    def _rebuild_model(self) -> None:
        # Build model based on view mode
        self.model.setRowCount(0)
        self.model.setColumnCount(6)
        if self._view_mode == "by_pn":
            # Column map
            self._col_indices = {"pn": 0, "ref": 1, "desc": 2, "mfg": 3, "ap": 4, "ds": 5}
            self._build_by_pn()
        else:
            self._col_indices = {"ref": 0, "pn": 1, "desc": 2, "mfg": 3, "ap": 4, "ds": 5}
            self._build_by_ref()
        # Setup columns menu and sorting based on new headers
        self._setup_columns_menu()
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(self._col_indices["ref"], Qt.SortOrder.AscendingOrder)
        # Defer autosize until columns are applied
        QTimer.singleShot(0, self._autosize_window_to_columns)
        QTimer.singleShot(0, self._install_datasheet_widgets)

    def _build_by_pn(self) -> None:
        from collections import defaultdict

        groups: Dict[int, List[object]] = defaultdict(list)
        for r in self._rows_raw:
            groups[r.part_id].append(r)

        for part_id, rows in groups.items():
            # Aggregate references
            refs_sorted = sorted((x.reference for x in rows), key=natural_key)
            refs_str = ", ".join(refs_sorted)
            # Determine value: use explicit if present, else auto-infer, then overlay staged
            explicit = next((x.active_passive for x in rows if getattr(x, "active_passive", None) in ("active", "passive")), None)
            mode_val = explicit or self._auto_infer(None, rows[0].reference)
            if part_id in self._dirty_parts:
                mode_val = self._dirty_parts[part_id]
            # Persist or stage auto-inferred, if any
            self._handle_auto_infer_persistence(part_id, mode_val, explicit)

            row_items = [
                QStandardItem(rows[0].part_number),
                QStandardItem(refs_str),
                QStandardItem(rows[0].description or ""),
                QStandardItem(rows[0].manufacturer or ""),
                QStandardItem(mode_val or ""),
                QStandardItem(""),
            ]
            for i, it in enumerate(row_items):
                # Non-editable cells; delegate handles clicks in AP column
                it.setEditable(False)
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled) & ~Qt.ItemFlag.ItemIsEditable)
                it.setData(part_id, PartIdRole)
                if i == self._col_indices["ap"]:
                    it.setData(mode_val, ModeRole)
                if i == self._col_indices["ds"]:
                    it.setData(self._part_datasheets.get(part_id), DatasheetRole)
            self.model.appendRow(row_items)

    def _build_by_ref(self) -> None:
        for r in self._rows_raw:
            explicit = getattr(r, "active_passive", None)
            mode_val = explicit or self._auto_infer(None, r.reference)
            if r.part_id in self._dirty_parts:
                mode_val = self._dirty_parts[r.part_id]
            self._handle_auto_infer_persistence(r.part_id, mode_val, explicit)
            items = [
                QStandardItem(r.reference),
                QStandardItem(r.part_number),
                QStandardItem(r.description or ""),
                QStandardItem(r.manufacturer or ""),
                QStandardItem(mode_val or ""),
                QStandardItem(""),
            ]
            for i, it in enumerate(items):
                it.setEditable(False)
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled) & ~Qt.ItemFlag.ItemIsEditable)
                it.setData(r.part_id, PartIdRole)
                if i == self._col_indices["ap"]:
                    it.setData(mode_val, ModeRole)
                if i == self._col_indices["ds"]:
                    it.setData(self._part_datasheets.get(r.part_id), DatasheetRole)
            self.model.appendRow(items)

    def _handle_auto_infer_persistence(self, part_id: int, inferred: Optional[str], explicit: Optional[str]) -> None:
        # Persist/stage only when there is no explicit value in DB and we inferred a mode
        if explicit in ("active", "passive"):
            # keep parts_state from DB
            self._parts_state[part_id] = explicit
            return
        self._parts_state.setdefault(part_id, inferred)
        if inferred in ("active", "passive"):
            if self.apply_act.isChecked():
                # Apply immediately
                try:
                    with app_state.get_session() as session:
                        services.update_part_active_passive(session, part_id, inferred)
                except Exception:
                    # Do not block UI; leave as inferred only
                    return
                else:
                    self._parts_state[part_id] = inferred
            else:
                # Stage for save
                self._dirty_parts[part_id] = inferred
                self.save_act.setEnabled(True)

    # ------------------------------------------------------------------
    def _on_value_changed(self, part_id: int, value: Optional[str]) -> None:
        # Determine old value from tracked state
        old_value = self._parts_state.get(part_id, None)
        # Update all rows with this part_id in the current model
        ap_col = self._col_indices["ap"]
        for row in range(self.model.rowCount()):
            pid = self.model.data(self.model.index(row, 0), PartIdRole)
            # Some models put PN at col 0, others Reference. PartIdRole is on all cells
            if pid != part_id:
                # try any col
                matched = False
                for c in range(self.model.columnCount()):
                    if self.model.data(self.model.index(row, c), PartIdRole) == part_id:
                        matched = True
                        break
                if not matched:
                    continue
            self.model.setData(self.model.index(row, ap_col), value or "")

        if self.apply_act.isChecked():
            # Persist immediately
            try:
                if value not in ("active", "passive"):
                    # Treat empty click as passive transition handled by delegate; here guard
                    value = "passive"
                with app_state.get_session() as session:
                    services.update_part_active_passive(session, part_id, value)
            except Exception as exc:  # pragma: no cover - depends on DB
                QMessageBox.warning(self, "Update failed", str(exc))
                # revert UI
                for row in range(self.model.rowCount()):
                    for c in range(self.model.columnCount()):
                        if self.model.data(self.model.index(row, c), PartIdRole) == part_id:
                            self.model.setData(self.model.index(row, ap_col), old_value or "")
                return
            self._parts_state[part_id] = value
        else:
            # Stage for save
            if value not in ("active", "passive"):
                value = "passive"
            self._dirty_parts[part_id] = value
            self.save_act.setEnabled(True)

    def _on_apply_toggled(self, checked: bool) -> None:
        if checked and self._dirty_parts:
            self._save_changes()

    def _save_changes(self) -> None:
        if not self._dirty_parts:
            return
        failures = []
        for part_id, value in list(self._dirty_parts.items()):
            try:
                with app_state.get_session() as session:
                    services.update_part_active_passive(session, part_id, value)
            except Exception as exc:
                failures.append(str(exc))
                # revert
                old_val = self._parts_state.get(part_id, None)
                for row in range(self.model.rowCount()):
                    for c in range(self.model.columnCount()):
                        if self.model.data(self.model.index(row, c), PartIdRole) == part_id:
                            self.model.setData(self.model.index(row, self._col_indices["ap"]), old_val or "")
            else:
                self._parts_state[part_id] = value
                del self._dirty_parts[part_id]
        if failures:
            QMessageBox.warning(self, "Save failed", "; ".join(failures))
        else:
            QMessageBox.information(self, "Saved", "Changes saved.")
        self.save_act.setEnabled(bool(self._dirty_parts))

    def _on_view_mode_changed(self, mode: str, checked: bool) -> None:
        if not checked:
            return
        if mode not in ("by_pn", "by_ref"):
            return
        self._view_mode = mode
        self._settings.setValue("view_mode", self._view_mode)
        # Rebuild model and columns menu to reflect new structure
        self._rebuild_model()

    def _autosize_window_to_columns(self) -> None:
        try:
            self.table.resizeColumnsToContents()
            total_w = self.table.verticalHeader().width() + (self.table.frameWidth() * 2)
            for c in range(self.model.columnCount()):
                if self.table.isColumnHidden(c):
                    continue
                w = self.table.columnWidth(c)
                total_w += w
            # Add a bit for scrollbar and padding
            total_w += 24
            # Keep a reasonable max to avoid huge windows
            total_w = min(max(total_w, 900), 1800)
            self.resize(total_w, max(self.height(), 600))
        except Exception:
            # Best-effort sizing; ignore issues in headless envs
            pass

    def closeEvent(self, event) -> None:  # pragma: no cover - UI glue
        # Persist column settings per mode
        mode_key = "cols_by_pn" if self._view_mode == "by_pn" else "cols_by_ref"
        for idx, act in enumerate(self._column_actions):
            self._settings.setValue(f"{mode_key}/col{idx}_visible", act.isChecked())
            self._settings.setValue(f"{mode_key}/col{idx}_width", self.table.columnWidth(idx))
        super().closeEvent(event)

    def _copy_selection(self) -> None:
        model = self.table.model()  # proxy
        if model is None:
            return
        idxs = self.table.selectionModel().selectedIndexes() if self.table.selectionModel() else []
        if not idxs:
            cur = self.table.currentIndex()
            if cur.isValid():
                idxs = [cur]
        if not idxs:
            return
        allowed_cols = {self._col_indices.get("pn"), self._col_indices.get("ref")}
        # Filter to allowed columns
        idxs = [i for i in idxs if i.column() in allowed_cols]
        if not idxs:
            return
        # Build grid by row/col
        rows = sorted({i.row() for i in idxs})
        cols = sorted({i.column() for i in idxs})
        lines = []
        for r in rows:
            vals = []
            for c in cols:
                idx = next((i for i in idxs if i.row() == r and i.column() == c), None)
                vals.append(str(model.data(idx) or "") if idx is not None else "")
            lines.append("\t".join(vals))
        text = "\n".join(lines)
        QGuiApplication.clipboard().setText(text)

    # ------------------------------------------------------------------
    def _icon_for_pdf(self) -> QIcon:
        # Try bundled icons
        try:
            from pathlib import Path
            p = Path(__file__).resolve().parent / "icons" / "pdf.png"
            if p.exists():
                return QIcon(str(p))
        except Exception:
            pass
        # Fallback to style icon
        return self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)

    def _icon_for_plus(self) -> QIcon:
        try:
            from pathlib import Path
            p = Path(__file__).resolve().parent / "icons" / "plus.png"
            if p.exists():
                return QIcon(str(p))
        except Exception:
            pass
        return self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)

    def _install_datasheet_widgets(self) -> None:
        # Rebuild buttons for Datasheet column
        ds_col = self._col_indices.get("ds")
        if ds_col is None:
            return
        for r in range(self.model.rowCount()):
            src_idx = self.model.index(r, ds_col)
            # find part_id on this row
            part_id = None
            for c in range(self.model.columnCount()):
                pid = self.model.data(self.model.index(r, c), PartIdRole)
                if pid is not None:
                    part_id = pid
                    break
            if part_id is None:
                continue
            path = self._part_datasheets.get(part_id)
            btn = QToolButton(self.table)
            if path and Path(path).exists():
                btn.setIcon(self._icon_for_pdf())
                btn.setToolTip("Open datasheet")
                btn.clicked.connect(lambda _=False, p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
            else:
                if path and not Path(path).exists():
                    btn.setToolTip("Stored path not found. Re-attach?")
                else:
                    btn.setToolTip("Attach datasheet")
                btn.setIcon(self._icon_for_plus())
                btn.clicked.connect(lambda _=False, pid=part_id: self._open_attach_dialog(pid))
            proxy_idx = self.proxy.mapFromSource(src_idx)
            self.table.setIndexWidget(proxy_idx, btn)

    def _open_attach_dialog(self, part_id: int) -> None:
        from .datasheet_attach_dialog import DatasheetAttachDialog
        dlg = DatasheetAttachDialog(part_id, self)
        dlg.attached.connect(lambda canonical, pid=part_id: self._on_datasheet_attached(pid, canonical))
        dlg.exec()

    def _on_datasheet_attached(self, part_id: int, canonical: str) -> None:
        # Update mapping and refresh buttons for affected rows
        self._part_datasheets[part_id] = canonical
        ds_col = self._col_indices.get("ds")
        if ds_col is not None:
            for r in range(self.model.rowCount()):
                for c in range(self.model.columnCount()):
                    if self.model.data(self.model.index(r, c), PartIdRole) == part_id:
                        self.model.setData(self.model.index(r, ds_col), canonical)
                        break
        self._install_datasheet_widgets()


class WrapTextDelegate(QStyledItemDelegate):
    """Delegate that wraps long text (e.g., References) within column width."""

    def paint(self, painter: QPainter, option: 'QStyleOptionViewItem', index):  # pragma: no cover - UI glue
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # Draw base item without text
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        # Draw wrapped text
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        topt = QTextOption()
        topt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(topt)
        doc.setTextWidth(opt.rect.width())
        doc.setPlainText(text)
        painter.save()
        painter.translate(opt.rect.topLeft())
        doc.drawContents(painter, QRectF(0, 0, opt.rect.width(), opt.rect.height()))
        painter.restore()

    def sizeHint(self, option: 'QStyleOptionViewItem', index):  # pragma: no cover - UI glue
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        topt = QTextOption()
        topt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(topt)
        # Provide a sensible width (fallback if opt.rect is empty)
        width = max(100, opt.rect.width())
        doc.setTextWidth(width)
        doc.setPlainText(opt.text)
        s = doc.size()
        # Add padding
        return QSize(int(width), int(s.height()) + 6)


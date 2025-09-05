"""BOM editor for classifying parts as active or passive.

Features implemented:
- Clickable pill switch (no combobox) cycling empty â†’ passive â†’ active â†’ passive â†’ â€¦
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
    QLabel, QMessageBox, QSpinBox, QComboBox, QFileDialog, QStyleOptionButton
)
from PyQt6.QtGui import QKeySequence, QPainter, QBrush, QColor, QDesktopServices, QGuiApplication, QTextDocument, QTextOption
import logging
from .. import services
from ..logic.autofill_rules import infer_from_pn_and_desc
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
        self._skip_cols: set[int] = set()

    def setFilterString(self, text: str) -> None:
        self._filter = text.lower()
        self.invalidateFilter()

    def setReferenceColumn(self, col: Optional[int]) -> None:
        self._ref_col = col
        self.invalidate()

    def setSkipColumns(self, cols: set[int]) -> None:
        self._skip_cols = set(cols)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent):  # pragma: no cover - Qt glue
        if not self._filter:
            return True
        model = self.sourceModel()
        cols = model.columnCount()
        for col in range(cols):
            if col in self._skip_cols:
                continue
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

    Values: None â†’ 'passive' â†’ 'active' â†’ 'passive' â†’ â€¦
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
            text = "â€”"

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


class TestMethodDelegate(QStyledItemDelegate):
    """Delegate providing a combobox for selecting test method."""

    methodChanged = pyqtSignal(int, str)  # part_id, method

    def createEditor(self, parent, option, index):  # pragma: no cover - Qt glue
        combo = QComboBox(parent)
        combo.addItems(["", "Macro", "Complex", "Quick test (QT)", "Python code"])
        combo.activated.connect(lambda _=0, w=combo: self._commit_close(w))
        return combo

    def setEditorData(self, editor: QComboBox, index):  # pragma: no cover - Qt glue
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor: QComboBox, model, index):  # pragma: no cover - Qt glue
        text = editor.currentText()
        model.setData(index, text)
        part_id = index.data(PartIdRole)
        self.methodChanged.emit(part_id, text)

    def editorEvent(self, event, model, option, index):  # pragma: no cover - Qt glue
        if event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if option.widget:
                option.widget.openPersistentEditor(index)
            return True
        return False

    def _commit_close(self, editor):  # pragma: no cover - Qt glue
        self.commitData.emit(editor)
        self.closeEditor.emit(editor)




class TestDetailDelegate(QStyledItemDelegate):
    """Delegate rendering a button-like area for test detail actions."""

    detailClicked = pyqtSignal(int)  # part_id

    def paint(self, painter: QPainter, option, index):  # pragma: no cover - UI glue
        text = index.data() or ""
        enabled = text != "â€”"
        btn = QStyleOptionButton()
        btn.rect = option.rect.adjusted(4, 4, -4, -4)
        btn.text = text
        btn.state = QStyle.StateFlag.State_Raised
        if enabled:
            btn.state |= QStyle.StateFlag.State_Enabled
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_PushButton, btn, painter)

    def editorEvent(self, event, model, option, index):  # pragma: no cover - Qt glue
        if event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if (index.data() or "") == "â€”":
                return False
            part_id = index.data(PartIdRole)
            self.detailClicked.emit(part_id)
            return True
        return False

    def createEditor(self, parent, option, index):  # pragma: no cover - no inline editor
        return None


class TestDetailDelegate(QStyledItemDelegate):
    """Delegate for Test Detail column.

    - When Test Method is 'Macro', shows a combobox with function options.
    - Otherwise renders a button-like area and emits detailClicked on click.
    """

    detailClicked = pyqtSignal(int)  # part_id
    detailChanged = pyqtSignal(int, object)  # part_id, new_detail (str|None)

    def __init__(self, parent=None, options: list[str] | None = None, test_method_col: Optional[int] = None) -> None:
        super().__init__(parent)
        self._options = options or []
        self._tm_col = test_method_col

    def set_test_method_col(self, col: int) -> None:
        self._tm_col = col

    def _method_for_index(self, index) -> str:
        try:
            if self._tm_col is None:
                return ""
            model = index.model()  # proxy
            tm_idx = model.index(index.row(), self._tm_col)
            return (model.data(tm_idx) or "")
        except Exception:
            return ""

    def paint(self, painter: QPainter, option, index):  # pragma: no cover - UI glue
        method = (self._method_for_index(index) or "")
        if method == "Macro":
            # Leave space for editor; draw base item look
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            style = opt.widget.style() if opt.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
            return
        # Default: button-like area
        text = index.data() or ""
        enabled = bool(text)
        btn = QStyleOptionButton()
        btn.rect = option.rect.adjusted(4, 4, -4, -4)
        btn.text = text
        btn.state = QStyle.StateFlag.State_Raised
        if enabled:
            btn.state |= QStyle.StateFlag.State_Enabled
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_PushButton, btn, painter)

    def editorEvent(self, event, model, option, index):  # pragma: no cover - Qt glue
        if event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            method = (self._method_for_index(index) or "")
            if method == "Macro":
                if option.widget:
                    option.widget.openPersistentEditor(index)
                return True
            # disabled placeholder check
            if not (index.data() or ""):
                return False
            part_id = index.data(PartIdRole)
            self.detailClicked.emit(part_id)
            return True
        return False

    def createEditor(self, parent, option, index):  # pragma: no cover - Qt glue
        method = (self._method_for_index(index) or "")
        if method != "Macro":
            return None
        combo = QComboBox(parent)
        items = [""] + list(self._options)
        combo.addItems(items)
        combo.activated.connect(lambda _=0, w=combo: self._commit_close(w))
        return combo

    def setEditorData(self, editor: QComboBox, index):  # pragma: no cover - Qt glue
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor: QComboBox, model, index):  # pragma: no cover - Qt glue
        text = (editor.currentText() or "").strip()
        model.setData(index, text)
        part_id = index.data(PartIdRole)
        self.detailChanged.emit(part_id, text or None)

    def _commit_close(self, editor):  # pragma: no cover - Qt glue
        self.commitData.emit(editor)
        self.closeEditor.emit(editor)

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
        # Track parts with an in-progress Auto Datasheet operation
        self._datasheet_loading: set[int] = set()
        self._test_assignments: Dict[int, dict] = {}
        self._part_packages: Dict[int, Optional[str]] = {}
        self._dirty_packages: Dict[int, Optional[str]] = {}
        self._part_values: Dict[int, Optional[str]] = {}
        self._dirty_values: Dict[int, Optional[str]] = {}
        self._tolerances: dict[int, tuple[Optional[str], Optional[str]]] = {}
        self._dirty_tolerances: dict[int, tuple[Optional[str], Optional[str]]] = {}
        self._dirty_tests: set[int] = set()
        self._locked_parts: set[int] = set()
        # View mode: 'by_pn' or 'by_ref'
        self._view_mode = self._settings.value("view_mode", "by_pn")
        self._col_indices = {  # will be updated on model rebuild
            "pn": 0,
            "ref": 1,
            "desc": 2,
            "mfg": 3,
            "ap": 4,
            "ds": 5,
            "test_method": 6,
            "test_detail": 7,
            "package": 8,
            "value": 9,
            "tol_p": 10,
            "tol_n": 11,
        }

        layout = QVBoxLayout(self)
        self.setWindowTitle(f"BOM Editor â€” Assembly {assembly_id}")
        self.toolbar = QToolBar()
        layout.addWidget(self.toolbar)

        # Filter
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filterâ€¦")
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

        # Visible lines control
        self.lines_label = QLabel("Lines:")
        self.toolbar.addWidget(self.lines_label)
        self.lines_spin = QSpinBox()
        self.lines_spin.setRange(1, 10)
        self.lines_spin.setValue(self._settings.value("lines", 1, type=int))
        self.toolbar.addWidget(self.lines_spin)

        # Apply immediately toggle
        self.apply_act = QAction("Apply Immediately", self)
        self.apply_act.setCheckable(True)
        self.toolbar.addAction(self.apply_act)

        # Save button
        self.save_act = QAction("Save", self)
        self.save_act.setEnabled(False)
        self.toolbar.addAction(self.save_act)

        # Autofill button
        self.autofill_act = QAction("Autofill", self)
        self.toolbar.addAction(self.autofill_act)

        # Auto datasheet button
        self.auto_ds_act = QAction("Auto Datasheet…", self)
        self.auto_ds_act.setEnabled(False)
        self.auto_ds_act.triggered.connect(self._auto_datasheet)
        self.toolbar.addAction(self.auto_ds_act)

        # BOM to VIVA export
        self.export_viva_act = QAction("BOM to VIVA", self)
        self.export_viva_act.triggered.connect(self._on_export_viva)
        self.toolbar.addAction(self.export_viva_act)

        # Table
        self.table = QTableView()
        layout.addWidget(self.table)
        self.model = QStandardItemModel(0, 5, self)
        self.model.itemChanged.connect(self._on_item_changed)
        self._updating = False
        # headers set in _rebuild_model()
        self.proxy = BOMFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        # Delegates
        self.delegate = CycleToggleDelegate(self.table)
        self.delegate.valueChanged.connect(self._on_value_changed)
        self.wrap_delegate = WrapTextDelegate(self.table)
        self.wrap_delegate.set_line_count(self.lines_spin.value())
        self.method_delegate = TestMethodDelegate(self.table)
        self.method_delegate.methodChanged.connect(self._on_method_changed)
        # Function options loaded for test detail delegate
        self._function_options = self._load_function_options()
        self.detail_delegate = TestDetailDelegate(self.table, options=self._function_options)
        self.detail_delegate.detailClicked.connect(self._on_detail_clicked)
        self.detail_delegate.detailChanged.connect(self._on_detail_changed)
        self.lines_spin.valueChanged.connect(self._on_lines_changed)
        # Column assignment done in _rebuild_model

        self.filter_edit.textChanged.connect(self.proxy.setFilterString)
        self.apply_act.toggled.connect(self._on_apply_toggled)
        self.save_act.triggered.connect(self._save_changes)
        self.autofill_act.triggered.connect(self._autofill_fields)
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
        self._on_lines_changed(self.lines_spin.value())
        if self.table.selectionModel():
            self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_auto_ds_act())
        self._update_auto_ds_act()

    # ------------------------------------------------------------------
    def _setup_columns_menu(self) -> None:
        # Clear previous actions
        self.columns_menu.clear()
        self._column_actions: list[QAction] = []
        if self._view_mode == "by_pn":
            headers = [
                "PN",
                "References",
                "Description",
                "Manufacturer",
                "Active/Passive",
                "Datasheet",
                "Test Method",
                "Test Detail",
                "Package",
                "Value",
                "Tol Positive",
                "Tol Negative",
            ]
        else:
            headers = [
                "Reference",
                "PN",
                "Description",
                "Manufacturer",
                "Active/Passive",
                "Datasheet",
                "Test Method",
                "Test Detail",
                "Package",
                "Value",
                "Tol Positive",
                "Tol Negative",
            ]
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
        # Assign delegates
        ap_col = self._col_indices["ap"]
        self.table.setItemDelegateForColumn(ap_col, self.delegate)
        ref_col = self._col_indices["ref"]
        self.table.setItemDelegateForColumn(ref_col, self.wrap_delegate)
        tm_col = self._col_indices["test_method"]
        self.table.setItemDelegateForColumn(tm_col, self.method_delegate)
        td_col = self._col_indices["test_detail"]
        # Keep delegate up to date on column positions and options
        self.detail_delegate.set_test_method_col(tm_col)
        self.table.setItemDelegateForColumn(td_col, self.detail_delegate)
        # Ensure combo is visible for 'Macro' rows
        self._sync_detail_editors()
        # Set reference column for natural sorting and skip AP from filter
        self.proxy.setReferenceColumn(ref_col)
        self.proxy.setSkipColumns({ap_col})

    def _load_data(self) -> None:
        # Keep canonical raw rows
        with app_state.get_session() as session:
            self._rows_raw = services.get_joined_bom_for_assembly(session, self._assembly_id)
        # Seed parts state from DB
        self._parts_state.clear()
        self._part_datasheets.clear()
        self._part_packages.clear()
        self._part_values.clear()
        self._tolerances.clear()
        for r in self._rows_raw:
            # Use DB-provided value; may be None in future schema
            self._parts_state[r.part_id] = getattr(r, "active_passive", None)
            self._part_datasheets[r.part_id] = getattr(r, "datasheet_url", None)
            self._part_packages[r.part_id] = getattr(r, "package", None)
            self._part_values[r.part_id] = getattr(r, "value", None)
            self._tolerances[r.part_id] = (getattr(r, "tol_p", None), getattr(r, "tol_n", None))
        # Overlay any saved test assignments from settings for visible parts
        self._load_test_assignments_from_settings()

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
        self.model.setColumnCount(12)
        if self._view_mode == "by_pn":
            # Column map
            self._col_indices = {
                "pn": 0,
                "ref": 1,
                "desc": 2,
                "mfg": 3,
                "ap": 4,
                "ds": 5,
                "test_method": 6,
                "test_detail": 7,
                "package": 8,
                "value": 9,
                "tol_p": 10,
                "tol_n": 11,
            }
            self._build_by_pn()
        else:
            self._col_indices = {
                "ref": 0,
                "pn": 1,
                "desc": 2,
                "mfg": 3,
                "ap": 4,
                "ds": 5,
                "test_method": 6,
                "test_detail": 7,
                "package": 8,
                "value": 9,
                "tol_p": 10,
                "tol_n": 11,
            }
            self._build_by_ref()
        # Setup columns menu and sorting based on new headers
        self._setup_columns_menu()
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(self._col_indices["ref"], Qt.SortOrder.AscendingOrder)
        # Defer autosize until columns are applied
        QTimer.singleShot(0, self._autosize_window_to_columns)
        QTimer.singleShot(0, self._install_datasheet_widgets)
        QTimer.singleShot(0, self._sync_detail_editors)

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

            ta = self._test_assignments.get(part_id, {"method": "", "qt_path": None})
            row_items = [
                QStandardItem(rows[0].part_number),
                QStandardItem(refs_str),
                QStandardItem(rows[0].description or ""),
                QStandardItem(rows[0].manufacturer or ""),
                QStandardItem(mode_val or ""),
                QStandardItem(""),
                QStandardItem(ta["method"]),
                QStandardItem(self._detail_text_for(ta)),
                QStandardItem(self._part_packages.get(part_id) or ""),
                QStandardItem(self._part_values.get(part_id) or ""),
                QStandardItem(self._tolerances.get(part_id, (None, None))[0] or ""),
                QStandardItem(self._tolerances.get(part_id, (None, None))[1] or ""),
            ]
            for i, it in enumerate(row_items):
                flags = it.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                editable = {
                    self._col_indices["test_method"],
                    self._col_indices["test_detail"],
                    self._col_indices["package"],
                    self._col_indices["value"],
                    self._col_indices["tol_p"],
                    self._col_indices["tol_n"],
                }
                if i not in editable or part_id in self._locked_parts:
                    flags &= ~Qt.ItemFlag.ItemIsEditable
                it.setFlags(flags)
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
            ta = self._test_assignments.get(r.part_id, {"method": "", "qt_path": None})
            items = [
                QStandardItem(r.reference),
                QStandardItem(r.part_number),
                QStandardItem(r.description or ""),
                QStandardItem(r.manufacturer or ""),
                QStandardItem(mode_val or ""),
                QStandardItem(""),
                QStandardItem(ta["method"]),
                QStandardItem(self._detail_text_for(ta)),
                QStandardItem(self._part_packages.get(r.part_id) or ""),
                QStandardItem(self._part_values.get(r.part_id) or ""),
                QStandardItem(self._tolerances.get(r.part_id, (None, None))[0] or ""),
                QStandardItem(self._tolerances.get(r.part_id, (None, None))[1] or ""),
            ]
            for i, it in enumerate(items):
                flags = it.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                editable = {
                    self._col_indices["test_method"],
                    self._col_indices["test_detail"],
                    self._col_indices["package"],
                    self._col_indices["value"],
                    self._col_indices["tol_p"],
                    self._col_indices["tol_n"],
                }
                if i not in editable or r.part_id in self._locked_parts:
                    flags &= ~Qt.ItemFlag.ItemIsEditable
                it.setFlags(flags)
                it.setData(r.part_id, PartIdRole)
                if i == self._col_indices["ap"]:
                    it.setData(mode_val, ModeRole)
                if i == self._col_indices["ds"]:
                    it.setData(self._part_datasheets.get(r.part_id), DatasheetRole)
            self.model.appendRow(items)
    def _detail_label_for(self, ta: dict) -> str:
        # Delegate to unified builder
        return self._detail_text_for(ta)

    def _refresh_rows_for_part(self, part_id: int) -> None:
        ta = self._test_assignments.get(part_id, {"method": "", "qt_path": None})
        method = ta.get("method", "")
        detail = self._detail_text_for(ta)
        tm_col = self._col_indices.get("test_method")
        td_col = self._col_indices.get("test_detail")
        for row in range(self.model.rowCount()):
            for c in range(self.model.columnCount()):
                if self.model.data(self.model.index(row, c), PartIdRole) == part_id:
                    if tm_col is not None:
                        self.model.setData(self.model.index(row, tm_col), method)
                    if td_col is not None:
                        self.model.setData(self.model.index(row, td_col), detail)
                    break

    def _on_method_changed(self, part_id: int, new_method: str) -> None:
        ta = self._test_assignments.setdefault(part_id, {"method": "", "qt_path": None})
        ta["method"] = new_method
        if new_method != "Quick test (QT)":
            ta["qt_path"] = None
        self._refresh_rows_for_part(part_id)
        # Ensure editors reflect Macro selection state
        self._sync_detail_editors()
        # Persist/stage according to Apply toggle
        if self.apply_act.isChecked():
            self._persist_test_assignment(part_id)
        else:
            self._dirty_tests.add(part_id)
            self.save_act.setEnabled(True)

    def _on_detail_clicked(self, part_id: int) -> None:
        ta = self._test_assignments.setdefault(part_id, {"method": "", "qt_path": None})
        method = ta.get("method", "")
        if method == "Macro":
            self._show_stub_dialog(
                "This would open the Macro selector (closed list) and save the chosen Macro for this PN. (Not implemented yet)."
            )
        elif method == "Complex":
            self._show_stub_dialog(
                "This would open the Complex linker (select complex from MDB) and link it to this PN. (Not implemented yet)."
            )
        elif method == "Quick test (QT)":
            path, _ = QFileDialog.getOpenFileName(self, "Select Quick Test XML", "", "Quick Test XML (*.xml)")
            if path:
                ta["qt_path"] = path
                self._refresh_rows_for_part(part_id)
        elif method == "Python code":
            self._show_stub_dialog(
                "This would open a project chooser (folder with code, description, library links) and link it to this PN. (Not implemented yet)."
            )


    def _on_detail_changed(self, part_id: int, new_detail: Optional[str]) -> None:
        # Record selection and refresh label, keep persistent editor
        ta = self._test_assignments.setdefault(part_id, {"method": "", "qt_path": None})
        ta["detail"] = new_detail or None
        self._refresh_rows_for_part(part_id)
        # Keep editor visible where applicable
        self._sync_detail_editors()
        # Persist/stage according to Apply toggle
        if self.apply_act.isChecked():
            self._persist_test_assignment(part_id)
        else:
            self._dirty_tests.add(part_id)
            self.save_act.setEnabled(True)

    def _sync_detail_editors(self) -> None:
        # Open persistent combobox editor for rows where method == 'Macro'
        model = self.table.model()  # proxy
        if model is None:
            return
        tm_col = self._col_indices.get("test_method")
        td_col = self._col_indices.get("test_detail")
        if tm_col is None or td_col is None:
            return
        rows = model.rowCount()
        for r in range(rows):
            tm_idx = model.index(r, tm_col)
            method = model.data(tm_idx) or ""
            td_idx = model.index(r, td_col)
            if method == "Macro":
                self.table.openPersistentEditor(td_idx)
            else:
                self.table.closePersistentEditor(td_idx)

    # ------------------------------------------------------------------
    # Test assignment persistence using QSettings
    def _persist_test_assignment(self, part_id: int) -> None:
        ta = self._test_assignments.get(part_id) or {}
        self._settings.setValue(f"test/method/{part_id}", ta.get("method") or "")
        self._settings.setValue(f"test/detail/{part_id}", ta.get("detail") or "")
        self._settings.setValue(f"test/qt_path/{part_id}", ta.get("qt_path") or "")

    def _load_test_assignments_from_settings(self) -> None:
        # Populate self._test_assignments with any saved values for parts in current view
        part_ids = {r.part_id for r in self._rows_raw}
        for pid in part_ids:
            method = self._settings.value(f"test/method/{pid}", "")
            detail = self._settings.value(f"test/detail/{pid}", "")
            qt_path = self._settings.value(f"test/qt_path/{pid}", "")
            if any([(method or "").strip(), (detail or "").strip(), (qt_path or "").strip()]):
                ta = self._test_assignments.setdefault(pid, {"method": "", "qt_path": None})
                ta["method"] = (method or "").strip()
                ta["detail"] = (detail or "").strip() or None
                ta["qt_path"] = (qt_path or "").strip() or None

    def _show_stub_dialog(self, message: str) -> None:
        from .dialogs.tm_stub_dialog import TestMethodStubDialog

        dlg = TestMethodStubDialog(message, self)
        dlg.exec()

    # ------------------------------------------------------------------
    def _detail_text_for(self, ta: dict) -> str:
        method = ta.get("method") or ""
        if method == "Macro":
            sel = ta.get("detail") or ta.get("macro") or None
            return sel or "Choose Macro..."
        if method == "Complex":
            return "Link Complex..."
        if method == "Quick test (QT)":
            path = ta.get("qt_path")
            return Path(path).name if path else "Select QT XML..."
        if method == "Python code":
            return "Open Python Project..."
        return ""

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

    def _fanout_part_field(self, part_id: int, field: str, value: Optional[str]) -> None:
        col = self._col_indices.get(field)
        if col is None:
            return
        self._updating = True
        for row in range(self.model.rowCount()):
            if self.model.data(self.model.index(row, col), PartIdRole) == part_id:
                self.model.setData(self.model.index(row, col), value or "")
        self._updating = False

    def _persist_or_stage_package(self, part_id: int, value: Optional[str]) -> None:
        if self.apply_act.isChecked():
            try:
                with app_state.get_session() as session:
                    services.update_part_package(session, part_id, value or "")
            except Exception as exc:
                QMessageBox.warning(self, "Update failed", str(exc))
                old_val = self._part_packages.get(part_id, None)
                self._fanout_part_field(part_id, "package", old_val)
                return
            self._part_packages[part_id] = value or None
            self._dirty_packages.pop(part_id, None)
        else:
            self._dirty_packages[part_id] = value or None
            self.save_act.setEnabled(True)

    def _persist_or_stage_value(self, part_id: int, value: Optional[str]) -> None:
        if self.apply_act.isChecked():
            try:
                with app_state.get_session() as session:
                    services.update_part_value(session, part_id, value or "")
            except Exception as exc:
                QMessageBox.warning(self, "Update failed", str(exc))
                old_val = self._part_values.get(part_id, None)
                self._fanout_part_field(part_id, "value", old_val)
                return
            self._part_values[part_id] = value or None
            self._dirty_values.pop(part_id, None)
        else:
            self._dirty_values[part_id] = value or None
            self.save_act.setEnabled(True)

    def _persist_or_stage_tolerances(
        self, part_id: int, tol_p: Optional[str], tol_n: Optional[str]
    ) -> None:
        if self.apply_act.isChecked():
            try:
                with app_state.get_session() as session:
                    services.update_part_tolerances(session, part_id, tol_p, tol_n)
            except Exception as exc:
                QMessageBox.warning(self, "Update failed", str(exc))
                old = self._tolerances.get(part_id, (None, None))
                self._fanout_part_field(part_id, "tol_p", old[0])
                self._fanout_part_field(part_id, "tol_n", old[1])
                return
            self._tolerances[part_id] = (tol_p, tol_n)
            self._dirty_tolerances.pop(part_id, None)
        else:
            self._tolerances[part_id] = (tol_p, tol_n)
            self._dirty_tolerances[part_id] = (tol_p, tol_n)
            self.save_act.setEnabled(True)

    def _on_item_changed(self, item: QStandardItem) -> None:
        if self._updating:
            return
        part_id = item.data(PartIdRole)
        if part_id is None:
            return
        col = item.column()
        text = (item.text() or "").strip() or None
        if col == self._col_indices.get("package"):
            self._part_packages[part_id] = text
            self._fanout_part_field(part_id, "package", text)
            self._persist_or_stage_package(part_id, text)
        elif col == self._col_indices.get("value"):
            self._part_values[part_id] = text
            self._fanout_part_field(part_id, "value", text)
            self._persist_or_stage_value(part_id, text)
        elif col == self._col_indices.get("tol_p"):
            cur = self._tolerances.get(part_id, (None, None))
            new_pair = (text, cur[1])
            self._tolerances[part_id] = new_pair
            self._fanout_part_field(part_id, "tol_p", text)
            self._persist_or_stage_tolerances(part_id, new_pair[0], new_pair[1])
        elif col == self._col_indices.get("tol_n"):
            cur = self._tolerances.get(part_id, (None, None))
            new_pair = (cur[0], text)
            self._tolerances[part_id] = new_pair
            self._fanout_part_field(part_id, "tol_n", text)
            self._persist_or_stage_tolerances(part_id, new_pair[0], new_pair[1])

    def _autofill_fields(self) -> None:
        proxy = self.table.model()
        if proxy is None:
            return
        pn_col = self._col_indices.get("pn")
        desc_col = self._col_indices.get("desc")
        rows = proxy.rowCount()
        for r in range(rows):
            pn = str(proxy.data(proxy.index(r, pn_col)) or "")
            desc = str(proxy.data(proxy.index(r, desc_col)) or "")
            part_id = proxy.data(proxy.index(r, pn_col), PartIdRole)
            if part_id is None:
                part_id = proxy.data(proxy.index(r, desc_col), PartIdRole)
            if part_id is None:
                continue
            ar = infer_from_pn_and_desc(pn, desc)
            if ar.package and not self._part_packages.get(part_id):
                self._part_packages[part_id] = ar.package
                self._fanout_part_field(part_id, "package", ar.package)
                self._persist_or_stage_package(part_id, ar.package)
            if ar.value and not self._part_values.get(part_id):
                self._part_values[part_id] = ar.value
                self._fanout_part_field(part_id, "value", ar.value)
                self._persist_or_stage_value(part_id, ar.value)
            if ar.tol_pos and ar.tol_neg:
                cur_p, cur_n = self._tolerances.get(part_id, (None, None))
                if not cur_p and not cur_n:
                    pair = (ar.tol_pos, ar.tol_neg)
                    self._tolerances[part_id] = pair
                    self._fanout_part_field(part_id, "tol_p", pair[0])
                    self._fanout_part_field(part_id, "tol_n", pair[1])
                    self._persist_or_stage_tolerances(part_id, pair[0], pair[1])

    def _auto_datasheet(self) -> None:
        proxy = self.table.model()
        sel = self.table.selectionModel()
        if proxy is None or sel is None or not sel.selectedIndexes():
            QMessageBox.information(self, "Auto Datasheet", "Select one or more rows first.")
            return
        rows = sorted({i.row() for i in sel.selectedIndexes()})
        pn_col = self._col_indices.get("pn")
        desc_col = self._col_indices.get("desc")
        mfg_col = self._col_indices.get("mfg")
        work = []
        for r in rows:
            pn = str(proxy.data(proxy.index(r, pn_col)) or "")
            desc = str(proxy.data(proxy.index(r, desc_col)) or "")
            mfg = str(proxy.data(proxy.index(r, mfg_col)) or "")
            pid = proxy.data(proxy.index(r, pn_col), PartIdRole) or proxy.data(proxy.index(r, desc_col), PartIdRole)
            if pid:
                from .auto_datasheet_dialog import WorkItem, AutoDatasheetDialog
                work.append(WorkItem(part_id=pid, pn=pn, mfg=mfg, desc=desc))
        if not work:
            QMessageBox.warning(self, "Auto Datasheet", "No resolvable parts in selection.")
            return
        logging.info("BOMEditor: starting Auto Datasheet for %d parts", len(work))
        # Mark selected parts as in-progress and update UI
        self._datasheet_loading |= {w.part_id for w in work}
        QTimer.singleShot(0, self._install_datasheet_widgets)
        dlg = AutoDatasheetDialog(self, work, on_locked_parts_changed=self._set_parts_locked)
        dlg.exec()
        # Refresh datasheet paths for affected parts
        from ..models import Part
        with app_state.get_session() as session:
            for w in work:
                p = session.get(Part, w.part_id)
                if p:
                    self._part_datasheets[w.part_id] = p.datasheet_url
        # Clear loading state and refresh icons
        self._datasheet_loading -= {w.part_id for w in work}
        QTimer.singleShot(0, self._install_datasheet_widgets)

    def _set_parts_locked(self, parts: set[int], lock: bool):
        if lock:
            self._locked_parts |= set(parts)
        else:
            self._locked_parts -= set(parts)
        self._rebuild_model()

    def _update_auto_ds_act(self) -> None:
        sel = self.table.selectionModel()
        self.auto_ds_act.setEnabled(bool(sel and sel.selectedIndexes()))

    def _on_apply_toggled(self, checked: bool) -> None:
        if checked and (
            self._dirty_parts
            or self._dirty_packages
            or self._dirty_values
            or self._dirty_tolerances
        ):
            self._save_changes()

    def _on_lines_changed(self, lines: int) -> None:
        """Adjust row heights when the visible line count changes."""
        self.wrap_delegate.set_line_count(lines)
        self.table.resizeRowsToContents()
        self._settings.setValue("lines", lines)

    def _save_changes(self) -> None:
        if (
            not self._dirty_parts
            and not self._dirty_packages
            and not self._dirty_values
            and not self._dirty_tests
            and not self._dirty_tolerances
        ):
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
        for part_id, pkg in list(self._dirty_packages.items()):
            try:
                with app_state.get_session() as session:
                    services.update_part_package(session, part_id, pkg or "")
            except Exception as exc:
                failures.append(str(exc))
                old_val = self._part_packages.get(part_id, None)
                self._fanout_part_field(part_id, "package", old_val)
            else:
                self._part_packages[part_id] = pkg or None
                del self._dirty_packages[part_id]
        for part_id, val in list(self._dirty_values.items()):
            try:
                with app_state.get_session() as session:
                    services.update_part_value(session, part_id, val or "")
            except Exception as exc:
                failures.append(str(exc))
                old_val = self._part_values.get(part_id, None)
                self._fanout_part_field(part_id, "value", old_val)
            else:
                self._part_values[part_id] = val or None
                del self._dirty_values[part_id]
        for part_id, (tp, tn) in list(self._dirty_tolerances.items()):
            try:
                with app_state.get_session() as session:
                    services.update_part_tolerances(session, part_id, tp, tn)
            except Exception as exc:
                failures.append(str(exc))
                old = self._tolerances.get(part_id, (None, None))
                self._fanout_part_field(part_id, "tol_p", old[0])
                self._fanout_part_field(part_id, "tol_n", old[1])
            else:
                self._tolerances[part_id] = (tp, tn)
                del self._dirty_tolerances[part_id]
        # Persist test assignments via settings
        for part_id in list(self._dirty_tests):
            try:
                self._persist_test_assignment(part_id)
            except Exception as exc:
                failures.append(str(exc))
            else:
                self._dirty_tests.discard(part_id)
        if failures:
            QMessageBox.warning(self, "Save failed", "; ".join(failures))
        else:
            QMessageBox.information(self, "Saved", "Changes saved.")
        self.save_act.setEnabled(
            bool(self._dirty_parts)
            or bool(self._dirty_packages)
            or bool(self._dirty_values)
            or bool(self._dirty_tolerances)
            or bool(self._dirty_tests)
        )

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

    # ------------------------------------------------------------------
    def _collect_table_rows(self) -> list[dict]:
        """Return current table rows as dictionaries for export."""
        tm_col = self._col_indices.get("test_method")
        td_col = self._col_indices.get("test_detail")
        ref_col = self._col_indices.get("ref")
        pn_col = self._col_indices.get("pn")
        rows: list[dict] = []
        for r in range(self.model.rowCount()):
            pn = self.model.data(self.model.index(r, pn_col)) if pn_col is not None else ""
            refs = self.model.data(self.model.index(r, ref_col)) if ref_col is not None else ""
            tm = self.model.data(self.model.index(r, tm_col)) if tm_col is not None else ""
            td = self.model.data(self.model.index(r, td_col)) if td_col is not None else ""
            if self._view_mode == "by_pn":
                ref_list = [x.strip() for x in (refs or "").split(",") if x.strip()]
                for ref in ref_list:
                    rows.append(
                        {
                            "reference": ref,
                            "part_number": pn or "",
                            "test_method": tm or "",
                            "test_detail": td or "",
                        }
                    )
            else:
                rows.append(
                    {
                        "reference": (refs or "").strip(),
                        "part_number": pn or "",
                        "test_method": tm or "",
                        "test_detail": td or "",
                    }
                )
        return rows

    def _on_export_viva(self) -> None:  # pragma: no cover - UI glue
        table_rows = self._collect_table_rows()
        with app_state.get_session() as session:
            try:
                rows = services.build_viva_groups(table_rows, session, self._assembly_id)
            except ValueError as exc:
                QMessageBox.warning(self, "Cannot export", str(exc))
                return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save BOM to VIVA",
            f"BOM_to_VIVA_{self._assembly_id}.txt",
            "Text files (*.txt)",
        )
        if not path:
            return
        services.write_viva_txt(path, rows)
        total_refs = sum(int(r["quantity"]) for r in rows)
        QMessageBox.information(
            self,
            "Export complete",
            f"Exported {len(rows)} groups / {total_refs} references to {path}",
        )

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
    def _load_function_options(self) -> list[str]:
        """Load function options from data/function_list.txt.

        Returns an empty list if not found. Lines starting with '#' are ignored.
        """
        candidates = []
        try:
            here = Path(__file__).resolve()
            candidates.append(here.parents[2] / "data" / "function_list.txt")
            candidates.append(here.parents[1] / "data" / "function_list.txt")
            candidates.append(Path.cwd() / "data" / "function_list.txt")
        except Exception:
            candidates.append(Path.cwd() / "data" / "function_list.txt")
        for p in candidates:
            try:
                if p.exists():
                    lines = p.read_text(encoding="utf-8").splitlines()
                    items = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
                    return items
            except Exception:
                continue
        return []

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
        # Fallback to a generic file icon
        return self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

    def _icon_for_plus(self) -> QIcon:
        try:
            from pathlib import Path
            p = Path(__file__).resolve().parent / "icons" / "plus.png"
            if p.exists():
                return QIcon(str(p))
        except Exception:
            pass
        return self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)

    def _icon_for_loading(self) -> QIcon:
        try:
            from pathlib import Path
            p = Path(__file__).resolve().parent / "icons" / "loading.png"
            if p.exists():
                return QIcon(str(p))
        except Exception:
            pass
        return self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)

    def _icon_for_attached(self) -> QIcon:
        try:
            from pathlib import Path
            p = Path(__file__).resolve().parent / "icons" / "check_green.png"
            if p.exists():
                return QIcon(str(p))
        except Exception:
            pass
        return self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)

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
            if part_id in self._datasheet_loading:
                btn.setIcon(self._icon_for_loading())
                btn.setEnabled(False)
                btn.setToolTip("Searching datasheet...")
            elif path and Path(path).exists():
                btn.setIcon(self._icon_for_attached())
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
    """Delegate that wraps long text (e.g., References) within column width.

    The visible number of lines can be adjusted to change row height.
    """

    def __init__(self, parent=None, lines: int = 1) -> None:
        super().__init__(parent)
        self._lines = lines

    def set_line_count(self, lines: int) -> None:
        self._lines = max(1, lines)

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
        # Provide a sensible width (fallback if opt.rect is empty)
        width = max(100, opt.rect.width())
        line_height = opt.fontMetrics.lineSpacing()
        # Add padding
        return QSize(int(width), int(line_height * self._lines) + 6)





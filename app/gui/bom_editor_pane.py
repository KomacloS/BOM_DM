"""BOM editor for classifying parts as active or passive.



Features implemented:

- Clickable pill switch (no combobox) cycling empty â†' passive â†' active â†' passive â†' â€¦

- Two view modes: By PN (grouped) and By Reference (flat)

- Auto-passive inference for R/L/C when value is empty (UI-only)

- Column visibility per-mode, persisted in QSettings

- Filter works across visible columns; natural sort for reference columns

"""



from __future__ import annotations



from typing import Any, Dict, List, Tuple, Optional

from pathlib import Path
import uuid



from PyQt6.QtGui import (

    QAction,

    QStandardItemModel,

    QStandardItem,

    QIcon,

    QKeySequence,

    QUndoStack,

    QUndoCommand,

)  # QAction/QStandardItem* are in QtGui

from PyQt6.QtCore import (

    Qt,

    QSortFilterProxyModel,

    QModelIndex,

    pyqtSignal,

    QSettings,

    QRect,

    QSize,

    QTimer,

    QUrl,

    QRectF,

    QPersistentModelIndex,

)

from PyQt6.QtWidgets import (

    QWidget, QTableView, QVBoxLayout, QLineEdit, QPushButton, QToolBar, QMenu,

    QToolButton, QStyle, QApplication,

    QStyledItemDelegate, QHeaderView, QAbstractItemView, QStyleOptionViewItem,

    QLabel, QMessageBox, QSpinBox, QComboBox, QFileDialog, QInputDialog, QStyleOptionButton,

    QSplitter, QProgressDialog

)

from PyQt6.QtGui import QPainter, QBrush, QColor, QDesktopServices, QGuiApplication, QTextDocument, QTextOption

import logging

from .. import services

from ..services.datasheets import get_local_open_path

from ..config import (
    DATA_ROOT,
    PDF_VIEWER,
    PDF_VIEWER_PATH,
    PDF_OPEN_DEBUG,
    get_ce_app_exe,
    get_complex_editor_settings,
    get_viva_export_settings,
    LOG_DIR,
)
import subprocess, shutil, time, os

from datetime import datetime

from ..logic.autofill_rules import infer_from_pn_and_desc
from ..logic.prefix_macros import load_prefix_macros, reload_prefix_macros
from . import state as app_state
from .widgets.complex_panel import ComplexPanel
from ..services.export_viva import VivaExportError, VivaExportResult
from ..integration.ce_supervisor import CESupervisor
from ..models import Assembly, TestMode, TestProfile, PartType
from ..domain import complex_linker
from ..domain.complex_linker import ComplexLink


_DEFAULT_MACRO_PREFIXES: list[tuple[str, str]] = [
    ("LED", "LED"),
    ("CR", "DIODE"),
    ("D", "DIODE"),
    ("C", "CAPACITOR"),
    ("R", "RESISTOR"),
    ("L", "INDUCTANCE"),
    ("J", "CONNECTOR"),
    ("P", "CONNECTOR"),
    ("U", "DIGITAL"),
    ("Q", "TRANSISTOR"),
    ("Z", "ZENER"),
    ("Y", "OSCILLATOR"),
]



PartIdRole = Qt.ItemDataRole.UserRole + 1

ModeRole = Qt.ItemDataRole.UserRole + 2  # stores 'active'/'passive'/None in AP column for delegate

DatasheetRole = Qt.ItemDataRole.UserRole + 3

_CE_SUPERVISOR: Optional[CESupervisor] = None

def _get_ce_supervisor() -> CESupervisor:
    global _CE_SUPERVISOR
    if _CE_SUPERVISOR is None:
        _CE_SUPERVISOR = CESupervisor(get_ce_app_exe())
    return _CE_SUPERVISOR


def _coerce_part_type(value: object | None) -> Optional[PartType]:
    if isinstance(value, PartType):
        return value
    if value is None:
        return None
    try:
        return PartType(str(value))
    except ValueError:
        return None


def _default_profile(part_type: Optional[PartType], mode: TestMode) -> TestProfile:
    """Return the default TestProfile for the given part type/mode."""

    if part_type is PartType.passive:
        return TestProfile.passive
    if mode is TestMode.powered:
        return TestProfile.active
    return TestProfile.passive

"""

Additional per-cell data roles

- BOMItemIdRole: primary key of BOMItem in by_ref view for per-row actions.

- LinkUrlRole: stores the actual product link while display text stays empty.

"""

BOMItemIdRole = Qt.ItemDataRole.UserRole + 4

LinkUrlRole = Qt.ItemDataRole.UserRole + 5





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





class NoWheelComboBox(QComboBox):

    """QComboBox that ignores wheel events to prevent accidental changes."""



    def wheelEvent(self, event):  # pragma: no cover - Qt glue

        event.ignore()





class NoWheelSpinBox(QSpinBox):

    """QSpinBox that ignores wheel events to prevent accidental changes."""



    def wheelEvent(self, event):  # pragma: no cover - Qt glue

        event.ignore()





class UndoableStandardItemModel(QStandardItemModel):

    """QStandardItemModel that emits old/new values on changes."""



    changed = pyqtSignal(QModelIndex, object, object)



    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):

        if isinstance(index, QPersistentModelIndex):

            index = QModelIndex(index)

        if role == Qt.ItemDataRole.EditRole:

            old = super().data(index, role)

            res = super().setData(index, value, role)

            if res and old != value:

                self.changed.emit(index, old, value)

            return res

        return super().setData(index, value, role)





class SetCellCommand(QUndoCommand):

    """Undo command encapsulating a single cell edit."""



    def __init__(self, pane: "BOMEditorPane", index: QModelIndex, old: object, new: object) -> None:

        super().__init__("Set Cell")

        self.pane = pane

        self.index = QPersistentModelIndex(index)

        self.old = old

        self.new = new

        # When first pushed the model already contains ``new``.  Track this so

        # that the initial ``redo`` executed by :class:`QUndoStack` is a no-op

        # and does not trigger change handlers again.

        self._first = True



    def undo(self) -> None:  # pragma: no cover - Qt glue

        model = self.pane.model

        self.pane._updating = True

        model.setData(QModelIndex(self.index), self.old)

        self.pane._updating = False



    def redo(self) -> None:  # pragma: no cover - Qt glue

        if self._first:

            self._first = False

            return

        model = self.pane.model

        self.pane._updating = True

        model.setData(QModelIndex(self.index), self.new)

        self.pane._updating = False





class CycleToggleDelegate(QStyledItemDelegate):

    """Delegate that draws a small pill and toggles value on click.



    Values: None â†' 'passive' â†' 'active' â†' 'passive' â†' â€¦

    """



    valueChanged = pyqtSignal(int, object)  # part_id, new_value (str or None)



    def paint(self, painter: QPainter, option, index):  # pragma: no cover - UI glue

        opt = QStyleOptionViewItem(option)

        opt.state &= ~QStyle.StateFlag.State_HasFocus



        # Fill the background based on state so it matches QSS selection/hover

        if opt.state & QStyle.StateFlag.State_Selected:

            painter.fillRect(opt.rect, QColor("#DCEBFF"))

        elif opt.state & QStyle.StateFlag.State_MouseOver:

            painter.fillRect(opt.rect, QColor("#F3F4F6"))

        else:

            painter.fillRect(opt.rect, opt.palette.base().color())



        value = index.data() or None

        rect: QRect = opt.rect.adjusted(6, 4, -6, -4)

        pal = opt.palette



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

            text = ""



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

        combo = NoWheelComboBox(parent)

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



    # Do not force editor on single click; rely on double-click/Edit key

    def editorEvent(self, event, model, option, index):  # pragma: no cover - Qt glue

        return False



    def _commit_close(self, editor):  # pragma: no cover - Qt glue

        self.commitData.emit(editor)

        self.closeEditor.emit(editor)









class TestDetailDelegate(QStyledItemDelegate):

    """Delegate rendering a button-like area for test detail actions."""



    detailClicked = pyqtSignal(int)  # part_id



    def paint(self, painter: QPainter, option, index):  # pragma: no cover - UI glue

        opt = QStyleOptionViewItem(option)

        opt.state &= ~QStyle.StateFlag.State_HasFocus



        # Fill the background based on state so it matches QSS selection/hover

        if opt.state & QStyle.StateFlag.State_Selected:

            painter.fillRect(opt.rect, QColor("#DCEBFF"))

        elif opt.state & QStyle.StateFlag.State_MouseOver:

            painter.fillRect(opt.rect, QColor("#F3F4F6"))

        else:

            painter.fillRect(opt.rect, opt.palette.base().color())



        text = index.data() or ""

        enabled = text != ""

        btn = QStyleOptionButton()

        btn.rect = opt.rect.adjusted(4, 4, -4, -4)

        btn.text = text

        btn.state = QStyle.StateFlag.State_Raised

        if enabled:

            btn.state |= QStyle.StateFlag.State_Enabled

        style = opt.widget.style() if opt.widget else QApplication.style()

        style.drawControl(QStyle.ControlElement.CE_PushButton, btn, painter)



    def editorEvent(self, event, model, option, index):  # pragma: no cover - Qt glue

        if event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:

            if (index.data() or "") == "":

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

        # Render as a normal text cell; editor appears on double-click

        opt = QStyleOptionViewItem(option)

        self.initStyleOption(opt, index)

        style = opt.widget.style() if opt.widget else QApplication.style()

        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)



    def editorEvent(self, event, model, option, index):  # pragma: no cover - Qt glue
        # Treat left-click as an action trigger to open the appropriate dialog/panel.
        try:
            if event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                part_id = index.data(PartIdRole)
                method = str(self._method_for_index(index) or "").strip()
                if isinstance(part_id, int) and method:
                    self.detailClicked.emit(part_id)
                    return True
        except Exception:
            pass
        return False



    def createEditor(self, parent, option, index):  # pragma: no cover - Qt glue

        method = (self._method_for_index(index) or "")

        if method != "Macro":

            return None

        combo = NoWheelComboBox(parent)

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

        self._datasheet_failed: set[int] = set()

        self._part_manual_links: Dict[int, str] = {}

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
        self._assembly_mode: TestMode = TestMode.unpowered
        self._resolved_tests: Dict[int, dict] = {}

        # Track manual edits to the Link column before Save

        self._dirty_links: Dict[int, Optional[str]] = {}

        # New staged edit trackers

        self._dirty_desc: Dict[int, Optional[str]] = {}

        self._dirty_mfg: Dict[int, Optional[str]] = {}

        self._locked_parts: set[int] = set()

        self._part_product_links: Dict[int, Optional[str]] = {}

        self._dirty_datasheets: Dict[int, Optional[str]] = {}

        # Staged datasheet add/remove when Apply is off

        self._dirty_datasheets: Dict[int, Optional[str]] = {}

        # View mode: 'by_pn' or 'by_ref'

        self._part_numbers: Dict[int, str] = {}

        self._complex_settings = get_complex_editor_settings()

        bridge_cfg = self._complex_settings.get('bridge', {}) if isinstance(self._complex_settings, dict) else {}

        bridge_enabled = bool(bridge_cfg.get('enabled')) if isinstance(bridge_cfg, dict) else False

        ui_enabled = bool(self._complex_settings.get('ui_enabled')) if isinstance(self._complex_settings, dict) else False

        self._complex_ui_enabled = ui_enabled and bridge_enabled

        self._complex_splitter: Optional[QSplitter] = None

        self._complex_panel: Optional[ComplexPanel] = None

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

        self.setWindowTitle(f"BOM Editor  Assembly {assembly_id}")

        self.toolbar = QToolBar()

        layout.addWidget(self.toolbar)



        # Filter

        self.filter_edit = QLineEdit()

        self.filter_edit.setPlaceholderText("Filter...")



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



        # Columns menu

        self.columns_menu = QMenu("Columns", self)



        # Visible lines control

        self.lines_label = QLabel("Lines:")

        self.lines_spin = NoWheelSpinBox()

        self.lines_spin.setRange(1, 10)

        self.lines_spin.setValue(self._settings.value("lines", 1, type=int))



        # Apply immediately toggle

        self.apply_act = QAction("Apply Immediately", self)

        self.apply_act.setCheckable(True)



        # Save button

        self.save_act = QAction("Save", self)

        self.save_act.setEnabled(False)



        # Autofill button

        self.autofill_act = QAction("Autofill", self)



        # Auto datasheet button

        self.auto_ds_act = QAction("Auto Datasheet...", self)

        self.auto_ds_act.setEnabled(False)

        self.auto_ds_act.triggered.connect(self._auto_datasheet)



        # BOM to VIVA export

        self.export_viva_act = QAction("BOM to VIVA", self)

        self.export_viva_act.triggered.connect(self._on_export_viva)



        # Reload prefix map

        self.reload_prefix_map_act = QAction("Reload Prefix Map", self)

        self.reload_prefix_map_act.triggered.connect(self._reload_prefix_map)



        # Table

        self.table = QTableView()



        # Enable per-cell hover

        self.table.setMouseTracking(True)

        self.table.viewport().setMouseTracking(True)



        # Style: white background, light-blue selection, light-gray hover.

        # Ensure hover doesn't look like native "blue selection" on Windows.

        self.table.setStyleSheet(

            """

QTableView {

    background: #FFFFFF;

    alternate-background-color: #FAFAFA;

}



QTableView::item {

    selection-color: black;  /* readable text on light backgrounds */

}



/* Hover on non-selected cells */

QTableView::item:hover:!selected {

    background: #F3F4F6;  /* light gray */

}



/* Selected cells, whether view is active or not */

QTableView::item:selected,

QTableView::item:selected:active,

QTableView::item:selected:!active {

    background: #DCEBFF;  /* light blue */

    color: black;

}



/* When a selected cell is also hovered, keep it in the same palette (slightly stronger if you prefer) */

QTableView::item:selected:hover {

    background: #CFE3FF;  /* optional slightly darker blue on hover+selected */

}

"""

        )



        self.model = UndoableStandardItemModel(0, 5, self)

        self.model.itemChanged.connect(self._on_item_changed)

        self.model.changed.connect(self._on_model_changed)

        self._updating = False

        self.undo_stack = QUndoStack(self)

        self.undo_stack.setUndoLimit(10)

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
        self.method_delegate_powered = TestMethodDelegate(self.table)

        self.method_delegate_powered.methodChanged.connect(
            lambda part_id, method: self._on_method_changed(part_id, method, TestMode.powered)
        )

        # Function options loaded for test detail delegate (with sensible fallback)

        self._function_options = self._load_function_options()

        # Map for case-insensitive validation and canonicalization

        self._function_options_canon = {opt.upper(): opt for opt in (self._function_options or [])}

        self._prefix_macros = load_prefix_macros()

        self.detail_delegate = TestDetailDelegate(self.table, options=self._function_options)

        self.detail_delegate.detailClicked.connect(self._on_detail_clicked)

        self.detail_delegate.detailChanged.connect(self._on_detail_changed)
        self.detail_delegate_powered = TestDetailDelegate(self.table, options=self._function_options)

        self.detail_delegate_powered.detailClicked.connect(
            lambda part_id: self._on_detail_clicked(part_id, TestMode.powered)
        )

        self.detail_delegate_powered.detailChanged.connect(
            lambda part_id, detail: self._on_detail_changed(part_id, detail, TestMode.powered)
        )

        self.lines_spin.valueChanged.connect(self._on_lines_changed)

        # Column assignment done in _rebuild_model



        self.filter_edit.textChanged.connect(self.proxy.setFilterString)

        self.apply_act.toggled.connect(self._on_apply_toggled)

        self.save_act.triggered.connect(self._save_changes)

        self.autofill_act.triggered.connect(self._autofill_fields)

        self.view_by_pn_act.toggled.connect(lambda checked: self._on_view_mode_changed("by_pn", checked))

        self.view_by_ref_act.toggled.connect(lambda checked: self._on_view_mode_changed("by_ref", checked))



        # Copy/Paste support

        self.copy_act = QAction("Copy", self)

        self.copy_act.setShortcut(QKeySequence.StandardKey.Copy)

        self.copy_act.triggered.connect(self._copy_selection)

        self.table.addAction(self.copy_act)

        self.paste_act = QAction("Paste", self)

        self.paste_act.setShortcut(QKeySequence.StandardKey.Paste)

        self.paste_act.triggered.connect(self._paste_selection)

        self.table.addAction(self.paste_act)

        # Delete selected rows

        self.delete_act = QAction("Delete Selected", self)

        self.delete_act.setShortcut(QKeySequence.StandardKey.Delete)

        # Delete key: clear selected cells unless entire rows are selected

        self.delete_act.triggered.connect(self._delete_or_clear_selection)

        self.table.addAction(self.delete_act)

        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)

        # Link context actions

        self.open_link_act = QAction("Open Link", self)

        self.copy_link_act = QAction("Copy Link", self)

        self.open_link_act.triggered.connect(self._open_selected_link)

        self.copy_link_act.triggered.connect(self._copy_selected_link)

        self.table.addAction(self.open_link_act)

        self.table.addAction(self.copy_link_act)



        # Remove datasheet action (context menu)

        self.remove_ds_act = QAction("Remove Datasheet", self)

        self.remove_ds_act.triggered.connect(self._remove_selected_datasheets)

        self.table.addAction(self.remove_ds_act)

        # Auto-link in Complex Editor (context menu and Tools menu)
        self.auto_link_act = QAction("Auto-link in Complex Editor", self)
        self.auto_link_act.setToolTip("For selected rows with Test method=Complex, auto-link by exact PN/alias match in CE")
        self.auto_link_act.triggered.connect(self._auto_link_selected_complex)
        self.table.addAction(self.auto_link_act)



        if self._complex_ui_enabled:

            self._complex_splitter = QSplitter(Qt.Orientation.Horizontal, self)

            self._complex_splitter.addWidget(self.table)

            self._complex_panel = ComplexPanel(self)

            self._complex_panel.linkUpdated.connect(self._on_complex_link_updated)

            self._complex_panel.hide()

            self._complex_splitter.addWidget(self._complex_panel)

            self._complex_splitter.setStretchFactor(0, 3)

            self._complex_splitter.setStretchFactor(1, 2)

            try:

                self._complex_splitter.setCollapsible(1, True)

            except Exception:

                pass

            layout.addWidget(self._complex_splitter)

        else:

            layout.addWidget(self.table)



        # Undo/Redo support via QUndoStack

        self.undo_act = QAction("Undo", self, shortcut=QKeySequence.StandardKey.Undo)

        self.redo_act = QAction("Redo", self, shortcut=QKeySequence.StandardKey.Redo)

        self.undo_act.triggered.connect(self.undo_stack.undo)

        self.redo_act.triggered.connect(self.undo_stack.redo)

        self.undo_act.setEnabled(False)

        self.redo_act.setEnabled(False)

        self.undo_stack.canUndoChanged.connect(self.undo_act.setEnabled)

        self.undo_stack.canRedoChanged.connect(self.redo_act.setEnabled)

        self.table.addAction(self.undo_act)

        self.table.addAction(self.redo_act)



        # Toolbar menus

        self.btn_edit = QToolButton(self)

        self.btn_edit.setText("Edit")

        self.btn_edit.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self.menu_edit = QMenu(self.btn_edit)

        self.menu_edit.addAction(self.undo_act)

        self.menu_edit.addAction(self.redo_act)

        self.menu_edit.addAction(self.copy_act)

        self.menu_edit.addAction(self.paste_act)

        self.menu_edit.addAction(self.delete_act)

        self.btn_edit.setMenu(self.menu_edit)

        self.toolbar.addWidget(self.btn_edit)



        self.btn_view = QToolButton(self)

        self.btn_view.setText("View")

        self.btn_view.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self.menu_view = QMenu(self.btn_view)

        self.menu_view.addAction(self.view_by_pn_act)

        self.menu_view.addAction(self.view_by_ref_act)

        self.menu_view.addMenu(self.columns_menu)

        self.btn_view.setMenu(self.menu_view)

        self.toolbar.addWidget(self.btn_view)



        self.btn_data = QToolButton(self)

        self.btn_data.setText("Data")

        self.btn_data.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self.menu_data = QMenu(self.btn_data)

        self.menu_data.addAction(self.apply_act)

        self.menu_data.addAction(self.save_act)

        self.btn_data.setMenu(self.menu_data)

        self.toolbar.addWidget(self.btn_data)



        self.btn_tools = QToolButton(self)

        self.btn_tools.setText("Tools")

        self.btn_tools.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self.menu_tools = QMenu(self.btn_tools)

        self.menu_tools.addAction(self.autofill_act)

        self.menu_tools.addAction(self.reload_prefix_map_act)

        self.menu_tools.addAction(self.auto_ds_act)

        self.menu_tools.addAction(self.export_viva_act)

        # Export to Excel

        self.export_xlsx_act = QAction("Export to Excel", self)

        self.export_xlsx_act.triggered.connect(self._export_excel)

        self.menu_tools.addAction(self.export_xlsx_act)
        # Add Auto-link action to Tools as well
        self.menu_tools.addAction(self.auto_link_act)
        # Bulk sync CE links by PN for current assembly
        self.sync_ce_links_act = QAction("Sync CE Links (Assembly)", self)
        self.sync_ce_links_act.setToolTip("Search CE for all PNs in this assembly and link exact matches")
        self.sync_ce_links_act.triggered.connect(self._sync_ce_links_assembly)
        self.menu_tools.addAction(self.sync_ce_links_act)

        self.btn_tools.setMenu(self.menu_tools)

        self.toolbar.addWidget(self.btn_tools)



        # Filter and lines widgets

        self.toolbar.addWidget(self.filter_edit)

        self.toolbar.addWidget(self.lines_label)

        self.toolbar.addWidget(self.lines_spin)



        # Enable wrapping and resize rows when columns change

        self.table.setWordWrap(True)

        self.table.horizontalHeader().sectionResized.connect(lambda *_: self.table.resizeRowsToContents())



        self._load_data()

        self._rebuild_model()

        self._on_lines_changed(self.lines_spin.value())

        if self.table.selectionModel():

            self.table.selectionModel().selectionChanged.connect(self._on_table_selection_changed)

        self._on_table_selection_changed()



    # ------------------------------------------------------------------

    def _set_headers(self) -> None:
        base = {
            "pn": "PN",
            "ref": "Reference",
            "desc": "Description",
            "mfg": "Manufacturer",
            "ap": "A/P",
            "ds": "DS",
            "link": "Link",
            "test_method": "Test method",
            "test_detail": "Test detail",
            "package": "Package",
            "value": "Value",
            "tol_p": "Tol (+)",
            "tol_n": "Tol (–)",
        }
        if "test_method_powered" in self._col_indices:
            base["test_method_powered"] = "Test method (powered)"
        if "test_detail_powered" in self._col_indices:
            base["test_detail_powered"] = "Test detail (powered)"
        if self._view_mode == "by_pn":
            base["ref"] = "References"

        inv = {v: k for k, v in self._col_indices.items()}
        headers = [""] * len(inv)
        for name, idx in self._col_indices.items():
            headers[idx] = base.get(name, name.replace("_", " " ).title())
        self.model.setHorizontalHeaderLabels(headers)

    def _setup_columns_menu(self) -> None:

        # Clear previous actions

        self.columns_menu.clear()

        self._column_actions: list[QAction] = []

        header_labels: list[str] = []
        for idx in range(self.model.columnCount()):
            header = self.model.headerData(idx, Qt.Orientation.Horizontal)
            label = str(header) if header is not None else ""
            header_labels.append(label)

        # Persist settings per mode

        mode_key = "cols_by_pn" if self._view_mode == "by_pn" else "cols_by_ref"

        for idx, name in enumerate(header_labels):
            display_name = name or f"Column {idx + 1}"

            act = QAction(display_name, self)

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
        tm_powered_col = self._col_indices.get("test_method_powered")

        if tm_powered_col is not None:

            self.table.setItemDelegateForColumn(tm_powered_col, self.method_delegate_powered)

        td_powered_col = self._col_indices.get("test_detail_powered")

        if td_powered_col is not None:

            self.detail_delegate_powered.set_test_method_col(tm_powered_col)

            self.table.setItemDelegateForColumn(td_powered_col, self.detail_delegate_powered)

        # Make icon-only columns compact while keeping icons prominent

        try:

            from PyQt6.QtWidgets import QHeaderView as _QHV

            hdr = self.table.horizontalHeader()

            ds_col = self._col_indices.get("ds")

            link_col = self._col_indices.get("link")

            icon_col_width = 36

            if ds_col is not None:

                hdr.setSectionResizeMode(ds_col, _QHV.ResizeMode.Fixed)

                self.table.setColumnWidth(ds_col, icon_col_width)

            if link_col is not None:

                hdr.setSectionResizeMode(link_col, _QHV.ResizeMode.Fixed)

                self.table.setColumnWidth(link_col, icon_col_width)

        except Exception:

            pass

        # Ensure combo is visible for 'Macro' rows

        self._sync_detail_editors()



    def _load_data(self) -> None:

        # Keep canonical raw rows
        with app_state.get_session() as session:
            assembly = session.get(Assembly, self._assembly_id)
            mode = TestMode.unpowered
            if assembly is not None and getattr(assembly, "test_mode", None) is not None:
                raw_mode = assembly.test_mode
                if isinstance(raw_mode, TestMode):
                    mode = raw_mode
                else:
                    try:
                        mode = TestMode(str(raw_mode))
                    except ValueError:
                        mode = TestMode.unpowered
            self._assembly_mode = mode
            self._rows_raw = services.get_joined_bom_for_assembly(session, self._assembly_id)

        # Seed parts state from DB
        self._parts_state.clear()
        self._part_datasheets.clear()
        self._part_packages.clear()
        self._part_numbers.clear()
        self._part_values.clear()
        self._tolerances.clear()
        self._dirty_datasheets.clear()
        self._resolved_tests.clear()
        # Track whether a part is already linked to a Complex Editor record
        self._part_ce_linked: set[int] = set()

        processed_parts: set[int] = set()
        for r in self._rows_raw:
            part_id = getattr(r, "part_id", None)
            if part_id is None or part_id in processed_parts:
                continue
            processed_parts.add(part_id)

            # Use DB-provided value; may be None in future schema
            self._parts_state[part_id] = getattr(r, "active_passive", None)
            self._part_datasheets[part_id] = getattr(r, "datasheet_url", None)
            self._part_packages[part_id] = getattr(r, "package", None)
            self._part_numbers[part_id] = getattr(r, "part_number", "")
            self._part_values[part_id] = getattr(r, "value", None)
            self._tolerances[part_id] = (getattr(r, "tol_p", None), getattr(r, "tol_n", None))
            # Seed product link if available so the Link column shows after reload
            self._part_product_links[part_id] = getattr(r, "product_url", None)

            resolved = self._resolved_tests.setdefault(
                part_id,
                {
                    "method": getattr(r, "test_method", None),
                    "detail": getattr(r, "test_detail", None),
                    "method_powered": getattr(r, "test_method_powered", None),
                    "detail_powered": getattr(r, "test_detail_powered", None),
                    "source": getattr(r, "test_resolution_source", None),
                    "message": getattr(r, "test_resolution_message", None),
                },
            )

            ta = self._test_assignments.setdefault(
                part_id, {"method": "", "qt_path": None, "method_powered": "", "qt_path_powered": None}
            )
            if part_id not in self._dirty_tests:
                ta["method"] = resolved.get("method") or ""
                detail_val = resolved.get("detail")
                if detail_val:
                    ta["detail"] = detail_val
                else:
                    ta.pop("detail", None)
                if ta["method"] == "Quick test (QT)" and not ta.get("qt_path"):
                    ta["qt_path"] = detail_val
            method_powered = resolved.get("method_powered")
            detail_powered = resolved.get("detail_powered")
            if method_powered:
                ta["method_powered"] = method_powered
            else:
                ta.pop("method_powered", None)
            if detail_powered:
                ta["detail_powered"] = detail_powered
            else:
                ta.pop("detail_powered", None)
            if ta.get("method_powered") == "Quick test (QT)" and not ta.get("qt_path_powered"):
                ta["qt_path_powered"] = detail_powered

            # Do not force method defaults based on CE link; powered and unpowered can differ

        # Load CE link status for visible parts (robust to driver return types)
        self._part_ce_linked = set()
        if processed_parts:
            try:
                with app_state.get_session() as session:
                    from sqlmodel import select as _select
                    rows = session.exec(
                        _select(ComplexLink.part_id).where(ComplexLink.part_id.in_(processed_parts))
                    ).all()
                    linked: set[int] = set()
                    for rec in rows:
                        try:
                            if isinstance(rec, (list, tuple)) and rec:
                                pid = rec[0]
                            elif hasattr(rec, "part_id"):
                                pid = getattr(rec, "part_id", None)
                            else:
                                pid = rec
                            if pid is not None:
                                linked.add(int(pid))
                        except Exception:
                            continue
                    self._part_ce_linked = linked
            except Exception:
                self._part_ce_linked = set()

        # Overlay any saved test assignments from settings for visible parts

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

        if self._view_mode == "by_pn":
            columns = [
                "pn",
                "ref",
                "desc",
                "mfg",
                "ap",
                "ds",
                "link",
                "test_method",
                "test_detail",
                "package",
                "value",
                "tol_p",
                "tol_n",
            ]
        else:
            columns = [
                "ref",
                "pn",
                "desc",
                "mfg",
                "ap",
                "ds",
                "link",
                "test_method",
                "test_detail",
                "package",
                "value",
                "tol_p",
                "tol_n",
            ]

        if self._assembly_mode is TestMode.powered:
            insert_at = columns.index("test_detail") + 1
            columns[insert_at:insert_at] = ["test_method_powered", "test_detail_powered"]

        self._col_indices = {name: idx for idx, name in enumerate(columns)}
        self.model.setColumnCount(len(columns))

        if self._view_mode == "by_pn":
            self._build_by_pn()
        else:
            self._build_by_ref()

        self._set_headers()
        self._setup_columns_menu()

        self.table.setSortingEnabled(True)
        self.table.sortByColumn(self._col_indices["ref"], Qt.SortOrder.AscendingOrder)

        ref_col = self._col_indices["ref"]
        ap_col = self._col_indices["ap"]
        self.proxy.setReferenceColumn(ref_col)
        self.proxy.setSkipColumns({ap_col})

        # Defer autosize until columns are applied
        QTimer.singleShot(0, self._autosize_window_to_columns)
        QTimer.singleShot(0, self._install_datasheet_widgets)
        QTimer.singleShot(0, self._sync_detail_editors)
        if self._complex_panel:
            QTimer.singleShot(0, self._update_complex_panel_context)


    def _build_by_pn(self) -> None:

        from collections import defaultdict

        groups: Dict[int, List[object]] = defaultdict(list)
        for r in self._rows_raw:
            if getattr(r, "part_id", None) is None:
                continue
            groups[r.part_id].append(r)

        for part_id, rows in groups.items():
            first = rows[0]
            refs_sorted = sorted((x.reference for x in rows), key=natural_key)
            refs_str = ",".join(refs_sorted)
            explicit = next((x.active_passive for x in rows if getattr(x, "active_passive", None) in ("active", "passive")), None)
            mode_val = explicit or self._auto_infer(None, first.reference)
            if part_id in self._dirty_parts:
                mode_val = self._dirty_parts[part_id]
            self._handle_auto_infer_persistence(part_id, mode_val, explicit)

            ta = self._test_assignments.get(part_id, {"method": "", "qt_path": None})
            resolved = self._resolved_tests.get(part_id, {})

            # Compute powered display values if columns are present
            powered_method_val = ta.get("method_powered") or resolved.get("method_powered") or ""
            powered_detail_val = ta.get("detail_powered") or resolved.get("detail_powered") or ""
            powered_detail_display = self._detail_text_for(
                {"method": powered_method_val, "detail": powered_detail_val}, part_id
            )

            values = {
                "pn": first.part_number or "",
                "ref": refs_str,
                "desc": first.description or "",
                "mfg": first.manufacturer or "",
                "ap": mode_val or "",
                "ds": "",
                "link": "",
                "test_method": ta.get("method", ""),
                "test_detail": self._detail_text_for(ta, part_id),
                "package": self._part_packages.get(part_id) or "",
                "value": self._part_values.get(part_id) or "",
                "tol_p": self._tolerances.get(part_id, (None, None))[0] or "",
                "tol_n": self._tolerances.get(part_id, (None, None))[1] or "",
            }
            if "test_method_powered" in self._col_indices:
                values["test_method_powered"] = powered_method_val
            if "test_detail_powered" in self._col_indices:
                values["test_detail_powered"] = powered_detail_display

            row_items = [QStandardItem("") for _ in range(len(self._col_indices))]
            for name, idx in self._col_indices.items():
                text_value = values.get(name)
                if text_value is not None:
                    row_items[idx].setText(str(text_value))

            editable_names = {
                "test_method",
                "test_detail",
                "test_method_powered",
                "test_detail_powered",
                "desc",
                "mfg",
                "package",
                "value",
                "tol_p",
                "tol_n",
            }
            editable = {self._col_indices[n] for n in editable_names if n in self._col_indices}

            for idx, item in enumerate(row_items):
                flags = item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                if idx not in editable or part_id in self._locked_parts:
                    flags &= ~Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                item.setData(part_id, PartIdRole)
                if idx == self._col_indices.get("ap"):
                    item.setData(mode_val, ModeRole)
                if idx == self._col_indices.get("ds"):
                    item.setData(self._part_datasheets.get(part_id), DatasheetRole)
                if idx == self._col_indices.get("link"):
                    item.setData(self._part_product_links.get(part_id) or "", LinkUrlRole)
            self.model.appendRow(row_items)


    def _build_by_ref(self) -> None:

        for r in self._rows_raw:
            part_id = getattr(r, "part_id", None)
            explicit = getattr(r, "active_passive", None)
            mode_val = explicit or self._auto_infer(None, r.reference)
            if part_id in self._dirty_parts:
                mode_val = self._dirty_parts[part_id]
            if part_id is not None:
                self._handle_auto_infer_persistence(part_id, mode_val, explicit)

            method_val = getattr(r, "test_method", None)
            detail_val = getattr(r, "test_detail", None)
            ta_display = {"method": method_val or "", "detail": detail_val}

            values = {
                "ref": r.reference,
                "pn": r.part_number,
                "desc": r.description or "",
                "mfg": r.manufacturer or "",
                "ap": mode_val or "",
                "ds": "",
                "link": "",
                "test_method": method_val or "",
                "test_detail": self._detail_text_for(ta_display, part_id),
                "package": self._part_packages.get(part_id, "") if part_id is not None else "",
                "value": self._part_values.get(part_id, "") if part_id is not None else "",
                "tol_p": self._tolerances.get(part_id, (None, None))[0] if part_id is not None else "",
                "tol_n": self._tolerances.get(part_id, (None, None))[1] if part_id is not None else "",
            }
            if "test_method_powered" in self._col_indices:
                powered_method_val = getattr(r, "test_method_powered", None) or ""
                values["test_method_powered"] = powered_method_val
            if "test_detail_powered" in self._col_indices:
                powered_method_val = getattr(r, "test_method_powered", None) or ""
                powered_detail_val = getattr(r, "test_detail_powered", None) or ""
                values["test_detail_powered"] = self._detail_text_for(
                    {"method": powered_method_val, "detail": powered_detail_val}, part_id
                )

            row_items = [QStandardItem("") for _ in range(len(self._col_indices))]
            for name, idx in self._col_indices.items():
                text_value = values.get(name)
                if text_value is not None:
                    row_items[idx].setText(str(text_value))

            editable_names = {
                "test_method",
                "test_detail",
                "test_method_powered",
                "test_detail_powered",
                "desc",
                "mfg",
                "package",
                "value",
                "tol_p",
                "tol_n",
            }
            editable = {self._col_indices[n] for n in editable_names if n in self._col_indices}

            for idx, item in enumerate(row_items):
                flags = item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                if idx not in editable or (part_id is not None and part_id in self._locked_parts):
                    flags &= ~Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                item.setData(part_id, PartIdRole)
                if idx == self._col_indices.get("ap"):
                    item.setData(mode_val, ModeRole)
                if idx == self._col_indices.get("ds") and part_id is not None:
                    item.setData(self._part_datasheets.get(part_id), DatasheetRole)
                if idx == self._col_indices.get("link") and part_id is not None:
                    item.setData(self._part_product_links.get(part_id) or "", LinkUrlRole)
            self.model.appendRow(row_items)


    def _detail_label_for(self, ta: dict) -> str:

        # Delegate to unified builder

        return self._detail_text_for(ta)



    def _refresh_rows_for_part(self, part_id: int) -> None:

        ta = self._test_assignments.get(
            part_id, {"method": "", "qt_path": None, "method_powered": "", "qt_path_powered": None}
        )

        method = ta.get("method", "")

        detail = self._detail_text_for(ta, part_id)
        powered_method = ta.get("method_powered", "")
        powered_display = self._detail_text_for(
            {
                "method": powered_method,
                "detail": ta.get("detail_powered"),
                "qt_path": ta.get("qt_path_powered") or ta.get("qt_path"),
            },
            part_id,
        )

        tm_col = self._col_indices.get("test_method")

        td_col = self._col_indices.get("test_detail")
        tm_powered_col = self._col_indices.get("test_method_powered")
        td_powered_col = self._col_indices.get("test_detail_powered")

        for row in range(self.model.rowCount()):

            for c in range(self.model.columnCount()):

                if self.model.data(self.model.index(row, c), PartIdRole) == part_id:

                    if tm_col is not None:

                        self.model.setData(self.model.index(row, tm_col), method)

                    if td_col is not None:

                        self.model.setData(self.model.index(row, td_col), detail)

                    if tm_powered_col is not None:

                        self.model.setData(self.model.index(row, tm_powered_col), powered_method)

                    if td_powered_col is not None:

                        self.model.setData(self.model.index(row, td_powered_col), powered_display)

                    break



    def _on_method_changed(
        self, part_id: int, new_method: str, power_mode: TestMode = TestMode.unpowered
    ) -> None:

        ta = self._test_assignments.setdefault(
            part_id, {"method": "", "qt_path": None, "method_powered": "", "qt_path_powered": None}
        )

        method_key = "method_powered" if power_mode is TestMode.powered else "method"

        detail_key = "detail_powered" if power_mode is TestMode.powered else "detail"

        qt_key = "qt_path_powered" if power_mode is TestMode.powered else "qt_path"

        previous = self._resolved_tests.get(part_id, {})
        prev_method = (previous.get(method_key) or "").strip()
        prev_detail = previous.get(detail_key)

        ta[method_key] = new_method

        if new_method != "Quick test (QT)":

            ta[qt_key] = None

        self._refresh_rows_for_part(part_id)

        # Keep bulk changes fast: don't auto-open the Complex panel.
        # Only refresh the panel context if it's already visible; opening happens on Test detail click.
        self._update_complex_panel_context()

        # Ensure editors reflect Macro selection state

        self._sync_detail_editors()

        # Persist/stage according to Apply toggle

        if self.apply_act.isChecked():

            try:
                self._persist_test_assignment(part_id)
            except Exception as exc:
                QMessageBox.warning(self, "Save failed", str(exc))
                ta[method_key] = prev_method
                ta[detail_key] = prev_detail
                self._refresh_rows_for_part(part_id)
                self._sync_detail_editors()
                self._dirty_tests.add(part_id)
                self.save_act.setEnabled(True)
                return
            else:
                self._dirty_tests.discard(part_id)

        else:

            self._dirty_tests.add(part_id)

            self.save_act.setEnabled(True)



    def _on_detail_clicked(
        self, part_id: int, power_mode: TestMode = TestMode.unpowered
    ) -> None:

        ta = self._test_assignments.setdefault(
            part_id, {"method": "", "qt_path": None, "method_powered": "", "qt_path_powered": None}
        )

        method_key = "method_powered" if power_mode is TestMode.powered else "method"

        qt_key = "qt_path_powered" if power_mode is TestMode.powered else "qt_path"

        method = ta.get(method_key, "")

        if method == "Macro":

            self._show_stub_dialog(

                "This would open the Macro selector (closed list) and save the chosen Macro for this PN. (Not implemented yet)."

            )

        elif method == "Complex":

            if self._complex_panel:

                self._ensure_complex_panel_visible_for_part(part_id)

            else:

                self._show_stub_dialog(

                    "Complex Editor integration is disabled. Enable it in settings to link complexes."

                )

        elif method == "Quick test (QT)":

            path, _ = QFileDialog.getOpenFileName(self, "Select Quick Test XML", "", "Quick Test XML (*.xml)")

            if path:

                ta[qt_key] = path

                self._refresh_rows_for_part(part_id)

        elif method == "Python code":

            self._show_stub_dialog(

                "This would open a project chooser (folder with code, description, library links) and link it to this PN. (Not implemented yet)."

            )





    def _on_detail_changed(
        self, part_id: int, new_detail: Optional[str], power_mode: TestMode = TestMode.unpowered
    ) -> None:

        # Record selection and refresh label, keep persistent editor

        ta = self._test_assignments.setdefault(
            part_id, {"method": "", "qt_path": None, "method_powered": "", "qt_path_powered": None}
        )
        previous = self._resolved_tests.get(part_id, {})
        detail_key = "detail_powered" if power_mode is TestMode.powered else "detail"
        prev_detail = previous.get(detail_key)

        ta[detail_key] = new_detail or None

        self._refresh_rows_for_part(part_id)

        # Keep editor visible where applicable

        self._sync_detail_editors()

        # Persist/stage according to Apply toggle

        if self.apply_act.isChecked():

            try:
                self._persist_test_assignment(part_id)
            except Exception as exc:
                QMessageBox.warning(self, "Save failed", str(exc))
                ta[detail_key] = prev_detail
                self._refresh_rows_for_part(part_id)
                self._sync_detail_editors()
                self._dirty_tests.add(part_id)
                self.save_act.setEnabled(True)
                return
            else:
                self._dirty_tests.discard(part_id)

        else:

            self._dirty_tests.add(part_id)

            self.save_act.setEnabled(True)



    def _sync_detail_editors(self) -> None:

        # Do not auto-open editors; keep cells passive until user double-clicks

        model = self.table.model()  # proxy

        if model is None:

            return

        rows = model.rowCount()

        column_pairs = [
            ("test_method", "test_detail"),
            ("test_method_powered", "test_detail_powered"),
        ]

        for method_key, detail_key in column_pairs:

            tm_col = self._col_indices.get(method_key)

            td_col = self._col_indices.get(detail_key)

            if tm_col is None or td_col is None:

                continue

            for r in range(rows):

                td_idx = model.index(r, td_col)

                # Always keep closed; opening is controlled by QAbstractItemView triggers

                self.table.closePersistentEditor(td_idx)



    # ------------------------------------------------------------------

    def _persist_test_assignment(self, part_id: int) -> None:
        """Persist staged test assignments for ``part_id`` into the DB."""

        ta = self._test_assignments.get(part_id) or {}
        method = (ta.get("method") or "").strip()
        detail = (ta.get("detail") or "").strip() or None
        method_powered = (ta.get("method_powered") or "").strip()
        detail_powered = (ta.get("detail_powered") or "").strip() or None
        qt_path = ta.get("qt_path") or None
        qt_path_powered = ta.get("qt_path_powered") or None

        part_type = _coerce_part_type(self._parts_state.get(part_id))
        profile_unpowered = _default_profile(part_type, TestMode.unpowered)
        profile_powered = _default_profile(part_type, TestMode.powered)

        # Treat Complex as a derived default based on CE link; do not persist to DB
        if method == "Complex":
            method = ""
            detail = None
        if method_powered == "Complex":
            method_powered = ""
            detail_powered = None

        try:
            with app_state.get_session() as session:
                if method:
                    services.save_part_test_map(
                        session,
                        part_id=part_id,
                        power_mode=TestMode.unpowered,
                        profile=profile_unpowered,
                        method=method,
                        detail=detail,
                        quick_test_path=qt_path,
                    )
                else:
                    services.remove_part_test_map(session, part_id, TestMode.unpowered, profile_unpowered)

                if method_powered:
                    services.save_part_test_map(
                        session,
                        part_id=part_id,
                        power_mode=TestMode.powered,
                        profile=profile_powered,
                        method=method_powered,
                        detail=detail_powered,
                        quick_test_path=qt_path_powered or qt_path,
                    )
                else:
                    services.remove_part_test_map(session, part_id, TestMode.powered, profile_powered)

                session.commit()
        except Exception:
            raise

        resolved = self._resolved_tests.setdefault(
            part_id,
            {
                "method": None,
                "detail": None,
                "method_powered": None,
                "detail_powered": None,
                "source": "mapping",
                "message": None,
            },
        )
        resolved["method"] = method or None
        resolved["detail"] = detail
        resolved["method_powered"] = method_powered or None
        resolved["detail_powered"] = detail_powered


    def _show_stub_dialog(self, message: str) -> None:

        from .dialogs.tm_stub_dialog import TestMethodStubDialog



        dlg = TestMethodStubDialog(message, self)

        dlg.exec()



    # ------------------------------------------------------------------

    def _detail_text_for(self, ta: dict, part_id: Optional[int] = None) -> str:

        method = ta.get("method") or ""

        if method == "Macro":

            sel = ta.get("detail") or ta.get("macro") or None

            return sel or "Choose Macro..."

        if method == "Complex":
            try:
                if part_id is not None and part_id in getattr(self, "_part_ce_linked", set()):
                    return "Linked"
            except Exception:
                pass
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



    def _persist_or_stage_description(self, part_id: int, value: Optional[str]) -> None:

        if self.apply_act.isChecked():

            try:

                with app_state.get_session() as session:

                    services.update_part_description(session, part_id, value or "")

            except Exception as exc:

                QMessageBox.warning(self, "Update failed", str(exc))

                # Revert in UI from DB snapshot

                self._fanout_part_field(part_id, "desc", None)

                return

            self._dirty_desc.pop(part_id, None)

        else:

            self._dirty_desc[part_id] = value or None

            self.save_act.setEnabled(True)



    def _persist_or_stage_manufacturer(self, part_id: int, value: Optional[str]) -> None:

        if self.apply_act.isChecked():

            try:

                with app_state.get_session() as session:

                    services.update_manufacturer_for_part_in_assembly(

                        session, self._assembly_id, part_id, value or ""

                    )

            except Exception as exc:

                QMessageBox.warning(self, "Update failed", str(exc))

                # No reliable previous value to restore; leave UI as-is

                return

            self._dirty_mfg.pop(part_id, None)

        else:

            self._dirty_mfg[part_id] = value or None

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

        elif col == self._col_indices.get("desc"):

            self._fanout_part_field(part_id, "desc", text)

            self._persist_or_stage_description(part_id, text)

        elif col == self._col_indices.get("mfg"):

            self._fanout_part_field(part_id, "mfg", text)

            self._persist_or_stage_manufacturer(part_id, text)

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



    def _extract_prefix(self, ref: str) -> str:

        i = 0

        while i < len(ref) and ref[i].isalpha():

            i += 1

        return ref[:i].upper()



    def _macro_for_reference(self, reference: str) -> str | None:

        if not reference:

            return None

        pfx = self._extract_prefix(reference)

        if not pfx:

            return None

        for key, macro in self._prefix_macros:

            if pfx.startswith(key):

                canon = self._function_options_canon.get(macro.upper())

                return canon

        for key, macro in _DEFAULT_MACRO_PREFIXES:

            if pfx.startswith(key):

                canon = self._function_options_canon.get(macro.upper())

                if canon:

                    return canon

        return None



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

            tm_col = self._col_indices.get("test_method")

            ref_col = self._col_indices.get("ref")

            if tm_col is not None and ref_col is not None:

                tm_text = str(proxy.data(proxy.index(r, tm_col)) or "").strip()

                if not tm_text:

                    ref_text = str(proxy.data(proxy.index(r, ref_col)) or "")

                    if self._view_mode == "by_pn":

                        first_ref = ref_text.split(",")[0].strip() if ref_text else ""

                    else:

                        first_ref = ref_text

                    macro = self._macro_for_reference(first_ref)

                    if macro:

                        ta = self._test_assignments.setdefault(
                            part_id,
                            {"method": "", "qt_path": None, "method_powered": "", "qt_path_powered": None},
                        )

                        ta["method"] = "Macro"

                        ta["detail"] = macro

                        ta["qt_path"] = None

                        self._refresh_rows_for_part(part_id)

                        if self.apply_act.isChecked():

                            self._persist_test_assignment(part_id)

                        else:

                            self._dirty_tests.add(part_id)

                            self.save_act.setEnabled(True)



    def _reload_prefix_map(self):

        self._prefix_macros = reload_prefix_macros()

        QMessageBox.information(self, "Prefix Map", "Prefix-to-macro map reloaded.")



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

        # Update UI immediately for each part as it's attached

        dlg.attached.connect(self._on_datasheet_attached)

        # Mark parts as searched-but-not-found in this BOM session

        dlg.failed.connect(self._on_datasheet_failed)

        # Receive manual page suggestions for hard downloads

        dlg.manualLink.connect(self._on_manual_link)

        dlg.exec()

        # Refresh datasheet paths for affected parts

        from ..models import Part

        with app_state.get_session() as session:

            for w in work:

                p = session.get(Part, w.part_id)

                if p:

                    self._part_datasheets[w.part_id] = p.datasheet_url

                    self._part_product_links[w.part_id] = getattr(p, "product_url", None)

                    # Update description live if inferred/filled during auto-datasheet

                    self._fanout_part_field(w.part_id, "desc", getattr(p, "description", None))

        # Clear loading state and refresh icons

        self._datasheet_loading -= {w.part_id for w in work}

        QTimer.singleShot(0, self._install_datasheet_widgets)



    def _set_parts_locked(self, parts: set[int], lock: bool):

        if lock:

            self._locked_parts |= set(parts)

        else:

            self._locked_parts -= set(parts)

        self._rebuild_model()



    def _on_table_selection_changed(self, *_args) -> None:

        self._update_auto_ds_act()

        self._update_complex_panel_context()

        # Enable/disable auto-link action based on selection
        try:
            self._update_auto_link_act()
        except Exception:
            pass



    def _update_auto_ds_act(self) -> None:

        sel = self.table.selectionModel()

        self.auto_ds_act.setEnabled(bool(sel and sel.selectedIndexes()))


    def _update_auto_link_act(self) -> None:
        sel = self.table.selectionModel()
        if not sel or not sel.selectedIndexes():
            self.auto_link_act.setEnabled(False)
            return
        part_ids: set[int] = set()
        for idx in sel.selectedIndexes():
            pid = idx.data(PartIdRole)
            if isinstance(pid, int):
                part_ids.add(pid)
        enable = False
        for pid in part_ids:
            method = (self._test_assignments.get(pid, {}) or {}).get("method", "")
            if str(method).strip().lower() == "complex":
                enable = True
                break
        self.auto_link_act.setEnabled(enable)


    def _selected_part_id(self) -> Optional[int]:

        sel = self.table.selectionModel()

        if not sel:

            return None

        for idx in sel.selectedIndexes():

            part_id = idx.data(PartIdRole)

            if isinstance(part_id, int):

                return part_id

        return None



    def _part_number_for_part(self, part_id: int) -> str:

        pn = self._part_numbers.get(part_id) or ""

        if pn:

            return pn

        for row in self._rows_raw:

            if getattr(row, 'part_id', None) == part_id:

                pn = getattr(row, 'part_number', '') or ''

                if pn:

                    self._part_numbers[part_id] = pn

                return pn

        return ''



    def _ensure_complex_panel_visible_for_part(self, part_id: int) -> None:

        if not self._complex_panel:

            return

        pn = self._part_number_for_part(part_id)

        self._complex_panel.set_context(part_id, pn)

        self._complex_panel.setVisible(True)

        if self._complex_splitter:

            sizes = self._complex_splitter.sizes()

            if len(sizes) == 2 and sizes[1] == 0:

                total = sum(sizes) or self.width() or 1

                self._complex_splitter.setSizes([max(total - 360, 320), 360])

        self._complex_panel.search_edit.setFocus()



    def _update_complex_panel_context(self) -> None:

        if not self._complex_panel:

            return

        part_id = self._selected_part_id()

        if part_id is None:

            self._complex_panel.set_context(None, None)

            self._complex_panel.hide()

            return

        method = self._test_assignments.get(part_id, {}).get('method', '')

        if method != 'Complex':

            self._complex_panel.set_context(None, None)

            self._complex_panel.hide()

            return
        # Only refresh if visible; do not auto-open on method change.
        if self._complex_panel.isVisible():
            self._ensure_complex_panel_visible_for_part(part_id)



    def _on_complex_link_updated(self, part_id: int) -> None:
        # Refresh in-memory CE link status for this part
        try:
            with app_state.get_session() as session:
                from sqlmodel import select as _select
                row = session.exec(_select(ComplexLink).where(ComplexLink.part_id == part_id)).first()
                if row is not None:
                    getattr(self, "_part_ce_linked", set()).add(part_id)
                else:
                    if hasattr(self, "_part_ce_linked") and part_id in self._part_ce_linked:
                        self._part_ce_linked.remove(part_id)
        except Exception:
            pass

        self._refresh_rows_for_part(part_id)

        self._ensure_complex_panel_visible_for_part(part_id)



    def _on_apply_toggled(self, checked: bool) -> None:

        if checked and (

            self._dirty_parts

            or self._dirty_packages

            or self._dirty_values

            or self._dirty_tolerances

            or self._dirty_datasheets

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

            and not getattr(self, "_dirty_desc", {})

            and not getattr(self, "_dirty_mfg", {})

            and not getattr(self, "_dirty_links", {})

            and not getattr(self, "_dirty_datasheets", {})

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

        # Persist edited descriptions

        for part_id, desc in list(getattr(self, "_dirty_desc", {}).items()):

            try:

                with app_state.get_session() as session:

                    services.update_part_description(session, part_id, desc or "")

            except Exception as exc:

                failures.append(str(exc))

            else:

                del self._dirty_desc[part_id]

        # Persist edited manufacturers (apply to all BOM items for this part in this assembly)

        for part_id, mfg in list(getattr(self, "_dirty_mfg", {}).items()):

            try:

                with app_state.get_session() as session:

                    services.update_manufacturer_for_part_in_assembly(session, self._assembly_id, part_id, mfg or "")

            except Exception as exc:

                failures.append(str(exc))

            else:

                del self._dirty_mfg[part_id]

        # Persist test assignments via settings

        for part_id in list(self._dirty_tests):

            try:

                self._persist_test_assignment(part_id)

            except Exception as exc:

                failures.append(str(exc))

            else:

                self._dirty_tests.discard(part_id)

        # Persist product links

        for part_id, link in list(self._dirty_links.items()):

            try:

                with app_state.get_session() as session:

                    services.update_part_product_url(session, part_id, link)

            except Exception as exc:

                failures.append(str(exc))

            else:

                self._part_product_links[part_id] = link

                del self._dirty_links[part_id]

        # Persist datasheet add/removes

        for part_id, ds in list(self._dirty_datasheets.items()):

            try:

                with app_state.get_session() as session:

                    if ds:

                        services.update_part_datasheet_url(session, part_id, ds)

                    else:

                        services.remove_part_datasheet(session, part_id, delete_file=True)

            except Exception as exc:

                failures.append(str(exc))

            else:

                self._part_datasheets[part_id] = ds or None

                del self._dirty_datasheets[part_id]

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

            or bool(getattr(self, "_dirty_desc", {}))

            or bool(getattr(self, "_dirty_mfg", {}))

            or bool(getattr(self, "_dirty_links", {}))

            or bool(getattr(self, "_dirty_datasheets", {}))

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

        if self._assembly_mode is TestMode.powered:

            powered_tm_col = self._col_indices.get("test_method_powered")

            powered_td_col = self._col_indices.get("test_detail_powered")

            if powered_tm_col is not None:

                tm_col = powered_tm_col

            if powered_td_col is not None:

                td_col = powered_td_col

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
        export_dir = self._select_viva_export_directory()
        if export_dir is None:
            return
        export_base_path = Path(export_dir)
        result = self._perform_viva_export(export_base_path, table_rows)
        if result is None:
            return
        trace_id = str(result.manifest.get("trace_id") or uuid.uuid4())
        mdb_name = str(result.manifest.get("mdb_name") or "bom_complexes.mdb")
        supervisor = _get_ce_supervisor()
        ready, info = supervisor.ensure_ready(trace_id) if supervisor else (True, {"status": "READY"})
        self._show_viva_export_success(result)
        if not ready:
            ce_result = {
                "status": info.get("status", "RETRY_LATER"),
                "trace_id": trace_id,
                "export_path": None,
                "exported_count": 0,
                "missing_count": 0,
                "report_path": None,
                "detail": info.get("detail", ""),
            }
            self._show_ce_export_summary(ce_result)
            return
        project_dir = Path(result.txt_path.parent)
        ce_result = self._run_ce_export(project_dir, table_rows, mdb_name, trace_id=trace_id)
        if ce_result is not None:
            self._show_ce_export_summary(ce_result)

    def _select_viva_export_directory(self) -> Optional[str]:
        settings = get_viva_export_settings() or {}
        start_dir = (
            settings.get("last_export_path")
            or settings.get("viva_export_base_dir")
            or str(DATA_ROOT)
        )
        start_dir = str(start_dir or str(DATA_ROOT))
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select VIVA export folder",
            start_dir,
        )
        return folder or None

    def _perform_viva_export(self, export_dir: Path, table_rows: list[dict]) -> Optional[VivaExportResult]:
        with app_state.get_session() as session:
            try:
                return services.perform_viva_export(
                    session,
                    self._assembly_id,
                    base_dir=str(export_dir),
                    bom_rows=table_rows,
                    strict=True,
                )
            except VivaExportError as exc:
                return self._handle_viva_export_error(export_dir, table_rows, exc)

    def _run_ce_export(
        self,
        viva_export_dir: Path,
        table_rows: list[dict],
        mdb_name: str,
        *,
        trace_id: str,
    ) -> Optional[dict[str, Any]]:
        ce_dir = viva_export_dir
        try:
            ce_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._show_error("Complex Editor Export", f"Unable to prepare CE export folder:\n{exc}")
            return None
        with app_state.get_session() as session:
            try:
                return services.export_bom_to_ce_bridge(
                    session,
                    self._assembly_id,
                    bom_rows=table_rows,
                    export_dir=ce_dir,
                    mdb_name=mdb_name,
                    trace_id=trace_id,
                )
            except Exception as exc:
                logger.exception("Complex Editor export failed", exc_info=True)
                self._show_error("Complex Editor Export", str(exc))
                return None

    def _handle_viva_export_error(
        self,
        export_dir: Path,
        table_rows: list[dict],
        error: VivaExportError,
    ) -> Optional[VivaExportResult]:
        if error.reason == "unlinked_required":
            return self._prompt_viva_export_relaxed(export_dir, table_rows, error)
        self._show_viva_export_error(export_dir, error)
        return None

    def _prompt_viva_export_relaxed(
        self,
        export_dir: Path,
        table_rows: list[dict],
        error: VivaExportError,
    ) -> Optional[VivaExportResult]:
        missing_rows = error.missing_rows[:]
        displayed = "\n".join(
            f"{row.get('reference', '')}  {row.get('part_number', '')}"
            for row in missing_rows[:8]
        )
        if len(missing_rows) > 8:
            displayed += f"\n... (+{len(missing_rows) - 8} more)"
        text = "Some fitted rows require Complex Editor links:\n\n" + (displayed or "No references listed.")
        msg = QMessageBox(self)
        msg.setWindowTitle("Complex links required")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(text)
        export_btn = msg.addButton("Export only resolved", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.exec()
        if msg.clickedButton() != export_btn:
            return None
        with app_state.get_session() as session:
            try:
                return services.perform_viva_export(
                    session,
                    self._assembly_id,
                    base_dir=str(export_dir),
                    bom_rows=table_rows,
                    strict=False,
                )
            except VivaExportError as exc:
                self._show_viva_export_error(export_dir, exc)
                return None

    def _show_viva_export_error(self, export_dir: Path, error: VivaExportError) -> None:
        manifest_path = export_dir / "viva_manifest.json"
        diagnostics_paths = [manifest_path]
        if error.diagnostics_path:
            diagnostics_paths.append(error.diagnostics_path)
        if getattr(error, "ce_diagnostics_path", None):
            diagnostics_paths.append(error.ce_diagnostics_path)
        def _message_box() -> QMessageBox:
            msg = QMessageBox(self)
            msg.setWindowTitle("Export failed")
            msg.setIcon(QMessageBox.Icon.Critical)
            lines = [str(error)]
            if error.trace_id:
                lines.append(f"Trace ID: {error.trace_id}")
            msg.setText("\n".join(lines))
            info_lines = [str(p.as_posix()) for p in diagnostics_paths]
            if error.suggestions:
                info_lines.extend(error.suggestions)
            msg.setInformativeText("\n".join(info_lines))
            msg.addButton("Copy diagnostics", QMessageBox.ButtonRole.ActionRole)
            msg.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
            msg.addButton("Open logs", QMessageBox.ButtonRole.ActionRole)
            msg.addButton(QMessageBox.StandardButton.Close)
            if error.unresolved_pns:
                msg.setDetailedText("Unresolved PNs: " + ", ".join(error.unresolved_pns))
            return msg
        while True:
            msg = _message_box()
            clicked = msg.exec()
            button = msg.clickedButton()
            if button is None:
                break
            label = button.text()
            if "Copy diagnostics" in label:
                self._maybe_copy_paths(diagnostics_paths)
                continue
            if "Open folder" in label:
                self._open_path_in_explorer(export_dir)
                continue
            if "Open logs" in label:
                self._open_path_in_explorer(LOG_DIR)
                continue
            break

    def _show_viva_export_success(self, result: services.export_viva.VivaExportResult) -> None:
        export_dir = result.manifest_path.parent
        summary_lines = [
            f"BOM TXT: {result.txt_path}",
            f"Manifest: {result.manifest_path}",
        ]
        details_lines: List[str] = []
        if result.warnings:
            details_lines.extend(result.warnings)
        if result.unresolved_pns:
            details_lines.append("Unresolved PNs: " + ", ".join(result.unresolved_pns))
        if result.trace_id:
            details_lines.append(f"Trace ID: {result.trace_id}")
        status_text = {
            "success": "Export completed successfully.",
            "partial": "Export completed with warnings.",
            "skipped": "Export produced the VIVA TXT only.",
        }.get(result.status, "Export completed.")
        def _message_box() -> QMessageBox:
            icon = QMessageBox.Icon.Information if result.status == "success" else QMessageBox.Icon.Warning
            title = "Export complete" if result.status == "success" else "Export summary"
            msg = QMessageBox(self)
            msg.setIcon(icon)
            msg.setWindowTitle(title)
            msg.setText(status_text)
            msg.setInformativeText("\n".join(summary_lines))
            if details_lines:
                msg.setDetailedText("\n".join(details_lines))
            msg.addButton("Copy details", QMessageBox.ButtonRole.ActionRole)
            msg.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
            msg.addButton(QMessageBox.StandardButton.Close)
            return msg
        copy_payload = "\n".join(summary_lines + details_lines)
        while True:
            msg = _message_box()
            clicked = msg.exec()
            button = msg.clickedButton()
            if button is None:
                break
            label = button.text()
            if "Copy details" in label:
                QGuiApplication.clipboard().setText(copy_payload)
                continue
            if "Open folder" in label:
                self._open_path_in_explorer(export_dir)
                continue
            break

    def _show_ce_export_summary(self, ce_result: dict[str, Any]) -> None:
        status_raw = str(ce_result.get("status") or "").upper()
        status_messages = {
            "SUCCESS": "Complex Editor export completed.",
            "PARTIAL_SUCCESS": "Complex Editor export completed with warnings.",
            "FAILED_INPUT": "Complex Editor export blocked due to input issues.",
            "FAILED_BACKEND": "Complex Editor export failed.",
            "RETRY_LATER": "Complex Editor export deferred.",
            "RETRY_WITH_BACKOFF": "Complex Editor bridge unavailable; retry later.",
        }
        icon = (
            QMessageBox.Icon.Information
            if status_raw in {"SUCCESS", "PARTIAL_SUCCESS"}
            else QMessageBox.Icon.Warning
        )
        msg = QMessageBox(self)
        msg.setWindowTitle("Complex Editor Export")
        msg.setIcon(icon)
        msg.setText(status_messages.get(status_raw, f"Complex Editor export status: {status_raw or 'UNKNOWN'}"))

        details: List[str] = []
        exported_count = ce_result.get("exported_count")
        if isinstance(exported_count, int):
            details.append(f"Exported components: {exported_count}")
        missing_count = ce_result.get("missing_count")
        if isinstance(missing_count, int) and missing_count:
            details.append(f"Reported rows: {missing_count}")

        export_path = ce_result.get("export_path")
        report_path = ce_result.get("report_path")
        trace_id = ce_result.get("trace_id")
        detail_text = ce_result.get("detail")

        if export_path:
            details.append(f"Export path: {export_path}")
        if report_path:
            details.append(f"Report: {report_path}")
        if trace_id:
            details.append(f"Trace ID: {trace_id}")
        if detail_text:
            details.append(str(detail_text))

        msg.setInformativeText("\n".join(details) if details else "No additional details.")

        open_folder_button = None
        open_report_button = None
        if export_path:
            open_folder_button = msg.addButton("Open CE Folder", QMessageBox.ButtonRole.ActionRole)
        if report_path:
            open_report_button = msg.addButton("Open Report", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Close)

        while True:
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is None:
                break
            if open_folder_button and clicked is open_folder_button:
                try:
                    target = Path(str(export_path))
                    folder = target if target.is_dir() else target.parent
                    self._open_path_in_explorer(folder)
                except Exception:
                    self._show_error("Open CE Folder", "Unable to open the CE export folder.")
                continue
            if open_report_button and clicked is open_report_button:
                try:
                    self._open_path_in_explorer(Path(str(report_path)))
                except Exception:
                    self._show_error("Open Report", "Unable to open the CE report.")
                continue
            break

    def _maybe_copy_paths(self, paths: List[Path]) -> None:
        if not paths:
            return
        payload = "\n".join(p.as_posix() for p in paths)
        QGuiApplication.clipboard().setText(payload)

    def _open_path_in_explorer(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _export_excel(self) -> None:  # pragma: no cover - UI glue
        # Save visible table to an .xlsx, preserving 'Link' hyperlinks; skip 'Datasheet' column
        from openpyxl import Workbook
        from openpyxl.styles import Font


        base = self._default_export_basename()

        default_name = f"{base} - BOM.xlsx" if base else f"BOM_{self._assembly_id}.xlsx"

        path, _ = QFileDialog.getSaveFileName(

            self, "Export BOM to Excel", default_name, "Excel Files (*.xlsx)"

        )

        if not path:

            return

        proxy = self.table.model()

        if proxy is None:

            return

        ds_col = self._col_indices.get("ds")

        link_col = self._col_indices.get("link")

        # Build list of visible columns (skip 'Datasheet')

        export_cols: list[int] = []

        headers: list[str] = []

        for c in range(self.model.columnCount()):

            if self.table.isColumnHidden(c):

                continue

            if ds_col is not None and c == ds_col:

                continue

            export_cols.append(c)

            hi = self.model.horizontalHeaderItem(c)

            headers.append(hi.text() if hi else str(c))

        wb = Workbook()

        ws = wb.active

        ws.title = "BOM"

        ws.append(headers)

        # Export visible rows in proxy order

        rows = proxy.rowCount()

        for r in range(rows):

            # First append values to keep shapes; then add hyperlinks for link column

            values = []

            for c in export_cols:

                idx = proxy.index(r, c)

                if link_col is not None and c == link_col:

                    link_val = proxy.data(idx, LinkUrlRole)

                    values.append(str(link_val or ""))

                else:

                    values.append(str(proxy.data(idx) or ""))

            ws.append(values)

            # Set hyperlink if link column included

            if link_col is not None and link_col in export_cols:

                idx = export_cols.index(link_col)

                url = values[idx]

                if isinstance(url, str) and url.lower().startswith(("http://", "https://")):

                    cell = ws.cell(row=ws.max_row, column=idx + 1)

                    cell.hyperlink = url

                    cell.font = Font(color="0563C1", underline="single")

        try:

            wb.save(path)

        except Exception as exc:

            QMessageBox.warning(self, "Export failed", str(exc))

            return

        QMessageBox.information(self, "Export", f"Saved Excel to {path}")



    def _default_export_basename(self) -> str:

        """Return 'Customer - Project' for current assembly, sanitized for filenames."""

        import re

        try:

            from ..models import Assembly, Project, Customer

            with app_state.get_session() as session:

                asm = session.get(Assembly, self._assembly_id)

                if not asm:

                    return ""

                proj = session.get(Project, asm.project_id) if getattr(asm, "project_id", None) else None

                cust = session.get(Customer, proj.customer_id) if proj and getattr(proj, "customer_id", None) else None

                cust_name = (cust.name if cust else "").strip()

                proj_title = (proj.title if proj else "").strip() or (getattr(proj, "name", "") or "").strip()

                base = " - ".join([p for p in (cust_name, proj_title) if p])

                # sanitize for filenames

                base = re.sub(r"[\\/:*?\"<>|]+", "_", base)[:120]

                return base

        except Exception:

            return ""



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



    def _paste_selection(self) -> None:

        model = self.table.model()

        if model is None:

            return

        text = QGuiApplication.clipboard().text()

        if text == "":

            return

        idxs = self.table.selectionModel().selectedIndexes() if self.table.selectionModel() else []

        if not idxs:

            cur = self.table.currentIndex()

            if cur.isValid():

                idxs = [cur]

        if not idxs:

            return

        col = idxs[0].column()

        if any(i.column() != col for i in idxs):

            return

        value = text.splitlines()[0].split("\t")[0]

        # Validate against allowed options for combo columns

        if col == self._col_indices.get("test_method"):

            allowed = {"", "Macro", "Complex", "Quick test (QT)", "Python code"}

            if value not in allowed:

                return

        elif col == self._col_indices.get("test_method_powered"):

            allowed = {"", "Macro", "Complex", "Quick test (QT)", "Python code"}

            if value not in allowed:

                return

        elif col == self._col_indices.get("test_detail"):

            if value and value not in self._function_options:

                return

            # Ensure Test Method is 'Macro' when pasting a Macro kind

            tm_col = self._col_indices.get("test_method")

            if tm_col is not None:

                for idx in idxs:

                    part_id = model.data(idx, PartIdRole)

                    if part_id is None:

                        continue

                    cur_method = str(model.data(model.index(idx.row(), tm_col)) or "")

                    if cur_method != "Macro":

                        model.setData(model.index(idx.row(), tm_col), "Macro")

                        self._on_method_changed(part_id, "Macro")

        elif col == self._col_indices.get("test_detail_powered"):

            if value and value not in self._function_options:

                return

            tm_col = self._col_indices.get("test_method_powered")

            if tm_col is not None:

                for idx in idxs:

                    part_id = model.data(idx, PartIdRole)

                    if part_id is None:

                        continue

                    cur_method = str(model.data(model.index(idx.row(), tm_col)) or "")

                    if cur_method != "Macro":

                        model.setData(model.index(idx.row(), tm_col), "Macro")

                        self._on_method_changed(part_id, "Macro", TestMode.powered)

        elif col == self._col_indices.get("ap"):

            allowed = {"", "active", "passive"}

            if value not in allowed:

                return

        for idx in idxs:

            part_id = model.data(idx, PartIdRole)

            model.setData(idx, value)

            if col == self._col_indices.get("test_method") and part_id is not None:

                self._on_method_changed(part_id, value)

            elif col == self._col_indices.get("test_detail") and part_id is not None:

                self._on_detail_changed(part_id, value or None)
            elif col == self._col_indices.get("test_method_powered") and part_id is not None:

                self._on_method_changed(part_id, value, TestMode.powered)

            elif col == self._col_indices.get("test_detail_powered") and part_id is not None:

                self._on_detail_changed(part_id, value or None, TestMode.powered)

            elif col == self._col_indices.get("ap") and part_id is not None:

                self._on_value_changed(part_id, (value or None) if value in ("active", "passive") else None)



    def _on_model_changed(self, index: QModelIndex, old, new) -> None:

        if self._updating:

            return

        # Push an undo command capturing this change. The command itself will

        # reapply the change when pushed, but the ``_updating`` flag prevents

        # recursive signal handling.

        cmd = SetCellCommand(self, index, old, new)

        self.undo_stack.push(cmd)

        # Track edits to the Link column and persist/apply as requested

        try:

            link_col = self._col_indices.get("link")

            if link_col is not None and index.column() == link_col:

                # Resolve part id for this row

                part_id = None

                for c in range(self.model.columnCount()):

                    pid = self.model.data(self.model.index(index.row(), c), PartIdRole)

                    if pid is not None:

                        part_id = pid

                        break

                if part_id is not None:

                    url = (new or "").strip() if isinstance(new, str) else (str(new).strip() if new is not None else "")

                    self._part_product_links[part_id] = url or None

                    if self.apply_act.isChecked():

                        try:

                            with app_state.get_session() as session:

                                services.update_part_product_url(session, part_id, url or None)

                        except Exception:

                            pass

                    else:

                        self._dirty_links[part_id] = url or None

                        self.save_act.setEnabled(True)

                    QTimer.singleShot(0, self._install_datasheet_widgets)

        except Exception:

            pass



    def _open_selected_link(self) -> None:

        sel = self.table.selectionModel()

        if not sel or not sel.selectedIndexes():

            return

        row = sel.selectedIndexes()[0].row()

        # Resolve part id

        part_id = None

        for c in range(self.model.columnCount()):

            pid = self.proxy.data(self.proxy.index(row, c), PartIdRole)

            if pid is not None:

                part_id = pid

                break

        if part_id is None:

            return

        link = self._part_product_links.get(part_id)

        if link:

            QDesktopServices.openUrl(QUrl.fromUserInput(link))



    def _copy_selected_link(self) -> None:

        sel = self.table.selectionModel()

        if not sel or not sel.selectedIndexes():

            return

        row = sel.selectedIndexes()[0].row()

        part_id = None

        for c in range(self.model.columnCount()):

            pid = self.proxy.data(self.proxy.index(row, c), PartIdRole)

            if pid is not None:

                part_id = pid

                break

        if part_id is None:

            return

        link = self._part_product_links.get(part_id)

        if link:

            from PyQt6.QtGui import QGuiApplication

            cb = QGuiApplication.clipboard()

            try:

                cb.setText(link)

            except Exception:

                pass


    def _auto_link_selected_complex(self) -> None:  # pragma: no cover - UI glue
        sel = self.table.selectionModel()
        proxy = self.table.model()
        if proxy is None or sel is None or not sel.selectedIndexes():
            QMessageBox.information(self, "Auto-link", "Select one or more rows first.")
            return
        # Unique part ids from selection
        part_ids: list[int] = []
        for idx in sel.selectedIndexes():
            pid = idx.data(PartIdRole)
            if isinstance(pid, int) and pid not in part_ids:
                part_ids.append(pid)
        if not part_ids:
            QMessageBox.information(self, "Auto-link", "No parts selected.")
            return
        # Filter parts with Test method = Complex
        targets: list[int] = []
        for pid in part_ids:
            method = (self._test_assignments.get(pid, {}) or {}).get("method", "")
            if str(method).strip().lower() == "complex":
                targets.append(pid)
        if not targets:
            QMessageBox.information(self, "Auto-link", "Selection has no rows with Test method = Complex.")
            return
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Auto-link in Complex Editor")
        dlg.setText(f"Attempt auto-link for {len(targets)} part(s) by exact PN/alias match?")
        ok_btn = dlg.addButton("Start", QMessageBox.ButtonRole.AcceptRole)
        dlg.addButton(QMessageBox.StandardButton.Cancel)
        dlg.exec()
        if dlg.clickedButton() is not ok_btn:
            return

        prog = QProgressDialog("Starting...", "", 0, len(targets), self)
        prog.setWindowTitle("Auto-link in Complex Editor")
        prog.setCancelButton(None)
        prog.setAutoClose(True)
        prog.setMinimumDuration(0)
        prog.show()

        successes = 0
        skipped = 0
        errors = 0
        for i, pid in enumerate(targets, start=1):
            pn = self._part_number_for_part(pid)
            prog.setLabelText(
                f"{i}/{len(targets)}: {pn or pid}  |  linked:{successes} skipped:{skipped} errors:{errors}"
            )
            prog.setValue(i - 1)
            QApplication.processEvents()
            if not pn:
                skipped += 1
                continue
            try:
                ok = complex_linker.auto_link_by_pn(pid, pn)
                if ok:
                    successes += 1
                    # Mark as CE-linked and set display methods for both modes
                    try:
                        self._part_ce_linked.add(pid)
                    except Exception:
                        pass
                    ta = self._test_assignments.setdefault(
                        pid, {"method": "", "qt_path": None, "method_powered": "", "qt_path_powered": None}
                    )
                    ta["method"] = "Complex"
                    ta["method_powered"] = "Complex"
                    # Persist or stage test assignment based on Apply toggle
                    if self.apply_act.isChecked():
                        try:
                            self._persist_test_assignment(pid)
                            self._dirty_tests.discard(pid)
                        except Exception:
                            errors += 1
                    else:
                        self._dirty_tests.add(pid)
                        try:
                            self.save_act.setEnabled(True)
                        except Exception:
                            pass
                    # Refresh row UI for this part
                    self._refresh_rows_for_part(pid)
                else:
                    skipped += 1
            except Exception:
                errors += 1
            # Keep UI responsive
            QApplication.processEvents()

        prog.setValue(len(targets))
        # If panel visible and pointing to a selected part, refresh context
        try:
            self._update_complex_panel_context()
        except Exception:
            pass

        details: list[str] = []
        details.append(f"Linked: {successes}")
        if skipped:
            details.append(f"Skipped: {skipped}")
        if errors:
            details.append(f"Errors: {errors}")
        QMessageBox.information(
            self,
            "Auto-link complete",
            "\n".join(details) if details else "No work performed.",
        )

    def _sync_ce_links_assembly(self) -> None:  # pragma: no cover - UI glue
        # Gather unique part ids in current model
        part_ids: list[int] = []
        seen: set[int] = set()
        for r in self._rows_raw:
            pid = getattr(r, "part_id", None)
            if isinstance(pid, int) and pid not in seen:
                seen.add(pid)
                part_ids.append(pid)
        if not part_ids:
            QMessageBox.information(self, "Sync CE Links", "No parts found in this assembly.")
            return

        prog = QProgressDialog("Starting...", "", 0, len(part_ids), self)
        prog.setWindowTitle("Sync CE Links (Assembly)")
        prog.setCancelButton(None)
        prog.setAutoClose(True)
        prog.setMinimumDuration(0)
        prog.show()

        successes = 0
        skipped = 0
        errors = 0
        for i, pid in enumerate(part_ids, start=1):
            pn = self._part_number_for_part(pid)
            prog.setLabelText(
                f"{i}/{len(part_ids)}: {pn or pid}  |  linked:{successes} skipped:{skipped} errors:{errors}"
            )
            prog.setValue(i - 1)
            QApplication.processEvents()
            if not pn:
                skipped += 1
                continue
            try:
                ok = complex_linker.auto_link_by_pn(pid, pn)
                if ok:
                    successes += 1
                    # Record locally for detail rendering, set Complex for both modes
                    try:
                        self._part_ce_linked.add(pid)
                    except Exception:
                        pass
                    ta = self._test_assignments.setdefault(
                        pid, {"method": "", "qt_path": None, "method_powered": "", "qt_path_powered": None}
                    )
                    ta["method"] = "Complex"
                    ta["method_powered"] = "Complex"
                    # Persist or stage test assignment based on Apply toggle
                    if self.apply_act.isChecked():
                        try:
                            self._persist_test_assignment(pid)
                            self._dirty_tests.discard(pid)
                        except Exception:
                            errors += 1
                    else:
                        self._dirty_tests.add(pid)
                        try:
                            self.save_act.setEnabled(True)
                        except Exception:
                            pass
                    self._refresh_rows_for_part(pid)
                else:
                    skipped += 1
            except Exception:
                errors += 1
            QApplication.processEvents()

        prog.setValue(len(part_ids))
        # Refresh panel context and table state
        try:
            self._update_complex_panel_context()
        except Exception:
            pass
        QMessageBox.information(
            self,
            "Sync CE Links",
            f"Linked: {successes}\nSkipped: {skipped}\nErrors: {errors}",
        )



    def _delete_selected_rows(self) -> None:  # pragma: no cover - UI glue

        proxy = self.table.model()

        sel = self.table.selectionModel()

        if proxy is None or sel is None or not sel.selectedIndexes():

            return

        rows = sorted({i.row() for i in sel.selectedIndexes()})

        bom_ids: set[int] = set()

        part_ids: set[int] = set()

        for r in rows:

            # Try to find a BOM item id on any column in this row

            bid = None

            pid = None

            for c in range(proxy.columnCount()):

                idx = proxy.index(r, c)

                if bid is None:

                    b = proxy.data(idx, BOMItemIdRole)

                    if isinstance(b, int):

                        bid = b

                if pid is None:

                    p = proxy.data(idx, PartIdRole)

                    if isinstance(p, int):

                        pid = p

                if bid is not None and pid is not None:

                    break

            if bid is not None:

                bom_ids.add(int(bid))

            elif pid is not None:

                part_ids.add(int(pid))

        if not bom_ids and not part_ids:

            return

        # Confirm

        total = len(bom_ids) + len(part_ids)

        msg = (

            f"Delete {total} row(s)?\n\n"

            + ("Grouped rows (by PN) will remove all references for that part in this assembly." if part_ids else "")

        )

        res = QMessageBox.question(self, "Delete", msg)

        if res != QMessageBox.StandardButton.Yes:

            return

        # Perform deletion

        try:

            with app_state.get_session() as session:

                if bom_ids:

                    services.delete_bom_items(session, list(bom_ids))

                for pid in part_ids:

                    services.delete_bom_items_for_part(session, self._assembly_id, pid)

        except Exception as exc:

            QMessageBox.warning(self, "Delete failed", str(exc))

            return

        # Reload data and model

        self._load_data()

        self._rebuild_model()



    def _visible_columns(self) -> list[int]:

        model = self.table.model()

        if model is None:

            return []

        cols = []

        for c in range(model.columnCount()):

            try:

                if not self.table.isColumnHidden(c):

                    cols.append(c)

            except Exception:

                cols.append(c)

        return cols



    def _is_full_row_selection(self) -> bool:

        proxy = self.table.model()

        sel = self.table.selectionModel()

        if proxy is None or sel is None:

            return False

        idxs = sel.selectedIndexes()

        if not idxs:

            return False

        vis_cols = set(self._visible_columns())

        # Map row -> set of selected visible columns

        rows = sorted({i.row() for i in idxs})

        for r in rows:

            cols_sel = {i.column() for i in idxs if i.row() == r and i.column() in vis_cols}

            if cols_sel != vis_cols:

                return False

        return True



    def _clear_selected_cells(self) -> None:

        proxy = self.table.model()

        sel = self.table.selectionModel()

        if proxy is None or sel is None:

            return

        idxs = sel.selectedIndexes()

        if not idxs:

            cur = self.table.currentIndex()

            if cur.isValid():

                idxs = [cur]

        if not idxs:

            return

        tm_col = self._col_indices.get("test_method")

        td_col = self._col_indices.get("test_detail")
        tm_powered_col = self._col_indices.get("test_method_powered")

        td_powered_col = self._col_indices.get("test_detail_powered")

        ap_col = self._col_indices.get("ap")

        for idx in idxs:

            try:

                # Only clear editable cells

                if not (proxy.flags(idx) & Qt.ItemFlag.ItemIsEditable):

                    continue

            except Exception:

                pass

            col = idx.column()

            part_id = proxy.data(idx, PartIdRole)

            # Apply column-specific hooks for consistency with paste handler

            proxy.setData(idx, "")

            if part_id is None:

                continue

            if tm_col is not None and col == tm_col:

                self._on_method_changed(part_id, "")

            elif td_col is not None and col == td_col:

                self._on_detail_changed(part_id, None)
            elif tm_powered_col is not None and col == tm_powered_col:

                self._on_method_changed(part_id, "", TestMode.powered)

            elif td_powered_col is not None and col == td_powered_col:

                self._on_detail_changed(part_id, None, TestMode.powered)

            elif ap_col is not None and col == ap_col:

                self._on_value_changed(part_id, None)



    def _delete_or_clear_selection(self) -> None:

        # If the user has selected entire rows (all visible columns), confirm row delete;

        # otherwise treat Delete as clearing the selected cell values only.

        if self._is_full_row_selection():

            self._delete_selected_rows()

        else:

            self._clear_selected_cells()



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

        candidates.append(DATA_ROOT / "function_list.txt")

        for p in candidates:

            try:

                if p.exists():

                    lines = p.read_text(encoding="utf-8").splitlines()

                    items = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]

                    if items:

                        return items

            except Exception:

                continue

        # Fallback: derive options from prefix_macros (unique macro names)

        try:

            from ..logic.prefix_macros import load_prefix_macros as _lpm

            macros = sorted({macro for _pref, macro in (_lpm() or [])})

            return macros

        except Exception:

            return []



    # ------------------------------------------------------------------

    @staticmethod

    def _configure_icon_button(button: QToolButton, color: str, enabled: bool, disabled_color: str | None = None) -> None:

        """Apply consistent styling to inline icon buttons."""

        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        button.setEnabled(enabled)

        disabled_color = disabled_color or color

        button.setStyleSheet(

            "QToolButton { color: %s; padding: 0px; border: none; } "

            "QToolButton:disabled { color: %s; opacity: 0.6; }" % (color, disabled_color)

        )



    @staticmethod

    def _configure_icon_button(button: QToolButton, color: str, enabled: bool) -> None:

        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        button.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)

        button.setEnabled(enabled)

        button.setStyleSheet(

            "QToolButton { color: %s; padding: 0px; border: none; }"

            " QToolButton:disabled { color: %s; opacity: 0.5; }" % (color, color)

        )



    def _load_icon(self, name: str, fallback: QStyle.StandardPixmap) -> QIcon:

        try:

            p = Path(__file__).resolve().parent / "icons" / name

            if p.exists():

                icon = QIcon(str(p))

                if not icon.isNull():

                    return icon

        except Exception:

            pass

        return self.style().standardIcon(fallback)



    def _icon_pdf_open(self) -> QIcon:

        return self._load_icon("pdf_green.svg", QStyle.StandardPixmap.SP_FileIcon)



    def _icon_pdf_add(self) -> QIcon:

        return self._load_icon("pdf_plus.svg", QStyle.StandardPixmap.SP_DialogOpenButton)



    def _icon_pdf_error(self) -> QIcon:

        return self._load_icon("pdf_x.svg", QStyle.StandardPixmap.SP_MessageBoxCritical)



    def _icon_for_loading(self) -> QIcon:

        return self._load_icon("pdf_loading.svg", QStyle.StandardPixmap.SP_BrowserReload)



    def _icon_link_open(self) -> QIcon:

        return self._load_icon("link_green.svg", QStyle.StandardPixmap.SP_DialogOpenButton)



    def _icon_link_add(self) -> QIcon:

        return self._load_icon("link_plus.svg", QStyle.StandardPixmap.SP_DialogOpenButton)



    def _install_datasheet_widgets(self) -> None:

        # Rebuild buttons for Datasheet and Link columns without showing raw text

        ds_col = self._col_indices.get("ds")

        link_col = self._col_indices.get("link")

        if ds_col is None and link_col is None:

            return

        self._updating = True

        try:

            for r in range(self.model.rowCount()):

                ds_idx = self.model.index(r, ds_col) if ds_col is not None else None

                link_idx = self.model.index(r, link_col) if link_col is not None else None

                if ds_idx is not None:

                    self.model.setData(ds_idx, "", Qt.ItemDataRole.DisplayRole)

                if link_idx is not None:

                    self.model.setData(link_idx, "", Qt.ItemDataRole.DisplayRole)



                part_id = None

                for c in range(self.model.columnCount()):

                    pid = self.model.data(self.model.index(r, c), PartIdRole)

                    if pid is not None:

                        part_id = pid

                        break

                if part_id is None:

                    continue



                path = self._part_datasheets.get(part_id)

                path_value = path or ""

                path_exists = bool(path_value) and Path(path_value).exists()

                btn = QToolButton(self.table)

                try:

                    from PyQt6.QtCore import QSize as _QSize

                    btn.setAutoRaise(True)

                    btn.setIconSize(_QSize(24, 24))

                    btn.setFixedSize(30, 30)

                except Exception:

                    pass



                _dirty_ds = getattr(self, "_dirty_datasheets", {})
                staged_flag = part_id in _dirty_ds

                staged_value = _dirty_ds.get(part_id)

                if part_id in getattr(self, "_datasheet_loading", set()):

                    btn.setIcon(self._icon_for_loading())

                    self._configure_icon_button(btn, "#1F6FEB", False)

                    btn.setToolTip("Searching datasheet...")

                elif staged_flag and staged_value is None:

                    btn.setIcon(self._icon_pdf_add())

                    self._configure_icon_button(btn, "#1F6FEB", True)

                    btn.setToolTip("Datasheet will be removed on Save. Click to attach a replacement.")

                    btn.clicked.connect(lambda _=False, pid=part_id: self._open_attach_dialog(pid))

                else:

                    effective_path = path if path_exists else (staged_value if staged_flag else path)

                    if effective_path and Path(effective_path).exists():

                        btn.setIcon(self._icon_pdf_open())

                        self._configure_icon_button(btn, "#28A745", True)

                        tip = "Open datasheet"

                        if staged_flag and staged_value:

                            tip += " (pending save)"

                        btn.setToolTip(tip)

                        btn.clicked.connect(lambda _=False, p=effective_path: self._open_pdf_path(p))

                    else:

                        if path_value.strip() and not path_exists:

                            btn.setIcon(self._icon_pdf_error())

                            self._configure_icon_button(btn, "#DC2626", True)

                            btn.setToolTip("Stored path not found. Click to attach a new datasheet.")

                        elif part_id in getattr(self, "_datasheet_failed", set()):

                            btn.setIcon(self._icon_pdf_error())

                            self._configure_icon_button(btn, "#DC2626", True)

                            btn.setToolTip("Search attempted; no datasheet found. Click to attach manually.")

                        else:

                            btn.setIcon(self._icon_pdf_add())

                            self._configure_icon_button(btn, "#1F6FEB", True)

                            btn.setToolTip("Attach datasheet")

                        btn.clicked.connect(lambda _=False, pid=part_id: self._open_attach_dialog(pid))



                if ds_idx is not None:

                    self.model.setData(ds_idx, self._part_datasheets.get(part_id), DatasheetRole)

                    proxy_idx = self.proxy.mapFromSource(ds_idx)

                    self.table.setIndexWidget(proxy_idx, btn)



                if link_idx is not None:

                    link_btn = QToolButton(self.table)

                    try:

                        from PyQt6.QtCore import QSize as _QSize

                        link_btn.setAutoRaise(True)

                        link_btn.setIconSize(_QSize(24, 24))

                        link_btn.setFixedSize(30, 30)

                    except Exception:

                        pass

                    link = getattr(self, "_dirty_links", {}).get(part_id, self._part_product_links.get(part_id))

                    link_effective = link or ""

                    self.model.setData(link_idx, link_effective, LinkUrlRole)

                    if link:

                        link_btn.setIcon(self._icon_link_open())

                        self._configure_icon_button(link_btn, "#28A745", True)

                        link_btn.setToolTip("Open product link")

                        link_btn.clicked.connect(lambda _=False, u=link_effective: QDesktopServices.openUrl(QUrl.fromUserInput(u)))

                    else:

                        link_btn.setIcon(self._icon_link_add())

                        self._configure_icon_button(link_btn, "#1F6FEB", True)

                        link_btn.setToolTip("Set product link")

                        link_btn.clicked.connect(lambda _=False, pid=part_id: self._prompt_link_entry(pid))

                    proxy_link_idx = self.proxy.mapFromSource(link_idx)

                    self.table.setIndexWidget(proxy_link_idx, link_btn)

        finally:

            self._updating = False



    def _open_attach_dialog(self, part_id: int) -> None:

        from .datasheet_attach_dialog import DatasheetAttachDialog

        dlg = DatasheetAttachDialog(part_id, self)

        dlg.attached.connect(lambda canonical, pid=part_id: self._on_datasheet_attached(pid, canonical))

        dlg.exec()



    def _on_datasheet_attached(self, part_id: int, canonical: str) -> None:

        # Respect Apply toggle: stage or persist

        self._datasheet_failed.discard(part_id)

        if self.apply_act.isChecked():

            try:

                with app_state.get_session() as session:

                    services.update_part_datasheet_url(session, part_id, canonical)

            except Exception as exc:

                QMessageBox.warning(self, "Attach failed", str(exc))

                return

            self._part_datasheets[part_id] = canonical

            self._dirty_datasheets.pop(part_id, None)

        else:

            self._part_datasheets[part_id] = canonical

            self._dirty_datasheets[part_id] = canonical

            self.save_act.setEnabled(True)

        # Update UI labels and link column from DB snapshot (best-effort)

        try:

            from ..models import Part

            with app_state.get_session() as session:

                p = session.get(Part, part_id)

                if p:

                    self._fanout_part_field(part_id, "desc", getattr(p, "description", None))

                    self._part_product_links[part_id] = getattr(p, "product_url", None)

        except Exception:

            pass

        self._install_datasheet_widgets()



    def _on_datasheet_failed(self, part_id: int) -> None:

        self._datasheet_failed.add(part_id)

        self._install_datasheet_widgets()



    def _on_manual_link(self, part_id: int, url: str) -> None:

        self._part_manual_links[part_id] = url

        # Persist as product link on the Part for reuse across projects

        try:

            with app_state.get_session() as session:

                services.update_part_product_url(session, part_id, url)

        except Exception:

            pass

        self._part_product_links[part_id] = url

        self._datasheet_failed.discard(part_id)

        # Update visible cell

        link_col = self._col_indices.get("link")

        if link_col is not None:

            for r in range(self.model.rowCount()):

                for c in range(self.model.columnCount()):

                    if self.model.data(self.model.index(r, c), PartIdRole) == part_id:

                        self.model.setData(self.model.index(r, link_col), url)

                        break

        self._install_datasheet_widgets()



    def _open_pdf_path(self, path: str) -> None:

        """Open a datasheet path using the configured viewer."""

        t0 = time.perf_counter()

        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        try:

            size = Path(path).stat().st_size if Path(path).exists() else -1

        except Exception:

            size = -1

        if PDF_OPEN_DEBUG:

            logging.info(

                "Open-PDF[%s]: start part_path=%s size=%s bytes viewer=%s",

                ts,

                path,

                size,

                PDF_VIEWER,

            )

        t1 = time.perf_counter()

        try:

            local_path = str(get_local_open_path(Path(path)))

        except Exception:

            local_path = path

        t2 = time.perf_counter()

        if PDF_OPEN_DEBUG:

            logging.info(

                "Open-PDF: resolved local path in %.1f ms -> %s",

                (t2 - t1) * 1000.0,

                local_path,

            )



        opened = False

        open_err: str | None = None

        t3 = time.perf_counter()

        try:

            if PDF_VIEWER == "chrome":

                exe = (

                    PDF_VIEWER_PATH

                    or shutil.which("chrome")

                    or shutil.which("chrome.exe")

                    or r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"

                )

                if exe and os.path.exists(exe):

                    url = QUrl.fromLocalFile(local_path).toString()

                    subprocess.Popen([exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    opened = True

                else:

                    open_err = f"Chrome not found (PDF_VIEWER_PATH={PDF_VIEWER_PATH})"

            elif PDF_VIEWER == "edge":

                exe = (

                    PDF_VIEWER_PATH

                    or shutil.which("msedge")

                    or shutil.which("msedge.exe")

                    or r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"

                )

                if exe and os.path.exists(exe):

                    url = QUrl.fromLocalFile(local_path).toString()

                    subprocess.Popen([exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    opened = True

                else:

                    open_err = f"Edge not found (PDF_VIEWER_PATH={PDF_VIEWER_PATH})"

            else:

                QDesktopServices.openUrl(QUrl.fromLocalFile(local_path))

                opened = True

        except Exception as e:  # pragma: no cover - environment dependent

            open_err = str(e)

        t4 = time.perf_counter()

        if PDF_OPEN_DEBUG:

            logging.info(

                "Open-PDF: open call returned in %.1f ms (ok=%s)%s",

                (t4 - t3) * 1000.0,

                opened,

                f" err={open_err}" if open_err else "",

            )

            logging.info("Open-PDF: total time %.1f ms", (t4 - t0) * 1000.0)



    def _prompt_link_entry(self, part_id: int) -> None:

        current = self._part_product_links.get(part_id) or ""

        text, ok = QInputDialog.getText(self, "Set Product Link", "Enter product URL:", text=current)

        if not ok:

            return

        text = (text or "").strip()

        link_col = self._col_indices.get("link")

        if link_col is None:

            return

        for row in range(self.model.rowCount()):

            idx = self.model.index(row, link_col)

            if self.model.data(idx, PartIdRole) == part_id:

                self.model.setData(idx, text)

                break

        QTimer.singleShot(0, self._install_datasheet_widgets)



    def _remove_selected_datasheets(self) -> None:

        proxy = self.table.model()

        sel = self.table.selectionModel()

        if proxy is None or sel is None or not sel.selectedIndexes():

            return

        rows = sorted({i.row() for i in sel.selectedIndexes()})

        # Collect part IDs that currently have a datasheet path

        target_pids: list[int] = []

        ds_col = self._col_indices.get("ds")

        for r in rows:

            # find part_id on this row

            part_id = None

            for c in range(proxy.columnCount()):

                pid = proxy.data(proxy.index(r, c), PartIdRole)

                if pid is not None:

                    part_id = pid

                    break

            if part_id is None:

                continue

            path = self._part_datasheets.get(part_id)

            if path:

                target_pids.append(part_id)

        if not target_pids:

            return

        # Confirm

        msg = (

            "Remove datasheet for the selected part?" if len(target_pids) == 1

            else f"Remove datasheets for {len(target_pids)} parts?"

        )

        res = QMessageBox.question(self, "Remove Datasheet", msg)

        if res != QMessageBox.StandardButton.Yes:

            return

        if self.apply_act.isChecked():

            removed_any = False

            with app_state.get_session() as session:

                for pid in target_pids:

                    try:

                        services.remove_part_datasheet(session, pid, delete_file=True)

                        self._part_datasheets[pid] = None

                        self._dirty_datasheets.pop(pid, None)

                        removed_any = True

                    except Exception as exc:

                        QMessageBox.warning(self, "Remove failed", str(exc))

            if removed_any:

                self._install_datasheet_widgets()

        else:

            # Stage removal

            for pid in target_pids:

                self._dirty_datasheets[pid] = None

                self._part_datasheets[pid] = None

            self.save_act.setEnabled(True)

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















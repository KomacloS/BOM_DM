"""BOM editor for classifying parts as active or passive."""

from __future__ import annotations

from typing import Dict

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QSettings, pyqtSignal
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QToolBar,
    QLineEdit,
    QToolButton,
    QMenu,
    QAction,
    QTableView,
    QStyledItemDelegate,
    QComboBox,
    QMessageBox,
)

from .. import services
from . import state as app_state


PartIdRole = Qt.ItemDataRole.UserRole + 1


class BOMFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._filter = ""

    def setFilterString(self, text: str) -> None:
        self._filter = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent):  # pragma: no cover - Qt glue
        if not self._filter:
            return True
        model = self.sourceModel()
        for col in range(4):  # PN, Reference, Description, Manufacturer
            idx = model.index(source_row, col, source_parent)
            data = model.data(idx)
            if data and self._filter in str(data).lower():
                return True
        return False


class ActivePassiveDelegate(QStyledItemDelegate):
    valueChanged = pyqtSignal(int, str)  # part_id, new_value

    def createEditor(self, parent, option, index):  # pragma: no cover - UI glue
        combo = QComboBox(parent)
        combo.addItems(["active", "passive"])
        part_id = index.data(PartIdRole)

        def _on_change(value: str) -> None:
            self.commitData.emit(combo)
            self.closeEditor.emit(combo, QStyledItemDelegate.NoHint)
            self.valueChanged.emit(part_id, value)

        combo.currentTextChanged.connect(_on_change)
        return combo

    def setEditorData(self, editor, index):  # pragma: no cover - UI glue
        editor.setCurrentText(index.data() or "passive")

    def setModelData(self, editor, model, index):  # pragma: no cover - UI glue
        model.setData(index, editor.currentText())


class BOMEditorPane(QWidget):
    """Widget presenting BOM items with active/passive classification."""

    def __init__(self, assembly_id: int, parent=None) -> None:
        super().__init__(parent)
        self._assembly_id = assembly_id
        self._settings = QSettings("BOM_DB", f"BOMEditorPane/{assembly_id}")
        self._dirty: Dict[int, str] = {}
        self._parts_state: Dict[int, str] = {}

        layout = QVBoxLayout(self)
        self.toolbar = QToolBar()
        layout.addWidget(self.toolbar)

        # Filter
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter…")
        self.toolbar.addWidget(self.filter_edit)

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
        self.model.setHorizontalHeaderLabels(
            ["PN", "Reference", "Description", "Manufacturer", "Active/Passive"]
        )
        self.proxy = BOMFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)

        # Delegate
        self.delegate = ActivePassiveDelegate(self.table)
        self.delegate.valueChanged.connect(self._on_value_changed)
        self.table.setItemDelegateForColumn(4, self.delegate)

        self.filter_edit.textChanged.connect(self.proxy.setFilterString)
        self.apply_act.toggled.connect(self._on_apply_toggled)
        self.save_act.triggered.connect(self._save_changes)

        self._setup_columns_menu()
        self._load_data()

    # ------------------------------------------------------------------
    def _setup_columns_menu(self) -> None:
        self._column_actions: list[QAction] = []
        for idx, name in enumerate(
            ["PN", "Reference", "Description", "Manufacturer", "Active/Passive"]
        ):
            act = QAction(name, self)
            act.setCheckable(True)
            visible = self._settings.value(f"col{idx}_visible", True, type=bool)
            act.setChecked(visible)
            act.toggled.connect(lambda checked, col=idx: self.table.setColumnHidden(col, not checked))
            self.columns_menu.addAction(act)
            self._column_actions.append(act)
            self.table.setColumnHidden(idx, not visible)
            width = self._settings.value(f"col{idx}_width", type=int)
            if width:
                self.table.setColumnWidth(idx, int(width))

    def _load_data(self) -> None:
        with app_state.get_session() as session:
            rows = services.get_joined_bom_for_assembly(session, self._assembly_id)
        self.model.setRowCount(0)
        for r in rows:
            items = [
                QStandardItem(r.part_number),
                QStandardItem(r.reference),
                QStandardItem(r.description or ""),
                QStandardItem(r.manufacturer or ""),
                QStandardItem(r.active_passive),
            ]
            for i, it in enumerate(items):
                it.setEditable(i == 4)
                it.setData(r.part_id, PartIdRole)
            self.model.appendRow(items)
            self._parts_state[r.part_id] = r.active_passive

    # ------------------------------------------------------------------
    def _on_value_changed(self, part_id: int, value: str) -> None:
        old_value = self._parts_state.get(part_id, "passive")
        # update all rows with this part_id
        for row in range(self.model.rowCount()):
            idx0 = self.model.index(row, 0)
            pid = self.model.data(idx0, PartIdRole)
            if pid == part_id:
                self.model.setData(self.model.index(row, 4), value)
        if self.apply_act.isChecked():
            try:
                with app_state.get_session() as session:
                    services.update_part_active_passive(session, part_id, value)
            except Exception as exc:  # pragma: no cover - depends on DB
                QMessageBox.warning(self, "Update failed", str(exc))
                # revert
                for row in range(self.model.rowCount()):
                    if self.model.data(self.model.index(row, 0), PartIdRole) == part_id:
                        self.model.setData(self.model.index(row, 4), old_value)
                return
            self._parts_state[part_id] = value
        else:
            self._dirty[part_id] = value
            self.save_act.setEnabled(True)

    def _on_apply_toggled(self, checked: bool) -> None:
        if checked and self._dirty:
            self._save_changes()

    def _save_changes(self) -> None:
        if not self._dirty:
            return
        failures = []
        for part_id, value in list(self._dirty.items()):
            try:
                with app_state.get_session() as session:
                    services.update_part_active_passive(session, part_id, value)
            except Exception as exc:
                failures.append(str(exc))
                # revert
                old_val = self._parts_state.get(part_id, "passive")
                for row in range(self.model.rowCount()):
                    if self.model.data(self.model.index(row, 0), PartIdRole) == part_id:
                        self.model.setData(self.model.index(row, 4), old_val)
            else:
                self._parts_state[part_id] = value
                del self._dirty[part_id]
        if failures:
            QMessageBox.warning(self, "Save failed", "; ".join(failures))
        else:
            QMessageBox.information(self, "Saved", "Changes saved.")
        self.save_act.setEnabled(bool(self._dirty))

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # pragma: no cover - UI glue
        for idx, act in enumerate(self._column_actions):
            self._settings.setValue(f"col{idx}_visible", act.isChecked())
            self._settings.setValue(f"col{idx}_width", self.table.columnWidth(idx))
        super().closeEvent(event)

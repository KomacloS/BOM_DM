from __future__ import annotations

import re
import sys
from functools import partial
from typing import Any, Callable

from PySide6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QSettings,
    QSize,
    Qt,
    QSortFilterProxyModel,
    QTimer,
)
from PySide6.QtGui import (
    QDesktopServices,
    QKeySequence,
    QStandardItem,
    QStandardItemModel,
    QCloseEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSplitter,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QLabel,
)
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QAbstractItemView, QHeaderView

from .. import services
from ..models import Part, PartType
from .state import get_session


COLUMNS = [
    "Part #",
    "Description",
    "Pkg",
    "Value",
    "Function",
    "Type (A/P)",
    "Power req",
    "Datasheet",
    "Product",
    "Tol +",
    "Tol −",
    "Created",
]


def _natural_key(value: str) -> list[Any]:
    parts = re.split(r"(\d+)", value)
    key: list[Any] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return key


class PartsProxyModel(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self._filter_text = ""
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)

    def setFilterText(self, text: str) -> None:
        self._filter_text = (text or "").strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # type: ignore[override]
        if not self._filter_text:
            return True
        model = self.sourceModel()
        if model is None:
            return True
        index = model.index(source_row, 0, source_parent)
        blob = index.data(Qt.ItemDataRole.UserRole + 1)
        if not blob:
            return True
        return self._filter_text in str(blob)

    def lessThan(self, left, right) -> bool:  # type: ignore[override]
        left_data = left.data(Qt.ItemDataRole.DisplayRole)
        right_data = right.data(Qt.ItemDataRole.DisplayRole)
        left_str = str(left_data) if left_data is not None else ""
        right_str = str(right_data) if right_data is not None else ""
        return _natural_key(left_str) < _natural_key(right_str)


class PartsTerminalWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BOM_DB – Parts Terminal")

        self._settings = QSettings("BOM_DB", "PartsTerminal")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh_parts)
        self._updating_form = False
        self._current_part_id: int | None = None
        self._current_original: dict[str, Any] | None = None
        self._parts: list[Part] = []

        container = QWidget()
        layout = QVBoxLayout(container)
        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search PN / desc / package / value…")
        self.search_edit.textChanged.connect(self._debounced_search)
        self.search_edit.returnPressed.connect(self.refresh_parts)
        toolbar.addWidget(self.search_edit, stretch=1)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_parts)
        toolbar.addWidget(self.refresh_btn)

        self.new_btn = QPushButton("New PN")
        self.new_btn.clicked.connect(self._create_part)
        toolbar.addWidget(self.new_btn)

        self.delete_btn = QPushButton("Delete PN")
        self.delete_btn.clicked.connect(self._delete_part)
        self.delete_btn.setEnabled(False)
        toolbar.addWidget(self.delete_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save_part)
        self.save_btn.setEnabled(False)
        toolbar.addWidget(self.save_btn)

        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self._revert_changes)
        self.revert_btn.setEnabled(False)
        toolbar.addWidget(self.revert_btn)

        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter = splitter

        self.model = QStandardItemModel(0, len(COLUMNS))
        self.model.setHorizontalHeaderLabels(COLUMNS)
        self.proxy = PartsProxyModel()
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.doubleClicked.connect(lambda _index: self.part_number_edit.setFocus())
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for idx in range(2, len(COLUMNS)):
            header.setSectionResizeMode(idx, QHeaderView.ResizeMode.ResizeToContents)

        splitter.addWidget(self.table)

        editor_container = QWidget()
        form_layout = QFormLayout(editor_container)

        self.part_number_edit = QLineEdit()
        form_layout.addRow("Part number", self.part_number_edit)

        self.description_edit = QLineEdit()
        form_layout.addRow("Description", self.description_edit)

        self.package_edit = QLineEdit()
        form_layout.addRow("Package", self.package_edit)

        self.value_edit = QLineEdit()
        form_layout.addRow("Value", self.value_edit)

        self.function_edit = QLineEdit()
        form_layout.addRow("Function", self.function_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItem("Passive", PartType.passive)
        self.type_combo.addItem("Active", PartType.active)
        form_layout.addRow("Type", self.type_combo)

        self.power_required_check = QCheckBox("Power required")
        form_layout.addRow("", self.power_required_check)

        self.datasheet_edit = QLineEdit()
        datasheet_row = QHBoxLayout()
        datasheet_row.addWidget(self.datasheet_edit)
        self.datasheet_open = QToolButton()
        self.datasheet_open.setText("Open")
        self.datasheet_open.clicked.connect(partial(self._open_url, self.datasheet_edit))
        datasheet_row.addWidget(self.datasheet_open)
        datasheet_widget = QWidget()
        datasheet_widget.setLayout(datasheet_row)
        form_layout.addRow("Datasheet", datasheet_widget)

        self.product_edit = QLineEdit()
        product_row = QHBoxLayout()
        product_row.addWidget(self.product_edit)
        self.product_open = QToolButton()
        self.product_open.setText("Open")
        self.product_open.clicked.connect(partial(self._open_url, self.product_edit))
        product_row.addWidget(self.product_open)
        product_widget = QWidget()
        product_widget.setLayout(product_row)
        form_layout.addRow("Product", product_widget)

        self.tol_p_edit = QLineEdit()
        form_layout.addRow("Tol +", self.tol_p_edit)

        self.tol_n_edit = QLineEdit()
        form_layout.addRow("Tol −", self.tol_n_edit)

        self.created_label = QLabel("–")
        form_layout.addRow("Created", self.created_label)

        splitter.addWidget(editor_container)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout.addWidget(splitter)
        self.setCentralWidget(container)

        self.status_label = QLabel("")
        self.statusBar().addWidget(self.status_label)

        self._connect_form_signals()
        self._restore_ui_state()

        QShortcut(QKeySequence.StandardKey.Find, self, activated=self._focus_search)
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self._save_part)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self.table, activated=self._delete_part)

        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self.refresh_parts()

    def sizeHint(self) -> QSize:  # pragma: no cover - Qt hint
        return QSize(1200, 700)

    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # pragma: no cover - Qt hook
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("header_state", self.table.horizontalHeader().saveState())
        self._settings.setValue("splitter_state", self._splitter.saveState())
        super().closeEvent(event)

    # ------------------------------------------------------------------
    def _restore_ui_state(self) -> None:
        geometry = self._settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        header_state = self._settings.value("header_state")
        if header_state:
            self.table.horizontalHeader().restoreState(header_state)
        splitter_state = self._settings.value("splitter_state")
        if splitter_state:
            self._splitter.restoreState(splitter_state)

    # ------------------------------------------------------------------
    def _debounced_search(self) -> None:
        self._search_timer.start()

    # ------------------------------------------------------------------
    def _focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    # ------------------------------------------------------------------
    def _connect_form_signals(self) -> None:
        for widget in (
            self.part_number_edit,
            self.description_edit,
            self.package_edit,
            self.value_edit,
            self.function_edit,
            self.datasheet_edit,
            self.product_edit,
            self.tol_p_edit,
            self.tol_n_edit,
        ):
            widget.textEdited.connect(self._on_field_edited)
        self.type_combo.currentIndexChanged.connect(self._on_field_edited)
        self.power_required_check.stateChanged.connect(self._on_field_edited)

    # ------------------------------------------------------------------
    def _on_field_edited(self, *args) -> None:
        if self._updating_form:
            return
        self._update_dirty_state()

    # ------------------------------------------------------------------
    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    # ------------------------------------------------------------------
    def _with_session(self, fn: Callable[[Any], Any]) -> Any:
        with get_session() as session:
            return fn(session)

    # ------------------------------------------------------------------
    def refresh_parts(self) -> None:
        query = self.search_edit.text()
        try:
            parts = self._with_session(lambda s: services.search_parts(s, query, limit=500))
        except Exception as exc:  # pragma: no cover - user feedback
            QMessageBox.warning(self, "Search", str(exc))
            return
        current_id = self._current_part_id
        self._populate_table(parts)
        self._set_status(f"{len(parts)} parts loaded")
        if current_id and self._select_part(current_id):
            return
        if parts:
            first_id = parts[0].id
            if first_id is not None:
                self._select_part(first_id)
        else:
            self._load_part(None)

    # ------------------------------------------------------------------
    def _populate_table(self, parts: list[Part]) -> None:
        self._parts = parts
        self.model.removeRows(0, self.model.rowCount())
        for row, part in enumerate(parts):
            values = [
                part.part_number,
                part.description or "",
                part.package or "",
                part.value or "",
                part.function or "",
                part.active_passive.value.capitalize(),
                "Yes" if part.power_required else "",
                part.datasheet_url or "",
                part.product_url or "",
                part.tol_p or "",
                part.tol_n or "",
                part.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
            row_items: list[QStandardItem] = []
            search_blob = " ".join(values).lower()
            for col, value in enumerate(values):
                item = QStandardItem(value)
                item.setEditable(False)
                if part.id is not None:
                    item.setData(part.id, Qt.ItemDataRole.UserRole)
                item.setData(search_blob, Qt.ItemDataRole.UserRole + 1)
                if value:
                    item.setToolTip(value)
                row_items.append(item)
            self.model.appendRow(row_items)
        self.proxy.setFilterText(self.search_edit.text())
        # Selection handling will toggle delete button

    # ------------------------------------------------------------------
    def _select_part(self, part_id: int) -> bool:
        for row in range(self.model.rowCount()):
            item = self.model.item(row, 0)
            if item is None:
                continue
            item_id = item.data(Qt.ItemDataRole.UserRole)
            if item_id == part_id:
                source_index = self.model.index(row, 0)
                proxy_index = self.proxy.mapFromSource(source_index)
                if proxy_index.isValid():
                    selection = QItemSelection(proxy_index, proxy_index)
                    self.table.selectionModel().select(
                        selection,
                        QItemSelectionModel.SelectionFlag.ClearAndSelect
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    self.table.scrollTo(proxy_index)
                    return True
        return False

    # ------------------------------------------------------------------
    def _on_selection_changed(self, selected: QItemSelection, _deselected: QItemSelection) -> None:
        indexes = selected.indexes()
        if not indexes:
            self._load_part(None)
            return
        proxy_index = indexes[0]
        source_index = self.proxy.mapToSource(proxy_index)
        item = self.model.itemFromIndex(source_index)
        if item is None:
            self._load_part(None)
            return
        part_id = item.data(Qt.ItemDataRole.UserRole)
        if part_id is None:
            self._load_part(None)
            return
        self._load_part(part_id)

    # ------------------------------------------------------------------
    def _load_part(self, part_id: int | None) -> None:
        self._current_part_id = part_id
        if part_id is None:
            self._current_original = None
            self._set_form_enabled(False)
            self._clear_form()
            self.delete_btn.setEnabled(False)
            return
        part = next((p for p in self._parts if p.id == part_id), None)
        if part is None:
            self._current_original = None
            self._set_form_enabled(False)
            self._clear_form()
            self.delete_btn.setEnabled(False)
            return
        self._set_form_enabled(True)
        self._updating_form = True
        try:
            self.part_number_edit.setText(part.part_number)
            self.description_edit.setText(part.description or "")
            self.package_edit.setText(part.package or "")
            self.value_edit.setText(part.value or "")
            self.function_edit.setText(part.function or "")
            index = self.type_combo.findData(part.active_passive)
            self.type_combo.setCurrentIndex(max(0, index))
            self.power_required_check.setChecked(bool(part.power_required))
            self.datasheet_edit.setText(part.datasheet_url or "")
            self.product_edit.setText(part.product_url or "")
            self.tol_p_edit.setText(part.tol_p or "")
            self.tol_n_edit.setText(part.tol_n or "")
            self.created_label.setText(part.created_at.strftime("%Y-%m-%d %H:%M"))
        finally:
            self._updating_form = False
        self._current_original = self._collect_form_data()
        self._update_dirty_state()
        self.delete_btn.setEnabled(True)

    # ------------------------------------------------------------------
    def _collect_form_data(self) -> dict[str, Any]:
        return {
            "part_number": self.part_number_edit.text().strip(),
            "description": self._normalized_text(self.description_edit.text()),
            "package": self._normalized_text(self.package_edit.text()),
            "value": self._normalized_text(self.value_edit.text()),
            "function": self._normalized_text(self.function_edit.text()),
            "active_passive": self.type_combo.currentData(),
            "power_required": self.power_required_check.isChecked(),
            "datasheet_url": self._normalized_text(self.datasheet_edit.text()),
            "product_url": self._normalized_text(self.product_edit.text()),
            "tol_p": self._normalized_text(self.tol_p_edit.text()),
            "tol_n": self._normalized_text(self.tol_n_edit.text()),
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _normalized_text(value: str) -> str | None:
        value = (value or "").strip()
        return value or None

    # ------------------------------------------------------------------
    def _update_dirty_state(self) -> None:
        if self._current_original is None or self._current_part_id is None:
            dirty = False
        else:
            dirty = self._collect_form_data() != self._current_original
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)

    # ------------------------------------------------------------------
    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (
            self.part_number_edit,
            self.description_edit,
            self.package_edit,
            self.value_edit,
            self.function_edit,
            self.type_combo,
            self.power_required_check,
            self.datasheet_edit,
            self.datasheet_open,
            self.product_edit,
            self.product_open,
            self.tol_p_edit,
            self.tol_n_edit,
        ):
            widget.setEnabled(enabled)
        if not enabled:
            self.created_label.setText("–")

    # ------------------------------------------------------------------
    def _clear_form(self) -> None:
        self._updating_form = True
        try:
            self.part_number_edit.clear()
            self.description_edit.clear()
            self.package_edit.clear()
            self.value_edit.clear()
            self.function_edit.clear()
            self.type_combo.setCurrentIndex(0)
            self.power_required_check.setChecked(False)
            self.datasheet_edit.clear()
            self.product_edit.clear()
            self.tol_p_edit.clear()
            self.tol_n_edit.clear()
        finally:
            self._updating_form = False
        self._update_dirty_state()

    # ------------------------------------------------------------------
    def _save_part(self) -> None:
        if not self.save_btn.isEnabled() or self._current_part_id is None:
            return
        data = self._collect_form_data()
        if not data["part_number"]:
            QMessageBox.warning(self, "Save", "Part number is required.")
            return
        try:
            part = self._with_session(
                lambda s: services.update_part(s, self._current_part_id, **data)
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Save", str(exc))
            return
        except Exception as exc:  # pragma: no cover - DB/runtime errors
            QMessageBox.warning(self, "Save", str(exc))
            return
        self._set_status(f"Saved {part.part_number}")
        self.refresh_parts()
        self._current_original = self._collect_form_data()
        self._update_dirty_state()

    # ------------------------------------------------------------------
    def _revert_changes(self) -> None:
        if self._current_part_id is None or self._current_original is None:
            return
        self._load_part(self._current_part_id)

    # ------------------------------------------------------------------
    def _create_part(self) -> None:
        part_number, ok = QInputDialog.getText(self, "New Part", "Part number:")
        if not ok:
            return
        part_number = part_number.strip()
        if not part_number:
            QMessageBox.warning(self, "New Part", "Part number cannot be empty.")
            return
        try:
            part = self._with_session(lambda s: services.create_part(s, part_number=part_number))
        except ValueError as exc:
            QMessageBox.warning(self, "New Part", str(exc))
            return
        except Exception as exc:  # pragma: no cover - DB/runtime errors
            QMessageBox.warning(self, "New Part", str(exc))
            return
        self.search_edit.setText(part.part_number)
        self.refresh_parts()
        self._set_status(f"Created {part.part_number}")

    # ------------------------------------------------------------------
    def _delete_part(self) -> None:
        if self._current_part_id is None:
            return
        try:
            refs = self._with_session(lambda s: services.count_part_references(s, self._current_part_id))
        except Exception as exc:  # pragma: no cover - DB/runtime errors
            QMessageBox.warning(self, "Delete", str(exc))
            return

        if refs:
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Delete Part")
            dialog.setText(f"This PN is referenced by {refs} BOM item(s). What do you want to do?")
            unlink_btn = dialog.addButton(
                "Unlink from BOMs and delete",
                QMessageBox.ButtonRole.AcceptRole,
            )
            dialog.addButton(QMessageBox.StandardButton.Cancel)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.exec()
            if dialog.clickedButton() is not unlink_btn:
                return
            mode = "unlink_then_delete"
        else:
            confirm = QMessageBox.question(
                self,
                "Delete Part",
                "Delete this part?",
                QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            mode = "block"
        try:
            self._with_session(
                lambda s: services.delete_part(s, self._current_part_id, mode=mode)
            )
        except RuntimeError as exc:
            QMessageBox.warning(self, "Delete", str(exc))
            return
        except Exception as exc:  # pragma: no cover - DB/runtime errors
            QMessageBox.warning(self, "Delete", str(exc))
            return
        pn = self.part_number_edit.text() or "Part"
        if mode == "unlink_then_delete":
            self._set_status(f"Deleted {pn} (unlinked {refs} BOM rows)")
        else:
            self._set_status(f"Deleted {pn}")
        self._current_part_id = None
        self.refresh_parts()

    # ------------------------------------------------------------------
    def _open_url(self, line_edit: QLineEdit) -> None:
        text = line_edit.text().strip()
        if not text:
            return
        url = QUrl.fromUserInput(text)
        if not url.isValid():
            QMessageBox.warning(self, "Open URL", "Invalid URL")
            return
        QDesktopServices.openUrl(url)


def main() -> None:  # pragma: no cover - manual entry point
    app = QApplication.instance()
    should_cleanup = False
    if app is None:
        app = QApplication(sys.argv)
        should_cleanup = True
    window = PartsTerminalWindow()
    window.show()
    if should_cleanup:
        sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover - module entry
    main()

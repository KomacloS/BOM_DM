from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .. import state as app_state
from ...services import SchematicFileInfo, SchematicPackInfo, list_schematic_packs


@dataclass(slots=True)
class _AutoContext:
    part_id: Optional[int]
    part_number: str
    reference: str


class SchematicPanel(QWidget):
    """Side panel showing schematic files with simple auto/manual search modes."""

    def __init__(
        self,
        assembly_id: int,
        open_callback: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._assembly_id = assembly_id
        self._open_callback = open_callback
        self._packs: list[SchematicPackInfo] = []
        self._current_pack_id: Optional[int] = None
        self._auto_context = _AutoContext(None, "", "")
        self._suppress_selection_message = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header_row = QHBoxLayout()
        title = QLabel("Schematics")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        header_row.addWidget(title)
        header_row.addStretch(1)
        self.manage_button = QPushButton("Manage…")
        self.manage_button.clicked.connect(self._open_manager)
        header_row.addWidget(self.manage_button)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: self.refresh())
        header_row.addWidget(self.refresh_button)
        layout.addLayout(header_row)

        self.pack_label = QLabel("No schematics available.")
        self.pack_label.setWordWrap(True)
        layout.addWidget(self.pack_label)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self.auto_radio = QRadioButton("Auto")
        self.manual_radio = QRadioButton("Manual")
        self.auto_radio.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.auto_radio)
        mode_group.addButton(self.manual_radio)
        mode_row.addWidget(self.auto_radio)
        mode_row.addWidget(self.manual_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        search_row = QHBoxLayout()
        self.search_mode_combo = QComboBox()
        self.search_mode_combo.addItem("Part Number", "pn")
        self.search_mode_combo.addItem("Reference", "ref")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search schematics…")
        self.search_button = QPushButton("Search")
        search_row.addWidget(self.search_mode_combo)
        search_row.addWidget(self.search_edit)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.files_list = QListWidget()
        self.files_list.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.files_list.currentItemChanged.connect(lambda _curr, _prev: self._update_info_for_selection())
        layout.addWidget(self.files_list)

        action_row = QHBoxLayout()
        self.open_button = QPushButton("Open Selected")
        self.open_button.clicked.connect(self._open_selected)
        action_row.addWidget(self.open_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.info_label = QLabel("Auto mode: select a BOM row to preview schematics.")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.search_button.clicked.connect(self._perform_manual_search)
        self.search_edit.returnPressed.connect(self._perform_manual_search)
        self.auto_radio.toggled.connect(self._on_mode_changed)

        self._update_search_enabled()
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        current = self._selected_file_info()
        current_id = current.id if current else None
        self._load_packs()
        pack = self._current_pack()
        if pack is None:
            self.pack_label.setText("No schematic packs configured. Use Manage to add files.")
            self.files_list.clear()
            self.open_button.setEnabled(False)
            self.info_label.setText("No schematic files attached to this assembly.")
            return

        self.pack_label.setText(
            f"{pack.display_name} — revision {pack.pack_revision}, {len(pack.files)} file"
            f"{'s' if len(pack.files) != 1 else ''}."
        )
        self.files_list.blockSignals(True)
        self.files_list.clear()
        restored_row: Optional[int] = None
        for idx, info in enumerate(pack.files):
            label = f"{info.file_order}. {info.file_name}"
            if info.page_count:
                plural = "s" if info.page_count != 1 else ""
                label += f" ({info.page_count} page{plural})"
            if not info.exists:
                label += " [missing]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, info.id)
            self.files_list.addItem(item)
            if current_id and info.id == current_id:
                restored_row = idx
        self.files_list.blockSignals(False)

        if restored_row is not None:
            self.files_list.setCurrentRow(restored_row)
        elif self.files_list.count() and self.files_list.currentRow() < 0:
            self.files_list.setCurrentRow(0)

        self._update_info_for_selection()
        self._update_search_enabled()
        if self.auto_radio.isChecked():
            self._run_auto_query()

    # ------------------------------------------------------------------
    def set_component_context(self, part_id: Optional[int], part_number: str, reference: str) -> None:
        self._auto_context = _AutoContext(part_id, part_number or "", reference or "")
        if self.auto_radio.isChecked():
            self._run_auto_query()

    def clear_component_context(self) -> None:
        self._auto_context = _AutoContext(None, "", "")
        if self.auto_radio.isChecked():
            self.info_label.setText("Auto mode: select a BOM row to preview schematics.")

    # ------------------------------------------------------------------
    def _load_packs(self) -> None:
        with app_state.get_session() as session:
            self._packs = list_schematic_packs(session, self._assembly_id)
        if not self._packs:
            self._current_pack_id = None
        elif self._current_pack_id not in {p.id for p in self._packs}:
            self._current_pack_id = self._packs[0].id

    def _current_pack(self) -> Optional[SchematicPackInfo]:
        if not self._packs:
            return None
        if self._current_pack_id is None:
            return self._packs[0]
        for pack in self._packs:
            if pack.id == self._current_pack_id:
                return pack
        return self._packs[0]

    def _selected_file_info(self) -> Optional[SchematicFileInfo]:
        item = self.files_list.currentItem()
        if not item:
            return None
        file_id = item.data(Qt.ItemDataRole.UserRole)
        pack = self._current_pack()
        if pack is None:
            return None
        for info in pack.files:
            if info.id == file_id:
                return info
        return None

    def _update_info_for_selection(self) -> None:
        info = self._selected_file_info()
        if info is None:
            self.open_button.setEnabled(False)
            if not self._suppress_selection_message:
                self.info_label.setText("Select a schematic to view it.")
            return
        self.open_button.setEnabled(True)
        if not self._suppress_selection_message:
            message = f"Selected: {info.file_name}"
            if not info.exists:
                message += " (file missing on disk)"
            self.info_label.setText(message)

    def _update_search_enabled(self) -> None:
        manual = self.manual_radio.isChecked()
        self.search_mode_combo.setEnabled(manual)
        self.search_edit.setEnabled(manual)
        self.search_button.setEnabled(manual)
        if not manual and self.auto_radio.isChecked():
            self.info_label.setText("Auto mode: select a BOM row to preview schematics.")

    def _on_mode_changed(self, checked: bool) -> None:
        self._update_search_enabled()
        if checked:
            self._run_auto_query()

    # ------------------------------------------------------------------
    def _perform_manual_search(self) -> None:
        if not self.manual_radio.isChecked():
            return
        query = self.search_edit.text().strip()
        if not query:
            self.info_label.setText("Enter a part number or reference to search.")
            return
        mode_key = self.search_mode_combo.currentData()
        self._highlight_for_query(query, mode_key, reason="manual")

    def _run_auto_query(self) -> None:
        pack = self._current_pack()
        if pack is None or not pack.files:
            self.info_label.setText("No schematic files attached to this assembly.")
            return
        ref = (self._auto_context.reference or "").split(",")[0].strip()
        pn = self._auto_context.part_number.strip()
        if ref:
            self._highlight_for_query(ref, "ref", reason="auto")
        elif pn:
            self._highlight_for_query(pn, "pn", reason="auto")
        else:
            self._suppress_selection_message = True
            self.files_list.setCurrentRow(0)
            self._suppress_selection_message = False
            info = self._selected_file_info()
            if info:
                self._update_info_message(info, "", "pn", matched=False, reason="auto")

    def _highlight_for_query(self, query: str, mode_key: str, reason: str) -> None:
        pack = self._current_pack()
        if pack is None or not pack.files:
            self.info_label.setText("No schematic files attached to this assembly.")
            return
        query_lower = query.lower()
        match_row: Optional[int] = None
        match_info: Optional[SchematicFileInfo] = None
        for idx, info in enumerate(pack.files):
            if query_lower and query_lower in info.file_name.lower():
                match_row = idx
                match_info = info
                break
        if match_row is None:
            # fallback: keep current or first file
            if self.files_list.currentRow() >= 0:
                match_row = self.files_list.currentRow()
                match_info = self._selected_file_info()
            else:
                match_row = 0
                match_info = pack.files[0]
            matched = False
        else:
            matched = True
        if match_row is not None:
            self._suppress_selection_message = True
            self.files_list.setCurrentRow(match_row)
            self._suppress_selection_message = False
            info = match_info or self._selected_file_info()
            if info:
                self._update_info_message(info, query, mode_key, matched=matched, reason=reason)
        else:
            self.info_label.setText("No schematic files attached to this assembly.")

    def _update_info_message(
        self,
        info: SchematicFileInfo,
        query: str,
        mode_key: str,
        *,
        matched: bool,
        reason: str,
    ) -> None:
        mode_label = "part number" if mode_key == "pn" else "reference"
        prefix = "Auto" if reason == "auto" else "Manual"
        if query:
            if matched:
                message = f"{prefix}: matched {mode_label} '{query}' → {info.file_name}"
            else:
                message = (
                    f"{prefix}: no match for {mode_label} '{query}'. "
                    f"Showing {info.file_name}"
                )
        else:
            message = f"{prefix}: showing {info.file_name}"
        if not info.exists:
            message += " (file missing on disk)"
        self.info_label.setText(message)

    # ------------------------------------------------------------------
    def _open_selected(self) -> None:
        info = self._selected_file_info()
        if info is None:
            return
        if not info.exists:
            QMessageBox.warning(self, "Schematics", "The selected PDF file is missing on disk.")
            return
        self._open_callback(str(info.absolute_path))

    def _open_manager(self) -> None:
        from ..dialogs.schematic_manager import SchematicsManagerDialog

        dialog = SchematicsManagerDialog(self._assembly_id, self._open_callback, self)
        dialog.exec()
        self.refresh()


__all__ = ["SchematicPanel"]

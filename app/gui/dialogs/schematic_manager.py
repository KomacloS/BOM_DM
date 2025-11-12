from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .. import state as app_state
from ...services import (
    SchematicFileInfo,
    SchematicPackInfo,
    add_schematic_file_from_path,
    create_schematic_pack,
    list_schematic_packs,
    remove_schematic_file,
    rename_schematic_pack,
    replace_schematic_file_from_path,
)


class SchematicsManagerDialog(QDialog):
    """Dialog for managing schematic PDF files for an assembly."""

    def __init__(
        self,
        assembly_id: int,
        open_callback: Optional[Callable[[str], None]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._assembly_id = assembly_id
        self._open_callback = open_callback
        self._packs: list[SchematicPackInfo] = []
        self._current_pack_id: Optional[int] = None

        self.setWindowTitle("Manage Schematics")
        layout = QVBoxLayout(self)

        header = QLabel(
            "Attach and maintain schematic PDF files for this assembly."
            " Files are stored inside the project data directory."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        pack_row = QHBoxLayout()
        pack_row.addWidget(QLabel("Pack name:"))
        self.pack_name_edit = QLineEdit()
        self.pack_name_edit.setPlaceholderText("e.g. Primary Schematics")
        pack_row.addWidget(self.pack_name_edit)
        self.save_name_btn = QPushButton("Save Name")
        self.save_name_btn.clicked.connect(self._on_save_name)
        pack_row.addWidget(self.save_name_btn)
        layout.addLayout(pack_row)

        self.pack_meta_label = QLabel("")
        self.pack_meta_label.setWordWrap(True)
        layout.addWidget(self.pack_meta_label)

        self.files_list = QListWidget()
        self.files_list.currentItemChanged.connect(self._on_selection_changed)
        self.files_list.itemDoubleClicked.connect(lambda _item: self._on_open_file())
        layout.addWidget(self.files_list)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add PDF…")
        self.add_btn.clicked.connect(self._on_add_file)
        btn_row.addWidget(self.add_btn)
        self.replace_btn = QPushButton("Replace…")
        self.replace_btn.clicked.connect(self._on_replace_file)
        btn_row.addWidget(self.replace_btn)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._on_remove_file)
        btn_row.addWidget(self.remove_btn)
        self.open_btn = QPushButton("Open")
        self.open_btn.clicked.connect(self._on_open_file)
        btn_row.addWidget(self.open_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.path_label = QLabel("Select a file to see its location.")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.pack_name_edit.textChanged.connect(lambda: self._update_buttons())

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self, select_file_id: Optional[int] = None) -> None:
        self._load_packs()
        pack = self._current_pack()
        if pack is None:
            self.pack_meta_label.setText("No schematic pack exists yet. Enter a name and click Save Name to create one.")
            self.files_list.clear()
            self.path_label.setText("Select or add a schematic file.")
            self._update_buttons()
            return

        self.pack_name_edit.blockSignals(True)
        self.pack_name_edit.setText(pack.display_name)
        self.pack_name_edit.blockSignals(False)

        count = len(pack.files)
        meta = f"Revision {pack.pack_revision} — {count} file{'s' if count != 1 else ''}"
        self.pack_meta_label.setText(meta)

        self.files_list.blockSignals(True)
        self.files_list.clear()
        selected_item: Optional[QListWidgetItem] = None
        for info in pack.files:
            label = f"{info.file_order}. {info.file_name}"
            if info.page_count:
                plural = "s" if info.page_count != 1 else ""
                label += f" ({info.page_count} page{plural})"
            if not info.exists:
                label += " [missing]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, info.id)
            self.files_list.addItem(item)
            if select_file_id is not None and info.id == select_file_id:
                selected_item = item
        self.files_list.blockSignals(False)

        if selected_item is not None:
            self.files_list.setCurrentItem(selected_item)
        elif self.files_list.count() and self.files_list.currentRow() < 0:
            self.files_list.setCurrentRow(0)

        self._update_path_label()
        self._update_buttons()

    # ------------------------------------------------------------------
    def _load_packs(self) -> None:
        with app_state.get_session() as session:
            self._packs = list_schematic_packs(session, self._assembly_id)
        available_ids = {p.id for p in self._packs}
        if not available_ids:
            self._current_pack_id = None
        elif self._current_pack_id not in available_ids:
            # Default to first pack
            self._current_pack_id = next(iter(available_ids))

    def _current_pack(self) -> Optional[SchematicPackInfo]:
        if self._current_pack_id is None:
            return self._packs[0] if self._packs else None
        for pack in self._packs:
            if pack.id == self._current_pack_id:
                return pack
        return None

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

    def _update_buttons(self) -> None:
        name_present = bool(self.pack_name_edit.text().strip())
        self.save_name_btn.setEnabled(name_present or self._current_pack() is not None)
        has_file = self._selected_file_info() is not None
        has_pack = self._current_pack() is not None
        self.add_btn.setEnabled(name_present or has_pack)
        self.replace_btn.setEnabled(has_file)
        self.remove_btn.setEnabled(has_file)
        self.open_btn.setEnabled(has_file)

    def _update_path_label(self) -> None:
        info = self._selected_file_info()
        if info is None:
            self.path_label.setText("Select or add a schematic file.")
            return
        text = str(info.absolute_path)
        if not info.exists:
            text += " — file missing"
        self.path_label.setText(text)

    # ------------------------------------------------------------------
    def _ensure_pack(self) -> Optional[int]:
        pack = self._current_pack()
        if pack is not None:
            return pack.id
        name = self.pack_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Schematics", "Enter a pack name before adding files.")
            return None
        try:
            with app_state.get_session() as session:
                created = create_schematic_pack(session, self._assembly_id, name)
        except Exception as exc:
            QMessageBox.warning(self, "Schematics", str(exc))
            return None
        self._current_pack_id = created.id
        self.refresh()
        return created.id

    def _on_save_name(self) -> None:
        name = self.pack_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Schematics", "Pack name cannot be empty.")
            return
        pack = self._current_pack()
        try:
            with app_state.get_session() as session:
                if pack is None:
                    created = create_schematic_pack(session, self._assembly_id, name)
                    self._current_pack_id = created.id
                else:
                    rename_schematic_pack(session, pack.id, name)
        except Exception as exc:
            QMessageBox.warning(self, "Schematics", str(exc))
            return
        self.refresh()

    def _on_add_file(self) -> None:
        pack_id = self._ensure_pack()
        if pack_id is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Select schematic PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        try:
            with app_state.get_session() as session:
                info = add_schematic_file_from_path(session, pack_id, Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Attach failed", str(exc))
            return
        self.refresh(select_file_id=info.id)

    def _on_replace_file(self) -> None:
        info = self._selected_file_info()
        if info is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Select replacement PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        try:
            with app_state.get_session() as session:
                updated = replace_schematic_file_from_path(session, info.id, Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Replace failed", str(exc))
            return
        self.refresh(select_file_id=updated.id)

    def _on_remove_file(self) -> None:
        info = self._selected_file_info()
        if info is None:
            return
        confirm = QMessageBox.question(
            self,
            "Remove schematic",
            f"Remove {info.file_name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            with app_state.get_session() as session:
                remove_schematic_file(session, info.id)
        except Exception as exc:
            QMessageBox.warning(self, "Remove failed", str(exc))
            return
        self.refresh()

    def _on_open_file(self) -> None:
        info = self._selected_file_info()
        if info is None:
            return
        if not info.exists:
            QMessageBox.warning(self, "Open schematic", "The stored file could not be found on disk.")
            return
        path = str(info.absolute_path)
        if self._open_callback:
            self._open_callback(path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_selection_changed(self, *_args) -> None:
        self._update_buttons()
        self._update_path_label()

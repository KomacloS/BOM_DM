from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import select

from app.domain import complex_linker
from app.domain.complex_linker import ComplexLink
from app.integration import ce_bridge_client
from app.integration.ce_bridge_client import (
    CEAuthError,
    CENetworkError,
    CENotFound,
    CEUserCancelled,
)
from app.gui import state as app_state
from app.config import get_complex_editor_settings

logger = logging.getLogger(__name__)


class ComplexPanel(QWidget):
    """Widget for attaching Complex Editor entries to BOM parts."""

    linkUpdated = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._part_id: Optional[int] = None
        self._part_number: str = ""
        self._current_link: Optional[Dict[str, Any]] = None
        self._busy = False
        self._ce_settings = get_complex_editor_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        note = ""
        if isinstance(self._ce_settings, dict):
            note = str(self._ce_settings.get("note_or_link") or "").strip()
        if note:
            note_label = QLabel(note, self)
            note_label.setWordWrap(True)
            note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(note_label)

        self._pn_label = QLabel("Current PN: -", self)
        self._pn_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._pn_label)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Search PN or alias...")
        self.search_button = QPushButton("Search", self)
        self.search_button.clicked.connect(self._on_search_clicked)
        search_row.addWidget(self.search_edit)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.results_list = QListWidget(self)
        self.results_list.itemSelectionChanged.connect(self._update_buttons_state)
        layout.addWidget(self.results_list, stretch=1)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        actions_row = QHBoxLayout()
        self.attach_button = QPushButton("Attach", self)
        self.attach_button.setEnabled(False)
        self.attach_button.clicked.connect(self._on_attach_clicked)
        self.create_button = QPushButton("Create in Complex Editor...", self)
        self.create_button.clicked.connect(self._on_create_clicked)
        actions_row.addWidget(self.attach_button)
        actions_row.addWidget(self.create_button)
        layout.addLayout(actions_row)

        info_layout = QGridLayout()
        label_id = QLabel("Linked CE ID:", self)
        self.linked_id_value = QLabel("-", self)
        self.linked_id_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.setEnabled(False)
        self.refresh_button.clicked.connect(self._on_refresh_clicked)

        label_db = QLabel("DB Path:", self)
        self.db_path_value = QLabel("-", self)
        self.db_path_value.setWordWrap(True)
        self.db_path_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        label_synced = QLabel("Last Synced:", self)
        self.synced_value = QLabel("-", self)
        self.synced_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        info_layout.addWidget(label_id, 0, 0)
        info_layout.addWidget(self.linked_id_value, 0, 1)
        info_layout.addWidget(self.refresh_button, 0, 2)
        info_layout.addWidget(label_db, 1, 0)
        info_layout.addWidget(self.db_path_value, 1, 1, 1, 2)
        info_layout.addWidget(label_synced, 2, 0)
        info_layout.addWidget(self.synced_value, 2, 1, 1, 2)
        layout.addLayout(info_layout)

        layout.addStretch(1)

        self._controls: Iterable[QWidget] = (
            self.search_edit,
            self.search_button,
            self.results_list,
            self.attach_button,
            self.create_button,
            self.refresh_button,
        )

        self.setEnabled(False)

    # ------------------------------------------------------------------
    def set_context(self, part_id: Optional[int], pn: Optional[str]) -> None:
        if part_id is None:
            self.setEnabled(False)
            self._part_id = None
            self._part_number = ""
            self._pn_label.setText("Current PN: -")
            self._current_link = None
            self._update_link_display()
            self.results_list.clear()
            self.attach_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.status_label.clear()
            self._update_buttons_state()
            return

        if part_id == self._part_id and pn == self._part_number:
            # No change
            return

        self.setEnabled(True)
        self._part_id = part_id
        self._part_number = pn or ""
        self._pn_label.setText(f"Current PN: {self._part_number or '-'}")
        self.search_edit.setText(self._part_number)
        self.results_list.clear()
        self.status_label.clear()
        self.attach_button.setEnabled(False)

        link = self._load_link_snapshot(part_id)
        self._current_link = link
        self._update_link_display()
        self._update_buttons_state()
    def _update_link_display(self) -> None:
        link = self._current_link or {}
        ce_id = link.get("ce_complex_id") or "-"
        db_path = link.get("ce_db_uri") or "-"
        synced = link.get("synced_at") or "-"
        self.linked_id_value.setText(str(ce_id))
        self.db_path_value.setText(str(db_path))
        self.synced_value.setText(str(synced))
        self.refresh_button.setEnabled(self._part_id is not None and ce_id not in ("", "-"))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        for widget in self._controls:
            widget.setEnabled(not busy)
        if not busy:
            self._update_buttons_state()
        QApplication.processEvents()

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)
        self.status_label.setText(message)

    def _load_link_snapshot(self, part_id: int) -> Optional[Dict[str, Any]]:
        try:
            with app_state.get_session() as session:
                result = session.exec(
                    select(ComplexLink).where(ComplexLink.part_id == part_id)
                ).first()
                if result is None:
                    return None
                return result.dict()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load complex link for part %s: %s", part_id, exc)
            return None

    def _on_search_clicked(self) -> None:
        if self._part_id is None:
            return
        query = (self.search_edit.text() or self._part_number or "").strip()
        if not query:
            self._show_error("Search", "Enter a part number or alias to search.")
            return
        self._set_busy(True)
        try:
            results = ce_bridge_client.search_complexes(query, limit=20)
        except CEAuthError as exc:
            self._show_error("Authentication", str(exc))
            results = []
        except CENetworkError as exc:
            self._show_error("Network", str(exc))
            results = []
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Search failed", str(exc))
            results = []
        finally:
            self._set_busy(False)
        self.results_list.clear()
        for row in results:
            if not isinstance(row, dict):
                continue
            pn = row.get("pn") or row.get("part_number") or ""
            ce_id = row.get("id") or row.get("ce_id") or "?"
            aliases = ", ".join(a for a in (row.get("aliases") or []) if isinstance(a, str))
            db_path = row.get("db_path") or row.get("ce_db_uri") or ""
            text_parts = [f"{pn}".strip(), f"(ID: {ce_id})"]
            if aliases:
                text_parts.append(f"aliases: {aliases}")
            if db_path:
                text_parts.append(f"db: {db_path}")
            item = QListWidgetItem(" | ".join(text_parts), self.results_list)
            item.setData(Qt.ItemDataRole.UserRole, row)
        if self.results_list.count():
            self.results_list.setCurrentRow(0)
        self._update_buttons_state()
        self.status_label.setText(f"Found {self.results_list.count()} result(s).")

    def _selected_result(self) -> Optional[Dict[str, Any]]:
        item = self.results_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _update_buttons_state(self) -> None:
        has_selection = self._selected_result() is not None
        self.attach_button.setEnabled(bool(has_selection) and not self._busy)

    def _on_attach_clicked(self) -> None:
        if self._part_id is None:
            return
        selected = self._selected_result()
        if not selected:
            return
        ce_id = selected.get("id") or selected.get("ce_id")
        if not ce_id:
            self._show_error("Attach", "Selected entry is missing an ID.")
            return
        self._set_busy(True)
        try:
            complex_linker.attach_existing_complex(self._part_id, str(ce_id))
        except CEAuthError as exc:
            self._show_error("Authentication", str(exc))
        except CENotFound as exc:
            self._show_error("Complex not found", str(exc))
        except CENetworkError as exc:
            self._show_error("Network", str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Attach failed", str(exc))
        else:
            self.status_label.setText(f"Attached Complex {ce_id} to part {self._part_id}.")
            self._current_link = self._load_link_snapshot(self._part_id)
            self._update_link_display()
            self.linkUpdated.emit(self._part_id)
        finally:
            self._set_busy(False)

    def _on_create_clicked(self) -> None:
        if self._part_id is None:
            return
        if not self._part_number:
            self._show_error("Create", "Part number is required to create a complex.")
            return
        aliases = None
        text = (self.search_edit.text() or "").strip()
        if text and text != self._part_number:
            aliases = [text]
        self._set_busy(True)
        try:
            complex_linker.create_and_attach_complex(self._part_id, self._part_number, aliases)
        except CEUserCancelled as exc:
            self.status_label.setText(str(exc))
        except CEAuthError as exc:
            self._show_error("Authentication", str(exc))
        except CENetworkError as exc:
            self._show_error("Network", str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Create failed", str(exc))
        else:
            self.status_label.setText(f"Created new Complex for part {self._part_number}.")
            if self._part_id is not None:
                self._current_link = self._load_link_snapshot(self._part_id)
                self._update_link_display()
                self.linkUpdated.emit(self._part_id)
        finally:
            self._set_busy(False)

    def _on_refresh_clicked(self) -> None:
        if self._part_id is None or not self._current_link:
            return
        ce_id = self._current_link.get("ce_complex_id")
        if not ce_id:
            self._show_error("Refresh", "No linked Complex to refresh.")
            return
        self._set_busy(True)
        try:
            complex_linker.attach_existing_complex(self._part_id, str(ce_id))
        except (CEAuthError, CENetworkError, CENotFound) as exc:
            self._show_error("Refresh failed", str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Refresh failed", str(exc))
        else:
            self.status_label.setText("Snapshot refreshed from Complex Editor.")
            self._current_link = self._load_link_snapshot(self._part_id)
            self._update_link_display()
            self.linkUpdated.emit(self._part_id)
        finally:
            self._set_busy(False)


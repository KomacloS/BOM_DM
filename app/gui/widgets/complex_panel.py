from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import select

from app.domain import complex_linker
from app.domain.complex_linker import CESelectionRequired, ComplexLink
from app.integration import ce_bridge_client
from app.integration import ce_bridge_manager
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
        self.unlink_button = QPushButton("Unlink", self)
        self.unlink_button.setVisible(False)
        self.unlink_button.clicked.connect(self._on_unlink_clicked)
        actions_row.addWidget(self.attach_button)
        actions_row.addWidget(self.create_button)
        actions_row.addWidget(self.unlink_button)
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
        info_layout.addWidget(self.db_path_value, 1, 1)

        self.open_button = QPushButton("Open in CE", self)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._on_open_ce_clicked)
        info_layout.addWidget(self.open_button, 1, 2)

        info_layout.addWidget(label_synced, 2, 0)
        info_layout.addWidget(self.synced_value, 2, 1)
        info_layout.setColumnStretch(1, 1)
        layout.addLayout(info_layout)

        self.link_warning = QLabel("", self)
        self.link_warning.setWordWrap(True)
        self.link_warning.setVisible(False)
        self.link_warning.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.link_warning.setOpenExternalLinks(False)
        self.link_warning.linkActivated.connect(self._on_link_warning_activated)
        layout.addWidget(self.link_warning)

        layout.addStretch(1)

        self._controls: Iterable[QWidget] = (
            self.search_edit,
            self.search_button,
            self.results_list,
            self.attach_button,
            self.create_button,
            self.refresh_button,
            self.open_button,
            self.unlink_button,
        )

        self.unlink_shortcut = QShortcut(QKeySequence("Ctrl+U"), self)
        self.unlink_shortcut.activated.connect(self._on_unlink_shortcut)
        self._link_status_reason: str = ""

        self.setEnabled(False)

    # ------------------------------------------------------------------
    def set_context(self, part_id: Optional[int], pn: Optional[str]) -> None:
        if part_id is None:
            self.setEnabled(False)
            self._part_id = None
            self._part_number = ""
            self._pn_label.setText("Current PN: -")
            self._clear_link_ui()
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
        self._refresh_link_status()

    def _update_link_display(self) -> None:
        link = self._current_link or {}
        ce_id = link.get("ce_complex_id") or "-"
        db_path = link.get("ce_db_uri") or "-"
        synced = link.get("synced_at") or "-"
        self.linked_id_value.setText(str(ce_id))
        self.db_path_value.setText(str(db_path))
        self.synced_value.setText(str(synced))
        has_link = self._part_id is not None and ce_id not in ("", "-")
        self.refresh_button.setEnabled(has_link)
        self.open_button.setEnabled(has_link and self._link_status_reason == "")
        self.unlink_button.setVisible(has_link)
        self.unlink_button.setEnabled(has_link and not self._busy)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        for widget in self._controls:
            widget.setEnabled(not busy)
        if not busy:
            self._update_buttons_state()
        QApplication.processEvents()

    def _show_error(self, title: str, message: str, details: Optional[str] = None) -> None:
        diag = ce_bridge_manager.get_last_ce_bridge_diagnostics()
        if details is None and diag is not None:
            details = diag.to_text()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        box.addButton(QMessageBox.StandardButton.Ok)
        details_button = None
        if details:
            details_button = box.addButton("View details…", QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if details_button and box.clickedButton() is details_button and details:
            self._show_details_dialog(details)
        self.status_label.setText(message)

    def _show_details_dialog(self, details: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Complex Editor Diagnostics")
        layout = QVBoxLayout(dialog)
        viewer = QPlainTextEdit(dialog)
        viewer.setReadOnly(True)
        viewer.setPlainText(details)
        layout.addWidget(viewer)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.resize(720, 480)
        dialog.exec()

    def _format_result_summary(self, row: Dict[str, Any]) -> str:
        pn = str(row.get("pn") or row.get("part_number") or "").strip()
        ce_id = row.get("id") or row.get("ce_id") or "?"
        aliases = ", ".join(a for a in (row.get("aliases") or []) if isinstance(a, str))
        db_path = row.get("db_path") or row.get("ce_db_uri") or ""
        parts = [pn or "<unknown>", f"(ID: {ce_id})"]
        if aliases:
            parts.append(f"aliases: {aliases}")
        if db_path:
            parts.append(f"db: {db_path}")
        return " | ".join(parts)

    def _format_link_message(self, link: Dict[str, Any]) -> str:
        ce_id = str(link.get("ce_complex_id") or "?")
        db_path = link.get("ce_db_uri") or "Complex Editor"
        return f"Linked to CE #{ce_id} from {db_path}"

    def _prompt_select_complex(self, matches: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        options = [m for m in matches if isinstance(m, dict)]
        if not options:
            return None
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Complex Editor Entry")
        layout = QVBoxLayout(dialog)
        list_widget = QListWidget(dialog)
        for match in options:
            item = QListWidgetItem(self._format_result_summary(match), list_widget)
            item.setData(Qt.ItemDataRole.UserRole, match)
        list_widget.itemDoubleClicked.connect(lambda *_args: dialog.accept())
        layout.addWidget(list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def _update_ok_state() -> None:
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(list_widget.currentItem() is not None)

        list_widget.currentItemChanged.connect(lambda *_args: _update_ok_state())
        _update_ok_state()
        dialog.resize(480, 320)
        result = dialog.exec()
        if result != QDialog.DialogCode.Accepted:
            return None
        item = list_widget.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _on_open_ce_clicked(self) -> None:
        self._set_busy(True)
        try:
            ce_bridge_manager.ensure_ce_bridge_ready(require_ui=True)
        except ce_bridge_manager.CEBridgeError as exc:
            self._show_error("Complex Editor", str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Complex Editor", str(exc))
        else:
            self.status_label.setText("Complex Editor UI opened.")
        finally:
            self._set_busy(False)

    def _on_link_warning_activated(self, target: str) -> None:
        if target == "cleanup":
            self._perform_unlink(confirm=False, user_initiated=True)
        elif target == "retry":
            self._refresh_link_status(show_errors=True)

    def _on_unlink_shortcut(self) -> None:
        if self.unlink_button.isVisible() and self.unlink_button.isEnabled():
            self._on_unlink_clicked()

    def _perform_unlink(self, *, confirm: bool, user_initiated: bool) -> None:
        if self._part_id is None:
            return
        if confirm:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle("Unlink Complex Editor")
            dialog.setText(
                "Remove link to Complex Editor? This only unlinks in BOM_DB; the complex in CE is untouched."
            )
            unlink_button = dialog.addButton("Unlink", QMessageBox.ButtonRole.AcceptRole)
            dialog.addButton(QMessageBox.StandardButton.Cancel)
            dialog.exec()
            if dialog.clickedButton() is not unlink_button:
                return
        try:
            self._set_busy(True)
            removed = complex_linker.unlink_existing_complex(self._part_id, user_initiated=user_initiated)
        finally:
            self._set_busy(False)
        if removed:
            self._clear_link_ui()
            self.status_label.setText("Unlinked from CE. Not linked. You can Attach or Create.")
            self.linkUpdated.emit(self._part_id)

    def _clear_link_ui(self) -> None:
        self._current_link = None
        self.linked_id_value.setText("-")
        self.db_path_value.setText("-")
        self.synced_value.setText("-")
        self.open_button.setEnabled(False)
        self.unlink_button.setVisible(False)
        self.unlink_button.setEnabled(False)
        self.link_warning.clear()
        self.link_warning.setVisible(False)
        self._link_status_reason = ""
        self._update_link_display()
        self._update_buttons_state()

    def _on_unlink_clicked(self) -> None:
        self._perform_unlink(confirm=True, user_initiated=True)

    def _refresh_link_status(self, show_errors: bool = False) -> None:
        self.link_warning.clear()
        self.link_warning.setVisible(False)
        if not self._current_link or not self._current_link.get("ce_complex_id"):
            self._link_status_reason = ""
            self._update_link_display()
            return
        try:
            stale, reason = complex_linker.check_link_stale(ce_bridge_client, self._current_link)
        except CEAuthError as exc:
            if show_errors:
                self._show_error("Authentication", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            if show_errors:
                self._show_error("Complex Editor", str(exc))
            return
        self._link_status_reason = reason if stale or reason else ""
        if stale and reason == "not_found":
            self.link_warning.setText(
                '<a href="cleanup">Linked CE record no longer exists. Clean up link</a>'
            )
            self.link_warning.setVisible(True)
        elif reason == "transient":
            self.link_warning.setText(
                '<a href="retry">Couldn’t verify CE link (network). Retry</a>'
            )
            self.link_warning.setVisible(True)
        elif reason == "auth":
            if show_errors:
                self._show_error(
                    "Authentication", "Invalid/expired CE bridge token; update the token in settings."
                )
        self.open_button.setEnabled(self._link_status_reason == "")
        self.unlink_button.setEnabled(self.unlink_button.isVisible() and not self._busy)

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
            item = QListWidgetItem(self._format_result_summary(row), self.results_list)
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
        self.create_button.setEnabled(not self._busy)
        has_link = self.unlink_button.isVisible()
        self.unlink_button.setEnabled(has_link and not self._busy)
        self.open_button.setEnabled(has_link and not self._busy and self._link_status_reason == "")

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
        record: Optional[Dict[str, Any]] = None
        try:
            record = complex_linker.attach_existing_complex(self._part_id, str(ce_id))
        except CEAuthError as exc:
            self._show_error("Authentication", str(exc))
        except CENotFound as exc:
            self._show_error("Complex not found", str(exc))
        except CENetworkError as exc:
            self._show_error("Network", str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Attach failed", str(exc))
        finally:
            self._set_busy(False)

        if record is None:
            return
        if self._part_id is not None:
            self._current_link = self._load_link_snapshot(self._part_id)
            link_data = self._current_link or record
            self._update_link_display()
            self.status_label.setText(self._format_link_message(link_data))
            self.linkUpdated.emit(self._part_id)

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
        record: Optional[Dict[str, Any]] = None
        try:
            def _status_update(message: str) -> None:
                self.status_label.setText(message)

            record = complex_linker.create_and_attach_complex(
                self._part_id,
                self._part_number,
                aliases,
                status_callback=_status_update,
            )
        except CESelectionRequired as exc:
            self._set_busy(False)
            selection = self._prompt_select_complex(exc.matches)
            if not selection:
                self.status_label.setText("Creation cancelled")
                return
            ce_id = selection.get("id") or selection.get("ce_id")
            if not ce_id:
                self.status_label.setText("Creation cancelled")
                return
            self._set_busy(True)
            try:
                record = complex_linker.attach_existing_complex(self._part_id, str(ce_id))
            except CEAuthError as err:
                self._show_error("Authentication", str(err))
                return
            except CENotFound as err:
                self._show_error("Complex not found", str(err))
                return
            except CENetworkError as err:
                self._show_error("Network", str(err))
                return
            except Exception as err:  # pragma: no cover - defensive
                self._show_error("Create failed", str(err))
                return
        except CEUserCancelled as exc:
            self.status_label.setText("Creation cancelled")
            return
        except CEAuthError as exc:
            self._show_error("Authentication", str(exc))
            return
        except CENetworkError as exc:
            self._show_error("Network", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Create failed", str(exc))
            return
        finally:
            self._set_busy(False)

        if record is None:
            return
        if self._part_id is not None:
            self._current_link = self._load_link_snapshot(self._part_id)
            link_data = self._current_link or record
            self._update_link_display()
            self.status_label.setText(self._format_link_message(link_data))
            self.linkUpdated.emit(self._part_id)

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


from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, Optional, Sequence

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
from app.domain.complex_linker import AliasConflictError, CESelectionRequired, ComplexLink
from app.integration import ce_bridge_client, ce_supervisor
from app.integration.ce_bridge_client import CEAuthError, CENetworkError, CENotFound, CEUserCancelled
from app.integration.ce_bridge_linker import (
    LinkerDecision,
    LinkerError,
    LinkerFeatureError,
    LinkerInputError,
    select_best_match,
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
        self._link_status_reason: str = ""
        self._alias_prompt_ce_id: Optional[str] = None
        self._cached_results: list[dict[str, Any]] = []
        self._last_decision: Optional[LinkerDecision] = None
        self._last_open_ce_id: Optional[str] = None
        self._last_open_time: float = 0.0
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
        self.attach_button = QPushButton("Link", self)
        self.attach_button.setEnabled(False)
        self.attach_button.clicked.connect(self._on_attach_clicked)
        self.add_link_button = QPushButton("Add & Link", self)
        self.add_link_button.setEnabled(False)
        self.add_link_button.clicked.connect(self._on_add_and_link_clicked)
        self.create_button = QPushButton("Create in Complex Editor...", self)
        self.create_button.clicked.connect(self._on_create_clicked)
        self.unlink_button = QPushButton("Unlink", self)
        self.unlink_button.setVisible(False)
        self.unlink_button.clicked.connect(self._on_unlink_clicked)
        actions_row.addWidget(self.attach_button)
        actions_row.addWidget(self.add_link_button)
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

        self.alias_prompt = QWidget(self)
        alias_prompt_layout = QHBoxLayout(self.alias_prompt)
        alias_prompt_layout.setContentsMargins(0, 0, 0, 0)
        self.alias_prompt_label = QLabel("", self.alias_prompt)
        self.alias_prompt_label.setWordWrap(True)
        alias_prompt_layout.addWidget(self.alias_prompt_label, 1)
        self.alias_prompt_add_button = QPushButton("Add & Link", self.alias_prompt)
        self.alias_prompt_add_button.clicked.connect(self._on_alias_prompt_add_clicked)
        alias_prompt_layout.addWidget(self.alias_prompt_add_button)
        self.alias_prompt_link_button = QPushButton("Just Link", self.alias_prompt)
        self.alias_prompt_link_button.clicked.connect(self._on_alias_prompt_link_clicked)
        alias_prompt_layout.addWidget(self.alias_prompt_link_button)
        self.alias_prompt.setVisible(False)
        layout.addWidget(self.alias_prompt)

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
            self.add_link_button,
            self.create_button,
            self.refresh_button,
            self.open_button,
            self.unlink_button,
            self.alias_prompt_add_button,
            self.alias_prompt_link_button,
        )

        self.unlink_shortcut = QShortcut(QKeySequence("Ctrl+U"), self)
        self.unlink_shortcut.activated.connect(self._on_unlink_shortcut)

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
            self.status_label.clear()
            self._cached_results.clear()
            self._update_buttons_state()
            return

        if part_id == self._part_id and pn == self._part_number:
            return

        self.setEnabled(True)
        self._part_id = part_id
        self._part_number = pn or ""
        self._pn_label.setText(f"Current PN: {self._part_number or '-'}")
        self.search_edit.setText(self._part_number)
        self.results_list.clear()
        self.status_label.clear()

        self._current_link = self._load_link_snapshot(part_id)
        self._update_link_display()
        self._refresh_link_status()
        self._update_buttons_state()

    # ------------------------------------------------------------------
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
        if not has_link:
            self.alias_prompt.setVisible(False)
            self._alias_prompt_ce_id = None

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        for widget in self._controls:
            widget.setEnabled(not busy)
        if not busy:
            self._update_buttons_state()
        QApplication.processEvents()

    def _show_error(self, title: str, message: str, details: Optional[str] = None) -> None:
        diag = ce_supervisor.get_last_ce_bridge_diagnostics()
        if details is None and diag is not None:
            details = diag.to_text()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        box.addButton(QMessageBox.StandardButton.Ok)
        details_button = None
        if details:
            details_button = box.addButton("View details...", QMessageBox.ButtonRole.ActionRole)
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

    def _format_result_summary(self, row: Dict[str, Any], *, highlight: bool = False) -> str:
        pn = str(row.get("pn") or row.get("part_number") or "").strip()
        ce_id = row.get("id") or row.get("ce_id") or "?"
        aliases = ", ".join(alias for alias in (row.get("aliases") or []) if isinstance(alias, str))
        db_path = row.get("db_path") or row.get("ce_db_uri") or ""
        match_kind = str(row.get("match_kind") or "").strip() or "unknown"
        reason = str(row.get("reason") or "").strip()
        normalized_input = str(row.get("normalized_input") or "").strip()
        normalized_targets = row.get("normalized_targets") or []
        prefix = "★ " if highlight else ""
        parts = [f"{prefix}{pn or '<unknown>'}", f"(ID: {ce_id})", f"match: {match_kind}"]
        if reason:
            parts.append(reason)
        if aliases:
            parts.append(f"aliases: {aliases}")
        if db_path:
            parts.append(f"db: {db_path}")
        if normalized_input:
            parts.append(f"norm: {normalized_input}")
        if isinstance(normalized_targets, Sequence) and normalized_targets:
            parts.append(f"targets: {self._format_normalized_targets(normalized_targets)}")
        return " | ".join(parts)

    def _format_link_message(self, link: Dict[str, Any]) -> str:
        ce_id = str(link.get("ce_complex_id") or "?")
        db_path = link.get("ce_db_uri") or "Complex Editor"
        return f"Linked to CE #{ce_id} from {db_path}"

    def _maybe_show_alias_prompt(self, results: Iterable[Dict[str, Any]]) -> None:
        self.alias_prompt.setVisible(False)
        self._alias_prompt_ce_id = None
        if not self._part_number:
            return
        items = [row for row in results if isinstance(row, dict)]
        if len(items) != 1:
            return
        data = items[0]
        ce_id = data.get("id") or data.get("ce_id")
        if not ce_id:
            return
        part_lower = self._part_number.strip().lower()
        pn_lower = str(data.get("pn") or "").strip().lower()
        aliases = [
            str(alias).strip().lower() for alias in (data.get("aliases") or []) if isinstance(alias, str)
        ]
        if not part_lower or part_lower == pn_lower or part_lower not in aliases:
            return
        self.alias_prompt_label.setText(
            f"Add {self._part_number} as alias and link to CE #{ce_id}?"
        )
        self._alias_prompt_ce_id = str(ce_id)
        self.alias_prompt.setVisible(True)

    def _choose_complex_for_open(
        self, matches: Iterable[Dict[str, Any]]
    ) -> tuple[Optional[Dict[str, Any]], bool]:
        items = [row for row in matches if isinstance(row, dict)]
        self._cached_results = items
        if not items:
            self._show_error("Open in CE", "No Complex Editor entry matches this part number.")
            return None, False

        if len(items) == 1:
            selection = items[0]
        else:
            selection = self._prompt_select_complex(items)
            if not selection:
                return None, False

        ce_id = selection.get("id") or selection.get("ce_id")
        if not ce_id:
            self._show_error("Open in CE", "Selected entry is missing an ID.")
            return None, False
        ce_id_str = str(ce_id)

        linked_ce_id = str(self._current_link.get("ce_complex_id")) if self._current_link else None
        allow_attach = linked_ce_id != ce_id_str

        attach_first = False
        if allow_attach:
            pn = (self._part_number or "").strip()
            part_lower = pn.lower()
            selection_pn = str(selection.get("pn") or "").strip().lower()
            aliases_lower = [
                str(alias).strip().lower() for alias in (selection.get("aliases") or []) if isinstance(alias, str)
            ]
            alias_only = (
                bool(part_lower)
                and part_lower != selection_pn
                and part_lower in aliases_lower
            )
            choice = self._prompt_open_choice(ce_id_str, allow_attach=True, default_attach=alias_only)
            if choice == "cancel":
                return None, False
            attach_first = choice == "add"

        return selection, attach_first

    def _prompt_open_choice(self, ce_id: str, *, allow_attach: bool, default_attach: bool) -> str:
        if not allow_attach:
            return "open"
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Open in Complex Editor")
        dialog.setText(f"Open Complex Editor record #{ce_id}?")
        open_button = dialog.addButton("Open", QMessageBox.ButtonRole.AcceptRole)
        attach_button = dialog.addButton("Add & Open", QMessageBox.ButtonRole.ActionRole)
        cancel_button = dialog.addButton(QMessageBox.StandardButton.Cancel)
        if default_attach:
            dialog.setDefaultButton(attach_button)
        else:
            dialog.setDefaultButton(open_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is attach_button:
            return "add"
        if clicked is open_button:
            return "open"
        return "cancel"


    def _on_search_clicked(self) -> None:
        if self._part_id is None:
            return
        query = (self.search_edit.text() or self._part_number or "").strip()
        if not query:
            self._show_error("Search", "Enter a part number or alias to search.")
            return
        self._set_busy(True)
        try:
            decision = select_best_match(query, limit=50)
        except LinkerInputError as exc:
            message = str(exc) or "Invalid part number."
            self._handle_search_failure("Invalid part number", message)
            return
        except LinkerFeatureError as exc:
            message = str(exc) or "Complex Editor linker feature unavailable."
            self._handle_search_failure("Bridge", message)
            return
        except LinkerError as exc:
            self._handle_search_failure("Search failed", str(exc))
            return
        except CEAuthError as exc:
            self._handle_search_failure("Authentication", str(exc))
            return
        except CENetworkError as exc:
            self._handle_search_failure("Network", str(exc))
            return
        except Exception as exc:
            self._handle_search_failure("Search failed", str(exc))
            return
        finally:
            self._set_busy(False)
        self._populate_search_results(decision)

    def _handle_search_failure(self, title: str, message: str) -> None:
        self._show_error(title, message)
        self.results_list.clear()
        self._cached_results = []
        self._last_decision = None
        self._maybe_show_alias_prompt(self._cached_results)
        self._update_buttons_state()
        self.status_label.setText(message)

    def _populate_search_results(self, decision: LinkerDecision) -> None:
        self._last_decision = decision
        self.results_list.clear()
        self._cached_results = []
        best_id = decision.best.id if decision.best else None
        preferred_item: Optional[QListWidgetItem] = None
        for candidate in decision.results:
            data = candidate.to_dict()
            self._cached_results.append(data)
            item = QListWidgetItem(
                self._format_result_summary(data, highlight=best_id is not None and data.get("id") == best_id),
                self.results_list,
            )
            item.setData(Qt.ItemDataRole.UserRole, data)
            if best_id and data.get("id") == best_id:
                preferred_item = item
        if preferred_item is not None:
            self.results_list.setCurrentItem(preferred_item)
        elif self.results_list.count():
            self.results_list.setCurrentRow(0)
        self._maybe_show_alias_prompt(self._cached_results)
        self._update_buttons_state()
        self.status_label.setText(self._compose_status_message(decision))

    def _compose_status_message(self, decision: LinkerDecision) -> str:
        parts: list[str] = [f"{len(decision.results)} result(s)", f"trace {decision.trace_id}"]
        if decision.needs_review:
            parts.append("needs review")
        best = decision.best
        if best:
            summary = f"best #{best.id} {best.pn}".strip()
            if best.match_kind:
                summary = f"{summary} [{best.match_kind}]"
            if best.reason:
                summary = f"{summary} - {best.reason}"
            parts.append(summary)
            if best.normalized_input:
                parts.append(f"norm input: {best.normalized_input}")
            if best.normalized_targets:
                parts.append(f"targets: {self._format_normalized_targets(best.normalized_targets)}")
        elif decision.normalized_input:
            parts.append(f"norm input: {decision.normalized_input}")
        return " | ".join(parts)

    def _format_normalized_targets(self, targets: Sequence[str]) -> str:
        values = [str(target) for target in targets if isinstance(target, str)]
        if len(values) > 5:
            return ", ".join(values[:5]) + ", …"
        return ", ".join(values)

    def _selected_result(self) -> Optional[Dict[str, Any]]:
        item = self.results_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _selected_ce_id(self) -> Optional[str]:
        selected = self._selected_result()
        if not selected:
            return None
        ce_id = selected.get("id") or selected.get("ce_id")
        return str(ce_id) if ce_id else None

    def _update_buttons_state(self) -> None:
        has_selection = self._selected_result() is not None
        selectable = bool(has_selection) and not self._busy
        self.attach_button.setEnabled(selectable)
        self.add_link_button.setEnabled(selectable and bool(self._part_number))
        has_link = self.unlink_button.isVisible()
        self.unlink_button.setEnabled(has_link and not self._busy)
        self.open_button.setEnabled(has_link and not self._busy and self._link_status_reason == "")
        if self.alias_prompt.isVisible():
            enabled = not self._busy
            self.alias_prompt_add_button.setEnabled(enabled)
            self.alias_prompt_link_button.setEnabled(enabled)

    def _apply_link_record(self, record: Optional[Dict[str, Any]]) -> None:
        if record is None or self._part_id is None:
            return
        self._current_link = self._load_link_snapshot(self._part_id)
        link_data = self._current_link or record
        self._link_status_reason = ""
        self.alias_prompt.setVisible(False)
        self._alias_prompt_ce_id = None
        self._update_link_display()
        self._refresh_link_status()
        self.status_label.setText(self._format_link_message(link_data))
        self.linkUpdated.emit(self._part_id)

    def _on_attach_clicked(self) -> None:
        ce_id = self._selected_ce_id()
        if ce_id is None:
            self._show_error("Link", "Select a Complex Editor entry first.")
            return
        self._link_to_ce(ce_id)

    def _on_add_and_link_clicked(self) -> None:
        ce_id = self._selected_ce_id()
        if ce_id is None:
            self._show_error("Add & Link", "Select a Complex Editor entry first.")
            return
        self._on_add_and_link_clicked_inner(ce_id)

    def _on_add_and_link_clicked_inner(self, ce_id: str | int) -> None:
        if self._part_id is None:
            return

        def _status_update(message: str) -> None:
            self.status_label.setText(message)

        try:
            self._set_busy(True)
            record = complex_linker.attach_as_alias_and_link(
                self._part_id,
                self._part_number,
                ce_id,
                status_callback=_status_update,
            )
        except AliasConflictError as exc:
            self._set_busy(False)
            self._show_alias_conflict_dialog(exc)
            return
        except CEAuthError as exc:
            self._set_busy(False)
            self._show_error("Authentication", str(exc))
            return
        except CENetworkError as exc:
            self._set_busy(False)
            self._show_error("Network", str(exc))
            return
        except Exception as exc:
            self._set_busy(False)
            self._show_error("Add & Link failed", str(exc))
            return
        else:
            self._set_busy(False)
            self._apply_link_record(record)

    def _on_alias_prompt_add_clicked(self) -> None:
        if self._alias_prompt_ce_id is not None:
            self._on_add_and_link_clicked_inner(self._alias_prompt_ce_id)

    def _on_alias_prompt_link_clicked(self) -> None:
        if self._alias_prompt_ce_id is not None:
            self._link_to_ce(self._alias_prompt_ce_id)

    def _show_alias_conflict_dialog(self, error: AliasConflictError) -> None:
        conflicts = ", ".join(error.conflicts) if error.conflicts else ""
        message = f"{self._part_number} already belongs to CE #{error.ce_id}."
        if conflicts:
            message = f"{message} Conflicts: {conflicts}"
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Alias conflict")
        dialog.setText(message)
        link_button = dialog.addButton(f"Link to CE #{error.ce_id}", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is link_button:
            self._link_to_ce(error.ce_id)

    def _link_to_ce(self, ce_id: str | int) -> None:
        if self._part_id is None:
            return
        record: Optional[Dict[str, Any]] = None
        try:
            self._set_busy(True)
            record = complex_linker.attach_existing_complex(self._part_id, str(ce_id))
        except CEAuthError as exc:
            self._show_error("Authentication", str(exc))
        except CENotFound as exc:
            self._show_error("Complex not found", str(exc))
        except CENetworkError as exc:
            self._show_error("Network", str(exc))
        except Exception as exc:
            self._show_error("Link failed", str(exc))
        finally:
            self._set_busy(False)
        self._apply_link_record(record)

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

        def _status_update(message: str) -> None:
            self.status_label.setText(message)

        record: Optional[Dict[str, Any]] = None
        try:
            self._set_busy(True)
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
            self._link_to_ce(ce_id)
            return
        except CEUserCancelled:
            self._set_busy(False)
            self.status_label.setText("Creation cancelled")
            return
        except LinkerInputError as exc:
            self._set_busy(False)
            self._show_error("Invalid part number", str(exc))
            return
        except LinkerFeatureError as exc:
            self._set_busy(False)
            self._show_error("Bridge", str(exc))
            return
        except LinkerError as exc:
            self._set_busy(False)
            self._show_error("Create failed", str(exc))
            return
        except CEAuthError as exc:
            self._set_busy(False)
            self._show_error("Authentication", str(exc))
            return
        except CENetworkError as exc:
            self._set_busy(False)
            self._show_error("Network", str(exc))
            return
        except Exception as exc:
            self._set_busy(False)
            self._show_error("Create failed", str(exc))
            return
        else:
            self._set_busy(False)
            self._apply_link_record(record)


    def _on_open_ce_clicked(self) -> None:
        if self._part_id is None:
            return
        if self._busy:
            return
        self._set_busy(True)
        status_messages: list[str] = []

        def _status_update(message: str) -> None:
            status_messages.append(message)
            display = message[:-2] if message.endswith("E|") else message
            if display:
                self.status_label.setText(display)

        context = {
            "link": self._current_link or None,
            "pn": self._part_number,
            "part_id": self._part_id,
        }
        use_cached = (
            self._last_open_ce_id is not None
            and (time.monotonic() - self._last_open_time) < 5.0
        )
        linked_ce_id = None
        if self._current_link and self._current_link.get("ce_complex_id"):
            linked_ce_id = str(self._current_link.get("ce_complex_id"))
        if use_cached and linked_ce_id and linked_ce_id != self._last_open_ce_id:
            use_cached = False

        try:
            self.status_label.setText("Starting Complex Editor (running diagnostics)...")
            result = complex_linker.open_in_ce(
                context,
                status_callback=_status_update,
                chooser=self._choose_complex_for_open,
                use_cached_preflight=use_cached,
            )
        except CEUserCancelled:
            if status_messages:
                final = status_messages[-1]
                self.status_label.setText(final[:-2] if final.endswith("E|") else final)
            else:
                self.status_label.setText("Open in CE cancelled.")
        except complex_linker.CEStaleLinkError:
            self.link_warning.setText(
                '<a href="cleanup">Linked CE record no longer exists. Clean up link</a>'
            )
            self.link_warning.setVisible(True)
        except complex_linker.CEBusyEditorError:
            self.link_warning.setText("Complex Editor is busy; finish current dialog and try again.")
            self.link_warning.setVisible(True)
        except LinkerInputError as exc:
            self._show_error("Invalid part number", str(exc))
        except LinkerFeatureError as exc:
            self._show_error("Bridge", str(exc))
        except LinkerError as exc:
            self._show_error("Search failed", str(exc))
        except CEAuthError as exc:
            self._show_error("Authentication", str(exc))
        except CENetworkError as exc:
            self._show_error("Network", str(exc))
        except CENotFound as exc:
            self._show_error("Open in CE", str(exc))
        except Exception as exc:
            self._show_error("Open in CE failed", str(exc))
        else:
            if result.link_record:
                self._apply_link_record(result.link_record)
            if result.already_open:
                self.status_label.setText("Already open in Complex Editor.")
            else:
                self.status_label.setText("Opened in Complex Editor.")
            self._last_open_ce_id = result.ce_id
            self._last_open_time = time.monotonic()
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
        self.alias_prompt.setVisible(False)
        self._alias_prompt_ce_id = None
        self.link_warning.clear()
        self.link_warning.setVisible(False)
        self._link_status_reason = ""
        self._update_buttons_state()

    def _on_unlink_clicked(self) -> None:
        self._perform_unlink(confirm=True, user_initiated=True)

    def _refresh_link_status(self, show_errors: bool = False) -> None:
        self.link_warning.clear()
        self.link_warning.setVisible(False)
        if not self._current_link or not self._current_link.get("ce_complex_id"):
            self._link_status_reason = ""
            self._update_buttons_state()
            return
        try:
            stale, reason = complex_linker.check_link_stale(ce_bridge_client, self._current_link)
        except CEAuthError as exc:
            if show_errors:
                self._show_error("Authentication", str(exc))
            return
        except Exception as exc:
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
                "<a href=\"retry\">Couldn't verify CE link (network). Retry</a>"
            )
            self.link_warning.setVisible(True)
        elif reason == "auth" and show_errors:
            self._show_error(
                "Authentication", "Invalid/expired CE bridge token; update the token in settings."
            )
        self.open_button.setEnabled(self._link_status_reason == "")
        self._update_buttons_state()

    def _load_link_snapshot(self, part_id: int) -> Optional[Dict[str, Any]]:
        try:
            with app_state.get_session() as session:
                result = session.exec(
                    select(ComplexLink).where(ComplexLink.part_id == part_id)
                ).first()
                if result is None:
                    return None
                return result.dict()
        except Exception as exc:
            logger.warning("Failed to load complex link for part %s: %s", part_id, exc)
            return None

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
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def _update_ok_state() -> None:
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
                list_widget.currentItem() is not None
            )

        list_widget.currentItemChanged.connect(lambda *_args: _update_ok_state())
        _update_ok_state()
        dialog.resize(480, 320)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        item = list_widget.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _on_refresh_clicked(self) -> None:
        if self._part_id is None or not self._current_link:
            return
        ce_id = self._current_link.get("ce_complex_id")
        if not ce_id:
            self._show_error("Refresh", "No linked Complex to refresh.")
            return
        record: Optional[Dict[str, Any]] = None
        try:
            self._set_busy(True)
            record = complex_linker.attach_existing_complex(self._part_id, str(ce_id))
        except (CEAuthError, CENetworkError, CENotFound) as exc:
            self._show_error("Refresh failed", str(exc))
        except Exception as exc:
            self._show_error("Refresh failed", str(exc))
        else:
            self.status_label.setText("Snapshot refreshed from Complex Editor.")
        finally:
            self._set_busy(False)
        self._apply_link_record(record)

from __future__ import annotations

import logging
import time
from contextlib import closing
from typing import Any, Dict, Iterable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
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

from app.config import get_complex_editor_settings
from app.domain import complex_creation, complex_linker
from app.domain.complex_linker import CEWizardLaunchError, ComplexLink
from app.integration import ce_bridge_client, ce_bridge_linker
from app.integration.ce_bridge_client import CEAuthError, CENetworkError, CENotFound
from app.integration.ce_bridge_linker import (
    LinkerError,
    LinkerFeatureError,
    LinkerInputError,
)
from app.gui import state as app_state

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
        self._creation_poll_timer: Optional[QTimer] = None
        self._creation_poll_deadline: float = 0.0
        self._creation_poller: Optional[complex_creation.WizardPoller] = None
        self._wizard_buffer_path: Optional[str] = None

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

    def _creation_timeout_seconds(self) -> float:
        if isinstance(self._ce_settings, dict):
            raw = self._ce_settings.get("create_wait_timeout_seconds")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = None
            if value and value > 0:
                return value
        return 240.0

    def _stop_creation_poll(self, *, keep_retry: bool = False) -> None:
        if self._creation_poll_timer is not None:
            self._creation_poll_timer.stop()
            self._creation_poll_timer.deleteLater()
            self._creation_poll_timer = None
        self._creation_poll_deadline = 0.0
        self._creation_poller = None
        self.progress.setVisible(False)
        self._set_refresh_label(keep_retry)

    def _start_creation_poll(self, outcome: complex_linker.CreateComplexOutcome) -> None:
        self._stop_creation_poll()
        if not outcome.polling_enabled or self._part_id is None or not self._part_number:
            self.progress.setVisible(False)
            self._set_refresh_label(True)
            return

        self._set_refresh_label(True)
        self._creation_poller = complex_creation.WizardPoller(
            self._part_id,
            self._part_number,
            limit=5,
            attach=complex_linker.attach_existing_complex,
        )
        timeout = max(1.0, self._creation_timeout_seconds())
        self._creation_poll_deadline = time.monotonic() + timeout
        self._creation_poll_timer = QTimer(self)
        self._creation_poll_timer.setInterval(1_000)
        self._creation_poll_timer.timeout.connect(self._poll_for_created_complex)
        self._creation_poll_timer.start()
        self.progress.setVisible(True)
        # Kick off an immediate poll so the user gets quick feedback
        self._poll_for_created_complex()

    def _poll_for_created_complex(self) -> None:
        if self._part_id is None or not self._part_number:
            self._stop_creation_poll()
            return
        if self._creation_poll_deadline and time.monotonic() > self._creation_poll_deadline:
            self._stop_creation_poll(keep_retry=True)
            self.status_label.setText("Still waiting for save in CE…")
            return
        poller = self._creation_poller
        if poller is None:
            self._stop_creation_poll()
            return
        try:
            result = poller.poll_once()
        except CEAuthError as exc:
            logger.info("Complex Editor polling auth failure: %s", exc)
            self._stop_creation_poll(keep_retry=True)
            self.status_label.setText("Still waiting for save in CE…")
            return
        except CENetworkError as exc:
            logger.info("Complex Editor polling network failure: %s", exc)
            self._stop_creation_poll(keep_retry=True)
            self.status_label.setText("Still waiting for save in CE…")
            return
        if result.attached and self._part_id is not None:
            self._stop_creation_poll()
            self._current_link = self._load_link_snapshot(self._part_id)
            self._update_link_display()
            self.linkUpdated.emit(self._part_id)
            ce_id = self._current_link.get("ce_complex_id") if self._current_link else ""
            complex_creation.cleanup_buffer(self._wizard_buffer_path)
            self._wizard_buffer_path = None
            self.status_label.setText(
                f"Complex saved in Complex Editor and attached (ID: {ce_id})."
            )


    # ------------------------------------------------------------------
    def set_context(self, part_id: Optional[int], pn: Optional[str]) -> None:
        if part_id is None:
            self.setEnabled(False)
            self._part_id = None
            self._part_number = ""
            self._stop_creation_poll()
            self._set_refresh_label(False)
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
        self._stop_creation_poll()
        self._set_refresh_label(False)
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
        self.refresh_button.setEnabled(
            self._part_id is not None and bool(self._part_number)
        )

    def _maybe_show_preflight_message(self) -> None:
        if not ce_bridge_client.is_preflight_recent():
            self.status_label.setText(
                "Complex Editor is starting (running diagnostics)…"
            )
            QApplication.processEvents()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        for widget in self._controls:
            widget.setEnabled(not busy)
        if not busy:
            self._update_buttons_state()
        QApplication.processEvents()

    def _set_refresh_label(self, waiting: bool) -> None:
        text = "Retry" if waiting else "Refresh"
        if self.refresh_button.text() != text:
            self.refresh_button.setText(text)

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)
        self.status_label.setText(message)

    def _show_launch_error(self, exc: CEWizardLaunchError) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Complex Editor")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(str(exc))
        fix_button = None
        if exc.fix_in_settings:
            fix_button = box.addButton("Fix in Settings", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        self.status_label.setText(str(exc))
        if fix_button is not None and box.clickedButton() == fix_button:
            self._open_settings_dialog()

    def _open_settings_dialog(self) -> None:
        from app.gui.dialogs.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self)
        dialog.exec()
        self._ce_settings = get_complex_editor_settings()

    def _load_link_snapshot(self, part_id: int) -> Optional[Dict[str, Any]]:
        try:
            with closing(app_state.get_session()) as session:
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
        self._maybe_show_preflight_message()
        self._set_busy(True)
        try:
            decision = ce_bridge_linker.select_best_match(query, limit=50)
        except LinkerInputError as exc:
            self._show_error("Invalid part number", str(exc))
            self.status_label.setText(str(exc))
            self.results_list.clear()
            self._update_buttons_state()
            return
        except LinkerFeatureError as exc:
            self._show_error("Bridge", str(exc))
            self.status_label.setText(str(exc))
            self.results_list.clear()
            self._update_buttons_state()
            return
        except LinkerError as exc:
            self._show_error("Search failed", str(exc))
            self.status_label.setText(str(exc))
            self.results_list.clear()
            self._update_buttons_state()
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Search failed", str(exc))
            self.status_label.setText(str(exc))
            self.results_list.clear()
            self._update_buttons_state()
            return
        finally:
            self._set_busy(False)

        self.results_list.clear()
        best_id = decision.best.id if decision.best else None
        best_row_index: Optional[int] = None
        for row in decision.results:
            pn = row.get("pn") or row.get("part_number") or ""
            ce_id = str(row.get("id") or row.get("ce_id") or row.get("comp_id") or "?")
            aliases = ", ".join(
                a.strip()
                for a in (row.get("aliases") or [])
                if isinstance(a, str) and a.strip()
            )
            db_path = row.get("db_path") or row.get("ce_db_uri") or ""
            match_kind = str(row.get("match_kind") or "").strip()
            reason = str(row.get("reason") or "").strip()
            analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
            normalized_input = str(
                analysis.get("normalized_input")
                or row.get("normalized_input")
                or ""
            ).strip()
            normalized_targets = [
                t.strip()
                for t in analysis.get("normalized_targets") or []
                if isinstance(t, str) and t.strip()
            ]
            if not normalized_targets:
                normalized_targets = [
                    t.strip()
                    for t in row.get("normalized_targets") or []
                    if isinstance(t, str) and t.strip()
                ]

            text_parts = [f"{pn}".strip(), f"(ID: {ce_id})"]
            if aliases:
                text_parts.append(f"aliases: {aliases}")
            if db_path:
                text_parts.append(f"db: {db_path}")
            if match_kind:
                text_parts.append(f"match: {match_kind}")
            if reason:
                text_parts.append(reason)
            if normalized_input:
                text_parts.append(f"norm in: {normalized_input}")
            if normalized_targets:
                text_parts.append(
                    "norm targets: " + ", ".join(normalized_targets[:5])
                    + ("…" if len(normalized_targets) > 5 else "")
                )

            item = QListWidgetItem(" | ".join(text_parts), self.results_list)
            item.setData(Qt.ItemDataRole.UserRole, row)
            if best_id and ce_id == best_id and best_row_index is None:
                best_row_index = self.results_list.count() - 1

        if best_row_index is not None:
            self.results_list.setCurrentRow(best_row_index)
        elif self.results_list.count():
            self.results_list.setCurrentRow(0)

        self._update_buttons_state()

        status_parts: list[str] = []
        count = self.results_list.count()
        status_parts.append(f"Found {count} result(s)")
        status_parts.append(f"Trace: {decision.trace_id}")
        if decision.needs_review:
            status_parts.append("needs review: multiple matches share rank")
        if decision.best:
            best = decision.best
            summary = f"Best: {best.pn or best.id} [{best.match_kind}]"
            if best.reason:
                summary += f" – {best.reason}"
            status_parts.append(summary)
            if best.normalized_input:
                status_parts.append(f"normalized input: {best.normalized_input}")
            if best.normalized_targets:
                status_parts.append(
                    "targets: "
                    + ", ".join(best.normalized_targets[:5])
                    + ("…" if len(best.normalized_targets) > 5 else "")
                )
        self.status_label.setText(" | ".join(status_parts))

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
        self._maybe_show_preflight_message()
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
        if self._creation_poll_timer is not None:
            self.status_label.setText(
                "Wizard already open / polling in progress."
            )
            return
        if not self._part_number:
            self._show_error("Create", "Part number is required to create a complex.")
            return
        self._maybe_show_preflight_message()
        aliases = None
        text = (self.search_edit.text() or "").strip()
        if text and text != self._part_number:
            aliases = [text]
        self._set_busy(True)
        outcome: Optional[complex_linker.CreateComplexOutcome] = None
        try:
            outcome = complex_linker.create_and_attach_complex(
                self._part_id, self._part_number, aliases
            )
        except CEAuthError:
            base_url = ce_bridge_client.get_active_base_url()
            self._show_error(
                "Authentication",
                f"Authentication to Complex Editor failed.\nBridge URL: {base_url}",
            )
        except CEWizardLaunchError as exc:
            self._set_busy(False)
            self._show_launch_error(exc)
            return
        except CENetworkError as exc:
            self._show_error("Network", str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Create failed", str(exc))
        else:
            if outcome is None:
                self.status_label.setText("Complex Editor did not return a result.")
            elif outcome.status == "attached":
                self.progress.setVisible(False)
                self.status_label.setText(outcome.message)
                if self._part_id is not None:
                    self._current_link = self._load_link_snapshot(self._part_id)
                    self._update_link_display()
                    self.linkUpdated.emit(self._part_id)
            elif outcome.status == "wizard":
                self._wizard_buffer_path = outcome.buffer_path
                self.status_label.setText(outcome.message)
                self.progress.setVisible(True)
                self.progress.setRange(0, 0)
                self._start_creation_poll(outcome)
            elif outcome.status == "cancelled":
                self.status_label.setText(outcome.message)
        finally:
            self._set_busy(False)
            if outcome and outcome.status == "wizard":
                if not outcome.polling_enabled:
                    self.progress.setVisible(False)
                    self._set_refresh_label(True)

    def _on_refresh_clicked(self) -> None:
        if self._part_id is None or not self._part_number:
            self._show_error("Refresh", "Part number is required to refresh.")
            return
        self._maybe_show_preflight_message()
        self._set_busy(True)
        try:
            attached = complex_linker.auto_link_by_pn(
                self._part_id, self._part_number, limit=3
            )
        except (CEAuthError, CENetworkError, CENotFound) as exc:
            self._show_error("Refresh failed", str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error("Refresh failed", str(exc))
        else:
            if attached and self._part_id is not None:
                self.status_label.setText(
                    "Complex saved in Complex Editor and attached."
                )
                self._current_link = self._load_link_snapshot(self._part_id)
                self._update_link_display()
                self.linkUpdated.emit(self._part_id)
                self._set_refresh_label(False)
                complex_creation.cleanup_buffer(self._wizard_buffer_path)
                self._wizard_buffer_path = None
            else:
                self.status_label.setText("Still waiting for save in CE…")
                self._set_refresh_label(True)
        finally:
            self._set_busy(False)


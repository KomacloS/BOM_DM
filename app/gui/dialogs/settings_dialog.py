from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import tempfile

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from PyQt6.QtGui import QGuiApplication
from sqlalchemy.engine import make_url

from ... import config
from ...ai_agents import apply_env_from_agents
from ...database import ensure_schema
from ...integration import ce_bridge_client
from ...integration.ce_bridge_diagnostics import CEBridgeDiagnostics
from ...integration.ce_bridge_manager import (
    ensure_ce_bridge_ready,
    get_last_ce_bridge_diagnostics,
)


class SettingsDialog(QDialog):
    """Dialog allowing the user to adjust portable path settings."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self._changes_applied = False
        self._initial_database_url = config.DATABASE_URL
        self._last_saved_database_url: str | None = None

        layout = QVBoxLayout(self)

        info = QLabel(
            "Configure where the application stores its data. "
            "Paths are saved to settings.toml next to the executable when running a packaged build."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.data_root_edit = QLineEdit(str(config.DATA_ROOT))
        self.datasheets_edit = QLineEdit(str(config.DATASHEETS_DIR))
        self.db_edit = QLineEdit(self._display_database_value())
        self.agents_edit = QLineEdit(str(config.get_agents_file_path()))

        self.data_root_browse = QPushButton("Browse...")
        self.data_root_browse.clicked.connect(self._browse_data_root)
        data_row = QHBoxLayout()
        data_row.addWidget(self.data_root_edit)
        data_row.addWidget(self.data_root_browse)
        form.addRow("Data root", data_row)

        self.datasheets_browse = QPushButton("Browse...")
        self.datasheets_browse.clicked.connect(self._browse_datasheets)
        sheet_row = QHBoxLayout()
        sheet_row.addWidget(self.datasheets_edit)
        sheet_row.addWidget(self.datasheets_browse)
        form.addRow("Datasheets", sheet_row)

        self.db_browse = QPushButton("Browse...")
        self.db_browse.clicked.connect(self._browse_database)
        db_row = QHBoxLayout()
        db_row.addWidget(self.db_edit)
        db_row.addWidget(self.db_browse)
        form.addRow("Database", db_row)

        self.agents_browse = QPushButton("Browse...")
        self.agents_browse.clicked.connect(self._browse_agents)
        agents_row = QHBoxLayout()
        agents_row.addWidget(self.agents_edit)
        agents_row.addWidget(self.agents_browse)
        form.addRow("agents.local.toml", agents_row)

        layout.addLayout(form)

        ce_settings = config.get_complex_editor_settings()
        ce_bridge = ce_settings.get("bridge", {}) if isinstance(ce_settings, dict) else {}

        ce_group = QGroupBox("Complex Editor")
        ce_form = QFormLayout()

        self.ce_ui_enabled_check = QCheckBox("Enable Complex Editor UI")
        self.ce_ui_enabled_check.setChecked(bool(ce_settings.get("ui_enabled", True)))
        ce_form.addRow("UI Enabled", self.ce_ui_enabled_check)

        self.ce_exe_edit = QLineEdit(str(ce_settings.get("exe_path") or ""))
        self.ce_exe_browse = QPushButton("Browse...")
        self.ce_exe_browse.clicked.connect(self._browse_ce_exe)
        exe_row = QHBoxLayout()
        exe_row.addWidget(self.ce_exe_edit)
        exe_row.addWidget(self.ce_exe_browse)
        ce_form.addRow("Executable", exe_row)

        self.ce_config_edit = QLineEdit(str(ce_settings.get("config_path") or ""))
        self.ce_config_browse = QPushButton("Browse...")
        self.ce_config_browse.clicked.connect(self._browse_ce_config)
        cfg_row = QHBoxLayout()
        cfg_row.addWidget(self.ce_config_edit)
        cfg_row.addWidget(self.ce_config_browse)
        ce_form.addRow("Config File", cfg_row)

        self.ce_note_edit = QLineEdit(str(ce_settings.get("note_or_link") or ""))
        ce_form.addRow("Note/Link", self.ce_note_edit)

        self.ce_bridge_enabled_check = QCheckBox("Enable HTTP Bridge")
        self.ce_bridge_enabled_check.setChecked(bool(ce_bridge.get("enabled", True)))
        ce_form.addRow("Bridge Enabled", self.ce_bridge_enabled_check)

        self.ce_auto_start_check = QCheckBox("Auto-start bridge when local")
        self.ce_auto_start_check.setChecked(bool(ce_settings.get("auto_start_bridge", True)))
        ce_form.addRow("Auto Start", self.ce_auto_start_check)

        self.ce_auto_stop_check = QCheckBox("Stop bridge on exit if auto-started")
        self.ce_auto_stop_check.setChecked(bool(ce_settings.get("auto_stop_bridge_on_exit", False)))
        ce_form.addRow("Auto Stop", self.ce_auto_stop_check)

        self.ce_base_url_edit = QLineEdit(str(ce_bridge.get("base_url") or "http://127.0.0.1:8765"))
        ce_form.addRow("Base URL", self.ce_base_url_edit)

        self.ce_token_edit = QLineEdit(str(ce_bridge.get("auth_token") or ""))
        self.ce_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        ce_form.addRow("Auth Token", self.ce_token_edit)

        self.ce_timeout_spin = QSpinBox()
        self.ce_timeout_spin.setRange(1, 120)
        timeout_val = ce_bridge.get("request_timeout_seconds")
        try:
            timeout_int = int(timeout_val) if timeout_val is not None else 10
        except (TypeError, ValueError):
            timeout_int = 10
        if timeout_int <= 0:
            timeout_int = 10
        self.ce_timeout_spin.setValue(timeout_int)
        ce_form.addRow("Request Timeout (s)", self.ce_timeout_spin)

        self.ce_test_button = QPushButton("Test Bridge")
        self.ce_test_button.clicked.connect(self._test_ce_bridge)
        ce_form.addRow("", self.ce_test_button)

        ce_group.setLayout(ce_form)
        layout.addWidget(ce_group)

        helper_row = QHBoxLayout()
        helper = QPushButton("Use data root for all")
        helper.clicked.connect(self._apply_portable_defaults)
        helper_row.addWidget(helper)
        helper_row.addStretch(1)
        layout.addLayout(helper_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def changes_applied(self) -> bool:
        return self._changes_applied

    def database_changed(self) -> bool:
        if not self._changes_applied:
            return False
        if self._last_saved_database_url is None:
            return False
        return self._last_saved_database_url != self._initial_database_url

    # ------------------------------------------------------------------
    def _display_database_value(self) -> str:
        path = self._sqlite_path_from_url(config.DATABASE_URL)
        return str(path) if path else config.DATABASE_URL

    @staticmethod
    def _sqlite_path_from_url(url: str) -> Path | None:
        try:
            parsed = make_url(url)
        except Exception:
            return None
        if parsed.get_backend_name() != "sqlite":
            return None
        database = parsed.database or ""
        if not database:
            return None
        return Path(database)

    @staticmethod
    def _sqlite_url_from_path(path: Path) -> str:
        return "sqlite:///" + path.resolve().as_posix()

    # ------------------------------------------------------------------
    def _browse_data_root(self) -> None:
        start = self.data_root_edit.text() or str(config.DATA_ROOT)
        directory = QFileDialog.getExistingDirectory(self, "Select data root", start)
        if directory:
            self.data_root_edit.setText(directory)

    def _browse_datasheets(self) -> None:
        start = self.datasheets_edit.text() or str(config.DATASHEETS_DIR)
        directory = QFileDialog.getExistingDirectory(self, "Select datasheets folder", start)
        if directory:
            self.datasheets_edit.setText(directory)

    def _browse_database(self) -> None:
        start = self.db_edit.text() or str(config.DATA_ROOT)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select database file",
            start,
            "SQLite DB (*.db);;All Files (*)",
        )
        if path:
            self.db_edit.setText(path)

    def _browse_agents(self) -> None:
        start = self.agents_edit.text() or str(config.DATA_ROOT)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select agents.local.toml",
            start,
            "TOML Files (*.toml);;All Files (*)",
        )
        if path:
            self.agents_edit.setText(path)

    def _browse_ce_exe(self) -> None:
        start = self.ce_exe_edit.text().strip() or str(config.DATA_ROOT)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Complex Editor executable",
            start,
            "Executables (*.exe);;All Files (*)" if sys.platform.startswith('win') else "All Files (*)",
        )
        if path:
            self.ce_exe_edit.setText(path)

    def _browse_ce_config(self) -> None:
        start = self.ce_config_edit.text().strip() or str(config.DATA_ROOT)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Complex Editor config",
            start,
            "Config Files (*.json *.yaml *.yml *.toml);;All Files (*)",
        )
        if path:
            self.ce_config_edit.setText(path)

    # ------------------------------------------------------------------
    def _collect_ce_settings(self) -> dict[str, object]:
        base_url = self.ce_base_url_edit.text().strip() or "http://127.0.0.1:8765"
        token = self.ce_token_edit.text().strip()
        return {
            "exe_path": self.ce_exe_edit.text().strip(),
            "config_path": self.ce_config_edit.text().strip(),
            "auto_start_bridge": self.ce_auto_start_check.isChecked(),
            "auto_stop_bridge_on_exit": self.ce_auto_stop_check.isChecked(),
            "bridge_enabled": self.ce_bridge_enabled_check.isChecked(),
            "bridge_base_url": base_url,
            "bridge_auth_token": token,
            "bridge_request_timeout_seconds": int(self.ce_timeout_spin.value()),
            "note_or_link": self.ce_note_edit.text().strip(),
            "ui_enabled": self.ce_ui_enabled_check.isChecked(),
        }

    def _test_ce_bridge(self) -> None:
        ce_values = self._collect_ce_settings()
        try:
            config.save_complex_editor_settings(
                exe_path=ce_values["exe_path"],
                config_path=ce_values["config_path"],
                auto_start_bridge=ce_values["auto_start_bridge"],
                auto_stop_bridge_on_exit=ce_values["auto_stop_bridge_on_exit"],
                bridge_enabled=ce_values["bridge_enabled"],
                bridge_base_url=ce_values["bridge_base_url"],
                bridge_auth_token=ce_values["bridge_auth_token"],
                bridge_request_timeout_seconds=ce_values["bridge_request_timeout_seconds"],
                note_or_link=ce_values["note_or_link"],
                ui_enabled=ce_values["ui_enabled"],
            )
        except Exception as exc:
            QMessageBox.critical(self, "Complex Editor", f"Failed to save settings: {exc}")
            return
        try:
            ensure_ce_bridge_ready()
            payload = ce_bridge_client.healthcheck()
        except Exception as exc:
            diagnostics = getattr(exc, "diagnostics", None)
            if diagnostics is None:
                diagnostics = get_last_ce_bridge_diagnostics()
            if diagnostics is None:
                diagnostics = self._build_bridge_diagnostics_fallback(exc)
            self._show_bridge_failure_dialog(exc, diagnostics)
            return
        status = payload.get("status") if isinstance(payload, dict) else payload
        QMessageBox.information(self, "Complex Editor", f"Bridge OK: {status}")

    def _build_bridge_diagnostics_fallback(self, exc: Exception) -> CEBridgeDiagnostics:
        diagnostics = CEBridgeDiagnostics()
        diagnostics.finalize("error", str(exc))
        diagnostics.attach_traceback(exc)
        return diagnostics

    def _show_bridge_failure_dialog(self, exc: Exception, diagnostics: CEBridgeDiagnostics) -> None:
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Icon.Warning)
        message_box.setWindowTitle("Complex Editor")
        message_box.setText(f"Bridge test failed: {exc}")
        details_button = message_box.addButton("Show Details...", QMessageBox.ButtonRole.ActionRole)
        message_box.addButton(QMessageBox.StandardButton.Ok)
        message_box.exec()
        if message_box.clickedButton() == details_button:
            self._show_bridge_diagnostics_dialog(diagnostics)

    def _show_bridge_diagnostics_dialog(self, diagnostics: CEBridgeDiagnostics) -> None:
        report = diagnostics.to_text()
        dialog = QDialog(self)
        dialog.setWindowTitle("Bridge Diagnostics")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        text_area = QPlainTextEdit(dialog)
        text_area.setPlainText(report)
        text_area.setReadOnly(True)
        text_area.setMinimumSize(640, 480)
        layout.addWidget(text_area)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        copy_button = QPushButton("Copy to Clipboard", dialog)
        save_button = QPushButton("Save Report...", dialog)
        button_box.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        button_box.addButton(save_button, QDialogButtonBox.ButtonRole.ActionRole)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        def _copy_report() -> None:
            QGuiApplication.clipboard().setText(text_area.toPlainText())

        def _save_report() -> None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            target = Path(tempfile.gettempdir()) / f"BOM_DB_CE_Diagnostics_{timestamp}.txt"
            target.write_text(text_area.toPlainText(), encoding="utf-8")
            QMessageBox.information(dialog, "Bridge Diagnostics", f"Report saved to: {target}")

        copy_button.clicked.connect(_copy_report)
        save_button.clicked.connect(_save_report)

        dialog.exec()

    def _apply_portable_defaults(self) -> None:
        data_root = Path(self.data_root_edit.text().strip() or str(config.DATA_ROOT)).expanduser().resolve()
        self.datasheets_edit.setText(str(data_root / "datasheets"))
        self.db_edit.setText(str(data_root / "app.db"))
        self.agents_edit.setText(str(data_root / "agents.local.toml"))

    # ------------------------------------------------------------------
    def accept(self) -> None:
        if self._write_settings():
            self._changes_applied = True
            super().accept()

    def _write_settings(self) -> bool:
        data_root_text = self.data_root_edit.text().strip()
        datasheets_text = self.datasheets_edit.text().strip()
        db_text = self.db_edit.text().strip()
        agents_text = self.agents_edit.text().strip()

        if not data_root_text or not datasheets_text or not db_text or not agents_text:
            QMessageBox.warning(self, "Settings", "All paths are required.")
            return False

        data_root = Path(data_root_text).expanduser().resolve()
        datasheets_dir = Path(datasheets_text).expanduser().resolve()
        agents_path = Path(agents_text).expanduser().resolve()

        db_url = self._normalize_database_value(db_text)
        if db_url is None:
            QMessageBox.warning(self, "Settings", "Database path must be a valid file path or URL.")
            return False

        old_agents = config.get_agents_file_path()

        try:
            data_root.mkdir(parents=True, exist_ok=True)
            datasheets_dir.mkdir(parents=True, exist_ok=True)
            agents_path.parent.mkdir(parents=True, exist_ok=True)
            sqlite_path = self._sqlite_path_from_url(db_url)
            if sqlite_path is not None:
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Settings", f"Failed to create directories: {exc}")
            return False

        try:
            config.save_paths_config(data_root=data_root, datasheets_dir=datasheets_dir, agents_file=agents_path)
            config.save_database_url(db_url)
            ce_values = self._collect_ce_settings()
            config.save_complex_editor_settings(
                exe_path=ce_values["exe_path"],
                config_path=ce_values["config_path"],
                auto_start_bridge=ce_values["auto_start_bridge"],
                auto_stop_bridge_on_exit=ce_values["auto_stop_bridge_on_exit"],
                bridge_enabled=ce_values["bridge_enabled"],
                bridge_base_url=ce_values["bridge_base_url"],
                bridge_auth_token=ce_values["bridge_auth_token"],
                bridge_request_timeout_seconds=ce_values["bridge_request_timeout_seconds"],
                note_or_link=ce_values["note_or_link"],
                ui_enabled=ce_values["ui_enabled"],
            )
        except Exception as exc:
            QMessageBox.critical(self, "Settings", f"Failed to write settings: {exc}")
            return False

        self._maybe_copy_agents_file(old_agents, agents_path)

        try:
            ensure_schema()
        except Exception as exc:
            QMessageBox.warning(self, "Settings", f"Database schema check failed: {exc}")

        apply_env_from_agents()
        self._last_saved_database_url = db_url
        return True

    def _maybe_copy_agents_file(self, old_path: Path, new_path: Path) -> None:
        if new_path == old_path:
            return
        if new_path.exists():
            return
        try:
            if old_path.exists():
                shutil.copy(old_path, new_path)
                return
            example = config.REPO_ROOT / "agents.example.toml"
            if example.exists():
                shutil.copy(example, new_path)
            else:
                new_path.touch(exist_ok=True)
        except Exception:
            # Non-fatal: surface in UI via message box later if needed.
            pass

    def _normalize_database_value(self, value: str) -> str | None:
        text = value.strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered.startswith("sqlite://"):
            return text
        if "://" in text:
            return text
        return self._sqlite_url_from_path(Path(text).expanduser())

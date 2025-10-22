from __future__ import annotations

import datetime
import json
import shutil
import uuid
import sys
import tempfile
import traceback
from pathlib import Path
from urllib.parse import urljoin

from PyQt6.QtGui import QGuiApplication
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
from sqlalchemy.engine import make_url

from ... import config
from ...ai_agents import apply_env_from_agents
from ...database import ensure_schema
from ...integration import ce_bridge_linker
from ...integration.ce_bridge_linker import LinkerError, LinkerFeatureError


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
        self.ce_norm_button = QPushButton("Normalization Rules…")
        self.ce_norm_button.clicked.connect(self._show_normalization_rules)

        button_row = QHBoxLayout()
        button_row.addWidget(self.ce_test_button)
        button_row.addWidget(self.ce_norm_button)
        button_row.addStretch(1)
        ce_form.addRow("", button_row)

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
            from ...integration.ce_supervisor import ensure_ready
            from ...integration import ce_bridge_client, ce_bridge_transport

            ensure_ready()
            base_url, token, timeout = ce_bridge_client.resolve_bridge_connection()
            trace_id = uuid.uuid4().hex
            session = ce_bridge_transport.get_session()
            headers = ce_bridge_transport.build_headers(token, trace_id=trace_id)
            try:
                request_timeout = float(timeout or 10)
            except (TypeError, ValueError):
                request_timeout = 10.0
            health_url = urljoin(base_url.rstrip("/") + "/", "admin/health")
            response = session.get(
                health_url,
                headers=headers,
                timeout=request_timeout,
            )
            response.raise_for_status()
            if response.content:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
            else:
                payload = {}
        except Exception as exc:
            diagnostics_text = self._diagnostics_text_from_exception(exc)
            hint = ""
            diagnostics = getattr(exc, "diagnostics", None)
            if diagnostics is not None:
                outcome = getattr(diagnostics, "outcome", "")
                headless = getattr(diagnostics, "headless", None)
                allow_headless = getattr(diagnostics, "allow_headless", None)
                if outcome == "timeout" and headless and allow_headless is False:
                    hint = (
                        "\n\nWe tried to launch Complex Editor automatically; "
                        "if you prefer to stay headless, set CE_ALLOW_HEADLESS_EXPORTS=1."
                    )
            message = QMessageBox(
                QMessageBox.Icon.Warning,
                "Complex Editor",
                f"Bridge test failed: {exc}{hint}",
                parent=self,
            )
            details_button = None
            if diagnostics_text:
                details_button = message.addButton("Show Details…", QMessageBox.ButtonRole.ActionRole)
            message.addButton(QMessageBox.StandardButton.Ok)
            message.exec()
            if details_button and message.clickedButton() is details_button:
                dialog = BridgeDiagnosticsDialog(self, diagnostics_text)
                dialog.exec()
            return
        info_lines = []
        if isinstance(payload, dict):
            ready = payload.get("ready")
            headless = payload.get("headless")
            allow_headless = payload.get("allow_headless")
            info_lines.append(f"Ready: {ready}")
            info_lines.append(f"Headless: {headless} (allow: {allow_headless})")
            status = payload.get("reason") or payload.get("detail") or payload.get("status")
            if status:
                info_lines.append(f"Reason: {status}")
            if payload.get("trace_id"):
                info_lines.append(f"Bridge Trace: {payload.get('trace_id')}")
        else:
            info_lines.append(f"Status: {payload}")
        info_lines.append(f"Request Trace: {trace_id}")
        QMessageBox.information(
            self,
            "Complex Editor",
            "Bridge OK!\n" + "\n".join(info_lines),
        )

    def _show_normalization_rules(self) -> None:
        try:
            payload = ce_bridge_linker.fetch_normalization_info()
        except LinkerFeatureError as exc:
            QMessageBox.warning(self, "Complex Editor", str(exc))
            return
        except LinkerError as exc:
            QMessageBox.warning(self, "Complex Editor", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.warning(
                self,
                "Complex Editor",
                f"Failed to load normalization rules: {exc}",
            )
            return

        if not isinstance(payload, dict):
            QMessageBox.information(
                self,
                "Complex Editor",
                "Normalization rules are unavailable.",
            )
            return

        rules_version = (
            payload.get("rules_version")
            or payload.get("normalization_rules_version")
            or "<unknown>"
        )
        trace_id = payload.get("trace_id") or "<unknown>"

        def _format_section(value: object) -> str:
            if isinstance(value, (dict, list)):
                try:
                    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
                except TypeError:
                    return str(value)
            return str(value)

        sections: list[str] = []
        for key in ("case", "remove_chars", "ignore_suffixes"):
            if key in payload:
                sections.append(f"{key}:\n{_format_section(payload.get(key))}")

        text_lines = [f"Rules version: {rules_version}", f"Trace: {trace_id}"]
        if sections:
            text_lines.append("")
            text_lines.extend(sections)

        message = QMessageBox(
            QMessageBox.Icon.Information,
            "Normalization Rules",
            "\n".join(text_lines),
            parent=self,
        )
        message.addButton(QMessageBox.StandardButton.Ok)
        message.exec()

    def _diagnostics_text_from_exception(self, exc: Exception) -> str:
        diagnostics = getattr(exc, "diagnostics", None)
        if diagnostics is not None and hasattr(diagnostics, "to_text"):
            try:
                return diagnostics.to_text()
            except Exception:  # pragma: no cover - defensive
                pass
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        tb = traceback.format_exc()
        lines = [
            "BOM_DB ↔ Complex Editor Bridge Diagnostics",
            f"Timestamp: {timestamp}",
            "Outcome: error",
            f"Reason: {exc}",
            "",
            "[Traceback]",
            tb.strip() or "<none>",
        ]
        return "\n".join(lines)

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


class BridgeDiagnosticsDialog(QDialog):
    def __init__(self, parent: QDialog | None, diagnostics_text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bridge Diagnostics")
        self.setModal(True)

        layout = QVBoxLayout(self)
        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setPlainText(diagnostics_text)
        layout.addWidget(self._text)

        button_row = QHBoxLayout()

        copy_button = QPushButton("Copy to Clipboard")
        copy_button.clicked.connect(self._copy_to_clipboard)
        button_row.addWidget(copy_button)

        save_button = QPushButton("Save Report…")
        save_button.clicked.connect(self._save_report)
        button_row.addWidget(save_button)

        button_row.addStretch(1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)

        layout.addLayout(button_row)

    def _copy_to_clipboard(self) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self._text.toPlainText())

    def _save_report(self) -> None:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"BOM_DB_CE_Diagnostics_{timestamp}.txt"
        destination = Path(tempfile.gettempdir()) / filename
        try:
            destination.write_text(self._text.toPlainText(), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - filesystem error
            QMessageBox.critical(self, "Bridge Diagnostics", f"Failed to save report: {exc}")
            return
        QMessageBox.information(self, "Bridge Diagnostics", f"Report saved to: {destination}")

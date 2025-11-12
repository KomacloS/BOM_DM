"""Entry point for the PyQt6 Projects Terminal."""

from __future__ import annotations

import sys
import atexit
import asyncio
import os
import logging
import traceback
from datetime import datetime
import faulthandler
import signal

from ..config import LOG_DIR, TRACEBACK_LOG_PATH

from ..ai_agents import apply_env_from_agents
from ..integration.ce_supervisor import stop_ce_bridge_if_started

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .state import AppState
from .dialogs.settings_dialog import SettingsDialog
from .widgets import AssembliesPane, CustomersPane, ProjectsPane
from .bom_editor_pane import BOMEditorPane

# Ensure the Complex Editor bridge is stopped if the application exits unexpectedly.
atexit.register(stop_ce_bridge_if_started)


class MainWindow(QMainWindow):
    """Main window with splitter and persisted state."""

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self._state = state
        self._settings = QSettings("BOM_DB", "ProjectsTerminal")

        self.setWindowTitle("BOM_DB – Projects Terminal")
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)

        self.cust = CustomersPane(state)
        self.proj = ProjectsPane(state)
        self.asm = AssembliesPane(state)

        self.cust.customerSelected.connect(self._on_customer_selected)
        self.proj.projectSelected.connect(self._on_project_selected)

        self.cust.customerSelected.connect(lambda cid: self._settings.setValue("last_customer", cid))
        self.proj.projectSelected.connect(lambda pid: self._settings.setValue("last_project", pid))
        self.asm.assemblySelected.connect(lambda aid: self._settings.setValue("last_assembly", aid))

        self.customers_group = QGroupBox("Customers")
        cg_layout = QVBoxLayout()
        cg_layout.addWidget(self.cust)
        self.customers_group.setLayout(cg_layout)

        self.projects_group = QGroupBox("Projects — None")
        pg_layout = QVBoxLayout()
        pg_layout.addWidget(self.proj)
        self.projects_group.setLayout(pg_layout)

        self.assemblies_group = QGroupBox("Assemblies — None")
        ag_layout = QVBoxLayout()
        ag_layout.addWidget(self.asm)
        self.bom_editor_btn = QPushButton("BOM Editor…")
        self.bom_editor_btn.clicked.connect(self._open_bom_editor)
        ag_layout.addWidget(self.bom_editor_btn)
        self.assemblies_group.setLayout(ag_layout)

        splitter.addWidget(self.customers_group)
        splitter.addWidget(self.projects_group)
        splitter.addWidget(self.assemblies_group)

        container = QWidget()
        main_layout = QVBoxLayout(container)
        top_bar = QHBoxLayout()
        self.settings_btn = QPushButton("Settings...")
        self.settings_btn.clicked.connect(self._open_settings)
        top_bar.addWidget(self.settings_btn)
        top_bar.addStretch(1)
        main_layout.addLayout(top_bar)
        main_layout.addWidget(splitter)
        self.setCentralWidget(container)

        self.resize(1200, 600)
        geom = self._settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)

        last_c = self._settings.value("last_customer", type=int)
        last_p = self._settings.value("last_project", type=int)
        last_a = self._settings.value("last_assembly", type=int)
        if last_c is not None:
            self.cust.select_id(last_c)
        if last_p is not None:
            self.proj.select_id(last_p)
        if last_a is not None:
            self.asm.select_id(last_a)

        self._editors: list[BOMEditorPane] = []

    def closeEvent(self, event) -> None:  # pragma: no cover - UI glue
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
        stop_ce_bridge_if_started()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted and dialog.changes_applied():
            if dialog.database_changed():
                self._reset_data_views()
                message = "Settings saved. Data was reloaded from the new database."
            else:
                self._state.refresh_customers()
                message = "Settings saved."
            QMessageBox.information(self, "Settings", message)

    def _reset_data_views(self) -> None:
        self.cust.table.clearSelection()
        self.cust.table.setRowCount(0)
        self.cust.delete_btn.setEnabled(False)
        self.projects_group.setTitle("Projects - None")
        self.proj.table.clearSelection()
        self.proj.table.setRowCount(0)
        self.proj.delete_btn.setEnabled(False)
        self.assemblies_group.setTitle("Assemblies - None")
        self.asm.table.clearSelection()
        self.asm.table.setRowCount(0)
        self.asm.items_table.setRowCount(0)
        self.asm.tasks_table.setRowCount(0)
        self.asm.delete_btn.setEnabled(False)
        self.asm.import_btn.setEnabled(False)
        self.asm.items_table.setEnabled(False)
        self.cust.customerSelected.emit(0)
        self.proj.projectSelected.emit(0)
        self.asm.assemblySelected.emit(0)
        self._state.refresh_customers()

    # --------------------------------------------------------------
    def _on_customer_selected(self, cid: int) -> None:  # pragma: no cover - UI glue
        row = self.cust.table.currentRow()
        name_item = self.cust.table.item(row, 1) if row >= 0 else None
        name = name_item.text() if name_item else "None"
        self.projects_group.setTitle(f"Projects — {name}")
        self.proj.set_customer(cid)

    def _on_project_selected(self, pid: int) -> None:  # pragma: no cover - UI glue
        row = self.proj.table.currentRow()
        title_item = self.proj.table.item(row, 2) if row >= 0 else None
        title = title_item.text() if title_item else "None"
        self.assemblies_group.setTitle(f"Assemblies — {title}")
        self.asm.set_project(pid)

    def _open_bom_editor(self) -> None:  # pragma: no cover - UI glue
        aid = self.asm.current_assembly_id() if hasattr(self.asm, "current_assembly_id") else None
        if not aid:
            QMessageBox.information(self, "BOM Editor", "Select an assembly first.")
            return
        editor = BOMEditorPane(aid)
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        editor.show()
        self._editors.append(editor)
        editor.destroyed.connect(lambda _obj, e=editor: self._editors.remove(e))


def main() -> None:  # pragma: no cover - thin wrapper
    # Basic logging to terminal so user sees actions
    level = os.getenv("BOM_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(levelname)s %(name)s: %(message)s")
    # Enable faulthandler to capture hard crashes (e.g., segfaults) into a log file
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _fh_path = LOG_DIR / "crash_faulthandler.log"
        _fh_file = open(_fh_path, "a", encoding="utf-8")
        faulthandler.enable(_fh_file, all_threads=True)
        # Also attempt to register for common termination signals when available
        for sig in (getattr(signal, "SIGABRT", None), getattr(signal, "SIGSEGV", None)):
            if sig is not None:
                try:
                    faulthandler.register(sig, file=_fh_file, all_threads=True)
                except Exception:
                    pass
    except Exception:
        pass
    # Capture exceptions in threads that escape to Python's threading.excepthook
    try:
        def _thread_excepthook(args):  # type: ignore[no-redef]
            try:
                with open(TRACEBACK_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write("\n===== Unhandled thread exception at " + datetime.utcnow().isoformat() + "Z =====\n")
                    traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback, file=f)
            except Exception:
                pass
            # Also echo to stderr
            traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
        import threading as _threading
        _threading.excepthook = _thread_excepthook  # type: ignore[assignment]
    except Exception:
        pass
    # Write unhandled exceptions to a simple traceback log (not full debug)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    def _excepthook(exc_type, exc_value, exc_tb):  # pragma: no cover - environment dependent
        try:
            with open(TRACEBACK_LOG_PATH, "a", encoding="utf-8") as f:
                f.write("\n===== Unhandled exception at " + datetime.utcnow().isoformat() + "Z =====\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        # Also print to stderr
        traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook
    # Capture Qt warnings/criticals/fatals into a dedicated log
    try:
        from PyQt6 import QtCore
        qt_logger = logging.getLogger("qt")
        from logging.handlers import RotatingFileHandler
        qt_log_path = LOG_DIR / "qt_messages.log"
        if not any(isinstance(h, logging.FileHandler) and str(getattr(h, 'baseFilename', '')).endswith('qt_messages.log') for h in qt_logger.handlers):
            qt_fh = RotatingFileHandler(qt_log_path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding='utf-8')
            qt_fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            qt_fh.setLevel(logging.INFO)
            qt_logger.addHandler(qt_fh)
            if qt_logger.level == logging.NOTSET:
                qt_logger.setLevel(logging.INFO)

        def _qt_msg_handler(mode, context, message):  # type: ignore[no-redef]
            try:
                # Map Qt message type to logging level
                if mode == QtCore.QtMsgType.QtDebugMsg:
                    lvl = logging.DEBUG
                elif mode == QtCore.QtMsgType.QtInfoMsg:
                    lvl = logging.INFO
                elif mode == QtCore.QtMsgType.QtWarningMsg:
                    lvl = logging.WARNING
                elif mode == QtCore.QtMsgType.QtCriticalMsg:
                    lvl = logging.ERROR
                else:  # QtFatalMsg
                    lvl = logging.CRITICAL
                where = f"{context.file}:{context.line} ({context.function})" if context and context.file else "<qt>"
                qt_logger.log(lvl, f"{where}: {message}")
            except Exception:
                pass
        QtCore.qInstallMessageHandler(_qt_msg_handler)
    except Exception:
        pass
    # Ensure pyppeteer/requests_html are happy on Windows threads
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    # Optional: force DEBUG for API layer with BOM_API_DEBUG=1
    if os.getenv("BOM_API_DEBUG", "").lower() in ("1", "true", "yes", "on"):
        logging.getLogger().setLevel(logging.DEBUG)
        for name in (
            "app.services.datasheet_api",
            "app.services.datasheet_html",
            "app.services.gpt_rerank",
            "app.gui.auto_datasheet_dialog",
        ):
            logging.getLogger(name).setLevel(logging.DEBUG)
        # Keep noisy libraries at warning
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)
    # Bridge agents.local.toml into environment for search/rerank services
    apply_env_from_agents()
    app = QApplication(sys.argv)
    state = AppState()
    win = MainWindow(state)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover
    main()

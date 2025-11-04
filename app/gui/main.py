"""Entry point for the PyQt6 Projects Terminal."""

from __future__ import annotations

import sys
import asyncio
import os
import logging
import traceback
import atexit
import signal
import faulthandler
from datetime import datetime
from typing import Optional, TextIO

from ..config import LOG_DIR, TRACEBACK_LOG_PATH

from ..ai_agents import apply_env_from_agents
from ..integration.ce_supervisor import stop_ce_bridge_if_started

from PyQt6.QtCore import Qt, QSettings, QThreadPool
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


logger = logging.getLogger(__name__)


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
    crash_log_path = TRACEBACK_LOG_PATH.with_name(f"{TRACEBACK_LOG_PATH.stem}_crash{TRACEBACK_LOG_PATH.suffix or '.log'}")
    fh_file: Optional[TextIO] = None
    fh_target: Optional[TextIO] = None
    faulthandler_enabled = False
    try:
        crash_log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        fh_file = open(crash_log_path, "a", encoding="utf-8")
        faulthandler.enable(fh_file, all_threads=True)
        faulthandler_enabled = True
        fh_target = fh_file
        logger.info("Crash diagnostics enabled: %s", crash_log_path)
    except Exception as exc:
        fh_file = None
        logger.debug("Unable to enable crash diagnostics file logging: %s", exc)
    if not faulthandler_enabled:
        try:
            faulthandler.enable(all_threads=True)
            faulthandler_enabled = True
            fh_target = None
            logger.info("Crash diagnostics enabled on standard error")
        except Exception as exc:
            logger.debug("Faulthandler enable failed: %s", exc)
    if faulthandler_enabled:
        for sig_name in ("SIGTERM", "SIGINT", "SIGABRT", "SIGSEGV", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                faulthandler.register(sig, file=fh_target or sys.stderr, all_threads=True, chain=True)
            except (RuntimeError, OSError, ValueError) as exc:
                logger.debug("Faulthandler register for %s failed: %s", sig_name, exc)
    def _log_exit() -> None:
        try:
            active_threads = QThreadPool.globalInstance().activeThreadCount()
        except Exception:
            active_threads = -1
        logger.info("Projects Terminal shutting down (active_threads=%s)", active_threads)
        if fh_file is not None:
            try:
                fh_file.flush()
            except Exception:
                pass
            try:
                faulthandler.disable()
            except Exception:
                pass
            try:
                fh_file.close()
            except Exception:
                pass
            logger.info("Crash diagnostics log closed: %s", crash_log_path)
    atexit.register(_log_exit)
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
    def _about_to_quit() -> None:
        try:
            pool = QThreadPool.globalInstance()
            logger.info(
                "Qt aboutToQuit signaled (active_threads=%d max_threads=%d)",
                pool.activeThreadCount(),
                pool.maxThreadCount(),
            )
        except Exception:
            logger.info("Qt aboutToQuit signaled")
    app.aboutToQuit.connect(_about_to_quit)
    state = AppState()
    win = MainWindow(state)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover
    main()

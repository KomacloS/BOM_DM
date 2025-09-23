"""Entry point for the PyQt6 Projects Terminal."""

from __future__ import annotations

import sys
import os
import logging

from ..ai_agents import apply_env_from_agents

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)

from .state import AppState
from .widgets import AssembliesPane, CustomersPane, ProjectsPane
from .bom_editor_pane import BOMEditorPane


class MainWindow(QMainWindow):
    """Main window with splitter and persisted state."""

    def __init__(self, state: AppState) -> None:
        super().__init__()
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
        self.setCentralWidget(splitter)

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

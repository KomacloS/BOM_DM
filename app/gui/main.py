"""Entry point for the PyQt6 Projects Terminal."""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import QApplication, QMainWindow, QSplitter

from .state import AppState
from .widgets import AssembliesPane, CustomersPane, ProjectsPane


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

        self.cust.customerSelected.connect(self.proj.set_customer)
        self.proj.projectSelected.connect(self.asm.set_project)

        self.cust.customerSelected.connect(lambda cid: self._settings.setValue("last_customer", cid))
        self.proj.projectSelected.connect(lambda pid: self._settings.setValue("last_project", pid))
        self.asm.assemblySelected.connect(lambda aid: self._settings.setValue("last_assembly", aid))

        splitter.addWidget(self.cust)
        splitter.addWidget(self.proj)
        splitter.addWidget(self.asm)
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

    def closeEvent(self, event) -> None:  # pragma: no cover - UI glue
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)


def main() -> None:  # pragma: no cover - thin wrapper
    app = QApplication(sys.argv)
    state = AppState()
    win = MainWindow(state)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover
    main()

"""Reusable GUI widgets for the Projects Terminal."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import IntegrityError

from . import state as app_state
from .. import services
from ..models import ProjectPriority, TaskStatus
from ..bom_schema import ALLOWED_HEADERS


class CustomersPane(QWidget):
    customerSelected = pyqtSignal(int)

    def __init__(self, state: app_state.AppState) -> None:
        super().__init__()
        self._state = state
        self._pending_id: Optional[int] = None

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["ID", "Name"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        form = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Name")
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("Email")
        btn = QPushButton("Create")
        btn.clicked.connect(self.create_customer)
        form.addWidget(self.name_edit)
        form.addWidget(self.email_edit)
        form.addWidget(btn)
        layout.addLayout(form)

        self.table.cellClicked.connect(self._on_select)
        self.search.textChanged.connect(lambda: state.refresh_customers(self.search.text()))
        state.customersChanged.connect(self._populate)
        state.refresh_customers()

    def select_id(self, cid: int) -> None:
        self._pending_id = cid

    # --------------------------------------------------------------
    def _populate(self, customers):  # pragma: no cover - UI glue
        self.table.setRowCount(len(customers))
        for row, c in enumerate(customers):
            self.table.setItem(row, 0, QTableWidgetItem(str(c.id)))
            self.table.setItem(row, 1, QTableWidgetItem(c.name))
        self.table.resizeColumnsToContents()
        if self._pending_id is not None:
            for row, c in enumerate(customers):
                if c.id == self._pending_id:
                    self.table.selectRow(row)
                    self._on_select(row, 0)
                    break
            self._pending_id = None

    def _on_select(self, row: int, _col: int) -> None:  # pragma: no cover - UI glue
        cid = int(self.table.item(row, 0).text())
        self.customerSelected.emit(cid)

    def create_customer(self) -> None:  # pragma: no cover - UI glue
        name = self.name_edit.text().strip()
        if not name:
            return
        email = self.email_edit.text().strip() or None
        with app_state.get_session() as session:
            try:
                services.create_customer(name, email, session)
            except IntegrityError:
                QMessageBox.warning(self, "Error", "Customer already exists")
                return
        self.name_edit.clear()
        self.email_edit.clear()
        self._state.refresh_customers()


class ProjectsPane(QWidget):
    projectSelected = pyqtSignal(int)

    def __init__(self, state: app_state.AppState) -> None:
        super().__init__()
        self._state = state
        self._customer_id: Optional[int] = None
        self._pending_id: Optional[int] = None

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ID", "Code", "Title"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        form = QFormLayout()
        self.code_edit = QLineEdit()
        self.title_edit = QLineEdit()
        self.prio_combo = QComboBox()
        self.prio_combo.addItems([p.value for p in ProjectPriority])
        self.due_edit = QDateEdit()
        self.due_edit.setCalendarPopup(True)
        btn = QPushButton("Create")
        btn.clicked.connect(self.create_project)
        form.addRow("Code", self.code_edit)
        form.addRow("Title", self.title_edit)
        form.addRow("Priority", self.prio_combo)
        form.addRow("Due", self.due_edit)
        form.addRow(btn)
        layout.addLayout(form)

        self.table.cellClicked.connect(self._on_select)
        state.projectsChanged.connect(self._populate)

    def select_id(self, pid: int) -> None:
        self._pending_id = pid

    # --------------------------------------------------------------
    def set_customer(self, cid: int) -> None:  # pragma: no cover - UI glue
        self._customer_id = cid
        self._state.refresh_projects(cid)

    def _populate(self, projects):  # pragma: no cover - UI glue
        self.table.setRowCount(len(projects))
        for row, p in enumerate(projects):
            self.table.setItem(row, 0, QTableWidgetItem(str(p.id)))
            self.table.setItem(row, 1, QTableWidgetItem(p.code))
            self.table.setItem(row, 2, QTableWidgetItem(p.title))
        self.table.resizeColumnsToContents()
        if self._pending_id is not None:
            for row, p in enumerate(projects):
                if p.id == self._pending_id:
                    self.table.selectRow(row)
                    self._on_select(row, 0)
                    break
            self._pending_id = None

    def _on_select(self, row: int, _col: int) -> None:  # pragma: no cover
        pid = int(self.table.item(row, 0).text())
        self.projectSelected.emit(pid)

    def create_project(self) -> None:  # pragma: no cover - UI glue
        if self._customer_id is None:
            return
        code = self.code_edit.text().strip()
        title = self.title_edit.text().strip()
        if not code or not title:
            return
        prio = self.prio_combo.currentText()
        due = self.due_edit.date().toPyDate()
        due_dt = datetime.combine(due, datetime.min.time()) if due else None
        with app_state.get_session() as session:
            try:
                services.create_project(self._customer_id, code, title, prio, due_dt, session)
            except IntegrityError:
                QMessageBox.warning(self, "Error", "Project already exists")
                return
        self.code_edit.clear()
        self.title_edit.clear()
        self._state.refresh_projects(self._customer_id)


class AssembliesPane(QWidget):
    assemblySelected = pyqtSignal(int)

    def __init__(self, state: app_state.AppState) -> None:
        super().__init__()
        self._state = state
        self._project_id: Optional[int] = None
        self._assembly_id: Optional[int] = None
        self._pending_id: Optional[int] = None

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["ID", "Rev"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        form = QHBoxLayout()
        self.rev_edit = QLineEdit()
        self.rev_edit.setPlaceholderText("Rev")
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Notes")
        btn = QPushButton("Create")
        btn.clicked.connect(self.create_assembly)
        form.addWidget(self.rev_edit)
        form.addWidget(self.notes_edit)
        form.addWidget(btn)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.import_btn = QPushButton("Import BOM…")
        self.import_btn.clicked.connect(self.upload_bom)
        self.template_btn = QPushButton("Download CSV template")
        self.template_btn.clicked.connect(self.download_template)
        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(self.template_btn)
        layout.addLayout(btn_row)

        self.items_table = QTableWidget(0, 4)
        self.items_table.setHorizontalHeaderLabels(
            ["Reference", "Qty", "Part Number", "Notes"]
        )
        self.items_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.items_table.horizontalHeader().setStretchLastSection(True)
        self.items_table.setAlternatingRowColors(True)
        layout.addWidget(self.items_table)

        self.status_filter = QComboBox()
        self.status_filter.addItems([s.value for s in TaskStatus])
        self.status_filter.currentTextChanged.connect(self._on_status_change)
        self.status_filter.setCurrentText(TaskStatus.todo.value)
        layout.addWidget(self.status_filter)

        self.tasks_table = QTableWidget(0, 3)
        self.tasks_table.setHorizontalHeaderLabels(["ID", "Title", "Status"])
        self.tasks_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.tasks_table.horizontalHeader().setStretchLastSection(True)
        self.tasks_table.setAlternatingRowColors(True)
        layout.addWidget(self.tasks_table)

        self.table.cellClicked.connect(self._on_select)
        state.assembliesChanged.connect(self._populate)
        state.bomItemsChanged.connect(self._populate_items)
        state.tasksChanged.connect(self._populate_tasks)
        state.bomImported.connect(self._import_finished)

    def select_id(self, aid: int) -> None:
        self._pending_id = aid

    # --------------------------------------------------------------
    def set_project(self, pid: int) -> None:  # pragma: no cover - UI glue
        self._project_id = pid
        self._state.refresh_assemblies(pid)

    def _populate(self, assemblies):  # pragma: no cover - UI glue
        self.table.setRowCount(len(assemblies))
        for row, a in enumerate(assemblies):
            self.table.setItem(row, 0, QTableWidgetItem(str(a.id)))
            self.table.setItem(row, 1, QTableWidgetItem(a.rev))
        self.table.resizeColumnsToContents()
        if self._pending_id is not None:
            for row, a in enumerate(assemblies):
                if a.id == self._pending_id:
                    self.table.selectRow(row)
                    self._on_select(row, 0)
                    break
            self._pending_id = None

    def _on_select(self, row: int, _col: int) -> None:  # pragma: no cover
        aid = int(self.table.item(row, 0).text())
        self._assembly_id = aid
        self.assemblySelected.emit(aid)
        if aid:
            self._state.refresh_bom_items(aid)
        if self._project_id:
            self._state.refresh_tasks(
                self._project_id, self.status_filter.currentText()
            )

    def create_assembly(self) -> None:  # pragma: no cover - UI glue
        if self._project_id is None:
            return
        rev = self.rev_edit.text().strip()
        if not rev:
            return
        notes = self.notes_edit.text().strip() or None
        with app_state.get_session() as session:
            try:
                services.create_assembly(self._project_id, rev, notes, session)
            except IntegrityError:
                QMessageBox.warning(self, "Error", "Assembly already exists")
                return
        self.rev_edit.clear()
        self.notes_edit.clear()
        self._state.refresh_assemblies(self._project_id)

    def upload_bom(self) -> None:  # pragma: no cover - UI glue
        if self._assembly_id is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import BOM", filter="CSV Files (*.csv)")
        if not path:
            return
        data = Path(path).read_bytes()
        self._state.import_bom(self._assembly_id, data)

    def download_template(self) -> None:  # pragma: no cover - UI glue
        path, _ = QFileDialog.getSaveFileName(
            self, "Save BOM template", "bom_template.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        Path(path).write_text(",".join(ALLOWED_HEADERS) + "\n", encoding="utf-8")

    def _on_status_change(self) -> None:  # pragma: no cover - UI glue
        if self._project_id:
            self._state.refresh_tasks(self._project_id, self.status_filter.currentText())

    def _populate_items(self, items):  # pragma: no cover - UI glue
        self.items_table.setRowCount(len(items))
        for row, i in enumerate(items):
            self.items_table.setItem(row, 0, QTableWidgetItem(i.reference))
            self.items_table.setItem(row, 1, QTableWidgetItem(str(i.qty)))
            pn = i.part_number or "—"
            self.items_table.setItem(row, 2, QTableWidgetItem(pn))
            self.items_table.setItem(row, 3, QTableWidgetItem(i.notes or ""))
        self.items_table.resizeColumnsToContents()

    def _populate_tasks(self, tasks):  # pragma: no cover - UI glue
        self.tasks_table.setRowCount(len(tasks))
        for row, t in enumerate(tasks):
            self.tasks_table.setItem(row, 0, QTableWidgetItem(str(t.id)))
            self.tasks_table.setItem(row, 1, QTableWidgetItem(t.title))
            self.tasks_table.setItem(row, 2, QTableWidgetItem(t.status.value))
        self.tasks_table.resizeColumnsToContents()

    def _import_finished(self, report):  # pragma: no cover - UI glue
        if self._assembly_id:
            self._state.refresh_bom_items(self._assembly_id)
        if self._project_id:
            self._state.refresh_tasks(
                self._project_id, self.status_filter.currentText()
            )
        tasks = ", ".join(str(t) for t in report.created_task_ids) or "none"
        QMessageBox.information(
            self,
            "Import",
            f"Imported {report.total} rows\n"
            f"Matched: {report.matched}\n"
            f"Unmatched: {report.unmatched}\n"
            f"Created task IDs: {tasks}",
        )
        if report.errors:
            QMessageBox.warning(self, "Import errors", "\n".join(report.errors))


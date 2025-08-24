"""Reusable GUI widgets for the Projects Terminal."""

from __future__ import annotations

from datetime import datetime
import re
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from . import state as app_state
from .. import services
from ..models import ProjectPriority, TaskStatus, Project, Assembly
from ..services.customers import CustomerExistsError
from .workflow import NewProjectWizard
from ..bom_schema import ALLOWED_HEADERS


class CustomersPane(QWidget):
    customerSelected = pyqtSignal(int)
    customerCreated = pyqtSignal(object)

    def __init__(self, state: app_state.AppState) -> None:
        super().__init__()
        self._state = state
        self._pending_id: Optional[int] = None

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        layout.addWidget(self.search)

        hdr = QHBoxLayout()
        self.delete_btn = QPushButton("Delete Customer…")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._on_delete_customer)
        hdr.addWidget(self.delete_btn)
        hdr.addStretch()
        layout.addLayout(hdr)

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
        self.create_btn = QPushButton("Create")
        self.create_btn.clicked.connect(self._on_create_customer)
        form.addWidget(self.name_edit)
        form.addWidget(self.email_edit)
        form.addWidget(self.create_btn)
        layout.addLayout(form)

        self.table.cellClicked.connect(self._on_select)

        # debounce search box
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(
            lambda: state.refresh_customers(self.search.text())
        )
        self.search.textChanged.connect(lambda: self._search_timer.start(300))

        state.customersChanged.connect(self._populate)
        self.customerCreated.connect(self._after_create_customer)
        state.refresh_customers()

    def refresh_customers_and_select(self, cid: int) -> None:
        self.select_id(cid)
        self._state.refresh_customers(self.search.text())

    def refresh_customers_and_clear_selection(self) -> None:
        self.table.clearSelection()
        self.delete_btn.setEnabled(False)
        self.customerSelected.emit(0)
        self._state.refresh_customers(self.search.text())

    def select_id(self, cid: int) -> None:
        self._pending_id = cid

    def _table_id_at(self, row: int) -> int:
        return int(self.table.item(row, 0).text())

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
        cid = self._table_id_at(row)
        self.delete_btn.setEnabled(True)
        self.customerSelected.emit(cid)

    # --------------------------------------------------------------
    def _on_create_customer(self) -> None:  # pragma: no cover - UI glue
        name = self.name_edit.text()
        email = self.email_edit.text()
        self.create_btn.setEnabled(False)

        def work():
            with app_state.get_session() as session:
                return services.create_customer(name, email, session)

        self._state._run(work, self.customerCreated)

    def _after_create_customer(self, result_or_exc):  # pragma: no cover - UI glue
        self.create_btn.setEnabled(True)
        if isinstance(result_or_exc, Exception):
            QMessageBox.warning(self, "Error", str(result_or_exc))
            return
        self.name_edit.clear()
        self.email_edit.clear()
        self.refresh_customers_and_select(result_or_exc.id)

    def _on_delete_customer(self) -> None:  # pragma: no cover - UI glue
        row = self.table.currentRow()
        if row < 0:
            return
        cid = self._table_id_at(row)
        with app_state.get_session() as s:
            n_projects = s.exec(
                select(func.count()).select_from(Project).where(Project.customer_id == cid)
            ).one()
        cascade = False
        if n_projects:
            resp = QMessageBox.question(
                self,
                "Delete Customer",
                f"This customer has {n_projects} projects.\nDelete EVERYTHING (cascade)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
            cascade = True
        try:
            with app_state.get_session() as s:
                services.delete_customer(cid, s, cascade=cascade)
        except services.DeleteBlockedError as e:
            QMessageBox.warning(self, "Cannot delete", str(e))
            return
        self.refresh_customers_and_clear_selection()


class ProjectsPane(QWidget):
    projectSelected = pyqtSignal(int)

    def __init__(self, state: app_state.AppState) -> None:
        super().__init__()
        self._state = state
        self._customer_id: Optional[int] = None
        self._pending_id: Optional[int] = None

        layout = QVBoxLayout(self)
        self.workflow_btn = QPushButton("➕ New Project (Workflow)")
        self.workflow_btn.clicked.connect(self._open_new_project_workflow)
        layout.addWidget(self.workflow_btn)

        hdr = QHBoxLayout()
        self.delete_btn = QPushButton("Delete Project…")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._on_delete_project)
        hdr.addWidget(self.delete_btn)
        hdr.addStretch()
        layout.addLayout(hdr)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ID", "Code", "Title"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        form = QFormLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setToolTip(
            "Code: short identifier for the project (e.g., \"ACME-001\" or \"P-213\"). "
            "Used in search, file naming, and URLs. Keep it short; letters, digits, "
            "dashes/underscores are recommended. It should be unique within the customer."
        )
        self.title_edit = QLineEdit()
        self.prio_combo = QComboBox()
        self.prio_combo.addItems([p.value for p in ProjectPriority])
        self.due_edit = QDateEdit()
        self.due_edit.setCalendarPopup(True)
        self.create_btn = QPushButton("Create")
        self.create_btn.clicked.connect(self.create_project)
        form.addRow("Code", self.code_edit)
        form.addRow("Title", self.title_edit)
        form.addRow("Priority", self.prio_combo)
        form.addRow("Due", self.due_edit)
        form.addRow(self.create_btn)
        layout.addLayout(form)

        self.table.cellClicked.connect(self._on_select)
        state.projectsChanged.connect(self._populate)

    def select_id(self, pid: int) -> None:
        self._pending_id = pid

    def _table_id_at(self, row: int) -> int:
        return int(self.table.item(row, 0).text())

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
        pid = self._table_id_at(row)
        self.delete_btn.setEnabled(True)
        self.projectSelected.emit(pid)

    def create_project(self) -> None:  # pragma: no cover - UI glue
        if self._customer_id is None:
            return
        code = self.code_edit.text().strip()
        title = self.title_edit.text().strip()
        if not code or not title:
            QMessageBox.warning(self, "Error", "Code and title are required")
            return
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", code):
            QMessageBox.warning(
                self,
                "Error",
                "Invalid code. Use letters, digits, dashes/underscores (max 32).",
            )
            return
        prio = self.prio_combo.currentText()
        due = self.due_edit.date().toPyDate()
        due_dt = datetime.combine(due, datetime.min.time()) if due else None
        self.create_btn.setEnabled(False)
        with app_state.get_session() as session:
            try:
                services.create_project(self._customer_id, code, title, prio, due_dt, session)
            except Exception as exc:
                QMessageBox.warning(self, "Error", str(exc))
                self.create_btn.setEnabled(True)
                return
        self.code_edit.clear()
        self.title_edit.clear()
        self._state.refresh_projects(self._customer_id)
        self.create_btn.setEnabled(True)

    # --------------------------------------------------------------
    def refresh_projects_and_select(self, cid: int, pid: int) -> None:
        self._customer_id = cid
        self.select_id(pid)
        self._state.refresh_projects(cid)

    def refresh_projects_and_clear_selection(self) -> None:
        self.table.clearSelection()
        self.delete_btn.setEnabled(False)
        self.projectSelected.emit(0)
        if self._customer_id is not None:
            self._state.refresh_projects(self._customer_id)

    def refresh_assemblies_and_select(self, pid: int, aid: int) -> None:
        self.projectSelected.emit(pid)
        # AssembliesPane selection handled externally

    def _open_new_project_workflow(self) -> None:  # pragma: no cover - UI glue
        wiz = NewProjectWizard(self._state, parent=self)
        if wiz.exec() == QDialog.DialogCode.Accepted:
            cid, pid, aid = wiz.result_ids()
            self.refresh_projects_and_select(cid, pid)
            self.projectSelected.emit(pid)
            if aid:
                self.refresh_assemblies_and_select(pid, aid)

    def _on_delete_project(self) -> None:  # pragma: no cover - UI glue
        row = self.table.currentRow()
        if row < 0:
            return
        pid = self._table_id_at(row)
        with app_state.get_session() as s:
            n_assemblies = s.exec(
                select(func.count()).select_from(Assembly).where(Assembly.project_id == pid)
            ).one()
        cascade = False
        if n_assemblies:
            resp = QMessageBox.question(
                self,
                "Delete Project",
                f"This project has {n_assemblies} assemblies.\nDelete EVERYTHING (cascade)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
            cascade = True
        try:
            with app_state.get_session() as s:
                services.delete_project(pid, s, cascade=cascade)
        except services.DeleteBlockedError as e:
            QMessageBox.warning(self, "Cannot delete", str(e))
            return
        self.refresh_projects_and_clear_selection()


class AssembliesPane(QWidget):
    assemblySelected = pyqtSignal(int)

    def __init__(self, state: app_state.AppState) -> None:
        super().__init__()
        self._state = state
        self._project_id: Optional[int] = None
        self._assembly_id: Optional[int] = None
        self._pending_id: Optional[int] = None

        layout = QVBoxLayout(self)
        hdr = QHBoxLayout()
        self.delete_btn = QPushButton("Delete Assembly…")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._on_delete_assembly)
        hdr.addWidget(self.delete_btn)
        hdr.addStretch()
        layout.addLayout(hdr)

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
        self.create_btn = QPushButton("Create")
        self.create_btn.clicked.connect(self.create_assembly)
        form.addWidget(self.rev_edit)
        form.addWidget(self.notes_edit)
        form.addWidget(self.create_btn)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.import_btn = QPushButton("Import BOM…")
        self.import_btn.clicked.connect(self._on_import_bom)
        self.template_btn = QPushButton("Download CSV template")
        self.template_btn.clicked.connect(self.download_template)
        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(self.template_btn)
        layout.addLayout(btn_row)
        # Sub-headers
        bom_lbl = QLabel("BOM Items")
        f = bom_lbl.font()
        f.setBold(True)
        bom_lbl.setFont(f)
        layout.addWidget(bom_lbl)

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

        tasks_lbl = QLabel("Tasks")
        f2 = tasks_lbl.font()
        f2.setBold(True)
        tasks_lbl.setFont(f2)
        layout.addWidget(tasks_lbl)

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

    def select_id(self, aid: int) -> None:
        self._pending_id = aid

    def _table_id_at(self, row: int) -> int:
        return int(self.table.item(row, 0).text())

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
        aid = self._table_id_at(row)
        self._assembly_id = aid
        self.delete_btn.setEnabled(True)
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
        self.create_btn.setEnabled(False)
        with app_state.get_session() as session:
            try:
                services.create_assembly(self._project_id, rev, notes, session)
            except IntegrityError as exc:
                QMessageBox.warning(self, "Error", str(exc))
                self.create_btn.setEnabled(True)
                return
        self.rev_edit.clear()
        self.notes_edit.clear()
        self._state.refresh_assemblies(self._project_id)
        self.create_btn.setEnabled(True)

    def _on_import_bom(self) -> None:  # pragma: no cover - UI glue
        if self._assembly_id is None:
            QMessageBox.information(self, "Import BOM", "Select an assembly first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select BOM CSV", "", "CSV Files (*.csv)"
        )
        if not path:
            return
        self.import_btn.setEnabled(False)

        def work():
            with open(path, "rb") as f:
                data = f.read()
            with app_state.get_session() as s:
                return services.import_bom(self._assembly_id, data, s)

        self._state._run(work, self._after_import_bom)

    def download_template(self) -> None:  # pragma: no cover - UI glue
        path, _ = QFileDialog.getSaveFileName(
            self, "Save BOM template", "bom_template.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        Path(path).write_text(",".join(ALLOWED_HEADERS) + "\n", encoding="utf-8")

    def _after_import_bom(self, result):  # pragma: no cover - UI glue
        self.import_btn.setEnabled(True)
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Import BOM", str(result))
            return
        report = result
        msg = (
            f"Imported lines: {report.total}\n"
            f"Matched: {report.matched}\n"
            f"Unmatched: {report.unmatched}\n"
            f"New tasks: {len(report.created_task_ids)}"
        )
        if report.errors:
            msg += "\n\nErrors:\n- " + "\n- ".join(report.errors[:10])
        QMessageBox.information(self, "Import complete", msg)
        if self._assembly_id:
            self._state.refresh_bom_items(self._assembly_id)
        if self._project_id:
            self._state.refresh_tasks(
                self._project_id, self.status_filter.currentText()
            )

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
        
    def _on_delete_assembly(self) -> None:  # pragma: no cover - UI glue
        row = self.table.currentRow()
        if row < 0:
            return
        aid = self._table_id_at(row)
        resp = QMessageBox.question(
            self,
            "Delete Assembly",
            "Delete this assembly?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        with app_state.get_session() as s:
            services.delete_assembly(aid, s)
        self._assembly_id = None
        self.delete_btn.setEnabled(False)
        if self._project_id:
            self._state.refresh_assemblies(self._project_id)
            self._state.refresh_tasks(
                self._project_id, self.status_filter.currentText()
            )
        self._state.bomItemsChanged.emit([])


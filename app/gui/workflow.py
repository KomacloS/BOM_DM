"""New Project Workflow wizard."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from . import state as app_state
from .. import services
from ..models import ProjectPriority


class NewProjectWizard(QWizard):
    """Guided wizard to create customer, project, and optional assembly."""

    def __init__(self, state: app_state.AppState, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._customer_id: Optional[int] = None
        self._project_id: Optional[int] = None
        self._assembly_id: Optional[int] = None

        self.setWindowTitle("New Project Workflow")

        self.addPage(_CustomerPage(self))
        self.addPage(_ProjectPage(self))
        self.addPage(_AssemblyPage(self))

    # ------------------------------------------------------------------
    def result_ids(self) -> tuple[int, int, Optional[int]]:
        return self._customer_id, self._project_id, self._assembly_id


class _CustomerPage(QWizardPage):
    def __init__(self, wiz: NewProjectWizard) -> None:
        super().__init__(wiz)
        self.wiz = wiz
        self.setTitle("Customer")

        layout = QVBoxLayout(self)
        self.use_existing = QRadioButton("Use existing customer")
        self.create_new = QRadioButton("Create new customer")
        self.use_existing.setChecked(True)

        self.combo = QComboBox()
        with app_state.get_session() as s:
            for c in services.list_customers(None, s):
                self.combo.addItem(c.name, c.id)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.email_edit = QLineEdit()
        form.addRow("Name", self.name_edit)
        form.addRow("Email", self.email_edit)

        layout.addWidget(self.use_existing)
        layout.addWidget(self.combo)
        layout.addWidget(self.create_new)
        layout.addLayout(form)

        self.use_existing.toggled.connect(self._toggle)
        self._toggle(True)

    def _toggle(self, use_existing: bool) -> None:
        self.combo.setEnabled(use_existing)
        self.name_edit.setEnabled(not use_existing)
        self.email_edit.setEnabled(not use_existing)

    def validatePage(self) -> bool:  # pragma: no cover - UI glue
        if self.use_existing.isChecked():
            cid = self.combo.currentData()
            if cid is None:
                QMessageBox.warning(self, "Error", "No customer selected")
                return False
            self.wiz._customer_id = cid
            return True

        name = self.name_edit.text()
        email = self.email_edit.text()
        try:
            with app_state.get_session() as s:
                cust = services.create_customer(name, email, s)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return False
        self.wiz._customer_id = cust.id
        return True


class _ProjectPage(QWizardPage):
    def __init__(self, wiz: NewProjectWizard) -> None:
        super().__init__(wiz)
        self.wiz = wiz
        self.setTitle("Project")

        form = QFormLayout(self)
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
        self.notes_edit = QLineEdit()
        form.addRow("Code", self.code_edit)
        form.addRow("Title", self.title_edit)
        form.addRow("Priority", self.prio_combo)
        form.addRow("Due", self.due_edit)
        form.addRow("Notes", self.notes_edit)

    def validatePage(self) -> bool:  # pragma: no cover - UI glue
        if self.wiz._customer_id is None:
            QMessageBox.warning(self, "Error", "Customer missing")
            return False
        code = self.code_edit.text().strip()
        title = self.title_edit.text().strip()
        if not code or not title:
            QMessageBox.warning(self, "Error", "Code and title are required")
            return False
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", code):
            QMessageBox.warning(
                self,
                "Error",
                "Invalid code. Use letters, digits, dashes/underscores (max 32).",
            )
            return False
        prio = self.prio_combo.currentText()
        due_qdate = self.due_edit.date()
        due = datetime.combine(due_qdate.toPyDate(), datetime.min.time()) if due_qdate else None
        notes = self.notes_edit.text().strip() or None
        try:
            with app_state.get_session() as s:
                proj = services.create_project(
                    self.wiz._customer_id, code, title, prio, due, s
                )
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return False
        self.wiz._project_id = proj.id
        return True


class _AssemblyPage(QWizardPage):
    def __init__(self, wiz: NewProjectWizard) -> None:
        super().__init__(wiz)
        self.wiz = wiz
        self.setTitle("Assembly")

        layout = QVBoxLayout(self)
        self.create_chk = QCheckBox("Create first assembly now?")
        layout.addWidget(self.create_chk)

        form = QFormLayout()
        self.rev_edit = QLineEdit()
        self.notes_edit = QLineEdit()
        form.addRow("Rev", self.rev_edit)
        form.addRow("Notes", self.notes_edit)
        layout.addLayout(form)

        self._toggle(False)
        self.create_chk.toggled.connect(self._toggle)

    def _toggle(self, checked: bool) -> None:
        self.rev_edit.setEnabled(checked)
        self.notes_edit.setEnabled(checked)

    def validatePage(self) -> bool:  # pragma: no cover - UI glue
        if not self.create_chk.isChecked():
            return True
        if self.wiz._project_id is None:
            QMessageBox.warning(self, "Error", "Project missing")
            return False
        rev = self.rev_edit.text().strip()
        notes = self.notes_edit.text().strip() or None
        try:
            with app_state.get_session() as s:
                asm = services.create_assembly(self.wiz._project_id, rev, notes, s)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return False
        self.wiz._assembly_id = asm.id
        return True

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QObject, QThread
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QFileDialog, QHBoxLayout, QFrame, QMessageBox
)

from .. import services
from . import state as app_state


class DropArea(QFrame):
    fileSelected = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAcceptDrops(True)
        self._label = QLabel("Drop PDF here")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self)
        lay.addWidget(self._label)

    def dragEnterEvent(self, event):  # pragma: no cover - UI glue
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.toLocalFile().lower().endswith(".pdf"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):  # pragma: no cover - UI glue
        for u in event.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".pdf"):
                self.fileSelected.emit(p)
                return


class Worker(QObject):
    finished = pyqtSignal(str, bool, str)  # path, existed, error

    def __init__(self, part_id: int, src_path: str) -> None:
        super().__init__()
        self.part_id = part_id
        self.src_path = src_path

    def run(self):  # pragma: no cover - simple worker
        try:
            p = Path(self.src_path)
            with app_state.get_session() as session:
                dst, existed = services.register_datasheet_for_part(session, self.part_id, p)
            self.finished.emit(str(dst), existed, "")
        except Exception as e:  # pragma: no cover - depends on FS
            self.finished.emit("", False, str(e))


class DatasheetAttachDialog(QDialog):
    attached = pyqtSignal(str)  # emits canonical path when linked

    def __init__(self, part_id: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Attach Datasheet")
        self._part_id = part_id
        self._selected: Optional[str] = None

        layout = QVBoxLayout(self)
        self.drop = DropArea(self)
        layout.addWidget(self.drop)
        self.path_label = QLabel("")
        layout.addWidget(self.path_label)
        btn_row = QHBoxLayout()
        self.choose_btn = QPushButton("Choose File…")
        self.ok_btn = QPushButton("OK")
        self.cancel_btn = QPushButton("Cancel")
        self.ok_btn.setEnabled(False)
        btn_row.addWidget(self.choose_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.drop.fileSelected.connect(self._on_file_selected)
        self.choose_btn.clicked.connect(self._choose_file)
        self.ok_btn.clicked.connect(self._on_ok)
        self.cancel_btn.clicked.connect(self.reject)

    def _on_file_selected(self, path: str) -> None:
        self._selected = path
        self.path_label.setText(path)
        self.ok_btn.setEnabled(path.lower().endswith('.pdf'))

    def _choose_file(self) -> None:  # pragma: no cover - UI glue
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF Files (*.pdf)")
        if path:
            self._on_file_selected(path)

    def _on_ok(self) -> None:  # pragma: no cover - UI glue
        if not self._selected:
            return
        self.setEnabled(False)
        # Fire background worker
        self._thread = QThread(self)
        self._worker = Worker(self._part_id, self._selected)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_finished(self, canonical: str, existed: bool, error: str) -> None:  # pragma: no cover - UI glue
        self.setEnabled(True)
        if error:
            QMessageBox.warning(self, "Attach failed", error)
            return
        # Inform parent to persist or stage the change. We no longer update DB here,
        # so the caller can respect the Apply/Save behavior.
        # If a duplicate existed, we still emitted the canonical existing path.
        self.attached.emit(canonical)
        self.accept()

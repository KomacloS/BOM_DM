from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
import tempfile, os

from PyQt6.QtCore import Qt, pyqtSignal, QObject, QThreadPool, QRunnable
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QProgressBar,
    QHBoxLayout, QPushButton, QCheckBox, QMessageBox
)

from .. import services
from ..services.datasheet_search import search_web, NoSearchProviderConfigured
from ..services.gpt_rerank import choose_best_datasheet_url
from . import state as app_state
import requests


@dataclass
class WorkItem:
    part_id: int
    pn: str
    mfg: str | None
    desc: str | None


class _Signals(QObject):
    rowStatus = pyqtSignal(int, str)
    rowDone = pyqtSignal(int, bool, bool)  # row, attached, duplicate


class _Worker(QRunnable):
    def __init__(self, row: int, wi: WorkItem, auto_link_dupes: bool, sig: _Signals):
        super().__init__()
        self.row = row
        self.wi = wi
        self.auto = auto_link_dupes
        self.sig = sig

    def run(self):
        try:
            self.sig.rowStatus.emit(self.row, "Searching…")
            cands: List[dict] = []
            for q in self._queries(self.wi):
                try:
                    for sr in search_web(q, count=10):
                        cands.append({"title": sr.title, "snippet": sr.snippet, "url": sr.url})
                except Exception:
                    continue
            if not cands:
                self.sig.rowStatus.emit(self.row, "No results")
                self.sig.rowDone.emit(self.row, False, False)
                return
            pdfs = [c for c in cands if c["url"].lower().endswith(".pdf")]
            shortlist = pdfs or cands[:8]
            best = choose_best_datasheet_url(self.wi.pn, self.wi.mfg or "", self.wi.desc or "", shortlist) or None
            if not best:
                best = pdfs[0]["url"] if pdfs else None
            if not best:
                self.sig.rowStatus.emit(self.row, "No candidate")
                self.sig.rowDone.emit(self.row, False, False)
                return
            self.sig.rowStatus.emit(self.row, "Downloading…")
            tmp = self._download_pdf(best)
            if not tmp:
                self.sig.rowStatus.emit(self.row, "Download failed")
                self.sig.rowDone.emit(self.row, False, False)
                return
            with app_state.get_session() as session:
                dst, existed = services.register_datasheet_for_part(session, self.wi.part_id, Path(tmp))
                if existed and not self.auto:
                    self.sig.rowStatus.emit(self.row, "Duplicate (review)")
                    self.sig.rowDone.emit(self.row, False, True)
                else:
                    services.update_part_datasheet_url(session, self.wi.part_id, str(dst))
                    self.sig.rowStatus.emit(self.row, "Attached")
                    self.sig.rowDone.emit(self.row, True, existed)
        except NoSearchProviderConfigured:
            self.sig.rowStatus.emit(self.row, "No search provider configured")
            self.sig.rowDone.emit(self.row, False, False)
        except Exception:
            self.sig.rowStatus.emit(self.row, "Error")
            self.sig.rowDone.emit(self.row, False, False)

    def _queries(self, wi: WorkItem) -> List[str]:
        q: List[str] = []
        m = (wi.mfg or "").strip()
        d = (wi.desc or "").strip()
        q.append(f"{wi.pn} datasheet pdf")
        if m:
            q.append(f"{m} {wi.pn} datasheet")
        q.append(f"{wi.pn} specification filetype:pdf")
        if m:
            q.append(f"{wi.pn} {m} pdf")
        if d:
            q.append(f"{wi.pn} {d} pdf")
        return q

    def _download_pdf(self, url: str) -> Optional[str]:
        with requests.get(url, stream=True, timeout=30) as r:
            if r.status_code != 200:
                return None
            ctype = r.headers.get("Content-Type", "").lower()
            if "pdf" not in ctype and not url.lower().endswith(".pdf"):
                return None
            fd, path = tempfile.mkstemp(suffix=".pdf")
            with os.fdopen(fd, "wb") as f:
                for chunk in r.iter_content(1024 * 64):
                    if chunk:
                        f.write(chunk)
            return path


class AutoDatasheetDialog(QDialog):
    def __init__(self, parent, work: List[WorkItem], on_locked_parts_changed):
        super().__init__(parent)
        self.setWindowTitle("Auto Datasheet")
        self.resize(900, 420)
        self.work = work
        self.on_locked_parts_changed = on_locked_parts_changed
        self.pool = QThreadPool.globalInstance()
        self.sig = _Signals()
        self.sig.rowStatus.connect(self._row_status)
        self.sig.rowDone.connect(self._row_done)

        self.table = QTableWidget(len(work), 5)
        self.table.setHorizontalHeaderLabels(["PN", "Manufacturer", "Description", "Status", "Result"])
        for i, wi in enumerate(work):
            self.table.setItem(i, 0, QTableWidgetItem(wi.pn))
            self.table.setItem(i, 1, QTableWidgetItem(wi.mfg or ""))
            self.table.setItem(i, 2, QTableWidgetItem(wi.desc or ""))
            self.table.setItem(i, 3, QTableWidgetItem("Queued"))
            self.table.setItem(i, 4, QTableWidgetItem(""))

        self.progress = QProgressBar()
        self.progress.setRange(0, len(work))
        self.progress.setValue(0)
        self.auto_dupes = QCheckBox("Auto-link duplicates without asking")
        self.auto_dupes.setChecked(True)
        self.btnStart = QPushButton("Start")
        self.btnCancel = QPushButton("Cancel")

        lo = QVBoxLayout(self)
        lo.addWidget(self.table)
        lo.addWidget(self.progress)
        lo2 = QHBoxLayout()
        lo2.addWidget(self.auto_dupes)
        lo2.addStretch(1)
        lo2.addWidget(self.btnStart)
        lo2.addWidget(self.btnCancel)
        lo.addLayout(lo2)

        self.done = 0
        self.dup_queue: List[int] = []

        self.btnStart.clicked.connect(self._start)
        self.btnCancel.clicked.connect(self.reject)

    def _start(self):
        if self.on_locked_parts_changed:
            self.on_locked_parts_changed({w.part_id for w in self.work}, lock=True)
        self.btnStart.setEnabled(False)
        self.btnCancel.setEnabled(False)
        auto = self.auto_dupes.isChecked()
        for i, wi in enumerate(self.work):
            self.pool.start(_Worker(i, wi, auto, self.sig))

    def _row_status(self, row: int, text: str):
        self.table.item(row, 3).setText(text)

    def _row_done(self, row: int, attached: bool, duplicate: bool):
        self.done += 1
        self.progress.setValue(self.done)
        self.table.item(row, 4).setText("Attached" if attached else ("Duplicate" if duplicate else "—"))
        if duplicate:
            self.dup_queue.append(row)
        if self.done == len(self.work):
            self._finish()

    def _finish(self):
        if self.on_locked_parts_changed:
            self.on_locked_parts_changed({w.part_id for w in self.work}, lock=False)
        if self.dup_queue and not self.auto_dupes.isChecked():
            self._review_duplicates()
        self.accept()

    def _review_duplicates(self):
        ret = QMessageBox.question(
            self,
            "Duplicates found",
            f"{len(self.dup_queue)} duplicates found. Link all to existing file?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret == QMessageBox.StandardButton.Yes:
            pass

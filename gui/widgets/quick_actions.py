"""Quick actions panel."""

from __future__ import annotations

from pathlib import Path

from app import config

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget, QProgressDialog

from ..api_client import BaseClient
from ..util import qt


class _ImportWorker(QThread):
    finished = Signal(object)

    def __init__(self, client: BaseClient, path: str) -> None:
        super().__init__()
        self._client = client
        self._path = path

    def run(self) -> None:  # pragma: no cover - Qt thread
        try:
            with open(self._path, "rb") as f:
                resp = self._client.post(
                    "/bom/import", files={"file": (Path(self._path).name, f)}
                )
        except Exception as exc:  # pragma: no cover - user path / IO errors
            resp = exc
        self.finished.emit(resp)


class QuickActions(QWidget):
    """Common helper tasks like imports and exports."""

    def __init__(self, client: BaseClient) -> None:
        super().__init__()
        self._client = client
        self._import_dialog: QProgressDialog | None = None
        self._import_worker: _ImportWorker | None = None

        layout = QVBoxLayout(self)
        imp_btn = QPushButton("Import BOM…")
        imp_btn.clicked.connect(self.import_bom)
        layout.addWidget(imp_btn)

        seed_btn = QPushButton("Seed Sample Data")
        seed_btn.clicked.connect(self.seed_sample)
        layout.addWidget(seed_btn)

        exp_btn = QPushButton("Open exports folder…")
        exp_btn.clicked.connect(self.export_files)
        layout.addWidget(exp_btn)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    def set_client(self, client: BaseClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    def import_bom(self) -> None:
        path = qt.pick_file(self, "Import BOM", "BOM Files (*.csv *.xlsx)")
        if not path:
            return
        if self._import_worker and self._import_worker.isRunning():
            return
        try:
            Path(path)  # ensure path is valid for error consistency
        except OSError as exc:  # pragma: no cover - path validation
            qt.error(self, "Import", str(exc))
            return

        dlg = QProgressDialog(self)
        dlg.setWindowTitle("Importing BOM")
        dlg.setLabelText("Uploading BOM…")
        dlg.setRange(0, 0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setCancelButton(None)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.show()
        self._import_dialog = dlg

        worker = _ImportWorker(self._client, path)
        worker.finished.connect(self._finish_import)
        worker.finished.connect(worker.deleteLater)
        self._import_worker = worker
        worker.start()

    def _finish_import(self, result: object) -> None:
        if self._import_dialog is not None:
            self._import_dialog.close()
            self._import_dialog.deleteLater()
            self._import_dialog = None
        self._import_worker = None

        if isinstance(result, Exception):
            qt.error(self, "Import", str(result))
            return
        resp = result
        if resp.status_code == 200:
            qt.alert(self, "Import", "Done")
        else:
            qt.error(self, "Import", resp.text)

    # ------------------------------------------------------------------
    def seed_sample(self) -> None:
        tpl = Path("bom_template.csv")
        for candidate in (
            config.APP_STORAGE_ROOT / "bom_template.csv",
            config.DATA_ROOT / "bom_template.csv",
        ):
            if tpl.exists():
                break
            if candidate.exists():
                tpl = candidate
        if not tpl.exists():
            resp = self._client.get("/bom/template")
            if resp.status_code == 200:
                tpl.write_bytes(resp.content)
            else:
                qt.error(self, "Seed", "Template download failed")
                return
        resp = self._client.post("/customers/", json={"name": "Sample"})
        if resp.status_code != 200:
            qt.error(self, "Seed", resp.text)
            return
        cust_id = resp.json().get("id")
        resp = self._client.post(
            "/projects/", json={"name": "Sample Project", "customer_id": cust_id}
        )
        if resp.status_code != 200:
            qt.error(self, "Seed", resp.text)
            return
        project_id = resp.json().get("id")
        with open(tpl, "rb") as f:
            resp = self._client.request(
                "POST",
                "/bom/import",
                files={"file": (tpl.name, f)},
                params={"project_id": project_id},
            )
        if resp.status_code == 200:
            qt.alert(self, "Seed", "Seeded")
        else:
            qt.error(self, "Seed", resp.text)

    # ------------------------------------------------------------------
    def export_files(self) -> None:
        path = qt.save_file(self, "Save BOM CSV", "CSV Files (*.csv)")
        if path:
            resp = self._client.get("/export/bom.csv")
            if resp.status_code == 200:
                Path(path).write_bytes(resp.content)
            else:
                qt.error(self, "Export", resp.text)
        path = qt.save_file(self, "Save Test Results", "Excel Files (*.xlsx)")
        if path:
            resp = self._client.get("/export/testresults.xlsx")
            if resp.status_code == 200:
                Path(path).write_bytes(resp.content)
            else:
                qt.error(self, "Export", resp.text)
        qt.alert(self, "Export", "Done")

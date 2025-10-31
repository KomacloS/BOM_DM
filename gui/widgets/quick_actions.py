"""Quick actions panel."""

from __future__ import annotations

from pathlib import Path

from app import config

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QPushButton,
    QVBoxLayout,
    QWidget,
    QInputDialog,
    QProgressDialog,
)

from ..api_client import BaseClient
from ..util import qt


class QuickActions(QWidget):
    """Common helper tasks like imports and exports."""

    def __init__(self, client: BaseClient) -> None:
        super().__init__()
        self._client = client

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
        # Ask for Assembly ID to import into
        asm_id, ok = QInputDialog.getInt(self, "Import BOM", "Assembly ID:", 1, 1)
        if not ok:
            return

        if self._client.is_local():
            # Run import in-process with progress
            try:
                data = Path(path).read_bytes()
            except OSError as exc:  # pragma: no cover - user path
                qt.error(self, "Import", str(exc))
                return

            dialog = QProgressDialog("Preparing import...", None, 0, 0, self)
            dialog.setWindowTitle("Importing BOM")
            dialog.setAutoClose(True)
            dialog.setCancelButton(None)
            dialog.setMinimumDuration(0)

            class _Worker(QThread):
                progress = Signal(int, int)
                finished_with = Signal(object, object)  # (result, error)

                def __init__(self, assembly_id: int, payload: bytes) -> None:
                    super().__init__()
                    self._assembly_id = assembly_id
                    self._payload = payload

                def run(self) -> None:  # pragma: no cover - Qt thread
                    from app.database import new_session
                    from app.services.bom_import import import_bom as svc_import
                    try:
                        session = new_session()
                        try:
                            def _progress(done: int, total: int) -> None:
                                self.progress.emit(done, total)

                            result = svc_import(self._assembly_id, self._payload, session, progress=_progress)
                        finally:
                            session.close()
                        self.finished_with.emit(result, None)
                    except Exception as exc:
                        self.finished_with.emit(None, exc)

            worker = _Worker(asm_id, data)

            def _on_progress(done: int, total: int) -> None:
                if total <= 0:
                    dialog.setRange(0, 0)
                    dialog.setLabelText("Importing...")
                    return
                if dialog.maximum() != total:
                    dialog.setRange(0, total)
                dialog.setValue(done)
                remaining = max(total - done, 0)
                dialog.setLabelText(f"Loaded {done} of {total}  (left: {remaining})")

            def _on_finished(result, error) -> None:
                dialog.close()
                if error is not None:
                    qt.error(self, "Import", str(error))
                    return
                # Show simple summary
                try:
                    total = getattr(result, "total", None)
                    matched = getattr(result, "matched", None)
                    unmatched = getattr(result, "unmatched", None)
                    errors = getattr(result, "errors", []) or []
                    summary = [
                        f"Total rows: {total}",
                        f"Matched: {matched}",
                        f"Unmatched: {unmatched}",
                    ]
                    if errors:
                        summary.append("Errors: " + ", ".join(errors[:5]))
                    qt.alert(self, "Import", "\n".join(summary))
                except Exception:
                    qt.alert(self, "Import", "Done")

            worker.progress.connect(_on_progress)
            worker.finished_with.connect(_on_finished)
            dialog.show()
            worker.start()
            return

        # HTTP/remote backend: fall back to simple upload with busy indicator
        dialog = QProgressDialog("Uploading...", None, 0, 0, self)
        dialog.setWindowTitle("Importing BOM")
        dialog.setCancelButton(None)
        dialog.setAutoClose(True)
        dialog.setMinimumDuration(0)
        dialog.show()
        try:
            with open(path, "rb") as f:
                resp = self._client.post(
                    f"/assemblies/{asm_id}/bom/import",
                    files={"file": (Path(path).name, f)},
                )
        except OSError as exc:  # pragma: no cover - user path
            dialog.close()
            qt.error(self, "Import", str(exc))
            return
        dialog.close()
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

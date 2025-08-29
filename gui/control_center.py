"""Main window for the debug GUI.

The implementation is intentionally lightweight; it wires together a header
with backend selection and a few debugging panels.  The design is modular so
additional widgets can be added without touching the core window.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:  # Provide a helpful error if PySide6 is missing
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    raise ModuleNotFoundError(
        "PySide6 is required to run the GUI. Install it with"
        " `python -m pip install -e .[full]`."
    ) from exc

from .api_client import HTTPClient, LocalClient, BaseClient
from .widgets.auth_panel import AuthPanel
from .widgets.db_panel import DBPanel
from .widgets.api_playground import APIPlayground
from .widgets.quick_actions import QuickActions
from .widgets.server_panel import ServerPanel
from .util import qt


# ---------------------------------------------------------------------------
# Optional helper to re-exec into the project's virtual environment similar to
# the previous Tk implementation.  This keeps the console entry point working
# when executed from the repository root.

def _reexec_into_venv() -> None:  # pragma: no cover - environment helper
    if os.environ.get("BOM_NO_REEXEC"):
        return
    if sys.prefix == sys.base_prefix:
        root = Path(__file__).resolve().parents[1]
        exe = root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
            "python.exe" if os.name == "nt" else "python"
        )
        if exe.exists():
            os.environ["BOM_NO_REEXEC"] = "1"
            os.execv(str(exe), [str(exe)] + sys.argv)


# ---------------------------------------------------------------------------
class ControlCenter(QMainWindow):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BOM Debug GUI")

        self.client: BaseClient = LocalClient()

        # ------------------------------------------------------------------
        # Header bar with backend selector and token preview
        header = QWidget()
        hb = QHBoxLayout(header)
        hb.addWidget(QLabel("Backend:"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["Local", "HTTP"])
        self.backend_combo.currentTextChanged.connect(self.switch_backend)
        hb.addWidget(self.backend_combo)
        self.base_url_edit = QLineEdit("http://localhost:8000")
        hb.addWidget(self.base_url_edit)
        hb.addStretch(1)
        hb.addWidget(QLabel("Token:"))
        self.token_label = QLabel("<none>")
        hb.addWidget(self.token_label)
        dl_btn = QPushButton("Download BOM template")
        dl_btn.clicked.connect(self.download_template)
        hb.addWidget(dl_btn)

        # ------------------------------------------------------------------
        self.tabs = QTabWidget()
        self.auth_panel = AuthPanel(self.client, self._token_changed)
        self.db_panel = DBPanel(self.client)
        self.playground_panel = APIPlayground(self.client)
        self.quick_panel = QuickActions(self.client)
        self.server_panel = ServerPanel()
        self.tabs.addTab(self.auth_panel, "Auth")
        self.tabs.addTab(self.db_panel, "DB")
        self.tabs.addTab(self.quick_panel, "Quick")
        self.tabs.addTab(self.playground_panel, "API")
        self.tabs.addTab(self.server_panel, "Server")
        self.server_panel.base_url_changed.connect(self.base_url_edit.setText)
        self.server_panel.set_enabled(False)

        central = QWidget()
        v = QVBoxLayout(central)
        v.addWidget(header)
        v.addWidget(self.tabs)
        self.setCentralWidget(central)

    # ------------------------------------------------------------------
    def switch_backend(self, text: str) -> None:
        token = self.client._token
        if text == "Local":
            self.client = LocalClient()
        else:
            self.client = HTTPClient(self.base_url_edit.text() or "http://localhost:8000")
        if token:
            self.client.set_token(token)
        # propagate new client to panels
        self.auth_panel.set_client(self.client)
        self.db_panel.set_client(self.client)
        self.quick_panel.set_client(self.client)
        self.playground_panel.set_client(self.client)
        self.server_panel.set_enabled(text == "HTTP")

    # ------------------------------------------------------------------
    def _token_changed(self, token: str) -> None:
        self.token_label.setText(token[:16] + "…" if token else "<none>")

    # ------------------------------------------------------------------
    def download_template(self) -> None:
        path = qt.save_file(self, "Save BOM template", "CSV Files (*.csv)")
        if not path:
            return
        resp = self.client.get("/bom/template")
        if resp.status_code == 200:
            with open(path, "wb") as f:
                f.write(resp.content)
            qt.alert(self, "Template", "Saved")
        else:
            qt.error(self, "Error", resp.text)

    # ------------------------------------------------------------------
    def closeEvent(self, event):  # pragma: no cover - Qt hook
        if isinstance(self.client, HTTPClient):
            try:
                self.client.close()
            except Exception:
                pass
        try:
            self.server_panel.stop_server()
        except Exception:
            pass
        super().closeEvent(event)


# ---------------------------------------------------------------------------
def main() -> None:  # pragma: no cover - manual entry point
    _reexec_into_venv()
    from app.database import ensure_schema

    ensure_schema()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen" if not os.environ.get("DISPLAY") else "")
    app = QApplication(sys.argv)
    win = ControlCenter()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover
    main()

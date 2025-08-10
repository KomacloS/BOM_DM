"""Minimal HTTP playground widget."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QComboBox,
    QPushButton,
    QTextEdit,
    QLabel,
)

from ..api_client import BaseClient
from ..util import qt


class APIPlayground(QWidget):
    """Tiny playground to issue ad-hoc requests."""

    def __init__(self, client: BaseClient) -> None:
        super().__init__()
        self._client = client

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.method = QComboBox()
        self.method.addItems(["GET", "POST", "PATCH", "DELETE"])
        top.addWidget(self.method)
        self.path_edit = QLineEdit("/")
        top.addWidget(self.path_edit)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self.send)
        top.addWidget(send_btn)
        layout.addLayout(top)

        file_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        file_btn = QPushButton("Choose file")
        file_btn.clicked.connect(self.choose_file)
        file_row.addWidget(self.file_edit)
        file_row.addWidget(file_btn)
        layout.addLayout(file_row)

        self.request_body = QTextEdit()
        self.request_body.setPlaceholderText("JSON body")
        layout.addWidget(self.request_body)

        layout.addWidget(QLabel("Response:"))
        self.response_view = QTextEdit()
        self.response_view.setReadOnly(True)
        layout.addWidget(self.response_view)

    # ------------------------------------------------------------------
    def set_client(self, client: BaseClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    def send(self) -> None:
        path = self.path_edit.text()
        method = self.method.currentText()
        files = None
        json = None
        file_path = self.file_edit.text().strip()
        if file_path:
            try:
                f = open(file_path, "rb")
                files = {"file": (file_path.split("/")[-1], f)}
            except OSError as exc:  # pragma: no cover - user input
                qt.error(self, "File", str(exc))
                return
        else:
            body = self.request_body.toPlainText().strip()
            if body:
                try:
                    import json as _json

                    json = _json.loads(body)
                except Exception as exc:  # pragma: no cover - user input
                    qt.error(self, "Invalid JSON", str(exc))
                    return
        try:
            resp = self._client.request(method, path, json=json, files=files)
        finally:
            if files:
                files["file"][1].close()
        self.response_view.setPlainText(
            f"Status: {resp.status_code}\n{resp.text}"
        )

    # ------------------------------------------------------------------
    def choose_file(self) -> None:
        path = qt.pick_file(self, "Choose file")
        if path:
            self.file_edit.setText(path)

"""Embedded uvicorn server controller."""

from __future__ import annotations

import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QGuiApplication


class ServerPanel(QWidget):
    """Start/stop a uvicorn subprocess and show logs."""

    base_url_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.proc: QProcess | None = None

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Host:"))
        self.host_edit = QLineEdit("127.0.0.1")
        row.addWidget(self.host_edit)
        row.addWidget(QLabel("Port:"))
        self.port_edit = QLineEdit("8000")
        row.addWidget(self.port_edit)
        row.addWidget(QLabel("Env:"))
        self.env_combo = QComboBox()
        self.env_combo.addItems(["prod", "dev"])
        row.addWidget(self.env_combo)
        copy_btn = QPushButton("Copy base URL")
        copy_btn.clicked.connect(self.copy_url)
        row.addWidget(copy_btn)
        layout.addLayout(row)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start_server)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_server)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        layout.addLayout(btn_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

    # ------------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self.start_btn.setEnabled(enabled and self.proc is None)
        if not enabled:
            self.stop_server()

    # ------------------------------------------------------------------
    def start_server(self) -> None:
        if self.proc is not None:
            return
        host = self.host_edit.text() or "127.0.0.1"
        port = self.port_edit.text() or "8000"
        env = QProcessEnvironment.systemEnvironment()
        env.insert("BOM_ENV", self.env_combo.currentText())
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyRead.connect(self._read_output)
        self.proc.start(
            sys.executable,
            ["-m", "uvicorn", "app.main:app", "--host", host, "--port", port]
        )
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.base_url_changed.emit(f"http://{host}:{port}")

    # ------------------------------------------------------------------
    def stop_server(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        self.proc.waitForFinished(3000)
        if self.proc.state() != QProcess.NotRunning:
            self.proc.kill()
            self.proc.waitForFinished(1000)
        self.proc = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    def _read_output(self) -> None:  # pragma: no cover - Qt callback
        if self.proc:
            data = bytes(self.proc.readAll()).decode("utf-8", "ignore")
            self.log_view.appendPlainText(data.rstrip())

    # ------------------------------------------------------------------
    def copy_url(self) -> None:
        url = f"http://{self.host_edit.text() or '127.0.0.1'}:{self.port_edit.text() or '8000'}"
        QGuiApplication.clipboard().setText(url)

    # ------------------------------------------------------------------
    def close(self) -> None:  # pragma: no cover - cleanup
        self.stop_server()
        super().close()

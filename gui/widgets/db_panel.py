"""Database configuration panel."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QLabel,
)
from sqlalchemy import text

from ..api_client import BaseClient
from ..util import qt


class DBPanel(QWidget):
    """Display and modify the ``DATABASE_URL`` setting."""

    def __init__(self, client: BaseClient) -> None:
        super().__init__()
        self._client = client

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Database URL:"))
        self.url_edit = QLineEdit()
        row.addWidget(self.url_edit)
        layout.addLayout(row)

        btns = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save)
        btns.addWidget(self.save_btn)
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self.reload)
        btns.addWidget(reload_btn)
        test_btn = QPushButton("Test")
        test_btn.clicked.connect(self.test)
        btns.addWidget(test_btn)
        layout.addLayout(btns)
        self.status = QLabel("")
        layout.addWidget(self.status)

        self.reload()

    # ------------------------------------------------------------------
    def set_client(self, client: BaseClient) -> None:
        self._client = client
        self.reload()

    # ------------------------------------------------------------------
    def reload(self) -> None:
        if self._client.is_local():
            from app import config

            self.url_edit.setReadOnly(False)
            self.save_btn.setEnabled(True)
            self.url_edit.setText(config.DATABASE_URL)
        else:
            # HTTP backend is read-only; just display the base URL if available
            base = getattr(self._client, "base_url", self.url_edit.text())
            self.url_edit.setText(base)
            self.url_edit.setReadOnly(True)
            self.save_btn.setEnabled(False)
        self.status.setText("")

    # ------------------------------------------------------------------
    def save(self) -> None:
        new_url = self.url_edit.text()
        if self._client.is_local():
            from app import config

            config.save_database_url(new_url)
            config.reload_settings()
            qt.alert(self, "DB", "Saved")
        else:
            resp = self._client.post("/ui/settings", json={"database_url": new_url})
            if resp.status_code == 200:
                qt.alert(self, "DB", "Saved")
            else:
                qt.error(self, "Error", resp.text)

    # ------------------------------------------------------------------
    def test(self) -> None:
        if self._client.is_local():
            try:
                from app import config
                from sqlmodel import Session

                with Session(config.engine) as sess:
                    sess.exec(text("SELECT 1"))
                self.status.setStyleSheet("color: green")
                self.status.setText("OK")
            except Exception as exc:  # pragma: no cover - debug helper
                self.status.setStyleSheet("color: red")
                self.status.setText(str(exc))
        else:
            resp = self._client.get("/health")
            if resp.status_code == 200:
                self.status.setStyleSheet("color: green")
                self.status.setText("OK")
            else:
                self.status.setStyleSheet("color: red")
                self.status.setText(resp.text)

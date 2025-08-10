"""Authentication panel."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QHBoxLayout,
)

from ..api_client import BaseClient
from ..util import qt


class AuthPanel(QWidget):
    """Simple username/password login widget."""

    def __init__(self, client: BaseClient, login_cb: Optional[Callable[[str], None]] = None) -> None:
        super().__init__()
        self._client = client
        self._login_cb = login_cb

        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("username")
        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("password")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addWidget(self.user_edit)
        form.addWidget(self.pass_edit)
        layout.addLayout(form)

        btns = QHBoxLayout()
        login_btn = QPushButton("Login")
        login_btn.clicked.connect(self.login)
        btns.addWidget(login_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear_token)
        btns.addWidget(clear_btn)
        me_btn = QPushButton("Who am I?")
        me_btn.clicked.connect(self.show_me)
        btns.addWidget(me_btn)
        layout.addLayout(btns)

        self.token_label = QLabel("<no token>")
        layout.addWidget(self.token_label)

    # ------------------------------------------------------------------
    def set_client(self, client: BaseClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    def login(self) -> None:
        resp = self._client.post(
            "/auth/token",
            data={"username": self.user_edit.text(), "password": self.pass_edit.text()},
        )
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            self._client.set_token(token)
            self.token_label.setText(token[:16] + "…")
            if self._login_cb:
                self._login_cb(token)
        else:
            qt.error(self, "Login failed", resp.text)

    # ------------------------------------------------------------------
    def clear_token(self) -> None:
        self._client.set_token(None)
        self.token_label.setText("<no token>")
        if self._login_cb:
            self._login_cb("")

    # ------------------------------------------------------------------
    def show_me(self) -> None:
        resp = self._client.get("/auth/me")
        if resp.status_code == 200:
            data = resp.json()
            qt.alert(self, "Me", f"{data.get('username')} ({data.get('role')})")
        else:
            qt.error(self, "Error", resp.text)

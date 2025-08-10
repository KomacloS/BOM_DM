"""Small Qt helper utilities used across widgets."""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget


def alert(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def error(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)


def confirm(parent: QWidget, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


def pick_file(parent: QWidget, caption: str, filt: str = "*") -> str:
    return QFileDialog.getOpenFileName(parent, caption, filter=filt)[0]


def save_file(parent: QWidget, caption: str, filt: str = "*") -> str:
    return QFileDialog.getSaveFileName(parent, caption, filter=filt)[0]

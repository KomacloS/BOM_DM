from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton


class TestMethodStubDialog(QDialog):
    """Simple placeholder dialog for unimplemented test method actions."""

    def __init__(self, message: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Not implemented")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(message))
        ok = QPushButton("OK")
        ok.clicked.connect(self.accept)
        layout.addWidget(ok)

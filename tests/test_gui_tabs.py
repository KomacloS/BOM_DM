import os
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:  # pragma: no cover - environment dependent
    from PySide6 import QtWidgets  # type: ignore
except Exception:  # pragma: no cover - skip when Qt is missing
    pytest.skip("PySide6 not available", allow_module_level=True)

from gui.widgets.auth_panel import AuthPanel
from gui.widgets.db_panel import DBPanel
from gui.widgets.quick_actions import QuickActions
from gui.widgets.server_panel import ServerPanel


class DummyClient:
    def __init__(self):
        self._token = None

    def set_token(self, token):
        self._token = token

    def get(self, path, params=None):
        if path == "/ui/settings":
            return types.SimpleNamespace(status_code=200, json=lambda: {"database_url": "sqlite://"})
        if path == "/auth/me":
            return types.SimpleNamespace(status_code=200, json=lambda: {"username": "u", "role": "r"})
        return types.SimpleNamespace(status_code=200, json=lambda: {})

    def post(self, path, json=None, files=None):
        if path == "/auth/token":
            return types.SimpleNamespace(status_code=200, json=lambda: {"access_token": "t"})
        return types.SimpleNamespace(status_code=200, json=lambda: {})

    def is_local(self):
        return True


panels = [
    lambda: AuthPanel(DummyClient()),
    lambda: DBPanel(DummyClient()),
    lambda: QuickActions(DummyClient()),
    lambda: ServerPanel(),
]


@pytest.mark.parametrize("factory", panels)
def test_panel_classes_instantiation(factory):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = factory()
    assert isinstance(w, QtWidgets.QWidget)
    w.close()
    app.quit()

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:  # pragma: no cover - environment dependent
    from PySide6 import QtWidgets  # type: ignore
except Exception:  # pragma: no cover - skip when Qt is missing
    pytest.skip("PySide6 not available", allow_module_level=True)

from gui import control_center


def test_build_ui_widgets():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ui = control_center.ControlCenter()
    assert ui.tabs.count() >= 1
    ui.close()
    app.quit()

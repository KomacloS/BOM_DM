import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from gui import control_center


def test_build_ui_widgets():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ui = control_center.ControlCenter()
    assert ui.tabs.count() >= 1
    ui.close()
    app.quit()

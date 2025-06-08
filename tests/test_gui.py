# root: tests/test_gui.py
import os
import tkinter as tk
import pytest

pytest.importorskip('tkinter')

if not os.environ.get('DISPLAY'):
    pytest.skip('GUI tests require a display', allow_module_level=True)

from gui import control_center


def test_build_ui_widgets():
    root = tk.Tk()
    try:
        ui = control_center.build_ui(root)
        widgets = root.winfo_children()
        assert widgets, 'no widgets created'
    finally:
        root.destroy()

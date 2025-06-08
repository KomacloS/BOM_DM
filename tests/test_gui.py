# root: tests/test_gui.py
import os
import tkinter as tk
import pytest

pytest.importorskip('tkinter')

if os.environ.get('CI'):
    pytest.skip('GUI tests skipped on CI', allow_module_level=True)

from gui import control_center


def test_build_ui_widgets():
    root = tk.Tk()
    try:
        ui = control_center.build_ui(root)
        widgets = root.winfo_children()
        assert widgets, 'no widgets created'
    finally:
        root.destroy()

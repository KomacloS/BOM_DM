import types
import sys
import os
import importlib
from pathlib import Path

import pytest


def stub_tk_module():
    class Widget:
        def __init__(self, *a, **k):
            self.tk = self
            self.children = {}
            self._data = {}
            self._data.update(k)
        def pack(self, *a, **k):
            pass
        def grid(self, *a, **k):
            pass
        def insert(self, *a, **k):
            pass
        def configure(self, *a, **k):
            pass
        def destroy(self):
            pass
        def winfo_children(self):
            return []
        def heading(self, *a, **k):
            pass
        def get_children(self):
            return []
        def __getitem__(self, key):
            return self._data.get(key)
        def __setitem__(self, key, value):
            self._data[key] = value

    class Root(Widget):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._last_child_ids = {}
            self._w = '.'

    class StringVar:
        def __init__(self, *a, **k):
            self.value = k.get('value')

    stub = types.SimpleNamespace(
        Tk=Root,
        Frame=Widget,
        Misc=Widget,
        Label=Widget,
        Button=Widget,
        Entry=Widget,
        LabelFrame=Widget,
        Radiobutton=Widget,
        Text=Widget,
        StringVar=StringVar,
        Toplevel=Widget,
        messagebox=types.SimpleNamespace(showinfo=lambda *a, **k: None, showerror=lambda *a, **k: None, askyesno=lambda *a, **k: True),
        filedialog=types.SimpleNamespace(askopenfilename=lambda *a, **k: '', asksaveasfilename=lambda *a, **k: ''),
    )
    stub.scrolledtext = types.SimpleNamespace(ScrolledText=Widget)
    stub.ttk = types.SimpleNamespace(Notebook=Widget, Treeview=Widget)
    return stub


def import_gui_with_stub(monkeypatch):
    monkeypatch.setenv('BOM_NO_REEXEC', '1')
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    tk_stub = stub_tk_module()
    monkeypatch.setitem(sys.modules, 'tkinter', tk_stub)
    monkeypatch.setitem(sys.modules, 'tkinter.ttk', tk_stub.ttk)
    monkeypatch.setitem(sys.modules, 'tkinter.scrolledtext', tk_stub.scrolledtext)
    import importlib
    cc = importlib.reload(importlib.import_module('gui.control_center'))
    monkeypatch.setattr(cc, 'requests', types.SimpleNamespace(get=lambda *a, **k: types.SimpleNamespace(status_code=200, json=lambda: []), post=lambda *a, **k: types.SimpleNamespace(status_code=200, json=lambda: {})))
    cc.BOMItemsTab.refresh = lambda self: None
    cc.TestResultsTab.refresh = lambda self: None
    cc.QuoteTab.refresh = lambda self: None
    cc.TraceabilityTab.query_board = lambda self: None
    cc.TraceabilityTab.query_component = lambda self: None
    cc.TOKEN = "test"
    return cc


def test_tab_classes_instantiation(monkeypatch):
    cc = import_gui_with_stub(monkeypatch)
    root = cc.tk.Tk()
    for cls in [
        cc.ServerTab,
        cc.BOMItemsTab,
        cc.ImportPDFTab,
        cc.QuoteTab,
        cc.TestResultsTab,
        cc.TraceabilityTab,
        cc.ExportTab,
        cc.UsersTab,
        cc.SettingsTab,
    ]:
        cls(root)


def test_autovenv_reexec(monkeypatch, tmp_path):
    cc = import_gui_with_stub(monkeypatch)
    monkeypatch.setattr(sys, 'prefix', '/usr')
    monkeypatch.setattr(sys, 'base_prefix', '/usr')
    vpy = Path('.venv') / ('Scripts' if os.name == 'nt' else 'bin') / ('python.exe' if os.name == 'nt' else 'python')
    vpy.parent.mkdir(parents=True, exist_ok=True)
    vpy.write_text('')
    called = {}
    monkeypatch.setattr(os, 'execv', lambda exe, args: called.setdefault('exe', exe))
    monkeypatch.delenv('BOM_NO_REEXEC', raising=False)
    cc._reexec_into_venv()
    assert called['exe'] == str(vpy.resolve())


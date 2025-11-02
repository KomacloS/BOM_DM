import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication

from app.gui.bom_editor_pane import BOMEditorPane
from app.logic import prefix_macros


class DummyPane(BOMEditorPane):
    def __init__(self):
        super().__init__(assembly_id=1)

    def _rebuild_model(self):
        pass


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_longest_match(qapp):
    pane = DummyPane()
    assert pane._macro_for_reference("LED12") == "LED"
    assert pane._macro_for_reference("L5") == "INDUCTANCE"


def test_macro_canonicalization(tmp_path, monkeypatch, qapp):
    mapping = tmp_path / "prefix_macros.txt"
    mapping.write_text("LED\tled\n", encoding="utf-8")
    monkeypatch.setattr(prefix_macros, "_candidate_paths", lambda: [mapping])
    prefix_macros.reload_prefix_macros()
    pane = DummyPane()
    pane._prefix_macros = prefix_macros.load_prefix_macros()
    assert pane._macro_for_reference("LED1") == "LED"

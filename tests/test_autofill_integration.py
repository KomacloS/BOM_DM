from app.logic.autofill_rules import infer_from_pn_and_desc
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QStandardItem
import pytest
from app.gui.bom_editor_pane import BOMEditorPane, PartIdRole


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


def test_autofill_only_fills_empty():
    rows = [
        {
            "pn": "C0603C104K5RAC",
            "desc": "CHIP CAP 0.1UF 10% 0603",
            "package": "",
            "value": "",
            "tol_pos": "",
            "tol_neg": "",
        },
        {
            "pn": "R0603-10K",
            "desc": "RES 10K 1%",
            "package": "0603",
            "value": "1k",
            "tol_pos": "",
            "tol_neg": "",
        },
    ]

    for row in rows:
        res = infer_from_pn_and_desc(row["pn"], row["desc"])
        if res.package and not row["package"]:
            row["package"] = res.package
        if res.value and not row["value"]:
            row["value"] = res.value
        if res.tol_pos and res.tol_neg and not row["tol_pos"] and not row["tol_neg"]:
            row["tol_pos"] = res.tol_pos
            row["tol_neg"] = res.tol_neg

    assert rows[0]["package"] == "0603" and rows[0]["value"] == "0.1uF"
    assert rows[0]["tol_pos"] == "10" and rows[0]["tol_neg"] == "10"
    # Second row retains existing non-empty fields
    assert rows[1]["package"] == "0603"
    assert rows[1]["value"] == "1k"


def _setup_pane_with_cols(pane: BOMEditorPane):
    pane._col_indices = {
        "pn": 0,
        "desc": 1,
        "ref": 2,
        "test_method": 3,
        "test_detail": 4,
        "package": 5,
        "value": 6,
        "tol_p": 7,
        "tol_n": 8,
    }
    pane.model.clear()
    pane.model.setColumnCount(9)


def test_macro_autofill_by_ref(qapp):
    pane = DummyPane()
    _setup_pane_with_cols(pane)
    items = [
        QStandardItem("pn"),
        QStandardItem("desc"),
        QStandardItem("Q5"),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
    ]
    items[0].setData(1, PartIdRole)
    pane.model.appendRow(items)
    items2 = [
        QStandardItem("pn"),
        QStandardItem("desc"),
        QStandardItem("Q7"),
        QStandardItem("Existing"),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
    ]
    items2[0].setData(2, PartIdRole)
    pane.model.appendRow(items2)
    pane._view_mode = "by_ref"
    pane._autofill_fields()
    assert pane.model.item(0, 3).text() == "Macro"
    assert pane.model.item(0, 4).text() == "TRANSISTOR"
    assert pane.model.item(1, 3).text() == "Existing"


def test_macro_autofill_by_pn(qapp):
    pane = DummyPane()
    _setup_pane_with_cols(pane)
    items = [
        QStandardItem("pn"),
        QStandardItem("desc"),
        QStandardItem("R1, R2, R5"),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
        QStandardItem(""),
    ]
    items[0].setData(3, PartIdRole)
    pane.model.appendRow(items)
    pane._view_mode = "by_pn"
    pane._autofill_fields()
    assert pane.model.item(0, 3).text() == "Macro"
    assert pane.model.item(0, 4).text() == "RESISTOR"

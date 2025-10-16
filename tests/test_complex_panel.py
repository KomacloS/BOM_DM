import os

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:
    pytest.skip("PyQt6 not available", allow_module_level=True)

from app.domain.complex_linker import CreateComplexOutcome
from app.gui.widgets.complex_panel import ComplexPanel


def _settings_stub():
    return {
        "ui_enabled": True,
        "bridge": {
            "enabled": True,
            "base_url": "http://bridge.local",
            "auth_token": "token",
            "request_timeout_seconds": 5,
        },
        "note_or_link": "",
    }


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_complex_panel_search_and_attach(monkeypatch, qapp):
    monkeypatch.setattr(
        "app.gui.widgets.complex_panel.get_complex_editor_settings",
        lambda: _settings_stub(),
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.ensure_ce_bridge_ready",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.is_preflight_recent",
        lambda max_age_s=5.0: True,
    )

    link_snapshot = {}

    def fake_load(self, part_id):
        return link_snapshot or None

    def fake_attach(part_id, ce_id):
        link_snapshot.clear()
        link_snapshot.update(
            {
                "part_id": part_id,
                "ce_complex_id": ce_id,
                "ce_db_uri": "C:/linked.mdb",
                "synced_at": "2024-04-01T00:00:00",
            }
        )

    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", fake_load)
    monkeypatch.setattr("app.domain.complex_linker.attach_existing_complex", fake_attach)
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.search_complexes",
        lambda pn, limit=20: [
            {"id": "ce-1", "pn": pn, "aliases": ["alt"], "db_path": "C:/linked.mdb"}
        ],
    )

    panel = ComplexPanel()
    panel.set_context(42, "PN123")
    panel.search_edit.setText("PN123")

    panel._on_search_clicked()
    qapp.processEvents()
    assert panel.results_list.count() == 1
    panel._on_attach_clicked()
    qapp.processEvents()

    assert panel.linked_id_value.text() == "ce-1"
    assert panel.db_path_value.text() == "C:/linked.mdb"
    assert panel.synced_value.text() == "2024-04-01T00:00:00"
    assert panel.refresh_button.isEnabled()

    panel.deleteLater()


def test_complex_panel_create_updates_ui(monkeypatch, qapp):
    monkeypatch.setattr(
        "app.gui.widgets.complex_panel.get_complex_editor_settings",
        lambda: _settings_stub(),
    )
    link_snapshot: dict[str, str] = {}

    def fake_load(self, part_id):
        return link_snapshot or None

    def fake_create(part_id, pn, aliases):
        assert part_id == 99
        assert pn == "PN-CREATE"
        link_snapshot.clear()
        link_snapshot.update(
            {
                "ce_complex_id": "55",
                "ce_db_uri": "C:/ce55.mdb",
                "synced_at": "2024-05-01T00:00:00",
            }
        )
        return CreateComplexOutcome(
            status="attached",
            message="Created Complex 55 and attached to part 99.",
            created_id="55",
        )

    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", fake_load)
    monkeypatch.setattr(
        "app.domain.complex_linker.create_and_attach_complex", fake_create
    )

    panel = ComplexPanel()
    panel.set_context(99, "PN-CREATE")
    panel._on_create_clicked()
    qapp.processEvents()

    assert panel.linked_id_value.text() == "55"
    assert panel.db_path_value.text() == "C:/ce55.mdb"
    assert panel.synced_value.text() == "2024-05-01T00:00:00"
    assert not panel.progress.isVisible()
    assert panel.status_label.text() == "Created Complex 55 and attached to part 99."

    panel.deleteLater()


def test_complex_panel_create_cancelled(monkeypatch, qapp):
    monkeypatch.setattr(
        "app.gui.widgets.complex_panel.get_complex_editor_settings",
        lambda: _settings_stub(),
    )
    link_snapshot: dict[str, str] = {}

    def fake_load(self, part_id):
        return link_snapshot or None

    def fake_create(part_id, pn, aliases):
        return CreateComplexOutcome(status="cancelled", message="Creation cancelled.")

    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", fake_load)
    monkeypatch.setattr(
        "app.domain.complex_linker.create_and_attach_complex", fake_create
    )

    panel = ComplexPanel()
    panel.set_context(77, "PN-CAN")
    panel._on_create_clicked()
    qapp.processEvents()

    assert panel.linked_id_value.text() == "-"
    assert panel.status_label.text() == "Creation cancelled."
    assert not panel.progress.isVisible()

    panel.deleteLater()

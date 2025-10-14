import os

import pytest
from PyQt6.QtWidgets import QApplication

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
                "ce_pn": "PN123",
                "total_pins": 42,
                "synced_at": "2024-04-01T00:00:00",
            }
        )
        return dict(link_snapshot)

    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", fake_load)
    monkeypatch.setattr("app.domain.complex_linker.attach_existing_complex", fake_attach)
    monkeypatch.setattr(
        "app.domain.complex_linker.check_link_stale",
        lambda *args, **kwargs: (False, ""),
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.search_complexes",
        lambda pn, limit=20: [
            {"id": "ce-1", "pn": pn, "aliases": ["alt"], "db_path": "C:/linked.mdb"}
        ],
    )

    panel = ComplexPanel()
    panel.show()
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
    assert panel.open_button.isEnabled()
    assert panel.status_label.text() == "Linked to CE #ce-1 from C:/linked.mdb"

    panel.deleteLater()


def test_complex_panel_unlink_clears_link(monkeypatch, qapp):
    monkeypatch.setattr(
        "app.gui.widgets.complex_panel.get_complex_editor_settings",
        lambda: _settings_stub(),
    )
    monkeypatch.setattr(
        "app.domain.complex_linker.check_link_stale",
        lambda *args, **kwargs: (False, ""),
    )

    link_snapshot = {
        "part_id": 7,
        "ce_complex_id": "ce-7",
        "ce_db_uri": "C:/existing.mdb",
        "ce_pn": "PN7",
        "total_pins": 12,
        "synced_at": "2024-01-01T00:00:00",
    }

    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", lambda self, pid: dict(link_snapshot))

    unlink_calls = []

    def fake_unlink(part_id, user_initiated=True):
        unlink_calls.append((part_id, user_initiated))
        return True

    monkeypatch.setattr("app.domain.complex_linker.unlink_existing_complex", fake_unlink)

    panel = ComplexPanel()
    panel.show()
    panel.set_context(7, "PN7")
    qapp.processEvents()

    assert panel.unlink_button.isVisible()

    panel._perform_unlink(confirm=False, user_initiated=True)
    qapp.processEvents()

    assert unlink_calls == [(7, True)]
    assert panel.linked_id_value.text() == "-"
    assert not panel.unlink_button.isVisible()
    assert "Not linked" in panel.status_label.text()

    panel.deleteLater()


def test_complex_panel_stale_link_cleanup(monkeypatch, qapp):
    monkeypatch.setattr(
        "app.gui.widgets.complex_panel.get_complex_editor_settings",
        lambda: _settings_stub(),
    )

    monkeypatch.setattr(
        "app.domain.complex_linker.check_link_stale",
        lambda *args, **kwargs: (True, "not_found"),
    )

    link_snapshot = {
        "part_id": 5,
        "ce_complex_id": "ce-missing",
        "ce_db_uri": "C:/missing.mdb",
        "ce_pn": "PN-MISS",
        "total_pins": 0,
        "synced_at": "2024-02-02T00:00:00",
    }

    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", lambda self, pid: dict(link_snapshot))

    unlink_calls = []
    monkeypatch.setattr(
        "app.domain.complex_linker.unlink_existing_complex",
        lambda part_id, user_initiated=True: unlink_calls.append((part_id, user_initiated)) or True,
    )

    panel = ComplexPanel()
    panel.show()
    panel.set_context(5, "PN-MISS")
    qapp.processEvents()

    assert panel.link_warning.isVisible()
    assert "Clean up link" in panel.link_warning.text()

    panel._on_link_warning_activated("cleanup")
    qapp.processEvents()

    assert unlink_calls == [(5, True)]
    assert panel.linked_id_value.text() == "-"
    assert not panel.link_warning.isVisible()

    panel.deleteLater()

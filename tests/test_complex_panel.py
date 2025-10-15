import os

from pathlib import Path
import pytest

TMP_SETTINGS = Path("tests/_tmp_settings.toml")
if not TMP_SETTINGS.exists():
    TMP_SETTINGS.write_text('[database]\nurl="sqlite:///:memory:"\n')

os.environ.setdefault("BOM_SETTINGS_PATH", str(TMP_SETTINGS.resolve()))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from PyQt6.QtWidgets import QApplication

from app.gui.widgets.complex_panel import ComplexPanel

from app.domain import complex_linker
from app.domain.complex_linker import OpenInCEResult

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
    monkeypatch.setattr("app.domain.complex_linker.record_bridge_action", lambda msg: None)
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
    monkeypatch.setattr("app.domain.complex_linker.record_bridge_action", lambda msg: None)
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
    monkeypatch.setattr("app.domain.complex_linker.record_bridge_action", lambda msg: None)
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

def test_complex_panel_add_and_link_flow(monkeypatch, qapp):
    monkeypatch.setattr(
        "app.gui.widgets.complex_panel.get_complex_editor_settings",
        lambda: _settings_stub(),
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.ensure_ce_bridge_ready",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.domain.complex_linker.record_bridge_action",
        lambda msg: None,
    )
    monkeypatch.setattr(
        "app.domain.complex_linker.check_link_stale",
        lambda *args, **kwargs: (False, ""),
    )

    link_snapshot = {
        "part_id": 11,
        "ce_complex_id": "ce-1",
        "ce_db_uri": "C:/linked.mdb",
        "ce_pn": "PN123",
        "total_pins": 42,
        "synced_at": "2024-04-01T00:00:00",
    }

    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", lambda self, pid: dict(link_snapshot))

    alias_calls = []

    def fake_add_and_link(part_id, pn, ce_id, status_callback=None):
        alias_calls.append((part_id, pn, ce_id))
        return dict(link_snapshot, ce_complex_id=ce_id)

    monkeypatch.setattr(
        "app.domain.complex_linker.attach_as_alias_and_link",
        fake_add_and_link,
    )

    monkeypatch.setattr(
        "app.integration.ce_bridge_client.search_complexes",
        lambda pn, limit=20: [
            {"id": "ce-1", "pn": pn, "aliases": [pn], "db_path": "C:/linked.mdb"}
        ],
    )

    panel = ComplexPanel()
    panel.show()
    panel.set_context(11, "PN123")
    panel.search_edit.setText("PN123")

    panel._on_search_clicked()
    qapp.processEvents()
    panel._on_add_and_link_clicked()
    qapp.processEvents()

    assert alias_calls == [(11, "PN123", "ce-1")]
    assert panel.linked_id_value.text() == "ce-1"
    assert panel.status_label.text() == "Linked to CE #ce-1 from C:/linked.mdb"

    panel.deleteLater()


def test_complex_panel_alias_prompt(monkeypatch, qapp):
    monkeypatch.setattr(
        "app.gui.widgets.complex_panel.get_complex_editor_settings",
        lambda: _settings_stub(),
    )
    monkeypatch.setattr(
        "app.domain.complex_linker.record_bridge_action",
        lambda msg: None,
    )
    monkeypatch.setattr(
        "app.domain.complex_linker.check_link_stale",
        lambda *args, **kwargs: (False, ""),
    )

    link_snapshot = {
        "part_id": 15,
        "ce_complex_id": "ce-2",
        "ce_db_uri": "C:/alias.mdb",
        "ce_pn": "OTHER",
        "total_pins": 10,
        "synced_at": "2024-05-01T00:00:00",
    }

    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", lambda self, pid: dict(link_snapshot))

    alias_calls = []

    def fake_add_and_link(part_id, pn, ce_id, status_callback=None):
        alias_calls.append((part_id, pn, ce_id))
        return dict(link_snapshot, ce_complex_id=ce_id)

    monkeypatch.setattr(
        "app.domain.complex_linker.attach_as_alias_and_link",
        fake_add_and_link,
    )

    monkeypatch.setattr(
        "app.integration.ce_bridge_client.search_complexes",
        lambda pn, limit=20: [
            {"id": "ce-2", "pn": "OTHER", "aliases": [pn], "db_path": "C:/alias.mdb"}
        ],
    )

    panel = ComplexPanel()
    panel.show()
    panel.set_context(15, "PNALIAS")
    panel.search_edit.setText("PNALIAS")

    panel._on_search_clicked()
    qapp.processEvents()

    assert panel.alias_prompt.isVisible()
    panel._on_alias_prompt_add_clicked()
    qapp.processEvents()

    assert alias_calls == [(15, "PNALIAS", "ce-2")]
    assert panel.linked_id_value.text() == "ce-2"

    panel.deleteLater()
def test_complex_panel_open_linked(monkeypatch, qapp):
    monkeypatch.setattr("app.gui.widgets.complex_panel.get_complex_editor_settings", lambda: _settings_stub())
    monkeypatch.setattr("app.domain.complex_linker.record_bridge_action", lambda msg: None)
    link_snapshot = {
        "part_id": 21,
        "ce_complex_id": "ce-21",
        "ce_db_uri": "C:/linked.mdb",
        "ce_pn": "PN21",
        "total_pins": 9,
        "synced_at": "2024-04-01T00:00:00",
    }
    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", lambda self, pid: dict(link_snapshot))
    open_calls = []
    def _open_in_ce(context, status_callback=None, chooser=None, use_cached_preflight=True):
        open_calls.append((context, use_cached_preflight))
        return OpenInCEResult(ce_id="ce-21", already_open=False)
    monkeypatch.setattr("app.domain.complex_linker.open_in_ce", _open_in_ce)
    panel = ComplexPanel()
    panel.show()
    panel.set_context(21, "PN21")
    qapp.processEvents()
    panel._on_open_ce_clicked()
    qapp.processEvents()
    assert open_calls
    assert panel.status_label.text() == "Opened in Complex Editor."
    panel.deleteLater()


def test_complex_panel_open_stale(monkeypatch, qapp):
    monkeypatch.setattr("app.gui.widgets.complex_panel.get_complex_editor_settings", lambda: _settings_stub())
    monkeypatch.setattr("app.domain.complex_linker.record_bridge_action", lambda msg: None)
    link_snapshot = {
        "part_id": 22,
        "ce_complex_id": "ce-22",
        "ce_db_uri": "C:/missing.mdb",
        "ce_pn": "PN22",
        "total_pins": 5,
        "synced_at": "2024-04-01T00:00:00",
    }
    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", lambda self, pid: dict(link_snapshot))
    monkeypatch.setattr(
        "app.domain.complex_linker.open_in_ce",
        lambda *args, **kwargs: (_ for _ in ()).throw(complex_linker.CEStaleLinkError("ce-22")),
    )
    panel = ComplexPanel()
    panel.show()
    panel.set_context(22, "PN22")
    qapp.processEvents()
    panel._on_open_ce_clicked()
    qapp.processEvents()
    assert panel.link_warning.isVisible()
    panel.deleteLater()


def test_complex_panel_open_busy(monkeypatch, qapp):
    monkeypatch.setattr("app.gui.widgets.complex_panel.get_complex_editor_settings", lambda: _settings_stub())
    monkeypatch.setattr("app.domain.complex_linker.record_bridge_action", lambda msg: None)
    link_snapshot = {
        "part_id": 23,
        "ce_complex_id": "ce-23",
        "ce_db_uri": "C:/busy.mdb",
        "ce_pn": "PN23",
        "total_pins": 4,
        "synced_at": "2024-04-01T00:00:00",
    }
    monkeypatch.setattr(ComplexPanel, "_load_link_snapshot", lambda self, pid: dict(link_snapshot))
    monkeypatch.setattr(
        "app.domain.complex_linker.open_in_ce",
        lambda *args, **kwargs: (_ for _ in ()).throw(complex_linker.CEBusyEditorError("busy")),
    )
    panel = ComplexPanel()
    panel.show()
    panel.set_context(23, "PN23")
    qapp.processEvents()
    panel._on_open_ce_clicked()
    qapp.processEvents()
    assert panel.link_warning.isVisible()
    assert "busy" in panel.link_warning.text().lower()
    panel.deleteLater()

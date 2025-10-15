import pytest
import os
from pathlib import Path

TMP_SETTINGS = Path("tests/_tmp_settings.toml")
if not TMP_SETTINGS.exists():
    TMP_SETTINGS.write_text('[database]\nurl="sqlite:///:memory:"\n')

os.environ.setdefault("BOM_SETTINGS_PATH", str(TMP_SETTINGS.resolve()))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.domain import complex_linker
from app.domain.complex_linker import ComplexLink
from app.integration import ce_bridge_client
from app.integration import ce_bridge_client
from app.integration.ce_bridge_client import CENetworkError, CENotFound, CEUserCancelled


@pytest.fixture
def sqlite_engine(monkeypatch):
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def new_session():
        SQLModel.metadata.create_all(engine)
        ComplexLink.__table__.create(engine, checkfirst=True)
        return Session(engine)

    monkeypatch.setattr(complex_linker.database, "new_session", new_session)
    return engine


def _capture_updates(monkeypatch, first_payload, second_payload=None):
    payloads = iter([first_payload, second_payload or first_payload])
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "get_complex",
        lambda _ce_id: next(payloads),
    )


def test_attach_existing_complex_inserts_and_updates(monkeypatch, sqlite_engine):
    payload1 = {
        "id": "ce-1",
        "db_path": "C:/complex1.mdb",
        "pn": "PN-1",
        "aliases": ["ALIAS"],
        "pin_map": {"1": "A"},
        "macro_ids": ["M1"],
        "source_hash": "abc",
        "total_pins": 24,
    }
    payload2 = {
        "id": "ce-1",
        "db_path": "C:/complex1_v2.mdb",
        "pn": "PN-1",
        "aliases": ["ALT"],
        "pin_map": {"2": "B"},
        "macro_ids": ["M2"],
        "source_hash": "xyz",
        "total_pins": 30,
    }

    timestamps = iter(["2024-01-01T00:00:00", "2024-01-02T00:00:00"])
    monkeypatch.setattr(complex_linker, "_utc_iso", lambda: next(timestamps))
    _capture_updates(monkeypatch, payload1, payload2)

    complex_linker.attach_existing_complex(5, "ce-1")
    complex_linker.attach_existing_complex(5, "ce-1")

    with Session(sqlite_engine) as session:
        link = session.exec(select(ComplexLink)).one()
        assert link.part_id == 5
        assert link.ce_complex_id == "ce-1"
        assert link.ce_db_uri == "C:/complex1_v2.mdb"
        assert link.ce_pn == "PN-1"
        assert link.aliases == '["ALT"]'
        assert link.pin_map == '{"2": "B"}'
        assert link.macro_ids == '["M2"]'
        assert link.source_hash == "xyz"
        assert link.total_pins == 30
        assert link.synced_at == "2024-01-02T00:00:00"


def test_create_and_attach_complex(monkeypatch, sqlite_engine):
    created = {"id": "ce-99"}
    detail = {
        "id": "ce-99",
        "db_path": "D:/complex99.mdb",
        "pn": "PN-777",
        "aliases": [],
        "pin_map": {},
        "macro_ids": [],
        "source_hash": None,
        "total_pins": 18,
    }
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "create_complex",
        lambda pn, aliases=None, status_callback=None: created,
    )
    monkeypatch.setattr(complex_linker.ce_bridge_client, "get_complex", lambda _id: detail)
    monkeypatch.setattr(complex_linker, "_utc_iso", lambda: "2024-03-01T12:00:00")

    record = complex_linker.create_and_attach_complex(7, "PN-777")
    assert record["ce_complex_id"] == "ce-99"

    with Session(sqlite_engine) as session:
        link = session.exec(select(ComplexLink).where(ComplexLink.part_id == 7)).one()
        assert link.ce_complex_id == "ce-99"
        assert link.ce_db_uri == "D:/complex99.mdb"
        assert link.ce_pn == "PN-777"
        assert link.total_pins == 18
        assert link.synced_at == "2024-03-01T12:00:00"


def test_create_and_attach_without_id_triggers_selection(monkeypatch):
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "create_complex",
        lambda pn, aliases=None, status_callback=None: {},
    )
    matches = [
        {"id": "ce-1", "pn": "PN-123", "aliases": []},
        {"id": "ce-2", "pn": "PN-123", "aliases": []},
    ]
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "search_complexes",
        lambda pn, limit=10: matches,
    )
    with pytest.raises(complex_linker.CESelectionRequired) as exc:
        complex_linker.create_and_attach_complex(1, "PN-123")
    assert exc.value.matches == matches


def test_auto_link_by_pn_success_and_network(monkeypatch):
    attached = []

    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "search_complexes",
        lambda pn, limit=10: [
            {"id": "ce-1", "pn": "pn-123", "aliases": ["alt-1"]},
            {"id": "ce-2", "pn": "other", "aliases": []},
        ],
    )
    monkeypatch.setattr(
        complex_linker,
        "attach_existing_complex",
        lambda part_id, ce_id: (attached.append((part_id, ce_id)) or {'ce_complex_id': ce_id}),
    )

    result = complex_linker.auto_link_by_pn(11, "PN-123")
    assert result is True
    assert attached == [(11, "ce-1")]

    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "search_complexes",
        lambda pn, limit=10: (_ for _ in ()).throw(CENetworkError("offline")),
    )
    attached.clear()
    assert complex_linker.auto_link_by_pn(11, "PN-123") is False
    assert attached == []


def test_unlink_existing_complex(monkeypatch, sqlite_engine):
    monkeypatch.setattr(complex_linker, "record_bridge_action", lambda msg: None)
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "get_complex",
        lambda _ce_id: {
            "id": "ce-77",
            "db_path": "C:/x.mdb",
            "pn": "PN-77",
            "aliases": [],
            "pin_map": {},
            "macro_ids": [],
            "source_hash": None,
            "total_pins": 64,
        },
    )
    complex_linker.attach_existing_complex(9, "ce-77")

    assert complex_linker.unlink_existing_complex(9) is True
    assert complex_linker.unlink_existing_complex(9) is False

    with Session(sqlite_engine) as session:
        assert session.exec(select(ComplexLink).where(ComplexLink.part_id == 9)).first() is None


def test_check_link_stale_not_found(monkeypatch):
    monkeypatch.setattr(complex_linker, "record_bridge_action", lambda msg: None)
    class DummyClient:
        def get_complex(self, ce_id):
            raise CENotFound("gone")

    stale, reason = complex_linker.check_link_stale(DummyClient(), {"ce_complex_id": "ce-1"})
    assert stale is True
    assert reason == "not_found"


def test_check_link_stale_transient(monkeypatch):
    monkeypatch.setattr(complex_linker, "record_bridge_action", lambda msg: None)
    class DummyClient:
        def get_complex(self, ce_id):
            raise CENetworkError("offline")

    stale, reason = complex_linker.check_link_stale(DummyClient(), {"ce_complex_id": "ce-1"})
    assert stale is False
    assert reason == "transient"


def test_check_link_stale_ok(monkeypatch):
    monkeypatch.setattr(complex_linker, "record_bridge_action", lambda msg: None)
    class DummyClient:
        def get_complex(self, ce_id):
            return {"id": ce_id}

    stale, reason = complex_linker.check_link_stale(DummyClient(), {"ce_complex_id": "ce-1"})
    assert stale is False
    assert reason == ""


def test_attach_as_alias_and_link(monkeypatch, sqlite_engine):
    monkeypatch.setattr(complex_linker, "record_bridge_action", lambda msg: None)
    alias_calls = []
    monkeypatch.setattr(
        complex_linker,
        "add_aliases",
        lambda ce_id, aliases: alias_calls.append((ce_id, aliases)) or {},
    )
    details = {
        "id": "ce-88",
        "pn": "PN-ALIAS",
        "db_path": "C:/ce88.mdb",
        "aliases": ["PN-ALIAS"],
        "pin_map": {},
        "macro_ids": [],
        "source_hash": None,
        "total_pins": 10,
    }
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "get_complex",
        lambda ce_id: dict(details, id=ce_id),
    )

    record = complex_linker.attach_as_alias_and_link(12, "PN-ALIAS", "ce-88")
    assert alias_calls == [("ce-88", ["PN-ALIAS"])]
    assert record["ce_complex_id"] == "ce-88"


def test_attach_as_alias_and_link_conflict(monkeypatch):
    monkeypatch.setattr(complex_linker, "record_bridge_action", lambda msg: None)

    def _raise_conflict(ce_id, aliases):
        raise ce_bridge_client.CEAliasConflict(ce_id, ["ce-77"])

    monkeypatch.setattr(complex_linker, "add_aliases", _raise_conflict)

    with pytest.raises(complex_linker.AliasConflictError) as exc:
        complex_linker.attach_as_alias_and_link(1, "PN", "ce-77")
    assert exc.value.conflicts == ["ce-77"]

def test_open_in_ce_uses_existing_link(monkeypatch):
    open_calls = []
    monkeypatch.setattr(
        complex_linker,
        "open_complex",
        lambda ce_id, status_callback=None, allow_cached=True: open_calls.append((ce_id, allow_cached)) or False,
    )
    link = {"ce_complex_id": "ce-1", "part_id": 5, "ce_pn": "PN1"}
    result = complex_linker.open_in_ce({"link": link, "pn": "PN1", "part_id": 5}, use_cached_preflight=True)
    assert open_calls == [("ce-1", True)]
    assert result.ce_id == "ce-1"
    assert result.already_open is False
    assert result.link_record == link


def test_open_in_ce_with_selection(monkeypatch):
    monkeypatch.setattr(
        complex_linker,
        "search_complexes",
        lambda pn, limit=20: [{"id": "ce-2", "pn": pn}],
    )
    open_calls = []
    monkeypatch.setattr(
        complex_linker,
        "open_complex",
        lambda ce_id, status_callback=None, allow_cached=True: open_calls.append(ce_id) or False,
    )

    result = complex_linker.open_in_ce(
        {"pn": "PN2", "part_id": 7},
        chooser=lambda items: (items[0], False),
        use_cached_preflight=False,
    )
    assert open_calls == ["ce-2"]
    assert result.ce_id == "ce-2"


def test_open_in_ce_attach_first(monkeypatch):
    monkeypatch.setattr(
        complex_linker,
        "search_complexes",
        lambda pn, limit=20: [{"id": "ce-3", "pn": pn, "aliases": []}],
    )
    open_calls = []
    monkeypatch.setattr(
        complex_linker,
        "open_complex",
        lambda ce_id, status_callback=None, allow_cached=True: open_calls.append(ce_id) or False,
    )
    attached = {"ce_complex_id": "ce-3", "part_id": 8, "ce_pn": "PN3"}
    monkeypatch.setattr(
        complex_linker,
        "attach_as_alias_and_link",
        lambda part_id, pn, ce_id, status_callback=None: dict(attached, part_id=part_id, ce_pn=pn),
    )

    result = complex_linker.open_in_ce(
        {"pn": "PN3", "part_id": 8},
        chooser=lambda items: (items[0], True),
    )
    assert open_calls == ["ce-3"]
    assert result.link_record["ce_complex_id"] == "ce-3"


def test_open_in_ce_stale_wrap(monkeypatch):
    monkeypatch.setattr(
        complex_linker,
        "open_complex",
        lambda ce_id, status_callback=None, allow_cached=True: (_ for _ in ()).throw(ce_bridge_client.CEStaleLink(ce_id)),
    )
    with pytest.raises(complex_linker.CEStaleLinkError):
        complex_linker.open_in_ce({"link": {"ce_complex_id": "ce-4"}})


def test_open_in_ce_busy_wrap(monkeypatch):
    monkeypatch.setattr(
        complex_linker,
        "open_complex",
        lambda ce_id, status_callback=None, allow_cached=True: (_ for _ in ()).throw(ce_bridge_client.CEBusyError("busy")),
    )
    with pytest.raises(complex_linker.CEBusyEditorError):
        complex_linker.open_in_ce({"link": {"ce_complex_id": "ce-5"}})


def test_open_in_ce_cancel(monkeypatch):
    monkeypatch.setattr(
        complex_linker,
        "search_complexes",
        lambda pn, limit=20: [{"id": "ce-6", "pn": pn}],
    )
    with pytest.raises(CEUserCancelled):
        complex_linker.open_in_ce({"pn": "PN6"}, chooser=lambda items: (None, False))
